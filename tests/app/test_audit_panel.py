# SPDX-License-Identifier: GPL-3.0-or-later
"""The Sith Audits panel in real Blender: retail limits, piece colours and file audits."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import MethodType, SimpleNamespace
from unittest import mock

import bpy

from app.support import SceneTestCase
from sith import app
from sith.audit.budget import auditBudget
from sith.audit.segmentation import pieceColours
from sith.key.key import Key, Keyframe, KeyframeFlag, KeyNode
from sith.key.keyWriter import saveKey
from sith.model import model3doWriter
from sith.model.model3do import Mesh3doNodeType
from sith.model.model3doExporter import export3do, makeModel3doFromObj
from sith.model.model3doLoader import Model3doFileVersion, load3do
from sith.model.utils import kModel3doGeometryMode
from sith.types import Vector3f

R, D, H, O = 0.01, 0.012, 0.02, 0.001   # two boxes that overlap by 2 * O at their seat (JK units)


def box(lo, hi):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    vertices = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    return vertices, [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def entry(frame, position=(0.0, 0.0, 0.0)):
    k = Keyframe()
    k.frame = frame
    k.flags = KeyframeFlag.NoChange
    k.position = Vector3f(*position)
    k.orientation = Vector3f(0.0, 0.0, 0.0)
    k.deltaPosition = Vector3f(0.0, 0.0, 0.0)
    k.deltaRotation = Vector3f(0.0, 0.0, 0.0)
    return k


def key(name, tracks, joints=2, frames=10):
    k = Key(name)
    k.numJoints, k.numFrames, k.fps, k.nodeTypes = joints, frames, 15.0, Mesh3doNodeType.Nothing
    for index, entries in tracks:
        node = KeyNode()
        node.idx, node.meshName, node.keyframes = index, f'n{index}', entries
        k.nodes.append(node)
    return k


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def report_json(text):
    return json.loads(text.as_string().split('Complete report (plain data):\n', 1)[1])


class AuditPanelTests(SceneTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.clear_state()
        self.addCleanup(self.clear_state)
        self.reports = []

    @staticmethod
    def clear_state():
        for text in list(bpy.data.texts):
            bpy.data.texts.remove(text)
        scene = bpy.context.scene
        if app.kSavedColorType in scene:
            del scene[app.kSavedColorType]

    def add_object(self, name, vertices, faces, parent=None):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(vertices, [], faces)
        mesh.uv_layers.new()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        setattr(obj, kModel3doGeometryMode, 'Texture')
        obj.parent = parent
        return obj

    def select(self, obj):
        for other in bpy.context.selected_objects:
            other.select_set(False)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

    def grid(self, size=25):
        vertices = [(0.01 * x, 0.01 * y, 0.0) for y in range(size) for x in range(size)]
        faces = [(r * size + c, r * size + c + 1, (r + 1) * size + c + 1, (r + 1) * size + c)
                 for r in range(size - 1) for c in range(size - 1)]
        obj = self.add_object('grid', vertices, faces)
        self.select(obj)
        return obj

    def boxes(self):
        """hip (root) carries the lower box; torso, its child, the upper box; hand hangs off torso."""
        hip = self.add_object('hip', *box((-R, -D, -H), (R, D, O)))
        torso = self.add_object('torso', *box((-R, -D, -O), (R, D, H)), parent=hip)
        hand = self.add_object('hand', *box((-R / 2, -D / 2, H), (R / 2, D / 2, H + 0.01)), parent=torso)
        for index, obj in enumerate((hip, torso, hand)):
            obj.sith_model3do_hnode_idx = index
        self.select(torso)
        return hip, torso, hand

    def report(self, kinds, message):
        self.reports.append((kinds, message))

    def run_operator(self, cls, context=None, **values):
        operator = SimpleNamespace(report=self.report, **values)
        for name in ('_audit',):
            if hasattr(cls, name):
                setattr(operator, name, MethodType(getattr(cls, name), operator))
        return cls.execute(operator, context or bpy.context)

    def check(self, **options):
        values = dict(target='JKDF2', absolute_uv=True, export_vert_colors=False, sync_mesh_list=True,
                      bake_pivots=False, detail_levels=1)
        values.update(options)
        return self.run_operator(app.CheckRetailLimits, **values)

    def export_bytes(self, root, tag, name='model.3do'):
        """Export into its own folder: the file name is part of the 3DO header."""
        path = self.folder / tag / name
        path.parent.mkdir()
        export3do(root, str(path), Model3doFileVersion.Version2_1, True, False, True)
        return path.read_bytes()

    # Panel and registration

    def test_panel_and_operators_are_registered_in_the_sith_tab(self):
        panel = bpy.types.VIEW3D_PT_sith_audits
        self.assertEqual((panel.bl_space_type, panel.bl_region_type, panel.bl_category, panel.bl_label),
                         ('VIEW_3D', 'UI', 'Sith', 'Sith Audits'))
        for name in ('check_retail_limits', 'colour_pieces', 'restore_colours', 'audit_files'):
            with self.subTest(operator=name):
                self.assertTrue(getattr(bpy.ops.sith, name).get_rna_type().description)
        self.assertIn('can take minutes', app.AuditFiles.__doc__)
        properties = bpy.ops.sith.audit_files.get_rna_type().properties
        self.assertEqual((properties['step'].default, properties['reference'].default, properties['zero_rotations'].default),
                         (2.0, 'REST', True))
        self.assertIn('kystand1', properties['reference_key'].description)

    # Check Retail Limits

    def test_check_counts_the_model_export_would_write_without_writing_a_file(self):
        obj = self.grid()
        meshes, objects = len(bpy.data.meshes), len(bpy.data.objects)
        vertices = [tuple(v.co) for v in obj.data.vertices]
        with mock.patch.object(model3doWriter, 'save3do', side_effect=AssertionError('the check wrote a file')):
            self.assertEqual(self.check(), {'FINISHED'})
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertEqual((len(bpy.data.meshes), len(bpy.data.objects)), (meshes, objects))
        self.assertEqual([tuple(v.co) for v in obj.data.vertices], vertices)
        warnings = [message for kinds, message in self.reports if kinds == {'WARNING'}]
        self.assertEqual(warnings[:2], [
            "Mesh 0 'grid' has 625 vertices; JKDF2 allows 512 per mesh (113 over) in geoset 0",
            "Mesh 0 'grid' has 576 faces; JKDF2 allows 512 per mesh (64 over) in geoset 0"])
        self.assertIn("Report: 'grid - Retail Limits'", warnings[-1])
        report = report_json(bpy.data.texts['grid - Retail Limits'])
        # The same counts as auditing the file the exporter writes with the same options.
        self.export_bytes(obj, 'exported', 'grid.3do')
        exported = auditBudget(load3do(self.folder / 'exported' / 'grid.3do')[0])
        self.assertEqual(report['issue_counts'], exported['issue_counts'])
        self.assertEqual(report['geosets'], exported['geosets'])

    def test_check_options_follow_the_export_dialog(self):
        obj = self.add_object('triangle', [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
        self.select(obj)
        self.assertEqual(self.check(detail_levels=4, target='MOTS'), {'FINISHED'})
        self.assertEqual(self.reports[-1][0], {'INFO'})
        self.assertIn('Within the MOTS retail limits', self.reports[-1][1])
        report = report_json(bpy.data.texts['triangle - Retail Limits'])
        self.assertEqual((report['status'], report['target'], report['geoset_count']), ('within_limits', 'MOTS', 4))
        properties = bpy.ops.sith.check_retail_limits.get_rna_type().properties
        export = bpy.ops.export_scene.sith_3do.get_rna_type().properties
        for name in ('absolute_uv', 'export_vert_colors', 'sync_mesh_list', 'bake_pivots', 'detail_levels'):
            with self.subTest(option=name):
                self.assertEqual((properties[name].name, properties[name].default),
                                 (export[name].name, export[name].default))

    def test_check_needs_a_selected_model_and_reuses_only_its_own_text(self):
        bpy.context.view_layer.objects.active = None
        self.assertEqual(self.check(), {'CANCELLED'})
        self.assertEqual(self.reports[-1][0], {'ERROR'})
        self.grid()
        user = bpy.data.texts.new('grid - Retail Limits')
        user.from_string('my notes')
        self.check()
        self.check()
        self.assertEqual(user.as_string(), 'my notes')
        self.assertEqual(sorted(t.name for t in bpy.data.texts), ['grid - Retail Limits', 'grid - Retail Limits.001'])

    # Colour Pieces and Restore Colours

    def view_context(self, color_type='TEXTURE'):
        space = SimpleNamespace(type='VIEW_3D', shading=SimpleNamespace(color_type=color_type, type='SOLID'))
        context = bpy.context
        return SimpleNamespace(active_object=context.active_object, selected_objects=context.selected_objects,
                               view_layer=context.view_layer, scene=context.scene, space_data=space, screen=None), space

    def test_colour_pieces_changes_only_viewport_colours_and_restore_is_exact(self):
        hip, torso, hand = self.boxes()
        originals = {}
        for i, obj in enumerate((hip, torso, hand)):
            obj.color = (0.1 + i / 7, 0.2, 0.3 + i / 11, 0.4)
            originals[obj.name] = tuple(obj.color)
        before = self.export_bytes(hip, 'before')
        context, space = self.view_context()
        self.assertEqual(self.run_operator(app.ColourPieces, context, by='NODE'), {'FINISHED'})
        model = makeModel3doFromObj('boxes', hip, hierarchyOnly=True)
        expected = pieceColours(model, by='node')
        for node, colour in zip(model.meshHierarchy, expected):
            with self.subTest(node=node.name):
                linear = [app._srgb_to_linear(c) for c in colour['rgb']]
                for got, want in zip(node.obj.color, (*linear, 1.0)):
                    self.assertAlmostEqual(got, want, places=6)
        self.assertEqual(space.shading.color_type, 'OBJECT')
        self.assertEqual(bpy.context.scene[app.kSavedColorType], 'TEXTURE')
        self.assertEqual(self.export_bytes(hip, 'coloured'), before)
        # A second run (by rigid part) keeps the first originals.
        self.assertEqual(self.run_operator(app.ColourPieces, context, by='PART'), {'FINISHED'})
        self.assertEqual(self.run_operator(app.RestoreColours, context), {'FINISHED'})
        self.assertEqual({obj.name: tuple(obj.color) for obj in (hip, torso, hand)}, originals)
        self.assertEqual(space.shading.color_type, 'TEXTURE')
        self.assertFalse(any(app.kSavedObjectColor in obj for obj in bpy.data.objects))
        self.assertNotIn(app.kSavedColorType, bpy.context.scene)
        self.assertEqual(self.export_bytes(hip, 'restored'), before)

    def test_restore_leaves_a_colour_mode_the_user_changed_and_reports_nothing_to_do(self):
        context, _ = self.view_context()
        self.assertEqual(self.run_operator(app.RestoreColours, context), {'CANCELLED'})
        self.assertEqual(self.reports[-1], ({'INFO'}, 'No colours to restore'))
        self.boxes()
        context, space = self.view_context()
        self.run_operator(app.ColourPieces, context, by='NODE')
        space.shading.color_type = 'MATERIAL'
        self.assertEqual(self.run_operator(app.RestoreColours, context), {'FINISHED'})
        self.assertEqual(space.shading.color_type, 'MATERIAL')
        self.assertNotIn(app.kSavedColorType, bpy.context.scene)

    def test_colour_pieces_round_trip_in_the_real_context(self):
        hip, _, _ = self.boxes()
        space = app._view3d_space(bpy.context)      # a real SpaceView3D when the session has a screen
        mode = space.shading.color_type if space is not None else None
        colour = tuple(hip.color)
        self.assertEqual(self.run_operator(app.ColourPieces, by='PART'), {'FINISHED'})
        self.assertIn(app.kSavedObjectColor, hip)
        if space is not None:
            self.assertEqual(space.shading.color_type, 'OBJECT')
            self.assertEqual(app.kSavedColorType in bpy.context.scene, mode != 'OBJECT')
        self.assertEqual(self.run_operator(app.RestoreColours), {'FINISHED'})
        self.assertEqual(tuple(hip.color), colour)
        self.assertNotIn(app.kSavedObjectColor, hip)
        self.assertNotIn(app.kSavedColorType, bpy.context.scene)
        if space is not None:
            self.assertEqual(space.shading.color_type, mode)

    # Audit Files

    def write_files(self):
        hip, _, _ = self.boxes()
        model = self.folder / 'boxes.3do'
        export3do(hip, str(model), Model3doFileVersion.Version2_1, False, False, True)
        keys = self.folder / 'key'
        keys.mkdir()
        saveKey(key('stand.key', [(0, [entry(0)]), (1, [entry(0)])]), keys / 'stand.key', 'synthetic')
        saveKey(key('still.key', [(0, [entry(0)]), (1, [entry(0)])]), keys / 'still.key', 'synthetic')
        saveKey(key('lift.key', [(0, [entry(0)]), (1, [entry(0), entry(2, (0.0, 0.0, 0.004))])]), keys / 'lift.key',
                'synthetic')
        files = {path: digest(path) for path in self.folder.rglob('*') if path.is_file()}
        return model, keys, files

    def audit(self, model, **options):
        values = dict(filepath=str(model), key_directory='', key_files='', step=1.0, joint_gaps=True,
                      seat_offsets=True, floor_contact=True, key_lint=True, reference='REST',
                      reference_key='kystand1.key', reference_frame=0.0, zero_rotations=True, move_size=0.065,
                      crouch_keys='kycrchgn.key, kycrchbk.key', lint_target='JKDF2')
        values.update(options)
        return self.run_operator(app.AuditFiles, **values)

    def test_audit_files_writes_one_report_per_check_and_changes_no_file(self):
        model, keys, files = self.write_files()
        self.assertEqual(self.audit(model), {'FINISHED'})
        self.assertEqual(files, {path: digest(path) for path in self.folder.rglob('*') if path.is_file()})
        gaps = report_json(bpy.data.texts['boxes.3do - Joint Gaps'])
        self.assertEqual(gaps['status'], 'review')
        self.assertEqual({row['name'] for row in gaps['keys'] if row['poses_with_daylight']}, {'lift.key'})
        seats = report_json(bpy.data.texts['boxes.3do - Seat Offsets'])
        self.assertEqual((seats['status'], seats['findings'][0]['key']), ('off_seat', 'lift.key'))
        floor = report_json(bpy.data.texts['boxes.3do - Floor Contact'])
        self.assertAlmostEqual(floor['floor'], -0.07, places=9)
        lint = report_json(bpy.data.texts['boxes.3do - KEY Lint'])
        self.assertEqual(set(lint['keys']), {'lift.key', 'stand.key', 'still.key'})
        self.assertEqual(lint['status'], 'review')   # synthetic track names differ from the model's
        message = self.reports[-1][1]
        self.assertTrue(message.startswith('Joint Gaps: review, Seat Offsets: off_seat, Floor Contact: '), message)
        self.assertIn('Reference: rest pose', bpy.data.texts['boxes.3do - Joint Gaps'].as_string())

    def test_audit_files_subset_and_reference_key(self):
        model, keys, _ = self.write_files()
        result = self.audit(model, key_directory=str(keys), key_files='lift.key, STILL.KEY', reference='KEY',
                            reference_key='stand.key', floor_contact=False, key_lint=False)
        self.assertEqual(result, {'FINISHED'})
        gaps = report_json(bpy.data.texts['boxes.3do - Joint Gaps'])
        self.assertEqual([row['name'] for row in gaps['keys']], ['lift.key', 'still.key'])
        self.assertIn('stand.key frame 0: KEY positions with zero rotation', gaps['parameters']['reference'])
        self.assertNotIn('boxes.3do - Floor Contact', bpy.data.texts)

    def test_audit_files_refuses_bad_inputs_before_auditing(self):
        model, keys, _ = self.write_files()
        cases = [dict(filepath=str(keys / 'lift.key')), dict(key_directory=str(self.folder / 'missing')),
                 dict(key_files='other.key'), dict(key_files='../key/lift.key'),
                 dict(joint_gaps=False, seat_offsets=False, floor_contact=False, key_lint=False),
                 dict(reference='KEY', reference_key='missing.key')]
        for options in cases:
            with self.subTest(options=options):
                self.reports.clear()
                self.assertEqual(self.audit(model, **options), {'CANCELLED'})
                self.assertEqual(self.reports[-1][0], {'ERROR'})
        self.assertFalse([t for t in bpy.data.texts if t.name.startswith('boxes.3do')])
