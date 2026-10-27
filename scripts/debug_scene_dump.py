"""TEMPORÄR: Diagnose-Dump für die Render-Pipeline (wird nach der Analyse entfernt)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import bpy  # noqa: E402
import numpy as np  # noqa: E402

from scripts.roblox_opencloud import load_avatar_bundle  # noqa: E402
from scripts import blender_render as br  # noqa: E402

OUT = Path("/tmp/debug_out")
OUT.mkdir(parents=True, exist_ok=True)


def username() -> str:
    for line in (REPO_ROOT / "username.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise RuntimeError("username.txt leer")


def uv_sample_stats(obj: bpy.types.Object) -> dict:
    """Mittlere Texturfarbe (linear) über die von den UVs abgetasteten Pixel."""
    slot = obj.material_slots[0] if obj.material_slots else None
    mat = slot.material if slot else None
    if mat is None:
        return {}
    _bsdf, tex = br._find_principled_and_tex(mat)
    if tex is None or tex.image is None:
        return {}
    img = tex.image
    w, h = img.size
    if not w or not h:
        return {}
    px = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(px)
    at = px.reshape(h, w, 4)

    me = obj.data
    uv_layer = me.uv_layers.active
    uvs = []
    if uv_layer is not None:
        n = len(me.loops)
        arr = np.empty(n * 2, dtype=np.float32)
        uv_layer.data.foreach_get("uv", arr)
        uvs = arr.reshape(n, 2)
    if len(uvs) == 0:
        return {"uv": None}
    xs = np.clip((uvs[:, 0] % 1.0) * (w - 1), 0, w - 1).astype(int)
    ys = np.clip((uvs[:, 1] % 1.0) * (h - 1), 0, h - 1).astype(int)
    sampled = at[ys, xs]
    return {
        "uv_min": [round(float(x), 3) for x in uvs.min(axis=0)],
        "uv_max": [round(float(x), 3) for x in uvs.max(axis=0)],
        "sampled_mean_rgba": [round(float(x), 4) for x in sampled.mean(axis=0)],
        "sampled_dark_fraction": round(
            float((sampled[:, :3].max(axis=1) < 0.05).mean()), 3
        ),
    }


def render_variants(bundle) -> None:
    """Vier Test-Renders, um zu sehen, welches Objekt den Kopf verdunkelt."""
    variants = [
        ("01_body_only", {"body"}),
        ("02_head_only", {"head"}),
        ("03_head_faces", {"head", "faces"}),
        ("04_all", {"body", "head", "faces", "other"}),
    ]
    for name, keep in variants:
        bpy.ops.wm.open_mainfile(filepath=str((REPO_ROOT / "WomanRig.blend").resolve()))
        with tempfile.TemporaryDirectory(prefix="dbg_") as tmp:
            work = Path(tmp)
            br.render_avatar_scene(
                blend_path=REPO_ROOT / "WomanRig.blend",
                bundle=bundle,
                output_png=OUT / f"{name}.png",
                work_dir=work,
                res_x=520,
                res_y=520,
            )
        for obj in bpy.data.objects:
            if obj.type != "MESH":
                continue
            if obj.name == "AvatarHead":
                group = "head"
            elif obj.name.startswith("Accessory_"):
                group = "faces" if ("Face" in obj.name or "Bangs" in obj.name or "Squin" in obj.name) else "other"
            elif obj.name.startswith("Rigcreationsgirl"):
                group = "body"
            else:
                group = "scene"
            if group not in keep and group != "scene":
                obj.hide_render = True
        bpy.context.scene.render.filepath = str(OUT / f"{name}.png")
        bpy.ops.render.render(write_still=True)
        print(f"[variant] {name} fertig")


def main() -> int:
    dump: dict = {}
    with tempfile.TemporaryDirectory(prefix="dbg_") as tmp:
        work = Path(tmp)
        bundle = load_avatar_bundle(
            username=username(), api_key=os.environ.get("ROBLOX_API_KEY", ""), work_dir=work
        )
        dump["username"] = bundle.username
        dump["body_colors"] = bundle.body_colors
        dump["head_spec"] = {
            "kind": bundle.head.kind,
            "skin_rgb": bundle.head.skin_rgb,
            "custom_texture_path": bundle.head.custom_texture_path,
            "face_texture_path": bundle.head.face_texture_path,
            "mesh_scale": bundle.head.mesh_scale,
        }
        for f in work.iterdir():
            try:
                shutil.copy(f, OUT / f.name)
            except Exception:
                pass

        br.render_avatar_scene(
            blend_path=REPO_ROOT / "WomanRig.blend",
            bundle=bundle,
            output_png=OUT / "preview.png",
            work_dir=work,
            res_x=700,
            res_y=700,
        )

    objects = []
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.hide_render:
            continue
        entry = {
            "name": obj.name,
            "materials": [s.material.name if s.material else None for s in obj.material_slots],
        }
        for mat in [s.material for s in obj.material_slots if s.material]:
            bsdf, tex = br._find_principled_and_tex(mat)
            img = tex.image if tex is not None else None
            entry["shader"] = {
                "material": mat.name,
                "roughness": round(bsdf.inputs["Roughness"].default_value, 3) if bsdf else None,
                "alpha_linked": bool(bsdf.inputs["Alpha"].is_linked) if bsdf else None,
                "alpha": round(bsdf.inputs["Alpha"].default_value, 3) if bsdf else None,
                "blend_method": getattr(mat, "surface_render_method", None),
                "image": img.name if img else None,
            }
        entry.update(uv_sample_stats(obj))
        objects.append(entry)
    dump["objects"] = objects
    (OUT / "dump.json").write_text(json.dumps(dump, indent=2), encoding="utf-8")

    render_variants(bundle)
    print("DEBUG_DUMP_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
