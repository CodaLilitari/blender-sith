# Modeling workflow: from references to a retail release

This guide takes a Jedi Knight player character from reference images to a release that retail
JK.EXE 1.01 loads without an engine patch. It is for the person or agent doing the work. It
assumes the method every character below was built with: the body is cut into rigid pieces that
ride on Kyle's skeleton, and the stock animations drive it.

Each stage says what went wrong in a real release, how to avoid it and which check finds it. The
cases are Darth Lilitari (R9 to R9.10), Lilivane (R1 to R3), Biddy Blas (R3.4 to R3.6), the T-800
Hybrid (r5 to r7), Mandalorian Red (0.2.2) and the Red Sith Lightwhip (motion 03 R2). The numbers
come from their release notes. Those releases are private review candidates. No model, KEY,
texture or release file from them is in this repository.

Every check runs from Python without Blender. Most also run from the optional
[MCP companion](MCP.md) or from the **Sith Audits** panel in Blender
([USAGE](../USAGE.md#-auditing-models)). [Where the tools fit](#where-the-tools-fit) lists them by
stage. Agents should also read the [agent guide](agents.md).

## Contents

- [Words and units](#words-and-units)
- [The audits](#the-audits)
- [The stages](#the-stages) and [where the tools fit](#where-the-tools-fit)
  1. [References](#1-references)
  2. [Body and scale](#2-body-and-scale)
  3. [Cutting into rigid pieces](#3-cutting-into-rigid-pieces)
  4. [Joint caps and pivots](#4-joint-caps-and-pivots)
  5. [Textures and editions](#5-textures-and-editions)
  6. [Hierarchy and node plan](#6-hierarchy-and-node-plan)
  7. [Animations](#7-animations)
  8. [Packaging](#8-packaging)
  9. [Acceptance](#9-acceptance)
- [Symptom, likely cause, check](#symptom-likely-cause-check)
- [Reading audit results](#reading-audit-results)
- [Release checklist](#release-checklist)
- [Running the audits from Python](#running-the-audits-from-python)
- [Sources](#sources)

## Words and units

- **JK units (JKU).** Model and KEY positions are in JK units. Kyle stands 0.181 JKU tall. The
  T-800 and Biddy Blas notes write 0.001 JKU as "1 mm"; this guide gives those values in JKU.
  Darth Lilitari's notes give some distances in millimetres or centimetres at her real height of
  1.82 m; this guide keeps their units.
- **Piece.** One rigid mesh on one node. These characters have 16 to 41 meshes on up to 64 nodes.
- **Cut.** The boundary between two pieces.
- **Cap.** Geometry that closes the parent piece over a cut, so the child can turn on it. A
  **lid** is a shallow cap on a ball joint: shoulder, waist, neck or head.
- **Pivot.** In the release notes, the point a piece turns about: the origin of its node's joint
  frame, which the KEYs position. The 3DO hierarchy's PIVOT field is a different thing. It only
  moves the node's mesh away from that origin (`v_world = F_i (v + pivot_i)`; see
  [coordinates and pivots](blender-pipeline.md#coordinates-and-pivots)).
- **Seat.** Where a node sits on its parent when the character stands: its position in the
  standing KEY, or the model's rest position. A KEY that moves a node off its seat opens a
  close-fitting joint by that distance.
- **Adapter node.** A static node between a Kyle node and its parent. It holds the character's own
  bone length, so KEYs that drive Kyle's nodes play on the character's proportions. It comes from
  Biddy Blas's rig; Darth Lilitari R9 and Lilivane use it too.
- **Daylight.** A line of sight through a joint that meets no polygon of the posed model.
- **Sample points.** The KEYs, frames and cameras an audit uses. A before-and-after comparison
  must use the same ones.
- **Editions.** The 16-bit edition has RGB565 MATs, for 3D acceleration. The 8-bit edition has
  Indexed MATs with the game palette, for the software renderer. Both use the same file names and
  ship as separate GOBs.
- **Detail levels.** The 3DO's geosets. The engine picks one by the camera's distance.

## The audits

The audits read native files or the add-on's resource objects. They need no Blender and do not
change their inputs. Reports follow `sith.key.modelBinding.auditModelKey`: a `status`,
`issue_counts`, at most 32 detailed findings and totals for everything. The face removal and the
reseat pass return new objects, and `reseatKeyFiles` writes only the KEYs that change, into a
folder you name. `setDetailLevels` replaces the geosets of the model it is given and returns that
model. The [audit index](audits/README.md) lists every status.

| Check | Module | Entry points | Stages | Notes |
| --- | --- | --- | --- | --- |
| Engine-equivalent posing | `sith.audit.pose` | `poseModel`, `PoseLayer`, `PoseLayer.playKey`, `restPose`, `keyFrameTimes` | all | [module docstring](../sith/audit/pose.py) |
| Model and KEY slots | `sith.key.modelBinding` | `auditModelKey` | 6, 7 | [binding audit](MCP.md#will-this-key-animate-the-intended-joint) |
| Joint gaps (daylight) | `sith.audit.articulation` | `auditArticulation`, `articulationJoints`, `referencePose`, `KYLE_JOINTS` | 4, 7 | [articulation](audits/articulation.md) |
| Seat offsets | `sith.audit.articulation` | `seatOffsets`, `loadKeyFolder` | 7 | [articulation](audits/articulation.md) |
| Cut lint: islands, ownership, enclosure | `sith.audit.segmentation` | `auditSegmentation`, `zeroRotationPose`, `testPoses`, `pieceColours` | 3 | [segmentation](audits/segmentation.md) |
| Face removal with caps | `sith.model.model3doEdit` | `removeFaces` | 3 | [segmentation](audits/segmentation.md) |
| Retail limits | `sith.audit.budget` | `auditBudget`, `budgetWarnings`; export option `failOverBudget` | 6, 8 | [budget](audits/budget.md) |
| Detail levels | `sith.model` | `setDetailLevels`; export option `detailLevels` | 8 | [budget](audits/budget.md) |
| Indexed dithering with masks | `sith.material.matWriter` | `encodeMat(..., dither=True)`, `MatCel(..., ditherMask)`; export option `textureDither` | 5 | [materials](audits/materials.md) |
| Both MAT editions in one export | `sith.model.model3doExporter` | `export3do(..., textureEditions=True)` | 5, 8 | [materials](audits/materials.md) |
| KEY lint: JOINTS, pops, priorities, node registry | `sith.audit.keylint` | `lintKeyHeader`, `scanKeyPops`, `keyPriorityReport`, `trackRegistry`; `loadKey(..., strictTracks=False)` | 6, 7 | [keylint](audits/keylint.md) |
| Euler forms | `sith.key.keyEuler` | `nearestEuler`, `eulerTwin`, `unwrapEuler` | 7 | [keylint](audits/keylint.md) |
| Floor contact and contact slip | `sith.audit.ground` | `auditFloorContact`, `thingFloorHeight`, `lowestPoints`, `keySequence`, `CarriedMesh` | 2, 7 | [ground](audits/ground.md) |
| Camera reading | `sith.audit.ground` | `auditCameraReading`, `thirdPersonViewDirection` | 7 | [ground](audits/ground.md) |
| Reseat pass and its check | `sith.key.keyReseat` | `reseatKeyFiles`, `reseatKey`, `checkReseat`, `checkReseatFiles`, `keySeats` | 7 | [reseat](audits/reseat.md) |

In the Blender 3DO export dialog the export options are **Detail Levels**, **Fail Over Retail
Limits**, **8-bit and 16-bit Editions** and **Dither Indexed Textures** (see
[USAGE](../USAGE.md#-exporting-3do-models)).

## The stages

```text
references -> body and scale -> rigid pieces -> caps and pivots -> textures and editions
    -> hierarchy and node plan -> animations -> packaging -> acceptance
```

| Stage | Hand on | Gate before the next stage |
| --- | --- | --- |
| 1. References | A character sheet; a quad body with a 4K texture | Every surface the animations show is seen in a view, or repainted |
| 2. Body and scale | The body in the character's own proportions; an adapter rig | Feet meet the floor in the stock KEYs |
| 3. Rigid pieces | One mesh per piece, cut on planes | No `high` cut-lint finding; `review` findings looked at |
| 4. Caps and pivots | Pivots at cut centres; caps shaped about them | Joint sweep and close-ups of the worst poses |
| 5. Textures and editions | Per-piece MATs in both editions from one 4K master | Both editions viewed in their own renderer |
| 6. Hierarchy and node plan | Node indices, rest seats, custom track slots | Within 64 nodes; `trackRegistry` clean against every character in the pack |
| 7. Animations | Reseated stock KEYs; custom KEYs | Seat offsets, sweep, KEY lint and floor before and after |
| 8. Packaging | Two GOBs, four detail levels | Budget within limits; changed members listed |
| 9. Acceptance | Captures, retail runs, a multiplayer join | Both renderers in retail; a second player joins |

### Where the tools fit

The same checks run from three places. Python reaches every option. The
[MCP companion](MCP.md#audit-tools) runs fixed jobs on copies of allowlisted files. The
**Sith Audits** panel in the 3D View sidebar (tab **Sith**) runs them on the active model or on
files ([USAGE](../USAGE.md#-auditing-models)).

| Stage | Python | MCP tool | Blender |
| --- | --- | --- | --- |
| 1. References | none; render the hidden surfaces | none | none |
| 2. Body and scale | `poseModel`, `auditFloorContact`, `lowestPoints` | `audit_ground` | **Audit Files** with **Floor Contact** |
| 3. Rigid pieces | `auditSegmentation`, `pieceColours`, `removeFaces` | `audit_segmentation` | **Colour Pieces** and **Restore Colours** |
| 4. Caps and pivots | `auditArticulation` | `audit_articulation` | **Audit Files** with **Joint Gaps** |
| 5. Textures and editions | `encodeMat(..., dither=True)`, `export3do(..., textureEditions=True)` | none | 3DO export: **8-bit and 16-bit Editions**, **Dither Indexed Textures** |
| 6. Hierarchy and node plan | `auditBudget`, `auditModelKey`, `trackRegistry` | `audit_budget`, `audit_model_key`, `audit_track_slots` | **Check Retail Limits** |
| 7. Animations | `seatOffsets`, `auditArticulation`, `reseatKeyFiles`, `lintKeyHeader`, `scanKeyPops`, `keyPriorityReport`, `auditFloorContact`, `auditCameraReading` | `audit_articulation` (seat offsets included), `reseat_keys`, `lint_keys`, `audit_ground` | **Audit Files** (**Joint Gaps**, **Seat Offsets**, **Floor Contact**, **KEY Lint**) and **Reseat KEYs** |
| 8. Packaging | `auditBudget`, `setDetailLevels`, `trackRegistry` | `audit_budget`, `audit_track_slots` | 3DO export: **Detail Levels**, **Fail Over Retail Limits**; **Check Retail Limits** |
| 9. Acceptance | none; a game test | none | none |

Some checks run only from Python: `removeFaces`, `keyPriorityReport`, `auditCameraReading`, carried
weapon meshes (`CarriedMesh`) and layered poses (`PoseSequence`). The MCP tools and the panel play
each KEY alone. Contact slip of planted feet runs from Python and from `audit_ground` with
`contacts`. The panel's **Floor Contact** measures only the floor and floating feet.

## 1. References

**What went wrong.**

- Darth Lilitari R9.1: a dark smudge on her rear. In the back reference her tabard hides the middle
  of her rear, so the generator never saw that surface. It filled it with a soft-edged dark patch
  that spread onto the lower and inner cheeks. Straight from behind, the tabard covers it. In idle,
  run and falls the undersides of the cheeks turn toward the camera and the patch shows. The joint
  caps next to it take their colour from the surface around them, so they turned dark too.

**Do this.**

1. Start from a character sheet, such as Lilitari's: one figure in front, side and back views, in
   one pose. Lilivane's sheet was cut into front, left and back views and sent to Tripo (an
   image-to-3D generator) as one multi-view Smart Mesh run in Quad mode. It gave 13,882 faces,
   almost all quads, with a 4K texture. Darth Lilitari R9's body came the same way from front and
   back references: 11,952 faces, quads, a 4K texture.
2. Ask for quads. A JK face can have more than three corners, so a quad costs one face of the
   512-face per-mesh limit where two triangles cost two.
3. List what the views hide before you generate: here, the rear behind the tabard. The generator
   invents those surfaces.
4. After generating, look at the body from below, from behind and inside the limbs, at the angles
   the animations show, not only at the sheet's angles.
5. Repaint invented surfaces in the 4K master before any bake. Darth Lilitari R9.1 repainted every
   texel much darker than her skin with the nearest bright skin around it. A narrow strap was kept
   dark, so it reads as clothing.

**Checks.** No audit reads a reference. The joint sweep cannot see a dark polygon. Render the rear
and the inner limbs in idle, run, crouch and falls, from behind and both back three-quarters.

## 2. Body and scale

**What went wrong.**

- Darth Lilitari R4 to R8.2: the generated body was bent onto Kyle's joints with skinning. To fit
  Kyle, her arms were made 34 to 38% longer, her thighs 16% longer and her shins 17.5% shorter, and
  copies of faces were laid across the joints. The faults Andrei saw at the shoulder, the back hair,
  the legs, the boots and the rear all came from that body. R6 to R8.2 changed only the head and
  hair, so the faults stayed. R9 started again from a new body.
- Biddy Blas R3.4: his calves were made 20% longer. His feet still had to meet the floor in all 91
  stock KEYs, so every later leg change had to be measured again.
- Lilivane R1: she stands 0.0875 JKU tall, 48% of Kyle's height. Her hitbox and camera height are
  still Kyle's: the player template sets them, not the model.

**Do this.**

1. Keep the character's own proportions. Do not stretch limbs to Kyle's bone lengths, and do not
   bend the body onto his joints with skinning.
2. After cutting (stage 3), turn each piece as a whole into Kyle's rest direction. Nothing is bent
   and nothing is stretched.
3. Use an adapter rig. Between each Kyle node and its parent, a static adapter holds the
   character's bone length in place of Kyle's. The stock KEYs still drive Kyle's 18 nodes, so every
   animation plays, on the character's skeleton. Darth Lilitari R9 at rest:

   | Length | Darth Lilitari | Kyle |
   | --- | --- | --- |
   | upper arm | 0.26 m | 0.36 m |
   | forearm | 0.18 m | 0.28 m |
   | shoulder width | 0.27 m | 0.39 m |
   | thigh | 0.44 m | 0.46 m |
   | shin | 0.40 m | 0.48 m |
   | hip width | 0.18 m | 0.17 m |

4. Accept the trade, and say so in the README. Shorter arms put the hands closer to the body in
   animations written for Kyle's reach: the hand to the chin in idle, the two-handed saber grip,
   the gun aim. Weapons follow the hands.
5. Reduce dense parts to fit the per-mesh limits and rebuild quads after reducing. Place a part
   taken from another build (Darth Lilitari's head B) by fitting its scale and position to the
   body.
6. A character much smaller than Kyle needs ground-contact tracks. In every frame of Lilivane's 91
   KEYs her lowest point is moved to Kyle's; in the swim KEYs the centres of the bounding boxes are
   matched instead.

**Checks.**

- `sith.audit.pose.restPose` and `poseModel` pose the rig as the engine does.
- `sith.audit.ground.auditFloorContact` gives each KEY's lowest point against the floor.
  `lowestPoints` compares it with stock Kyle's, sample by sample. The stock KEYs are not clean on
  Kyle either, so compare KEY by KEY ([ground](audits/ground.md)).
- Through MCP: `audit_ground` with the engine floor (`floor: "engine"`, the default). Name the
  crouch KEYs in `crouch_keys`; it has none by default. In Blender: **Audit Files** with **Floor
  Contact**, whose **Crouch KEYs** default to `kycrchgn.key, kycrchbk.key`.

## 3. Cutting into rigid pieces

**What went wrong.**

- Mandalorian Red 0.2.1: the sculpt's arms hung at the sides, touching the body. Where the cut put
  arm surface into the torso, that surface rode on the torso. At rest the arm covered it. When the
  arm moved, it stayed behind in the air. On the T-visor model it was a long blade of forearm and
  hand armour down the left flank and two small loose pieces: 74 torso faces. On the no-helmet
  model it was a loose 24-face shard of the left hand by the hip. 0.2.2 removed them and closed the
  opening in the flank with 12 cap faces carrying the surrounding texture.
- Darth Lilitari R9 to R9.2, the "second shoulder": a fishnet lump stayed beside her arm when it
  came forward. Painting every piece a solid colour showed that the lump was the torso. The
  shoulder cut was an upright plane 1 cm outside the shoulder pivot, so the back and top of the
  shoulder stayed with the torso. The fishnet shell went to the arm only above a fixed height, so
  the inner sleeve near the armpit stayed too. A tall rounded cap, about 1.5 cm high, sat over the
  cut. R9.3 cut through the pivot and gave the arm the outer half of the shoulder and the whole
  sleeve.
- Lilivane R1: the upper arm was taken as everything within a fixed radius of the line from
  shoulder to elbow. The arm's real centre line runs inside that line, so the back and inner side
  of the upper arm stayed with the torso. It showed as a pale tab by the wing root when the arm
  swung forward. R2 took the arm about its measured centre line.
- Lilivane R1 to R3: the cut runs through the top of her belt. A thin strip of belt rides on the
  bottom of the torso and shows as a dark line when the torso lifts. Recolouring it drew a skin line
  across the belt at rest, which was worse, so it stays.

**Do this.**

1. Slice at planes through the joint pivots, so every cut edge lies exactly on a plane. Keep quads
   when slicing.
2. Give a limb its surface from the limb's measured centre line. Do not use the line between
   estimated joints, and do not split at a fixed height.
3. Where the source has a limb touching the body, expect limb surface in the body piece, and look
   for it.
4. Cut costume edges on their own boundary, such as the bottom of a belt or a hem, not through
   them.
5. To see which piece owns a surface, draw every piece in its own solid colour and capture the
   frame that shows the fault. In Blender, **Colour Pieces** does this for the active model, and
   **Restore Colours** puts the previous colours back.
6. Remove misplaced faces and close any opening with cap faces that carry the surrounding texture.
   Keep surface that belongs to the piece. On the Mandalorian's red-visor model the lint flagged
   torso surface under each shoulder plate. That is the torso's own shoulder, so it stayed.

**Checks.**

- `sith.audit.segmentation.auditSegmentation`, the cut lint
  ([segmentation](audits/segmentation.md)):
  - stray islands: pieces of a part that touch nothing else in it (the 24-face shard);
  - foreign surface: faces nearer another part's bone than their own (the forearm blade);
  - covered surface: one part's faces inside another part's volume.

  It lists, per mesh, the face indices to select. Run it in the pose the pieces were cut in:
  `reference=zeroRotationPose(model, stand=standKey)` for characters cut with Kyle's joints at
  zero rotation, as Darth Lilitari, Lilivane and Biddy Blas were. Start with `high` findings.
  `review` findings are mostly the normal overlaps at joints.
- `sith.model.model3doEdit.removeFaces` removes faces from a copy of the model and closes each
  opening with caps that take the surrounding material and texture vertices.
  `sith.audit.segmentation.pieceColours` gives each node, mesh or part a stable colour for the
  solid-colour view.
- Through MCP: `audit_segmentation`, with `reference_key` and `zero_rotations: true` for these
  characters. Each flagged mesh comes with its `high_faces` and `review_faces` (at most 256 of each
  in the result; all of them in `report.json`). Face removal runs only from Python.
- After removing faces, run the joint sweep again. On the T-visor model the blade had been covering
  pinholes. Removing it took the waist from 10 poses with daylight (16 lines) to 15 (24 lines), the
  left shoulder from 63 to 78 poses and the right elbow from 35 to 53. A floating blade is the
  bigger fault, so 0.2.2 took the pinholes.

## 4. Joint caps and pivots

**What went wrong.**

- Lilivane R1 and Darth Lilitari R9.1: steps at the knees and ankles in a bend, and on Darth
  Lilitari a radial star in the knee texture. Two causes:
  - Each cap was aimed from the pivot toward the average of the cut's vertices. With an off-centre
    pivot that laid the cap almost flat in the cut.
  - The hinge pivots (knees, ankles, elbows, wrists) were estimated joint positions, not the
    centres of their cuts. On Lilivane they sat up to about 1% of her height off centre. Darth
    Lilitari R9.2 moved hers by about 5 mm at the elbows, 3 at the wrists, 8 at the knees and 7 at
    the ankles, at her real height of 1.82 m.

  On Darth Lilitari the knees showed it most, because her stance has bent knees.
- T-800 r5 and r6: a lid stood behind the skull, and a plate on the skull swung out as a fin when
  the head nodded. The stock KEYs turn the head about its node's origin at the chin, 0.007 JKU in
  front of the neck column, by up to 58° down, 82° back and 96° sideways. Standing still, the head
  is already 30° down and 31° sideways against the neck. A column top under the back of the skull
  was therefore left standing whenever the head moved. r7 cut the column back with a sphere centred
  on that pivot and removed the plate. In the r7 notes' own render measure, the neck top standing
  behind the skull fell from 485 pixels per pose (r5) and 394 (r6) to 76. At idle, the
  90th-percentile distance from the exposed neck top to the head fell from 0.0068 JKU (r5) to
  0.0037.
- Darth Lilitari R9.3 to R9.10: with an arm raised or pulled across, the shallow shoulder lid shows
  as a pale patch on the back of the shoulder. Its texture first came from the area around the
  joint and baked in the dark of the glove, the bodice and the armpit. It now comes from the
  fishnet on her upper back, where the lid shows.
- Darth Lilitari R9.10 and Lilivane R3: a skin plug hanging from the torso into the hips, and a
  dome rising from the hips, both closed the waist in bent poses. In twisted stances the plug swung
  out through the back of the belt and the dome showed above it. Her torso turns about an axis
  near her spine, not through the middle of her waist, and the armed standing pose already turns
  it 32° on the hips and tips it 16°. Neither shipped.
- T-800 r7: a head-owned block that filled the space under the skull scored better on the lid
  measure, but at idle it read as a dark slab hanging off the skull. It was left out.

**Do this.**

1. Find where the engine really turns each joint: the node's origin, as the KEYs place it. Measure
   each joint's range over every KEY you will ship, as the child's rotation in its parent's frame
   from `sith.audit.pose`. A joint shaped about the wrong point shows a lid, a fin or a step.
2. Put each hinge pivot at the centre of its cut. Recompute the cut and the pivot until the pivot
   stops moving.
3. Shape caps about the real pivot:
   - a hinge cap leaves the cut straight along the limb, then closes;
   - a ball-joint lid keeps its points at the cut edge's distance from the pivot, so the child's
     edge slides over it as it turns.

   Keep every cap point inside the neighbouring piece's surface.
4. Keep lids shallow. Darth Lilitari's shoulder lid is at most 0.35 of the cut's radius high.
5. Texture each cap from the surface where it will show. When a cap samples the surface around
   its joint, ignore samples much darker than that surface's median colour (Darth Lilitari R9.1).
   A hip cap is then skin, and an elbow cap inside a black glove stays black.
6. Test plugs and fills in twisted and turned stances, not only in the bent pose that showed the
   gap.

**Checks.**

- `sith.audit.articulation.auditArticulation` casts lines of sight through each joint, for every
  pose, from eight cameras. A line that meets no polygon is daylight. Run it before and after on
  the same sample points ([articulation](audits/articulation.md)).
- Through MCP: `audit_articulation`. In Blender: **Audit Files** with **Joint Gaps**. Both default
  to the rest pose. For a character cut at a stand KEY's seats, pass that KEY with zero rotations
  (`reference_key` and `zero_rotations: true`, or **Reference KEY** with **Zero Rotations**). A
  wrong reference gives no warning.
- Lids, fins and steps are surface standing outside the body, not daylight. Capture the worst poses
  of each joint from the side and from behind (stage 9), and the deep bends.

## 5. Textures and editions

**What went wrong.**

- Darth Lilitari R9.10, two faults the joint sweep cannot see, because they are dark polygons and
  not daylight:
  - a dark disc on a bent knee (`kycrchgn` frame 19): the rounded caps at the knee end of both
    thighs were mapped to dark texels. R9.10 remapped them to the fishnet of their own thigh page;
  - a dark band under the upper hair when the head turns (`kyblock1` frames 8 and 11, `kystab`
    frame 3): the hair cap lifted off the hair curtain and showed the dark shell under it. R9.10
    added a 14-face hair underlayer that is rigid with the head.
- Lilivane R1: the atlas tool works on triangles only. Where an atlas seam crossed a quad, the
  quad had to be written as two triangles, because a JK face has one texture coordinate per
  corner. The first build was 18% triangles (2,610 of 14,608 faces). Moving the stray half of each
  split face into the atlas piece that holds the rest of the face, where that kept the winding and
  did not overlap, brought it to 998 triangles of 13,793 faces.
- 8-bit editions: the stock palette has no skin ramp, so nearest colour turns skin into bands.
  Coda's rule is nearest colour, with skin dithered between the two nearest palette colours. Even
  dithered, Darth Lilitari's skin is warmer in 8-bit than in 16-bit; that is the palette.
- Lilivane R1: her see-through wings are grey-violet in 8-bit. The software renderer blends
  see-through pixels through the game's 256-colour table, which has no good purples. In 16-bit they
  are purple.
- T-800 r6: the spine stack and lumbar extension drew in retail as a light hexagon at the waist and
  a light disc in the pelvis. r7 gave them the dark column swatch.

**Do this.**

1. Keep one 4K master texture and the UV mesh (an OBJ) that maps it. Paint the master, not the
   per-piece atlases (38 for Darth Lilitari R9.3, 41 for Lilivane). Keep the UV layout and the
   4096x4096 size.
2. Split the build into a geometry stage and a bake stage. A repaint then reruns only the bake,
   about ten minutes per model for Darth Lilitari and Lilivane, and gives both editions.
3. Bake each piece to its own 256x256 MAT with mipmaps. Heal quads that the atlas split.
4. Caps and lids sample the master near their joint. Painting there changes them too.
5. Write both editions from the same bake: RGB565 for 3D acceleration, and Indexed with the game's
   CMP for the software renderer. Dither the skin only, through a mask. Everything else takes the
   nearest colour.
6. Before repainting a released character, rebuild its MATs from their sources and check that they
   match the shipped MATs byte for byte. Biddy Blas's MAT build does.
7. View each edition in its own renderer.

**Checks.**

- In the 3DO export, **8-bit and 16-bit Editions** (`export3do(..., textureEditions=True)`)
  writes both MAT sets with the same names, into `mat8` and `mat16` by default. **Dither Indexed
  Textures** (`textureDither=True`) dithers the 8-bit edition, only where the material's
  `sith_dither_mask_<cel>` image allows. Outside Blender, `encodeMat(..., dither=True)` with a
  `MatCel` mask does the same ([materials](audits/materials.md)).
- Decode both editions with the CMP and compare them with the master.
- Render the deep bends and head turns. Dark polygons are not daylight.

## 6. Hierarchy and node plan

**What went wrong.**

- Lilivane R1: in the falling deaths her upper body ended up lying apart from her hips. Node 1, the
  root of the upper body, still sat where Kyle's pelvis is, much higher than her hips. Turning node
  1 swung her torso about a point above her waist. Node 1 now sits on her own hip node. The stock
  KEYs give nodes 1 and 2 the same position, which matches how Kyle is built.
- Lilivane R1 and R2 used `models.dat` entry 28, the same entry as Darth Lilitari. Her
  ground-contact tracks use nodes 47 and 48, which Biddy Blas's KEYs also animate. Each GOB would
  override parts of the other. The combined pack of Darth Lilitari and Lilivane gave them entries
  28 and 29.
- Custom tracks travel inside KEYs with the stock file names, and a pack holds one file per name.
  Two characters in one pack therefore play the same KEYs, and a track one of them adds moves the
  other's node at that slot too. The release notes say Biddy Blas cannot join Lilivane until her
  two tracks move to free node numbers. The registry check below finds more slots that clash.
- Darth Lilitari R9.9 and Lilivane R3: the torso carrier (node 1) and the torso (node 17) now rest
  at the KEY seat (Coda's fallback coordinates). Their two offsets cancel, so the rest pose is
  unchanged. It matters when an animation drives one of the two and not the other during a blend,
  which the lightwhip KEYs do.

**Do this.**

1. Keep Kyle's 18 nodes at indices 0 to 17 with his types, offsets, rest rotations and pivots. KEYs
   address nodes by index.
2. Put adapters, extra mesh pieces and ground nodes after them. Darth Lilitari R9 has adapters at
   19 to 23 and 25 to 35, and extra mesh pieces at 36 to 46, 51 to 53 and 57. Each extra piece is a
   child of its Kyle node with zero offset. The other indices up to 63 are empty fillers.
3. When a piece is over the per-mesh limits, split it into several meshes on extra nodes.
4. Keep the upper body's root, node 1, on the character's own hip.
5. Rest the nodes that KEYs position at their KEY seat.
6. Before you choose slots for custom tracks or extra pieces, list the slots that other KEYs in
   the install animate. Four stock KEYs (`kyfall`, `kyidle1b`, `kyidle2b` and `kysbsabr`, which
   declare JOINTS 19) animate index 18. The T-800 K1 alternate animates 24. Biddy Blas's KEYs
   animate 47 to 61 and 63, and Lilivane's animate 47 and 48. A track at an empty filler with no
   mesh below it moves nothing. A track at a slot where another character has a piece moves that
   piece.
7. Stay within 64 nodes. Each puppet track keeps one entry per node in a 64-entry array
   (`RDPUPPET_MAX_NODES`).

**Checks.**

- `sith.audit.budget.auditBudget`: node count, geosets and per-mesh limits. In Blender, **Check
  Retail Limits** counts the active model as the 3DO exporter would build it. Through MCP,
  `audit_budget` counts a 3DO file as stored.
- `sith.key.modelBinding.auditModelKey` for each KEY: the slot each track addresses, and whether
  the KEY's name for it matches the model's. Through MCP: `audit_model_key` for one KEY, or
  `lint_keys` with `model_path` for a folder.
- `sith.audit.keylint.trackRegistry`: the custom slots each character uses, `slot_collision`
  where two characters drive one slot with different tracks, and `slot_intrusion` where one
  character's track would move a piece of the other's model. Checked while writing this guide on
  the released files:
  - Darth Lilitari R9.10 with Lilivane R3: no findings.
  - Lilivane R3 with Biddy Blas R3.6: collisions at 47 and 48, and intrusions at 51 to 53, 57 to
    59 and 61, where Lilivane has hip and torso pieces.
  - Darth Lilitari R9.10 with Biddy Blas R3.6: intrusions at 51 to 53 and 57 in all 91 KEYs, and
    at 63 in 8. Biddy's adapter tracks there would move her extra torso pieces and her collar.
    Darth Lilitari R9's notes rightly call her empty fillers in Biddy's range harmless; her extra
    pieces at 51 to 53 and 57 sit in the same range.

  Through MCP: `audit_track_slots` with 2 to 8 characters.
- Pose the deaths, falls and swims with `sith.audit.pose`. They turn the root nodes.

## 7. Animations

### Stock KEYs and reseating

**What went wrong.** Some stock KEYs move a joint off its seat as well as turning it. The distances
were authored for Kyle, whose pieces overlap generously. A character cut into close-fitting pieces
opens by exactly those distances.

| Stock KEYs | Off their seat |
| --- | --- |
| The saber family (`kysb*`) | The hip piece 0.002 to 0.008 JKU below its seat under the torso, with the thighs pushed up into the hip to make up for it |
| The armed runs (`kyrun1`, `kyrun2`) | Both forearms about 0.004 JKU off the elbow |
| `kystab2` | The shoulders about 0.004 JKU |
| The fist walk and the three runs | The shoulders about 0.002 JKU; the same walk and runs move the neck and head about 0.002 |
| The armed walks | The torso about 0.001 JKU high, and each thigh dropped in turn for the stride |

Results of reseating, from the joint sweep (every second frame of the 91 stock KEYs, 1,745 poses,
eight cameras):

| Character | KEYs reseated | Before | After |
| --- | --- | --- | --- |
| Darth Lilitari R9.9 to R9.10 | 37 | Waist daylight in 152 poses (31 KEYs), 1,608 lines of sight | 71 poses (18 KEYs), 489 lines |
| Lilivane R2 to R3 | 42 | Waist in 195 poses (20 KEYs), 14,380 lines; elbows in 175 and 192 poses | Waist in 8 poses (3 KEYs), 26 lines; elbows none |
| Biddy Blas R3.5 to R3.6 | 13, adapter tracks only | Waist in 15 poses; `kysbforc` in 11 of 11 sampled poses | Waist in 4 poses; `kysbforc` none |

These are the released counts. Red's sweep sampled each line of sight at a pixel centre of a
360-pixel render. `auditArticulation` tests lines exactly, so at the edge of an opening it can
count a few more ([articulation](audits/articulation.md#limits)).

The first version of the hip rule gave the hip the thighs' whole average offset. It closed the
saber family, and captures of the flagged poses looked right. The full sweep then showed that it
had opened the armed walks. On Darth Lilitari, `kywalks` had daylight in 25 of 40 sampled poses
where R9.9 had none. On Lilivane, `kywalkg` went from 1,454 lines of sight to 3,977 and `kywalks`
from 800 to 3,767. In a walk the thighs drop for the stride, and that rule lowered the hip after
them. Neither build shipped.

Reseating cannot fix an opening that comes from rotation alone, with every joint on its seat.
After R9.10, Darth Lilitari's waist still opens in the choke KEY (`kychoke0`, 23 of 34 poses) and
the right strafe (`kystrafr`, 9 of 40).

**Do this.**

1. Before shipping a close-fitting character, pose every stock KEY on it. Run `seatOffsets` for how
   far each KEY moves each node off its seat, and `auditArticulation` for daylight.
2. Where seat offsets open a joint, reseat the KEYs with `sith.key.keyReseat.reseatKeyFiles`, the
   MCP tool `reseat_keys` or **Reseat KEYs** in Blender. Each writes a reseated copy of only the
   KEYs that change, never over a source KEY. The rules:
   - the arm, neck and head joints go back to their standing seats;
   - the hip piece moves between the torso and the legs, which stay exactly where the animation
     put them. Axis by axis in the hip's frame: where the hip's and the thighs' offsets point
     opposite ways, the smaller is taken out of both. A thigh push left after that goes to the
     waist, up to 0.0025 JKU. Nothing is pulled further apart than stock had it, and no sideways or
     front-to-back offset grows.
   - Lilivane's KEYs add an optional step: where a waist gap is left, the hip follows the torso up
     by at most 0.0015 JKU. It closed her walking slit and cost a little at the hip joints.
3. Run the reseat check (`checkReseat`) on every reseated KEY. All three routes run it on every
   written KEY by default (`check=True`, the **Check** option). No joint's orientation may change.
   The torso, thighs, calves and feet must stay where they were: within 0.00005 JKU between frames
   for Darth Lilitari and 0.00006 for Lilivane. Report how far the hip moved; on Darth Lilitari, up
   to 0.0074 JKU. A failed check (status `fail`) means that KEY must not be installed.
4. Run the whole sweep again on the same sample points and compare it KEY by KEY. A KEY can go the
   other way: on Darth Lilitari, `kysbtred` went from no waist daylight to two lines in each of four
   poses.
5. Know the cost:
   - A GOB's KEYs replace the stock ones for every player model in that install. Every character
     there gets the reseated joints.
   - Some characters keep the stock tracks byte for byte (Biddy Blas's rule, which his checkers
     enforce). Reseat those through their own adapter tracks instead. Biddy R3.6 rewrote the hip
     adapter's track (node 48) and added tracks for the two thigh adapters (54 and 60) in 13 KEYs.
     Stock tracks 0 to 17 stayed byte-identical.

**Checks.** `seatOffsets` and `auditArticulation` ([articulation](audits/articulation.md)), the
reseat check ([reseat](audits/reseat.md)), then the floor again with `auditFloorContact`: Lilivane
R3's ground-contact tracks were recomputed on top of the reseated KEYs. Through MCP:
`audit_articulation` (it adds seat offsets unless `seat_offsets` is false), `reseat_keys`, then
`audit_articulation` again on the written KEYs with the same `step` and reference. In Blender:
**Audit Files** with **Joint Gaps** and **Seat Offsets**, **Reseat KEYs**, then **Audit Files**
again.

### Custom KEYs

**What went wrong.**

- The lightwhip's KEYs had pops between entries. From each entry the engine moves each Euler
  component with that entry's own deltas until the next entry. A large change between entries, or
  a switch to the other Euler form of the same rotation, swings a joint through a pose nobody
  authored. Three solver fixes removed the pops:
  - the elbow may swing only so far per frame;
  - the hilt's roll is free once a stroke starts;
  - each frame keeps whichever of a rotation's two Euler forms lies nearer the last frame's.

  The R2 crack is written at 120 fps, so nothing swings out between its entries. Its way back to
  the stance is keyed hand by hand, not left to a late whole-joint blend that could flip.
- Coda's check flagged all 29 body KEYs of the earlier lightwhip set (Motion 02) for declaring
  JOINTS 18 or 20 against a 64-node model.
- In Motion 02's upper-body KEYs the torso sits off its standing seat by up to 0.006 JKU to her
  right and 0.003 forward. The whip plays a leg KEY and an upper-body KEY together, so it has to be
  audited on blended poses. For the R2 edition made for the SBX mod, the seam audit checked every
  move on the base KEYs it plays over: 1,176 blended poses. The waist was clean once her torso
  twist in one sweep was eased by 5°.
- Biddy Blas R3.4 lifted the swinging foot by sliding the foot joint up the calf, so in his walk
  the boot slid up his shin by about 0.0075 JKU. R3.5 took part of the lift from hip and knee bend,
  and the slide fell to about 0.004 JKU.
- Biddy Blas R3.5's walks and strafes use half-frame entries, which no stock KEY does. Read back
  from the running game, retail loaded every one exactly and played them smoothly.

**Do this.**

1. Declare JOINTS equal to the model's node count: 64 for a 64-node model. Retail stores a KEY's
   tracks in an array sized by JOINTS:
   - a track numbered at or past JOINTS is written outside that array, because
     `rdKeyframe_LoadEntry` does not check the node number;
   - a KEY with fewer JOINTS than the model has nodes reads past its array and then checks the
     bound (`rdPuppet_BuildJointMatrices`). It plays, but JOINTS 64 costs nothing.
2. Keep entries in time order and inside FRAMES. Use at most 8 markers: the loader fails above 8.
3. Keep each joint's Euler form continuous from entry to entry, and write more entries where a
   joint moves fast. `sith.key.keyEuler.nearestEuler` gives the triple nearest the previous one.
   In Blender, the KEY exporter does this for every rotation it converts (quaternion, axis-angle
   or another Euler order). YXZ Euler curves are written as authored, so keep those continuous
   yourself.
4. Lift a foot by bending the hip and knee, not by sliding a joint along its parent.
5. Audit the poses players see. Layer the KEYs with the priorities the COG or puppet uses:
   `PoseLayer.playKey(key, frame, priority)` gives COG `PlayKey`'s low and high priorities.

**Checks.**

- `sith.audit.keylint` ([keylint](audits/keylint.md)). Load custom KEYs with
  `loadKey(path, strictTracks=False)`, so the lint sees tracks the engine reads but the default
  loader refuses.
  - `lintKeyHeader`: tracks at or past JOINTS, tracks at 64 and above in a KEY with more than 64
    JOINTS, repeated tracks, entries out of order or past FRAMES, JOINTS below the model's node
    count, and a JOINTS 64 recommendation. It checks the header against the target's limits
    (markers, names, FRAMES and FPS) with or without a model. With a model it includes
    `auditModelKey`.
  - `scanKeyPops`, at quarter-frame steps: Euler flips, steps too wide for a straight line in
    Euler components, jumps at an entry, Euler-form switches that show in fades, angular-speed
    spikes and, with a model, jumps in node positions.
  - `keyPriorityReport`: which nodes a KEY drives at high and at low priority, and which it leaves
    to the layers below.
- `seatOffsets` and `auditArticulation` on the blended poses.
- `sith.audit.ground`: floor contact for each KEY or layered sequence, and contact slip for planted
  feet. Contact slip measures sliding on the floor. It does not measure a joint sliding along its
  parent, such as the boot up the shin. Read that from `seatOffsets` (the foot, or the adapter
  that carries it, moving off its seat), or from the ankle in the calf's frame in `Pose.frames`.
- Through MCP: `lint_keys` (loads KEYs the way the engine reads them, with `strictTracks=False`)
  and `audit_ground` with `contacts` and `travel`. In Blender: **Audit Files** with **KEY Lint**.
  Both play each KEY alone, so audit blended poses and priorities from Python.

### Weapon meshes a COG swaps

**What went wrong.**

- The lightwhip's long mesh dipped into the floor just before the COG swapped the resting whip
  back, because the hand was already heading down to the hip. R2 brings the resting whip back
  earlier: at 0.38 s in the lashes (was 0.45), 0.27 s in the parries (0.30) and 0.78 s in the crack
  (0.83). The short resting hook still grazes the floor slightly while she strafes or runs.
- The crack's straight whip read as a stub. JK's third-person camera sits behind her, and the crack
  snapped straight ahead, along the view. It was not culling, clipping or the KEY. In R2 the crack
  snaps out 33 to 35° to her right of straight ahead, a little above level. It shows 0.24 to 0.27
  JKU of whip across the camera's view, against 0.09 to 0.12 before.

**Do this.** Aim long strikes across the camera's view. Bring a resting mesh back before the hand
reaches the hip. Test with the mesh times copied from the COG.

**Checks.** `auditFloorContact` with `CarriedMesh` entries follows the COG's mesh swaps without
running the COG. `auditCameraReading` measures how long a mesh looks across the third-person view,
which looks 6.84° down at a level aim ([ground](audits/ground.md)). Both run only from Python.

## 8. Packaging

**What went wrong.**

- The Blender exporter wrote geoset 0 only, so three identical geosets were added outside Blender
  for every release. Biddy Blas R3.3, kept as a control, dropped detail at the usual distances.
  R3.5, with identical detail levels, kept full detail when a second player saw him from far away.
- Lilivane and Darth Lilitari both used `models.dat` entry 28 (stage 6).
- A GOB's KEYs replace the stock ones for every player model in the install (stage 7).
- GOBs that carry stock KEYs, the palette or files made from the game's own archives
  (`models.dat`, `jkstrings.uni`, KEYs padded to the model's joint count) are for private use on an
  owned install. For a public release the model goes into a package that builds those files on the
  user's machine. That is the route of the T-800, through Coda's installer.

**Do this.**

1. Export with **Detail Levels** set to 4 (`detailLevels=4`, or `setDetailLevels(model, 4)` on a
   native model). Every geoset is a full copy, so the character keeps full detail at every
   distance.
2. Keep the retail budget check. Every 2.1 export checks the model it is about to write, after
   vertices split at sharp edges and texture vertices at UV seams. A mesh can go over without
   anyone editing it. **Fail Over Retail Limits** stops the export instead of warning. Darth
   Lilitari R9.10's largest meshes sit exactly at 512 vertices, 768 texture vertices and 512 faces.
3. Build one GOB per edition, with the same member names. Install one edition at a time. (The fork
   does not write GOBs.)
4. Give each character its own `models.dat` entry, and check its KEY node slots against every
   character in the pack.
5. Compare every GOB member with the previous release. List the changed members with a count. The
   rest must be byte-identical. Darth Lilitari R9.10 differs from R9.9 in 38 of 201 members (the
   model and 37 KEYs), Lilivane R3 from R2 in 43 of 176, and Biddy Blas R3.6 from R3.5 in 13 of 121.
   In Mandalorian Red 0.2.2 only the changed model file differs in each GOB.
6. Keep test-only rigs out of the GOBs you ship.

**Checks.** `auditBudget` on the exported 3DO: status `within_limits` and 4 geosets.
`budgetWarnings` merges a finding repeated across identical geosets into one line
([budget](audits/budget.md)). `trackRegistry` for every pair of characters in the pack. Through
MCP: `audit_budget` on the exported file and `audit_track_slots` with every character of the pack.
In Blender: **Check Retail Limits** with **Detail Levels** 4, before you export.

## 9. Acceptance

**The fast loop: OpenJKDF2.** Capture frozen poses in OpenJKDF2, in 16-bit OpenGL and in the 8-bit
software rasteriser. Each character keeps a standard set (24 or 25 captures for Darth Lilitari, 27
for Lilivane, 9 for Biddy Blas) and close views of the places that went wrong before:

- arm from the side and back three-quarter, hem, back hair;
- head from the back, the side and three-quarters;
- legs from the front, back and side; rear three-quarter; boots from the front and side;
- run and crouch from behind; run, saber, crouch, jump and gun poses.

Capture each flagged pose before and after as a frozen frame in both renderers. OpenJKDF2 is a
reimplementation. A pass there is a fast check, not acceptance.

**Acceptance: retail.**

1. Use retail JK.EXE 1.01 with no engine patch. These releases ran it under Wine.
2. Run each edition alone, as one GOB in its own `-path` folder.
3. From the exact GOBs you ship, host a multiplayer game as the character: in software 8-bit, then
   with 3D acceleration in 16-bit. Look at the character with the external camera, and step through
   the character picker.
4. Read `debug.log`. In these releases it holds only the four stock string warnings.
5. Join with a second instance that has the same GOB. The T-800 r7 joiner loaded the host's model;
   Biddy Blas's two joins matched checksums. A second player is also how you see the face straight
   on, and the far detail levels.
6. Put the registry back afterwards and remove the test characters.
7. Write down what you did not test. The T-800 r7 notes list crouching seen from the side at close
   range, deaths, force powers, jumps and landings, two instances in 3D mode, real Direct3D
   hardware, two physical machines and frame rate.

The 16-bit edition's preview in the character editor is speckled; that is how the menu draws RGB565
MATs. In the game it draws correctly.

**Recording moves picked by movement.** The lightwhip picks each stroke from how she moves when she
fires. For the first recording a script tapped the movement key for 0.1 to 0.2 s and fired. The tap
shoved her, and she glided with her feet still in the stance: her boots moved 74 and 97 pixels in a
1280-wide frame. The second take held her in place with a test-only rig. It set the engine's
immobile actor flag (0x40000) once her first stroke had begun and zeroed any velocity left. Her
boots then moved 0 to 1 pixel over 17 strokes. Caption such moves as shown in place, and never ship
the rig.

## Symptom, likely cause, check

| Symptom | Likely cause | Check | Case |
| --- | --- | --- | --- |
| A piece stays behind when an arm moves (a "second shoulder", a ghost forearm) | The cut left limb surface in the torso | `auditSegmentation`; the pieces drawn in solid colours; `removeFaces` to fix | Mandalorian Red 0.2.2; Darth Lilitari R9.3; Lilivane R2 |
| Daylight at the waist in saber moves; forearms off the elbows in armed runs | Stock KEY seat offsets | `seatOffsets`, then `auditArticulation`; reseat with `reseatKeyFiles` (`reseat_keys`, **Reseat KEYs**) and run both again | Darth Lilitari R9.10; Lilivane R3; Biddy Blas R3.6 |
| A slit above the belt at the back in armed walks | The walks carry the torso high and drop each thigh | `auditArticulation`; the reseat's hip-follows-torso step | Lilivane R3 |
| The waist opens in the choke KEY or a strafe | Rotation alone, with every joint on its seat | `auditArticulation` with no matching seat offset; a reseat cannot help | Darth Lilitari R9.10 |
| A dark smudge on the rear | The reference hid that surface and the generator filled it | The references and the 4K master; renders of the rear in idle, run and falls | Darth Lilitari R9.1 |
| A dark disc on a bent knee; a dark band under the hair when the head turns | A cap mapped to dark texels; a hair cap lifting off the surface under it | Renders of deep bends and head turns; daylight counts cannot see dark polygons | Darth Lilitari R9.10 |
| A neck lid or fin | Joint geometry not shaped about the real pivot: the T-800's head turns about the chin, 0.007 JKU in front of the neck | The pivot and the range of motion (`sith.audit.pose`); captures from the side and behind in far nods | T-800 r7 |
| A step at a bent knee or ankle; a star in the knee texture | A pivot off the centre of its cut; a cap aimed at the cut's vertex average | The pivots and caps; `auditArticulation`; close-ups | Darth Lilitari R9.2; Lilivane R2 |
| The upper body lies apart from the hips in deaths | Node 1 is not on the character's own hip | The node plan; deaths posed with `sith.audit.pose` | Lilivane R1 |
| A boot sliding up the shin | The foot lift slides the foot joint along the calf | `seatOffsets` on the foot or the adapter that carries it; the ankle in the calf's frame from `Pose.frames` | Biddy Blas R3.4 to R3.5 |
| Feet in the floor, or floating | New proportions without new ground-contact tracks | `auditFloorContact`; `lowestPoints` against stock Kyle | Biddy Blas R3.4; Lilivane R1 |
| A weapon mesh dips into the floor | The COG swaps the resting mesh back too late | `auditFloorContact` with `CarriedMesh` | Lightwhip R2 |
| A straight weapon reading as a stub | It points along the camera's view | `auditCameraReading` | Lightwhip R2 |
| A joint swings through an unauthored pose between KEY entries | A large change between entries, or a flip to the other Euler form | `scanKeyPops` | Lightwhip R2 |
| A stroke that slides in a recording | The recording method: a tapped movement key shoves the character | Hold the character in place for moves picked by movement | Lightwhip R2 captures |
| Two characters break each other in one install | A shared `models.dat` entry, or one character's KEY tracks landing on the other's nodes | `trackRegistry`; one `models.dat` entry each | Lilivane R1 |
| The character turns blocky far away | One geoset only | **Detail Levels** 4; a far view from a second player | Biddy Blas R3.3 |
| A mesh over the retail limits that nobody edited | Export splits vertices at sharp edges and texture vertices at seams | `auditBudget`, which every 2.1 export runs | Export budget notes |
| Skin in bands in 8-bit | Nearest colour without dithering | **Dither Indexed Textures** with a skin mask | Darth Lilitari R9 |
| See-through colours wrong in 8-bit only | The software renderer blends through the 256-colour table | Both editions, each in its own renderer | Lilivane R1 |

## Reading audit results

- **A count is a lead, not a verdict.** Look at every flagged joint up close before you call it a
  fault. On Mandalorian Red the knees counted about 2,000 lines of sight in 450 to 490 poses. Up
  close they were rays into the crease of a bent knee: open air, not a hole. Render flagged poses
  on a magenta background and mark any background the body fully encloses in green. Background the
  body does not enclose is open air. Enclosed background still needs a look: on Mandalorian Red it
  was specks, or the space between the legs or between an arm and the body.
- **Size matters.** One or two lines in a pose is a grazing sliver at a rim. Dozens is an opening
  you can see.
- **Know what a check cannot see.** The joint sweep sees gaps. It does not see one piece pushing
  through another, and it does not see dark polygons.
- **Compare on the same sample points, and run the whole set.** The first hip rule looked right in
  the flagged poses and opened the walks. Only the full sweep showed it.
- **Read the audit's status, not the job's.** An MCP job's `status: passed` means the audit ran and
  its result was accepted. The verdict is in `audit.<section>.status`. Every status is listed in the
  [audit index](audits/README.md#statuses).
- **Reproduce before you change.** Darth Lilitari R9.2 first rebuilt R9.1 with the new copy of the
  tools and no changes. It reproduced R9.1's model and every MAT byte for byte. Only then did the
  changes start.
- **Correct the record.** Mandalorian Red's README carries a correction. The red-visor model's head
  count and the no-helmet model's wrist and elbow counts were first reported as real gaps, from the
  counts and a few small renders. The close-ups showed crease rays.
- **Write down the trade.** Removing the Mandalorian blade uncovered pinholes. Removing the T-800's
  lowest lumbar disc opened 3 of 6,394 engine poses by 0.0011 to 0.0018 JKU, so the disc stayed and
  was darkened instead. The README says which way each trade went.
- **Time a subset first.** A full joint sweep is 1,745 poses per character from eight cameras.

## Release checklist

- [ ] Before-and-after audits on the same sample points: the same KEYs, frames and cameras. The
      README gives both counts, per joint and per KEY.
- [ ] The previous release's folder left as it was.
- [ ] Every changed file listed, with a count against the previous release. Every untouched file
      byte-identical, and untouched meshes equal number for number.
- [ ] Both editions: 16-bit RGB565 and 8-bit Indexed, with the same file names, in separate GOBs,
      from the same bake.
- [ ] All detail levels: four identical geosets, and `auditBudget` within limits on the exported
      model.
- [ ] KEYs: JOINTS matches the model. The binding audit and KEY lint are clean, or every finding is
      explained. Node slots are checked against every character the KEYs will share an install
      with.
- [ ] OpenJKDF2 standard captures pass in both renderers. Flagged poses are captured before and
      after.
- [ ] Retail JK.EXE 1.01 without an engine patch, from the exact GOBs: software 8-bit and 3D 16-bit,
      `debug.log` read, the registry put back.
- [ ] A multiplayer join with the same GOB.
- [ ] No test-only rig or debug file in the shipped GOBs.
- [ ] A README that gives:
  - what changed and why, and what is left;
  - how to install it;
  - the SHA-256 of each GOB and of the model;
  - the tests, with numbers, and what was not tested;
  - whether the GOBs carry game files, and so are for private use only.

## Running the audits from Python

The audits run without Blender. Load the model and the KEY folder once and pass them to each
check. This pass covers the budget, the cut lint, the floor and stages 6 and 7; each audit's own
page has its options.

```python
from sith.model.model3doLoader import load3do
from sith.audit.pose import PoseLayer, poseModel
from sith.audit.budget import auditBudget, budgetWarnings
from sith.audit.segmentation import auditSegmentation, zeroRotationPose
from sith.audit.keylint import lintKeyHeader
from sith.audit.articulation import auditArticulation, loadKeyFolder, referencePose, seatOffsets
from sith.audit.ground import auditFloorContact, keySequence, thingFloorHeight

model, _ = load3do('character.3do')
keys = loadKeyFolder('key')                  # {file name: KEY}, sorted by name
stand = keys['kystand1.key']

budget = auditBudget(model, 'JKDF2')
print('budget', budget['status'])
for line in budgetWarnings(budget):
    print(line)

# These characters are cut with every joint straight at the standing KEY's seats. For a model cut
# in its rest pose, leave out `reference` in the calls below.
cuts = auditSegmentation(model, reference=zeroRotationPose(model, stand=stand))
print('cuts', cuts['status'], cuts['severity_counts'])

for name, key in keys.items():               # header lint; 'binding' holds auditModelKey's report
    header = lintKeyHeader(key, model, target='JKDF2')
    if header['status'] != 'consistent':
        print(name, header['status'], header['issue_counts'], header['binding']['issue_counts'])

seats = seatOffsets(model, keys, reference=referencePose(model, stand))
print('seats', seats['status'], seats['issue_counts'])

saber = {name: keys[name] for name in ('kysbthr2.key', 'kysbforc.key')}   # time a few KEYs first
gaps = auditArticulation(model, saber, reference=referencePose(model, stand, 0, zeroRotations=True))
print('gaps', gaps['status'], gaps['issue_counts'])

# The engine floor of the stock player template (movesize 0.065). The crouch KEYs play on the
# crouch floor (audits/ground.md).
standing = thingFloorHeight(model, 0.065)
crouching = thingFloorHeight(model, 0.065, crouching=True)
crouch = ('kycrchgn.key', 'kycrchbk.key')
sequences = [keySequence(key, floor=crouching if name in crouch else None, name=name)
             for name, key in keys.items()]
floor = auditFloorContact(model, sequences, floor=standing)
print('floor', floor['status'], floor['issue_counts'])

pose = poseModel(model, [PoseLayer.playKey(keys['kysbthr2.key'], 0.0, 1)])
print(pose.frames.shape)                     # one 4x4 joint frame per node
```

A sound character still shows findings here. Read them before you accept or fix anything:

- The cut lint's `review` findings are mostly the normal overlaps at joints. Its `high` findings
  (status `suspect`) come first. `severity_counts` gives the findings by severity; `issue_counts`
  counts them by kind, `info` included. Darth Lilitari R9.10 has no `high` finding.
- A KEY that declares fewer JOINTS than the model has nodes reports `joints_below_model`, and its
  binding report `declared_joint_count_differs`. The stock KEYs declare JOINTS 18 or 19, so on a
  64-node model every stock KEY reports both until it is padded to JOINTS 64. The padded KEYs the
  releases ship (stage 8) report neither.
- A KEY that animates a filler slot reports `joint_name_differs` in its binding report. On Darth
  Lilitari R9.10 these are the four KEYs with a track at index 18.
- `seatOffsets` reports `off_seat` when any KEY moves a node more than 0.00002 units from its seat.
  Most stock KEYs move the root and the torso carrier away from where the stand KEY holds them, so
  this is the usual status. An offset matters at a joint that opens: read the offsets of that
  joint's `seat_nodes` (in the joint-gap report) next to its daylight counts.
- Floor findings in jumps, falls, deaths and swims are mostly by design, and the stock KEYs are not
  clean on Kyle either. Compare with stock Kyle KEY by KEY, on the same floors.

`loadKeyFolder` uses the default KEY loader and stops at a KEY it cannot read. Pass `errors=[]` to
skip such files and list them. Load custom KEYs with `loadKey(path, strictTracks=False)` before you
lint them (stage 7), add `scanKeyPops`, and for a pack `trackRegistry`
([keylint](audits/keylint.md)). A sweep of all 91 stock KEYs at step 2 is 1,745 poses; time a few
KEYs first.

## Sources

The release notes this guide draws on, by character and release. They are private and are not in
this repository.

- Darth Lilitari: R9 (rebuild from scratch), R9.1 (rear texture), R9.2 (joints), R9.3 (the ghost
  shoulder) and R9.10 (articulation pass).
- Lilivane: R1, R2 (joint fix) and R3 (joints reseated in the KEYs).
- Biddy Blas: R3.5 (longer legs, blended walk lift) and R3.6 (hip reseated in the saber KEYs).
- T-800 Hybrid: r7 notes and README.
- Mandalorian Red: 0.2.2 (arm ghosts removed).
- Red Sith Lightwhip: motion 03 R2 (retail and SBX) and its capture notes, take 2.
- The stock-animation audit of the four characters, 23 November 2026.
- Red's handoff to Coda of 18 November 2026 (retexture sources).
