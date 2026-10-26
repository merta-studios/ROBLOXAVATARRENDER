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
        dump["accessories"] = [
            {
                "asset_id": a.asset_id,
                "name": a.name,
                "type": a.asset_type_id,
                "attachment": a.attachment_name,
                "texture": a.texture_path,
                "base_color": a.base_color,
                "tint": a.tint_color,
            }
            for a in bundle.accessories
        ]
        for f in work.iterdir():
            try:
                shutil.copy(f, OUT / f.name)
            except Exception:
                pass

        out_png = OUT / "preview.png"
        br.render_avatar_scene(
            blend_path=REPO_ROOT / "WomanRig.blend",
            bundle=bundle,
            output_png=out_png,
            work_dir=work,
            res_x=700,
            res_y=700,
        )

    # Szenen-Dump nach dem Render
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objects = []
    for obj in bpy.data.objects:
        entry: dict = {
            "name": obj.name,
            "type": obj.type,
            "hide_render": obj.hide_render,
            "hide_viewport": obj.hide_viewport,
            "parent": obj.parent.name if obj.parent else None,
        }
        if obj.type == "MESH":
            ev = obj.evaluated_get(depsgraph)
            me = None
            try:
                me = ev.to_mesh()
            except Exception:
                me = None
            if me is not None and len(me.vertices):
                n = len(me.vertices)
                co = np.empty(n * 3, dtype=np.float32)
                me.vertices.foreach_get("co", co)
                pts = co.reshape(n, 3).astype(np.float64)
                mw = np.array(ev.matrix_world, dtype=np.float64)
                world = (pts @ mw[:3, :3].T) + mw[:3, 3]
                entry["verts"] = n
                entry["bbox_min"] = [round(float(x), 3) for x in world.min(axis=0)]
                entry["bbox_max"] = [round(float(x), 3) for x in world.max(axis=0)]
            entry["materials"] = [
                s.material.name if s.material else None for s in obj.material_slots
            ]
            if me is not None:
                ev.to_mesh_clear()
            entry["modifiers"] = [m.type for m in obj.modifiers]
        objects.append(entry)
    dump["objects"] = objects

    materials = []
    for mat in bpy.data.materials:
        bsdf, tex = br._find_principled_and_tex(mat)
        info: dict = {"name": mat.name}
        if bsdf is not None:
            info["roughness"] = round(bsdf.inputs["Roughness"].default_value, 4)
            info["metal"] = round(bsdf.inputs["Metallic"].default_value, 4)
            info["base_color"] = [round(x, 4) for x in bsdf.inputs["Base Color"].default_value]
            info["base_linked"] = bool(bsdf.inputs["Base Color"].is_linked)
        if tex is not None and tex.image is not None:
            img = tex.image
            info["image"] = {
                "name": img.name,
                "filepath": img.filepath,
                "size": list(img.size),
                "has_data": bool(img.has_data),
            }
            try:
                px = np.empty(len(img.pixels), dtype=np.float32)
                img.pixels.foreach_get(px)
                arr = px.reshape(-1, 4)
                info["image"]["mean_rgba"] = [round(float(x), 4) for x in arr.mean(axis=0)]
                if arr.shape[0] == img.size[0] * img.size[1] and img.size[0] > 0:
                    at = arr.reshape(img.size[1], img.size[0], 4)
                    info["image"]["center_px"] = [
                        round(float(x), 4) for x in at[img.size[1] // 2, img.size[0] // 2]
                    ]
            except Exception as exc:
                info["image"]["error"] = str(exc)
        materials.append(info)
    dump["materials"] = materials

    dump["scene"] = {
        "camera": bpy.context.scene.camera.name if bpy.context.scene.camera else None,
        "engine": bpy.context.scene.render.engine,
    }
    (OUT / "dump.json").write_text(json.dumps(dump, indent=2), encoding="utf-8")
    print("DEBUG_DUMP_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
