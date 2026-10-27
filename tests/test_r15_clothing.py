"""Reine Python-Tests fuer R15-Classic-Clothing-Texturen, UVs und Hintergruende."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from scripts.r15_blocky_rig import EXPECTED_PARTS, PART_COLOR_KEYS, load_r15_blocky_rig
from scripts.r15_clothing import (
    FACE_NAMES,
    FACE_TILE_POS,
    R15_ATLAS_H,
    R15_ATLAS_W,
    compose_r15_part_textures,
    compute_part_uvs,
    face_uv_rect,
)
from scripts.render_config import resolve_background

BODY_COLORS = {
    "head": (245, 205, 48),
    "torso": (13, 105, 172),
    "rightArm": (1, 2, 3),
    "leftArm": (4, 5, 6),
    "rightLeg": (7, 8, 9),
    "leftLeg": (10, 11, 12),
}
SHIRT_RGB = (220, 30, 30)
PANTS_RGB = (30, 220, 30)
TSHIRT_RGB = (30, 30, 220)
FACE_RGB = (255, 255, 255)


def tile_center(face: str) -> tuple[int, int]:
    col, row = FACE_TILE_POS[face]
    return (col * 128 + 64, row * 128 + 64)


class R15ClothingTests(unittest.TestCase):
    def test_composes_15_part_textures_with_clothing_and_tshirt(self) -> None:
        shirt = Image.new("RGBA", (585, 559), (*SHIRT_RGB, 255))
        pants = Image.new("RGBA", (585, 559), (*PANTS_RGB, 255))
        tshirt = Image.new("RGBA", (512, 512), (*TSHIRT_RGB, 255))
        face = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        ImageDraw.Draw(face).ellipse((240, 240, 272, 272), fill=(*FACE_RGB, 255))

        with tempfile.TemporaryDirectory() as tmp:
            out = compose_r15_part_textures(
                BODY_COLORS, shirt, pants, tshirt, face, Path(tmp)
            )
            self.assertEqual(len(out), len(EXPECTED_PARTS))
            for part_name, path in out.items():
                with self.subTest(part=part_name):
                    with Image.open(path) as image:
                        self.assertEqual(image.size, (R15_ATLAS_W, R15_ATLAS_H))
                        self.assertEqual(image.mode, "RGB")

            with Image.open(out["UpperTorso"]) as upper:
                # T-Shirt liegt als Decal vor dem Shirt.
                self.assertEqual(upper.getpixel(tile_center("Front")), TSHIRT_RGB)
                self.assertEqual(upper.getpixel(tile_center("Back")), SHIRT_RGB)
            with Image.open(out["LowerTorso"]) as lower:
                self.assertEqual(lower.getpixel(tile_center("Front")), TSHIRT_RGB)
                self.assertEqual(lower.getpixel(tile_center("Back")), PANTS_RGB)
            with Image.open(out["LeftUpperArm"]) as arm:
                self.assertEqual(arm.getpixel(tile_center("Front")), SHIRT_RGB)
            with Image.open(out["LeftUpperLeg"]) as leg:
                self.assertEqual(leg.getpixel(tile_center("Front")), PANTS_RGB)
            with Image.open(out["Head"]) as head:
                self.assertEqual(head.getpixel(tile_center("Front")), FACE_RGB)
                self.assertEqual(head.getpixel(tile_center("Back")), BODY_COLORS["head"])

    def test_without_clothing_all_tiles_use_body_colors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = compose_r15_part_textures(BODY_COLORS, None, None, None, None, Path(tmp))
            self.assertEqual(len(out), len(EXPECTED_PARTS))
            for part_name, path in out.items():
                expected = BODY_COLORS[PART_COLOR_KEYS[part_name]]
                with Image.open(path) as image:
                    for face in FACE_NAMES:
                        with self.subTest(part=part_name, face=face):
                            self.assertEqual(image.getpixel(tile_center(face)), expected)

    def test_compute_part_uvs_maps_all_six_faces(self) -> None:
        parts = load_r15_blocky_rig()
        for part_name in ("UpperTorso", "LeftUpperArm", "LeftUpperLeg", "Head"):
            with self.subTest(part=part_name):
                uvs = compute_part_uvs(parts[part_name])
                self.assertTrue(uvs)
                tiles: set[str] = set()
                for u, v in uvs:
                    self.assertGreaterEqual(u, -1e-6)
                    self.assertLessEqual(u, 1.0 + 1e-6)
                    self.assertGreaterEqual(v, -1e-6)
                    self.assertLessEqual(v, 1.0 + 1e-6)
                    for face in FACE_NAMES:
                        u0, v0, u1, v1 = face_uv_rect(face)
                        if u0 - 1e-6 <= u <= u1 + 1e-6 and v0 - 1e-6 <= v <= v1 + 1e-6:
                            tiles.add(face)
                            break
                if part_name == "Head":
                    self.assertIn("Front", tiles)
                    self.assertGreaterEqual(len(tiles), 2)
                else:
                    self.assertEqual(tiles, set(FACE_NAMES))

    def test_classic_render_wires_part_textures_and_new_uvs(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        classic = (repo_root / "scripts/classic_render.py").read_text(encoding="utf-8")
        clothing = (repo_root / "scripts/r15_clothing.py").read_text(encoding="utf-8")
        self.assertIn("r15_part_textures", classic)
        self.assertIn("texture_path=texture_path", classic)
        self.assertIn("compute_part_uvs(part)", classic)
        self.assertNotIn("import bpy", clothing)
        self.assertIn("compose_r15_part_textures", clothing)

    def test_monochrome_background_folders_exist_and_resolve(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        bg_root = repo_root / "backgrounds"
        expected = {
            "weiss/softes-licht": "weiss/softes-licht.png",
            "weiss/papier-minimal": "weiss/papier-minimal.png",
            "weiss/helle-wellen": "weiss/helle-wellen.png",
            "schwarz/deeper-space": "schwarz/deeper-space.png",
            "schwarz/neon-linien": "schwarz/neon-linien.png",
            "schwarz/dunkles-raster": "schwarz/dunkles-raster.png",
        }
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            for selector, relative in expected.items():
                with self.subTest(selector=selector):
                    image_path = bg_root / relative
                    self.assertTrue(image_path.is_file())
                    with Image.open(image_path) as image:
                        self.assertEqual(image.size, (512, 512))
                    kind, path = resolve_background(selector, work_dir)
                    self.assertEqual(kind, "image")
                    self.assertEqual(Path(path).resolve(), image_path.resolve())
            # Nackter Motivname waehlt automatisch den Originalordner.
            kind, path = resolve_background("neon-linien", work_dir)
            self.assertEqual(kind, "image")
            self.assertEqual(Path(path).resolve(), (bg_root / "schwarz/neon-linien.png").resolve())


if __name__ == "__main__":
    unittest.main()
