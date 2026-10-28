"""Load and validate the repository's commented TOML render configuration."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from PIL import Image

from scripts.background_catalog import ORIGINAL_FOLDER_BY_NAME

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKGROUND_DIR = REPO_ROOT / "backgrounds"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
HEX_COLOR = re.compile(r"^#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

DEFAULT_CONFIG: dict[str, Any] = {
    "username": "",
    "background": "transparent",
    "material": "Glossy",
    # GFX-Typ "HearthandsType" (Preppy Hearthands Type): ersetzt das rechte Bein
    # durch das Korblox Deathspeaker Right Leg (Asset-ID 139607718) und/oder
    # blendet Kopf und Gesicht-Accessoires für den Headless-Look aus.
    "korblox": False,
    "headless": False,
}

# GFX-Typen, für die es einen eigenen Konfigurationsordner gibt.
GFX_TYPES = ("HearthandsType", "ClassicType")

# ClassicType-Posen: "None" = normales Stehen, "Equipped" = am Spielerprofil
# ausgerüstete Pose, "Emote" = Pose aus einem Emote (Asset-ID).
CLASSIC_POSES = ("None", "Equipped", "Emote")


def read_config(path: Path) -> dict[str, Any]:
    """Parse and validate the commented TOML configuration used for rendering."""
    if not path.is_file():
        raise ValueError(f"Die Konfigurationsdatei '{path.name}' wurde nicht gefunden.")
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise ValueError(f"Die Konfigurationsdatei '{path.name}' ist leer.")
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Ungültiges TOML in '{path.name}': {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Die Konfiguration muss ein TOML-Objekt sein.")
    config = {**DEFAULT_CONFIG, **parsed}

    username = str(config["username"]).strip().lstrip("@").strip()
    if not username:
        raise ValueError("Bitte in configs.toml unter 'username' einen Roblox-Benutzernamen eintragen.")
    config["username"] = username

    material = str(config["material"]).strip().casefold()
    if material not in {"glossy", "matt"}:
        raise ValueError("'material' muss genau 'Glossy' oder 'Matt' sein.")
    config["material"] = "Glossy" if material == "glossy" else "Matt"

    bg = str(config["background"]).strip()
    if not bg:
        raise ValueError("'background' darf nicht leer sein; nutze 'transparent', eine lokale Datei, URL oder Hex-Farbe.")
    if bg.casefold() == "transparent":
        config["background"] = "transparent"
    elif HEX_COLOR.fullmatch(bg):
        config["background"] = bg if bg.startswith("#") else f"#{bg}"
    elif urlparse(bg).scheme:
        if urlparse(bg).scheme not in {"http", "https"} or not urlparse(bg).netloc:
            raise ValueError("Hintergrund-URLs müssen gültige http:// oder https:// URLs sein.")
        config["background"] = bg
    else:
        config["background"] = bg

    for bool_key in ("korblox", "headless"):
        if not isinstance(config[bool_key], bool):
            raise ValueError(f"'{bool_key}' muss true oder false sein.")

    return config


def read_classic_config(path: Path) -> dict[str, Any]:
    """Liest und validiert die Konfiguration des GFX-Typs 'ClassicType'.

    ClassicType lädt den Avatar als R15 komplett von der Roblox Open Cloud API
    (GET /v4/avatar/users/{userId}) und verzichtet auf Layered Clothing sowie
    Accessoires vom Typ "Equipment". Neben den gemeinsamen Einstellungen
    (username, background, material, outline) definiert es Pose, Drehung und
    den Abstand von oben/unten des fertigen Avatars im Bild.
    """
    if not path.is_file():
        raise ValueError(f"Die Konfigurationsdatei '{path.name}' wurde nicht gefunden.")
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise ValueError(f"Die Konfigurationsdatei '{path.name}' ist leer.")
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Ungültiges TOML in '{path.name}': {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Die Konfiguration muss ein TOML-Objekt sein.")
    config = {**DEFAULT_CONFIG, **parsed}

    username = str(config["username"]).strip().lstrip("@").strip()
    if not username:
        raise ValueError("Bitte in configs.toml unter 'username' einen Roblox-Benutzernamen eintragen.")
    config["username"] = username

    material = str(config["material"]).strip().casefold()
    if material not in {"glossy", "matt"}:
        raise ValueError("'material' muss genau 'Glossy' oder 'Matt' sein.")
    config["material"] = "Glossy" if material == "glossy" else "Matt"

    bg = str(config["background"]).strip()
    if not bg:
        raise ValueError("'background' darf nicht leer sein; nutze 'transparent', eine lokale Datei, URL oder Hex-Farbe.")
    if bg.casefold() == "transparent":
        config["background"] = "transparent"
    elif HEX_COLOR.fullmatch(bg):
        config["background"] = bg if bg.startswith("#") else f"#{bg}"
    elif urlparse(bg).scheme:
        if urlparse(bg).scheme not in {"http", "https"} or not urlparse(bg).netloc:
            raise ValueError("Hintergrund-URLs müssen gültige http:// oder https:// URLs sein.")
        config["background"] = bg
    else:
        config["background"] = bg

    pose = str(config.get("pose", "None")).strip()
    if pose not in CLASSIC_POSES:
        raise ValueError(
            f"'pose' muss einer von {', '.join(CLASSIC_POSES)} sein, erhalten: {pose!r}."
        )
    config["pose"] = pose

    try:
        emote_asset_id = int(config.get("emote_asset_id", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("'emote_asset_id' muss eine ganze Zahl sein.") from exc
    if emote_asset_id < 0:
        raise ValueError("'emote_asset_id' darf nicht negativ sein.")
    config["emote_asset_id"] = emote_asset_id

    try:
        rotation_deg = float(config.get("rotation_deg", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("'rotation_deg' muss eine Zahl sein (Grad).") from exc
    if not -180.0 <= rotation_deg <= 180.0:
        raise ValueError("'rotation_deg' muss zwischen -180 und 180 liegen.")
    config["rotation_deg"] = rotation_deg

    for key in ("abstand_oben", "abstand_unten"):
        try:
            value = float(config.get(key, 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"'{key}' muss eine Zahl sein (Prozent der Avatar-Höhe).") from exc
        config[key] = value

    return config


def gfx_type_from_config_path(config_file: Path, gfx_type: str | None) -> str:
    """Ermittelt den GFX-Typ: erst das explizite Argument, sonst den Namen des
    übergeordneten Ordners der Konfigurationsdatei, falls dieser einem bekannten
    GFX-Typ entspricht, andernfalls 'HearthandsType'."""
    if gfx_type:
        if gfx_type not in GFX_TYPES:
            raise ValueError(
                f"Unbekannter GFX-Typ '{gfx_type}'. Erlaubt: {', '.join(GFX_TYPES)}."
            )
        return gfx_type
    parent = config_file.parent.name
    if parent in GFX_TYPES:
        return parent
    return "HearthandsType"


def normalize_color(value: str) -> tuple[int, int, int]:
    value = value.strip().casefold()
    if value == "white":
        return (255, 255, 255)
    if value == "black":
        return (0, 0, 0)
    if not HEX_COLOR.fullmatch(value):
        raise ValueError("Farben müssen Hex (z. B. #ff8800) oder 'white'/'black' sein.")
    raw = value.lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    return tuple(bytes.fromhex(raw))  # type: ignore[return-value]


def resolve_background(value: str, work_dir: Path) -> tuple[str, str | None]:
    """Return ('transparent'|'image'|'color', path-or-None); download URL images safely."""
    if value == "transparent":
        return "transparent", None
    if HEX_COLOR.fullmatch(value):
        return "color", value
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        try:
            response = requests.get(value, timeout=(15, 60), stream=True)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            ext = Path(parsed.path).suffix.lower()
            if ext not in IMAGE_EXTENSIONS:
                ext = ".png" if content_type == "image/png" else ".jpg"
            target = work_dir / f"background{ext}"
            size = 0
            with target.open("wb") as file:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > 50 * 1024 * 1024:
                        raise ValueError("Hintergrundbild ist größer als 50 MB.")
                    file.write(chunk)
            with Image.open(target) as image:
                image.verify()
            return "image", str(target)
        except Exception as exc:
            raise ValueError(f"Hintergrund-URL konnte nicht als Bild geladen werden: {exc}") from exc

    # Color folders are part of the selector (e.g. "blau/kristallsplitter").
    # A bare design name remains accepted when it matches exactly one image.
    requested = value.strip().replace("\\", "/").strip("/").casefold()
    has_folder = "/" in requested
    candidates = (
        [p for p in BACKGROUND_DIR.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
        if BACKGROUND_DIR.is_dir() else []
    )
    matches = []
    for path in candidates:
        relative = path.relative_to(BACKGROUND_DIR).as_posix().casefold()
        relative_stem = relative[: -len(path.suffix)]
        if has_folder:
            if requested in {relative, relative_stem}:
                matches.append(path)
        elif requested in {path.name.casefold(), path.stem.casefold()}:
            matches.append(path)
    if len(matches) == 1:
        return "image", str(matches[0])
    if len(matches) > 1:
        # A bare design name means its original uploaded palette. Explicit
        # "color/design" values continue to select a particular recolor.
        if not has_folder:
            design_name = Path(requested).stem.casefold()
            original_folder = ORIGINAL_FOLDER_BY_NAME.get(design_name)
            original_matches = [
                path for path in matches
                if original_folder
                and path.parent.relative_to(BACKGROUND_DIR).as_posix().casefold() == original_folder
                and path.stem.casefold() == design_name
            ]
            if len(original_matches) == 1:
                return "image", str(original_matches[0])
        choices = ", ".join(sorted(
            f"{p.parent.relative_to(BACKGROUND_DIR).as_posix()}/{p.stem}" for p in matches
        ))
        raise ValueError(f"Der Hintergrundname '{value}' ist mehrdeutig. Wähle Ordner/Name: {choices}.")
    available = sorted(
        f"{p.parent.relative_to(BACKGROUND_DIR).as_posix()}/{p.stem}" for p in candidates
    )
    listing = ", ".join(available[:12]) if available else "(noch keine lokalen Bilddateien)"
    if len(available) > 12:
        listing += f" und {len(available) - 12} weitere"
    raise ValueError(f"Lokaler Hintergrund '{value}' nicht gefunden. Beispiele: {listing}.")
