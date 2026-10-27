# Lokale Hintergründe

Alle Bilder liegen in nach tatsächlicher Farbe benannten Unterordnern. Die Dateinamen beschreiben nur das Motiv, keine Farbe. Für eine bestimmte Farbe in `configs.toml` Ordner und Motivnamen verwenden, z. B. `lila/herzen-konfetti` oder `blau/herzen-konfetti`. Die Erweiterung `.png` ist optional. Ein unqualifizierter Name wie `sternnebel` wählt automatisch die Originalfarbe des hochgeladenen Motivs.

Jedes Motiv liegt im Ordner seiner Originalfarbe sowie in drei zusätzlichen Farbvarianten. Die Varianten werden in die Ordner `rot`, `blau`, `gruen` und `gold` einsortiert; pro Motiv wird die Variante ausgelassen, die der Originalfarbe am nächsten liegt. So steht die Farbe nur im Ordnernamen, nicht zusätzlich im Dateinamen. Transparente Bereiche und Helligkeitsdetails bleiben erhalten.

Die hochgeladenen Originale sind PNG-Vorschauen mit 512 × 512 Pixeln; die Varianten haben dieselbe Auflösung. Unterstützte lokale Formate: PNG, JPG/JPEG, WebP, TIFF und BMP. Alle werden als Image Texture auf das Mesh-Plane `Background` in `WomanRig.blend` gelegt.

| Nr. | Original-Ordner | Motivname |
|---:|---|---|
| 1 | `lila` | `herzen-konfetti` |
| 2 | `lila` | `wirbel-linien` |
| 3 | `lila` | `schachbrett-tunnel` |
| 4 | `lila` | `marmor-wirbel` |
| 5 | `gruen` | `energie-wirbel` |
| 6 | `blau` | `kreuz-strahl` |
| 7 | `blau` | `glitzer-strahl` |
| 8 | `lila` | `organische-formen` |
| 9 | `pink` | `blueten-spritzer` |
| 10 | `lila` | `stern-explosion` |
| 11 | `gold` | `sonnenstrahlen` |
| 12 | `lila` | `herzen-glitzer` |
| 13 | `tuerkis` | `neon-sterne` |
| 14 | `blau` | `hologramm-prismen` |
| 15 | `blau` | `optikwellen` |
| 16 | `tuerkis` | `stern-strahl` |
| 17 | `blau` | `frostspritzer` |
| 18 | `lila` | `kristallsplitter` |
| 19 | `pink` | `fluid-wellen` |
| 20 | `rot` | `sternnebel` |

Die Namen und Farbordner lassen sich reproduzierbar mit `python scripts/generate_background_variants.py` herstellen.

Der GFXRhino-PFP-Background-Pack ist kostenlos auf der [Produktseite](https://gfxrhino.com/products/pfp-background-pack/) verfügbar. Lizenzhinweise: https://gfxrhino.com/license
