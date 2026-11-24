# Floor contact, contact slip and camera reading

`sith.audit.ground` poses a 3DO with KEYs the way the engine does (`sith.audit.pose`) and measures
three things a still model cannot show:

- **Floor contact.** For every sample of every KEY, or of a layered pose, the lowest point of the
  model against a floor height. It reports samples below the floor, samples where the feet float
  above it, and each KEY's lowest and highest value.
- **Contact slip.** How far a planted foot slides along the floor between samples. Under a walk or
  run it measures the slide against the ground moving under the foot.
- **Camera reading.** How long a mesh, such as a weapon, looks from JK's third-person camera. A long
  mesh pointing along the view reads as a stub.

Weapon meshes that a COG swaps in and out can be added for given time ranges without running the
COG. The audit needs no Blender and does not change its inputs.

## Why it exists

- Biddy Blas's calves were made 20% longer. His feet still had to meet the floor in all 91 stock
  animations, and the leg fixes that kept them there had to be measured again after every change.
  One such fix lifted the swinging foot by sliding the foot joint up the calf: in his walk the boot
  slid up his shin by about 0.0075 JK units in R3.4 and about 0.004 in R3.5. (Release notes write
  0.001 JK units as 1 mm.)
- The lightwhip's long mesh dipped into the floor just before the COG swapped the resting mesh back,
  because the hand was already on its way down to the hip.
- The whip's straight crack read as a stub. JK's third-person camera sits behind her, and the whip
  pointed straight ahead, along the view.

## Where the floor is

The engine does not stand a character on its feet. Physics holds a walking thing at a fixed height
above the floor it is attached to (`sithPhysics_UpdateThingPhysics`). That height is the template's
`height`; when the template sets none, it is the model's `insertOffset.z`, but at least
`movesize + 0.005` (`sithPhysics_GetThingHeight`). A crouching thing is held at `movesize + 0.01`.
The model is then drawn from the thing's position (`sithRender` hands the thing's orientation and
position to `rdThing_Draw`). Whether the feet touch the floor is therefore up to the 3DO and the
KEYs alone. If the posed feet reach below the floor, they sink into it. If they stop short of it, the
character floats.

Positions here are in the frame of `sith.audit.pose`: JK units, +y forward, +z up. The root hangs from
`T(insertOffset)`, so this is the thing's frame moved by the insert offset. With the stock player
template (`walkplayer`: movesize 0.065, no height) and Kyle's insert offset (z = 0.120114), the floor
is the plane z = 0 when standing and z = 0.045114 when crouching. Stock Kyle bears this out: his
standing KEYs put his lowest point between -0.0007 and 0, and his crouch walks between 0.037 and
0.048.

`floor` chooses the plane:

| `floor=` | Floor used |
| --- | --- |
| `None` (default) | The lowest point of the whole model in its rest pose |
| a number | That height in the posed frame, for example `thingFloorHeight(model, 0.065)` |
| a list of `PoseLayer` | The lowest point of the whole model in that pose, for example a standing KEY's first frame |
| `PoseSequence(..., floor=h)` | `h` for that sequence only, for example the crouch floor for crouch KEYs |

The rest pose is only a convention. The rest poses of Kyle, Biddy Blas R3.6 and Darth Lilitari R9.10
stand 0.0030, -0.0038 and 0.0034 off the engine's floor. Their standing KEY's first frame is within
0.0007 of it. For a player model, pass `floor=thingFloorHeight(model, moveSize)` with the template's
`movesize` (and `height`, if the template sets one). Give the crouch KEYs
`thingFloorHeight(model, moveSize, crouching=True)` as their own floor.

## Running it

```python
from sith.model.model3doLoader import load3do
from sith.key.keyLoader import loadKey
from sith.audit.ground import (auditFloorContact, auditCameraReading, keySequence, lowestPoints,
                               thingFloorHeight, CarriedMesh, PoseSample, PoseSequence)
from sith.audit.pose import PoseLayer

model, _ = load3do('biddybl.3do')
stand = thingFloorHeight(model, 0.065)                  # z = 0 for Kyle's insert offset
crouch = thingFloorHeight(model, 0.065, crouching=True)

sequences = [
    loadKey('kystand1.key'),                                            # played alone, integer frames
    keySequence(loadKey('kywalkf.key'), 0.25, direction=(0, 1)),        # quarter frames, walks forward
    keySequence(loadKey('kystrafl.key'), 0.25, direction=(-1, 0)),
    keySequence(loadKey('kycrchgn.key'), 0.25, direction=(0, 1), floor=crouch),
]
report = auditFloorContact(model, sequences, floor=stand, contacts=['k_lfoot', 'k_rfoot'])
print(report['status'], report['issue_counts'])
for row in report['sequences']:
    print(row['name'], row['lowest_min'], row['lowest_max'], [s['sweep'] for s in row['slip']])
```

Nodes are given by index or by a unique name. Two stock nodes share the name `$$$dummy`, so give
those by index.

A layered pose, such as a lower-body KEY under an upper-body KEY on a stand, is a `PoseSequence` of
`PoseSample` objects. Each sample has its own layers with the priorities the COG or puppet would use:

```python
stand1 = loadKey('kystand1.key')
legs = loadKey('lwpfore.key')            # lower-body KEY
upper = loadKey('rlwkfore.key')          # upper-body KEY
times = [0.5 * i for i in range(2 * (upper.numFrames - 1) + 1)]
samples = [PoseSample(f, (PoseLayer(stand1, 0.0, lowPri=0),
                          PoseLayer(legs, min(f, legs.numFrames - 1), lowPri=1, highPri=3),
                          PoseLayer(upper, min(f, upper.numFrames - 1), lowPri=2, highPri=4)))
           for f in times]
lash = PoseSequence('rlwkfore', samples)
```

The time of a sample is free. Here it is the upper KEY's frame, which is also the clock of the COG
that swaps the meshes below.

To compare a model with the model its KEYs were made for, sample by sample, use `lowestPoints`. It
returns the lowest point and its node, mesh and vertex for each sample:

```python
kyleModel, _ = load3do('ky.3do')
mine = lowestPoints(model, loadKey('kyidle1.key'))
kyle = lowestPoints(kyleModel, loadKey('kyidle1.key'))         # the stock KEY
worst = max(abs(a['z'] - b['z']) for a, b in zip(mine, kyle))
```

## Reading the floor report

The report is plain data. `status` is `clear`, `review` or `empty` (nothing to sample).
`issue_counts` counts findings by code. `findings` holds at most 32 detailed entries, and
`findings_truncated` says whether there were more. `sequences` has one summary per KEY or sequence,
with every value relative to that sequence's floor:

- `lowest_min`, `lowest_max` and their times: the lowest point of the measured meshes, at its deepest
  and at its highest.
- `contact_min`, `contact_max`: the same for the contact meshes alone.
- `below_samples`, `floating_samples`: how many samples crossed each tolerance.
- `slip`: one entry per contact node (see below).

There is one finding per sequence and code, naming its worst sample:

| Code | Meaning | Identity given |
| --- | --- | --- |
| `below_floor` | The lowest point is more than `tolerance` (default 0.002) under the floor | sequence, time, `depth`, node, mesh or carried mesh, vertex, sample count |
| `floating` | The contact meshes' lowest point is more than `floatTolerance` (default 0.002) over the floor | sequence, time, `height`, node, mesh, vertex, sample count |
| `contact_slip` | A planted contact slid faster than `slipTolerance` (default 0.003) per unit of sample time | sequence, contact node, first and last time of the worst interval, `slide_per_frame`, interval count |

Jumps, falls, deaths and the swim KEYs float or sink by design. Read the findings against what the
KEY is for. The stock KEYs are not clean either. On stock Kyle, the 75 KEYs that are not swim KEYs
give 24 `below_floor`, 20 `floating` and 30 `contact_slip` findings at integer frames with the
defaults (engine floor, feet as contacts, crouch floor and travel set). Only 35 are clean. His walks
have a moment around frames 0 and 40 when both feet are 0.015 to 0.019 off the floor. His
back-pedals and left strafes drag the toe of the swinging foot along the floor. When a custom model
plays stock KEYs, audit Kyle with the same settings and compare KEY by KEY.

## Contact slip

Slip is measured only for the nodes passed as `contacts`. A contact node is **planted** at a sample
when both hold:

1. its mesh's lowest vertex is no more than `contactBand` (default 0.0015) above the floor, or
   anywhere below it;
2. its joint, the origin of its joint frame (the ankle for a foot), is within `stanceBand` (default
   0.0015) of the lowest height that joint reaches in the sequence.

The second test separates a flat foot from a swinging foot whose toe drags along the floor. Stock
Kyle's back-pedal needs it: the swinging foot's toe sits lower than the planted foot.

Between two samples where a node is planted, the audit follows the vertices that touch the floor
(within `contactBand`) at both samples. Their mean horizontal movement, less the ground's own
movement, is the **slide**. If no vertex touches at both samples, the foot has rolled from heel to
toe. That interval counts as `rolling_intervals`, not slide. Each `slip` entry gives
`planted_intervals`, `rolling_intervals`, `slide_total` (JK units), `slide_max_per_frame` and its
time, `over_tolerance` and `sweep`.

Walks, runs, back-pedals, strafes and crouch walks are movement-controlled (puppet flag 0x1). The
engine does not play them by time. `sithPuppet_UpdatePuppet` advances them 280 KEY frames for every
unit the thing moves along its main horizontal axis. Under such a KEY a planted foot must move
backwards, in the model's frame, by 1/280 unit per KEY frame. `keySequence(key, step, direction=...)`
sets that travel:

| Stock KEYs | `direction` |
| --- | --- |
| `kywalkf`, `kywalkg`, `kywalks`, `kyrun0`, `kyrun1`, `kyrun2`, `kycrchgn` | `(0, 1)` |
| `kybackp0`, `kybackp1`, `kycrchbk` | `(0, -1)` |
| `kystrafl`, `kystrfl1` | `(-1, 0)` |
| `kystrafr`, `kystrfr1` | `(1, 0)` |

For a layered sequence whose base KEY advances at `r` base frames per unit of sample time, set
`PoseSequence(..., travel=(x * r / 280, y * r / 280))` with `(x, y)` a unit direction.

`sweep` is the planted vertices' mean backward speed divided by the ground's speed. At 1 the foot
holds the ground. Below 1 it is carried forward with the body. Above 1 it is pushed back. Stock Kyle
reads 0.87 to 0.90 in his walks and 0.80 to 0.88 in his runs, foot by foot. Red's release notes
measured the sweep at the ankle instead of the touching vertices, both feet together. The two agree
for a flat foot and differ where the foot rolls or drags: Kyle's back-pedal reads 0.78 at the ankle,
and 0.87 (left) and 0.31 (right) at the floor.

## Carried meshes

COG `jkSetWeaponMesh` gives a player a model with one geoset holding one mesh. The engine draws it
with the matrix of the puppet's primary-weapon joint (`jkPlayer_renderSaberWeaponMesh`). That matrix
includes the joint's pivot, so it is `Pose.meshFrames[node]`. A `CarriedMesh` stands in for it:

```python
INF = float('inf')
resting = load3do('rptg.3do')[0]           # the short resting whip
long = load3do('rlwgfore.3do')[0]          # the lash, shown from frame 3 to 11.4
carried = [CarriedMesh.fromModel(long, 'k_rhand', [(3, 11.4)], name='rlwgfore'),
           CarriedMesh.fromModel(resting, 'k_rhand', [(-INF, 3), (11.4, INF)], name='rptg')]
report = auditFloorContact(model, [lash], floor=[PoseLayer(stand1, 0.0)], nodes=[], carried=carried,
                           tolerance=0.0)
```

`ranges` are half-open `[start, end)` intervals of sample time. `nodes=[]` measures only the carried
meshes. `hides` names model nodes whose own meshes are not drawn while the carried mesh shows, for a
swap that replaces part of the model. `CarriedMesh.fromModel` applies the weapon model's own
hierarchy but not its insert offset, because the engine places the weapon's root at the joint matrix
itself. Points can also be given directly as an (N, 3) array in the joint's mesh frame. The schedule
is the caller's to supply: copy the times from the COG.

## Camera reading

JK's third-person camera is camera 1, of type 4. `sithCamera_Startup` sets its offset to (0, -0.2,
0.06) from the eye. `sithCamera_Update` looks from there at a point 0.3 ahead of the eye
(`sithCamera_trans`), in the thing's frame turned by the aim pitch. At a level aim it looks along
(0, 0.5, -0.06), 6.84 degrees below level, behind and a little above the character.
`thirdPersonViewDirection(aimPitch)` returns this direction in the model's frame. Red's whip tools
used 15 degrees; the source gives 6.84.

```python
camera = auditCameraReading(model, [lash], carried=carried)
for row in camera['sequences'][0]['rows']:
    print(row['time'], row['carried'], row['across'], row['length'], row['ratio'])
```

For each sample, `across` is the longest distance between two points of the shown meshes after
projecting them onto the plane across the view. `length` is the same without projecting, and
`ratio` is `across / length`. A straight stick at angle a to the view reads `length * sin(a)`. A
sample **reads short** when `length` is at least `minLength` (default 0.05) and `ratio` is below
`shortRatio` (default 0.5). The finding `reads_short` gives the sequence, the sample count and the
sample with the lowest ratio. Pass `direction=` to use another view, or `nodes=` to measure meshes of
the model itself.

## Checked against released numbers

These checks ran on the private release files. None of that data is in the repository.

- **Biddy Blas R3.6, all 91 KEYs, compared with stock Kyle sample by sample with `lowestPoints`.**
  At integer frames, over the 57 KEYs that are not locomotion, swim or jump KEYs, his lowest point
  is -0.000149 to +0.000193 from Kyle's. That is the released range. Every KEY that is not a swim
  KEY matches the released per-KEY rows to 5e-7. Against the floor at quarter frames, `kywalkf`
  reaches -0.00185 and `kystrafl` -0.00535, as released.
- **The lightwhip, R2 KEYs and R2 COG timing, ten moves at half frames.** The resting hook touches
  the floor at the same half frames as the released report in every move: 2, 0, 6, 3, 0, 5, 0, 0, 0
  and 0 of them. The long meshes never do.
- **The crack, seen along the engine's camera direction.** The old crack's straight whip reads short
  in 15 of its 20 half frames, at a ratio down to 0.09. The R2 crack's never does: its ratio stays
  between 0.55 and 1.00.

## Limits

- Poses are sampled, not played. Puppet timing, fades between KEYs, physics, slopes, steps and the
  player's aim are not run. The floor is one flat plane. Supply aim as `preRotations` on a
  `PoseSample` when it matters.
- The floor is only as right as the `floor` you choose. The engine's own floor depends on the
  template's `height` and `movesize`, which the 3DO does not hold.
- Slip measures sliding on the floor. It does not measure a joint moving along its parent, as the boot
  moving up the shin did. Read that from `Pose.frames`: the ankle in the calf's frame is
  `inv(F_calf) @ F_foot[:, 3]`.
- Travel covers straight movement. Turning KEYs advance with the turn rate and are not modelled, so
  their planted feet show as sliding. Moving diagonally in the game also slides the feet sideways;
  that is not a KEY fault.
- The planted test uses fixed bands. A foot that floats more than `contactBand` above the floor is
  never planted, so fix floating first. A very low-poly foot may count as rolling where a finer one
  would slide.
- The camera reading is orthographic. It ignores perspective, the camera's distance, MotS's
  COG-set camera offsets, the camera's collision with walls, and anything between the camera and
  the mesh. The aim pitch must be given.
- Carried meshes follow the times you give; the COG is not run. Geoset 0 is measured unless
  `geoset` says otherwise.
