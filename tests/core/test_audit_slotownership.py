# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic player, attachment and puppet contracts. No game assets required."""
from pathlib import Path
import hashlib
import struct
import tempfile
import unittest

from sith.audit.slotownership import auditSlotOwnership, puppetKeyUses, auditFiles
from sith.key.key import Key, KeyNode, Keyframe
from sith.model.model3do import Model3do, Mesh3doNode
from sith.types import Vector3f


def rig(rows):
    m = Model3do('actor')
    for i, (name, parent, mesh) in enumerate(rows):
        n = Mesh3doNode(name)
        n.idx, n.parentIdx, n.meshIdx = i, parent, mesh
        m.meshHierarchy.append(n)
    return m


def clip(slot, label='anything', offset=1):
    k = Key('test')
    k.numFrames, k.numJoints = 8, slot + 1
    n = KeyNode()
    n.idx, n.meshName = slot, label
    f = Keyframe()
    f.position = Vector3f(offset, 0, 0)
    n.keyframes.append(f)
    k.nodes.append(n)
    return k


class SlotOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.ref = rig([('root', -1, -1), ('hip', 0, 0), ('torso', 0, 1)])
        self.picker = rig([('root', -1, -1), ('hip', 0, 0), ('torso', 3, 1),
                           ('carrier', 0, -1)])

    def test_both_fidgets_and_constant_off_bind_attachment_track(self):
        keys = {'first-b.key': clip(3, 'carrier'), 'second-b.key': clip(3, 'gun')}
        r = auditSlotOwnership(self.ref, self.picker, keys)
        self.assertEqual(r['status'], 'conflict')
        self.assertEqual([f['new_body_slots'] for f in r['findings']], [[2], [2]])

    def test_empty_endpoint_or_absent_slot_does_not_move_body(self):
        empty = rig([('root', -1, -1), ('hip', 0, 0), ('torso', 0, 1), ('unused', 0, -1)])
        for model in (empty, self.ref):
            self.assertEqual(auditSlotOwnership(self.ref, model, {'idle': clip(3)})['status'], 'clear')

    def test_legitimate_prop_and_explicit_custom_body_panel(self):
        model = rig([('root', -1, -1), ('hip', 0, 0), ('torso', 0, 1), ('prop', 2, 2)])
        result = auditSlotOwnership(self.ref, model, {'idle': clip(3)})
        self.assertEqual(result['findings'][0]['classification'], 'review_attachment')
        self.assertEqual(result['findings'][0]['new_body_slots'], [])
        self.assertEqual(auditSlotOwnership(self.ref, model, {'idle': clip(3)}, [3])['status'], 'conflict')
        # The same prop slot on the animation's original rig is a matching owner.
        self.assertEqual(auditSlotOwnership(model, model, {'idle': clip(3, 'other')})['status'], 'clear')

    def test_named_body_part_follows_an_attachment_across_slot_changes(self):
        keys = {'idle': clip(3, 'weapon')}
        base = [('root', -1, -1), ('hip', 0, 0), ('head', 0, 1)]
        old = rig(base + [('snout', 2, 2), ('unused', 0, -1)])
        new = rig(base + [('unused', 0, -1), ('snout', 2, 2)])
        for model, slot, status in [(old, 3, 'conflict'), (new, 4, 'clear')]:
            result = auditSlotOwnership(self.ref, model, keys, bodyNodes=['Snout'])
            self.assertEqual(result['status'], status)
            self.assertEqual(result['declared_body_slots'], [{'slot': slot, 'name': 'snout'}])
        # An undeclared weapon mesh on a formerly empty slot still gets an advisory.
        prop = rig(base + [('blade', 2, 2), ('snout', 2, 3)])
        self.assertEqual(auditSlotOwnership(self.ref, prop, keys, bodyNodes=['snout'])['status'], 'review')

    def test_named_body_declarations_fail_if_missing_ambiguous_or_empty(self):
        base = [('root', -1, -1), ('hip', 0, 0), ('head', 0, 1)]
        keys = {'idle': clip(3)}
        for rows, text in [([], 'Missing'), ([('snout', 2, 2), ('SNOUT', 2, 3)], 'Ambiguous'),
                           ([('snout', 2, -1)], 'no mesh')]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, text):
                auditSlotOwnership(self.ref, rig(base + rows), keys, bodyNodes=['snout'])

    def test_bind_pose_track_and_unallocated_track(self):
        key = clip(3, offset=0)
        self.assertEqual(auditSlotOwnership(self.ref, self.picker, {'idle': key})['status'], 'clear')
        key.nodes[0].keyframes[0].position = Vector3f(99, 0, 0)
        key.numJoints = 3
        self.assertEqual(auditSlotOwnership(self.ref, self.picker, {'idle': key})['status'], 'clear')

    def test_parent_cycle_and_out_of_range_body_contract(self):
        self.picker.meshHierarchy[3].parentIdx = 2
        with self.assertRaises(ValueError):
            auditSlotOwnership(self.ref, self.picker, {'idle': clip(3)})
        with self.assertRaises(ValueError):
            auditSlotOwnership(self.ref, self.ref, {'idle': clip(3)}, [10])

    def test_pup_inventory_and_duplicate_resource_uses(self):
        text = ('# fidget2 fake.key\nMode=0\nstand STILL.KEY 0 0 0\n'
                'Mode=1, BasedOn=0\nfidget2 second.key 32 1 1 # ignore other.key\n'
                'Mode=2, BasedOn=0\nfidget2 blade.key 32 1 1\nfire blade.key 14 3 3\n')
        uses = puppetKeyUses(text)
        self.assertEqual(list(uses), ['still.key', 'second.key', 'blade.key'])
        self.assertEqual(uses['blade.key'], [{'mode': 2, 'action': 'fidget2'}, {'mode': 2, 'action': 'fire'}])

    def test_missing_effective_key_and_unsafe_pup_path_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            p = root/'a.pup'
            p.write_text('Mode=0\nfidget2 missing.key 0 0 0')
            with self.assertRaisesRegex(ValueError, 'Missing or ambiguous'):
                auditFiles(root/'ref', root/'body', p, root)
        with self.assertRaises(ValueError):
            puppetKeyUses('Mode=0\nfidget2 ../other.key 0 0 0')

    def test_exact_package_member_receipt_and_same_basename(self):
        fixtures = Path(__file__).resolve().parents[1] / 'tv'
        model = fixtures / 'model' / 'triangle.3do'
        key = fixtures / 'key' / 'winding_and_holds.key'
        raw = model.read_bytes()
        other = b'not a model'
        entries = [('3do\\unit.3do', other), ('3DO\\world\\Unit.3do', raw)]
        with tempfile.TemporaryDirectory() as tmp:
            gob = Path(tmp) / 'actors.gob'
            def pack(rows):
                data = bytearray()
                index = bytearray()
                for name, payload in rows:
                    index.extend(struct.pack('<II128s', 12 + len(data), len(payload), name.encode()))
                    data.extend(payload)
                gob.write_bytes(struct.pack('<4sII', b'GOB ', 20, 12 + len(data)) +
                                data + struct.pack('<I', len(rows)) + index)
            pack(entries)
            report = auditFiles(model, gob, directKeys=[key], modelMember='3do/world/unit.3do')
            self.assertEqual(report['model'], {
                'path': '3DO/world/Unit.3do', 'sha256': hashlib.sha256(raw).hexdigest(),
                'archive': {'path': str(gob), 'sha256': hashlib.sha256(gob.read_bytes()).hexdigest()}})
            for member in ('unit.3do', '3do/../unit.3do', None):
                with self.assertRaises(ValueError):
                    auditFiles(model, gob, directKeys=[key], modelMember=member)
            pack(entries + [('3do/world/unit.3do', raw)])
            with self.assertRaisesRegex(ValueError, 'Ambiguous'):
                auditFiles(model, gob, directKeys=[key], modelMember='3do/world/unit.3do')
            gob.write_bytes(gob.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, 'Truncated'):
                auditFiles(model, gob, directKeys=[key], modelMember='3do/world/unit.3do')
