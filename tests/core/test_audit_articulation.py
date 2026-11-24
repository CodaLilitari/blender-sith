# SPDX-License-Identifier: GPL-3.0-or-later
"""Seam-ray daylight audit and KEY seat offsets on synthetic joints (no Blender, no game data)."""
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from sith.audit import articulation as art
from sith.audit.articulation import (AnchorSettings, SWEEP_CAMERAS, articulationJoints, auditArticulation,
                                     cameraDirection, loadKeyFolder, referencePose, seatOffsets)
from sith.audit.pose import PoseLayer
from sith.key.key import Key, Keyframe, KeyframeFlag, KeyNode
from sith.key.keyWriter import saveKey
from sith.model.model3do import Mesh3do, Mesh3doFace, Mesh3doNode, Mesh3doNodeType, Model3do, Model3doGeoSet
from sith.types import Vector3f

R, H, O = 0.01, 0.02, 0.001       # half width, length and seat overlap of the synthetic parts (JK units)
D = 0.012                         # half depth: boxes are not square, so no radial ray meets a corner


def polygon_mesh(name, vertices, polygons):
    mesh = Mesh3do(0, name)
    mesh.vertices = [Vector3f(*v) for v in vertices]
    for corners in polygons:
        face = Mesh3doFace()
        face.vertexIdxs = list(corners)
        face.uvIdxs = [0] * len(corners)
        mesh.faces.append(face)
    return mesh


def box(name, lo, hi):
    """A closed box: every side capped."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
               (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return polygon_mesh(name, corners, quads)


def tube(name, z0, z1, radius=R, segments=16):
    """An open cylinder: both ends are open rims. Its corners sit between the radial rays."""
    ring = [(radius * math.cos(2 * math.pi * (k + 0.5) / segments),
             radius * math.sin(2 * math.pi * (k + 0.5) / segments)) for k in range(segments)]
    vertices = [(x, y, z0) for x, y in ring] + [(x, y, z1) for x, y in ring]
    quads = [(k, (k + 1) % segments, segments + (k + 1) % segments, segments + k) for k in range(segments)]
    return polygon_mesh(name, vertices, quads)


def model(meshes, nodes):
    """nodes: (name, parent, mesh index or -1, position, rotation)."""
    m = Model3do('synthetic')
    geoset = Model3doGeoSet()
    geoset.meshes = meshes
    m.geosets = [geoset]
    for i, (name, parent, mesh, position, rotation) in enumerate(nodes):
        node = Mesh3doNode(name)
        node.idx = i
        node.type = Mesh3doNodeType.Nothing
        node.parentIdx = parent
        node.meshIdx = mesh
        node.position = Vector3f(*position)
        node.rotation = Vector3f(*rotation)
        node.pivot = Vector3f(0.0, 0.0, 0.0)
        m.meshHierarchy.append(node)
    return m


def two_boxes(names=('hip', 'torso'), rotation=(0.0, 0.0, 0.0)):
    """Parent box below the pivot, child box above it; they overlap by 2 * O at the seat."""
    return model([box('lower', (-R, -D, -H), (R, D, O)), box('upper', (-R, -D, -O), (R, D, H))],
                 [(names[0], -1, 0, (0, 0, 0), (0, 0, 0)), (names[1], 0, 1, (0, 0, 0), rotation)])


def two_tubes():
    """Open tubes whose rims meet at the waist, overlapping by 2 * O."""
    return model([tube('hips', -H, O), tube('belly', -O, H)],
                 [('k_hip', -1, 0, (0, 0, 0), (0, 0, 0)), ('k_torso', 0, 1, (0, 0, 0), (0, 0, 0))])


def entry(frame, pos=(0, 0, 0), rot=(0, 0, 0), dpos=(0, 0, 0), drot=(0, 0, 0), flags=KeyframeFlag.NoChange):
    k = Keyframe()
    k.frame = frame
    k.flags = flags
    k.position = Vector3f(*pos)
    k.orientation = Vector3f(*rot)
    k.deltaPosition = Vector3f(*dpos)
    k.deltaRotation = Vector3f(*drot)
    return k


def key(tracks, joints=2, frames=10, name='test.key'):
    k = Key(name)
    k.numJoints = joints
    k.numFrames = frames
    k.fps = 15.0
    k.nodeTypes = Mesh3doNodeType.Nothing
    for idx, entries in tracks.items():
        node = KeyNode()
        node.idx = idx
        node.meshName = 'n%d' % idx
        node.keyframes = entries
        k.nodes.append(node)
    return k


def slide(lift, frames=10, start=2, name='slide.key'):
    """Child seated until ``start``, then lifted by ``lift`` along +z."""
    return key({0: [entry(0)], 1: [entry(0), entry(start, (0, 0, lift))]}, frames=frames, name=name)


class CoverageTests(unittest.TestCase):
    def test_line_meets_a_triangle_from_either_side_and_edges_count(self):
        vertices = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        triangles = np.array([[0, 1, 2]])
        # Above the triangle, beside it, and on its edge, below the plane.
        points = np.array([[0.2, 0.2, 5.0], [0.9, 0.9, 0.0], [0.5, 0.0, -3.0]])
        for direction in ((0.0, 0.0, 1.0), (0.0, 0.0, -1.0)):
            covered = art._covered(vertices, triangles, points, np.array(direction))
            self.assertEqual(covered.tolist(), [True, False, True])
        # A slanted line through the first point meets the plane at (-0.3, 0.2): outside.
        self.assertFalse(art._covered(vertices, triangles, points[:1], np.array([0.1, 0.0, 1.0]))[0])
        # Seen edge-on, a triangle covers nothing.
        self.assertFalse(art._covered(vertices, triangles, np.array([[0.2, 0.0, 0.0]]),
                                      np.array([0.0, 1.0, 0.0])).any())

    def test_grid_cull_matches_brute_force(self):
        rng = np.random.default_rng(11)
        size = np.concatenate([rng.uniform(0.001, 0.02, 300), rng.uniform(0.3, 1.5, 20)])
        centres = rng.uniform(-0.5, 0.5, (len(size), 1, 2))
        xy = (centres + rng.uniform(-1, 1, (len(size), 3, 2)) * size[:, None, None]).reshape(-1, 2)
        triangles = np.arange(len(xy)).reshape(-1, 3)
        points = np.concatenate([rng.uniform(-0.6, 0.6, (400, 2)), rng.uniform(-0.05, 0.05, (200, 2))])

        def brute(p):
            for a, b, c in xy[triangles]:
                d1 = (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])
                d2 = (p[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (p[1] - c[1])
                d3 = (p[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (p[1] - a[1])
                if not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0)):
                    return True
            return False

        expected = [brute(p) for p in points]
        self.assertEqual(art._coveredPlanar(xy, triangles, points).tolist(), expected)
        self.assertTrue(any(expected) and not all(expected))

    def test_camera_directions_and_shared_lines(self):
        np.testing.assert_allclose(cameraDirection(0, 0), [0, 1, 0], atol=1e-15)
        np.testing.assert_allclose(cameraDirection(90, 0), [1, 0, 0], atol=1e-15)
        np.testing.assert_allclose(cameraDirection(0, 90), [0, 0, 1], atol=1e-15)
        directions = [cameraDirection(az, el) for az, el in SWEEP_CAMERAS]
        # Level cameras 180 degrees apart see along the same lines; raised ones do not pair up.
        self.assertEqual(art._directionGroups(directions), [0, 1, 2, 3, 0, 5, 2, 7])


class JointTests(unittest.TestCase):
    def kyle_like(self):
        meshes = [box('hip', (-R, -D, -H), (R, D, O)), box('torso', (-R, -D, -O), (R, D, H)),
                  box('collar', (-R, -D, H - O), (R, D, H + 0.004)), box('thigh', (0, -D, -2 * H), (R, D, -H + O))]
        return model(meshes, [('$$$dummy', -1, -1, (0, 0, 0), (0, 0, 0)),
                              ('$$$dummy', 0, -1, (0, 0, 0), (0, 0, 0)),
                              ('k_hip', 0, 0, (0, 0, 0), (0, 0, 0)),
                              ('k_torso', 1, 1, (0, 0, 0), (0, 0, 0)),
                              ('collar', 3, 2, (0, 0, 0), (0, 0, 0)),
                              ('k_rthigh', 2, 3, (0.005, 0, -H), (0, 0, 0))])

    def test_kyle_waist_joins_sibling_parts_and_undriven_nodes_ride_along(self):
        m = self.kyle_like()
        stand = key({i: [entry(0)] for i in (0, 1, 2, 3, 5)}, joints=6)
        joints = articulationJoints(m, [stand])
        self.assertEqual([(j.label, j.parent, j.child) for j in joints], [('waist', 2, 3), ('r hip', 2, 5)])
        waist = joints[0]
        self.assertEqual(waist.childNodes, (3, 4))     # the collar has no track: it rides with the torso
        self.assertEqual(waist.parentNodes, (2,))
        self.assertEqual(waist.seatNodes, (1, 2, 3))  # carrier, hip and torso place the two parts
        self.assertEqual(joints[1].seatNodes, (5,))

    def test_without_keys_every_node_can_move(self):
        labels = [j.label for j in articulationJoints(self.kyle_like())]
        self.assertEqual(labels, ['waist', 'r hip', 'k_torso-collar'])

    def test_listed_joints_set_the_parts(self):
        m = self.kyle_like()
        # This KEY also drives the collar, as an adapter track would: derived, it is its own part.
        adapter = key({i: [entry(0)] for i in range(6)}, joints=6)
        derived = articulationJoints(m, [adapter])
        self.assertEqual([j.label for j in derived], ['waist', 'r hip', 'k_torso-collar'])
        self.assertEqual(derived[0].childNodes, (3,))
        # Listed, Kyle's pieces carry everything hung under them, as in Red's sweep.
        listed = articulationJoints(m, [adapter], [('k_hip', 'k_torso')])
        self.assertEqual((listed[0].childNodes, listed[0].seatNodes), ((3, 4), (1, 2, 3)))
        self.assertEqual(art.KYLE_JOINT_LABELS['k_torso'], 'waist')
        self.assertEqual(len(art.KYLE_JOINTS), 15)

    def test_explicit_joints_by_name_or_index(self):
        m = self.kyle_like()
        joints = articulationJoints(m, (), [('k_hip', 'k_torso'), ('mine', 2, 5)])
        self.assertEqual([(j.label, j.parent, j.child) for j in joints], [('waist', 2, 3), ('mine', 2, 5)])
        for bad in ([('$$$dummy', 'k_hip')], [(2, 2)], [('k_hip',)], [('nope', 'k_hip')], [(2, 99)]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                articulationJoints(m, (), bad)


class AuditTests(unittest.TestCase):
    def test_sliding_the_child_off_its_seat_opens_daylight(self):
        report = auditArticulation(two_boxes(), [slide(3 * O)])
        joint = report['joints'][0]
        self.assertEqual((joint['label'], joint['anchors']['kind']), ('hip-torso', 'core'))
        count = joint['anchors']['count']
        self.assertEqual((count, joint['anchors']['dropped']), (16, 0))
        self.assertEqual(report['status'], 'review')
        # Frame 0 is seated; frames 2..8 are lifted 3 * O: a gap of O opens between the parts.
        self.assertEqual(report['totals']['poses'], 5)
        self.assertEqual(joint['poses_with_daylight'], 4)
        worst = joint['worst'][0]
        self.assertEqual(worst['frame'], 2.0)
        # Every level camera sees through every anchor; raised cameras mostly meet a cap.
        self.assertTrue({0, 2, 4, 6} <= set(worst['cameras']))
        self.assertGreaterEqual(worst['lines'], 4 * count)
        self.assertAlmostEqual(worst['separation'], 3 * O, places=15)
        self.assertAlmostEqual(joint['max_separation']['value'], 3 * O, places=15)
        self.assertEqual(report['keys'][0]['joints'], [{'joint': 'hip-torso', 'poses': 4,
                                                        'lines': joint['lines']}])
        self.assertIn('lead, not a verdict', report['reading'])

    def test_a_lift_within_the_overlap_stays_closed(self):
        report = auditArticulation(two_boxes(), [slide(1.5 * O)])
        self.assertEqual((report['status'], report['totals']['lines']), ('clean', 0))

    def test_a_small_rotation_inside_the_overlap_stays_closed(self):
        tip = key({1: [entry(0), entry(2, rot=(3.0, 0, 0))]}, name='tip.key')
        report = auditArticulation(two_boxes(), [tip])
        joint = report['joints'][0]
        self.assertEqual((report['status'], joint['lines']), ('clean', 0))
        self.assertGreater(joint['max_separation']['value'], 0.0)

    def test_camera_set_is_respected(self):
        lift = [slide(3 * O)]
        steep = auditArticulation(two_boxes(), lift, cameras=[(0, 70)])
        self.assertEqual((steep['status'], steep['parameters']['cameras']), ('clean', [[0.0, 70.0]]))
        level = auditArticulation(two_boxes(), lift, cameras=[(30, 0)])
        joint = level['joints'][0]
        self.assertEqual(joint['worst'][0]['lines'], joint['anchors']['count'])
        self.assertEqual(joint['worst'][0]['cameras'], [0])
        with self.assertRaises(ValueError):
            auditArticulation(two_boxes(), lift, cameras=[])

    def test_step_is_respected(self):
        for step, poses in ((2.0, 5), (3.0, 4), (1.0, 10), (0.5, 19)):
            with self.subTest(step=step):
                report = auditArticulation(two_boxes(), [slide(3 * O)], step=step)
                self.assertEqual(report['totals']['poses'], poses)
                self.assertEqual(report['parameters']['step'], step)
        with self.assertRaises(ValueError):
            auditArticulation(two_boxes(), [slide(3 * O)], step=0)

    def test_findings_are_bounded_and_totals_count_everything(self):
        lifted = key({1: [entry(0, (0, 0, 3 * O))]}, frames=40, name='lifted.key')
        report = auditArticulation(two_boxes(), {'all forty': lifted}, step=1.0, worst=3)
        joint = report['joints'][0]
        self.assertEqual(report['issue_counts'], {'daylight': 40})
        self.assertEqual(len(report['findings']), art.DETAIL_LIMIT)
        self.assertTrue(report['findings_truncated'])
        self.assertEqual(report['totals']['poses_with_daylight'], 40)
        self.assertEqual(joint['poses_with_daylight'], 40)
        self.assertEqual(len(joint['worst']), 3)
        self.assertEqual(report['keys'][0]['name'], 'all forty')
        self.assertEqual(report['findings'][0]['code'], 'daylight')
        self.assertEqual(report['totals']['lines'], sum(e['lines'] for e in report['keys'][0]['joints']))
        self.assertEqual(json.loads(json.dumps(report)), report)     # plain data, ready for MCP

    def test_membrane_anchors_sit_between_open_rims(self):
        lifted = slide(4 * O, name='lift.key')
        report = auditArticulation(two_tubes(), [lifted])
        waist = report['joints'][0]
        self.assertEqual((waist['label'], waist['anchors']['kind']), ('waist', 'membrane'))
        self.assertEqual((waist['anchors']['count'], waist['anchors']['dropped']), (32, 0))
        self.assertEqual(waist['poses_with_daylight'], 4)
        seated = auditArticulation(two_tubes(), [slide(1.5 * O)])
        self.assertEqual(seated['status'], 'clean')
        core = auditArticulation(two_tubes(), [lifted], settings=AnchorSettings(membraneJoints=()))
        self.assertEqual(core['joints'][0]['anchors']['kind'], 'core')

    def test_anchors_measure_movement_away_from_the_reference(self):
        # The child mesh is built straight, but its rest rotation bends it 40 degrees.
        bent = two_boxes(rotation=(40.0, 0.0, 0.0))
        straight = key({0: [entry(0)], 1: [entry(0)]}, frames=4, name='straight.key')
        fromRest = auditArticulation(bent, [straight])
        self.assertGreater(fromRest['joints'][0]['max_separation']['value'], 0.001)
        built = referencePose(bent, straight, 0, zeroRotations=True)
        fromCut = auditArticulation(bent, [straight], reference=built)
        self.assertAlmostEqual(fromCut['joints'][0]['max_separation']['value'], 0.0, places=12)
        self.assertEqual(fromCut['parameters']['reference'],
                         'straight.key frame 0: KEY positions with zero rotation; rest elsewhere')
        # A reference that is itself open hides the opening: read the reference as "closed".
        m = two_boxes()
        lifted = key({0: [entry(0)], 1: [entry(0, (0, 0, 3 * O))]}, frames=4, name='lifted.key')
        self.assertEqual(auditArticulation(m, [lifted])['status'], 'review')
        hidden = auditArticulation(m, [lifted], reference=referencePose(m, lifted))
        self.assertEqual(hidden['status'], 'clean')
        other = model([], [('lonely', -1, -1, (0, 0, 0), (0, 0, 0))])
        with self.assertRaises(ValueError):
            auditArticulation(m, [lifted], reference=referencePose(other))

    def test_layers_play_together_and_report_every_pose(self):
        idle = key({1: [entry(0)]}, name='idle.key')
        lift = key({1: [entry(0, (0, 0, 3 * O))]}, frames=4, name='lift.key')
        seen = []
        report = auditArticulation(two_boxes(), [[PoseLayer(idle, 0, lowPri=1), PoseLayer(lift, 0, lowPri=2)]],
                                   onPose=lambda name, frame, joints: seen.append((name, frame, joints[0]['lines'])))
        self.assertEqual(report['keys'][0]['name'], 'idle.key+lift.key')
        self.assertEqual(report['totals']['poses'], 5)          # the first layer's KEY sets the times
        self.assertEqual([s[:2] for s in seen], [('idle.key+lift.key', t) for t in (0.0, 2.0, 4.0, 6.0, 8.0)])
        self.assertTrue(all(lines > 0 for _, _, lines in seen))
        with self.assertRaises(TypeError):
            auditArticulation(two_boxes(), [[idle]])

    def test_bad_arguments_raise_before_any_work(self):
        for bad in ({'geoset': 1}, {'worst': -1}, {'step': -2.0}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                auditArticulation(two_boxes(), [slide(3 * O)], **bad)
        with self.assertRaises(ValueError):
            auditArticulation(two_boxes(), [])

    def test_unmeasured_joints_are_reported(self):
        flat = model([box('lower', (-R, -D, -H), (R, D, O)), box('far', (1, 1, 1), (1.01, 1.01, 1.01))],
                     [('hip', -1, 0, (0, 0, 0), (0, 0, 0)), ('far', 0, 1, (0, 0, 0), (0, 0, 0))])
        report = auditArticulation(flat, [slide(3 * O)])
        self.assertEqual(report['status'], 'no_joints')
        self.assertEqual(report['issue_counts'], {'joint_not_measured': 1})
        self.assertEqual(report['findings'][0]['code'], 'joint_not_measured')
        self.assertFalse(report['joints'][0]['measured'])

    def test_settings_scale_lengths_only_and_reject_nonsense(self):
        half = AnchorSettings().scaled(0.5)
        self.assertEqual((half.nearRadius, half.rimMatch, half.rays, half.weld), (0.0225, 0.003, 16, 3e-6))
        for bad in ({'rays': 2}, {'fraction': 0.0}, {'maxRadius': -1.0}, {'membraneJoints': 'waist'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                AnchorSettings(**bad)


class ReferenceTests(unittest.TestCase):
    def test_reference_pose_takes_key_seats_and_optionally_zero_rotation(self):
        m = two_boxes(rotation=(40.0, 0.0, 0.0))
        stand = key({1: [entry(0, (0, 0.002, 0), (10, 20, 30))]}, name='stand.key')
        withRotation = referencePose(m, stand)
        np.testing.assert_allclose(withRotation.local.positions[1], [0, 0.002, 0])
        np.testing.assert_allclose(withRotation.local.rotations, [[0, 0, 0], [10, 20, 30]])
        zero = referencePose(m, stand, zeroRotations=True)
        np.testing.assert_allclose(zero.local.rotations, 0.0)
        np.testing.assert_allclose(zero.frames[1][:3, 3], [0, 0.002, 0])
        self.assertEqual(referencePose(m).description, 'rest pose')
        np.testing.assert_allclose(referencePose(m).local.rotations[1], [40, 0, 0])
        with self.assertRaises(ValueError):
            referencePose(m, zeroRotations=True)


class SeatOffsetTests(unittest.TestCase):
    def test_exact_offsets_with_their_frames(self):
        creep = key({0: [entry(0)], 1: [entry(0, dpos=(0, 0, 0.001), flags=KeyframeFlag.PositionChange)]},
                    name='creep.key')
        report = seatOffsets(two_boxes(), [creep])
        self.assertEqual((report['status'], report['issue_counts']), ('off_seat', {'off_seat': 1}))
        finding = report['findings'][0]
        self.assertEqual((finding['key'], finding['node'], finding['name'], finding['frame']),
                         ('creep.key', 1, 'torso', 9.0))
        self.assertAlmostEqual(finding['offset'], 0.009, places=15)
        np.testing.assert_allclose(finding['vector'], [0, 0, 0.009], atol=1e-15)
        self.assertEqual([(n['index'], n['keys'], n['keys_off_seat']) for n in report['nodes']], [(0, 1, 0), (1, 1, 1)])
        self.assertEqual(report['nodes'][0]['largest']['offset'], 0.0)
        self.assertEqual(report['keys'][0]['tracks'], 2)
        self.assertEqual(report['parameters'], {'reference': 'rest positions', 'tolerance': 2e-5})
        self.assertEqual(json.loads(json.dumps(report)), report)

    def test_entry_frames_between_whole_frames_are_measured(self):
        jump = key({1: [entry(0), entry(2.5, (0.004, 0, 0)), entry(3)]}, frames=5, name='jump.key')
        largest = seatOffsets(two_boxes(), [jump])['findings'][0]
        self.assertEqual((largest['frame'], largest['offset']), (2.5, 0.004))

    def test_a_reference_key_sets_the_seats(self):
        stand = key({1: [entry(0, (0, 0, 0.002))]}, name='stand.key')
        lifted = key({1: [entry(0, (0, 0, 0.002))]}, name='lifted.key')
        m = two_boxes()
        self.assertEqual(seatOffsets(m, [lifted])['status'], 'off_seat')
        fromStand = seatOffsets(m, [lifted], reference=referencePose(m, stand))
        self.assertEqual((fromStand['status'], fromStand['findings']), ('on_seat', []))
        self.assertIn('stand.key frame 0', fromStand['parameters']['reference'])
        self.assertEqual(seatOffsets(m, [key({})])['status'], 'no_tracks')

    def test_findings_are_bounded(self):
        keys = [key({1: [entry(0, (0, 0, 0.001 * (k + 1)))]}, name='k%02d.key' % k) for k in range(40)]
        report = seatOffsets(two_boxes(), keys)
        self.assertEqual((len(report['findings']), report['findings_truncated']), (art.DETAIL_LIMIT, True))
        self.assertEqual(report['issue_counts'], {'off_seat': 40})
        self.assertEqual(report['findings'][0]['key'], 'k39.key')
        self.assertEqual(report['nodes'][0]['largest']['key'], 'k39.key')


class KeyFolderTests(unittest.TestCase):
    def test_folder_loads_sorted_and_collects_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('b.KEY', 'a.key'):
                k = key({1: [entry(0)]}, name=name)
                k.fps = 15.0
                saveKey(k, Path(folder) / name, 'synthetic')
            (Path(folder) / 'notes.txt').write_text('not a KEY')
            self.assertEqual(list(loadKeyFolder(folder)), ['a.key', 'b.KEY'])
            (Path(folder) / 'c.key').write_text('SECTION: HEADER\nFLAGS 0x0\nNOT A KEY\n')
            errors = []
            self.assertEqual(list(loadKeyFolder(folder, errors=errors)), ['a.key', 'b.KEY'])
            self.assertEqual(len(errors), 1)
            self.assertTrue(errors[0].startswith('c.key: '))
            with self.assertRaises(Exception):
                loadKeyFolder(folder)


if __name__ == '__main__':
    unittest.main()
