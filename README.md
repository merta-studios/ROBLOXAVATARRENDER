# Roblox R6 Avatar GFX Renderer (`WomanRig.blend` + Blender Cycles)

Vollautomatischer GitHub-Actions-Renderer für Roblox **R6-Avatare** auf Basis von `WomanRig.blend` und der **Roblox Open Cloud API**.

## Funktionsweise

1. **`configs.json` bearbeiten**: Benutzername, Hintergrund, Material und Avatar-Umrandung werden zentral in [`configs.json`](./configs.json) eingestellt.
   - **Hintergrund**: `transparent`, ein Hex-Farbwert (z. B. `#2a5bff`), eine direkte Bild-URL oder der Name einer Bilddatei in [`backgrounds/`](./backgrounds/). Das Bild wird als Image Texture auf das Mesh-Plane `Background` gelegt.
   - **Material**: Nur `Glossy` (bisheriger Glanz) und `Matt` (hohe Roughness, kein Klarlack).
   - **Umrandung**: Dicke in Prozent der Bildbreite, Position innen/außen, Transparenz-Fade ab einem prozentualen Dickenanteil und Farbe als Hex, `white` oder `black`. Bei 2000 px Bildbreite entspricht 1 % genau 20 px; `0` deaktiviert sie.
   - **Kopf & Gesicht**: Dynamic-/Custom-Heads behalten ihre eigene Mesh-UV-Map und Kopftextur; bei transparenten Texturbereichen wird die Head-BodyColor ergänzt. Nur der klassische R6-Kopf erhält das klassische Face-Decal. Brillen und andere Face-Accessoires bleiben echte Meshes und ersetzen niemals das Gesicht. Dynamische Gesichtsausdrücke werden im statischen Render nicht animiert.
2. **Automatischer R6-Render (`.github/workflows/render-avatar.yml`)**:
   - Lädt über die **Roblox Open Cloud API** (`ROBLOX_API_KEY`) die Körperfarben, klassische Kleidung (`Shirt`, `Pants`, `T-Shirt`), das Gesicht (`Face`), den Kopf (`Head`) und alle starren R6-Accessoires (`Hat`, `Hair`, `Face`, `Neck`, `Shoulder`, `Front`, `Back`, `Waist`).
   - Öffnet [`WomanRig.blend`](./WomanRig.blend) in **Blender 5.0 (`bpy`)**, wendet den hochauflösenden R6-Texturatlas auf alle fertig geposten Körperteile an und platziert Kopf sowie Accessoires mit exakt denselben Principled-BSDF-Materialeinstellungen auf dem Rig.
   - Engine, feste orthografische Kamera, Studio-Licht und Auflösung sind in `WomanRig.blend` gespeichert. Die externe Environment Texture/HDRI wird bewusst deaktiviert; stattdessen nutzt die Pipeline eine stabile Studio-Verlaufsumgebung, damit fehlende EXR-Dateien kein magenta/rosa Render verursachen. Die Bild-Plane `Background` wird nur für den gewählten Hintergrund texturiert.
   - Rendert mit **Blender Cycles** als **RGBA**. Bei `background: "transparent"` bleibt der Bildhintergrund transparent.
   - Zeigt in der **Workflow-Übersicht (`Summary`) ausschließlich eine Sache**: entweder das fertig gerenderte Bild oder eine präzise Fehlermeldung.
3. **Auto Merge -> main (`.github/workflows/auto-pr-merge.yml`)**:
   - Jeder Push auf einen Arbeitsbranch wird automatisch direkt per `git merge --no-ff` nach `main` gemerged und gepusht. Es werden **keine Pull Requests** erstellt (siehe [`AGENTS.md`](./AGENTS.md)).
4. **Automatische Bereinigung (`.github/workflows/cleanup.yml`)**:
   - Räumt alte Branches, alte Versionen/Releases, alte Tags, alte Workflow-Runs, Artefakte und Caches automatisch auf und behält immer nur den jeweils neuesten Stand.

## Feste Szene in WomanRig.blend

Das konstante Studio-Setup wird einmalig in der Blender-Datei gespeichert und nicht bei jedem Render per Python neu aufgebaut. Nach Änderungen an Kamera, Beleuchtung oder Render-Voreinstellungen kann es mit Blender 5.x erneut gespeichert werden:

```bash
python scripts/configure_blend_scene.py --blend-file WomanRig.blend
```

Die Avatar-Pipeline validiert danach nur noch, dass eine Kamera vorhanden ist und Cycles als Engine gespeichert wurde.

## Einmalige Einrichtung

1. **Roblox Open Cloud API Key erstellen** ([Creator Hub -> Credentials](https://create.roblox.com/dashboard/credentials)):
   - **Access Permissions**:
     - `legacy-asset:manage` *(Pflicht für die Open Cloud Asset Delivery API `/asset-delivery-api/v1/assetId/{id}`)*
     - `asset:read` *(Empfohlen für Assets v1)*
     - `user.advanced:read` *(Optional für erweiterte Open-Cloud-v2-Nutzerabfragen)*
   - **Accepted IP Addresses**: `0.0.0.0/0` *(damit wechselnde GitHub-Actions-Runner zugreifen dürfen)*
2. **GitHub Secret hinterlegen**:
   - Im Repository unter **Settings -> Secrets and variables -> Actions -> New repository secret**
   - Name: `ROBLOX_API_KEY`
   - Secret: Den kopierten Roblox Open Cloud API Key einfügen.
3. **GitHub Actions Workflow-Berechtigungen prüfen**:
   - Unter **Settings -> Actions -> General -> Workflow permissions**:
   - **Read and write permissions** aktivieren
