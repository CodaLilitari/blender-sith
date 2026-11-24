# SPDX-License-Identifier: GPL-3.0-or-later
"""KEY lint on synthetic models and KEYs (no Blender, no game data)."""
from pathlib import Path
import tempfile
import unittest

import numpy as np

from sith.audit.keylint import (Character, PopLimits, keyPriorityReport, lintKeyHeader,
                                rotationAngle, scanKeyPops, trackRegistry)
from sith.audit.pose import PoseLayer, rotationMatrix
from sith.key.key import Key, Keyframe, KeyframeFlag, KeyNode
from sith.key.keyEuler import eulerTwin, nearestEuler, unwrapEuler
from sith.key.keyLoader import loadKey
from sith.key.keyWriter import saveKey
from sith.model.model3do import Mesh3do, Mesh3doNode, Mesh3doNodeType, Model3do, Model3doGeoSet
from sith.types import Vector3f

T = Mesh3doNodeType


def entry(frame, rot=(0, 0, 0), drot=(0, 0, 0), pos=(0, 0, 0)):
    k = Keyframe()
    k.frame = frame
    k.flags = KeyframeFlag.OrientationChange if any(drot) else KeyframeFlag.NoChange
    k.position = Vector3f(*pos)
    k.orientation = Vector3f(*rot)
    k.deltaPosition = Vector3f(0.0, 0.0, 0.0)
    k.deltaRotation = Vector3f(*drot)
    return k


def path(rotations, start=0):
    """Entries one frame apart, each moving linearly to the next stored triple."""
    out = []
    for i, rot in enumerate(rotations):
        nxt = rotations[i + 1] if i + 1 < len(rotations) else rot
        out.append(entry(start + i, rot, tuple(b - a for a, b in zip(rot, nxt))))
    return out


def key(tracks, joints=3, frames=10, fps=15.0, types=T.Nothing, name='test.key'):
    """tracks: {node index: [Keyframe]} or a list of (index, [Keyframe]) pairs."""
    k = Key(name)
    k.numJoints, k.numFrames, k.fps, k.nodeTypes = joints, frames, fps, types
    for idx, entries in (tracks if isinstance(tracks, list) else list(tracks.items())):
        node = KeyNode()
        node.idx = idx
        node.meshName = 'n%d' % idx
        node.keyframes = entries
        k.nodes.append(node)
    return k


def model(specs, name='m'):
    """specs: (name, type, parent, position, has_mesh) per node; meshes hold one vertex."""
    m = Model3do(name)
    m.insertOffset = Vector3f(0.0, 0.0, 0.0)
    geoset = Model3doGeoSet()
    nodes = []
    for i, (node_name, kind, parent, position, has_mesh) in enumerate(specs):
        node = Mesh3doNode(node_name)
        node.idx, node.type, node.parentIdx = i, kind, parent
        node.position = Vector3f(*position)
        node.rotation = Vector3f(0.0, 0.0, 0.0)
        node.pivot = Vector3f(0.0, 0.0, 0.0)
        node.meshIdx = -1
        if has_mesh:
            mesh = Mesh3do(len(geoset.meshes), node_name)
            mesh.vertices = [Vector3f(0.0, 0.0, 0.0), Vector3f(0.0, 0.0, 0.2)]
            node.meshIdx = len(geoset.meshes)
            geoset.meshes.append(mesh)
        nodes.append(node)
    m.geosets = [geoset]
    m.meshHierarchy = nodes
    return m


def chain():
    """root (torso) -> arm (left arm) -> hand (left hand), 1 unit apart."""
    return model([('root', T.Torso, -1, (0, 0, 0), True),
                  ('arm', T.LeftArm, 0, (1, 0, 0), True),
                  ('hand', T.LeftHand, 1, (1, 0, 0), True)])


class EulerTests(unittest.TestCase):
    def test_twin_is_the_same_rotation(self):
        rng = np.random.default_rng(3)
        for pyr in rng.uniform(-400, 400, size=(30, 3)):
            np.testing.assert_allclose(rotationMatrix(eulerTwin(pyr)), rotationMatrix(pyr), atol=1e-12)

    def test_unwrap_moves_whole_turns_only(self):
        self.assertEqual(unwrapEuler((0, 170, 0), (0, -170, 720)), (0.0, 190.0, 0.0))
        self.assertEqual(unwrapEuler((0, 0, 0), (0, 180, -180)), (0.0, 180.0, -180.0))

    def test_nearest_takes_the_twin_over_the_top(self):
        # Red's lightwhip case: past pitch 90, a decomposition returns the other form.
        self.assertEqual(nearestEuler((88, 10, 20), (88, 190, 200)), (92.0, 10.0, 20.0))
        self.assertEqual(nearestEuler((0, 170, 0), (0, -170, 0)), (0.0, 190.0, 0.0))
        self.assertEqual(nearestEuler((10, 20, 30), (10, 20, 30)), (10.0, 20.0, 30.0))
        for previous, current in (((88, 10, 20), (88, 190, 200)), ((5, -100, 40), (170, 70, 230))):
            np.testing.assert_allclose(rotationMatrix(nearestEuler(previous, current)),
                                       rotationMatrix(current), atol=1e-12)

    def test_rotation_angle(self):
        self.assertAlmostEqual(rotationAngle((0, 0, 0), (0, 30, 0)), 30.0, places=6)
        self.assertAlmostEqual(rotationAngle((10, 20, 30), eulerTwin((10, 20, 30))), 0.0, places=4)


class HeaderTests(unittest.TestCase):
    def test_clean_key_with_model_is_consistent(self):
        m = chain()
        k = key({0: [entry(0)], 1: [entry(0)], 2: [entry(0)]}, joints=3)
        for track, node in zip(k.nodes, m.meshHierarchy):
            track.meshName = node.name
        report = lintKeyHeader(k, m)
        self.assertEqual((report['status'], report['findings']), ('consistent', []))
        self.assertEqual(report['binding']['status'], 'consistent')
        # The recommendation is advice, not a finding.
        self.assertEqual([r['code'] for r in report['recommendations']], ['joints_64'])
        k.numJoints = 64
        report = lintKeyHeader(k, m)
        self.assertEqual((report['findings'], report['recommendations']), ([], []))
        # The binding audit still notes JOINTS 64 against 3 nodes; keylint keeps its own counts apart.
        self.assertEqual(report['binding']['issue_counts'], {'declared_joint_count_differs': 1})
        self.assertEqual((report['issue_counts'], report['status']), ({}, 'review'))

    def test_track_past_joints_and_duplicates(self):
        k = key([(1, [entry(0, (5, 0, 0))]), (1, [entry(0, (7, 0, 0))]), (3, [entry(0)]),
                 (-1, [entry(0)])], joints=3)
        report = lintKeyHeader(k)
        self.assertEqual(report['status'], 'incompatible')
        self.assertEqual(report['issue_counts'], {'track_past_joints': 2, 'duplicate_track': 1})
        dup = next(f for f in report['findings'] if f['code'] == 'duplicate_track')
        self.assertEqual((dup['index'], dup['tracks'], dup['kept_track']), (1, 2, 1))
        past = [f['index'] for f in report['findings'] if f['code'] == 'track_past_joints']
        self.assertEqual(past, [3, -1])

    def test_entry_order_and_entries_after_frames(self):
        k = key({0: [entry(0), entry(4), entry(4), entry(2)],
                 1: [entry(0), entry(10), entry(11)]}, frames=10)
        report = lintKeyHeader(k)
        self.assertEqual(report['status'], 'incompatible')
        order = next(f for f in report['findings'] if f['code'] == 'entry_order')
        self.assertEqual((order['index'], order['entry'], order['frame'], order['previous_frame'],
                          order['entries']), (0, 2, 4.0, 4.0, 2))
        late = next(f for f in report['findings'] if f['code'] == 'entry_past_frames')
        # An entry at FRAMES plays when the play holds its last frame; one after never plays.
        self.assertEqual((late['index'], late['entries'], late['first_frame']), (1, 1, 11.0))

    def test_joints_below_model_and_recommendation(self):
        m = model([('n%d' % i, T.Torso, i - 1, (0, 0, 1), True) for i in range(5)])
        k = key({0: [entry(0)], 1: [entry(0)]}, joints=3)
        report = lintKeyHeader(k, m, target='MOTS')
        self.assertEqual(report['status'], 'review')
        below = next(f for f in report['findings'] if f['code'] == 'joints_below_model')
        self.assertEqual((below['declared_joints'], below['model_joints'], below['undriven_nodes']), (3, 5, 2))
        self.assertEqual([r['code'] for r in report['recommendations']], ['joints_64'])
        self.assertEqual(report['recommended_joints'], 64)
        self.assertEqual(report['binding']['key_target'], 'MOTS')

    def test_binding_verdict_raises_status(self):
        m = chain()
        k = key({0: [entry(0)]}, joints=64)
        k.nodes[0].meshName = 'not_root'
        report = lintKeyHeader(k, m)
        self.assertEqual(report['findings'], [])
        self.assertEqual(report['binding']['issue_counts'], {'declared_joint_count_differs': 1,
                                                             'joint_name_differs': 1})
        self.assertEqual(report['status'], 'review')

    def test_target_and_empty_key(self):
        with self.assertRaises(ValueError):
            lintKeyHeader(key({}), target='IJIM')
        self.assertEqual(lintKeyHeader(key({}, joints=64))['status'], 'no_tracks')

    def test_lenient_loader_keeps_tracks_the_engine_reads(self):
        k = key([(0, [entry(0)]), (0, [entry(0, (1, 0, 0))]), (2, [entry(0)])], joints=2, name='bad.key')
        with tempfile.TemporaryDirectory() as temp:
            path_ = Path(temp) / 'bad.key'
            saveKey(k, path_, 'synthetic')
            with self.assertRaisesRegex(ValueError, 'outside its joint count'):
                loadKey(path_)
            loaded = loadKey(path_, strictTracks=False)
        self.assertEqual([n.idx for n in loaded.nodes], [0, 0, 2])
        self.assertEqual(lintKeyHeader(loaded)['issue_counts'], {'track_past_joints': 1, 'duplicate_track': 1})


class PopTests(unittest.TestCase):
    def test_smooth_motion_has_no_findings(self):
        k = key({1: path([(0, 2 * f, f) for f in range(21)])}, frames=20)
        report = scanKeyPops(k, chain())
        self.assertEqual(report['status'], 'consistent')
        self.assertEqual((report['issue_counts'], report['tracks_scanned']), ({}, 1))
        self.assertEqual(report['nodes_scanned'], 2)  # the arm and the hand it carries

    def test_euler_twin_flip_between_entries(self):
        # Yaw turns 1 degree per frame; from entry 11 the KEY stores the other Euler form.
        rots = [(0, f, 0) if f <= 10 else eulerTwin((0, f, 0)) for f in range(21)]
        k = key({1: path(rots)}, frames=20)
        report = scanKeyPops(k, chain())
        self.assertEqual(report['issue_counts'], {'euler_flip': 1, 'position_jump': 1})
        flip = next(f for f in report['findings'] if f['code'] == 'euler_flip')
        self.assertEqual((flip['index'], flip['entry'], flip['frame'], flip['end_frame'], flip['form']),
                         (1, 10, 10.0, 11.0, 'twin'))
        self.assertAlmostEqual(flip['rotation_change'], 1.0, places=3)
        self.assertGreater(flip['detour'], 300)
        np.testing.assert_allclose(flip['nearest'], (0, 11, 0), atol=1e-9)
        jump = next(f for f in report['findings'] if f['code'] == 'position_jump')
        # The arm turns about its own origin; only the hand it carries is thrown around.
        self.assertEqual((jump['name'], jump['nodes']), ('hand', 1))
        self.assertGreaterEqual(jump['frame'], 10.0)
        self.assertLessEqual(jump['end_frame'], 11.0)

    def test_whole_turn_wrap_and_wide_step(self):
        k = key({0: [entry(0, (0, 170, 0), (0, -340, 0)), entry(1, (0, -170, 0))],
                 1: [entry(0, (-39.1, -47.0, -177.5), (0.2, -172.5, 56.3)),
                     entry(1, (-38.9, -219.5, -121.2))]}, frames=2)
        report = scanKeyPops(k)
        by_code = {f['code']: f for f in report['findings']}
        self.assertEqual(report['issue_counts'], {'euler_flip': 1, 'wide_step': 1})
        self.assertEqual((by_code['euler_flip']['index'], by_code['euler_flip']['form']), (0, 'turn'))
        self.assertAlmostEqual(by_code['euler_flip']['rotation_change'], 20.0, places=3)
        np.testing.assert_allclose(by_code['euler_flip']['nearest'], (0, 190, 0), atol=1e-9)
        self.assertEqual(by_code['wide_step']['index'], 1)
        self.assertGreater(by_code['wide_step']['detour'], 45)

    def test_yaw_roll_exchange_near_pitch_90_is_not_a_flip(self):
        # The rotation barely moves although yaw and roll each change by 150 degrees.
        k = key({1: [entry(0, (89.5, 0, 0), (0, 150, -150)), entry(1, (89.5, 150, -150))]}, frames=2)
        self.assertEqual(scanKeyPops(k)['issue_counts'], {})

    def test_form_switch_at_a_continuous_entry(self):
        rot = (10.0, 20.0, 30.0)
        k = key({1: [entry(0, rot), entry(5, eulerTwin(rot)), entry(8, eulerTwin(rot))],
                 2: [entry(0, (0, 10, 0)), entry(5, (0, 370, 0))]}, frames=10)
        report = scanKeyPops(k)
        self.assertEqual(report['issue_counts'], {'form_switch': 1})
        switch = report['findings'][0]
        self.assertEqual((switch['index'], switch['entry'], switch['frame']), (1, 1, 5.0))
        self.assertAlmostEqual(switch['component_change'], 180.0, places=6)

    def test_velocity_spike_against_neighbours(self):
        # 2 degrees per frame, then 120 degrees in one frame, at 30 FPS: 3600 deg/s against 60.
        rots = [(0, 2 * f, 0) if f <= 10 else (0, 2 * f + 118, 0) for f in range(21)]
        k = key({1: path(rots)}, frames=20, fps=30.0)
        report = scanKeyPops(k)
        self.assertEqual(report['issue_counts'], {'velocity_spike': 1})
        spike = report['findings'][0]
        self.assertEqual((spike['index'], spike['frame'], spike['end_frame']), (1, 10.0, 11.0))
        self.assertAlmostEqual(spike['speed'], 3600.0, places=3)
        self.assertAlmostEqual(spike['reference'], 360.0, places=6)  # the floor
        self.assertAlmostEqual(spike['ratio'], 10.0, places=3)
        quiet = scanKeyPops(k, limits=PopLimits(spikeRatio=12.0))
        self.assertEqual(quiet['issue_counts'], {})

    def test_two_frame_burst_sets_its_own_reference(self):
        # A deliberate two-frame strike is not a pop; ignoring a frame each side would flag its edges.
        rots = [(0, 2 * f, 0) if f <= 10 else (0, 2 * f + 118 * (min(f, 12) - 10), 0) for f in range(21)]
        k = key({1: path(rots)}, frames=20, fps=30.0)
        self.assertEqual(scanKeyPops(k)['issue_counts'], {})
        self.assertGreaterEqual(scanKeyPops(k, limits=PopLimits(guard=1.0))['issue_counts']['velocity_spike'], 1)

    def test_position_scan_node_selection_and_base_layers(self):
        rots = [(0, f, 0) if f <= 10 else eulerTwin((0, f, 0)) for f in range(21)]
        k = key({1: path(rots)}, frames=20)
        m = chain()
        only_hand = scanKeyPops(k, m, nodes=['HAND'])
        self.assertEqual(only_hand['nodes_scanned'], 1)
        self.assertEqual(only_hand['issue_counts']['position_jump'], 1)
        with self.assertRaises(ValueError):
            scanKeyPops(k, m, nodes=[7])
        base = key({0: [entry(0, (0, 0, 45))]}, frames=1)
        layered = scanKeyPops(k, m, baseLayers=[PoseLayer(base, 0.0, lowPri=0)])
        self.assertEqual(layered['issue_counts'], {'euler_flip': 1, 'position_jump': 1})
        self.assertEqual(layered['jump_floor'], 0.2)  # model height per second

    def test_skips_unordered_tracks_and_bounds_findings(self):
        flips = {i: path([(0, 0, 0), eulerTwin((0, 1, 0))]) for i in range(40)}
        flips[40] = [entry(0), entry(3), entry(2)]
        report = scanKeyPops(key(flips, joints=64, frames=2))
        self.assertEqual(report['issue_counts'], {'euler_flip': 40})
        self.assertEqual(len(report['findings']), 32)
        self.assertTrue(report['findings_truncated'])
        self.assertEqual(report['tracks_skipped'], [{'index': 40, 'key_name': 'n40', 'reason': 'entry_order'}])
        with self.assertRaises(ValueError):
            scanKeyPops(key(flips, joints=64, frames=0))
        with self.assertRaises(ValueError):
            PopLimits(step=0)
        self.assertEqual(scanKeyPops(key({}, frames=2))['status'], 'no_tracks')


class PriorityTests(unittest.TestCase):
    def test_mask_splits_high_low_and_lower_layers(self):
        k = key({0: [entry(0)], 1: [entry(0)], 3: [entry(0)], 4: [entry(0)], 2: []},
                joints=4, types=T.LeftArm | T.Head)
        report = keyPriorityReport(k, chain(), priority=1)
        self.assertEqual((report['node_types'], report['node_type_names']), ('0x000A', ['LeftArm', 'Head']))
        self.assertEqual((report['low_priority'], report['high_priority']), (1, 3))
        self.assertEqual([n['name'] for n in report['high']], ['arm'])
        self.assertEqual([n['name'] for n in report['low']], ['root'])
        self.assertEqual([n['name'] for n in report['lower_layers']], ['hand'])
        self.assertEqual(report['counts'], {'high': 1, 'low': 1, 'lower_layers': 1})
        self.assertEqual([(t['index'], t['reason']) for t in report['unused_tracks']],
                         [(3, 'no_model_node'), (4, 'past_joints'), (2, 'empty')])

    def test_explicit_priorities_and_arguments(self):
        k = key({1: [entry(0)]})
        report = keyPriorityReport(k, chain(), lowPri=2, highPri=5)
        self.assertEqual((report['low_priority'], report['high_priority']), (2, 5))
        self.assertEqual(keyPriorityReport(k, chain(), lowPri=2)['high_priority'], 2)
        for kwargs in ({}, {'priority': 1, 'lowPri': 1}, {'priority': 1, 'highPri': 4}):
            with self.assertRaises(ValueError):
                keyPriorityReport(k, chain(), **kwargs)


def character(name, prefix, extra_tracks, keys_extra=()):
    """Six-node character: 0-2 shared stock names, 3-5 its own; node 5 of 'a' has no mesh."""
    specs = [('k_root', T.Torso, -1, (0, 0, 0), True), ('k_a', T.Torso, 0, (0, 0, 1), True),
             ('k_b', T.Torso, 1, (0, 0, 1), True)]
    specs += [('%s_%d' % (prefix, i), T.Torso, 0, (0, 0, 0), not (prefix == 'a' and i == 5))
              for i in (3, 4, 5)]
    stock = {i: [entry(0, (i, 0, 0))] for i in range(3)}
    run = key({**stock, **extra_tracks}, joints=64, name='KYRUN1.KEY' if prefix == 'b' else 'kyrun1.key')
    idle = key({**stock, 3: [entry(0, (0, 0, 9))]}, joints=64, name='kyidle.key')
    return Character(name, model(specs, prefix), [run, idle, *keys_extra])


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.a = character('A', 'a', {3: [entry(0, (1, 2, 3))], 4: [entry(0, (4, 0, 0))]},
                           [key({3: [entry(0)]}, joints=64, name='awave.key')])
        self.b = character('B', 'b', {3: [entry(0, (9, 9, 9))], 5: [entry(0, (5, 0, 0))]})

    def test_collision_intrusion_and_listing(self):
        report = trackRegistry([self.a, self.b], custom=3)
        self.assertEqual(report['status'], 'incompatible')
        self.assertEqual(report['custom_rule'], {'rule': 'index', 'from': 3})
        self.assertEqual(report['issue_counts'], {'slot_collision': 1, 'slot_intrusion': 1})
        collision, intrusion = report['findings']
        self.assertEqual((collision['code'], collision['slot'], collision['nodes'], collision['keys'],
                          collision['key_names']), ('slot_collision', 3, ['a_3', 'b_3'], 1, ['kyrun1.key']))
        self.assertEqual((intrusion['code'], intrusion['slot'], intrusion['driven_by']),
                         ('slot_intrusion', 4, 'A'))
        # B's slot 5 track lands on A's meshless leaf: not a finding.
        a, b = report['characters']
        self.assertEqual([(s['slot'], s['node'], s['keys']) for s in a['slots']],
                         [(3, 'a_3', 3), (4, 'a_4', 1)])
        self.assertEqual([(s['slot'], s['keys']) for s in b['slots']], [(3, 2), (5, 1)])

    def test_rules_and_identical_tracks(self):
        names = trackRegistry([self.a, self.b], custom='names')
        self.assertEqual(names['issue_counts'], {'slot_collision': 1, 'slot_intrusion': 1})
        only4 = trackRegistry([self.a, self.b], custom={4})
        self.assertEqual(only4['custom_rule'], {'rule': 'slots', 'slots': [4]})
        self.assertEqual(only4['issue_counts'], {'slot_intrusion': 1})
        # Everything is custom: stock slots 0-2 are equal in both packs, so still no finding there.
        everything = trackRegistry([self.a, ('B', self.b.model, self.b.keys)], custom=0)
        self.assertEqual(everything['issue_counts'], {'slot_collision': 1, 'slot_intrusion': 1})
        same = character('C', 'c', {3: [entry(0, (1, 2, 3))], 4: [entry(0, (4, 0, 0))]})
        clean = trackRegistry([self.a, same], custom=3)
        self.assertEqual((clean['status'], clean['findings']), ('consistent', []))

    def test_rejects_ambiguous_inputs(self):
        twice = Character('D', self.a.model, [key({}, name='x.key'), key({}, name='X.KEY')])
        with self.assertRaises(ValueError):
            trackRegistry([twice])
        with self.assertRaises(ValueError):
            trackRegistry([self.a], custom='kyle')


if __name__ == '__main__':
    unittest.main()
