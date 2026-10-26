# Roblox R6 Avatar GFX Renderer (`WomanRig.blend` + Blender EEVEE)

Vollautomatischer GitHub-Actions-Renderer für Roblox **R6-Avatare** auf Basis von `WomanRig.blend` und der **Roblox Open Cloud API**.

## Funktionsweise

1. **`username.txt` bearbeiten**: Trage einen beliebigen Roblox-Benutzernamen in [`username.txt`](./username.txt) ein und speichere/committe die Änderung.
2. **Automatischer R6-Render (`.github/workflows/render-avatar.yml`)**:
   - Lädt über die **Roblox Open Cloud API** (`ROBLOX_API_KEY`) die Körperfarben, klassische Kleidung (`Shirt`, `Pants`, `T-Shirt`), das Gesicht (`Face`), den Kopf (`Head`) und alle starren R6-Accessoires (`Hat`, `Hair`, `Face`, `Neck`, `Shoulder`, `Front`, `Back`, `Waist`).
   - Öffnet [`WomanRig.blend`](./WomanRig.blend) in **Blender 5.0 (`bpy`)**, wendet den hochauflösenden R6-Texturatlas auf alle fertig geposten Körperteile an und platziert Kopf sowie Accessoires mit exakt denselben Principled-BSDF-Materialeinstellungen auf dem Rig.
   - Rendert mit **Blender EEVEE** bei **transparentem Hintergrund (`RGBA`)**.
   - Zeigt in der **Workflow-Übersicht (`Summary`) ausschließlich eine Sache**: entweder das fertig gerenderte Bild oder eine präzise Fehlermeldung.
3. **Auto Merge -> main (`.github/workflows/auto-pr-merge.yml`)**:
   - Jeder Push auf einen Arbeitsbranch wird automatisch direkt per `git merge --no-ff` nach `main` gemerged und gepusht. Es werden **keine Pull Requests** erstellt (siehe [`AGENTS.md`](./AGENTS.md)).
4. **Automatische Bereinigung (`.github/workflows/cleanup.yml`)**:
   - Räumt alte Branches, alte Versionen/Releases, alte Tags, alte Workflow-Runs, Artefakte und Caches automatisch auf und behält immer nur den jeweils neuesten Stand.

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
