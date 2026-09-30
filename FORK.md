# Blender-Sith Agentic

A focused community fork of [Blender-Sith by Crt Vavros / smlu](https://github.com/smlu/blender-sith), based on upstream commit [`ecc90131bcfdeabd400225e8a72ce94cbff1ec5c`](https://github.com/smlu/blender-sith/commit/ecc90131bcfdeabd400225e8a72ce94cbff1ec5c). The original Git history, copyright notices and GPL-3.0-or-later license are retained. Fork changes were developed by Coda in collaboration with Andrei for the JK/MotS Model Workshop.

Fork release identifier: `v1.0.1-coda.1`; Blender extension manifest version: `1.0.1`, display name: **Blender Sith (Coda)**. The extension ID remains `sith`, so use it as an alternative to the upstream extension rather than installing both in the same Blender extension repository.

Source distribution: [GitLab](https://gitlab.com/codalilitari/blender-sith) and [GitHub](https://github.com/CodaLilitari/blender-sith). This is a source fork with upstream Git history retained. The GitHub repository is standalone and is not attached to GitHub's upstream fork network.

This fork improves model export and scripted metadata assignment. It does not modify Blender itself or supply a new MCP server. The upstream UI and import/export entry points remain available. The project-specific regression scope is Jedi Knight: Dark Forces II and Mysteries of the Sith single-player and multiplayer models, using 3DO 2.1 and KEY; it does not cover level authoring.

## Changes

### Preserve the normals Blender actually displays

The upstream exporter merges vertices by position and color and writes BMesh vertex normals. That loses sharp-face, sharp-edge and custom corner-normal distinctions. Two disconnected triangles sharing positions can collapse six vertices into four, assigning the wrong normals to corners. A flat-shaded cube becomes eight smoothed vertices.

SMLiberator reported the sharp-edge/automatic-merge problem in [upstream issue 13](https://github.com/smlu/blender-sith/issues/13) on 10 October 2025, including the tradeoff between preserving discontinuities and the exported vertex budget. This patch implements normal-aware merging and checks that budget consequence explicitly; it does not claim the original discovery or introduce the optional merge-control UI proposed in that report.

The fork reads `Mesh.corner_normals` in the original polygon/loop order and uses position, color **and normal** as the vertex key. Coincident corners merge only when all three agree. UVs remain independently indexed. A flat or all-sharp cube exports 24 vertices; a smooth cube still exports eight. This is necessary vertex splitting, so inspect the exported vertex budget after hardening edges.

The read is based on Blender's [documented corner-normal representation](https://docs.blender.org/api/5.0/bpy.types.Mesh.html#bpy.types.Mesh.corner_normals). It covers normals already present on the source mesh; modifier evaluation is a separate issue below.

### Make hexadecimal metadata predictable

`HexProperty` now normalizes before applying its digit budget. A four-digit field accepts `FFFF`, `0xFFFF` and zero-padded equivalents without RNA string-length callback errors. Values use uppercase canonical digits and the existing even-digit padding. Negative, malformed and out-of-range values leave the previous value unchanged.

Unset properties now expose their declared defaults: KEY high-priority node types are `FFFF`, hierarchy node type is `01`, and unspecified node flags are zero. This is an intentional behavior correction; previously the custom getter returned an empty string even when a default was declared. Saved explicit values remain explicit. Direct writes to Blender's raw ID-property dictionary bypass the setter, so use the registered properties in scripts.

## Validation

Run [the background regression suite](docs/agent-use.md). It creates synthetic geometry and a small animation, exports files and parses them back. It needs no commercial game data, model assets or network. Retained machine-readable results are [upstream](tests/evidence/upstream-bpy-5.1.2.json) and [fork candidate](tests/evidence/coda-bpy-5.1.2.json); both record the same harness hash and their exact source hashes.

On Blender's `bpy` 5.1.2 runtime, the same ten checks returned:

| Check | Upstream | Fork |
|---|---:|---:|
| Disconnected coincident vertices with different normals | Fail | Pass |
| Flat-shaded cube | Fail | Pass |
| Smooth-shaded cube | Pass | Pass |
| Smooth cube with sharp edges | Fail | Pass |
| Custom per-corner normals | Fail | Pass |
| Mixed n-gon and disconnected faces with distinct corner normals | Fail | Pass |
| UV seams, absolute UVs, materials, byte colors, face flags/modes | Pass | Pass |
| Parent/child hierarchy and model offset | Pass | Pass |
| Hex metadata prefix, default and range behavior | Fail | Pass |
| Animated KEY serialization/reparse | Pass | Pass |

Source mesh snapshots and add-on source hashes remained unchanged during the tests. Numeric normal comparisons allow the six-decimal precision of the 3DO writer. The existing sRGB byte-color export behavior is preserved; this patch does not redefine color management. Results are evidence for these synthetic contracts, not retail runtime, multiplayer interoperability or every Blender version. The upstream minimum is Blender 5.0; this run used 5.1.2.

The [original four-observation workflow probe](tests/evidence/remaining-limitations-bpy-5.1.2.json) was also rerun against this candidate. It reported the normal and metadata problems resolved, with modifier evaluation and shared-mesh duplication still observed. That observation run is deliberately distinct from the ten-check regression suite.

## Known limitations and working alternatives

- **Unapplied modifiers:** the exporter still reads the base mesh. Use a prepared export duplicate with the desired modifiers applied or an evaluated-mesh conversion before exporting. Keep the editable original. Weighted-normal modifiers must be applied if their result is to become source custom normals.
- **Object transforms:** this patch does not change transform or radius code. Apply non-uniform or negative scale on an export duplicate, inspect its normals and winding, and recheck hierarchy offsets. The normal regression fixtures use unit scale.
- **Shared mesh datablocks:** two hierarchy objects can still export two mesh records. That is an optimization opportunity, not proof the model is invalid. Object scale and mesh properties must be considered before introducing reuse.
- **Additional geosets/LODs:** the Blender scene importer remains limited to geoset 0. Preserve other geosets through a format-aware writer or a separately checked process.
- **3DO 2.2:** an additional synthetic experiment found an existing writer/reader disagreement for RGB face extra light: the writer uses a parenthesized triple, while the loader expects three space-separated values. Upstream labels 2.2 as IJIM RGB. It is not fixed or covered by the ten passing JK/MotS 2.1 checks.
- **Engine appearance:** normal correctness in an exported file does not certify a renderer's lighting behavior. Calibrate material/UV behavior and test the actual engine build and character actions before releasing models.

## License and attribution

Blender-Sith is Copyright (C) 2019–2026 Crt Vavros. Original and modified add-on code remains under [GPL-3.0-or-later](LICENSE). The new regression script carries the same license. No proprietary game data, generated character model, model weights, private transcripts, or third-party asset pack is included.
