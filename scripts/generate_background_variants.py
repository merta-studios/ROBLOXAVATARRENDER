"""Name uploaded previews and keep each palette in its own color folder."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.background_catalog import DESIGN_NAMES, ORIGINAL_COLORS

ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_DIR = ROOT / "backgrounds"
BACKGROUND_NAMES = DESIGN_NAMES

# Previous flat filenames, kept here so running the script migrates them in place.
LEGACY_NAMES = {
    1: "pastell-herzen-konfetti", 2: "pink-weiss-liquid", 3: "violett-schachbrett-tunnel",
    4: "lila-liquid-marmor", 5: "smaragd-energie-wirbel", 6: "elektroblau-kreuze",
    7: "eisblau-glitzer", 8: "lavendel-organische-formen", 9: "pink-blueten-spritzer",
    10: "violette-sternexplosion", 11: "goldene-sonnenstrahlen", 12: "lila-herzen-glitzer",
    13: "smaragd-neonsterne", 14: "nacht-hologramm-prismen", 15: "blau-weisse-optikwellen",
    16: "smaragd-lichtstrahl", 17: "eisblau-frostspritzer", 18: "violett-kristallsplitter",
    19: "rosa-liquid-wellen", 20: "rot-schwarzer-sternnebel",
}

# Hue targets; the closest target to each original palette is omitted, leaving
# three alternate colors per design. Target hues are stored in Pillow's 0..255 range.
PALETTES = {"rot": 4, "blau": 151, "gruen": 92, "gold": 32}


def _dominant_hue(image: Image.Image) -> float:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    hsv = np.asarray(image.convert("HSV"), dtype=np.uint8)
    hue = hsv[:, :, 0].astype(np.float64) * (360.0 / 255.0)
    saturation = hsv[:, :, 1].astype(np.float64) / 255.0
    weight = saturation * rgba[:, :, 3].astype(np.float64) / 255.0
    valid = weight > 0.02
    if not valid.any():
        return float(PALETTES["blau"] * (360.0 / 255.0))
    angle = np.deg2rad(hue[valid])
    x = float(np.sum(np.cos(angle) * weight[valid]))
    y = float(np.sum(np.sin(angle) * weight[valid]))
    if math.isclose(x, 0.0, abs_tol=1e-8) and math.isclose(y, 0.0, abs_tol=1e-8):
        return float(np.average(hue[valid], weights=weight[valid]))
    return math.degrees(math.atan2(y, x)) % 360.0


def _hue_distance(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _make_variant(source: Path, destination: Path, target_hue_byte: int) -> None:
    original = Image.open(source).convert("RGBA")
    source_hue = _dominant_hue(original)
    target_hue = target_hue_byte * (360.0 / 255.0)
    hue_shift = int(round(((target_hue - source_hue + 180.0) % 360.0 - 180.0) * (255.0 / 360.0)))

    hsv = np.asarray(original.convert("HSV"), dtype=np.uint8).copy()
    hsv[:, :, 0] = ((hsv[:, :, 0].astype(np.int16) + hue_shift) % 256).astype(np.uint8)
    recolored = Image.fromarray(hsv, "HSV").convert("RGBA")
    original_arr = np.asarray(original, dtype=np.uint8)
    recolored_arr = np.asarray(recolored, dtype=np.uint8).copy()
    original_hsv = np.asarray(original.convert("HSV"), dtype=np.uint8)
    neutral = original_hsv[:, :, 1] < 24
    recolored_arr[neutral, :3] = original_arr[neutral, :3]
    recolored_arr[:, :, 3] = original_arr[:, :, 3]
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(recolored_arr, "RGBA").save(destination, optimize=True)


def _organize_design(number: int, name: str) -> None:
    root_original = BACKGROUND_DIR / f"{name}.png"
    source_preview = BACKGROUND_DIR / f"Background{number}-Preview.png"
    legacy_original = BACKGROUND_DIR / f"{LEGACY_NAMES[number]}.png"
    original = BACKGROUND_DIR / ORIGINAL_COLORS[number] / f"{name}.png"

    # Support numbered uploads, the previous descriptive flat layout, and reruns.
    if source_preview.is_file():
        if root_original.exists():
            raise FileExistsError(f"Beide Quelldateien vorhanden: {source_preview} und {root_original}")
        source_preview.rename(root_original)
    if legacy_original.is_file():
        if root_original.exists():
            raise FileExistsError(f"Beide alten/neuen Namen vorhanden: {legacy_original} und {root_original}")
        legacy_original.rename(root_original)
    if root_original.is_file():
        original.parent.mkdir(parents=True, exist_ok=True)
        if original.exists():
            raise FileExistsError(f"Zieldatei existiert bereits: {original}")
        root_original.rename(original)
    if not original.is_file():
        raise FileNotFoundError(f"Original fehlt: {source_preview.name} oder {original}")

    original_image = Image.open(original).convert("RGBA")
    source_hue = _dominant_hue(original_image)
    closest_palette = min(
        PALETTES,
        key=lambda color: _hue_distance(source_hue, PALETTES[color] * (360.0 / 255.0)),
    )

    for color, hue in PALETTES.items():
        if color == closest_palette:
            continue
        old_flat_variant = BACKGROUND_DIR / f"{LEGACY_NAMES[number]}-{color}.png"
        target = BACKGROUND_DIR / color / f"{name}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        if old_flat_variant.is_file():
            if target.exists():
                raise FileExistsError(f"Farbvariante ist doppelt vorhanden: {old_flat_variant} und {target}")
            old_flat_variant.rename(target)
        elif not target.exists():
            _make_variant(original, target, hue)


def main() -> None:
    for number, name in BACKGROUND_NAMES.items():
        _organize_design(number, name)
    print(f"Bereit: {len(BACKGROUND_NAMES)} Motive mit Farbvarianten in farbigen Unterordnern unter {BACKGROUND_DIR}")


if __name__ == "__main__":
    main()
