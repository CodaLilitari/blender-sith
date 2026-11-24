# SPDX-License-Identifier: GPL-3.0-or-later
"""Find joints that open when KEYs move a character's rigid parts apart.

A JK/MotS character is a hierarchy of rigid meshes. Each KEY track places one node in its
parent's frame (``rdPuppet_BuildJointMatrices``; ``sith.audit.pose`` reproduces it); no mesh
bends or stretches. A seam that is closed in the pose the parts were cut in opens when a pose
moves the child part relative to the parent part in a way the cut did not allow for. Most often a
KEY moves a joint off its seat (a translation); sometimes a rotation tips one rim off the other.
Kyle's meshes overlap generously, so his KEYs can do this unseen; close-fitting parts cannot.

``auditArticulation`` looks for such openings the way Red's seam-ray sweep did:

1. Joints. A node that no audited KEY drives rides with its parent, so the hierarchy falls into
   rigid parts. Each part with meshes meets the nearest part above it that has meshes. Parts with
   no such part above them meet the first of them: Kyle's hip and torso both hang from the root
   through meshless carriers, and this rule joins them at the waist. Kyle's node names label the
   joints when present. Callers can list joints instead (``KYLE_JOINTS`` is Red's list); then the
   listed nodes start the parts.
2. Anchors. In a reference pose (the pose the parts were cut in; default: the rest pose), rays
   leave the joint's pivot (the child node's origin) across the limb axis and stop at the nearest
   polygon of either part. An anchor sits at a fraction of each hit distance. At joints named in
   ``AnchorSettings.membraneJoints`` (the waist by default) where both parts end in matching open
   rims near the pivot, the anchors are instead rim points pulled towards the centre: points on
   the membrane between the rims. Each anchor is kept twice, rigid with the parent part and rigid
   with the child part. Anchors that already see daylight in the reference pose are dropped.
3. Daylight. In a pose the two copies of an anchor part. The line of sight through their midpoint
   along a camera direction is tested against every polygon of the whole posed model. A line that
   meets no polygon is daylight through the joint. Counts are lines of sight per camera.

``seatOffsets`` reports the usual cause next to it: how far each KEY moves each node from its
seat.

Limits. A pose is a static evaluation of one KEY (or of given layers) at one time: no puppet
timing, looping, fades or aim pitch. By default a KEY is sampled up to ``FRAMES - 1``, as Red's
sweep was, so the last interval up to ``FRAMES`` is not; ``toEnd`` samples it too. Every polygon
counts as cover whichever way it faces, although the engine skips single-sided faces seen from
behind (``rdModel3_DrawMesh``), so an opening seen into an open shell can be missed. The sweep
finds gaps, not one part pushing through another, and not dark polygons that read as holes.
Anchor placement is a heuristic with lengths tuned for Kyle-scale characters
(``AnchorSettings``). Lines are tested exactly, where Red's sweep sampled them at the centres of
360-pixel renders, so borderline counts differ a little from his.
"""

from dataclasses import asdict, dataclass, field, replace
import math
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np

from ..key.keyLoader import loadKey
from .pose import (LocalPose, Pose, PoseLayer, blendLocalPose, jointFrames, keyFrameTimes, keyTrack,
                   meshFrames, posedMeshVertices, poseModel, restPose, sampleTrack)

__all__ = [
    'AnchorSettings', 'Joint', 'ReferencePose', 'SWEEP_CAMERAS', 'KYLE_JOINTS', 'KYLE_JOINT_LABELS',
    'articulationJoints', 'auditArticulation', 'cameraDirection', 'loadKeyFolder', 'referencePose',
    'seatOffsets',
]

DETAIL_LIMIT = 32

# Red's sweep cameras: (azimuth, elevation) in degrees, every 45 degrees round the figure.
SWEEP_CAMERAS: Tuple[Tuple[float, float], ...] = (
    (0.0, 0.0), (45.0, 12.0), (90.0, 0.0), (135.0, 22.0),
    (180.0, 0.0), (225.0, 22.0), (270.0, 0.0), (315.0, 12.0))

# Red's sweep joints for Kyle's skeleton: (label, parent node, child node), in his order.
KYLE_JOINTS: Tuple[Tuple[str, str, str], ...] = (
    ('waist', 'k_hip', 'k_torso'), ('neck', 'k_torso', 'k_neck'), ('head', 'k_neck', 'k_head'),
    ('r shoulder', 'k_torso', 'k_rshouldr'), ('l shoulder', 'k_torso', 'k_lshouldr'),
    ('r elbow', 'k_rshouldr', 'k_rforearm'), ('l elbow', 'k_lshouldr', 'k_lforearm'),
    ('r wrist', 'k_rforearm', 'k_rhand'), ('l wrist', 'k_lforearm', 'k_lhand'),
    ('r hip', 'k_hip', 'k_rthigh'), ('l hip', 'k_hip', 'k_lthigh'),
    ('r knee', 'k_rthigh', 'k_rcalf'), ('l knee', 'k_lthigh', 'k_lcalf'),
    ('r ankle', 'k_rcalf', 'k_rfoot'), ('l ankle', 'k_lcalf', 'k_lfoot'))

# Labels for derived joints whose child part starts at one of Kyle's nodes.
KYLE_JOINT_LABELS: Dict[str, str] = {child: label for label, _, child in KYLE_JOINTS}

READING_RULE = (
    'A count is a lead, not a verdict. Counts are lines of sight summed over the cameras: one or '
    'two lines in a pose are usually a grazing sliver at a rim; dozens are an opening you can see. '
    'A line into the crease of a bent joint counts too (on Mandalorian Red, knee counts in the '
    'hundreds were rays into the crease of a bent knee). Look at each flagged pose in a close-up '
    'before changing a model or a KEY.')

_LENGTHS = ('nearRadius', 'axisRadius', 'maxRadius', 'rimReach', 'rimMinExtent', 'rimMaxExtent',
            'rimMatch')


@dataclass(frozen=True)
class AnchorSettings:
    """Where the anchors go. Lengths are JK units; the defaults are Red's, for Kyle-scale characters.

    - ``rays``: radial rays cast across the limb axis from each pivot.
    - ``fraction``: an anchor sits at this fraction of its ray's hit distance (inside the wall).
    - ``nearRadius``: only polygons whose centre lies this close to the pivot can stop a ray.
    - ``axisRadius``: vertices this close to the pivot set the limb axis (parent side to child side).
    - ``maxRadius``: a ray that travels further than this found no limb wall and is ignored.
    - ``minAnchors``: with fewer hits than this the joint is not measured.
    - ``outlierRatio``: rays longer than this times the median escaped the limb and are dropped.
    - ``membraneJoints``: joint labels where open rims are looked for. Red's sweep did this at the
      waist only, because the limb joints of his characters are capped.
    - ``rimReach``, ``rimMinPoints``, ``rimMinExtent``, ``rimMaxExtent``, ``rimRoundness``: an open
      rim is a connected chain of boundary edges with at least ``rimMinPoints`` vertices, all within
      ``rimReach`` of the pivot, an extent between the two limits, and points that surround their
      centre (the mean of the unit directions from the centre is at most ``rimRoundness`` long).
    - ``rimMatch``: the two rims must lie closer than this on average to be paired.
    - ``rimPull``: membrane anchors sit this fraction of the way from each rim point to the centre.
    - ``weld``: vertices this close count as one when rims are traced (not scaled).
    """
    rays: int = 16
    fraction: float = 0.75
    nearRadius: float = 0.045
    axisRadius: float = 0.03
    maxRadius: float = 0.03
    minAnchors: int = 6
    outlierRatio: float = 1.8
    membraneJoints: Tuple[str, ...] = ('waist',)
    rimReach: float = 0.022
    rimMinPoints: int = 8
    rimMinExtent: float = 0.008
    rimMaxExtent: float = 0.05
    rimMatch: float = 0.006
    rimRoundness: float = 0.35
    rimPull: float = 0.2
    weld: float = 3e-6

    def __post_init__(self):
        if isinstance(self.membraneJoints, str):
            raise ValueError('membraneJoints is a sequence of joint labels, not one string')
        object.__setattr__(self, 'membraneJoints', tuple(self.membraneJoints))
        for name in _LENGTHS + ('weld', 'outlierRatio'):
            value = getattr(self, name)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f'{name} must be a positive number')
        if not 0.0 < self.fraction <= 1.0 or not 0.0 <= self.rimPull < 1.0:
            raise ValueError('fraction must be in (0, 1] and rimPull in [0, 1)')
        if self.rays < 3 or self.minAnchors < 1 or self.rimMinPoints < 3 or self.rimRoundness <= 0:
            raise ValueError('rays, minAnchors, rimMinPoints and rimRoundness are too small')

    def scaled(self, factor: float) -> 'AnchorSettings':
        """These settings for a character ``factor`` times Kyle's size (lengths scale, counts do not)."""
        if not (math.isfinite(factor) and factor > 0):
            raise ValueError('factor must be a positive number')
        return replace(self, **{name: getattr(self, name) * factor for name in _LENGTHS})


@dataclass(frozen=True)
class Joint:
    """Two rigid parts that meet at the child node's origin.

    ``parent`` and ``child`` are the node indices whose frames carry the two copies of each anchor.
    ``parentNodes`` and ``childNodes`` are the rigid parts (nodes that move together in the
    audited KEYs). ``seatNodes`` are the driven nodes whose tracks place the child part relative to
    the parent part: read their seat offsets next to this joint.
    """
    label: str
    parent: int
    child: int
    parentNodes: Tuple[int, ...]
    childNodes: Tuple[int, ...]
    seatNodes: Tuple[int, ...]


@dataclass
class ReferencePose(Pose):
    """A ``Pose`` with a short description of how it was built, for reports."""
    description: str = 'rest pose'


def cameraDirection(azimuth: float, elevation: float) -> np.ndarray:
    """Unit vector from the model to a camera, in degrees.

    Azimuth 0 looks at the character's front (+y), 90 at its right side (+x); elevation raises the
    camera above the horizontal plane. Only the direction matters: lines of sight are parallel.
    """
    a, e = math.radians(float(azimuth)), math.radians(float(elevation))
    return np.array([math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)])


def loadKeyFolder(path, *, errors: Optional[List[str]] = None) -> Dict[str, object]:
    """Text or binary KEYs in a folder, by file name, sorted without regard to case.

    A KEY that fails to load raises, unless ``errors`` is a list: then its file name and the
    error are appended there and the KEY is skipped.
    """
    keys: Dict[str, object] = {}
    for item in sorted(Path(path).iterdir(), key=lambda p: (p.name.casefold(), p.name)):
        if not item.is_file() or item.suffix.casefold() != '.key':
            continue
        try:
            keys[item.name] = loadKey(item)
        except Exception as exc:  # each KEY's failure is reported, not fatal, when asked
            if errors is None:
                raise
            errors.append(f'{item.name}: {exc}')
    return keys


def referencePose(model, key=None, frame: float = 0.0, *, zeroRotations: bool = False,
                  geoset: int = 0) -> ReferencePose:
    """The pose the parts were cut in, for placing anchors and reading seats.

    Without a KEY this is the rest pose. With one, every node the KEY drives takes the KEY's
    position at ``frame`` and its rotation, or zero rotation with ``zeroRotations``; every other
    node keeps its rest position and rotation. Red's builds of Kyle-skeleton characters are cut
    with the standing KEY's seats and zero rotation, which is the reference his sweep used:
    ``referencePose(model, standKey, 0, zeroRotations=True)``. The cut lint's
    ``sith.audit.segmentation.zeroRotationPose(model, stand=standKey, frame=0)`` builds the same
    joint frames, mesh frames and vertices; either can be passed to either audit.
    """
    if key is None:
        if zeroRotations:
            raise ValueError('zeroRotations applies to the nodes a KEY drives; give the KEY')
        pose = restPose(model, geoset=geoset)
        return ReferencePose(pose.local, pose.frames, pose.meshFrames, pose.vertices, 'rest pose')
    rest = blendLocalPose(model, [])
    positions, rotations = rest.positions.copy(), rest.rotations.copy()
    restWeight = rest.restWeight.copy()
    drivenBy = list(rest.drivenBy)
    for i in range(len(model.meshHierarchy)):
        track = keyTrack(key, i)
        if track is None:
            continue
        position, rotation = sampleTrack(track, float(frame))
        positions[i] = position
        rotations[i] = 0.0 if zeroRotations else rotation
        restWeight[i] = 0.0
        drivenBy[i] = (0,)
    local = LocalPose(positions, rotations, restWeight, drivenBy)
    frames = jointFrames(model, local)
    name = getattr(key, 'name', 'KEY')
    rule = 'zero rotation' if zeroRotations else 'its rotations'
    description = f'{name} frame {float(frame):g}: KEY positions with {rule}; rest elsewhere'
    return ReferencePose(local, frames, meshFrames(model, frames),
                         posedMeshVertices(model, frames, geoset), description)


# ----------------------------------------------------------------------------------- inputs

@dataclass
class _Source:
    name: str
    layers: Tuple[PoseLayer, ...]

    def keys(self):
        return [layer.key for layer in self.layers]

    def times(self, step: float, toEnd: bool = False) -> List[float]:
        return keyFrameTimes(self.layers[0].key, step, toEnd=toEnd)

    def at(self, time: float) -> List[PoseLayer]:
        return [replace(layer, frame=float(layer.frame) + time) for layer in self.layers]


def _isKey(value) -> bool:
    return hasattr(value, 'nodes') and hasattr(value, 'numFrames') and hasattr(value, 'numJoints')


def _sources(keys) -> List[_Source]:
    """KEYs played alone, or sequences of ``PoseLayer`` played together."""
    if _isKey(keys) or isinstance(keys, PoseLayer):
        items = [(None, keys)]
    elif isinstance(keys, Mapping):
        items = list(keys.items())
    else:
        items = [(None, item) for item in keys]
    out = []
    for name, item in items:
        if _isKey(item):
            layers = (PoseLayer(item, 0.0),)
        else:
            layers = (item,) if isinstance(item, PoseLayer) else tuple(item)
            if not layers or not all(isinstance(layer, PoseLayer) for layer in layers):
                raise TypeError('Give KEYs, or sequences of PoseLayer for KEYs played together')
        if name is None:
            name = '+'.join(str(getattr(layer.key, 'name', 'KEY')) for layer in layers)
        out.append(_Source(str(name), layers))
    return out


def _cameraList(cameras) -> List[Tuple[float, float]]:
    cams = [(float(az), float(el)) for az, el in (SWEEP_CAMERAS if cameras is None else cameras)]
    if not cams or not all(math.isfinite(v) for cam in cams for v in cam):
        raise ValueError('Give at least one camera as finite (azimuth, elevation) degrees')
    return cams


# ------------------------------------------------------------------------------- hierarchy

def _parentOf(nodes, i) -> int:
    parent = nodes[i].parentIdx
    return parent if parent is not None and 0 <= parent < len(nodes) else -1


def _ancestors(nodes, i) -> List[int]:
    chain = [i]
    while _parentOf(nodes, chain[-1]) >= 0:
        chain.append(_parentOf(nodes, chain[-1]))
        if len(chain) > len(nodes):
            raise ValueError('3DO hierarchy has a parent cycle')
    return chain


def _drivenNodes(model, keys) -> set:
    count = len(model.meshHierarchy)
    driven = set()
    for key in keys:
        for track in key.nodes:
            if 0 <= track.idx < count and keyTrack(key, track.idx) is not None:
                driven.add(track.idx)
    return driven


def _partTops(model, driven) -> List[int]:
    """For each node, the top node of its rigid part (itself when driven or a root)."""
    nodes = model.meshHierarchy
    tops = []
    for i in range(len(nodes)):
        for j in _ancestors(nodes, i):
            if j in driven or _parentOf(nodes, j) < 0:
                tops.append(j)
                break
    return tops


def _meshCarriers(model, geoset: int) -> Dict[int, int]:
    """Mesh index -> the node that carries it (the first one, as ``sith.audit.pose`` does)."""
    if model.geosets and not 0 <= geoset < len(model.geosets):
        raise ValueError(f'Model has no geoset {geoset}')
    meshes = model.geosets[geoset].meshes if model.geosets else []
    carriers: Dict[int, int] = {}
    for i, node in enumerate(model.meshHierarchy):
        m = node.meshIdx
        if m is not None and 0 <= m < len(meshes) and m not in carriers:
            carriers[m] = i
    return carriers


def _nodeIndex(model, ref) -> int:
    nodes = model.meshHierarchy
    if isinstance(ref, (int, np.integer)) and not isinstance(ref, bool):
        if not 0 <= int(ref) < len(nodes):
            raise ValueError(f'Node index {ref} is outside the hierarchy')
        return int(ref)
    matches = [i for i, node in enumerate(nodes) if node.name.casefold() == str(ref).casefold()]
    if len(matches) != 1:
        raise ValueError(f'Node name {ref!r} matches {len(matches)} nodes; give its index')
    return matches[0]


def articulationJoints(model, keys=(), joints=None, *, geoset: int = 0) -> List[Joint]:
    """The joints the audit measures, from the hierarchy and the KEYs that will drive it.

    Nodes no KEY drives ride with their parent; with no KEYs every node counts as driven. Each
    rigid part with meshes meets the nearest part above it with meshes; parts with none above them
    meet the first of them.

    ``joints`` replaces the derivation with (parent, child) or (label, parent, child) entries, by
    node name or index. Then the listed nodes start the parts: every other node rides with the
    nearest listed node above it. ``KYLE_JOINTS`` lists Red's 15 joints for Kyle's skeleton, whose
    parts are Kyle's pieces with everything hung under them, as in his sweep.
    """
    nodes = model.meshHierarchy
    keyList = [k for source in _sources(keys) for k in source.keys()] if keys is not None else []
    driven = _drivenNodes(model, keyList) if keyList else set(range(len(nodes)))

    def label(parent, child):
        name = nodes[child].name.casefold()
        return KYLE_JOINT_LABELS.get(name, f'{nodes[parent].name}-{nodes[child].name}')

    listed = None
    if joints is not None:
        listed = []
        for entry in joints:
            entry = tuple(entry)
            if len(entry) == 3:
                text, parent, child = str(entry[0]), _nodeIndex(model, entry[1]), _nodeIndex(model, entry[2])
            elif len(entry) == 2:
                parent, child = _nodeIndex(model, entry[0]), _nodeIndex(model, entry[1])
                text = label(parent, child)
            else:
                raise ValueError('A joint is (parent, child) or (label, parent, child)')
            if parent == child:
                raise ValueError(f'Joint {text!r} names the same node twice')
            listed.append((text, parent, child))
    tops = _partTops(model, driven if listed is None else {n for _, p, c in listed for n in (p, c)})
    members: Dict[int, List[int]] = {}
    for i, top in enumerate(tops):
        members.setdefault(top, []).append(i)

    def make(text, parent, child):
        a, b = _ancestors(nodes, child), _ancestors(nodes, parent)
        above = set(b)
        common = next((x for x in a if x in above), None)
        path = (a[:a.index(common)] + b[:b.index(common)]) if common is not None else a + b
        return Joint(text, parent, child, tuple(members[tops[parent]]), tuple(members[tops[child]]),
                     tuple(sorted(x for x in set(path) if x in driven)))

    if listed is not None:
        return [make(*entry) for entry in listed]

    carried = set(_meshCarriers(model, geoset).values())
    hasMesh = {top for top, part in members.items() if any(i in carried for i in part)}
    found, topLevel = [], []
    for top in sorted(hasMesh):
        above = _parentOf(nodes, top)
        while above >= 0 and tops[above] not in hasMesh:
            above = _parentOf(nodes, tops[above])
        if above >= 0:
            found.append(make(label(tops[above], top), tops[above], top))
        else:
            topLevel.append(top)
    found += [make(label(topLevel[0], top), topLevel[0], top) for top in topLevel[1:]]
    order = {name: k for k, name in enumerate(KYLE_JOINT_LABELS.values())}
    return sorted(found, key=lambda j: (order.get(j.label, len(order)), j.child))


# -------------------------------------------------------------------------------- geometry

class _Geometry:
    """One geoset as fan triangles, with each vertex's carrying node."""

    def __init__(self, model, geoset: int):
        self.carriers = _meshCarriers(model, geoset)
        meshes = model.geosets[geoset].meshes if model.geosets else []
        local, owner, tris, triMesh = [], [], [], []
        self.polygons: Dict[int, List[List[int]]] = {}
        self.skippedFaces = 0
        base = 0
        for m, node in sorted(self.carriers.items(), key=lambda item: item[1]):
            verts = np.array([tuple(v) for v in meshes[m].vertices], dtype=float).reshape(-1, 3)
            polygons = []
            for face in meshes[m].faces:
                idx = [int(k) for k in face.vertexIdxs]
                if len(idx) < 3 or min(idx) < 0 or max(idx) >= len(verts):
                    self.skippedFaces += 1
                    continue
                polygon = [base + k for k in idx]
                polygons.append(polygon)
                tris += [(polygon[0], polygon[k], polygon[k + 1]) for k in range(1, len(polygon) - 1)]
                triMesh += [m] * (len(polygon) - 2)
            self.polygons[m] = polygons
            local.append(verts)
            owner.append(np.full(len(verts), node, dtype=np.int64))
            base += len(verts)
        self.local = np.concatenate(local) if local else np.zeros((0, 3))
        self.owner = np.concatenate(owner) if owner else np.zeros(0, dtype=np.int64)
        self.tris = np.array(tris, dtype=np.int64).reshape(-1, 3)
        self.triMesh = np.array(triMesh, dtype=np.int64)

    def world(self, carried: np.ndarray) -> np.ndarray:
        """World vertices from mesh frames ``F_i * T(pivot_i)``."""
        frames = carried[self.owner]
        return np.einsum('vij,vj->vi', frames[:, :3, :3], self.local) + frames[:, :3, 3]

    def meshesOf(self, nodes: Iterable[int]) -> List[int]:
        nodes = set(nodes)
        return [m for m, node in self.carriers.items() if node in nodes]


def _rayNearest(origin, direction, tris) -> float:
    """Smallest t > 1e-7 where ``origin + t * direction`` meets a triangle; -1 when none."""
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    p = np.cross(direction, e2)
    det = np.einsum('ij,ij->i', e1, p)
    ok = np.abs(det) > 1e-14
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = origin - tris[:, 0]
    u = np.einsum('ij,ij->i', s, p) * inv
    q = np.cross(s, e1)
    v = (q @ direction) * inv
    t = np.einsum('ij,ij->i', e2, q) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-7)
    return float(t[hit].min()) if hit.any() else -1.0


def _planeBasis(direction) -> np.ndarray:
    """(3, 2) orthonormal basis of the plane across ``direction``."""
    d = np.asarray(direction, dtype=float) / np.linalg.norm(direction)
    helper = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    r = np.cross(helper, d)
    r /= np.linalg.norm(r)
    return np.stack([r, np.cross(d, r)], axis=1)


def _inside(x, y, ti, q, span) -> np.ndarray:
    """Point-in-triangle in the plane, edges included (the barycentric rule of a rasteriser).

    ``x`` and ``y`` hold each triangle's corner coordinates, ``ti`` picks a triangle for each point
    ``q``, and ``span`` is each triangle's larger bounding-box side: a triangle seen edge-on covers
    nothing.
    """
    ax, ay, bx, by, cx, cy = x[ti, 0], y[ti, 0], x[ti, 1], y[ti, 1], x[ti, 2], y[ti, 2]
    qx, qy = q[:, 0] - cx, q[:, 1] - cy
    den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    ok = np.abs(den) > 1e-14 * span[ti] ** 2
    den = np.where(ok, den, 1.0)
    w0 = ((by - cy) * qx + (cx - bx) * qy) / den
    w1 = ((cy - ay) * qx + (ax - cx) * qy) / den
    return ok & (w0 >= -1e-9) & (w1 >= -1e-9) & (1.0 - w0 - w1 >= -1e-9)


_PAIR_CHUNK = 1 << 20


def _coveredPlanar(xy: np.ndarray, triangles: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """For each 2D point, whether some triangle of 2D vertices ``xy`` contains it.

    Triangles are culled by their bounding boxes against a grid over the points, so the work
    follows the triangles near the points rather than every triangle times every point.
    """
    hit = np.zeros(len(pts), dtype=bool)
    if not len(pts) or not len(triangles):
        return hit
    x, y = xy[:, 0][triangles], xy[:, 1][triangles]
    lo = np.stack([np.minimum(np.minimum(x[:, 0], x[:, 1]), x[:, 2]),
                   np.minimum(np.minimum(y[:, 0], y[:, 1]), y[:, 2])], axis=1)
    hi = np.stack([np.maximum(np.maximum(x[:, 0], x[:, 1]), x[:, 2]),
                   np.maximum(np.maximum(y[:, 0], y[:, 1]), y[:, 2])], axis=1)
    span = np.maximum(hi[:, 0] - lo[:, 0], hi[:, 1] - lo[:, 1])
    margin = (span * 1e-7 + 1e-15)[:, None]
    lo, hi = lo - margin, hi + margin
    pmin, pmax = pts.min(axis=0), pts.max(axis=0)
    ti = np.nonzero(np.all(hi >= pmin, axis=1) & np.all(lo <= pmax, axis=1))[0]
    if not len(ti):
        return hit
    lo, hi = lo[ti], hi[ti]
    extent = float((pmax - pmin).max())
    cell = max(float(np.median(span[ti])), extent / 1024.0, 1e-12)
    dims = np.floor((pmax - pmin) / cell).astype(np.int64) + 1
    pkey = np.minimum(np.floor((pts - pmin) / cell).astype(np.int64), dims - 1)
    pkey = pkey[:, 0] * dims[1] + pkey[:, 1]
    order = np.argsort(pkey, kind='stable')
    keys, start, count = np.unique(pkey[order], return_index=True, return_counts=True)
    c0 = np.clip(np.floor((lo - pmin) / cell).astype(np.int64), 0, dims - 1)
    c1 = np.clip(np.floor((hi - pmin) / cell).astype(np.int64), 0, dims - 1)
    ny = c1[:, 1] - c0[:, 1] + 1
    cells = (c1[:, 0] - c0[:, 0] + 1) * ny

    big = np.nonzero(cells > max(len(keys), 16))[0]
    for chunk in np.array_split(big, max(1, len(big) * len(pts) // _PAIR_CHUNK + 1)):
        if not len(chunk):
            continue
        inbox = np.all((pts[None] >= lo[chunk, None]) & (pts[None] <= hi[chunk, None]), axis=2)
        tt, pp = np.nonzero(inbox)
        inside = _inside(x, y, ti[chunk[tt]], pts[pp], span)
        hit[pp[inside]] = True

    small = np.nonzero(cells <= max(len(keys), 16))[0]
    bounds = np.searchsorted(np.cumsum(cells[small]), np.arange(_PAIR_CHUNK, int(cells[small].sum()), _PAIR_CHUNK))
    for chunk in np.split(small, bounds):
        if not len(chunk):
            continue
        m = cells[chunk]
        rep = np.repeat(np.arange(len(chunk)), m)
        off = np.arange(int(m.sum())) - np.repeat(np.cumsum(m) - m, m)
        tri_ = chunk[rep]
        key = (c0[tri_, 0] + off // ny[tri_]) * dims[1] + c0[tri_, 1] + off % ny[tri_]
        pos = np.minimum(np.searchsorted(keys, key), len(keys) - 1)
        found = keys[pos] == key
        tri_, pos = tri_[found], pos[found]
        n = count[pos]
        rep2 = np.repeat(np.arange(len(tri_)), n)
        within = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
        pi = order[start[pos][rep2] + within]
        inside = _inside(x, y, ti[tri_[rep2]], pts[pi], span)
        hit[pi[inside]] = True
    return hit


def _covered(vertices: np.ndarray, triangles: np.ndarray, points: np.ndarray, direction) -> np.ndarray:
    """For each point, whether its line of sight along ``direction`` meets a triangle (any facing).

    The line runs both ways through the point, so a camera and the opposite camera see the same
    lines. Triangles are (T, 3) indices into ``vertices``.
    """
    basis = _planeBasis(direction)
    return _coveredPlanar(vertices @ basis, triangles, points @ basis)


def _directionGroups(directions) -> List[int]:
    """Index of the first camera whose lines are the same lines (same or opposite direction)."""
    groups = []
    for i, d in enumerate(directions):
        same = next((j for j in range(i) if abs(abs(float(np.dot(d, directions[j]))) - 1.0) < 1e-12), i)
        groups.append(same)
    return groups


# --------------------------------------------------------------------------------- anchors

@dataclass
class _Anchors:
    joint: Joint
    kind: str = 'none'
    parentLocal: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    childLocal: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    radius: float = 0.0
    dropped: int = 0
    reason: str = ''

    @property
    def measured(self) -> bool:
        return len(self.parentLocal) > 0


def _openRims(geometry, meshes, world, pivot, settings) -> List[np.ndarray]:
    """Open boundary chains of a part near the pivot that go round it (welded across its meshes)."""
    polygons = []
    for m in meshes:
        polygons += geometry.polygons.get(m, [])
    if not polygons:
        return []
    used = np.unique(np.concatenate([np.asarray(p) for p in polygons]))
    grid = np.round(world[used] / settings.weld).astype(np.int64)
    _, first, inverse = np.unique(grid, axis=0, return_index=True, return_inverse=True)
    weldOf = dict(zip(used.tolist(), inverse.reshape(-1).tolist()))
    points = world[used[first]]
    edges = []
    for polygon in polygons:
        welded = [weldOf[v] for v in polygon]
        edges += [(min(a, b), max(a, b)) for a, b in zip(welded, welded[1:] + welded[:1]) if a != b]
    if not edges:
        return []
    pairs, counts = np.unique(np.array(edges, dtype=np.int64), axis=0, return_counts=True)
    adjacency: Dict[int, List[int]] = {}
    for a, b in pairs[counts == 1].tolist():
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    rims, seen = [], set()
    for startPoint in adjacency:
        if startPoint in seen:
            continue
        component, stack = [], [startPoint]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            component.append(x)
            stack += adjacency[x]
        rim = points[component]
        if len(component) < settings.rimMinPoints:
            continue
        if np.linalg.norm(rim - pivot, axis=1).max() > settings.rimReach:
            continue
        if np.linalg.norm(rim.max(axis=0) - rim.min(axis=0)) < settings.rimMinExtent:
            continue
        spread = rim - rim.mean(axis=0)
        spread /= np.maximum(np.linalg.norm(spread, axis=1, keepdims=True), 1e-9)
        if np.linalg.norm(spread.mean(axis=0)) > settings.rimRoundness:
            continue
        rims.append(rim)
    return rims


def _membraneAnchors(parentRims, childRims, settings):
    """Anchor pairs on the membrane between the best matching parent and child rims, or None."""
    best, pair = settings.rimMatch, None
    for rp in parentRims:
        for rc in childRims:
            extent = max(np.linalg.norm(rp.max(axis=0) - rp.min(axis=0)),
                         np.linalg.norm(rc.max(axis=0) - rc.min(axis=0)))
            if extent > settings.rimMaxExtent:
                continue
            distance = np.linalg.norm(rp[:, None] - rc[None], axis=2)
            mean = 0.5 * (distance.min(axis=0).mean() + distance.min(axis=1).mean())
            if mean < best:
                best, pair = mean, (rp, rc, distance)
    if pair is None:
        return None
    rp, rc, distance = pair
    centre = 0.5 * (rp.mean(axis=0) + rc.mean(axis=0))
    nearParent = rp[distance.argmin(axis=0)]     # for each child rim point
    nearChild = rc[distance.argmin(axis=1)]      # for each parent rim point
    pull = settings.rimPull
    onParent = np.concatenate([nearParent, rp])
    onChild = np.concatenate([rc, nearChild])
    return onParent + pull * (centre - onParent), onChild + pull * (centre - onChild)


def _placeAnchors(joint, geometry, frames, tris, world, settings) -> _Anchors:
    out = _Anchors(joint)
    pivot = frames[joint.child][:3, 3]
    parentMeshes, childMeshes = geometry.meshesOf(joint.parentNodes), geometry.meshesOf(joint.childNodes)
    if not parentMeshes or not childMeshes:
        out.reason = 'no mesh on the %s side' % ('parent' if not parentMeshes else 'child')
        return out
    onParent = np.isin(geometry.triMesh, parentMeshes)
    onChild = np.isin(geometry.triMesh, childMeshes)
    near = tris[onParent | onChild]
    near = near[np.linalg.norm(near.mean(axis=1) - pivot, axis=1) < settings.nearRadius]
    parentPoints = tris[onParent].reshape(-1, 3)
    childPoints = tris[onChild].reshape(-1, 3)
    parentPoints = parentPoints[np.linalg.norm(parentPoints - pivot, axis=1) < settings.axisRadius]
    childPoints = childPoints[np.linalg.norm(childPoints - pivot, axis=1) < settings.axisRadius]
    if not len(near) or not len(parentPoints) or not len(childPoints):
        out.reason = 'no polygons near the pivot on both sides'
        return out
    axis = childPoints.mean(axis=0) - parentPoints.mean(axis=0)
    if np.linalg.norm(axis) < 1e-12:
        out.reason = 'no limb axis: both sides centre on the same point'
        return out
    axis /= np.linalg.norm(axis)
    u = np.cross(axis, [0.0, 0.0, 1.0] if abs(axis[2]) < 0.9 else [1.0, 0.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    anchors, radii = [], []
    for k in range(settings.rays):
        angle = 2.0 * math.pi * k / settings.rays
        direction = math.cos(angle) * u + math.sin(angle) * v
        r = _rayNearest(pivot, direction, near)
        if 0.0 < r <= settings.maxRadius:
            anchors.append(pivot + settings.fraction * r * direction)
            radii.append(r)
    if len(anchors) < settings.minAnchors:
        out.reason = f'{len(anchors)} of {settings.rays} rays met a wall within maxRadius'
        return out
    anchors, radii = np.array(anchors), np.array(radii)
    keep = radii <= settings.outlierRatio * np.median(radii)
    parentSide, radii = anchors[keep], radii[keep]
    childSide, out.kind = parentSide.copy(), 'core'
    if joint.label.casefold() in {name.casefold() for name in settings.membraneJoints}:
        membrane = _membraneAnchors(_openRims(geometry, parentMeshes, world, pivot, settings),
                                    _openRims(geometry, childMeshes, world, pivot, settings), settings)
        if membrane is not None:
            (parentSide, childSide), out.kind = membrane, 'membrane'
    toParent, toChild = np.linalg.inv(frames[joint.parent]), np.linalg.inv(frames[joint.child])
    out.parentLocal = parentSide @ toParent[:3, :3].T + toParent[:3, 3]
    out.childLocal = childSide @ toChild[:3, :3].T + toChild[:3, 3]
    out.radius = float(np.median(radii))
    return out


def _measure(rig: List[_Anchors], frames, vertices, triangles, directions, groups) -> List[Optional[tuple]]:
    """Per joint: None when not measured, else (largest separation, [(camera, anchor) in daylight]).

    Cameras in one group see along the same lines (same or opposite direction) and share a test.
    """
    mids, seps, starts = [], [], [0]
    for a in rig:
        if not a.measured:
            continue
        fp, fc = frames[a.joint.parent], frames[a.joint.child]
        onParent = a.parentLocal @ fp[:3, :3].T + fp[:3, 3]
        onChild = a.childLocal @ fc[:3, :3].T + fc[:3, 3]
        mids.append(0.5 * (onParent + onChild))
        seps.append(float(np.linalg.norm(onParent - onChild, axis=1).max()))
        starts.append(starts[-1] + len(onParent))
    points = np.concatenate(mids) if mids else np.zeros((0, 3))
    daylight = {ci: ~_covered(vertices, triangles, points, d)
                for ci, d in enumerate(directions) if groups[ci] == ci}
    misses = [[] for _ in seps]
    for ci in range(len(directions)):
        found = np.nonzero(daylight[groups[ci]])[0]
        owners = np.searchsorted(starts, found, side='right') - 1
        for j, k in zip(owners.tolist(), found.tolist()):
            misses[j].append((ci, k - starts[j]))
    results, measured = [], iter(zip(seps, misses))
    for a in rig:
        results.append(next(measured) if a.measured else None)
    return results


def _buildRig(joints, geometry, reference, settings, directions, groups):
    """Anchors for every joint, less those that see daylight in the reference pose itself."""
    frames = reference.frames
    world = geometry.world(reference.meshFrames)
    tris = world[geometry.tris]
    rig = [_placeAnchors(joint, geometry, frames, tris, world, settings) for joint in joints]
    for a, result in zip(rig, _measure(rig, frames, world, geometry.tris, directions, groups)):
        if result is None:
            continue
        bad = sorted({k for _, k in result[1]})
        if bad:
            keep = np.ones(len(a.parentLocal), dtype=bool)
            keep[bad] = False
            a.parentLocal, a.childLocal, a.dropped = a.parentLocal[keep], a.childLocal[keep], len(bad)
            if not keep.any():
                a.reason = 'every anchor saw daylight in the reference pose'
    return rig


# ------------------------------------------------------------------------------------ audit

def _nodeRef(model, i) -> dict:
    return {'index': int(i), 'name': model.meshHierarchy[i].name}


def auditArticulation(model, keys, *, step: float = 2.0, cameras=None, joints=None,
                      reference: Optional[Pose] = None, settings: Optional[AnchorSettings] = None,
                      geoset: int = 0, worst: int = 6,
                      onPose: Optional[Callable[[str, float, List[dict]], None]] = None,
                      toEnd: bool = False) -> dict:
    """Seam-ray sweep: daylight through each joint, in every sampled pose of every KEY.

    ``keys`` are KEYs (a sequence or a name -> KEY mapping) played alone at full weight, or
    sequences of ``PoseLayer`` played together; layers keep their own frame offsets and priorities
    and the first layer's KEY sets the sample times. Each KEY is sampled every ``step`` frames up
    to ``FRAMES - 1``, as Red's sweep did; with ``toEnd`` the last interval is sampled too, up to
    ``FRAMES`` itself (``keyFrameTimes``).
    ``cameras`` are (azimuth, elevation) degrees (default ``SWEEP_CAMERAS``). ``reference`` is the
    pose the parts were cut in (default: rest pose; see ``referencePose``). ``joints`` replaces the
    derived joints (see ``articulationJoints``). ``onPose(name, frame, joints)`` receives every
    pose's per-joint result, for callers that need more than the bounded report.

    The report counts lines of sight with no polygon on them; see ``READING_RULE``.
    """
    sources = _sources(keys)
    if not sources:
        raise ValueError('Give at least one KEY to audit')
    if worst < 0:
        raise ValueError('worst must not be negative')
    timeLists = [source.times(step, toEnd) for source in sources]
    settings = settings or AnchorSettings()
    cams = _cameraList(cameras)
    directions = [cameraDirection(az, el) for az, el in cams]
    groups = _directionGroups(directions)
    jointList = articulationJoints(model, [k for s in sources for k in s.keys()], joints, geoset=geoset)
    geometry = _Geometry(model, geoset)
    if reference is None:
        reference = referencePose(model, geoset=geoset)
    if np.shape(reference.frames) != (len(model.meshHierarchy), 4, 4):
        raise ValueError('The reference pose does not belong to this model')
    rig = _buildRig(jointList, geometry, reference, settings, directions, groups)

    stats =[{'poses': 0, 'lines': 0, 'keys': set(), 'sep': None} for _ in rig]
    keyRows, events = [], []
    for si, (source, times) in enumerate(zip(sources, timeLists)):
        row = {'name': source.name, 'poses': 0, 'poses_with_daylight': 0, 'lines': 0, 'joints': []}
        perJoint = [[0, 0] for _ in rig]
        for time in times:
            pose = poseModel(model, source.at(time), geoset=geoset, vertices=False)
            results = _measure(rig, pose.frames, geometry.world(pose.meshFrames), geometry.tris,
                               directions, groups)
            row['poses'] += 1
            poseLines, perPose = 0, []
            for ji, (a, result) in enumerate(zip(rig, results)):
                if result is None:
                    perPose.append({'joint': a.joint.label, 'separation': None, 'lines': 0, 'cameras': []})
                    continue
                separation, misses = result
                lines, camsSeen = len(misses), sorted({c for c, _ in misses})
                perPose.append({'joint': a.joint.label, 'separation': separation, 'lines': lines,
                                'cameras': camsSeen})
                s = stats[ji]
                if s['sep'] is None or separation > s['sep'][0]:
                    s['sep'] = (separation, source.name, time)
                if lines:
                    s['poses'] += 1
                    s['lines'] += lines
                    s['keys'].add(si)
                    perJoint[ji][0] += 1
                    perJoint[ji][1] += lines
                    poseLines += lines
                    events.append((-lines, si, time, ji, camsSeen, separation))
            if poseLines:
                row['poses_with_daylight'] += 1
                row['lines'] += poseLines
            if onPose is not None:
                onPose(source.name, time, perPose)
        row['joints'] = [{'joint': rig[ji].joint.label, 'poses': p, 'lines': n}
                         for ji, (p, n) in enumerate(perJoint) if p]
        keyRows.append(row)

    events.sort(key=lambda e: e[:3])

    def event(e, code=None):
        lines, si, time, ji, camsSeen, separation = e
        entry = {'joint': rig[ji].joint.label, 'key': sources[si].name, 'frame': time,
                 'lines': -lines, 'cameras': camsSeen, 'separation': separation}
        return {'code': code, **entry} if code else entry

    jointRows = []
    for ji, a in enumerate(rig):
        s = stats[ji]
        jointRows.append({
            'label': a.joint.label, 'parent': _nodeRef(model, a.joint.parent),
            'child': _nodeRef(model, a.joint.child),
            'seat_nodes': [_nodeRef(model, i) for i in a.joint.seatNodes],
            'measured': a.measured, 'reason': a.reason,
            'anchors': {'kind': a.kind, 'count': len(a.parentLocal), 'dropped': a.dropped,
                        'radius': a.radius},
            'poses_with_daylight': s['poses'], 'keys_with_daylight': len(s['keys']), 'lines': s['lines'],
            'max_separation': (None if s['sep'] is None else
                               {'value': s['sep'][0], 'key': s['sep'][1], 'frame': s['sep'][2]}),
            'worst': [event(e) for e in events if e[3] == ji][:worst],
        })

    unmeasured = [a for a in rig if not a.measured]
    counts = {}
    if events:
        counts['daylight'] = len(events)
    if unmeasured:
        counts['joint_not_measured'] = len(unmeasured)
    findings = [{'code': 'joint_not_measured', 'joint': a.joint.label, 'reason': a.reason}
                for a in unmeasured][:DETAIL_LIMIT]
    findings += [event(e, 'daylight') for e in events[:DETAIL_LIMIT - len(findings)]]
    poses = sum(row['poses'] for row in keyRows)
    report = {
        'status': ('no_joints' if len(unmeasured) == len(rig) else
                   'review' if counts else 'clean'),
        'issue_counts': counts,
        'findings': findings,
        'findings_truncated': sum(counts.values()) > len(findings),
        'totals': {'keys': len(sources), 'poses': poses, 'joints': len(rig),
                   'joints_measured': len(rig) - len(unmeasured),
                   'poses_with_daylight': sum(row['poses_with_daylight'] for row in keyRows),
                   'lines': sum(s['lines'] for s in stats)},
        'joints': jointRows,
        'keys': keyRows,
        'geometry': {'geoset': geoset, 'triangles': int(len(geometry.tris)),
                     'skipped_faces': geometry.skippedFaces},
        'parameters': {'step': float(step), 'toEnd': bool(toEnd), 'cameras': [list(cam) for cam in cams],
                       'reference': getattr(reference, 'description', 'given pose'),
                       'worst': worst, 'settings': {k: (list(v) if isinstance(v, tuple) else v)
                                                    for k, v in asdict(settings).items()}},
        'reading': READING_RULE,
    }
    return report


# ----------------------------------------------------------------------------- seat offsets

def seatOffsets(model, keys, *, reference: Optional[Pose] = None, tolerance: float = 2e-5,
                toEnd: bool = False) -> dict:
    """The largest translation each KEY gives each node away from its seat, with its frame.

    A seat is the node's position in ``reference`` (default: the model's rest positions; pass
    ``referencePose(model, standKey)`` to measure from the standing KEY). The offset is the KEY's
    track position minus the seat, in the parent's frame, at every whole frame and every entry
    frame below ``FRAMES``; with ``toEnd``, at ``FRAMES`` too, the end of the last interval the
    engine plays (see ``keyFrameTimes``). Offsets above ``tolerance`` count as off the seat. This
    is the usual cause of an opening joint: read it next to ``auditArticulation``'s ``seat_nodes``.
    """
    if not (math.isfinite(tolerance) and tolerance >= 0):
        raise ValueError('tolerance must be a non-negative number')
    nodes = model.meshHierarchy
    seats = (np.array([tuple(n.position) for n in nodes], dtype=float).reshape(-1, 3)
             if reference is None else np.asarray(reference.local.positions, dtype=float))
    if seats.shape != (len(nodes), 3):
        raise ValueError('The reference pose does not belong to this model')
    distinct, seen = [], set()
    for source in _sources(keys):
        for layer in source.layers:
            if id(layer.key) not in seen:
                seen.add(id(layer.key))
                distinct.append((source.name if len(source.layers) == 1 else
                                 str(getattr(layer.key, 'name', 'KEY')), layer.key))
    perNode: Dict[int, dict] = {}
    keyRows, offSeat = [], []
    for name, key in distinct:
        frames = int(getattr(key, 'numFrames', 0))
        total = max(frames, 1)
        row = {'name': name, 'tracks': 0, 'off_seat': []}
        for i in range(len(nodes)):
            track = keyTrack(key, i)
            if track is None:
                continue
            row['tracks'] += 1
            times = set(float(f) for f in range(total)) | {float(e.frame) for e in track.keyframes
                                                            if 0 <= float(e.frame) < total}
            if toEnd:
                times.add(float(max(frames, 0)))
            times = sorted(times)
            best = None
            for time in times:
                vector = sampleTrack(track, time)[0] - seats[i]
                length = float(np.linalg.norm(vector))
                if best is None or length > best[0]:
                    best = (length, time, vector)
            entry = {'node': i, 'name': nodes[i].name, 'offset': best[0], 'frame': best[1],
                     'vector': [float(v) for v in best[2]]}
            node = perNode.setdefault(i, {'index': i, 'name': nodes[i].name, 'keys': 0,
                                          'keys_off_seat': 0, 'largest': None})
            node['keys'] += 1
            if node['largest'] is None or best[0] > node['largest']['offset']:
                node['largest'] = {'key': name, 'offset': best[0], 'frame': best[1],
                                   'vector': entry['vector']}
            if best[0] > tolerance:
                node['keys_off_seat'] += 1
                row['off_seat'].append(entry)
                offSeat.append((-best[0], len(keyRows), i, entry))
        row['off_seat'].sort(key=lambda e: (-e['offset'], e['node']))
        keyRows.append(row)
    offSeat.sort(key=lambda e: e[:3])
    findings = [{'code': 'off_seat', 'key': keyRows[k]['name'], **entry}
                for _, k, _, entry in offSeat[:DETAIL_LIMIT]]
    counts = {'off_seat': len(offSeat)} if offSeat else {}
    return {
        'status': 'no_tracks' if not perNode else 'off_seat' if offSeat else 'on_seat',
        'issue_counts': counts,
        'findings': findings,
        'findings_truncated': len(offSeat) > len(findings),
        'nodes': [perNode[i] for i in sorted(perNode)],
        'keys': keyRows,
        'parameters': {'reference': ('rest positions' if reference is None else
                                     getattr(reference, 'description', 'given pose')),
                       'tolerance': tolerance, 'toEnd': bool(toEnd)},
    }
