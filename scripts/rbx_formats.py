"""Parser für Roblox Modell-Formate (.rbxm / .rbxmx) und Mesh-Formate (.mesh v1.00 - v7.00)."""

from __future__ import annotations

import base64
import json
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import lz4.block
import numpy as np
import zstandard

try:
    import DracoPy

    HAS_DRACO = True
except ImportError:
    HAS_DRACO = False


RBXM_MAGIC = b"<roblox!\x89\xff\r\n\x1a\n"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

_NORMALS = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
    (-1.0, 0.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, -1.0),
)
BASIC_ROTATIONS: dict[int, tuple[float, ...]] = {}
for _rid in range(1, 37):
    _x = _NORMALS[(_rid - 1) // 6]
    _y = _NORMALS[(_rid - 1) % 6]
    if abs(_x[0] * _y[0] + _x[1] * _y[1] + _x[2] * _y[2]) > 0.5:
        continue
    _z = (
        _x[1] * _y[2] - _x[2] * _y[1],
        _x[2] * _y[0] - _x[0] * _y[2],
        _x[0] * _y[1] - _x[1] * _y[0],
    )
    BASIC_ROTATIONS[_rid] = (
        _x[0], _y[0], _z[0],
        _x[1], _y[1], _z[1],
        _x[2], _y[2], _z[2],
    )

IDENTITY_CFRAME: tuple[float, ...] = (
    0.0, 0.0, 0.0,
    1.0, 0.0, 0.0,
    0.0, 1.0, 0.0,
    0.0, 0.0, 1.0,
)


def cframe_position(cframe: Any) -> tuple[float, float, float]:
    """(x, y, z) eines Roblox-CFrames (12-Tupel oder Matrix-Prop)."""
    if cframe is None:
        return (0.0, 0.0, 0.0)
    values = list(cframe)
    if len(values) < 3:
        return (0.0, 0.0, 0.0)
    return (float(values[0]), float(values[1]), float(values[2]))


def compose_cframes(
    parent: Any, child: Any
) -> tuple[float, float, float, float, float, float, float, float, float, float, float, float]:
    """Multipliziert zwei Roblox-CFrames: ``parent * child``.

    Roblox-CFrames sind (Position, Rotationsmatrix). Die Multiplikation
    ``C1 * C2`` verschiebt ``C2`` zuerst und dreht es dann um ``C1``:
    ``p = p1 + R1 * p2`` und ``R = R1 * R2``.
    """
    p1 = cframe_position(parent)
    p2 = cframe_position(child)
    v = list(child) if child is not None else list(IDENTITY_CFRAME)
    if len(v) < 12:
        v = list(IDENTITY_CFRAME)
    r1 = list(parent) if parent is not None else list(IDENTITY_CFRAME)
    if len(r1) < 12:
        r1 = list(IDENTITY_CFRAME)

    # Rotationsmatrix von C1 (Zeilenweise: r00 r01 r02 / r10 .. / r20 ..)
    m1 = [r1[3:6], r1[6:9], r1[9:12]]
    m2 = [v[3:6], v[6:9], v[9:12]]

    def _rot(m: list[list[float]], vec: tuple[float, float, float]) -> tuple[float, float, float]:
        return (
            m[0][0] * vec[0] + m[0][1] * vec[1] + m[0][2] * vec[2],
            m[1][0] * vec[0] + m[1][1] * vec[1] + m[1][2] * vec[2],
            m[2][0] * vec[0] + m[2][1] * vec[1] + m[2][2] * vec[2],
        )

    def _mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
        return [
            [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)
        ]

    rp = _rot(m1, p2)
    rm = _mul(m1, m2)
    return (
        p1[0] + rp[0], p1[1] + rp[1], p1[2] + rp[2],
        rm[0][0], rm[0][1], rm[0][2],
        rm[1][0], rm[1][1], rm[1][2],
        rm[2][0], rm[2][1], rm[2][2],
    )


def translate_cframe(
    cframe: Any, offset: tuple[float, float, float]
) -> tuple[float, float, float, float, float, float, float, float, float, float, float, float]:
    """Verschiebt ein CFrame um ``offset`` (in Roblox-Weltachsen)."""
    values = list(cframe) if cframe is not None else list(IDENTITY_CFRAME)
    if len(values) < 12:
        values = list(IDENTITY_CFRAME)
    moved = compose_cframes(
        (offset[0], offset[1], offset[2], 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
        tuple(values),
    )
    return moved


def extract_asset_id(value: Any) -> int | None:
    """Extrahiert eine numerische Roblox Asset-ID aus Content-Strings/Tupeln."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, tuple) and len(value) == 2 and value[0] == "uri":
        value = value[1]
    text = str(value).strip()
    if not text or text == "0" or text.lower().startswith(("rbxasset://", "rbxthumb://")):
        return None
    for pat in (r"rbxassetid://(\d+)", r"[?&]id=(\d+)", r"/assetId/(\d+)"):
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            aid = int(m.group(1))
            return aid if aid > 0 else None
    if text.isdigit():
        aid = int(text)
        return aid if aid > 0 else None
    matches = re.findall(r"(\d{4,})", text)
    if matches:
        aid = int(matches[-1])
        return aid if aid > 0 else None
    return None


@dataclass
class RbxNode:
    class_name: str
    name: str = ""
    props: dict[str, Any] = field(default_factory=dict)
    # Rohdaten fuer String-/BinaryString-Properties. Roblox nutzt denselben
    # Property-Typ (0x01) fuer Text und Binaerblobs (z. B. die
    # "ValuesAndTimes"-Daten einer FloatCurve). Der Text wird weiterhin
    # UTF-8-entschaerft gespeichert, die unveraenderten Bytes liegen hier.
    raw_props: dict[str, bytes] = field(default_factory=dict)
    children: list["RbxNode"] = field(default_factory=list)
    parent: "RbxNode | None" = field(default=None, repr=False)

    def prop(self, *names: str, default: Any = None) -> Any:
        for n in names:
            if n in self.props and self.props[n] is not None:
                return self.props[n]
        for key, val in self.props.items():
            if key.lower() in {n.lower() for n in names} and val is not None:
                return val
        return default

    def find_first_child(self, name: str) -> "RbxNode | None":
        for ch in self.children:
            if ch.name == name:
                return ch
        return None

    def find_first_child_of_class(self, class_name: str) -> "RbxNode | None":
        for ch in self.children:
            if ch.class_name == class_name:
                return ch
        return None

    def descendants(self) -> list["RbxNode"]:
        out: list[RbxNode] = []
        stack = list(reversed(self.children))
        while stack:
            node = stack.pop()
            out.append(node)
            stack.extend(reversed(node.children))
        return out


@dataclass
class MeshData:
    version: str
    positions: list[tuple[float, float, float]]
    normals: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    colors: list[tuple[float, float, float, float]] | None
    faces: list[tuple[int, int, int]]


# ---------------------------------------------------------------------------
# Binary .rbxm Parser
# ---------------------------------------------------------------------------

class _BinReader:
    def __init__(self, data: bytes, offset: int = 0) -> None:
        self.data = data
        self.o = offset

    def u8(self) -> int:
        v = self.data[self.o]
        self.o += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.data, self.o)[0]
        self.o += 2
        return v

    def i16(self) -> int:
        v = struct.unpack_from("<h", self.data, self.o)[0]
        self.o += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.data, self.o)[0]
        self.o += 4
        return v

    def f32s(self, n: int) -> tuple[float, ...]:
        vals = struct.unpack_from(f"<{n}f", self.data, self.o)
        self.o += 4 * n
        return vals

    def f64(self) -> float:
        v = struct.unpack_from("<d", self.data, self.o)[0]
        self.o += 8
        return v

    def bytes_(self) -> bytes:
        n = self.u32()
        b = self.data[self.o : self.o + n]
        self.o += n
        return b

    def string(self) -> str:
        return self.bytes_().decode("utf-8", errors="replace")

    def planes(self, count: int, width: int) -> np.ndarray:
        sz = count * width
        arr = np.frombuffer(self.data, dtype=np.uint8, count=sz, offset=self.o).reshape(width, count)
        self.o += sz
        return arr

    def interleaved_u32(self, count: int) -> np.ndarray:
        if count == 0:
            return np.zeros(0, dtype=np.uint32)
        p = self.planes(count, 4).astype(np.uint32)
        return (p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]

    def interleaved_i32(self, count: int) -> np.ndarray:
        if count == 0:
            return np.zeros(0, dtype=np.int64)
        raw = self.interleaved_u32(count).astype(np.int64)
        return (raw >> 1) ^ -(raw & 1)

    def interleaved_f32(self, count: int) -> list[float]:
        if count == 0:
            return []
        raw = self.interleaved_u32(count)
        vals = ((raw >> 1) | ((raw & 1) << 31)).astype(np.uint32).view(np.float32)
        return vals.astype(np.float64).tolist()

    def referents(self, count: int) -> list[int]:
        if count == 0:
            return []
        return np.cumsum(self.interleaved_i32(count)).astype(int).tolist()


def _decompress_chunk(raw: bytes, comp_len: int, uncomp_len: int) -> bytes:
    if comp_len == 0:
        return raw
    if raw[:4] == ZSTD_MAGIC:
        dctx = zstandard.ZstdDecompressor()
        return dctx.decompress(raw, max_output_size=max(uncomp_len, 1))
    return lz4.block.decompress(raw, uncompressed_size=uncomp_len)


def _read_rotations(r: _BinReader, n: int) -> list[tuple[float, ...]]:
    out: list[tuple[float, ...]] = []
    for _ in range(n):
        rid = r.u8()
        if rid == 0:
            out.append(tuple(float(x) for x in r.f32s(9)))
        else:
            out.append(BASIC_ROTATIONS.get(rid, IDENTITY_CFRAME[3:]))
    return out


def _parse_rbxm_binary(data: bytes) -> list[RbxNode]:
    pos = 32
    shared_strings: list[bytes] = []
    classes: dict[int, tuple[str, list[int]]] = {}
    nodes_by_ref: dict[int, RbxNode] = {}
    parent_pairs: list[tuple[int, int]] = []
    prop_chunks: list[bytes] = []

    while pos + 16 <= len(data):
        cname = data[pos : pos + 4]
        comp_len, uncomp_len, _ = struct.unpack_from("<III", data, pos + 4)
        pos += 16
        payload_len = comp_len if comp_len > 0 else uncomp_len
        raw_chunk = data[pos : pos + payload_len]
        pos += payload_len
        if cname == b"END\x00":
            break
        body = _decompress_chunk(raw_chunk, comp_len, uncomp_len)

        if cname == b"SSTR":
            r = _BinReader(body)
            _ver = r.u32()
            scount = r.u32()
            for _ in range(scount):
                r.o += 16  # MD5 hash
                shared_strings.append(r.bytes_())
        elif cname == b"INST":
            r = _BinReader(body)
            cid = r.u32()
            cls_name = r.string()
            obj_fmt = r.u8()
            cnt = r.u32()
            refs = r.referents(cnt)
            if obj_fmt == 1:
                r.o += cnt
            classes[cid] = (cls_name, refs)
            for ref in refs:
                nodes_by_ref[ref] = RbxNode(class_name=cls_name, name=cls_name)
        elif cname == b"PROP":
            prop_chunks.append(body)
        elif cname == b"PRNT":
            r = _BinReader(body)
            _fmt = r.u8()
            cnt = r.u32()
            child_refs = r.referents(cnt)
            parent_refs = r.referents(cnt)
            parent_pairs.extend(zip(child_refs, parent_refs))

    for body in prop_chunks:
        r = _BinReader(body)
        cid = r.u32()
        prop_name = r.string()
        if cid not in classes or r.o >= len(body):
            continue
        ptype = r.u8()
        _cls_name, refs = classes[cid]
        n = len(refs)
        vals: list[Any] | None = None
        raws: list[bytes] | None = None
        try:
            if ptype == 0x01:  # String / BinaryString / ContentId
                raws = [r.bytes_() for _ in range(n)]
                vals = [b.decode("utf-8", errors="replace") for b in raws]
            elif ptype == 0x02:  # Bool
                vals = [r.u8() != 0 for _ in range(n)]
            elif ptype == 0x03:  # Int32
                vals = r.interleaved_i32(n).tolist()
            elif ptype == 0x04:  # Float32
                vals = r.interleaved_f32(n)
            elif ptype == 0x05:  # Float64
                vals = [r.f64() for _ in range(n)]
            elif ptype == 0x0B:  # BrickColor
                vals = r.interleaved_u32(n).tolist()
            elif ptype == 0x0C:  # Color3 (float)
                cr, cg, cb = r.interleaved_f32(n), r.interleaved_f32(n), r.interleaved_f32(n)
                vals = list(zip(cr, cg, cb))
            elif ptype == 0x0D:  # Vector2
                vx, vy = r.interleaved_f32(n), r.interleaved_f32(n)
                vals = list(zip(vx, vy))
            elif ptype == 0x0E:  # Vector3
                vx, vy, vz = r.interleaved_f32(n), r.interleaved_f32(n), r.interleaved_f32(n)
                vals = list(zip(vx, vy, vz))
            elif ptype == 0x10:  # CFrame
                rots = _read_rotations(r, n)
                px, py, pz = r.interleaved_f32(n), r.interleaved_f32(n), r.interleaved_f32(n)
                vals = [(x, y, z, *rot) for x, y, z, rot in zip(px, py, pz, rots)]
            elif ptype == 0x12:  # Enum / token
                vals = r.interleaved_u32(n).tolist()
            elif ptype == 0x13:  # Ref
                vals = [nodes_by_ref.get(ref) for ref in r.referents(n)]
            elif ptype == 0x1A:  # Color3uint8
                planes = r.planes(n, 3)
                vals = [
                    (int(planes[0, i]) / 255.0, int(planes[1, i]) / 255.0, int(planes[2, i]) / 255.0)
                    for i in range(n)
                ]
            elif ptype == 0x1C:  # SharedString
                idxs = r.interleaved_u32(n).tolist()
                raws = [
                    shared_strings[idx] if 0 <= idx < len(shared_strings) else b""
                    for idx in idxs
                ]
                vals = [b.decode("utf-8", errors="replace") for b in raws]
            elif ptype == 0x1E:  # OptionalCFrame
                if r.u8() == 0x10:
                    rots = _read_rotations(r, n)
                    px, py, pz = r.interleaved_f32(n), r.interleaved_f32(n), r.interleaved_f32(n)
                    if r.u8() == 0x02:
                        vals = []
                        for x, y, z, rot in zip(px, py, pz, rots):
                            present = r.u8() != 0
                            vals.append((x, y, z, *rot) if present else None)
            elif ptype == 0x22:  # Content (2024+ MeshContent / ColorMapContent)
                kinds = r.interleaved_i32(n).tolist()
                uri_count = r.u32()
                uris = [r.string() for _ in range(uri_count)]
                obj_count = r.u32()
                _objs = r.referents(obj_count)
                vals = []
                ui = 0
                for k in kinds:
                    if k == 1 and ui < len(uris):
                        vals.append(("uri", uris[ui]))
                        ui += 1
                    else:
                        vals.append(None)
        except Exception:
            continue

        if vals is not None and len(vals) == n:
            for idx, (ref, val) in enumerate(zip(refs, vals)):
                node = nodes_by_ref[ref]
                node.props[prop_name] = val
                if raws is not None:
                    node.raw_props[prop_name] = raws[idx]
                if prop_name == "Name" and isinstance(val, str):
                    node.name = val

    roots: list[RbxNode] = []
    has_parent: set[int] = set()
    for cref, pref in parent_pairs:
        child = nodes_by_ref.get(cref)
        if child is None:
            continue
        has_parent.add(cref)
        parent = nodes_by_ref.get(pref)
        if parent is not None:
            child.parent = parent
            parent.children.append(child)
        else:
            roots.append(child)

    for ref, node in nodes_by_ref.items():
        if ref not in has_parent:
            roots.append(node)

    return roots


# ---------------------------------------------------------------------------
# XML .rbxmx Parser
# ---------------------------------------------------------------------------

def _float_child(el: ET.Element, tag: str, default: float = 0.0) -> float:
    ch = el.find(tag)
    if ch is not None and ch.text:
        try:
            return float(ch.text.strip())
        except ValueError:
            pass
    return default


def _parse_xml_item(item_el: ET.Element, parent: RbxNode | None = None) -> RbxNode:
    cls_name = item_el.attrib.get("class", "Instance")
    node = RbxNode(class_name=cls_name, name=cls_name, parent=parent)

    props_el = item_el.find("Properties")
    if props_el is not None:
        for pel in props_el:
            pname = pel.attrib.get("name", "")
            tag = pel.tag
            val: Any = None
            text = (pel.text or "").strip()

            if tag in ("string", "ProtectedString", "BinaryString"):
                val = text
            elif tag == "Content":
                url_el = pel.find("url")
                if url_el is not None and url_el.text:
                    val = url_el.text.strip()
                elif text and text.lower() != "null":
                    val = text
            elif tag == "bool":
                val = text.lower() == "true"
            elif tag in ("int", "int64", "token"):
                try:
                    val = int(text)
                except ValueError:
                    val = 0
            elif tag in ("float", "double"):
                try:
                    val = float(text)
                except ValueError:
                    val = 0.0
            elif tag == "Vector3":
                val = (
                    _float_child(pel, "X"),
                    _float_child(pel, "Y"),
                    _float_child(pel, "Z"),
                )
            elif tag == "CoordinateFrame":
                val = (
                    _float_child(pel, "X"),
                    _float_child(pel, "Y"),
                    _float_child(pel, "Z"),
                    _float_child(pel, "R00", 1.0),
                    _float_child(pel, "R01", 0.0),
                    _float_child(pel, "R02", 0.0),
                    _float_child(pel, "R10", 0.0),
                    _float_child(pel, "R11", 1.0),
                    _float_child(pel, "R12", 0.0),
                    _float_child(pel, "R20", 0.0),
                    _float_child(pel, "R21", 0.0),
                    _float_child(pel, "R22", 1.0),
                )
            elif tag == "OptionalCoordinateFrame":
                cf_el = pel.find("CFrame")
                if cf_el is not None:
                    val = (
                        _float_child(cf_el, "X"),
                        _float_child(cf_el, "Y"),
                        _float_child(cf_el, "Z"),
                        _float_child(cf_el, "R00", 1.0),
                        _float_child(cf_el, "R01", 0.0),
                        _float_child(cf_el, "R02", 0.0),
                        _float_child(cf_el, "R10", 0.0),
                        _float_child(cf_el, "R11", 1.0),
                        _float_child(cf_el, "R12", 0.0),
                        _float_child(cf_el, "R20", 0.0),
                        _float_child(cf_el, "R21", 0.0),
                        _float_child(cf_el, "R22", 1.0),
                    )
            elif tag == "Color3":
                if pel.find("R") is not None:
                    val = (
                        _float_child(pel, "R", 1.0),
                        _float_child(pel, "G", 1.0),
                        _float_child(pel, "B", 1.0),
                    )
                elif text.isdigit():
                    u = int(text)
                    val = (((u >> 16) & 0xFF) / 255.0, ((u >> 8) & 0xFF) / 255.0, (u & 0xFF) / 255.0)
            elif tag == "Color3uint8":
                try:
                    u = int(text)
                    val = (((u >> 16) & 0xFF) / 255.0, ((u >> 8) & 0xFF) / 255.0, (u & 0xFF) / 255.0)
                except ValueError:
                    pass

            if pname and val is not None:
                node.props[pname] = val
                if tag == "BinaryString":
                    # Binaer-Properties stehen in .rbxmx als Base64.
                    try:
                        node.raw_props[pname] = base64.b64decode(text) if text else b""
                    except Exception:
                        node.raw_props[pname] = b""
                if pname == "Name" and isinstance(val, str):
                    node.name = val

    for child_el in item_el.findall("Item"):
        ch = _parse_xml_item(child_el, parent=node)
        node.children.append(ch)

    return node


def parse_rbx_model(data: bytes) -> list[RbxNode]:
    """Parst ein binäres (.rbxm) oder XML-basiertes (.rbxmx) Roblox-Modell."""
    if data[:14] == RBXM_MAGIC:
        return _parse_rbxm_binary(data)
    text_start = data.lstrip()
    if text_start.startswith(b"<roblox") or text_start.startswith(b"<?xml"):
        xml_str = text_start.decode("utf-8", errors="replace")
        root = ET.fromstring(xml_str)
        return [_parse_xml_item(item_el) for item_el in root.findall("Item")]
    raise ValueError("Unbekanntes Roblox-Modellformat (weder .rbxm noch .rbxmx).")


# ---------------------------------------------------------------------------
# Roblox .mesh Parser (v1.00 - v7.00 inklusive Draco COREMESH)
# ---------------------------------------------------------------------------

def _read_binary_vertices(
    data: bytes, offset: int, count: int, vsize: int
) -> tuple[
    list[tuple[float, float, float]],
    list[tuple[float, float, float]],
    list[tuple[float, float]],
    list[tuple[float, float, float, float]] | None,
    int,
]:
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    colors: list[tuple[float, float, float, float]] | None = [] if vsize >= 40 else None

    pos = offset
    for _ in range(count):
        px, py, pz, nx, ny, nz, tu, tv = struct.unpack_from("<8f", data, pos)
        positions.append((px, py, pz))
        normals.append((nx, ny, nz))
        uvs.append((tu, 1.0 - tv))
        if vsize >= 40 and colors is not None:
            r, g, b, a = struct.unpack_from("<4B", data, pos + 36)
            colors.append((r / 255.0, g / 255.0, b / 255.0, a / 255.0))
        pos += vsize
    return positions, normals, uvs, colors, pos


def parse_rbx_mesh(data: bytes) -> MeshData:
    """Parst ein Roblox FileMesh (.mesh) der Versionen 1.00 bis 7.00."""
    if not data.startswith(b"version "):
        raise ValueError("Ungültige Roblox .mesh-Datei (Header 'version ' fehlt).")

    nl = data.index(b"\n")
    ver_line = data[:nl].decode("ascii", errors="replace").strip()
    ver_num = ver_line.replace("version", "").strip()
    body = data[nl + 1 :]

    # Version 1.00 / 1.01 (ASCII)
    if ver_num.startswith("1."):
        lines = body.decode("utf-8", errors="replace").splitlines()
        if len(lines) < 2:
            raise ValueError(f"Ungültiges v{ver_num} Mesh (zu wenige Zeilen).")
        face_count = int(lines[0].strip())
        arr = json.loads("[" + lines[1].strip().replace("][", "],[") + "]")
        vert_count = len(arr) // 3
        scale = 0.5 if ver_num == "1.00" else 1.0
        positions = []
        normals = []
        uvs = []
        for i in range(vert_count):
            vp = arr[i * 3]
            vn = arr[i * 3 + 1]
            vt = arr[i * 3 + 2]
            positions.append((float(vp[0]) * scale, float(vp[1]) * scale, float(vp[2]) * scale))
            normals.append((float(vn[0]), float(vn[1]), float(vn[2])))
            uvs.append((float(vt[0]), 1.0 - float(vt[1])))
        faces = [(i * 3, i * 3 + 1, i * 3 + 2) for i in range(min(face_count, vert_count // 3))]
        return MeshData(ver_num, positions, normals, uvs, None, faces)

    # Version 6.00 / 7.00 (Chunked COREMESH + LODS)
    if ver_num.startswith(("6.", "7.")) or body[:8] == b"COREMESH":
        offset = 0
        coremesh: tuple[int, bytes] | None = None
        lod_payload: bytes | None = None
        while offset + 16 <= len(body):
            ctype = body[offset : offset + 8].decode("ascii", errors="replace").rstrip("\x00")
            cver, csize = struct.unpack_from("<II", body, offset + 8)
            offset += 16
            cdata = body[offset : offset + csize]
            offset += csize
            if ctype == "COREMESH":
                coremesh = (cver, cdata)
            elif ctype == "LODS" and cver == 1:
                lod_payload = cdata

        if coremesh is None:
            raise ValueError(f"Kein COREMESH-Chunk in .mesh v{ver_num} gefunden.")

        cver, cdata = coremesh
        if cver == 1:
            num_verts = struct.unpack_from("<I", cdata, 0)[0]
            positions, normals, uvs, colors, pos = _read_binary_vertices(cdata, 4, num_verts, 40)
            num_faces = struct.unpack_from("<I", cdata, pos)[0]
            pos += 4
            faces = [struct.unpack_from("<3I", cdata, pos + i * 12) for i in range(num_faces)]
        elif cver == 2:
            if not HAS_DRACO:
                raise RuntimeError("DracoPy ist nicht installiert, benötigt für Roblox .mesh v7.00.")
            draco_size = struct.unpack_from("<I", cdata, 0)[0]
            dmesh = DracoPy.decode(cdata[4 : 4 + draco_size])
            pts = np.asarray(dmesh.points, dtype=np.float32).reshape(-1, 3)
            num_verts = len(pts)
            positions = [(float(p[0]), float(p[1]), float(p[2])) for p in pts]

            raw_norms = None
            if hasattr(dmesh, "get_attribute_by_unique_id"):
                try:
                    na = dmesh.get_attribute_by_unique_id(1)
                    if na is not None and "data" in na:
                        raw_norms = np.asarray(na["data"], dtype=np.float32).reshape(-1, 3)
                except Exception:
                    pass
            if raw_norms is None and getattr(dmesh, "normals", None) is not None:
                arr = np.asarray(dmesh.normals, dtype=np.float32)
                if arr.size == num_verts * 3:
                    raw_norms = arr.reshape(-1, 3)
            if raw_norms is not None and len(raw_norms) == num_verts:
                normals = [(float(n[0]), float(n[1]), float(n[2])) for n in raw_norms]
            else:
                normals = []

            raw_uvs = None
            if hasattr(dmesh, "get_attribute_by_unique_id"):
                try:
                    ua = dmesh.get_attribute_by_unique_id(2)
                    if ua is not None and "data" in ua:
                        raw_uvs = np.asarray(ua["data"], dtype=np.float32).reshape(-1, 2)
                except Exception:
                    pass
            if raw_uvs is None and getattr(dmesh, "tex_coord", None) is not None:
                arr = np.asarray(dmesh.tex_coord, dtype=np.float32)
                if arr.size == num_verts * 2:
                    raw_uvs = arr.reshape(-1, 2)
            if raw_uvs is not None and len(raw_uvs) == num_verts:
                uvs = [(float(t[0]), 1.0 - float(t[1])) for t in raw_uvs]
            else:
                uvs = [(0.0, 0.0)] * num_verts

            colors = None
            if hasattr(dmesh, "get_attribute_by_unique_id"):
                try:
                    ca = dmesh.get_attribute_by_unique_id(4)
                    if ca is not None and "data" in ca:
                        carr = np.asarray(ca["data"], dtype=np.uint8).reshape(-1, 4)
                        if len(carr) == num_verts:
                            colors = [
                                (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, c[3] / 255.0)
                                for c in carr
                            ]
                except Exception:
                    pass

            faces = [(int(f[0]), int(f[1]), int(f[2])) for f in dmesh.faces]
        else:
            raise ValueError(f"Unbekannte COREMESH-Version {cver} in .mesh v{ver_num}.")

        if lod_payload and len(lod_payload) >= 15:
            num_offsets = struct.unpack_from("<I", lod_payload, 3)[0]
            if num_offsets >= 2:
                lod0_start, lod0_end = struct.unpack_from("<II", lod_payload, 7)
                if 0 <= lod0_start < lod0_end <= len(faces):
                    faces = faces[lod0_start:lod0_end]

        return MeshData(ver_num, positions, normals, uvs, colors, faces)

    # Version 2.00 - 5.xx
    header_size = struct.unpack_from("<H", body, 0)[0]
    num_bones = 0
    num_lod_offsets = 0

    if ver_num.startswith("2."):
        vsize = struct.unpack_from("<B", body, 2)[0]
        num_verts, num_faces = struct.unpack_from("<II", body, 4)
    elif ver_num.startswith("3."):
        vsize = struct.unpack_from("<B", body, 2)[0]
        num_lod_offsets = struct.unpack_from("<H", body, 6)[0]
        num_verts, num_faces = struct.unpack_from("<II", body, 8)
    elif ver_num.startswith(("4.", "5.")):
        vsize = 40
        num_verts, num_faces = struct.unpack_from("<II", body, 4)
        num_lod_offsets, num_bones = struct.unpack_from("<HH", body, 12)
    else:
        raise ValueError(f"Nicht unterstützte Roblox .mesh-Version: {ver_num}")

    positions, normals, uvs, colors, pos = _read_binary_vertices(body, header_size, num_verts, vsize)
    if num_bones > 0:
        pos += num_verts * 8  # Skinning-Gewichte überspringen

    faces = [struct.unpack_from("<3I", body, pos + i * 12) for i in range(num_faces)]
    pos += num_faces * 12

    if num_lod_offsets >= 2 and pos + num_lod_offsets * 4 <= len(body):
        lods = struct.unpack_from(f"<{num_lod_offsets}I", body, pos)
        if 0 <= lods[0] < lods[1] <= len(faces):
            faces = faces[lods[0] : lods[1]]

    return MeshData(ver_num, positions, normals, uvs, colors, faces)
