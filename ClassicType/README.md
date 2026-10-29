# ClassicType (R15)

Dieser GFX-Typ rendert den Roblox-Avatar als **R15** in einer komplett eigenen
Blender-Szene (`scripts/classic_render.py`). Es wird **keine** `WomanRig.blend`
geöffnet – die R6-Blend-Datei gehört ausschließlich zu `HearthandsType`.

## Körper

Als Körper dient der offizielle Standard-R15-Blocky-Rig von Roblox
(`Roblox/avatar` → `Legacy/rigs/R15_Block.fbx`). Die 15 Meshes, ihre UVs und
die originalen Attachment-Positionen liegen aufbereitet in
`ClassicType/assets/r15_blocky.json.gz`.

Fehlende Körperteile im Inventar werden einzeln durch diesen Blocky-Rig
ersetzt (inklusive Classic-Shirt/-Hose/-T-Shirt und Face-Decal, die über
`scripts/r15_clothing.py` als Texturen mit neu berechneten UVs aufgebracht
werden).

Der Blocky-Rig ist dabei **glänzend** wie ein Roblox-Avatar: Seine Polygone
werden mit `use_smooth = True` erzeugt (flat shading hätte ihn matt und
plastisch wirken lassen), das Material nutzt dieselben Glanz-Parameter wie
der HearthandsType (Rauheit 0.13, Specular 0.55, Klarlack 0.32/0.06).

## Größen-Werte des Avatars (Body-Scales)

Roblox skaliert die Körperteile eines Avatars über vier Werte, die die
öffentliche Avatar-API im Feld `scales` liefert (1.0 = Standard):

* `width`, `height`, `depth` – `BodyWidthScale`, `BodyHeightScale` und
  `BodyDepthScale`. Sie multiplizieren **jede** Dimension des jeweiligen
  Körperteils (ein breiterer Avatar wird in X *und* Z breiter).
* `head` – `HeadScale`. Der Kopf wird **uniform** skaliert, nicht pro Achse.

Das ist Roblox' dokumentiertes Verhalten („R15 Character Scaling“, devforum
30642) und die Rthro-Scaling-Spezifikation (devforum 199611):

```lua
if bodyPart.Name == "Head" then
    return scaleBodyType * headScale
else
    return scaleBodyType * Vector3.new(bodyWidth, bodyHeight, bodyDepth)
end
```

Der Renderer setzt das so um (`BodyScale` in `scripts/classic_render.py`):

* Die Vertices der Körperteile werden pro Achse skaliert, der Kopf uniform
  um seinen Mittelpunkt.
* Die Positionen der Teile (ihre `CFrame`s im Rig-Space) und die Gelenke der
  Pose werden mit denselben Werten skaliert, damit die Pose auch bei einem
  skalierten Avatar an den richtigen Stellen sitzt.
* Accessoires erben die Skalierung ihres Körperteils; Accessoires am Kopf
  (Hut, Haar, Gesicht) werden wie der Kopf uniform skaliert.
* Der Abstand zum Boden wächst mit der Höhe (`2 * height`).

## Korblox & Headless

Beide Optionen kennt der ClassicType jetzt genauso wie der HearthandsType
(`korblox`, `headless` in `ClassicType/configs.toml`):

* **`korblox = true`** – das rechte Oberbein wird durch das **Korblox
  Deathspeaker Right Leg** (Asset 139607718) ersetzt. Das Asset liefert zwar
  die Segmente Oberbein, Unterbein und Fuß, aber nur das Oberbein ist ein
  eigenes Stück: Unterbein und Fuß sind die mechanisch aus dem klassischen
  R6-Bein abgeleiteten Kopien (der Fuß ist ein schwarzer Klotz). Deshalb wird
  – wie beim HearthandsType, wo das Korblox-Bein ebenfalls nur ein Stück ist –
  ausschließlich das Oberbein übernommen; Unterbein und Fuß bleiben beim
  Avatar.
* **`headless = true`** – der Kopf wird ausgeblendet und die
  Gesicht-Accessoires (Asset-Typ 42, z. B. Brillen oder Masken) ebenfalls.
  Haare, Hüte und Hals-Accessoires bleiben sichtbar.

Dasselbe gilt für ein **ausgerüstetes** klassisches Bein-Paket: Erkennt der
Loader ein aus R6 konvertiertes Bein (die Teile tragen `AvatarPartScaleType`
und ihre Rig-Attachments liegen in R6-Koordinaten), übernimmt er nur das
Oberbein. Echte R15-Pakete (z. B. „Headless Horseman Left Leg“, 134082507)
behalten alle drei Segmente.

## Körperteile aus dem Inventar (Asset-Typen 17, 27–31)

Inventory-Körperteile (z. B. „Roblox 2.0 Torso“) werden als echte Meshes mit
ihren eigenen UVs/Texturen gerendert. dabei gelten die Roblox-Regeln:

* **Quelle der Teile** – Ein Körperteil-Asset enthält mehrere Varianten
  desselben Körpers. Verwendet wird der Ordner `R15Fixed` (der kanonische
  Roblox-Rig-Stand), ersatzweise `R15ArtistIntent` (die vom Künstler gewählte
  Pose). `R6` (klassische Variante) und `R15` (Studio-Vorschau, dort liegen
  die Teile 2 Studs zu hoch) werden nie verwendet.
* **Platzierung** – Die Position kommt aus dem im Asset gespeicherten
  `CFrame` des Segments (relativ zur `HumanoidRootPart` des Assets).
  Zusätzlich wird das Asset über seine **Rig-Attachments** am Standard-Rig
  ausgerichtet: Roblox hängt ein Körperteil-Asset über die
  `R15FixedRigAttachmentMap` ein (Roblox-Client-Plugin `AssetImporter`,
  `Utility/RigSetup/FixedRigPositions.luac.s`). Weichen die
  Rig-Attachments eines Assets von dieser Map ab – wie bei vielen aus R6
  konvertierten Paketen, die 1–2 Studs zu tief sitzen –, wird der
  Versatz als Median über alle passenden Attachments berechnet und auf alle
  Segmente des Assets angewendet. Die CFrames werden als Roblox-Koordinaten
  interpretiert und in Blender-Koordinaten umgerechnet
  (`x_bl, y_bl, z_bl = -x_rbx, z_rbx, y_rbx`, Root → `z = +2`).
* **Textur** – Die Textur kommt aus der `SurfaceAppearance` (`ColorMap`) des
  Segments, sonst vom `TextureID` des MeshParts. Klassische Pakete wie
  „Roblox 2.0“ bringen gar keine eigene Textur mit (am MeshPart ist
  `TextureID` leer, am `CharacterMesh` existiert nur `BodyPart`). Im
  Roblox-Client zeigen diese Meshes die klassische Körpertextur, die aus
  `BodyColors` und Classic-Kleidung zusammengesetzt wird. Der Renderer
  malt diese klassische Körpertextur (`compose_r15_part_textures`) direkt
  auf das Mesh. Ohne das bliebe nur die flache `BodyColor` und die Teile
  wirkten braun.
  * **Warum nicht die UVs des Pakets?** Die UVs eines klassischen Pakets
    liegen *nicht* im Raum des Classic-Templates. Gemessen am
    „Roblox 2.0“-Paket (`27112025`): Das Front-Face des `LowerTorso`
    belegt im Template den Bereich x 5–196, y 415–546 – also den
    Limb-Bereich, nicht die Torso-Vorderansicht; das Front-Face des
    `UpperTorso` belegt nur ein 90 × 56 px großes Teilstück der
    Torso-Vorderansicht. Ein Abtasten des Templates mit diesen UVs würde
    die Shirt-Grafik völlig zerrissen auf die Teile legen. Roblox legt die
    Classic-Kleidung deshalb **je Fläche** auf: Jede Fläche des Meshs wird
    über ihre Normale als Front/Back/Left/Right/Top/Bottom klassifiziert
    und bekommt das passende Tile des Atlas (`compute_part_uvs`,
    `_remap_part_uvs`). Belege: In Roblox' eigenem R15-Screenshot des
    „Roblox 2.0“-Torsos (devforum 174204) steht das *vollständige*
    Vorderpanel mittig auf der Brust – bei einer Projektion über die
    Asset-UVs wäre nur ein Teilstück davon zu sehen. Genau diese
    Flächen-Projektion macht die Grafik auch auf gerundeten
    Paket-Geometrien „gestreckt“ – das entspricht dem Verhalten, das
    Roblox selbst zeigt (devforum 4721459: „R15 stretches classic shirt
    texture more than R6“).
  * **Template-Aufteilung** – Die Rechtecke stammen aus dem offiziellen
    Classic-Template (585 × 559, `Template-Shirts.png`): Torso
    Front/Back 128 × 128, Seiten 64 × 128, UP/DOWN 128 × 64, Gliedmaßen
    64 × 128 bzw. 64 × 64-Kappen. Die R15-Teilung des Torsos bei
    96/32 px ist die gepunktete Linie im offiziellen Template (im
    R15-Template `Template-Shirts-R15.png` an derselben Stelle zu messen);
    sie passt zum Verhältnis des R15-Blocky-Rigs (`UpperTorso` 1.6 Studs
    hoch, `LowerTorso` 0.4). Die Gliedmaßen (64/48/16 px) sind eine
    Näherung an die Rig-Proportionen des R15-Arms (Oberarm 0.918,
    Unterarm 0.760, Hand 0.275 Studs ≈ 60/50/18 px); das R15-Template
    unterteilt den Arm in mehrere Biege-Bänder, die Roblox für die
    klassischen R6-Pakete „in einem Stück“ auf die R15-Segmente legt.

* **Kleidung** – Classic-Kleidung gehört nur zum klassischen Körper
  (Blocky-Rig). Ein ausgerüstetes Körperteil-Paket bringt seine eigene
  Textur mit und wird deshalb nicht zusätzlich mit der Kleidung
  projiziert.
* **Farbe** – Die `BodyColors` des Avatars sind die `MeshPart.Color` des
  Segments. Das ist die dokumentierte Roblox-Verhaltensweise für
  „custom skin tones“: Die Alpha-Maske der Textur blendet über die
  Körperfarbe.
* **Alpha** – Es gilt der `AlphaMode` der `SurfaceAppearance`
  (`Enum.AlphaMode`: `Overlay` = 0, `Transparency` = 1, `TintMask` = 2,
  `Opaque` = 3):
  * `Overlay` (Standard): Die ColorMap wird über die `MeshPart.Color`
    gelegt; transparente Texel zeigen die Körperfarbe – niemals Schwarz.
  * `Transparency`: Der Alpha-Kanal schneidet echte Löcher in das Mesh.
  * `Opaque`: Der Alpha-Kanal wird ignoriert.
  * `TintMask`: Die getönte ColorMap wird über die ungetönte gemischt.

  Klassische Pakete ohne `SurfaceAppearance` nutzen `Transparency`: Ihre
  Textur ist eine echte Alpha-Textur, deren transparente Stellen in
  Roblox ebenfalls durchsichtig sind. `SurfaceAppearance.Color` ist ein
  Multiplikator auf die ColorMap und wird linear auf die Texturfarbe
  angewendet.


## Emote & Pose

`pose = "Emote"` (Emote über `emote_asset_id`), `pose = "Animation"`
(Animation über `animation_asset_id`) und `pose = "Equipped"` (die am
Spieleravatar ausgerüstete Pose) setzen den Avatar in die Pose der
geladenen Animation. `scripts/rbx_animation.py` liest die Animation,
`scripts/classic_render.py` rechnet sie auf das R15-Rig der Classic-Szene um.

| `pose` | Quelle | ID aus der Konfiguration |
| --- | --- | --- |
| `"None"` | normales Stehen | – |
| `"Equipped"` | vom Spieler ausgerüstete Profilpose (Slot 1) | – |
| `"Emote"` | Emote (Asset-Typ 61 „EmoteAnimation“) | `emote_asset_id` |
| `"Animation"` | Animation (Asset-Typ 53 „Animation“, z. B. „Ninja Run“, 658830056) | `animation_asset_id` |

`"Animation"` und `"Emote"` laufen über denselben Loader (`load_animation()`):
Beide Asset-Arten enthalten eine Keyframe-Sequenz, und der eingefrorene
Moment ist – wie in der Roblox-Vorschau – die **erste Keyframe**.

### Die beiden Animationsformate von Roblox

**`KeyframeSequence`** (klassisch, z. B. „Emote_Hello“): Jeder `Keyframe`
enthält einen verschachtelten `Pose`-Baum (`HumanoidRootPart` → `LowerTorso`
→ `UpperTorso` → `Head`/`LeftUpperArm` → …). Die `CFrame` einer Pose ist die
Transformation des Gelenks **relativ zum Elterngelenk** (lokal) – dieselbe
Konvention, die Roblox' eigener CurveAnimation→KeyframeSequence-Konverter
verwendet.

**`CurveAnimation`** (neu, UGC-Emotes wie „Imported Animation Clip“): Je Bone
ein `Folder` mit einer `Vector3Curve "Position"` und einer
`EulerRotationCurve "Rotation"` (mit `FloatCurve`-Kindern je Achse). Der
Wert steckt als Binärblob in `ValuesAndTimes`:

| Offset | Inhalt |
| --- | --- |
| `u32` | Version (2) |
| `u32` | Anzahl der Keys |
| je Key (14 Byte) | `u8` Interpolationsart (0 konstant, 1 linear, 2 cubic), `u8` reserviert, `f32` Wert, `f32` linke Tangente, `f32` rechte Tangente (Wert pro Sekunde) |
| `u32` | Zeitformat (1) |
| `u32` | Anzahl der Zeitpunkte |
| je Zeitpunkt (4 Byte) | `u32` Tick – **2400 Ticks = 1 Sekunde** |

Verifiziert an zwei echten Assets: Die Keyframes der „Emote_Hello“-Animation
liegen 0,042 s (= 1/24 s) auseinander, die der CurveAnimation in Vielfachen
von 80 Ticks (= 1/30 s bei 2400 Ticks/s); mit 14400 Ticks/s ergäbe sich eine
unplausible Gesamtdauer von 0,82 s für den 4,9-Sekunden-Tanz.

Die Euler-Winkel werden in der Reihenfolge `Enum.RotationOrder` multipliziert,
für den von Roblox gelieferten Standard `XYZ` (Order 0) also `Rx @ Ry @ Rz`
– bestätigt an zwei unabhängigen Readern des Formats, die dieselbe Matrix
liefern.

### Anwenden der Pose

Roblox bewegt die Segmente über Motor6D-Joints: Jedes Segment hängt an genau
einem Gelenk und dreht sich genau darum. Der Renderer rechnet dieselbe Kette
nach:

* Drehpunkt jedes Segments = die zugehörige Position der offiziellen
  `R15FixedRigAttachmentMap` (`Root`/`Waist`/`Neck`/`Shoulder`/`Elbow`/`Wrist`/
  `Hip`/`Knee`/`Ankle`), also genau die Punkte, an denen Roblox ein
  Körperteil-Asset einhängt.
* Die Rotationen werden von der Wurzel aus über die Bone-Hierarchie
  zusammengesetzt; Kind-Segmente wandern mit der Rotation des Elternsegments
  mit (Vorwärtsrechnung über die Gelenkpositionen).
* Accessoires hängen an dem Segment, an dem ihr Attachment im Standard-Rig
  sitzt, und folgen damit der Pose.

R6-Begriffe alter Emotes (`Left Arm`, `Torso`, …) werden auf die
R15-Segmente umgebildet und dabei um dieselben Gelenke gedreht.

### Vorschau-Moment

Der eingefrorene Moment ist die **erste Keyframe** der Animation. Roblox
erzeugt das Vorsau-Bild einer UGC-Emote ebenfalls aus der ersten Keyframe
(Roblox-Forum: „UGC emotes use the first keyframe for the thumbnail“); das
System rät den Moment also nicht.

### Ausgerüstete Pose (`pose = "Equipped"`)

Die öffentliche Avatar-API (`avatar.roblox.com/v2/avatar/users/{id}/avatar`)
liefert im Feld `emotes` die ausgerüsteten Emotes mit ihrer Slot-Nummer.
Slot 1 ist die Emote, die Roblox auch als Profilpose verwendet. Der ältere
Endpunkt `/v1/avatar/thumbnail-customizations` liefert die Profilpose
direkt, verlangt aber ein `.ROBLOSECURITY`-Cookie des eigenen Accounts und
antwortet deshalb immer mit HTTP 401 – er ist ohne Login nicht nutzbar.
