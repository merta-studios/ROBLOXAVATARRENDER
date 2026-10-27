"""Blender 5.x Cycles Szene-Builder & Renderer für WomanRig.blend."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import bpy
import numpy as np
from scipy.ndimage import distance_transform_edt
from mathutils import Matrix, Quaternion, Vector
from PIL import Image, ImageDraw

from scripts.rbx_formats import IDENTITY_CFRAME, MeshData
from scripts.roblox_opencloud import (
    AccessorySpec,
    AvatarBundle,
    HeadSpec,
    KORBLOX_RIGHT_LEG_ASSET_ID,
)

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

# Sehr kleiner gemeinsamer Versatz nach unten für nachgeladene Teile (Blender-Z).
# 0,01 Stud ≈ wenige Renderpixel und reicht für sauber anliegenden Schmuck.
AVATAR_ATTACHMENT_DROP = 0.01


def _clone_rig_material(
    template_mat: bpy.types.Material | None,
    name: str,
    *,
    texture_path: str | None = None,
    solid_srgb: tuple[float, float, float] = (1.0, 1.0, 1.0),
    tint_srgb: tuple[float, float, float] = (1.0, 1.0, 1.0),
    alpha_blend: bool = False,
    transparency: float = 0.0,
    finish: str = "Glossy",
) -> bpy.types.Material:
    """Erzeugt ein frisches, konfigurierbares Principled-Material für Avatar-Teile.

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

    # Glossy behält exakt den bisherigen Kunststoff-Look. Matt entfernt den Klarlack
    # und streut Reflexe über eine hohe Roughness.
    is_glossy = finish == "Glossy"
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = GLOSS_ROUGHNESS if is_glossy else 0.82
    bsdf.inputs["IOR"].default_value = GLOSS_IOR
    bsdf.inputs["Specular IOR Level"].default_value = GLOSS_SPECULAR_IOR_LEVEL if is_glossy else 0.25
    bsdf.inputs["Coat Weight"].default_value = GLOSS_COAT_WEIGHT if is_glossy else 0.0
    bsdf.inputs["Coat Roughness"].default_value = GLOSS_COAT_ROUGHNESS if is_glossy else 1.0
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
    border_spread = float(border.std(axis=0).max())
    bg = np.median(border.astype(np.float32), axis=0)

    if border_spread <= 12.0:
        # Gleichmäßiger Rand -> Farbe global keyen, mit weichem Übergang.
        dist = np.abs(arr[:, :, :3].astype(np.float32) - bg).max(axis=2)
        inner, outer = 18.0, 52.0
        alpha = np.clip((dist - inner) / (outer - inner), 0.0, 1.0)
    elif float(_rgb_luma(tuple(bg))) <= 72.0:
        # Manche Face-/Makeup-Thumbnails haben einen schwarzen oder verlaufenden
        # dunklen Canvas statt eines einfarbigen Hintergrunds. Ein globaler
        # Color-Key erkennt sie nicht und übermalt dann die gesamte Kopfhaut.
        # Entferne ausschließlich dunkle Pixel, die mit dem Bildrand verbunden
        # sind; dunkle Augen/Mund bleiben erhalten, da sie innen liegen.
        rgb = arr[:, :, :3].astype(np.float32)
        luma = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
        candidate = Image.fromarray(
            np.where(luma <= 96.0, 0, 255).astype(np.uint8), mode="L"
        ).copy()
        width, height = candidate.size
        edge_points = (
            [(x, 0) for x in range(width)]
            + [(x, height - 1) for x in range(width)]
            + [(0, y) for y in range(1, height - 1)]
            + [(width - 1, y) for y in range(1, height - 1)]
        )
        for point in edge_points:
            if candidate.getpixel(point) == 0:
                ImageDraw.floodfill(candidate, point, 128)
        alpha = (np.asarray(candidate) != 128).astype(np.float32)
    else:
        # Rand enthält kein plausibles dunkles Canvas und ist kein uniformer
        # Hintergrund: Das Bild ist vermutlich Vollbild-Artwork.
        return img

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


def _prepare_custom_head_texture(
    skin_rgb: tuple[int, int, int],
    texture_path: str,
    out_path: Path,
) -> str:
    """Preserve a custom head's own UV texture, filling only transparent pixels with BodyColor.

    DynamicHead ColorMaps are authored for the head mesh's UV layout. Replacing
    them with a projected classic face distorts or erases the actual face. When
    the map contains alpha, its transparent areas are the skin-tone mask rather
    than holes in the head, so fill those areas with the avatar's HeadColor.
    """
    image = Image.open(texture_path).convert("RGBA")
    if image.getchannel("A").getextrema()[0] < 255:
        skin = Image.new("RGBA", image.size, (*skin_rgb, 255))
        skin.alpha_composite(image)
        skin.convert("RGB").save(out_path, format="PNG")
        return str(out_path)
    return texture_path


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


def _setup_studio_lighting_and_world(
    scene: bpy.types.Scene, *, add_lights: bool = True
) -> None:
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
    # Die gespeicherte HDRI wird aus dem aktiven Shader-Baum entfernt.
    # Fehlende/verschobene EXR-Dateien erzeugen in Blender ein magenta Bild.
    old_hdri_images = [
        node.image for node in tree.nodes
        if node.type == "TEX_ENVIRONMENT" and node.image is not None
    ]
    tree.nodes.clear()
    for image in old_hdri_images:
        if image.users == 0:
            bpy.data.images.remove(image)

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

    if add_lights:
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


def _assign_background_texture(
    kind: str, image_path: str | None, value: str, work_dir: Path
) -> bpy.types.Object | None:
    """Set the image-textured material on the authored Background plane."""
    plane = bpy.data.objects.get("Background")
    if kind == "transparent":
        if plane is not None:
            plane.hide_render = True
        return plane
    if plane is None or plane.type != "MESH":
        raise RuntimeError("WomanRig.blend muss ein Mesh-Plane-Objekt namens 'Background' enthalten.")

    if kind == "color":
        from scripts.render_config import normalize_color

        rgb = normalize_color(value)
        image_path = str(work_dir / "background_color.png")
        Image.new("RGB", (4, 4), rgb).save(image_path)
    if not image_path or not Path(image_path).is_file():
        raise RuntimeError("Die Hintergrund-Bilddatei fehlt.")

    image = bpy.data.images.load(str(Path(image_path).resolve()), check_existing=True)
    image.colorspace_settings.name = "sRGB"
    material = bpy.data.materials.new("RenderBackgroundImage")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.name = "BackgroundImageTexture"
    tex.image = image
    emission = tree.nodes.new("ShaderNodeEmission")
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    mix = tree.nodes.new("ShaderNodeMixShader")
    tree.links.new(tex.outputs["Color"], emission.inputs["Color"])
    tree.links.new(tex.outputs["Alpha"], mix.inputs[0])
    tree.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    tree.links.new(emission.outputs["Emission"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    plane.data.materials.clear()
    plane.data.materials.append(material)
    plane.hide_render = False
    return plane


def _apply_avatar_outline(
    output_png: Path,
    avatar_png: Path,
    *,
    thickness_percent: float,
    outside: bool,
    fade_start_percent: float,
    color: tuple[int, int, int],
) -> None:
    """Composite a percentage-width outline from the isolated avatar alpha pass."""
    with Image.open(output_png) as rendered, Image.open(avatar_png) as avatar:
        base = rendered.convert("RGBA")
        alpha = np.asarray(avatar.convert("RGBA").getchannel("A"), dtype=np.uint8)
    if alpha.shape != (base.height, base.width):
        raise RuntimeError("Die Alpha-Maske für die Umrandung hat eine abweichende Bildgröße.")

    mask = alpha >= 128
    if not mask.any():
        raise RuntimeError("Für die Umrandung wurde kein Avatar im isolierten Render gefunden.")
    width = thickness_percent * base.width / 100.0
    distances = distance_transform_edt(~mask if outside else mask)
    valid = (~mask if outside else mask) & (distances <= width + 0.5)
    progress = np.clip((distances - 0.5) / max(width, 1e-6), 0.0, 1.0)
    fade = fade_start_percent / 100.0
    if fade >= 1.0:
        opacity = np.ones_like(progress)
    else:
        opacity = np.where(progress <= fade, 1.0, (1.0 - progress) / (1.0 - fade))
    opacity = np.where(valid, opacity, 0.0)

    overlay_arr = np.zeros((base.height, base.width, 4), dtype=np.uint8)
    overlay_arr[:, :, :3] = color
    overlay_arr[:, :, 3] = np.clip(opacity * 255.0, 0, 255).astype(np.uint8)
    overlay = Image.fromarray(overlay_arr, "RGBA")
    Image.alpha_composite(base, overlay).save(output_png, format="PNG")


def _evaluated_world_bbox(obj: bpy.types.Object) -> tuple[np.ndarray, np.ndarray] | None:
    """Welt-Achsen-aligned-Bounding-Box eines Objekts (evaluiert, inkl. Modifiers)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    try:
        if mesh is None or len(mesh.vertices) == 0:
            return None
        coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", coords)
        pts = coords.reshape(-1, 3)
        mw = np.array(eval_obj.matrix_world, dtype=np.float64)
        world = (pts @ mw[:3, :3].T) + mw[:3, 3]
        return world.min(axis=0), world.max(axis=0)
    finally:
        eval_obj.to_mesh_clear()


def _rig_body_bbox(
    objects: list[bpy.types.Object],
) -> tuple[np.ndarray, np.ndarray] | None:
    """Gesamte Welt-Bounding-Box einer Liste von Avatar-Objekten."""
    lo: np.ndarray | None = None
    hi: np.ndarray | None = None
    for obj in objects:
        bb = _evaluated_world_bbox(obj)
        if bb is None:
            continue
        b_lo, b_hi = bb
        lo = b_lo if lo is None else np.minimum(lo, b_lo)
        hi = b_hi if hi is None else np.maximum(hi, b_hi)
    if lo is None:
        return None
    return lo, hi


def _find_right_leg_object(rig_body_objects: list[bpy.types.Object]) -> bpy.types.Object | None:
    """Findet das rechte Bein des geposteten R6-Rigs.

    Beine sind die niedrig gelegenen, bein-hohen Rig-Teile. Durch die
    Roblox->Blender-Basisverschiebung liegt das rechte Bein auf der negativen
    Blender-X-Seite.
    """
    legs: list[tuple[bpy.types.Object, np.ndarray]] = []
    for obj in rig_body_objects:
        bb = _evaluated_world_bbox(obj)
        if bb is None:
            continue
        b_lo, b_hi = bb
        height = float(b_hi[2] - b_lo[2])
        center = (b_lo + b_hi) / 2.0
        if 0.3 < height < 3.5 and float(center[2]) < 1.6:
            legs.append((obj, center))
    if not legs:
        return None
    right_legs = [entry for entry in legs if entry[1][0] < 0.0]
    target = right_legs if right_legs else legs
    return min(target, key=lambda entry: entry[1][0])[0]


def _place_korblox_leg(
    target_collection: bpy.types.Collection,
    render_objects: list[bpy.types.Object],
    template_mat: bpy.types.Material,
    korblox: dict[str, Any],
    leg_obj: bpy.types.Object,
    material_finish: str,
) -> None:
    """Versteckt das originale rechte Bein des R6-Rigs und setzt das Korblox
    Deathspeaker Right Leg (Asset-ID 139607718) an dessen Stelle ein, mit
    den eigenen Materialien/Texturen des Assets."""
    leg_obj.hide_render = True

    bb = _evaluated_world_bbox(leg_obj)
    if bb is None:
        print("[korblox] Rechtes Bein hat keine Geometrie - Ersatz wird uebersprungen.")
        return
    _leg_lo, leg_hi = bb
    leg_center = (bb[0] + bb[1]) / 2.0
    leg_height = float(leg_hi[2] - bb[0][2])

    mdata: MeshData = korblox["mesh"]
    if not mdata.positions:
        print("[korblox] Korblox-Mesh ist leer - Ersatz wird uebersprungen.")
        return
    arr = np.asarray(mdata.positions, dtype=np.float64)
    # T_RBX_TO_BL: Blender-Hoehe (Z) entspricht der Roblox-Y-Extent des Beins.
    k_height = float(arr[:, 1].max() - arr[:, 1].min())
    if k_height <= 1e-6:
        k_height = 1.0
    scale = leg_height / k_height

    acc_mesh = _build_blender_mesh_from_rbx("KorbloxRightLegMesh", mdata, (scale, scale, scale), (0.0, 0.0, 0.0))
    acc_mat = _clone_rig_material(
        template_mat,
        "KorbloxRightLegMat",
        texture_path=korblox["texture_path"],
        solid_srgb=korblox["base_color"],
        finish=material_finish,
    )
    acc_mesh.materials.append(acc_mat)

    korblox_obj = bpy.data.objects.new("KorbloxRightLeg", acc_mesh)
    target_collection.objects.link(korblox_obj)
    korblox_obj.matrix_world = _translation_mat4(*leg_center)
    korblox_obj.matrix_world.translation.z -= AVATAR_ATTACHMENT_DROP
    render_objects.append(korblox_obj)
    print(
        f"[korblox] Rechtes Bein ersetzt durch Korblox Deathspeaker Right Leg "
        f"(Asset {KORBLOX_RIGHT_LEG_ASSET_ID}, Massstab {scale:.2f})."
    )


def render_avatar_scene(
    blend_path: Path,
    bundle: AvatarBundle,
    output_png: Path,
    work_dir: Path,
    res_x: int = 2000,
    res_y: int = 2000,
    _dry_run: bool = False,
    material_finish: str = "Glossy",
    background_kind: str = "transparent",
    background_path: str | None = None,
    background_value: str = "transparent",
    outline: dict | None = None,
    korblox: bool = False,
    headless: bool = False,
    korblox_asset: dict[str, Any] | None = None,
) -> None:
    """Öffnet WomanRig.blend, richtet Avatar/Background ein und rendert mit Cycles.

    Nur für den GFX-Typ 'HearthandsType' (R6, WomanRig.blend, korblox, headless).
    ClassicType verwendet scripts/classic_render.py und öffnet NICHT diese Datei.
    """
    bpy.ops.wm.open_mainfile(filepath=str(blend_path.resolve()))
    scene = bpy.context.scene
    # Entferne die externe HDRI aus dem geladenen Blend und nutze stattdessen
    # die stabile Studio-Umgebung. Die gespeicherten Softboxen bleiben erhalten.
    _setup_studio_lighting_and_world(scene, add_lights=False)
    background_plane = _assign_background_texture(
        background_kind, background_path, background_value, work_dir
    )

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
                    finish=material_finish,
                )
            slot.material = body_materials[source.name]

    target_collection = rig_body_objects[0].users_collection[0] if rig_body_objects[0].users_collection else scene.collection
    render_objects: list[bpy.types.Object] = list(rig_body_objects)

    # 3. Kopf auf dem geposten Rig platzieren (Zentrum = (0, 0, 4.5)_bl = (0, 4.5, 0)_rbx)
    head_spec: HeadSpec = bundle.head
    if head_spec.kind != "headless" and not headless:
        if head_spec.kind in {"custom_mesh", "dynamic_head"} and head_spec.mesh is not None:
            has_custom_texture = bool(
                head_spec.custom_texture_path
                and Path(head_spec.custom_texture_path).is_file()
            )
            # Native dynamic/custom head maps use their own UVs. Only heads
            # without an authored map get a projected classic R6 face fallback.
            use_front_proj = not has_custom_texture and head_spec.face_texture_path is not None
            h_mesh = _build_blender_mesh_from_rbx(
                "AvatarHeadMesh",
                head_spec.mesh,
                head_spec.mesh_scale,
                head_spec.mesh_offset,
                project_front_face_uv=use_front_proj,
            )
            if has_custom_texture:
                head_tex = _prepare_custom_head_texture(
                    head_spec.skin_rgb,
                    head_spec.custom_texture_path,
                    work_dir / "head_custom_composite.png",
                )
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
            finish=material_finish,
        )
        h_mesh.materials.append(head_mat)
        head_obj = bpy.data.objects.new("AvatarHead", h_mesh)
        target_collection.objects.link(head_obj)
        head_obj.location = Vector((0.0, 0.0, 4.5 - AVATAR_ATTACHMENT_DROP))
        render_objects.append(head_obj)

    # 4. Alle starren R6-Accessoires mit denselben Material-Einstellungen platzieren
    for idx, acc in enumerate(bundle.accessories):
        if headless and acc.asset_type_id == 42:
            # Headless-Look: Gesicht-Accessoires (Brillen, Masken u. a.) werden
            # zusammen mit dem Kopf ausgeblendet. Haare, Hüte und Hals-
            # Accessoires bleiben sichtbar.
            continue
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
            finish=material_finish,
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
        acc_obj.matrix_world.translation.z -= AVATAR_ATTACHMENT_DROP
        render_objects.append(acc_obj)

    # 4b. Korblox: rechtes R6-Bein durch das Korblox Deathspeaker Right Leg ersetzen (HearthandsType)
    if korblox and korblox_asset is not None:
        leg_obj = _find_right_leg_object(rig_body_objects)
        if leg_obj is not None:
            _place_korblox_leg(
                target_collection,
                render_objects,
                template_mat,
                korblox_asset,
                leg_obj,
                material_finish,
            )
        else:
            print("[korblox] Rechtes Rig-Bein nicht gefunden - Korblox-Ersatz wird uebersprungen.")

    # 5. Kamera, Studio-Licht, Render-Engine und unveränderliche Ausgabeoptionen
    # sind in WomanRig.blend gespeichert. Zur Laufzeit wird ausschließlich der
    # benutzerspezifische Ausgabepfad gesetzt.
    _log_scene_diagnostics(scene, render_objects)
    if scene.camera is None:
        raise RuntimeError("WomanRig.blend enthält keine gespeicherte Render-Kamera.")
    if scene.render.engine != "CYCLES":
        raise RuntimeError(
            f"WomanRig.blend muss mit Cycles gespeichert sein (gefunden: {scene.render.engine})."
        )

    output_png.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(output_png.resolve())
    if _dry_run:
        return
    bpy.ops.render.render(write_still=True)

    if not output_png.is_file() or output_png.stat().st_size == 0:
        raise RuntimeError(f"Blender hat keine Ausgabedatei unter '{output_png}' erzeugt.")

    outline_cfg = outline or {}
    thickness = float(outline_cfg.get("thickness_percent", 0))
    if thickness > 0:
        avatar_png = work_dir / "avatar_outline_source.png"
        original_filepath = scene.render.filepath
        original_hidden = background_plane.hide_render if background_plane is not None else None
        try:
            if background_plane is not None:
                background_plane.hide_render = True
            scene.render.filepath = str(avatar_png.resolve())
            bpy.ops.render.render(write_still=True)
        finally:
            scene.render.filepath = original_filepath
            if background_plane is not None and original_hidden is not None:
                background_plane.hide_render = original_hidden
        if not avatar_png.is_file() or avatar_png.stat().st_size == 0:
            raise RuntimeError("Isolierter Avatar-Render für die Umrandung wurde nicht erzeugt.")
        from scripts.render_config import normalize_color

        _apply_avatar_outline(
            output_png,
            avatar_png,
            thickness_percent=thickness,
            outside=bool(outline_cfg.get("outside", True)),
            fade_start_percent=float(outline_cfg.get("fade_start_percent", 0)),
            color=normalize_color(str(outline_cfg.get("color", "white"))),
        )
