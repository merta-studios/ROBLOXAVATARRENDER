"""Eigener Render-Pfad fuer den GFX-Typ ``ClassicType`` (R15).

WICHTIG: Hier wird KEINE Blend-Datei geoeffnet. Die R6-Blend-Datei gehoert
ausschliesslich zum R6-Pfad HearthandsType. ClassicType baut eine komplett
leere Blender-Szene mit eigener Kamera, eigenem Licht und Ortho-Projektion.

Als Koerper wird der offizielle Standard-R15-Blocky-Rig von Roblox verwendet
(``Roblox/avatar`` -> ``Legacy/rigs/R15_Block.fbx``). Die 15 Meshes, ihre UVs
und die originalen Attachment-Positionen liegen einmalig aufbereitet in
``ClassicType/assets/r15_blocky.json.gz``. Es wird also weiterhin KEINE
Blend-Datei benoetigt: Die Szene entsteht komplett aus diesen Daten. Fehlende
Koerperteil-Assets im Inventar werden damit nicht mehr durch einfache Quader,
sondern exakt durch die Standard-R15-Teile ersetzt. Classic-Shirt, -Hose und
-T-Shirt (sowie das Face-Decal) werden ueber ``scripts/r15_clothing.py`` als
pro-Teil-Texturen mit neu berechneten UVs aufgebracht. Geladene Accessoires
sind ebenfalls echte Meshes mit UVs/Texturen. Emote-Keyframes werden mangels
Armatur ueber Gruppen-Empties naeherungsweise auf die R15-Teile uebertragen --
bei ``pose = "None"`` wird weder ein Emote geladen noch eine Pose angewendet.

Ausgewertet werden hier: ``classic_pose``, ``classic_emote``,
``classic_rotation_deg``, ``classic_abstand_oben``, ``classic_abstand_unten``,
``material_finish``, ``background_*`` und ``outline``.
"""

from __future__ import annotations

import functools
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
from scripts.image_compositing import compose_background
from scripts.r15_blocky_rig import R15Part, load_r15_attachments, load_r15_blocky_rig
from scripts.r15_clothing import compute_part_uvs
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

@functools.lru_cache(maxsize=1)
def _r15_attachments_rbx() -> dict[str, tuple[float, float, float]]:
    """Original-Attachments des offiziellen Rigs zurueck nach Roblox-Koordinaten.

    ``load_r15_attachments()`` liefert Blender-Koordinaten (Z oben, -Y vorne).
    Die bestehende Accessoire-Matrix rechnet in Roblox-Koordinaten, daher wird
    hier mit ``T3_RBX_TO_BL`` zuruecktransformiert. T3 ist involutorisch.
    """
    out: dict[str, tuple[float, float, float]] = {}
    for name, bl_pos in load_r15_attachments().items():
        rbx = T3_RBX_TO_BL @ np.asarray(bl_pos, dtype=np.float64)
        out[name] = (float(rbx[0]), float(rbx[1]), float(rbx[2]))
    out["__LEGACY_HAT__"] = out["HatAttachment"]
    return out


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

# Emote-Keyframes nutzen R6-Begriffe. Jeder Begriff fasst die drei (Torso: zwei)
# zusammengehoerigen R15-Teile zu einer beweglichen Gruppe zusammen.
R15_EMOTE_GROUPS: dict[str, tuple[str, ...]] = {
    "Head": ("Head",),
    "Torso": ("UpperTorso", "LowerTorso"),
    "LeftArm": ("LeftUpperArm", "LeftLowerArm", "LeftHand"),
    "RightArm": ("RightUpperArm", "RightLowerArm", "RightHand"),
    "LeftLeg": ("LeftUpperLeg", "LeftLowerLeg", "LeftFoot"),
    "RightLeg": ("RightUpperLeg", "RightLowerLeg", "RightFoot"),
}

# Welches Attachment bewegt sich mit welcher Koerpergruppe? Nur relevant,
# wenn tatsaechlich ein Emote angewendet wird.
ATTACHMENT_GROUP: dict[str, str] = {
    "HatAttachment": "Head",
    "HairAttachment": "Head",
    "FaceFrontAttachment": "Head",
    "FaceCenterAttachment": "Head",
    "NeckAttachment": "Torso",
    "BodyFrontAttachment": "Torso",
    "BodyBackAttachment": "Torso",
    "LeftCollarAttachment": "LeftArm",
    "RightCollarAttachment": "RightArm",
    "LeftShoulderAttachment": "LeftArm",
    "RightShoulderAttachment": "RightArm",
    "LeftGripAttachment": "LeftArm",
    "RightGripAttachment": "RightArm",
    "WaistFrontAttachment": "Torso",
    "WaistCenterAttachment": "Torso",
    "WaistBackAttachment": "Torso",
}

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
    
    # Studio-Umgebung mit ColorRamp für glossy Reflexionen
    tex_coord = tree.nodes.new("ShaderNodeTexCoord")
    sep_xyz = tree.nodes.new("ShaderNodeSeparateXYZ")
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "LINEAR"
    stops: list[tuple[float, tuple[float, float, float, float]]] = [
        (0.00, (0.020, 0.022, 0.028, 1.0)),  # unten: dunkler Boden
        (0.38, (0.080, 0.090, 0.110, 1.0)),
        (0.50, (0.520, 0.550, 0.620, 1.0)),  # Horizontkante = Reflexionslinie
        (0.56, (0.180, 0.190, 0.215, 1.0)),
        (1.00, (0.620, 0.650, 0.720, 1.0)),  # oben: heller Himmel/Softbox
    ]
    elements = ramp.color_ramp.elements
    while len(elements) > 1:
        elements.remove(elements[-1])
    elements[0].position, elements[0].color = stops[0]
    for pos, col in stops[1:]:
        el = elements.new(pos)
        el.color = col

    bg = tree.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = 0.75
    out = tree.nodes.new("ShaderNodeOutputWorld")

    tree.links.new(tex_coord.outputs["Generated"], sep_xyz.inputs["Vector"])
    tree.links.new(sep_xyz.outputs["Z"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    tree.links.new(bg.outputs["Background"], out.inputs["Surface"])

    cam_data = bpy.data.cameras.new("ClassicCamera")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ORTHO_SCALE
    cam_data.sensor_fit = "VERTICAL"
    cam_data.clip_start = 0.1
    cam_data.clip_end = 1000.0
    cam = bpy.data.objects.new("ClassicCamera", cam_data)
    scene.collection.objects.link(cam)
    # Kamera-Initialposition: wird später dynamisch angepasst
    cam.location = Vector((0.0, -CAMERA_DISTANCE, 0.0))
    cam.rotation_euler = (math.pi / 2, 0.0, 0.0)
    scene.camera = cam

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
        # Nur echte Alpha-Texturen muessen blended rendern; die neuen
        # R15-Teiltexturen sind opaque RGB und sollen kein Sorting-Rauschen.
        if hasattr(mat, "surface_render_method") and getattr(image, "channels", 4) == 4:
            mat.surface_render_method = "BLENDED"
    else:
        bsdf.inputs["Base Color"].default_value = (*linear, 1.0)
    return mat


def _create_r15_part(
    part: R15Part,
    material: Any,
    scene: Any,
    render_objects: list[Any],
) -> Any:
    """Erzeugt ein Mesh aus den offiziellen R15-Blocky-Rig-Daten."""
    mesh = bpy.data.meshes.new(f"{part.name}Mesh")
    mesh.from_pydata(part.vertices, [], part.faces)
    mesh.update()
    uv_layer = mesh.uv_layers.new(name="UVMap")
    part_uvs = compute_part_uvs(part)
    for loop in mesh.loops:
        if loop.index < len(part_uvs):
            uv_layer.data[loop.index].uv = part_uvs[loop.index]
    if part.name == "Head":
        for poly in mesh.polygons:
            poly.use_smooth = True
    mesh.materials.append(material)
    obj = bpy.data.objects.new(part.name, mesh)
    scene.collection.objects.link(obj)
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


def _effective_attachment_name(acc: Any) -> str:
    """Bestimmt das tatsaechlich verwendete Attachment (inkl. Legacy-Hut)."""
    attachments = _r15_attachments_rbx()
    if acc.attachment_name and acc.attachment_name in attachments:
        return str(acc.attachment_name)
    if acc.attachment_name is None and acc.legacy_attachment_point != IDENTITY_CFRAME:
        return "__LEGACY_HAT__"
    return FALLBACK_ATTACHMENT_BY_TYPE.get(int(acc.asset_type_id), "HatAttachment")


def _accessory_group(acc: Any) -> str:
    """Koerpergruppe, mit der ein Accessoire bei Emotes mitbewegt wird."""
    name = _effective_attachment_name(acc)
    if name == "__LEGACY_HAT__":
        name = "HatAttachment"
    return ATTACHMENT_GROUP.get(name, "Torso")


def _accessory_matrix_bl(acc: Any) -> Any:
    """Berechnet C_rig @ C_adj @ inv(C_acc) und wechselt in Blender-Koordinaten."""
    attachment_name = _effective_attachment_name(acc)
    rig_pos = _r15_attachments_rbx()[attachment_name]
    if attachment_name == "__LEGACY_HAT__":
        acc_cf = acc.legacy_attachment_point
    elif acc.attachment_name:
        acc_cf = acc.attachment_cframe
    else:
        acc_cf = acc.legacy_attachment_point

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


def _apply_emote_to_r15(
    classic_emote: Any,
    body_objects: dict[str, Any],
    scene: Any,
) -> dict[str, Any]:
    """Uebertraegt Keyframe-Rotationen auf Gruppen-Empties des R15-Rigs.

    Es gibt weiterhin keine Armatur mit Knochen-Hierarchie. Statt einzelne
    Bloecke zu drehen, werden die zusammengehoerigen R15-Teile einer Gruppe
    (z. B. LeftUpperArm + LeftLowerArm + LeftHand) an ein Empty gehaengt.
    Die Emote-Rotation wirkt dann auf die ganze Gruppe um ihren gemeinsamen
    Mittelpunkt. Rueckgabe sind die Empties, damit Accessoires mitbewegt
    werden koennen. Bei ``pose = "None"`` wird diese Funktion nicht aufgerufen.
    """
    preview = getattr(classic_emote, "preview_keyframe", None)
    poses = getattr(preview, "poses", {}) if preview is not None else {}
    group_empties: dict[str, Any] = {}

    for group_name, part_names in R15_EMOTE_GROUPS.items():
        group_parts = [body_objects[name] for name in part_names if name in body_objects]
        if not group_parts:
            continue
        bpy.context.view_layer.update()
        lo = np.array([math.inf, math.inf, math.inf], dtype=np.float64)
        hi = np.array([-math.inf, -math.inf, -math.inf], dtype=np.float64)
        for obj in group_parts:
            corners = np.asarray([tuple(c) for c in obj.bound_box], dtype=np.float64)
            mw = np.asarray(obj.matrix_world, dtype=np.float64)
            world = corners @ mw[:3, :3].T + mw[:3, 3]
            lo = np.minimum(lo, world.min(axis=0))
            hi = np.maximum(hi, world.max(axis=0))
        pivot = (lo + hi) / 2.0

        empty = bpy.data.objects.new(f"ClassicR15Group_{group_name}", None)
        scene.collection.objects.link(empty)
        empty.matrix_world = Matrix(_translation_mat4(*pivot).tolist())
        bpy.context.view_layer.update()
        inverse = np.linalg.inv(np.asarray(empty.matrix_world, dtype=np.float64))
        for obj in group_parts:
            obj.parent = empty
            obj.matrix_parent_inverse = Matrix(inverse.tolist())
        group_empties[group_name] = empty

    applied = 0
    for pose_name, cf in poses.items():
        part = _pose_part_name(pose_name)
        empty = group_empties.get(part) if part else None
        if empty is None or cf is None or len(cf) != 12:
            continue
        r_rbx = np.asarray(cf[3:12], dtype=np.float64).reshape(3, 3)
        r_bl = T3_RBX_TO_BL @ r_rbx @ T3_RBX_TO_BL
        rot4 = np.eye(4, dtype=np.float64)
        rot4[:3, :3] = r_bl
        pivot = np.asarray(empty.matrix_world.translation, dtype=np.float64)
        around_pivot = _translation_mat4(*pivot) @ rot4 @ _translation_mat4(*(-pivot))
        empty.matrix_world = Matrix(around_pivot.tolist()) @ empty.matrix_world
        applied += 1
    print(
        f"[classic] Emote-Pose auf {applied} von {len(group_empties)} "
        "R15-Koerpergruppen angewendet (Gruppen-Empties statt Armatur)."
    )
    return group_empties


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
    classic_pose: str = "None",
    classic_emote: Any = None,
    classic_rotation_deg: float = 0.0,
    classic_abstand_oben: float = 0.0,
    classic_abstand_unten: float = 0.0,
) -> None:
    """Rendert ClassicType in einer eigenen Szene -- ohne R6-Blend-Datei.

    Der Koerper kommt aus dem offiziellen Standard-R15-Blocky-Rig
    (``ClassicType/assets/r15_blocky.json.gz``), nicht aus Platzhalter-Quadern.
    """
    _require_bpy()
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)

    scene, camera = _build_empty_scene(res_x, res_y)
    render_objects: list[Any] = []
    body_objects: dict[str, Any] = {}
    accessory_parents: list[tuple[Any, str]] = []
    group_empties: dict[str, Any] = {}

    # 1. Offizieller Standard-R15-Blocky-Rig: 15 Meshes mit Classic-Clothing.
    #    Fehlende Koerperteil-Assets aus dem Inventar werden so exakt durch
    #    Roblox' Standardteile ersetzt, nicht durch einfache Quader. Jedes Teil
    #    hat seine eigene 3x2-Textur mit Shirt/Pants/T-Shirt bzw. Face-Decal.
    #    Bei headless (bundle.head.kind == "headless") wird der Kopf nicht gerendert.
    official_parts = load_r15_blocky_rig()
    part_textures = getattr(bundle, "r15_part_textures", {}) or {}
    is_headless = getattr(bundle.head, "kind", "") == "headless"
    
    for name, part in official_parts.items():
        # Headless: keinen Kopf rendern
        if name == "Head" and is_headless:
            continue
            
        rgb = bundle.body_colors.get(part.color_key, DEFAULT_BODY_RGB[part.color_key])
        srgb = tuple(max(0.0, min(1.0, float(c) / 255.0)) for c in rgb)
        texture_path = part_textures.get(name)
        material = _solid_material(
            f"Classic{name}Mat", srgb, material_finish, texture_path=texture_path
        )
        body_objects[name] = _create_r15_part(part, material, scene, render_objects)

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
        accessory_parents.append((obj, _accessory_group(acc)))
        accessory_count += 1

    # 3. Pose. Bei "None" ist classic_emote immer None und es passiert nichts.
    if classic_pose in ("Equipped", "Emote") and classic_emote is not None:
        group_empties = _apply_emote_to_r15(classic_emote, body_objects, scene)
        bpy.context.view_layer.update()
        for obj, group_name in accessory_parents:
            empty = group_empties.get(group_name)
            if empty is None:
                continue
            inverse = np.linalg.inv(np.asarray(empty.matrix_world, dtype=np.float64))
            obj.parent = empty
            obj.matrix_parent_inverse = Matrix(inverse.tolist())
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
    # Nur Top-Level-Objekte transformieren. Objekte, die bei einem Emote an
    # ein Gruppen-Empty gehaengt wurden, folgen ihrem Parent automatisch.
    transform_targets = [
        obj for obj in render_objects if getattr(obj, "parent", None) is None
    ] + list(group_empties.values())
    for obj in transform_targets:
        obj.matrix_world = transform_bl @ obj.matrix_world
    bpy.context.view_layer.update()

    final_bbox = _world_bbox(render_objects)
    if final_bbox is None:  # pragma: no cover - defensive
        raise RuntimeError("ClassicType: Avatar-Geometrie nach Transformation verschwunden.")
    final_lo, final_hi = final_bbox
    final_center = (final_lo + final_hi) / 2.0
    final_height = float(final_hi[2] - final_lo[2])
    
    # Kamera-Z so positionieren, dass oben abstand_oben% und unten abstand_unten% Platz ist
    # margin_world = abstand_oben% der skalierten Avatar-Höhe
    # Kamera-Zentrum = Avatar-Top + margin_top - ortho/2
    camera_z = (
        final_center[2]
        + final_height / 2.0
        + margin_world
        - ORTHO_SCALE / 2.0
    )
    camera.location = Vector(
        (float(final_center[0]), float(final_center[1]) - CAMERA_DISTANCE, float(camera_z))
    )
    # Kamera schaut geradeaus (nicht geneigt), damit die Ortho-Projektion korrekt ist
    target = Vector((float(final_center[0]), float(final_center[1]) - CAMERA_DISTANCE + 1, float(camera_z)))
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.ortho_scale = ORTHO_SCALE
    camera.data.sensor_fit = "VERTICAL"
    
    # Berechne tatsächliche Ränder für die Diagnose
    margin_top_world = margin_world
    margin_bottom_world = ORTHO_SCALE - final_height - margin_world
    print(
        f"[classic] {len(render_objects)} Objekte ({accessory_count} Accessoires), "
        f"Hoehe={avatar_height:.3f} -> {final_height:.3f}, Drehung={classic_rotation_deg:.1f} deg, "
        f"Rand oben={margin_top_world:.3f} ({margin_top_world/final_height*100:.1f}%), "
        f"Rand unten={margin_bottom_world:.3f} ({margin_bottom_world/final_height*100:.1f}%) Welt-Einheiten."
    )

    # 4b. Studio-Lichter dynamisch auf den Avatar-Mittelpunkt ausrichten
    avatar_center = Vector(tuple(float(v) for v in final_center))
    def add_area_light(name: str, loc: tuple[float, float, float], energy: float, size: float) -> None:
        ldata = bpy.data.lights.new(name=name, type="AREA")
        ldata.energy = energy
        ldata.size = size
        ldata.shape = "DISK"
        lobj = bpy.data.objects.new(name=name, object_data=ldata)
        scene.collection.objects.link(lobj)
        # Licht-Position relativ zum Avatar-Mittelpunkt
        lobj.location = Vector((
            float(final_center[0]) + loc[0],
            float(final_center[1]) + loc[1],
            float(final_center[2]) + loc[2],
        ))
        direction = avatar_center - lobj.location
        lobj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    add_area_light("ClassicKeyLight", (-3.0, -4.5, 3.5), 260.0, 4.0)
    add_area_light("ClassicFillLight", (3.4, -4.0, 1.3), 180.0, 4.2)
    add_area_light("ClassicRimLight", (1.2, 4.0, 3.3), 200.0, 3.2)

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

    if not output_png.is_file() or output_png.stat().st_size == 0:
        raise RuntimeError(f"ClassicType hat keine Ausgabedatei unter '{output_png}' erzeugt.")
