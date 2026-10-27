"""Einmaliges Speichern des festen Render-Setups in WomanRig.blend.

Ausführen mit Blender 5.x/bpy 5.x, z. B.:
    python scripts/configure_blend_scene.py --blend-file WomanRig.blend
Die Avatar-Pipeline ändert danach keine Kamera-, Licht- oder Engine-Werte mehr.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import bpy
from mathutils import Vector

from scripts.blender_render import _setup_studio_lighting_and_world

# Fester, quadratischer Oberkörper-Ausschnitt. Der Bildausschnitt umfasst die
# üblichen Haare/Hüte und reicht bis knapp unter die Hüfte.
CAMERA_LOCATION = (0.0, -20.0, 3.80)
CAMERA_TARGET = (0.0, 0.0, 3.80)
CAMERA_ORTHO_SCALE = 4.0
RENDER_SIZE = 2000


def configure_scene() -> None:
    scene = bpy.context.scene

    # Idempotent: keine alten Setup-Objekte duplizieren.
    for obj in list(bpy.data.objects):
        if obj.type in {"CAMERA", "LIGHT"} or obj.name.startswith("GFX_"):
            bpy.data.objects.remove(obj, do_unlink=True)

    _setup_studio_lighting_and_world(scene)

    camera_data = bpy.data.cameras.new("GFX_Camera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = CAMERA_ORTHO_SCALE
    camera_data.clip_start = 0.1
    camera_data.clip_end = 1000.0
    camera = bpy.data.objects.new("GFX_Camera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = Vector(CAMERA_LOCATION)
    direction = Vector(CAMERA_TARGET) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera

    scene.render.engine = "CYCLES"
    scene.render.film_transparent = True
    scene.render.resolution_x = RENDER_SIZE
    scene.render.resolution_y = RENDER_SIZE
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = 1.0
    scene.render.pixel_aspect_y = 1.0
    scene.render.use_border = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"

    scene.cycles.device = "CPU"
    scene.cycles.samples = 64
    scene.cycles.use_denoising = True
    if hasattr(scene.cycles, "denoiser"):
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 4
    scene.cycles.diffuse_bounces = 1
    scene.cycles.glossy_bounces = 2
    scene.cycles.transmission_bounces = 2
    scene.cycles.volume_bounces = 0
    scene.cycles.transparent_max_bounces = 4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blend-file", default="WomanRig.blend")
    args = parser.parse_args()
    blend_path = Path(args.blend_file).resolve()
    if not blend_path.is_file():
        raise SystemExit(f"Blender-Datei nicht gefunden: {blend_path}")
    bpy.ops.wm.open_mainfile(filepath=str(blend_path))
    configure_scene()
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    print(
        f"Gespeichert: {blend_path} | Engine={bpy.context.scene.render.engine} "
        f"| Kamera={bpy.context.scene.camera.name} bei {tuple(bpy.context.scene.camera.location)}"
    )


if __name__ == "__main__":
    main()
