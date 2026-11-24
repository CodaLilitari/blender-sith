# SPDX-License-Identifier: GPL-3.0-or-later
"""Identical detail levels (geosets) written natively and parsed back (no Blender)."""
from pathlib import Path
import tempfile
import unittest

import numpy as np

from sith.audit.budget import auditBudget
from sith.audit.pose import poseModel
from sith.model import setDetailLevels
from sith.model.model3do import Model3doGeoSet
from sith.model.model3doLoader import Model3doFileVersion, load3do
from sith.model.model3doWriter import save3do
from sith.types import Vector3f

from core.test_audit_budget import mesh, model


def chain():
    """Three meshes on a parented chain with offsets, rotations and a pivot."""
    subject = model([(0, 'hip', 8, 6, 4, 4), (1, 'torso', 6, 5, 3), (2, 'head', 5, 4, 2)])
    for node, position, rotation, pivot in zip(subject.meshHierarchy,
                                              ((0, 0, 0.1), (0, 0.01, 0.05), (0, 0, 0.04)),
                                              ((0, 0, 0), (10, 20, 0), (0, -15, 5)),
                                              ((0, 0, 0), (0.002, 0, 0.003), (0, 0, 0.01))):
        node.position, node.rotation, node.pivot = (Vector3f(*position), Vector3f(*rotation),
                                                    Vector3f(*pivot))
    for index, item in enumerate(subject.geosets[0].meshes):
        item.vertices = [Vector3f(0.01 * index + 0.001 * k, -0.002 * k, 0.003 * k) for k in range(len(item.vertices))]
        item.radius = 0.5
    subject.radius = 0.75
    return subject


def text(model3do):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'lod.3do'
        save3do(model3do, path, Model3doFileVersion.Version2_1, 'detail levels')
        return path.read_text(encoding='utf-8')


def geosetBlocks(source):
    """The GEOMETRYDEF text before the first geoset, and each geoset's text after its number."""
    geometry = source.split('SECTION: GEOMETRYDEF\n', 1)[1].split('###############\nSECTION: HIERARCHYDEF', 1)[0]
    head, *blocks = geometry.split('# Geometry Set definition\n')
    numbers = [block.split('\n', 1)[0] for block in blocks]
    return head, numbers, [block.split('\n', 1)[1] for block in blocks]


class DetailLevelTests(unittest.TestCase):
    def test_copies_are_identical_and_independent_of_geoset_zero(self):
        subject = setDetailLevels(chain(), 4)
        self.assertEqual(len(subject.geosets), 4)
        first = subject.geosets[0]
        for copy in subject.geosets[1:]:
            self.assertIsNot(copy, first)
            for original, duplicate in zip(first.meshes, copy.meshes):
                self.assertIsNot(duplicate, original)
                self.assertEqual((duplicate.idx, duplicate.name, duplicate.radius, duplicate.vertices,
                                  duplicate.uvs, duplicate.normals),
                                 (original.idx, original.name, original.radius, original.vertices,
                                  original.uvs, original.normals))
                self.assertEqual([face.vertexIdxs for face in duplicate.faces],
                                 [face.vertexIdxs for face in original.faces])
        subject.geosets[2].meshes[0].faces[0].vertexIdxs.append(0)
        subject.geosets[3].meshes[1].vertices[0] = Vector3f(9.0, 9.0, 9.0)
        self.assertEqual(len(first.meshes[0].faces[0].vertexIdxs), 4)
        self.assertNotEqual(first.meshes[1].vertices[0], Vector3f(9.0, 9.0, 9.0))

    def test_parse_back_keeps_radius_insert_offset_hierarchy_and_every_geoset(self):
        original = chain()
        single = text(original)
        written = text(setDetailLevels(chain(), 3))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lod.3do'
            path.write_text(written, encoding='utf-8')
            parsed, version = load3do(path)
        self.assertEqual(version, Model3doFileVersion.Version2_1)
        self.assertEqual(len(parsed.geosets), 3)
        self.assertEqual(parsed.radius, original.radius)
        self.assertEqual(tuple(parsed.insertOffset), tuple(original.insertOffset))
        self.assertEqual([(node.name, node.meshIdx, node.parentIdx, tuple(node.position), tuple(node.rotation),
                           tuple(node.pivot)) for node in parsed.meshHierarchy],
                         [(node.name, node.meshIdx, node.parentIdx, tuple(node.position), tuple(node.rotation),
                           tuple(node.pivot)) for node in original.meshHierarchy])
        # Each written geoset is the one-geoset file's geoset; the rest of the file is unchanged.
        head, numbers, blocks = geosetBlocks(written)
        singleHead, singleNumbers, singleBlocks = geosetBlocks(single)
        self.assertEqual((numbers, singleNumbers), (['GEOSET 0', 'GEOSET 1', 'GEOSET 2'], ['GEOSET 0']))
        self.assertEqual(blocks, singleBlocks * 3)
        self.assertEqual(head, singleHead.replace('GEOSETS 1', 'GEOSETS 3'))
        self.assertEqual(written.split('SECTION: GEOMETRYDEF')[0], single.split('SECTION: GEOMETRYDEF')[0])
        self.assertEqual(written.split('SECTION: HIERARCHYDEF')[1], single.split('SECTION: HIERARCHYDEF')[1])
        # The engine poses every geoset to the same world vertices.
        poses = [poseModel(parsed, geoset=index).vertices for index in range(3)]
        for pose in poses[1:]:
            self.assertEqual(sorted(pose), sorted(poses[0]))
            for key in pose:
                np.testing.assert_array_equal(pose[key], poses[0][key])
        report = auditBudget(parsed)
        self.assertEqual([summary['mesh_counts'] for summary in report['geosets'][1:]],
                         [report['geosets'][0]['mesh_counts']] * 2)

    def test_one_level_keeps_geoset_zero_and_replaces_other_geosets(self):
        subject = chain()
        first = subject.geosets[0]
        extra = Model3doGeoSet()
        extra.meshes = [mesh(0, 'lowpoly')]
        subject.geosets.append(extra)
        setDetailLevels(subject, 1)
        self.assertEqual(len(subject.geosets), 1)
        self.assertIs(subject.geosets[0], first)
        self.assertEqual(text(subject), text(chain()))

    def test_counts_outside_one_to_four_and_models_without_geosets_reject(self):
        for count in (0, 5, -1, 1.5, '2', None):
            with self.subTest(count=count), self.assertRaises(ValueError):
                setDetailLevels(chain(), count)
        empty = chain()
        empty.geosets = []
        with self.assertRaisesRegex(ValueError, 'geoset 0'):
            setDetailLevels(empty, 2)
        self.assertEqual(len(setDetailLevels(chain(), np.int64(2)).geosets), 2)


if __name__ == '__main__':
    unittest.main()
