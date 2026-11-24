# Reseating stock KEY joints

`sith.key.keyReseat` moves joints that stock KEYs push off their seats back onto them, in the KEY
data. It never changes a rotation. A check poses every changed KEY against its source and reports
what moved. Both run without Blender.

## The fault it fixes

The stock KEYs were authored on Kyle, whose meshes overlap at every joint. Some of them move a
joint a few thousandths of a JK unit off its usual seat:

- The saber family seats the hip piece 0.002 to 0.008 units low and pushes the thighs up into
  it to make up for it.
- The two armed runs shift both forearms about 0.004 units.
- `kystab2` shifts the shoulders about 0.004 units.

Kyle hides this. A character cut into close-fitting rigid pieces opens at those joints. Darth
Lilitari R9.9 showed daylight at the waist in 152 of 1,745 sampled poses. Reseating 37 of her 91
KEYs brought that to 71 (R9.10). Lilivane R2 showed it in 195 poses; reseating 42 KEYs brought it
to 8 (R3).

The first version of the hip rule gave the hip the thighs' whole mean offset. It closed the
saber family. The full seam sweep then showed it had opened the waist in the walks: in a walk the
thighs drop for the stride, and the hip followed them down (`kywalks` on Darth Lilitari: daylight
in 25 of 40 sampled poses, none before). The rule below replaced it. It only takes out offsets
that point opposite ways.

## The rules

**Seated joints.** By default the shoulders, forearms, hands, neck and head. A seated joint that
leaves its seat by more than `tolerance` (0.00002 units) on any axis, at any time the engine
plays, takes its seat position for the whole KEY. Its entries, rotations and timing are kept.
Seats come from a reference KEY (the stand KEY `kystand1.key` at frame 0 for R9.10 and R3), or
from the model's rest positions if you ask for them.

**The hip.** At each time the pass measures two offsets in the hip's own frame (side, front, up):

- `waist`: how far the hip sits from its seat under the torso;
- `push`: the thighs' mean offset from their seats on the hip.

On each axis where the two point opposite ways, the smaller of the two is taken out of both. The
hip moves, and the thighs move back by the same amount, so the torso and both legs stay where
the KEY put them. Where the thighs are still pushed up after that, up to `waistTuck` (0.0025
units) of the push goes to the waist as a small overlap. No joint ends up further apart than the
KEY had it, and no sideways or front-to-back offset grows.

`waistLift` is off by default. Lilivane R3 used 0.0015: where a waist gap is left, the hip
follows the torso up by at most that much. The hip joints open by the same distance.

**Layout.** The hip rule needs Kyle's layout. The hip and the torso's carrier (the torso's
nearest ancestor that the KEY drives: Kyle's second `$$$DUMMY`) hang from one frame, the torso
hangs from the carrier and the thighs hang from the hip. Adapter nodes between them are allowed
when the KEY does not drive them and they have no rest rotation. Darth Lilitari and Lilivane have
such adapters. Anything else is reported as unsupported, and the hip is left alone.

**Node names.** `ReseatNames` sets the hip, torso, thigh and seated joint names. The defaults are
Kyle's. Names are matched without regard to case and must each match exactly one model node.

**Timing.** The engine takes a track's value at time `t` from its last entry at or before `t`,
with that entry's deltas applied, also past the last entry (`rdPuppet_BuildJointMatrices`). A
looping KEY plays every time below `FRAMES`; a KEY that pauses or fades out at its end holds the
pose at `FRAMES` (`rdPuppet_AdvanceTrack`). The puppet file or COG that plays the KEY decides
which (`sithPuppet_PlayKey`); the KEY's own `FLAGS` do not. So the hip and thigh tracks are
rebuilt with an entry at every whole frame and at every source entry time, and each entry runs to
the value the rule gives just before the next one. A step in the source stays a step. The last
entry carries the motion on to `FRAMES`.

## Run it from Python

One KEY:

```python
from sith.model.model3doLoader import load3do
from sith.key.keyLoader import loadKey
from sith.key.keyReseat import checkReseat, keySeats, reseatKey

model, _ = load3do('dlil.3do')
seats = keySeats(loadKey('key/kystand1.key'))       # the stand KEY at frame 0
source = loadKey('key/kysbthr2.key')
result, report = reseatKey(model, source, seats)     # waistLift=0.0015 adds Lilivane's step
check = checkReseat(model, source, result)
```

`reseatKey` returns a new `Key` and leaves its inputs as they were. Save it with
`sith.key.keyWriter.saveKey`.

A folder of KEYs:

```python
import glob
from sith.key.keyReseat import reseatKeyFiles

report = reseatKeyFiles('dlil.3do', sorted(glob.glob('key/*.key')), 'reseated',
                        standKeyPath='key/kystand1.key')
```

`standKeyPath` is required. Pass `None` to take the seats from the model's rest positions; do
that only when the rest pose matches the stand KEY. Only the KEYs that change are written. The
function never replaces a file. Before it reads anything, it refuses two KEYs with the same file
name and an output folder that already holds a file or folder named like one of the KEYs. Names
are compared without regard to case. It checks the model's node names and reads the stand KEY
before it creates the output folder, so a run refused for those reasons leaves nothing behind.
Give it a new folder. With `check=True` (the default) every written file is loaded back and
checked against its source. `checkReseatFiles(model, pairs)` runs the same check on any
`(source, result)` file pairs.

## Read the result

`reseatKey` report:

- `status`: `reseated`, `unchanged`, `review` (a finding stopped a rule) or `skipped` (the KEY
  cannot be rewritten safely and is returned unchanged).
- `hip_rule`: `applied`, `within_tolerance`, `not_applicable` (the KEY does not drive the hip,
  the torso, both thighs and a carrier), `unsupported`, `missing_seat` or `off`.
- `carrier`: the node used as the torso's carrier.
- `tracks`: one record per rewritten track, with its node, its rule (`seat`, `hip` or `thigh`),
  its largest position change on any axis, and its entry counts before and after.
- `issue_counts` and `findings` (at most 32 details). Codes: `non_text_key`,
  `invalid_frame_count`, `invalid_track_timing`, `unrecognized_markers` (the loader dropped a
  marker type, so a rewritten file would lose it), `missing_seat`, and `hip_rule_unsupported`
  with a `reason`: `rotated_node_between_torso_and_carrier`, `hip_and_carrier_nested`,
  `hip_and_carrier_frames_differ` or `thigh_frame_differs` (a driven or rotated node between),
  or `thigh_not_under_hip`.

`checkReseat` poses the source and the result alone at full weight at every half frame from 0 to
`FRAMES` inclusive and compares joint frames:

- `largest_orientation_change`: the largest change of any rotation-matrix element of any joint.
  It must be 0 (at most 1e-9).
- `fixed_moved_at_frames` and `fixed_moved_between_frames`: how far any fixed joint moved, in JK
  units. The fixed joints are every joint except the hip piece and the seated joints, with
  everything that hangs from them; the thighs and everything below them stay fixed. So the torso,
  the root and the carrier are fixed too. Limits: 1e-6 at whole frames, 1e-4 between them.
- `hip_moved` and `seated_moved`: how far the hip and the seated joints moved.

Each metric gives its value, the time, and the node's index and name. The header, the markers,
the track list and every track the pass cannot rewrite must be equal. `status` is `pass` or
`fail`; findings are `orientation_changed`, `fixed_joint_moved`, `header_differs`,
`markers_differ`, `track_list_differs` and `unexpected_track_change`.

A good result on Darth Lilitari R9.9's 91 KEYs: 37 KEYs written, orientation change 3e-15,
fixed joints 1e-16 units at whole frames and 0.00005 between frames, the hip up to 0.0074 and the
seated joints up to 0.0059 units.

`reseatKeyFiles` report: `status` (`fail` when a check failed, `review` when any KEY has a
finding, else `reseated` or `unchanged`), `seats`, `changed_keys`, `written`, the worst check
values over the set in `check`, and `per_key` (status, hip rule, largest shift by track index,
check result).

## The KEYs shipped with R9.10 and R3

Darth Lilitari R9.10 and Lilivane R3 were reseated by the research version of this pass. This
module makes the same changes to the same KEYs. At whole frames the results agree within 5e-9
units, the precision of the 8 decimals the research version wrote. Between frames they agree
within 5e-8: where one source entry hands over to the next, the stock numbers do not meet
exactly, and the research version ran its line to the next entry's value instead of the value
just before it.

The exception is the last frame. The research version gave every rewritten track's last entry no
motion, so between `FRAMES - 1` and `FRAMES` the hip, the thighs and the reseated arms and head
stood still while the rest of the body moved on. Every such KEY that the stock puppet file plays
loops, so this plays on every cycle and the joints jump at the loop seam. The largest are the
turns in place (the feet jump about 0.028 units on Darth Lilitari) and swimming. `checkReseat`
covers the last frame and fails those KEYs. The KEYs this module writes keep the motion there.

## Limits

- Reseating cannot close a waist that opens from rotation alone, with every joint on its seat:
  the choke KEY `kychoke0` and the strafes. The pass has nothing to give there.
- A GOB's KEYs replace the stock ones for every player model in that install. Reseated KEYs
  change the joints of the other characters too.
- It rewrites Kyle's own tracks. That conflicts with rigs whose rule is that stock tracks stay
  byte for byte, such as Biddy Blas's. His reseat (R3.6) moves his adapter tracks instead; this
  module does not write adapter tracks.
- The check proves that nothing else moved. It does not prove that the joints close. Run the
  joint-gap audit before and after: the first hip rule passed its own check and still opened the
  walks.
- Each KEY is checked alone at full weight. Blends, fades, the player's aim pitch and puppet
  timing are not checked. The hip's compensation holds only where the hip and both thighs come
  from the same KEY.
- The rule is not linear between entries, so the legs can move slightly between frames while the
  hip turns: up to 0.00006 units on Darth Lilitari and Lilivane.
- Run it on KEYs before adding custom tracks that move the hip's or the carrier's parent frames,
  such as Lilivane's ground-contact tracks. With those tracks the rule reports
  `hip_and_carrier_frames_differ`.
- Text KEYs only. The writer stores numbers at full precision and sorts the markers by frame, so
  a written file differs in text from its source even where the values are equal.
