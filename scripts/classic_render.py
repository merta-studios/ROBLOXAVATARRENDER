"""Eigener Render-Pfad fuer den GFX-Typ ``ClassicType`` (R15).

WICHTIG: Hier wird KEINE Blend-Datei geoeffnet. Die R6-Blend-Datei gehoert
ausschliesslich zum R6-Pfad HearthandsType. ClassicType baut eine komplett
leere Blender-Szene mit eigener Kamera, eigenem Licht und Ortho-Projektion.

Platzhalter: Es existiert noch kein R15-Rig-Blend. Deshalb werden die sechs
BodyColors des Avatars als farbige Bloecke dargestellt. Geladene Accessoires
sind dagegen echte Meshes mit UVs/Texturen und werden an ihren Attachments
platziert. Sobald ein R15-Rig vorhanden ist, koennen die Bloecke durch die
Armatur ersetzt werden; die Schnittstelle ``render_classic_scene`` bleibt dann
unveraendert. Emote-Keyframes werden ohne Armatur nur naeherungsweise auf die
Bloecke uebertragen -- insbesondere wird bei ``pose = "None"`` weder ein Emote
geladen noch irgendeine Herz-Pose angewendet.

Ausgewertet werden hier: ``classic_pose``, ``classic_emote``,
``classic_rotation_deg``, ``classic_abstand_oben``, ``classic_abstand_unten``,
``material_finish``, ``background_*`` und ``outline``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

try:  # bpy ist nur in der CI / in Blender vorhanden, nicht in den Pure-Python-Tests.
    import bpy
    from mathutils import Matrix, Vector
except ImportError:  # pragma: no cover - lokal ohne Blender-Systembibliotheken
    bpy = None  # type: ignore[assignment]
    Matrix = Vector = None  # type: ignore[assignment]

from scripts.gfx_layout import compute_spacing_transform
from scripts.image_compositing import apply_avatar_outline, compose_background
from scripts.rbx_formats import IDENTITY_CFRAME, MeshData
from scripts.render_config import normalize_color
from scripts.roblox_opencloud import AvatarBundle

# Vertikale Welt-Spanne der Ortho-Kamera. Die Abstaende sind Prozent der
# Avatar-Hoehe und werden relativ zu diesem Wert berechnet.
ORTHO_SCALE = 4.0
CAMERA_DISTANCE = 20.0

# Basiswechsel Roblox -> Blender: (x_bl, y_bl, z_bl) = (-rx, rz, ry).
T_RBX_TO_BL = np.array(
    [
        [-1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ],
    dtype=np.float64,
)
T3_RBX_TO_BL = T_RBX_TO_BL[:3, :3]

# Ungefaehre Attachment-Positionen in Roblox-Koordinaten. Da die Koerperbloecke
# ein Platzhalter sind, genuegt eine R15-naehere Positionierung an Kopf, Torso,
# Schultern, Huefte und Fuessen.
R15_ATTACHMENTS_RBX: dict[str, tuple[float, float, float]] = {
    "HatAttachment": (0.0, 5.05, 0.0),
    "HairAttachment": (0.0, 5.05, 0.0),
    "FaceFrontAttachment": (0.0, 4.5, -0.42),
    "FaceCenterAttachment": (0.0, 4.5, 0.0),
    "NeckAttachment": (0.0, 3.95, 0.0),
    "BodyFrontAttachment": (0.0, 3.0, -0.38),
    "BodyBackAttachment": (0.0, 3.0, 0.34),
    "LeftCollarAttachment": (-0.55, 3.82, 0.0),
    "RightCollarAttachment": (0.55, 3.82, 0.0),
    "LeftShoulderAttachment": (-0.82, 3.78, -0.03),
    "RightShoulderAttachment": (0.82, 3.78, -0.03),
    "WaistCenterAttachment": (0.0, 2.33, 0.0),
    "WaistFrontAttachment": (0.0, 2.33, -0.35),
    "WaistBackAttachment": (0.0, 2.33, 0.3),
    "LeftFootAttachment": (-0.25, 0.62, 0.0),
    "RightFootAttachment": (0.25, 0.62, 0.0),
    "__LEGACY_HAT__": (0.0, 4.95, 0.0),
}

FALLBACK_ATTACHMENT_BY_TYPE: dict[int, str] = {
    8: "HatAttachment",
    41: "HairAttachment",
    42: "FaceFrontAttachment",
    43: "NeckAttachment",
    44: "RightShoulderAttachment",
    45: "BodyFrontAttachment",
    46: "BodyBackAttachment",
    47: "WaistBackAttachment",
}

# (Name, Groesse (x,y,z), Mittelpunkt (x,y,z), BodyColors-Key) in Blender-Koordinaten.
BODY_BLOCKS: tuple[tuple[str, tuple[float, float, float], tuple[float, float, float], str], ...] = (
    ("Head", (0.66, 0.66, 0.66), (0.0, 0.0, 4.5), "head"),
    ("Torso", (0.92, 0.56, 1.2), (0.0, 0.0, 3.0), "torso"),
    ("RightArm", (0.34, 0.34, 1.18), (-0.64, 0.0, 3.0), "rightArm"),
    ("LeftArm", (0.34, 0.34, 1.18), (0.64, 0.0, 3.0), "leftArm"),
    ("RightLeg", (0.36, 0.36, 1.18), (-0.25, 0.0, 1.22), "rightLeg"),
    ("LeftLeg", (0.36, 0.36, 1.18), (0.25, 0.0, 1.22), "leftLeg"),
)

DEFAULT_BODY_RGB: dict[str, tuple[int, int, int]] = {
    "head": (245, 205, 48),
    "torso": (13, 105, 172),
    "rightArm": (245, 205, 48),
    "leftArm": (245, 205, 48),
    "rightLeg": (164, 189, 71),
    "leftLeg": (164, 189, 71),
}


def _require_bpy() -> None:
    if bpy is None:
        raise RuntimeError(
            "ClassicType benoetigt bpy (Blender 5.x). Die reine Python-Logik "
            "kann ohne Blender getestet werden, der Render nicht."
        )


def _srgb_to_linear(channel: float) -> float:
    c = max(0.0, min(1.0, float(channel)))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _translation_mat4(x: float, y: float, z: float) -> np.ndarray:
    mat = np.eye(4, dtype=np.float64)
    mat[0, 3], mat[1, 3], mat[2, 3] = x, y, z
    return mat


def _rotation_z_mat4(rad: float) -> np.ndarray:
    c, s = math.cos(rad), math.sin(rad)
    mat = np.eye(4, dtype=np.float64)
    mat[0, 0], mat[0, 1] = c, -s
    mat[1, 0], mat[1, 1] = s, c
    return mat


def _scale_mat4(scale: float) -> np.ndarray:
    return np.diag([scale, scale, scale, 1.0]).astype(np.float64)


def _cframe_to_mat4(cf: tuple[float, ...]) -> np.ndarray:
    """Roblox-CFrame (Position + 3x3-Rotation) als homogene 4x4-Matrix."""
    values = tuple(cf) if cf is not None and len(cf) == 12 else IDENTITY_CFRAME
    px, py, pz, r00, r01, r02, r10, r11, r12, r20, r21, r22 = values
    return np.array(
        [
            [r00, r01, r02, px],
            [r10, r11, r12, py],
            [r20, r21, r22, pz],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _adjustment_mat4(
    pos: tuple[float, float, float], rot_deg: tuple[float, float, float]
) -> np.ndarray:
    rx, ry, rz = (math.radians(float(v)) for v in rot_deg)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float64)
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    out = np.eye(4, dtype=np.float64)
    out[:3, :3] = mx @ my @ mz
    out[0, 3], out[1, 3], out[2, 3] = (float(pos[0]), float(pos[1]), float(pos[2]))
    return out


def _build_empty_scene(res_x: int, res_y: int) -> tuple[Any, Any]:
    """Erstellt eine leere Szene (kein Blend-File!) mit Kamera, Licht und Ortho."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.film_transparent = True
    scene.render.resolution_x = res_x
    scene.render.resolution_y = res_y
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = 1.0
    scene.render.pixel_aspect_y = 1.0
    scene.render.use_border = False
    if hasattr(scene.render.image_settings, "media_type"):
        scene.render.image_settings.media_type = "IMAGE"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "Standard"
    try:
        scene.view_settings.look = "None"
    except (TypeError, ValueError):  # pragma: no cover - je nach Blender-Version
        pass
    if hasattr(scene, "cycles"):
        scene.cycles.device = "CPU"
        scene.cycles.samples = 64
        scene.cycles.use_denoising = True
        scene.cycles.max_bounces = 4

    world = bpy.data.worlds.new("ClassicWorld")
    scene.world = world
    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    bg = tree.nodes.new("ShaderNodeBackground")
    # Nicht sichtbar (film_transparent), liefert aber eine definierte Umgebung
    # fuer die Reflexionen des Kunststoff-Materials.
    bg.inputs["Color"].default_value = (0.08, 0.09, 0.11, 1.0)
    bg.inputs["Strength"].default_value = 0.7
    out = tree.nodes.new("ShaderNodeOutputWorld")
    tree.links.new(bg.outputs["Background"], out.inputs["Surface"])

    cam_data = bpy.data.cameras.new("ClassicCamera")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ORTHO_SCALE
    cam_data.sensor_fit = "VERTICAL"
    cam_data.clip_start = 0.1
    cam_data.clip_end = 1000.0
    cam = bpy.data.objects.new("ClassicCamera", cam_data)
    scene.collection.objects.link(cam)
    cam.location = Vector((0.0, -CAMERA_DISTANCE, 2.7))
    cam.rotation_euler = (Vector((0.0, 0.0, 2.7)) - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    # Drei weiche Studio-Leuchten, alle auf den Avatar-Mittelpunkt gerichtet.
    def add_light(name: str, loc: tuple[float, float, float], energy: float, size: float) -> None:
        ldata = bpy.data.lights.new(name=name, type="AREA")
        ldata.energy = energy
        ldata.size = size
        ldata.shape = "DISK"
        lobj = bpy.data.objects.new(name=name, object_data=ldata)
        scene.collection.objects.link(lobj)
        lobj.location = Vector(loc)
        target = Vector((0.0, 0.0, 2.7))
        lobj.rotation_euler = (target - lobj.location).to_track_quat("-Z", "Y").to_euler()

    add_light("ClassicKeyLight", (-3.0, -4.5, 6.2), 260.0, 4.0)
    add_light("ClassicFillLight", (3.4, -4.0, 4.0), 180.0, 4.2)
    add_light("ClassicRimLight", (1.2, 4.0, 6.0), 200.0, 3.2)
    return scene, cam


def _solid_material(
    name: str,
    rgb_srgb: tuple[float, float, float],
    finish: str,
    *,
    texture_path: str | None = None,
    tint_srgb: tuple[float, float, float] = (1.0, 1.0, 1.0),
    alpha: float = 1.0,
) -> Any:
    """Erzeugt ein Principled-Material (Glossy/Matt) ohne Rig-Abhaengigkeit."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    is_glossy = str(finish).casefold() != "matt"
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = 0.13 if is_glossy else 0.82
    if bsdf.inputs.get("IOR"):
        bsdf.inputs["IOR"].default_value = 1.5
    if bsdf.inputs.get("Specular IOR Level"):
        bsdf.inputs["Specular IOR Level"].default_value = 0.55 if is_glossy else 0.25
    if bsdf.inputs.get("Coat Weight"):
        bsdf.inputs["Coat Weight"].default_value = 0.32 if is_glossy else 0.0
    if bsdf.inputs.get("Coat Roughness"):
        bsdf.inputs["Coat Roughness"].default_value = 0.06 if is_glossy else 1.0
    if bsdf.inputs.get("Alpha"):
        bsdf.inputs["Alpha"].default_value = max(0.0, min(1.0, float(alpha)))

    linear = tuple(
        _srgb_to_linear(max(0.0, min(1.0, float(c) * float(t))))
        for c, t in zip(rgb_srgb, tint_srgb)
    )
    if texture_path and Path(texture_path).is_file():
        tex = tree.nodes.new("ShaderNodeTexImage")
        image = bpy.data.images.load(str(Path(texture_path).resolve()), check_existing=True)
        image.colorspace_settings.name = "sRGB"
        tex.image = image
        tex.interpolation = "Smart"
        tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        tree.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        if hasattr(mat, "surface_render_method"):
            mat.surface_render_method = "BLENDED"
    else:
        bsdf.inputs["Base Color"].default_value = (*linear, 1.0)
    return mat


def _create_body_block(
    name: str,
    size: tuple[float, float, float],
    pos: tuple[float, float, float],
    material: Any,
    scene: Any,
    render_objects: list[Any],
) -> Any:
    sx, sy, sz = (v / 2.0 for v in size)
    verts = [
        (-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
        (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz),
    ]
    faces = [
        (0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
        (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
    ]
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location = Vector(pos)
    render_objects.append(obj)
    return obj


def _build_accessory_mesh(
    name: str,
    mdata: MeshData,
    scale_rbx: tuple[float, float, float],
    offset_rbx: tuple[float, float, float],
    material: Any,
) -> Any | None:
    """Konvertiert ein geladenes Roblox-Accessoire-Mesh inkl. UVs nach Blender."""
    sx, sy, sz = scale_rbx
    ox, oy, oz = offset_rbx
    verts: list[tuple[float, float, float]] = []
    for px, py, pz in mdata.positions:
        rx, ry, rz = px * sx + ox, py * sy + oy, pz * sz + oz
        verts.append((-rx, rz, ry))
    n_verts = len(verts)
    faces = [
        (f0, f1, f2)
        for f0, f1, f2 in mdata.faces
        if 0 <= f0 < n_verts and 0 <= f1 < n_verts and 0 <= f2 < n_verts
        and f0 != f1 and f1 != f2 and f0 != f2
    ]
    if not verts or not faces:
        return None

    mesh = bpy.data.meshes.new(f"{name}Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    for poly in mesh.polygons:
        poly.use_smooth = True
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        vi = loop.vertex_index
        if vi < len(mdata.uvs):
            uv_layer.data[loop.index].uv = mdata.uvs[vi]
    mesh.materials.append(material)
    return mesh


def _accessory_matrix_bl(acc: Any) -> Any:
    """Berechnet C_rig @ C_adj @ inv(C_acc) und wechselt in Blender-Koordinaten."""
    if acc.attachment_name in R15_ATTACHMENTS_RBX:
        rig_pos = R15_ATTACHMENTS_RBX[acc.attachment_name]
        acc_cf = acc.attachment_cframe
    elif acc.attachment_name is None and acc.legacy_attachment_point != IDENTITY_CFRAME:
        rig_pos = R15_ATTACHMENTS_RBX["__LEGACY_HAT__"]
        acc_cf = acc.legacy_attachment_point
    else:
        fallback = FALLBACK_ATTACHMENT_BY_TYPE.get(acc.asset_type_id, "HatAttachment")
        rig_pos = R15_ATTACHMENTS_RBX[fallback]
        acc_cf = acc.attachment_cframe if acc.attachment_name else acc.legacy_attachment_point

    c_rig = _translation_mat4(*rig_pos)
    c_acc = _cframe_to_mat4(acc_cf)
    c_adj = _adjustment_mat4(acc.meta_pos, acc.meta_rot_deg)
    try:
        c_acc_inv = np.linalg.inv(c_acc)
    except np.linalg.LinAlgError:
        c_acc_inv = np.eye(4, dtype=np.float64)
    handle_rbx = c_rig @ c_adj @ c_acc_inv
    handle_bl = T_RBX_TO_BL @ handle_rbx @ T_RBX_TO_BL
    return Matrix(handle_bl.tolist())


def _world_bbox(objects: list[Any]) -> tuple[np.ndarray, np.ndarray] | None:
    """Welt-AABB aller Mesh-Objekte ueber ihre Bound-Box-Ecken."""
    bpy.context.view_layer.update()
    lo = np.array([math.inf, math.inf, math.inf], dtype=np.float64)
    hi = np.array([-math.inf, -math.inf, -math.inf], dtype=np.float64)
    found = False
    for obj in objects:
        if obj.type != "MESH" or obj.hide_render:
            continue
        corners = np.asarray([tuple(c) for c in obj.bound_box], dtype=np.float64)
        if corners.size == 0:
            continue
        mw = np.asarray(obj.matrix_world, dtype=np.float64)
        world = corners @ mw[:3, :3].T + mw[:3, 3]
        lo = np.minimum(lo, world.min(axis=0))
        hi = np.maximum(hi, world.max(axis=0))
        found = True
    return (lo, hi) if found else None


def _pose_part_name(pose_name: str) -> str | None:
    key = str(pose_name).casefold().replace(" ", "").replace("_", "").replace("-", "")
    if "left" in key and "arm" in key:
        return "LeftArm"
    if "right" in key and "arm" in key:
        return "RightArm"
    if "left" in key and "leg" in key:
        return "LeftLeg"
    if "right" in key and "leg" in key:
        return "RightLeg"
    if "torso" in key or "root" in key:
        return "Torso"
    if "head" in key or "neck" in key:
        return "Head"
    return None


def _apply_emote_to_blocks(classic_emote: Any, body_objects: dict[str, Any]) -> None:
    """Uebertraegt Keyframe-Rotationen naeherungsweise auf die Platzhalter-Bloecke.

    Ohne R15-Armature gibt es keine Knochen-Hierarchie. Die Rotationen werden
    deshalb einzeln um den Mittelpunkt des jeweiligen Blocks angewendet. Bei
    ``pose = "None"`` wird diese Funktion gar nicht erst aufgerufen.
    """
    preview = getattr(classic_emote, "preview_keyframe", None)
    poses = getattr(preview, "poses", {}) if preview is not None else {}
    applied = 0
    for pose_name, cf in poses.items():
        part = _pose_part_name(pose_name)
        obj = body_objects.get(part) if part else None
        if obj is None or cf is None or len(cf) != 12:
            continue
        r_rbx = np.asarray(cf[3:12], dtype=np.float64).reshape(3, 3)
        r_bl = T3_RBX_TO_BL @ r_rbx @ T3_RBX_TO_BL
        rot4 = np.eye(4, dtype=np.float64)
        rot4[:3, :3] = r_bl
        pivot = np.asarray(obj.matrix_world.translation, dtype=np.float64)
        around_pivot = _translation_mat4(*pivot) @ rot4 @ _translation_mat4(*(-pivot))
        obj.matrix_world = Matrix(around_pivot.tolist()) @ obj.matrix_world
        applied += 1
    print(
        f"[classic] Emote-Pose auf {applied} Platzhalter-Bloecke angewendet "
        "(kein R15-Rig-Blend vorhanden)."
    )


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
    """Rendert ClassicType in einer eigenen Szene -- ohne R6-Blend-Datei."""
    _require_bpy()
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)

    scene, camera = _build_empty_scene(res_x, res_y)
    render_objects: list[Any] = []
    body_objects: dict[str, Any] = {}

    # 1. Platzhalter-Koerper: sechs farbige Bloecke in den BodyColors des Avatars.
    for name, size, pos, color_key in BODY_BLOCKS:
        rgb = bundle.body_colors.get(color_key, DEFAULT_BODY_RGB[color_key])
        srgb = tuple(max(0.0, min(1.0, float(c) / 255.0)) for c in rgb)
        material = _solid_material(f"Classic{name}Mat", srgb, material_finish)
        body_objects[name] = _create_body_block(
            name, size, pos, material, scene, render_objects
        )

    # 2. Geladene Accessoires als echte Meshes an ihren Attachment-Positionen.
    accessory_count = 0
    for idx, acc in enumerate(bundle.accessories):
        mdata = getattr(acc, "mesh", None)
        if mdata is None or not getattr(mdata, "positions", None):
            continue
        eff_scale = tuple(
            float(acc.mesh_scale[i]) * float(acc.meta_scale[i]) for i in range(3)
        )
        eff_offset = tuple(
            float(acc.mesh_offset[i]) * float(acc.meta_scale[i]) for i in range(3)
        )
        base = tuple(max(0.0, min(1.0, float(c))) for c in acc.base_color)
        tint = tuple(max(0.0, min(1.0, float(c))) for c in acc.tint_color)
        material = _solid_material(
            f"ClassicAccMat_{acc.asset_id}_{idx}",
            base,
            material_finish,
            texture_path=acc.texture_path,
            tint_srgb=tint,
            alpha=1.0 - float(getattr(acc, "transparency", 0.0) or 0.0),
        )
        mesh = _build_accessory_mesh(
            f"ClassicAcc_{acc.asset_id}_{idx}", mdata, eff_scale, eff_offset, material
        )
        if mesh is None:
            bpy.data.materials.remove(material)
            continue
        obj = bpy.data.objects.new(f"ClassicAccessory_{acc.asset_id}_{idx}", mesh)
        scene.collection.objects.link(obj)
        obj.matrix_world = _accessory_matrix_bl(acc)
        render_objects.append(obj)
        accessory_count += 1

    # 3. Pose. Bei "None" ist classic_emote immer None und es passiert nichts.
    if classic_pose in ("Equipped", "Emote") and classic_emote is not None:
        _apply_emote_to_blocks(classic_emote, body_objects)
    elif classic_pose not in ("None", "Equipped", "Emote"):
        print(f"[classic] Unbekannte Pose {classic_pose!r} - normales Stehen wird verwendet.")

    # 4. Drehung um die vertikale Achse + Abstaende von oben/unten.
    bbox = _world_bbox(render_objects)
    if bbox is None:
        raise RuntimeError("ClassicType: keine renderbare Avatar-Geometrie vorhanden.")
    lo, hi = bbox
    center = (lo + hi) / 2.0
    avatar_height = float(hi[2] - lo[2])
    width = float(hi[0] - lo[0])
    depth = float(hi[1] - lo[1])
    if avatar_height <= 1e-6:
        raise RuntimeError("ClassicType: die Avatar-Hoehe ist 0.")

    scale, margin_world = compute_spacing_transform(
        avatar_height,
        ORTHO_SCALE,
        res_y,
        classic_abstand_oben,
        classic_abstand_unten,
    )
    # Bei sehr breiten Accessoires zusaetzlich horizontal begrenzen. Die
    # vertikale Abstandsrechnung bleibt dabei der Massstab fuer den Rand.
    horizontal_span = ORTHO_SCALE * (res_x / max(res_y, 1))
    widest = max(width, depth, 1e-6)
    if widest * scale > horizontal_span:
        scale = horizontal_span / widest
        margin_world = (classic_abstand_oben / 100.0) * avatar_height * scale

    transform = (
        _translation_mat4(*center)
        @ _rotation_z_mat4(math.radians(classic_rotation_deg))
        @ _scale_mat4(scale)
        @ _translation_mat4(*(-center))
    )
    transform_bl = Matrix(transform.tolist())
    for obj in render_objects:
        obj.matrix_world = transform_bl @ obj.matrix_world

    final_bbox = _world_bbox(render_objects)
    if final_bbox is None:  # pragma: no cover - defensive
        raise RuntimeError("ClassicType: Avatar-Geometrie nach Transformation verschwunden.")
    final_lo, final_hi = final_bbox
    final_center = (final_lo + final_hi) / 2.0
    final_height = float(final_hi[2] - final_lo[2])
    camera_z = (
        final_center[2]
        + final_height / 2.0
        + margin_world
        - ORTHO_SCALE / 2.0
    )
    camera.location = Vector(
        (float(final_center[0]), float(final_center[1]) - CAMERA_DISTANCE, float(camera_z))
    )
    target = Vector(tuple(float(v) for v in final_center))
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.ortho_scale = ORTHO_SCALE
    camera.data.sensor_fit = "VERTICAL"
    print(
        f"[classic] {len(render_objects)} Objekte ({accessory_count} Accessoires), "
        f"Hoehe={avatar_height:.3f} -> {final_height:.3f}, Drehung={classic_rotation_deg:.1f} deg, "
        f"Rand oben={margin_world:.3f} Welt-Einheiten."
    )

    # 5. Immer transparent rendern. Hintergrund und Umrandung sind reine
    #    PNG-Nachbearbeitung und benoetigen keine zweite Szene / Blend-Datei.
    avatar_png = work_dir / "classic_avatar_rgba.png"
    if avatar_png.resolve() == output_png.resolve():
        raise RuntimeError("Ausgabepfad kollidiert mit dem temporaeren Avatar-Render.")
    scene.render.filepath = str(avatar_png.resolve())
    bpy.ops.render.render(write_still=True)
    if not avatar_png.is_file() or avatar_png.stat().st_size == 0:
        raise RuntimeError(f"Blender hat keine Ausgabedatei unter '{avatar_png}' erzeugt.")

    compose_background(
        avatar_png,
        output_png,
        kind=background_kind,
        image_path=background_path,
        value=background_value,
    )

    outline_cfg = outline or {}
    thickness = float(outline_cfg.get("thickness_percent", 0))
    if thickness > 0:
        apply_avatar_outline(
            output_png,
            avatar_png,
            thickness_percent=thickness,
            outside=bool(outline_cfg.get("outside", True)),
            fade_start_percent=float(outline_cfg.get("fade_start_percent", 0)),
            color=normalize_color(str(outline_cfg.get("color", "white"))),
        )

    if not output_png.is_file() or output_png.stat().st_size == 0:
        raise RuntimeError(f"ClassicType hat keine Ausgabedatei unter '{output_png}' erzeugt.")
