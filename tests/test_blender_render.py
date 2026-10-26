"""Blender regression checks: python -m unittest discover -s tests."""
import tempfile
import unittest
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
from PIL import Image

from scripts.blender_render import _clone_rig_material, _setup_auto_framed_camera


class RenderRegressionTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def test_color_texture_does_not_drive_roughness_or_use_rig_uvs(self):
        source = bpy.data.materials.new('Rig')
        source.use_nodes = True
        shader = source.node_tree.nodes.get('Principled BSDF')
        image_node = source.node_tree.nodes.new('ShaderNodeTexImage')
        source.node_tree.links.new(image_node.outputs['Color'], shader.inputs['Roughness'])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'skin.png'
            Image.new('RGB', (8, 8), (234, 184, 146)).save(path)
            mat = _clone_rig_material(source, 'Head', texture_path=str(path))
        shader = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
        self.assertFalse(shader.inputs['Roughness'].is_linked)
        self.assertAlmostEqual(shader.inputs['Roughness'].default_value, 0.18)
        self.assertEqual(shader.inputs['Base Color'].links[0].from_node.type, 'TEX_IMAGE')
        self.assertFalse(shader.inputs['Alpha'].is_linked)

    def test_square_portrait_top_margin_and_thigh_crop(self):
        scene = bpy.context.scene
        scene.render.resolution_x = scene.render.resolution_y = 2000
        bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 3))
        body = bpy.context.object
        body.scale = (1, 0.5, 3)
        bpy.context.view_layer.update()
        _setup_auto_framed_camera(scene, [body], 2000, 2000)
        bpy.context.view_layer.update()
        top = world_to_camera_view(scene, scene.camera, Vector((0, 0, 6)))
        bottom = world_to_camera_view(scene, scene.camera, Vector((0, 0, 1.95)))
        self.assertAlmostEqual(top.y, 0.975, places=5)
        self.assertAlmostEqual(bottom.y, 0.0, places=5)
        feet = world_to_camera_view(scene, scene.camera, Vector((0, 0, 0)))
        self.assertLess(feet.y, 0)
