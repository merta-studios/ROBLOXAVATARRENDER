"""Tests fuer das Auslesen der Roblox-Emote-Animationen.

Deckt beide von Roblox gelieferten Formate ab:

* ``KeyframeSequence`` (klassisch) mit verschachteltem Pose-Baum,
* ``CurveAnimation`` (neu) mit FloatCurve-Bloeb ("ValuesAndTimes").

Das Binaerformat der FloatCurve wird hier mit einem synthetischen Blob
getestet, der byteweise dem von Roblox gelieferten Format entspricht
(verifiziert an "Emote_Hello" und "Imported Animation Clip").
"""

import struct
import unittest

import numpy as np

from scripts import rbx_animation as ra
from scripts.rbx_formats import RbxNode


def build_float_curve(keys, ticks, interpolation=1, tangents=None):
    """FloatCurve-Blob im Roblox-Format ("ValuesAndTimes") bauen."""
    blob = bytearray()
    blob += struct.pack("<II", 2, len(keys))
    for index, value in enumerate(keys):
        left, right = tangents[index] if tangents else (0.0, 0.0)
        blob += struct.pack("<BBfff", interpolation, 0, value, left, right)
    blob += struct.pack("<II", 1, len(ticks))
    for tick in ticks:
        blob += struct.pack("<I", tick)
    return bytes(blob)


def float_curve_node(name, keys, ticks, interpolation=1):
    node = RbxNode("FloatCurve", name)
    node.raw_props["ValuesAndTimes"] = build_float_curve(keys, ticks, interpolation)
    return node


def pose_node(name, cframe):
    node = RbxNode("Pose", name)
    node.props["CFrame"] = cframe
    return node


def identity_cframe():
    return (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


class FloatCurveTests(unittest.TestCase):
    def test_decodes_values_times_and_tangents(self):
        blob = build_float_curve(
            [1.0, 2.0, 3.0],
            [0, 1200, 2400],
            tangents=[(0.0, 0.5), (0.25, 0.75), (0.0, 0.0)],
        )
        keys = ra.decode_float_curve(blob)
        self.assertEqual(len(keys), 3)
        self.assertAlmostEqual(keys[0].time, 0.0)
        self.assertAlmostEqual(keys[1].time, 0.5)  # 1200 Ticks / 2400 pro Sekunde
        self.assertAlmostEqual(keys[2].time, 1.0)
        self.assertAlmostEqual(keys[1].value, 2.0)
        self.assertAlmostEqual(keys[1].left_tangent, 0.25)
        self.assertAlmostEqual(keys[1].right_tangent, 0.75)

    def test_constant_curve(self):
        blob = struct.pack("<IIIf", 2, 0, 1, 0.0)
        keys = ra.decode_float_curve(blob)
        self.assertEqual(len(keys), 1)
        self.assertAlmostEqual(keys[0].value, 0.0)

    def test_broken_blob_returns_empty(self):
        self.assertEqual(ra.decode_float_curve(b""), [])
        self.assertEqual(ra.decode_float_curve(b"\x02\x00\x00\x00"), [])

    def test_linear_and_cubic_sampling(self):
        keys = [
            ra.CurveKey(time=0.0, value=0.0, interpolation=ra.INTERP_LINEAR),
            ra.CurveKey(time=1.0, value=10.0, interpolation=ra.INTERP_LINEAR),
        ]
        self.assertAlmostEqual(ra.sample_curve(keys, 0.5), 5.0)
        self.assertAlmostEqual(ra.sample_curve(keys, -1.0), 0.0)
        self.assertAlmostEqual(ra.sample_curve(keys, 2.0), 10.0)

        constant = [
            ra.CurveKey(time=0.0, value=1.0, interpolation=ra.INTERP_CONSTANT),
            ra.CurveKey(time=1.0, value=2.0, interpolation=ra.INTERP_CONSTANT),
        ]
        self.assertAlmostEqual(ra.sample_curve(constant, 0.9), 1.0)


class EulerTests(unittest.TestCase):
    def test_xyz_order_matches_roblox_curve_animation(self):
        # Roblox Enum.RotationOrder 0 ("XYZ"): R = Rx @ Ry @ Rz
        rx, ry, rz = 0.3, -0.7, 1.1
        cx, sx = np.cos(rx), np.sin(rx)
        cy, sy = np.cos(ry), np.sin(ry)
        cz, sz = np.cos(rz), np.sin(rz)
        expected = np.array(
            [
                [cy * cz, -cy * sz, sy],
                [sx * sy * cz + cx * sz, -sx * sy * sz + cx * cz, -sx * cy],
                [-cx * sy * cz + sx * sz, cx * sy * sz + sx * cz, cx * cy],
            ]
        )
        got = ra.euler_matrix(rx, ry, rz, 0)
        np.testing.assert_allclose(got, expected, atol=1e-9)

    def test_pure_axis_rotations(self):
        np.testing.assert_allclose(
            ra.euler_matrix(0.0, 0.0, np.pi / 2.0), ra._axis_rotation("Z", np.pi / 2.0),
            atol=1e-12,
        )
        np.testing.assert_allclose(
            ra.euler_matrix(np.pi / 2.0, 0.0, 0.0), ra._axis_rotation("X", np.pi / 2.0),
            atol=1e-12,
        )


class KeyframeSequenceTests(unittest.TestCase):
    def build_sequence(self):
        sequence = RbxNode("KeyframeSequence", "Emote_Test")
        keyframe = RbxNode("Keyframe", "Keyframe")
        keyframe.props["Time"] = 0.0
        root = pose_node("HumanoidRootPart", identity_cframe())
        keyframe.children.append(root)
        torso = pose_node("LowerTorso", identity_cframe())
        root.children.append(torso)
        upper = pose_node("UpperTorso", identity_cframe())
        torso.children.append(upper)
        arm = pose_node(
            "LeftUpperArm",
            (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
        )
        upper.children.append(arm)
        sequence.children.append(keyframe)
        return sequence

    def test_reads_pose_hierarchy(self):
        clip = ra.parse_keyframe_sequence(self.build_sequence())
        self.assertEqual(clip.source_format, "KeyframeSequence")
        self.assertEqual(clip.parents["LowerTorso"], "HumanoidRootPart")
        self.assertEqual(clip.parents["UpperTorso"], "LowerTorso")
        self.assertEqual(clip.parents["LeftUpperArm"], "UpperTorso")
        self.assertEqual(clip.bone_order()[0], "HumanoidRootPart")
        self.assertAlmostEqual(clip.preview_time(), 0.0)

    def test_reads_rotation_of_pose(self):
        # 90 Grad um Z
        cframe = (0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        sequence = RbxNode("KeyframeSequence", "Emote_Test")
        keyframe = RbxNode("Keyframe", "Keyframe")
        keyframe.props["Time"] = 0.0
        root = pose_node("HumanoidRootPart", identity_cframe())
        keyframe.children.append(root)
        arm = pose_node("LeftUpperArm", cframe)
        root.children.append(arm)
        sequence.children.append(keyframe)

        clip = ra.parse_keyframe_sequence(sequence)
        rotations = clip.local_rotations(0.0)
        self.assertAlmostEqual(ra.rotation_angle_deg(rotations["LeftUpperArm"]), 90.0, places=3)


class CurveAnimationTests(unittest.TestCase):
    def build_animation(self):
        animation = RbxNode("CurveAnimation", "Imported Animation Clip")
        root = RbxNode("Folder", "HumanoidRootPart")
        animation.children.append(root)
        torso = RbxNode("Folder", "LowerTorso")
        root.children.append(torso)
        rotation = RbxNode("EulerRotationCurve", "Rotation")
        rotation.props["RotationOrder"] = 0
        rotation.children.append(float_curve_node("X", [0.0, 1.0], [0, 2400]))
        rotation.children.append(float_curve_node("Y", [0.0], [0]))
        rotation.children.append(float_curve_node("Z", [0.0], [0]))
        torso.children.append(rotation)
        position = RbxNode("Vector3Curve", "Position")
        position.children.append(float_curve_node("X", [0.0], [0]))
        position.children.append(float_curve_node("Y", [0.0], [0]))
        position.children.append(float_curve_node("Z", [0.0], [0]))
        torso.children.append(position)
        return animation

    def test_reads_bones_and_curves(self):
        clip = ra.parse_curve_animation(self.build_animation())
        self.assertEqual(clip.source_format, "CurveAnimation")
        self.assertEqual(clip.parents["LowerTorso"], "HumanoidRootPart")
        self.assertEqual(clip.times, [0.0, 1.0])
        rotations = clip.local_rotations(0.0)
        self.assertAlmostEqual(ra.rotation_angle_deg(rotations["LowerTorso"]), 0.0)
        rotations = clip.local_rotations(1.0)
        self.assertAlmostEqual(ra.rotation_angle_deg(rotations["LowerTorso"]), 57.2958, places=3)


class ForwardKinematicsTests(unittest.TestCase):
    """Die Pose wird wie bei Roblox ueber die Gelenk-Kette berechnet."""

    def test_world_rotation_composes_with_parent(self):
        from scripts.classic_render import _emote_world_transforms

        clip = ra.AnimationClip(source_format="Test", tracks={})
        parent = ra.BoneTrack(name="HumanoidRootPart", parent=None)
        parent.rotation_curves = {
            "X": [ra.CurveKey(time=0.0, value=0.0)],
            "Y": [ra.CurveKey(time=0.0, value=0.0)],
            "Z": [ra.CurveKey(time=0.0, value=0.0)],
        }
        child = ra.BoneTrack(name="UpperTorso", parent="HumanoidRootPart")
        child.rotation_curves = {
            "X": [ra.CurveKey(time=0.0, value=0.0)],
            "Y": [ra.CurveKey(time=0.0, value=0.0)],
            "Z": [ra.CurveKey(time=0.0, value=np.pi / 2.0)],
        }
        clip.tracks = {"HumanoidRootPart": parent, "UpperTorso": child}

        world_rot, world_joint = _emote_world_transforms(clip, 0.0)
        self.assertAlmostEqual(
            ra.rotation_angle_deg(world_rot["UpperTorso"]), 90.0, places=3
        )
        # Das Gelenk der Taille bleibt am Root, wandert aber nicht.
        np.testing.assert_allclose(world_joint["UpperTorso"], [0.0, 0.4, 0.0], atol=1e-9)

    def test_child_joint_follows_parent_rotation(self):
        from scripts.classic_render import _emote_world_transforms

        clip = ra.AnimationClip(source_format="Test", tracks={})
        parent = ra.BoneTrack(name="HumanoidRootPart", parent=None)
        parent.rotation_curves = {
            "X": [ra.CurveKey(time=0.0, value=0.0)],
            "Y": [ra.CurveKey(time=0.0, value=0.0)],
            "Z": [ra.CurveKey(time=0.0, value=np.pi / 2.0)],
        }
        child = ra.BoneTrack(name="LeftUpperArm", parent="HumanoidRootPart")
        child.rotation_curves = {
            "X": [ra.CurveKey(time=0.0, value=0.0)],
            "Y": [ra.CurveKey(time=0.0, value=0.0)],
            "Z": [ra.CurveKey(time=0.0, value=0.0)],
        }
        clip.tracks = {"HumanoidRootPart": parent, "LeftUpperArm": child}

        _world_rot, world_joint = _emote_world_transforms(clip, 0.0)
        # Die Schulter (-1, 1.763, 0) wird um die Root-Achse (Z) gedreht:
        # x und y tauschen die Rollen.
        np.testing.assert_allclose(
            world_joint["LeftUpperArm"], [-1.763, -1.0, 0.0], atol=1e-9
        )


class BoneMappingTests(unittest.TestCase):
    def test_r15_and_r6_names(self):
        self.assertEqual(ra.bone_to_r15("LeftUpperArm"), "LeftUpperArm")
        self.assertEqual(ra.bone_to_r15("Left Arm"), "LeftUpperArm")
        self.assertEqual(ra.bone_to_r15("Torso"), "UpperTorso")
        self.assertIsNone(ra.bone_to_r15("Klingon"))

    def test_r6_pivots_use_r15_joints(self):
        self.assertEqual(
            ra.R6_BONE_PIVOT_ATTACHMENTS["Left Arm"], "LeftShoulderRigAttachment"
        )
        self.assertEqual(ra.R6_BONE_PIVOT_ATTACHMENTS["Head"], "NeckRigAttachment")


if __name__ == "__main__":
    unittest.main()
