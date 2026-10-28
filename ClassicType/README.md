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
  „Roblox 2.0“ bringen gar keine eigene Textur mit: Ihre Meshes sind auf
  die klassische R6-Körpertextur gemappt, die der Roblox-Client aus
  `BodyColors` und Classic-Kleidung zusammensetzt. Solche Teile nutzen
  deshalb den zusammengesetzten klassischen Körperatlas
  (`compose_r6_body_texture`). Ohne ihn bliebe nur die flache `BodyColor`
  und die Teile wirkten braun.
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

