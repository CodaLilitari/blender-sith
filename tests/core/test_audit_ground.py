# SPDX-License-Identifier: GPL-3.0-or-later
"""Floor contact, contact slip, carried meshes and camera reading on synthetic models (no game data)."""
import math
import unittest

import numpy as np

from sith.audit.ground import (CAMERA_OFFSET, CAMERA_TARGET, MOVE_FRAMES_PER_UNIT, CarriedMesh, PoseSample,
                               PoseSequence, auditCameraReading, auditFloorContact, keySequence, lowestPoints,
                               referenceFloor, thingFloorHeight, thirdPersonViewDirection, viewExtent)
from sith.audit.pose import PoseLayer
from sith.key.key import Key, Keyframe, KeyframeFlag, KeyNode
from sith.model.model3do import Mesh3do, Mesh3doNode, Mesh3doNodeType, Model3do, Model3doGeoSet
from sith.types import Vector3f

SOLE = ((-0.01, -0.02, 0.0), (0.01, -0.02, 0.0), (0.01, 0.02, 0.0), (-0.01, 0.02, 0.0))
FOOT = (0.0, 0.0, -0.1)       # the foot joint's rest position under the hip


def model3do(name, specs, insert=(0.0, 0.0, 0.0)):
    """specs: (name, type, parent, position, rotation, pivot, vertices) per node; one mesh per node with vertices."""
    model = Model3do(name)
    model.insertOffset = Vector3f(*insert)
    geoset = Model3doGeoSet()
    nodes = []
    for i, (nodeName, ntype, parent, position, rotation, pivot, vertices) in enumerate(specs):
        node = Mesh3doNode(nodeName)
        node.idx = i
        node.type = ntype
        node.parentIdx = parent
        node.position = Vector3f(*position)
        node.rotation = Vector3f(*rotation)
        node.pivot = Vector3f(*pivot)
        node.meshIdx = -1
        if vertices:
            mesh = Mesh3do(len(geoset.meshes), nodeName)
            mesh.vertices = [Vector3f(*v) for v in vertices]
            node.meshIdx = len(geoset.meshes)
            geoset.meshes.append(mesh)
        nodes.append(node)
    model.geosets = [geoset]
    model.meshHierarchy = nodes
    return model


def leg(insert=(0.0, 0.0, 0.0), sole=SOLE):
    """A hip 0.1 above a flat foot: the sole lies on z = 0 at rest (plus the insert offset)."""
    return model3do('leg', (('k_hip', Mesh3doNodeType.Hip, -1, (0.0, 0.0, 0.1), (0, 0, 0), (0, 0, 0), [(0.0, 0.0, 0.0)]),
                            ('k_lfoot', Mesh3doNodeType.LeftLeg, 0, FOOT, (0, 0, 0), (0, 0, 0), sole)), insert)


def entry(frame, pos=FOOT, rot=(0, 0, 0), dpos=(0, 0, 0), drot=(0, 0, 0), flags=KeyframeFlag.NoChange):
    k = Keyframe()
    k.frame = frame
    k.flags = flags
    k.position = Vector3f(*pos)
    k.orientation = Vector3f(*rot)
    k.deltaPosition = Vector3f(*dpos)
    k.deltaRotation = Vector3f(*drot)
    return k


def key(tracks, joints=2, frames=6, name='test.key'):
    """tracks: {node index: [Keyframe]}."""
    k = Key(name)
    k.numJoints = joints
    k.numFrames = frames
    k.fps = 15.0
    for idx, entries in tracks.items():
        node = KeyNode()
        node.idx = idx
        node.meshName = 'n%d' % idx
        node.keyframes = entries
        k.nodes.append(node)
    return k


def foot(*steps, frames=6, name='test.key'):
    """A KEY whose foot track holds each (frame, position[, rotation]) until the next."""
    return key({1: [entry(s[0], s[1], s[2] if len(s) > 2 else (0, 0, 0)) for s in steps]}, frames=frames, name=name)


def stick(direction, length=0.2):
    points = [(0.0, 0.0, 0.0)] + [tuple(length * t * np.asarray(direction, float)) for t in (0.5, 1.0)]
    return np.array(points)


class FloorHeightTests(unittest.TestCase):
    def test_engine_floor_follows_thing_height_rules(self):
        kyle = leg(insert=(0.0, 0.0, 0.120114))
        self.assertAlmostEqual(thingFloorHeight(kyle, 0.065), 0.0)
        self.assertAlmostEqual(thingFloorHeight(kyle, 0.065, crouching=True), 0.120114 - 0.075)
        self.assertAlmostEqual(thingFloorHeight(kyle, 0.065, height=0.15), 0.120114 - 0.15)
        low = leg(insert=(0.0, 0.0, 0.03))
        self.assertAlmostEqual(thingFloorHeight(low, 0.065), 0.03 - 0.07)   # at least movesize + 0.005

    def test_reference_floor_is_the_lowest_point_of_a_pose(self):
        model = leg()
        self.assertAlmostEqual(referenceFloor(model), 0.0)
        lowered = foot((0, (0.0, 0.0, -0.104)))
        self.assertAlmostEqual(referenceFloor(model, [PoseLayer(lowered, 0.0)]), -0.004)
        self.assertAlmostEqual(referenceFloor(model, nodes=['k_hip']), 0.1)
        self.assertAlmostEqual(referenceFloor(leg(insert=(0.0, 0.0, 0.05))), 0.05)


class FloorContactTests(unittest.TestCase):
    def setUp(self):
        self.model = leg()

    def test_foot_lowered_below_the_floor(self):
        k = foot((0, FOOT), (2, (0.0, 0.0, -0.105)), (3, FOOT))
        report = auditFloorContact(self.model, [k])
        self.assertEqual(report['status'], 'review')
        self.assertEqual(report['floor_source'], 'rest_pose')
        self.assertEqual(report['issue_counts'], {'below_floor': 1})
        summary = report['sequences'][0]
        self.assertEqual((summary['samples'], summary['below_samples']), (6, 1))
        self.assertAlmostEqual(summary['lowest_min'], -0.005)
        self.assertEqual(summary['lowest_min_time'], 2.0)
        found = report['findings'][0]
        self.assertEqual((found['code'], found['sequence'], found['time']), ('below_floor', 'test.key', 2.0))
        self.assertAlmostEqual(found['depth'], 0.005)
        self.assertEqual((found['node'], found['node_name'], found['mesh'], found['carried']), (1, 'k_lfoot', 1, None))
        self.assertIn(found['vertex'], range(4))

    def test_small_dip_within_tolerance_is_not_a_finding(self):
        k = foot((0, FOOT), (2, (0.0, 0.0, -0.1015)))
        report = auditFloorContact(self.model, [k])
        self.assertEqual(report['status'], 'clear')
        self.assertAlmostEqual(report['sequences'][0]['lowest_min'], -0.0015)

    def test_floating_frame_of_the_contact_mesh(self):
        k = foot((0, FOOT), (3, (0.0, 0.0, -0.09)), (4, FOOT))
        report = auditFloorContact(self.model, [k], contacts=['k_lfoot'])
        self.assertEqual(report['issue_counts'], {'floating': 1})
        found = report['findings'][0]
        self.assertEqual((found['time'], found['node']), (3.0, 1))
        self.assertAlmostEqual(found['height'], 0.01)
        summary = report['sequences'][0]
        self.assertAlmostEqual(summary['contact_max'], 0.01)
        self.assertAlmostEqual(summary['lowest_max'], 0.01)
        self.assertEqual(summary['floating_samples'], 1)

    def test_floor_choices(self):
        k = foot((0, FOOT))
        given = auditFloorContact(self.model, [k], floor=-0.01)
        self.assertEqual((given['floor'], given['floor_source']), (-0.01, 'given'))
        self.assertEqual(given['issue_counts'], {'floating': 1})
        standing = foot((0, (0.0, 0.0, -0.103)), name='stand.key')
        pose = auditFloorContact(self.model, [k], floor=[PoseLayer(standing, 0.0)])
        self.assertEqual(pose['floor_source'], 'reference_pose')
        self.assertAlmostEqual(pose['floor'], -0.003)
        self.assertEqual(pose['issue_counts'], {'floating': 1})
        crouch = keySequence(foot((0, (0.0, 0.0, -0.06))), floor=0.04)
        report = auditFloorContact(self.model, [crouch])
        self.assertEqual(report['status'], 'clear')
        self.assertEqual(report['sequences'][0]['floor'], 0.04)
        with self.assertRaises(TypeError):
            auditFloorContact(self.model, [k], floor=True)
        with self.assertRaises(TypeError):
            auditFloorContact(self.model, [k], floor=['not a layer'])

    def test_lowest_points_identify_each_sample(self):
        k = foot((0, FOOT), (1, (0.0, 0.0, -0.102)))
        rows = lowestPoints(self.model, k)
        self.assertEqual([row['time'] for row in rows], [0, 1, 2, 3, 4, 5])
        self.assertAlmostEqual(rows[1]['z'], -0.002)
        self.assertEqual((rows[1]['node_name'], rows[1]['mesh'], rows[1]['carried']), ('k_lfoot', 1, None))
        hipOnly = lowestPoints(self.model, k, nodes=[0])
        self.assertAlmostEqual(hipOnly[0]['z'], 0.1)
        nothing = lowestPoints(self.model, k, nodes=[])
        self.assertIsNone(nothing[0]['z'])

    def test_findings_are_bounded_and_counted(self):
        keys = [foot((0, (0.0, 0.0, -0.11)), frames=1, name='k%02d.key' % i) for i in range(40)]
        report = auditFloorContact(self.model, keys)
        self.assertEqual(report['issue_counts'], {'below_floor': 40})
        self.assertEqual(len(report['findings']), 32)
        self.assertTrue(report['findings_truncated'])
        self.assertEqual(len(report['sequences']), 40)

    def test_empty_input_and_bad_arguments(self):
        self.assertEqual(auditFloorContact(self.model, [])['status'], 'empty')
        with self.assertRaises(TypeError):
            auditFloorContact(self.model, ['kywalkf.key'])
        with self.assertRaises(ValueError):
            auditFloorContact(self.model, [], contacts=['k_rfoot'])
        with self.assertRaises(ValueError):
            auditFloorContact(self.model, [], nodes=[7])
        with self.assertRaises(ValueError):
            auditFloorContact(self.model, [], geoset=3)
        twins = leg()
        twins.meshHierarchy[1].name = 'k_hip'
        with self.assertRaises(ValueError):
            auditFloorContact(twins, [], contacts=['K_HIP'])
        self.assertEqual(auditFloorContact(twins, [], contacts=[1])['contacts'], [1])


class SequenceTests(unittest.TestCase):
    def test_key_sequence_travel_follows_the_engine_rate(self):
        k = foot((0, FOOT), frames=3)
        seq = keySequence(k, 0.5, direction=(0, 2))
        self.assertEqual(seq.name, 'test.key')
        self.assertEqual([s.time for s in seq.samples], [0, 0.5, 1.0, 1.5, 2.0])
        self.assertEqual(seq.travel, (0.0, 1.0 / MOVE_FRAMES_PER_UNIT))
        self.assertEqual(seq.samples[1].layers[0].frame, 0.5)
        with self.assertRaises(ValueError):
            keySequence(k, direction=(0, 0))
        with self.assertRaises(ValueError):
            keySequence(k, direction=(0, 1, 0))

    def test_the_last_interval_is_sampled_on_request(self):
        k = foot((0, FOOT), frames=3)
        self.assertEqual([s.time for s in keySequence(k, 0.5).samples], [0, 0.5, 1.0, 1.5, 2.0])
        ends = keySequence(k, 0.5, toEnd=True)
        self.assertEqual([s.time for s in ends.samples], [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
        self.assertEqual(ends.samples[-1].layers[0].frame, 3.0)
        # The foot sinks from frame 5 on and is 0.005 under the floor only at FRAMES (6).
        sink = key({1: [entry(0, FOOT), entry(5, FOOT, dpos=(0.0, 0.0, -0.005), flags=KeyframeFlag.PositionChange)]})
        model = leg()
        default = auditFloorContact(model, [sink])
        self.assertEqual((default['status'], default['to_end'], default['samples']), ('clear', False, 6))
        report = auditFloorContact(model, [sink], toEnd=True)
        self.assertEqual((report['status'], report['to_end'], report['samples']), ('review', True, 7))
        found = report['findings'][0]
        self.assertEqual((found['code'], found['time']), ('below_floor', 6.0))
        self.assertAlmostEqual(found['depth'], 0.005)
        self.assertEqual([row['time'] for row in lowestPoints(model, sink, toEnd=True)], [0, 1, 2, 3, 4, 5, 6])
        camera = auditCameraReading(model, [sink], nodes=['k_lfoot'], toEnd=True)
        self.assertEqual(camera['sequences'][0]['samples'], 7)

    def test_sample_times_must_increase(self):
        k = foot((0, FOOT))
        with self.assertRaises(ValueError):
            PoseSequence('bad', [PoseSample(1.0, (PoseLayer(k, 1.0),)), PoseSample(1.0, (PoseLayer(k, 1.0),))])
        with self.assertRaises(ValueError):
            PoseSequence('bad', [], travel=(0.0, 1.0, 0.0))

    def test_layered_samples(self):
        model = leg()
        base = foot((0, FOOT), name='base.key')
        lower = foot((0, (0.0, 0.0, -0.11)), name='lower.key')
        samples = [PoseSample(t, (PoseLayer(base, t, lowPri=0), PoseLayer(lower, t, lowPri=1)))
                   for t in (0.0, 1.0)]
        report = auditFloorContact(model, [PoseSequence('pair', samples)])
        self.assertAlmostEqual(report['sequences'][0]['lowest_min'], -0.01)


class SlipTests(unittest.TestCase):
    def setUp(self):
        self.model = leg()

    def audit(self, sequences, **options):
        return auditFloorContact(self.model, sequences, contacts=['k_lfoot'], **options)

    def test_planted_foot_sliding_in_place(self):
        k = key({1: [entry(0, FOOT, dpos=(0.0, 0.004, 0.0), flags=KeyframeFlag.PositionChange)]})
        report = self.audit([k])
        slip = report['sequences'][0]['slip'][0]
        self.assertEqual((slip['node'], slip['node_name'], slip['planted_intervals']), (1, 'k_lfoot', 5))
        self.assertAlmostEqual(slip['slide_total'], 0.02)
        self.assertAlmostEqual(slip['slide_max_per_frame'], 0.004)
        self.assertIsNone(slip['sweep'])
        self.assertEqual(report['issue_counts'], {'contact_slip': 1})
        found = report['findings'][0]
        self.assertEqual((found['code'], found['node'], found['intervals']), ('contact_slip', 1, 5))
        self.assertAlmostEqual(found['slide_per_frame'], 0.004)
        self.assertEqual(found['time_end'] - found['time'], 1.0)

    def test_still_foot_does_not_slide(self):
        report = self.audit([foot((0, FOOT))])
        slip = report['sequences'][0]['slip'][0]
        self.assertEqual((slip['planted_intervals'], slip['slide_total'], slip['over_tolerance']), (5, 0.0, 0))
        self.assertEqual(report['status'], 'clear')

    def test_travel_matches_the_ground(self):
        rate = 1.0 / MOVE_FRAMES_PER_UNIT
        moving = key({1: [entry(0, FOOT, dpos=(0.0, -rate, 0.0), flags=KeyframeFlag.PositionChange)]})
        report = self.audit([keySequence(moving, 0.5, direction=(0, 1))])
        slip = report['sequences'][0]['slip'][0]
        self.assertAlmostEqual(slip['sweep'], 1.0)
        self.assertAlmostEqual(slip['slide_total'], 0.0)
        self.assertEqual(report['status'], 'clear')
        # A foot that stays put under a travelling KEY is dragged at the ground's speed.
        still = self.audit([keySequence(foot((0, FOOT)), direction=(0, 1))])
        slip = still['sequences'][0]['slip'][0]
        self.assertAlmostEqual(slip['sweep'], 0.0)
        self.assertAlmostEqual(slip['slide_max_per_frame'], rate)
        self.assertEqual(still['issue_counts'], {'contact_slip': 1})
        backwards = key({1: [entry(0, FOOT, dpos=(rate, 0.0, 0.0), flags=KeyframeFlag.PositionChange)]})
        strafe = self.audit([keySequence(backwards, direction=(-1, 0))])
        self.assertAlmostEqual(strafe['sequences'][0]['slip'][0]['sweep'], 1.0)

    def test_raised_joint_is_not_planted(self):
        # Frames 3-5: the foot pitches its toe down and lifts its joint 0.014, with the toe still on the floor.
        drop = math.sin(math.radians(45)) * 0.02
        toe = ((-0.01, 0.02, 0.0), (0.01, 0.02, 0.0), (0.0, -0.02, 0.0))
        model = leg(sole=toe)
        k = key({1: [entry(0, FOOT), entry(3, (0.0, 0.0, -0.1 + drop), (-45, 0, 0), dpos=(0.0, 0.01, 0.0),
                                                 flags=KeyframeFlag.PositionChange)]})
        report = auditFloorContact(model, [k], contacts=['k_lfoot'])
        slip = report['sequences'][0]['slip'][0]
        self.assertEqual(slip['planted_intervals'], 2)        # 0-1 and 1-2 only
        self.assertEqual(slip['slide_total'], 0.0)
        self.assertEqual(report['status'], 'clear')
        loose = auditFloorContact(model, [k], contacts=['k_lfoot'], stanceBand=0.05)
        self.assertEqual(loose['sequences'][0]['slip'][0]['planted_intervals'], 5)
        self.assertEqual(loose['issue_counts'], {'contact_slip': 1})
        self.assertEqual(loose['findings'][0]['intervals'], 3)  # the toe drags 0.0059, then 0.01 a frame

    def test_rolling_contact_is_not_slide(self):
        # The foot rocks from its heel row to its toe row about a joint that stays on the floor.
        k = key({1: [entry(0, FOOT, (10, 0, 0)), entry(1, FOOT, (-10, 0, 0)), entry(2, FOOT, (10, 0, 0))]}, frames=3)
        report = self.audit([k])
        slip = report['sequences'][0]['slip'][0]
        self.assertEqual((slip['planted_intervals'], slip['rolling_intervals']), (0, 2))
        self.assertIsNone(slip['slide_max_per_frame'])

    def test_floating_foot_is_never_planted(self):
        report = self.audit([foot((0, (0.0, 0.0, -0.095)))], floatTolerance=0.01)
        slip = report['sequences'][0]['slip'][0]
        self.assertEqual(slip['planted_intervals'], 0)
        self.assertEqual(report['status'], 'clear')

    def test_slip_needs_named_contacts(self):
        k = key({1: [entry(0, FOOT, dpos=(0.0, 0.004, 0.0), flags=KeyframeFlag.PositionChange)]})
        report = auditFloorContact(self.model, [k])
        self.assertEqual(report['sequences'][0]['slip'], [])
        self.assertEqual(report['slip_nodes'], [])
        self.assertEqual(report['status'], 'clear')


class CarriedMeshTests(unittest.TestCase):
    def setUp(self):
        self.model = leg()
        self.still = foot((0, FOOT), frames=6)

    def test_carried_mesh_dips_only_while_shown(self):
        whip = CarriedMesh('long', 'k_hip', [(0.0, 0.1, 0.0), (0.0, 0.2, -0.106)], ranges=[(2, 4)])
        rest = CarriedMesh('rest', 0, [(0.0, 0.05, -0.05)], ranges=[(0, 2), (4, math.inf)])
        report = auditFloorContact(self.model, [self.still], carried=[whip, rest])
        self.assertEqual(report['carried'], ['long', 'rest'])
        self.assertEqual(report['issue_counts'], {'below_floor': 1})
        found = report['findings'][0]
        self.assertEqual((found['carried'], found['node'], found['node_name'], found['mesh'], found['vertex']),
                         ('long', 0, 'k_hip', None, 1))
        self.assertEqual(report['sequences'][0]['below_samples'], 2)        # frames 2 and 3
        self.assertAlmostEqual(found['depth'], 0.006)
        rows = lowestPoints(self.model, self.still, nodes=[], carried=[whip, rest])
        self.assertEqual([r['carried'] for r in rows], ['rest', 'rest', 'long', 'long', 'rest', 'rest'])
        self.assertAlmostEqual(rows[0]['z'], 0.05)

    def test_carried_mesh_rides_the_node_and_its_pivot(self):
        model = leg()
        model.meshHierarchy[0].pivot = Vector3f(0.0, 0.0, 0.01)
        k = key({0: [entry(0, (0.0, 0.0, 0.1), (0, 90, 0))]}, frames=1)
        tip = CarriedMesh('tip', 0, [(0.0, 0.1, 0.0)])
        rows = lowestPoints(model, k, nodes=[], carried=[tip])
        self.assertAlmostEqual(rows[0]['z'], 0.11)          # hip at 0.1 plus its pivot; yaw keeps the height
        rows = lowestPoints(model, k, nodes=[], carried=[CarriedMesh('tip', 0, [(0.0, 0.1, -0.2)])])
        self.assertAlmostEqual(rows[0]['z'], -0.09)

    def test_hidden_nodes_are_not_measured(self):
        cover = CarriedMesh('cover', 'k_hip', [(0.0, 0.0, 0.05)], ranges=[(1, 3)], hides=['k_lfoot'])
        rows = lowestPoints(self.model, self.still, carried=[cover])
        self.assertEqual([r['node_name'] for r in rows[:4]], ['k_lfoot', 'k_hip', 'k_hip', 'k_lfoot'])
        self.assertAlmostEqual(rows[1]['z'], 0.1)

    def test_from_model_applies_hierarchy_but_not_insert_offset(self):
        weapon = model3do('weapon', (('grip', Mesh3doNodeType.Nothing, -1, (0.0, 0.01, 0.0), (0, 90, 0), (0.0, 0.0, 0.002),
                                      [(0.0, 0.0, 0.0), (0.0, 0.1, 0.0)]),), insert=(0.5, 0.5, 0.5))
        mesh = CarriedMesh.fromModel(weapon, 'k_hip', ranges=[(0, 1)])
        self.assertEqual(mesh.name, 'weapon')
        # Yaw 90 turns +y to -x; the pivot lifts the mesh and the node position moves it forward.
        np.testing.assert_allclose(mesh.points, [[0.0, 0.01, 0.002], [-0.1, 0.01, 0.002]], atol=1e-12)
        self.assertTrue(mesh.shownAt(0.5))
        self.assertFalse(mesh.shownAt(1.0))

    def test_carried_mesh_validation(self):
        with self.assertRaises(ValueError):
            CarriedMesh('bad', 0, [(0, 0, 0)], ranges=[(2, 2)])
        a = CarriedMesh('same', 0, [(0, 0, 0)])
        with self.assertRaises(ValueError):
            auditFloorContact(self.model, [], carried=[a, CarriedMesh('same', 1, [(0, 0, 0)])])
        with self.assertRaises(ValueError):
            auditFloorContact(self.model, [], carried=[CarriedMesh('x', 'k_rhand', [(0, 0, 0)])])


class CameraTests(unittest.TestCase):
    def test_default_view_comes_from_the_engine_offsets(self):
        view = thirdPersonViewDirection()
        expected = np.subtract(CAMERA_TARGET, CAMERA_OFFSET)
        np.testing.assert_allclose(view, expected / np.linalg.norm(expected))
        self.assertAlmostEqual(math.degrees(math.atan2(-view[2], view[1])), math.degrees(math.atan2(0.06, 0.5)))
        up = thirdPersonViewDirection(aimPitch=30)
        self.assertAlmostEqual(math.degrees(math.atan2(up[2], up[1])), 30 - math.degrees(math.atan2(0.06, 0.5)))
        self.assertAlmostEqual(float(np.linalg.norm(up)), 1.0)

    def test_view_extent_of_a_stick(self):
        view = (0.0, 1.0, 0.0)
        across, length = viewExtent(stick((0, 1, 0)), view)
        self.assertAlmostEqual(across, 0.0)
        self.assertAlmostEqual(length, 0.2)
        across, length = viewExtent(stick((1, 0, 0)), view)
        self.assertAlmostEqual(across, 0.2)
        angled = (math.sin(math.radians(30)), math.cos(math.radians(30)), 0.0)
        self.assertAlmostEqual(viewExtent(stick(angled), view)[0], 0.1)
        rng = np.random.default_rng(3)
        cloud = rng.normal(size=(700, 3))
        best = max(np.linalg.norm(a - b) for a in cloud for b in cloud[:50])
        self.assertGreaterEqual(viewExtent(cloud, view)[1], best - 1e-12)
        with self.assertRaises(ValueError):
            viewExtent(np.zeros((0, 3)), view)
        with self.assertRaises(ValueError):
            viewExtent(stick((1, 0, 0)), (0, 0, 0))

    def test_long_mesh_along_the_view_reads_short(self):
        model = leg()
        still = foot((0, FOOT), frames=4)
        ahead = CarriedMesh('ahead', 'k_hip', stick((0, 1, 0)), ranges=[(0, 2)])
        aside = CarriedMesh('aside', 'k_hip', stick((1, 0, 0)), ranges=[(2, 4)])
        report = auditCameraReading(model, [still], carried=[ahead, aside])
        self.assertAlmostEqual(report['below_level_degrees'], math.degrees(math.atan2(0.06, 0.5)))
        rows = report['sequences'][0]['rows']
        self.assertEqual([row['carried'] for row in rows], [['ahead'], ['ahead'], ['aside'], ['aside']])
        self.assertAlmostEqual(rows[0]['across'], 0.2 * 0.06 / math.hypot(0.5, 0.06))
        self.assertAlmostEqual(rows[0]['length'], 0.2)
        self.assertAlmostEqual(rows[2]['ratio'], 1.0)
        self.assertEqual(report['issue_counts'], {'reads_short': 1})
        found = report['findings'][0]
        self.assertEqual((found['sequence'], found['samples'], found['time'], found['carried']),
                         ('test.key', 2, 0.0, ['ahead']))
        self.assertEqual(report['sequences'][0]['short_samples'], 2)

    def test_short_meshes_and_empty_samples(self):
        model = leg()
        still = foot((0, FOOT), frames=2)
        tiny = CarriedMesh('tiny', 0, stick((0, 1, 0), length=0.02), ranges=[(1, 2)])
        report = auditCameraReading(model, [still], carried=[tiny])
        rows = report['sequences'][0]['rows']
        self.assertIsNone(rows[0]['across'])
        self.assertEqual(rows[1]['carried'], ['tiny'])
        self.assertEqual(report['status'], 'clear')                      # shorter than minLength
        self.assertEqual(auditCameraReading(model, [still], carried=[tiny], minLength=0.0)['status'], 'review')
        sideways = auditCameraReading(model, [still], carried=[tiny], minLength=0.0, direction=(1, 0, 0))
        self.assertEqual(sideways['status'], 'clear')
        nodes = auditCameraReading(model, [still], nodes=['k_hip', 'k_lfoot'], direction=(0, 0, 1))
        self.assertAlmostEqual(nodes['sequences'][0]['rows'][0]['length'], math.hypot(0.1, math.hypot(0.01, 0.02)))
        with self.assertRaises(ValueError):
            auditCameraReading(model, [still], carried=[tiny], direction=(0, 0, 0))


if __name__ == '__main__':
    unittest.main()
