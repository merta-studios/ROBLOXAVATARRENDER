"""TEMPORÄR: Diagnose-Dump für die Render-Pipeline (wird nach der Analyse entfernt)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from scripts.roblox_opencloud import (  # noqa: E402
    RobloxOpenCloudClient,
    _mesh_bbox_size,
    load_avatar_bundle,
)
from scripts.rbx_formats import extract_asset_id, parse_rbx_model  # noqa: E402

OUT = Path("/tmp/debug_out")
OUT.mkdir(parents=True, exist_ok=True)


def username() -> str:
    for line in (REPO_ROOT / "username.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise RuntimeError("username.txt leer")


def walk(node, depth=0, out=None):
    out = out if out is not None else []
    props = {}
    for key in node.props.keys() if hasattr(node, "props") else []:
        val = node.props.get(key)
        if isinstance(val, (bytes, str)) and len(str(val)) > 120:
            val = str(val)[:120] + "..."
        props[key] = val
    out.append(
        {
            "depth": depth,
            "class": node.class_name,
            "name": node.name,
            "props": props,
        }
    )
    for child in node.children:
        walk(child, depth + 1, out)
    return out


def dump_asset_tree(client: RobloxOpenCloudClient, asset_id: int, label: str) -> list:
    raw = client.fetch_asset_bytes(asset_id)
    print(f"[asset] {label} {asset_id}: {len(raw)} bytes, head={raw[:16]!r}")
    try:
        roots = parse_rbx_model(raw)
    except Exception as exc:
        print(f"[asset]   parse error: {exc}")
        return []
    nodes = []
    for root in roots:
        walk(root, 0, nodes)
    for n in nodes:
        print(f"[asset] {label} {'  ' * n['depth']}{n['class']} '{n['name']}' {n['props']}")
    return nodes


def mesh_debug(client: RobloxOpenCloudClient, mesh_id: int, label: str) -> None:
    from scripts.rbx_formats import parse_rbx_mesh

    raw = client.fetch_asset_bytes(mesh_id)
    mesh = parse_rbx_mesh(raw)
    ext = _mesh_bbox_size(mesh)
    uv = np.asarray(mesh.uvs) if mesh.uvs else np.zeros((0, 2))
    print(
        f"[mesh] {label} {mesh_id}: verts={len(mesh.positions)} tris={len(mesh.faces)} "
        f"bbox={tuple(round(x, 3) for x in ext)} uv_verts={len(uv)}"
    )
    if len(uv):
        print(
            f"[mesh]   uv x[{uv[:,0].min():.3f},{uv[:,0].max():.3f}] "
            f"y[{uv[:,1].min():.3f},{uv[:,1].max():.3f}] mean={uv.mean(axis=0).round(3)}"
        )


def main() -> int:
    client = RobloxOpenCloudClient(os.environ.get("ROBLOX_API_KEY", ""))
    user_id, canonical, display = client.resolve_user(username())
    meta = client.fetch_avatar_metadata(user_id)
    assets = meta.get("assets") or []
    print(f"[user] {canonical} ({user_id}) assets={len(assets)}")
    for item in assets:
        atype = int((item.get("assetType") or {}).get("id") or 0)
        print(f"[user]   type={atype:3d} id={item.get('id')} name={item.get('name')}")

    for item in assets:
        atype = int((item.get("assetType") or {}).get("id") or 0)
        if atype in (17, 42, 41, 8):
            nodes = dump_asset_tree(client, int(item["id"]), str(item.get("name"))[:20])
            for n in nodes:
                mid = None
                if n["class"] in ("MeshPart", "SpecialMesh"):
                    mid = extract_asset_id(
                        n["props"].get("MeshId")
                        or n["props"].get("MeshContent")
                        or n["props"].get("MeshID")
                    )
                if mid:
                    mesh_debug(client, int(mid), n["class"])

    bundle = load_avatar_bundle(username=username(), api_key=os.environ.get("ROBLOX_API_KEY", ""), work_dir=OUT)
    print(f"[bundle] head kind={bundle.head.kind} skin={bundle.head.skin_rgb} "
          f"custom_tex={bundle.head.custom_texture_path}")
    print("DEBUG_DUMP_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
