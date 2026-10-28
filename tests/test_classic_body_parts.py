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


def part(name, mesh_id, texture_id=None):
    props = {"MeshId": f"rbxassetid://{mesh_id}", "Size": (1, 1, 1), "InitialSize": (1, 1, 1)}
    if texture_id:
        props["TextureID"] = f"rbxassetid://{texture_id}"
    return RbxNode("MeshPart", name, props)


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
            self.assertEqual(client.requests, [30, 130, 31, 131, 32, 132])
            self.assertEqual(len({p.texture_path for p in parts.values()}), 3)
            for p in parts.values():
                self.assertTrue(Path(p.texture_path).is_file())

    def test_bad_or_missing_segment_keeps_other_segments(self):
        roots = [RbxNode("Folder", "R15", children=[part("RightUpperLeg", 999), part("RightLowerLeg", 34)])]
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(Client(), roots, 200, 31,
                ("RightUpperLeg", "RightLowerLeg", "RightFoot"), Path(tmp))
        self.assertEqual(set(parts), {"RightLowerLeg"})

    def test_surfaceappearance_without_colormap_falls_back_to_mesh_texture(self):
        node = part("UpperTorso", 35, 135)
        node.children.append(RbxNode("SurfaceAppearance"))
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.roblox_opencloud.parse_rbx_mesh", side_effect=mesh):
            parts = _load_r15_body_segments(Client(), [node], 300, 27, ("UpperTorso",), Path(tmp))
            self.assertTrue(parts["UpperTorso"].texture_path.endswith("135.png"))

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
            for actual, expected in zip(obj.location, part_center(part_data)):
                self.assertAlmostEqual(actual, expected, places=5)
            self.assertEqual(tuple(obj.rotation_euler), (0.0, 0.0, 0.0))
            self.assertEqual(tuple(obj.data.vertices[1].co), (-1.0, 0.0, 0.0))
            self.assertIsNotNone(obj.data.uv_layers.get("UVMap"))
            self.assertIsNotNone(obj.data.uv_layers.get("ClothingUV"))
            nodes = obj.data.materials[0].node_tree.nodes
            self.assertEqual({n.uv_map for n in nodes if n.type == "UVMAP"}, {"UVMap", "ClothingUV"})
            self.assertTrue(any(n.type == "MIX_RGB" for n in nodes))
