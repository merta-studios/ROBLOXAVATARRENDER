"""Tests for config parsing and user-selectable render inputs."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from scripts.render_config import (
    gfx_type_from_config_path,
    normalize_color,
    read_classic_config,
    read_config,
    resolve_background,
)


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

    def test_korblox_and_headless_default_to_false(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_config(Path(temp), {"username": "User"})
            config = read_config(path)
        self.assertFalse(config["korblox"])
        self.assertFalse(config["headless"])

    def test_korblox_and_headless_can_be_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_config(Path(temp), {
                "username": "User",
                "korblox": True,
                "headless": True,
            })
            config = read_config(path)
        self.assertTrue(config["korblox"])
        self.assertTrue(config["headless"])

    def test_korblox_and_headless_must_be_bool(self):
        for override in [{"korblox": "yes"}, {"headless": 1}]:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as temp:
                data = {"username": "User", **override}
                path = self.write_config(Path(temp), data)
                with self.assertRaisesRegex(ValueError, "muss true oder false sein"):
                    read_config(path)

    def test_reads_classic_config(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_config(Path(temp), {
                "username": "@ClassicUser",
                "background": "#ff0000",
                "material": "matt",
                "pose": "Emote",
                "emote_asset_id": 12345678,
                "rotation_deg": 45,
                "abstand_oben": 10,
                "abstand_unten": -10,
                "outline": {"thickness_percent": 1.0, "outside": True, "fade_start_percent": 50, "color": "black"},
            })
            config = read_classic_config(path)
        self.assertEqual(config["username"], "ClassicUser")
        self.assertEqual(config["background"], "#ff0000")
        self.assertEqual(config["material"], "Matt")
        self.assertEqual(config["pose"], "Emote")
        self.assertEqual(config["emote_asset_id"], 12345678)
        self.assertEqual(config["rotation_deg"], 45.0)
        self.assertEqual(config["abstand_oben"], 10.0)
        self.assertEqual(config["abstand_unten"], -10.0)
        self.assertEqual(config["outline"]["color"], "black")
        self.assertFalse(config["korblox"])
        self.assertFalse(config["headless"])

    def test_classic_config_validates_pose_and_ranges(self):
        cases = [
            ({"pose": "Tanzt"}, "'pose' muss"),
            ({"rotation_deg": 190}, "zwischen -180 und 180"),
            ({"rotation_deg": -191}, "zwischen -180 und 180"),
            ({"emote_asset_id": -5}, "nicht negativ"),
        ]
        for override, expected in cases:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as temp:
                data = {"username": "User", **override}
                path = self.write_config(Path(temp), data)
                with self.assertRaisesRegex(ValueError, expected):
                    read_classic_config(path)

    def test_gfx_type_from_config_path(self):
        self.assertEqual(gfx_type_from_config_path(Path("HearthandsType/configs.toml"), None), "HearthandsType")
        self.assertEqual(gfx_type_from_config_path(Path("ClassicType/configs.toml"), None), "ClassicType")
        self.assertEqual(gfx_type_from_config_path(Path("configs.toml"), None), "HearthandsType")
        self.assertEqual(gfx_type_from_config_path(Path("HearthandsType/configs.toml"), "ClassicType"), "ClassicType")
        with self.assertRaisesRegex(ValueError, "Unbekannter GFX-Typ"):
            gfx_type_from_config_path(Path("x.toml"), "Nope")

    def test_korblox_asset_id_matches_official_catalog(self):
        """Die offizielle Korblox Deathspeaker Right Leg hat Asset-ID 139607718."""
        from scripts.roblox_opencloud import KORBLOX_RIGHT_LEG_ASSET_ID

        self.assertEqual(KORBLOX_RIGHT_LEG_ASSET_ID, 139607718)


if __name__ == "__main__":
    unittest.main()
