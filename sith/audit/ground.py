# SPDX-License-Identifier: GPL-3.0-or-later
"""Floor contact, contact slip and the third-person camera's reading of a posed model.

Where the floor is. The engine does not stand a character on its mesh. Physics holds an attached
walking thing at a fixed height above the floor plane it is attached to
(``sithPhysics_UpdateAttachedThingPhysics``): the template's ``height``, or when that is 0 the
model's ``insertOffset.z``, but at least ``movesize + 0.005`` (``sithPhysics_GetThingHeight``). A
crouching thing is held at ``movesize + 0.01``. The model is drawn from the thing's own position:
``sithRender`` hands the thing's orientation and position to ``rdThing_Draw``, and
``rdThing_AccumulateMatrices`` starts from that placement. Whether the feet meet the floor is
therefore decided by the 3DO and the KEYs alone. This module measures it.

Coordinates are those of ``sith.audit.pose``: JK units, +y forward, +z up, with the root's parent
frame at ``T(insertOffset)``. That is the thing's frame moved by the insert offset, so with the
stock player template the standing floor is the plane z = 0. The floor is always a horizontal
plane ``z = floor`` in this frame. ``thingFloorHeight`` gives the engine's floor; the audits
default to the lowest point of the rest pose and also take a reference pose or a height.

Carried meshes. COG ``jkSetWeaponMesh`` gives a player a one-mesh model that is drawn with the
matrix of the puppet's primary-weapon joint (``jkCog_SetWeaponMesh``,
``jkPlayer_renderSaberWeaponMesh``). That joint matrix includes the joint's pivot, so it is
``Pose.meshFrames[node]``. A ``CarriedMesh`` stands in for such a mesh over given time ranges, so a
cog's mesh swaps can be audited without running the cog.

Limits shared by every check here: poses are evaluated at sample times only. Puppet timing,
fades, collision, slopes, steps and the player's aim are not run; callers pass aim as
``preRotations`` on a ``PoseSample``. Underwater (swim) KEYs have no floor.
"""

from dataclasses import dataclass
import math
import numbers
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

from .pose import PoseLayer, keyFrameTimes, poseModel, restPose, rotationMatrix

__all__ = [
    'MOVE_FRAMES_PER_UNIT', 'CAMERA_OFFSET', 'CAMERA_TARGET', 'CarriedMesh', 'PoseSample',
    'PoseSequence', 'keySequence', 'thingFloorHeight', 'referenceFloor', 'lowestPoints',
    'auditFloorContact', 'thirdPersonViewDirection', 'viewExtent', 'auditCameraReading',
]

DETAIL_LIMIT = 32

# sithPuppet_UpdatePuppet advances a movement-controlled track (puppet flag 0x1) by
# |speed * dt| * 280 KEY frames, through rdPuppet_AdvanceTrack.
MOVE_FRAMES_PER_UNIT = 280.0

# sithCamera_Startup: camera 1 (type 4, the external camera) sits at this offset from the eye.
# sithCamera_Update looks from there at sithCamera_trans, 0.3 ahead of the eye.
CAMERA_OFFSET = (0.0, -0.2, 0.06)
CAMERA_TARGET = (0.0, 0.3, 0.0)

NodeRef = Union[int, str]
_ALWAYS = ((-math.inf, math.inf),)


@dataclass(eq=False)
class CarriedMesh:
    """A mesh drawn on a node of the model during given time ranges, as a weapon cog shows it.

    ``points`` are (V, 3) vertices in the carrying node's mesh frame (``Pose.meshFrames``).
    ``ranges`` are half-open ``[start, end)`` intervals of sample time (KEY frames for a KEY played
    alone) during which the mesh is shown; the default shows it at every sample. ``hides`` lists
    nodes of the model whose own meshes are not drawn while this mesh is shown. ``node`` and the
    entries of ``hides`` are node indices or unique node names. ``name`` identifies the mesh in
    reports and must be unique within one audit.
    """
    name: str
    node: NodeRef
    points: np.ndarray
    ranges: Sequence[Tuple[float, float]] = _ALWAYS
    hides: Sequence[NodeRef] = ()

    def __post_init__(self):
        self.name = str(self.name)
        self.points = np.asarray(self.points, dtype=float).reshape(-1, 3)
        ranges = []
        for start, end in self.ranges:
            start, end = float(start), float(end)
            if not start < end:
                raise ValueError(f'carried mesh {self.name!r}: each range must start before it ends')
            ranges.append((start, end))
        self.ranges = tuple(ranges)
        self.hides = tuple(self.hides)

    def shownAt(self, time: float) -> bool:
        """True when one of the ranges holds ``time``."""
        return any(start <= time < end for start, end in self.ranges)

    @classmethod
    def fromModel(cls, model, node: NodeRef, ranges: Sequence[Tuple[float, float]] = _ALWAYS, *,
                  name: Optional[str] = None, hides: Sequence[NodeRef] = (), geoset: int = 0) -> 'CarriedMesh':
        """The vertices of ``model`` at rest, placed the way the engine draws a weapon model.

        The weapon model's own hierarchy applies (positions, rotations, pivots); its insert offset
        does not, because ``rdThing_AccumulateMatrices`` starts from the joint matrix itself. COG
        ``jkSetWeaponMesh`` accepts only a model with one geoset holding one mesh; this helper takes
        every mesh of ``geoset`` and leaves that rule to the caller.
        """
        pose = restPose(model, vertices=False)
        offset = np.array(tuple(getattr(model, 'insertOffset', (0.0, 0.0, 0.0))), dtype=float)
        parts = []
        for i, local in _Geometry(model, geoset).local.items():
            frame = pose.meshFrames[i]
            parts.append(local @ frame[:3, :3].T + (frame[:3, 3] - offset))
        points = np.vstack(parts) if parts else np.zeros((0, 3))
        label = name if name is not None else (getattr(model, 'name', '') or 'carried')
        return cls(label, node, points, ranges, hides)


@dataclass(frozen=True)
class PoseSample:
    """One pose to audit: a time and the KEY layers that pose the model at that time.

    ``time`` orders the samples and selects carried meshes; it need not equal any layer's frame.
    ``preRotations`` are passed to ``poseModel``.
    """
    time: float
    layers: Tuple[PoseLayer, ...]
    preRotations: Optional[Mapping[int, Sequence[float]]] = None


@dataclass
class PoseSequence:
    """Named samples in increasing time, audited together like one KEY.

    ``floor`` replaces the audit's floor for this sequence (a crouch KEY plays on the crouch
    floor). ``travel`` is how far the thing moves per unit of sample time while the sequence plays,
    as (x, y) in the model's frame; it is None for a KEY played in place. See ``keySequence``.
    """
    name: str
    samples: Sequence[PoseSample]
    floor: Optional[float] = None
    travel: Optional[Tuple[float, float]] = None

    def __post_init__(self):
        self.name = str(self.name)
        self.samples = tuple(self.samples)
        times = [float(sample.time) for sample in self.samples]
        if any(later <= earlier for earlier, later in zip(times, times[1:])):
            raise ValueError(f'sequence {self.name!r}: sample times must increase')
        if self.floor is not None:
            self.floor = float(self.floor)
        if self.travel is not None:
            travel = tuple(float(v) for v in self.travel)
            if len(travel) != 2:
                raise ValueError(f'sequence {self.name!r}: travel is an (x, y) pair')
            self.travel = travel


def keySequence(key, step: float = 1.0, *, direction: Optional[Sequence[float]] = None,
                floor: Optional[float] = None, name: Optional[str] = None) -> PoseSequence:
    """A KEY played alone, sampled at ``keyFrameTimes(key, step)``.

    ``direction`` marks a movement-controlled KEY and the way the thing moves while it plays, as
    (x, y) in the model's frame: (0, 1) for a forward walk or run, (0, -1) for walking back,
    (-1, 0) and (1, 0) for strafing left and right. Puppet entries with flag 0x1 (stock ``ky.pup``:
    walk, run, walkback, the strafes, crouch forward and back, the turns) do not advance with
    time; ``sithPuppet_UpdatePuppet`` advances them by 280 KEY frames per unit moved along the
    thing's main horizontal axis. The sequence's travel is therefore ``direction / 280`` per frame.
    A turning KEY advances with the turn rate instead, which this travel model does not cover.
    """
    travel = None
    if direction is not None:
        vector = tuple(float(v) for v in direction)
        if len(vector) != 2:
            raise ValueError('direction is an (x, y) pair')
        length = math.hypot(*vector)
        if length == 0.0:
            raise ValueError('direction must not be zero')
        travel = (vector[0] / length / MOVE_FRAMES_PER_UNIT, vector[1] / length / MOVE_FRAMES_PER_UNIT)
    samples = [PoseSample(t, (PoseLayer(key, t),)) for t in keyFrameTimes(key, step)]
    return PoseSequence(getattr(key, 'name', '') if name is None else name, samples, floor, travel)


def thingFloorHeight(model, moveSize: float, *, height: float = 0.0, crouching: bool = False) -> float:
    """The floor's z in the posed frame for a thing at rest on flat ground, where physics holds it.

    ``sithPhysics_UpdateAttachedThingPhysics`` holds an attached thing's position at a height
    above the floor. Standing, it is ``height``; with ``height`` 0 (the template sets none) it is
    ``max(insertOffset.z, moveSize + 0.005)`` (``sithPhysics_GetThingHeight``). Crouching, it is
    ``moveSize + 0.01``. The posed frame starts at the insert offset, so the floor
    lies at ``insertOffset.z`` minus that height. JK's stock ``walkplayer`` (movesize 0.065, no
    height) on a model with Kyle's insert offset stands on z = 0 and crouches on z = 0.045114.
    Slopes, steps and the gradual pull towards that height are not modelled.
    """
    insertZ = float(tuple(getattr(model, 'insertOffset', (0.0, 0.0, 0.0)))[2])
    if crouching:
        held = float(moveSize) + 0.01
    elif height:
        held = float(height)
    else:
        held = max(insertZ, float(moveSize) + 0.005)
    return insertZ - held


def referenceFloor(model, layers: Sequence[PoseLayer] = (), *, nodes: Optional[Iterable[NodeRef]] = None,
                   preRotations: Optional[Mapping[int, Sequence[float]]] = None, geoset: int = 0) -> float:
    """The lowest z of the posed model, or of ``nodes``' meshes, in a reference pose.

    With no ``layers`` this is the rest pose (the 3DO as authored); pass a standing KEY's first
    frame, for example ``[PoseLayer(standKey, 0.0)]``, to measure the floor the KEYs were made on.
    This is a convention, not the engine's floor: see ``thingFloorHeight``.
    """
    setup = _Setup(model, geoset, nodes, (), ())
    _, points, _ = setup.posed(PoseSample(0.0, tuple(layers), preRotations))
    lows = [float(p[:, 2].min()) for p in points.values() if len(p)]
    if not lows:
        raise ValueError('the reference pose draws no vertices')
    return min(lows)


def lowestPoints(model, sequence, *, nodes: Optional[Iterable[NodeRef]] = None,
                 carried: Sequence[CarriedMesh] = (), step: float = 1.0, geoset: int = 0) -> List[dict]:
    """Per sample, the lowest posed vertex of ``nodes``' meshes and of the carried meshes shown.

    ``sequence`` is a ``PoseSequence`` or a KEY played alone at ``step``. ``nodes`` defaults to every
    node with a mesh; an empty list measures carried meshes only. Each entry has ``time``, ``z``
    (None when nothing is drawn), ``node`` and ``node_name`` (the drawing or carrying node),
    ``mesh`` (None for a carried mesh), ``carried`` (its name, else None) and ``vertex``. Use it to
    compare a model with a reference model sample by sample.
    """
    setup = _Setup(model, geoset, nodes, (), carried)
    out = []
    for sample in _asSequence(sequence, step).samples:
        _, points, carriedPoints = setup.posed(sample)
        low = setup.lowest(points, carriedPoints, setup.nodes)
        entry = {'time': float(sample.time), 'z': None, 'node': None, 'node_name': None, 'mesh': None,
                 'carried': None, 'vertex': None}
        if low is not None:
            entry.update(setup.identify(low))
            entry['z'] = low[0]
        out.append(entry)
    return out


def auditFloorContact(model, sequences: Iterable, *, floor=None, nodes: Optional[Iterable[NodeRef]] = None,
                      contacts: Optional[Iterable[NodeRef]] = None, carried: Sequence[CarriedMesh] = (),
                      step: float = 1.0, tolerance: float = 0.002, floatTolerance: float = 0.002,
                      contactBand: float = 0.0015, stanceBand: float = 0.0015, slipTolerance: float = 0.003,
                      geoset: int = 0) -> dict:
    """Floor contact and contact slip of a model over KEYs or layered pose sequences.

    ``sequences`` holds KEYs (each played alone at ``step``) and ``PoseSequence`` objects.
    ``floor`` is None for the lowest point of the whole model at rest, a number for a height in the
    posed frame, or a list of ``PoseLayer`` for the lowest point of a reference pose. A sequence's
    own ``floor`` replaces it.

    At each sample the lowest point is taken over ``nodes``' meshes (default: all) and the carried
    meshes shown then, less the nodes they hide. A sample is ``below`` when that point is more than
    ``tolerance`` under the floor. The contact point is the lowest point of ``contacts``' meshes
    (default: ``nodes``' meshes, without carried meshes); a sample is ``floating`` when it is more
    than ``floatTolerance`` over the floor. Jumps, falls and deaths float or sink by design: read
    the findings against the KEY, not as faults by themselves.

    Contact slip is measured only for ``contacts`` named explicitly. A contact node is planted at
    a sample when its mesh's lowest vertex is no more than ``contactBand`` over the floor and its
    joint (the origin of its joint frame, the ankle for a foot) is within ``stanceBand`` of the
    lowest height that joint reaches in the sequence. The joint test tells a flat foot from a
    swinging foot whose toe drags along the floor. Between two samples where the node is planted,
    the vertices within ``contactBand`` of the floor at both samples are followed; their mean
    horizontal displacement, less the ground's own motion under a travelling sequence
    (``-travel * dt``), is the slide. An interval where no vertex stays in the band is counted as
    rolling, not slide. A slide faster than ``slipTolerance`` JK units per unit of sample time is a
    finding. For a travelling sequence ``sweep`` is the planted vertices' mean backward speed over
    the ground's speed: 1 means no slip, below 1 the foot is carried along, above 1 it is pushed
    back. A foot that floats above ``contactBand`` is never planted, so fix floating first.

    Findings are one per sequence and code (``contact_slip``: per contact node), with the sample
    count and the worst sample; ``issue_counts`` counts them and ``sequences`` holds each one's
    minimum and maximum. Lengths are JK units.
    """
    setup = _Setup(model, geoset, nodes, contacts, carried)
    explicitContacts = contacts is not None
    floorValue, floorSource = _resolveFloor(model, floor, geoset)
    report = {'floor': floorValue, 'floor_source': floorSource, 'nodes': list(setup.nodes),
              'contacts': list(setup.contacts), 'slip_nodes': list(setup.contacts) if explicitContacts else [],
              'carried': [c.name for c in setup.carried], 'step': step, 'tolerance': tolerance,
              'float_tolerance': floatTolerance, 'contact_band': contactBand, 'stance_band': stanceBand,
              'slip_tolerance': slipTolerance, 'samples': 0, 'sequences': [], 'issue_counts': {}, 'findings': []}
    finding = _findings(report)

    for sequence in (_asSequence(item, step) for item in sequences):
        level = floorValue if sequence.floor is None else sequence.floor
        summary = {'name': sequence.name, 'samples': len(sequence.samples), 'floor': level, 'travel': sequence.travel,
                   'lowest_min': None, 'lowest_min_time': None, 'lowest_max': None, 'lowest_max_time': None,
                   'contact_min': None, 'contact_max': None, 'contact_max_time': None,
                   'below_samples': 0, 'floating_samples': 0, 'slip': []}
        deepest = highest = None
        slips = {c: _SlipState() for c in setup.contacts} if explicitContacts else {}
        records = []
        for sample in sequence.samples:
            time = float(sample.time)
            frames, points, carriedPoints = setup.posed(sample)
            low = setup.lowest(points, carriedPoints, setup.nodes)
            if low is not None:
                z = low[0] - level
                if summary['lowest_min'] is None or z < summary['lowest_min']:
                    summary['lowest_min'], summary['lowest_min_time'] = z, time
                if summary['lowest_max'] is None or z > summary['lowest_max']:
                    summary['lowest_max'], summary['lowest_max_time'] = z, time
                if z < -tolerance:
                    summary['below_samples'] += 1
                    if deepest is None or z < deepest[0]:
                        deepest = (z, time, low)
            contact = setup.lowest(points, {}, setup.contacts)
            if contact is not None:
                z = contact[0] - level
                if summary['contact_min'] is None or z < summary['contact_min']:
                    summary['contact_min'] = z
                if summary['contact_max'] is None or z > summary['contact_max']:
                    summary['contact_max'], summary['contact_max_time'] = z, time
                if z > floatTolerance:
                    summary['floating_samples'] += 1
                    if highest is None or z > highest[0]:
                        highest = (z, time, contact)
            if slips:
                records.append((time, {c: points[c] for c in slips if c in points},
                                {c: float(frames[c][2, 3]) for c in slips}))
        if records:
            jointLow = {c: min(record[2][c] for record in records) for c in slips}
            for (start, before, joints0), (end, after, joints1) in zip(records, records[1:]):
                for c, state in slips.items():
                    if max(joints0[c], joints1[c]) - jointLow[c] > stanceBand:
                        continue
                    state.step(before.get(c), after.get(c), start, end, level, contactBand, sequence.travel,
                               slipTolerance)
        report['samples'] += len(sequence.samples)

        if deepest is not None:
            finding('below_floor', sequence=sequence.name, samples=summary['below_samples'], time=deepest[1],
                    depth=-deepest[0], **setup.identify(deepest[2]))
        if highest is not None:
            finding('floating', sequence=sequence.name, samples=summary['floating_samples'], time=highest[1],
                    height=highest[0], **setup.identify(highest[2]))
        for c, state in slips.items():
            entry = state.summary(c, setup.nodeName(c), sequence.travel is not None)
            summary['slip'].append(entry)
            if state.over:
                finding('contact_slip', sequence=sequence.name, node=c, node_name=setup.nodeName(c),
                        intervals=state.over, time=state.worstStart, time_end=state.worstEnd,
                        slide_per_frame=state.worst)
        report['sequences'].append(summary)

    report['findings_truncated'] = sum(report['issue_counts'].values()) > len(report['findings'])
    report['status'] = 'empty' if not report['samples'] else ('review' if report['issue_counts'] else 'clear')
    return report


def thirdPersonViewDirection(aimPitch: float = 0.0) -> np.ndarray:
    """Unit view direction of JK's external (third-person) camera in the model's frame.

    ``sithCamera_Startup`` gives camera 1 (type 4) the offset (0, -0.2, 0.06). ``sithCamera_Update``
    places that camera at the offset from the eye, in the thing's frame turned by the head pitch,
    and looks at ``sithCamera_trans``, the point 0.3 ahead of the eye in the same frame. At a level
    aim the direction is (0, 0.5, -0.06) normalised, 6.84 degrees below level; ``aimPitch``
    (degrees, up positive) turns it about the x axis. The eye offset moves both points and does not
    change the direction. Not modelled: MotS's COG-set camera angle and position offsets, and the
    camera's collision with walls.
    """
    direction = np.array(CAMERA_TARGET, dtype=float) - np.array(CAMERA_OFFSET, dtype=float)
    direction /= np.linalg.norm(direction)
    if aimPitch:
        direction = rotationMatrix((float(aimPitch), 0.0, 0.0)) @ direction
    return direction


def viewExtent(points, direction) -> Tuple[float, float]:
    """(extent across the view, full extent) of a point set seen along ``direction``.

    The full extent is the largest distance between two of the points. The extent across the view
    is the same measure after projecting the points onto the plane normal to ``direction``: an
    orthographic view, without perspective. A straight stick of length L at an angle a to the view
    reads L sin(a).
    """
    cloud = np.unique(np.asarray(points, dtype=float).reshape(-1, 3), axis=0)
    if not len(cloud):
        raise ValueError('there are no points to measure')
    view = np.asarray(direction, dtype=float).reshape(3)
    length = float(np.linalg.norm(view))
    if length == 0.0:
        raise ValueError('the view direction must not be zero')
    view = view / length
    across = cloud - np.outer(cloud @ view, view)
    return _diameter(across), _diameter(cloud)


def auditCameraReading(model, sequences: Iterable, *, carried: Sequence[CarriedMesh] = (),
                       nodes: Iterable[NodeRef] = (), direction=None, aimPitch: float = 0.0, step: float = 1.0,
                       shortRatio: float = 0.5, minLength: float = 0.05, geoset: int = 0) -> dict:
    """How long the measured meshes read from the third-person camera, sample by sample.

    At each sample the carried meshes shown then and ``nodes``' meshes (default: none) are measured
    together with ``viewExtent`` along ``direction`` (default: ``thirdPersonViewDirection(aimPitch)``).
    The view direction is fixed in the model's frame because the camera turns with the thing. A
    sample reads short when its full extent is at least ``minLength`` and the extent across the
    view is below ``shortRatio`` of it: a long mesh pointing along the view looks like a stub.
    Every sample is reported in ``rows``; findings are one per sequence with the count and the
    sample with the lowest ratio. Perspective, the camera's distance and anything standing between
    the camera and the mesh are not modelled.
    """
    setup = _Setup(model, geoset, nodes, (), carried)
    view = thirdPersonViewDirection(aimPitch) if direction is None else np.asarray(direction, dtype=float).reshape(3)
    if not float(np.linalg.norm(view)):
        raise ValueError('the view direction must not be zero')
    view = view / np.linalg.norm(view)
    report = {'direction': [float(v) for v in view],
              'below_level_degrees': math.degrees(math.asin(max(-1.0, min(1.0, -float(view[2]))))),
              'nodes': list(setup.nodes), 'carried': [c.name for c in setup.carried], 'short_ratio': shortRatio,
              'min_length': minLength, 'samples': 0, 'sequences': [], 'issue_counts': {}, 'findings': []}
    finding = _findings(report)
    for sequence in (_asSequence(item, step) for item in sequences):
        rows, worst, short = [], None, 0
        for sample in sequence.samples:
            _, points, carriedPoints = setup.posed(sample)
            parts = [p for p in points.values() if len(p)] + [p for p in carriedPoints.values() if len(p)]
            row = {'time': float(sample.time), 'carried': sorted(carriedPoints), 'across': None, 'length': None,
                   'ratio': None}
            if parts:
                across, length = viewExtent(np.vstack(parts), view)
                row.update(across=across, length=length, ratio=across / length if length > 0.0 else None)
                if length >= minLength and row['ratio'] is not None and row['ratio'] < shortRatio:
                    short += 1
                    if worst is None or row['ratio'] < worst['ratio']:
                        worst = row
            rows.append(row)
        measured = [row['across'] for row in rows if row['across'] is not None]
        report['sequences'].append({'name': sequence.name, 'samples': len(rows), 'short_samples': short,
                                    'across_min': min(measured) if measured else None,
                                    'across_max': max(measured) if measured else None, 'rows': rows})
        report['samples'] += len(rows)
        if worst is not None:
            finding('reads_short', sequence=sequence.name, samples=short, time=worst['time'],
                    carried=list(worst['carried']), across=worst['across'], length=worst['length'],
                    ratio=worst['ratio'])
    report['findings_truncated'] = sum(report['issue_counts'].values()) > len(report['findings'])
    report['status'] = 'empty' if not report['samples'] else ('review' if report['issue_counts'] else 'clear')
    return report


# ---------------------------------------------------------------------------------------------- helpers

def _findings(report):
    def finding(code, **details):
        counts = report['issue_counts']
        counts[code] = counts.get(code, 0) + 1
        if len(report['findings']) < DETAIL_LIMIT:
            report['findings'].append({'code': code, **details})
    return finding


def _asSequence(item, step: float) -> PoseSequence:
    if isinstance(item, PoseSequence):
        return item
    if not hasattr(item, 'numFrames') or not hasattr(item, 'nodes'):
        raise TypeError('sequences hold KEYs and PoseSequence objects')
    return keySequence(item, step)


def _resolveFloor(model, floor, geoset: int) -> Tuple[float, str]:
    if floor is None:
        return referenceFloor(model, geoset=geoset), 'rest_pose'
    if isinstance(floor, bool):
        raise TypeError('floor is None, a height or a list of PoseLayer')
    if isinstance(floor, numbers.Real):
        return float(floor), 'given'
    layers = tuple(floor)
    if not all(isinstance(layer, PoseLayer) for layer in layers):
        raise TypeError('floor is None, a height or a list of PoseLayer')
    return referenceFloor(model, layers, geoset=geoset), 'reference_pose'


def _nodeIndex(model, ref: NodeRef) -> int:
    nodes = model.meshHierarchy
    if isinstance(ref, numbers.Integral) and not isinstance(ref, bool):
        index = int(ref)
        if 0 <= index < len(nodes):
            return index
        raise ValueError(f'node {index} is not in the hierarchy ({len(nodes)} nodes)')
    if isinstance(ref, str):
        matches = [i for i, node in enumerate(nodes) if node.name.casefold() == ref.casefold()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError(f'no node is named {ref!r}')
        raise ValueError(f'{len(matches)} nodes are named {ref!r}; give an index instead')
    raise TypeError('a node is given by its index or its name')


def _diameter(points: np.ndarray, chunk: int = 256) -> float:
    """Largest distance between two rows of ``points`` (exact, in blocks to bound memory)."""
    if len(points) < 2:
        return 0.0
    squares = (points * points).sum(axis=1)
    best = 0.0
    for start in range(0, len(points), chunk):
        block = points[start:start + chunk]
        d2 = squares[start:start + chunk, None] + squares[None, :] - 2.0 * (block @ points.T)
        best = max(best, float(d2.max()))
    return math.sqrt(max(best, 0.0))


class _Geometry:
    """Each mesh-carrying node's vertices for one geoset, read once.

    The engine draws a node's mesh with that node's matrix (``rdModel3_DrawHNode``), so a mesh that
    two nodes share is drawn, and measured, once per node.
    """

    def __init__(self, model, geoset: int):
        if model.geosets and not 0 <= geoset < len(model.geosets):
            raise ValueError(f'geoset {geoset} is not in the model ({len(model.geosets)} geosets)')
        meshes = model.geosets[geoset].meshes if model.geosets else []
        arrays: Dict[int, np.ndarray] = {}
        self.local: Dict[int, np.ndarray] = {}
        self.mesh: Dict[int, int] = {}
        for i, node in enumerate(model.meshHierarchy):
            m = node.meshIdx
            if m is None or m < 0 or m >= len(meshes):
                continue
            if m not in arrays:
                arrays[m] = np.array([tuple(v) for v in meshes[m].vertices], dtype=float).reshape(-1, 3)
            if len(arrays[m]):
                self.local[i] = arrays[m]
                self.mesh[i] = m


class _Setup:
    """Resolved node lists and carried meshes for one call, and the posing of one sample."""

    def __init__(self, model, geoset, nodes, contacts, carried):
        self.model = model
        self.geoset = geoset
        self.geometry = _Geometry(model, geoset)
        drawn = sorted(self.geometry.local)
        self.nodes = drawn if nodes is None else sorted({_nodeIndex(model, n) for n in nodes})
        self.contacts = self.nodes if contacts is None else sorted({_nodeIndex(model, n) for n in contacts})
        self.carried = tuple(carried)
        names = [c.name for c in self.carried]
        if len(set(names)) != len(names):
            raise ValueError('carried meshes need distinct names')
        self.carriers = [_nodeIndex(model, c.node) for c in self.carried]
        self.hides = [{_nodeIndex(model, h) for h in c.hides} for c in self.carried]
        self.wanted = sorted((set(self.nodes) | set(self.contacts)) & set(drawn))

    def nodeName(self, index: int) -> str:
        return self.model.meshHierarchy[index].name

    def posed(self, sample: PoseSample):
        """Joint frames, then world vertices of the wanted nodes and of the carried meshes shown."""
        pose = poseModel(self.model, sample.layers, preRotations=sample.preRotations, geoset=self.geoset,
                         vertices=False)
        carriers = pose.meshFrames
        time = float(sample.time)
        hidden = set()
        carriedPoints = {}
        for mesh, node, hides in zip(self.carried, self.carriers, self.hides):
            if mesh.shownAt(time):
                hidden |= hides
                frame = carriers[node]
                carriedPoints[mesh.name] = mesh.points @ frame[:3, :3].T + frame[:3, 3]
        points = {}
        for i in self.wanted:
            if i in hidden:
                continue
            frame = carriers[i]
            points[i] = self.geometry.local[i] @ frame[:3, :3].T + frame[:3, 3]
        return pose.frames, points, carriedPoints

    def lowest(self, points, carriedPoints, among):
        """(z, node, carried name or None, vertex) of the lowest vertex, or None if nothing is drawn."""
        best = None
        for i in among:
            p = points.get(i)
            if p is None or not len(p):
                continue
            v = int(np.argmin(p[:, 2]))
            if best is None or p[v, 2] < best[0]:
                best = (float(p[v, 2]), i, None, v)
        for mesh, node in zip(self.carried, self.carriers):
            p = carriedPoints.get(mesh.name)
            if p is None or not len(p):
                continue
            v = int(np.argmin(p[:, 2]))
            if best is None or p[v, 2] < best[0]:
                best = (float(p[v, 2]), node, mesh.name, v)
        return best

    def identify(self, low) -> dict:
        _, node, carried, vertex = low
        return {'node': node, 'node_name': self.nodeName(node),
                'mesh': None if carried is not None else self.geometry.mesh.get(node),
                'carried': carried, 'vertex': vertex}


class _SlipState:
    """Running slide totals of one contact node over one sequence."""

    def __init__(self):
        self.planted = self.rolling = self.over = 0
        self.total = self.worst = 0.0
        self.worstStart = self.worstEnd = None
        self.along: List[float] = []

    def step(self, before, after, start, end, level, band, travel, tolerance):
        if before is None or after is None:
            return
        low0 = before[:, 2] - level
        low1 = after[:, 2] - level
        if low0.min() > band or low1.min() > band:
            return
        touching = (low0 <= band) & (low1 <= band)
        if not touching.any():
            self.rolling += 1
            return
        dt = end - start
        move = (after[touching, :2] - before[touching, :2]).mean(axis=0)
        ground = -np.asarray(travel, dtype=float) * dt if travel is not None else np.zeros(2)
        slide = float(np.hypot(*(move - ground)))
        rate = slide / dt
        self.planted += 1
        self.total += slide
        if rate > self.worst or self.worstStart is None:
            self.worst, self.worstStart, self.worstEnd = rate, start, end
        if rate > tolerance:
            self.over += 1
        if travel is not None:
            speed = math.hypot(*travel)
            if speed > 0.0:
                self.along.append(-float(move @ np.asarray(travel, dtype=float)) / (speed * speed * dt))

    def summary(self, node: int, name: str, travelling: bool) -> dict:
        return {'node': node, 'node_name': name, 'planted_intervals': self.planted,
                'rolling_intervals': self.rolling, 'slide_total': self.total,
                'slide_max_per_frame': self.worst if self.planted else None,
                'slide_max_time': self.worstStart if self.planted else None,
                'over_tolerance': self.over,
                'sweep': (sum(self.along) / len(self.along)) if travelling and self.along else None}
