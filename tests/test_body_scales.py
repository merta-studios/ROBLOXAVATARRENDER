"""Regressionen fuer die Groessen-Werte des Avatars (Body-Scales)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from scripts.rbx_formats import MeshData

try:
    import bpy
except ImportError:  # pragma: no cover - lokal ohne Blender-Systembibliotheken
    bpy = None


class BodyScaleValueTests(unittest.TestCase):
    def test_reads_width_height_depth_and_head_from_the_bundle(self):
        from scripts.classic_render import BodyScale

        bundle = SimpleNamespace(scales={"width": 0.9, "height": 1.1, "depth": 0.95, "head": 0.8})
        scale = BodyScale.from_bundle(bundle)
        self.assertAlmostEqual(scale.width, 0.9)
        self.assertAlmostEqual(scale.height, 1.1)
        self.assertAlmostEqual(scale.depth, 0.95)
        self.assertAlmostEqual(scale.head, 0.8)
        self.assertFalse(scale.is_identity)

    def test_missing_or_invalid_scales_stay_at_the_default(self):
        from scripts.classic_render import BodyScale

        self.assertTrue(BodyScale.from_bundle(SimpleNamespace(scales={})).is_identity)
        self.assertTrue(BodyScale.from_bundle(SimpleNamespace(scales=None)).is_identity)
        broken = SimpleNamespace(scales={"width": 0.0, "height": "x", "depth": -1.0})
        self.assertTrue(BodyScale.from_bundle(broken).is_identity)

    def test_percent_values_are_converted_to_ratios(self):
        """Aeltere API-Antworten liefern Prozentwerte (90 statt 0.9)."""
        from scripts.classic_render import BodyScale

        scale = BodyScale.from_bundle(SimpleNamespace(scales={"width": 90.0}))
        self.assertAlmostEqual(scale.width, 0.9)

    def test_axis_order_differs_between_rig_and_blender_space(self):
        from scripts.classic_render import BodyScale

        scale = BodyScale(width=2.0, height=3.0, depth=4.0, head=5.0)
        # Roblox-Rig: x = Breite, y = Hoehe, z = Tiefe.
        self.assertEqual(scale.local_scale(), (2.0, 3.0, 4.0))
        # Blender: x = Breite, y = Tiefe, z = Hoehe.
        self.assertEqual(scale.bl_scale(), (2.0, 4.0, 3.0))
        self.assertEqual(tuple(scale.rig_point((1.0, 1.0, 1.0))), (2.0, 3.0, 4.0))


@unittest.skipIf(bpy is None, "Blender-Systembibliotheken fehlen")
class BodyScaleRenderTests(unittest.TestCase):
    def test_part_position_and_floor_offset_follow_the_height_scale(self):
        from scripts.classic_render import BodyScale, _body_part_matrix_bl

        scale = BodyScale(width=2.0, height=3.0, depth=4.0, head=1.0)
        cframe = (1.0, 1.0, 0.5, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        matrix = _body_part_matrix_bl(cframe, scale)
        # Roblox (x, y, z) -> Blender (-x, z, y) und dann Bodenoffset 2 * Hoehe.
        location = matrix.translation
        self.assertAlmostEqual(location.x, -2.0, places=5)
        self.assertAlmostEqual(location.y, 2.0, places=5)
        self.assertAlmostEqual(location.z, 3.0 + 6.0, places=5)

    def test_blocky_part_vertices_are_scaled_per_axis(self):
        from scripts.classic_render import BodyScale, _create_r15_part
        from scripts.r15_blocky_rig import load_r15_blocky_rig

        bpy.ops.wm.read_factory_settings(use_empty=True)
        part = load_r15_blocky_rig()["RightLowerLeg"]
        scale = BodyScale(width=2.0, height=3.0, depth=4.0, head=1.0)
        material = bpy.data.materials.new("TestMat")
        objects = []
        obj = _create_r15_part(part, material, bpy.context.scene, objects, scale)
        base = np.asarray(part.vertices, dtype=np.float64)
        scaled = np.asarray([tuple(v.co) for v in obj.data.vertices], dtype=np.float64)
        # Der Blocky-Rig liegt im Blender-Raum: x = Breite, y = Tiefe, z = Hoehe.
        expected = np.stack(
            [base[:, 0] * 2.0, base[:, 1] * 4.0, base[:, 2] * 3.0], axis=1
        )
        self.assertTrue(np.allclose(scaled, expected, atol=1e-5))

    def test_inventory_part_mesh_is_scaled_in_blender_axes(self):
        from scripts.classic_render import BodyScale, _create_inventory_part
        from scripts.r15_blocky_rig import load_r15_blocky_rig
        from scripts.roblox_opencloud import BodyPartSpec

        bpy.ops.wm.read_factory_settings(use_empty=True)
        verts = [
            (-1.0, -1.0, -1.0), (1.0, -1.0, -1.0), (1.0, 1.0, -1.0), (-1.0, 1.0, -1.0),
            (-1.0, -1.0, 1.0), (1.0, -1.0, 1.0), (1.0, 1.0, 1.0), (-1.0, 1.0, 1.0),
        ]
        faces = [(0, 3, 2), (0, 2, 1), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                 (2, 3, 7), (2, 7, 6), (1, 2, 6), (1, 6, 5), (0, 4, 7), (0, 7, 3)]
        mesh = MeshData("test", verts, [(0.0, 0.0, 1.0)] * len(verts), [(0.0, 0.0)] * len(verts), None, faces)
        body = BodyPartSpec(1, 31, mesh)
        scale = BodyScale(width=2.0, height=3.0, depth=4.0, head=1.0)
        part = load_r15_blocky_rig()["RightLowerLeg"]
        with tempfile.TemporaryDirectory() as tmp:
            objects = []
            obj = _create_inventory_part(
                part, body, (1, 1, 1), "Matt", None, bpy.context.scene, objects, scale=scale
            )
        coords = np.asarray([tuple(v.co) for v in obj.data.vertices], dtype=np.float64)
        # Roblox (x, y, z) -> Blender (-x, z, y); danach Breite/Tiefe/Hoehe.
        expected = np.stack([-verts[0][0] * 2.0, verts[0][2] * 4.0, verts[0][1] * 3.0])
        self.assertTrue(np.allclose(coords[0], expected, atol=1e-5))

    def test_head_is_scaled_uniformly(self):
        from scripts.classic_render import BodyScale, _create_r15_part
        from scripts.r15_blocky_rig import load_r15_blocky_rig

        bpy.ops.wm.read_factory_settings(use_empty=True)
        part = load_r15_blocky_rig()["Head"]
        scale = BodyScale(width=1.0, height=1.0, depth=1.0, head=2.0)
        material = bpy.data.materials.new("TestMat")
        objects = []
        obj = _create_r15_part(part, material, bpy.context.scene, objects, scale)
        base = np.asarray(part.vertices, dtype=np.float64)
        scaled = np.asarray([tuple(v.co) for v in obj.data.vertices], dtype=np.float64)
        center = (base.min(axis=0) + base.max(axis=0)) / 2.0
        expected = center + (base - center) * 2.0
        self.assertTrue(np.allclose(scaled, expected, atol=1e-5))

    def test_emote_joints_follow_the_body_scale(self):
        from scripts.classic_render import BodyScale, _emote_joint_rig

        scale = BodyScale(width=2.0, height=3.0, depth=4.0, head=1.0)
        joint = _emote_joint_rig("Left Hip", scale)
        rest = _emote_joint_rig("Left Hip")
        self.assertAlmostEqual(joint[0], rest[0] * 2.0, places=5)
        self.assertAlmostEqual(joint[1], rest[1] * 3.0, places=5)
        self.assertAlmostEqual(joint[2], rest[2] * 4.0, places=5)
