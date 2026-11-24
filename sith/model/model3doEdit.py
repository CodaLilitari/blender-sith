# SPDX-License-Identifier: GPL-3.0-or-later
"""Native 3DO edits that return a new model, without Blender.

``removeFaces`` takes faces out of one mesh and closes any opening the removal leaves with cap
faces. It ports the method of Red's ``degost.py`` (Mandalorian Red 0.2.2), which removed 74 faces
from one torso mesh and closed the opening in her flank with 12 caps:

- Vertices are welded by position, so a seam where the same point is stored twice still counts
  as one point. An opening is a loop of edges that had a face on both sides before and has a face
  on one side only after.
- The opening edges are walked into loops. Where two openings touch at one point (a pinch), the
  walk is split there, so each loop passes through each point once. Edges that run on to an open
  border of the mesh form a chain, not a loop: each chain is listed once in ``unclosed``.
- Each loop gets the triangulation of least area over it (``triangulatePolygon``). The caps run
  against the neighbouring faces' edges, so they face the same way as the surface around them, and
  every edge inside a cap has a cap face on each side, running opposite ways.
- A cap never joins two loop points that an edge of the mesh already joins, and never has three
  corners of one face the mesh already has (a kept face, or a cap made earlier). Where an opening
  wraps round a kept face (a "tooth"), the cheapest cap over the tooth is that face turned inside
  out; the Mandalorian flank opening wraps round two such faces, and two caps in Red's release are
  those faces reversed. A loop that cannot be capped under this rule is listed in ``unclosed`` and
  left open.
- The caps take the material most faces around the opening use, with the face type, modes and
  extra light of a neighbouring face of that material. Each cap corner takes the vertex and texture
  vertex a neighbouring face uses at that point, so the cap carries the surrounding texture and adds
  no vertices or texture vertices.

The engine draws only the faces a mesh lists (``rdModel3_DrawMesh`` in OpenJKDF2
``Primitives/rdModel3.c``), so a vertex no face uses is not drawn. Removed faces leave their
vertices in place: indices stay stable and the report counts the vertices left unused.

Red's ``degost.py`` ear-clipped each opening in a best-fit plane. Ear clipping can run out of ears
on a loop far from flat (the Mandalorian flank opening has ``planarity`` 0.18) and must then cut a
corner that may face the wrong way. Every triangle of the least-area triangulation keeps the loop's
winding instead.

Limits: each loop is capped on its own. A removal that leaves two rims, such as a band of faces,
gets a cap on each rim: that closes both pieces where the band ran round a tube, but on a flat
patch the outer cap lies over the kept faces inside the band. The same holds where one opening
touches itself at a point, since the walk is split there. Caps across an opening far from flat may
cut inside the surface, which the report measures as ``planarity``; the caller checks the result in
the poses that matter. Only one mesh of one geoset changes; models with identical detail levels
need the same call for each geoset.
"""

import copy
import itertools
from typing import Dict, Iterable, List, Optional, Set, Tuple

import numpy as np

from .model3do import Mesh3doFace
from ..types import Vector3f

__all__ = ['copyModel', 'removeFaces', 'weldPositions', 'componentLabels', 'polygonNormal', 'triangulatePolygon']


def copyModel(model):
    """Deep copy of a native ``Model3do`` that keeps Blender object links shared.

    Node ``obj`` links, set by the Blender importer and exporter, refer to scene objects and stay the
    same objects. Everything else, including Grim binary metadata dictionaries, is copied, so the copy
    can be edited without changing the original.
    """
    memo = {}
    for node in model.meshHierarchy:
        obj = getattr(node, '_o', None)
        if obj is not None:
            memo[id(obj)] = obj
    return copy.deepcopy(model, memo)


def componentLabels(count: int, a, b) -> np.ndarray:
    """Connected-component labels for ``count`` items joined by the pairs ``(a[k], b[k])``.

    Each label is the smallest item index in its component.
    """
    labels = np.arange(count)
    a = np.asarray(a, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)
    if len(a) == 0:
        return labels
    while True:
        low = np.minimum(labels[a], labels[b])
        new = labels.copy()
        np.minimum.at(new, a, low)
        np.minimum.at(new, b, low)
        while True:                       # follow each label to its root
            jumped = new[new]
            if np.array_equal(jumped, new):
                break
            new = jumped
        if np.array_equal(new, labels):
            return labels
        labels = new


def weldPositions(points, tolerance: float = 1e-6) -> np.ndarray:
    """Weld labels for 3D points: points closer than ``tolerance`` on every axis share a label.

    Points within ``tolerance`` always share a label; points up to twice the tolerance apart on an
    axis may share one too, and welding is transitive. The label is the smallest index of the group.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    if tolerance <= 0:
        raise ValueError('weld tolerance must be positive')
    count = len(pts)
    if count == 0:
        return np.zeros(0, dtype=np.int64)
    cell = 2.0 * tolerance
    a: List[np.ndarray] = []
    b: List[np.ndarray] = []
    # Eight grids shifted by half a cell per axis: two points within the tolerance share a cell in
    # at least one of them, whichever cell boundaries they straddle.
    for shift in range(8):
        offset = np.array([(shift >> axis) & 1 for axis in range(3)], dtype=float) * tolerance
        keys = np.floor((pts + offset) / cell).astype(np.int64)
        _, inverse = np.unique(keys, axis=0, return_inverse=True)
        inverse = inverse.reshape(-1)
        order = np.argsort(inverse, kind='stable')
        grouped = inverse[order]
        same = grouped[1:] == grouped[:-1]
        a.append(order[1:][same])
        b.append(order[:-1][same])
    return componentLabels(count, np.concatenate(a), np.concatenate(b))


def polygonNormal(points) -> np.ndarray:
    """Newell normal of a polygon (not normalized): twice its area along the right-hand normal."""
    p = np.asarray(points, dtype=float).reshape(-1, 3)
    q = np.roll(p, -1, axis=0)
    return np.array([np.sum((p[:, 1] - q[:, 1]) * (p[:, 2] + q[:, 2])),
                     np.sum((p[:, 2] - q[:, 2]) * (p[:, 0] + q[:, 0])),
                     np.sum((p[:, 0] - q[:, 0]) * (p[:, 1] + q[:, 1]))])


def triangulatePolygon(points, *, blockedEdges: Iterable = (),
                       blockedTriangles: Iterable = ()) -> Optional[List[Tuple[int, int, int]]]:
    """Minimum-area triangulation of a closed loop of 3D points, in the loop's own winding.

    Dynamic programming over the loop (the hole-filling weight of Liepa, "Filling Holes in Meshes",
    2003, with area as the weight): every triangle ``(i, k, j)`` has ``i < k < j``, so each one runs the
    same way round as the loop, every inner edge is used once in each direction and every loop edge
    once in its own direction. That holds for loops that are far from flat too. Among triangulations
    of equal area (a flat convex loop) the shorter inner edges win. Takes time cubic in the loop's
    length. The loop should pass through each point once; ``removeFaces`` splits a loop that does not.

    ``blockedEdges`` are pairs of loop positions that no triangle may join, unless they are
    neighbours on the loop. ``blockedTriangles`` are triples of loop positions that no triangle may
    have as its corners. Returns None when every triangulation uses one of them.
    """
    p = np.asarray(points, dtype=float).reshape(-1, 3)
    count = len(p)
    if count < 3:
        return []
    blocked = np.zeros((count, count), dtype=bool)
    for pair in blockedEdges:
        a, b = sorted(int(x) for x in pair)
        if b - a > 1 and (a, b) != (0, count - 1):
            blocked[a, b] = True
    middles: Dict[Tuple[int, int], List[int]] = {}
    for triple in blockedTriangles:
        a, k, b = sorted(int(x) for x in triple)
        middles.setdefault((a, b), []).append(k)
    cost = np.zeros((count, count))
    choice = np.full((count, count), -1, dtype=np.int64)
    for span in range(2, count):
        for i in range(count - span):
            j = i + span
            if blocked[i, j]:
                cost[i, j] = np.inf
                continue
            k = np.arange(i + 1, j)
            area = 0.5 * np.linalg.norm(np.cross(p[k] - p[i], p[j] - p[i]), axis=1)
            spread = np.sum((p[k] - p[i]) ** 2, axis=1) + np.sum((p[j] - p[k]) ** 2, axis=1)
            total = cost[i, k] + cost[k, j] + area + 1e-6 * spread
            for m in middles.get((i, j), ()):
                total[m - i - 1] = np.inf
            best = int(np.argmin(total))
            cost[i, j] = total[best]
            choice[i, j] = k[best]
    if not np.isfinite(cost[0, count - 1]):
        return None
    tris: List[Tuple[int, int, int]] = []
    stack = [(0, count - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        k = int(choice[i, j])
        tris.append((i, k, j))
        stack.extend(((i, k), (k, j)))
    return sorted(tris)


def _boundaryEdges(faces, keep, weld) -> Dict[Tuple[int, int], int]:
    """Undirected welded edge -> number of kept faces using it (corners welded together are skipped)."""
    counts: Dict[Tuple[int, int], int] = {}
    for fi in keep:
        ids = [int(weld[v]) for v in faces[fi].vertexIdxs]
        for k in range(len(ids)):
            a, b = ids[k], ids[(k + 1) % len(ids)]
            if a != b:
                key = (a, b) if a < b else (b, a)
                counts[key] = counts.get(key, 0) + 1
    return counts


def _repeatedEdges(faces, weld) -> int:
    """Directed welded edges that two or more of ``faces`` run the same way."""
    counts: Dict[Tuple[int, int], int] = {}
    for face in faces:
        ids = [int(weld[v]) for v in face.vertexIdxs]
        for k in range(len(ids)):
            a, b = ids[k], ids[(k + 1) % len(ids)]
            if a != b:
                counts[(a, b)] = counts.get((a, b), 0) + 1
    return sum(1 for count in counts.values() if count > 1)


def _walks(edges: List[Tuple[int, int]]) -> List[Tuple[List[int], bool]]:
    """Trails of directed ``edges`` that use each edge once, as (points, closed) pairs.

    A point that more edges leave than reach starts that many chains, and each chain is followed
    from there until no unused edge leaves its last point, so a chain comes out whole. The edges
    left over form closed walks, each followed until it is back at its first point.
    """
    outgoing: Dict[int, List[int]] = {}
    surplus: Dict[int, int] = {}
    for a, b in edges:
        outgoing.setdefault(a, []).append(b)
        surplus[a] = surplus.get(a, 0) + 1
        surplus[b] = surplus.get(b, 0) - 1
    used: Set[Tuple[int, int]] = set()

    def follow(point, home=None):
        trail = [point]
        while True:
            choices = [n for n in outgoing.get(point, ()) if (point, n) not in used]
            if not choices:
                return trail, False
            step = home if home in choices else choices[0]
            used.add((point, step))
            if step == home:
                return trail, True
            trail.append(step)
            point = step

    walks = [follow(point) for point, extra in list(surplus.items()) for _ in range(max(extra, 0))]
    for a, b in edges:
        if (a, b) not in used:
            walks.append(follow(a, home=a))
    return walks


def _splitWalk(points: List[int], closed: bool) -> Tuple[List[List[int]], Optional[List[int]]]:
    """Cut a walk at every point it reaches twice into simple loops (each point once).

    Returns the loops and what is left of an open walk: the chain without its loops (None for a
    closed walk, whose remainder is a loop too). Two openings that touch at one point (a pinch) can
    be walked as one loop through that point twice; this gives the two loops back.
    """
    loops: List[List[int]] = []
    path: List[int] = []
    where: Dict[int, int] = {}
    for point in points:
        if point in where:
            start = where[point]
            loops.append(path[start:])
            for other in path[start + 1:]:
                del where[other]
            del path[start + 1:]
        else:
            where[point] = len(path)
            path.append(point)
    if closed:
        loops.append(path)
        return loops, None
    return loops, path


def removeFaces(model, meshIdx: int, faces: Iterable[int], *, geoset: int = 0, closeOpenings: bool = True,
                weld: float = 1e-6):
    """Remove ``faces`` from mesh ``meshIdx`` of ``geoset`` and close the openings left behind.

    Returns ``(newModel, report)``. The input model is not changed. ``faces`` are indices into the
    mesh's face list; repeats are ignored. With ``closeOpenings`` false the faces are only removed.

    The report gives ``faces_before``, ``faces_removed``, ``cap_faces`` and ``faces_after``; a
    ``face_map`` from each old face index to its new index (-1 when removed; kept faces keep their
    order and caps follow them); ``openings`` with each capped loop's vertex indices, its new cap face
    indices, the material it took and its ``planarity`` (largest distance from the loop's plane
    divided by the loop's size); ``unclosed``, the chains and loops left open, each with its
    ``points``, up to 64 ``vertices`` and the ``reason``; ``open_edges_before`` and
    ``open_edges_after`` (edges with a face on one side only, welded by position: when every opening
    was closed the second is not larger than the first, and it is smaller when removed faces had open
    edges of their own); ``repeated_edges_before`` and ``repeated_edges_after`` (directed edges, welded
    by position, that two or more faces run the same way: a face repeated, or one turned inside out
    against its neighbour); and ``unused_vertices``.
    """
    if not 0 <= geoset < len(model.geosets):
        raise ValueError(f'3DO has no geoset {geoset}')
    meshes = model.geosets[geoset].meshes
    if not 0 <= meshIdx < len(meshes):
        raise ValueError(f'Geoset {geoset} has no mesh {meshIdx}')
    source = meshes[meshIdx]
    drop = set()
    for value in faces:
        index = int(value)
        if not 0 <= index < len(source.faces):
            raise ValueError(f'Mesh {meshIdx} ({source.name}) has no face {index}')
        drop.add(index)

    out = copyModel(model)
    mesh = out.geosets[geoset].meshes[meshIdx]
    positions = np.array([tuple(v) for v in mesh.vertices], dtype=float).reshape(-1, 3)
    for fi, face in enumerate(mesh.faces):
        if any(not 0 <= v < len(positions) for v in face.vertexIdxs):
            raise ValueError(f'Mesh {meshIdx} ({mesh.name}), face {fi} has a vertex index out of range')
    welded = weldPositions(positions, weld) if len(positions) else np.zeros(0, dtype=np.int64)
    everything = range(len(mesh.faces))
    keep = [fi for fi in everything if fi not in drop]
    before = _boundaryEdges(mesh.faces, everything, welded)
    after = _boundaryEdges(mesh.faces, keep, welded)

    # Directed edges of kept faces that were shared before and are open now. The cap over the opening
    # must use each such edge in the opposite direction to stay consistent with its neighbour.
    capEdges: Dict[Tuple[int, int], Tuple[int, int]] = {}     # cap edge (b, a) -> (face, corner of a)
    order: List[Tuple[int, int]] = []
    for fi in keep:
        face = mesh.faces[fi]
        ids = [int(welded[v]) for v in face.vertexIdxs]
        for k in range(len(ids)):
            a, b = ids[k], ids[(k + 1) % len(ids)]
            if a == b:
                continue
            key = (a, b) if a < b else (b, a)
            if after.get(key) == 1 and before.get(key, 0) > 1 and (b, a) not in capEdges:
                capEdges[(b, a)] = (fi, k)
                order.append((b, a))

    corner: Dict[Tuple[int, int], Tuple[int, int]] = {}         # (welded point, material) -> (vertex, uv)
    anyCorner: Dict[int, Tuple[int, int]] = {}
    for (b, a), (fi, k) in ((edge, capEdges[edge]) for edge in order):
        face = mesh.faces[fi]
        n = len(face.vertexIdxs)
        for point, slot in ((a, k), (b, (k + 1) % n)):
            uv = face.uvIdxs[slot] if slot < len(face.uvIdxs) else -1
            entry = (face.vertexIdxs[slot], uv)
            corner.setdefault((point, face.materialIdx), entry)
            anyCorner.setdefault(point, entry)

    loops: List[List[int]] = []
    unclosed: List[dict] = []

    def leaveOpen(points, reason):
        unclosed.append({'points': len(points), 'vertices': [anyCorner[p][0] for p in points][:64],
                         'reason': reason})

    if closeOpenings:
        for points, closed in _walks(order):
            simple, chain = _splitWalk(points, closed)
            loops.extend(simple)
            if chain is not None:
                leaveOpen(chain, 'the edges do not close into a loop')

    # What a cap must not reuse: the welded edges and faces the mesh has, kept faces and caps made so
    # far. Where an opening wraps round a kept face, the cap of least area over that face's corners is
    # the face itself turned inside out, sharing its edges.
    neighbours: Dict[int, Set[int]] = {}
    facesAt: Dict[int, List[List[int]]] = {}

    def occupy(ids):
        for k in range(len(ids)):
            a, b = ids[k], ids[(k + 1) % len(ids)]
            if a != b:
                neighbours.setdefault(a, set()).add(b)
                neighbours.setdefault(b, set()).add(a)
        for point in set(ids):
            facesAt.setdefault(point, []).append(ids)

    for fi in keep:
        occupy([int(welded[v]) for v in mesh.faces[fi].vertexIdxs])

    newFaces = [mesh.faces[fi] for fi in keep]
    openings: List[dict] = []
    for loop in loops:
        # The cap takes the material used by most of the faces around the opening.
        materials: Dict[int, int] = {}
        template = None
        for k in range(len(loop)):
            edge = (loop[k], loop[(k + 1) % len(loop)])
            if edge in capEdges:
                face = mesh.faces[capEdges[edge][0]]
                materials[face.materialIdx] = materials.get(face.materialIdx, 0) + 1
        material = max(sorted(materials), key=lambda m: materials[m])
        for k in range(len(loop)):
            edge = (loop[k], loop[(k + 1) % len(loop)])
            if edge in capEdges and mesh.faces[capEdges[edge][0]].materialIdx == material:
                template = mesh.faces[capEdges[edge][0]]
                break
        slots = [corner.get((p, material), anyCorner[p]) for p in loop]
        points = positions[[s[0] for s in slots]]
        where = {p: k for k, p in enumerate(loop)}
        edgesUsed = [(k, where[q]) for k, p in enumerate(loop) for q in neighbours.get(p, ()) if q in where]
        facesUsed = set()
        for p in loop:
            for ids in facesAt.get(p, ()):
                hits = sorted({where[q] for q in ids if q in where})
                facesUsed.update(itertools.combinations(hits, 3))
        tris = triangulatePolygon(points, blockedEdges=edgesUsed, blockedTriangles=facesUsed)
        if not tris:
            leaveOpen(loop, 'every cap would reuse an edge or the corners of a face the mesh has')
            continue
        for tri in tris:
            occupy([loop[i] for i in tri])
        normal = polygonNormal(points)
        span = float(np.max(np.ptp(points, axis=0))) or 1.0
        unit = normal / (np.linalg.norm(normal) or 1.0)
        planarity = float(np.max(np.abs((points - points.mean(axis=0)) @ unit)) / span)
        first = len(newFaces)
        for tri in tris:
            cap = Mesh3doFace()
            cap.materialIdx = template.materialIdx
            cap.type = template.type
            cap.geometryMode = template.geometryMode
            cap.lightMode = template.lightMode
            cap.textureMode = template.textureMode
            cap.color = template.color
            cap.vertexIdxs = [slots[i][0] for i in tri]
            cap.uvIdxs = [slots[i][1] for i in tri]
            p0, p1, p2 = (positions[slots[i][0]] for i in tri)
            n = np.cross(p1 - p0, p2 - p0)
            length = float(np.linalg.norm(n))
            n = n / length if length > 0 else unit
            cap.normal = Vector3f(*(float(x) for x in n))
            newFaces.append(cap)
        openings.append({'vertices': [s[0] for s in slots], 'cap_faces': list(range(first, len(newFaces))),
                         'material': int(material), 'planarity': round(planarity, 6)})

    faceMap = [-1] * len(mesh.faces)
    for new, old in enumerate(keep):
        faceMap[old] = new
    repeatedBefore = _repeatedEdges(mesh.faces, welded)
    mesh.faces = newFaces
    usedVertices = {v for face in newFaces for v in face.vertexIdxs}
    final = _boundaryEdges(newFaces, range(len(newFaces)), welded)
    report = {
        'geoset': geoset, 'mesh': meshIdx, 'name': mesh.name,
        'faces_before': len(faceMap), 'faces_removed': len(drop),
        'cap_faces': len(newFaces) - len(keep), 'faces_after': len(newFaces),
        'openings': openings, 'unclosed': unclosed,
        'open_edges_before': sum(1 for c in before.values() if c == 1),
        'open_edges_after': sum(1 for c in final.values() if c == 1),
        'repeated_edges_before': repeatedBefore,
        'repeated_edges_after': _repeatedEdges(newFaces, welded),
        'unused_vertices': len(positions) - len(usedVertices),
        'face_map': faceMap,
    }
    return out, report
