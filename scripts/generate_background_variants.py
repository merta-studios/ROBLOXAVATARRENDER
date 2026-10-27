"""Give uploaded previews semantic names and export alternate color palettes."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_DIR = ROOT / "backgrounds"

# Descriptive names, in the numeric order of the uploaded BackgroundN-Preview files.
BACKGROUND_NAMES = {
    1: "pastell-herzen-konfetti",
    2: "pink-weiss-liquid",
    3: "violett-schachbrett-tunnel",
    4: "lila-liquid-marmor",
    5: "smaragd-energie-wirbel",
    6: "elektroblau-kreuze",
    7: "eisblau-glitzer",
    8: "lavendel-organische-formen",
    9: "pink-blueten-spritzer",
    10: "violette-sternexplosion",
    11: "goldene-sonnenstrahlen",
    12: "lila-herzen-glitzer",
    13: "smaragd-neonsterne",
    14: "nacht-hologramm-prismen",
    15: "blau-weisse-optikwellen",
    16: "smaragd-lichtstrahl",
    17: "eisblau-frostspritzer",
    18: "violett-kristallsplitter",
    19: "rosa-liquid-wellen",
    20: "rot-schwarzer-sternnebel",
}

# Hue targets; the closest target to each original palette is omitted, so every
# source receives three genuinely different alternatives.
PALETTES = {
    "rot": 4,
    "blau": 151,
    "gruen": 92,
    "gold": 32,
}


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
    # Hue is undefined for grey/white details; retain their original appearance.
    original_arr = np.asarray(original, dtype=np.uint8)
    recolored_arr = np.asarray(recolored, dtype=np.uint8).copy()
    original_hsv = np.asarray(original.convert("HSV"), dtype=np.uint8)
    neutral = original_hsv[:, :, 1] < 24
    recolored_arr[neutral, :3] = original_arr[neutral, :3]
    recolored_arr[:, :, 3] = original_arr[:, :, 3]
    Image.fromarray(recolored_arr, "RGBA").save(destination, optimize=True)


def main() -> None:
    for number, name in BACKGROUND_NAMES.items():
        uploaded = BACKGROUND_DIR / f"Background{number}-Preview.png"
        named = BACKGROUND_DIR / f"{name}.png"
        if uploaded.is_file():
            if named.exists():
                raise FileExistsError(f"Kann {uploaded.name} nicht nach {named.name} umbenennen: Zieldatei existiert.")
            uploaded.rename(named)
        if not named.is_file():
            raise FileNotFoundError(f"Hintergrund fehlt: {uploaded.name} oder {named.name}")

        original = Image.open(named).convert("RGBA")
        source_hue = _dominant_hue(original)
        nearest_palette = min(
            PALETTES,
            key=lambda color: _hue_distance(source_hue, PALETTES[color] * (360.0 / 255.0)),
        )
        for color, hue in PALETTES.items():
            if color == nearest_palette:
                continue
            output = BACKGROUND_DIR / f"{name}-{color}.png"
            _make_variant(named, output, hue)

    print(f"Bereit: {len(BACKGROUND_NAMES)} Originale und jeweils drei Farbvarianten in {BACKGROUND_DIR}")


if __name__ == "__main__":
    main()
