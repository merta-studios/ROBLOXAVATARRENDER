"""Blender 5.x EEVEE Szene-Builder & Renderer für WomanRig.blend."""

from __future__ import annotations

import math
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector
from PIL import Image

from scripts.rbx_formats import IDENTITY_CFRAME, MeshData
from scripts.roblox_opencloud import AccessorySpec, AvatarBundle, HeadSpec

# Basiswechsel Roblox (+X=Rechts, +Y=Oben, +Z=Hinten) <-> Blender (-X=CharRechts, +Y=Hinten, +Z=Oben)
# det(T) = +1.0, T @ T = I (reine orthogonale Transformation ohne Spiegelung/Normalen-Flip)
T_RBX_TO_BL = np.array(
    [
        [-1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ],
    dtype=np.float64,
)

# Exakte R6-Rig-Attachment-Positionen (in Roblox-Weltkoordinaten passend zum geposten WomanRig.blend)
# Head-Zentrum = (0, 4.5, 0)_rbx, Torso-Zentrum = (0, 3.0, 0)_rbx
RIG_ATTACHMENTS_RBX: dict[str, tuple[float, float, float]] = {
    # Kopf-Attachments (Head Center = 0, 4.5, 0)
    "HatAttachment": (0.0, 5.1, 0.0),
    "HairAttachment": (0.0, 5.1, 0.0),
    "FaceFrontAttachment": (0.0, 4.5, -0.6),
    "FaceCenterAttachment": (0.0, 4.5, 0.0),
    "__LEGACY_HAT__": (0.0, 5.0, 0.0),
    # Torso-Attachments (abgestimmt auf WomanRig Torso: x=±0.663, z_rbx=-0.48..+0.36, y_rbx=2.24..4.08)
    "NeckAttachment": (0.0, 4.0, 0.0),
    "BodyFrontAttachment": (0.0, 3.0, -0.48),
    "BodyBackAttachment": (0.0, 3.0, 0.37),
    "LeftCollarAttachment": (-0.72, 3.92, 0.0),
    "RightCollarAttachment": (0.72, 3.92, 0.0),
    "WaistCenterAttachment": (0.0, 2.24, 0.0),
    "WaistFrontAttachment": (0.0, 2.24, -0.45),
    "WaistBackAttachment": (0.0, 2.24, 0.35),
    # Schulter- & Arm-Attachments (passend zu den geposten Armen von WomanRig.blend)
    "RightShoulderAttachment": (0.86, 3.92, -0.03),
    "LeftShoulderAttachment": (-0.86, 3.92, -0.03),
    "RightGripAttachment": (1.08, 2.08, -0.10),
    "LeftGripAttachment": (-1.45, 2.22, -0.10),
    # Fuß-Attachments
    "RightFootAttachment": (0.50, 0.12, 0.0),
    "LeftFootAttachment": (-0.50, 0.12, 0.0),
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


def _srgb_to_linear(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _cframe_to_mat4(cf: tuple[float, ...]) -> np.ndarray:
    px, py, pz, r00, r01, r02, r10, r11, r12, r20, r21, r22 = cf
    return np.array(
        [
            [r00, r01, r02, px],
            [r10, r11, r12, py],
            [r20, r21, r22, pz],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _translation_mat4(x: float, y: float, z: float) -> np.ndarray:
    m = np.eye(4, dtype=np.float64)
    m[0, 3], m[1, 3], m[2, 3] = x, y, z
    return m


def _adjustment_mat4(
    pos: tuple[float, float, float],
    rot_deg: tuple[float, float, float],
) -> np.ndarray:
    rx, ry, rz = (math.radians(rot_deg[0]), math.radians(rot_deg[1]), math.radians(rot_deg[2]))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float64)
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    rot = mx @ my @ mz
    out = np.eye(4, dtype=np.float64)
    out[:3, :3] = rot
    out[0, 3], out[1, 3], out[2, 3] = pos
    return out


def _find_principled_and_tex(mat: bpy.types.Material):
    bsdf = None
    tex = None
    if mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED" and bsdf is None:
                bsdf = node
            elif node.type == "TEX_IMAGE" and tex is None:
                tex = node
    return bsdf, tex


def _clone_rig_material(
    template_mat: bpy.types.Material,
    name: str,
    *,
    texture_path: str | None = None,
    solid_srgb: tuple[float, float, float] = (1.0, 1.0, 1.0),
    tint_srgb: tuple[float, float, float] = (1.0, 1.0, 1.0),
    alpha_blend: bool = False,
    transparency: float = 0.0,
) -> bpy.types.Material:
    """Klont das Original-Material aus WomanRig.blend für identische Shader-Einstellungen."""
    mat = template_mat.copy()
    mat.name = name
    tree = mat.node_tree
    bsdf, tex_node = _find_principled_and_tex(mat)
    if bsdf is None or tree is None:
        return mat

    # Entferne alte Links in Base Color / Alpha, behalte aber exakt alle BSDF-Parameter des Rigs
    for sock_name in ("Base Color", "Alpha"):
        sock = bsdf.inputs.get(sock_name)
        if sock:
            for lnk in list(sock.links):
                tree.links.remove(lnk)

    alpha_val = max(0.0, min(1.0, 1.0 - transparency))
    if bsdf.inputs.get("Alpha"):
        bsdf.inputs["Alpha"].default_value = alpha_val

    if texture_path:
        if tex_node is None:
            tex_node = tree.nodes.new("ShaderNodeTexImage")
        img = bpy.data.images.load(texture_path, check_existing=True)
        img.colorspace_settings.name = "sRGB"
        tex_node.image = img
        tex_node.interpolation = "Smart"

        is_tinted = any(abs(c - 1.0) > 1e-3 for c in tint_srgb)
        if is_tinted:
            mix = tree.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 1.0
            mix.inputs[7].default_value = (
                _srgb_to_linear(tint_srgb[0]),
                _srgb_to_linear(tint_srgb[1]),
                _srgb_to_linear(tint_srgb[2]),
                1.0,
            )
            tree.links.new(tex_node.outputs["Color"], mix.inputs[6])
            tree.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
        else:
            tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])

        if alpha_blend:
            tree.links.new(tex_node.outputs["Alpha"], bsdf.inputs["Alpha"])
            if hasattr(mat, "surface_render_method"):
                mat.surface_render_method = "BLENDED"
    else:
        if tex_node is not None:
            tree.nodes.remove(tex_node)
        bsdf.inputs["Base Color"].default_value = (
            _srgb_to_linear(solid_srgb[0]),
            _srgb_to_linear(solid_srgb[1]),
            _srgb_to_linear(solid_srgb[2]),
            1.0,
        )

    if transparency > 1e-3 and hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"

    return mat


def _bake_head_composite_texture(
    skin_rgb: tuple[int, int, int],
    face_texture_path: str | None,
    out_path: Path,
) -> str:
    """Baut eine 1024x1024 sRGB Kopftextur (Hautfarbe + Gesichts-Decal im Front-UV-Feld)."""
    size = 1024
    canvas = Image.new("RGBA", (size, size), (*skin_rgb, 255))
    if face_texture_path and Path(face_texture_path).is_file():
        face_img = Image.open(face_texture_path).convert("RGBA")
        # Front-UV-Bereich [0.125 .. 0.875] x [0.125 .. 0.875] -> 768x768 Pixel zentriert
        f_resized = face_img.resize((768, 768), Image.Resampling.LANCZOS)
        canvas.alpha_composite(f_resized, dest=(128, 128))
    canvas.convert("RGB").save(out_path, format="PNG")
    return str(out_path)


def _create_classic_r6_head_mesh(name: str = "R6ClassicHeadMesh") -> bpy.types.Mesh:
    """Erzeugt das authentische klassische Roblox R6 SpecialMesh(Head) mit glatten Bevels & Front-UVs."""
    # Rotationsprofil in (r, z_rel) von -0.60 bis +0.60 (Radius 0.60, Bevel-Radius 0.28)
    profile: list[tuple[float, float]] = [(0.0, -0.60), (0.16, -0.60), (0.32, -0.60)]
    bevel_steps = 10
    for i in range(1, bevel_steps + 1):
        phi = -math.pi / 2.0 + (math.pi / 2.0) * (i / bevel_steps)
        r = 0.32 + 0.28 * math.cos(phi)
        z = -0.32 + 0.28 * math.sin(phi)
        profile.append((r, z))

    mid_steps = 8
    for i in range(1, mid_steps):
        z = -0.32 + 0.64 * (i / mid_steps)
        profile.append((0.60, z))

    for i in range(bevel_steps + 1):
        phi = (math.pi / 2.0) * (i / bevel_steps)
        r = 0.32 + 0.28 * math.cos(phi)
        z = 0.32 + 0.28 * math.sin(phi)
        profile.append((r, z))

    profile.extend([(0.16, 0.60), (0.0, 0.60)])

    seg_u = 64
    verts: list[tuple[float, float, float]] = []
    rings: list[list[int]] = []

    for r, z in profile:
        if r < 1e-6:
            idx = len(verts)
            verts.append((0.0, 0.0, z))
            rings.append([idx])
        else:
            ring_indices: list[int] = []
            for j in range(seg_u):
                theta = 2.0 * math.pi * (j / seg_u)
                verts.append((r * math.cos(theta), r * math.sin(theta), z))
                ring_indices.append(len(verts) - 1)
            rings.append(ring_indices)

    faces: list[tuple[int, ...]] = []
    for ri in range(len(rings) - 1):
        r0 = rings[ri]
        r1 = rings[ri + 1]
        if len(r0) == 1 and len(r1) > 1:
            pole = r0[0]
            for j in range(seg_u):
                jn = (j + 1) % seg_u
                faces.append((pole, r1[jn], r1[j]))
        elif len(r0) > 1 and len(r1) == 1:
            pole = r1[0]
            for j in range(seg_u):
                jn = (j + 1) % seg_u
                faces.append((r0[j], r0[jn], pole))
        elif len(r0) > 1 and len(r1) > 1:
            for j in range(seg_u):
                jn = (j + 1) % seg_u
                faces.append((r0[j], r0[jn], r1[jn], r1[j]))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    for poly in mesh.polygons:
        poly.use_smooth = True

    uv_layer = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        # Front-Halbkugel zeigt in Richtung Kamera (-Y_bl)
        is_front = poly.center.y < -0.02 and abs(poly.normal.z) < 0.92
        for loop_idx in poly.loop_indices:
            v = mesh.vertices[mesh.loops[loop_idx].vertex_index].co
            if is_front:
                u = 0.125 + 0.75 * ((v.x + 0.60) / 1.20)
                vv = 0.125 + 0.75 * ((v.z + 0.60) / 1.20)
                uv_layer.data[loop_idx].uv = (
                    max(0.125, min(0.875, u)),
                    max(0.125, min(0.875, vv)),
                )
            else:
                uv_layer.data[loop_idx].uv = (0.02, 0.02)

    return mesh


def _build_blender_mesh_from_rbx(
    name: str,
    mdata: MeshData,
    scale_rbx: tuple[float, float, float],
    offset_rbx: tuple[float, float, float],
    *,
    project_front_face_uv: bool = False,
) -> bpy.types.Mesh:
    """Konvertiert ein geparstes Roblox MeshData direkt in ein Blender-Mesh inkl. UVs & Custom Normals."""
    sx, sy, sz = scale_rbx
    ox, oy, oz = offset_rbx

    verts_bl: list[tuple[float, float, float]] = []
    for px, py, pz in mdata.positions:
        rx = px * sx + ox
        ry = py * sy + oy
        rz = pz * sz + oz
        # T_RBX_TO_BL: (x_bl, y_bl, z_bl) = (-rx, rz, ry)
        verts_bl.append((-rx, rz, ry))

    # Degenerierte Dreiecke herausfiltern
    valid_faces: list[tuple[int, int, int]] = []
    n_verts = len(verts_bl)
    for f0, f1, f2 in mdata.faces:
        if 0 <= f0 < n_verts and 0 <= f1 < n_verts and 0 <= f2 < n_verts:
            if f0 != f1 and f1 != f2 and f0 != f2:
                valid_faces.append((f0, f1, f2))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts_bl, [], valid_faces)
    mesh.update()

    for poly in mesh.polygons:
        poly.use_smooth = True

    uv_layer = mesh.uv_layers.new(name="UVMap")
    if project_front_face_uv and verts_bl:
        arr = np.asarray(verts_bl, dtype=np.float64)
        min_v = arr.min(axis=0)
        max_v = arr.max(axis=0)
        span_x = max(float(max_v[0] - min_v[0]), 0.01)
        span_z = max(float(max_v[2] - min_v[2]), 0.01)
        mid_y = float(0.5 * (min_v[1] + max_v[1]))
        for poly in mesh.polygons:
            is_front = poly.center.y < mid_y and poly.normal.y < -0.1
            for loop_idx in poly.loop_indices:
                v = mesh.vertices[mesh.loops[loop_idx].vertex_index].co
                if is_front:
                    u = 0.125 + 0.75 * ((v.x - min_v[0]) / span_x)
                    vv = 0.125 + 0.75 * ((v.z - min_v[2]) / span_z)
                    uv_layer.data[loop_idx].uv = (
                        max(0.125, min(0.875, u)),
                        max(0.125, min(0.875, vv)),
                    )
                else:
                    uv_layer.data[loop_idx].uv = (0.02, 0.02)
    else:
        uvs = mdata.uvs
        for loop in mesh.loops:
            vi = loop.vertex_index
            if vi < len(uvs):
                uv_layer.data[loop.index].uv = uvs[vi]

    if mdata.normals and len(mdata.normals) == n_verts:
        inv_sx = 1.0 / sx if abs(sx) > 1e-6 else 1.0
        inv_sy = 1.0 / sy if abs(sy) > 1e-6 else 1.0
        inv_sz = 1.0 / sz if abs(sz) > 1e-6 else 1.0
        norms_bl: list[tuple[float, float, float]] = []
        for nx, ny, nz in mdata.normals:
            gx, gy, gz = nx * inv_sx, ny * inv_sy, nz * inv_sz
            bx, by, bz = -gx, gz, gy
            ln = math.sqrt(bx * bx + by * by + bz * bz)
            if ln > 1e-8:
                norms_bl.append((bx / ln, by / ln, bz / ln))
            else:
                norms_bl.append((0.0, -1.0, 0.0))
        try:
            mesh.normals_split_custom_set_from_vertices(norms_bl)
        except Exception:
            pass

    return mesh


def _setup_studio_lighting_and_world(scene: bpy.types.Scene) -> None:
    """Ersetzt die fehlende EXR-Umgebung durch sauberes Studio-Licht (transparenter Hintergrund)."""
    world = scene.world or bpy.data.worlds.new("StudioWorld")
    scene.world = world
    tree = world.node_tree
    tree.nodes.clear()

    tex_coord = tree.nodes.new("ShaderNodeTexCoord")
    sep_xyz = tree.nodes.new("ShaderNodeSeparateXYZ")
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (0.35, 0.37, 0.42, 1.0)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (0.95, 0.96, 1.0, 1.0)

    bg = tree.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = 0.50
    out = tree.nodes.new("ShaderNodeOutputWorld")

    tree.links.new(tex_coord.outputs["Generated"], sep_xyz.inputs["Vector"])
    tree.links.new(sep_xyz.outputs["Z"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    tree.links.new(bg.outputs["Background"], out.inputs["Surface"])

    def add_area_light(
        name: str,
        loc: tuple[float, float, float],
        target: tuple[float, float, float],
        energy: float,
        size: float,
        color: tuple[float, float, float],
    ) -> None:
        ldata = bpy.data.lights.new(name=name, type="AREA")
        ldata.energy = energy
        ldata.size = size
        ldata.color = color
        lobj = bpy.data.objects.new(name=name, object_data=ldata)
        scene.collection.objects.link(lobj)
        lobj.location = Vector(loc)
        direction = Vector(target) - Vector(loc)
        lobj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    # Klassisches 3-Punkt GFX Studio-Setup
    add_area_light("GFX_KeyLight", (-3.2, -4.8, 5.6), (0.0, 0.0, 3.1), 155.0, 3.6, (1.0, 0.98, 0.95))
    add_area_light("GFX_FillLight", (3.6, -4.6, 3.4), (0.0, 0.0, 2.7), 85.0, 4.2, (0.93, 0.96, 1.0))
    add_area_light("GFX_RimLight", (0.4, 4.2, 5.5), (0.0, 0.0, 3.6), 115.0, 3.0, (1.0, 1.0, 1.0))


def _setup_auto_framed_camera(
    scene: bpy.types.Scene,
    render_objects: list[bpy.types.Object],
    res_x: int,
    res_y: int,
) -> None:
    """Positioniert eine 50mm Studio-Kamera exakt passend zur Bounding-Box von Rig + Accessoires."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    min_pt = np.array([1e9, 1e9, 1e9], dtype=np.float64)
    max_pt = np.array([-1e9, -1e9, -1e9], dtype=np.float64)
    found = False

    for obj in render_objects:
        if obj.hide_render or obj.type != "MESH":
            continue
        eval_obj = obj.evaluated_get(depsgraph)
        try:
            eval_mesh = eval_obj.to_mesh()
        except Exception:
            eval_mesh = None
        if eval_mesh is not None and len(eval_mesh.vertices) > 0:
            n = len(eval_mesh.vertices)
            co = np.empty(n * 3, dtype=np.float32)
            eval_mesh.vertices.foreach_get("co", co)
            pts_local = co.reshape(n, 3).astype(np.float64)
            mw = np.array(eval_obj.matrix_world, dtype=np.float64)
            pts_world = (pts_local @ mw[:3, :3].T) + mw[:3, 3]
            min_pt = np.minimum(min_pt, pts_world.min(axis=0))
            max_pt = np.maximum(max_pt, pts_world.max(axis=0))
            found = True
            eval_obj.to_mesh_clear()

    if not found:
        min_pt = np.array([-1.6, -0.8, 0.0])
        max_pt = np.array([1.6, 0.8, 5.4])

    cx = float(0.5 * (min_pt[0] + max_pt[0]))
    cz = float(0.5 * (min_pt[2] + max_pt[2]))
    span_x = float(max_pt[0] - min_pt[0])
    span_z = float(max_pt[2] - min_pt[2])
    front_y = float(min_pt[1])

    cam_data = bpy.data.cameras.new("GFX_Camera")
    cam_data.lens = 55.0
    cam_data.sensor_fit = "HORIZONTAL"
    cam_data.sensor_width = 36.0

    aspect = res_x / res_y
    half_fov_h = math.atan(0.5 * cam_data.sensor_width / cam_data.lens)
    half_fov_v = math.atan(math.tan(half_fov_h) / aspect)

    margin = 1.20
    dist_h = (0.5 * span_x * margin) / math.tan(half_fov_h)
    dist_v = (0.5 * span_z * margin) / math.tan(half_fov_v)
    dist = max(dist_h, dist_v, 6.5)

    cam_obj = bpy.data.objects.new("GFX_Camera", cam_data)
    scene.collection.objects.link(cam_obj)

    # Leichter, schmeichelhafter GFX-Kamerawinkel von vorne (-Y -> +Y)
    cam_loc = Vector((cx - 0.35, front_y - dist, cz + 0.18))
    target = Vector((cx, 0.0, cz + 0.02))
    cam_obj.location = cam_loc
    cam_obj.rotation_euler = (target - cam_loc).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam_obj


def render_avatar_scene(
    blend_path: Path,
    bundle: AvatarBundle,
    output_png: Path,
    work_dir: Path,
    res_x: int = 1080,
    res_y: int = 1350,
    _dry_run: bool = False,
) -> None:
    """Öffnet WomanRig.blend, texturiert Körperteile, platziert Kopf & Accessoires und rendert mit EEVEE."""
    bpy.ops.wm.open_mainfile(filepath=str(blend_path.resolve()))
    scene = bpy.context.scene

    # 1. Verstecke das Wireframe-Hilfsobjekt 'WomanRig', behalte alle geposten Körperteile
    rig_body_objects: list[bpy.types.Object] = []
    for obj in bpy.data.objects:
        if obj.name.startswith("Rigcreationsgirl") and obj.type == "MESH":
            obj.hide_render = False
            rig_body_objects.append(obj)
        elif obj.name == "WomanRig":
            obj.hide_render = True
            obj.hide_viewport = True

    if not rig_body_objects:
        raise RuntimeError("Keine 'Rigcreationsgirl*' Körper-Meshes in WomanRig.blend gefunden.")

    template_mat = bpy.data.materials.get("Material") or rig_body_objects[0].active_material
    if template_mat is None:
        raise RuntimeError("Kein Basis-Material in WomanRig.blend gefunden.")

    # 2. Wende zusammengesetzte R6-Avatar-Textur auf alle bestehenden Körperteile an
    body_img = bpy.data.images.load(bundle.body_texture_path, check_existing=True)
    body_img.colorspace_settings.name = "sRGB"
    visited_mats: set[str] = set()
    for bobj in rig_body_objects:
        for slot in bobj.material_slots:
            mat = slot.material
            if mat is None or mat.name in visited_mats or not mat.node_tree:
                continue
            visited_mats.add(mat.name)
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE":
                    node.image = body_img
                    node.interpolation = "Smart"

    target_collection = rig_body_objects[0].users_collection[0] if rig_body_objects[0].users_collection else scene.collection
    render_objects: list[bpy.types.Object] = list(rig_body_objects)

    # 3. Kopf auf dem geposten Rig platzieren (Zentrum = (0, 0, 4.5)_bl = (0, 4.5, 0)_rbx)
    head_spec: HeadSpec = bundle.head
    if head_spec.kind != "headless":
        if head_spec.kind == "custom_mesh" and head_spec.mesh is not None:
            use_front_proj = head_spec.custom_texture_path is None and head_spec.face_texture_path is not None
            h_mesh = _build_blender_mesh_from_rbx(
                "AvatarHeadMesh",
                head_spec.mesh,
                head_spec.mesh_scale,
                head_spec.mesh_offset,
                project_front_face_uv=use_front_proj,
            )
            if head_spec.custom_texture_path:
                head_tex = head_spec.custom_texture_path
            elif use_front_proj:
                head_tex = _bake_head_composite_texture(
                    head_spec.skin_rgb,
                    head_spec.face_texture_path,
                    work_dir / "head_composite.png",
                )
            else:
                head_tex = None
        else:
            h_mesh = _create_classic_r6_head_mesh("AvatarClassicHeadMesh")
            head_tex = _bake_head_composite_texture(
                head_spec.skin_rgb,
                head_spec.face_texture_path,
                work_dir / "head_composite.png",
            )

        skin_srgb = (
            head_spec.skin_rgb[0] / 255.0,
            head_spec.skin_rgb[1] / 255.0,
            head_spec.skin_rgb[2] / 255.0,
        )
        head_mat = _clone_rig_material(
            template_mat,
            "AvatarHeadMaterial",
            texture_path=head_tex,
            solid_srgb=skin_srgb,
        )
        h_mesh.materials.append(head_mat)
        head_obj = bpy.data.objects.new("AvatarHead", h_mesh)
        target_collection.objects.link(head_obj)
        head_obj.location = Vector((0.0, 0.0, 4.5))
        render_objects.append(head_obj)

    # 4. Alle starren R6-Accessoires mit denselben Material-Einstellungen platzieren
    for idx, acc in enumerate(bundle.accessories):
        eff_scale = (
            acc.mesh_scale[0] * acc.meta_scale[0],
            acc.mesh_scale[1] * acc.meta_scale[1],
            acc.mesh_scale[2] * acc.meta_scale[2],
        )
        eff_offset = (
            acc.mesh_offset[0] * acc.meta_scale[0],
            acc.mesh_offset[1] * acc.meta_scale[1],
            acc.mesh_offset[2] * acc.meta_scale[2],
        )
        acc_mesh = _build_blender_mesh_from_rbx(
            f"AccMesh_{acc.asset_id}_{idx}",
            acc.mesh,
            eff_scale,
            eff_offset,
        )

        acc_mat = _clone_rig_material(
            template_mat,
            f"AccMat_{acc.asset_id}_{idx}",
            texture_path=acc.texture_path,
            solid_srgb=acc.base_color,
            tint_srgb=acc.tint_color,
            alpha_blend=acc.alpha_blend,
            transparency=acc.transparency,
        )
        acc_mesh.materials.append(acc_mat)

        # Berechne Handle-CFrame im Roblox-Raum: C_rig @ C_adj @ inv(C_acc)
        if acc.attachment_name and acc.attachment_name in RIG_ATTACHMENTS_RBX:
            rig_pos = RIG_ATTACHMENTS_RBX[acc.attachment_name]
            c_rig = _translation_mat4(*rig_pos)
            c_acc = _cframe_to_mat4(acc.attachment_cframe)
        elif acc.attachment_name is None and acc.legacy_attachment_point != IDENTITY_CFRAME:
            rig_pos = RIG_ATTACHMENTS_RBX["__LEGACY_HAT__"]
            c_rig = _translation_mat4(*rig_pos)
            c_acc = _cframe_to_mat4(acc.legacy_attachment_point)
        else:
            fb_name = FALLBACK_ATTACHMENT_BY_TYPE.get(acc.asset_type_id, "HatAttachment")
            rig_pos = RIG_ATTACHMENTS_RBX[fb_name]
            c_rig = _translation_mat4(*rig_pos)
            c_acc = _cframe_to_mat4(
                acc.attachment_cframe if acc.attachment_name else acc.legacy_attachment_point
            )

        c_adj = _adjustment_mat4(acc.meta_pos, acc.meta_rot_deg)
        try:
            c_acc_inv = np.linalg.inv(c_acc)
        except np.linalg.LinAlgError:
            c_acc_inv = np.eye(4, dtype=np.float64)

        c_handle_rbx = c_rig @ c_adj @ c_acc_inv
        c_handle_bl = T_RBX_TO_BL @ c_handle_rbx @ T_RBX_TO_BL

        acc_obj = bpy.data.objects.new(f"Accessory_{acc.asset_id}_{acc.name[:24]}", acc_mesh)
        target_collection.objects.link(acc_obj)
        acc_obj.matrix_world = Matrix(c_handle_bl.tolist())
        render_objects.append(acc_obj)

    # 5. Studio-Beleuchtung, Kamera & Blender EEVEE Render-Konfiguration (transparenter Hintergrund)
    _setup_studio_lighting_and_world(scene)
    _setup_auto_framed_camera(scene, render_objects, res_x, res_y)

    scene.render.engine = "BLENDER_EEVEE"
    scene.render.film_transparent = True
    scene.render.resolution_x = res_x
    scene.render.resolution_y = res_y
    scene.render.resolution_percentage = 100
    if hasattr(scene.render.image_settings, "media_type"):
        scene.render.image_settings.media_type = "IMAGE"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"

    if hasattr(scene, "eevee"):
        if hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = 64
        if hasattr(scene.eevee, "use_shadows"):
            scene.eevee.use_shadows = True
        if hasattr(scene.eevee, "use_raytracing"):
            scene.eevee.use_raytracing = True

    output_png.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(output_png.resolve())
    if _dry_run:
        return
    bpy.ops.render.render(write_still=True)

    if not output_png.is_file() or output_png.stat().st_size == 0:
        raise RuntimeError(f"Blender EEVEE hat keine Ausgabedatei unter '{output_png}' erzeugt.")
