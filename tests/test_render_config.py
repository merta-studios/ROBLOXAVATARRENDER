"""Tests for config parsing and user-selectable render inputs."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from scripts.render_config import normalize_color, read_config, resolve_background


class RenderConfigTests(unittest.TestCase):
    def write_config(self, directory: Path, data: dict) -> Path:
        path = directory / "configs.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_reads_defaults_and_normalizes_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_config(Path(temp), {
                "username": "@ExampleUser",
                "material": "matt",
                "outline": {"thickness_percent": 1.25, "outside": False,
                            "fade_start_percent": 50, "color": "#abc"},
            })
            config = read_config(path)
        self.assertEqual(config["username"], "ExampleUser")
        self.assertEqual(config["background"], "transparent")
        self.assertEqual(config["material"], "Matt")
        self.assertEqual(config["outline"]["thickness_percent"], 1.25)
        self.assertEqual(config["outline"]["color"], "#abc")

    def test_validates_material_and_outline_percentages(self):
        for override, expected in [
            ({"material": "Satin"}, "genau 'Glossy' oder 'Matt'"),
            ({"outline": {"thickness_percent": 101}}, "zwischen 0 und 100"),
            ({"outline": {"fade_start_percent": -1}}, "zwischen 0 und 100"),
        ]:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as temp:
                data = {"username": "User", **override}
                path = self.write_config(Path(temp), data)
                with self.assertRaisesRegex(ValueError, expected):
                    read_config(path)

    def test_normalizes_short_hex_and_shortcut_colors(self):
        self.assertEqual(normalize_color("#abc"), (170, 187, 204))
        self.assertEqual(normalize_color("WHITE"), (255, 255, 255))
        self.assertEqual(normalize_color("black"), (0, 0, 0))

    def test_resolves_local_background_by_stem(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backgrounds = root / "backgrounds"
            backgrounds.mkdir()
            image = backgrounds / "neon-city.png"
            Image.new("RGB", (8, 8), "purple").save(image)
            with patch("scripts.render_config.BACKGROUND_DIR", backgrounds):
                kind, path = resolve_background("Neon-City", root)
        self.assertEqual(kind, "image")
        self.assertEqual(Path(path), image)

    def test_resolves_transparent_and_color_backgrounds_without_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(resolve_background("transparent", root), ("transparent", None))
            self.assertEqual(resolve_background("#12abef", root), ("color", "#12abef"))


if __name__ == "__main__":
    unittest.main()
