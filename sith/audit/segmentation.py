# SPDX-License-Identifier: GPL-3.0-or-later
"""Rigid-piece cut lint: surface left in the wrong piece ("ghosts"), without Blender.

The engine draws each hierarchy node's mesh with that node's own joint matrix (``rdModel3_DrawHNode``
with ``paJointMatrices`` from ``rdThing_AccumulateMatrices``; OpenJKDF2 ``Primitives/rdModel3.c`` and
``Engine/rdThing.c``). Every face therefore moves exactly with the node that carries its mesh. When a
character is cut into rigid pieces and the cut leaves part of a limb's surface in the torso's mesh,
that surface stays with the torso when the limb moves away. At rest it hides inside or against the
limb, so a still model does not show it.

The lint works in one reference pose (the rest pose unless the caller passes another) and looks at
rigid parts: the nodes that move together (``rigidParts``). It gathers three kinds of evidence:

- Islands: pieces of a part that do not touch the rest of it. Vertices are welded by position across
  all meshes of the part, so a surface split over several meshes is one piece. A small island that
  sits nearer another part's bone than its own is the strongest signal.
- Foreign surface: faces nearer another part's bone than their own by at least ``margin``. A part's
  bone is a set of segments from its joint to each child part's joint (``partBones``).
- Covered surface: faces inside another part's volume. The test is the generalized winding number
  (``windingNumbers``), which reads 1 inside a closed surface, 0 outside, and falls off smoothly
  across the opening of an open one, so a limb left open at its cut still encloses what is deep
  inside it. A part never counts as covering its descendants' faces: a child's end tucked into its
  parent at the joint is how rigid pieces are built.

Evidence on a part's main piece is grouped into connected regions, one per joint at which the
claiming part turns against the face's own part. Each region and each small island is measured:

- ``facing``: share of its area whose normals point toward the claiming bone and away from the own
  bone. That is the own part's skin facing the other part, which is normal (a flank beside a hanging
  arm). Surface built from the other part wraps that part's bone instead.
- ``lever``: how far it reaches from that joint. A turn of the joint by an angle leaves it behind by
  about the lever times the angle in radians.
- ``separation`` (only with test poses): how far it actually moves away from the claiming part in
  those poses, with the KEY and frame where that is largest.

Severity. A region, or an island that is small (at most ``islandShare`` of its part's area) and nearer
another part's bone than its own by ``margin``, is a candidate when it does not face the other part
(``facing`` at most ``facingMax``) and has at least ``minFaces`` faces. A candidate claimed by an
ancestor of its own part is a child's end lying near its parent's bone, the normal overlap at a joint:
``review`` from ``leverHigh``, otherwise ``info``. Any other candidate is ``high`` from ``leverHigh``
(or, with test poses and ``separationHigh``, from that separation) and ``review`` from
``leverReview``. Everything else is ``info``: counted, and listed only on request.

Limits, plainly: a bone is a straight segment, so surface right at a joint (the round of a shoulder)
is about equally near both bones and only the enclosure, island and lever evidence speaks for it.
The thresholds are JK units with defaults for Kyle-sized characters. The reference pose must be the
pose the pieces were cut in; many of Andrei's characters were cut with Kyle's joints at zero rotation
(``zeroRotationPose``), not in the stored rest pose. A finding is a lead for a close look in the
poses where the limb moves away, not a verdict.
"""

import colorsys
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..model.model3doEdit import componentLabels, weldPositions
from .pose import (Pose, PoseLayer, blendLocalPose, jointFrames, keyFrameTimes, keyTrack, meshFrames,
                   poseModel, posedMeshVertices, sampleTrack)

__all__ = [
    'KYLE_JOINT_NAMES', 'rigidParts', 'drivenNodes', 'zeroRotationPose', 'partBones', 'windingNumbers',
    'testPoses', 'auditSegmentation', 'pieceColours',
]

DETAIL_LIMIT = 32
SEVERITIES = ('high', 'review', 'info')

# Kyle's joints, the skeleton most custom characters reuse (the $$$DUMMY nodes carry no mesh).
KYLE_JOINT_NAMES = ('k_hip', 'k_torso', 'k_neck', 'k_head', 'k_lshouldr', 'k_lforearm', 'k_lhand',
                    'k_rshouldr', 'k_rforearm', 'k_rhand', 'k_lthigh', 'k_lcalf', 'k_lfoot', 'k_rthigh',
                    'k_rcalf', 'k_rfoot')


# ----------------------------------------------------------------------------------------- rigid parts
def _parents(model) -> List[int]:
    nodes = model.meshHierarchy
    parents = []
    for i, node in enumerate(nodes):
        parent = int(node.parentIdx) if node.parentIdx is not None else -1
        if parent >= len(nodes) or parent < -1:
            raise ValueError(f'3DO node {i} ({node.name}) has an invalid parent index {parent}')
        parents.append(parent)
    return parents


def rigidParts(model, joints: Optional[Iterable] = None) -> List[int]:
    """The rigid part of every hierarchy node: the index of the joint node it moves with.

    A joint node starts a part; any other node moves with its parent and joins the parent's part.
    Roots are always joint nodes. ``joints`` names the others by index or by name (case does not
    matter). By default they are the nodes named like Kyle's skeleton (``KYLE_JOINT_NAMES``) when the
    model has any, because custom characters hang extra mesh and adapter nodes under those joints;
    otherwise every node is its own part. Pass ``drivenNodes(model, keys)`` to take them from the
    KEYs the character plays instead.
    """
    nodes = model.meshHierarchy
    parents = _parents(model)
    if joints is None:
        names = [node.name.lower() for node in nodes]
        kyle = set(KYLE_JOINT_NAMES)
        if any(name in kyle for name in names):
            isJoint = [name in kyle or parent < 0 for name, parent in zip(names, parents)]
        else:
            isJoint = [True] * len(nodes)
    else:
        byName: Dict[str, int] = {}
        for i, node in enumerate(nodes):
            byName.setdefault(node.name.lower(), i)
        chosen = set()
        for joint in joints:
            if isinstance(joint, str):
                if joint.lower() not in byName:
                    raise ValueError(f'3DO has no node named {joint!r}')
                chosen.add(byName[joint.lower()])
            else:
                index = int(joint)
                if not 0 <= index < len(nodes):
                    raise ValueError(f'3DO has no node {index}')
                chosen.add(index)
        isJoint = [i in chosen or parents[i] < 0 for i in range(len(nodes))]
    parts = []
    for i in range(len(nodes)):
        j = i
        for _ in range(len(nodes) + 1):
            if isJoint[j]:
                break
            j = parents[j]
        else:
            raise ValueError('3DO hierarchy has a parent cycle')
        parts.append(j)
    return parts


def drivenNodes(model, keys: Iterable) -> List[int]:
    """Nodes that at least one of ``keys`` drives: a track below the KEY's JOINTS (``keyTrack``)."""
    driven = set()
    count = len(model.meshHierarchy)
    for key in keys:
        for i in range(count):
            if keyTrack(key, i) is not None:
                driven.add(i)
    return sorted(driven)


def zeroRotationPose(model, *, stand=None, frame: float = 0.0, nodes: Optional[Iterable[int]] = None,
                     geoset: int = 0) -> Pose:
    """The pose many custom characters were cut in: joints at zero rotation.

    Red's joint sweep for Darth Lilitari, Lilivane and Biddy Blas used Kyle's nodes at the stand KEY's
    positions with zero rotation as the pose their pieces were cut in. Nodes in ``nodes`` (default: the
    nodes ``stand`` drives, else the nodes named like Kyle's skeleton, else every node) get zero
    rotation, and the position ``stand`` gives them at ``frame`` where it has a track. Every other node
    keeps its rest position and rotation.
    """
    count = len(model.meshHierarchy)
    if nodes is None:
        if stand is not None:
            nodes = drivenNodes(model, [stand])
        else:
            kyle = set(KYLE_JOINT_NAMES)
            nodes = [i for i, n in enumerate(model.meshHierarchy) if n.name.lower() in kyle] or range(count)
    local = blendLocalPose(model, [])
    for i in nodes:
        i = int(i)
        if not 0 <= i < count:
            raise ValueError(f'3DO has no node {i}')
        local.rotations[i] = 0.0
        track = keyTrack(stand, i) if stand is not None else None
        if track is not None:
            local.positions[i] = sampleTrack(track, float(frame))[0]
    frames = jointFrames(model, local)
    return Pose(local, frames, meshFrames(model, frames), posedMeshVertices(model, frames, geoset))


# ----------------------------------------------------------------------------------------- geometry
def windingNumbers(points, triangles, *, chunk: int = 250_000) -> np.ndarray:
    """Generalized winding number of each point with respect to triangles ``(T, 3, 3)``.

    Triangles follow the right-hand rule outward, as 3DO faces do (a face's stored normal is
    ``(v1 - v0) x (v2 - v0)``). The number is 1 inside a closed surface, 0 outside, and changes
    smoothly across the opening of an open one (Jacobson, Kavan and Sorkine-Hornung 2013), with each
    triangle's solid angle from Van Oosterom and Strackee (1983). ``chunk`` bounds the point-triangle
    pairs held in memory at once.
    """
    p = np.asarray(points, dtype=float).reshape(-1, 3)
    t = np.asarray(triangles, dtype=float).reshape(-1, 3, 3)
    out = np.zeros(len(p))
    if len(p) == 0 or len(t) == 0:
        return out
    step = max(1, chunk // len(t))
    for start in range(0, len(p), step):
        q = p[start:start + step, None, :]
        a = t[None, :, 0, :] - q
        b = t[None, :, 1, :] - q
        c = t[None, :, 2, :] - q
        la = np.linalg.norm(a, axis=2)
        lb = np.linalg.norm(b, axis=2)
        lc = np.linalg.norm(c, axis=2)
        det = np.sum(a * np.cross(b, c), axis=2)
        div = (la * lb * lc + np.sum(a * b, axis=2) * lc + np.sum(b * c, axis=2) * la
               + np.sum(c * a, axis=2) * lb)
        out[start:start + step] = np.arctan2(det, div).sum(axis=1) / (2.0 * math.pi)
    return out


def _segmentPoints(points, segments):
    """Nearest point on each segment ``(K, 2, 3)`` for each point ``(P, 3)``: distances ``(P, K)`` and points."""
    a = segments[:, 0, :][None]
    ab = (segments[:, 1, :] - segments[:, 0, :])[None]
    length = np.sum(ab * ab, axis=2)
    rel = points[:, None, :] - a
    with np.errstate(invalid='ignore', divide='ignore'):
        t = np.where(length > 1e-18, np.sum(rel * ab, axis=2) / np.where(length > 1e-18, length, 1.0), 0.0)
    t = np.clip(t, 0.0, 1.0)
    nearest = a + t[..., None] * ab
    return np.linalg.norm(points[:, None, :] - nearest, axis=2), nearest


def _pointTriangleDistance(points, triangles, *, chunk: int = 250_000) -> np.ndarray:
    """Smallest distance from each point to any triangle (Ericson, Real-Time Collision Detection 5.1.5)."""
    p = np.asarray(points, dtype=float).reshape(-1, 3)
    t = np.asarray(triangles, dtype=float).reshape(-1, 3, 3)
    out = np.full(len(p), np.inf)
    if len(p) == 0 or len(t) == 0:
        return out
    step = max(1, chunk // len(t))
    for start in range(0, len(p), step):
        q = p[start:start + step, None, :]
        a, b, c = t[None, :, 0, :], t[None, :, 1, :], t[None, :, 2, :]
        ab, ac, ap = b - a, c - a, q - a
        d1, d2 = np.sum(ab * ap, axis=2), np.sum(ac * ap, axis=2)
        bp = q - b
        d3, d4 = np.sum(ab * bp, axis=2), np.sum(ac * bp, axis=2)
        cp = q - c
        d5, d6 = np.sum(ab * cp, axis=2), np.sum(ac * cp, axis=2)
        va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
        with np.errstate(invalid='ignore', divide='ignore'):
            total = va + vb + vc
            v = np.where(total != 0, vb / np.where(total != 0, total, 1), 0.0)
            w = np.where(total != 0, vc / np.where(total != 0, total, 1), 0.0)
            near = a + ab * v[..., None] + ac * w[..., None]
            tab = np.where(d1 - d3 != 0, d1 / np.where(d1 - d3 != 0, d1 - d3, 1), 0.0)
            tac = np.where(d2 - d6 != 0, d2 / np.where(d2 - d6 != 0, d2 - d6, 1), 0.0)
            den = (d4 - d3) + (d5 - d6)
            tbc = np.where(den != 0, (d4 - d3) / np.where(den != 0, den, 1), 0.0)
        inA = (d1 <= 0) & (d2 <= 0)
        inB = (d3 >= 0) & (d4 <= d3)
        inC = (d6 >= 0) & (d5 <= d6)
        onAB = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
        onAC = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
        onBC = (va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0)
        # Ericson's order of tests: vertex regions first, then edges, then the face.
        near = np.where(onBC[..., None], b + (c - b) * tbc[..., None], near)
        near = np.where(onAC[..., None], a + ac * tac[..., None], near)
        near = np.where(onAB[..., None], a + ab * tab[..., None], near)
        near = np.where(inC[..., None], c, near)
        near = np.where(inB[..., None], b, near)
        near = np.where(inA[..., None], a, near)
        out[start:start + step] = np.linalg.norm(q - near, axis=2).min(axis=1)
    return out


class _Scene:
    """Faces of every rigid part in the reference pose, with welding, islands and bones."""

    def __init__(self, model, parts, frames, geoset, weld):
        if not 0 <= geoset < len(model.geosets):
            raise ValueError(f'3DO has no geoset {geoset}')
        nodes = model.meshHierarchy
        meshes = model.geosets[geoset].meshes
        self.model, self.nodes, self.meshes, self.parts = model, nodes, meshes, parts
        self.frames = np.asarray(frames, dtype=float)
        self.origin = self.frames[:, :3, 3]
        self.carried = meshFrames(model, self.frames)
        self.carrier: Dict[int, int] = {}
        users: Dict[int, int] = {}
        for i, node in enumerate(nodes):
            m = node.meshIdx
            if m is not None and 0 <= m < len(meshes):
                users[m] = users.get(m, 0) + 1
                self.carrier.setdefault(m, i)
        self.unplaced = [m for m in range(len(meshes)) if m not in self.carrier]
        self.shared = sorted(m for m, n in users.items() if n > 1)
        parents = _parents(model)
        self.partParent = {p: (parts[parents[p]] if parents[p] >= 0 else -1) for p in sorted(set(parts))}

        # Per part: every face of every mesh the part carries, in mesh order.
        self.faces: Dict[int, dict] = {}
        for p in sorted(set(parts)):
            members = [(m, i) for m, i in sorted(self.carrier.items()) if parts[i] == p]
            self.faces[p] = self._collect(members, weld)
        self.geometric = [p for p, data in self.faces.items() if len(data['area']) and data['area'].sum() > 0]
        self.bones = self._bones()

    def _collect(self, members, weld):
        verts, corners, offsets, faceMesh, faceIndex, faceNode = [], [], [0], [], [], []
        tri, triFace = [], []
        base = 0
        for m, i in members:
            mesh = self.meshes[m]
            local = np.array([tuple(v) for v in mesh.vertices], dtype=float).reshape(-1, 3)
            frame = self.carried[i]
            verts.append(local @ frame[:3, :3].T + frame[:3, 3])
            for fi, face in enumerate(mesh.faces):
                ids = [int(v) for v in face.vertexIdxs]
                if any(not 0 <= v < len(local) for v in ids):
                    raise ValueError(f'Mesh {m} ({mesh.name}), face {fi} has a vertex index out of range')
                f = len(faceMesh)
                corners.extend(v + base for v in ids)
                offsets.append(len(corners))
                faceMesh.append(m)
                faceIndex.append(fi)
                faceNode.append(i)
                for k in range(1, len(ids) - 1):
                    tri.append((ids[0] + base, ids[k] + base, ids[k + 1] + base))
                    triFace.append(f)
            base += len(local)
        world = np.vstack(verts) if verts else np.zeros((0, 3))
        corners = np.array(corners, dtype=np.int64)
        offsets = np.array(offsets, dtype=np.int64)
        tri = np.array(tri, dtype=np.int64).reshape(-1, 3)
        triFace = np.array(triFace, dtype=np.int64)
        count = len(faceMesh)
        triangles = world[tri] if len(tri) else np.zeros((0, 3, 3))
        cross = (np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
                 if len(tri) else np.zeros((0, 3)))
        area = np.zeros(count)
        normal = np.zeros((count, 3))
        np.add.at(area, triFace, 0.5 * np.linalg.norm(cross, axis=1))
        np.add.at(normal, triFace, cross)
        length = np.linalg.norm(normal, axis=1)
        normal = normal / np.where(length > 0, length, 1.0)[:, None]
        sizes = np.diff(offsets)
        centroid = np.zeros((count, 3))
        if len(corners):
            np.add.at(centroid, np.repeat(np.arange(count), sizes), world[corners])
        centroid /= np.maximum(sizes, 1)[:, None]
        welded = weldPositions(world, weld) if len(world) else np.zeros(0, dtype=np.int64)
        # Islands: faces joined through welded corners.
        a, b = [], []
        for f in range(count):
            ids = welded[corners[offsets[f]:offsets[f + 1]]]
            if len(ids) > 1:
                a.extend(ids[:-1])
                b.extend(ids[1:])
        labels = componentLabels(len(world), np.array(a, dtype=np.int64), np.array(b, dtype=np.int64))
        island = np.array([labels[welded[corners[offsets[f]]]] if sizes[f] else -1 - f for f in range(count)],
                          dtype=np.int64)
        return dict(world=world, welded=welded, corners=corners, offsets=offsets,
                    mesh=np.array(faceMesh, dtype=np.int64), index=np.array(faceIndex, dtype=np.int64),
                    node=np.array(faceNode, dtype=np.int64),
                    tri=tri, triFace=triFace, triangles=triangles, area=area, normal=normal,
                    centroid=centroid, island=island)

    def _bones(self):
        """Segments from each geometric part's joint to its child parts' joints (or to its far end)."""
        children: Dict[int, List[int]] = {p: [] for p in self.faces}
        for c, parent in self.partParent.items():
            if parent >= 0:
                children[parent].append(c)
        bones = {}
        for p in self.geometric:
            o = self.origin[p]
            segs = [(o, self.origin[c]) for c in sorted(children[p])]
            if not segs:
                world = self.faces[p]['world']
                used = np.unique(self.faces[p]['corners'])
                pts = world[used] if len(used) else o[None]
                d = pts.mean(axis=0) - o
                length = float(np.linalg.norm(d))
                if length > 1e-12:
                    d = d / length
                    reach = max(float(np.percentile((pts - o) @ d, 90)), 0.0)
                    segs = [(o, o + d * reach)]
                else:
                    segs = [(o, o)]
            bones[p] = np.array(segs, dtype=float).reshape(-1, 2, 3)
        return bones

    def ancestors(self, p) -> List[int]:
        chain = []
        seen = set()
        p = self.partParent.get(p, -1)
        while p >= 0 and p not in seen:
            chain.append(p)
            seen.add(p)
            p = self.partParent.get(p, -1)
        return chain

    def relation(self, own, other) -> str:
        if other in self.ancestors(own):
            return 'ancestor'
        if own in self.ancestors(other):
            return 'descendant'
        return 'other'

    def branch(self, own, other) -> int:
        """The joint at which ``other`` turns against ``own``: the part just below their common ancestor."""
        chainOwn = [own] + self.ancestors(own)
        chainOther = [other] + self.ancestors(other)
        common = next((p for p in chainOwn if p in chainOther), None)
        if common is None:
            return chainOther[-1]
        if common != other:
            return chainOther[chainOther.index(common) - 1]
        return chainOwn[chainOwn.index(common) - 1]


def partBones(model, *, joints=None, reference=None, geoset: int = 0, weld: float = 1e-6) -> Dict[int, np.ndarray]:
    """Bone segments ``(S, 2, 3)`` of every part that carries geometry, keyed by the part's joint node.

    A part's bone runs from its joint to each child part's joint, in the reference pose (rest by
    default). A part with no child part gets one segment from its joint toward the far side of its
    own geometry (90th percentile of its vertices along the direction to their centre).
    """
    parts = rigidParts(model, joints)
    frames = _referenceFrames(model, reference)
    return _Scene(model, parts, frames, geoset, weld).bones


def _referenceFrames(model, reference):
    if reference is None:
        return poseModel(model, (), vertices=False).frames
    frames = getattr(reference, 'frames', reference)
    frames = np.asarray(frames, dtype=float)
    if frames.shape != (len(model.meshHierarchy), 4, 4):
        raise ValueError('reference must be a Pose or joint frames (N, 4, 4) for this model')
    return frames


# ----------------------------------------------------------------------------------------- test poses
def testPoses(keys: Iterable, *, step: float = 2.0, priority: int = 1, base: Sequence[PoseLayer] = ()):
    """Test poses for ``auditSegmentation``: each KEY on its own at every ``step`` frames.

    Returns ``(label, layers)`` pairs labelled ``name@frame``. ``base`` layers (for example a standing
    KEY at a lower priority) play under each KEY, as the engine fills nodes a KEY leaves alone.
    """
    poses = []
    for key in keys:
        name = getattr(key, 'name', 'key')
        for frame in keyFrameTimes(key, step):
            poses.append((f'{name}@{frame:g}', list(base) + [PoseLayer(key, frame, lowPri=priority)]))
    return poses


# ----------------------------------------------------------------------------------------- the audit
def _majority(values, weights):
    totals: Dict[int, float] = {}
    for value, weight in zip(values, weights):
        totals[int(value)] = totals.get(int(value), 0.0) + float(weight)
    return max(sorted(totals), key=lambda v: totals[v])


def auditSegmentation(model, *, geoset: int = 0, joints=None, reference=None, poses=None,
                      margin: float = 0.004, leverHigh: float = 0.035, leverReview: float = 0.012,
                      facingMax: float = 0.5, islandShare: float = 0.1, minFaces: int = 3,
                      separationHigh: Optional[float] = None, weld: float = 1e-6,
                      detail: str = 'review') -> dict:
    """Find surface cut into the wrong rigid piece. Returns a plain-data report.

    ``joints`` defines the rigid parts (``rigidParts``). ``reference`` is the pose the pieces were cut
    in: a ``Pose`` or joint frames, the rest pose by default. ``poses`` are optional test poses,
    ``(label, layers)`` pairs such as ``testPoses(keys)``; each region then also gets ``separation``,
    the largest distance it moves away from the claiming part in those poses, and ``worst_pose``.

    Distances are JK units with defaults for Kyle-sized characters (about 0.18 units tall): ``margin``
    (how much nearer another bone a face must be), ``leverHigh`` and ``leverReview`` (how far a region
    must reach from its joint for ``high`` and ``review``), ``facingMax`` (largest ``facing`` share a
    reported region may have), ``islandShare`` (largest share of its part's area an island may have
    and still count as small), ``minFaces`` (fewest faces of a reported region or island),
    ``separationHigh`` (with ``poses`` only; off by default: a separation that makes a region ``high``
    whatever its lever) and ``weld`` (welding distance). ``detail`` is the lowest severity listed in
    ``findings`` (``high``, ``review`` or ``info``); the counts cover every severity.

    The report has a ``status`` (``suspect`` with any ``high`` finding, ``review`` with any ``review``
    finding, else ``clean``); ``issue_counts`` by code and ``severity_counts``; at most 32 detailed
    ``findings``, worst first, each naming the part, the meshes and face indices, the claiming part,
    the joint and the measures; ``meshes``, one summary per mesh with the face indices of its ``high``
    and ``review`` findings for selection; ``parts``; and the ``parameters`` used.
    """
    if detail not in SEVERITIES:
        raise ValueError('detail must be high, review or info')
    for name, value in (('margin', margin), ('leverHigh', leverHigh), ('leverReview', leverReview),
                        ('facingMax', facingMax), ('islandShare', islandShare), ('weld', weld),
                        ('separationHigh', 0.0 if separationHigh is None else separationHigh)):
        if not (isinstance(value, (int, float)) and math.isfinite(value) and value >= 0):
            raise ValueError(f'{name} must be a finite number not below zero')
    if weld <= 0:
        raise ValueError('weld must be positive')
    parts = rigidParts(model, joints)
    frames = _referenceFrames(model, reference)
    scene = _Scene(model, parts, frames, geoset, weld)
    nodes = model.meshHierarchy
    poses = None if poses is None else list(poses)
    geometric = scene.geometric
    segParts = np.array([p for p in geometric for _ in range(len(scene.bones[p]))], dtype=np.int64)
    segments = np.concatenate([scene.bones[p] for p in geometric]) if geometric else np.zeros((0, 2, 3))

    records = []
    for p in geometric:
        records.extend(_partRecords(scene, p, segments, segParts, margin))

    if poses is not None:
        _separations(model, scene, records, poses)

    for rec in records:
        rec['severity'] = _severity(rec, leverHigh, leverReview, facingMax, minFaces, islandShare,
                                    separationHigh if poses is not None else None)

    rank = {s: k for k, s in enumerate(SEVERITIES)}
    records.sort(key=lambda r: (rank[r['severity']], -r['lever'], -r['area'], r['part'], r['first']))
    severityCounts = {s: sum(1 for r in records if r['severity'] == s) for s in SEVERITIES}
    issueCounts: Dict[str, int] = {}
    for rec in records:
        issueCounts[rec['code']] = issueCounts.get(rec['code'], 0) + 1
    listed = [r for r in records if rank[r['severity']] <= rank[detail]]
    findings = [_finding(scene, r) for r in listed[:DETAIL_LIMIT]]

    meshSummaries = []
    for m, i in sorted(scene.carrier.items()):
        mesh = scene.meshes[m]
        entry = {'mesh': m, 'name': mesh.name, 'node': i, 'node_name': nodes[i].name, 'part': parts[i],
                 'part_name': nodes[parts[i]].name, 'faces': len(mesh.faces),
                 'high_faces': [], 'review_faces': [], 'info_faces': 0}
        for rec in records:
            faces = rec['faces'].get(m)
            if not faces:
                continue
            if rec['severity'] == 'info':
                entry['info_faces'] += len(faces)
            else:
                entry[rec['severity'] + '_faces'].extend(faces)
        entry['high_faces'] = sorted(set(entry['high_faces']))
        entry['review_faces'] = sorted(set(entry['review_faces']) - set(entry['high_faces']))
        meshSummaries.append(entry)

    partSummaries = []
    for p in sorted(scene.faces):
        data = scene.faces[p]
        members = sorted({int(m) for m in data['mesh']})
        islands = len({int(x) for x in data['island']}) if len(data['island']) else 0
        partSummaries.append({'part': p, 'name': nodes[p].name,
                              'nodes': [i for i, q in enumerate(parts) if q == p], 'meshes': members,
                              'faces': int(len(data['area'])), 'area': round(float(data['area'].sum()), 9),
                              'pieces': islands})

    status = 'suspect' if severityCounts['high'] else 'review' if severityCounts['review'] else 'clean'
    report = {
        'status': status, 'geoset': geoset,
        'reference': 'rest' if reference is None else 'given',
        'parameters': {'margin': margin, 'leverHigh': leverHigh, 'leverReview': leverReview,
                       'facingMax': facingMax, 'islandShare': islandShare, 'minFaces': minFaces,
                       'separationHigh': separationHigh, 'weld': weld, 'detail': detail},
        'issue_counts': issueCounts, 'severity_counts': severityCounts,
        'findings': findings, 'findings_truncated': len(listed) > len(findings),
        'meshes': meshSummaries, 'parts': partSummaries,
        'unplaced_meshes': scene.unplaced, 'shared_meshes': scene.shared,
    }
    if poses is not None:
        report['poses'] = len(poses)
    return report


def _partRecords(scene, p, segments, segParts, margin):
    """Island and region records for part ``p``: faces, claimer, joint and static measures."""
    data = scene.faces[p]
    count = len(data['area'])
    if count == 0:
        return []
    centroid, normal, area = data['centroid'], data['normal'], data['area']
    dist, _ = _segmentPoints(centroid, segments)
    ownMask = segParts == p
    ownDist = np.where(ownMask[None], dist, np.inf)
    otherDist = np.where(ownMask[None], np.inf, dist)
    own = ownDist.min(axis=1)
    bestSeg = otherDist.argmin(axis=1)
    best = otherDist[np.arange(count), bestSeg]
    claimer = np.where(np.isfinite(best), segParts[bestSeg], -1)
    lead = np.where(np.isfinite(best), own - best, -np.inf)

    # Covered faces: inside another part's volume, never counting a part's ancestors.
    covered = np.full(count, -1)
    coverWinding = np.zeros(count)
    ancestors = set(scene.ancestors(p))
    for q in scene.geometric:
        if q == p or q in ancestors:
            continue
        tris = scene.faces[q]['triangles']
        if not len(tris):
            continue
        low = tris.reshape(-1, 3).min(axis=0)
        high = tris.reshape(-1, 3).max(axis=0)
        inside = np.all((centroid >= low) & (centroid <= high), axis=1) & (area > 0)
        sel = np.where(inside)[0]
        if not len(sel):
            continue
        w = windingNumbers(centroid[sel], tris)
        better = (w > 0.5) & (w > coverWinding[sel])
        covered[sel[better]] = q
        coverWinding[sel[better]] = w[better]

    # Islands: the main piece is the one with the most area.
    islandArea: Dict[int, float] = {}
    for label, a in zip(data['island'], area):
        islandArea[int(label)] = islandArea.get(int(label), 0.0) + float(a)
    main = max(sorted(islandArea), key=lambda label: (islandArea[label], -label))
    total = float(area.sum()) or 1.0
    records = []
    mainFaces = data['island'] == main
    mainTriangles = data['triangles'][mainFaces[data['triFace']]] if len(data['triFace']) else data['triangles']

    def ownSegs():
        return segments[segParts == p]

    def measures(faces, claim):
        """Shared measures for a set of faces claimed by part ``claim``."""
        joint = scene.branch(p, claim)
        lever = float(np.linalg.norm(centroid[faces] - scene.origin[joint], axis=1).max())
        dClaim, nearClaim = _segmentPoints(centroid[faces], segments[segParts == claim])
        pickClaim = nearClaim[np.arange(len(faces)), dClaim.argmin(axis=1)]
        dOwn, nearOwn = _segmentPoints(centroid[faces], ownSegs())
        pickOwn = nearOwn[np.arange(len(faces)), dOwn.argmin(axis=1)]
        toClaim = centroid[faces] - pickClaim
        toOwn = centroid[faces] - pickOwn
        facing = (np.sum(normal[faces] * toClaim, axis=1) < 0) & (np.sum(normal[faces] * toOwn, axis=1) > 0)
        a = area[faces]
        weight = float(a.sum()) or 1.0
        return {
            'claimed_by': int(claim), 'relation': scene.relation(p, claim), 'joint': int(joint),
            'lever': lever, 'facing': float(a[facing].sum() / weight),
            'nearer': float(a[lead[faces] >= margin].sum() / weight),
            'enclosed': float(a[covered[faces] >= 0].sum() / weight),
            'margin': float(np.max(lead[faces])) if len(faces) else 0.0,
        }

    def byMesh(faces):
        out: Dict[int, List[int]] = {}
        for f in faces:
            out.setdefault(int(data['mesh'][f]), []).append(int(data['index'][f]))
        return {m: sorted(v) for m, v in out.items()}

    for label in sorted(islandArea):
        if label == main:
            continue
        faces = np.where(data['island'] == label)[0]
        a = area[faces]
        weight = float(a.sum())
        if weight <= 0:
            continue                      # degenerate faces only: nothing is drawn
        centre = (centroid[faces] * a[:, None]).sum(axis=0) / weight
        d, _ = _segmentPoints(centre[None], segments)
        dOwn = float(d[0, segParts == p].min())
        others = np.where(segParts != p)[0]
        if len(others):
            k = others[d[0, others].argmin()]
            claim, dOther = int(segParts[k]), float(d[0, k])
        else:
            claim, dOther = -1, math.inf
        slots = np.concatenate([np.arange(data['offsets'][f], data['offsets'][f + 1]) for f in faces])
        used = np.unique(data['corners'][slots])
        points = data['world'][used]
        gap = float(_pointTriangleDistance(points, mainTriangles).min()) if len(mainTriangles) else math.inf
        # Another part's surface nearer than the island's own main piece: the piece it sits on.
        contact, touching = (gap if math.isfinite(gap) else math.inf), -1
        for q in scene.geometric:
            tris = scene.faces[q]['triangles']
            if q == p or not len(tris) or not math.isfinite(contact):
                continue
            low, high = points.min(axis=0) - contact, points.max(axis=0) + contact
            near = np.all((tris.max(axis=1) >= low) & (tris.min(axis=1) <= high), axis=1)
            if near.any():
                d = float(_pointTriangleDistance(points, tris[near]).min())
                if d < contact:
                    contact, touching = d, q
        rec = {'code': 'stray_island', 'part': p, 'faces': byMesh(faces), 'count': int(len(faces)),
               'area': weight, 'share': weight / total, 'gap': gap,
               'touching': touching, 'contact': contact if touching >= 0 else None,
               'island_lead': dOwn - dOther, 'island_claimed': dOther + margin <= dOwn,
               'inside_main': float(windingNumbers(centre[None], mainTriangles)[0]) if len(mainTriangles) else 0.0,
               'first': int(faces.min()), 'face_ids': faces}
        if claim >= 0:
            rec.update(measures(faces, claim))
            rec['margin'] = dOwn - dOther
        else:
            rec.update({'claimed_by': -1, 'relation': None, 'joint': p, 'lever': 0.0, 'facing': 0.0,
                        'nearer': 0.0, 'enclosed': 0.0, 'margin': 0.0})
        records.append(rec)

    # Regions on the main piece: connected faces with evidence, one joint (branch) per region.
    claim = np.where(lead > 0, claimer, covered)
    branch = np.array([scene.branch(p, int(c)) if c >= 0 else -1 for c in claim], dtype=np.int64)
    seed = mainFaces & (((lead >= margin) & (claimer >= 0)) | (covered >= 0))
    grow = mainFaces & (claim >= 0)
    adjacency: Dict[int, List[int]] = {}
    for f in np.where(grow)[0]:
        for v in data['welded'][data['corners'][data['offsets'][f]:data['offsets'][f + 1]]]:
            adjacency.setdefault(int(v), []).append(int(f))
    seen = np.zeros(count, dtype=bool)
    for s in np.where(seed)[0]:
        if seen[s]:
            continue
        seen[s] = True
        stack, region = [int(s)], []
        while stack:
            f = stack.pop()
            region.append(f)
            for v in data['welded'][data['corners'][data['offsets'][f]:data['offsets'][f + 1]]]:
                for g in adjacency.get(int(v), ()):
                    if not seen[g] and branch[g] == branch[s]:
                        seen[g] = True
                        stack.append(g)
        faces = np.array(sorted(region), dtype=np.int64)
        who = _majority(claim[faces], area[faces])
        rec = {'code': 'foreign_surface', 'part': p, 'faces': byMesh(faces), 'count': int(len(faces)),
               'area': float(area[faces].sum()), 'first': int(faces.min()), 'face_ids': faces}
        rec.update(measures(faces, who))
        if rec['enclosed'] > rec['nearer']:
            rec['code'] = 'covered_surface'
        records.append(rec)
    return records


def _separations(model, scene, records, poses):
    """Largest displacement of each record's faces relative to its claiming part over ``poses``."""
    work = []
    for rec in records:
        rec['separation'] = 0.0
        rec['worst_pose'] = None
        claim = rec.get('claimed_by', -1)
        if claim < 0:
            continue
        data = scene.faces[rec['part']]
        faces = rec['face_ids']
        nodesOf = data['node'][faces]
        world = data['centroid'][faces]
        # Face centroids in their carrying node's mesh frame, then relative to the claiming joint at rest.
        restRel = np.zeros((len(faces), 3))
        mesh_local = np.zeros((len(faces), 3))
        claimInv = np.linalg.inv(scene.frames[claim])
        for node in np.unique(nodesOf):
            sel = nodesOf == node
            inv = np.linalg.inv(scene.carried[node])
            mesh_local[sel] = world[sel] @ inv[:3, :3].T + inv[:3, 3]
            restRel[sel] = world[sel] @ claimInv[:3, :3].T + claimInv[:3, 3]
        work.append((rec, claim, nodesOf, mesh_local, restRel))
    if not work:
        return
    for label, layers in poses:
        posed = poseModel(model, layers, vertices=False)
        for rec, claim, nodesOf, mesh_local, restRel in work:
            inv = np.linalg.inv(posed.frames[claim])
            moved = np.zeros_like(mesh_local)
            for node in np.unique(nodesOf):
                sel = nodesOf == node
                frame = inv @ posed.meshFrames[node]
                moved[sel] = mesh_local[sel] @ frame[:3, :3].T + frame[:3, 3]
            d = float(np.linalg.norm(moved - restRel, axis=1).max())
            if d > rec['separation']:
                rec['separation'] = d
                rec['worst_pose'] = label


def _severity(rec, leverHigh, leverReview, facingMax, minFaces, islandShare, separationHigh):
    if rec['code'] == 'stray_island' and (rec['share'] > islandShare or not rec['island_claimed']):
        return 'info'                     # a large piece, or one nearest its own bone
    if rec['claimed_by'] < 0 or rec['facing'] > facingMax or rec['count'] < minFaces:
        return 'info'                     # the own part's skin facing the other part, or a sliver
    if rec['relation'] == 'ancestor':
        # A child's own end near its parent's bone is how rigid pieces overlap at a joint.
        return 'review' if rec['lever'] >= leverHigh else 'info'
    if rec['lever'] >= leverHigh:
        return 'high'
    if separationHigh is not None and rec.get('separation', 0.0) >= separationHigh:
        return 'high'
    if rec['lever'] >= leverReview:
        return 'review'
    return 'info'


def _finding(scene, rec) -> dict:
    nodes = scene.nodes
    out = {'code': rec['code'], 'severity': rec['severity'], 'part': rec['part'],
           'part_name': nodes[rec['part']].name,
           'meshes': [{'mesh': m, 'name': scene.meshes[m].name, 'faces': faces}
                      for m, faces in sorted(rec['faces'].items())],
           'face_count': rec['count'], 'area': round(rec['area'], 9)}
    claim = rec.get('claimed_by', -1)
    if claim >= 0:
        out.update({'claimed_by': claim, 'claimed_by_name': nodes[claim].name, 'relation': rec['relation'],
                    'joint': rec['joint'], 'joint_name': nodes[rec['joint']].name})
    for key in ('lever', 'margin'):
        out[key] = round(float(rec[key]), 6)
    for key in ('facing', 'nearer', 'enclosed'):
        out[key] = round(float(rec[key]), 3)
    if rec['code'] == 'stray_island':
        out.update({'share': round(rec['share'], 4), 'gap': round(rec['gap'], 6),
                    'inside_main': round(rec['inside_main'], 3)})
        if rec['touching'] >= 0:
            out.update({'touching': rec['touching'], 'touching_name': nodes[rec['touching']].name,
                        'contact': round(rec['contact'], 6)})
    if 'separation' in rec:
        out['separation'] = round(rec['separation'], 6)
        out['worst_pose'] = rec['worst_pose']
    return out


# ----------------------------------------------------------------------------------------- piece colours
_GOLDEN = 0.6180339887498949


def _indexColour(index: int) -> Tuple[float, float, float]:
    hue = (0.1 + index * _GOLDEN) % 1.0
    saturation = (0.85, 0.55, 0.7)[index % 3]
    value = (0.95, 0.72)[index % 2]
    return colorsys.hsv_to_rgb(hue, saturation, value)


def pieceColours(model, *, by: str = 'node', joints=None, geoset: int = 0) -> List[dict]:
    """A stable, distinct solid colour per hierarchy node, mesh or rigid part, as plain data.

    For the diagnostic that found Darth Lilitari R9.2's second shoulder: paint each piece one colour and
    see which piece a lump belongs to. ``by='node'`` gives one entry per hierarchy node, ``'mesh'`` one
    per mesh of ``geoset`` and ``'part'`` one per node coloured by its rigid part (``rigidParts`` with
    ``joints``). Each entry has ``index``, ``name``, ``rgb`` (three sRGB floats from 0 to 1) and ``hex``.

    A colour depends only on its index (golden-ratio hue steps with alternating saturation and value),
    so the same node or mesh keeps its colour across models and runs. Indices one to three apart get
    clearly different hues; indices that are Fibonacci numbers apart (8, 13, 21, 34, 55) get close hues
    but different saturation or value.
    """
    if by == 'node':
        items = [(i, n.name, i) for i, n in enumerate(model.meshHierarchy)]
    elif by == 'mesh':
        meshes = model.geosets[geoset].meshes if 0 <= geoset < len(model.geosets) else []
        items = [(m, mesh.name, m) for m, mesh in enumerate(meshes)]
    elif by == 'part':
        parts = rigidParts(model, joints)
        items = [(i, n.name, parts[i]) for i, n in enumerate(model.meshHierarchy)]
    else:
        raise ValueError("by must be 'node', 'mesh' or 'part'")
    out = []
    for index, name, key in items:
        rgb = _indexColour(key)
        entry = {'index': index, 'name': name, 'rgb': tuple(round(c, 6) for c in rgb),
                 'hex': '#' + ''.join(f'{int(round(c * 255)):02x}' for c in rgb)}
        if by == 'part':
            entry['part'] = key
        out.append(entry)
    return out
