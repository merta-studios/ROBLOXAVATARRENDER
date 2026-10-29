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
sind ebenfalls echte Meshes mit UVs/Texturen. Emote-Posen werden ueber die Gelenk-Kette des
R15-Rigs angewendet (jedes Segment dreht sich um das Gelenk, an dem es im
Standard-Rig haengt) -- bei ``pose = "None"`` wird weder ein Emote geladen
noch eine Pose angewendet.

Ausgewertet werden hier: ``classic_pose``, ``classic_emote``,
``classic_rotation_deg``, ``classic_abstand_oben``, ``classic_abstand_unten``,
``material_finish``, ``background_*`` und ``outline``.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass
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
from scripts.r15_blocky_rig import (
    R15_FIXED_ATTACHMENT_POSITIONS,
    R15Part,
    load_r15_attachments,
    load_r15_blocky_rig,
)
from scripts.r15_clothing import compute_part_uvs
from scripts.rbx_animation import (
    R6_BONE_PIVOT_ATTACHMENTS,
    R15_BONE_PARENTS,
    bone_to_r15,
    normalize_clip_to_r15,
)
from scripts.rbx_formats import IDENTITY_CFRAME, MeshData
from scripts.render_config import normalize_color
from scripts.roblox_opencloud import AvatarBundle
from scripts.blender_render import _build_blender_mesh_from_rbx

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

# Gelenk (Drehpunkt) jedes R15-Segments im offiziellen Rig.
#
# Roblox bewegt die Segmente ueber Motor6D-Joints: jedes Segment haengt an
# genau einem Gelenk und dreht sich genau darum. Die Gelenkpositionen stehen
# im Rig-Space (HumanoidRootPart im Ursprung, Fusssohlen bei y = -2) und
# stammen aus der offiziellen R15Fixed-Rig-Attachment-Map
# (Root/Waist/Neck/Shoulder/Elbow/Wrist/Hip/Knee/Ankle) - also genau den
# Positionen, die Roblox beim Einhaengen eines Koerperteil-Assets benutzt.
R15_BONE_PIVOT_ATTACHMENTS: dict[str, str] = {
    "LowerTorso": "RootRigAttachment",
    "UpperTorso": "WaistRigAttachment",
    "Head": "NeckRigAttachment",
    "LeftUpperArm": "LeftShoulderRigAttachment",
    "LeftLowerArm": "LeftElbowRigAttachment",
    "LeftHand": "LeftWristRigAttachment",
    "RightUpperArm": "RightShoulderRigAttachment",
    "RightLowerArm": "RightElbowRigAttachment",
    "RightHand": "RightWristRigAttachment",
    "LeftUpperLeg": "LeftHipRigAttachment",
    "LeftLowerLeg": "LeftKneeRigAttachment",
    "LeftFoot": "LeftAnkleRigAttachment",
    "RightUpperLeg": "RightHipRigAttachment",
    "RightLowerLeg": "RightKneeRigAttachment",
    "RightFoot": "RightAnkleRigAttachment",
}

# Der Root sitzt im Ursprung des Rig-Space und ist der Drehpunkt des
# HumanoidRootPart selbst.
R15_ROOT_JOINT = (0.0, 0.0, 0.0)

# Welches R15-Segment bewegt sich mit welchem Accessoire? Die Accessoires
# haengen an den Roblox-Attachments; die Zuordnung nennt das Segment, an dem
# das Attachment im Standard-Rig sitzt. Nur relevant, wenn eine Emote-Pose
# angewendet wird.
ATTACHMENT_BONE: dict[str, str] = {
    "HatAttachment": "Head",
    "HairAttachment": "Head",
    "FaceFrontAttachment": "Head",
    "FaceCenterAttachment": "Head",
    "NeckAttachment": "UpperTorso",
    "BodyFrontAttachment": "UpperTorso",
    "BodyBackAttachment": "UpperTorso",
    "LeftCollarAttachment": "UpperTorso",
    "RightCollarAttachment": "UpperTorso",
    "LeftShoulderAttachment": "LeftUpperArm",
    "RightShoulderAttachment": "RightUpperArm",
    "LeftGripAttachment": "LeftHand",
    "RightGripAttachment": "RightHand",
    "WaistFrontAttachment": "LowerTorso",
    "WaistCenterAttachment": "LowerTorso",
    "WaistBackAttachment": "LowerTorso",
}

DEFAULT_BODY_RGB: dict[str, tuple[int, int, int]] = {
    "head": (245, 205, 48),
    "torso": (13, 105, 172),
    "rightArm": (245, 205, 48),
    "leftArm": (245, 205, 48),
    "rightLeg": (164, 189, 71),
    "leftLeg": (164, 189, 71),
}

# Mapping von Asset-Typ zu R15-Teilnamen
ASSET_TYPE_TO_PART_NAMES: dict[int, tuple[str, ...]] = {
    17: ("Head",),
    27: ("UpperTorso", "LowerTorso"),
    28: ("RightUpperArm", "RightLowerArm", "RightHand"),
    29: ("LeftUpperArm", "LeftLowerArm", "LeftHand"),
    30: ("LeftUpperLeg", "LeftLowerLeg", "LeftFoot"),
    31: ("RightUpperLeg", "RightLowerLeg", "RightFoot"),
}


@dataclass(frozen=True)
class BodyScale:
    """Die Groessen-Werte eines Avatars (Roblox "Humanoid"-Scales).

    Roblox skaliert die Koerperteile des R15-Rigs in allen drei Dimensionen
    (``BodyWidthScale``, ``BodyHeightScale``, ``BodyDepthScale``), den Kopf
    dagegen uniform (``HeadScale``). Das beschreibt Roblox in der
    Ankuendigung "R15 Character Scaling" (devforum 30642) und in der
    Rthro-Scaling-Spezifikation (devforum 199611):

        if bodyPart.Name == "Head" then
            return scaleBodyType * headScale
        else
            return scaleBodyType * Vector3.new(bodyWidth, bodyHeight, bodyDepth)
        end

    Die Werte kommen aus dem Feld ``scales`` der oeffentlichen Avatar-API
    (Verhaeltnis zum Standard, 1.0 = Standard).
    """

    width: float = 1.0
    height: float = 1.0
    depth: float = 1.0
    head: float = 1.0

    @classmethod
    def from_bundle(cls, bundle: Any) -> "BodyScale":
        raw = getattr(bundle, "scales", {}) or {}

        def _value(key: str, default: float = 1.0) -> float:
            try:
                value = float(raw.get(key, default))
            except (TypeError, ValueError):
                return default
            if value <= 0.0:
                return default
            if value > 3.0:
                # Aeltere API-Antworten liefern Prozentwerte (z. B. 90).
                value /= 100.0
            return min(max(value, 0.1), 3.0)

        return cls(
            width=_value("width"),
            height=_value("height"),
            depth=_value("depth"),
            head=_value("head"),
        )

    @property
    def is_identity(self) -> bool:
        return (
            self.width == 1.0
            and self.height == 1.0
            and self.depth == 1.0
            and self.head == 1.0
        )

    def rig_point(self, point: tuple[float, float, float] | np.ndarray) -> np.ndarray:
        """Punkt im Roblox-Rig-Space skalieren (x=Breite, y=Hoehe, z=Tiefe)."""
        x, y, z = (float(v) for v in point)
        return np.array([x * self.width, y * self.height, z * self.depth], dtype=np.float64)

    def local_scale(self) -> tuple[float, float, float]:
        """Skalierung der Mesh-Vertices im lokalen Part-Raum (x, y, z)."""
        return (self.width, self.height, self.depth)

    def bl_scale(self) -> tuple[float, float, float]:
        """Skalierung der Blocky-Rig-Vertices im Blender-Raum (x, y, z).

        Der Blocky-Rig liegt bereits im Blender-Raum (Z nach oben, x nach
        hinten); deshalb tauschen Hoehe und Tiefe ihre Plaetze.
        """
        return (self.width, self.depth, self.height)


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
    clothing_path: str | None = None,
    alpha_mode: str = "overlay",
) -> Any:
    """Erzeugt ein Principled-Material (Glossy/Matt) ohne Rig-Abhaengigkeit.

    ``alpha_mode`` bildet Roblox' ``Enum.AlphaMode`` ab:

    * ``overlay``      - Die ColorMap liegt ueber der BodyColor, transparente
      Texel zeigen die BodyColor (Standard bei SurfaceAppearance).
    * ``transparency`` - Der Alphakanal der Textur schneidet-Loecher aus dem
      Mesh (klassische Texturen ohne SurfaceAppearance).
    * ``tintmask``     - Getintete Textur wird ueber die ungetintete gemischt.
    * ``opaque``       - Der Alphakanal der Textur wird ignoriert.
    """
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
        uv = tree.nodes.new("ShaderNodeUVMap")
        uv.uv_map = "UVMap"
        tree.links.new(uv.outputs["UV"], tex.inputs["Vector"])

        # Roblox: SurfaceAppearance.Color ist ein Multiplikator auf die ColorMap.
        tint_linear = tuple(
            _srgb_to_linear(max(0.0, min(1.0, float(t)))) for t in tint_srgb
        )
        if any(abs(t - 1.0) > 1e-6 for t in tint_linear):
            tint_node = tree.nodes.new("ShaderNodeMixRGB")
            tint_node.blend_type = "MULTIPLY"
            tint_node.inputs[0].default_value = 1.0
            tree.links.new(tex.outputs["Color"], tint_node.inputs[1])
            tint_node.inputs[2].default_value = (*tint_linear, 1.0)
            textured = tint_node.outputs["Color"]
        else:
            textured = tex.outputs["Color"]

        mode = str(alpha_mode or "overlay").casefold()
        if mode == "overlay":
            # Die Textur liegt ueber der BodyColor; transparente Texel zeigen
            # die BodyColor des MeshParts.
            mix = tree.nodes.new("ShaderNodeMixRGB")
            mix.blend_type = "MIX"
            base_linear = tuple(
                _srgb_to_linear(max(0.0, min(1.0, float(c)))) for c in rgb_srgb
            )
            mix.inputs[1].default_value = (*base_linear, 1.0)
            tree.links.new(textured, mix.inputs[2])
            tree.links.new(tex.outputs["Alpha"], mix.inputs[0])
            tree.links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])
        elif mode == "tintmask":
            # Getintete Textur ueber die ungetintete Textur (Alpha = Maske).
            mix = tree.nodes.new("ShaderNodeMixRGB")
            mix.blend_type = "MIX"
            tree.links.new(tex.outputs["Color"], mix.inputs[1])
            tree.links.new(textured, mix.inputs[2])
            tree.links.new(tex.outputs["Alpha"], mix.inputs[0])
            tree.links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])
        else:
            # transparency / opaque: die Texturfarbe ist die Farbe des Meshes.
            tree.links.new(textured, bsdf.inputs["Base Color"])
            if mode == "transparency":
                # Der Alphakanal der Textur schneidet Loecher aus dem Mesh.
                multiply = tree.nodes.new("ShaderNodeMath")
                multiply.operation = "MULTIPLY"
                tree.links.new(tex.outputs["Alpha"], multiply.inputs[0])
                multiply.inputs[1].default_value = max(0.0, min(1.0, float(alpha)))
                tree.links.new(multiply.outputs["Value"], bsdf.inputs["Alpha"])
        # Nur echte Alpha-Texturen muessen blended rendern; die neuen
        # R15-Teiltexturen sind opaque RGB und sollen kein Sorting-Rauschen.
        if hasattr(mat, "surface_render_method") and getattr(image, "channels", 4) == 4:
            mat.surface_render_method = "BLENDED"
    else:
        bsdf.inputs["Base Color"].default_value = (*linear, 1.0)
    if clothing_path and Path(clothing_path).is_file():
        overlay = tree.nodes.new("ShaderNodeTexImage")
        overlay.image = bpy.data.images.load(str(Path(clothing_path).resolve()), check_existing=True)
        uv = tree.nodes.new("ShaderNodeUVMap")
        uv.uv_map = "ClothingUV"
        tree.links.new(uv.outputs["UV"], overlay.inputs["Vector"])
        mix = tree.nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MIX"
        base = bsdf.inputs["Base Color"]
        if base.is_linked:
            tree.links.new(base.links[0].from_socket, mix.inputs[1])
        else:
            mix.inputs[1].default_value = base.default_value
        tree.links.new(overlay.outputs["Color"], mix.inputs[2])
        tree.links.new(overlay.outputs["Alpha"], mix.inputs[0])
        tree.links.new(mix.outputs["Color"], base)
    return mat


def _add_clothing_uv(mesh: Any, part_name: str, color_key: str) -> None:
    """Projektion fuer Classic-Kleidung ohne die Original-UVs zu ueberschreiben."""
    part = R15Part(
        name=part_name,
        vertices=[tuple(v.co) for v in mesh.vertices],
        faces=[tuple(p.vertices) for p in mesh.polygons],
        uvs=[], color_key=color_key,
    )
    uvs = compute_part_uvs(part)
    layer = mesh.uv_layers.new(name="ClothingUV")
    for loop in mesh.loops:
        layer.data[loop.index].uv = uvs[loop.index]


def _body_part_matrix_bl(cframe: tuple[float, ...], scale: "BodyScale | None" = None) -> Matrix:
    """Transformiert ein Roblox-CFrame (Character-Space) in den Blender-Raum.

    Roblox-Character-Space: HumanoidRootPart im Ursprung, Fusssohlen bei
    y = -2. Blender: Z nach oben, Fusssohlen bei z = 0, also liegt der
    Avatar-Root bei z = +2. Die Basisumkehrung ``T_RBX_TO_BL`` ist
    involutorisch und wird daher auf beiden Seiten benutzt.

    Bei skalierten Avataren wird die Position des Teils mit den
    Body-Scales multipliziert (die Groesse selbst steckt im Mesh) und der
    Boden-Abstand entsprechend auf 2 * Height gesetzt.
    """
    mat_rbx = _cframe_to_mat4(cframe)
    if scale is not None:
        position = scale.rig_point((mat_rbx[0, 3], mat_rbx[1, 3], mat_rbx[2, 3]))
        mat_rbx[0, 3], mat_rbx[1, 3], mat_rbx[2, 3] = position
    mat_bl = T_RBX_TO_BL @ mat_rbx @ T_RBX_TO_BL
    mat_bl[2, 3] += 2.0 * (scale.height if scale is not None else 1.0)
    return Matrix(mat_bl.tolist())


def _scale_mesh_vertices(mesh: Any, factors: tuple[float, float, float]) -> None:
    """Skaliert alle Vertices eines Meshes (lokaler Part-Raum)."""
    if all(abs(f - 1.0) < 1e-9 for f in factors):
        return
    for vertex in mesh.vertices:
        vertex.co = (
            vertex.co.x * factors[0],
            vertex.co.y * factors[1],
            vertex.co.z * factors[2],
        )
    mesh.update()


def _scale_mesh_uniform_about_center(mesh: Any, factor: float) -> None:
    """Skaliert ein Mesh uniform um seinen eigenen Mittelpunkt (z. B. der Kopf)."""
    if abs(factor - 1.0) < 1e-9 or not mesh.vertices:
        return
    coords = np.asarray([tuple(v.co) for v in mesh.vertices], dtype=np.float64)
    center = (coords.min(axis=0) + coords.max(axis=0)) / 2.0
    for vertex in mesh.vertices:
        co = np.asarray(tuple(vertex.co), dtype=np.float64)
        vertex.co = tuple(center + (co - center) * factor)
    mesh.update()


def _create_inventory_part(
    part: R15Part, body_part: Any, srgb: tuple[float, float, float],
    finish: str, clothing_path: str | None, scene: Any, render_objects: list[Any],
    classic_texture_path: str | None = None,
    scale: "BodyScale | None" = None,
) -> Any:
    # Der Mesh-Builder konvertiert bereits Roblox -> Blender. Eine zweite
    # Achsentransformation wuerde die Teile auf den Boden kippen.
    mesh = _build_blender_mesh_from_rbx(
        f"{part.name}Mesh", body_part.mesh, body_part.mesh_scale, body_part.mesh_offset
    )
    if scale is not None:
        # Roblox skaliert die Koerperteile in allen drei Dimensionen, den
        # Kopf dagegen uniform (HeadScale). Das Mesh liegt bereits im
        # Blender-Raum, deshalb werden Hoehe und Tiefe getauscht.
        if part.name == "Head":
            _scale_mesh_uniform_about_center(mesh, scale.head)
        else:
            _scale_mesh_vertices(mesh, scale.bl_scale())
    _add_clothing_uv(mesh, part.name, part.color_key)
    texture_path = body_part.texture_path
    if not texture_path and classic_texture_path and Path(classic_texture_path).is_file():
        # Klassisches Paket ohne eigene Textur (z. B. "Roblox 2.0"): Roblox
        # zeigt dort die klassische Koerpertextur aus BodyColors und
        # Classic-Kleidung. Die UVs des Assets passen nicht zu diesem Atlas,
        # also werden die UVs neu berechnet: jede Flaeche des Meshs bekommt
        # das Tile ihres Face (Front/Back/Left/Right/Top/Bottom).
        texture_path = classic_texture_path
        _remap_part_uvs(mesh, part)
    material = _solid_material(
        f"Classic{part.name}Mat", srgb, finish,
        texture_path=texture_path,
        tint_srgb=body_part.tint_color,
        alpha=1.0 - body_part.transparency,
        clothing_path=clothing_path,
        alpha_mode=getattr(body_part, "alpha_mode", "overlay"),
    )
    mesh.materials.append(material)
    obj = bpy.data.objects.new(part.name, mesh)
    scene.collection.objects.link(obj)
    # Das Asset bringt seinen eigenen CFrame mit (Ordner "R15Fixed" des
    # Assets). Genau dort sitzt das Teil am Roblox-Rig; die Positionen wurden
    # beim Laden ueber die Rig-Attachments auf das Standard-Rig ausgerichtet.
    obj.matrix_world = _body_part_matrix_bl(
        getattr(body_part, "cframe", IDENTITY_CFRAME), scale
    )
    render_objects.append(obj)
    return obj


def _remap_part_uvs(mesh: Any, part: R15Part) -> None:
    """Berechnet die UVs eines Inventar-Meshs neu (klassischer Body-Atlas).

    Jede Flaeche wird ueber ihre Normale als Front/Back/Left/Right/Top/Bottom
    klassifiziert und auf das passende Tile des Atlas gemappt - genau so, wie
    Roblox die klassischen Koerperteile auf die Koerpertextur legt.
    """
    proxy = R15Part(
        name=part.name,
        vertices=[tuple(v.co) for v in mesh.vertices],
        faces=[tuple(p.vertices) for p in mesh.polygons],
        uvs=[],
        color_key=part.color_key,
    )
    uvs = compute_part_uvs(proxy)
    layer = mesh.uv_layers.get("UVMap") or mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        if loop.index < len(uvs):
            layer.data[loop.index].uv = uvs[loop.index]


def _create_r15_part(
    part: R15Part,
    material: Any,
    scene: Any,
    render_objects: list[Any],
    scale: "BodyScale | None" = None,
) -> Any:
    """Erzeugt ein Mesh aus den offiziellen R15-Blocky-Rig-Daten."""
    mesh = bpy.data.meshes.new(f"{part.name}Mesh")
    vertices = part.vertices
    if scale is not None:
        # Die Blocky-Rig-Vertices liegen im Blender-Raum (Z nach oben). Der
        # Kopf wird - wie bei Roblox - uniform skaliert, der Rest pro Achse.
        if part.name == "Head":
            coords = np.asarray(vertices, dtype=np.float64)
            center = (coords.min(axis=0) + coords.max(axis=0)) / 2.0
            # Der Kopf folgt der Body-Hoehe (der Hals sitzt bei hoeherem
            # Koerper auch hoeher), seine Groesse wird uniform mit
            # HeadScale skaliert. Wichtig: der Groessen-Versatz wird vom
            # urspruenglichen Mittelpunkt aus gemessen - sonst entsteht eine
            # Scherung und der Kopf sitzt sichtbar zu hoch.
            new_center = np.array([center[0], center[1], center[2] * scale.height])
            vertices = [
                tuple(new_center + (np.asarray(v, dtype=np.float64) - center) * scale.head)
                for v in vertices
            ]
        else:
            factors = scale.bl_scale()
            vertices = [
                (v[0] * factors[0], v[1] * factors[1], v[2] * factors[2]) for v in vertices
            ]
    mesh.from_pydata(vertices, [], part.faces)
    mesh.update()
    uv_layer = mesh.uv_layers.new(name="UVMap")
    part_uvs = compute_part_uvs(part)
    for loop in mesh.loops:
        if loop.index < len(part_uvs):
            uv_layer.data[loop.index].uv = part_uvs[loop.index]
    # Glatte Normalen: Roblox zeichnet die Koerperteile mit weichen,
    # gerundeten Kanten. Ohne sie wirkt der Blocky-Fallback flach und
    # spiegelungsarm - die Inventar-Meshes werden ebenfalls smooth geshaded.
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


def _accessory_bone(acc: Any) -> str:
    """R15-Segment, mit dem ein Accessoire bei Emotes mitbewegt wird."""
    name = _effective_attachment_name(acc)
    if name == "__LEGACY_HAT__":
        name = "HatAttachment"
    return ATTACHMENT_BONE.get(name, "UpperTorso")


def _accessory_matrix_bl(acc: Any, scale: "BodyScale | None" = None) -> Any:
    """Berechnet C_rig @ C_adj @ inv(C_acc) und wechselt in Blender-Koordinaten."""
    attachment_name = _effective_attachment_name(acc)
    rig_pos = _r15_attachments_rbx()[attachment_name]
    if attachment_name == "__LEGACY_HAT__":
        acc_cf = acc.legacy_attachment_point
    elif acc.attachment_name:
        acc_cf = acc.attachment_cframe
    else:
        acc_cf = acc.legacy_attachment_point

    if scale is not None:
        # Das Attachment liegt auf dem Koerper und wandet daher mit den
        # Body-Scales mit.
        rig_pos = tuple(float(v) for v in scale.rig_point(rig_pos))

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


# Accessoire-Typen, die am Kopf haengen (Hut, Haar, Gesicht). Sie werden
# wie der Kopf uniform mit dem HeadScale skaliert, alle anderen mit den
# Body-Scales des Koerpers.
HEAD_ACCESSORY_TYPE_IDS = {8, 41, 42}


def _accessory_scale_factors(acc: Any, scale: "BodyScale | None") -> tuple[float, float, float]:
    """Skalierungsfaktoren eines Accessoires (Roblox-Raum: x, y, z)."""
    if scale is None:
        return (1.0, 1.0, 1.0)
    if int(getattr(acc, "asset_type_id", 0) or 0) in HEAD_ACCESSORY_TYPE_IDS:
        return (scale.head, scale.head, scale.head)
    return scale.local_scale()


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


_DIAG_SCENE: dict[str, Any] = {}


def _diag_part_boxes(body_objects: dict[str, Any]) -> dict[str, list[float]]:
    """TEMPORARY: Welt-Bounding-Boxen aller R15-Segmente."""
    out: dict[str, list[float]] = {}
    bpy.context.view_layer.update()
    for name, obj in (body_objects or {}).items():
        if obj is None:
            continue
        corners = np.asarray([tuple(c) for c in obj.bound_box], dtype=np.float64)
        if corners.size == 0:
            continue
        mw = np.asarray(obj.matrix_world, dtype=np.float64)
        world = corners @ mw[:3, :3].T + mw[:3, 3]
        lo, hi = world.min(axis=0), world.max(axis=0)
        out[name] = [round(float(v), 4) for v in (*lo, *hi)]
    return out


def _diag_accessory_boxes(accessory_parents: list[tuple[Any, str]] | None) -> dict[str, list[float]]:
    """TEMPORARY: Welt-Bounding-Boxen der Accessoires."""
    out: dict[str, list[float]] = {}
    bpy.context.view_layer.update()
    for obj, bone_name in accessory_parents or []:
        corners = np.asarray([tuple(c) for c in obj.bound_box], dtype=np.float64)
        if corners.size == 0:
            continue
        mw = np.asarray(obj.matrix_world, dtype=np.float64)
        world = corners @ mw[:3, :3].T + mw[:3, 3]
        lo, hi = world.min(axis=0), world.max(axis=0)
        out[f"{obj.name}|{bone_name}"] = [round(float(v), 4) for v in (*lo, *hi)]
    return out


def _emote_joint_rig(
    bone_name: str,
    scale: "BodyScale | None" = None,
    overrides: dict[str, tuple[float, float, float]] | None = None,
) -> tuple[float, float, float]:
    """Gelenkposition eines Bones im Roblox-Rig-Space.

    Root im Ursprung, Fusssohlen bei y = -2. Fuer R6-Begriffe (alte Emotes)
    gilt die entsprechende R15-Position: R6-Schulter = R15-Schulter usw.

    Die Position wird mit den Groessen-Werten des Avatars skaliert, damit die
    Gelenke des Rigs exakt dort liegen, wo Roblox sie nach dem Setzen von
    BodyWidthScale/BodyHeightScale/BodyDepthScale erwartet.

    ``overrides`` ersetzt die Standard-Position durch die Position, die das
    Koerperteil-Asset selbst mitbringt: Roblox baut das Rig eines
    Koerperteils aus dessen eigenen "RigAttachment"-Positionen neu auf
    (``Humanoid:BuildRigFromAttachments()``), die Gelenke der Pose liegen
    also dort, wo das Asset sie hinlegt.
    """
    attachment = R15_BONE_PIVOT_ATTACHMENTS.get(bone_name)
    if attachment is None:
        attachment = R6_BONE_PIVOT_ATTACHMENTS.get(bone_name)
    pos: tuple[float, float, float] | None = None
    if overrides:
        pos = overrides.get(bone_name)
    if pos is None:
        if attachment is None:
            pos = R15_ROOT_JOINT
        else:
            pos = R15_FIXED_ATTACHMENT_POSITIONS.get(attachment) or R15_ROOT_JOINT
    if scale is None:
        return (float(pos[0]), float(pos[1]), float(pos[2]))
    scaled = scale.rig_point(pos)
    return (float(scaled[0]), float(scaled[1]), float(scaled[2]))


def _rig_to_bl_point(
    point: tuple[float, float, float] | np.ndarray, scale: "BodyScale | None" = None
) -> np.ndarray:
    """Punkt aus dem (skalierten) Roblox-Rig-Space in den Blender-Raum umrechnen.

    Im Blender-Raum liegt der Avatar-Root bei z = +2 (Fusssohlen bei z = 0)
    und x zeigt nach hinten, daher (x_bl, y_bl, z_bl) = (-x_rbx, z_rbx, y_rbx+2).
    Bei skalierten Avataren liegt der Root entsprechend hoeher (2 * Height).
    """
    x, y, z = (float(v) for v in point)
    offset = 2.0 * (scale.height if scale is not None else 1.0)
    return np.array([-x, z, y + offset], dtype=np.float64)


def _rig_pivot_overrides(
    body_parts: dict[str, Any] | None,
) -> dict[str, tuple[float, float, float]]:
    """Gelenkpositionen aus den "RigAttachment"-Positionen der Assets.

    Roblox baut das Rig aus den Attachments des Koerperteils neu auf, die
    Gelenke liegen also bei den Positionen des Assets. Segmente ohne eigene
    Attachments (blocky rig) behalten die Standard-Positionen des Rigs.
    """
    out: dict[str, tuple[float, float, float]] = {}
    for name, spec in (body_parts or {}).items():
        attachment = R15_BONE_PIVOT_ATTACHMENTS.get(name)
        if not attachment:
            continue
        position = (getattr(spec, "rig_attachments", None) or {}).get(attachment)
        if position is None:
            continue
        out[name] = (float(position[0]), float(position[1]), float(position[2]))
    return out


def _emote_world_transforms(
    clip: Any,
    time: float,
    scale: "BodyScale | None" = None,
    overrides: dict[str, tuple[float, float, float]] | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Vorwaertsberechnung der Emote-Pose ueber die Rig-Hierarchie.

    Roblox bewegt die Segmente ueber eine Kette von Gelenken (Motor6Ds): die
    Rotation eines Segments ist relativ zum Elternsegment, und jedes Segment
    dreht sich um das Gelenk, an dem es haengt. Daraus ergibt sich fuer jeden
    Bone eine Welt-Rotation und die Welt-Position seines Gelenks.

    Bones, deren Eltern nicht in der Animation vorkommen, werden an der
    Standard-Hierarchie des R15-Rigs eingehaengt (deren Rotation ist dann die
    Identitaet).

    Rueckgabe: (Welt-Rotationen, Welt-Gelenkpositionen) im Roblox-Rig-Space.
    """
    clip = normalize_clip_to_r15(clip)
    local = clip.local_rotations(time)
    parents = clip.parents
    identity = np.eye(3, dtype=np.float64)
    world_rot: dict[str, np.ndarray] = {}
    world_joint: dict[str, np.ndarray] = {}
    for bone in clip.bone_order():
        r_local = np.asarray(local.get(bone, identity), dtype=np.float64)
        joint = np.asarray(_emote_joint_rig(bone, scale, overrides), dtype=np.float64)
        # Eltern-Bone: bevorzugt aus der Animation, sonst aus dem R15-Rig.
        parent = parents.get(bone)
        if parent is None or parent not in world_rot:
            parent = R15_BONE_PARENTS.get(bone)
        if parent is None or parent not in world_rot:
            world_rot[bone] = r_local
            world_joint[bone] = joint
            continue
        parent_joint_rest = np.asarray(
            _emote_joint_rig(parent, scale, overrides), dtype=np.float64
        )
        world_rot[bone] = world_rot[parent] @ r_local
        world_joint[bone] = world_joint[parent] + world_rot[parent] @ (
            joint - parent_joint_rest
        )
    return world_rot, world_joint


def _apply_emote_to_r15(
    classic_emote: Any,
    body_objects: dict[str, Any],
    scene: Any,
    accessory_parents: list[tuple[Any, str]] | None = None,
    scale: "BodyScale | None" = None,
    overrides: dict[str, tuple[float, float, float]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Uebertraegt die Emote-Pose auf das R15-Rig der Classic-Szene.

    Die Pose wird - wie bei Roblox - ueber die Gelenk-Kette des Rigs
    berechnet: jedes Segment wird um das Gelenk gedreht, an dem es im
    Standard-Rig haengt (Schulter, Ellbogen, Huefte, Knie, Hals, Root), und
    die Kind-Segmente wandern mit.

    Fuer jedes Segment wird ein Empty an der Ruhelage seines Gelenks
    angelegt. Die Accessoires werden zuerst an diese Empties gehaengt (mit
    der Inverse der Ruhelage, damit sie ihre Position behalten) und
    verschieben sich danach automatisch mit der Pose.

    Rueckgabe: (Empties je Bone der Animation, Empties je R15-Segment).
    """
    clip = getattr(classic_emote, "clip", None)
    if clip is None:
        return {}, {}
    time = clip.preview_time()
    world_rot, world_joint = _emote_world_transforms(clip, time, scale, overrides)

    bone_empties: dict[str, Any] = {}
    part_empties: dict[str, Any] = {}
    for bone in world_rot:
        pivot_rest = _rig_to_bl_point(_emote_joint_rig(bone, scale, overrides), scale)
        empty = bpy.data.objects.new(f"ClassicR15Bone_{bone}", None)
        scene.collection.objects.link(empty)
        empty.matrix_world = Matrix(_translation_mat4(*pivot_rest.tolist()).tolist())
        bone_empties[bone] = empty
        part_name = bone_to_r15(bone)
        if part_name:
            # Accessoires haengen am R15-Segment, auch wenn die Emote
            # R6-Begriffe benutzt.
            part_empties.setdefault(part_name, empty)
    bpy.context.view_layer.update()

    # Accessoires an die Empties haengen, solange die Empties noch auf der
    # Ruhelage stehen - erst danach wird die Pose gerechnet.
    for obj, bone_name in accessory_parents or []:
        empty = part_empties.get(bone_name)
        if empty is None:
            continue
        inverse = np.linalg.inv(np.asarray(empty.matrix_world, dtype=np.float64))
        obj.parent = empty
        obj.matrix_parent_inverse = Matrix(inverse.tolist())

    applied = 0
    for bone, rot_rbx in world_rot.items():
        r_bl = T3_RBX_TO_BL @ np.asarray(rot_rbx, dtype=np.float64) @ T3_RBX_TO_BL
        rot4 = np.eye(4, dtype=np.float64)
        rot4[:3, :3] = r_bl
        pivot_rest = _rig_to_bl_point(_emote_joint_rig(bone, scale, overrides), scale)
        pivot_world = _rig_to_bl_point(world_joint[bone], scale)

        # Das Gelenk wandert an seine neue Position und dreht sich mit.
        bone_empties[bone].matrix_world = Matrix(
            (_translation_mat4(*pivot_world.tolist()) @ rot4).tolist()
        )

        # Das Koerpersegment selbst: um das Gelenk drehen und das Gelenk
        # an seine neue Position verschieben.
        part_name = bone_to_r15(bone)
        obj = body_objects.get(part_name) if part_name else None
        if obj is not None:
            rest = np.asarray(obj.matrix_world, dtype=np.float64)
            posed = (
                _translation_mat4(*pivot_world.tolist())
                @ rot4
                @ _translation_mat4(*(-pivot_rest).tolist())
                @ rest
            )
            obj.matrix_world = Matrix(posed.tolist())
        applied += 1

    bpy.context.view_layer.update()
    print(
        f"[classic] Emote-Pose aus {clip.source_format} auf {applied} Bones angewendet "
        f"(Vorschau-Moment t={time:.3f}s)."
    )
    return bone_empties, part_empties


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
    korblox: bool = False,
    headless: bool = False,
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
    bone_empties: dict[str, Any] = {}

    # Jedes Inventar-Mesh ist lokal zu genau einem R15-Segment. Fehlende
    # Segmente behalten ihren offiziellen Blocky-Fallback und ihre Kleidung.
    official_parts = load_r15_blocky_rig()
    part_textures = getattr(bundle, "r15_part_textures", {}) or {}
    body_parts = getattr(bundle, "body_parts", {}) or {}
    # "headless" erzwingt den Headless-Look (wie beim GFX-Typ HearthandsType).
    is_headless = getattr(bundle.head, "kind", "") == "headless" or bool(headless)
    # "korblox" ersetzt das rechte Oberbein; das Asset selbst wird schon beim
    # Laden in die Body-Parts des Avatars geschrieben (Pipeline).
    if korblox and "RightUpperLeg" not in body_parts:
        print(
            "[korblox] Kein Korblox-Oberbein geladen - das Bein des Avatars "
            "bleibt vollstaendig erhalten."
        )
    # Groessen-Werte des Avatars (Breite/Hoehe/Tiefe/Kopf).
    body_scale = BodyScale.from_bundle(bundle)
    if not body_scale.is_identity:
        print(
            "[classic] Avatar-Groessen angewendet: "
            f"Breite={body_scale.width:.2f} Hoehe={body_scale.height:.2f} "
            f"Tiefe={body_scale.depth:.2f} Kopf={body_scale.head:.2f}"
        )

    for name, part in official_parts.items():
        if name == "Head" and is_headless:
            continue
        rgb = bundle.body_colors.get(part.color_key, DEFAULT_BODY_RGB[part.color_key])
        srgb = tuple(max(0.0, min(1.0, float(c) / 255.0)) for c in rgb)
        body_part = body_parts.get(name)
        if body_part is None:
            texture_path = part_textures.get(name)
            material = _solid_material(
                f"Classic{name}Mat", srgb, material_finish, texture_path=texture_path
            )
            body_objects[name] = _create_r15_part(
                part, material, scene, render_objects, body_scale
            )
            continue

        # Ein ausgerüstetes Koerperteil-Asset bringt seine eigene Textur mit.
        # Classic-Kleidung liegt nur auf dem klassischen Koerper (Blocky-Rig);
        # ein Paket wie "Roblox 2.0" zeigt die Kleidung nur, weil seine Meshes
        # auf die klassische Koerpertextur gemappt sind, in die der Client
        # BodyColors und Kleidung einrechnet. Ein zusaetzliches Projizieren
        # der Kleidung auf die UVs des Assets waere daher doppelt und falsch.
        body_objects[name] = _create_inventory_part(
            part, body_part, srgb, material_finish, None, scene, render_objects,
            classic_texture_path=part_textures.get(name),
            scale=body_scale,
        )

    # 2. Geladene Accessoires als echte Meshes an ihren Attachment-Positionen.
    accessory_count = 0
    for idx, acc in enumerate(bundle.accessories):
        mdata = getattr(acc, "mesh", None)
        if mdata is None or not getattr(mdata, "positions", None):
            continue
        if headless and int(getattr(acc, "asset_type_id", 0) or 0) == 42:
            # Headless-Look: Gesicht-Accessoires (Brillen, Masken) entfallen;
            # Haare, Hueete und Hals-Accessoires bleiben sichtbar.
            continue
        # Accessoires am Kopf (Hut, Haar, Gesicht) werden wie der Kopf
        # uniform skaliert, alle anderen mit den Body-Scales.
        size_factors = _accessory_scale_factors(acc, body_scale)
        eff_scale = tuple(
            float(acc.mesh_scale[i]) * float(acc.meta_scale[i]) * size_factors[i]
            for i in range(3)
        )
        eff_offset = tuple(
            float(acc.mesh_offset[i]) * float(acc.meta_scale[i]) * size_factors[i]
            for i in range(3)
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
        obj.matrix_world = _accessory_matrix_bl(acc, body_scale)
        render_objects.append(obj)
        accessory_parents.append((obj, _accessory_bone(acc)))
        accessory_count += 1

    # TEMPORARY DIAG: Ruhelage aller Koerperteile und Accessoires.
    _diag_rest = _diag_part_boxes(body_objects)
    _diag_acc_rest = _diag_accessory_boxes(accessory_parents)
    _pivot_overrides = _rig_pivot_overrides(body_parts)

    # 3. Pose. Bei "None" ist classic_emote immer None und es passiert nichts.
    if classic_pose in ("Equipped", "Emote", "Animation") and classic_emote is not None:
        bone_empties, _part_empties = _apply_emote_to_r15(
            classic_emote, body_objects, scene, accessory_parents, body_scale,
            _pivot_overrides,
        )
    elif classic_pose not in ("None", "Equipped", "Emote", "Animation"):
        print(f"[classic] Unbekannte Pose {classic_pose!r} - normales Stehen wird verwendet.")

    # TEMPORARY DIAG
    _DIAG_SCENE.update(
        {
            "parts_rest": _diag_rest,
            "parts_posed": _diag_part_boxes(body_objects),
            "acc_rest": _diag_acc_rest,
            "acc_posed": _diag_accessory_boxes(accessory_parents),
            "pivot_overrides": {
                name: [round(float(v), 4) for v in pos]
                for name, pos in _pivot_overrides.items()
            },
            "rig_attachments": {
                name: {
                    att: [round(float(v), 4) for v in pos]
                    for att, pos in (getattr(spec, "rig_attachments", None) or {}).items()
                }
                for name, spec in (body_parts or {}).items()
            },
            "rig_attachments_raw": {
                name: {
                    att: [round(float(v), 4) for v in pos]
                    for att, pos in (getattr(spec, "rig_attachments_raw", None) or {}).items()
                }
                for name, spec in (body_parts or {}).items()
            },
            "standard_attachments": {
                att: [round(float(v), 4) for v in pos]
                for att, pos in R15_FIXED_ATTACHMENT_POSITIONS.items()
            },
        }
    )

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
    ] + list(bone_empties.values())
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

