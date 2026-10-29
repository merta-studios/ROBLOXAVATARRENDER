"""Haupt-Orchestrator für den Roblox Avatar GFX Render-Workflow.

Unterstützt zwei vollständig getrennte GFX-Typen:

* ``HearthandsType`` (Preppy Hearthands Type): Rendert auf Basis von
  ``WomanRig.blend`` (R6-Rig) mit optionalem Korblox-Bein und Headless-Look.
* ``ClassicType``: Rendert den R15-Avatar in einer eigenen Szene (siehe
  ``scripts/classic_render.py``) -- komplett ohne ``WomanRig.blend``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

# Stelle sicher, dass das Repository-Root im Python-Pfad liegt
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image

from scripts.roblox_opencloud import (
    AvatarBundle,
    EMOTE_DIAGNOSTICS,
    PipelineError,
    _sanitize_secret,
    RobloxOpenCloudClient,
    load_avatar_bundle,
)
from scripts.render_config import (
    CLASSIC_POSES,
    gfx_type_from_config_path,
    read_classic_config,
    read_config,
    resolve_background,
)


def verify_rendered_png(png_path: Path, *, expect_transparency: bool = True) -> None:
    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise PipelineError("Das gerenderte PNG fehlt oder ist 0 Bytes groß.")
    img = Image.open(png_path)
    img.load()
    if img.mode != "RGBA":
        raise PipelineError(f"Erwartetes RGBA-Bild mit transparentem Hintergrund, erhalten: {img.mode}.")
    alpha_min, alpha_max = img.getchannel("A").getextrema()
    if alpha_max == 0:
        raise PipelineError(
            "Das gerenderte Bild ist vollständig transparent (leer). Prüfe Kamera/OpenGL-Kontext."
        )
    if expect_transparency and alpha_min == 255:
        raise PipelineError(
            "Der Hintergrund sollte transparent sein, der Alpha-Kanal ist jedoch durchgehend deckend."
        )


def embed_diagnostics(png_path: Path, payload: Any) -> None:
    """TEMPORAERE DIAGNOSE: schreibt Emote-Infos in die PNG-Metadaten."""
    if not payload:
        return
    import base64
    import gzip

    from PIL import PngImagePlugin

    blob = base64.b64encode(gzip.compress(json.dumps(payload).encode("utf-8"))).decode("ascii")
    img = Image.open(png_path)
    img.load()
    info = PngImagePlugin.PngInfo()
    info.add_text("rbxdiag", blob)
    img.save(png_path, format="PNG", pnginfo=info)
    print(f"[diag] Diagnose-Daten ({len(blob)} Bytes) in die PNG-Metadaten geschrieben.")


def render_hearthands(
    *,
    config: dict[str, Any],
    bundle: AvatarBundle,
    client: RobloxOpenCloudClient,
    work_dir: Path,
    output_png: Path,
    blend_path: Path,
    background_kind: str,
    background_path: str | None,
    background_value: str,
) -> None:
    """Rendert den HearthandsType-GFX (R6, WomanRig.blend, korblox, headless)."""
    from scripts.blender_render import render_avatar_scene

    korblox_asset: dict[str, Any] | None = None
    if bool(config["korblox"]):
        try:
            korblox_asset = client.load_korblox_leg(work_dir)
        except PipelineError as exc:
            raise PipelineError(str(exc)) from exc

    render_avatar_scene(
        blend_path=blend_path,
        bundle=bundle,
        output_png=output_png,
        work_dir=work_dir,
        material_finish=config["material"],
        background_kind=background_kind,
        background_path=background_path,
        background_value=background_value,
        korblox=bool(config["korblox"]),
        headless=bool(config["headless"]),
        korblox_asset=korblox_asset,
    )


def resolve_classic_pose(
    *,
    config: dict[str, Any],
    bundle: AvatarBundle,
    client: RobloxOpenCloudClient,
    work_dir: Path,
) -> tuple[str, Any, int]:
    """Ermittelt ClassicType-Pose und Emote-Asset -- ohne Blender-Abhaengigkeit.

    Bei ``pose = "None"`` werden WEDER ``fetch_equipped_emote`` NOCH
    ``load_emote_animation`` aufgerufen. Bei ``"Equipped"`` wird die
    ausgeruestete Profilpose ueber die API ermittelt und anschliessend wie ein
    normales Emote geladen; bei ``"Emote"`` gilt ``emote_asset_id``.
    """
    pose = str(config.get("pose", "None"))
    if pose not in CLASSIC_POSES:
        raise PipelineError(
            f"Ungueltige ClassicType-Pose {pose!r}. Erlaubt: {', '.join(CLASSIC_POSES)}."
        )
    emote_asset_id = int(config.get("emote_asset_id", 0) or 0)
    classic_emote: Any = None

    if pose == "Equipped":
        equipped = client.fetch_equipped_emote(bundle.user_id)
        if equipped and equipped.get("emote_asset_id"):
            emote_asset_id = int(equipped["emote_asset_id"])
            print(f"[pose] Ausgeruestete Profilpose (Emote {emote_asset_id}) wurde verwendet.")
        else:
            print("[pose] Keine ausgeruestete Profilpose gefunden - Pose bleibt 'None'.")
            pose = "None"

    if pose in ("Equipped", "Emote") and emote_asset_id > 0:
        classic_emote = client.load_emote_animation(emote_asset_id, work_dir)
        if classic_emote is None:
            print(f"[pose] Emote {emote_asset_id} konnte nicht geladen werden - Pose bleibt 'None'.")
            pose = "None"
    elif pose == "Emote":
        print("[pose] Keine gueltige emote_asset_id - Pose bleibt 'None'.")
        pose = "None"

    return pose, classic_emote, emote_asset_id


def render_classic(
    *,
    config: dict[str, Any],
    bundle: AvatarBundle,
    client: RobloxOpenCloudClient,
    work_dir: Path,
    output_png: Path,
    background_kind: str,
    background_path: str | None,
    background_value: str,
) -> None:
    """Rendert den ClassicType-GFX (R15, eigene Szene, kein WomanRig.blend)."""
    from scripts.classic_render import render_classic_scene

    classic_pose, classic_emote, _emote_asset_id = resolve_classic_pose(
        config=config,
        bundle=bundle,
        client=client,
        work_dir=work_dir,
    )

    render_classic_scene(
        bundle=bundle,
        output_png=output_png,
        work_dir=work_dir,
        material_finish=config["material"],
        background_kind=background_kind,
        background_path=background_path,
        background_value=background_value,
        classic_pose=classic_pose,
        classic_emote=classic_emote,
        classic_rotation_deg=float(config.get("rotation_deg", 0)),
        classic_abstand_oben=float(config.get("abstand_oben", 0)),
        classic_abstand_unten=float(config.get("abstand_unten", 0)),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rendert einen Roblox Avatar (HearthandsType oder ClassicType).")
    parser.add_argument("--config-file", default="HearthandsType/configs.toml", help="Pfad zur TOML-Konfiguration. Der übergeordnete Ordner bestimmt den GFX-Typ.")
    parser.add_argument("--blend-file", default="WomanRig.blend", help="Pfad zur WomanRig.blend Datei (nur HearthandsType).")
    parser.add_argument("--output", default="render/latest.png", help="Zielpfad für das gerenderte transparente PNG.")
    parser.add_argument("--status-json", default="/tmp/render_status.json", help="Pfad für den strukturierten Ergebnis-Report.")
    parser.add_argument("--gfx-type", default=None, help="GFX-Typ: 'HearthandsType' oder 'ClassicType'. Wird aus dem Konfigurations-Ordner abgeleitet, falls nicht angegeben.")
    args = parser.parse_args()

    api_key = os.environ.get("ROBLOX_API_KEY", "")
    status_path = Path(args.status_json)
    output_path = (REPO_ROOT / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    blend_path = (REPO_ROOT / args.blend_file).resolve() if not Path(args.blend_file).is_absolute() else Path(args.blend_file)
    config_file = (REPO_ROOT / args.config_file).resolve() if not Path(args.config_file).is_absolute() else Path(args.config_file)

    try:
        if not config_file.is_file():
            raise PipelineError(f"Die Konfigurationsdatei '{config_file.name}' wurde nicht gefunden.")

        try:
            gfx_type_arg = args.gfx_type or None
            gfx_type = gfx_type_from_config_path(config_file, gfx_type_arg)
            if gfx_type == "ClassicType":
                config = read_classic_config(config_file)
            else:
                config = read_config(config_file)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc

        username = os.environ.get("INPUT_USERNAME", "").strip() or config["username"]
        # Normalisierter Konfigurationswert fuer Hintergrundfarben. Die
        # Renderer erhalten zusaetzlich den von resolve_background() ermittelten
        # Typ und (bei Bildern/URLs) den konkreten Dateipfad.
        background_value = str(config["background"])

        client = RobloxOpenCloudClient(api_key)

        with tempfile.TemporaryDirectory(prefix="rbx_gfx_") as tmp:
            work_dir = Path(tmp)
            try:
                background_kind, background_path = resolve_background(background_value, work_dir)
            except ValueError as exc:
                raise PipelineError(str(exc)) from exc

            bundle = load_avatar_bundle(
                username=username,
                api_key=api_key,
                work_dir=work_dir,
                gfx_type=gfx_type,
                client=client,
            )

            if gfx_type == "ClassicType":
                render_classic(
                    config=config,
                    bundle=bundle,
                    client=client,
                    work_dir=work_dir,
                    output_png=output_path,
                    background_kind=background_kind,
                    background_path=background_path,
                    background_value=background_value,
                )
            else:
                if not blend_path.is_file():
                    raise PipelineError(f"Die Blender-Datei '{blend_path.name}' wurde nicht gefunden.")
                render_hearthands(
                    config=config,
                    bundle=bundle,
                    client=client,
                    work_dir=work_dir,
                    output_png=output_path,
                    blend_path=blend_path,
                    background_kind=background_kind,
                    background_path=background_path,
                    background_value=background_value,
                )

        verify_rendered_png(output_path, expect_transparency=(background_kind == "transparent"))
        # TEMPORAERE DIAGNOSE
        try:
            embed_diagnostics(output_path, EMOTE_DIAGNOSTICS)
        except Exception as exc:  # pragma: no cover
            print(f"[diag] Diagnose konnte nicht geschrieben werden: {exc}")

        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "gfx_type": gfx_type,
                    "username": bundle.username,
                    "display_name": bundle.display_name,
                    "user_id": bundle.user_id,
                    "accessories_count": len(bundle.accessories),
                    "scales": getattr(bundle, "scales", {}),
                    "player_avatar_type": getattr(bundle, "player_avatar_type", None),
                    "output_png": str(output_path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return 0

    except PipelineError as exc:
        err_msg = _sanitize_secret(str(exc), api_key)
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps({"ok": False, "error": err_msg}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"RENDER_ERROR: {err_msg}", file=sys.stderr)
        return 1

    except Exception as exc:
        tb = _sanitize_secret(traceback.format_exc(), api_key)
        err_msg = f"Unerwarteter Fehler in der Render-Pipeline ({type(exc).__name__}: {_sanitize_secret(str(exc), api_key)}):\n\n{tb}"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps({"ok": False, "error": err_msg}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(err_msg, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
