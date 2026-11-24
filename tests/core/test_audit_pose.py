# SPDX-License-Identifier: GPL-3.0-or-later
"""Engine-equivalent posing on synthetic hierarchies and KEYs (no Blender, no game data)."""
import math
import unittest

import numpy as np

from sith.audit.pose import (PoseLayer, blendLocalPose, jointFrames, keyFrameTimes, keyTrack,
                             normalizeAngleAcute, poseModel, posedMeshVertices, posedNodeVertices,
                             restPose, rotationMatrix, sampleTrack)
from sith.key.key import Key, Keyframe, KeyframeFlag, KeyNode
from sith.model.model3do import Mesh3do, Mesh3doNode, Mesh3doNodeType, Model3do, Model3doGeoSet
from sith.types import Vector3f


def elementary(pitch, yaw, roll):
    """Independent construction from axis rotations, for comparison with the engine formula."""
    p, y, r = (math.radians(v) for v in (pitch, yaw, roll))
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    ry = np.array([[math.cos(r), 0, math.sin(r)], [0, 1, 0], [-math.sin(r), 0, math.cos(r)]])
    rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    return rz @ rx @ ry


def chain(pivot=(0.5, 0.0, 0.0), insert=(0.0, 0.0, 0.0)):
    """root (torso) -> arm (left arm, pivot) -> hand (left hand); one vertex per mesh at the origin."""
    model = Model3do('chain')
    model.insertOffset = Vector3f(*insert)
    geoset = Model3doGeoSet()
    specs = (('root', Mesh3doNodeType.Torso, -1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
             ('arm', Mesh3doNodeType.LeftArm, 0, (1.0, 0.0, 0.0), pivot),
             ('hand', Mesh3doNodeType.LeftHand, 1, (1.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
    nodes = []
    for i, (name, ntype, parent, position, piv) in enumerate(specs):
        mesh = Mesh3do(i, name)
        mesh.vertices = [Vector3f(0.0, 0.0, 0.0)]
        geoset.meshes.append(mesh)
        node = Mesh3doNode(name)
        node.idx = i
        node.type = ntype
        node.meshIdx = i
        node.parentIdx = parent
        node.position = Vector3f(*position)
        node.rotation = Vector3f(0.0, 0.0, 0.0)
        node.pivot = Vector3f(*piv)
        nodes.append(node)
    model.geosets = [geoset]
    model.meshHierarchy = nodes
    return model


def entry(frame, pos=(0, 0, 0), rot=(0, 0, 0), dpos=(0, 0, 0), drot=(0, 0, 0), flags=KeyframeFlag.NoChange):
    k = Keyframe()
    k.frame = frame
    k.flags = flags
    k.position = Vector3f(*pos)
    k.orientation = Vector3f(*rot)
    k.deltaPosition = Vector3f(*dpos)
    k.deltaRotation = Vector3f(*drot)
    return k


def key(tracks, joints=3, frames=10, types=Mesh3doNodeType.Nothing, name='test.key'):
    """tracks: {node index: [Keyframe]} in insertion order (duplicates via a list of pairs)."""
    k = Key(name)
    k.numJoints = joints
    k.numFrames = frames
    k.fps = 15.0
    k.nodeTypes = types
    items = tracks if isinstance(tracks, list) else list(tracks.items())
    for idx, entries in items:
        node = KeyNode()
        node.idx = idx
        node.meshName = 'n%d' % idx
        node.keyframes = entries
        k.nodes.append(node)
    return k


class RotationTests(unittest.TestCase):
    def test_matches_axis_composition_and_is_orthonormal(self):
        rng = np.random.default_rng(7)
        for pyr in rng.uniform(-400.0, 400.0, size=(40, 3)):
            m = rotationMatrix(pyr)
            np.testing.assert_allclose(m, elementary(*pyr), atol=1e-12)
            np.testing.assert_allclose(m @ m.T, np.eye(3), atol=1e-12)

    def test_yaw_turns_forward_axis_left(self):
        # JK: +y forward, +z up; yaw is about +z.
        np.testing.assert_allclose(rotationMatrix((0, 90, 0)) @ [0, 1, 0], [-1, 0, 0], atol=1e-12)
        np.testing.assert_allclose(rotationMatrix((90, 0, 0)) @ [0, 1, 0], [0, 0, 1], atol=1e-12)

    def test_acute_range_keeps_plus_180(self):
        values = [180.0, -180.0, 190.0, 540.0, -190.0, 0.0, 359.0, -360.0]
        np.testing.assert_allclose(normalizeAngleAcute(values),
                                   [180.0, 180.0, -170.0, 180.0, 170.0, 0.0, -1.0, 0.0])


class SamplingTests(unittest.TestCase):
    def test_entry_rule_and_deltas(self):
        k = key({1: [entry(0, (1, 2, 3), (10, 0, 0), (0.5, 0, 0), (2, 0, 0), KeyframeFlag.AllChange),
                     entry(4, (9, 9, 9), (0, 0, 0))]})
        track = keyTrack(k, 1)
        pos, rot = sampleTrack(track, 2.5)
        np.testing.assert_allclose(pos, [2.25, 2, 3])
        np.testing.assert_allclose(rot, [15, 0, 0])
        pos, rot = sampleTrack(track, 4.0)
        np.testing.assert_allclose(pos, [9, 9, 9])

    def test_position_and_rotation_flags_are_separate(self):
        k = key({1: [entry(0, (0, 0, 0), (0, 0, 0), (1, 0, 0), (1, 0, 0), KeyframeFlag.OrientationChange)]})
        pos, rot = sampleTrack(keyTrack(k, 1), 3.0)
        np.testing.assert_allclose(pos, [0, 0, 0])
        np.testing.assert_allclose(rot, [3, 0, 0])

    def test_time_before_first_entry_uses_entry_zero(self):
        k = key({1: [entry(2, (0, 0, 0), (0, 0, 0), (1, 0, 0), (0, 0, 0), KeyframeFlag.PositionChange)]})
        pos, _ = sampleTrack(keyTrack(k, 1), 1.0)
        np.testing.assert_allclose(pos, [-1, 0, 0])

    def test_joints_bound_and_duplicate_tracks(self):
        k = key([(1, [entry(0, (5, 0, 0))]), (1, [entry(0, (7, 0, 0))]), (3, [entry(0, (1, 1, 1))])], joints=3)
        self.assertEqual(tuple(sampleTrack(keyTrack(k, 1), 0)[0]), (7.0, 0.0, 0.0))
        self.assertIsNone(keyTrack(k, 3))
        self.assertIsNone(keyTrack(k, 0))

    def test_frame_times(self):
        self.assertEqual(keyFrameTimes(key({}, frames=5)), [0, 1, 2, 3, 4])
        self.assertEqual(keyFrameTimes(key({}, frames=3), 0.5), [0, 0.5, 1.0, 1.5, 2.0])
        self.assertEqual(keyFrameTimes(key({}, frames=0)), [0])


class HierarchyTests(unittest.TestCase):
    def test_pivot_moves_own_mesh_not_children(self):
        model = chain(pivot=(0.5, 0, 0), insert=(0, 0, 0.25))
        pose = restPose(model)
        np.testing.assert_allclose(pose.frames[1][:3, 3], [1, 0, 0.25])
        np.testing.assert_allclose(pose.frames[2][:3, 3], [2, 0, 0.25])
        np.testing.assert_allclose(pose.vertices[1][0], [1.5, 0, 0.25])
        np.testing.assert_allclose(pose.vertices[2][0], [2, 0, 0.25])
        self.assertTrue(np.all(pose.local.restWeight == 1.0))

    def test_rotation_carries_children_and_rotated_pivot(self):
        model = chain(pivot=(0.5, 0, 0))
        k = key({1: [entry(0, (1, 0, 0), (0, 90, 0))]})
        pose = poseModel(model, [PoseLayer(k, 0, lowPri=1)])
        np.testing.assert_allclose(pose.frames[2][:3, 3], [1, 1, 0], atol=1e-12)
        np.testing.assert_allclose(pose.vertices[1][0], [1, 0.5, 0], atol=1e-12)
        np.testing.assert_allclose(pose.local.restWeight, [1, 0, 1])
        self.assertEqual(pose.local.drivenBy, [(), (0,), ()])

    def test_pre_rotation_applies_in_joint_space(self):
        model = chain(pivot=(0, 0, 0))
        local = blendLocalPose(model, [])
        frames = jointFrames(model, local, {1: (0, 90, 0)})
        np.testing.assert_allclose(frames[2][:3, 3], [1, 1, 0], atol=1e-12)
        np.testing.assert_allclose(frames[1][:3, 3], [1, 0, 0], atol=1e-12)

    def test_shared_mesh_is_drawn_per_node(self):
        model = chain(pivot=(0, 0, 0))
        model.meshHierarchy[2].meshIdx = 1
        frames = restPose(model, vertices=False).frames
        byMesh = posedMeshVertices(model, frames)
        byNode = posedNodeVertices(model, frames)
        self.assertEqual(sorted(byMesh), [0, 1])
        self.assertEqual(sorted(byNode), [0, 1, 2])
        np.testing.assert_allclose(byMesh[1][0], [1, 0, 0])
        np.testing.assert_allclose(byNode[2][0], [2, 0, 0])

    def test_meshless_nodes_and_cycles(self):
        model = chain()
        model.meshHierarchy[2].meshIdx = -1
        self.assertEqual(sorted(posedMeshVertices(model, restPose(model, vertices=False).frames)), [0, 1])
        model.meshHierarchy[0].parentIdx = 2
        with self.assertRaises(ValueError):
            restPose(model)


class BlendTests(unittest.TestCase):
    def setUp(self):
        self.model = chain()
        self.model.meshHierarchy[1].rotation = Vector3f(0.0, 170.0, 0.0)

    def test_rotation_delta_takes_the_short_way(self):
        k = key({1: [entry(0, (1, 0, 0), (0, -170, 0))]})
        local = blendLocalPose(self.model, [PoseLayer(k, 0)])
        np.testing.assert_allclose(local.rotations[1], [0, 190, 0])
        half = blendLocalPose(self.model, [PoseLayer(k, 0, weight=0.5)])
        np.testing.assert_allclose(half.rotations[1], [0, 180, 0])
        self.assertAlmostEqual(half.restWeight[1], 0.5)

    def test_higher_priority_wins_and_lower_fills(self):
        a = key({1: [entry(0, (3, 0, 0), (0, 170, 0))]})
        b = key({1: [entry(0, (1, 2, 0), (0, 170, 0))]})
        local = blendLocalPose(self.model, [PoseLayer(b, 0, lowPri=1), PoseLayer(a, 0, lowPri=2)])
        np.testing.assert_allclose(local.positions[1], [3, 0, 0])
        self.assertEqual(local.drivenBy[1], (1,))
        local = blendLocalPose(self.model, [PoseLayer(b, 0, lowPri=1), PoseLayer(a, 0, weight=0.25, lowPri=2)])
        np.testing.assert_allclose(local.positions[1], [1 + 0.25 * 2, 0.75 * 2, 0])
        self.assertAlmostEqual(local.restWeight[1], 0.0)
        self.assertEqual(local.drivenBy[1], (1, 0))

    def test_equal_priorities_average_by_weight(self):
        a = key({1: [entry(0, (3, 0, 0), (0, 170, 0))]})
        b = key({1: [entry(0, (1, 0, 0), (0, 150, 0))]})
        local = blendLocalPose(self.model, [PoseLayer(a, 0, lowPri=1), PoseLayer(b, 0, lowPri=1)])
        np.testing.assert_allclose(local.positions[1], [2, 0, 0])
        np.testing.assert_allclose(local.rotations[1], [0, 160, 0])

    def test_node_type_mask_selects_high_priority(self):
        masked = key({0: [entry(0, (0, 0, 1))], 1: [entry(0, (2, 0, 0), (0, 170, 0))]},
                     types=Mesh3doNodeType.LeftArm)
        other = key({0: [entry(0, (0, 0, 5))], 1: [entry(0, (5, 0, 0), (0, 170, 0))]})
        layers = [PoseLayer(masked, 0, lowPri=1, highPri=3), PoseLayer(other, 0, lowPri=2)]
        local = blendLocalPose(self.model, layers)
        np.testing.assert_allclose(local.positions[1], [2, 0, 0])
        np.testing.assert_allclose(local.positions[0], [0, 0, 5])
        play = PoseLayer.playKey(masked, 0, 1)
        self.assertEqual((play.lowPri, play.highPri), (1, 3))

    def test_lower_priority_is_skipped_once_weight_is_full(self):
        a = key({1: [entry(0, (3, 0, 0), (0, 170, 0))]})
        b = key({1: [entry(0, (9, 0, 0), (0, 170, 0))]})
        local = blendLocalPose(self.model, [PoseLayer(a, 0, lowPri=2), PoseLayer(b, 0, lowPri=1)])
        np.testing.assert_allclose(local.positions[1], [3, 0, 0])
        self.assertEqual(local.drivenBy[1], (0,))


if __name__ == '__main__':
    unittest.main()
