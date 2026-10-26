# Agent-Workflow-Regeln (Verbindlich)

Diese Regeln gelten für jede KI-Agent-Session in diesem Repository (`merta-studios/ROBLOXAVATARRENDER`):

## 1. Immer direkt und ohne Nachfrage auf `main` bringen
- Wenn du Änderungen am Repository vornimmst, **committest, pusht und mergest du sie sofort auf `main` — ohne vorher um Erlaubnis zu fragen**.
- Frage den Nutzer niemals „Soll ich committen/pushen/einen PR erstellen?“, sondern führe den kompletten Weg bis auf `main` direkt selbstständig aus.

## 2. Mehrfach-Merges pro Session auf demselben Arbeitsbranch
- Arena bindet jede Session fest an ihren eigenen Branch (`arena/<session-id>`). Wechsle niemals den Branch und lösche den aktuellen Session-Branch während der laufenden Session nicht.
- So bringst du Änderungen **beliebig oft pro Session** sauber auf `main`:
  1. Vor neuen Änderungen aktuellen Stand von `origin/main` holen und in den Arbeitsbranch mergen (`git fetch origin main && git merge --no-edit origin/main`), damit keine Konflikte entstehen.
  2. Änderungen committen und auf den aktuellen Session-Branch pushen:
     ```bash
     git add -A
     git commit -m "<präzise Commit-Nachricht>"
     git push origin <aktueller-arena-branch>
     ```
  3. **Automatisch + Direkt-Merge**:
     - Der Push startet automatisch den GitHub-Workflow `.github/workflows/auto-pr-merge.yml`, der einen Pull Request nach `main` erstellt und mit `--merge` zusammenführt.
     - Zusätzlich kannst (und sollst) du den PR direkt per `gh` erstellen und mit `--merge` (niemals `--squash` und niemals `--delete-branch`, damit Folge-Pushes in derselben Session konfliktfrei bleiben!) mergen:
       ```bash
       PR_NUM=$(gh pr list --state open --head "$(git branch --show-current)" --base main --json number --jq '.[0].number')
       if [ -z "$PR_NUM" ]; then
         gh pr create --base main --head "$(git branch --show-current)" --title "<Titel>" --body "<Beschreibung>"
         PR_NUM=$(gh pr list --state open --head "$(git branch --show-current)" --base main --json number --jq '.[0].number')
       fi
       gh pr merge "$PR_NUM" --merge
       ```
- Durch `--merge` bleibt der gemeinsame Merge-Base-Verlauf erhalten, sodass **Push 2, Push 3, Push 4 usw. in derselben Session** jederzeit wieder einen neuen PR öffnen und direkt nach `main` mergen können.

## 3. GFX-Render-Workflow & Fehlerdiagnose
- Die Datei `username.txt` im Repository-Root enthält den zu rendernden Roblox-Benutzernamen.
- Jede Änderung an `username.txt` auf `main` startet `.github/workflows/render-avatar.yml` (kann auch per `gh workflow run render-avatar.yml --ref main` ausgelöst werden).
- In der Workflow-Übersicht (`GITHUB_STEP_SUMMARY`) steht **ausschließlich** entweder:
  - das fertig gerenderte transparente PNG (gespeichert im Rolling-Tag/Release `latest-render`, ohne die Git-Historie von `main` aufzublähen), **oder**
  - die exakte Fehlermeldung zum direkten Beheben durch dich als Agent.
- Halte das Repository sauber: keine temporären Artefakte, keine unnötigen Dateien oder Duplikate einchecken. Alte Branches, alte Workflow-Läufe und alte Artefakte räumt `.github/workflows/cleanup.yml` automatisch weg.
