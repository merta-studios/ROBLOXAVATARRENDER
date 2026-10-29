"""TEMPORAERE DIAGNOSE (nur fuer die Analyse, nicht produktiv).

Schreibt Rohdaten von Koerperteil-Assets und Animationen in die
PNG-Metadaten, damit sie ausserhalb der CI ausgewertet werden koennen.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from scripts.rbx_formats import parse_rbx_model

INTERESTING_ASSETS = (139607718, 139607625, 134082507, 27112025, 27112052)
INTERESTING_ANIMATIONS = (658830056,)


def _cframe(node: Any, prop: str) -> Any:
    value = node.prop(prop)
    if value is None:
        return None
    try:
        return [round(float(v), 4) for v in tuple(value)[:12]]
    except Exception:  # pragma: no cover
        return str(value)


def dump_assets(client: Any, asset_ids: tuple[int, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for asset_id in asset_ids:
        entry: dict[str, Any] = {}
        try:
            raw = client.fetch_asset_bytes(asset_id)
        except Exception as exc:  # pragma: no cover
            entry["error"] = str(exc)[:300]
            out[str(asset_id)] = entry
            continue
        entry["bytes"] = len(raw)
        entry["raw_b64"] = base64.b64encode(raw).decode("ascii")
        try:
            roots = parse_rbx_model(raw)
        except Exception as exc:
            entry["parse_error"] = str(exc)[:300]
            out[str(asset_id)] = entry
            continue
        nodes: list[dict[str, Any]] = []

        def visit(node: Any, context: str = "") -> None:
            marker = node.name.casefold()
            if marker in ("r6", "r15artistintent", "r15fixed", "r15"):
                context = marker
            info: dict[str, Any] = {
                "class": node.class_name,
                "name": node.name,
                "ctx": context,
            }
            for prop in ("CFrame", "Size", "MeshSize", "TextureID", "Transparency",
                         "MeshId", "MeshContent", "Scale", "Offset", "Color3uint8",
                         "BrickColor", "SpecialMesh", "SpecialMeshType"):
                value = node.prop(prop)
                if value is None:
                    continue
                if prop in ("CFrame",):
                    info[prop] = _cframe(node, prop)
                elif isinstance(value, (int, float)):
                    info[prop] = round(float(value), 5)
                elif isinstance(value, (tuple, list)):
                    try:
                        info[prop] = [round(float(v), 5) for v in tuple(value)[:8]]
                    except Exception:
                        info[prop] = str(value)[:80]
                else:
                    info[prop] = str(value)[:120]
            sa = node.find_first_child_of_class("SurfaceAppearance")
            if sa is not None:
                sa_info: dict[str, Any] = {}
                for prop in ("AlphaMode", "ColorMap", "ColorMapContent"):
                    value = sa.prop(prop)
                    if value is None:
                        continue
                    if isinstance(value, (int, float)):
                        sa_info[prop] = round(float(value), 5)
                    elif isinstance(value, (tuple, list)):
                        try:
                            sa_info[prop] = [round(float(v), 5) for v in tuple(value)[:8]]
                        except Exception:
                            sa_info[prop] = str(value)[:80]
                    else:
                        sa_info[prop] = str(value)[:120]
                info["SurfaceAppearance"] = sa_info
            nodes.append(info)
            for child in node.children:
                visit(child, context)

        for root in roots:
            visit(root)
        entry["nodes"] = nodes
        out[str(asset_id)] = entry
    return out


def dump_animations(client: Any, asset_ids: tuple[int, ...], work_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for asset_id in asset_ids:
        entry: dict[str, Any] = {"asset_id": asset_id}
        try:
            raw = client.fetch_asset_bytes(asset_id)
            entry["bytes"] = len(raw)
            entry["raw_b64"] = base64.b64encode(raw).decode("ascii")
            roots = parse_rbx_model(raw)
            nodes = []

            def visit(node: Any, context: str = "") -> None:
                info = {
                    "class": node.class_name,
                    "name": node.name,
                    "ctx": context,
                }
                for prop in ("AnimationId", "Loop", "Priority", "Weight", "TimeScale"):
                    value = node.prop(prop)
                    if value is not None:
                        info[prop] = str(value)[:120]
                nodes.append(info)
                for child in node.children:
                    visit(child, context)

            for root in roots:
                visit(root)
            entry["nodes"] = nodes
        except Exception as exc:  # pragma: no cover
            entry["error"] = str(exc)[:300]
        try:
            emote = client.load_emote_animation(asset_id, work_dir)
            if emote is not None:
                clip = emote.clip
                entry["format"] = clip.source_format
                entry["bones"] = sorted(clip.bone_order())
                entry["times"] = len(clip.times)
                entry["preview_time"] = clip.preview_time()
        except Exception as exc:  # pragma: no cover
            entry["clip_error"] = str(exc)[:300]
        out[str(asset_id)] = entry
    return out


def collect(client: Any, work_dir: Path) -> dict[str, Any]:
    return {
        "body_parts": dump_assets(client, INTERESTING_ASSETS),
        "animations": dump_animations(client, INTERESTING_ANIMATIONS, work_dir),
    }
