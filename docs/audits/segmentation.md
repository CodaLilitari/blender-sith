# Cut lint: surface left in the wrong piece

`sith.audit.segmentation` looks for surface that a cut put into the wrong rigid piece of a character.
`sith.model.model3doEdit.removeFaces` takes such faces out of a mesh and closes the opening they leave.
Both run without Blender and never change the model they are given.

## The fault

The engine draws each hierarchy node's mesh with that node's own matrix (`rdModel3_DrawHNode` with
the joint matrices from `rdThing_AccumulateMatrices`, in OpenJKDF2 `Primitives/rdModel3.c` and
`Engine/rdThing.c`). A face moves only with the node that carries its mesh. If the cut that splits a
sculpt into rigid pieces leaves part of an arm in the torso's mesh, that part stays with the torso
when the arm moves. At rest it hides inside or against the arm, so a still model looks right.

Two releases had this fault:

- **Mandalorian Red 0.2.1.** The sculpt's arms touched her flanks. The T-visor model's torso mesh held
  a long blade of left forearm and hand armour down her left flank, plus two small loose pieces, one by
  the right hip and one by the right shoulder. The no-helmet model's torso held a loose 24-face shard of
  her left hand by her left hip. Version 0.2.2 removed 74 and 24 torso faces and closed the opening in
  the T-visor's flank with 12 cap faces. The red-visor model had no ghost, but its torso has surface
  under each shoulder plate that is its own shoulder.
- **Darth Lilitari R9.2.** A "second shoulder" stayed beside her arm when it came forward. The torso
  piece had kept the round of her shoulder (the cut was an upright plane 1 cm outside the shoulder
  pivot), the inner side of the sleeve near the armpit and a tall cap over the cut. It was found by
  painting each piece a solid colour. R9.3 moved the cut to the pivot and lowered the cap.

## What it checks

### Rigid parts and bones

A rigid part is a set of nodes that move together. `rigidParts(model, joints=None)` gives each node
the index of the joint node it moves with. A joint node starts a part; any other node joins its
parent's part. By default the joint nodes are the roots and the nodes named like Kyle's skeleton
(`k_hip`, `k_torso`, `k_neck`, `k_head`, the shoulders, forearms, hands, thighs, calves and feet),
because custom characters hang extra mesh and adapter nodes under those joints. All of Darth
Lilitari's torso meshes (nine or ten, carried by `k_torso` and its adapter children) form one part. A
model with no Kyle names
gets one part per node. Pass `joints` (node indices or names) to choose others, or
`joints=drivenNodes(model, keys)` to start a part at every node the character's KEYs drive.

A part's bone is a set of straight segments from its joint to each child part's joint. A part with no
child part (a hand, a foot, the head) gets one segment from its joint toward the far side of its own
geometry. `partBones(model)` returns them.

### The reference pose

Ownership and enclosure only mean something in the pose the pieces were cut in. The default is the
rest pose stored in the 3DO. That is right for Mandalorian Red. Darth Lilitari, Lilivane and Biddy
Blas were cut with Kyle's joints at zero rotation; pass
`reference=zeroRotationPose(model, stand=standKey)`, which puts the joints the stand KEY drives at its
positions with zero rotation, as Red's joint sweep did.

### Three kinds of evidence

- **Islands.** Pieces of a part that touch nothing else in the part. Vertices are welded by position
  (within `weld`, 1e-6 JK units by default) across all meshes of the part, so a surface split over
  several meshes, or stored twice along a texture seam, is still one piece. The piece with the most
  area is the main piece; the others are islands.
- **Foreign surface.** Faces nearer another part's bone than their own by at least `margin` (0.004 JK
  units by default, about 4 cm at Kyle's size).
- **Covered surface.** Faces whose centre lies inside another part's volume. The inside test is the
  generalized winding number (`windingNumbers`): 1 inside a closed surface, 0 outside, falling off
  smoothly across the opening of an open one. Limbs are often left open at their cut; a point deep in
  an open limb still reads above 1/2, and a point beyond the opening reads below it. Faces follow the
  3DO right-hand rule, so a mesh wound inside out reads negative and covers nothing. A part never
  covers faces of its own ancestors: a child's end tucked into its parent at the joint is how rigid
  pieces are built.

Evidence on the main piece is grouped into connected regions. A region starts at a face that is
nearer another bone by `margin` or covered by another part, and grows to touching faces that are
nearer that part's bone at all or covered by it. Each region belongs to one joint: the joint at which
the claiming part turns against the face's own part (for torso faces claimed by the left forearm,
that is the left shoulder).

### Measures

Every region and island is measured:

| Field | Meaning |
|---|---|
| `claimed_by`, `relation` | The part whose bone is nearest (or whose volume covers the faces), and whether it is a `descendant` of the face's part, an `ancestor`, or `other`. |
| `joint` | The joint at which the two parts turn against each other. |
| `lever` | How far the faces reach from that joint, in JK units. A turn of the joint by an angle leaves them behind by about the lever times the angle in radians. |
| `facing` | Share of the area whose normals point toward the claiming bone and away from the own bone. That is the own part's skin facing the other part, which is normal: a flank beside a hanging arm. Surface built from the other part wraps that part's bone instead. |
| `nearer`, `enclosed` | Share of the area nearer the claiming bone by `margin`, and share inside another part. |
| `margin` | The largest lead of the claiming bone over the own bone, in JK units. |
| `share`, `gap`, `touching`, `contact` | Islands only: share of the part's area, distance to the main piece, and the other part whose surface is nearer than the main piece, with that distance. |
| `separation`, `worst_pose` | With test poses only: the largest distance the faces move away from the claiming part, and the pose where that happens. |

### Severity

A region, or an island that is small (at most `islandShare` of its part's area, 0.1 by default) and
nearer another part's bone by `margin`, is a candidate if `facing` is at most `facingMax` (0.5) and it
has at least `minFaces` faces (3).

- A candidate claimed by an **ancestor** of its own part is a child's end near its parent's bone, the
  normal overlap at a joint. It is `review` from `leverHigh`, otherwise `info`.
- Any other candidate is `high` from `leverHigh` (0.035 JK units) and `review` from `leverReview`
  (0.012).
- Everything else is `info`.

With test poses, `separationHigh` (off by default) also makes a candidate `high` when its separation
reaches that value. It is off because a pose can separate parts that only overlap in the reference
pose: in Darth Lilitari's zero-rotation pose her right hand dips into her thigh, and the stock death
KEYs move it 0.106 units away.

## Run it

```python
import glob
from sith.model.model3doLoader import load3do
from sith.key.keyLoader import loadKey
from sith.audit.segmentation import auditSegmentation, testPoses, zeroRotationPose

model, _ = load3do('character.3do')
report = auditSegmentation(model)                        # rest pose

stand = loadKey('kystand1.key')                          # for characters cut at zero rotation
report = auditSegmentation(model, reference=zeroRotationPose(model, stand=stand))

keys = [loadKey(path) for path in sorted(glob.glob('key/*.key'))]
report = auditSegmentation(model, poses=testPoses(keys, step=2.0))   # every second frame
```

`testPoses(keys, step, priority, base)` plays each KEY on its own and labels each pose `name@frame`;
`base` layers (a standing KEY at a lower priority) play under each one. Any list of
`(label, [PoseLayer, ...])` pairs works.

Time grows with the face count: Biddy Blas (4,090 faces) takes 0.1 seconds, Mandalorian Red (5,045)
0.3, the T-800 (6,830) 1.2, Lilivane (14,051) 3.8 and Darth Lilitari (14,603) 6 to 10. The run with
1,745 stock poses took about 3 seconds on Mandalorian Red. Memory stays under 300 MB.

## Read the result

The report is plain data:

- `status`: `suspect` with any `high` finding, `review` with any `review` finding, otherwise `clean`.
- `issue_counts` by code (`stray_island`, `foreign_surface`, `covered_surface`) and `severity_counts`,
  over every finding of every severity.
- `findings`: at most 32, worst first, with `findings_truncated`. Each names the part, the meshes and
  face indices, the claiming part, the joint and the measures. `detail='info'` lists `info` findings too.
- `meshes`: one entry per mesh with `high_faces` and `review_faces`, the face indices to select in
  Blender, and a count of `info_faces`.
- `parts`: each part's nodes, meshes, faces, area and number of pieces.
- `parameters`, `reference` (`rest` or `given`), and `unplaced_meshes` and `shared_meshes` (meshes no
  node carries, and meshes more than one node carries; the first node wins, as in `sith.audit.pose`).

Start with `high`. Select its faces, then look at them in the pose where the claiming part moves away:
with test poses that is `worst_pose`. A finding is a lead, not a verdict.

`review` holds mostly the normal overlaps at joints, where one piece's end sits inside the next:
shins in boots, the hip under the thighs, the torso under the shoulder plates. Check them in the poses
that bend those joints. A region that covers more than the overlap, or that stands out of the outline
when the joint turns, is the one to cut back.

## Remove the faces

```python
from sith.model.model3doEdit import removeFaces
from sith.model.model3doWriter import save3do
from sith.model.model3doLoader import Model3doFileVersion

fixed, change = removeFaces(model, meshIdx=0, faces=[339, 340, 341], geoset=0)
save3do(fixed, 'character_fixed.3do', Model3doFileVersion.Version2_1, 'ghost faces removed')
```

`removeFaces` returns a new model and a report. Vertices are welded by position. An opening is a loop
of edges that had a face on both sides before and has one after. Each opening gets the triangulation
of least area over its loop; its caps run against the neighbouring faces' edges, so they face the same
way as the surface around them. Each cap takes its material, face type, modes and extra light from the
material most faces around the opening use, and each corner takes the vertex and texture vertex that a
neighbouring face uses there, so the cap carries the surrounding texture and adds no vertices.

The report gives the face counts, a `face_map` from old to new face indices (-1 for removed faces;
kept faces keep their order and caps follow), each opening's loop, cap faces, material, `planarity`
and `shared_edges` (cap edges across the opening that the mesh already had), loops that could not be
walked in `unclosed`, the open edges before and after, and the vertices no face uses any more. Removed
faces leave their vertices in place, so indices stay stable.

## Piece colours

`pieceColours(model, by='node')` gives each hierarchy node a solid colour, as `rgb` (sRGB, 0 to 1)
and `hex`; `by='mesh'` gives one per mesh and `by='part'` colours each node by its rigid part. A colour
depends only on its index, so a node keeps its colour across models and runs. This is the data for the
solid-colour view that found Darth Lilitari's second shoulder. In Blender, **Colour Pieces** in the
Sith Audits panel paints the active model by node or by rigid part with these colours, and
**Restore Colours** puts the previous colours back
([USAGE](../../USAGE.md#colour-pieces-and-restore-colours)).

## What it reports on released characters

Mandalorian Red, rest pose, default parameters:

| Model | Before the fix (0.2.1) | After (0.2.2) |
|---|---|---|
| T-visor | `high`: a 76-face region of torso faces claimed by the left upper arm (lever 0.049, facing 0.34) and the 8-face island by the right hip (lever 0.044). Together they hold 64 of the 74 faces Red removed: 56 of the 60 blade faces and all of the 8-face piece. The region also takes 20 faces around the left armpit that Red kept. | No `high`. |
| No helmet | `high`: the 24-face island, exactly the 24 faces Red removed (lever 0.039). | No `high`. |
| Red visor | No `high`. The torso surface under each shoulder plate is `review` (`covered_surface`, 15 and 25 faces, levers 0.020 and 0.017). | Unchanged. |

The 6-face loose piece by the T-visor's right shoulder is `info`: it lies at the shoulder pivot,
equally near the torso's and the arm's bones, so neither claims it. In the stock KEYs (every second
frame, 1,745 poses) the blade region moves 0.091 units from the arm (`kyfjump0` frame 2) and the hip
island 0.087 (`kygndth0` frame 74).

`removeFaces` with Red's two face lists gives his face counts: 479 to 417 torso faces on the T-visor
(74 removed, one 14-point opening, 12 caps) and 480 to 456 on the no-helmet (24 removed, no opening).
The kept faces are identical to his. The T-visor caps are a different triangulation with 12% less area
than his, since the opening is far from flat (planarity 0.18). Two cap edges across it coincide with
edges the torso already had, in his release and here (`shared_edges` 2).

Darth Lilitari, zero-rotation reference: R9.2 has two `review` regions of torso surface claimed by the
shoulders, 83 and 82 faces (lever 0.013, facing 0.02). All 165 faces are among the 328 torso faces the
R9.3 fix changed. In R9.3 and R9.10 the same places are shallow lids of 70 or 71 faces at lever 0.008,
reported as `info`. In the stored rest pose the shoulder regions read about the same in all three
versions, because her pieces were not cut in that pose.

Biddy Blas R3.6, Lilivane R2 and R3 and the T-800 r7 have no `high` finding in either reference pose.
The largest `review` lever among them is Biddy's torso under his head, at 0.030.

## Limits

- A bone is a straight segment. Surface right at a joint, such as the round of a shoulder within a
  centimetre or two of the pivot, is about equally near both bones. Only enclosure, islands and the
  lever speak for it, so a cut that is wrong by a centimetre at a pivot reaches `review` at most.
- The thresholds are JK units with defaults for Kyle-sized characters. Scale them with the character.
  The `high` threshold sits between the largest lever of a normal overlap seen (0.030) and the smallest
  ghost (0.039); that is a narrow gap, measured on five characters.
- `facing` excuses surface whose normals point at the other part's bone. A ghost plate lying flat
  against the inner side of an arm, facing the arm's bone, would be excused too.
- The default parts follow Kyle's names. A custom node driven by its own KEY tracks (a cape, a tail)
  needs `joints`, or it is treated as part of its Kyle ancestor.
- The reference pose must be the pose the pieces were cut in; the wrong one makes normal overlaps look
  like ghosts and can hide a real one.
- One geoset at a time. Characters with identical detail levels need the same edit on every geoset.
- `removeFaces` edits one mesh and leaves unused vertices in place. A loop that does not close is left
  open and listed. Caps across an opening far from flat may cut inside the surface: check them in the
  poses that matter.
