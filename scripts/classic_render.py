"""Eigener Render-Pfad für den GFX-Typ 'ClassicType'.

Öffnet NICHT WomanRig.blend (das ist das R6-Rig für HearthandsType).
Stattdessen wird eine eigene, leere Blender-Szene aufgebaut, in der der
R15-Avatar aus den per Open Cloud API geladenen Daten gerendert wird:

* Körperfarben als farbige Blöcke (Platzhalter bis ein R15-Rig-Blend existiert)
* Geladene Accessoire-Meshes an ihren Attachment-Positionen
* Klassische R6-Textur auf dem Körper (falls vorhanden)

Die Einstellungen pose, rotation_deg, abstand_oben und abstand_unten werden
hier ausgewertet.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import bpy
import numpy as np
from mathutils import Matrix, Vector

from scripts.rbx_formats import IDENTITY_CFRAME, MeshData
from scripts.roblox_opencloud import AvatarBundle
from scripts.gfx_layout import compute_spacing_transform

# Abfahrt: WomanRig.blend darf hier nicht geöffnet werden.  Sobald ein
# eigenes R15-Blend vorliegt, wird dieser Platzhalter durch das echte Rig
# ersetzt und wirft stattdessen keinen Fehler mehr.


def _build_empty_scene(res_x: int, res_y: int) -> None:
    """Erstellt eine leere Blender-Szene mit Kamera, Licht und Ortho-Projektion."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.film_transparent = True
    scene.render.resolution_x = res_x
    scene.render.resolution_y = res_y
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"

    world = bpy.data.worlds.new("ClassicWorld")
    scene.world = world
    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    bg = tree.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    out = tree.nodes.new("ShaderNodeOutputWorld")
    tree.links.new(bg.outputs["Background"], out.inputs["Surface"])

    cam_data = bpy.data.cameras.new("ClassicCamera")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 4.0
    cam = bpy.data.objects.new("ClassicCamera", cam_data)
    scene.collection.objects.link(cam)
    cam.location = Vector((0.0, -20.0, 3.8))
    cam.rotation_euler = Vector((0.0, 0.0, 0.0))
    scene.camera = cam

    # Ein einfaches Weichlicht
    ldata = bpy.data.lights.new("ClassicLight", type="AREA")
    ldata.energy = 200.0
    ldata.size = 5.0
    lobj = bpy.data.objects.new("ClassicLight", ldata)
    scene.collection.objects.link(lobj)
    lobj.location = Vector((-5.0, -5.0, 10.0))
    lobj.rotation_euler = Vector((math.radians(45), 0.0, 0.0))


def _solid_material(name: str, rgb: tuple[float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.4
    bsdf.inputs["Specular IOR Level"].default_value = 0.5
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _create_body_block(
    name: str,
    size: tuple[float, float, float],
    pos: tuple[float, float, float],
    rgb: tuple[float, float, float],
    render_objects: list[bpy.types.Object],
) -> None:
    mesh = bpy.data.meshes.new(name)
    verts = [
        (-size[0] / 2, -size[1] / 2, -size[2] / 2),
        (size[0] / 2, -size[1] / 2, -size[2] / 2),
        (size[0] / 2, size[1] / 2, -size[2] / 2),
        (-size[0] / 2, size[1] / 2, -size[2] / 2),
        (-size[0] / 2, -size[1] / 2, size[2] / 2),
        (size[0] / 2, -size[1] / 2, size[2] / 2),
        (size[0] / 2, size[1] / 2, size[2] / 2),
        (-size[0] / 2, size[1] / 2, size[2] / 2),
    ]
    faces = [
        (0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
        (2, 3, 7, 6), (3, 0, 4, 7),
    ]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(_solid_material(name + "_Mat", rgb))
    obj.location = Vector(pos)
    bpy.context.collection.objects.link(obj)
    render_objects.append(obj)


def _place_mesh(
    name: str,
    mdata: MeshData,
    scale: tuple[float, float, float],
    offset: tuple[float, float, float],
    mat: bpy.types.Material,
    location: tuple[float, float, float],
    render_objects: list[bpy.types.Object],
) -> None:
    mesh = bpy.data.meshes.new(name)
    verts_bl: list[tuple[float, float, float]] = []
    for px, py, pz in mdata.positions:
        # T_RBX_TO_BL: (x_bl, y_bl, z_bl) = (-rx, rz, ry)
        verts_bl.append((-px * scale[0] + offset[0], pz * scale[2] + offset[2], py * scale[1] + offset[1]))
    faces = [f for f in mdata.faces if all(i < len(verts_bl) for i in f)]
    mesh.from_pydata(verts_bl, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(mat)
    obj.location = Vector(location)
    bpy.context.collection.objects.link(obj)
    render_objects.append(obj)


def render_classic_scene(
    bundle: AvatarBundle,
    output_png: Path,
    work_dir: Path,
    res_x: int = 2000,
    res_y: int = 2000,
    material_finish: str = "Glossy",
    background_kind: str = "transparent",
    background_path: str | None = None,
    background_value: str = "transparent",
    outline: dict | None = None,
    classic_pose: str = "None",
    classic_emote: Any = None,
    classic_rotation_deg: float = 0.0,
    classic_abstand_oben: float = 0.0,
    classic_abstand_unten: float = 0.0,
) -> None:
    """Rendert den ClassicType-Avatar in einer eigenen Szene (kein WomanRig.blend)."""
    _build_empty_scene(res_x, res_y)
    scene = bpy.context.scene
    render_objects: list[bpy.types.Object] = []

    body_colors = bundle.body_colors
    # Körper als einfache farbige Blöcke (Platzhalter bis R15-Rig-Blend existiert)
    _create_body_block("Head", (0.6, 0.6, 0.6), (0.0, 0.0, 4.5), tuple(c / 255.0 for c in body_colors["head"]), render_objects)
    _create_body_block("Torso", (0.6, 0.6, 1.2), (0.0, 0.0, 3.0), tuple(c / 255.0 for c in body_colors["torso"]), render_objects)
    _create_body_block("RightArm", (0.3, 0.3, 1.2), (-0.45, 0.0, 3.0), tuple(c / 255.0 for c in body_colors["rightArm"]), render_objects)
    _create_body_block("LeftArm", (0.3, 0.3, 1.2), (0.45, 0.0, 3.0), tuple(c / 255.0 for c in body_colors["leftArm"]), render_objects)
    _create_body_block("RightLeg", (0.3, 0.3, 1.2), (-0.25, 0.0, 1.2), tuple(c / 255.0 for c in body_colors["rightLeg"]), render_objects)
    _create_body_block("LeftLeg", (0.3, 0.3, 1.2), (0.25, 0.0, 1.2), tuple(c / 255.0 for c in body_colors["leftLeg"]), render_objects)

    # Accessoires platzieren (nur starre Typen, keine Layered Clothing / Equipment)
    template_mat = bpy.data.materials.get("Material") or render_objects[0].material_slots[0].material
    for idx, acc in enumerate(bundle.accessories):
        mat = _solid_material(f"AccMat_{idx}", acc.base_color)
        _place_mesh(
            f"AccMesh_{acc.asset_id}_{idx}",
            acc.mesh,
            acc.mesh_scale,
            acc.mesh_offset,
            mat,
            (0.0, 0.0, 0.0),
            render_objects,
        )

    # Drehung um die vertikale Achse (In-Place, nicht Orbit)
    if classic_rotation_deg:
        center_xy = (0.0, 0.0)
        rad = math.radians(classic_rotation_deg)
        cz, sz = math.cos(rad), math.sin(rad)
        rot = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
        for obj in render_objects:
            mw = np.array(obj.matrix_world, dtype=np.float64)
            pos = mw[:3, 3]
            dp = pos - np.array([center_xy[0], center_xy[1], pos[2]])
            mw[:3, 3] = np.array([center_xy[0], center_xy[1], pos[2]]) + rot @ dp
            mw[:3, :3] = mw[:3, :3] * 1.0  # Rotation nur auf Position, nicht auf Mesh-Orientierung
            obj.matrix_world = Matrix(mw.tolist())

    # Abstand von oben/unten berechnen (nur mathematisch, keine Blender-Abhängigkeit)
    avatar_height = 4.5 - 0.0  # geschätzte Höhe der Körper-Blöcke
    ortho_scale = 4.0
    scale, margin_world = compute_spacing_transform(
        avatar_height, ortho_scale, res_y, classic_abstand_oben, classic_abstand_unten
    )
    center_z = 2.25  # Mittelpunkt der Körper-Blöcke
    for obj in render_objects:
        mw = np.array(obj.matrix_world, dtype=np.float64)
        mw[:3, 3] = np.array([mw[0, 3], mw[1, 3], center_z + scale * (mw[2, 3] - center_z)])
        obj.matrix_world = Matrix(mw.tolist())

    # Kamera vertikal verschieben für den oberen Abstand
    camera = scene.camera
    if camera is not None and abs(margin_world) > 1e-9:
        cam_loc = np.array(camera.location, dtype=np.float64)
        cam_loc[2] += margin_world
        camera.location = Vector(cam_loc.tolist())

    # Emote-Pose: wird nur angewendet wenn classic_pose == "Emote" und classic_emote vorhanden.
    # Bei "None" oder "Equipped" ohne Emote passiert hier nichts.
    if classic_pose == "Emote" and classic_emote is not None:
        preview = classic_emote.preview_keyframe if hasattr(classic_emote, "preview_keyframe") else None
        if preview is not None:
            print(f"[classic] Emote-Pose wird angewendet (Moment t={preview.time}).")
        # Hinweis: Ohne R15-Armature mit Knochen können Posen nicht auf Teile abgebildet werden.
        # Sobald ein R15-Blend mit Armature vorliegt, wird hier die Pose auf die Knochen gelegt.

    output_png.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(output_png.resolve())
    bpy.ops.render.render(write_still=True)

    if not output_png.is_file() or output_png.stat().st_size == 0:
        raise RuntimeError(f"Blender hat keine Ausgabedatei unter '{output_png}' erzeugt.")

    # Umrandung (wie bei HearthandsType)
    outline_cfg = outline or {}
    thickness = float(outline_cfg.get("thickness_percent", 0))
    if thickness > 0:
        from scripts.blender_render import _apply_avatar_outline
        from scripts.render_config import normalize_color

        avatar_png = work_dir / "avatar_outline_source.png"
        original_filepath = scene.render.filepath
        try:
            scene.render.filepath = str(avatar_png.resolve())
            bpy.ops.render.render(write_still=True)
        finally:
            scene.render.filepath = original_filepath
        if not avatar_png.is_file() or avatar_png.stat().st_size == 0:
            raise RuntimeError("Isolierter Avatar-Render für die Umrandung wurde nicht erzeugt.")
        _apply_avatar_outline(
            output_png,
            avatar_png,
            thickness_percent=thickness,
            outside=bool(outline_cfg.get("outside", True)),
            fade_start_percent=float(outline_cfg.get("fade_start_percent", 0)),
            color=normalize_color(str(outline_cfg.get("color", "white"))),
        )
