# Roblox Avatar GFX Renderer (`WomanRig.blend` + Blender Cycles)

Vollautomatischer GitHub-Actions-Renderer für Roblox-Avatare auf Basis von `WomanRig.blend` und der **Roblox Open Cloud API**. Es gibt zwei Render-Typen (GFX-Typen), jeder mit eigener Konfigurationsdatei:

- **`HearthandsType/configs.toml`** – "Preppy Hearthands Type": das klassische R6-Render aus `WomanRig.blend` mit optionalem Korblox-Bein und Headless-Look.
- **`ClassicType/configs.toml`** – der Avatar wird als R15 komplett über die Roblox Open Cloud API geladen (Layered Clothing und Equipment-Accessoires werden ausgeschlossen). Fehlende Körperteile ersetzt der offizielle Standard-R15-Blocky-Rig; dazu kommen wählbare Posen, Drehung und Abstand von oben/unten.

Die gemeinsamen Hintergründe bleiben im geteilten Ordner [`backgrounds/`](./backgrounds/) und gehören nicht zu einem einzelnen GFX-Typ. Neben den bestehenden Farbordnern gibt es jetzt auch [`backgrounds/weiss/`](./backgrounds/weiss/) und [`backgrounds/schwarz/`](./backgrounds/schwarz/) mit je drei ruhigen, monochromen Avatar-Hintergründen.

## Funktionsweise

1. **Konfiguration bearbeiten**: Benutzername, Hintergrund, Material und Avatar-Umrandung werden zentral in der Konfigurationsdatei des gewählten GFX-Typs eingestellt. Die Beschreibungen und gültigen Werte stehen dort direkt als Kommentar über der Einstellung.
   - **Hintergrund**: `transparent`, ein Hex-Farbwert (z. B. `#2a5bff`), eine direkte Bild-URL oder einen lokalen Motivnamen aus [`backgrounds/`](./backgrounds/); ein nackter Name wählt die Originalfarbe, `Farbordner/Motivname` wie `lila/herzen-konfetti`, `weiss/softes-licht` oder `schwarz/neon-linien` eine konkrete Variante.
   - **Material**: `Glossy` (bisheriger Glanz) oder `Matt` (hohe Roughness, kein Klarlack).
   - **Umrandung**: Dicke in Prozent der Bildbreite, Position innen/außen, Transparenz-Fade ab einem prozentualen Dickenanteil und Farbe als Hex, `white` oder `black`. Bei 2000 px Bildbreite entspricht 1 % genau 20 px; `0` deaktiviert sie. Geht der Avatar über den Bildrand hinaus, wird die Silhouette für die Distanzberechnung über den Rand hinaus fortgesetzt – die Outline klebt dann nicht als Linie am Canvas-Rand, sondern wird mit abgeschnitten.
2. **Automatischer Render (`.github/workflows/render-avatar.yml`)**:
   - Lädt über die **Roblox Open Cloud API** (`ROBLOX_API_KEY`) die Avatar-Daten des Nutzers. Nur `HearthandsType` öffnet `WomanRig.blend` in **Blender 5.0 (`bpy`)**; `ClassicType` baut eine eigene leere Szene aus dem offiziellen Standard-R15-Blocky-Rig.
   - Rendert mit **Blender Cycles** als **RGBA**. Bei `background: "transparent"` bleibt der Bildhintergrund transparent.
   - Zeigt in der **Workflow-Übersicht (`Summary`) ausschließlich eine Sache**: entweder das fertig gerenderte Bild oder eine präzise Fehlermeldung.

## GFX-Typ "HearthandsType" (Preppy Hearthands Type)

Das ist der bisherige, klassische R6-Render auf Basis von `WomanRig.blend`. Zusätzliche Optionen in [`HearthandsType/configs.toml`](./HearthandsType/configs.toml):

- **`korblox`** (`true`/`false`): Wenn `true`, wird das rechte Bein des R6-Rigs durch das **Korblox Deathspeaker Right Leg** ersetzt (offizielles Roblox-Asset, [Katalog-ID 139607718](https://www.roblox.com/de/catalog/139607718/Korblox-Deathspeaker-Right-Leg), früher Teil des 17.000 Robux teuren Korblox-Deathspeaker-Bundles). Das Bein wird über die Open Cloud Asset Delivery API geladen und mit den eigenen Materialien/Texturen des Assets gerendert – der Look ist damit auch für Avatare rendernbar, die sich das Bundle nicht leisten können.
- **`headless`** (`true`/`false`): Wenn `true`, wird der Kopf unsichtbar gemacht und auch die Gesicht-Accessoires (z. B. Brillen oder Masken) ausgeblendet. Haare, Hüte und Hals-Accessoires bleiben sichtbar (Headless-Horseman-Look).

Kopf & Gesicht: Dynamic-/Custom-Heads behalten ihre eigene Mesh-UV-Map und Kopftextur; bei transparenten Texturbereichen wird die Head-BodyColor ergänzt. Nur der klassische R6-Kopf erhält das klassische Face-Decal. Brillen und andere Face-Accessoires bleiben echte Meshes und ersetzen niemals das Gesicht.

## GFX-Typ "ClassicType"

Der zweite Ordner [`ClassicType/`](./ClassicType/) enthält eine eigene [`configs.toml`](./ClassicType/configs.toml) mit `username`, `outline`, `background`, `material` und weiteren Einstellungen. Der Avatar wird anders geladen: als **R15 komplett über die Roblox Avatar/Open-Cloud-API** – ohne Layered Clothing (Asset-Typen 64 bis 72) und ohne Accessoires vom Typ "Equipment". Der öffentliche v4-Endpunkt benötigt ein `.ROBLOSECURITY`-Cookie; deshalb nutzt der Client den funktionierenden öffentlichen v2-Endpunkt (`fetch_avatar_metadata_v4()` kapselt das).

**Strikte Trennung:** `ClassicType` öffnet **`WomanRig.blend` nicht**. `scripts/classic_render.py` baut eine eigene leere Blender-Szene mit Kamera, Licht und Ortho-Projektion. `scripts/blender_render.py` gehört ausschließlich zu `HearthandsType` und erhält keine ClassicType-Parameter. Als Körper dient der **offizielle Standard-R15-Blocky-Rig von Roblox**: Die Originaldatei [`ClassicType/assets/R15_Block.fbx`](./ClassicType/assets/R15_Block.fbx) stammt aus dem offiziellen [`Roblox/avatar`](https://github.com/Roblox/avatar/blob/main/Legacy/rigs/R15_Block.fbx)-Repo und wurde einmalig mit `python3 -m scripts.r15_blocky_rig` in das kompakte, `bpy`-freie Asset [`ClassicType/assets/r15_blocky.json.gz`](./ClassicType/assets/r15_blocky.json.gz) überführt (15 Meshes inkl. UVs und originalen Attachment-Positionen). Fehlende Körperteil-Assets im Inventar werden damit nicht mehr durch einfache Quader, sondern durch genau diese Standard-R15-Teile ersetzt. `scripts/r15_clothing.py` erzeugt aus Classic-Shirt, -Hose, -T‑Shirt und Face-Decal für jedes der 15 Teile eine eigene 3×2-Textur und berechnet die UVs aus den Mesh-Normalen neu. Das Shirt-Template liegt dabei korrekt auf **Ober- und Untertorso** (96/32-Split), die Hose darunter zusätzlich auf dem Untertorso; die sechs BodyColors bleiben als Grundfarbe sichtbar, wo Kleidung transparent ist. Geladene Accessoires sind echte Meshes mit UVs/Texturen. Emote-Keyframes werden mangels Armatur über Gruppen-Empties (jeweils die drei zusammengehörigen R15-Teile) näherungsweise angewendet; Accessoires bewegen sich dabei mit ihrer Gruppe mit. Bei `pose = "None"` wird kein Emote geladen und keine Pose angewendet. Hintergrund und Umrandung werden nach einem transparenten Render als gemeinsame PNG-Nachbearbeitung in `scripts/image_compositing.py` gesetzt.

Weitere ClassicType-Einstellungen:

- **`pose`**: `"None"` (normales Stehen), `"Equipped"` (die am Spieleravatar ausgerüstete Pose, die auch auf seinem Profil angewendet wird) oder `"Emote"` (Pose aus einem Emote nach `emote_asset_id`).
- **`emote_asset_id`**: Asset-ID des Emotes, nur relevant bei `pose = "Emote"`. Das System friert den Moment ein, der auch in der Emote-Vorschau zu sehen ist (erste Keyframe der per Open Cloud API geladenen Animation), anstatt zu raten.
- **`rotation_deg`**: Drehung in Grad um die vertikale Achse: `0` = schaut zur Mitte, `45` = schräg nach rechts, `-45` = schräg nach links.
- **`abstand_oben` / `abstand_unten`**: Abstand vom oberen bzw. unteren Bildrand in Prozent der Avatar-Höhe (Länge zwischen höchstem und tiefstem Punkt des 3D-Modells). `100` = eine komplette Avatar-Höhe Platz vor dem Avatar, `10` = 10 Prozent davon, `-10` = der Avatar rutscht bereits zu 10% in den Rand hinein.

Das kompakte R15-Asset lässt sich bei Bedarf aus der mitgelieferten Original-FBX neu erzeugen: `python3 -m scripts.r15_blocky_rig`. Der Render selbst liest nur `r15_blocky.json.gz` und benötigt weder die FBX-Datei noch `bpy` zum Laden der Geometrie. Individuell ausgerüstete Körperteil-Assets aus dem Inventar werden aktuell nicht als eigene Meshes geladen; sichtbar ist in diesem Fall weiterhin der offizielle Standard-R15-Blocky-Rig mit den Classic-Kleidungstexturen und den BodyColors des Avatars.

Umrandungs-Einstellungen (`[outline]`) funktionieren wie bei HearthandsType.

## Lokale Prüfung

```bash
python3 -m py_compile scripts/*.py tests/*.py
python3 -m unittest discover -s tests -v
```

Ohne installiertes `bpy` laufen genau die 26 reinen Python-Tests; die Blender-Regressionstests werden nur in der CI gesammelt. Die Render-Pfade selbst benötigen `bpy==5.0.1` und das GitHub-Secret `ROBLOX_API_KEY`.

## Auto Merge & Bereinigung

Wie bisher: Jeder Push auf einen Arbeitsbranch wird automatisch direkt nach `main` gemerged (keine Pull Requests, siehe [`AGENTS.md`](./AGENTS.md)). Änderungen an `HearthandsType/`, `ClassicType/`, `backgrounds/`, `WomanRig.blend`, `scripts/`, `tests/` oder `requirements.txt` starten auf `main` den Render-Workflow. Wurde `ClassicType/` geändert, übergibt der Auto-Merge-Workflow explizit `gfx-type=ClassicType`; sonst rendert er `HearthandsType`.

## Einmalige Einrichtung

1. **Roblox Open Cloud API Key erstellen** ([Creator Hub -> Credentials](https://create.roblox.com/dashboard/credentials)):
   - **Access Permissions**:
     - `legacy-asset:manage` *(Pflicht für die Open Cloud Asset Delivery API `/asset-delivery-api/v1/assetId/{id}` – auch für das Korblox-Bein und Emote-Animationen)*
     - `asset:read` *(Empfohlen für Assets v1)*
     - `user.advanced:read` *(Optional für erweiterte Open-Cloud-v2-Nutzerabfragen)*
   - **Accepted IP Addresses**: `0.0.0.0/0` *(damit wechselnde GitHub-Actions-Runner zugreifen dürfen)*
2. **GitHub Secret hinterlegen**: Name `ROBLOX_API_KEY` unter **Settings -> Secrets and variables -> Actions -> New repository secret**.
3. **GitHub Actions Workflow-Berechtigungen prüfen**: Unter **Settings -> Actions -> General -> Workflow permissions**: **Read and write permissions** aktivieren.
