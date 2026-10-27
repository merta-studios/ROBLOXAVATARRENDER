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
    _prepare_custom_head_texture,
    _build_blender_mesh_from_rbx,
    _setup_studio_lighting_and_world,
    _apply_avatar_outline,
)
from scripts.rbx_formats import MeshData
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

    def test_matt_material_has_no_clearcoat_and_high_roughness(self):
        mat = _clone_rig_material(None, "Matt", finish="Matt")
        shader = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        self.assertAlmostEqual(shader.inputs["Roughness"].default_value, 0.82)
        self.assertEqual(shader.inputs["Coat Weight"].default_value, 0.0)

    def test_configure_removes_hdris_from_world_shader(self):
        world = bpy.data.worlds.new("HDRIWorld")
        world.use_nodes = True
        tree = world.node_tree
        tree.nodes.clear()
        env = tree.nodes.new("ShaderNodeTexEnvironment")
        env_name = env.name
        hdri_name = "TestHDRI"
        env.image = bpy.data.images.new(hdri_name, width=4, height=2)
        background = tree.nodes.new("ShaderNodeBackground")
        output = tree.nodes.new("ShaderNodeOutputWorld")
        tree.links.new(env.outputs["Color"], background.inputs["Color"])
        tree.links.new(background.outputs["Background"], output.inputs["Surface"])
        bpy.context.scene.world = world

        _setup_studio_lighting_and_world(bpy.context.scene)

        self.assertIsNone(tree.nodes.get(env_name))
        self.assertFalse(any(node.type == "TEX_ENVIRONMENT" for node in tree.nodes))
        self.assertIsNone(bpy.data.images.get(hdri_name))
        world_outputs = [node for node in tree.nodes if node.type == "OUTPUT_WORLD"]
        self.assertTrue(world_outputs)
        self.assertTrue(any(link.to_node in world_outputs for link in tree.links))

    def test_outline_uses_image_width_and_stays_outside_avatar(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "base.png"
            avatar_path = Path(tmp) / "avatar.png"
            Image.new("RGBA", (20, 20), (0, 0, 0, 0)).save(base_path)
            avatar = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
            ImageDraw.Draw(avatar).rectangle((7, 7, 12, 12), fill=(255, 0, 0, 255))
            avatar.save(avatar_path)
            _apply_avatar_outline(
                base_path, avatar_path, thickness_percent=10, outside=True,
                fade_start_percent=100, color=(255, 255, 255),
            )
            result = Image.open(base_path).convert("RGBA")
        self.assertGreater(result.getpixel((6, 9))[3], 0)
        self.assertEqual(result.getpixel((9, 9))[3], 0)
        self.assertEqual(result.getpixel((4, 9))[3], 0)

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

    def test_custom_head_texture_uses_skin_for_alpha_without_replacing_opaque_face(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom_path = Path(tmp) / "dynamic_head.png"
            opaque_path = Path(tmp) / "opaque_head.png"
            image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            ImageDraw.Draw(image).rectangle((2, 2, 5, 5), fill=(30, 15, 10, 255))
            image.save(custom_path)
            opaque = Image.new("RGBA", (8, 8), (25, 15, 10, 255))
            opaque.save(opaque_path)

            composited = _prepare_custom_head_texture(SKIN, str(custom_path), Path(tmp) / "composite.png")
            skin_pixel = Image.open(composited).convert("RGB").getpixel((0, 0))
            face_pixel = Image.open(composited).convert("RGB").getpixel((3, 3))
            opaque_result = _prepare_custom_head_texture(
                SKIN, str(opaque_path), Path(tmp) / "unused.png"
            )

        self.assertEqual(skin_pixel, SKIN)
        self.assertEqual(face_pixel, (30, 15, 10))
        self.assertEqual(opaque_result, str(opaque_path))

    def test_custom_head_mesh_keeps_its_authored_uvs(self):
        mesh_data = MeshData(
            version="test",
            positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            normals=[],
            uvs=[(0.1, 0.2), (0.8, 0.2), (0.1, 0.9)],
            colors=None,
            faces=[(0, 1, 2)],
        )
        mesh = _build_blender_mesh_from_rbx(
            "DynamicHeadUVTest", mesh_data, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
        )
        actual = [tuple(loop.uv) for loop in mesh.uv_layers[0].data]
        for uv, expected in zip(actual, [(0.1, 0.2), (0.8, 0.2), (0.1, 0.9)]):
            self.assertAlmostEqual(uv[0], expected[0])
            self.assertAlmostEqual(uv[1], expected[1])

    def test_blend_scene_preset_uses_fixed_cycles_camera(self):
        configure_scene()
        scene = bpy.context.scene
        self.assertEqual(scene.render.engine, "CYCLES")
        self.assertIsNotNone(scene.camera)
        self.assertEqual(scene.camera.name, "GFX_Camera")
        for actual, expected in zip(scene.camera.location, CAMERA_LOCATION):
            self.assertAlmostEqual(actual, expected, places=5)
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
