"""Haupt-Orchestrator für den Roblox R6 Avatar GFX Render-Workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

# Stelle sicher, dass das Repository-Root im Python-Pfad liegt
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image

from scripts.roblox_opencloud import PipelineError, _sanitize_secret, RobloxOpenCloudClient, load_avatar_bundle
from scripts.render_config import (
    gfx_type_from_config_path,
    read_classic_config,
    read_config,
    resolve_background,
)


def verify_rendered_png(png_path: Path, *, expect_transparency: bool = True) -> None:
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
    if expect_transparency and alpha_min == 255:
        raise PipelineError(
            "Der Hintergrund sollte transparent sein, der Alpha-Kanal ist jedoch durchgehend deckend."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rendert einen Roblox R6 Avatar mit WomanRig.blend in Blender Cycles.")
    parser.add_argument("--config-file", default="HearthandsType/configs.toml", help="Pfad zur kommentierten TOML-Konfiguration für Avatar und Render. Der übergeordnete Ordner bestimmt den GFX-Typ (HearthandsType oder ClassicType).")
    parser.add_argument("--blend-file", default="WomanRig.blend", help="Pfad zur WomanRig.blend Datei.")
    parser.add_argument("--output", default="render/latest.png", help="Zielpfad für das gerenderte transparente PNG.")
    parser.add_argument("--status-json", default="/tmp/render_status.json", help="Pfad für den strukturierten Ergebnis-Report.")
    parser.add_argument("--gfx-type", default=None, help="GFX-Typ: 'HearthandsType' oder 'ClassicType'. Wird aus dem Konfigurations-Ordner abgeleitet, falls nicht angegeben.")
    args = parser.parse_args()

    api_key = os.environ.get("ROBLOX_API_KEY", "")
    status_path = Path(args.status_json)
    output_path = (REPO_ROOT / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    blend_path = (REPO_ROOT / args.blend_file).resolve() if not Path(args.blend_file).is_absolute() else Path(args.blend_file)
    config_file = (REPO_ROOT / args.config_file).resolve() if not Path(args.config_file).is_absolute() else Path(args.config_file)

    try:
        if not blend_path.is_file():
            raise PipelineError(f"Die Blender-Datei '{blend_path.name}' wurde im Repository nicht gefunden.")

        try:
            gfx_type_arg = args.gfx_type or None  # leerer String wird zu None
            gfx_type = gfx_type_from_config_path(config_file, gfx_type_arg)
            if gfx_type == "ClassicType":
                config = read_classic_config(config_file)
            else:
                config = read_config(config_file)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc

        username = os.environ.get("INPUT_USERNAME", "").strip() or config["username"]

        # Ein gemeinsamer Client für alle Open-Cloud-Abfragen (Bundle, Korblox-Bein, Emote).
        client = RobloxOpenCloudClient(api_key)

        with tempfile.TemporaryDirectory(prefix="rbx_gfx_") as tmp:
            work_dir = Path(tmp)
            try:
                background_kind, background_path = resolve_background(config["background"], work_dir)
            except ValueError as exc:
                raise PipelineError(str(exc)) from exc
            bundle = load_avatar_bundle(
                username=username,
                api_key=api_key,
                work_dir=work_dir,
                gfx_type=gfx_type,
                client=client,
            )

            # Korblox-Bein vorab laden (nur HearthandsType mit korblox=true).
            korblox_asset: dict[str, Any] | None = None
            if gfx_type == "HearthandsType" and bool(config["korblox"]):
                try:
                    korblox_asset = client.load_korblox_leg(work_dir)
                except PipelineError as exc:
                    raise PipelineError(str(exc)) from exc

            # ClassicType: Emote-/Profilpose vorab auflösen.
            classic_emote = None
            classic_pose = str(config.get("pose", "None"))
            if gfx_type == "ClassicType":
                emote_asset_id = int(config.get("emote_asset_id", 0))
                if classic_pose == "Equipped":
                    equipped = client.fetch_equipped_emote(bundle.user_id)
                    if equipped and equipped.get("emote_asset_id"):
                        emote_asset_id = int(equipped["emote_asset_id"])
                        print(
                            f"[pose] Ausgerüstete Profilpose (Emote {emote_asset_id}) wurde verwendet."
                        )
                    else:
                        print("[pose] Keine ausgerüstete Profilpose gefunden - Pose bleibt 'None'.")
                if classic_pose == "Emote" and emote_asset_id:
                    classic_emote = client.load_emote_animation(emote_asset_id, work_dir)

            # Importiere bpy erst nach erfolgreicher API-Abfrage für schnelle Fehlerdiagnose
            from scripts.blender_render import render_avatar_scene

            render_avatar_scene(
                blend_path=blend_path,
                bundle=bundle,
                output_png=output_path,
                work_dir=work_dir,
                material_finish=config["material"],
                background_kind=background_kind,
                background_path=background_path,
                background_value=config["background"],
                outline=config["outline"],
                gfx_type=gfx_type,
                korblox=bool(config["korblox"]),
                headless=bool(config["headless"]),
                korblox_asset=korblox_asset,
                classic_pose=classic_pose if gfx_type == "ClassicType" else "None",
                classic_emote=classic_emote,
                classic_rotation_deg=float(config.get("rotation_deg", 0)) if gfx_type == "ClassicType" else 0.0,
                classic_abstand_oben=float(config.get("abstand_oben", 0)) if gfx_type == "ClassicType" else 0.0,
                classic_abstand_unten=float(config.get("abstand_unten", 0)) if gfx_type == "ClassicType" else 0.0,
            )

        verify_rendered_png(output_path, expect_transparency=(background_kind == "transparent"))

        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "gfx_type": gfx_type,
                    "username": bundle.username,
                    "display_name": bundle.display_name,
                    "user_id": bundle.user_id,
                    "accessories_count": len(bundle.accessories),
                    "scales": bundle.scales,
                    "player_avatar_type": bundle.player_avatar_type,
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
