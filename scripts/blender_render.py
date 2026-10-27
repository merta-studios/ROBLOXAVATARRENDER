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


# Glänzender Roblox-Kunststoff (GFX-Look): niedrige Roughness + klarer Klarlack.
GLOSS_ROUGHNESS = 0.13
GLOSS_SPECULAR_IOR_LEVEL = 0.55
GLOSS_COAT_WEIGHT = 0.32
GLOSS_COAT_ROUGHNESS = 0.06
GLOSS_IOR = 1.5


def _clone_rig_material(
    template_mat: bpy.types.Material | None,
    name: str,
    *,
    texture_path: str | None = None,
    solid_srgb: tuple[float, float, float] = (1.0, 1.0, 1.0),
    tint_srgb: tuple[float, float, float] = (1.0, 1.0, 1.0),
    alpha_blend: bool = False,
    transparency: float = 0.0,
) -> bpy.types.Material:
    """Erzeugt ein frisches, glänzendes Principled-Material für Körper/Kopf/Accessoires.

    Das Rig-Material aus `WomanRig.blend` wird bewusst NICHT kopiert: es ist ein
    komplexer Diffuse/Anisotropic-Glossy-Node-Baum, dessen Texturen auf Windows-
    Pfade zeigen und dessen Principled-Werte (u. a. Roughness 0) nicht zu einem
    Roblox-Avatar passen. Dadurch ist das Ergebnis deterministisch und hängt
    nicht von rig-spezifischen UV-, Attribut- oder Farb-Links ab.
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    if tree is None:  # pragma: no cover - defensive
        return mat
    tree.nodes.clear()

    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "AvatarPrincipled"
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    # Glänzender, nichtmetallischer Roblox-Kunststoff, unabhängig vom Albedo.
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = GLOSS_ROUGHNESS
    bsdf.inputs["IOR"].default_value = GLOSS_IOR
    bsdf.inputs["Specular IOR Level"].default_value = GLOSS_SPECULAR_IOR_LEVEL
    bsdf.inputs["Coat Weight"].default_value = GLOSS_COAT_WEIGHT
    bsdf.inputs["Coat Roughness"].default_value = GLOSS_COAT_ROUGHNESS
    if bsdf.inputs.get("Coat Tint"):
        bsdf.inputs["Coat Tint"].default_value = (1.0, 1.0, 1.0, 1.0)
    if bsdf.inputs.get("Coat IOR"):
        bsdf.inputs["Coat IOR"].default_value = GLOSS_IOR
    if bsdf.inputs.get("Sheen Weight"):
        bsdf.inputs["Sheen Weight"].default_value = 0.0
    if bsdf.inputs.get("Emission Strength"):
        bsdf.inputs["Emission Strength"].default_value = 0.0
    if bsdf.inputs.get("Transmission Weight"):
        bsdf.inputs["Transmission Weight"].default_value = 0.0
    if bsdf.inputs.get("Subsurface Weight"):
        bsdf.inputs["Subsurface Weight"].default_value = 0.0

    alpha_val = max(0.0, min(1.0, 1.0 - transparency))
    if bsdf.inputs.get("Alpha"):
        bsdf.inputs["Alpha"].default_value = alpha_val

    if texture_path:
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
        bsdf.inputs["Base Color"].default_value = (
            _srgb_to_linear(solid_srgb[0]),
            _srgb_to_linear(solid_srgb[1]),
            _srgb_to_linear(solid_srgb[2]),
            1.0,
        )

    if transparency > 1e-3 and hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"

    return mat


def _rgb_luma(rgb: tuple[float, float, float]) -> float:
    """Grobe Helligkeit (0..255) eines sRGB-Farbtripels."""
    return 0.2126 * float(rgb[0]) + 0.7152 * float(rgb[1]) + 0.0722 * float(rgb[2])


def _face_overlay_rgba(face_img: Image.Image) -> Image.Image:
    """Liefert das Gesichts-Decal als RGBA-Overlay mit transparentem Hintergrund.

    Roblox-Gesichter sind normalerweise transparente PNGs. Manche Face-Assets
    (bzw. als JPEG ausgelieferte CDN-Antworten) haben jedoch einen deckenden,
    einfarbigen Hintergrund - in der Praxis fast immer Schwarz. Ungeprüft
    über den Kopf komponiert ergibt das exakt den Fehler "Kopf ist schwarz":
    der gesamte Front-UV-Bereich wird mit der Hintergrundfarbe übermalt, nur
    Augen/Mund bleiben sichtbar.

    Deshalb: vorhandenes Alpha respektieren, sonst die Randfarbe als
    Hintergrund wegkeyen (mit weichem Rand gegen harte Kanten).
    """
    img = face_img.convert("RGBA")
    if img.getchannel("A").getextrema()[0] < 250:
        return img  # echtes Alpha vorhanden -> unverändert verwenden

    arr = np.asarray(img, dtype=np.int16)
    h, w = arr.shape[:2]
    ring = max(1, min(h, w) // 64)
    border = np.concatenate(
        [
            arr[:ring, :, :3].reshape(-1, 3),
            arr[h - ring :, :, :3].reshape(-1, 3),
            arr[:, :ring, :3].reshape(-1, 3),
            arr[:, w - ring :, :3].reshape(-1, 3),
        ]
    )
    # Kein einfarbiger Rand -> Vollbild-Artwork, nicht keyen.
    if float(border.std(axis=0).max()) > 12.0:
        return img

    bg = np.median(border.astype(np.float32), axis=0)
    dist = np.abs(arr[:, :, :3].astype(np.float32) - bg).max(axis=2)
    inner, outer = 18.0, 52.0
    alpha = np.clip((dist - inner) / (outer - inner), 0.0, 1.0)

    out = arr.copy()
    out[:, :, 3] = np.round(alpha * 255.0).astype(np.int16)
    # Transparente Pixel auf Schwarz setzen, damit beim Filtern keine
    # Hintergrundfarbe in die Kanten blutet.
    transparent = out[:, :, 3] == 0
    out[transparent, 0:3] = 0
    return Image.fromarray(out.astype(np.uint8), mode="RGBA")


def _bake_head_composite_texture(
    skin_rgb: tuple[int, int, int],
    face_texture_path: str | None,
    out_path: Path,
) -> str:
    """Baut eine 1024x1024 sRGB Kopftextur (Hautfarbe + Gesichts-Decal im Front-UV-Feld)."""
    size = 1024
    pad = 128
    face_size = size - 2 * pad  # Front-UV-Bereich [0.125 .. 0.875]
    canvas = Image.new("RGBA", (size, size), (*skin_rgb, 255))
    if face_texture_path and Path(face_texture_path).is_file():
        face_img = _face_overlay_rgba(Image.open(face_texture_path))
        # Front-UV-Bereich [0.125 .. 0.875] x [0.125 .. 0.875] -> 768x768 Pixel zentriert
        f_resized = face_img.resize((face_size, face_size), Image.Resampling.LANCZOS)
        canvas.alpha_composite(f_resized, dest=(pad, pad))

        # Sicherheitsnetz: Die Kopfhaut darf niemals durch das Decal abdunkeln.
        # Ein korrektes Face bedeckt nur Augen/Mund (mittlere Helligkeit bleibt
        # nahe der Hautfarbe); ein deckender Hintergrund würde die komplette
        # Frontfläche einfärben.
        front = np.asarray(canvas.convert("RGB"), dtype=np.float32)[
            pad : size - pad, pad : size - pad
        ]
        skin_luma = max(_rgb_luma(skin_rgb), 1.0)
        front_luma = float(
            0.2126 * front[:, :, 0].mean()
            + 0.7152 * front[:, :, 1].mean()
            + 0.0722 * front[:, :, 2].mean()
        )
        if front_luma < 0.55 * skin_luma:
            print(
                "WARN: Gesichts-Decal verdunkelt die Kopfhaut "
                f"(Decal-Helligkeit {front_luma:.1f} vs. Haut {skin_luma:.1f}) - "
                "Decal wird verworfen, damit der Kopf die korrekte Hautfarbe behält."
            )
            canvas = Image.new("RGBA", (size, size), (*skin_rgb, 255))
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
    """Studio-Licht mit klar definierten Reflexen (glossy Kunststoff-Look).

    Roblox-Avatare sind lackierter Kunststoff: Der Glanz entsteht nicht nur
    über die Roughness, sondern vor allem über die *Umgebung*, die sich im
    Material spiegelt. Eine weiche Verlaufs-Umgebung ohne Kanten wirkt immer
    matt - deshalb hat die Welt hier eine harte Horizontkante (Reflexionslinie)
    und die Lampen sind kleiner und heller (definierte Specular-Highlights).
    """
    world = scene.world or bpy.data.worlds.new("StudioWorld")
    scene.world = world
    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()

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

    def add_area_light(
        name: str,
        loc: tuple[float, float, float],
        target: tuple[float, float, float],
        energy: float,
        size: float,
        color: tuple[float, float, float],
        shape: str = "SQUARE",
    ) -> None:
        ldata = bpy.data.lights.new(name=name, type="AREA")
        ldata.energy = energy
        ldata.size = size
        ldata.shape = shape
        ldata.color = color
        lobj = bpy.data.objects.new(name=name, object_data=ldata)
        scene.collection.objects.link(lobj)
        lobj.location = Vector(loc)
        direction = Vector(target) - Vector(loc)
        lobj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    # GFX-Setup: grosse, weiche Softboxen -> gleichmäßiger Glanz statt harter
    # weisser Rechteck-Reflexe auf dem Kunststoff.
    add_area_light("GFX_KeyLight", (-2.8, -4.6, 5.4), (0.0, 0.0, 3.4), 150.0, 3.4,
                   (1.0, 0.98, 0.95), shape="DISK")
    add_area_light("GFX_FillLight", (3.6, -4.2, 2.9), (0.0, 0.0, 2.8), 90.0, 4.2,
                   (0.92, 0.95, 1.0), shape="DISK")
    add_area_light("GFX_RimLight", (0.8, 3.8, 5.4), (0.0, 0.0, 3.6), 130.0, 2.6,
                   (1.0, 1.0, 1.0), shape="DISK")
    add_area_light("GFX_TopSoftbox", (0.0, -1.2, 7.4), (0.0, 0.0, 4.4), 110.0, 3.4,
                   (1.0, 1.0, 1.0), shape="DISK")
    add_area_light("GFX_Kicker", (3.0, -1.4, 4.4), (0.0, 0.0, 3.8), 55.0, 1.6,
                   (1.0, 0.99, 0.97), shape="DISK")


def _log_scene_diagnostics(scene: bpy.types.Scene, render_objects: list[bpy.types.Object]) -> None:
    """Kompakte Diagnose in den Workflow-Logs (hilft beim Beheben von Render-Fehlern)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    print(f"[render] Objekte im Render: {len(render_objects)}")
    for obj in render_objects:
        if obj.type != "MESH":
            continue
        eval_obj = obj.evaluated_get(depsgraph)
        try:
            mesh = eval_obj.to_mesh()
        except Exception:
            mesh = None
        if mesh is None:
            continue
        n = len(mesh.vertices)
        if n == 0:
            eval_obj.to_mesh_clear()
            continue
        co = np.empty(n * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        pts = co.reshape(n, 3).astype(np.float64)
        mw = np.array(eval_obj.matrix_world, dtype=np.float64)
        world = (pts @ mw[:3, :3].T) + mw[:3, 3]
        lo, hi = world.min(axis=0), world.max(axis=0)
        mats = [slot.material.name for slot in obj.material_slots if slot.material]
        print(
            f"[render]   {obj.name[:38]:38s} x[{lo[0]:+.2f},{hi[0]:+.2f}] "
            f"z[{lo[2]:+.2f},{hi[2]:+.2f}] y[{lo[1]:+.2f},{hi[1]:+.2f}] mats={mats}"
        )
        eval_obj.to_mesh_clear()

    for mat in bpy.data.materials:
        if not mat.name.startswith(("AvatarBody_", "AvatarHead", "AccMat_")):
            continue
        bsdf, tex = _find_principled_and_tex(mat)
        img = None
        if tex is not None and tex.image is not None:
            img = f"{tex.image.name} {tex.image.size[0]}x{tex.image.size[1]}"
        rough = None
        if bsdf is not None and bsdf.inputs.get("Roughness"):
            rough = round(bsdf.inputs["Roughness"].default_value, 3)
        print(f"[render]   MAT {mat.name[:38]:38s} roughness={rough} tex={img}")
        if mat.name.startswith("AvatarHead") and tex is not None and tex.image is not None:
            try:
                px = np.empty(len(tex.image.pixels), dtype=np.float32)
                tex.image.pixels.foreach_get(px)
                rgb = px.reshape(-1, 4)[:, :3]
                mean = rgb.mean(axis=0)
                print(
                    "[render]   Kopf-Textur Mittelwert (linear): "
                    f"R={mean[0]:.3f} G={mean[1]:.3f} B={mean[2]:.3f}"
                )
            except Exception as exc:  # pragma: no cover - nur Diagnose
                print(f"[render]   Kopf-Textur konnte nicht gelesen werden: {exc}")

    others = [o for o in bpy.data.objects if o not in render_objects]
    print(f"[render] Nicht gerenderte Szenen-Objekte: {len(others)}")
    for obj in others:
        print(
            f"[render]   {obj.name[:30]:30s} type={obj.type:9s} "
            f"hide_render={obj.hide_render} hide_viewport={obj.hide_viewport}"
        )


def _setup_auto_framed_camera(
    scene: bpy.types.Scene,
    render_objects: list[bpy.types.Object],
    res_x: int,
    res_y: int,
) -> None:
    """Enger Oberkörper-Ausschnitt mit 2,5 Prozent Abstand über Kopf/Accessoires."""
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
    # Hüfte des WomanRig liegt bei Z=2.24: nur 0.29 Einheiten Oberschenkel
    # zeigen. Füße und untere Accessoires sollen die Kamera nicht herauszoomen.
    bottom = 1.95
    top = max(float(max_pt[2]), 2.5)
    top_padding = 0.025
    frame_height = (top - bottom) / (1.0 - top_padding)
    center_z = bottom + frame_height * 0.5

    cam_data = bpy.data.cameras.new("GFX_Camera")
    # Orthografisch: die Oberkante aller Accessoires hat unabhängig von ihrer
    # Tiefe exakt denselben kleinen Abstand; keine perspektivischen Rand-Clips.
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = frame_height * max(1.0, res_x / res_y)
    cam_data.clip_end = 1000.0
    cam_obj = bpy.data.objects.new("GFX_Camera", cam_data)
    scene.collection.objects.link(cam_obj)
    cam_obj.location = Vector((cx, float(min_pt[1]) - 20.0, center_z))
    cam_obj.rotation_euler = Vector((0.0, 1.0, 0.0)).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam_obj


def render_avatar_scene(
    blend_path: Path,
    bundle: AvatarBundle,
    output_png: Path,
    work_dir: Path,
    res_x: int = 2000,
    res_y: int = 2000,
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
    body_materials: dict[str, bpy.types.Material] = {}
    for bobj in rig_body_objects:
        for slot in bobj.material_slots:
            source = slot.material
            if source is None:
                continue
            if source.name not in body_materials:
                body_materials[source.name] = _clone_rig_material(
                    source,
                    f"AvatarBody_{source.name}",
                    texture_path=bundle.body_texture_path,
                )
            slot.material = body_materials[source.name]

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
    _log_scene_diagnostics(scene, render_objects)
    _setup_studio_lighting_and_world(scene)
    _setup_auto_framed_camera(scene, render_objects, res_x, res_y)

    scene.render.engine = "BLENDER_EEVEE"
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
    scene.view_settings.look = "None"

    if hasattr(scene, "eevee"):
        if hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = 128
        if hasattr(scene.eevee, "use_shadows"):
            scene.eevee.use_shadows = True
        if hasattr(scene.eevee, "use_raytracing"):
            scene.eevee.use_raytracing = True
        # Klare Spiegelungen statt weichgezeichneter "matter" Reflexe.
        rt_opts = getattr(scene.eevee, "ray_tracing_options", None)
        if rt_opts is not None:
            for attr, value in (
                ("trace_max_roughness", 0.6),
                ("use_denoise", True),
                ("resolution_scale", "1"),
            ):
                if hasattr(rt_opts, attr):
                    try:
                        setattr(rt_opts, attr, value)
                    except Exception:
                        pass

    output_png.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(output_png.resolve())
    if _dry_run:
        return
    bpy.ops.render.render(write_still=True)

    if not output_png.is_file() or output_png.stat().st_size == 0:
        raise RuntimeError(f"Blender EEVEE hat keine Ausgabedatei unter '{output_png}' erzeugt.")
