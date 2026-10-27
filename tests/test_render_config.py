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
        path = directory / "configs.toml"
        lines = []
        for key, value in data.items():
            if key == "outline":
                continue
            lines.append(f"{key} = {self.toml_value(value)}")
        if "outline" in data:
            lines.append("\n[outline]")
            for key, value in data["outline"].items():
                lines.append(f"{key} = {self.toml_value(value)}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def toml_value(value) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

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

    def test_resolves_color_folder_and_requires_folder_when_stems_repeat(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backgrounds = root / "backgrounds"
            blue = backgrounds / "blau"
            red = backgrounds / "rot"
            blue.mkdir(parents=True)
            red.mkdir()
            Image.new("RGB", (8, 8), "blue").save(blue / "same-design.png")
            Image.new("RGB", (8, 8), "red").save(red / "same-design.png")
            with patch("scripts.render_config.BACKGROUND_DIR", backgrounds):
                kind, path = resolve_background("blau/same-design", root)
                self.assertEqual(kind, "image")
                self.assertEqual(Path(path), blue / "same-design.png")
                with self.assertRaisesRegex(ValueError, "mehrdeutig"):
                    resolve_background("same-design", root)

    def test_bare_design_name_selects_its_original_color_folder(self):
        kind, path = resolve_background("sternnebel", Path("/tmp"))
        self.assertEqual(kind, "image")
        self.assertEqual(Path(path), Path("backgrounds/rot/sternnebel.png").resolve())

    def test_resolves_transparent_and_color_backgrounds_without_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(resolve_background("transparent", root), ("transparent", None))
            self.assertEqual(resolve_background("#12abef", root), ("color", "#12abef"))


if __name__ == "__main__":
    unittest.main()
