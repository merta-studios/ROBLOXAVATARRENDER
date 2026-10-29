"""Roblox-Emote-Animationen auslesen (KeyframeSequence und CurveAnimation).

Roblox liefert Emote-Animationen in zwei Formaten:

* **KeyframeSequence** (klassisch, z. B. "Emote_Hello"): je Keyframe ein
  verschachtelter ``Pose``-Baum. Die ``CFrame`` einer Pose beschreibt das
  Gelenk **relativ zum Elterngelenk** (lokal) - genau so, wie Roblox es
  im Animation-Editor anzeigt.
* **CurveAnimation** (neu, UGC-Emotes wie "Imported Animation Clip"): je Bone
  ein ``Folder`` mit einer ``Vector3Curve "Position"`` und einer
  ``EulerRotationCurve "Rotation"``. Die eigentlichen Werte stecken als
  Binaerblob in den ``FloatCurve``-Kindern (``ValuesAndTimes``).

Format der FloatCurve (``ValuesAndTimes``), verifiziert gegen echte
Roblox-Assets (KeyframeSequence "Emote_Hello" mit 43 Keyframes und
CurveAnimation "Imported Animation Clip" mit 129 Keys):

======================  ====================================================
``u32``                Version (2)
``u32``                Anzahl der Keys
je Key (14 Byte)       ``u8`` Interpolationsart (0 konstant, 1 linear,
                       2 cubic), ``u8`` reserviert, ``f32`` Wert,
                       ``f32`` linke Tangente, ``f32`` rechte Tangente
                       (Tangenten in Werten pro Sekunde)
``u32``                Zeitformat (1)
``u32``                Anzahl der Zeitpunkte
je Zeitpunkt (4 Byte)  ``u32`` Tick - 2400 Ticks entsprechen einer Sekunde
======================  ====================================================

Die Roblox-Zeiten laufen in Ticks; daraus wird die Zeit in Sekunden
berechnet. Getestet an beiden Formaten: die dekodierten Zeiten ergeben die
bekannten Emote-Abstaende (z. B. 0.042 s = 1/24 s bei "Emote_Hello") und
die Werte laufen in plausiblen Groessenordnungen.

Die Rig-Hierarchie liefert die Elternbeziehung jedes Bones: bei der
KeyframeSequence der verschachtelte Pose-Baum, bei der CurveAnimation die
Ordner-Struktur. Beide beschreiben dieselbe R15-Kette
(HumanoidRootPart -> LowerTorso -> UpperTorso -> Arme/Head, LowerTorso ->
Beine).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from scripts.rbx_formats import RbxNode

# Roblox rechnet Animationszeiten in Ticks (1/2400 Sekunde).
TICKS_PER_SECOND = 2400.0

# Interpolationsarten einer FloatCurve.
INTERP_CONSTANT = 0
INTERP_LINEAR = 1
INTERP_CUBIC = 2

# RotationOrder der EulerRotationCurve (identisch zu Enum.RotationOrder).
ROTATION_ORDERS: dict[int, tuple[str, str, str]] = {
    0: ("X", "Y", "Z"),
    1: ("X", "Z", "Y"),
    2: ("Y", "Z", "X"),
    3: ("Y", "X", "Z"),
    4: ("Z", "X", "Y"),
    5: ("Z", "Y", "X"),
}

# Rig-Hierarchie des Standard-R15: Bone -> Eltern-Bone.
R15_BONE_PARENTS: dict[str, str | None] = {
    "HumanoidRootPart": None,
    "LowerTorso": "HumanoidRootPart",
    "UpperTorso": "LowerTorso",
    "Head": "UpperTorso",
    "LeftUpperArm": "UpperTorso",
    "LeftLowerArm": "LeftUpperArm",
    "LeftHand": "LeftLowerArm",
    "RightUpperArm": "UpperTorso",
    "RightLowerArm": "RightUpperArm",
    "RightHand": "RightLowerArm",
    "LeftUpperLeg": "LowerTorso",
    "LeftLowerLeg": "LeftUpperLeg",
    "LeftFoot": "LeftLowerLeg",
    "RightUpperLeg": "LowerTorso",
    "RightLowerLeg": "RightUpperLeg",
    "RightFoot": "RightLowerLeg",
}

# R6-Begriffe auf die entsprechenden R15-Bones abbilden (Roblox retargetet
# R6-Emotes auf das R15-Rig, wenn ein R15-Avatar sie abspielt).
R6_BONE_TO_R15: dict[str, str] = {
    "Head": "Head",
    "Torso": "UpperTorso",
    "Left Arm": "LeftUpperArm",
    "Right Arm": "RightUpperArm",
    "Left Leg": "LeftUpperLeg",
    "Right Leg": "RightUpperLeg",
}

# Gelenk (Drehpunkt) der R6-Begriffe im R15-Rig. Roblox haengt die R6-Posen
# an denselben Gelenken ein wie die R15-Posen: R6-Schulter = R15-Schulter,
# R6-Huefte = R15-Huefte, R6-Hals = R15-Hals, R6-Torso = R15-Root.
R6_BONE_PIVOT_ATTACHMENTS: dict[str, str] = {
    "Torso": "RootRigAttachment",
    "Head": "NeckRigAttachment",
    "Left Arm": "LeftShoulderRigAttachment",
    "Right Arm": "RightShoulderRigAttachment",
    "Left Leg": "LeftHipRigAttachment",
    "Right Leg": "RightHipRigAttachment",
}


@dataclass(frozen=True)
class CurveKey:
    """Ein Key einer FloatCurve."""

    time: float
    value: float
    interpolation: int = INTERP_LINEAR
    left_tangent: float = 0.0
    right_tangent: float = 0.0


@dataclass
class BoneTrack:
    """Ein Bone der Animation mit seiner lokalen Rotation."""

    name: str
    parent: str | None = None
    # Legacy (KeyframeSequence): (Zeit, lokale Rotationsmatrix)
    frames: list[tuple[float, np.ndarray]] = field(default_factory=list)
    # CurveAnimation: Euler-Kurven je Achse
    rotation_curves: dict[str, list[CurveKey]] = field(default_factory=dict)
    # CurveAnimation: Positionskurven je Achse (Offset zur Ruhelage)
    position_curves: dict[str, list[CurveKey]] = field(default_factory=dict)
    rotation_order: int = 0


@dataclass
class AnimationClip:
    """Eine gelesene Animation mit den lokalen Rotationen je Bone."""

    source_format: str
    tracks: dict[str, BoneTrack] = field(default_factory=dict)
    times: list[float] = field(default_factory=list)

    # ------------------------------------------------------------------
    @property
    def parents(self) -> dict[str, str | None]:
        return {name: track.parent for name, track in self.tracks.items()}

    def bone_order(self) -> list[str]:
        """Bones in Reihenfolge der Eltern -> Kinder (topologisch)."""
        ordered: list[str] = []
        remaining = dict(self.tracks)
        while remaining:
            progressed = False
            for name in list(remaining):
                parent = remaining[name].parent
                if parent is None or parent not in remaining or parent in ordered:
                    ordered.append(name)
                    del remaining[name]
                    progressed = True
            if not progressed:  # Zyklus defensiv abbrechen
                ordered.extend(remaining)
                break
        return ordered

    def local_rotations(self, time: float) -> dict[str, np.ndarray]:
        """Lokale Rotationen aller Bones zum Zeitpunkt ``time``."""
        out: dict[str, np.ndarray] = {}
        for name, track in self.tracks.items():
            out[name] = _track_rotation(track, time)
        return out

    def preview_time(self) -> float:
        """Der Vorschau-Moment der Animation: die erste Keyframe.

        Roblox nutzt fuer UGC-Emotes die erste Keyframe als Vorsau-Bild
        (Roblox-Forum: "UGC emotes use the first keyframe for the
        thumbnail"). Das entspricht dem fruehesten Zeitpunkt der Animation.
        """
        if not self.times:
            return 0.0
        return float(min(self.times))


def _track_rotation(track: BoneTrack, time: float) -> np.ndarray:
    """Lokale Rotation eines Bones zum Zeitpunkt ``time``."""
    if track.frames:
        return _sample_frames(track.frames, time)
    if not track.rotation_curves:
        return np.eye(3, dtype=np.float64)
    angles = {
        axis: sample_curve(keys, time)
        for axis, keys in track.rotation_curves.items()
    }
    return euler_matrix(
        angles.get("X", 0.0), angles.get("Y", 0.0), angles.get("Z", 0.0),
        track.rotation_order,
    )


def _sample_frames(frames: list[tuple[float, np.ndarray]], time: float) -> np.ndarray:
    """Rotation zwischen zwei Keyframes interpolieren (slerp)."""
    if not frames:
        return np.eye(3, dtype=np.float64)
    if time <= frames[0][0]:
        return frames[0][1]
    if time >= frames[-1][0]:
        return frames[-1][1]
    for index in range(1, len(frames)):
        if frames[index][0] >= time:
            t0, r0 = frames[index - 1]
            t1, r1 = frames[index]
            if t1 <= t0:
                return r1
            blend = (time - t0) / (t1 - t0)
            return _slerp(r0, r1, blend)
    return frames[-1][1]


def _slerp(r0: np.ndarray, r1: np.ndarray, blend: float) -> np.ndarray:
    """Rotationen interpolieren ( Rodrigues / Quaternion )."""
    q0 = _matrix_to_quaternion(r0)
    q1 = _matrix_to_quaternion(r1)
    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1 = -q1
        dot = -dot
    if dot > 0.9995:
        q = q0 + blend * (q1 - q0)
        norm = float(np.linalg.norm(q))
        if norm <= 0.0:
            return r0
        q = q / norm
    else:
        theta = math.acos(max(-1.0, min(1.0, dot)))
        sin_theta = math.sin(theta)
        if abs(sin_theta) < 1e-9:
            q = q0
        else:
            w0 = math.sin((1.0 - blend) * theta) / sin_theta
            w1 = math.sin(blend * theta) / sin_theta
            q = w0 * q0 + w1 * q1
    return _quaternion_to_matrix(q)


def _matrix_to_quaternion(m: np.ndarray) -> np.ndarray:
    """3x3-Rotationsmatrix -> Quaternion (x, y, z, w)."""
    trace = float(m[0, 0] + m[1, 1] + m[2, 2])
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    q = np.array([x, y, z, w], dtype=np.float64)
    norm = float(np.linalg.norm(q))
    return q / norm if norm > 0.0 else np.array([0.0, 0.0, 0.0, 1.0])


def _quaternion_to_matrix(q: np.ndarray) -> np.ndarray:
    """Quaternion (x, y, z, w) -> 3x3-Rotationsmatrix."""
    x, y, z, w = (float(v) for v in q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def euler_matrix(x: float, y: float, z: float, order: int = 0) -> np.ndarray:
    """Euler-Winkel (Radiant) -> 3x3-Rotationsmatrix.

    Reihenfolge wie in Roblox' ``Enum.RotationOrder``: die Achsen werden in
    der angegebenen Reihenfolge multipliziert, fuer ``XYZ`` (Order 0, der von
    Roblox gelieferte Standard) also ``Rx @ Ry @ Rz``. Verifiziert an zwei
    unabhaengigen Readern des Roblox-Formats (RBX_Toolbox und Fleasion), die
    beide dieselbe Matrix fuer ``XYZ`` ergeben.
    """
    axes = ROTATION_ORDERS.get(int(order), ROTATION_ORDERS[0])
    angles = {"X": float(x), "Y": float(y), "Z": float(z)}
    matrix = np.eye(3, dtype=np.float64)
    for axis in reversed(axes):
        matrix = _axis_rotation(axis, angles[axis]) @ matrix
    return matrix


def _axis_rotation(axis: str, angle: float) -> np.ndarray:
    c = math.cos(angle)
    s = math.sin(angle)
    if axis == "X":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)
    if axis == "Y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def decode_float_curve(raw: bytes | bytearray | None) -> list[CurveKey]:
    """FloatCurve-Blob (``ValuesAndTimes``) in Keys zerlegen."""
    if not raw or len(raw) < 8:
        return []
    data = bytes(raw)
    version, count = struct.unpack_from("<II", data, 0)
    if version == 0 and count == 0:
        return []
    if count == 0:
        # Konstante Kurve: u32 interpolation, f32 wert
        interpolation = struct.unpack_from("<I", data, 8)[0]
        value = struct.unpack_from("<f", data, 12)[0]
        return [CurveKey(time=0.0, value=value, interpolation=interpolation)]
    if len(data) < 8 + count * 14:
        return []
    values: list[tuple[int, float, float, float]] = []
    for index in range(count):
        offset = 8 + index * 14
        interpolation = data[offset]
        value = struct.unpack_from("<f", data, offset + 2)[0]
        left = struct.unpack_from("<f", data, offset + 6)[0]
        right = struct.unpack_from("<f", data, offset + 10)[0]
        values.append((interpolation, value, left, right))
    times = _decode_curve_times(data, count)
    keys: list[CurveKey] = []
    for index in range(count):
        time = times[index] if index < len(times) else (times[-1] if times else 0.0)
        interpolation, value, left, right = values[index]
        keys.append(
            CurveKey(
                time=time,
                value=value,
                interpolation=interpolation,
                left_tangent=left,
                right_tangent=right,
            )
        )
    return keys


def _decode_curve_times(data: bytes, count: int) -> list[float]:
    """Zeitpunkte (Ticks -> Sekunden) aus dem FloatCurve-Blob lesen."""
    base = 8 + count * 14
    if base + 8 > len(data):
        return []
    _time_format, time_count = struct.unpack_from("<II", data, base)
    offset = base + 8
    times: list[float] = []
    for _ in range(min(time_count, count)):
        if offset + 4 > len(data):
            break
        tick = struct.unpack_from("<I", data, offset)[0]
        times.append(tick / TICKS_PER_SECOND)
        offset += 4
    return times


def sample_curve(keys: list[CurveKey], time: float) -> float:
    """FloatCurve zum Zeitpunkt ``time`` auswerten.

    Interpolationsarten: 0 = konstant (Wert bleibt stehen), 1 = linear,
    2 = cubic (Hermite mit den Tangenten in Werten pro Sekunde).
    """
    if not keys:
        return 0.0
    if time <= keys[0].time:
        return keys[0].value
    if time >= keys[-1].time:
        return keys[-1].value
    for index in range(1, len(keys)):
        if keys[index].time >= time:
            before = keys[index - 1]
            after = keys[index]
            break
    else:  # pragma: no cover - defensive
        return keys[-1].value
    duration = after.time - before.time
    if duration <= 0.0:
        return after.value
    blend = (time - before.time) / duration
    if before.interpolation == INTERP_CONSTANT:
        return before.value
    if before.interpolation == INTERP_CUBIC:
        start_slope = before.right_tangent * duration
        end_slope = after.left_tangent * duration
        blend_sq = blend * blend
        blend_cu = blend_sq * blend
        h00 = 2.0 * blend_cu - 3.0 * blend_sq + 1.0
        h10 = blend_cu - 2.0 * blend_sq + blend
        h01 = -2.0 * blend_cu + 3.0 * blend_sq
        h11 = blend_cu - blend_sq
        return (
            h00 * before.value
            + h10 * start_slope
            + h01 * after.value
            + h11 * end_slope
        )
    return before.value * (1.0 - blend) + after.value * blend


def _rotation_from_cframe(cframe: Iterable[float]) -> np.ndarray:
    """Rotationsanteil eines Roblox-CFrame (12 Werte) als 3x3-Matrix."""
    values = [float(v) for v in cframe]
    if len(values) < 12:
        return np.eye(3, dtype=np.float64)
    return np.array(
        [
            [values[3], values[4], values[5]],
            [values[6], values[7], values[8]],
            [values[9], values[10], values[11]],
        ],
        dtype=np.float64,
    )


def parse_keyframe_sequence(node: RbxNode) -> AnimationClip:
    """Legacy-``KeyframeSequence`` lesen.

    Jeder Keyframe enthaelt einen verschachtelten ``Pose``-Baum. Die
    ``CFrame`` einer Pose ist die Transformation des Gelenks relativ zum
    Elterngelenk; daraus wird die lokale Rotation jedes Bones gewonnen.
    """
    tracks: dict[str, BoneTrack] = {}
    times: list[float] = []
    for keyframe in node.children:
        if keyframe.class_name != "Keyframe":
            continue
        time = float(keyframe.prop("Time", default=0.0) or 0.0)
        times.append(time)
        _collect_poses(keyframe, None, tracks, time)
    for track in tracks.values():
        track.frames.sort(key=lambda item: item[0])
    return AnimationClip(
        source_format="KeyframeSequence",
        tracks=tracks,
        times=sorted(times),
    )


def _collect_poses(
    node: RbxNode,
    parent: str | None,
    tracks: dict[str, BoneTrack],
    time: float,
) -> None:
    """Pose-Baum eines Keyframes rekursiv einsammeln."""
    for child in node.children:
        if child.class_name != "Pose":
            continue
        cframe = child.prop("CFrame")
        if cframe is not None:
            track = tracks.get(child.name)
            if track is None:
                track = BoneTrack(name=child.name, parent=parent)
                tracks[child.name] = track
            elif track.parent is None and parent is not None:
                track.parent = parent
            track.frames.append((time, _rotation_from_cframe(cframe)))
        _collect_poses(child, child.name, tracks, time)


def parse_curve_animation(node: RbxNode) -> AnimationClip:
    """Neue ``CurveAnimation`` lesen (Ordner je Bone + FloatCurves)."""
    tracks: dict[str, BoneTrack] = {}
    times: set[float] = set()
    for child in node.children:
        if child.class_name != "Folder":
            continue
        _collect_bone(child, None, tracks, times)
    for track in tracks.values():
        for keys in track.rotation_curves.values():
            times.update(key.time for key in keys)
    return AnimationClip(
        source_format="CurveAnimation",
        tracks=tracks,
        times=sorted(times),
    )


def _collect_bone(
    folder: RbxNode,
    parent: str | None,
    tracks: dict[str, BoneTrack],
    times: set[float],
) -> None:
    """Bone-Ordner auslesen und in Unterordner rekursieren."""
    track = BoneTrack(name=folder.name, parent=parent)
    for child in folder.children:
        if child.class_name == "Vector3Curve" and child.name == "Position":
            for curve in child.children:
                if curve.class_name != "FloatCurve":
                    continue
                keys = decode_float_curve(curve.raw_props.get("ValuesAndTimes"))
                if keys:
                    track.position_curves[curve.name] = keys
                    times.update(key.time for key in keys)
        elif child.class_name == "EulerRotationCurve" and child.name == "Rotation":
            try:
                track.rotation_order = int(child.prop("RotationOrder", default=0) or 0)
            except (TypeError, ValueError):
                track.rotation_order = 0
            for curve in child.children:
                if curve.class_name != "FloatCurve":
                    continue
                keys = decode_float_curve(curve.raw_props.get("ValuesAndTimes"))
                if keys:
                    track.rotation_curves[curve.name] = keys
                    times.update(key.time for key in keys)
    # Auch Bones ohne Kurven (z. B. der Root) werden angelegt, damit die
    # Hierarchie vollstaendig bleibt.
    tracks[folder.name] = track
    for child in folder.children:
        if child.class_name == "Folder":
            _collect_bone(child, folder.name, tracks, times)


def parse_animation_asset(roots: Iterable[RbxNode]) -> AnimationClip | None:
    """Aus den Wurzeln eines Animations-Assets die Animation heraussuchen.

    Gesucht wird in den Wurzeln und in deren Kindern: manche Emote-Assets
    enthalten die Animation direkt, andere verweisen mit ``AnimationId`` auf
    ein zweites Asset (dann liefert dieser Aufruf ``None``).
    """
    sequences = [n for n in roots if n.class_name == "KeyframeSequence"]
    if sequences:
        return parse_keyframe_sequence(sequences[0])
    curves = [n for n in roots if n.class_name == "CurveAnimation"]
    if curves:
        return parse_curve_animation(curves[0])
    for root in roots:
        for node in root.descendants():
            if node.class_name == "KeyframeSequence":
                return parse_keyframe_sequence(node)
            if node.class_name == "CurveAnimation":
                return parse_curve_animation(node)
    return None


def bone_to_r15(name: str) -> str | None:
    """Bone-Namen auf das R15-Rig abbilden (R15- und R6-Namen)."""
    if name in R15_BONE_PARENTS:
        return name
    return R6_BONE_TO_R15.get(name)


def rotation_angle_deg(matrix: np.ndarray) -> float:
    """Groesster Drehwinkel einer Rotationsmatrix (Grad)."""
    trace = float(np.trace(matrix))
    cos = max(-1.0, min(1.0, (trace - 1.0) / 2.0))
    return float(np.degrees(np.arccos(cos)))


def describe_clip(clip: AnimationClip) -> dict[str, Any]:
    """Kurzbeschreibung einer Animation (fuer Logs/Diagnose)."""
    return {
        "format": clip.source_format,
        "bones": sorted(clip.tracks),
        "times": len(clip.times),
        "preview_time": clip.preview_time(),
    }
