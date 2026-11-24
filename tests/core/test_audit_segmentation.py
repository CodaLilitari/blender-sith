# SPDX-License-Identifier: GPL-3.0-or-later
"""Rigid-piece cut lint and face removal on synthetic models (no Blender, no game data)."""
import json
import math
import unittest

import numpy as np

from sith.audit.pose import Pose
from sith.audit.segmentation import (KYLE_JOINT_NAMES, auditSegmentation, drivenNodes, partBones,
                                     pieceColours, rigidParts, testPoses, windingNumbers,
                                     zeroRotationPose)
from sith.key.key import Key, Keyframe, KeyframeFlag, KeyNode
from sith.model.model3do import Mesh3do, Mesh3doFace, Mesh3doNode, Model3do, Model3doGeoSet
from sith.model.model3doEdit import (componentLabels, copyModel, removeFaces, triangulatePolygon,
                                     weldPositions)
from sith.types import Vector2f, Vector3f, Vector4f

SHOULDER = (0.03, 0.0, 0.055)


class MeshBuilder:
    """Vertices deduplicated by position; faces as index lists wound by the right-hand rule."""

    def __init__(self):
        self.points = []
        self.index = {}
        self.faces = []

    def vertex(self, p):
        key = tuple(round(float(x), 9) for x in p)
        if key not in self.index:
            self.index[key] = len(self.points)
            self.points.append(key)
        return self.index[key]

    def face(self, corners):
        self.faces.append([self.vertex(p) for p in corners])
        return len(self.faces) - 1

    def box(self, lo, hi, n=4):
        """Closed box, each side split into n x n quads, every face wound outward."""
        first = len(self.faces)
        for axis in range(3):
            u, v = [a for a in range(3) if a != axis]
            # e_u x e_v is +e_axis for axes 0 and 2 and -e_axis for axis 1.
            sign = -1 if axis == 1 else 1
            for side in (0, 1):
                outward = 1 if side else -1
                for i in range(n):
                    for j in range(n):
                        quad = []
                        for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1)):
                            p = [0.0, 0.0, 0.0]
                            p[axis] = hi[axis] if side else lo[axis]
                            p[u] = lo[u] + (hi[u] - lo[u]) * (i + di) / n
                            p[v] = lo[v] + (hi[v] - lo[v]) * (j + dj) / n
                            quad.append(p)
                        if sign * outward < 0:
                            quad.reverse()
                        self.face(quad)
        return list(range(first, len(self.faces)))

    def tetra(self, centre, size):
        c = np.asarray(centre, dtype=float)
        a, b, d, e = (c + size * np.array(x) for x in ((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)))
        first = len(self.faces)
        for tri in ((a, b, d), (a, d, e), (a, e, b), (b, e, d)):
            n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
            if n @ (np.mean(tri, axis=0) - c) < 0:
                tri = (tri[0], tri[2], tri[1])
            self.face(tri)
        return list(range(first, len(self.faces)))

    def mesh(self, idx, name, offset=(0.0, 0.0, 0.0), material=0):
        mesh = Mesh3do(idx, name)
        off = np.asarray(offset, dtype=float)
        mesh.vertices = [Vector3f(*(np.array(p) - off)) for p in self.points]
        mesh.vertexColors = [Vector4f(1.0, 1.0, 1.0, 1.0) for _ in self.points]
        mesh.normals = [Vector3f(0.0, 0.0, 1.0) for _ in self.points]
        mesh.uvs = [Vector2f(p[0] * 10.0, p[2] * 10.0) for p in self.points]
        for corners in self.faces:
            face = Mesh3doFace()
            face.materialIdx = material
            face.vertexIdxs = list(corners)
            face.uvIdxs = list(corners)
            pts = np.array([mesh.vertices[k] for k in corners])
            n = np.cross(pts[1] - pts[0], pts[2] - pts[0])
            face.normal = Vector3f(*(n / np.linalg.norm(n)))
            mesh.faces.append(face)
        return mesh


def node(idx, name, parent, mesh, position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0)):
    n = Mesh3doNode(name)
    n.idx = idx
    n.parentIdx = parent
    n.meshIdx = mesh
    n.position = Vector3f(*position)
    n.rotation = Vector3f(*rotation)
    n.pivot = Vector3f(0.0, 0.0, 0.0)
    return n


def character(strip=False, shard=False, shards=0):
    """Torso box at the origin, a head above it and a long right arm hanging beside it.

    ``strip``: a flap of torso faces from the top edge out to the arm's outer side and down it, the
    way a cut leaves arm surface in the torso. ``shard``: a loose tetrahedron of torso faces by the
    end of the arm. ``shards``: that many loose tetrahedra along the arm. Returns the model and the
    torso face indices of each added feature.
    """
    torso = MeshBuilder()
    body = torso.box((-0.02, -0.01, 0.0), (0.02, 0.01, 0.06))
    added = {'body': body, 'connect': [], 'hang': [], 'shard': [], 'shards': []}
    if strip:
        ys = [-0.01, -0.005, 0.0, 0.005, 0.01]
        levels = [0.05, 0.03, 0.01, -0.01]
        for y0, y1 in zip(ys, ys[1:]):
            added['connect'].append(torso.face([(0.02, y1, 0.06), (0.02, y0, 0.06), (0.036, y0, 0.05),
                                                (0.036, y1, 0.05)]))
            for z0, z1 in zip(levels, levels[1:]):
                added['hang'].append(torso.face([(0.036, y0, z0), (0.036, y0, z1), (0.036, y1, z1),
                                                 (0.036, y1, z0)]))
    if shard:
        added['shard'] = torso.tetra((0.038, 0.0, -0.015), 0.002)
    for k in range(shards):
        added['shards'].append(torso.tetra((0.04, -0.004 + 0.008 * (k % 2), 0.015 - 0.001 * k), 0.0006))
    head = MeshBuilder()
    head.box((-0.008, -0.008, 0.065), (0.008, 0.008, 0.085), n=2)
    arm = MeshBuilder()
    arm.box((0.025, -0.005, -0.025), (0.035, 0.005, 0.055), n=4)
    model = Model3do('synthetic.3do')
    geoset = Model3doGeoSet()
    geoset.meshes = [torso.mesh(0, 'torso', material=1), head.mesh(1, 'head', offset=(0.0, 0.0, 0.065)),
                     arm.mesh(2, 'arm', offset=SHOULDER, material=2)]
    model.geosets = [geoset]
    model.meshHierarchy = [node(0, 'k_torso', -1, 0), node(1, 'k_head', 0, 1, (0.0, 0.0, 0.065)),
                           node(2, 'k_rshouldr', 0, 2, SHOULDER)]
    return model, added


def snapshot(model):
    """Every value that an audit or an edit must not change."""
    out = []
    for geoset in model.geosets:
        for mesh in geoset.meshes:
            out.append((mesh.name, tuple(mesh.vertices), tuple(mesh.uvs),
                        tuple((tuple(f.vertexIdxs), tuple(f.uvIdxs), f.materialIdx, tuple(f.normal))
                              for f in mesh.faces)))
    out.append(tuple((n.name, n.parentIdx, n.meshIdx, tuple(n.position), tuple(n.rotation))
                     for n in model.meshHierarchy))
    return out


def entry(frame, pos=(0, 0, 0), rot=(0, 0, 0)):
    k = Keyframe()
    k.frame = frame
    k.flags = KeyframeFlag.NoChange
    k.position = Vector3f(*pos)
    k.orientation = Vector3f(*rot)
    k.deltaPosition = Vector3f(0, 0, 0)
    k.deltaRotation = Vector3f(0, 0, 0)
    return k


def key(tracks, joints=3, frames=2, name='test.key'):
    k = Key(name)
    k.numJoints = joints
    k.numFrames = frames
    k.fps = 15.0
    for idx, entries in tracks.items():
        n = KeyNode()
        n.idx = idx
        n.meshName = 'n%d' % idx
        n.keyframes = entries
        k.nodes.append(n)
    return k


def selected(report, severity):
    faces = set()
    for mesh in report['meshes']:
        if mesh['mesh'] == 0:
            faces.update(mesh[severity + '_faces'])
    return faces


def edges(mesh):
    """Directed welded edges -> faces using them."""
    weld = weldPositions(np.array([tuple(v) for v in mesh.vertices]), 1e-6)
    out = {}
    for fi, face in enumerate(mesh.faces):
        ids = [int(weld[v]) for v in face.vertexIdxs]
        for k in range(len(ids)):
            out.setdefault((ids[k], ids[(k + 1) % len(ids)]), []).append(fi)
    return out


def triangles(mesh):
    pts = np.array([tuple(v) for v in mesh.vertices])
    return np.array([[pts[f.vertexIdxs[0]], pts[f.vertexIdxs[k]], pts[f.vertexIdxs[k + 1]]]
                     for f in mesh.faces for k in range(1, len(f.vertexIdxs) - 1)])


class GeometryHelperTests(unittest.TestCase):
    def test_weld_joins_points_across_cell_boundaries(self):
        pts = [(0, 0, 0), (0.9e-6, 0, 0), (5e-6, 0, 0), (1.9999995e-6, 7, 7), (2.0000005e-6, 7, 7)]
        labels = weldPositions(pts, 1e-6)
        self.assertEqual(labels[0], labels[1])
        self.assertNotEqual(labels[0], labels[2])
        self.assertEqual(labels[3], labels[4])
        with self.assertRaises(ValueError):
            weldPositions(pts, 0.0)

    def test_component_labels_are_smallest_members(self):
        self.assertEqual(list(componentLabels(6, [0, 4, 3], [1, 3, 5])), [0, 0, 2, 3, 3, 3])
        self.assertEqual(list(componentLabels(3, [], [])), [0, 1, 2])

    def test_winding_number_inside_outside_and_open(self):
        builder = MeshBuilder()
        builder.box((0, 0, 0), (1, 1, 1), n=1)
        mesh = builder.mesh(0, 'cube')
        tris = triangles(mesh)
        w = windingNumbers([(0.5, 0.5, 0.5), (2.0, 0.5, 0.5), (0.5, 0.5, -3.0)], tris)
        np.testing.assert_allclose(w, [1.0, 0.0, 0.0], atol=1e-9)
        np.testing.assert_allclose(windingNumbers([(0.5, 0.5, 0.5)], tris[:, ::-1]), [-1.0], atol=1e-9)
        # Without its top the box is open: deep inside still reads above one half.
        open_top = [t for t in tris if not np.allclose(t[:, 2], 1.0)]
        self.assertGreater(windingNumbers([(0.5, 0.5, 0.2)], open_top)[0], 0.5)
        self.assertLess(windingNumbers([(0.5, 0.5, 1.5)], open_top)[0], 0.5)

    def test_triangulation_keeps_the_loop_winding(self):
        square = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
        tris = triangulatePolygon(square)
        self.assertEqual(len(tris), 2)
        area = sum(0.5 * np.linalg.norm(np.cross(np.subtract(square[b], square[a]), np.subtract(square[c], square[a])))
                   for a, b, c in tris)
        self.assertAlmostEqual(area, 1.0)
        # A concave L: the least-area triangulation cannot overlap itself.
        ell = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (1, 2, 0), (0, 2, 0)]
        tris = triangulatePolygon(ell)
        area = sum(0.5 * np.linalg.norm(np.cross(np.subtract(ell[b], ell[a]), np.subtract(ell[c], ell[a])))
                   for a, b, c in tris)
        self.assertEqual(len(tris), 4)
        self.assertAlmostEqual(area, 3.0)
        # A saddle far from any plane: every edge is used once in each direction, loop edges once forward.
        saddle = [(math.cos(t), math.sin(t), 0.6 * math.cos(2 * t)) for t in np.linspace(0, 2 * math.pi, 9)[:-1]]
        tris = triangulatePolygon(saddle)
        self.assertEqual(len(tris), 6)
        directed = {}
        for a, b, c in tris:
            for e in ((a, b), (b, c), (c, a)):
                directed[e] = directed.get(e, 0) + 1
        for (a, b), count in directed.items():
            self.assertEqual(count, 1)
            if (b - a) % 8 == 1:
                continue
            if (a - b) % 8 == 1:
                self.fail('a loop edge runs against the loop')
            self.assertIn((b, a), directed)


class PartTests(unittest.TestCase):
    def hierarchy(self, names):
        model = Model3do('parts')
        parents = [-1, 0, 1, 2, 3]
        model.meshHierarchy = [node(i, name, parents[i], -1) for i, name in enumerate(names)]
        return model

    def test_kyle_names_start_parts_and_other_nodes_follow_their_parent(self):
        model = self.hierarchy(['$$$dummy', 'k_torso', 'adapter', 'k_lshouldr', 'pad'])
        self.assertEqual(rigidParts(model), [0, 1, 1, 3, 3])
        self.assertEqual(rigidParts(model, joints=['K_TORSO', 4]), [0, 1, 1, 1, 4])
        self.assertEqual(rigidParts(self.hierarchy(['a', 'b', 'c', 'd', 'e'])), [0, 1, 2, 3, 4])
        with self.assertRaises(ValueError):
            rigidParts(model, joints=['k_nothere'])
        with self.assertRaises(ValueError):
            rigidParts(model, joints=[9])
        self.assertIn('k_rfoot', KYLE_JOINT_NAMES)

    def test_driven_nodes_ignore_tracks_past_joints(self):
        model = self.hierarchy(['$$$dummy', 'k_torso', 'adapter', 'k_lshouldr', 'pad'])
        k = key({0: [entry(0)], 3: [entry(0)], 7: [entry(0)]}, joints=5)
        self.assertEqual(drivenNodes(model, [k]), [0, 3])
        self.assertEqual(rigidParts(model, joints=drivenNodes(model, [k])), [0, 0, 0, 3, 3])

    def test_zero_rotation_pose_uses_stand_positions(self):
        model, _ = character()
        model.meshHierarchy[2].rotation = Vector3f(20.0, 10.0, 5.0)
        stand = key({2: [entry(0, pos=(0.031, 0.0, 0.054), rot=(40.0, 0.0, 0.0))]})
        pose = zeroRotationPose(model, stand=stand)
        self.assertIsInstance(pose, Pose)
        np.testing.assert_allclose(pose.local.rotations[2], [0, 0, 0])
        np.testing.assert_allclose(pose.frames[2][:3, 3], [0.031, 0.0, 0.054])
        np.testing.assert_allclose(pose.frames[2][:3, :3], np.eye(3), atol=1e-12)
        pose = zeroRotationPose(model)
        np.testing.assert_allclose(pose.frames[2][:3, 3], SHOULDER)
        np.testing.assert_allclose(pose.frames[2][:3, :3], np.eye(3), atol=1e-12)

    def test_bones_run_to_child_joints_or_toward_the_geometry(self):
        model, _ = character()
        bones = partBones(model)
        torso = {tuple(np.round(seg[1], 6)) for seg in bones[0]}
        self.assertEqual(torso, {(0.0, 0.0, 0.065), SHOULDER})
        arm = bones[2]
        self.assertEqual(arm.shape, (1, 2, 3))
        np.testing.assert_allclose(arm[0, 0], SHOULDER)
        self.assertLess(arm[0, 1, 2], 0.0)            # down the arm, past the torso's bottom
        np.testing.assert_allclose(arm[0, 1, :2], SHOULDER[:2], atol=1e-9)


class AuditTests(unittest.TestCase):
    def test_clean_control_model(self):
        model, _ = character()
        before = snapshot(model)
        report = auditSegmentation(model)
        self.assertEqual(report['status'], 'clean')
        self.assertEqual(report['severity_counts']['high'], 0)
        self.assertEqual(report['severity_counts']['review'], 0)
        self.assertEqual(report['findings'], [])
        self.assertEqual(auditSegmentation(model, detail='info')['severity_counts']['info'], 0)
        self.assertEqual(snapshot(model), before)

    def test_skin_facing_a_close_limb_is_info(self):
        # The arm hangs 1 mm from the flank: the flank is nearer the arm's bone, far from the shoulder,
        # but its normals point at the arm. That is the torso's own skin, not arm surface.
        model, _ = character()
        model.meshHierarchy[2].position = Vector3f(0.026, 0.0, 0.055)
        report = auditSegmentation(model, detail='info')
        self.assertEqual(report['status'], 'clean')
        flank, = report['findings']
        self.assertEqual((flank['part_name'], flank['claimed_by_name'], flank['severity']),
                         ('k_torso', 'k_rshouldr', 'info'))
        self.assertGreater(flank['facing'], 0.5)
        self.assertGreater(flank['lever'], 0.035)
        self.assertEqual(auditSegmentation(model, facingMax=1.0)['severity_counts']['high'], 1)

    def test_strip_and_shard_are_high(self):
        model, added = character(strip=True, shard=True)
        before = snapshot(model)
        report = auditSegmentation(model)
        self.assertEqual(snapshot(model), before)
        self.assertEqual(report['status'], 'suspect')
        json.dumps(report)
        high = [f for f in report['findings'] if f['severity'] == 'high']
        self.assertEqual(len(high), 2)
        island = next(f for f in high if f['code'] == 'stray_island')
        self.assertEqual(island['meshes'], [{'mesh': 0, 'name': 'torso', 'faces': added['shard']}])
        self.assertEqual((island['claimed_by'], island['relation'], island['joint']), (2, 'descendant', 2))
        self.assertGreater(island['gap'], 0.0)
        self.assertGreater(island['lever'], 0.035)
        strip = next(f for f in high if f['code'] != 'stray_island')
        faces = set(strip['meshes'][0]['faces'])
        self.assertEqual((strip['code'], strip['claimed_by_name'], strip['joint_name']),
                         ('foreign_surface', 'k_rshouldr', 'k_rshouldr'))
        self.assertTrue(set(added['hang'][1::3]) | set(added['hang'][2::3]) <= faces)  # the hanging part
        self.assertFalse(faces & set(added['body']))
        self.assertLessEqual(strip['facing'], 0.5)
        self.assertEqual(selected(report, 'high'), faces | set(added['shard']))
        torso = report['meshes'][0]
        self.assertEqual((torso['mesh'], torso['node_name'], torso['part_name']), (0, 'k_torso', 'k_torso'))

    def test_fix_removes_the_findings_and_keeps_the_input(self):
        model, added = character(strip=True, shard=True)
        before = snapshot(model)
        fixed, report = removeFaces(model, 0, added['connect'] + added['hang'] + added['shard'])
        self.assertEqual(snapshot(model), before)
        self.assertEqual(report['cap_faces'], 0)                  # the flap and the shard leave no opening
        self.assertEqual(report['faces_after'], len(added['body']))
        self.assertEqual(report['open_edges_after'], 0)
        self.assertEqual(auditSegmentation(fixed)['status'], 'clean')

    def test_test_poses_measure_separation(self):
        model, added = character(strip=True, shard=True)
        swing = key({2: [entry(0, pos=SHOULDER, rot=(90.0, 0.0, 0.0))]}, name='swing.key')
        still = key({2: [entry(0, pos=SHOULDER)]}, name='still.key')
        poses = testPoses([still, swing], step=1.0)
        self.assertEqual([label for label, _ in poses], ['still.key@0', 'still.key@1', 'swing.key@0', 'swing.key@1'])
        report = auditSegmentation(model, poses=poses)
        self.assertEqual(report['poses'], 4)
        for finding in report['findings']:
            if finding['severity'] == 'high':
                self.assertEqual(finding['worst_pose'], 'swing.key@0')
                self.assertGreater(finding['separation'], 0.05)
        # Separation can raise a region that does not reach leverHigh, only when asked to.
        quiet = auditSegmentation(model, poses=poses, leverHigh=1.0)
        self.assertEqual(quiet['severity_counts']['high'], 0)
        raised = auditSegmentation(model, poses=poses, leverHigh=1.0, separationHigh=0.05)
        self.assertEqual(raised['severity_counts']['high'], 2)
        self.assertEqual(auditSegmentation(model, leverHigh=1.0, separationHigh=0.05)['severity_counts']['high'], 0)

    def test_findings_are_bounded_and_counted(self):
        model, added = character(shards=40)
        report = auditSegmentation(model)
        self.assertEqual(report['issue_counts'].get('stray_island'), 40)
        self.assertEqual(report['severity_counts']['high'], 40)
        self.assertEqual(len(report['findings']), 32)
        self.assertTrue(report['findings_truncated'])
        self.assertEqual(len(selected(report, 'high')), 160)       # every face stays selectable

    def test_reference_pose_and_parameters(self):
        model, _ = character(strip=True)
        frames = zeroRotationPose(model).frames
        self.assertEqual(auditSegmentation(model, reference=frames)['reference'], 'given')
        with self.assertRaises(ValueError):
            auditSegmentation(model, reference=np.eye(4))
        with self.assertRaises(ValueError):
            auditSegmentation(model, detail='everything')
        with self.assertRaises(ValueError):
            auditSegmentation(model, margin=-1.0)
        with self.assertRaises(ValueError):
            auditSegmentation(model, geoset=3)
        # Two lines of evidence: with no face nearer the arm's bone by a huge margin, the strip is still
        # found because its connecting faces run inside the arm's top, and the region grows from there.
        wide = auditSegmentation(model, margin=1.0)
        strip, = [f for f in wide['findings'] if f['severity'] == 'high']
        self.assertEqual((strip['code'], strip['nearer']), ('covered_surface', 0.0))
        self.assertGreater(strip['enclosed'], 0.0)


class RemoveFacesTests(unittest.TestCase):
    def closed_box(self):
        builder = MeshBuilder()
        builder.box((0.0, 0.0, 0.0), (0.04, 0.04, 0.04), n=4)
        mesh = builder.mesh(0, 'box')
        for face in mesh.faces:
            if np.allclose([mesh.vertices[v][0] for v in face.vertexIdxs], 0.04):
                face.materialIdx = 3                              # the +x side
        model = Model3do('box.3do')
        geoset = Model3doGeoSet()
        geoset.meshes = [mesh]
        model.geosets = [geoset]
        model.meshHierarchy = [node(0, 'box', -1, 0)]
        return model

    def patch(self, model):
        mesh = model.geosets[0].meshes[0]
        out = []
        for fi, face in enumerate(mesh.faces):
            pts = np.array([tuple(mesh.vertices[v]) for v in face.vertexIdxs])
            if np.allclose(pts[:, 0], 0.04) and pts[:, 1].min() >= 0.01 - 1e-9 and pts[:, 1].max() <= 0.03 + 1e-9 \
                    and pts[:, 2].min() >= 0.01 - 1e-9 and pts[:, 2].max() <= 0.03 + 1e-9:
                out.append(fi)
        return out

    def test_caps_close_the_opening_with_the_surrounding_texture(self):
        model = self.closed_box()
        before = snapshot(model)
        drop = self.patch(model)
        self.assertEqual(len(drop), 4)
        fixed, report = removeFaces(model, 0, drop)
        self.assertEqual(snapshot(model), before)
        self.assertEqual((report['faces_before'], report['faces_removed'], report['cap_faces']), (96, 4, 6))
        self.assertEqual((report['open_edges_before'], report['open_edges_after']), (0, 0))
        self.assertEqual(report['unclosed'], [])
        opening, = report['openings']
        self.assertEqual((len(opening['vertices']), opening['material'], opening['shared_edges']), (8, 3, 0))
        self.assertAlmostEqual(opening['planarity'], 0.0)
        mesh = fixed.geosets[0].meshes[0]
        self.assertEqual(len(mesh.faces), 98)
        self.assertEqual(opening['cap_faces'], list(range(92, 98)))
        # Closed and consistent: every edge has one face each way.
        for (a, b), faces in edges(mesh).items():
            self.assertEqual(len(faces), 1)
            self.assertIn((b, a), edges(mesh))
        self.assertAlmostEqual(windingNumbers([(0.02, 0.02, 0.02)], triangles(mesh))[0], 1.0)
        for cap in mesh.faces[92:]:
            self.assertEqual(cap.materialIdx, 3)
            self.assertEqual(cap.uvIdxs, cap.vertexIdxs)          # each corner takes its neighbour's texture vertex
            normal = np.array(tuple(cap.normal))
            np.testing.assert_allclose(normal, [1.0, 0.0, 0.0], atol=1e-9)
            self.assertIsNone(cap.binaryData)
        self.assertEqual(report['face_map'][drop[0]], -1)
        self.assertEqual(report['face_map'][0], 0)
        self.assertEqual(report['unused_vertices'], 1)          # the patch's middle vertex

    def test_without_closing_the_opening_stays_open(self):
        model = self.closed_box()
        fixed, report = removeFaces(model, 0, self.patch(model), closeOpenings=False)
        self.assertEqual((report['cap_faces'], report['open_edges_after']), (0, 8))
        self.assertEqual(len(fixed.geosets[0].meshes[0].faces), 92)

    def test_metadata_and_blender_links(self):
        model = self.closed_box()

        class SceneObject:
            def __deepcopy__(self, memo):
                raise AssertionError('Blender objects must not be copied')

        link = SceneObject()
        model.meshHierarchy[0].obj = link
        model.geosets[0].meshes[0].faces[0].binaryData = {'id': 7}
        copied = copyModel(model)
        self.assertIs(copied.meshHierarchy[0].obj, link)
        self.assertIsNot(copied.geosets[0].meshes[0], model.geosets[0].meshes[0])
        fixed, _ = removeFaces(model, 0, self.patch(model))
        self.assertEqual(fixed.geosets[0].meshes[0].faces[0].binaryData, {'id': 7})
        self.assertIsNot(fixed.geosets[0].meshes[0].faces[0].binaryData,
                         model.geosets[0].meshes[0].faces[0].binaryData)

    def test_invalid_requests(self):
        model = self.closed_box()
        for args in ((1, [0]), (0, [96]), (0, [-1])):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    removeFaces(model, *args)
        with self.assertRaises(ValueError):
            removeFaces(model, 0, [0], geoset=1)


class ColourTests(unittest.TestCase):
    def test_colours_are_stable_distinct_and_follow_parts(self):
        model = Model3do('many')
        model.meshHierarchy = [node(i, KYLE_JOINT_NAMES[i] if i < 4 else f'x{i}', i - 1 if i else -1, -1)
                               for i in range(64)]
        colours = pieceColours(model)
        self.assertEqual([c['index'] for c in colours], list(range(64)))
        self.assertEqual(len({c['hex'] for c in colours}), 64)
        small, _ = character()
        self.assertEqual([c['hex'] for c in pieceColours(small)], [c['hex'] for c in colours[:3]])
        for c in colours:
            self.assertTrue(all(0.0 <= x <= 1.0 for x in c['rgb']))
            self.assertRegex(c['hex'], r'^#[0-9a-f]{6}$')
        for i in range(63):                                          # neighbours in index order differ clearly
            a, b = np.array(colours[i]['rgb']), np.array(colours[i + 1]['rgb'])
            self.assertGreater(np.linalg.norm(a - b), 0.25)
        parts = pieceColours(model, by='part')
        self.assertEqual(parts[5]['hex'], parts[4]['hex'])           # x5 moves with x4's part (k_head's)
        self.assertEqual(parts[5]['part'], 3)
        meshes = pieceColours(small, by='mesh')
        self.assertEqual([m['name'] for m in meshes], ['torso', 'head', 'arm'])
        with self.assertRaises(ValueError):
            pieceColours(small, by='face')


if __name__ == '__main__':
    unittest.main()
