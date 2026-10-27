"""Canonical design names and original-color folders for local backgrounds."""

DESIGN_NAMES = {
    1: "herzen-konfetti",
    2: "wirbel-linien",
    3: "schachbrett-tunnel",
    4: "marmor-wirbel",
    5: "energie-wirbel",
    6: "kreuz-strahl",
    7: "glitzer-strahl",
    8: "organische-formen",
    9: "blueten-spritzer",
    10: "stern-explosion",
    11: "sonnenstrahlen",
    12: "herzen-glitzer",
    13: "neon-sterne",
    14: "hologramm-prismen",
    15: "optikwellen",
    16: "stern-strahl",
    17: "frostspritzer",
    18: "kristallsplitter",
    19: "fluid-wellen",
    20: "sternnebel",
}

ORIGINAL_COLORS = {
    1: "lila", 2: "lila", 3: "lila", 4: "lila", 5: "gruen",
    6: "blau", 7: "blau", 8: "lila", 9: "pink", 10: "lila",
    11: "gold", 12: "lila", 13: "tuerkis", 14: "blau", 15: "blau",
    16: "tuerkis", 17: "blau", 18: "lila", 19: "pink", 20: "rot",
}

ORIGINAL_FOLDER_BY_NAME = {
    DESIGN_NAMES[number]: ORIGINAL_COLORS[number] for number in DESIGN_NAMES
}
