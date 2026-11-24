# SPDX-License-Identifier: GPL-3.0-or-later
"""Retail resource budget of a native 3DO: counts per mesh and per geoset against engine limits.

JKDF2 and MOTS load text 3DOs with ``rdModel3_LoadEntry`` (Primitives/rdModel3.c in OpenJKDF2,
reconstructed from retail JK.EXE 1.01; MOTS uses the same loader). These rules are enforced or
assumed by the retail engine:

- A mesh with more than 512 vertices (``numVertices > 0x200``), 768 texture vertices
  (``numUVs > 0x300``) or 512 faces (``numFaces > 0x200``) fails the model load.
- A face with no corners or with more than 24 corners fails the model load.
- ``rdModel3`` holds four geosets (``rdGeoset aGeos[4]``). The loader does not check the count,
  so a fifth geoset overwrites the rest of the model record.
- A puppet track keeps one entry per joint in ``aCurKfNodeEntryNums[64]``
  (``RDPUPPET_MAX_NODES``, Engine/rdPuppet.c). JK clears one entry per hierarchy node; OpenJKDF2
  notes that the bound check in ``rdPuppet_ClearTrackNodes`` came later, with Grim Fandango.
  An animated model therefore has at most 64 hierarchy nodes.
- A face's material index is used without a bound check (``aMaterials[index]``; OpenJKDF2 marks
  its clamp as added), and ``rdModel3_DrawHNode`` draws ``aMeshes[node->meshIdx]`` of the
  selected geoset without one. An index outside the model's tables reads past them.

The loader sets no limit on the number of materials, on meshes per geoset or on totals per
geoset; those are reported as counts. The audit does not check that face corners index
existing vertices or texture vertices, name lengths (``save3do`` warns about those), or what
the renderer does per frame. IJIM and GRIM have no sourced limits here: their reports carry
counts and no verdict.
"""

from typing import Dict, List

__all__ = ['DETAIL_LIMIT', 'kBudgetTargets', 'kModelLimits', 'auditBudget', 'budgetWarnings']

DETAIL_LIMIT = 32

kBudgetTargets = ('JKDF2', 'MOTS', 'IJIM', 'GRIM')

_kRetailLimits = {
    'vertices': 512,          # per mesh; rdModel3_LoadEntry fails on numVertices > 0x200
    'texture_vertices': 768,  # per mesh; rdModel3_LoadEntry fails on numUVs > 0x300
    'faces': 512,             # per mesh; rdModel3_LoadEntry fails on numFaces > 0x200
    'face_corners': 24,       # per face; rdModel3_LoadEntry fails on 0 or more than 24
    'geosets': 4,             # per model; rdModel3.aGeos[4]
    'hierarchy_nodes': 64,    # per animated model; rdPuppetTrack.aCurKfNodeEntryNums[64]
}

kModelLimits = {'JKDF2': dict(_kRetailLimits), 'MOTS': dict(_kRetailLimits)}

_kMeshCounts = ('vertices', 'texture_vertices', 'faces')


def _meshCounts(index, mesh) -> Dict:
    return {'index': index, 'name': mesh.name[:128], 'vertices': len(mesh.vertices),
            'texture_vertices': len(mesh.uvs), 'faces': len(mesh.faces),
            'face_corners': max((len(face.vertexIdxs) for face in mesh.faces), default=0)}


def _largest(meshes, key):
    if not meshes:
        return None
    top = max(meshes, key=lambda counts: counts[key])  # the first of equal meshes
    return {'index': top['index'], 'name': top['name'], 'count': top[key]}


def auditBudget(model, target: str = 'JKDF2') -> Dict:
    """Count meshes, vertices, texture vertices, faces and corners against ``target``'s limits.

    Every geoset is counted as stored; the audit does not assume geosets are copies of each
    other. ``status`` is ``within_limits`` or ``over_limits`` for JKDF2 and MOTS, and
    ``counts_only`` for IJIM and GRIM. Each finding names its geoset and mesh (index and name)
    and how far it is over; ``issue_counts`` totals every finding, including those past the
    first ``DETAIL_LIMIT``. The model is not changed.
    """
    if target not in kBudgetTargets:
        raise ValueError(f"Choose one of {', '.join(kBudgetTargets)} for the model budget")
    limits = kModelLimits.get(target)
    nodes = model.meshHierarchy
    materialCount = len(model.materials)
    report = {'target': target, 'limits': dict(limits) if limits else None,
              'geoset_count': len(model.geosets), 'hierarchy_nodes': len(nodes),
              'materials': materialCount, 'geosets': [], 'issue_counts': {}, 'findings': []}

    def finding(code, **details):
        counts = report['issue_counts']
        counts[code] = counts.get(code, 0) + 1
        if len(report['findings']) < DETAIL_LIMIT:
            report['findings'].append({'code': code, **details})

    if limits:
        for code, key, count in (('too_many_geosets', 'geosets', len(model.geosets)),
                                 ('too_many_hierarchy_nodes', 'hierarchy_nodes', len(nodes))):
            if count > limits[key]:
                finding(code, count=count, limit=limits[key], over=count - limits[key])

    for geosetIndex, geoset in enumerate(model.geosets):
        meshes = [_meshCounts(index, mesh) for index, mesh in enumerate(geoset.meshes)]
        report['geosets'].append({
            'geoset': geosetIndex, 'meshes': len(meshes),
            **{key: sum(counts[key] for counts in meshes) for key in _kMeshCounts},
            'largest': {key: _largest(meshes, key) for key in (*_kMeshCounts, 'face_corners')},
            'mesh_counts': meshes,
        })
        if not limits:
            continue

        for counts, mesh in zip(meshes, geoset.meshes):
            where = {'geoset': geosetIndex, 'mesh': counts['index'], 'name': counts['name']}
            for key in _kMeshCounts:
                if counts[key] > limits[key]:
                    finding('too_many_' + key, **where, count=counts[key], limit=limits[key],
                            over=counts[key] - limits[key])
            for faceIndex, face in enumerate(mesh.faces):
                corners = len(face.vertexIdxs)
                if corners == 0:
                    finding('empty_face', **where, face=faceIndex)
                elif corners > limits['face_corners']:
                    finding('too_many_face_corners', **where, face=faceIndex, count=corners,
                            limit=limits['face_corners'], over=corners - limits['face_corners'])
                if not -1 <= face.materialIdx < materialCount:
                    finding('material_out_of_range', **where, face=faceIndex,
                            material=face.materialIdx, materials=materialCount)
        for node in nodes:
            meshIdx = -1 if node.meshIdx is None else node.meshIdx
            if meshIdx != -1 and not 0 <= meshIdx < len(geoset.meshes):
                finding('missing_mesh', geoset=geosetIndex, node=node.idx, node_name=node.name[:128],
                        mesh=meshIdx, meshes=len(geoset.meshes))

    report['findings_truncated'] = sum(report['issue_counts'].values()) > len(report['findings'])
    report['status'] = ('counts_only' if not limits else
                        'over_limits' if report['issue_counts'] else 'within_limits')
    return report


_kMeshMessages = {
    'too_many_vertices': 'has {count} vertices; {target} allows {limit} per mesh ({over} over)',
    'too_many_texture_vertices': 'has {count} texture vertices; {target} allows {limit} per mesh ({over} over)',
    'too_many_faces': 'has {count} faces; {target} allows {limit} per mesh ({over} over)',
    'too_many_face_corners': 'face {face} has {count} corners; {target} allows {limit} ({over} over)',
    'empty_face': 'face {face} has no corners; {target} cannot load it',
    'material_out_of_range': 'face {face} uses material {material}, but the model lists {materials}',
}

# Per-mesh limit codes and the ``mesh_counts`` field each one compares.
_kMeshLimitCounts = {'too_many_' + key: key for key in _kMeshCounts}


def _sameMeshGeosets(report: Dict, item: Dict) -> List[int]:
    """Every geoset whose mesh ``item['mesh']`` has the same name and count as a per-mesh finding.

    ``mesh_counts`` lists every mesh of every geoset, so this finds the repeats of the finding
    that fall past the report's detail limit. Each match is a finding of the same code: the count
    is the same, so it is over the same limit.
    """
    key = _kMeshLimitCounts[item['code']]
    geosets = []
    for row in report['geosets']:
        rows = row.get('mesh_counts') or []
        counts = rows[item['mesh']] if item['mesh'] < len(rows) else None
        if (counts is not None and counts.get('index') == item['mesh'] and counts.get('name') == item['name']
                and counts.get(key) == item['count']):
            geosets.append(row['geoset'])
    return geosets


def budgetWarnings(report: Dict) -> List[str]:
    """One line per finding of ``auditBudget``; repeats across geosets share one line.

    A finding that differs only in its geoset (for example in identical detail levels) is
    listed once with the geosets it occurs in. For the per-mesh limits (vertices, texture
    vertices and faces) the line names every such geoset, also those whose findings fall past
    the report's detail limit, because the per-mesh counts of every geoset are complete. Face,
    material and mesh-index findings name only the geosets of the listed findings. Findings
    that no line covers are summarized by code with their number.
    """
    target = report['target']
    entries: Dict[tuple, Dict] = {}
    for item in report['findings']:
        details = tuple(sorted((name, value) for name, value in item.items() if name != 'geoset'))
        entry = entries.setdefault(details, {'item': item, 'geosets': []})
        if 'geoset' in item:
            entry['geosets'].append(item['geoset'])

    # How many findings the lines stand for, by code: one per geoset a line names, or one for a
    # model-wide finding. The rest are summarized below.
    covered: Dict[str, int] = {}
    for entry in entries.values():
        item = entry['item']
        if item['code'] in _kMeshLimitCounts:
            entry['geosets'] = sorted(set(entry['geosets']) | set(_sameMeshGeosets(report, item)))
        covered[item['code']] = covered.get(item['code'], 0) + max(len(entry['geosets']), 1)

    lines = []
    for entry in entries.values():
        item, geosets = entry['item'], entry['geosets']
        code = item['code']
        where = ('geoset ' if len(geosets) == 1 else 'geosets ') + ', '.join(map(str, geosets))
        if code in ('too_many_geosets', 'too_many_hierarchy_nodes'):
            label = 'geosets' if code == 'too_many_geosets' else 'hierarchy nodes'
            lines.append(f"Model has {item['count']} {label}; {target} allows {item['limit']} "
                         f"({item['over']} over)")
        elif code == 'missing_mesh':
            lines.append(f"Node {item['node']} '{item['node_name']}' uses mesh {item['mesh']}, "
                         f"but {where} has {item['meshes']} meshes")
        else:
            text = _kMeshMessages[code].format(target=target, **item)
            lines.append(f"Mesh {item['mesh']} '{item['name']}' {text} in {where}")

    for code, total in sorted(report['issue_counts'].items()):
        if total > covered.get(code, 0):
            lines.append(f"{total - covered.get(code, 0)} more '{code}' findings are not listed")
    return lines
