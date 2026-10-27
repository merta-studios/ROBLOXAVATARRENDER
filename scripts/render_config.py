"""Load and validate the repository's commented TOML render configuration."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKGROUND_DIR = REPO_ROOT / "backgrounds"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
HEX_COLOR = re.compile(r"^#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

DEFAULT_CONFIG: dict[str, Any] = {
    "username": "",
    "background": "transparent",
    "material": "Glossy",
    "outline": {
        "thickness_percent": 0,
        "outside": True,
        "fade_start_percent": 0,
        "color": "white",
    },
}


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
    outline = parsed.get("outline", {})
    if not isinstance(outline, dict):
        raise ValueError("'outline' muss ein TOML-Abschnitt mit Schlüssel-Wert-Paaren sein.")
    config["outline"] = {**DEFAULT_CONFIG["outline"], **outline}

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

    outline_cfg = config["outline"]
    try:
        thickness = float(outline_cfg["thickness_percent"])
        fade = float(outline_cfg["fade_start_percent"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("Umrandungsdicke und Transparenzstart müssen Zahlen sein.") from exc
    if not 0 <= thickness <= 100:
        raise ValueError("'thickness_percent' muss zwischen 0 und 100 liegen.")
    if not 0 <= fade <= 100:
        raise ValueError("'fade_start_percent' muss zwischen 0 und 100 liegen.")
    if not isinstance(outline_cfg["outside"], bool):
        raise ValueError("'outline.outside' muss true oder false sein.")
    outline_cfg["thickness_percent"] = thickness
    outline_cfg["fade_start_percent"] = fade
    outline_color = str(outline_cfg["color"]).strip()
    normalize_color(outline_color)  # sofort prüfen; den editierbaren TOML-String beibehalten
    return config


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

    # Only files within backgrounds/ are selectable. Match filenames or stems, case-insensitively.
    requested = Path(value).name.casefold()
    matches = [
        path for path in BACKGROUND_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        and (path.name.casefold() == requested or path.stem.casefold() == requested)
    ] if BACKGROUND_DIR.is_dir() else []
    if len(matches) == 1:
        return "image", str(matches[0])
    if len(matches) > 1:
        raise ValueError(f"Der Hintergrundname '{value}' ist nicht eindeutig; nutze den Dateinamen mit Endung.")
    available = sorted(p.stem for p in BACKGROUND_DIR.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS) if BACKGROUND_DIR.is_dir() else []
    listing = ", ".join(available) if available else "(noch keine lokalen Bilddateien)"
    raise ValueError(f"Lokaler Hintergrund '{value}' nicht gefunden. Verfügbare Hintergründe: {listing}.")
