"""GFX-typunabhaengige PNG-Nachbearbeitung (reines Python, kein ``bpy``).

Sowohl HearthandsType als auch ClassicType benoetigen dieselbe Umrandung.
ClassicType setzt zusaetzlich den Hintergrund *nach* einem transparenten
Avatar-Render zusammen. Beides passiert hier, damit keiner der beiden
Render-Pfade die Szene des anderen oeffnen oder kennen muss.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy.ndimage import distance_transform_edt
from PIL import Image

from scripts.render_config import normalize_color


def apply_avatar_outline(
    output_png: Path,
    avatar_png: Path,
    *,
    thickness_percent: float,
    outside: bool,
    fade_start_percent: float,
    color: tuple[int, int, int],
) -> None:
    """Legt eine Umrandung um den Avatar-Alpha-Kanal aus ``avatar_png``.

    ``output_png`` ist bereits das finale Bild (mit oder ohne Hintergrund);
    ``avatar_png`` muss der isolierte RGBA-Render ohne Hintergrund sein.
    """
    output_png = Path(output_png)
    avatar_png = Path(avatar_png)
    if output_png.resolve() == avatar_png.resolve():
        raise RuntimeError("Umrandung benoetigt getrennte Alpha-Quelle und Ausgabedatei.")

    with Image.open(output_png) as rendered, Image.open(avatar_png) as avatar:
        base = rendered.convert("RGBA")
        alpha = np.asarray(avatar.convert("RGBA").getchannel("A"), dtype=np.uint8)
    if alpha.shape != (base.height, base.width):
        raise RuntimeError("Die Alpha-Maske für die Umrandung hat eine abweichende Bildgröße.")

    mask = alpha >= 128
    if not mask.any():
        raise RuntimeError("Für die Umrandung wurde kein Avatar im isolierten Render gefunden.")
    width = thickness_percent * base.width / 100.0

    # Wenn der Avatar ueber den Canvas-Rand hinausgeht, darf die Outline dort
    # nicht als kuenstliche Linie am Rand kleben. Dazu wird die Silhouette mit
    # 'edge' ueber den Rand hinaus verlaengert; die Berechnung laeuft auf der
    # groesseren Maske und wird anschliessend auf das Originalbild zugeschnitten.
    pad = int(math.ceil(width)) + 2
    padded_mask = np.pad(mask, pad, mode="edge")
    field = (~padded_mask if outside else padded_mask)
    distances = distance_transform_edt(field)
    valid = field & (distances <= width + 0.5)
    progress = np.clip((distances - 0.5) / max(width, 1e-6), 0.0, 1.0)
    fade = fade_start_percent / 100.0
    if fade >= 1.0:
        opacity = np.ones_like(progress)
    else:
        opacity = np.where(progress <= fade, 1.0, (1.0 - progress) / (1.0 - fade))
    opacity = np.where(valid, opacity, 0.0)[pad:-pad, pad:-pad]

    overlay_arr = np.zeros((base.height, base.width, 4), dtype=np.uint8)
    overlay_arr[:, :, :3] = color
    overlay_arr[:, :, 3] = np.clip(opacity * 255.0, 0, 255).astype(np.uint8)
    overlay = Image.fromarray(overlay_arr, mode="RGBA")
    Image.alpha_composite(base, overlay).save(output_png, format="PNG")


def _cover_resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Skaliert ein Hintergrundbild randlos (center crop) auf die Render-Groesse."""
    if image.size == size:
        return image
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    new_size = (
        max(target_w, int(round(image.width * scale))),
        max(target_h, int(round(image.height * scale))),
    )
    resized = image.resize(new_size, Image.Resampling.LANCZOS)
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def compose_background(
    avatar_png: Path,
    output_png: Path,
    *,
    kind: str,
    image_path: str | None,
    value: str,
) -> None:
    """Setzt den konfigurierten Hintergrund hinter den transparenten Avatar-Render.

    ``kind`` entspricht dem Ergebnis von ``resolve_background()``:
    ``transparent``, ``color`` oder ``image``. Das Ergebnis ist immer RGBA,
    damit ``verify_rendered_png()`` beide GFX-Typen gleich pruefen kann.
    """
    avatar_png = Path(avatar_png)
    output_png = Path(output_png)
    if not avatar_png.is_file() or avatar_png.stat().st_size == 0:
        raise RuntimeError("Der transparente Avatar-Render fehlt oder ist leer.")

    with Image.open(avatar_png) as rendered:
        avatar = rendered.convert("RGBA")

    if kind == "transparent":
        background = Image.new("RGBA", avatar.size, (0, 0, 0, 0))
    elif kind == "color":
        background = Image.new("RGBA", avatar.size, (*normalize_color(value), 255))
    elif kind == "image":
        if not image_path or not Path(image_path).is_file():
            raise RuntimeError("Die Hintergrund-Bilddatei fehlt.")
        with Image.open(image_path) as src:
            background = _cover_resize(src.convert("RGBA"), avatar.size)
    else:
        raise RuntimeError(f"Unbekannter Hintergrund-Typ: {kind!r}.")

    output_png.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(background, avatar).save(output_png, format="PNG")
