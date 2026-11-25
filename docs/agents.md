# Agent guide

This page is for AI agents, and the people who direct them, taking a Jedi Knight (JKDF2) or
Mysteries of the Sith (MotS) character through Blender Sith (Coda). It says what to run at each
stage, the rules to keep, how to read each report and how to report results. The exact commands,
runtimes and launchers are in [agent usage](agent-use.md). The MCP setup is in [MCP](MCP.md). The
real faults behind each check are in the [modeling workflow](modeling-workflow.md).

The audits, the reseat pass and the export options this guide uses (the retail budget, **Detail
Levels**, dithering, **Avoid Fullbright Colours** and the two editions) are in this source tree,
not in the 1.2.0 release. Use matching source.

## Rules

1. **No game data in repositories or published reports.** Do not commit or attach stock KEYs, MATs,
   CMPs or 3DOs from JK or MotS, the user's character files, or textures. Tests build synthetic
   models and KEYs in code, or use small hand-written files under `tests/tv/`. A published report
   names files and gives numbers; it does not carry the files. Job logs and receipts hold absolute
   local paths: review them before you publish anything from them.
2. **Never edit the user's originals.** Work on copies in a new folder. The audits do not change
   their inputs. The MCP tools copy their inputs into a new job. `reseatKeyFiles` writes only into
   the folder you name, `reseat_keys` only into its job's `reseated` folder, and **Reseat KEYs**
   only into its **Output Folder**. None of them replaces a file: `reseatKeyFiles` and **Reseat
   KEYs** refuse a folder that already holds a file or folder named like one of the KEYs.
   `removeFaces` and `reseatKey` return new objects; save them to new paths.
3. **Before and after on the same sample points.** Use the same KEYs, step, `toEnd`, cameras,
   reference pose and floor for both runs, and run the whole set. A fix that looks right in the
   flagged poses can open others: the first hip rule of the reseat pass did.
4. **Both editions.** A JK/MotS character ships a 16-bit RGB565 edition and an 8-bit Indexed
   edition with the same file names, in separate GOBs, from the same bake. Each is viewed in its
   own renderer. The 16-bit edition has no texture transparency: cut-outs such as hair cards are
   opaque there, and the export warns "The 16-bit edition discards alpha from ..." for each such
   image. Faces with the **Translucent** flag still blend in both.
5. **Retail acceptance is a game test.** A person or a test rig runs retail JK.EXE 1.01 with no
   engine patch, from the exact GOBs, in software 8-bit and in 3D 16-bit, and a second player
   joins. OpenJKDF2 is a fast check, not acceptance. No audit is acceptance.
6. **Reproduce before you change.** Rebuild the previous release from its sources and compare it
   byte for byte before you change anything.
7. **Say what a change costs.** A GOB's KEYs replace the stock KEYs for every player model in that
   install. Reseated stock KEYs move the other characters' joints too.

## The stages

The [modeling workflow](modeling-workflow.md#the-stages) explains each stage. This table says which
audit to run.

| Stage | Run | Python | MCP tool | Blender |
| --- | --- | --- | --- | --- |
| 1. References | No audit. Render the surfaces the references hide. | | | |
| 2. Body and scale | Floor contact on the engine floor, compared with stock Kyle KEY by KEY | `auditFloorContact(model, sequences, floor=thingFloorHeight(model, 0.065))`, crouch KEYs on `thingFloorHeight(model, 0.065, crouching=True)`; `lowestPoints` | `audit_ground` | **Audit Files**: **Floor Contact** |
| 3. Rigid pieces | Cut lint in the cutting pose; piece colours | `auditSegmentation`, `pieceColours`, `removeFaces` | `audit_segmentation` | **Colour Pieces** |
| 4. Caps and pivots | Joint gaps, then close-ups of the worst poses | `auditArticulation` | `audit_articulation` | **Audit Files**: **Joint Gaps** |
| 5. Textures and editions | Both editions in one export; dither the skin only, through a `sith_dither_mask_<cel>` image on each skin material | `export3do(..., textureEditions=True, textureDither=True)`, in Blender's Python, with the masks on the materials; `textureAvoidFullbright=True` where skin and cloth must take shadow | | 3DO export: **8-bit and 16-bit Editions**, **Dither Indexed Textures**, **Avoid Fullbright Colours** |
| 6. Hierarchy and node plan | Budget, binding, track slots of every character in the pack | `auditBudget`, `auditModelKey`, `trackRegistry` | `audit_budget`, `audit_model_key`, `audit_track_slots` | **Check Retail Limits** |
| 7. Animations | Seat offsets and joint gaps; reseat; run them again; KEY lint; floor | `seatOffsets`, `auditArticulation`, `reseatKeyFiles`, `lintKeyHeader`, `scanKeyPops`, `auditFloorContact` | `audit_articulation`, `reseat_keys`, `lint_keys`, `audit_ground` | **Audit Files**, **Reseat KEYs** |
| 8. Packaging | Budget on the exported 3DO with 4 detail levels | `auditBudget`, `setDetailLevels` | `audit_budget` | **Check Retail Limits** |
| 9. Acceptance | Game tests by a person or a rig | | | |

Layered poses, priorities, carried weapon meshes, the camera reading and sampling a KEY's last
interval (`toEnd=True`) run only from Python. By default a KEY is sampled up to `FRAMES - 1`; the
engine plays on to `FRAMES`. `step` must be a positive finite number of frames.

- **The floor (stage 2).** `auditFloorContact` measures against the `floor` you pass. Without one
  it uses the rest pose's lowest point, which can sit a few thousandths of a unit off the engine
  floor. Pass `thingFloorHeight(model, moveSize)` with the player template's movesize (0.065 for
  stock `walkplayer`), and give the crouch KEYs the crouch floor through
  `keySequence(..., floor=...)`. `audit_ground` and **Floor Contact** use the engine floor by
  default. State the floor in every report.
- **Masks and fullbright entries (stage 5).** Without a `sith_dither_mask_<cel>` image a material
  dithers every texel the palette has no close colour for, not only skin. Dithering never adds a
  fullbright entry, one the CMP never darkens, but nearest colour can still pick one, and it glows
  in shadow. `textureAvoidFullbright=True` (**Avoid Fullbright Colours**) keeps them out of every
  Indexed texture of that export, for skin and cloth that must take shadow; export a texture meant
  to glow, such as eyes, separately without it. The fullbright set comes from the CMP given at
  export, which needs its light table.

## Choose the reference pose

The joint gaps, the seat offsets and the cut lint measure from the pose in which the pieces were
cut. The report compares the chosen joint frames with rest and flags a reference KEY that drives no model nodes. A plausible result can still come from the wrong cut pose, so state it in every report.

- **Rest pose** (the default): a model authored in its rest pose, such as Mandalorian Red.
- **Stand KEY with zero rotations**: characters cut with every joint straight at a stand KEY's
  seats, such as Darth Lilitari, Lilivane and Biddy Blas. Use the stand KEY the character was cut
  against: stock `kystand1` for Darth Lilitari, Biddy Blas's own `kystand1`. A reseat can rewrite
  the character's own copy of `kystand1` too, so for a reseated set load the stand KEY the pieces
  were cut against from its own path, not from the reseated folder.

| Route | Rest pose | Stand KEY with zero rotations |
| --- | --- | --- |
| Python | `reference` left out | `referencePose(model, stand, 0, zeroRotations=True)` or `zeroRotationPose(model, stand=stand)`: they build the same pose, and either works for both audits |
| MCP | `reference_key` left out | `reference_key` and `zero_rotations: true` |
| Blender **Audit Files** | **Reference Pose**: **Rest Pose** | **Reference KEY** with **Zero Rotations** |

## Read a report

Each audit report has a `status`, `issue_counts` (totals per code, including findings not listed)
and at most 32 detailed `findings`, with `findings_truncated` when there were more.
`keyPriorityReport` only describes, and has no status. The
[audit index](audits/README.md#statuses) lists every status.

- **A count is a lead, not a verdict.** Look at every flagged pose up close before you call it a
  fault or change anything. On Mandalorian Red, knee counts in the hundreds were lines of sight
  into the crease of a bent knee: open air, not a hole. One or two lines in a pose are usually a
  sliver at a rim. Dozens are an opening you can see.
- **Findings are common on a sound character.** The cut lint's `review` findings are mostly the
  normal overlaps at joints; start with `high` (status `suspect`). `seatOffsets` usually reports
  `off_seat` for a set of stock KEYs, because most of them move the root and the torso carrier.
  Floor findings in jumps, falls, deaths and swims are mostly by design, and stock Kyle is not clean
  either: compare with Kyle KEY by KEY.
- **Know what a check cannot see.** The joint sweep sees gaps. It does not see one piece pushing
  through another, or dark polygons that read as holes. Poses are sampled one at a time: no puppet
  timing, fades, aim or physics.
- **The MCP job status is not the verdict.** `status: passed` means the audit ran and its result
  was accepted. The verdict is `audit.<section>.status`.
- **Unreadable KEYs are listed, not fatal.** Check `audit.inputs.keys.skipped`, the
  `unreadable_key` findings of the KEY lint and the `unreadable` rows of `reseat_keys` before you
  call a run complete. When no KEY can be read, the joint-gap, floor and track-slot jobs, and the
  cut lint with `key_dir`, end as `process_failed`; `lint_keys` and `reseat_keys` end as `passed`
  with status `review`.

## Report what ran

A report that someone can act on says:

- **What ran:** the function or tool, the add-on source by commit or package hash, the inputs by
  file name with their SHA-256, the parameters (step, `toEnd`, KEY subset, reference pose and its
  stand KEY, floor), the runtime and the time it took.
- **What did not run:** checks you skipped, KEYs you left out with `keys`, unreadable KEYs, jobs
  that ended in `timeout`, platforms and renderers you did not test, and every game test still to
  do. Write "not run", not nothing.
- **Before and after:** both counts, per joint and per KEY, on the same sample points. Never
  compare counts from different references, steps or KEY sets.
- **Statuses as reported:** quote them exactly. "The audit passed" is not "the model passed".
- **Corrections:** when a close-up shows that a counted fault is open air, say so, and correct
  any earlier report.

## Python

The audits run in ordinary Python 3.11+ with NumPy, without Blender. Load the model and the KEYs
once and pass them to each check. The [modeling workflow's Python pass](modeling-workflow.md#running-the-audits-from-python)
runs the budget, the cut lint, the KEY lint, the seat offsets, a few KEYs of the joint sweep and
the floor in one script. The pieces:

```python
from sith.model.model3doLoader import load3do
from sith.key.keyLoader import loadKey
from sith.audit.articulation import auditArticulation, loadKeyFolder, referencePose, seatOffsets
from sith.audit.ground import auditFloorContact, keySequence, thingFloorHeight
from sith.audit.keylint import Character, lintKeyHeader, scanKeyPops, trackRegistry

model, _ = load3do('character.3do')
errors = []
keys = loadKeyFolder('key', errors=errors)         # {file name: KEY}; unreadable files listed in errors
stand = loadKey('stock/kystand1.key')              # the stand KEY the pieces were cut against
reference = referencePose(model, stand, 0, zeroRotations=True)

gaps = auditArticulation(model, keys, step=2.0, reference=reference)
seats = seatOffsets(model, keys, reference=referencePose(model, stand))
print(gaps['status'], gaps['totals'], seats['status'])

standing = thingFloorHeight(model, 0.065)          # the engine floor for stock walkplayer
crouching = thingFloorHeight(model, 0.065, crouching=True)
crouch = ('kycrchgn.key', 'kycrchbk.key')
floor = auditFloorContact(model, [keySequence(key, floor=crouching if name in crouch else None, name=name)
                                  for name, key in keys.items()], floor=standing)
print(floor['status'], floor['issue_counts'])

custom = loadKey('move.key', strictTracks=False)   # read the way the engine reads it
print(lintKeyHeader(custom, model, target='JKDF2')['status'], scanKeyPops(custom, model)['status'])

other, _ = load3do('other.3do')
pack = trackRegistry([Character('Mine', model, list(keys.values())),
                      Character('Other', other, list(loadKeyFolder('other_key').values()))])
print(pack['status'], pack['issue_counts'])
```

A reseat writes only the KEYs that change, into a new folder, and checks each one:

```python
import glob
from sith.key.keyReseat import reseatKeyFiles

report = reseatKeyFiles(model, sorted(glob.glob('key/*.key')), 'reseated', standKeyPath='stock/kystand1.key')
print(report['status'], report['changed_keys'], report['check']['failed_keys'])
```

`reseatKeyFiles` refuses an output folder that already holds a file or folder named like one of
the KEYs, so give it a new one. Then run `auditArticulation` again on the written KEYs with the same
step, reference and stand KEY. Each audit's page in the [audit reference](audits/README.md) lists
its options and report fields.

## MCP tools

The optional MCP companion runs each audit as a bounded job on copies of allowlisted files. Its
13 tools:

| Tool | Use |
| --- | --- |
| `server_info` | Configured roots, limits and tools. Launches nothing. |
| `inspect_3do` | Counts of one 3DO by geoset, with triangles, quads and n-gons. |
| `audit_model_key` | One 3DO against one KEY: the slot each track addresses. |
| `convert_3do_to_blend` | A new geometry-only `.blend` of geoset 0. |
| `run_regression`, `build_extension` | The synthetic regression harness; a reproducible extension ZIP. |
| `audit_budget` | Retail limits of a 3DO file. |
| `audit_segmentation` | Cut lint. |
| `audit_articulation` | Joint gaps and seat offsets. Slow: give `keys` or a larger `step` first. |
| `audit_ground` | Floor contact and contact slip. |
| `lint_keys` | KEY header, track table and pops; the binding audit with `model_path`. |
| `audit_track_slots` | Track slots shared by 2 to 8 characters in one pack. |
| `reseat_keys` | Reseated copies of the KEYs that change, inside the job. |

A joint-gap sweep of a character cut at the stock stand KEY's seats:

```json
{"model_path": "/srv/authorized-models/mychar/mychar.3do", "key_dir": "/srv/authorized-models/mychar/key",
 "keys": ["kysbthr2.key", "kysbforc.key"], "step": 2.0,
 "reference_key": "/srv/authorized-models/stock/kystand1.key", "zero_rotations": true}
```

A reseat, then the same sweep on the written KEYs:

```json
{"model_path": "/srv/authorized-models/mychar/mychar.3do", "key_dir": "/srv/authorized-models/mychar/key",
 "stand_key": "/srv/authorized-models/stock/kystand1.key"}
```

The written KEYs are in the job's `reseated` folder, listed under `outputs`. To compare, run
`audit_articulation` on the source folder with `keys` set to the written names, then on a copy of
the written KEYs in an allowlisted folder, with the same `step` and `reference_key`. The job folder
is not under a read root unless you configure one there, and the server has no copy tool, so a
person or a file tool makes that copy. Install only written files that passed the check, after
review.

Parameters, limits, result fields and statuses are in [MCP audit tools](MCP.md#audit-tools). A full
sweep can exceed the default 120-second job timeout; configure about 300 seconds for full sweeps.

## Blender

The **Sith Audits** panel is in the 3D View sidebar (**N**), tab **Sith**
([USAGE](../USAGE.md#-auditing-models)):

- **Check Retail Limits** counts the active model as the 3DO exporter would build it, with the
  export dialog's options. It writes no file.
- **Colour Pieces** paints each piece one solid colour; **Restore Colours** puts the colours back.
- **Audit Files** runs the joint gaps, seat offsets, floor contact and KEY lint on a 3DO file and a
  KEY folder. Blender waits until it ends.
- **Reseat KEYs** writes reseated KEYs into the **Output Folder** you choose. It is the only panel
  command that writes files.

Each report goes to a Text datablock: a summary, then the complete report.
