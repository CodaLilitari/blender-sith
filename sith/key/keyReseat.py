# SPDX-License-Identifier: GPL-3.0-or-later
"""Put stock KEY joints back on their seats for characters cut into close-fitting rigid pieces.

Stock KEYs were authored on Kyle, whose meshes overlap at every joint. Some of them move a joint
a few thousandths of a JK unit off its usual seat. The saber KEYs seat the hip piece up to 0.008
units low and push the thighs up into it to make up for it. The two armed runs shift both
forearms about 0.004 units, and ``kystab2`` shifts the shoulders about 0.004 units. Kyle hides
this. A character cut into close-fitting rigid pieces opens at those joints. ``reseatKey`` moves
the joints back in the KEY data. It never changes a rotation, and it moves no joint outside the
hip piece and the seated joints.

The rules are those of the reseat pass behind Darth Lilitari R9.10 and Lilivane R3:

- A seated joint (by default the shoulders, forearms, hands, neck and head) that leaves its seat
  by more than ``tolerance`` on any axis, at any time the engine plays, takes its seat position
  for the whole KEY. Its entries, rotations and timing are kept.
- The hip piece is moved between the torso and the thighs. Two offsets are measured in the hip's
  own frame (side, front, up): ``waist``, how far the hip sits from its seat under the torso, and
  ``push``, the thighs' mean offset from their seats on the hip. On each axis where the two point
  opposite ways, the smaller of the two is taken out of both: the hip moves, and the thighs move
  back by the same amount, so the torso and the legs stay where the KEY put them. Where the
  thighs are still pushed up after that, up to ``waistTuck`` of the push goes to the waist as a
  small overlap. No joint ends up further apart than the KEY had it, and no sideways or
  front-to-back offset grows. ``waistLift`` (off by default; Lilivane R3 used 0.0015) then lets
  the hip follow the torso up where a waist gap is left. That opens the hip joints by the same
  distance.

The hip rule needs Kyle's layout. The hip and the torso's carrier (the torso's nearest ancestor
that the KEY drives: Kyle's second ``$$$DUMMY``) hang from one frame, the torso hangs from the
carrier and the thighs hang from the hip. Model nodes between them are allowed when the KEY does
not drive them and they have no rest rotation (the adapter nodes of custom characters): they
only add constant offsets, and those cancel. Otherwise the rule is reported as unsupported and
not applied.

Timing follows the engine. A track's value at time ``t`` comes from its last entry at or before
``t``, with that entry's deltas applied, also past the last entry (``rdPuppet_BuildJointMatrices``
in OpenJKDF2's ``Engine/rdPuppet.c``). ``rdPuppet_AdvanceTrack`` wraps a looping track at
``FRAMES``, so every time below ``FRAMES`` is played. A track that does not loop stops at
``FRAMES``; when it pauses or fades out there, the pose at ``FRAMES`` is held. The puppet file or
the COG that plays a KEY decides which applies (``sithPuppet_PlayKey``), not the KEY's ``FLAGS``.
The hip and thigh tracks are therefore rebuilt with an entry at every whole frame and at every
source entry time. Each entry runs in a straight line to the value the rule gives just before the
next entry, so a step in the source stays a step, and the last entry carries the motion on to
``FRAMES``. Between entries the rule is not linear, so the legs can move slightly between frames
while the hip turns (up to about 0.00006 units on Darth Lilitari and Lilivane).

``checkReseat`` poses a KEY and its reseated copy alone at full weight with ``sith.audit.pose``
and reports what moved. Blends with other KEYs, fades, the player's aim pitch and puppet timing
are not checked.
"""

import copy
from dataclasses import dataclass
import math
import os
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from ..audit.pose import PoseLayer, keyTrack, nodeTypeValue, poseModel, rotationMatrix, sampleTrack
from ..types import Vector3f
from .key import KeyFileFormat, Keyframe, KeyframeFlag

__all__ = ['ReseatNames', 'keySeats', 'modelSeats', 'reseatKey', 'checkReseat', 'reseatKeyFiles',
           'checkReseatFiles']

DETAIL_LIMIT = 32


@dataclass(frozen=True)
class ReseatNames:
    """Model node names the reseat pass works on, matched without regard to case.

    The defaults are Kyle's skeleton, which most custom characters reuse. ``hip=None`` turns the
    hip rule off and an empty ``seated`` turns the seat rule off. The torso's carrier is not
    named: for each KEY it is the torso's nearest ancestor that the KEY drives.
    """
    hip: Optional[str] = 'k_hip'
    torso: str = 'k_torso'
    thighs: Tuple[str, ...] = ('k_lthigh', 'k_rthigh')
    seated: Tuple[str, ...] = ('k_lshouldr', 'k_lforearm', 'k_lhand', 'k_rshouldr', 'k_rforearm',
                               'k_rhand', 'k_neck', 'k_head')


@dataclass(frozen=True)
class _Rig:
    names: Tuple[str, ...]
    parents: Tuple[int, ...]
    rotated: Tuple[bool, ...]
    hip: Optional[int]
    torso: Optional[int]
    thighs: Tuple[int, ...]
    seated: Tuple[int, ...]

    def ancestors(self, index: int) -> List[int]:
        """Ancestors of ``index``, parent first."""
        out: List[int] = []
        parent = self.parents[index]
        while parent >= 0:
            if parent == index or parent in out:
                raise ValueError('3DO hierarchy has a parent cycle')
            out.append(parent)
            parent = self.parents[parent]
        return out

    def subtree(self, roots: Iterable[int]) -> set:
        """``roots`` and every node below them."""
        roots = set(roots)
        return {i for i in range(len(self.parents)) if i in roots or roots.intersection(self.ancestors(i))}


def _rig(model, names: ReseatNames) -> _Rig:
    nodes = model.meshHierarchy
    found: Dict[str, List[int]] = {}
    parents = []
    for i, node in enumerate(nodes):
        if node.idx != i:
            raise ValueError('The reseat pass needs model nodes numbered in hierarchy order')
        parent = -1 if node.parentIdx is None or node.parentIdx < 0 else int(node.parentIdx)
        if parent >= len(nodes):
            raise ValueError(f'3DO node {i} has a parent outside the hierarchy')
        parents.append(parent)
        found.setdefault(node.name.casefold(), []).append(i)

    def one(name: str) -> int:
        hits = found.get(str(name).casefold(), [])
        if len(hits) != 1:
            raise ValueError(f"The model has {len(hits)} nodes named '{name}'; the reseat pass needs exactly one")
        return hits[0]

    hip = one(names.hip) if names.hip else None
    torso = one(names.torso) if hip is not None else None
    thighs = tuple(one(name) for name in names.thighs) if hip is not None else ()
    seated = tuple(sorted(one(name) for name in names.seated))
    if hip is not None and not thighs:
        raise ValueError('The hip rule needs at least one thigh')
    if len(set(thighs)) != len(thighs) or len(set(seated)) != len(seated):
        raise ValueError('Each reseat joint name may appear only once')
    if hip is not None and (hip == torso or hip in thighs or torso in thighs):
        raise ValueError('The hip, the torso and the thighs must be different nodes')
    if hip is not None and set(seated) & {hip, torso, *thighs}:
        raise ValueError('A seated joint cannot also be the hip, the torso or a thigh')
    rig = _Rig(tuple(node.name for node in nodes), tuple(parents),
               tuple(any(float(v) for v in node.rotation) for node in nodes), hip, torso, thighs, seated)
    for i in range(len(nodes)):
        rig.ancestors(i)                       # reject cycles before any KEY is read
    return rig


class _Findings:
    """Issue counts for everything and details for the first ``DETAIL_LIMIT`` issues."""

    def __init__(self):
        self.counts: Dict[str, int] = {}
        self.details: List[dict] = []

    def add(self, code: str, **details):
        self.counts[code] = self.counts.get(code, 0) + 1
        if len(self.details) < DETAIL_LIMIT:
            self.details.append({'code': code, **details})

    def into(self, report: dict) -> dict:
        report['issue_counts'] = dict(self.counts)
        report['findings'] = list(self.details)
        report['findings_truncated'] = sum(self.counts.values()) > len(self.details)
        return report


def _vector(value) -> np.ndarray:
    return np.asarray(tuple(value), dtype=float).reshape(3)


def keySeats(key, frame: float = 0.0) -> Dict[int, Tuple[float, float, float]]:
    """Joint positions a reference KEY holds at ``frame``, by node index.

    The reseat pass for Darth Lilitari R9.10 and Lilivane R3 took its seats from the stand KEY
    (``kystand1.key``) at frame 0. Tracks at or past the KEY's ``JOINTS`` are ignored, as in the
    engine, and a later track for a node replaces an earlier one.
    """
    seats = {}
    for node in key.nodes:
        track = keyTrack(key, node.idx)
        if track is not None:
            seats[node.idx] = tuple(float(v) for v in sampleTrack(track, float(frame))[0])
    return seats


def modelSeats(model) -> Dict[int, Tuple[float, float, float]]:
    """The model's rest positions by node index.

    Use them only when the model's rest pose is the seat the KEYs expect. Compare them with
    ``keySeats`` of the stand KEY first: a custom model's rest pose often differs from it.
    """
    return {i: tuple(float(v) for v in node.position) for i, node in enumerate(model.meshHierarchy)}


def _entryValues(entry, time: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(position, rotation, rotation per frame) of one entry extended to ``time``."""
    flags = int(entry.flags)
    dt = time - float(entry.frame)
    position = _vector(entry.position)
    rotation = _vector(entry.orientation)
    spin = _vector(entry.deltaRotation) if flags & 2 else np.zeros(3)
    if flags & 1:
        position = position + _vector(entry.deltaPosition) * dt
    return position, rotation + spin * dt, spin


def _governingEntry(track, time: float):
    """The entry the engine uses at ``time``: the last one at or before it, else the first."""
    current = track.keyframes[0]
    for candidate in track.keyframes:
        if float(candidate.frame) <= time:
            current = candidate
        else:
            break
    return current


def _segmentEnds(track, frames: int) -> List[np.ndarray]:
    """Positions at both ends of every straight piece of a track within ``[0, frames]``.

    A piece is linear, so its largest distance from any point is at one of its ends.
    """
    entries = track.keyframes
    out = []
    for k, entry in enumerate(entries):
        start = 0.0 if k == 0 else float(entry.frame)
        end = float(entries[k + 1].frame) if k + 1 < len(entries) else float(frames)
        start, end = max(start, 0.0), min(end, float(frames))
        if start <= end:
            out.extend(_entryValues(entry, t)[0] for t in (start, end))
    return out


def _entry(frame: float, position, rotation, velocity, spin) -> Keyframe:
    entry = Keyframe()
    entry.frame = float(frame)
    entry.flags = KeyframeFlag((1 if np.any(velocity != 0.0) else 0) | (2 if np.any(spin != 0.0) else 0))
    entry.position = Vector3f(*(float(v) for v in position))
    entry.orientation = Vector3f(*(float(v) for v in rotation))
    entry.deltaPosition = Vector3f(*(float(v) for v in velocity))
    entry.deltaRotation = Vector3f(*(float(v) for v in spin))
    return entry


def _seatedEntries(track, seat: np.ndarray) -> List[Keyframe]:
    """The track's own entries at ``seat``, with no position motion and the rotations unchanged."""
    out = []
    for source in track.keyframes:
        entry = Keyframe()
        entry.frame = source.frame
        entry.flags = KeyframeFlag(int(source.flags) & int(KeyframeFlag.OrientationChange))
        entry.position = Vector3f(*(float(v) for v in seat))
        entry.orientation = source.orientation
        entry.deltaPosition = Vector3f(0.0, 0.0, 0.0)
        entry.deltaRotation = source.deltaRotation
        out.append(entry)
    return out


def _bakeTimes(tracks, frames: int) -> List[float]:
    """Every whole frame below ``frames`` and every source entry time within ``[0, frames]``."""
    times = {float(f) for f in range(frames)}
    for track in tracks:
        times.update(float(e.frame) for e in track.keyframes if 0.0 <= float(e.frame) <= frames)
    return sorted(times)


def _intervalEnd(times: Sequence[float], k: int, frames: int) -> Optional[float]:
    """Where the baked entry at ``times[k]`` stops applying: the next time, else ``frames``."""
    if k + 1 < len(times):
        return times[k + 1]
    return float(frames) if times[k] < frames else None


def _bakedEntries(track, times: Sequence[float], starts: Sequence[np.ndarray],
                  ends: Sequence[Optional[np.ndarray]], frames: int) -> List[Keyframe]:
    """Entries at ``times`` that run in a straight line from ``starts[k]`` to ``ends[k]``.

    ``ends[k]`` is the position just before the next entry takes over, so a step in the source
    stays a step. No source entry lies strictly between two baked times, so each baked entry
    copies the rotation and rotation rate of the source entry that governs its time.
    """
    out = []
    for k, time in enumerate(times):
        end = _intervalEnd(times, k, frames)
        velocity = np.zeros(3) if end is None else (ends[k] - starts[k]) / (end - time)
        _, rotation, spin = _entryValues(_governingEntry(track, time), time)
        out.append(_entry(time, starts[k], rotation, velocity, spin))
    return out


def _hipShiftLocal(waist: np.ndarray, push: np.ndarray, waistTuck: float, waistLift: float) -> np.ndarray:
    """The hip's shift in its own frame (side, front, up); the thighs move back by the same shift.

    ``waist`` is how far the hip sits from its seat under the torso (up is positive: into the
    torso). ``push`` is the thighs' mean offset from their seats on the hip (up is positive: into
    the hip).
    """
    shift = np.zeros(3)
    for axis in range(3):
        if waist[axis] * push[axis] < 0.0:
            shift[axis] = math.copysign(min(abs(waist[axis]), abs(push[axis])), push[axis])
    if waistLift > 0.0 and waist[2] + shift[2] < 0.0:
        shift[2] += min(-(waist[2] + shift[2]), waistLift)
    if waistTuck > 0.0 and push[2] - shift[2] > 0.0 and waist[2] + shift[2] < waistTuck:
        shift[2] += min(push[2] - shift[2], waistTuck - (waist[2] + shift[2]))
    return shift


def _hipSetup(rig: _Rig, key):
    """('ready', carrier, None), ('not_applicable', None, None) or ('unsupported', carrier, problem)."""
    def driven(i):
        return keyTrack(key, i) is not None

    if not all(driven(i) for i in (rig.hip, rig.torso, *rig.thighs)):
        return 'not_applicable', None, None
    above = rig.ancestors(rig.torso)
    carrier = next((i for i in above if driven(i)), None)
    if carrier is None:
        return 'not_applicable', None, None

    def problem(reason, index):
        return 'unsupported', carrier, {'reason': reason, 'index': index, 'name': rig.names[index][:128]}

    def blocking(path):
        return next((i for i in path if driven(i) or rig.rotated[i]), None)

    bad = blocking(above[:above.index(carrier)])
    if bad is not None:
        return problem('rotated_node_between_torso_and_carrier', bad)
    hipUp, carrierUp = rig.ancestors(rig.hip), rig.ancestors(carrier)
    if carrier in hipUp or rig.hip in carrierUp:
        return problem('hip_and_carrier_nested', rig.hip)
    common = next((i for i in hipUp if i in carrierUp), None)
    between = (hipUp + carrierUp) if common is None else (hipUp[:hipUp.index(common)] + carrierUp[:carrierUp.index(common)])
    bad = blocking(between)
    if bad is not None:
        return problem('hip_and_carrier_frames_differ', bad)
    for thigh in rig.thighs:
        thighUp = rig.ancestors(thigh)
        if rig.hip not in thighUp:
            return problem('thigh_not_under_hip', thigh)
        bad = blocking(thighUp[:thighUp.index(rig.hip)])
        if bad is not None:
            return problem('thigh_frame_differs', bad)
    return 'ready', carrier, None


def _hipRule(rig: _Rig, key, carrier: int, seats, frames: int, waistTuck: float, waistLift: float,
             tolerance: float):
    """('applied', {index: (entries, rule, largest shift)}) or ('within_tolerance', {})."""
    hip, torso, thighs = rig.hip, rig.torso, rig.thighs
    tracks = {i: keyTrack(key, i) for i in (carrier, hip, torso, *thighs)}
    largest = {i: 0.0 for i in (hip, *thighs)}

    def reseated(entries, time):
        """New hip and thigh positions where ``entries`` govern the source at ``time``."""
        value = {i: _entryValues(entry, time) for i, entry in entries.items()}
        (p1, r1, _), (p2, r2, _), p17 = value[carrier], value[hip], value[torso][0]
        carrierTurn, hipTurn = rotationMatrix(r1), rotationMatrix(r2)
        waist = (p2 - p1) - (seats[hip] - seats[carrier]) - carrierTurn @ (p17 - seats[torso])
        push = np.mean([hipTurn @ (value[t][0] - seats[t]) for t in thighs], axis=0)
        shift = hipTurn @ _hipShiftLocal(hipTurn.T @ waist, hipTurn.T @ push, waistTuck, waistLift)
        back = hipTurn.T @ shift
        largest[hip] = max(largest[hip], float(np.abs(shift).max()))
        for thigh in thighs:
            largest[thigh] = max(largest[thigh], float(np.abs(back).max()))
        return {hip: p2 + shift, **{thigh: value[thigh][0] - back for thigh in thighs}}

    times = _bakeTimes(tracks.values(), frames)
    starts, ends = [], []
    for k, time in enumerate(times):
        governing = {i: _governingEntry(track, time) for i, track in tracks.items()}
        end = _intervalEnd(times, k, frames)
        starts.append(reseated(governing, time))
        ends.append(None if end is None else reseated(governing, end))
    if largest[hip] <= tolerance:
        return 'within_tolerance', {}
    out = {}
    for i, rule in ((hip, 'hip'), *((thigh, 'thigh') for thigh in thighs)):
        entries = _bakedEntries(tracks[i], times, [s[i] for s in starts], [None if e is None else e[i] for e in ends],
                                frames)
        out[i] = (entries, rule, largest[i])
    return 'applied', out


def _checkSettings(**values):
    for name, value in values.items():
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f'{name} must be a finite, nonnegative JK distance')


def reseatKey(model, key, seats: Mapping[int, Sequence[float]], *, names: Optional[ReseatNames] = None,
              waistTuck: float = 0.0025, waistLift: float = 0.0, tolerance: float = 2e-5):
    """Return ``(reseated copy of key, report)`` for ``key`` played on ``model``. Neither is changed.

    ``seats`` maps node indices to seat positions: ``keySeats`` of the stand KEY, or
    ``modelSeats``. A seated joint is reseated when it leaves its seat by more than ``tolerance``
    on any axis at any time in ``[0, FRAMES]``. The hip rule is applied when it would move the hip
    by more than ``tolerance``. Rewritten tracks replace their source tracks in place; every other
    track, the markers and the header are copied as they are.

    The report gives ``status`` (``reseated``, ``unchanged``, ``review`` when a finding stopped a
    rule, or ``skipped`` when the KEY cannot be rewritten safely), ``hip_rule`` (``applied``,
    ``within_tolerance``, ``not_applicable``, ``unsupported``, ``missing_seat`` or ``off``), the
    ``carrier`` it used, and one record per rewritten track with its largest position change
    (any axis, JK units) and its entry counts before and after.
    """
    rig = _rig(model, names or ReseatNames())
    _checkSettings(waistTuck=waistTuck, waistLift=waistLift, tolerance=tolerance)
    seats = {int(i): _vector(position) for i, position in seats.items()}
    result = copy.deepcopy(key)
    findings = _Findings()
    frames = int(key.numFrames)
    report = {'key': key.name, 'frames': frames, 'changed': False, 'hip_rule': 'off', 'carrier': None,
              'tracks': []}

    if key.fileFormat != KeyFileFormat.Text:
        findings.add('non_text_key')
    elif frames < 1:
        findings.add('invalid_frame_count', frames=frames)
    else:
        if getattr(key, 'sourceSkippedMarkers', 0):
            # The loader drops marker types it does not know, so a rewritten file would lose them.
            findings.add('unrecognized_markers', count=int(key.sourceSkippedMarkers))
        for node in key.nodes:
            times = [float(e.frame) for e in node.keyframes]
            if any(b <= a for a, b in zip(times, times[1:])):
                findings.add('invalid_track_timing', index=node.idx, key_name=node.meshName[:128])
    if findings.counts:
        report['status'] = 'skipped'
        return result, findings.into(report)

    rewritten = {}
    for i in rig.seated:
        track = keyTrack(key, i)
        if track is None:
            continue
        if i not in seats:
            findings.add('missing_seat', index=i, name=rig.names[i][:128])
            continue
        largest = max(float(np.abs(position - seats[i]).max()) for position in _segmentEnds(track, frames))
        if largest > tolerance:
            rewritten[i] = (_seatedEntries(track, seats[i]), 'seat', largest)

    if rig.hip is not None:
        state, carrier, problem = _hipSetup(rig, key)
        if carrier is not None:
            report['carrier'] = {'index': carrier, 'name': rig.names[carrier][:128]}
        if problem is not None:
            findings.add('hip_rule_unsupported', **problem)
        if state == 'ready':
            missing = [i for i in (carrier, rig.hip, rig.torso, *rig.thighs) if i not in seats]
            for i in missing:
                findings.add('missing_seat', index=i, name=rig.names[i][:128])
            if missing:
                state = 'missing_seat'
            else:
                state, entries = _hipRule(rig, key, carrier, seats, frames, waistTuck, waistLift, tolerance)
                rewritten.update(entries)
        report['hip_rule'] = state

    for i in sorted(rewritten):
        entries, rule, largest = rewritten[i]
        keyTrack(result, i).keyframes = entries
        report['tracks'].append({'index': i, 'name': rig.names[i][:128], 'rule': rule, 'largest_shift': largest,
                                 'entries': [len(keyTrack(key, i).keyframes), len(entries)]})
    report['changed'] = bool(rewritten)
    report['status'] = 'review' if findings.counts else ('reseated' if rewritten else 'unchanged')
    return result, findings.into(report)


def _plain(value):
    return int(nodeTypeValue(value)) if hasattr(value, 'value') else value


def _entryRecord(entry):
    return (float(entry.frame), int(entry.flags), tuple(entry.position), tuple(entry.orientation),
            tuple(entry.deltaPosition), tuple(entry.deltaRotation))


def _worst(value=0.0, time=None, index=None, name=None):
    return {'value': value, 'time': time, 'index': index, 'name': name}


_METRICS = ('largest_orientation_change', 'fixed_moved_at_frames', 'fixed_moved_between_frames', 'hip_moved',
            'seated_moved')


def checkReseat(model, source, result, *, names: Optional[ReseatNames] = None, step: float = 0.5,
                rotationTolerance: float = 1e-9, frameTolerance: float = 1e-6, betweenTolerance: float = 1e-4):
    """Pose ``source`` and ``result`` on ``model`` and report what the reseat moved.

    Each KEY is posed alone at full weight (``sith.audit.pose``) at every ``step`` from 0 to
    ``FRAMES`` inclusive, which covers every time the engine plays. Joint frames are compared.
    Expected: no joint's orientation changes (largest difference of any rotation-matrix element,
    at most ``rotationTolerance``), and no joint outside the hip piece and the seated joints moves
    (at most ``frameTolerance`` JK units at whole frames and ``betweenTolerance`` between them).
    Fixed joints are all the others: the torso, the thighs and everything below them, the root
    and the carrier. The hip and the seated joints may move; the report gives how far and when.
    The header, the markers, the track list and every track the pass cannot rewrite must also be
    equal. ``status`` is ``pass`` or ``fail``.
    """
    rig = _rig(model, names or ReseatNames())
    if not (math.isfinite(step) and step > 0.0):
        raise ValueError('step must be a positive number of frames')
    _checkSettings(rotationTolerance=rotationTolerance, frameTolerance=frameTolerance,
                   betweenTolerance=betweenTolerance)
    findings = _Findings()
    frames = max(int(source.numFrames), 1)
    for field in ('flags', 'nodeTypes', 'numFrames', 'fps', 'numJoints'):
        if _plain(getattr(source, field)) != _plain(getattr(result, field)):
            findings.add('header_differs', field=field)
    marks = [sorted((float(m.frame), int(m.type)) for m in k.markers) for k in (source, result)]
    if marks[0] != marks[1]:
        findings.add('markers_differ')
    tracks = [{node.idx: node for node in k.nodes} for k in (source, result)]
    if sorted(tracks[0]) != sorted(tracks[1]):
        findings.add('track_list_differs')
    rewritable = set(rig.seated) | ({rig.hip, *rig.thighs} if rig.hip is not None else set())
    for i in sorted(set(tracks[0]) & set(tracks[1])):
        a, b = tracks[0][i], tracks[1][i]
        if a.meshName != b.meshName or (i not in rewritable and
                                        [_entryRecord(e) for e in a.keyframes] != [_entryRecord(e) for e in b.keyframes]):
            findings.add('unexpected_track_change', index=i, key_name=a.meshName[:128])

    moved = rig.subtree(rig.seated)
    if rig.hip is not None:
        moved |= rig.subtree([rig.hip]) - rig.subtree(rig.thighs)
    count = len(rig.names)
    fixed = np.array([i not in moved for i in range(count)])
    seatedMask = np.array([i in rig.seated for i in range(count)])
    hipMask = np.array([i == rig.hip for i in range(count)])
    allMask = np.ones(count, dtype=bool)
    worst = {metric: _worst() for metric in _METRICS}

    def keep(metric, values, mask, time):
        if not mask.any():
            return
        index = int(np.argmax(np.where(mask, values, -1.0)))
        if values[index] > worst[metric]['value']:
            worst[metric] = _worst(float(values[index]), time, index, rig.names[index][:128])

    times = [round(k * step, 9) for k in range(int(math.floor(frames / step + 1e-9)) + 1)]
    for time in times:
        a = poseModel(model, [PoseLayer(source, time, lowPri=1)], vertices=False).frames
        b = poseModel(model, [PoseLayer(result, time, lowPri=1)], vertices=False).frames
        turn = np.abs(a[:, :3, :3] - b[:, :3, :3]).max(axis=(1, 2))
        move = np.linalg.norm(a[:, :3, 3] - b[:, :3, 3], axis=1)
        onFrame = abs(time - round(time)) < 1e-9
        limit = frameTolerance if onFrame else betweenTolerance
        keep('largest_orientation_change', turn, allMask, time)
        keep('fixed_moved_at_frames' if onFrame else 'fixed_moved_between_frames', move, fixed, time)
        keep('hip_moved', move, hipMask, time)
        keep('seated_moved', move, seatedMask, time)
        for i in np.flatnonzero(turn > rotationTolerance):
            findings.add('orientation_changed', time=time, index=int(i), name=rig.names[i][:128], value=float(turn[i]))
        for i in np.flatnonzero(fixed & (move > limit)):
            findings.add('fixed_joint_moved', time=time, index=int(i), name=rig.names[i][:128], value=float(move[i]))

    report = {'key': source.name, 'frames': int(source.numFrames), 'step': step, 'samples': len(times), **worst}
    report['status'] = 'fail' if findings.counts else 'pass'
    return findings.into(report)


def _merge(findings: _Findings, report: dict, keyName: str):
    """Add one KEY's findings to those of a set, naming the KEY and keeping the counts exact."""
    shown: Dict[str, int] = {}
    for detail in report['findings']:
        findings.add(**detail, key=keyName)
        shown[detail['code']] = shown.get(detail['code'], 0) + 1
    for code, count in report['issue_counts'].items():
        findings.counts[code] = findings.counts.get(code, 0) + count - shown.get(code, 0)


def _checkTotals(checks: Sequence[dict]) -> dict:
    """The worst value of each check metric over a set of KEYs, with the KEY it came from."""
    totals = {'checked_keys': len(checks),
              'failed_keys': sorted(c['key'] for c in checks if c['status'] != 'pass')}
    for metric in _METRICS:
        totals[metric] = dict(_worst(), key=None)
        for checked in checks:
            if checked[metric]['value'] > totals[metric]['value']:
                totals[metric] = dict(checked[metric], key=checked['key'])
    return totals


def _loadModel(model):
    """``model`` itself when it is already loaded, else the model read from that path."""
    if hasattr(model, 'meshHierarchy'):
        return model
    from ..model.model3doLoader import load3do
    return load3do(model)[0]


def reseatKeyFiles(model, keyPaths: Iterable, outDir, *, standKeyPath, standFrame: float = 0.0,
                   names: Optional[ReseatNames] = None, waistTuck: float = 0.0025, waistLift: float = 0.0,
                   tolerance: float = 2e-5, check: bool = True) -> dict:
    """Reseat text KEY files for one model and write the changed ones to ``outDir`` with ``saveKey``.

    ``model`` is a loaded ``Model3do`` or the path of a 3DO. Seats come from the KEY at
    ``standKeyPath`` at ``standFrame`` (the stand KEY ``kystand1.key`` at frame 0 for Darth
    Lilitari R9.10 and Lilivane R3). Pass ``standKeyPath=None`` to use the model's rest positions
    instead. A KEY that needs no change is not written: install only the written files. With
    ``check``, every written file is loaded back and compared with its source by ``checkReseat``.

    The report's ``status`` is ``fail`` when a check failed, ``review`` when any KEY has a
    finding, else ``reseated`` or ``unchanged``. ``per_key`` gives each KEY's status, hip rule,
    largest shift by track index and check result.
    """
    from .keyLoader import loadKey
    from .keyWriter import saveKey
    paths = list(keyPaths)
    _checkSettings(waistTuck=waistTuck, waistLift=waistLift, tolerance=tolerance)
    seenNames: Dict[str, str] = {}
    for path in paths:
        name = os.path.basename(str(path))
        if name.casefold() in seenNames:
            raise ValueError(f'Two KEYs are named {name}; reseat them into separate folders')
        seenNames[name.casefold()] = name
        target = os.path.join(str(outDir), name)
        if os.path.exists(target) and os.path.exists(path) and os.path.samefile(target, path):
            raise ValueError(f'The output folder holds the source KEY {name}; choose another output folder')
    model = _loadModel(model)
    seats = keySeats(loadKey(standKeyPath), standFrame) if standKeyPath is not None else modelSeats(model)
    seatSource = (f'{os.path.basename(str(standKeyPath))} frame {standFrame:g}' if standKeyPath is not None
                  else 'model rest positions')
    os.makedirs(outDir, exist_ok=True)
    findings = _Findings()
    perKey, checks, written = {}, [], []
    for path in paths:
        name = os.path.basename(str(path))
        try:
            key = loadKey(path)
        except (AssertionError, TypeError, ValueError, OSError, EOFError) as error:
            findings.add('unreadable_key', key=name, detail=str(error)[:256])
            continue
        new, report = reseatKey(model, key, seats, names=names, waistTuck=waistTuck, waistLift=waistLift,
                                tolerance=tolerance)
        _merge(findings, report, name)
        entry = {'status': report['status'], 'hip_rule': report['hip_rule'],
                 'tracks': {str(t['index']): t['largest_shift'] for t in report['tracks']}}
        if report['changed']:
            target = os.path.join(str(outDir), name)
            saveKey(new, target, f"KEYFRAME '{name}' reseated by Blender Sith (keyReseat): seats from {seatSource}, "
                                 f'waist tuck {waistTuck:g}, waist lift {waistLift:g}, tolerance {tolerance:g}')
            written.append(target)
            entry['written'] = target
            if check:
                checked = checkReseat(model, key, loadKey(target), names=names)
                _merge(findings, checked, name)
                checks.append(checked)
                entry['check'] = checked['status']
        perKey[name] = entry
    changed = sorted(name for name, entry in perKey.items() if 'written' in entry)
    failed = any(c['status'] != 'pass' for c in checks)
    status = 'fail' if failed else 'review' if findings.counts else 'reseated' if changed else 'unchanged'
    report = {'status': status, 'seats': seatSource, 'keys': len(perKey), 'changed_keys': changed,
              'unchanged_keys': len(perKey) - len(changed), 'written': written,
              'check': _checkTotals(checks) if check else None, 'per_key': perKey}
    return findings.into(report)


def checkReseatFiles(model, pairs: Iterable, *, names: Optional[ReseatNames] = None, step: float = 0.5,
                     rotationTolerance: float = 1e-9, frameTolerance: float = 1e-6,
                     betweenTolerance: float = 1e-4) -> dict:
    """Run ``checkReseat`` over ``(source path, reseated path)`` pairs for one model.

    ``model`` is a loaded ``Model3do`` or the path of a 3DO. Pairs whose files are byte for byte
    equal are counted as identical and not posed.
    """
    from .keyLoader import loadKey
    model = _loadModel(model)
    findings = _Findings()
    checks, identical = [], 0
    for sourcePath, resultPath in pairs:
        with open(sourcePath, 'rb') as a, open(resultPath, 'rb') as b:
            if a.read() == b.read():
                identical += 1
                continue
        checked = checkReseat(model, loadKey(sourcePath), loadKey(resultPath), names=names, step=step,
                              rotationTolerance=rotationTolerance, frameTolerance=frameTolerance,
                              betweenTolerance=betweenTolerance)
        _merge(findings, checked, os.path.basename(str(sourcePath)))
        checks.append(checked)
    report = _checkTotals(checks)
    report['status'] = 'fail' if report['failed_keys'] else 'pass'
    report['identical_keys'] = identical
    report['per_key'] = {c['key']: {'status': c['status'], **{m: c[m]['value'] for m in _METRICS}} for c in checks}
    return findings.into(report)
