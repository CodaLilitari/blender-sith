# Retail model budget and detail levels

`sith.audit.budget.auditBudget` counts a native 3DO against the limits of the
retail JKDF2 and MOTS engines. The 3DO exporter runs it on every 2.1 export.
`sith.model.setDetailLevels` writes up to four identical geosets.

## What the budget checks

JKDF2 and MOTS load text 3DOs with `rdModel3_LoadEntry`. OpenJKDF2
reconstructs it from retail JK.EXE 1.01 in `src/Primitives/rdModel3.c`. MOTS uses
the same loader and the same limits.

| Limit | Value | Engine rule |
| --- | --- | --- |
| Vertices per mesh | 512 | `rdModel3_LoadEntry` fails when `numVertices > 0x200`. |
| Texture vertices per mesh | 768 | `rdModel3_LoadEntry` fails when `numUVs > 0x300`. |
| Faces per mesh | 512 | `rdModel3_LoadEntry` fails when `numFaces > 0x200`. |
| Corners per face | 1 to 24 | `rdModel3_LoadEntry` fails on 0 or more than 24. |
| Geosets per model | 4 | `rdModel3` holds `rdGeoset aGeos[4]`. The loader does not check the count, so a fifth geoset overwrites the rest of the model record. |
| Hierarchy nodes | 64 | Each puppet track keeps `aCurKfNodeEntryNums[64]` (`RDPUPPET_MAX_NODES`). JK clears one entry per node; OpenJKDF2 notes that the bound check in `rdPuppet_ClearTrackNodes` came later, with Grim Fandango. |

The audit also reports two lookups the engine does without a bound check:

- `material_out_of_range`: a face's material index is not -1 and not below the
  number of materials. Retail reads `aMaterials[index]`; OpenJKDF2 marks its
  clamp as added.
- `missing_mesh`: a hierarchy node uses a mesh index that a geoset does not
  have. `rdModel3_DrawHNode` draws `aMeshes[node->meshIdx]` of the selected
  geoset.

The loader sets no limit on the number of materials, on meshes per geoset or on
totals per geoset. The report gives those as counts.

## Why

A mesh over a per-mesh limit stops the model from loading. Andrei's largest
character meshes sit exactly at the limits: 512 vertices, 768 texture vertices
or 512 faces.

The fork's exporter merges corners only when position, normal and color all
match. It keeps vertices separate at sharp edges and custom normals, and it adds
texture vertices at UV seams. The Blender mesh can be under a limit while the
exported mesh is over it, without anyone editing the mesh. The exporter therefore
checks the model it is about to write, after merging and splitting.

## Detail levels

JKDF2 and MOTS choose a geoset by camera distance. `sithRender_RenderThings`
switches on the model's geoset count and the level's LOD distances. Andrei wants
full detail at every distance. Before this option, three identical geosets were
added to each release outside Blender.

`setDetailLevels(model, count)` replaces the geosets with `count` independent
copies of geoset 0, from 1 to 4. It does not change the radius, insert offset,
materials or hierarchy. Every copy has the same meshes in the same order, so node
mesh indices stay valid. The **Detail Levels** export option calls it after the
model is built.

## Run it from Python

```python
from sith.model.model3doLoader import load3do
from sith.model import setDetailLevels
from sith.audit.budget import auditBudget, budgetWarnings

model, _ = load3do('character.3do')
report = auditBudget(model, 'JKDF2')       # or 'MOTS'; 'IJIM' and 'GRIM' give counts only
print(report['status'])                    # within_limits, over_limits or counts_only
for line in budgetWarnings(report):
    print(line)

setDetailLevels(model, 4)                  # four identical geosets
```

In Blender, the 3DO exporter takes the same checks as options:

```python
from sith.model.model3doExporter import export3do
from sith.model.model3doLoader import Model3doFileVersion

warnings = []
export3do(obj, 'character.3do', Model3doFileVersion.Version2_1, True, False, True,
          detailLevels=4, failOverBudget=False, warnings=warnings)
```

A 2.1 export adds one warning per problem to `warnings`. With
`failOverBudget=True` it raises `ValueError` before writing any file. Other
formats are not checked.

## Read the result

The report is plain data, like `sith.key.modelBinding.auditModelKey`:

| Field | Meaning |
| --- | --- |
| `target`, `limits` | The target and its limits. `limits` is `None` for IJIM and GRIM. |
| `status` | `within_limits`, `over_limits`, or `counts_only` for IJIM and GRIM. |
| `geoset_count`, `hierarchy_nodes`, `materials` | Model-wide counts. |
| `geosets` | One entry per geoset: `meshes`, `vertices`, `texture_vertices`, `faces`, the `largest` mesh for each count and for `face_corners`, and `mesh_counts` for every mesh. |
| `issue_counts` | The number of findings of each code, including those not listed. |
| `findings` | At most 32 entries. Each names its `geoset`, `mesh` index and `name`, and for limits the `count`, `limit` and `over`. Face findings add `face`. |
| `findings_truncated` | True when `issue_counts` holds more findings than `findings`. |

Finding codes: `too_many_vertices`, `too_many_texture_vertices`,
`too_many_faces`, `too_many_face_corners`, `empty_face`, `too_many_geosets`,
`too_many_hierarchy_nodes`, `material_out_of_range`, `missing_mesh`.

`budgetWarnings(report)` gives one line per finding. A finding repeated in
several geosets, such as in identical detail levels, becomes one line that lists
the geosets:

```text
Mesh 0 'torso' has 600 vertices; JKDF2 allows 512 per mesh (88 over) in geosets 0, 1, 2, 3
```

## Limits

- The audit counts what is stored. It does not run the engine. A model within
  these limits can still fail for other reasons, such as a missing MAT.
- It does not check that face corners index existing vertices or texture
  vertices, or name lengths. `save3do` warns about long names.
- Only puppet tracks use the 64-entry array; `rdThing` sizes its joint arrays by
  the model. The audit reports the limit for every model, but a model that no
  puppet animates is not bound by it. Every character has a puppet.
- IJIM and GRIM limits are not sourced here. Their reports have counts and no
  verdict.
- `setDetailLevels` copies geoset 0. It does not make reduced meshes. Blender
  still imports geoset 0 only.
- The per-frame renderer limits of OpenJKDF2 (`RDCACHE_MAX_TRIS` and similar) are
  not model limits and are not checked.
