"""Roblox Open Cloud API Client, R6-Textur-Compositor & Avatar-Asset-Loader."""

from __future__ import annotations

import base64
import gzip
import io
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import requests
import zstandard
from PIL import Image, ImageDraw

from scripts.brick_colors import brick_color_to_rgb, hex_to_rgb
from scripts.r15_blocky_rig import BODY_PART_ASSET_TYPES, R15_FIXED_ATTACHMENT_POSITIONS
from scripts.r15_clothing import (
    CLOTHING_RECTS,
    compose_r15_part_textures,
    crop_template_rect as _crop_template,
)
from scripts.rbx_animation import parse_animation_asset
from scripts.rbx_formats import (
    IDENTITY_CFRAME,
    MeshData,
    RBXM_MAGIC,
    RbxNode,
    ZSTD_MAGIC,
    compose_cframes,
    cframe_position,
    extract_asset_id,
    parse_rbx_mesh,
    parse_rbx_model,
    translate_cframe,
)

DEFAULT_SMILE_ASSET_ID = 144075659

# Roblox Enum.AlphaMode (siehe create.roblox.com/docs/reference/engine/enums/AlphaMode)
ALPHA_MODE_NAMES: dict[int, str] = {0: "overlay", 1: "transparency", 2: "tintmask", 3: "opaque"}

# Korblox Deathspeaker Right Leg - das berühmte, 17.000 Robux teure Bein des
# Korblox-Deathspeaker-Bundles (offiziell von Roblox, nicht mehr im Verkauf).
# Asset-ID 139607718, Asset-Typ Right Leg (31).
# https://www.roblox.com/de/catalog/139607718/Korblox-Deathspeaker-Right-Leg
KORBLOX_RIGHT_LEG_ASSET_ID = 139607718

# R6-kompatible starre Accessoire-Typen (Layered Clothing 64..72 wird in R6 bewusst ignoriert)
RIGID_ACCESSORY_TYPE_IDS = {
    8,   # Hat
    41,  # HairAccessory
    42,  # FaceAccessory
    43,  # NeckAccessory
    44,  # ShoulderAccessory
    45,  # FrontAccessory
    46,  # BackAccessory
    47,  # WaistAccessory
}

class PipelineError(RuntimeError):
    """Klarer, nutzer- und agentenlesbarer Fehler für die GitHub Workflow-Übersicht."""


@dataclass
class HeadSpec:
    kind: str  # "classic_head" | "custom_mesh" | "dynamic_head" | "headless"
    mesh: MeshData | None = None
    mesh_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    mesh_offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    skin_rgb: tuple[int, int, int] = (245, 205, 48)
    face_texture_path: str | None = None
    custom_texture_path: str | None = None


@dataclass
class BodyPartSpec:
    """Ein Körperteil aus dem Inventar (MeshPart oder SpecialMesh)."""
    asset_id: int
    asset_type_id: int  # 17=Head, 27=Torso, 28=RightArm, 29=LeftArm, 30=LeftLeg, 31=RightLeg
    mesh: MeshData
    mesh_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    mesh_offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    cframe: tuple[float, ...] = IDENTITY_CFRAME
    transparency: float = 0.0
    base_color: tuple[float, float, float] = (0.8, 0.8, 0.8)
    texture_path: str | None = None
    tint_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    # Roblox Enum.AlphaMode: overlay | transparency | tintmask | opaque
    alpha_mode: str = "overlay"


@dataclass
class AccessorySpec:
    asset_id: int
    name: str
    asset_type_id: int
    mesh: MeshData
    mesh_scale: tuple[float, float, float]
    mesh_offset: tuple[float, float, float]
    attachment_name: str | None
    attachment_cframe: tuple[float, ...]
    legacy_attachment_point: tuple[float, ...]
    texture_path: str | None
    tint_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    base_color: tuple[float, float, float] = (0.8, 0.8, 0.8)
    alpha_blend: bool = False
    transparency: float = 0.0
    meta_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    meta_rot_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    meta_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


@dataclass
class AvatarBundle:
    username: str
    display_name: str
    user_id: int
    body_texture_path: str
    body_colors: dict[str, tuple[int, int, int]]
    head: HeadSpec
    accessories: list[AccessorySpec] = field(default_factory=list)
    # ClassicType: R15-Skalierung (z. B. height/width/head) und Avatar-Typ, die
    # von der Open Cloud v4-Avatar-API geliefert werden.
    scales: dict[str, float] = field(default_factory=dict)
    player_avatar_type: int | None = None
    # ClassicType: pro R15-Teil eine eigene Shirt/Pants/T-Shirt/Face-Textur.
    r15_part_textures: dict[str, str] = field(default_factory=dict)
    # ClassicType: Körperteile aus dem Inventar (Asset-Typen 17, 27-31).
    # Nur für fehlende Teile wird der blocky rig als Fallback verwendet.
    body_parts: dict[str, BodyPartSpec] = field(default_factory=dict)
    r15_clothing_overlays: dict[str, str] = field(default_factory=dict)


@dataclass
class EmoteAnimation:
    """Eine geladene Emote-Animation (KeyframeSequence oder CurveAnimation)."""

    asset_id: int
    clip: Any

    @property
    def preview_time(self) -> float:
        """Der Moment der Emote-Vorschau: die erste Keyframe der Animation.

        Roblox erzeugt das Vorsau-Bild einer UGC-Emote aus der ersten
        Keyframe (Roblox-Forum: "UGC emotes use the first keyframe for the
        thumbnail"), also wird nichts geraten.
        """
        if self.clip is None:
            return 0.0
        return float(self.clip.preview_time())


def _all_nodes(roots: list[RbxNode]) -> list[RbxNode]:
    """Alle Knoten eines geparsten Modells (inklusive der Wurzeln)."""
    nodes: list[RbxNode] = []
    for root in roots:
        nodes.append(root)
        nodes.extend(root.descendants())
    return nodes


def _sanitize_secret(text: str, secret: str) -> str:
    if secret and len(secret) >= 6:
        return text.replace(secret, "***REDACTED***")
    return text


def _decompress_http_bytes(raw: bytes) -> bytes:
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    if raw[:4] == ZSTD_MAGIC:
        return zstandard.ZstdDecompressor().decompress(raw, max_output_size=64 * 1024 * 1024)
    return raw


class RobloxOpenCloudClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = (api_key or "").strip()
        if not self.api_key:
            raise PipelineError(
                "Das GitHub Secret 'ROBLOX_API_KEY' ist nicht gesetzt oder leer.\n"
                "Lösung: Lege deinen Roblox Open Cloud API Key im Repository unter "
                "'Settings -> Secrets and variables -> Actions -> New repository secret' "
                "mit dem Namen ROBLOX_API_KEY ab."
            )
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "RobloxAvatarGFXRenderer/1.0"})
        self._asset_cache: dict[int, bytes] = {}

    def _request(
        self,
        method: str,
        url: str,
        *,
        use_api_key: bool = False,
        json_body: Any = None,
        params: dict[str, str] | None = None,
        timeout: int = 25,
    ) -> requests.Response:
        headers: dict[str, str] = {}
        if use_api_key:
            headers["x-api-key"] = self.api_key

        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                resp = self.session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    params=params,
                    timeout=timeout,
                )
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                msg = _sanitize_secret(str(exc), self.api_key)
                raise PipelineError(f"Netzwerkfehler bei Anfrage an {url}: {msg}") from exc
        raise PipelineError(f"Anfrage an {url} fehlgeschlagen: {last_exc}")

    def validate_api_key(self) -> None:
        """Prüft den Open Cloud API Key vorab über den Introspect-Endpunkt."""
        resp = self._request(
            "POST",
            "https://apis.roblox.com/api-keys/v1/introspect",
            use_api_key=True,
            json_body={"apiKey": self.api_key},
        )
        if resp.status_code in (401, 403):
            detail = _sanitize_secret(resp.text[:300], self.api_key)
            lower = detail.lower()
            if "ip" in lower:
                raise PipelineError(
                    f"Roblox Open Cloud API Key lehnt die GitHub-Actions-IP ab (HTTP {resp.status_code}).\n"
                    f"Bitte trage beim API Key unter 'Accepted IP Addresses' den Bereich '0.0.0.0/0' ein.\n"
                    f"Antwort: {detail}"
                )
            raise PipelineError(
                f"Roblox Open Cloud API Key ist ungültig oder nicht autorisiert (HTTP {resp.status_code}).\n"
                f"Bitte prüfe das Secret 'ROBLOX_API_KEY'. Antwort: {detail}"
            )
        if resp.status_code == 200:
            try:
                info = resp.json()
            except ValueError:
                return
            if info.get("enabled") is False:
                raise PipelineError(
                    "Der hinterlegte Roblox Open Cloud API Key ist im Creator Dashboard deaktiviert ('enabled: false')."
                )
            if info.get("expired") is True:
                raise PipelineError(
                    f"Der hinterlegte Roblox Open Cloud API Key ist abgelaufen "
                    f"(Ablaufdatum: {info.get('expirationTimeUtc')}). Bitte erstelle einen neuen API Key."
                )

    def resolve_user(self, username: str) -> tuple[int, str, str]:
        """Löst Roblox-Benutzername -> (user_id, canonical_name, display_name) auf und prüft per Open Cloud v2."""
        clean = username.strip().lstrip("@").strip()
        if not clean:
            raise PipelineError(
                "Die Konfiguration enthält keinen Roblox-Benutzernamen. "
                "Bitte trage dort einen gültigen Benutzernamen ein."
            )

        resp = self._request(
            "POST",
            "https://users.roblox.com/v1/usernames/users",
            json_body={"usernames": [clean], "excludeBannedUsers": False},
        )
        if resp.status_code != 200:
            raise PipelineError(
                f"Fehler bei der Roblox-Benutzersuche für '{clean}' (HTTP {resp.status_code}): {resp.text[:250]}"
            )
        items = resp.json().get("data") or []
        if not items:
            raise PipelineError(
                f"Der Roblox-Benutzer '{clean}' existiert nicht. Bitte prüfe den Namen in 'configs.toml'."
            )

        user_id = int(items[0]["id"])
        canonical_name = str(items[0].get("name") or clean)
        display_name = str(items[0].get("displayName") or canonical_name)

        # Open Cloud v2 Users API Verifikation
        oc_resp = self._request(
            "GET",
            f"https://apis.roblox.com/cloud/v2/users/{user_id}",
            use_api_key=True,
        )
        if oc_resp.status_code == 200:
            oc_data = oc_resp.json()
            canonical_name = str(oc_data.get("name") or canonical_name)
            display_name = str(oc_data.get("displayName") or display_name)
        elif oc_resp.status_code == 401:
            detail = _sanitize_secret(oc_resp.text[:250], self.api_key)
            raise PipelineError(f"Ungültiger ROBLOX_API_KEY bei Open Cloud Users v2 (HTTP 401): {detail}")
        elif oc_resp.status_code == 403 and "ip" in oc_resp.text.lower():
            detail = _sanitize_secret(oc_resp.text[:250], self.api_key)
            raise PipelineError(
                f"ROBLOX_API_KEY blockiert die GitHub-Actions-IP (HTTP 403). "
                f"Bitte '0.0.0.0/0' bei 'Accepted IP Addresses' erlauben. Detail: {detail}"
            )

        return user_id, canonical_name, display_name

    def fetch_avatar_metadata(self, user_id: int) -> dict[str, Any]:
        """Lädt BodyColors und getragene Assets des Nutzers."""
        for url in (
            f"https://avatar.roblox.com/v2/avatar/users/{user_id}/avatar",
            f"https://avatar.roblox.com/v1/users/{user_id}/avatar",
        ):
            resp = self._request("GET", url)
            if resp.status_code == 200:
                return resp.json()
        raise PipelineError(
            f"Avatar-Daten für User-ID {user_id} konnten nicht geladen werden (HTTP {resp.status_code})."
        )

    def fetch_avatar_metadata_v4(self, user_id: int) -> dict[str, Any]:
        """Lädt die R15-Avatar-Definition (Farben, Skalierung, getragene Assets)
        eines Nutzers. Das ist der funktionierende Endpunkt fuer ClassicType
        (v2 Avatar API, funktioniert ohne Cookie fuer oeffentliche Nutzerdaten)."""
        return self.fetch_avatar_metadata(user_id)

    def fetch_equipped_emote(self, user_id: int) -> dict[str, Any] | None:
        """Liefert die am Spieleravatar ausgerüstete Emote (Profilpose).

        Roblox stattet die Emotes direkt am Avatar aus (bis zu acht Slots).
        Die **öffentliche** Avatar-API liefert genau diese ausgerüsteten Emotes
        im Feld ``emotes``; ``position`` ist die Slot-Nummer (1-basiert).
        Slot 1 ist der Emote, den Roblox auch als Profilpose benutzt
        (Avatar-Editor -> "Body" -> "Emotes" -> "Profilbild-Pose").

        Der ältere Endpunkt ``/v1/avatar/thumbnail-customizations`` liefert
        zwar direkt die Profilpose, verlangt aber ein .ROBLOSECURITY-Cookie
        des eigenen Accounts und antwortet mit einem Open Cloud API Key
        deshalb immer mit HTTP 401 - er ist ohne Cookie nicht nutzbar.
        """
        try:
            meta = self.fetch_avatar_metadata(user_id)
        except PipelineError as exc:
            print(f"[avatar] Ausgeruestete Emote konnte nicht abgefragt werden: {exc}")
            return None

        emotes = [e for e in (meta.get("emotes") or []) if isinstance(e, dict)]
        if not emotes:
            print("[avatar] Am Avatar ist keine Emote ausgeruestet - Pose bleibt 'None'.")
            return None

        def _slot(entry: dict[str, Any]) -> int:
            try:
                return int(entry.get("position") or 0)
            except (TypeError, ValueError):
                return 0

        emotes.sort(key=_slot)
        first = emotes[0]
        try:
            asset_id = int(first.get("assetId") or 0)
        except (TypeError, ValueError):
            asset_id = 0
        if asset_id <= 0:
            print("[avatar] Ausgeruestete Emote hat keine gueltige Asset-ID - Pose bleibt 'None'.")
            return None
        return {
            "emote_asset_id": asset_id,
            "emote_name": str(first.get("assetName") or ""),
            "emote_slot": _slot(first),
        }

    def load_emote_animation(self, asset_id: int, work_dir: Path) -> EmoteAnimation | None:
        """Laedt eine Emote-Animation (Asset-Typ Animation/EmoteAnimation) per
        Open Cloud Asset Delivery und parst sie aus.

        Roblox liefert Emotes in zwei Formaten, beide werden unterstuetzt:

        * ``KeyframeSequence`` (klassisch): verschachtelter ``Pose``-Baum je
          Keyframe.
        * ``CurveAnimation`` (neu, UGC-Emotes): Ordner je Bone mit
          FloatCurve-Bloeb (``ValuesAndTimes``).

        Manche Emote-Assets sind nur ein ``Animation``-Wrapper; dann zeigt
        dessen ``AnimationId`` auf das eigentliche Animations-Asset.

        Das System rat den Vorschau-Moment nicht aus: der verwendete Moment
        ist die erste Keyframe der Animation, also genau der Anfang der
        Emote-Vorschau (so erzeugt Roblox auch das Vorsau-Bild).
        """
        try:
            raw = self.fetch_asset_bytes(asset_id)
        except PipelineError as exc:
            print(f"[emote] Emote {asset_id} konnte nicht geladen werden: {exc}")
            return None

        try:
            roots = parse_rbx_model(raw)
        except Exception as exc:
            print(f"[emote] Emote {asset_id} konnte nicht geparst werden: {exc}")
            return None

        clip = parse_animation_asset(roots)
        if clip is None:
            # Neuere Emote-Assets sind nur ein "Animation"-Wrapper: die
            # eigentliche Animation liegt in einem zweiten Asset, auf das die
            # Property "AnimationId" zeigt.
            for node in _all_nodes(roots):
                if node.class_name != "Animation":
                    continue
                target = extract_asset_id(node.prop("AnimationId"))
                if not target or target == asset_id:
                    continue
                try:
                    raw2 = self.fetch_asset_bytes(target)
                    roots2 = parse_rbx_model(raw2)
                except Exception as exc:
                    print(
                        f"[emote] Ziel-Asset {target} des Emotes {asset_id} "
                        f"konnte nicht geladen werden: {exc}"
                    )
                    continue
                clip = parse_animation_asset(roots2)
                if clip is not None:
                    break

        if clip is None:
            print(
                f"[emote] Emote {asset_id} enthaelt weder eine KeyframeSequence "
                "noch eine CurveAnimation."
            )
            return None

        print(
            f"[emote] Emote {asset_id}: {clip.source_format} geladen, "
            f"{len(clip.tracks)} Bones, {len(clip.times)} Zeitpunkte, "
            f"Vorschau-Moment t={clip.preview_time():.3f}s."
        )
        return EmoteAnimation(asset_id=asset_id, clip=clip)


    def load_korblox_leg(self, work_dir: Path) -> dict[str, Any]:
        """Laedt das Korblox Deathspeaker Right Leg (Asset-ID 139607718) per
        Open Cloud Asset Delivery und gibt Mesh, Textur und Basisfarbe zurueck."""
        work_dir.mkdir(parents=True, exist_ok=True)
        raw = self.fetch_asset_bytes(KORBLOX_RIGHT_LEG_ASSET_ID)
        roots = parse_rbx_model(raw)
        nodes: list[RbxNode] = []
        for rt in roots:
            nodes.append(rt)
            nodes.extend(rt.descendants())

        mesh_part = next((n for n in nodes if n.class_name == "MeshPart"), None)
        legacy_handle = next((n for n in nodes if n.name == "Handle"), None)
        sm = legacy_handle.find_first_child_of_class("SpecialMesh") if legacy_handle is not None else None

        texture_node = mesh_part if mesh_part is not None else sm
        color_node = mesh_part if mesh_part is not None else legacy_handle

        mid: int | None = None
        if mesh_part is not None:
            mid = extract_asset_id(mesh_part.prop("MeshId", "MeshContent"))
        elif sm is not None:
            mid = extract_asset_id(sm.prop("MeshId", "MeshContent", "MeshId"))
        if not mid:
            raise PipelineError(f"Korblox-Asset {KORBLOX_RIGHT_LEG_ASSET_ID} enthaelt kein Mesh.")

        mdata = parse_rbx_mesh(self.fetch_asset_bytes(mid))

        tex_id: int | None = None
        if texture_node is not None:
            sa = texture_node.find_first_child_of_class("SurfaceAppearance")
            if sa is not None:
                tex_id = extract_asset_id(sa.prop("ColorMap", "ColorMapContent"))
        if not tex_id and texture_node is not None:
            tex_id = extract_asset_id(
                texture_node.prop("TextureID", "TextureId", "TextureContent")
            )

        tex_path: str | None = None
        if tex_id:
            timg = self.fetch_image(tex_id)
            tex_path = str(work_dir / f"korblox_tex_{tex_id}.png")
            timg.save(tex_path, format="PNG")

        base_color: tuple[float, float, float] = (0.8, 0.8, 0.8)
        if color_node is not None:
            col_raw = color_node.prop("Color", "Color3uint8")
            if isinstance(col_raw, tuple) and len(col_raw) == 3:
                base_color = (float(col_raw[0]), float(col_raw[1]), float(col_raw[2]))
            elif color_node.prop("BrickColor") is not None:
                brgb = brick_color_to_rgb(int(color_node.prop("BrickColor")))
                base_color = (brgb[0] / 255.0, brgb[1] / 255.0, brgb[2] / 255.0)

        return {
            "mesh": mdata,
            "texture_path": tex_path,
            "base_color": base_color,
        }

    def fetch_asset_bytes(self, asset_id: int) -> bytes:
        """Lädt Rohbytes eines Assets über die Roblox Open Cloud Asset Delivery API."""
        if asset_id in self._asset_cache:
            return self._asset_cache[asset_id]

        url = f"https://apis.roblox.com/asset-delivery-api/v1/assetId/{asset_id}"
        resp = self._request("GET", url, use_api_key=True)

        if resp.status_code in (401, 403):
            detail = _sanitize_secret(resp.text[:300], self.api_key)
            lower = detail.lower()
            if "scope" in lower or "permission" in lower or "authorized" in lower:
                raise PipelineError(
                    f"Dem ROBLOX_API_KEY fehlt die Berechtigung zum Laden von Avatar-Assets (Asset {asset_id}, HTTP {resp.status_code}).\n"
                    f"Bitte gib dem Open Cloud API Key die Berechtigung 'legacy-asset:manage' (und 'asset:read') "
                    f"sowie IP-Freigabe '0.0.0.0/0'.\nDetail: {detail}"
                )
            if "ip" in lower:
                raise PipelineError(
                    f"ROBLOX_API_KEY lehnt die IP von GitHub Actions ab (HTTP {resp.status_code}). "
                    f"Bitte '0.0.0.0/0' unter 'Accepted IP Addresses' eintragen.\nDetail: {detail}"
                )
            raise PipelineError(
                f"Open Cloud Asset Delivery verweigert Zugriff auf Asset {asset_id} (HTTP {resp.status_code}): {detail}"
            )

        location: str | None = None
        if resp.status_code == 200:
            try:
                payload = resp.json()
                location = payload.get("location")
                if not location and payload.get("locations"):
                    location = payload["locations"][0].get("location")
            except ValueError:
                # Falls Endpunkt direkt Binärdaten liefert
                if resp.content:
                    data = _decompress_http_bytes(resp.content)
                    self._asset_cache[asset_id] = data
                    return data

        # Fallback auf öffentlichen v2-Endpunkt für eingebaute Standard-Assets
        if not location:
            anon = self._request("GET", f"https://assetdelivery.roblox.com/v2/assetId/{asset_id}")
            if anon.status_code == 200:
                payload = anon.json()
                location = payload.get("location")
                if not location and payload.get("locations"):
                    location = payload["locations"][0].get("location")

        if not location:
            raise PipelineError(
                f"Asset {asset_id} konnte über Open Cloud Asset Delivery nicht aufgelöst werden "
                f"(HTTP {resp.status_code})."
            )

        # Niemals den API Key an das CDN mitsenden!
        cdn_resp = self._request("GET", location, use_api_key=False, timeout=30)
        if cdn_resp.status_code != 200:
            raise PipelineError(
                f"CDN-Download für Asset {asset_id} fehlgeschlagen (HTTP {cdn_resp.status_code})."
            )

        data = _decompress_http_bytes(cdn_resp.content)
        self._asset_cache[asset_id] = data
        return data

    def fetch_image(self, asset_id: int, max_depth: int = 3) -> Image.Image:
        """Lädt ein Bild-Asset (löst automatisch Shirt/Pants/Decal XML/RBXM-Wrapper auf)."""
        raw = self.fetch_asset_bytes(asset_id)
        try:
            img = Image.open(io.BytesIO(raw))
            img.load()
            print(
                f"[image] {asset_id}: {img.mode} {img.size} "
                f"transparency={img.info.get('transparency')} "
                f"raw_format={raw[:8]!r}"
            )
            return img.convert("RGBA")
        except Exception:
            pass

        if max_depth <= 0:
            raise PipelineError(f"Asset {asset_id} enthielt nach Auflösung keine gültige Bilddatei.")

        roots = parse_rbx_model(raw)
        all_nodes: list[RbxNode] = []
        for rt in roots:
            all_nodes.append(rt)
            all_nodes.extend(rt.descendants())

        for node in all_nodes:
            for prop_key in (
                "ShirtTemplate",
                "PantsTemplate",
                "Graphic",
                "Texture",
                "ColorMap",
                "ColorMapContent",
                "TextureId",
                "TextureID",
                "Image",
            ):
                inner_id = extract_asset_id(node.prop(prop_key))
                if inner_id and inner_id != asset_id:
                    return self.fetch_image(inner_id, max_depth=max_depth - 1)

        raise PipelineError(f"Konnte keine Textur-ID aus Modell-Asset {asset_id} extrahieren.")


# ---------------------------------------------------------------------------
# R6 Body Texture Compositor (exakt abgestimmt auf WomanRig.blend UVs)
# ---------------------------------------------------------------------------

# _crop_template kommt aus scripts.r15_clothing (dort liegen auch die
# unveraenderten Template-Konstanten fuer den R6- und den R15-Compositor).


def _paste_with_bleed(canvas: Image.Image, tile: Image.Image, x: int, y: int, bleed: int = 8) -> None:
    """Fügt ein Tile ein und erweitert seine Ränder um `bleed` Pixel gegen Subsurf-UV-Seams."""
    w, h = tile.size
    canvas.paste(tile, (x, y))
    if bleed <= 0:
        return
    top_edge = tile.crop((0, 0, w, 1)).resize((w, bleed), Image.Resampling.NEAREST)
    bot_edge = tile.crop((0, h - 1, w, h)).resize((w, bleed), Image.Resampling.NEAREST)
    left_edge = tile.crop((0, 0, 1, h)).resize((bleed, h), Image.Resampling.NEAREST)
    right_edge = tile.crop((w - 1, 0, w, h)).resize((bleed, h), Image.Resampling.NEAREST)
    canvas.paste(top_edge, (x, max(0, y - bleed)))
    canvas.paste(bot_edge, (x, min(canvas.height - bleed, y + h)))
    canvas.paste(left_edge, (max(0, x - bleed), y))
    canvas.paste(right_edge, (min(canvas.width - bleed, x + w), y))


def _make_face_tile(
    skin_rgb: tuple[int, int, int],
    layers: list[Image.Image],
    out_w: int,
    out_h: int,
    rotate_cw_90: bool,
) -> Image.Image:
    """Baut ein einzelnes Körperflächen-Tile aus Hautfarbe + Kleidungsschichten."""
    # Vor Rotation: Breite = out_h (wenn 90° CW rotiert) sonst out_w
    pre_w = out_h if rotate_cw_90 else out_w
    pre_h = out_w if rotate_cw_90 else out_h
    base = Image.new("RGBA", (pre_w, pre_h), (*skin_rgb, 255))
    for layer in layers:
        resized = layer.resize((pre_w, pre_h), Image.Resampling.LANCZOS)
        base.alpha_composite(resized)
    if rotate_cw_90:
        base = base.transpose(Image.Transpose.ROTATE_270)
    return base


def compose_r6_body_texture(
    body_colors: dict[str, tuple[int, int, int]],
    shirt_img: Image.Image | None,
    pants_img: Image.Image | None,
    tshirt_img: Image.Image | None,
    scale: int = 2,
) -> Image.Image:
    """Erzeugt den hochauflösenden R6-Körper-Texturatlas (2048x1024) passend für WomanRig.blend."""
    W, H = 1024 * scale, 512 * scale
    bleed = 5 * scale

    torso_rgb = body_colors["torso"]
    r_arm_rgb = body_colors["rightArm"]
    l_arm_rgb = body_colors["leftArm"]
    r_leg_rgb = body_colors["rightLeg"]
    l_leg_rgb = body_colors["leftLeg"]
    head_rgb = body_colors["head"]

    canvas = Image.new("RGBA", (W, H), (*torso_rgb, 255))
    draw = ImageDraw.Draw(canvas)

    # 1. WomanRig Spezial-UV-Bereiche (Hals & 3D-Hände/Finger im unteren Streifen y=396..512)
    draw.rectangle([220 * scale, 396 * scale, 314 * scale, 512 * scale], fill=(*head_rgb, 255))
    draw.rectangle([314 * scale, 396 * scale, 426 * scale, 512 * scale], fill=(*r_arm_rgb, 255))
    draw.rectangle([426 * scale, 396 * scale, 548 * scale, 512 * scale], fill=(*l_arm_rgb, 255))

    def torso_layers(face: str) -> list[Image.Image]:
        out: list[Image.Image] = []
        if pants_img is not None:
            out.append(_crop_template(pants_img, CLOTHING_RECTS["torso"][face]))
        if shirt_img is not None:
            out.append(_crop_template(shirt_img, CLOTHING_RECTS["torso"][face]))
        if face == "Front" and tshirt_img is not None:
            out.append(tshirt_img)
        return out

    def arm_layers(side: str, face: str) -> list[Image.Image]:
        if shirt_img is None:
            return []
        return [_crop_template(shirt_img, CLOTHING_RECTS[side][face])]

    def leg_layers(side: str, face: str) -> list[Image.Image]:
        if pants_img is None:
            return []
        return [_crop_template(pants_img, CLOTHING_RECTS[side][face])]

    def put(
        rgb: tuple[int, int, int],
        layers: list[Image.Image],
        x: int,
        y: int,
        w: int,
        h: int,
        rot90: bool,
    ) -> None:
        tile = _make_face_tile(rgb, layers, w * scale, h * scale, rot90)
        _paste_with_bleed(canvas, tile, x * scale, y * scale, bleed=bleed)

    # 2. Torso-Streifen (x=24..120) + Caps
    put(torso_rgb, torso_layers("Right"), 24, 0, 96, 64, True)
    put(torso_rgb, torso_layers("Front"), 24, 64, 96, 128, True)
    put(torso_rgb, torso_layers("Left"), 24, 192, 96, 64, True)
    put(torso_rgb, torso_layers("Back"), 24, 256, 96, 128, True)
    put(torso_rgb, torso_layers("Top"), 150, 328, 96, 64, False)
    put(torso_rgb, torso_layers("Bottom"), 252, 328, 96, 64, False)

    # 3. Rechter Arm (x=174..270) + Caps
    put(r_arm_rgb, arm_layers("right", "Back"), 174, 64, 96, 64, True)
    put(r_arm_rgb, arm_layers("right", "Right"), 174, 128, 96, 64, True)
    put(r_arm_rgb, arm_layers("right", "Front"), 174, 192, 96, 64, True)
    put(r_arm_rgb, arm_layers("right", "Left"), 174, 256, 96, 64, True)
    put(r_arm_rgb, arm_layers("right", "Bottom"), 570, 328, 48, 64, False)
    put(r_arm_rgb, arm_layers("right", "Top"), 678, 328, 48, 64, False)

    # 4. Linker Arm (x=324..420) + Caps (inkl. WomanRig verschobene Schulter-UV bei 150,402)
    put(l_arm_rgb, arm_layers("left", "Right"), 324, 0, 96, 64, True)
    put(l_arm_rgb, arm_layers("left", "Front"), 324, 64, 96, 64, True)
    put(l_arm_rgb, arm_layers("left", "Left"), 324, 128, 96, 64, True)
    put(l_arm_rgb, arm_layers("left", "Back"), 324, 192, 96, 64, True)
    put(l_arm_rgb, arm_layers("left", "Bottom"), 624, 328, 48, 64, False)
    put(l_arm_rgb, arm_layers("left", "Top"), 732, 328, 48, 64, False)
    put(l_arm_rgb, arm_layers("left", "Top"), 150, 402, 48, 64, False)

    # 5. Rechtes Bein (x=474..570) + Caps
    put(r_leg_rgb, leg_layers("right", "Left"), 474, 0, 96, 64, True)
    put(r_leg_rgb, leg_layers("right", "Back"), 474, 64, 96, 64, True)
    put(r_leg_rgb, leg_layers("right", "Right"), 474, 128, 96, 64, True)
    put(r_leg_rgb, leg_layers("right", "Front"), 474, 192, 96, 64, True)
    put(r_leg_rgb, leg_layers("right", "Top"), 354, 328, 48, 64, False)
    put(r_leg_rgb, leg_layers("right", "Bottom"), 462, 328, 48, 64, False)

    # 6. Linkes Bein (x=624..720) + Caps
    put(l_leg_rgb, leg_layers("left", "Front"), 624, 64, 96, 64, True)
    put(l_leg_rgb, leg_layers("left", "Left"), 624, 128, 96, 64, True)
    put(l_leg_rgb, leg_layers("left", "Back"), 624, 192, 96, 64, True)
    put(l_leg_rgb, leg_layers("left", "Right"), 624, 256, 96, 64, True)
    put(l_leg_rgb, leg_layers("left", "Top"), 408, 328, 48, 64, False)
    put(l_leg_rgb, leg_layers("left", "Bottom"), 516, 328, 48, 64, False)

    return canvas.convert("RGB")


def _draw_fallback_classic_smile(size: int = 512) -> Image.Image:
    """Zeichnet das klassische Roblox-Standardgesicht als Fallback."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Zwei klassische ovale Augen
    ew, eh = int(size * 0.065), int(size * 0.145)
    lx, rx = int(size * 0.37), int(size * 0.63)
    ey = int(size * 0.38)
    d.ellipse([lx - ew, ey - eh, lx + ew, ey + eh], fill=(20, 20, 20, 255))
    d.ellipse([rx - ew, ey - eh, rx + ew, ey + eh], fill=(20, 20, 20, 255))
    # Klassischer Lächel-Bogen
    bbox = [int(size * 0.30), int(size * 0.38), int(size * 0.70), int(size * 0.72)]
    d.arc(bbox, start=18, end=162, fill=(20, 20, 20, 255), width=max(6, int(size * 0.032)))
    return img


def _extract_body_colors(meta: dict[str, Any]) -> dict[str, tuple[int, int, int]]:
    c3 = meta.get("bodyColor3s") or {}
    bc = meta.get("bodyColors") or {}

    def resolve(hex_key: str, id_key: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
        rgb = hex_to_rgb(c3.get(hex_key))
        if rgb is not None:
            return rgb
        return brick_color_to_rgb(bc.get(id_key), default=fallback)

    return {
        "head": resolve("headColor3", "headColorId", (245, 205, 48)),
        "torso": resolve("torsoColor3", "torsoColorId", (13, 105, 172)),
        "rightArm": resolve("rightArmColor3", "rightArmColorId", (245, 205, 48)),
        "leftArm": resolve("leftArmColor3", "leftArmColorId", (245, 205, 48)),
        "rightLeg": resolve("rightLegColor3", "rightLegColorId", (164, 189, 71)),
        "leftLeg": resolve("leftLegColor3", "leftLegColorId", (164, 189, 71)),
    }


def _mesh_bbox_size(mesh: MeshData) -> tuple[float, float, float]:
    if not mesh.positions:
        return (1.0, 1.0, 1.0)
    arr = np.asarray(mesh.positions, dtype=np.float64)
    ext = arr.max(axis=0) - arr.min(axis=0)
    return (
        float(ext[0]) if ext[0] > 1e-6 else 1.0,
        float(ext[1]) if ext[1] > 1e-6 else 1.0,
        float(ext[2]) if ext[2] > 1e-6 else 1.0,
    )


def _compute_meshpart_scale(node: RbxNode, mesh: MeshData) -> tuple[float, float, float]:
    size = node.prop("Size", "size")
    init_size = node.prop("InitialSize")
    if size is None:
        return (1.0, 1.0, 1.0)
    if init_size is None:
        init_size = _mesh_bbox_size(mesh)
    sx = float(size[0]) / float(init_size[0]) if abs(float(init_size[0])) > 1e-6 else 1.0
    sy = float(size[1]) / float(init_size[1]) if abs(float(init_size[1])) > 1e-6 else 1.0
    sz = float(size[2]) / float(init_size[2]) if abs(float(init_size[2])) > 1e-6 else 1.0
    return (sx, sy, sz)


def _absolute_cframe(node: RbxNode) -> tuple[float, ...]:
    """CFrame eines Knotens relativ zur Wurzel des Assets.

    Roblox-CFrames eines Parts sind relativ zum Elternteil. Fuer Koerperteil-
    Assets liegen die Parts in einem Ordner ("R15Fixed"/"R15ArtistIntent"),
    dessen Transform die Wurzel des Assets bildet.
    """
    chain: list[RbxNode] = []
    current: RbxNode | None = node
    while current is not None:
        chain.append(current)
        current = current.parent
    cframe: tuple[float, ...] = IDENTITY_CFRAME
    for item in reversed(chain):
        cframe = compose_cframes(cframe, item.prop("CFrame", default=IDENTITY_CFRAME))
    return cframe


def _rig_alignment_offset(nodes: list[RbxNode]) -> tuple[float, float, float]:
    """Berechnet den Versatz eines Assets zum offiziellen R15Fixed-Rig.

    Roblox haengt ein Koerperteil-Asset ueber dessen Rig-Attachments ein:
    die "Rig"-Attachments eines Assets muessen auf den Positionen der
    offiziellen ``R15FixedRigAttachmentMap`` landen (Roblox-Client-Plugin
    "AssetImporter"). Viele Assets (z. B. aus R6 konvertierte Pakete) liegen
    dabei um einen festen Betrag versetzt - die Teile sassen dann sichtbar
    neben dem Avatar. Der Versatz wird als Median ueber alle passenden
    Attachments bestimmt, damit ein einzelnes abweichendes Attachment den
    Versatz nicht verschiebt.
    """
    deltas: list[tuple[float, float, float]] = []
    for node in nodes:
        part_cframe = _absolute_cframe(node)
        for attachment in node.children:
            if attachment.class_name != "Attachment":
                continue
            target = R15_FIXED_ATTACHMENT_POSITIONS.get(attachment.name)
            if target is None:
                continue
            position = cframe_position(
                compose_cframes(part_cframe, attachment.prop("CFrame", default=IDENTITY_CFRAME))
            )
            deltas.append((target[0] - position[0], target[1] - position[1], target[2] - position[2]))
    if not deltas:
        return (0.0, 0.0, 0.0)
    offset = []
    for axis in range(3):
        values = sorted(delta[axis] for delta in deltas)
        offset.append(float(values[len(values) // 2]))
    return (offset[0], offset[1], offset[2])


def _character_mesh_texture(roots: list[RbxNode]) -> int | None:
    """Textur eines klassischen CharacterMesh im Asset (R6-Ordner).

    Klassische Koerperteil-Pakete (z. B. "Roblox 2.0") tragen ihre Textur
    nicht am MeshPart, sondern am CharacterMesh des Assets. Ohne diese Textur
    fallen die Teile auf die flache BodyColor zurueck und wirken braun.
    """
    for root in roots:
        for node in [root] + root.descendants():
            if node.class_name != "CharacterMesh":
                continue
            texture_id = extract_asset_id(
                node.prop("BaseTextureId", "OverrideTextureId", "TextureId", "TextureID")
            )
            if texture_id:
                return texture_id
    return None


def _load_r15_body_segments(
    client: Any, roots: list[RbxNode], asset_id: int, asset_type_id: int,
    names: tuple[str, ...], work_dir: Path,
) -> dict[str, BodyPartSpec]:
    """Laedt benannte R15-Segmente aus einem Koerperteil-Asset.

    Quellen-Prioritaet: "R15Fixed" (der kanonische Roblox-Rig-Stand) vor
    "R15ArtistIntent" (die vom Kuenstler gewaehlte Pose). "R6" (klassisch)
    und "R15" (Studio-Vorschau, 2 Studs zu hoch) werden nie verwendet.

    Die Positionen der Teile ergeben sich aus zwei Roblox-Regeln:
      1. Der Teil sitzt an seinem authored CFrame im Ordner des Assets.
      2. Das Asset haengt ueber seine Rig-Attachments am Standard-Rig ein.
         Weicht das Asset davon ab, wird der gesamte Versatz berechnet
         (:func:`_rig_alignment_offset`) und auf alle Teile angewendet.
    """
    candidates: dict[str, tuple[int, RbxNode]] = {}

    def visit(node: RbxNode, context: str = "") -> None:
        marker = node.name.casefold()
        if marker in ("r6", "r15artistintent", "r15fixed", "r15"):
            context = marker
        # "R6" ist die klassische Variante, "R15" die Studio-Vorschau (dort
        # liegen die Teile 2 Studs zu hoch). Beide werden nicht verwendet.
        if context not in ("r6", "r15") and node.name in names and node.class_name in ("MeshPart", "Part"):
            priority = {"r15fixed": 4, "r15artistintent": 3, "": 1}.get(context, 0)
            if node.name not in candidates or priority > candidates[node.name][0]:
                candidates[node.name] = (priority, node)
        for child in node.children:
            visit(child, context)

    for root in roots:
        visit(root)

    chosen = [node for _, node in candidates.values()]
    align_offset = _rig_alignment_offset(chosen)
    if any(abs(v) > 1e-6 for v in align_offset):
        print(
            f"[body_part] Asset {asset_id}: Rig-Versatz zurueck auf das Standard-Rig "
            f"({align_offset[0]:.3f}, {align_offset[1]:.3f}, {align_offset[2]:.3f})."
        )
    character_texture = _character_mesh_texture(roots)

    result: dict[str, BodyPartSpec] = {}
    for name, (_, node) in candidates.items():
        try:
            sm = node.find_first_child_of_class("SpecialMesh") if node.class_name == "Part" else None
            source = sm or node
            mid = extract_asset_id(source.prop("MeshId", "MeshContent"))
            if not mid:
                continue
            mesh = parse_rbx_mesh(client.fetch_asset_bytes(mid))
            if not mesh.positions or not mesh.faces:
                continue
            scale = _compute_meshpart_scale(node, mesh) if sm is None else tuple(float(v) for v in sm.prop("Scale", default=(1, 1, 1)))
            offset = (0.0, 0.0, 0.0) if sm is None else tuple(float(v) for v in sm.prop("Offset", default=(0, 0, 0)))
            sa = node.find_first_child_of_class("SurfaceAppearance")
            tex_id = extract_asset_id(sa.prop("ColorMap", "ColorMapContent")) if sa else None
            if not tex_id:
                tex_id = extract_asset_id(source.prop("TextureID", "TextureId", "TextureContent"))
            if not tex_id:
                # Klassische Pakete: die Textur haengt am CharacterMesh des Assets.
                tex_id = character_texture
            alpha_mode = "transparency"
            tint = (1.0, 1.0, 1.0)
            if sa is not None:
                try:
                    alpha_mode = ALPHA_MODE_NAMES.get(int(sa.prop("AlphaMode", default=0) or 0), "overlay")
                except (TypeError, ValueError):
                    alpha_mode = "overlay"
                color = sa.prop("Color")
                if color is not None and len(tuple(color)) >= 3:
                    tint = tuple(float(c) for c in tuple(color)[:3])
            texture_path = None
            if tex_id:
                try:
                    texture_path = str(work_dir / f"bodypart_{asset_id}_{name}_{tex_id}.png")
                    client.fetch_image(tex_id).save(texture_path, format="PNG")
                except Exception as exc:
                    print(f"[body_part] Textur {tex_id} fuer {name} nicht verfuegbar: {exc}")
                    texture_path = None
            if texture_path is None:
                # Klassische Pakete ("Roblox 2.0") bringen keine eigene Textur
                # mit. Ihre Meshes zeigen im Roblox-Client die klassische
                # Koerpertextur (BodyColors + Classic-Kleidung). Der Renderer
                # malt diese Textur direkt auf die UVs des Meshs.
                print(
                    f"[body_part] {name} von Asset {asset_id} hat keine eigene Textur "
                    "- es wird die klassische Koerpertextur verwendet."
                )
            part_cframe = translate_cframe(_absolute_cframe(node), align_offset)
            result[name] = BodyPartSpec(
                asset_id=asset_id, asset_type_id=asset_type_id, mesh=mesh,
                mesh_scale=scale, mesh_offset=offset,
                cframe=part_cframe,
                transparency=float(node.prop("Transparency", default=0.0) or 0.0),
                texture_path=texture_path,
                tint_color=tint,
                alpha_mode=alpha_mode,
            )
        except Exception as exc:
            print(f"[body_part] Segment {name} von {asset_id} konnte nicht geladen werden: {exc}")
    return result


def load_avatar_bundle(
    username: str,
    api_key: str,
    work_dir: Path,
    gfx_type: str = "HearthandsType",
    client: RobloxOpenCloudClient | None = None,
) -> AvatarBundle:
    """Lädt den kompletten Avatar eines Roblox-Nutzers über die Open Cloud API.

    Für gfx_type="ClassicType" wird der Avatar als R15 komplett von der Open
    Cloud v4 Avatar API geladen (Farben, Skalierung, getragene Assets). Layered
    Clothing (Asset-Typen 64-72) sowie Accessoires vom Typ "Equipment" werden
    nicht geladen.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    client = client or RobloxOpenCloudClient(api_key)
    client.validate_api_key()

    user_id, canonical_name, display_name = client.resolve_user(username)
    if gfx_type == "ClassicType":
        meta = client.fetch_avatar_metadata_v4(user_id)
        scales_raw = meta.get("scales") or {}
        scales = {str(k): float(v) for k, v in scales_raw.items() if isinstance(v, (int, float))}
        player_avatar_type = meta.get("playerAvatarType")
        if isinstance(player_avatar_type, bool):
            player_avatar_type = None
        elif player_avatar_type is not None:
            try:
                player_avatar_type = int(player_avatar_type)
            except (TypeError, ValueError):
                player_avatar_type = None
    else:
        meta = client.fetch_avatar_metadata(user_id)
        scales = {}
        player_avatar_type = None
    body_colors = _extract_body_colors(meta)

    assets = meta.get("assets") or []
    by_type: dict[int, list[dict[str, Any]]] = {}
    for item in assets:
        atype = int((item.get("assetType") or {}).get("id") or 0)
        by_type.setdefault(atype, []).append(item)

    # 1. Kleidung laden & R6 Körpertextur zusammensetzen
    shirt_img = client.fetch_image(int(by_type[11][0]["id"])) if by_type.get(11) else None
    pants_img = client.fetch_image(int(by_type[12][0]["id"])) if by_type.get(12) else None
    tshirt_img = client.fetch_image(int(by_type[2][0]["id"])) if by_type.get(2) else None

    body_tex = compose_r6_body_texture(body_colors, shirt_img, pants_img, tshirt_img, scale=2)
    body_tex_path = str(work_dir / "r6_body_texture.png")
    body_tex.save(body_tex_path, format="PNG")

    # 2. Gesichts-Textur laden
    face_img: Image.Image | None = None
    if by_type.get(18):
        face_img = client.fetch_image(int(by_type[18][0]["id"]))
    else:
        try:
            face_img = client.fetch_image(DEFAULT_SMILE_ASSET_ID)
        except Exception:
            face_img = _draw_fallback_classic_smile(512)

    face_tex_path = str(work_dir / "face_texture.png")
    face_img.save(face_tex_path, format="PNG")
    alpha_info = "kein Alpha"
    if face_img.mode in ("RGBA", "LA", "PA"):
        a_min, a_max = face_img.getchannel("A").getextrema()
        alpha_info = f"Alpha {a_min}..{a_max}"
    print(
        f"[face] {face_img.size[0]}x{face_img.size[1]} {face_img.mode} ({alpha_info}) "
        f"Kopf-Hautfarbe={tuple(body_colors['head'])}"
    )

    # 2b. ClassicType: pro R15-Teil eine passende Classic-Clothing-Textur.
    #     Der R6-Atlas oben bleibt unveraendert fuer HearthandsType.
    r15_part_textures: dict[str, str] = {}
    r15_clothing_overlays: dict[str, str] = {}
    if gfx_type == "ClassicType":
        r15_clothing_overlays = compose_r15_part_textures(
            body_colors, shirt_img, pants_img, tshirt_img, face_img, work_dir, transparent_base=True
        )
        r15_part_textures = compose_r15_part_textures(
            body_colors, shirt_img, pants_img, tshirt_img, face_img, work_dir
        )
        print(f"[classic clothing] {len(r15_part_textures)} R15-Teiltexturen erzeugt.")

    # 3. Kopf laden (Classic Head, Custom SpecialMesh Head, Headless oder DynamicHead)
    head_spec = HeadSpec(
        kind="classic_head",
        skin_rgb=body_colors["head"],
        face_texture_path=face_tex_path,
    )
    head_asset_item = (by_type.get(17) or by_type.get(79) or [None])[0]
    if head_asset_item is not None:
        head_aid = int(head_asset_item["id"])
        raw_head = client.fetch_asset_bytes(head_aid)
        try:
            roots = parse_rbx_model(raw_head)
            nodes = [n for r in roots for n in ([r] + r.descendants())]
            # Dynamic heads sind MeshParts mit eigener, UV-gebundener Gesichts-/Kopftextur.
            mp = next((n for n in nodes if n.class_name == "MeshPart"), None)
            sm = next((n for n in nodes if n.class_name == "SpecialMesh"), None)
            if mp is not None:
                trans = float(mp.prop("Transparency", default=0.0) or 0.0)
                mid = extract_asset_id(mp.prop("MeshId", "MeshContent"))
                if trans >= 0.98:
                    head_spec.kind = "headless"
                elif mid:
                    mdata = parse_rbx_mesh(client.fetch_asset_bytes(mid))
                    mscale = _compute_meshpart_scale(mp, mdata)
                    ext = _mesh_bbox_size(mdata)
                    if max(ext[0] * mscale[0], ext[1] * mscale[1], ext[2] * mscale[2]) < 0.08:
                        head_spec.kind = "headless"
                    else:
                        head_spec.kind = "dynamic_head"
                        head_spec.mesh = mdata
                        head_spec.mesh_scale = mscale
                        sa = mp.find_first_child_of_class("SurfaceAppearance")
                        tid = extract_asset_id(
                            (sa.prop("ColorMap", "ColorMapContent") if sa else None)
                            or mp.prop("TextureID", "TextureId", "TextureContent")
                        )
                        if tid:
                            himg = client.fetch_image(tid)
                            hp = str(work_dir / f"head_tex_{tid}.png")
                            himg.save(hp, format="PNG")
                            head_spec.custom_texture_path = hp
                            print(
                                f"[head] Dynamic-Head-Textur {tid} aktiv; "
                                "Mesh-UVs und eingebautes Gesicht bleiben erhalten."
                            )
            elif sm is not None:
                mtype = sm.prop("MeshType", default=0)
                sscale = sm.prop("Scale", default=(1.25, 1.25, 1.25))
                soff = sm.prop("Offset", default=(0.0, 0.0, 0.0))
                mid = extract_asset_id(sm.prop("MeshId", "MeshContent"))
                if (mtype == 5 or mid) and mid:
                    mdata = parse_rbx_mesh(client.fetch_asset_bytes(mid))
                    ext = _mesh_bbox_size(mdata)
                    if max(ext[0] * abs(sscale[0]), ext[1] * abs(sscale[1]), ext[2] * abs(sscale[2])) < 0.08:
                        head_spec.kind = "headless"
                    else:
                        head_spec.kind = "custom_mesh"
                        head_spec.mesh = mdata
                        head_spec.mesh_scale = (float(sscale[0]), float(sscale[1]), float(sscale[2]))
                        head_spec.mesh_offset = (float(soff[0]), float(soff[1]), float(soff[2]))
                        tid = extract_asset_id(sm.prop("TextureId", "TextureID", "TextureContent"))
                        if tid:
                            himg = client.fetch_image(tid)
                            hp = str(work_dir / f"head_tex_{tid}.png")
                            himg.save(hp, format="PNG")
                            head_spec.custom_texture_path = hp
        except PipelineError:
            raise
        except Exception:
            # Falls es direkt eine .mesh Datei war
            if raw_head.startswith(b"version "):
                mdata = parse_rbx_mesh(raw_head)
                ext = _mesh_bbox_size(mdata)
                if max(ext) < 0.08:
                    head_spec.kind = "headless"
                else:
                    head_spec.kind = "custom_mesh"
                    head_spec.mesh = mdata

    # Jedes R15-Segment besitzt ein eigenes Mesh und eigene Materialdaten.
    # Fehler einzelner Segmente duerfen die uebrigen nicht unterdruecken.
    body_parts: dict[str, BodyPartSpec] = {}
    if gfx_type == "ClassicType":
        for item in assets:
            atype_id = int((item.get("assetType") or {}).get("id") or 0)
            names = ("Head",) if atype_id == 17 else BODY_PART_ASSET_TYPES.get(atype_id, ())
            if not names:
                continue
            aid = int(item["id"])
            try:
                roots = parse_rbx_model(client.fetch_asset_bytes(aid))
                body_parts.update(
                    _load_r15_body_segments(client, roots, aid, atype_id, names, work_dir)
                )
            except Exception as exc:
                print(f"[body_part] Asset {aid} konnte nicht geladen werden: {exc}")

    # 4. Starre R6-Accessoires laden (ClassicType: keine Layered Clothing und
    # keine Accessoires vom Typ "Equipment").
    accessories: list[AccessorySpec] = []
    for item in assets:
        atype_id = int((item.get("assetType") or {}).get("id") or 0)
        atype_name = str((item.get("assetType") or {}).get("name") or "").casefold()
        if atype_id not in RIGID_ACCESSORY_TYPE_IDS:
            continue
        if atype_name == "equipment":
            continue

        aid = int(item["id"])
        aname = str(item.get("name") or f"Accessory_{aid}")
        raw_acc = client.fetch_asset_bytes(aid)
        roots = parse_rbx_model(raw_acc)
        all_nodes = [n for r in roots for n in ([r] + r.descendants())]
        acc_node = next(
            (n for n in all_nodes if n.class_name in ("Accessory", "Hat", "Accoutrement")),
            roots[0] if roots else None,
        )
        if acc_node is None:
            continue

        handle = acc_node.find_first_child("Handle")
        if handle is None:
            handle = next(
                (n for n in all_nodes if n.name == "Handle" or n.class_name in ("MeshPart", "Part")),
                None,
            )
        if handle is None:
            continue

        transparency = float(handle.prop("Transparency", default=0.0) or 0.0)
        if transparency >= 0.99:
            continue

        att_node = handle.find_first_child_of_class("Attachment")
        att_name = att_node.name if att_node is not None else None
        att_cf = (
            tuple(float(x) for x in att_node.prop("CFrame", default=IDENTITY_CFRAME))
            if att_node is not None
            else IDENTITY_CFRAME
        )
        legacy_ap = tuple(
            float(x) for x in acc_node.prop("AttachmentPoint", default=IDENTITY_CFRAME)
        )

        mesh_data: MeshData | None = None
        mesh_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
        mesh_offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
        tint_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
        tex_id: int | None = None
        alpha_blend = False

        base_col_raw = handle.prop("Color", "Color3uint8")
        base_color: tuple[float, float, float] = (0.8, 0.8, 0.8)
        if isinstance(base_col_raw, tuple) and len(base_col_raw) == 3:
            base_color = (float(base_col_raw[0]), float(base_col_raw[1]), float(base_col_raw[2]))
        elif handle.prop("BrickColor") is not None:
            brgb = brick_color_to_rgb(int(handle.prop("BrickColor")))
            base_color = (brgb[0] / 255.0, brgb[1] / 255.0, brgb[2] / 255.0)

        if handle.class_name == "MeshPart":
            mid = extract_asset_id(handle.prop("MeshId", "MeshContent"))
            if not mid:
                continue
            mesh_data = parse_rbx_mesh(client.fetch_asset_bytes(mid))
            mesh_scale = _compute_meshpart_scale(handle, mesh_data)
            sa = handle.find_first_child_of_class("SurfaceAppearance")
            if sa is not None:
                tex_id = extract_asset_id(sa.prop("ColorMap", "ColorMapContent"))
                amode = sa.prop("AlphaMode", default=0)
                if amode == 1:  # Enum.AlphaMode.Transparency
                    alpha_blend = True
                sa_col = sa.prop("Color")
                if isinstance(sa_col, tuple) and len(sa_col) == 3:
                    tint_color = (float(sa_col[0]), float(sa_col[1]), float(sa_col[2]))
            if not tex_id:
                tex_id = extract_asset_id(
                    handle.prop("TextureID", "TextureId", "TextureContent")
                )
        else:
            sm = handle.find_first_child_of_class("SpecialMesh")
            if sm is None:
                sm = next((n for n in handle.children if "Mesh" in n.class_name), None)
            if sm is None:
                continue
            mid = extract_asset_id(sm.prop("MeshId", "MeshContent"))
            if not mid:
                continue
            mesh_data = parse_rbx_mesh(client.fetch_asset_bytes(mid))
            sc = sm.prop("Scale", default=(1.0, 1.0, 1.0))
            of = sm.prop("Offset", default=(0.0, 0.0, 0.0))
            vc = sm.prop("VertexColor", default=(1.0, 1.0, 1.0))
            mesh_scale = (float(sc[0]), float(sc[1]), float(sc[2]))
            mesh_offset = (float(of[0]), float(of[1]), float(of[2]))
            tint_color = (float(vc[0]), float(vc[1]), float(vc[2]))
            tex_id = extract_asset_id(sm.prop("TextureId", "TextureID", "TextureContent"))

        tex_path: str | None = None
        if tex_id:
            timg = client.fetch_image(tex_id)
            tex_path = str(work_dir / f"acc_{aid}_tex_{tex_id}.png")
            timg.save(tex_path, format="PNG")

        # Face-/Eyewear-Accessories bleiben Meshes. Ein dünner Bounding-Box-
        # Anteil ist kein Beleg für ein 2D-Decal: Brillen haben dieselbe
        # Geometrie-Eigenschaft und müssen mit ihrer eigenen UV-Textur erhalten
        # bleiben.

        meta_info = item.get("meta") or {}
        mpos = meta_info.get("position") or {}
        mrot = meta_info.get("rotation") or {}
        msca = meta_info.get("scale") or {}

        accessories.append(
            AccessorySpec(
                asset_id=aid,
                name=aname,
                asset_type_id=atype_id,
                mesh=mesh_data,
                mesh_scale=mesh_scale,
                mesh_offset=mesh_offset,
                attachment_name=att_name,
                attachment_cframe=att_cf,
                legacy_attachment_point=legacy_ap,
                texture_path=tex_path,
                tint_color=tint_color,
                base_color=base_color,
                alpha_blend=alpha_blend,
                transparency=transparency,
                meta_pos=(
                    float(mpos.get("X", 0.0)),
                    float(mpos.get("Y", 0.0)),
                    float(mpos.get("Z", 0.0)),
                ),
                meta_rot_deg=(
                    float(mrot.get("X", 0.0)),
                    float(mrot.get("Y", 0.0)),
                    float(mrot.get("Z", 0.0)),
                ),
                meta_scale=(
                    float(msca.get("X", 1.0)),
                    float(msca.get("Y", 1.0)),
                    float(msca.get("Z", 1.0)),
                ),
            )
        )

    return AvatarBundle(
        username=canonical_name,
        display_name=display_name,
        user_id=user_id,
        body_texture_path=body_tex_path,
        body_colors=body_colors,
        head=head_spec,
        accessories=accessories,
        scales=scales,
        player_avatar_type=player_avatar_type,
        r15_part_textures=r15_part_textures,
        body_parts=body_parts,
        r15_clothing_overlays=r15_clothing_overlays,
    )
