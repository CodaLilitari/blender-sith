# SPDX-License-Identifier: GPL-3.0-or-later
"""Retail model budget on synthetic models (no Blender, no game data)."""
from pathlib import Path
import tempfile
import unittest

from sith.audit.budget import DETAIL_LIMIT, auditBudget, budgetWarnings, kModelLimits
from sith.model.model3do import (Mesh3do, Mesh3doFace, Mesh3doNode, Model3do, Model3doGeoSet,
                                 setDetailLevels)
from sith.model.model3doLoader import Model3doFileVersion, load3do
from sith.model.model3doWriter import save3do
from sith.types import Vector2f, Vector3f, Vector4f


def mesh(index, name, vertices=3, uvs=3, faces=1, corners=3, material=0):
    """A mesh with the requested counts; every face uses the first corners of the vertex list."""
    result = Mesh3do(index, name)
    result.vertices = [Vector3f(float(i), 0.0, 0.0) for i in range(vertices)]
    result.vertexColors = [Vector4f(0.0, 0.0, 0.0, 1.0)] * vertices
    result.normals = [Vector3f(0.0, 0.0, 1.0)] * vertices
    result.uvs = [Vector2f(float(i), 0.0) for i in range(uvs)]
    for _ in range(faces):
        face = Mesh3doFace()
        face.materialIdx = material
        face.vertexIdxs = [i % max(vertices, 1) for i in range(corners)]
        face.uvIdxs = [i % max(uvs, 1) for i in range(corners)]
        face.normal = Vector3f(0.0, 0.0, 1.0)
        result.faces.append(face)
    return result


def model(meshes, geosets=1, nodes=None, materials=('a.mat',)):
    """One node per mesh in a chain (or ``nodes`` empty joints), ``geosets`` copies of the meshes."""
    result = Model3do('budget.3do')
    result.materials = list(materials)
    result.radius = 1.0
    result.insertOffset = Vector3f(0.0, 0.0, 0.25)
    for _ in range(geosets):
        geoset = Model3doGeoSet()
        geoset.meshes = [mesh(*spec) if isinstance(spec, tuple) else spec() for spec in meshes]
        result.geosets.append(geoset)
    count = len(meshes) if nodes is None else nodes
    for i in range(count):
        node = Mesh3doNode('joint%d' % i)
        node.idx = i
        node.meshIdx = i if i < len(meshes) else -1
        node.parentIdx = i - 1
        node.firstChildIdx = i + 1 if i + 1 < count else -1
        node.numChildren = 1 if i + 1 < count else 0
        result.meshHierarchy.append(node)
    return result


class BudgetTests(unittest.TestCase):
    def test_counts_totals_and_largest_meshes_per_geoset(self):
        subject = model([(0, 'body', 40, 50, 30), (1, 'head', 60, 20, 10, 4)])
        extra = Model3doGeoSet()
        extra.meshes = [mesh(0, 'body_lod', 10, 12, 8), mesh(1, 'head_lod', 5, 6, 2)]
        subject.geosets.append(extra)
        report = auditBudget(subject)
        self.assertEqual(report['status'], 'within_limits')
        self.assertEqual((report['geoset_count'], report['hierarchy_nodes'], report['materials']), (2, 2, 1))
        first, second = report['geosets']
        self.assertEqual((first['meshes'], first['vertices'], first['texture_vertices'], first['faces']),
                         (2, 100, 70, 40))
        self.assertEqual(first['largest']['vertices'], {'index': 1, 'name': 'head', 'count': 60})
        self.assertEqual(first['largest']['texture_vertices'], {'index': 0, 'name': 'body', 'count': 50})
        self.assertEqual(first['largest']['face_corners'], {'index': 1, 'name': 'head', 'count': 4})
        self.assertEqual(first['mesh_counts'][0], {'index': 0, 'name': 'body', 'vertices': 40,
                                                   'texture_vertices': 50, 'faces': 30, 'face_corners': 3})
        self.assertEqual((second['vertices'], second['faces']), (15, 10))
        self.assertEqual((report['issue_counts'], report['findings'], report['findings_truncated']),
                         ({}, [], False))
        self.assertEqual(budgetWarnings(report), [])

    def test_meshes_exactly_at_the_retail_limits_are_within_them(self):
        limits = kModelLimits['JKDF2']
        subject = model([(0, 'full', limits['vertices'], limits['texture_vertices'], limits['faces'],
                          limits['face_corners'])], geosets=4, nodes=64)
        self.assertEqual(auditBudget(subject)['status'], 'within_limits')
        self.assertEqual(limits, {'vertices': 512, 'texture_vertices': 768, 'faces': 512,
                                  'face_corners': 24, 'geosets': 4, 'hierarchy_nodes': 64})

    def test_each_mesh_limit_names_the_mesh_and_how_far_it_is_over(self):
        report = auditBudget(model([(0, 'ok'), (1, 'torso', 513, 770, 515)]))
        self.assertEqual(report['status'], 'over_limits')
        self.assertEqual(report['issue_counts'], {'too_many_vertices': 1, 'too_many_texture_vertices': 1,
                                                  'too_many_faces': 1})
        expected = {'geoset': 0, 'mesh': 1, 'name': 'torso'}
        self.assertEqual(report['findings'], [
            {'code': 'too_many_vertices', **expected, 'count': 513, 'limit': 512, 'over': 1},
            {'code': 'too_many_texture_vertices', **expected, 'count': 770, 'limit': 768, 'over': 2},
            {'code': 'too_many_faces', **expected, 'count': 515, 'limit': 512, 'over': 3}])
        self.assertEqual(budgetWarnings(report)[0],
                         "Mesh 1 'torso' has 513 vertices; JKDF2 allows 512 per mesh (1 over) in geoset 0")

    def test_face_corner_limit_and_faces_without_corners(self):
        subject = model([(0, 'cap', 30, 30, 2, 25)])
        subject.geosets[0].meshes[0].faces[1].vertexIdxs = []
        report = auditBudget(subject)
        self.assertEqual(report['issue_counts'], {'too_many_face_corners': 1, 'empty_face': 1})
        self.assertEqual(report['findings'][0], {'code': 'too_many_face_corners', 'geoset': 0, 'mesh': 0,
                                                 'name': 'cap', 'face': 0, 'count': 25, 'limit': 24, 'over': 1})
        self.assertEqual(report['findings'][1], {'code': 'empty_face', 'geoset': 0, 'mesh': 0, 'name': 'cap',
                                                 'face': 1})

    def test_geoset_and_hierarchy_node_limits(self):
        report = auditBudget(model([(0, 'm')], geosets=5, nodes=65))
        self.assertEqual(report['findings'][:2], [
            {'code': 'too_many_geosets', 'count': 5, 'limit': 4, 'over': 1},
            {'code': 'too_many_hierarchy_nodes', 'count': 65, 'limit': 64, 'over': 1}])
        self.assertIn('Model has 5 geosets; JKDF2 allows 4 (1 over)', budgetWarnings(report))

    def test_unchecked_material_and_mesh_lookups_are_reported(self):
        subject = model([(0, 'a', 3, 3, 1, 3, 1), (1, 'b', 3, 3, 1, 3, -1), (2, 'c', 3, 3, 1, 3, -2)],
                        materials=('one.mat',))
        subject.meshHierarchy[2].meshIdx = 7
        report = auditBudget(subject)
        self.assertEqual(report['issue_counts'], {'material_out_of_range': 2, 'missing_mesh': 1})
        self.assertEqual([item['material'] for item in report['findings'][:2]], [1, -2])
        self.assertEqual(report['findings'][2], {'code': 'missing_mesh', 'geoset': 0, 'node': 2,
                                                 'node_name': 'joint2', 'mesh': 7, 'meshes': 3})

    def test_findings_are_bounded_while_totals_count_every_problem(self):
        report = auditBudget(model([(0, 'fan', 30, 30, DETAIL_LIMIT + 8, 25)]))
        self.assertEqual(report['issue_counts'], {'too_many_face_corners': DETAIL_LIMIT + 8})
        self.assertEqual(len(report['findings']), DETAIL_LIMIT)
        self.assertTrue(report['findings_truncated'])
        self.assertEqual(budgetWarnings(report)[-1], "8 more 'too_many_face_corners' findings are not listed")

    def test_identical_problems_in_detail_level_copies_share_one_warning(self):
        subject = setDetailLevels(model([(0, 'torso', 600)]), 4)
        report = auditBudget(subject)
        self.assertEqual(report['issue_counts'], {'too_many_vertices': 4})
        self.assertEqual(budgetWarnings(report), [
            "Mesh 0 'torso' has 600 vertices; JKDF2 allows 512 per mesh (88 over) in geosets 0, 1, 2, 3"])

    def test_mots_shares_the_limits_and_unsourced_targets_report_counts_only(self):
        oversized = model([(0, 'big', 900, 900, 900, 30)], geosets=5, nodes=70)
        self.assertEqual(auditBudget(oversized, 'MOTS')['issue_counts'], auditBudget(oversized)['issue_counts'])
        for target in ('IJIM', 'GRIM'):
            with self.subTest(target=target):
                report = auditBudget(oversized, target)
                self.assertEqual((report['status'], report['limits'], report['findings'], report['issue_counts']),
                                 ('counts_only', None, [], {}))
                self.assertEqual(report['geosets'][0]['vertices'], 900)
        for target in ('jkdf2', 'IJIM2', None):
            with self.subTest(target=target), self.assertRaises(ValueError):
                auditBudget(oversized, target)

    def test_empty_models_have_no_counts_and_no_findings(self):
        report = auditBudget(Model3do('empty.3do'))
        self.assertEqual((report['status'], report['geosets'], report['geoset_count']), ('within_limits', [], 0))

    def test_parsed_text_model_counts_match_the_written_model_and_the_input_is_unchanged(self):
        subject = model([(0, 'body', 40, 50, 30), (1, 'head', 60, 20, 10, 4)], geosets=2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'budget.3do'
            save3do(subject, path, Model3doFileVersion.Version2_1, '')
            before = path.read_bytes()
            written = auditBudget(subject)
            save3do(subject, path, Model3doFileVersion.Version2_1, '')
            self.assertEqual(path.read_bytes(), before)
            parsed = auditBudget(load3do(path)[0])
        self.assertEqual(parsed, written)


if __name__ == '__main__':
    unittest.main()
