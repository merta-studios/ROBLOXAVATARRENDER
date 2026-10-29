"""Offizieller Standard-R15-Blocky-Rig als reines Python-Asset.

Quelle: ``Roblox/avatar`` -> ``Legacy/rigs/R15_Block.fbx`` (offizielles Roblox
GitHub-Repo, verlinkt ueber die Creator-Hub-Ressourcen-Seite). Die FBX-Datei
liegt zur Nachvollziehbarkeit unter ``ClassicType/assets/R15_Block.fbx`` im
Repository. Fuer den Render wird **nicht** die FBX-Datei geoeffnet und auch
keine .blend-Datei benoetigt: ``extract_from_fbx()`` hat die 15 Koerperteile
einmalig in ``ClassicType/assets/r15_blocky.json.gz`` ueberfuehrt.

Das Runtime-Modul ``classic_render.py`` laedt nur dieses kompakte JSON. Damit
bleibt die Trennung erhalten: HearthandsType nutzt sein R6-Blend, ClassicType
baut seine Szene aus diesen 15 Meshes selbst auf.

FBX-Koordinaten (Maya: Y oben, +Z vorne) -> Blender (Z oben, -Y vorne):
    (x_bl, y_bl, z_bl) = (x_fbx, -z_fbx, y_fbx)
"""

from __future__ import annotations

import gzip
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = REPO_ROOT / "ClassicType" / "assets"
SOURCE_FBX_PATH = ASSET_DIR / "R15_Block.fbx"
RIG_DATA_PATH = ASSET_DIR / "r15_blocky.json.gz"

FBX_MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"

# Die 15 Standard-R15-Teile und ihre BodyColors-Zuordnung. Ein Roblox-Avatar
# ersetzt immer alle drei Segmente eines Assets (z. B. LeftUpperArm,
# LeftLowerArm, LeftHand) gemeinsam durch dasselbe BodyColor.
PART_COLOR_KEYS: dict[str, str] = {
    "Head": "head",
    "UpperTorso": "torso",
    "LowerTorso": "torso",
    "LeftUpperArm": "leftArm",
    "LeftLowerArm": "leftArm",
    "LeftHand": "leftArm",
    "RightUpperArm": "rightArm",
    "RightLowerArm": "rightArm",
    "RightHand": "rightArm",
    "LeftUpperLeg": "leftLeg",
    "LeftLowerLeg": "leftLeg",
    "LeftFoot": "leftLeg",
    "RightUpperLeg": "rightLeg",
    "RightLowerLeg": "rightLeg",
    "RightFoot": "rightLeg",
}

# Roblox-Attachment-Name -> Model-Name in der offiziellen FBX-Datei.
# Die Positionen kommen nicht mehr aus Schaetzwerten, sondern aus den
# Original-Attachment-Nodes des Rigs.
ATTACHMENT_MODEL_NAMES: dict[str, str] = {
    "HatAttachment": "Hat_Att",
    "HairAttachment": "Hair_Att",
    "FaceFrontAttachment": "FaceFront_Att",
    "FaceCenterAttachment": "FaceCenter_Att",
    "NeckAttachment": "Neck_Att",
    "BodyFrontAttachment": "BodyFront_Att",
    "BodyBackAttachment": "BodyBack_Att",
    "LeftCollarAttachment": "LeftCollar_Att",
    "RightCollarAttachment": "RightCollar_Att",
    "LeftShoulderAttachment": "LeftShoulder_Att",
    "RightShoulderAttachment": "RightShoulder_Att",
    "LeftGripAttachment": "LeftGrip_Att",
    "RightGripAttachment": "RightGrip_Att",
    "WaistFrontAttachment": "WaistFront_Att",
    "WaistCenterAttachment": "WaistCenter_Att",
    "WaistBackAttachment": "WaistBack_Att",
}

# Asset-Typ -> die drei R15-Teile, die dieses Asset ersetzt.
BODY_PART_ASSET_TYPES: dict[int, tuple[str, ...]] = {
    27: ("UpperTorso", "LowerTorso"),
    28: ("RightUpperArm", "RightLowerArm", "RightHand"),
    29: ("LeftUpperArm", "LeftLowerArm", "LeftHand"),
    30: ("LeftUpperLeg", "LeftLowerLeg", "LeftFoot"),
    31: ("RightUpperLeg", "RightLowerLeg", "RightFoot"),
}

# Offizielle Roblox-Rig-Attachment-Positionen des R15Fixed-Rigs.
#
# Quelle: Roblox-Client-Plugin "AssetImporter" aus dem offiziellen Repository
# github.com/MaximumADHD/Roblox-Client-Tracker ->
# BuiltInPlugins/AssetImporter/Src/Utility/RigSetup/FixedRigPositions.luac.s
# (Tabelle "R15FixedRigAttachmentMap"). Das ist genau die Tabelle, die Roblox
# selbst benutzt, um ein Koerperteil-Asset in das Standard-Rig einzuhängen:
# die Rig-Attachments eines Assets muessen auf diesen Positionen landen.
#
# Koordinaten: Roblox-Character-Space, HumanoidRootPart im Ursprung, die
# Fusssohlen liegen bei y = -2 (Roblox-Rig-Root sitzt 2 Studs ueber dem Boden).
R15_FIXED_ATTACHMENT_POSITIONS: dict[str, tuple[float, float, float]] = {
    "RootRigAttachment": (0.0, 0.0, 0.0),
    "WaistRigAttachment": (0.0, 0.4, 0.0),
    "NeckRigAttachment": (0.0, 2.0, 0.0),
    "LeftShoulderRigAttachment": (-1.0, 1.763, 0.0),
    "RightShoulderRigAttachment": (1.0, 1.763, 0.0),
    "LeftElbowRigAttachment": (-1.5, 1.035, 0.0),
    "RightElbowRigAttachment": (1.5, 1.035, 0.0),
    "LeftWristRigAttachment": (-1.5, 0.275, 0.0),
    "RightWristRigAttachment": (1.5, 0.275, 0.0),
    "LeftHipRigAttachment": (-0.5, 0.0, 0.0),
    "RightHipRigAttachment": (0.5, 0.0, 0.0),
    "LeftKneeRigAttachment": (-0.5, -0.822, 0.0),
    "RightKneeRigAttachment": (0.5, -0.822, 0.0),
    "LeftAnkleRigAttachment": (-0.5, -1.748, 0.0),
    "RightAnkleRigAttachment": (0.5, -1.748, 0.0),
    "LeftGripAttachment": (-1.5, 0.0, 0.0),
    "RightGripAttachment": (1.5, 0.0, 0.0),
    "LeftShoulderAttachment": (-1.5, 1.953, 0.0),
    "RightShoulderAttachment": (1.5, 1.953, 0.0),
    "BodyFrontAttachment": (0.0, 1.0, -0.5),
    "BodyBackAttachment": (0.0, 1.0, 0.5),
    "LeftCollarAttachment": (-1.0, 2.0, 0.0),
    "RightCollarAttachment": (1.0, 2.0, 0.0),
    "NeckAttachment": (0.0, 2.0, 0.0),
    "LeftFootAttachment": (-0.5, -2.0, 0.0),
    "RightFootAttachment": (0.5, -2.0, 0.0),
    "WaistCenterAttachment": (0.0, 0.0, 0.0),
    "WaistFrontAttachment": (0.0, 0.0, -0.5),
    "WaistBackAttachment": (0.0, 0.0, 0.5),
    "FaceCenterAttachment": (0.0, 2.5, 0.0),
    "FaceFrontAttachment": (0.0, 2.5, -0.6),
    "HairAttachment": (0.0, 3.1, 0.0),
    "HatAttachment": (0.0, 3.1, 0.0),
}

# Gelenk (Drehpunkt) jedes R15-Segments im offiziellen Rig.
#
# Roblox bewegt die Segmente ueber Motor6D-Joints: jedes Segment haengt an
# genau einem Gelenk und dreht sich genau darum. Die Gelenkpositionen stehen
# im Rig-Space (HumanoidRootPart im Ursprung, Fusssohlen bei y = -2) und
# stammen aus der offiziellen R15Fixed-Rig-Attachment-Map
# (Root/Waist/Neck/Shoulder/Elbow/Wrist/Hip/Knee/Ankle) - also genau den
# Positionen, die Roblox beim Einhaengen eines Koerperteil-Assets benutzt.
#
# Diese Zuordnung ist die gemeinsame Basis von Renderer und Client: sie
# bestimmt, welches "RigAttachment" eines Segments das Gelenk zum Elternteil
# ist und damit auch, welcher Versatz ein Asset zum Standard-Rig hat.
R15_BONE_PIVOT_ATTACHMENTS: dict[str, str] = {
    "LowerTorso": "RootRigAttachment",
    "UpperTorso": "WaistRigAttachment",
    "Head": "NeckRigAttachment",
    "LeftUpperArm": "LeftShoulderRigAttachment",
    "LeftLowerArm": "LeftElbowRigAttachment",
    "LeftHand": "LeftWristRigAttachment",
    "RightUpperArm": "RightShoulderRigAttachment",
    "RightLowerArm": "RightElbowRigAttachment",
    "RightHand": "RightWristRigAttachment",
    "LeftUpperLeg": "LeftHipRigAttachment",
    "LeftLowerLeg": "LeftKneeRigAttachment",
    "LeftFoot": "LeftAnkleRigAttachment",
    "RightUpperLeg": "RightHipRigAttachment",
    "RightLowerLeg": "RightKneeRigAttachment",
    "RightFoot": "RightAnkleRigAttachment",
}

EXPECTED_PARTS = tuple(PART_COLOR_KEYS)


@dataclass(frozen=True)
class R15Part:
    """Ein Koerperteil in Blender-Koordinaten (Z oben, -Y vorne)."""

    name: str
    vertices: list[tuple[float, float, float]]
    faces: list[tuple[int, ...]]
    # Ein UV-Eintrag pro Face-Ecke, in derselben Reihenfolge wie die
    # abgeflachten Faces. Nötig, weil ein Kubus pro Seite andere UVs hat.
    uvs: list[tuple[float, float]]
    color_key: str


def part_center(part: R15Part) -> tuple[float, float, float]:
    """Mittelpunkt eines Segments im Welt-Koordinatensystem des Standardrigs."""
    return tuple((min(v[i] for v in part.vertices) + max(v[i] for v in part.vertices)) / 2 for i in range(3))


def _read_rig_data(path: Path | None = None) -> dict[str, Any]:
    path = Path(path or RIG_DATA_PATH)
    if not path.is_file():
        raise RuntimeError(
            f"Standard-R15-Blocky-Rig fehlt: {path}. "
            "Bitte 'python3 -m scripts.r15_blocky_rig' ausfuehren."
        )
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def load_r15_blocky_rig(path: Path | None = None) -> dict[str, R15Part]:
    """Laedt die 15 vorbereiteten Standard-R15-Teile (kein bpy, keine FBX)."""
    raw = _read_rig_data(path)
    parts: dict[str, R15Part] = {}
    for name, data in raw.get("parts", {}).items():
        parts[name] = R15Part(
            name=name,
            vertices=[tuple(float(x) for x in v) for v in data["vertices"]],
            faces=[tuple(int(i) for i in f) for f in data["faces"]],
            uvs=[tuple(float(x) for x in uv) for uv in data["uvs"]],
            color_key=str(data["color_key"]),
        )
    missing = [name for name in EXPECTED_PARTS if name not in parts]
    if missing:
        raise RuntimeError(f"Standard-R15-Blocky-Rig unvollstaendig, es fehlen: {', '.join(missing)}.")
    for part in parts.values():
        if not part.vertices or not part.faces or len(part.uvs) != sum(len(f) for f in part.faces):
            raise RuntimeError(f"Standard-R15-Teil {part.name} hat ungueltige Geometrie/UVs.")
    return parts


def load_r15_attachments(path: Path | None = None) -> dict[str, tuple[float, float, float]]:
    """Laedt die originalen Attachment-Positionen in Blender-Koordinaten."""
    raw = _read_rig_data(path)
    attachments: dict[str, tuple[float, float, float]] = {}
    for name, values in raw.get("attachments", {}).items():
        if len(values) != 3:
            raise RuntimeError(f"Attachment {name} hat keine gueltige Position.")
        attachments[name] = (float(values[0]), float(values[1]), float(values[2]))
    missing = [name for name in ATTACHMENT_MODEL_NAMES if name not in attachments]
    if missing:
        raise RuntimeError(f"Standard-R15-Attachments unvollstaendig, es fehlen: {', '.join(missing)}.")
    return attachments


# ---------------------------------------------------------------------------
# Einmalige Extraktion aus der offiziellen Binary-FBX-Datei.
# Wird nur von ``main()`` benutzt, nicht vom Render-Pfad.
# ---------------------------------------------------------------------------
class _Reader:
    def __init__(self, data: bytes) -> None:
        self.d = data
        self.p = 0

    def read(self, n: int) -> bytes:
        out = self.d[self.p : self.p + n]
        self.p += n
        return out

    def u8(self) -> int:
        return struct.unpack("<B", self.read(1))[0]

    def i16(self) -> int:
        return struct.unpack("<h", self.read(2))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.read(4))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.read(8))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.read(8))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.read(4))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self.read(8))[0]


class _Node:
    def __init__(self, name: str, props: list[tuple[str, Any]], children: list["_Node"]) -> None:
        self.name = name
        self.props = props
        self.children = children

    def child(self, name: str) -> "_Node | None":
        return next((c for c in self.children if c.name == name), None)

    def values(self) -> list[Any]:
        return [value for _kind, value in self.props]


def _clean_fbx_name(value: Any) -> str:
    if isinstance(value, tuple):
        value = value[1]
    if isinstance(value, bytes):
        return value.split(b"\x00\x01")[0].decode("latin1")
    return str(value)


def _read_prop(r: _Reader) -> tuple[str, Any]:
    kind = chr(r.u8())
    if kind == "Y":
        return kind, r.i16()
    if kind == "C":
        return kind, bool(r.u8())
    if kind == "I":
        return kind, r.i32()
    if kind == "F":
        return kind, r.f32()
    if kind == "D":
        return kind, r.f64()
    if kind == "L":
        return kind, r.i64()
    if kind in ("S", "R"):
        return kind, r.read(r.u32())
    if kind in ("f", "d", "l", "i", "b"):
        count, encoding, clen = r.u32(), r.u32(), r.u32()
        raw = r.read(clen)
        if encoding == 1:
            raw = zlib.decompress(raw)
        fmt = {"f": "f", "d": "d", "l": "q", "i": "i", "b": "b"}[kind]
        size = {"f": 4, "d": 8, "l": 8, "i": 4, "b": 1}[kind]
        return kind, list(struct.unpack("<" + fmt * count, raw[: count * size]))
    raise ValueError(f"Unbekannter FBX-Property-Typ {kind!r} bei Offset {r.p - 1}.")


def _read_node(r: _Reader, version: int) -> _Node:
    if version >= 7500:
        end, nprops, _plen, nlen = r.u64(), r.u64(), r.u64(), r.u8()
        null_len = 25
    else:
        end, nprops, _plen, nlen = r.u32(), r.u32(), r.u32(), r.u8()
        null_len = 13
    name = r.read(nlen).decode("latin1")
    props = [_read_prop(r) for _ in range(nprops)]
    children: list[_Node] = []
    while r.p < end:
        if all(b == 0 for b in r.d[r.p : r.p + null_len]):
            r.p += null_len
            break
        children.append(_read_node(r, version))
    r.p = end
    return _Node(name, props, children)


def _parse_fbx(path: Path) -> list[_Node]:
    data = Path(path).read_bytes()
    if not data.startswith(FBX_MAGIC):
        raise ValueError(f"Keine binaere FBX-Datei: {path}")
    version = struct.unpack_from("<I", data, len(FBX_MAGIC))[0]
    r = _Reader(data)
    r.p = len(FBX_MAGIC) + 4
    nodes: list[_Node] = []
    null_len = 25 if version >= 7500 else 13
    while r.p < len(data):
        if all(b == 0 for b in data[r.p : r.p + null_len]):
            break
        nodes.append(_read_node(r, version))
    return nodes


def _local_translation(model: _Node) -> tuple[float, float, float]:
    p70 = model.child("Properties70")
    if p70 is None:
        return (0.0, 0.0, 0.0)
    for prop in p70.children:
        if prop.name != "P":
            continue
        values = prop.values()
        if values and _clean_fbx_name(values[0]) == "Lcl Translation":
            nums = [float(v) for v in values[4:] if isinstance(v, (int, float))]
            if len(nums) >= 3:
                return (nums[0], nums[1], nums[2])
    return (0.0, 0.0, 0.0)


def _polygon_faces(indices: list[int]) -> list[list[int]]:
    faces: list[list[int]] = []
    current: list[int] = []
    for idx in indices:
        if idx < 0:
            current.append(-idx - 1)
            faces.append(current)
            current = []
        else:
            current.append(idx)
    return [f for f in faces if len(f) >= 3]


def extract_from_fbx(fbx_path: Path | None = None) -> dict[str, Any]:
    """Extrahiert die 15 ``*_Geo``-Meshes inkl. UVs nach Blender-Koordinaten."""
    fbx_path = Path(fbx_path or SOURCE_FBX_PATH)
    top = _parse_fbx(fbx_path)
    objects = next(n for n in top if n.name == "Objects")
    connections = next(n for n in top if n.name == "Connections")

    models: dict[int, tuple[str, _Node]] = {}
    geometries: dict[int, _Node] = {}
    for node in objects.children:
        if node.name == "Model":
            models[int(node.props[0][1])] = (_clean_fbx_name(node.props[1][1]), node)
        elif node.name == "Geometry":
            geometries[int(node.props[0][1])] = node

    parent: dict[int, int] = {}
    geometry_owner: dict[int, str] = {}
    for conn in connections.children:
        vals = conn.values()
        if len(vals) < 3 or vals[0] != b"OO":
            continue
        child, par = int(vals[1]), int(vals[2])
        if child in models and par in models:
            parent[child] = par
        if child in geometries and par in models:
            owner = models[par][0]
            if owner.endswith("_Geo"):
                geometry_owner[child] = owner[: -len("_Geo")]

    def world_translation(model_id: int) -> tuple[float, float, float]:
        total = [0.0, 0.0, 0.0]
        seen: set[int] = set()
        current: int | None = model_id
        while current is not None and current in models and current not in seen:
            seen.add(current)
            tx, ty, tz = _local_translation(models[current][1])
            total[0] += tx
            total[1] += ty
            total[2] += tz
            current = parent.get(current)
        return (total[0], total[1], total[2])

    model_id_by_name = {name: mid for mid, (name, _node) in models.items()}
    parts_raw: dict[str, dict[str, Any]] = {}
    all_z: list[float] = []

    for geom_id, part_name in geometry_owner.items():
        if part_name not in PART_COLOR_KEYS:
            continue
        geom = geometries[geom_id]
        verts_node = geom.child("Vertices")
        poly_node = geom.child("PolygonVertexIndex")
        uv_node = geom.child("LayerElementUV")
        if verts_node is None or poly_node is None or uv_node is None:
            continue
        verts = [float(v) for v in verts_node.props[0][1]]
        poly_indices = [int(i) for i in poly_node.props[0][1]]
        uv_flat = [float(v) for v in uv_node.child("UV").props[0][1]]
        uv_index = [int(i) for i in uv_node.child("UVIndex").props[0][1]]
        faces = _polygon_faces(poly_indices)
        corner_count = sum(len(f) for f in faces)
        if corner_count != len(uv_index):
            raise RuntimeError(f"{part_name}: UVIndex passt nicht zur Polygonzahl.")

        mid = model_id_by_name.get(f"{part_name}_Geo")
        tx, ty, tz = world_translation(mid) if mid is not None else (0.0, 0.0, 0.0)
        converted: list[list[float]] = []
        for i in range(0, len(verts), 3):
            x, y, z = verts[i], verts[i + 1], verts[i + 2]
            # Welttranslation in FBX-Koordinaten, dann Achsenwechsel nach Blender.
            converted.append([x + tx, -(z + tz), y + ty])
            all_z.append(y + ty)

        uvs: list[list[float]] = []
        for corner, uv_idx in enumerate(uv_index):
            if 2 * uv_idx + 1 >= len(uv_flat):
                uvs.append([0.0, 0.0])
            else:
                uvs.append([uv_flat[2 * uv_idx], uv_flat[2 * uv_idx + 1]])

        parts_raw[part_name] = {
            "vertices": converted,
            "faces": faces,
            "uvs": uvs,
            "color_key": PART_COLOR_KEYS[part_name],
        }

    missing = [name for name in EXPECTED_PARTS if name not in parts_raw]
    if missing:
        raise RuntimeError(f"FBX enthaelt nicht alle R15-Teile, es fehlen: {', '.join(missing)}.")

    # Alle Teile gemeinsam auf den Boden (z=0) legen, damit die Kamera-
    # Bounding-Box und die Abstandsrechnung stabil bleiben.
    min_z = min(all_z) if all_z else 0.0
    for part in parts_raw.values():
        for vertex in part["vertices"]:
            vertex[2] -= min_z

    attachments_raw: dict[str, list[float]] = {}
    for roblox_name, fbx_name in ATTACHMENT_MODEL_NAMES.items():
        attachment_id = model_id_by_name.get(fbx_name)
        if attachment_id is None:
            continue
        tx, ty, tz = world_translation(attachment_id)
        attachments_raw[roblox_name] = [tx, -tz, ty - min_z]

    missing_attachments = [name for name in ATTACHMENT_MODEL_NAMES if name not in attachments_raw]
    if missing_attachments:
        raise RuntimeError(
            "FBX enthaelt nicht alle R15-Attachments, es fehlen: "
            + ", ".join(missing_attachments)
        )

    return {
        "source": "Roblox/avatar@main:Legacy/rigs/R15_Block.fbx",
        "source_url": "https://github.com/Roblox/avatar/blob/main/Legacy/rigs/R15_Block.fbx",
        "axis_conversion": [1, 0, 0, 0, 0, -1, 0, 1, 0],
        "ground_offset_z": min_z,
        "parts": parts_raw,
        "attachments": attachments_raw,
    }


def main() -> None:
    data = extract_from_fbx()
    RIG_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(RIG_DATA_PATH, "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
    print(
        f"Geschrieben: {RIG_DATA_PATH} ({RIG_DATA_PATH.stat().st_size} Bytes, "
        f"{len(data['parts'])} R15-Teile)"
    )


if __name__ == "__main__":
    main()
