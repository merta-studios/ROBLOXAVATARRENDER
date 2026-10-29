"""Tests for config parsing and user-selectable render inputs."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

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
            })
            config = read_config(path)
        self.assertEqual(config["username"], "ExampleUser")
        self.assertEqual(config["background"], "transparent")
        self.assertEqual(config["material"], "Matt")

    def test_validates_material_percentage(self):
        for override, expected in [
            ({"material": "Satin"}, "genau 'Glossy' oder 'Matt'"),
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
        from scripts.image_compositing import compose_background

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(resolve_background("transparent", root), ("transparent", None))
            self.assertEqual(resolve_background("#12abef", root), ("color", "#12abef"))

            # ClassicType setzt den Hintergrund nach dem transparenten Render
            # zusammen; das Ergebnis muss weiterhin ein RGBA-PNG sein.
            avatar = root / "avatar.png"
            out = root / "out.png"
            Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(avatar)
            compose_background(avatar, out, kind="color", image_path=None, value="#12abef")
            with Image.open(out) as image:
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.convert("RGBA").getpixel((1, 1)), (18, 171, 239, 255))

            # Bild-Hintergrund funktioniert auch ohne bpy.
            bg = root / "bg.png"
            Image.new("RGBA", (8, 8), (10, 20, 30, 255)).save(bg)
            avatar8 = root / "avatar8.png"
            square = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            ImageDraw.Draw(square).rectangle((3, 3, 4, 4), fill=(200, 30, 40, 255))
            square.save(avatar8)
            composed = root / "composed.png"
            compose_background(avatar8, composed, kind="image", image_path=str(bg), value="image")
            with Image.open(composed) as image:
                rgba = image.convert("RGBA")
                self.assertEqual(rgba.getpixel((0, 0))[:3], (10, 20, 30))
                self.assertEqual(rgba.getpixel((3, 3))[:3], (200, 30, 40))

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
                "animation_asset_id": 658830056,
                "rotation_deg": 45,
                "abstand_oben": 10,
                "abstand_unten": -10,
            })
            config = read_classic_config(path)
        self.assertEqual(config["username"], "ClassicUser")
        self.assertEqual(config["background"], "#ff0000")
        self.assertEqual(config["material"], "Matt")
        self.assertEqual(config["pose"], "Emote")
        self.assertEqual(config["emote_asset_id"], 12345678)
        self.assertEqual(config["animation_asset_id"], 658830056)
        self.assertEqual(config["rotation_deg"], 45.0)
        self.assertEqual(config["abstand_oben"], 10.0)
        self.assertEqual(config["abstand_unten"], -10.0)
        self.assertFalse(config["korblox"])
        self.assertFalse(config["headless"])

        # Bei pose="None" darf KEIN Emote geladen werden; bei "Emote" schon.
        from scripts.run_render_pipeline import resolve_classic_pose

        class SpyClient:
            def __init__(self):
                self.calls = []

            def fetch_equipped_emote(self, user_id):
                self.calls.append(("equipped", user_id))
                return None

            def load_animation(self, asset_id, work_dir):
                self.calls.append(("load", asset_id))
                return SimpleNamespace(asset_id=asset_id)

        with tempfile.TemporaryDirectory() as pose_tmp:
            work_dir = Path(pose_tmp)
            bundle = SimpleNamespace(user_id=42)
            none_client = SpyClient()
            pose, emote, _asset_id = resolve_classic_pose(
                config={"pose": "None", "emote_asset_id": 999},
                bundle=bundle,
                client=none_client,
                work_dir=work_dir,
            )
            self.assertEqual(pose, "None")
            self.assertIsNone(emote)
            self.assertEqual(none_client.calls, [])

            emote_client = SpyClient()
            with patch("builtins.print"):
                resolve_classic_pose(
                    config={"pose": "Emote", "emote_asset_id": 123},
                    bundle=bundle,
                    client=emote_client,
                    work_dir=work_dir,
                )
            self.assertEqual(emote_client.calls, [("load", 123)])

            # pose = "Animation" laedt die Animation ueber animation_asset_id.
            animation_client = SpyClient()
            with patch("builtins.print"):
                pose, emote, asset_id = resolve_classic_pose(
                    config={"pose": "Animation", "animation_asset_id": 658830056},
                    bundle=bundle,
                    client=animation_client,
                    work_dir=work_dir,
                )
            self.assertEqual(pose, "Animation")
            self.assertIsNotNone(emote)
            self.assertEqual(animation_client.calls, [("load", 658830056)])
            self.assertEqual(asset_id, 658830056)

            # Ohne animation_asset_id bleibt die Pose "None".
            empty_client = SpyClient()
            with patch("builtins.print"):
                pose, emote, _asset_id = resolve_classic_pose(
                    config={"pose": "Animation", "animation_asset_id": 0},
                    bundle=bundle,
                    client=empty_client,
                    work_dir=work_dir,
                )
            self.assertEqual(pose, "None")
            self.assertEqual(empty_client.calls, [])

        # Die echte ClassicType/configs.toml darf die ClassicType-Optionen nicht
        # versehentlich als TOML-Unterschluessel von [outline] enthalten.
        import tomllib

        repo_root = Path(__file__).resolve().parents[1]
        raw = tomllib.loads((repo_root / "ClassicType/configs.toml").read_text(encoding="utf-8"))
        for key in ("pose", "emote_asset_id", "animation_asset_id", "rotation_deg",
                    "abstand_oben", "abstand_unten", "korblox", "headless"):
            with self.subTest(key=key):
                self.assertIn(key, raw)
                self.assertNotIn(key, raw.get("outline", {}))

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

        # Statische Trennung: R6-Renderer kennt keine ClassicType-Parameter,
        # R15-Renderer oeffnet keine Blend-Datei, Orchestrator definiert alle
        # Variablen vor ihrer Verwendung.
        repo_root = Path(__file__).resolve().parents[1]
        r6_source = (repo_root / "scripts/blender_render.py").read_text(encoding="utf-8")
        for forbidden in (
            "classic_pose", "classic_emote", "classic_rotation_deg",
            "classic_abstand_oben", "classic_abstand_unten",
            "EmoteAnimation", "EmoteKeyframe", "compute_spacing_transform",
            "_apply_emote_pose_to_armature", "_apply_rotation_around_z",
            "_apply_scale_around_center", "ClassicType",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, r6_source)
        self.assertNotIn("classic", r6_source.casefold())

        r15_source = (repo_root / "scripts/classic_render.py").read_text(encoding="utf-8")
        self.assertNotIn("open_mainfile", r15_source)
        self.assertNotIn("WomanRig.blend", r15_source)
        self.assertIn("read_factory_settings", r15_source)
        # Keine Quader-Platzhalter mehr: der offizielle R15-Blocky-Rig wird
        # als reines Python-Asset geladen (kein bpy, keine Blend-Datei).
        self.assertNotIn("BODY_BLOCKS", r15_source)
        self.assertIn("load_r15_blocky_rig", r15_source)
        self.assertIn("_create_r15_part", r15_source)
        rig_source = (repo_root / "scripts/r15_blocky_rig.py").read_text(encoding="utf-8")
        self.assertNotIn("import bpy", rig_source)
        fbx = repo_root / "ClassicType" / "assets" / "R15_Block.fbx"
        self.assertTrue(fbx.is_file())
        self.assertTrue(fbx.read_bytes().startswith(b"Kaydara FBX Binary"))

        from scripts.r15_blocky_rig import (
            ATTACHMENT_MODEL_NAMES,
            EXPECTED_PARTS,
            load_r15_attachments,
            load_r15_blocky_rig,
        )

        parts = load_r15_blocky_rig()
        self.assertEqual(len(parts), len(EXPECTED_PARTS))
        self.assertIn("UpperTorso", parts)
        self.assertEqual(parts["LeftHand"].color_key, "leftArm")
        for name in EXPECTED_PARTS:
            with self.subTest(part=name):
                self.assertIn(name, parts)
                self.assertGreater(len(parts[name].vertices), 0)
                self.assertGreater(len(parts[name].faces), 0)
                self.assertEqual(
                    len(parts[name].uvs),
                    sum(len(face) for face in parts[name].faces),
                )
        self.assertEqual(len(load_r15_attachments()), len(ATTACHMENT_MODEL_NAMES))

        pipeline_source = (repo_root / "scripts/run_render_pipeline.py").read_text(encoding="utf-8")
        main_source = pipeline_source.split("def main()", 1)[-1]
        self.assertNotIn("output_png=output_png", main_source)
        self.assertEqual(main_source.count("output_png=output_path"), 2)
        self.assertIn("background_value = str(config[\"background\"])", main_source)

    def test_korblox_asset_id_matches_official_catalog(self):
        """Die offizielle Korblox Deathspeaker Right Leg hat Asset-ID 139607718."""
        from scripts.roblox_opencloud import KORBLOX_RIGHT_LEG_ASSET_ID

        self.assertEqual(KORBLOX_RIGHT_LEG_ASSET_ID, 139607718)


if __name__ == "__main__":
    unittest.main()
