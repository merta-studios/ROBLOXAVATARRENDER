"""TEMPORAERE DIAGNOSE (wird nach der Messung wieder entfernt).

Sammelt Fakten ueber die klassischen Koerperteil-Pakete und die Classic-
Kleidungs-Templates, die nur im CI (mit Open-Cloud-Key) verfuegbar sind:

* das UV-Layout der Mesh-Flaechen je Richtung (Front/Back/Left/Right/Top/Bottom)
* die Alpha-/Farbstatistik der Shirt-/Pants-Template je offiziellem Rechteck
* die Template-Bilder selbst als Base64-PNG
"""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path
from typing import Any

from PIL import Image

from scripts.r15_clothing import CLOTHING_RECTS, crop_template_rect

DIAG: dict[str, Any] = {}


def _png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _region_stats(img: Image.Image) -> dict[str, Any]:
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    hist = alpha.histogram()
    total = max(1, img.width * img.height)
    pixels = list(rgba.getdata())
    opaque = [p for p in pixels if p[3] == 255]
    opaque_mean = None
    if opaque:
        opaque_mean = [
            round(sum(p[i] for p in opaque) / len(opaque), 1) for i in range(3)
        ]
    return {
        "alpha_mean": round(sum(i * c for i, c in enumerate(hist)) / total, 1),
        "alpha_zero_frac": round(hist[0] / total, 4),
        "alpha_full_frac": round(hist[255] / total, 4),
        "rgb_mean_opaque": opaque_mean,
    }


def probe_template(img: Image.Image | None, label: str) -> dict[str, Any] | None:
    if img is None:
        return None
    out: dict[str, Any] = {
        "size": list(img.size),
        "mode": img.mode,
        "overall": _region_stats(img),
        "regions": {},
        "png_b64": _png_b64(img),
    }
    for group, faces in CLOTHING_RECTS.items():
        for face, rect in faces.items():
            crop = crop_template_rect(img, rect)
            out["regions"][f"{group}.{face}"] = {
                "rect": list(rect),
                "crop_size": list(crop.size),
                **_region_stats(crop),
            }
    DIAG.setdefault("templates", {})[label] = out
    return out


def probe_file(path: str | None, label: str) -> None:
    if not path:
        return
    p = Path(path)
    if not p.is_file():
        return
    with Image.open(p) as img:
        img.load()
        DIAG.setdefault("files", {})[label] = {
            "size": list(img.size),
            "mode": img.mode,
            "png_b64": _png_b64(img.convert("RGBA")),
        }


def _face_direction(normal: tuple[float, float, float]) -> str:
    nx, ny, nz = normal
    ax, ay, az = abs(nx), abs(ny), abs(nz)
    if ax >= ay and ax >= az:
        return "Right" if nx < 0 else "Left"
    if ay >= ax and ay >= az:
        return "Front" if ny < 0 else "Back"
    return "Top" if nz > 0 else "Bottom"


def probe_mesh_faces(mesh: Any, label: str) -> None:
    """UV-Layout der Mesh-Flaechen, gruppiert nach Flaechennormale."""
    positions = mesh.positions
    uvs = mesh.uvs
    groups: dict[str, Any] = {}
    for face in mesh.faces:
        idx = [i for i in face if 0 <= i < len(positions)]
        if len(idx) < 3:
            continue
        a, b, c = positions[idx[0]], positions[idx[1]], positions[idx[2]]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length < 1e-9:
            continue
        direction = _face_direction((nx / length, ny / length, nz / length))
        face_uvs = [uvs[i] for i in idx if i < len(uvs)]
        if not face_uvs:
            continue
        rect = [
            round(min(u for u, _ in face_uvs), 4),
            round(min(v for _, v in face_uvs), 4),
            round(max(u for u, _ in face_uvs), 4),
            round(max(v for _, v in face_uvs), 4),
        ]
        g = groups.setdefault(
            direction, {"count": 0, "rect": list(rect), "rects": [], "tris": 0}
        )
        g["count"] += 1
        g["tris"] += len(idx) - 2
        g["rects"].append(rect)
        g["rect"] = [
            min(g["rect"][0], rect[0]),
            min(g["rect"][1], rect[1]),
            max(g["rect"][2], rect[2]),
            max(g["rect"][3], rect[3]),
        ]
    lo = [round(min(p[i] for p in positions), 4) for i in range(3)] if positions else []
    hi = [round(max(p[i] for p in positions), 4) for i in range(3)] if positions else []
    DIAG.setdefault("faces", {})[label] = {
        "verts": len(positions),
        "bbox_lo": lo,
        "bbox_hi": hi,
        "groups": groups,
    }
