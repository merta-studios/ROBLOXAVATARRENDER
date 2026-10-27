"""Classic-Shirt/Pants/T-Shirt/Face als Texturen fuer den R15-Blocky-Rig.

``compose_r6_body_texture`` baut einen 2048x1024-Atlas exklusiv fuer die UVs
von ``WomanRig.blend``. Der offizielle R15-Blocky-Rig hat andere UVs; deshalb
bekommt hier **jedes der 15 R15-Teile eine eigene 384x256-Textur** mit sechs
128x128-Face-Tiles (Front/Back/Left/Right/Top/Bottom). Ausserdem werden die UVs
aus den Mesh-Normalen neu berechnet, damit die Tiles exakt auf den sechs
Wuerfelseiten liegen. Das Modul ist reines Python/PIL und braucht kein ``bpy``.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from scripts.r15_blocky_rig import EXPECTED_PARTS, PART_COLOR_KEYS, R15Part

# Offizielle Pixel-Rechtecke auf dem 585x559 Roblox-Classic-Shirt/Pants-Template.
TEMPLATE_W = 585
TEMPLATE_H = 559
CLOTHING_RECTS: dict[str, dict[str, tuple[int, int, int, int]]] = {
    "torso": {
        "Front": (231, 74, 128, 128),
        "Back": (427, 74, 128, 128),
        "Right": (165, 74, 64, 128),
        "Left": (361, 74, 64, 128),
        "Top": (231, 8, 128, 64),
        "Bottom": (231, 204, 128, 64),
    },
    "right": {
        "Front": (217, 355, 64, 128),
        "Back": (85, 355, 64, 128),
        "Right": (151, 355, 64, 128),
        "Left": (19, 355, 64, 128),
        "Top": (217, 289, 64, 64),
        "Bottom": (217, 485, 64, 64),
    },
    "left": {
        "Front": (308, 355, 64, 128),
        "Back": (440, 355, 64, 128),
        "Right": (506, 355, 64, 128),
        "Left": (374, 355, 64, 128),
        "Top": (308, 289, 64, 64),
        "Bottom": (308, 485, 64, 64),
    },
}

R15_TILE = 128
R15_ATLAS_W = 3 * R15_TILE
R15_ATLAS_H = 2 * R15_TILE
FACE_NAMES = ("Front", "Back", "Left", "Right", "Top", "Bottom")

# 3x2-Grid. Spalte 0..2, Zeile 0..1 (Zeile 0 = oben im Bild).
FACE_TILE_POS: dict[str, tuple[int, int]] = {
    "Left": (0, 0),
    "Front": (1, 0),
    "Right": (2, 0),
    "Bottom": (0, 1),
    "Back": (1, 1),
    "Top": (2, 1),
}

# R15-Aufteilung gemaess den Classic-Clothing-UV-Guidelines:
# Torso 96/32 px, Gliedmassen 64/48/16 px der 128 px hohen Template-Faces.
_SEGMENT_BY_PART: dict[str, str] = {
    "UpperTorso": "torso_upper",
    "LowerTorso": "torso_lower",
    "LeftUpperArm": "limb_upper",
    "LeftLowerArm": "limb_lower",
    "LeftHand": "limb_end",
    "RightUpperArm": "limb_upper",
    "RightLowerArm": "limb_lower",
    "RightHand": "limb_end",
    "LeftUpperLeg": "limb_upper",
    "LeftLowerLeg": "limb_lower",
    "LeftFoot": "limb_end",
    "RightUpperLeg": "limb_upper",
    "RightLowerLeg": "limb_lower",
    "RightFoot": "limb_end",
}


def crop_template_rect(img: Image.Image, rect: tuple[int, int, int, int]) -> Image.Image:
    """Schneidet ein Face-Rechteck aus einem (ggf. skalierten) 585x559-Template."""
    x, y, w, h = rect
    if img.size != (TEMPLATE_W, TEMPLATE_H):
        sx = img.width / TEMPLATE_W
        sy = img.height / TEMPLATE_H
        box = (
            int(round(x * sx)),
            int(round(y * sy)),
            int(round((x + w) * sx)),
            int(round((y + h) * sy)),
        )
    else:
        box = (x, y, x + w, y + h)
    return img.crop(box)


def _vertical_crop(img: Image.Image, segment: str | None) -> Image.Image:
    w, h = img.size
    if h <= 0 or w <= 0:
        return img
    splits = {
        "torso_upper": (0.0, 0.75),
        "torso_lower": (0.75, 1.0),
        "limb_upper": (0.0, 0.5),
        "limb_lower": (0.5, 0.875),
        "limb_end": (0.875, 1.0),
    }
    if segment not in splits:
        return img
    top, bottom = splits[segment]
    y0, y1 = int(round(h * top)), int(round(h * bottom))
    if y1 <= y0:
        y1 = min(h, y0 + 1)
    return img.crop((0, y0, w, y1))


def _as_tile(img: Image.Image, segment: str | None = None) -> Image.Image:
    cropped = _vertical_crop(img.convert("RGBA"), segment)
    return cropped.resize((R15_TILE, R15_TILE), Image.Resampling.LANCZOS)


def _part_style(part_name: str) -> tuple[str | None, str | None, str | None]:
    """(Template-Gruppe, Segment, Template-Name) fuer ein R15-Teil."""
    if part_name == "Head":
        return (None, None, None)
    if part_name in ("UpperTorso", "LowerTorso"):
        return ("torso", _SEGMENT_BY_PART[part_name], "shirt" if part_name == "UpperTorso" else "pants")
    side = "left" if part_name.startswith("Left") else "right"
    template = "shirt" if "Arm" in part_name or "Hand" in part_name else "pants"
    return (side, _SEGMENT_BY_PART[part_name], template)


def _face_layers(
    part_name: str,
    face: str,
    shirt_img: Image.Image | None,
    pants_img: Image.Image | None,
    tshirt_img: Image.Image | None,
    face_img: Image.Image | None,
) -> list[Image.Image]:
    """RGBA-Ebenen, die ueber die BodyColor eines Face-Tiles gelegt werden."""
    if part_name == "Head":
        if face == "Front" and face_img is not None:
            return [_as_tile(face_img)]
        return []

    group, segment, template_name = _part_style(part_name)
    if group is None:
        return []
    template = shirt_img if template_name == "shirt" else pants_img
    if template is None:
        return []

    rect = CLOTHING_RECTS[group][face]
    # Top/Bottom-Kappen sind nur 64 px hoch; dort nicht vertikal splitten.
    effective_segment = None if face in ("Top", "Bottom") else segment
    layers: list[Image.Image] = [_as_tile(crop_template_rect(template, rect), effective_segment)]

    # T-Shirt liegt als Decal auf der vorderen Torso-Haelfte.
    if part_name in ("UpperTorso", "LowerTorso") and face == "Front" and tshirt_img is not None:
        layers.append(_as_tile(tshirt_img, segment))

    return layers


def compose_r15_part_texture(
    part_name: str,
    body_colors: dict[str, tuple[int, int, int]],
    shirt_img: Image.Image | None,
    pants_img: Image.Image | None,
    tshirt_img: Image.Image | None,
    face_img: Image.Image | None,
) -> Image.Image:
    """Erzeugt die 384x256-Textur mit sechs Face-Tiles fuer ein R15-Teil."""
    color_key = PART_COLOR_KEYS[part_name]
    rgb = tuple(max(0, min(255, int(c))) for c in body_colors.get(color_key, (200, 200, 200)))
    canvas = Image.new("RGB", (R15_ATLAS_W, R15_ATLAS_H), rgb)
    for face, (col, row) in FACE_TILE_POS.items():
        tile = Image.new("RGBA", (R15_TILE, R15_TILE), (*rgb, 255))
        for layer in _face_layers(part_name, face, shirt_img, pants_img, tshirt_img, face_img):
            tile.alpha_composite(layer)
        canvas.paste(tile.convert("RGB"), (col * R15_TILE, row * R15_TILE))
    return canvas


def compose_r15_part_textures(
    body_colors: dict[str, tuple[int, int, int]],
    shirt_img: Image.Image | None,
    pants_img: Image.Image | None,
    tshirt_img: Image.Image | None,
    face_img: Image.Image | None,
    work_dir: Path,
) -> dict[str, str]:
    """Schreibt alle 15 R15-Teiltexturen und liefert ``{Teilname: PNG-Pfad}``."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    for part_name in EXPECTED_PARTS:
        image = compose_r15_part_texture(
            part_name, body_colors, shirt_img, pants_img, tshirt_img, face_img
        )
        path = work_dir / f"r15_{part_name}.png"
        image.save(path, format="PNG", optimize=True)
        out[part_name] = str(path)
    return out


# ---------------------------------------------------------------------------
# UV-Berechnung: eine Face-Tile pro Wuerfelseite, geometrisch aus den Normalen.
# ---------------------------------------------------------------------------
def face_uv_rect(face: str) -> tuple[float, float, float, float]:
    """(u0, v0, u1, v1) einer Face-Tile in Blender-UV-Koordinaten."""
    col, row = FACE_TILE_POS[face]
    u0 = col * R15_TILE / R15_ATLAS_W
    u1 = (col + 1) * R15_TILE / R15_ATLAS_W
    # Bildzeile 0 ist oben; Blender-V=0 ist unten.
    v1 = 1.0 - row * R15_TILE / R15_ATLAS_H
    v0 = 1.0 - (row + 1) * R15_TILE / R15_ATLAS_H
    return (u0, v0, u1, v1)


def _polygon_normal(vertices: list[tuple[float, float, float]], face: tuple[int, ...]) -> tuple[float, float, float]:
    if len(face) < 3:
        return (0.0, -1.0, 0.0)
    a, b, c = (vertices[face[i]] for i in range(3))
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-9:
        return (0.0, -1.0, 0.0)
    return (nx / length, ny / length, nz / length)


def _classify_face(normal: tuple[float, float, float]) -> str:
    nx, ny, nz = normal
    ax, ay, az = abs(nx), abs(ny), abs(nz)
    if ax >= ay and ax >= az:
        return "Right" if nx < 0 else "Left"
    if ay >= ax and ay >= az:
        return "Front" if ny < 0 else "Back"
    return "Top" if nz > 0 else "Bottom"


def _vertex_uv(
    part_name: str,
    face: str,
    vertex: tuple[float, float, float],
    lo: tuple[float, float, float],
    hi: tuple[float, float, float],
) -> tuple[float, float]:
    x, y, z = vertex
    rx = max(hi[0] - lo[0], 1e-6)
    ry = max(hi[1] - lo[1], 1e-6)
    rz = max(hi[2] - lo[2], 1e-6)
    nx, ny, nz = (x - lo[0]) / rx, (y - lo[1]) / ry, (z - lo[2]) / rz

    if part_name == "Head":
        # Nur die Vorderseite zeigt das Gesichts-Decal; alle anderen Tiles sind
        # einfarbig, deshalb reicht dort die Mitte des jeweiligen Face-Tiles.
        if face != "Front":
            u0, v0, u1, v1 = face_uv_rect(face)
            return ((u0 + u1) / 2.0, (v0 + v1) / 2.0)
        u, v = nx, nz
    elif face == "Front":
        u, v = nx, nz
    elif face == "Back":
        u, v = 1.0 - nx, nz
    elif face == "Right":
        u, v = ny, nz
    elif face == "Left":
        u, v = 1.0 - ny, nz
    elif face == "Top":
        u, v = nx, ny
    else:  # Bottom
        u, v = nx, 1.0 - ny

    u = max(0.0, min(1.0, u))
    v = max(0.0, min(1.0, v))
    u0, v0, u1, v1 = face_uv_rect(face)
    return (u0 + u * (u1 - u0), v0 + v * (v1 - v0))


def compute_part_uvs(part: R15Part) -> list[tuple[float, float]]:
    """Berechnet pro Mesh-Loop eine UV, die auf das passende Face-Tile zeigt."""
    vertices = [tuple(float(v) for v in vert) for vert in part.vertices]
    if not vertices:
        return []
    lo = tuple(min(v[i] for v in vertices) for i in range(3))
    hi = tuple(max(v[i] for v in vertices) for i in range(3))
    uvs: list[tuple[float, float]] = []
    for face in part.faces:
        normal = _polygon_normal(vertices, face)
        face_name = _classify_face(normal)
        for vi in face:
            if 0 <= vi < len(vertices):
                uvs.append(_vertex_uv(part.name, face_name, vertices[vi], lo, hi))
            else:
                uvs.append(face_uv_rect("Front")[:2])
    return uvs
