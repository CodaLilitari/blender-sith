# SPDX-License-Identifier: GPL-3.0-or-later
"""The KEY reseat pass and its check on a synthetic rig (no Blender, no game data)."""
import copy
import os
import tempfile
import unittest

import numpy as np

from sith.audit.pose import PoseLayer, keyTrack, poseModel, rotationMatrix, sampleTrack
from sith.key.key import Key, KeyFileFormat, Keyframe, KeyframeFlag, KeyMarker, KeyMarkerType, KeyNode
from sith.key.keyLoader import loadKey
from sith.key.keyReseat import (ReseatNames, _hipShiftLocal, checkReseat, checkReseatFiles, keySeats,
                                modelSeats, reseatKey, reseatKeyFiles)
from sith.key.keyWriter import saveKey
from sith.model.model3do import Mesh3doNode, Mesh3doNodeType, Model3do
from sith.types import Vector3f

# Kyle's layout with two adapter nodes of the kind custom characters add: the hip hangs from the
# root through a_hip, the torso from the carrier through a_torso. The KEYs drive nodes 0-15.
RIG = (
    ('$$$dummy', -1, (0.0, 0.0, 0.0)),             # 0 root
    ('$$$dummy', 0, (0.0, 0.0013, -0.0122)),       # 1 torso carrier
    ('k_hip', 16, (0.0, 0.0013, -0.0178)),         # 2
    ('k_torso', 17, (0.0, 0.0007, 0.0061)),        # 3
    ('k_lthigh', 2, (-0.0085, 0.0009, -0.0007)),   # 4
    ('k_rthigh', 2, (0.0087, 0.0009, -0.0014)),    # 5
    ('k_lcalf', 4, (-0.0028, 0.0002, -0.0469)),    # 6
    ('k_rcalf', 5, (0.0030, 0.0002, -0.0461)),     # 7
    ('k_lshouldr', 3, (-0.0194, -0.0055, 0.0363)), # 8
    ('k_lforearm', 8, (-0.0113, 0.0001, -0.0326)), # 9
    ('k_lhand', 9, (-0.0020, 0.0034, -0.0281)),    # 10
    ('k_rshouldr', 3, (0.0200, -0.0056, 0.0366)),  # 11
    ('k_rforearm', 11, (0.0110, 0.0001, -0.0338)), # 12
    ('k_rhand', 12, (0.0016, 0.0034, -0.0280)),    # 13
    ('k_neck', 3, (0.0, -0.0050, 0.0442)),         # 14
    ('k_head', 14, (0.0, 0.0006, 0.0058)),         # 15
    ('a_hip', 0, (0.0, -0.0022, 0.0027)),          # 16 adapter, not in the KEYs
    ('a_torso', 1, (0.0001, -0.0038, 0.0036)),     # 17 adapter, not in the KEYs
)
JOINTS = 16
ROOT, CARRIER, HIP, TORSO, LTHIGH, RTHIGH, LCALF, RCALF = range(8)
LSHOULDER, LFOREARM, LHAND, RSHOULDER, RFOREARM, RHAND, NECK, HEAD = range(8, 16)
THIGHS = (LTHIGH, RTHIGH)
# The stand KEY seats the left forearm slightly off the model's rest position.
SEATS = {i: np.array(RIG[i][2]) + (np.array([0.0, 0.0, 0.0005]) if i == LFOREARM else 0.0) for i in range(JOINTS)}

# A looping saber-like KEY, one entry per frame, the last one carrying the motion back to frame 0.
# waist: the hip's offset from its seat under the torso; push: the thighs' offset on the hip.
# Both are in the hip's own frame (side, front, up). SHIFT is what the rule must move the hip by.
FRAMES = 4
WAIST = [(0.0, 0.0, -0.005), (0.0, 0.0, -0.001), (0.0, 0.0, -0.001), (0.002, -0.001, 0.002)]
PUSH = [(0.0, 0.0, 0.004), (0.0, 0.0, 0.004), (0.0, 0.0, -0.003), (-0.001, -0.003, -0.001)]
SHIFT = [(0.0, 0.0, 0.004),        # saber: hip low, thighs pushed up; the smaller comes out of both
         (0.0, 0.0, 0.0035),       # 0.001 from the opposite offsets, then 0.0025 of the push tucked
         (0.0, 0.0, 0.0),          # walk: the thighs drop for the stride; the hip must not follow
         (-0.001, 0.0, -0.001)]    # opposite sideways and up; the front offsets agree and stay
HIP_TURN = [(10.0, 20.0, 5.0), (11.0, 22.0, 5.0), (12.0, 24.0, 4.0), (11.0, 22.0, 5.0)]
CARRIER_TURN = [(5.0, 0.0, 0.0), (6.0, 1.0, 0.0), (7.0, 2.0, 0.0), (6.0, 1.0, 0.0)]
CARRIER_OFFSET = np.array([0.0001, 0.0, -0.0002])
TORSO_OFFSET = np.array([0.0, 0.0003, 0.0002])
FOREARM_SHIFT = np.array([0.004, 0.0, 0.0])


def rig(nodes=RIG):
    model = Model3do('rig')
    model.insertOffset = Vector3f(0.0, 0.0, 0.12)
    model.geosets = []
    hierarchy = []
    for i, (name, parent, position) in enumerate(nodes):
        node = Mesh3doNode(name)
        node.idx = i
        node.type = Mesh3doNodeType.Nothing
        node.meshIdx = -1
        node.parentIdx = parent
        node.position = Vector3f(*position)
        node.rotation = Vector3f(0.0, 0.0, 0.0)
        node.pivot = Vector3f(0.0, 0.0, 0.0)
        hierarchy.append(node)
    model.meshHierarchy = hierarchy
    return model


def entry(frame, position, rotation=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0), spin=(0.0, 0.0, 0.0)):
    k = Keyframe()
    k.frame = float(frame)
    k.flags = KeyframeFlag((1 if any(velocity) else 0) | (2 if any(spin) else 0))
    k.position = Vector3f(*(float(v) for v in position))
    k.orientation = Vector3f(*(float(v) for v in rotation))
    k.deltaPosition = Vector3f(*(float(v) for v in velocity))
    k.deltaRotation = Vector3f(*(float(v) for v in spin))
    return k


def looping(positions, rotations):
    """One entry per frame; each runs to the next frame's values and the last runs back to frame 0."""
    count = len(positions)
    out = []
    for f in range(count):
        g = (f + 1) % count
        out.append(entry(f, positions[f], rotations[f], np.subtract(positions[g], positions[f]),
                         np.subtract(rotations[g], rotations[f])))
    return out


def makeKey(tracks, frames, joints=JOINTS, name='test.key', markers=((1.0, KeyMarkerType.LeftFoot),)):
    key = Key(name)
    key.numJoints = joints
    key.numFrames = frames
    key.fps = 15.0
    for idx in sorted(tracks):
        node = KeyNode()
        node.idx = idx
        node.meshName = RIG[idx][0]
        node.keyframes = tracks[idx]
        key.nodes.append(node)
    for frame, kind in markers:
        marker = KeyMarker()
        marker.frame = frame
        marker.type = kind
        key.markers.append(marker)
    return key


def standKey():
    return makeKey({i: [entry(0, SEATS[i])] for i in range(JOINTS)}, 1, name='kystand1.key', markers=())


def saberKey():
    """Hip low and thighs pushed up, a frame where the thighs drop, and a shifted left forearm."""
    still = [(0.0, 0.0, 0.0)] * FRAMES
    tracks = {i: looping([SEATS[i]] * FRAMES, still) for i in range(JOINTS)}
    carrier = SEATS[CARRIER] + CARRIER_OFFSET
    tracks[CARRIER] = looping([carrier] * FRAMES, CARRIER_TURN)
    tracks[TORSO] = looping([SEATS[TORSO] + TORSO_OFFSET] * FRAMES,
                            [(-10.0, 5.0, 0.0), (-11.0, 6.0, 1.0), (-12.0, 7.0, 2.0), (-11.0, 6.0, 1.0)])
    # Placing the hip so that its waist offset, measured as the rule does, is WAIST[f] in its own frame.
    hip = [SEATS[HIP] + CARRIER_OFFSET + rotationMatrix(CARRIER_TURN[f]) @ TORSO_OFFSET
           + rotationMatrix(HIP_TURN[f]) @ np.array(WAIST[f]) for f in range(FRAMES)]
    tracks[HIP] = looping(hip, HIP_TURN)
    tracks[LTHIGH] = looping([SEATS[LTHIGH] + PUSH[f] for f in range(FRAMES)],
                             [(20.0, 0.0, 0.0), (23.0, 1.0, 0.0), (26.0, 2.0, 0.0), (23.0, 1.0, 0.0)])
    tracks[RTHIGH] = looping([SEATS[RTHIGH] + PUSH[f] for f in range(FRAMES)],
                             [(20.0, 0.0, 0.0), (17.0, -1.0, 0.0), (14.0, -2.0, 0.0), (17.0, -1.0, 0.0)])
    tracks[LCALF] = looping([SEATS[LCALF]] * FRAMES, [(-30.0, 0.0, 0.0), (-33.0, 0.0, 0.0), (-36.0, 0.0, 0.0), (-33.0, 0.0, 0.0)])
    tracks[LSHOULDER] = looping([SEATS[LSHOULDER]] * FRAMES, [(13.0, -26.0, 3.0), (15.0, -24.0, 3.0), (17.0, -22.0, 3.0), (15.0, -24.0, 3.0)])
    tracks[LFOREARM] = looping([SEATS[LFOREARM] + FOREARM_SHIFT] * FRAMES,
                               [(44.0, 35.0, -30.0), (46.0, 35.0, -30.0), (48.0, 35.0, -30.0), (46.0, 35.0, -30.0)])
    return makeKey(tracks, FRAMES, name='kysabr.key', markers=((1.0, KeyMarkerType.LeftFoot), (3.0, KeyMarkerType.RightFoot)))


def records(key):
    return [(n.idx, n.meshName, [(float(e.frame), int(e.flags), tuple(e.position), tuple(e.orientation),
                                  tuple(e.deltaPosition), tuple(e.deltaRotation)) for e in n.keyframes])
            for n in key.nodes]


def header(key):
    return (key.name, int(key.flags), int(getattr(key.nodeTypes, 'value', key.nodeTypes)), key.numFrames, key.fps,
            key.numJoints, [(float(m.frame), int(m.type)) for m in key.markers], key.fileFormat)


def modelRecord(model):
    return [(n.idx, n.name, n.parentIdx, tuple(n.position), tuple(n.rotation)) for n in model.meshHierarchy]


def world(model, key, time):
    return poseModel(model, [PoseLayer(key, time, lowPri=1)], vertices=False).frames


class HipRuleTests(unittest.TestCase):
    def test_rule_takes_the_smaller_opposite_offset_and_tucks_the_rest(self):
        for waist, push, shift in zip(WAIST, PUSH, SHIFT):
            np.testing.assert_allclose(_hipShiftLocal(np.array(waist), np.array(push), 0.0025, 0.0), shift,
                                       rtol=0, atol=1e-15)

    def test_waist_lift_follows_the_torso_at_the_hip_joints_cost(self):
        waist, push = np.array([0.0, 0.0, -0.005]), np.array([0.0, 0.0, 0.001])
        np.testing.assert_allclose(_hipShiftLocal(waist, push, 0.0025, 0.0), [0, 0, 0.001], atol=1e-15)
        np.testing.assert_allclose(_hipShiftLocal(waist, push, 0.0025, 0.0015), [0, 0, 0.0025], atol=1e-15)
        np.testing.assert_allclose(_hipShiftLocal(waist, push, 0.0, 0.0), [0, 0, 0.001], atol=1e-15)

    def test_no_joint_ends_further_apart_than_stock(self):
        """Up is positive into the joint: a negative waist or push offset is a gap, a positive one an overlap."""
        rng = np.random.default_rng(23)
        tuck = 0.0025
        for waist, push in rng.uniform(-0.01, 0.01, size=(4000, 2, 3)):
            shift = _hipShiftLocal(waist, push, tuck, 0.0)
            newWaist, newPush = waist + shift, push - shift
            for axis in range(3):
                # The thighs only ever move back towards their seats and never past them.
                self.assertGreaterEqual(newPush[axis] * push[axis], -1e-18)
                self.assertLessEqual(abs(newPush[axis]), abs(push[axis]) + 1e-15)
            for axis in (0, 1):
                # Sideways and front to back the hip only moves towards its seat.
                self.assertGreaterEqual(newWaist[axis] * waist[axis], -1e-18)
                self.assertLessEqual(abs(newWaist[axis]), abs(waist[axis]) + 1e-15)
            # Up: a waist gap never grows and an overlap never exceeds the larger of stock's and the tuck.
            self.assertGreaterEqual(newWaist[2], min(waist[2], 0.0) - 1e-15)
            self.assertLessEqual(newWaist[2], max(waist[2], tuck) + 1e-15)


class ReseatTests(unittest.TestCase):
    def setUp(self):
        self.model = rig()
        self.stand = standKey()
        self.seats = keySeats(self.stand)
        self.source = saberKey()

    def test_seats_come_from_the_stand_key_or_the_rest_pose(self):
        np.testing.assert_allclose(self.seats[LFOREARM], SEATS[LFOREARM])
        self.assertEqual(sorted(self.seats), list(range(JOINTS)))
        rest = modelSeats(self.model)
        np.testing.assert_allclose(rest[LFOREARM], RIG[LFOREARM][2])
        self.assertEqual(len(rest), len(RIG))

    def test_saber_key_is_reseated_and_inputs_are_unchanged(self):
        before, beforeHeader, beforeModel = records(self.source), header(self.source), modelRecord(self.model)
        result, report = reseatKey(self.model, self.source, self.seats)
        self.assertEqual((records(self.source), header(self.source), modelRecord(self.model)),
                         (before, beforeHeader, beforeModel))
        self.assertEqual((report['status'], report['hip_rule'], report['changed']), ('reseated', 'applied', True))
        self.assertEqual(report['carrier'], {'index': CARRIER, 'name': '$$$dummy'})
        self.assertEqual([(t['index'], t['rule']) for t in report['tracks']],
                         [(HIP, 'hip'), (LTHIGH, 'thigh'), (RTHIGH, 'thigh'), (LFOREARM, 'seat')])
        self.assertEqual([t['entries'] for t in report['tracks']], [[4, 4]] * 4)
        shifts = {t['index']: t['largest_shift'] for t in report['tracks']}
        self.assertAlmostEqual(shifts[LFOREARM], 0.004, places=12)
        self.assertAlmostEqual(shifts[LTHIGH], 0.004, places=12)
        self.assertEqual((report['issue_counts'], report['findings'], report['findings_truncated']), ({}, [], False))

        # The header, the markers and every track the pass does not rewrite are as they were.
        self.assertEqual(header(result), header(self.source))
        rewritten = {HIP, LTHIGH, RTHIGH, LFOREARM}
        self.assertEqual([r for r in records(result) if r[0] not in rewritten],
                         [r for r in records(self.source) if r[0] not in rewritten])

    def test_rule_moves_the_hip_and_brings_the_thighs_back_at_every_frame(self):
        result, _ = reseatKey(self.model, self.source, self.seats)
        for f in range(FRAMES):
            turn = rotationMatrix(HIP_TURN[f])
            old = sampleTrack(keyTrack(self.source, HIP), f)[0]
            np.testing.assert_allclose(sampleTrack(keyTrack(result, HIP), f)[0], old + turn @ SHIFT[f], atol=1e-15)
            for thigh in THIGHS:
                old = sampleTrack(keyTrack(self.source, thigh), f)[0]
                np.testing.assert_allclose(sampleTrack(keyTrack(result, thigh), f)[0], old - SHIFT[f], atol=1e-15)
        # The walk frame: the thighs dropped and the hip did not follow them down.
        np.testing.assert_allclose(sampleTrack(keyTrack(result, HIP), 2.0)[0],
                                   sampleTrack(keyTrack(self.source, HIP), 2.0)[0], atol=1e-15)
        # The shifted forearm sits on its seat with its own entries and rotations.
        forearm = keyTrack(result, LFOREARM)
        self.assertEqual([e.frame for e in forearm.keyframes], [e.frame for e in keyTrack(self.source, LFOREARM).keyframes])
        for time in (0.0, 1.5, 3.75, 4.0):
            position, rotation = sampleTrack(forearm, time)
            np.testing.assert_allclose(position, SEATS[LFOREARM], atol=1e-15)
            np.testing.assert_array_equal(rotation, sampleTrack(keyTrack(self.source, LFOREARM), time)[1])

    def test_torso_and_legs_stay_where_the_key_put_them(self):
        result, _ = reseatKey(self.model, self.source, self.seats)
        fixed = [ROOT, CARRIER, TORSO, LTHIGH, RTHIGH, LCALF, RCALF, 17]
        for time in np.arange(0.0, FRAMES + 1e-9, 0.25):
            a, b = world(self.model, self.source, time), world(self.model, result, time)
            np.testing.assert_allclose(b[:, :3, :3], a[:, :3, :3], rtol=0, atol=1e-12)
            limit = 1e-14 if float(time).is_integer() else 1e-4
            np.testing.assert_allclose(b[fixed, :3, 3], a[fixed, :3, 3], rtol=0, atol=limit)
        check = checkReseat(self.model, self.source, result)
        self.assertEqual(check['status'], 'pass', check['findings'])
        self.assertEqual(check['samples'], 2 * FRAMES + 1)
        self.assertLess(check['largest_orientation_change']['value'], 1e-12)
        self.assertLess(check['fixed_moved_at_frames']['value'], 1e-14)
        self.assertLess(check['fixed_moved_between_frames']['value'], 1e-4)
        self.assertAlmostEqual(check['hip_moved']['value'], 0.004, places=12)
        self.assertAlmostEqual(check['seated_moved']['value'], 0.004, places=12)
        self.assertIn(check['seated_moved']['name'], ('k_lforearm', 'k_lhand'))

    def test_last_frame_keeps_its_motion_to_frames(self):
        """A looping KEY plays every time below FRAMES: the reseated tracks must keep moving there."""
        result, _ = reseatKey(self.model, self.source, self.seats)
        for idx in (HIP, LTHIGH, RTHIGH, LFOREARM):
            last = keyTrack(result, idx).keyframes[-1]
            self.assertTrue(int(last.flags) & 2, idx)
            for time in (3.25, 3.5, 4.0):
                np.testing.assert_allclose(sampleTrack(keyTrack(result, idx), time)[1],
                                           sampleTrack(keyTrack(self.source, idx), time)[1], rtol=0, atol=1e-12)
        # The source loops seamlessly, and so does the result.
        np.testing.assert_allclose(world(self.model, result, 4.0), world(self.model, result, 0.0), rtol=0, atol=1e-12)

        # A copy whose rewritten tracks stop at FRAMES-1 fails the check, and only on the last frame.
        frozen = copy.deepcopy(result)
        for idx in (HIP, LTHIGH, RTHIGH, LFOREARM):
            last = keyTrack(frozen, idx).keyframes[-1]
            last.flags = KeyframeFlag.NoChange
        check = checkReseat(self.model, self.source, frozen)
        self.assertEqual(check['status'], 'fail')
        self.assertGreater(check['issue_counts']['orientation_changed'], 0)
        self.assertTrue(all(f['time'] > FRAMES - 1 for f in check['findings']), check['findings'])
        self.assertEqual(check['largest_orientation_change']['time'], float(FRAMES))

    def test_steps_and_entries_between_frames_are_kept(self):
        """The hip steps up at frame 1.5. The rebuilt tracks keep the step, so the legs never move."""
        frames = 3
        tracks = {i: [entry(0, SEATS[i])] for i in range(JOINTS)}
        low = SEATS[HIP] + np.array([0.0, 0.0, -0.004])
        tracks[HIP] = [entry(0, low, (0.0, 30.0, 0.0), velocity=(0.0, 0.0, 0.0005)),
                       entry(1.5, low + np.array([0.0, 0.0, 0.003]), (0.0, 30.0, 0.0))]
        for thigh in THIGHS:
            tracks[thigh] = [entry(f, SEATS[thigh] + np.array([0.0, 0.0, 0.003]), (10.0 * f, 0.0, 0.0),
                                   spin=(10.0, 0.0, 0.0)) for f in range(frames)]
        source = makeKey(tracks, frames)
        result, report = reseatKey(self.model, source, self.seats)
        self.assertEqual(report['hip_rule'], 'applied')
        self.assertEqual([e.frame for e in keyTrack(result, HIP).keyframes], [0.0, 1.0, 1.5, 2.0])
        for time in np.linspace(0.0, frames, 61):
            a, b = world(self.model, source, time), world(self.model, result, time)
            np.testing.assert_allclose(b[[LTHIGH, RTHIGH, LCALF, RCALF], :3, 3], a[[LTHIGH, RTHIGH, LCALF, RCALF], :3, 3],
                                       rtol=0, atol=1e-14)
        before = sampleTrack(keyTrack(result, HIP), 1.5 - 1e-9)[0]
        after = sampleTrack(keyTrack(result, HIP), 1.5)[0]
        self.assertAlmostEqual(after[2] - before[2], 0.003 - 0.0005 * 1.5, places=9)
        self.assertEqual(checkReseat(self.model, source, result)['status'], 'pass')

    def test_waist_lift_is_an_option(self):
        tracks = {i: [entry(0, SEATS[i])] for i in range(JOINTS)}
        tracks[HIP] = [entry(0, SEATS[HIP] + np.array([0.0, 0.0, -0.005]))]
        for thigh in THIGHS:
            tracks[thigh] = [entry(0, SEATS[thigh] + np.array([0.0, 0.0, 0.001]))]
        source = makeKey(tracks, 1)
        for lift, shift in ((0.0, 0.001), (0.0015, 0.0025)):
            result, _ = reseatKey(self.model, source, self.seats, waistLift=lift)
            np.testing.assert_allclose(sampleTrack(keyTrack(result, HIP), 0.0)[0], SEATS[HIP] + [0, 0, shift - 0.005],
                                       atol=1e-15)
            np.testing.assert_allclose(sampleTrack(keyTrack(result, LTHIGH), 0.0)[0], SEATS[LTHIGH] + [0, 0, 0.001 - shift],
                                       atol=1e-15)
            self.assertEqual(checkReseat(self.model, source, result)['status'], 'pass')

    def test_seat_rule_triggers_on_any_played_time(self):
        """A seated joint is moved when it leaves its seat by more than the tolerance at any time up to FRAMES."""
        def forearmKey(entries):
            tracks = {i: [entry(0, SEATS[i])] for i in range(JOINTS)}
            tracks[RFOREARM] = entries
            return makeKey(tracks, 2)

        seat = SEATS[RFOREARM]
        for offset, moved in ((1.9e-5, False), (2.1e-5, True)):
            _, report = reseatKey(self.model, forearmKey([entry(0, seat + [0.0, offset, 0.0])]), self.seats)
            self.assertEqual(RFOREARM in [t['index'] for t in report['tracks']], moved, offset)
        # Every stored position is on the seat, but the last entry drifts off it before FRAMES.
        source = forearmKey([entry(0, seat), entry(1, seat, velocity=(0.0, 0.0, 0.001))])
        result, report = reseatKey(self.model, source, self.seats)
        self.assertEqual([(t['index'], t['rule']) for t in report['tracks']], [(RFOREARM, 'seat')])
        self.assertAlmostEqual(report['tracks'][0]['largest_shift'], 0.001, places=15)
        np.testing.assert_allclose(sampleTrack(keyTrack(result, RFOREARM), 2.0)[0], seat, atol=1e-15)

    def test_key_on_its_seats_is_unchanged(self):
        result, report = reseatKey(self.model, self.stand, self.seats)
        self.assertEqual((report['status'], report['hip_rule'], report['changed'], report['tracks']),
                         ('unchanged', 'within_tolerance', False, []))
        self.assertEqual(records(result), records(self.stand))

    def test_custom_names_and_switching_rules_off(self):
        nodes = (('root', -1, (0.0, 0.0, 0.0)), ('carrier', 0, (0.0, 0.0, 0.1)), ('pelvis', 0, (0.0, 0.0, 0.08)),
                 ('chest', 1, (0.0, 0.0, 0.0)), ('leg_l', 2, (-0.01, 0.0, 0.0)), ('leg_r', 2, (0.01, 0.0, 0.0)),
                 ('arm_l', 3, (-0.02, 0.0, 0.03)))
        model = rig(nodes)
        seats = modelSeats(model)
        tracks = {i: [entry(0, seats[i])] for i in range(len(nodes))}
        tracks[2] = [entry(0, np.array(seats[2]) + [0.0, 0.0, -0.003])]
        tracks[4] = [entry(0, np.array(seats[4]) + [0.0, 0.0, 0.002])]
        tracks[5] = [entry(0, np.array(seats[5]) + [0.0, 0.0, 0.002])]
        tracks[6] = [entry(0, np.array(seats[6]) + [0.003, 0.0, 0.0])]
        source = makeKey(tracks, 1, joints=len(nodes))
        names = ReseatNames(hip='Pelvis', torso='chest', thighs=('leg_l', 'leg_r'), seated=('ARM_L',))
        result, report = reseatKey(model, source, seats, names=names)
        self.assertEqual([(t['index'], t['rule']) for t in report['tracks']], [(2, 'hip'), (4, 'thigh'), (5, 'thigh'), (6, 'seat')])
        np.testing.assert_allclose(sampleTrack(keyTrack(result, 2), 0.0)[0], np.array(seats[2]) + [0, 0, -0.001], atol=1e-15)
        self.assertEqual(checkReseat(model, source, result, names=names)['status'], 'pass')
        result, report = reseatKey(model, source, seats, names=ReseatNames(hip=None, torso='chest', seated=()))
        self.assertEqual((report['status'], report['hip_rule'], report['tracks']), ('unchanged', 'off', []))
        with self.assertRaisesRegex(ValueError, "0 nodes named 'k_hip'"):
            reseatKey(model, source, seats)

    def test_model_names_must_be_unique_and_settings_valid(self):
        model = rig()
        model.meshHierarchy[RTHIGH].name = 'K_HIP'
        with self.assertRaisesRegex(ValueError, "2 nodes named 'k_hip'"):
            reseatKey(model, self.source, self.seats)
        with self.assertRaises(ValueError):
            reseatKey(self.model, self.source, self.seats, waistTuck=-0.001)
        with self.assertRaises(ValueError):
            reseatKey(self.model, self.source, self.seats, tolerance=float('nan'))
        with self.assertRaises(ValueError):
            reseatKey(self.model, self.source, self.seats, names=ReseatNames(seated=('k_hip',)))
        with self.assertRaises(ValueError):
            checkReseat(self.model, self.source, self.source, step=0.0)


class LayoutTests(unittest.TestCase):
    """The hip rule only runs where the hip and the carrier share a frame and the thighs hang from the hip."""

    def setUp(self):
        self.seats = keySeats(standKey())
        self.source = saberKey()

    def reseat(self, model, source=None):
        return reseatKey(model, source or self.source, self.seats)

    def assertUnsupported(self, report, reason, index):
        self.assertEqual((report['status'], report['hip_rule']), ('review', 'unsupported'))
        self.assertEqual(report['issue_counts'], {'hip_rule_unsupported': 1})
        self.assertEqual((report['findings'][0]['reason'], report['findings'][0]['index']), (reason, index))
        # The seat rule still runs.
        self.assertEqual([(t['index'], t['rule']) for t in report['tracks']], [(LFOREARM, 'seat')])

    def test_rotated_adapter_between_hip_and_root(self):
        model = rig()
        model.meshHierarchy[16].rotation = Vector3f(0.0, 5.0, 0.0)
        self.assertUnsupported(self.reseat(model)[1], 'hip_and_carrier_frames_differ', 16)

    def test_driven_adapter_between_hip_and_root(self):
        source = copy.deepcopy(self.source)
        source.numJoints = len(RIG)
        node = KeyNode()
        node.idx = 16
        node.meshName = 'a_hip'
        node.keyframes = [entry(0, RIG[16][2])]
        source.nodes.append(node)
        self.assertUnsupported(self.reseat(rig(), source)[1], 'hip_and_carrier_frames_differ', 16)

    def test_rotated_adapter_between_torso_and_carrier(self):
        model = rig()
        model.meshHierarchy[17].rotation = Vector3f(2.0, 0.0, 0.0)
        self.assertUnsupported(self.reseat(model)[1], 'rotated_node_between_torso_and_carrier', 17)

    def test_thigh_not_under_the_hip(self):
        model = rig()
        model.meshHierarchy[LTHIGH].parentIdx = ROOT
        self.assertUnsupported(self.reseat(model)[1], 'thigh_not_under_hip', LTHIGH)

    def test_key_without_the_carrier_uses_the_root_and_is_refused(self):
        source = copy.deepcopy(self.source)
        source.nodes = [n for n in source.nodes if n.idx != CARRIER]
        _, report = self.reseat(rig(), source)
        self.assertUnsupported(report, 'hip_and_carrier_nested', HIP)
        self.assertEqual(report['carrier'], {'index': ROOT, 'name': '$$$dummy'})

    def test_key_without_a_thigh_track_leaves_the_hip(self):
        source = copy.deepcopy(self.source)
        source.nodes = [n for n in source.nodes if n.idx != RTHIGH]
        _, report = self.reseat(rig(), source)
        self.assertEqual((report['status'], report['hip_rule'], report['issue_counts']), ('reseated', 'not_applicable', {}))

    def test_missing_seat_is_reported(self):
        seats = dict(self.seats)
        del seats[CARRIER]
        del seats[LFOREARM]
        _, report = reseatKey(rig(), self.source, seats)
        self.assertEqual((report['status'], report['hip_rule'], report['changed']), ('review', 'missing_seat', False))
        self.assertEqual(report['issue_counts'], {'missing_seat': 2})
        self.assertEqual(sorted(f['index'] for f in report['findings']), [CARRIER, LFOREARM])


class SkipTests(unittest.TestCase):
    def setUp(self):
        self.model = rig()
        self.seats = keySeats(standKey())

    def assertSkipped(self, source, code):
        before = records(source)
        result, report = reseatKey(self.model, source, self.seats)
        self.assertEqual((report['status'], report['changed'], list(report['issue_counts'])), ('skipped', False, [code]))
        self.assertEqual(records(result), before)
        self.assertEqual(records(source), before)

    def test_keys_that_cannot_be_rewritten_safely(self):
        source = saberKey()
        source.fileFormat = KeyFileFormat.GrimBinary
        self.assertSkipped(source, 'non_text_key')
        source = saberKey()
        source.numFrames = 0
        self.assertSkipped(source, 'invalid_frame_count')
        source = saberKey()
        keyTrack(source, LCALF).keyframes[2].frame = 1.0
        self.assertSkipped(source, 'invalid_track_timing')
        source = saberKey()
        source.sourceSkippedMarkers = 1      # the loader dropped a marker type it does not know
        self.assertSkipped(source, 'unrecognized_markers')


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.model = rig()
        self.source = saberKey()
        self.result, _ = reseatKey(self.model, self.source, keySeats(standKey()))

    def check(self, result):
        return checkReseat(self.model, self.source, result)

    def test_uncompensated_thigh_moves_the_leg(self):
        bad = copy.deepcopy(self.result)
        first = keyTrack(bad, LTHIGH).keyframes[0]
        first.position = Vector3f(first.position[0], first.position[1], first.position[2] + 0.001)
        check = self.check(bad)
        self.assertEqual(check['status'], 'fail')
        self.assertIn('fixed_joint_moved', check['issue_counts'])
        self.assertEqual(check['fixed_moved_at_frames']['time'], 0.0)
        self.assertIn(check['fixed_moved_at_frames']['name'], ('k_lthigh', 'k_lcalf'))
        self.assertAlmostEqual(check['fixed_moved_at_frames']['value'], 0.001, places=12)

    def test_rotation_and_untouched_track_changes_are_found(self):
        bad = copy.deepcopy(self.result)
        first = keyTrack(bad, HIP).keyframes[0]
        first.orientation = Vector3f(first.orientation[0] + 1.0, first.orientation[1], first.orientation[2])
        self.assertIn('orientation_changed', self.check(bad)['issue_counts'])
        bad = copy.deepcopy(self.result)
        keyTrack(bad, LCALF).keyframes[1].orientation = Vector3f(-34.0, 0.0, 0.0)
        counts = self.check(bad)['issue_counts']
        self.assertIn('unexpected_track_change', counts)
        self.assertIn('orientation_changed', counts)

    def test_header_markers_and_track_list(self):
        bad = copy.deepcopy(self.result)
        bad.fps = 30.0
        bad.markers = bad.markers[:1]
        bad.nodes = [n for n in bad.nodes if n.idx != RHAND]
        check = self.check(bad)
        self.assertEqual(check['issue_counts'].get('header_differs'), 1)
        self.assertEqual(check['findings'][0], {'code': 'header_differs', 'field': 'fps'})
        self.assertIn('markers_differ', check['issue_counts'])
        self.assertIn('track_list_differs', check['issue_counts'])

    def test_findings_are_bounded_and_counted(self):
        bad = copy.deepcopy(self.result)
        for node in bad.nodes:
            for e in node.keyframes:
                e.orientation = Vector3f(e.orientation[0] + 5.0, e.orientation[1], e.orientation[2])
        check = self.check(bad)
        self.assertEqual(len(check['findings']), 32)
        self.assertTrue(check['findings_truncated'])
        self.assertGreater(sum(check['issue_counts'].values()), 32)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.model = rig()
        self.src = os.path.join(self.directory.name, 'src')
        os.makedirs(self.src)
        self.paths = []
        for key in (standKey(), saberKey(), makeKey({i: [entry(0, SEATS[i])] for i in range(JOINTS)}, 2, name='still.key')):
            path = os.path.join(self.src, key.name)
            saveKey(key, path, 'synthetic test KEY')
            self.paths.append(path)
        self.stand = self.paths[0]

    def test_writes_only_changed_keys_and_checks_them(self):
        out = os.path.join(self.directory.name, 'out')
        report = reseatKeyFiles(self.model, self.paths, out, standKeyPath=self.stand)
        self.assertEqual((report['status'], report['seats'], report['keys']), ('reseated', 'kystand1.key frame 0', 3))
        self.assertEqual((report['changed_keys'], report['unchanged_keys']), (['kysabr.key'], 2))
        self.assertEqual(sorted(os.listdir(out)), ['kysabr.key'])
        self.assertEqual(report['written'], [os.path.join(out, 'kysabr.key')])
        self.assertEqual(report['check']['failed_keys'], [])
        self.assertEqual(report['check']['checked_keys'], 1)
        entry = report['per_key']['kysabr.key']
        self.assertEqual((entry['status'], entry['hip_rule'], entry['check']), ('reseated', 'applied', 'pass'))
        self.assertEqual(sorted(entry['tracks']), ['2', '4', '5', '9'])
        self.assertEqual(report['per_key']['still.key'], {'status': 'unchanged', 'hip_rule': 'within_tolerance', 'tracks': {}})

        # The file holds exactly the KEY reseatKey returns: the writer keeps every number.
        source = loadKey(self.paths[1])
        expected, _ = reseatKey(self.model, source, keySeats(loadKey(self.stand)))
        written = loadKey(os.path.join(out, 'kysabr.key'))
        self.assertEqual(records(written), records(expected))
        self.assertEqual(header(written)[1:], header(source)[1:])

        pairs = [(self.paths[1], os.path.join(out, 'kysabr.key')), (self.paths[2], self.paths[2])]
        checked = checkReseatFiles(self.model, pairs)
        self.assertEqual((checked['status'], checked['identical_keys'], checked['checked_keys']), ('pass', 1, 1))
        self.assertEqual(checked['per_key']['kysabr.key']['status'], 'pass')

    def test_model_rest_seats_must_be_asked_for(self):
        out = os.path.join(self.directory.name, 'rest')
        with self.assertRaises(TypeError):
            reseatKeyFiles(self.model, self.paths, out)
        report = reseatKeyFiles(self.model, self.paths, out, standKeyPath=None)
        self.assertEqual(report['seats'], 'model rest positions')
        self.assertIn('kysabr.key', report['changed_keys'])

    def test_refuses_to_overwrite_sources_or_merge_names(self):
        with self.assertRaisesRegex(ValueError, 'holds the source KEY'):
            reseatKeyFiles(self.model, self.paths, self.src, standKeyPath=self.stand)
        other = os.path.join(self.directory.name, 'other')
        os.makedirs(other)
        twin = os.path.join(other, 'KYSABR.KEY')
        saveKey(saberKey(), twin, 'synthetic test KEY')
        out = os.path.join(self.directory.name, 'out')
        with self.assertRaisesRegex(ValueError, 'Two KEYs are named'):
            reseatKeyFiles(self.model, self.paths + [twin], out, standKeyPath=self.stand)
        self.assertFalse(os.path.exists(out))

    def test_unreadable_key_is_reported(self):
        broken = os.path.join(self.src, 'broken.key')
        with open(broken, 'w', encoding='utf-8') as stream:
            stream.write('SECTION: HEADER\nFLAGS 0\n')
        report = reseatKeyFiles(self.model, [broken, self.paths[1]], os.path.join(self.directory.name, 'out'),
                                standKeyPath=self.stand)
        self.assertEqual(report['status'], 'review')
        self.assertEqual(report['issue_counts'], {'unreadable_key': 1})
        self.assertEqual(report['findings'][0]['key'], 'broken.key')
        self.assertEqual(report['changed_keys'], ['kysabr.key'])


if __name__ == '__main__':
    unittest.main()
