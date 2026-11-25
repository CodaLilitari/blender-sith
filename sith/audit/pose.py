# SPDX-License-Identifier: GPL-3.0-or-later
"""Pose a native 3DO hierarchy with text KEYs the way the engine does, without Blender.

This follows the JK/MotS puppet code (`rdKeyframe_LoadEntry`, `rdPuppet_BuildJointMatrices`
and `rdThing_AccumulateMatrices`, as reconstructed by OpenJKDF2 from retail JK.EXE 1.01):

- A KEY stores each track at its node number. A later track for the same node replaces an
  earlier one. Tracks numbered at or past the KEY's ``JOINTS`` are ignored here; retail
  writes and reads them outside the KEY's allocation, which ``sith.audit.keylint`` reports.
- A track's value at time ``t`` comes from its last entry at or before ``t``. Entry flag 1
  adds ``deltaPosition * (t - entry.frame)``, flag 2 adds ``deltaRotation * (t - entry.frame)``.
  Before the first entry the engine still uses entry 0, so ``t - frame`` can be negative.
- Each joint blends deltas from its rest position and rotation, not absolute values. A track's
  priority for a node is its high priority where the KEY's node-type mask shares a bit with
  the node's type, else its low priority. The highest priority accumulates; the next priority
  fills whatever weight the highest left; the rest of the weight falls back to the rest pose.
  Rotation deltas are normalized to the engine's acute range ``(-180, 180]`` per component.
- Joint frames accumulate parent to child: ``F_i = F_parent * T(position_i) * R(PYR_i)``.
  A node's pivot offsets its own mesh only: ``v_world = F_i * (v + pivot_i)``.
- The root's parent frame here is ``T(insertOffset)``, the add-on's model frame. The engine
  itself draws from the thing's placement and uses the insert offset when it places the thing
  (``sithThing_CreateThing``) and for its physics height (``sithPhysics``), so these frames are
  the thing's frame shifted by the insert offset. That leaves every relative measure unchanged and
  puts a stock standing character's floor near z = 0; ``sith.audit.ground`` documents the
  engine floor for absolute heights.
- A mesh shared by several nodes is drawn once per node (``rdModel3_DrawHNode``).
  ``Pose.vertices`` keeps the first node's copy per mesh; ``posedNodeVertices`` gives every drawn
  instance.

Rotations are pitch/yaw/roll degrees built as ``Rz(yaw) * Rx(pitch) * Ry(roll)``, the order of
``rdMatrix_Build34``. Positions are JK units. A pose is a static evaluation: it does not run
puppet timing, looping, fades, collision or the player's aim pitch, which callers can supply
as ``preRotations``.
"""

from dataclasses import dataclass, field
import math
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    'PoseLayer', 'LocalPose', 'Pose', 'rotationMatrix', 'normalizeAngleAcute', 'keyTrack',
    'sampleTrack', 'blendLocalPose', 'jointFrames', 'meshFrames', 'posedMeshVertices',
    'posedNodeVertices', 'poseModel', 'restPose', 'keyFrameTimes', 'nodeTypeValue', 'cutReferenceDiagnostic',
]


def nodeTypeValue(value) -> int:
    """Integer bits of a node-type mask stored as a Flag, an int or None."""
    if value is None:
        return 0
    return int(getattr(value, 'value', value))


def normalizeAngleAcute(angles):
    """Map degrees to the engine's acute range ``(-180, 180]`` (``stdMath_NormalizeAngleAcute``)."""
    a = np.mod(np.asarray(angles, dtype=float), 360.0)
    return np.where(a > 180.0, a - 360.0, a)


def rotationMatrix(pyr) -> np.ndarray:
    """3x3 rotation for (pitch, yaw, roll) degrees: ``Rz(yaw) @ Rx(pitch) @ Ry(roll)``."""
    pitch, yaw, roll = (math.radians(float(v)) for v in pyr)
    sp, cp = math.sin(pitch), math.cos(pitch)
    sy, cy = math.sin(yaw), math.cos(yaw)
    sr, cr = math.sin(roll), math.cos(roll)
    # Columns are the engine's rvec, lvec and uvec (rdMatrix_Build34).
    return np.array([[cr * cy - sr * sy * sp, -sy * cp, cr * sy * sp + sr * cy],
                     [sr * cy * sp + cr * sy, cy * cp, sr * sy - sp * cy * cr],
                     [-sr * cp, sp, cr * cp]], dtype=float)


@dataclass
class PoseLayer:
    """One playing KEY track: which KEY, at what time, with what weight and priorities.

    ``weight`` is the track's play-speed weight. At or below 1 it scales the KEY's deltas; above
    1 it counts in the blend without scaling, as in the engine. ``highPri`` defaults to
    ``lowPri``. COG ``PlayKey(thing, key, p, flags)`` uses ``p`` and ``p + 2``: see ``playKey``.
    """
    key: object
    frame: float
    weight: float = 1.0
    lowPri: int = 0
    highPri: Optional[int] = None

    @classmethod
    def playKey(cls, key, frame: float, priority: int, weight: float = 1.0) -> 'PoseLayer':
        """The priorities COG ``PlayKey`` assigns: low ``priority``, high ``priority + 2``."""
        return cls(key, frame, weight, priority, priority + 2)

    def priorityFor(self, nodeType) -> int:
        high = self.lowPri if self.highPri is None else self.highPri
        return high if nodeTypeValue(getattr(self.key, 'nodeTypes', 0)) & nodeTypeValue(nodeType) else self.lowPri


@dataclass
class LocalPose:
    """Per-node local transforms after blending.

    ``positions`` (N, 3) and ``rotations`` (N, 3, pitch/yaw/roll degrees) are what the engine
    passes to ``rdMatrix_Build34``. ``restWeight`` is the share of each node left on its rest
    pose (1 means no layer drove it). ``drivenBy`` lists, per node, the layer indices that
    contributed, highest priority first.
    """
    positions: np.ndarray
    rotations: np.ndarray
    restWeight: np.ndarray
    drivenBy: List[Tuple[int, ...]] = field(default_factory=list)


@dataclass
class Pose:
    """A posed model: local transforms, joint frames, mesh frames and world vertices by mesh."""
    local: LocalPose
    frames: np.ndarray
    meshFrames: np.ndarray
    vertices: Dict[int, np.ndarray]


def keyTrack(key, nodeIdx: int):
    """The track the engine uses for ``nodeIdx``: the last one stored there, below ``JOINTS``."""
    if nodeIdx < 0 or nodeIdx >= int(getattr(key, 'numJoints', 0)):
        return None
    track = None
    for node in key.nodes:
        if node.idx == nodeIdx:
            track = node
    if track is None or not track.keyframes:
        return None
    return track


def sampleTrack(track, frame: float) -> Tuple[np.ndarray, np.ndarray]:
    """Absolute (position, rotation) of one track at ``frame``, with the engine's entry rule."""
    entries = track.keyframes
    entry = entries[0]
    for candidate in entries:
        if float(candidate.frame) <= frame:
            entry = candidate
        else:
            break
    dt = frame - float(entry.frame)
    flags = int(entry.flags)
    position = np.array(entry.position, dtype=float)
    rotation = np.array(entry.orientation, dtype=float)
    if flags & 1:
        position = position + np.array(entry.deltaPosition, dtype=float) * dt
    if flags & 2:
        rotation = rotation + np.array(entry.deltaRotation, dtype=float) * dt
    return position, rotation


def _rest(model) -> Tuple[np.ndarray, np.ndarray]:
    nodes = model.meshHierarchy
    return (np.array([tuple(n.position) for n in nodes], dtype=float).reshape(-1, 3),
            np.array([tuple(n.rotation) for n in nodes], dtype=float).reshape(-1, 3))


def blendLocalPose(model, layers: Sequence[PoseLayer]) -> LocalPose:
    """Blend KEY layers into per-node local transforms (``rdPuppet_BuildJointMatrices``).

    ``layers`` are visited in order, like the engine's track slots. Ties at equal priority
    accumulate in that order, which only matters through floating-point rounding.
    """
    restPos, restRot = _rest(model)
    count = len(model.meshHierarchy)
    positions = np.empty((count, 3))
    rotations = np.empty((count, 3))
    restWeight = np.empty(count)
    drivenBy: List[Tuple[int, ...]] = []
    tracks = [{} for _ in layers]
    for li, layer in enumerate(layers):
        for node in model.meshHierarchy:
            track = keyTrack(layer.key, node.idx)
            if track is not None:
                tracks[li][node.idx] = track

    for i, node in enumerate(model.meshHierarchy):
        best = second = 0            # priorities: v75 and v73 in the engine
        wBest = wSecond = 0.0        # weights:    v70 and v71
        dpBest = np.zeros(3); drBest = np.zeros(3)
        dpSecond = np.zeros(3); drSecond = np.zeros(3)
        usedBest: List[int] = []; usedSecond: List[int] = []
        for li, layer in enumerate(layers):
            track = tracks[li].get(node.idx)
            if track is None:
                continue
            pri = layer.priorityFor(node.type)
            if not (pri >= second and (pri >= best or wBest < 1.0)):
                continue
            pos, rot = sampleTrack(track, float(layer.frame))
            dp = pos - restPos[i]
            dr = normalizeAngleAcute(rot - restRot[i])
            w = float(layer.weight)
            if w <= 1.0:
                w = max(w, 0.0)
                dp = dp * w; dr = dr * w
            if pri == best:
                dpBest = dpBest + dp; drBest = drBest + dr; wBest += w; usedBest.append(li)
            elif pri <= best:
                if pri <= second:
                    dpSecond = dpSecond + dp; drSecond = drSecond + dr; wSecond += w; usedSecond.append(li)
                else:
                    dpSecond, drSecond, wSecond, second, usedSecond = dp, dr, w, pri, [li]
            else:
                dpSecond, drSecond, wSecond, second, usedSecond = dpBest, drBest, wBest, best, usedBest
                dpBest, drBest, wBest, best, usedBest = dp, dr, w, pri, [li]

        if wBest >= 1.0 or wSecond <= 0.0:
            if wBest > 1.0:
                dpBest = dpBest / wBest; drBest = drBest / wBest
            left = max(0.0, 1.0 - wBest)
            used = tuple(usedBest)
        else:
            if wSecond > 1.0:
                dpSecond = dpSecond / wSecond; drSecond = drSecond / wSecond
                wSecond = 1.0
            dpBest = dpBest + dpSecond * (1.0 - wBest)
            drBest = drBest + drSecond * (1.0 - wBest)
            left = max(0.0, 1.0 - wBest - wSecond * (1.0 - wBest))
            used = tuple(usedBest) + tuple(usedSecond)
        positions[i] = restPos[i] + dpBest
        rotations[i] = restRot[i] + normalizeAngleAcute(drBest)
        restWeight[i] = left
        drivenBy.append(used)
    return LocalPose(positions, rotations, restWeight, drivenBy)


def _nodeMatrix(position, rotation, preRotation=None) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = rotationMatrix(rotation)
    m[:3, 3] = position
    if preRotation is not None and any(float(v) for v in preRotation):
        r = np.eye(4); r[:3, :3] = rotationMatrix(preRotation)
        m = m @ r                    # rdMatrix_PreRotate34
    return m


def jointFrames(model, local: LocalPose, preRotations: Optional[Mapping[int, Iterable[float]]] = None) -> np.ndarray:
    """(N, 4, 4) joint frames ``F_i``, parent first, rooted at ``T(insertOffset)`` (see the module notes).

    ``preRotations`` maps node indices to a (pitch, yaw, roll) applied in the joint's own
    space after blending, as the engine does for the player's aim.
    """
    nodes = model.meshHierarchy
    count = len(nodes)
    frames = np.zeros((count, 4, 4))
    done = [False] * count
    insert = np.eye(4)
    insert[:3, 3] = tuple(getattr(model, 'insertOffset', (0.0, 0.0, 0.0)))
    preRotations = preRotations or {}

    def build(i, depth=0):
        if done[i]:
            return frames[i]
        if depth > count:
            raise ValueError('3DO hierarchy has a parent cycle')
        parent = nodes[i].parentIdx
        base = insert if parent is None or parent < 0 else build(parent, depth + 1)
        frames[i] = base @ _nodeMatrix(local.positions[i], local.rotations[i], preRotations.get(i))
        done[i] = True
        return frames[i]

    for i in range(count):
        build(i)
    return frames


def cutReferenceDiagnostic(model, reference, frames) -> dict:
    """Describe the chosen cut pose without inferring which pose the artist used."""
    restLocal = blendLocalPose(model, ())
    identity = np.eye(3)
    rotated = sum(not np.allclose(rotationMatrix(pyr), identity, rtol=0, atol=1e-9) for pyr in restLocal.rotations)
    rest = jointFrames(model, restLocal)
    matches = np.isclose(frames, rest, rtol=0, atol=1e-9).all(axis=(1, 2))
    changed = int(np.count_nonzero(~matches))
    name = 'rest pose' if reference is None else getattr(reference, 'description', 'caller-supplied pose')
    driven = getattr(reference, 'keyDrivenNodes', None)
    advice = 'Compare this pose with the source used to cut the rigid pieces before interpreting the counts.'
    if driven == 0:
        advice = 'The reference KEY drives no nodes in this hierarchy. Check its JOINTS and track indices.'
    elif changed == 0:
        advice = (f'The selected frames match rest. Stored local rotations differ from identity on {rotated} of {len(matches)} nodes. '
                  'If the pieces were cut in a KEY pose, '
                  'check the original KEY, frame and rotation setting.')
    return {'description': name, 'node_count': len(matches), 'rotated_rest_nodes': rotated,
            'frames_different_from_rest': changed,
            'key_driven_nodes': driven, 'advice': advice}


def meshFrames(model, frames: np.ndarray) -> np.ndarray:
    """Frames that carry each node's mesh: ``F_i * T(pivot_i)``."""
    out = frames.copy()
    for i, node in enumerate(model.meshHierarchy):
        pivot = np.array(tuple(node.pivot), dtype=float)
        out[i, :3, 3] = frames[i, :3, :3] @ pivot + frames[i, :3, 3]
    return out


def posedMeshVertices(model, frames: np.ndarray, geoset: int = 0) -> Dict[int, np.ndarray]:
    """World vertices by mesh index for one geoset, from joint frames ``F_i``.

    A mesh not referenced by any node is omitted. If several nodes reference one mesh, the
    first one wins and the shared use is left to ``sith.audit`` reports.
    """
    carried = meshFrames(model, frames)
    meshes = model.geosets[geoset].meshes if model.geosets else []
    out: Dict[int, np.ndarray] = {}
    for i, node in enumerate(model.meshHierarchy):
        m = node.meshIdx
        if m is None or m < 0 or m >= len(meshes) or m in out:
            continue
        verts = np.array([tuple(v) for v in meshes[m].vertices], dtype=float).reshape(-1, 3)
        out[m] = verts @ carried[i, :3, :3].T + carried[i, :3, 3]
    return out


def posedNodeVertices(model, frames: np.ndarray, geoset: int = 0) -> Dict[int, np.ndarray]:
    """World vertices by hierarchy node for one geoset: every instance the engine draws.

    Unlike ``posedMeshVertices``, a mesh referenced by several nodes appears once per node.
    """
    carried = meshFrames(model, frames)
    meshes = model.geosets[geoset].meshes if model.geosets else []
    out: Dict[int, np.ndarray] = {}
    for i, node in enumerate(model.meshHierarchy):
        m = node.meshIdx
        if m is None or m < 0 or m >= len(meshes):
            continue
        verts = np.array([tuple(v) for v in meshes[m].vertices], dtype=float).reshape(-1, 3)
        out[i] = verts @ carried[i, :3, :3].T + carried[i, :3, 3]
    return out


def poseModel(model, layers: Sequence[PoseLayer] = (), *, preRotations=None, geoset: int = 0,
              vertices: bool = True) -> Pose:
    """Blend ``layers`` and return the posed model. With no layers this is the rest pose."""
    local = blendLocalPose(model, layers)
    frames = jointFrames(model, local, preRotations)
    carried = meshFrames(model, frames)
    verts = posedMeshVertices(model, frames, geoset) if vertices else {}
    return Pose(local, frames, carried, verts)


def restPose(model, *, geoset: int = 0, vertices: bool = True) -> Pose:
    """The model as authored: every node at its rest position and rotation."""
    return poseModel(model, (), geoset=geoset, vertices=vertices)


def keyFrameTimes(key, step: float = 1.0, *, toEnd: bool = False) -> List[float]:
    """Sample times ``0, step, 2*step ...`` of a KEY played alone. Time 0 is always included.

    By default the times stop at ``FRAMES - 1``, as Red's joint sweep sampled. The engine also
    plays the last interval: a looping KEY shows every time below ``FRAMES`` before it wraps, and a
    KEY that holds or fades at its end stops at ``FRAMES`` (``rdPuppet_AdvanceTrack`` in OpenJKDF2
    ``Engine/rdPuppet.c``). With ``toEnd`` the times are every ``k * step`` below ``FRAMES``, then
    ``FRAMES`` itself. ``step`` must be a positive finite number of frames.
    """
    if not (math.isfinite(step) and step > 0):
        raise ValueError('step must be a positive finite number')
    step = float(step)
    frames = int(getattr(key, 'numFrames', 0))
    if toEnd:
        end = max(frames, 0)
        count = max(1, int(math.ceil(end / step - 1e-9))) if end else 0
        return [round(k * step, 9) for k in range(count)] + [float(end)]
    count = int(math.floor((max(frames, 1) - 1) / step + 1e-9)) + 1
    return [round(k * step, 9) for k in range(count)]
