"""Haupt-Orchestrator für den Roblox R6 Avatar GFX Render-Workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

# Stelle sicher, dass das Repository-Root im Python-Pfad liegt
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image

from scripts.roblox_opencloud import PipelineError, _sanitize_secret, load_avatar_bundle


def read_username_file(path: Path) -> str:
    if not path.is_file():
        raise PipelineError(f"Die Trigger-Datei '{path.name}' wurde im Repository nicht gefunden.")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            return line
    raise PipelineError(
        f"Die Datei '{path.name}' ist leer. Bitte trage dort einen gültigen Roblox-Benutzernamen ein."
    )


def verify_rendered_png(png_path: Path) -> None:
    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise PipelineError("Das gerenderte PNG fehlt oder ist 0 Bytes groß.")
    img = Image.open(png_path)
    img.load()
    if img.mode != "RGBA":
        raise PipelineError(f"Erwartetes RGBA-Bild mit transparentem Hintergrund, erhalten: {img.mode}.")
    alpha_min, alpha_max = img.getchannel("A").getextrema()
    if alpha_max == 0:
        raise PipelineError(
            "Das gerenderte Bild ist vollständig transparent (leer). Prüfe Kamera/OpenGL-Kontext."
        )
    if alpha_min == 255:
        raise PipelineError(
            "Der Hintergrund des gerenderten Bildes ist nicht transparent (Alpha durchgehend 255)."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rendert einen Roblox R6 Avatar mit WomanRig.blend in Blender EEVEE.")
    parser.add_argument("--username-file", default="username.txt", help="Pfad zur Textdatei mit dem Roblox-Benutzernamen.")
    parser.add_argument("--blend-file", default="WomanRig.blend", help="Pfad zur WomanRig.blend Datei.")
    parser.add_argument("--output", default="render/latest.png", help="Zielpfad für das gerenderte transparente PNG.")
    parser.add_argument("--status-json", default="/tmp/render_status.json", help="Pfad für den strukturierten Ergebnis-Report.")
    args = parser.parse_args()

    api_key = os.environ.get("ROBLOX_API_KEY", "")
    status_path = Path(args.status_json)
    output_path = (REPO_ROOT / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    blend_path = (REPO_ROOT / args.blend_file).resolve() if not Path(args.blend_file).is_absolute() else Path(args.blend_file)
    user_file = (REPO_ROOT / args.username_file).resolve() if not Path(args.username_file).is_absolute() else Path(args.username_file)

    try:
        if not blend_path.is_file():
            raise PipelineError(f"Die Blender-Datei '{blend_path.name}' wurde im Repository nicht gefunden.")

        username = read_username_file(user_file)

        with tempfile.TemporaryDirectory(prefix="rbx_gfx_") as tmp:
            work_dir = Path(tmp)
            bundle = load_avatar_bundle(username=username, api_key=api_key, work_dir=work_dir)

            # Importiere bpy erst nach erfolgreicher API-Abfrage für schnelle Fehlerdiagnose
            from scripts.blender_render import render_avatar_scene

            render_avatar_scene(
                blend_path=blend_path,
                bundle=bundle,
                output_png=output_path,
                work_dir=work_dir,
            )

        verify_rendered_png(output_path)

        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "username": bundle.username,
                    "display_name": bundle.display_name,
                    "user_id": bundle.user_id,
                    "accessories_count": len(bundle.accessories),
                    "output_png": str(output_path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return 0

    except PipelineError as exc:
        err_msg = _sanitize_secret(str(exc), api_key)
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps({"ok": False, "error": err_msg}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"RENDER_ERROR: {err_msg}", file=sys.stderr)
        return 1

    except Exception as exc:
        tb = _sanitize_secret(traceback.format_exc(), api_key)
        err_msg = f"Unerwarteter Fehler in der Render-Pipeline ({type(exc).__name__}: {_sanitize_secret(str(exc), api_key)}):\n\n{tb}"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps({"ok": False, "error": err_msg}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(err_msg, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
