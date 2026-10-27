"""Blender regression checks: python -m unittest discover -s tests."""
import tempfile
import unittest
from pathlib import Path

import bpy
import numpy as np
from PIL import Image, ImageDraw

from scripts.blender_render import (
    AVATAR_ATTACHMENT_DROP,
    GLOSS_COAT_ROUGHNESS,
    GLOSS_COAT_WEIGHT,
    GLOSS_ROUGHNESS,
    _bake_head_composite_texture,
    _clone_rig_material,
)
from scripts.configure_blend_scene import (
    CAMERA_LOCATION,
    CAMERA_ORTHO_SCALE,
    RENDER_SIZE,
    configure_scene,
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
        self.assertAlmostEqual(shader.inputs["Roughness"].default_value, GLOSS_ROUGHNESS)
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

    def test_blend_scene_preset_uses_fixed_cycles_camera(self):
        configure_scene()
        scene = bpy.context.scene
        self.assertEqual(scene.render.engine, "CYCLES")
        self.assertIsNotNone(scene.camera)
        self.assertEqual(scene.camera.name, "GFX_Camera")
        self.assertEqual(tuple(scene.camera.location), CAMERA_LOCATION)
        self.assertEqual(scene.camera.data.type, "ORTHO")
        self.assertEqual(scene.camera.data.ortho_scale, CAMERA_ORTHO_SCALE)
        self.assertEqual(scene.render.resolution_x, RENDER_SIZE)
        self.assertEqual(scene.render.resolution_y, RENDER_SIZE)
        self.assertTrue(scene.render.film_transparent)
        self.assertEqual(
            len([obj for obj in scene.objects if obj.name.startswith("GFX_") and obj.type == "LIGHT"]),
            5,
        )

    def test_loaded_head_and_accessories_share_a_tiny_downward_offset(self):
        self.assertEqual(AVATAR_ATTACHMENT_DROP, 0.01)
