# Agent-Workflow-Regeln (Verbindlich)

Diese Regeln gelten für jede KI-Agent-Session in diesem Repository (`merta-studios/ROBLOXAVATARRENDER`):

## 1. Immer direkt und ohne Nachfrage auf `main` bringen
- Wenn du Änderungen am Repository vornimmst, **committest, pusht und mergest du sie sofort auf `main` — ohne vorher um Erlaubnis zu fragen**.
- Frage den Nutzer niemals „Soll ich committen/pushen/einen PR erstellen?“, sondern führe den kompletten Weg bis auf `main` direkt selbstständig aus.

## 2. Direkt-Merge nach `main` ohne Pull Requests
- Arena bindet jede Session fest an ihren eigenen Branch (`arena/<session-id>`). Wechsle niemals den Branch und lösche den aktuellen Session-Branch während der laufenden Session nicht.
- **Es werden keine Pull Requests erstellt oder gemerged** (`gh pr create` / `gh pr merge` sind verboten). Der Nutzer möchte keine PRs.
- So bringst du Änderungen auf `main`:
  1. Vor neuen Änderungen aktuellen Stand von `origin/main` in den Arbeitsbranch holen und mergen (`git fetch origin main && git merge --no-edit origin/main`).
  2. Änderungen committen und auf den Session-Branch pushen:
     ```bash
     git add -A
     git commit -m "<präzise Commit-Nachricht>"
     git push origin <aktueller-arena-branch>
     ```
  3. Der Workflow `.github/workflows/auto-pr-merge.yml` ("Auto Merge -> main") merged den Branch danach automatisch per `git merge --no-ff` direkt nach `main` und pusht `main`. Du musst dafür nichts weiter tun.

## 3. GFX-Render-Workflow & Fehlerdiagnose
- Die Datei `configs.toml` im Repository-Root enthält den Roblox-Benutzernamen, Hintergrund, Material und die Umrandungs-Einstellungen.
- Änderungen an `configs.toml`, `backgrounds/` oder `WomanRig.blend` auf `main` starten `.github/workflows/render-avatar.yml` (kann auch per `gh workflow run render-avatar.yml --ref main` ausgelöst werden).
- In der Workflow-Übersicht (`GITHUB_STEP_SUMMARY`) steht **ausschließlich** entweder:
  - das fertig gerenderte PNG mit dem in `configs.toml` gewählten Hintergrund (gespeichert im Rolling-Tag/Release `latest-render`, ohne die Git-Historie von `main` aufzublähen), **oder**
  - die exakte Fehlermeldung zum direkten Beheben durch dich als Agent.
- Halte das Repository sauber: keine temporären Artefakte, keine unnötigen Dateien oder Duplikate einchecken. Alte Branches, alte Workflow-Läufe und alte Artefakte räumt `.github/workflows/cleanup.yml` automatisch weg.
