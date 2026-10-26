"""Blender regression checks: python -m unittest discover -s tests."""
import tempfile
import unittest
from pathlib import Path

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
from PIL import Image, ImageDraw

from scripts.blender_render import (
    GLOSS_COAT_ROUGHNESS,
    GLOSS_COAT_WEIGHT,
    GLOSS_ROUGHNESS,
    _bake_head_composite_texture,
    _clone_rig_material,
    _setup_auto_framed_camera,
)

SKIN = (234, 184, 146)


class RenderRegressionTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def test_texture_material_is_glossy_and_ignores_rig_shader(self):
        """Rig-Shader (glossy/zero-Roughness, UV-/Attribut-Links) darf nicht durchschlagen."""
        source = bpy.data.materials.new('Rig')
        source.use_nodes = True
        shader = source.node_tree.nodes.get('Principled BSDF')
        shader.inputs['Roughness'].default_value = 0.0
        image_node = source.node_tree.nodes.new('ShaderNodeTexImage')
        source.node_tree.links.new(image_node.outputs['Color'], shader.inputs['Roughness'])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'skin.png'
            Image.new('RGB', (8, 8), SKIN).save(path)
            mat = _clone_rig_material(source, 'Head', texture_path=str(path))
        shader = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
        self.assertFalse(shader.inputs['Roughness'].is_linked)
        self.assertAlmostEqual(shader.inputs['Roughness'].default_value, GLOSS_ROUGHNESS)
        self.assertAlmostEqual(shader.inputs['Coat Weight'].default_value, GLOSS_COAT_WEIGHT)
        self.assertAlmostEqual(
            shader.inputs['Coat Roughness'].default_value, GLOSS_COAT_ROUGHNESS
        )
        self.assertEqual(shader.inputs['Metallic'].default_value, 0.0)
        self.assertGreaterEqual(shader.inputs['Specular IOR Level'].default_value, 0.5)
        self.assertEqual(shader.inputs['Base Color'].links[0].from_node.type, 'TEX_IMAGE')
        self.assertFalse(shader.inputs['Alpha'].is_linked)

    def test_opaque_face_background_keeps_head_skin_colored(self):
        """Deckender (schwarzer) Decal-Hintergrund darf die Kopfhaut nicht übermalen."""
        with tempfile.TemporaryDirectory() as tmp:
            face_path = Path(tmp) / 'face_opaque.png'
            face = Image.new('RGB', (256, 256), (0, 0, 0))
            draw = ImageDraw.Draw(face)
            draw.ellipse([100, 100, 130, 130], fill=(255, 255, 255))
            face.save(face_path)
            out = _bake_head_composite_texture(SKIN, str(face_path), Path(tmp) / 'head.png')
            img = Image.open(out).convert('RGB')
        # Zentrum des Front-UV-Bereichs muss Hautfarbe sein, nicht Schwarz
        self.assertEqual(img.getpixel((512, 512)), SKIN)
        # Augendarstellung (weiß) bleibt erhalten
        self.assertGreater(max(img.getpixel((512, 380))), 200)

    def test_transparent_face_decal_is_composited_over_skin(self):
        with tempfile.TemporaryDirectory() as tmp:
            face_path = Path(tmp) / 'face_alpha.png'
            face = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
            ImageDraw.Draw(face).ellipse([60, 60, 200, 200], fill=(25, 25, 25, 255))
            face.save(face_path)
            out = _bake_head_composite_texture(SKIN, str(face_path), Path(tmp) / 'head.png')
            img = Image.open(out).convert('RGB')
        self.assertEqual(img.getpixel((512, 512)), (25, 25, 25))
        self.assertEqual(img.getpixel((5, 5)), SKIN)

    def test_fully_dark_face_asset_falls_back_to_skin(self):
        with tempfile.TemporaryDirectory() as tmp:
            face_path = Path(tmp) / 'face_dark.png'
            arr = np.zeros((256, 256, 3), dtype=np.uint8)
            arr[:, :, 0] = np.linspace(0, 60, 256)[None, :].astype(np.uint8)
            Image.fromarray(arr).save(face_path)
            out = _bake_head_composite_texture(SKIN, str(face_path), Path(tmp) / 'head.png')
            img = Image.open(out).convert('RGB')
        self.assertEqual(img.getpixel((512, 512)), SKIN)

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
