# Joint-gap audit and seat offsets

`sith.audit.articulation` finds joints that open when KEYs move a character's rigid parts apart.
It runs without Blender, on the add-on's 3DO and KEY resources, and returns plain data.

- `auditArticulation` sweeps every joint in every sampled pose of every KEY. It reports lines of
  sight through a joint that meet no polygon: daylight.
- `seatOffsets` reports how far each KEY moves each node from its seat. That is the usual cause
  of an opening, so read it next to the joint report.

## Why it exists

Darth Lilitari's waist, Lilivane's elbows and Biddy Blas's belt opened in stock animations. The
stock KEYs do more than rotate joints. Some move a joint off its seat: the saber family sets the
hip piece up to 0.008 JK units below its place under the torso, and the armed runs shift the
forearms about 0.004 units off the elbow. Kyle's meshes overlap generously and hide this. A
character cut into close-fitting rigid parts opens by exactly those distances.

Red found these openings with a research script: every joint, every second frame of all 91 stock
KEYs, eight cameras. This module is that sweep, ported into the add-on with its thresholds named
and documented.

## How it works

A JK character is a hierarchy of rigid meshes. Each KEY track sets one node's position and
rotation in its parent's frame (`rdPuppet_BuildJointMatrices`). No mesh bends or stretches.
`sith.audit.pose` reproduces the engine's posing; this audit builds on it.

1. **Joints.** A node that no audited KEY drives rides with its parent, so the hierarchy falls into
   rigid parts. Each part with meshes meets the nearest part above it that has meshes. Parts with
   no such part above them meet the first of them. Kyle's hip and torso both hang from the root
   through meshless carriers, so this rule joins them at the waist. Joints whose child starts at
   one of Kyle's nodes get his labels (`waist`, `neck`, `l elbow` and so on); other joints are
   labelled `parent-child` by node name. With Kyle's skeleton and the stock KEYs this gives the
   same 15 joints Red's script listed by hand. A part with no mesh is skipped over: if a
   character's neck carries no mesh, its head meets its torso.

   A joint list can be given instead. Then the listed nodes start the parts, and every other node
   rides with the nearest listed node above it. `KYLE_JOINTS` is Red's list of 15 joints; with it,
   Kyle's pieces carry everything hung under them, as in his script. Use it when a character's own
   KEYs drive adapter nodes that should count as part of Kyle's pieces.
2. **Anchors.** In the reference pose (see below), rays leave the joint's pivot, the child node's
   origin, across the limb axis. Each ray stops at the nearest polygon of either part, and an
   anchor sits at 75% of that distance, inside the limb. At the joints named in
   `AnchorSettings.membraneJoints` (only the waist by default), if both parts end in open rims that
   match near the pivot, the anchors are instead rim points pulled 20% towards the centre: points
   on the membrane between the two rims. Each anchor is kept twice, once rigid with
   the parent part and once rigid with the child part. Anchors that already see daylight in the
   reference pose are dropped.
3. **Daylight.** In a posed frame the two copies of each anchor part. The audit draws the line of
   sight through their midpoint along each camera direction and tests it against every polygon of
   the whole posed model. A line that meets no polygon is daylight through the joint.

The default cameras are Red's eight (`SWEEP_CAMERAS`): every 45 degrees round the figure, level or
raised 12 or 22 degrees. Azimuth 0 looks at the character's front (+y); azimuth 90 looks at its
right side (+x).

## Run it from Python

```python
from sith.model.model3doLoader import load3do
from sith.audit.articulation import auditArticulation, loadKeyFolder, referencePose, seatOffsets

model, _ = load3do('mychar.3do')
keys = loadKeyFolder('key')            # {file name: KEY}, sorted; pass errors=[] to skip bad files
report = auditArticulation(model, keys)
seats = seatOffsets(model, keys)
```

Characters built the way Red builds Kyle-skeleton characters are cut with the standing KEY's seats
and zero rotation, not in their 3DO rest pose. Give that pose as the reference, from the standing
KEY the pieces were cut against:

```python
from sith.key.keyLoader import loadKey

stand = loadKey('stock/kystand1.key')   # the standing KEY the pieces were cut against
report = auditArticulation(model, keys, reference=referencePose(model, stand, 0, zeroRotations=True))
seats = seatOffsets(model, keys, reference=referencePose(model, stand))
```

Load it from its own path. A reseat can rewrite a character's own copy of `kystand1.key` too;
keep the one the pieces were cut against, the stock one for Darth Lilitari.

Options of `auditArticulation`:

| Option | Default | Meaning |
| --- | --- | --- |
| `step` | `2.0` | Sample every `step` frames from frame 0 up to `FRAMES - 1` (`keyFrameTimes`). |
| `toEnd` | `False` | Also sample the last interval: every `step` below `FRAMES`, then `FRAMES` itself. |
| `cameras` | `SWEEP_CAMERAS` | (azimuth, elevation) pairs in degrees. |
| `joints` | derived | `(parent, child)` or `(label, parent, child)` entries, by node name or index; `KYLE_JOINTS` for Kyle's 15. |
| `reference` | rest pose | The pose the parts were cut in, from `referencePose` or `poseModel`. |
| `settings` | `AnchorSettings()` | Anchor placement lengths and counts (table below). |
| `geoset` | `0` | Which level of detail to test. |
| `worst` | `6` | How many worst KEY frames each joint lists. |
| `onPose` | none | `onPose(name, frame, joints)` receives every pose's per-joint result. |

`keys` may be a list of KEYs, a `{name: KEY}` mapping, or entries that are lists of `PoseLayer`.
A KEY plays alone at full weight. A list of layers plays together with its own priorities and
frame offsets, as the lightwhip does with a leg KEY and an upper-body KEY; the first layer's KEY
sets the sample times. `articulationJoints(model, keys)` lists the joints the audit will measure.

## Read the result

The report follows `auditModelKey`: a `status`, `issue_counts`, at most 32 `findings` and totals
for everything.

- `status`: `clean` (no daylight), `review` (daylight somewhere, or a joint could not be
  measured), or `no_joints` (nothing could be measured).
- `findings`: joints that could not be measured, then the worst (joint, KEY, frame) entries by
  lines of sight. Each names the joint, the KEY, the frame, the line count, the cameras that saw
  through and the largest anchor separation.
- `joints`: for each joint, its parent and child nodes, its `seat_nodes`, how its anchors were
  placed (`kind` `core` or `membrane`, `count`, `dropped`, `radius`), the poses and KEYs with
  daylight, the total lines, the largest separation with its KEY and frame, and its worst entries.
- `keys`: for each KEY, poses sampled, poses with daylight, lines, and lines by joint.
- `parameters`: the step, `toEnd`, cameras, reference and settings used. `reading` repeats the rule
  below.

**A count is a lead, not a verdict.** Counts are lines of sight summed over the cameras. One or two
lines in a pose are usually a grazing sliver at a rim. Dozens are an opening you can see. A line
into the crease of a bent joint counts too: on Mandalorian Red, knee counts in the hundreds were
rays into the crease of a bent knee. Look at each flagged pose in a close-up before changing a
model or a KEY.

Level cameras 180 degrees apart see along the same lines, because a line of sight runs both ways.
Each such pair counts a line twice, as Red's script did.

## Read the seat offsets next to it

`seatOffsets` measures, for each KEY and each node it drives, the largest distance between the
KEY's position and the node's seat, with the frame. A seat is the node's position in the
reference: the model's rest positions by default, or a reference KEY such as the standing KEY. The
offset vector is in the parent's frame. It is measured at every whole frame and every entry frame
below the KEY's `FRAMES`. With `toEnd=True` it is measured at `FRAMES` too.

Each joint in the audit lists its `seat_nodes`: the driven nodes whose tracks place the child part
relative to the parent part. For Kyle's waist these are the torso carrier, the hip and the torso.
When a joint shows daylight in a KEY, look up those nodes for that KEY in `seats['keys']`. An
offset there is a translation the cut did not allow for. No offset means rotation alone opened the
joint.

The report has a `status` (`on_seat`, `off_seat` or `no_tracks`), `issue_counts`, at most 32
`findings` (the largest offsets above `tolerance`, default 0.00002 JK units), `nodes` (each node's
largest offset over all KEYs) and `keys` (each KEY's nodes off their seats).

## Choose the reference pose

The report includes `reference_diagnostic`. `frames_different_from_rest` counts world joint frames that differ from stored rest (absolute matrix-entry tolerance `1e-9`). A parent moving can change several descendant frames. `rotated_rest_nodes` counts stored local rotations whose matrices differ from identity at the same tolerance. For a KEY supplied through `referencePose`, `key_driven_nodes` counts nodes with an applicable track, including tracks that hold the rest transform. A zero count flags a KEY that contributes nothing. Other pose inputs leave the track count unset. Confirm the original cut pose before interpreting the findings.

The anchors measure movement away from the reference, so the reference must be the pose in which
the seams close. A pose equal to the reference parts nothing. A reference that is itself open hides
the opening.

- `referencePose(model)`: the rest pose, as the 3DO stores it. This is the default. It suits models
  authored in their rest pose.
- `referencePose(model, key, frame)`: the nodes the KEY drives take its position and rotation at
  `frame`; every other node keeps its rest values.
- `referencePose(model, key, frame, zeroRotations=True)`: the same positions with zero rotation on
  the nodes the KEY drives. Red's Kyle-skeleton builds keep Kyle's bent rest rotations in the 3DO
  but are cut with every joint straight at the standing KEY's seats, so this is the reference for
  them. Use the standing KEY the character was built against: if it carries adapter tracks, those
  nodes take its positions and zero rotation too. The cut lint's
  `zeroRotationPose(model, stand=key, frame=frame)` builds the same pose; either works for either
  audit.

## Parameters

`AnchorSettings` holds Red's values. Lengths are JK units and fit Kyle-scale characters, about
0.2 units tall. `AnchorSettings().scaled(f)` scales every length for a character `f` times that
size; counts and ratios do not change.

| Field | Default | Meaning |
| --- | --- | --- |
| `rays` | 16 | Radial rays cast across the limb axis from each pivot. |
| `fraction` | 0.75 | An anchor sits at this fraction of its ray's hit distance. |
| `nearRadius` | 0.045 | Only polygons whose centre lies this close to the pivot can stop a ray. |
| `axisRadius` | 0.03 | Vertices this close to the pivot set the limb axis, from the parent side to the child side. |
| `maxRadius` | 0.03 | A ray that travels further than this found no limb wall and is ignored. |
| `minAnchors` | 6 | With fewer hits than this the joint is not measured. |
| `outlierRatio` | 1.8 | Rays longer than this times the median escaped the limb and are dropped. |
| `membraneJoints` | `('waist',)` | Joint labels where open rims are looked for. Red's limb joints are capped. |
| `rimReach` | 0.022 | Every point of an open rim lies within this distance of the pivot. |
| `rimMinPoints` | 8 | An open rim has at least this many vertices. |
| `rimMinExtent`, `rimMaxExtent` | 0.008, 0.05 | Limits on a rim's bounding-box diagonal. |
| `rimRoundness` | 0.35 | A rim must surround its centre: the mean of the unit directions from the centre is at most this long. |
| `rimMatch` | 0.006 | The parent and child rims must lie closer than this on average. |
| `rimPull` | 0.2 | Membrane anchors sit this fraction of the way from each rim point to the centre. |
| `weld` | 0.000003 | Vertices this close count as one when rims are traced. Not scaled. |

## Limits

- A pose is a static evaluation of one KEY, or of given layers, at one time. The audit does not run
  puppet timing, looping, fades, the player's aim pitch or blends the game makes on its own, such
  as a fire animation over a run. Layers are not looped or stopped: past its last frame a shorter
  KEY keeps following its last entry.
- By default the last interval of each KEY is not sampled. The samples stop at or before
  `FRAMES - 1`, as in Red's sweep, but the engine plays on to `FRAMES`: a looping KEY shows every
  time below `FRAMES` before it wraps, and a KEY that holds or fades at its end stops at `FRAMES`
  (`rdPuppet_AdvanceTrack`). In 27 of the 91 stock KEYs a joint turns more than one degree, or
  moves more than 0.0001 units, between frame `FRAMES - 1` and `FRAMES`. Pass `toEnd=True` to
  `auditArticulation` and `seatOffsets` to sample that interval too. The MCP tools and the Sith
  Audits panel use the default.
- Every polygon counts as cover, whichever way it faces. The engine skips single-sided faces seen
  from behind (`rdModel3_DrawMesh`), so an opening seen into an open shell can be missed.
- The sweep finds gaps. It does not find one part pushing through another, and it does not find
  dark polygons that read as holes.
- Anchor placement is a heuristic tuned for Kyle-scale characters with capped limbs. A joint whose
  geometry does not fit it is reported as not measured, with the reason.
- Lines are tested exactly. Red's script sampled each line at the centre of a pixel in a 360-pixel
  render of the whole model, about 0.0006 JK units across for a Kyle-scale character. Counts at
  the edge of an opening therefore differ by a few lines from his. The module has no option to
  sample his pixel centres, so it does not reproduce his reports line for line.
- On a Kyle-scale character with 14,600 faces per detail level, 91 KEYs at step 2 (1,745 poses)
  took 80 seconds on one core and peaked at 165 MB, model included.
