# SPDX-License-Identifier: GPL-3.0-or-later
"""Lint text KEYs for custom JK/MotS animations, without Blender.

Four checks. Each returns plain data and none edits its inputs:

- ``lintKeyHeader``: the header and track table as the engine loads them. It adds to
  ``sith.key.modelBinding.auditModelKey``, which it runs when given a model; without one it runs
  that audit's checks that need no model.
- ``scanKeyPops``: pops at and between entries. These are Euler-form flips, steps too wide for a
  straight line in Euler components, jumps at an entry, angular-speed spikes and, with a model,
  world-position jumps.
- ``keyPriorityReport``: which model nodes a KEY drives at its high and low priority, and which
  it leaves to lower layers.
- ``trackRegistry``: the custom track slots several characters use, and where they collide
  when their KEYs must share one pack.

Engine rules reproduced here (OpenJKDF2's reconstruction of retail JK.EXE 1.01):

- ``rdKeyframe_LoadEntry`` allocates one track record per ``JOINTS`` and stores each track at
  ``aNodes[node]`` without a bound check. Retail allocates exactly ``JOINTS`` records, so a
  track numbered outside ``0 .. JOINTS-1`` is written outside the allocation. A later track for
  the same node overwrites the earlier record.
- ``rdPuppet_BuildJointMatrices`` reads a track only for nodes below ``JOINTS``. It finds the
  current entry by scanning forward from the last one, and keeps that entry per track number in
  a 64-entry array. From each entry it moves each (pitch, yaw, roll) component and the position
  with that entry's own deltas until the next entry, then takes the next entry's stored values.
  It blends the KEY's priority per node: the high priority where the KEY's node-type mask shares
  a bit with the node's type, else the low.
- ``rdPuppet_AdvanceTrack`` keeps playback time below ``FRAMES`` while a KEY loops. It stops at
  ``FRAMES`` when the play holds its last frame.

The pop scan samples tracks with ``sith.audit.pose``. It judges motion in rotation space: two
Euler triples for one rotation look the same in a still pose but can play differently.
"""

from dataclasses import asdict, dataclass
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..key.keyEuler import eulerTwin, nearestEuler, unwrapEuler
from ..key.modelBinding import DETAIL_LIMIT, auditModelKey, keyTargetFindings
from ..model.model3do import Mesh3doNodeType
from .pose import PoseLayer, keyTrack, nodeTypeValue, poseModel, restPose

__all__ = [
    'RECOMMENDED_JOINTS', 'PopLimits', 'Character', 'rotationAngle', 'lintKeyHeader',
    'scanKeyPops', 'keyPriorityReport', 'trackRegistry',
]

# rdPuppet tracks address at most RDPUPPET_MAX_NODES (64) model nodes (engine_config.h), and each
# playing track keeps its entry cursors in a 64-entry array (rdPuppetTrack.aCurKfNodeEntryNums).
RECOMMENDED_JOINTS = 64
_TARGETS = ('JKDF2', 'MOTS')
_STATUS_LEVEL = {'incompatible': 2, 'review': 1, 'consistent': 0, 'no_tracks': 0}


class _Findings:
    """Severity, totals and a bounded detail list, as in ``sith.key.modelBinding``."""

    def __init__(self):
        self.severity = 0
        self.counts: Dict[str, int] = {}
        self.items: List[dict] = []

    def add(self, level: int, code: str, **details):
        self.severity = max(self.severity, level)
        self.counts[code] = self.counts.get(code, 0) + 1
        self.items.append({'code': code, **details})

    def finish(self, report: dict, *, order=None):
        items = sorted(self.items, key=order) if order else self.items
        report['issue_counts'] = dict(self.counts)
        report['findings'] = items[:DETAIL_LIMIT]
        report['findings_truncated'] = len(items) > DETAIL_LIMIT
        return report


def _status(severity: int, empty: bool) -> str:
    if severity == 2:
        return 'incompatible'
    if severity == 1:
        return 'review'
    return 'no_tracks' if empty else 'consistent'


def _name(value) -> str:
    return str(value)[:128]


def _rotations(pyr) -> np.ndarray:
    """(n, 3, 3) matrices for (n, 3) pitch/yaw/roll degrees, as ``pose.rotationMatrix``."""
    pyr = np.radians(np.asarray(pyr, dtype=float).reshape(-1, 3))
    sp, cp = np.sin(pyr[:, 0]), np.cos(pyr[:, 0])
    sy, cy = np.sin(pyr[:, 1]), np.cos(pyr[:, 1])
    sr, cr = np.sin(pyr[:, 2]), np.cos(pyr[:, 2])
    return np.stack([np.stack([cr * cy - sr * sy * sp, -sy * cp, cr * sy * sp + sr * cy], -1),
                     np.stack([sr * cy * sp + cr * sy, cy * cp, sr * sy - sp * cy * cr], -1),
                     np.stack([-sr * cp, sp, cr * cp], -1)], -2)


def _angles(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Degrees between matching rotation matrices (broadcasts over the leading axis)."""
    trace = np.einsum('...ij,...ij->...', a, b)
    return np.degrees(np.arccos(np.clip((trace - 1.0) / 2.0, -1.0, 1.0)))


def rotationAngle(a, b) -> float:
    """Degrees between the rotations of two (pitch, yaw, roll) triples."""
    ra, rb = _rotations([a, b])
    return float(_angles(ra, rb))


# --- header and track table ---------------------------------------------------------------

def lintKeyHeader(key, model=None, *, target: str = 'JKDF2') -> dict:
    """Check a KEY's header and track table against what the engine loads and plays.

    Findings (``incompatible`` unless marked):

    - ``track_past_joints``: a track numbered below 0 or at or past ``JOINTS``. Retail stores
      it outside the KEY's track allocation.
    - ``track_past_puppet_nodes``: a track with entries, numbered 64 or above, in a KEY that
      declares more than 64 ``JOINTS``. ``rdPuppet_BuildJointMatrices`` keeps each playing KEY's
      current entry per track number in a 64-entry array (``aCurKfNodeEntryNums`` in
      ``rdPuppetTrack``). For such a track it reads past that array and can write past it: track
      64 lands on the playback time, and later numbers on the fields after it, including the KEY
      pointer.
    - ``duplicate_track`` (review): more than one track for a node; the last one wins.
    - ``entry_order``: an entry whose frame is not after the previous entry's. The engine's
      forward scan skips the earlier entry and plays this one from a later time. With a model,
      the binding audit reports the same tracks as ``invalid_track_timing``; this finding adds
      the entry and frames.
    - ``entry_past_frames`` (review): entries after ``FRAMES``. They never play. An entry at
      ``FRAMES`` is not a finding: a play that holds its last frame reaches it.
    - ``joints_below_model`` (review, needs a model): the model has nodes at or past
      ``JOINTS``, which this KEY cannot drive. OpenJKDF2 guards a read of the KEY's track table
      for those nodes that the original may make past its end.

    ``recommended_joints`` is 64 for both targets. Writing ``JOINTS 64`` lets the KEY address
    every node the engine supports and avoids that read.

    The header is checked against the target as ``auditModelKey`` checks it
    (``sith.key.modelBinding.keyTargetFindings``): ``non_text_key``, ``invalid_key_number``,
    ``key_target_limit`` (JOINTS, markers and name lengths; more than 8 markers fails the load),
    ``unrecognized_markers`` (review) and ``invalid_playback_header`` (FRAMES below 1 or FPS not
    above 0). Without a model they are findings of this report. With a model, ``binding`` holds
    ``auditModelKey(model, key, target)``, which reports them, and ``status`` is the worse of the
    two reports. Tracks are checked as stored. Load files with
    ``loadKey(path, strictTracks=False)`` so that out-of-range and repeated tracks reach this
    check.
    """
    if target not in _TARGETS:
        raise ValueError('Choose JKDF2 or MOTS for the KEY lint')
    joints = int(key.numJoints)
    frames = int(key.numFrames)
    report = {'key': _name(key.name), 'key_target': target, 'declared_joints': joints,
              'recommended_joints': RECOMMENDED_JOINTS,
              'model_joints': len(model.meshHierarchy) if model is not None else None,
              'frames': frames, 'tracks': len(key.nodes), 'recommendations': [], 'binding': None}
    found = _Findings()
    if model is None:
        for level, code, details in keyTargetFindings(key, target):
            found.add(level, code, **details)

    seen: Dict[int, List[int]] = {}
    for position, track in enumerate(key.nodes):
        seen.setdefault(int(track.idx), []).append(position)
        if not 0 <= track.idx < joints:
            found.add(2, 'track_past_joints', index=int(track.idx), key_name=_name(track.meshName),
                      joints=joints)
        entries = track.keyframes
        late = [i for i in range(1, len(entries)) if entries[i].frame <= entries[i - 1].frame]
        if late:
            first = late[0]
            found.add(2, 'entry_order', index=int(track.idx), key_name=_name(track.meshName),
                      entry=first, frame=float(entries[first].frame),
                      previous_frame=float(entries[first - 1].frame), entries=len(late))
        past = [entry for entry in entries if entry.frame > frames]
        if past:
            found.add(1, 'entry_past_frames', index=int(track.idx), key_name=_name(track.meshName),
                      entries=len(past), first_frame=float(past[0].frame), frames=frames)
    for index, positions in sorted(seen.items()):
        if len(positions) > 1:
            kept = key.nodes[positions[-1]]
            found.add(1, 'duplicate_track', index=index, tracks=len(positions),
                      kept_track=positions[-1], key_name=_name(kept.meshName))
        played = keyTrack(key, index)        # None past JOINTS: track_past_joints covers it
        if index >= RECOMMENDED_JOINTS and played is not None:
            found.add(2, 'track_past_puppet_nodes', index=index, key_name=_name(played.meshName),
                      entries=len(played.keyframes), joints=joints)

    if model is not None:
        nodes = len(model.meshHierarchy)
        if joints < nodes:
            found.add(1, 'joints_below_model', declared_joints=joints, model_joints=nodes,
                      undriven_nodes=sum(1 for node in model.meshHierarchy if node.idx >= joints))
        report['binding'] = auditModelKey(model, key, target)
    if joints != RECOMMENDED_JOINTS:
        report['recommendations'].append({
            'code': 'joints_64',
            'detail': f'Write JOINTS {RECOMMENDED_JOINTS} (found {joints}) so the KEY can address '
                      f'every node the engine supports'})

    found.finish(report)
    severity = found.severity
    if report['binding'] is not None:
        severity = max(severity, _STATUS_LEVEL.get(report['binding']['status'], 0))
    report['status'] = _status(severity, not key.nodes)
    return report


# --- pops at and between entries ----------------------------------------------------------

@dataclass(frozen=True)
class PopLimits:
    """Thresholds for ``scanKeyPops``. Angles are degrees, distances JK units, times KEY frames.

    ``step`` is the sample spacing. ``flipDetour`` is how far, in degrees, the path between two
    entries may stray beyond the shortest turn between them.

    A sample interval is a spike when the joint turns at least ``spikeRatio`` times faster than
    its reference: the larger of the two neighbour medians and ``spikeFloor`` degrees per second.
    The neighbours are the sample intervals in the ``window`` frames on each side; ``guard``
    frames next to the interval can be left out of them, and none are by default. With the
    defaults, nothing slower than 2880 degrees per second is a spike. A median ignores up to 3 of
    the 8 samples in a 2-frame window, so a burst of about one frame (one entry in a KEY keyed
    every frame) stands out, while a longer motion, such as a deliberate strike, sets its own
    reference. Position jumps use ``jumpRatio`` and ``jumpFloor`` the same way, in JK units per
    second. ``jumpFloor`` defaults to one model height per second, so with the defaults a node
    must move at least 8 model heights per second.

    ``entryAngle`` and ``entryDistance`` are the largest rotation and position change at an entry,
    from where the previous entry's motion ends to the entry's stored values, that still counts as
    continuous. ``formJump`` is the component change, in degrees, at which a continuous entry
    counts as a change of Euler form.
    """
    step: float = 0.25
    flipDetour: float = 45.0
    spikeRatio: float = 8.0
    spikeFloor: float = 360.0
    jumpRatio: float = 8.0
    jumpFloor: Optional[float] = None
    window: float = 2.0
    guard: float = 0.0
    formJump: float = 90.0
    entryAngle: float = 2.0
    entryDistance: float = 0.001

    def __post_init__(self):
        if not self.step > 0 or not self.window > 0 or self.guard < 0:
            raise ValueError('Pop scan step and window must be positive and guard nonnegative')
        if not self.entryAngle >= 0 or not self.entryDistance >= 0:
            raise ValueError('Pop scan entryAngle and entryDistance must be nonnegative')


def _increasing(entries) -> bool:
    return all(b.frame > a.frame for a, b in zip(entries, entries[1:]))


def _trackArrays(track):
    frames = np.array([float(e.frame) for e in track.keyframes])
    orient = np.array([tuple(e.orientation) for e in track.keyframes], dtype=float).reshape(-1, 3)
    delta = np.array([tuple(e.deltaRotation) if int(e.flags) & 2 else (0.0, 0.0, 0.0)
                      for e in track.keyframes], dtype=float).reshape(-1, 3)
    return frames, orient, delta


def _trackPositions(track):
    """Each entry's position and position delta per frame (the delta only with flag 0x1)."""
    position = np.array([tuple(e.position) for e in track.keyframes], dtype=float).reshape(-1, 3)
    velocity = np.array([tuple(e.deltaPosition) if int(e.flags) & 1 else (0.0, 0.0, 0.0)
                         for e in track.keyframes], dtype=float).reshape(-1, 3)
    return position, velocity


def _trackRotations(arrays, times: np.ndarray) -> np.ndarray:
    """PYR at ``times`` with the engine's entry rule (``pose.sampleTrack``), for ordered entries."""
    frames, orient, delta = arrays
    index = np.clip(np.searchsorted(frames, times, side='right') - 1, 0, len(frames) - 1)
    return orient[index] + delta[index] * (times - frames[index])[:, None]


def _grid(total: float, step: float) -> np.ndarray:
    count = int(math.floor(total / step + 1e-9))
    times = np.arange(count + 1) * step
    if times[-1] < total - 1e-9:
        times = np.append(times, total)
    return times


def _spikes(speed: np.ndarray, times: np.ndarray, limits: PopLimits, ratio: float, floor: float):
    """Merged runs of sample intervals at least ``ratio`` times their neighbours' median speed.

    The neighbours of an interval starting at ``t`` are the intervals starting in
    ``[t - guard - window, t - guard)`` and in ``(t + guard, t + guard + window]``. The reference
    is the larger of the two medians, and at least ``floor``. Returns (first interval, last
    interval, peak interval, reference at the peak) per run.
    """
    start = times[:-1]
    eps = 1e-9
    flagged = []
    references = np.zeros(len(speed))
    for j, t in enumerate(start):
        b0 = np.searchsorted(start, t - limits.guard - limits.window - eps, side='left')
        b1 = np.searchsorted(start, t - limits.guard - eps, side='left')
        a0 = np.searchsorted(start, t + limits.guard + eps, side='right')
        a1 = np.searchsorted(start, t + limits.guard + limits.window + eps, side='right')
        sides = [float(np.median(side)) for side in (speed[b0:b1], speed[a0:a1]) if len(side)]
        if not sides:
            continue
        references[j] = max(max(sides), floor)
        if speed[j] >= ratio * references[j]:
            flagged.append(j)
    runs = []
    for j in flagged:
        if runs and j == runs[-1][-1] + 1:
            runs[-1].append(j)
        else:
            runs.append([j])
    out = []
    for run in runs:
        peak = max(run, key=lambda j: speed[j])
        out.append((run[0], run[-1], peak, references[peak]))
    return out


def _segments(arrays, total: float):
    """(start, end, entry) intervals the engine plays from one entry, clipped to ``total``."""
    frames = arrays[0]
    bounds = ([0.0] if frames[0] > 0 else []) + [float(f) for f in frames if f < total] + [total]
    out = []
    for a, b in zip(bounds, bounds[1:]):
        if b > a:
            entry = max(int(np.searchsorted(frames, a, side='right')) - 1, 0)
            out.append((a, b, entry))
    return out


def _segmentPath(arrays, a: float, b: float, entry: int, step: float):
    """Start and end PYR, component change, rotation change and detour of one segment.

    The engine plays ``[a, b)`` from one entry, so the end is that entry's left limit at ``b``.
    The detour is the largest ``angle(start, x) + angle(x, end) - angle(start, end)`` over the
    sampled path: zero on a shortest turn, about 360 degrees for a full extra turn.
    """
    frames, orient, delta = arrays
    samples = max(8, int(math.ceil((b - a) / step)))
    times = np.linspace(a, b, samples + 1)
    pyr = orient[entry] + delta[entry] * (times - frames[entry])[:, None]
    rot = _rotations(pyr)
    fromStart = _angles(rot[:1], rot)
    toEnd = _angles(rot[-1:], rot)
    change = float(fromStart[-1])
    detour = float(np.max(fromStart + toEnd - change))
    return pyr[0], pyr[-1], float(np.max(np.abs(pyr[-1] - pyr[0]))), change, max(detour, 0.0)


def _nodeIndices(model, nodes) -> List[int]:
    byName = {node.name.casefold(): i for i, node in enumerate(model.meshHierarchy)}
    out = []
    for value in nodes:
        if isinstance(value, str):
            if value.casefold() not in byName:
                raise ValueError(f"Model has no node named '{value}'")
            out.append(byName[value.casefold()])
        else:
            if not 0 <= int(value) < len(model.meshHierarchy):
                raise ValueError(f'Model has no node {value}')
            out.append(int(value))
    return sorted(set(out))


def _affectedNodes(model, driven: Iterable[int]) -> List[int]:
    """Model nodes whose world frame depends on a driven node: the driven and their descendants."""
    driven = set(driven)
    nodes = model.meshHierarchy
    memo: Dict[int, bool] = {}

    def affected(i, depth=0):
        if i in memo:
            return memo[i]
        if depth > len(nodes):
            raise ValueError('3DO hierarchy has a parent cycle')
        parent = nodes[i].parentIdx
        memo[i] = nodes[i].idx in driven or (parent is not None and 0 <= parent < len(nodes)
                                            and affected(parent, depth + 1))
        return memo[i]

    return [i for i in range(len(nodes)) if affected(i)]


def _modelHeight(model) -> float:
    rest = restPose(model)
    points = [v for v in rest.vertices.values() if len(v)]
    points = np.vstack(points) if points else rest.meshFrames[:, :3, 3]
    height = float(np.ptp(points[:, 2])) if len(points) else 0.0
    return height if height > 1e-6 else 0.1


def scanKeyPops(key, model=None, *, limits: PopLimits = PopLimits(), nodes=None,
                baseLayers: Sequence[PoseLayer] = (), lowPri: int = 1,
                highPri: Optional[int] = None) -> dict:
    """Find pops at and between KEY entries: motion the engine plays that no entry describes.

    From each entry the engine moves a track with that entry's own deltas until the next entry,
    then takes the next entry's stored values (``rdPuppet_BuildJointMatrices``). Per track (the
    track the engine uses for each node below ``JOINTS``), over ``0 .. FRAMES``:

    - ``euler_flip``: between two entries, the path strays at least ``flipDetour`` degrees
      beyond the shortest turn, and the end rotation has a nearer Euler triple: the other
      form (``form: twin``) or fewer whole turns (``form: turn``). ``nearest`` is that triple.
    - ``wide_step``: the same detour with the end already in its nearest form. The step is too
      wide for a straight line in Euler components and needs more entries.
    - ``entry_jump``: at an entry below ``FRAMES``, the joint snaps from where the previous
      entry's motion ends to the entry's stored values. Its rotation turns more than
      ``entryAngle`` degrees (``angle``) or its position moves more than ``entryDistance`` JK
      units (``distance``). ``form`` is ``twin`` or ``turn`` when the stored triple is also not
      the triple of its rotation nearest where the motion ended (the other Euler form, or extra
      whole turns), else None.
    - ``form_switch``: at an entry below ``FRAMES`` that does not jump, a component changes by
      at least ``formJump`` degrees beyond whole turns. A layer played at full weight does not
      show this. A partial-weight blend scales each component, so a fade or a second-priority
      fill does show it.
    - ``velocity_spike``: the joint turns ``spikeRatio`` times faster than its neighbours (see
      ``PopLimits``). Spikes inside a flagged flip or wide step, or across an ``entry_jump``
      whose rotation jumps, are not reported again.

    With a model, the KEY is posed with ``sith.audit.pose`` on top of ``baseLayers`` (held at
    their own frames) at priorities ``lowPri``/``highPri``. Each node's mesh-frame origin is
    sampled every ``step`` frames:

    - ``position_jump``: in one sample interval, a node moves ``jumpRatio`` times faster than
      its neighbours. A run of such intervals is one finding. It names the node that moved
      furthest, and ``nodes`` counts the nodes involved.

    ``nodes`` limits the position scan to these model node indices or names. By default it
    covers every node whose world frame a KEY track moves. Speeds are per second at the KEY's
    FPS. All findings are ``review``: the KEY plays, but its motion there is not the smooth
    motion its entries describe. Tracks whose entries are not in increasing frame order are
    listed in ``tracks_skipped``; ``lintKeyHeader`` reports them.
    """
    total = float(key.numFrames)
    fps = float(key.fps)
    if not total >= 1 or not fps > 0:
        raise ValueError('KEY frame count and FPS must be positive to scan for pops')
    times = _grid(total, limits.step)
    report = {'key': _name(key.name), 'frames': int(key.numFrames), 'fps': fps,
              'limits': asdict(limits), 'tracks_scanned': 0, 'tracks_skipped': [],
              'nodes_scanned': 0}
    found = _Findings()

    tracks = {}
    for track in key.nodes:
        chosen = keyTrack(key, int(track.idx))
        if chosen is not None:
            tracks[int(track.idx)] = chosen
    for index, track in sorted(tracks.items()):
        if not _increasing(track.keyframes):
            report['tracks_skipped'].append({'index': index, 'key_name': _name(track.meshName),
                                             'reason': 'entry_order'})
            continue
        report['tracks_scanned'] += 1
        arrays = _trackArrays(track)
        name = _name(track.meshName)

        # (a) Each played interval: does it take a turn its endpoints do not describe?
        explained = []
        for a, b, entry in _segments(arrays, total):
            start, end, component, change, detour = _segmentPath(arrays, a, b, entry, limits.step)
            if detour < limits.flipDetour:
                continue
            explained.append((a, b))
            nearest = np.array(nearestEuler(start, end))
            details = dict(index=index, key_name=name, entry=entry, frame=round(a, 4),
                           end_frame=round(b, 4), component_change=round(component, 3),
                           rotation_change=round(change, 3), detour=round(detour, 3))
            # nearestEuler minimizes the summed component change; compare the same measure.
            if float(np.sum(np.abs(nearest - start))) < float(np.sum(np.abs(end - start))) - 1e-6:
                twin = np.array(unwrapEuler(start, eulerTwin(end)))
                form = 'twin' if np.allclose(nearest, twin) else 'turn'
                found.add(1, 'euler_flip', form=form,
                          nearest=[round(float(v), 4) for v in nearest], **details)
            else:
                found.add(1, 'wide_step', **details)

        # At each later entry: does the joint jump from where the previous entry's motion ends,
        # or keep its rotation in another Euler form?
        frames, orient, delta = arrays
        position, velocity = _trackPositions(track)
        turned = []
        for i in range(1, len(frames)):
            if frames[i] >= total:
                break
            span = frames[i] - frames[i - 1]
            left = orient[i - 1] + delta[i - 1] * span
            angle = rotationAngle(left, orient[i])
            distance = float(np.linalg.norm(position[i - 1] + velocity[i - 1] * span - position[i]))
            if angle > limits.entryAngle or distance > limits.entryDistance:
                nearest = np.array(nearestEuler(left, orient[i]))
                form = None
                # As for euler_flip: is the stored triple further from the motion than it must be?
                if np.sum(np.abs(nearest - left)) < np.sum(np.abs(orient[i] - left)) - 1e-6:
                    twin = np.array(unwrapEuler(left, eulerTwin(orient[i])))
                    form = 'twin' if np.allclose(nearest, twin) else 'turn'
                if angle > limits.entryAngle:
                    turned.append(float(frames[i]))
                found.add(1, 'entry_jump', index=index, key_name=name, entry=i,
                          frame=round(float(frames[i]), 4), angle=round(angle, 3),
                          distance=round(distance, 6), form=form)
                continue
            shift = np.abs(np.array(unwrapEuler(left, orient[i])) - left)
            if float(shift.max()) >= limits.formJump:
                found.add(1, 'form_switch', index=index, key_name=name, entry=i,
                          frame=round(float(frames[i]), 4),
                          component_change=round(float(shift.max()), 3))

        # (b) Angular speed per sample interval, against its neighbours.
        rot = _rotations(_trackRotations(arrays, times))
        speed = _angles(rot[:-1], rot[1:]) / np.diff(times) * fps
        for first, last, peak, reference in _spikes(speed, times, limits, limits.spikeRatio,
                                                    limits.spikeFloor):
            a, b = float(times[first]), float(times[last + 1])
            if any(a < end and b > begin for begin, end in explained):
                continue
            if any(a < frame <= b for frame in turned):     # the entry_jump already covers it
                continue
            found.add(1, 'velocity_spike', index=index, key_name=name, frame=round(a, 4),
                      end_frame=round(b, 4), speed=round(float(speed[peak]), 3),
                      reference=round(float(reference), 3),
                      ratio=round(float(speed[peak] / reference), 3))

    # (c) World positions of the nodes the KEY moves.
    if model is not None and tracks:
        scanned = (_nodeIndices(model, nodes) if nodes is not None
                   else _affectedNodes(model, tracks))
        report['nodes_scanned'] = len(scanned)
        if scanned:
            floor = limits.jumpFloor if limits.jumpFloor is not None else _modelHeight(model)
            report['jump_floor'] = round(float(floor), 6)
            base = list(baseLayers)
            positions = np.array([
                poseModel(model, base + [PoseLayer(key, float(t), 1.0, lowPri, highPri)],
                          vertices=False).meshFrames[scanned, :3, 3]
                for t in times])
            distance = np.linalg.norm(np.diff(positions, axis=0), axis=2)
            moving = distance / np.diff(times)[:, None] * fps
            # Runs per node, then one event per overlapping or adjacent group of runs.
            runs = sorted((first, last, column, peak, reference)
                          for column in range(len(scanned))
                          for first, last, peak, reference in _spikes(
                              moving[:, column], times, limits, limits.jumpRatio, floor))
            events: List[List[tuple]] = []
            for run in runs:
                if events and run[0] <= max(r[1] for r in events[-1]) + 1:
                    events[-1].append(run)
                else:
                    events.append([run])
            for event in events:
                first = min(r[0] for r in event)
                last = max(r[1] for r in event)
                _, _, column, peak, reference = max(event, key=lambda r: distance[r[3], r[2]])
                node = scanned[column]
                found.add(1, 'position_jump', index=node,
                          name=_name(model.meshHierarchy[node].name),
                          frame=round(float(times[first]), 4),
                          end_frame=round(float(times[last + 1]), 4),
                          distance=round(float(distance[peak, column]), 6),
                          speed=round(float(moving[peak, column]), 6),
                          reference=round(float(reference), 6),
                          ratio=round(float(moving[peak, column] / reference), 3),
                          nodes=len({r[2] for r in event}))

    found.finish(report, order=lambda f: (f.get('frame', 0.0), f.get('index', 0), f['code']))
    report['status'] = _status(found.severity, not tracks)
    return report


# --- priorities ----------------------------------------------------------------------------

def _typeNames(mask: int) -> List[str]:
    return [flag.name for flag in Mesh3doNodeType if flag.value and mask & flag.value == flag.value]


def keyPriorityReport(key, model, *, priority: Optional[int] = None, lowPri: Optional[int] = None,
                      highPri: Optional[int] = None) -> dict:
    """Which model nodes a KEY drives at its high and at its low priority.

    Give the COG ``PlayKey`` priority ``p`` (low ``p``, high ``p + 2``, as ``PoseLayer.playKey``)
    or explicit ``lowPri`` and ``highPri`` (``highPri`` defaults to ``lowPri``). A node gets the
    high priority when the KEY's node-type mask shares a bit with the node's type
    (``rdPuppet_BuildJointMatrices``). ``lower_layers`` lists the nodes with no track here: other
    KEYs or the rest pose decide them. ``unused_tracks`` lists tracks the model cannot use:
    outside ``JOINTS``, past the model's nodes, or without entries. The report only describes;
    which layer wins a node also depends on the other KEYs playing.
    """
    if (priority is None) == (lowPri is None):
        raise ValueError('Give either a PlayKey priority or an explicit low priority')
    if priority is not None:
        if highPri is not None:
            raise ValueError('A PlayKey priority sets the high priority itself')
        low, high = int(priority), int(priority) + 2
    else:
        low = int(lowPri)
        high = low if highPri is None else int(highPri)
    mask = nodeTypeValue(getattr(key, 'nodeTypes', 0))
    report = {'key': _name(key.name), 'node_types': f'0x{mask:04X}',
              'node_type_names': _typeNames(mask), 'low_priority': low, 'high_priority': high,
              'high': [], 'low': [], 'lower_layers': [], 'unused_tracks': []}
    modelNodes = {node.idx for node in model.meshHierarchy}
    for node in model.meshHierarchy:
        kind = nodeTypeValue(node.type)
        entry = {'index': int(node.idx), 'name': _name(node.name), 'type': f'0x{kind:04X}'}
        if keyTrack(key, int(node.idx)) is None:
            report['lower_layers'].append(entry)
        elif mask & kind:
            report['high'].append(entry)
        else:
            report['low'].append(entry)
    for track in key.nodes:
        index = int(track.idx)
        reason = None
        if not 0 <= index < int(key.numJoints):
            reason = 'past_joints'
        elif index not in modelNodes:
            reason = 'no_model_node'
        elif not track.keyframes:
            reason = 'empty'
        if reason:
            report['unused_tracks'].append({'index': index, 'key_name': _name(track.meshName),
                                            'reason': reason})
    report['counts'] = {name: len(report[name]) for name in ('high', 'low', 'lower_layers')}
    return report


# --- track slots across characters ---------------------------------------------------------

@dataclass
class Character:
    """One character for ``trackRegistry``: a name, its model and the KEYs it ships.

    ``keys`` are KEY resources. Their ``name`` is the file name the engine looks up; it is
    compared without case.
    """
    name: str
    model: object
    keys: Sequence[object]


def _sameTrack(a, b, tolerance=1e-6) -> bool:
    if len(a.keyframes) != len(b.keyframes):
        return False
    for x, y in zip(a.keyframes, b.keyframes):
        if int(x.flags) != int(y.flags) or abs(float(x.frame) - float(y.frame)) > tolerance:
            return False
        for u, v in ((x.position, y.position), (x.orientation, y.orientation),
                     (x.deltaPosition, y.deltaPosition), (x.deltaRotation, y.deltaRotation)):
            if any(abs(float(p) - float(q)) > tolerance for p, q in zip(u, v)):
                return False
    return True


def _visibleNodes(model) -> set:
    """Node indices that carry a mesh or have a descendant that does."""
    nodes = model.meshHierarchy
    visible = set()
    for i, node in enumerate(nodes):
        if node.meshIdx is not None and node.meshIdx >= 0:
            j, steps = i, 0
            while j is not None and 0 <= j < len(nodes) and j not in visible and steps <= len(nodes):
                visible.add(j)
                j, steps = nodes[j].parentIdx, steps + 1
    return {nodes[i].idx for i in visible}


def trackRegistry(characters, *, custom=18) -> dict:
    """List each character's custom track slots and the slots that clash in a shared pack.

    A pack holds one file per KEY name, and every model that plays that name gets its tracks.
    ``custom`` says which slots count:

    - an int ``n`` (default 18): slots ``n`` and up, past Kyle's 18-joint skeleton;
    - ``'names'``: slots the two models name differently, or that only one model has;
    - a collection of slot numbers.

    For each pair of characters, each KEY name both ship and each custom slot, the tracks the
    engine would use are compared (``pose.keyTrack``):

    - ``slot_collision`` (incompatible): both drive the slot, with different tracks. One of the
      two must move to another slot before they share a pack.
    - ``slot_intrusion`` (incompatible): one drives the slot; the other's model has a node there
      that carries a mesh, or has a descendant that does, and its own KEY leaves it alone. In a
      shared pack, the first character's track would move it.

    Equal tracks are not findings. Nodes without a mesh anywhere below them are not findings
    either. ``characters`` lists each character's custom slots with the node name in its own
    model and the number of its KEYs that drive the slot.
    """
    chars = [c if isinstance(c, Character) else Character(*c) for c in characters]
    if isinstance(custom, str):
        if custom != 'names':
            raise ValueError("custom must be a first slot, 'names' or a collection of slots")
        rule = {'rule': 'names'}
    elif isinstance(custom, int):
        rule = {'rule': 'index', 'from': int(custom)}
    else:
        rule = {'rule': 'slots', 'slots': sorted({int(s) for s in custom})}

    tables = []
    for char in chars:
        byName = {}
        for key in char.keys:
            name = str(key.name).casefold()
            if name in byName:
                raise ValueError(f"Character '{char.name}' has two KEYs named '{key.name}'")
            byName[name] = key
        names = {node.idx: node.name for node in char.model.meshHierarchy}
        tables.append((char, byName, names, _visibleNodes(char.model)))

    def isCustom(slot, a, b=None):
        if rule['rule'] == 'index':
            return slot >= rule['from']
        if rule['rule'] == 'slots':
            return slot in rule['slots']
        others = [b] if b is not None else [t for t in tables if t is not a]
        own = a[2].get(slot)
        return any(own is None or other[2].get(slot) is None
                   or own.casefold() != other[2][slot].casefold() for other in others)

    report = {'custom_rule': rule, 'characters': []}
    for table in tables:
        char, byName, names, _ = table
        used: Dict[int, int] = {}
        for key in byName.values():
            for track in key.nodes:
                slot = int(track.idx)
                if keyTrack(key, slot) is track and isCustom(slot, table):
                    used[slot] = used.get(slot, 0) + 1
        report['characters'].append({
            'name': char.name, 'model_joints': len(char.model.meshHierarchy), 'keys': len(byName),
            'slots': [{'slot': s, 'node': names.get(s), 'keys': n} for s, n in sorted(used.items())]})

    found = _Findings()
    for i in range(len(tables)):
        for j in range(i + 1, len(tables)):
            a, b = tables[i], tables[j]
            shared = sorted(set(a[1]) & set(b[1]))
            clashes: Dict[Tuple[str, int, int], List[str]] = {}
            for name in shared:
                ka, kb = a[1][name], b[1][name]
                slots = {int(t.idx) for t in ka.nodes} | {int(t.idx) for t in kb.nodes}
                for slot in sorted(slots):
                    if not isCustom(slot, a, b):
                        continue
                    ta, tb = keyTrack(ka, slot), keyTrack(kb, slot)
                    if ta is None and tb is None:
                        continue
                    if ta is not None and tb is not None:
                        if not _sameTrack(ta, tb):
                            clashes.setdefault(('slot_collision', slot, -1), []).append(name)
                        continue
                    driver, other = (0, b) if ta is not None else (1, a)
                    if slot in other[3]:
                        clashes.setdefault(('slot_intrusion', slot, driver), []).append(name)
            for (code, slot, driver), keyNames in sorted(clashes.items(),
                                                         key=lambda item: (item[0][1], item[0][0])):
                details = dict(slot=slot, characters=[a[0].name, b[0].name],
                               nodes=[a[2].get(slot), b[2].get(slot)], keys=len(keyNames),
                               key_names=keyNames[:8])
                if code == 'slot_intrusion':
                    details['driven_by'] = (a, b)[driver][0].name
                found.add(2, code, **details)
    found.finish(report)
    report['status'] = _status(found.severity, False)
    return report
