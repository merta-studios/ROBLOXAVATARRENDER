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

* **Platzierung** – Die Position kommt aus dem im Asset gespeicherten
  `CFrame` des Segments (relativ zur `HumanoidRootPart` des Assets). Roblox
  exportiert jedes Körperteil-Asset so, dass dessen Wurzel auf dem
  Avatar-Wurzelpunkt liegt (`MakeExportVersion` im Roblox-Client-Plugin
  `AvatarImporter`). Die CFrames werden deshalb direkt als Roblox-Koordinaten
  interpretiert und in Blender-Koordinaten umgerechnet
  (`x_bl, y_bl, z_bl = -x_rbx, z_rbx, y_rbx`, Root → `z = +2`).
* **Farbe** – Die `BodyColors` des Avatars sind die `MeshPart.Color` des
  Segments. Das ist die dokumentierte Roblox-Verhaltensweise für
  „custom skin tones“: Die Alpha-Maske der Textur blendet über die
  Körperfarbe.
* **Alpha** – Es gilt der `AlphaMode` der `SurfaceAppearance`:
  * `Overlay` (Standard): Die ColorMap wird über die `MeshPart.Color`
    gelegt; transparente Texel zeigen die Körperfarbe – niemals Schwarz.
  * `Transparency`: Der Alpha-Kanal schneidet echte Löcher in das Mesh.
  * `Opaque`: Der Alpha-Kanal wird ignoriert.
  * `TintMask`: Die getönte ColorMap wird über die ungetönte gemischt.
