"""Reine Layout-Berechnungen für den GFX-Render (ohne Blender-Abhängigkeit).

Hier werden die ClassicType-Einstellungen "AbstandOben" / "AbstandUnten"
umgerechnet: der Abstand ist in Prozent der Avatar-Höhe angegeben, wobei die
Avatar-Höhe die Länge zwischen dem höchsten und tiefsten Punkt des 3D-Modells
ist. 100 bedeutet eine komplette Avatar-Höhe Platz vor dem Avatar, 10 nur 10
Prozent davon, und -10 schiebt den Avatar bereits zu 10% in den Bildrand
hinein.
"""

from __future__ import annotations


def compute_spacing_transform(
    avatar_height: float,
    ortho_scale: float,
    image_height_px: int,
    abstand_oben: float,
    abstand_unten: float,
) -> tuple[float, float]:
    """Rechnet Abstand-von-oben/unten in eine Skalierung und einen oberen
    Rand (Welt-Einheiten) um.

    Der Avatar wird um `scale` um seinen Mittelpunkt skaliert, sodass er plus
    beide Raender exakt die Bildhoehe fuellt. `margin_world` ist der Abstand
    der Avatar-Oberkante vom oberen Bildrand in Welt-Einheiten (entspricht
    abstand_oben Prozent der skalierten Avatar-Hoehe). Negative
    Abstandswerte schieben den Avatar entsprechend in den Rand hinein.

    Returns:
        (scale, margin_world)
    """
    if avatar_height <= 1e-6 or ortho_scale <= 1e-6:
        return 1.0, 0.0
    if image_height_px <= 0:
        raise ValueError("image_height_px muss positiv sein.")

    sum_percent = (abstand_oben + abstand_unten) / 100.0
    # Extrem negative Abstaende duerfen nicht zu Division durch 0 oder zu einer
    # negativen Skalierung fuehren. 0.01 ist ein Schutzlimit, keine Layout-Regel.
    denominator = max(1.0 + sum_percent, 0.01)
    scale = ortho_scale / (avatar_height * denominator)
    margin_world = (abstand_oben / 100.0) * ortho_scale / denominator
    return scale, margin_world
