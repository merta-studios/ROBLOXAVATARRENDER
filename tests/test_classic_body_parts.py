"""Regressionen fuer R15-Inventar-Segmente und Classic-Kleidung."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from scripts.r15_blocky_rig import load_r15_blocky_rig, part_center
from scripts.r15_clothing import CLOTHING_RECTS, FACE_TILE_POS, R15_TILE, compose_r15_part_texture
from scripts.rbx_formats import MeshData, RbxNode
from scripts.roblox_opencloud import _load_r15_body_segments


def part(name, mesh_id, texture_id=None, cframe=None):
    props = {"MeshId": f"rbxassetid://{mesh_id}", "Size": (1, 1, 1), "InitialSize": (1, 1, 1)}
    if texture_id:
        props["TextureID"] = f"rbxassetid://{texture_id}"
    if cframe:
        props["CFrame"] = cframe
    return RbxNode("MeshPart", name, props)


def attachment(name, cframe):
    return RbxNode("Attachment", name, {"CFrame": cframe})


def cframe_at(x, y, z):
    return (x, y, z, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


class Client:
    def __init__(self):
        self.requests = []

    def fetch_asset_bytes(self, asset_id):
        self.requests.append(asset_id)
        if asset_id == 999:
            raise ValueError("bad segment")
        return str(asset_id).encode()

    def fetch_image(self, asset_id):
        self.requests.append(asset_id)
        return Image.new("RGBA", (2, 2), (asset_id % 255, 0, 0, 255))


def mesh(raw):
    return MeshData("test", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 0)] * 3, None, [(0, 1, 2)])


class ClassicBodyPartTests(unittest.TestCase):
    def test_each_r15_segment_uses_its_own_mesh_and_texture_not_r6(self):
        roots = [RbxNode("Folder", "root", children=[
            RbxNode("Folder", "R6", children=[part("LeftUpperArm", 10, 110)]),
            RbxNode("Folder", "R15Fixed", children=[part("LeftUpperArm", 20, 120)]),
            RbxNode("Folder", "R15ArtistIntent", children=[
                part("LeftUpperArm", 30, 130),
                part("LeftLowerArm", 31, 131),
                part("LeftHand", 32, 132),
            ]),
        ])]
        client = Client()
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(client, roots, 100, 29,
                ("LeftUpperArm", "LeftLowerArm", "LeftHand"), Path(tmp))
            self.assertEqual(set(parts), {"LeftUpperArm", "LeftLowerArm", "LeftHand"})
            # R15Fixed ist der kanonische Roblox-Rig-Stand und hat Vorrang
            # vor R15ArtistIntent; R6 wird nie verwendet.
            self.assertEqual(client.requests, [20, 120, 31, 131, 32, 132])
            self.assertEqual(len({p.texture_path for p in parts.values()}), 3)
            for p in parts.values():
                self.assertTrue(Path(p.texture_path).is_file())

    def test_bad_or_missing_segment_keeps_other_segments(self):
        roots = [RbxNode("Folder", "R15Fixed", children=[part("RightUpperLeg", 999), part("RightLowerLeg", 34)])]
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(Client(), roots, 200, 31,
                ("RightUpperLeg", "RightLowerLeg", "RightFoot"), Path(tmp))
        self.assertEqual(set(parts), {"RightLowerLeg"})

    def test_studio_preview_folder_is_not_used(self):
        """Der Ordner "R15" enthaelt die Studio-Vorschau (2 Studs zu hoch)."""
        roots = [RbxNode("Folder", "root", children=[
            RbxNode("Folder", "R15", children=[part("UpperTorso", 50, 150)]),
        ])]
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(Client(), roots, 405, 27, ("UpperTorso",), Path(tmp))
        self.assertEqual(set(parts), set())

    def test_surfaceappearance_without_colormap_falls_back_to_mesh_texture(self):
        node = part("UpperTorso", 35, 135)
        node.children.append(RbxNode("SurfaceAppearance"))
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(Client(), [node], 300, 27, ("UpperTorso",), Path(tmp))
            self.assertTrue(parts["UpperTorso"].texture_path.endswith("135.png"))

    def test_rig_attachments_align_the_asset_with_the_official_fixed_rig(self):
        """Ein Asset, das 1 Stud zu tief sitzt, wird auf das Standard-Rig gehoben."""
        arm = part("RightUpperArm", 40, 140, cframe_at(1.336, 1.133, 0.007))
        arm.children.append(attachment("RightShoulderRigAttachment", cframe_at(-0.336, -0.370, -0.007)))
        arm.children.append(attachment("RightElbowRigAttachment", cframe_at(0.164, -1.098, -0.007)))
        roots = [RbxNode("Folder", "root", children=[
            RbxNode("Folder", "R15Fixed", children=[arm]),
        ])]
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(Client(), roots, 400, 28, ("RightUpperArm",), Path(tmp))
        spec = parts["RightUpperArm"]
        # Das Asset liegt 1.0 zu tief (Schulter 0.763 statt 1.763), also wird
        # das gesamte Teil um 1.0 nach oben geschoben.
        self.assertAlmostEqual(spec.cframe[1], 2.133, places=4)
        self.assertAlmostEqual(spec.cframe[0], 1.336, places=4)

    def test_alignment_is_zero_when_attachments_match_the_fixed_rig(self):
        arm = part("LeftUpperArm", 41, 141, cframe_at(-1.290, 1.379, 0.024))
        arm.children.append(attachment("LeftShoulderRigAttachment", cframe_at(0.290, 0.384, -0.024)))
        roots = [RbxNode("Folder", "root", children=[
            RbxNode("Folder", "R15Fixed", children=[arm]),
        ])]
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(Client(), roots, 401, 29, ("LeftUpperArm",), Path(tmp))
        self.assertAlmostEqual(parts["LeftUpperArm"].cframe[1], 1.379, places=4)

    def test_classic_r6_leg_asset_contributes_only_its_upper_leg(self):
        """Ein aus R6 konvertiertes Bein liefert nur das Oberbein.

        Das Korblox Deathspeaker Right Leg (139607718) ist ein solches Paket:
        seine Teile tragen "AvatarPartScaleType" und liegen in R6-Koordinaten
        (die Rig-Attachments sitzen 1 Stud zu tief). Unterbein und Fuss sind
        daraus mechanisch abgeleitet (der Fuss ist ein schwarzer Klotz) und
        sollen beim Avatar bleiben.
        """
        upper = part("RightUpperLeg", 60, 160, cframe_at(0.4812, -1.2418, -0.030))
        upper.children.append(attachment("RightHipRigAttachment", cframe_at(0.0188, 0.2418, 0.030)))
        upper.children.append(RbxNode("StringValue", "AvatarPartScaleType", {"Value": "Classic"}))
        lower = part("RightLowerLeg", 61, 161, cframe_at(0.5877, -2.2097, -0.0708))
        lower.children.append(RbxNode("StringValue", "AvatarPartScaleType", {"Value": "Classic"}))
        foot = part("RightFoot", 62, 162, cframe_at(0.5980, -2.8713, -0.011))
        foot.children.append(RbxNode("StringValue", "AvatarPartScaleType", {"Value": "Classic"}))
        roots = [RbxNode("Folder", "root", children=[
            RbxNode("Folder", "R15Fixed", children=[upper, lower, foot]),
        ])]
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(Client(), roots, 139607718, 31,
                ("RightUpperLeg", "RightLowerLeg", "RightFoot"), Path(tmp))
        self.assertEqual(set(parts), {"RightUpperLeg"})
        # Das Oberbein wird auf das Standard-Rig gehoben (Hueft auf y = 0).
        self.assertAlmostEqual(parts["RightUpperLeg"].cframe[1], -0.2418, places=4)

    def test_properly_aligned_leg_asset_keeps_all_its_segments(self):
        """Ein echtes R15-Bein-Paket (z. B. Headless Horseman) bleibt vollstaendig."""
        upper = part("LeftUpperLeg", 63, 163, cframe_at(-0.4471, -0.3160, -0.0169))
        upper.children.append(attachment("LeftHipRigAttachment", cframe_at(-0.0529, 0.3160, 0.0169)))
        lower = part("LeftLowerLeg", 64, 164, cframe_at(-0.4831, -1.1116, 0.0499))
        lower.children.append(attachment("LeftKneeRigAttachment", cframe_at(-0.0169, 0.2896, -0.0499)))
        foot = part("LeftFoot", 65, 165, cframe_at(-0.5129, -1.6914, -0.1421))
        roots = [RbxNode("Folder", "root", children=[
            RbxNode("Folder", "R15Fixed", children=[upper, lower, foot]),
        ])]
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(Client(), roots, 134082507, 30,
                ("LeftUpperLeg", "LeftLowerLeg", "LeftFoot"), Path(tmp))
        self.assertEqual(set(parts), {"LeftUpperLeg", "LeftLowerLeg", "LeftFoot"})

    def test_classic_package_texture_comes_from_the_character_mesh(self):
        """Klassische Pakete tragen ihre Textur am CharacterMesh, nicht am MeshPart."""
        character = RbxNode("CharacterMesh", "CharacterMesh", {"BaseTextureId": "rbxassetid://777"})
        roots = [RbxNode("Folder", "root", children=[
            RbxNode("Folder", "R6", children=[character]),
            RbxNode("Folder", "R15Fixed", children=[part("UpperTorso", 42)]),
        ])]
        client = Client()
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(client, roots, 402, 27, ("UpperTorso",), Path(tmp))
        self.assertIn(777, client.requests)
        self.assertTrue(parts["UpperTorso"].texture_path.endswith("777.png"))

    def test_alpha_mode_follows_surfaceappearance_and_defaults_to_transparency(self):
        node = part("UpperTorso", 43, 143)
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(Client(), [node], 403, 27, ("UpperTorso",), Path(tmp))
            self.assertEqual(parts["UpperTorso"].alpha_mode, "transparency")

        node = part("UpperTorso", 44, 144)
        node.children.append(RbxNode("SurfaceAppearance", "SurfaceAppearance", {"AlphaMode": 0}))
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(Client(), [node], 404, 27, ("UpperTorso",), Path(tmp))
            self.assertEqual(parts["UpperTorso"].alpha_mode, "overlay")

    def test_official_centers_are_distinct_and_upright(self):
        parts = load_r15_blocky_rig()
        center = lambda name: part_center(parts[name])
        self.assertGreater(center("Head")[2], center("UpperTorso")[2])
        self.assertGreater(center("UpperTorso")[2], center("LowerTorso")[2])
        self.assertGreater(center("LeftUpperArm")[2], center("LeftHand")[2])
        self.assertGreater(center("RightUpperLeg")[2], center("RightFoot")[2])
        self.assertLess(center("RightFoot")[0], center("LeftFoot")[0])

    def test_transparent_clothing_atlas_preserves_underlying_mesh_texture(self):
        shirt = Image.new("RGBA", (585, 559), (0, 0, 0, 0))
        x, y, w, h = CLOTHING_RECTS["torso"]["Front"]
        shirt.paste((220, 10, 20, 255), (x, y, x + w, y + h))
        atlas = compose_r15_part_texture("UpperTorso", {}, shirt, None, None, None, transparent_base=True)
        col, row = FACE_TILE_POS["Front"]
        self.assertEqual(atlas.getpixel((col * R15_TILE + 64, row * R15_TILE + 64)), (220, 10, 20, 255))
        col, row = FACE_TILE_POS["Back"]
        self.assertEqual(atlas.getpixel((col * R15_TILE + 64, row * R15_TILE + 64))[3], 0)

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipIf(bpy is None, "Blender-Systembibliotheken fehlen")
class ClassicBlenderBodyTests(unittest.TestCase):
    def test_segment_uses_single_axis_conversion_and_separate_clothing_uv(self):
        from scripts.classic_render import _create_inventory_part
        from scripts.roblox_opencloud import BodyPartSpec

        bpy.ops.wm.read_factory_settings(use_empty=True)
        part_data = load_r15_blocky_rig()["LeftUpperArm"]
        body = BodyPartSpec(1, 29, mesh(b"1"))
        with tempfile.TemporaryDirectory() as tmp:
            texture = Path(tmp) / "base.png"
            overlay = Path(tmp) / "overlay.png"
            Image.new("RGB", (4, 4), (30, 40, 50)).save(texture)
            Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(overlay)
            body.texture_path = str(texture)
            objects = []
            obj = _create_inventory_part(part_data, body, (1, 1, 1), "Matt", str(overlay), bpy.context.scene, objects)
            self.assertEqual(objects, [obj])
            # Ohne authored CFrame steht das Teil am Avatar-Root (Roblox y = 0
            # entspricht Blender z = +2, weil die Fusssohlen bei z = 0 liegen).
            for actual, expected in zip(obj.location, (0.0, 0.0, 2.0)):
                self.assertAlmostEqual(actual, expected, places=5)
            self.assertEqual(tuple(obj.rotation_euler), (0.0, 0.0, 0.0))
            self.assertEqual(tuple(obj.data.vertices[1].co), (-1.0, 0.0, 0.0))
            self.assertIsNotNone(obj.data.uv_layers.get("UVMap"))
            self.assertIsNotNone(obj.data.uv_layers.get("ClothingUV"))
            nodes = obj.data.materials[0].node_tree.nodes
            self.assertEqual({n.uv_map for n in nodes if n.type == "UVMAP"}, {"UVMap", "ClothingUV"})
            self.assertTrue(any(n.type == "MIX_RGB" for n in nodes))

    def test_inventory_part_is_placed_at_the_authored_cframe(self):
        """Das Teil sitzt an seinem CFrame im Asset, nicht am Blocky-Zentrum."""
        from scripts.classic_render import _create_inventory_part
        from scripts.roblox_opencloud import BodyPartSpec

        bpy.ops.wm.read_factory_settings(use_empty=True)
        part_data = load_r15_blocky_rig()["LeftUpperArm"]
        body = BodyPartSpec(1, 29, mesh(b"1"))
        # Roblox: linker Oberarm bei x = -1.29, y = 1.379 (Rig-Space).
        body.cframe = (-1.29, 1.379, 0.024, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        objects = []
        obj = _create_inventory_part(part_data, body, (1, 1, 1), "Matt", None, bpy.context.scene, objects)
        # Roblox (x, y, z) -> Blender (-x, z, y + 2)
        self.assertAlmostEqual(obj.location.x, 1.29, places=5)
        self.assertAlmostEqual(obj.location.y, 0.024, places=5)
        self.assertAlmostEqual(obj.location.z, 3.379, places=5)

    def test_package_without_own_texture_uses_the_classic_body_texture(self):
        """Klassische Pakete ohne eigene Textur bekommen die Koerpertextur."""
        from scripts.classic_render import _create_inventory_part
        from scripts.roblox_opencloud import BodyPartSpec

        bpy.ops.wm.read_factory_settings(use_empty=True)
        part_data = load_r15_blocky_rig()["UpperTorso"]
        body = BodyPartSpec(1, 27, mesh(b"1"))
        self.assertIsNone(body.texture_path)
        with tempfile.TemporaryDirectory() as tmp:
            texture = Path(tmp) / "classic_body.png"
            Image.new("RGB", (8, 8), (30, 40, 50)).save(texture)
            objects = []
            obj = _create_inventory_part(
                part_data, body, (1, 1, 1), "Matt", None, bpy.context.scene, objects,
                classic_texture_path=str(texture),
            )
            images = [
                node.image.name for node in obj.data.materials[0].node_tree.nodes
                if node.type == "TEX_IMAGE"
            ]
            self.assertTrue(any("classic_body" in name for name in images))
            # Die UVs zeigen auf den klassischen Body-Atlas (3x2-Tiles).
            layer = obj.data.uv_layers["UVMap"]
            for loop in obj.data.loops:
                u, v = layer.data[loop.index].uv
                self.assertGreaterEqual(u, 0.0)
                self.assertLessEqual(u, 1.0)
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 1.0)

    def test_classic_package_is_projected_per_face_not_via_the_package_uvs(self):
        """Klassische Pakete werden je Flaeche auf das Classic-Template projiziert.

        Die UVs eines klassischen Pakets (z. B. "Roblox 2.0") liegen nicht im
        Template-Raum: gemessen liegt das Front-Face des LowerTorso im
        unteren Limb-Bereich des Templates. Roblox legt die Classic-Kleidung
        darum ueber die offiziellen Template-Rechtecke je Flaeche auf das
        Mesh - die UVs des Assets werden dabei verworfen.
        """
        from scripts.classic_render import _create_inventory_part
        from scripts.r15_clothing import face_uv_rect
        from scripts.roblox_opencloud import BodyPartSpec

        bpy.ops.wm.read_factory_settings(use_empty=True)
        # Wuerfel im Roblox-Raum (x rechts, y oben, z nach hinten).
        verts = [
            (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
        ]
        quads = [
            (0, 3, 2, 1),   # Roblox-Front (-z)
            (4, 5, 6, 7),   # +z
            (0, 1, 5, 4),   # -y
            (2, 3, 7, 6),   # +y
            (0, 4, 7, 3),   # -x
            (1, 2, 6, 5),   # +x
        ]
        faces = []
        for a, b, c, d in quads:
            faces.append((a, b, c))
            faces.append((a, c, d))
        # UVs des Pakets: liegen bewusst nicht im Template-Raum.
        uvs = [(0.35, 0.75)] * len(verts)
        body = BodyPartSpec(1, 27, MeshData("test", verts, [(0.0, 0.0, 1.0)] * len(verts), uvs, None, faces))
        self.assertIsNone(body.texture_path)

        part_data = load_r15_blocky_rig()["UpperTorso"]
        with tempfile.TemporaryDirectory() as tmp:
            texture = Path(tmp) / "classic_body.png"
            Image.new("RGB", (8, 8), (30, 40, 50)).save(texture)
            objects = []
            obj = _create_inventory_part(
                part_data, body, (1, 1, 1), "Matt", None, bpy.context.scene, objects,
                classic_texture_path=str(texture),
            )
            layer = obj.data.uv_layers["UVMap"]
            expected = {
                "Front": face_uv_rect("Front"), "Back": face_uv_rect("Back"),
                "Left": face_uv_rect("Left"), "Right": face_uv_rect("Right"),
                "Top": face_uv_rect("Top"), "Bottom": face_uv_rect("Bottom"),
            }
            checked = 0
            for poly in obj.data.polygons:
                nx, ny, nz = poly.normal
                ax, ay, az = abs(nx), abs(ny), abs(nz)
                if ax >= ay and ax >= az:
                    direction = "Right" if nx < 0 else "Left"
                elif ay >= ax and ay >= az:
                    direction = "Front" if ny < 0 else "Back"
                else:
                    direction = "Top" if nz > 0 else "Bottom"
                u0, v0, u1, v1 = expected[direction]
                for loop_index in poly.loop_indices:
                    u, v = layer.data[loop_index].uv
                    self.assertGreaterEqual(u, u0 - 1e-6)
                    self.assertLessEqual(u, u1 + 1e-6)
                    self.assertGreaterEqual(v, v0 - 1e-6)
                    self.assertLessEqual(v, v1 + 1e-6)
                    # Die UVs des Pakets wurden verworfen.
                    self.assertNotAlmostEqual(u, 0.35, places=4)
                checked += 1
            self.assertEqual(checked, 12)

    def test_alpha_modes_follow_roblox_semantics(self):
        """Overlay mischt die BodyColor, Transparency schneidet Loecher."""
        from scripts.classic_render import _solid_material

        bpy.ops.wm.read_factory_settings(use_empty=True)
        with tempfile.TemporaryDirectory() as tmp:
            texture = Path(tmp) / "tex.png"
            img = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
            img.putpixel((0, 0), (0, 255, 0, 0))
            img.save(texture)

            overlay = _solid_material("OverlayMat", (1, 1, 1), "Matt", texture_path=str(texture), alpha_mode="overlay")
            bsdf = overlay.node_tree.nodes["Principled BSDF"]
            mix = next(n for n in overlay.node_tree.nodes if n.type == "MIX_RGB")
            self.assertEqual(mix.blend_type, "MIX")
            self.assertAlmostEqual(bsdf.inputs["Alpha"].default_value, 1.0, places=5)
            self.assertIsNotNone(bsdf.inputs["Base Color"].links)

            transparency = _solid_material("TransMat", (1, 1, 1), "Matt", texture_path=str(texture), alpha_mode="transparency")
            bsdf = transparency.node_tree.nodes["Principled BSDF"]
            self.assertIsNotNone(bsdf.inputs["Alpha"].links)
            math_node = next(n for n in transparency.node_tree.nodes if n.type == "MATH")
            self.assertEqual(math_node.operation, "MULTIPLY")

            opaque = _solid_material("OpaqueMat", (1, 1, 1), "Matt", texture_path=str(texture), alpha_mode="opaque")
            bsdf = opaque.node_tree.nodes["Principled BSDF"]
            self.assertFalse(bsdf.inputs["Alpha"].links)
            self.assertAlmostEqual(bsdf.inputs["Alpha"].default_value, 1.0, places=5)


class HeadRigOriginTests(unittest.TestCase):
    """Der DynamicHead haengt an seinen eigenen Vector3Value-Attachments."""

    def _node(self, class_name, name, props=None, children=()):
        from scripts.rbx_formats import RbxNode

        return RbxNode(class_name=class_name, name=name, props=dict(props or {}), children=list(children))

    def test_origin_is_the_median_difference_to_the_standard_rig(self):
        from scripts.roblox_opencloud import _head_rig_origin

        nodes = [
            self._node("Vector3Value", "NeckRigAttachment", {"Value": (0.0, -0.5, 0.0)}),
            self._node("Vector3Value", "HairAttachment", {"Value": (0.0, 0.6, 0.0)}),
            # Ausreisser (FaceCenter liegt in Roblox nicht exakt auf der Mitte).
            self._node("Vector3Value", "FaceCenterAttachment", {"Value": (0.0, -0.094, 0.0)}),
        ]
        origin = _head_rig_origin(nodes)
        self.assertIsNotNone(origin)
        self.assertAlmostEqual(origin[0], 0.0, places=5)
        self.assertAlmostEqual(origin[1], 2.5, places=5)
        self.assertAlmostEqual(origin[2], 0.0, places=5)

    def test_without_matching_attachments_there_is_no_origin(self):
        from scripts.roblox_opencloud import _head_rig_origin

        nodes = [self._node("Vector3Value", "Unbekannt", {"Value": (1.0, 2.0, 3.0)})]
        self.assertIsNone(_head_rig_origin(nodes))
        self.assertIsNone(_head_rig_origin([]))
