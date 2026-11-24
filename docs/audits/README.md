# Audit reference

These are the model and KEY audits of Blender Sith (Coda). They read native 3DO and KEY files, or
the add-on's resource objects, and return plain data. They need no Blender, and none of them
changes its inputs. Each page says what its audit checks, the problem in real characters that led
to it, how to run it, how to read its result and what it cannot see.

For the order in which to run them while building a character, read the
[modeling workflow](../modeling-workflow.md#where-the-tools-fit). For agents, read the
[agent guide](../agents.md).

## The audits

| Page | What it checks | Python | MCP tool | Blender |
| --- | --- | --- | --- | --- |
| [Retail budget and detail levels](budget.md) | Vertices, texture vertices and faces per mesh, corners per face, geosets and nodes against the retail JKDF2/MOTS loader. Writes up to four identical geosets. | `auditBudget`, `budgetWarnings`, `setDetailLevels` | `audit_budget` | **Check Retail Limits**; 3DO export **Detail Levels** and **Fail Over Retail Limits** |
| [Cut lint](segmentation.md) | Surface a cut left in the wrong rigid piece: stray islands, foreign surface, covered surface. Removes faces and caps the opening. Colours each piece. | `auditSegmentation`, `zeroRotationPose`, `testPoses`, `pieceColours`, `removeFaces` | `audit_segmentation` | **Colour Pieces** and **Restore Colours** |
| [Joint gaps and seat offsets](articulation.md) | Lines of sight through each joint in every sampled pose. How far each KEY moves each node off its seat. | `auditArticulation`, `seatOffsets`, `referencePose`, `loadKeyFolder` | `audit_articulation` | **Audit Files** with **Joint Gaps** and **Seat Offsets** |
| [Floor contact, slip and camera reading](ground.md) | The lowest point against the engine floor. Planted feet sliding. How long a mesh reads from the third-person camera. | `auditFloorContact`, `thingFloorHeight`, `lowestPoints`, `auditCameraReading` | `audit_ground` (floor and slip) | **Audit Files** with **Floor Contact** (no slip) |
| [KEY lint](keylint.md) | The header and track table as the engine loads them. Pops between entries. Priorities. Track slots shared by characters in one pack. | `lintKeyHeader`, `scanKeyPops`, `keyPriorityReport`, `trackRegistry` | `lint_keys`, `audit_track_slots` | **Audit Files** with **KEY Lint** |
| [KEY reseat](reseat.md) | Puts the joints that stock KEYs push off their seats back on them, in the KEY data. Checks that nothing else moved. | `reseatKeyFiles`, `reseatKey`, `checkReseat`, `checkReseatFiles` | `reseat_keys` | **Reseat KEYs** |
| [MAT dithering and editions](materials.md) | Ordered dithering for Indexed MATs, limited by a mask. Both the 8-bit and the 16-bit edition from one export. | `encodeMat(..., dither=True)`, `export3do(..., textureEditions=True)` | none | 3DO export **8-bit and 16-bit Editions** and **Dither Indexed Textures**; MAT export **Dither** |

Two checks are documented elsewhere:

- **Model and KEY binding.** `sith.key.modelBinding.auditModelKey` reports the model node each KEY
  track addresses and the names that differ. See the
  [worked example](../MCP.md#will-this-key-animate-the-intended-joint). MCP tool:
  `audit_model_key`. `lintKeyHeader` includes it when given a model.
- **Engine-equivalent posing.** `sith.audit.pose` poses a model with KEYs the way
  `rdPuppet_BuildJointMatrices` does. Every audit that poses a model uses it. Its
  [module docstring](../../sith/audit/pose.py) gives the math and the engine rules.

The [Sith Audits panel](../../USAGE.md#-auditing-models) is in the 3D View sidebar, tab **Sith**.
The [MCP companion](../MCP.md#audit-tools) runs the audits as bounded jobs on copies of allowlisted
files.

## Reports

Every audit report is plain data and follows `auditModelKey`:

- `status`: the verdict, one word (below).
- `issue_counts`: the number of findings of each code, including findings not listed.
- `findings`: at most 32 detailed findings, in the order each page gives. `findings_truncated` is
  true when there were more. Each finding gives enough to find the problem again: the mesh and
  face, the node, the KEY and its frame.
- Sections of its own, such as `joints` and `keys` for the joint-gap audit or `meshes` for the cut
  lint. Each page lists them.

Lengths are JK units. Times are KEY frames. Angles are degrees.

## Statuses

| Report | Statuses |
| --- | --- |
| `auditBudget` | `within_limits`, `over_limits`; `counts_only` for IJIM and GRIM, whose limits are not sourced |
| `auditSegmentation` | `clean`; `review` (mostly the normal overlaps at joints); `suspect` (a `high` finding) |
| `auditArticulation` | `clean` (no daylight); `review` (daylight somewhere, or a joint could not be measured); `no_joints` (nothing could be measured) |
| `seatOffsets` | `on_seat`; `off_seat` (a KEY moves a node more than 0.00002 units off its seat, usual for stock KEYs); `no_tracks` |
| `auditFloorContact`, `auditCameraReading` | `clear`, `review`, `empty` (nothing to sample) |
| `lintKeyHeader`, `scanKeyPops`, `auditModelKey` | `consistent`, `review`, `incompatible`, `no_tracks` |
| `trackRegistry` | `consistent`, `incompatible` |
| `keyPriorityReport` | none: it describes which nodes a KEY drives at which priority |
| `reseatKey` | `reseated`, `unchanged`, `review` (a finding stopped a rule), `skipped` (the KEY cannot be rewritten safely) |
| `checkReseat`, `checkReseatFiles` | `pass`, `fail` |
| `reseatKeyFiles` | `reseated`, `unchanged`, `review`; `fail` when a written KEY failed its check: do not install that KEY |

The MCP companion adds a job status around each report. `passed` means only that the audit ran and
its result was accepted. The verdict is `audit.<section>.status`. In the `lint_keys` result a KEY
that cannot be read is listed with the status `unreadable` and counts as `review`.

## Read a count as a lead

A count is a lead, not a verdict. Look at each flagged pose, face or KEY before you change a model
or a KEY. On Mandalorian Red, knee counts in the hundreds were lines of sight into the crease of a
bent knee: open air, not a hole. One or two lines in a pose are usually a sliver at a rim; dozens
are an opening you can see. Compare before and after only on the same sample points: the same
KEYs, step, cameras and reference pose.

## The reference pose

The joint-gap audit, the seat offsets and the cut lint measure from the pose in which the pieces
were cut. A wrong reference gives plausible counts and no warning.

- The default is the rest pose stored in the 3DO. It suits a model authored in its rest pose, such
  as Mandalorian Red.
- Characters cut with every joint straight at a stand KEY's seats, such as Darth Lilitari,
  Lilivane and Biddy Blas, need that stand KEY with zero rotations:
  `referencePose(model, stand, 0, zeroRotations=True)`, or `zeroRotationPose(model, stand=stand)`,
  which builds the same pose; either works for both audits. Use the stand KEY the character was
  cut against. The MCP tools take
  `reference_key` with `zero_rotations: true`. In **Audit Files**, choose **Reference KEY** with
  **Zero Rotations**.

## Limits shared by every audit

- The engine rules come from OpenJKDF2's reconstruction of retail JK.EXE 1.01. Each module's
  docstring names the engine functions it follows. No audit runs the game.
- Poses are sampled one at a time. Puppet timing, fades, looping, physics and the player's aim are
  not run.
- A KEY played alone is sampled at `0, step, 2*step ...` up to `FRAMES - 1` at most
  (`keyFrameTimes`), as Red's sweep was. The engine also plays the last interval, up to `FRAMES`:
  a looping KEY shows every time below `FRAMES`, and a KEY that holds or fades at its end stops at
  `FRAMES`. The joint-gap audit, the seat offsets, the cut lint's `testPoses` and the floor and
  camera audits take `toEnd=True` to sample it too; the MCP tools and the panel use the default.
  The KEY lint's pop scan already samples up to `FRAMES`.
- `step` must be a positive finite number of frames.
- Thresholds are JK units chosen for Kyle-sized characters, from a few released characters. Scale
  them for other sizes.
- The audits are geometric checks of native files. A person, or a test in the game, still has to
  accept a model.
