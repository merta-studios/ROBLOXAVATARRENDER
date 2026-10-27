"""Reine Layout-Tests für den ClassicType-Abstand (benötigen kein Blender/bpy)."""

import unittest

from scripts.gfx_layout import compute_spacing_transform

ORTHO_SCALE = 4.0
IMAGE_HEIGHT_PX = 2000


class GfxLayoutTests(unittest.TestCase):
    def test_ohne_abstand_fuellt_der_avatar_den_rahmen(self):
        """AbstandOben = 0 und AbstandUnten = 0: Avatar fuellt die Bildhoehe."""
        scale, margin = compute_spacing_transform(2.0, ORTHO_SCALE, IMAGE_HEIGHT_PX, 0, 0)
        self.assertAlmostEqual(scale, ORTHO_SCALE / 2.0)
        self.assertAlmostEqual(margin, 0.0)
        # Bildschirmhoehe des Avatars = avatar_height * scale / ortho * IMAGE_HEIGHT_PX = IMAGE_HEIGHT_PX
        screen_px = 2.0 * scale / ORTHO_SCALE * IMAGE_HEIGHT_PX
        self.assertAlmostEqual(screen_px, IMAGE_HEIGHT_PX)

    def test_abstand_oben_100_oben_eine_avatar_hoehe_frei(self):
        """AbstandOben = 100: oben steht eine volle (skalierte) Avatar-Hoehe
        bevor der Avatar beginnt; beide teilen sich die Bildhoehe je zur
        Haelfte."""
        scale, margin = compute_spacing_transform(2.0, ORTHO_SCALE, IMAGE_HEIGHT_PX, 100, 0)
        self.assertAlmostEqual(scale, ORTHO_SCALE / (2.0 * 2.0))
        screen_px = 2.0 * scale / ORTHO_SCALE * IMAGE_HEIGHT_PX
        self.assertAlmostEqual(screen_px, IMAGE_HEIGHT_PX / 2.0)
        margin_px = margin / ORTHO_SCALE * IMAGE_HEIGHT_PX
        self.assertAlmostEqual(margin_px, IMAGE_HEIGHT_PX / 2.0)

    def test_abstand_oben_10_oben_zehn_prozent_der_avatar_hoehe_frei(self):
        """AbstandOben = 10, AbstandUnten = 0: oben 10% der (skalierten)
        Avatar-Hoehe Platz; der Avatar schrumpft geringfugig, damit beides
        in die Bildhoehe passt."""
        scale, margin = compute_spacing_transform(2.0, ORTHO_SCALE, IMAGE_HEIGHT_PX, 10, 0)
        self.assertAlmostEqual(scale, ORTHO_SCALE / (2.0 * 1.1))
        screen_px = 2.0 * scale / ORTHO_SCALE * IMAGE_HEIGHT_PX
        self.assertAlmostEqual(screen_px, IMAGE_HEIGHT_PX / 1.1)
        margin_px = margin / ORTHO_SCALE * IMAGE_HEIGHT_PX
        self.assertAlmostEqual(margin_px, 0.1 * screen_px)

    def test_negativer_abstand_rutscht_in_den_rand(self):
        """AbstandUnten = -10: der Avatar rutscht zu 10% in den unteren Rand."""
        scale, margin = compute_spacing_transform(2.0, ORTHO_SCALE, IMAGE_HEIGHT_PX, 0, -10)
        self.assertAlmostEqual(margin, 0.0)
        screen_px = 2.0 * scale / ORTHO_SCALE * IMAGE_HEIGHT_PX
        # 10% grösser als das Bild -> läuft unten aus dem Rahmen
        self.assertAlmostEqual(screen_px, IMAGE_HEIGHT_PX / 0.9)

    def test_oben_und_unten_teilen_sich_den_platz(self):
        scale, margin = compute_spacing_transform(2.0, ORTHO_SCALE, IMAGE_HEIGHT_PX, 25, 25)
        screen_px = 2.0 * scale / ORTHO_SCALE * IMAGE_HEIGHT_PX
        self.assertAlmostEqual(screen_px, IMAGE_HEIGHT_PX / 1.5)
        margin_px = margin / ORTHO_SCALE * IMAGE_HEIGHT_PX
        self.assertAlmostEqual(margin_px, 0.25 * screen_px)


if __name__ == "__main__":
    unittest.main()
