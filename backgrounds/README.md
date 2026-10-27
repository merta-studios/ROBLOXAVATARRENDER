# Lokale Hintergründe

Die Dateien in diesem Ordner lassen sich in `configs.toml` über ihren Dateinamen (mit oder ohne `.png`) auswählen. Die 20 hochgeladenen Motive wurden nach ihrem sichtbaren Inhalt benannt. Zu jedem Original gibt es drei zusätzliche Farbvarianten mit Suffix `-rot`, `-blau`, `-gruen` oder `-gold`; die Variante, die der Originalfarbe am nächsten liegt, wird pro Motiv ausgelassen. Transparente Bereiche und Helligkeitsdetails bleiben bei der Umfärbung erhalten.

Die Original-Uploads sind PNG-Vorschauen mit 512 × 512 Pixeln. Die Farbvarianten haben dieselbe Auflösung. Unterstützte lokale Formate: PNG, JPG/JPEG, WebP, TIFF und BMP. Die Bilder werden als Image Texture auf das Mesh-Plane `Background` in `WomanRig.blend` gelegt.

| Nr. | Dateiname des Originals |
|---:|---|
| 1 | `pastell-herzen-konfetti` |
| 2 | `pink-weiss-liquid` |
| 3 | `violett-schachbrett-tunnel` |
| 4 | `lila-liquid-marmor` |
| 5 | `smaragd-energie-wirbel` |
| 6 | `elektroblau-kreuze` |
| 7 | `eisblau-glitzer` |
| 8 | `lavendel-organische-formen` |
| 9 | `pink-blueten-spritzer` |
| 10 | `violette-sternexplosion` |
| 11 | `goldene-sonnenstrahlen` |
| 12 | `lila-herzen-glitzer` |
| 13 | `smaragd-neonsterne` |
| 14 | `nacht-hologramm-prismen` |
| 15 | `blau-weisse-optikwellen` |
| 16 | `smaragd-lichtstrahl` |
| 17 | `eisblau-frostspritzer` |
| 18 | `violett-kristallsplitter` |
| 19 | `rosa-liquid-wellen` |
| 20 | `rot-schwarzer-sternnebel` |

Die Farbvarianten lassen sich reproduzierbar mit `python scripts/generate_background_variants.py` erzeugen.

Der GFXRhino-PFP-Background-Pack ist kostenlos auf der [Produktseite](https://gfxrhino.com/products/pfp-background-pack/) verfügbar. Lizenzhinweise: https://gfxrhino.com/license
