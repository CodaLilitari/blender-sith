# KEY lint

`sith.audit.keylint` checks text KEYs for custom Jedi Knight (JKDF2) and Mysteries of the Sith
(MOTS) animations. It runs without Blender. It reads the add-on's KEY and 3DO resources and
returns plain data. It never edits its inputs.

| Function | What it answers |
| --- | --- |
| `lintKeyHeader` | Does the engine load the header and track table the way the author meant? |
| `scanKeyPops` | Does the KEY play motion between entries that no entry describes? |
| `keyPriorityReport` | Which model nodes does the KEY drive, and at which priority? |
| `trackRegistry` | Can several characters' KEYs share one pack? |

## Why it exists

Three faults in custom characters led to it.

- **Pops in the lightwhip KEYs.** The engine moves each pitch, yaw and roll component in a
  straight line from one entry to the next. A large change between entries, or a switch to the
  other Euler form of the same rotation, swings a joint through a pose nobody authored. Red fixed
  the lightwhip moves with two rules. The elbow may turn only so far per frame. Each frame keeps
  the Euler triple nearest the previous frame's.
- **JOINTS below the model's node count.** Coda's binding check flagged 29 body KEYs that
  declare `JOINTS 18` or `JOINTS 20` for a 64-node model.
- **Track slots that collide.** Lilivane's ground-contact tracks on nodes 47 and 48 collide
  with Biddy's adapter tracks on the same nodes. Both characters ship KEYs under the same file
  names, and a pack holds one file per name. The two cannot share a pack until one of them
  moves its tracks.

## Engine rules it relies on

The rules come from OpenJKDF2's reconstruction of retail JK.EXE 1.01.

- `rdKeyframe_LoadEntry` allocates one track record per `JOINTS`. It stores each track at its
  node number without a bound check. Retail allocates exactly `JOINTS` records, so a track
  numbered below 0 or at `JOINTS` or above is written outside the allocation. A later track for
  the same node overwrites the earlier one.
- `rdPuppet_BuildJointMatrices` reads tracks only for nodes below `JOINTS`. It finds the
  current entry by scanning forward from the last one. It moves each Euler component linearly
  from that entry. A track counts at its high priority on nodes whose type shares a bit with the
  KEY's `TYPE` mask, and at its low priority elsewhere.
- `rdPuppet_AdvanceTrack` keeps playback time below `FRAMES` while a KEY loops. When a play
  holds its last frame, time stops at `FRAMES`.
- `RDPUPPET_MAX_NODES` is 64 (`engine_config.h`).

Rotations are `R = Rz(yaw) Rx(pitch) Ry(roll)`. Every rotation has two Euler forms,
`(p, y, r)` and `(180 - p, y + 180, r + 180)`. Each component can also carry whole turns.
`sith.key.keyEuler` holds the helpers: `eulerTwin`, `unwrapEuler` and `nearestEuler`.

## Running it

```python
from sith.model.model3doLoader import load3do
from sith.key.keyLoader import loadKey
from sith.audit.keylint import (Character, PopLimits, keyPriorityReport, lintKeyHeader,
                                scanKeyPops, trackRegistry)

model, _ = load3do('character.3do')
key = loadKey('move.key', strictTracks=False)

header = lintKeyHeader(key, model, target='JKDF2')
pops = scanKeyPops(key, model)
layers = keyPriorityReport(key, model, priority=2)      # COG PlayKey priority
pack = trackRegistry([Character('A', model_a, keys_a), Character('B', model_b, keys_b)])
```

`loadKey(path, strictTracks=False)` keeps tracks that the default loader refuses: tracks
numbered outside `JOINTS`, repeated tracks for one node, and more tracks than `JOINTS`. The
engine reads such files, so the lint must see them. Do not import or export a KEY loaded this
way.

`scanKeyPops` and `keyPriorityReport` work on the KEY alone; the model is optional for
`scanKeyPops` and adds the position check. To pose a move over a stand or a gait, pass
`baseLayers=[PoseLayer(stand_key, 0.0, lowPri=0)]` (from `sith.audit.pose`). Use the scanned
KEY's priorities, for example `lowPri=2, highPri=4` for `PlayKey(..., 2, ...)`. Base layers are
held at the frames you give them.

## Reading the results

Each audit report has:

- `status`: `incompatible` (the engine misreads or misplays the data), `review` (it plays, but
  look at it), `consistent` (no findings) or `no_tracks` (nothing to check).
- `issue_counts`: the total for every finding code.
- `findings`: at most 32 detailed findings. `findings_truncated` says whether more exist.

Each finding names its KEY track (`index`, `key_name`) or model node (`index`, `name`). Times are
KEY frames. Speeds are per second at the KEY's FPS. Angles are degrees. Positions are JK units.

### `lintKeyHeader(key, model=None, *, target='JKDF2')`

| Code | Status | Meaning |
| --- | --- | --- |
| `track_past_joints` | incompatible | A track numbered below 0 or at `JOINTS` or above. Retail writes it outside the KEY's allocation. |
| `entry_order` | incompatible | An entry whose frame is not after the previous one's. The forward scan skips the earlier entry and plays this one from a later time. Gives the entry, both frames, and how many entries in the track are out of order. |
| `duplicate_track` | review | Several tracks for one node. `kept_track` is the one the engine keeps: the last. |
| `entry_past_frames` | review | Entries after `FRAMES`. They never play. An entry at exactly `FRAMES` is not a finding. |
| `joints_below_model` | review | The model has nodes at `JOINTS` or above. This KEY cannot drive them. OpenJKDF2 also guards a read of the track table for them that the original may make past its end. Needs a model. |

`recommended_joints` is 64. `recommendations` has a `joints_64` entry when the KEY declares
another count. A recommendation never changes `status`.

With a model, `binding` holds `sith.key.modelBinding.auditModelKey(model, key, target)`.
`status` is the worse of the two reports. The binding audit reports the same out-of-order tracks
as `invalid_track_timing`; `entry_order` adds the entry and the frames. A KEY with `JOINTS 64`
on a model with fewer nodes gets the binding audit's `declared_joint_count_differs`, which is
expected.

### `scanKeyPops(key, model=None, *, limits=PopLimits(), nodes=None, baseLayers=(), lowPri=1, highPri=None)`

Every finding here is `review`. The KEY plays, but its motion between entries is not what its
entries describe.

| Code | Meaning | Size fields |
| --- | --- | --- |
| `euler_flip` | Between two entries the path strays at least `flipDetour` degrees beyond the shortest turn, and the end rotation has a nearer Euler triple: the other form (`form: twin`) or fewer whole turns (`form: turn`). `nearest` is that triple. | `component_change`, `rotation_change`, `detour` |
| `wide_step` | The same detour, with the end already in its nearest form. The step is too wide for linear Euler interpolation. Add entries. | as above |
| `form_switch` | At an entry the rotation is continuous (within 1 degree) but a component jumps by at least `formJump` beyond whole turns. A layer at full weight does not show it. A fade or a second-priority fill does, because partial weights scale each component. | `component_change` |
| `velocity_spike` | The joint turns `spikeRatio` times faster than its neighbours. Not repeated inside an `euler_flip` or `wide_step`. | `speed`, `reference`, `ratio` |
| `position_jump` | With a model: a node moves `jumpRatio` times faster than its neighbours. One finding per event names the node that moved furthest. `nodes` counts the nodes involved. | `distance`, `speed`, `reference`, `ratio` |

`detour` is the largest `angle(start, x) + angle(x, end) - angle(start, end)` along the sampled
path. It is 0 on a shortest turn and close to 360 for an extra full turn. Near pitch 90, yaw and
roll can each change a lot while the rotation barely moves. The detour does not count that as a
flip.

| `PopLimits` field | Default | Meaning |
| --- | --- | --- |
| `step` | 0.25 | Sample spacing in KEY frames. Each segment also gets at least 8 samples. |
| `flipDetour` | 45 | Detour, in degrees, that makes a segment an `euler_flip` or `wide_step`. |
| `spikeRatio`, `spikeFloor` | 8, 360 | A spike is at least 8 times its reference. The reference is never below 360 degrees per second. |
| `jumpRatio`, `jumpFloor` | 8, model height per second | The same rule for node positions. `jump_floor` in the report gives the value used. |
| `window`, `guard` | 2, 0 | Frames on each side whose median speed is the reference, and frames next to the interval left out. |
| `formJump` | 90 | Component change that makes a continuous entry a `form_switch`. |

The reference is the larger of the two medians. A median ignores up to 3 of the 8 samples in a
2-frame window. A burst of about one frame (one entry, in a KEY keyed every frame) therefore
stands out. A longer motion, such as a deliberate strike, sets its own reference. Raise `guard`
to compare against farther neighbours.

The report also gives `tracks_scanned`, `tracks_skipped` (tracks with out-of-order entries; see
`entry_order`), `nodes_scanned` and the `limits` used. By default the position check covers
every node whose world frame a KEY track moves. `nodes` takes indices or names instead. Positions
are mesh-frame origins (`sith.audit.pose`), not vertices.

### `keyPriorityReport(key, model, *, priority=None, lowPri=None, highPri=None)`

Give the COG `PlayKey` priority `p` (low `p`, high `p + 2`), or `lowPri` and optionally
`highPri`. The report lists:

- `high`, `low`: the nodes this KEY drives, each at its priority for that node;
- `lower_layers`: nodes with no track here, so other KEYs or the rest pose decide them;
- `unused_tracks`: tracks the model cannot use, with a `reason` of `past_joints`,
  `no_model_node` or `empty`;
- `node_types` and `node_type_names`: the KEY's `TYPE` mask.

It describes one KEY. Which layer wins a node also depends on the other KEYs playing.

### `trackRegistry(characters, *, custom=18)`

`characters` are `Character(name, model, keys)` or `(name, model, keys)` tuples. Each KEY's
`name` is the file name the engine looks up. Names are compared without case.

`custom` says which track slots count:

- an int `n` (default 18): slots `n` and up, past Kyle's 18-joint skeleton;
- `'names'`: slots the two models name differently, or that only one model has;
- a collection of slot numbers.

`custom_rule` in the report records the choice. For each pair of characters, each KEY name both
ship, and each custom slot, the registry compares the tracks the engine would use:

| Code | Status | Meaning |
| --- | --- | --- |
| `slot_collision` | incompatible | Both characters drive the slot, with different tracks. One must move to another slot before they share a pack. |
| `slot_intrusion` | incompatible | One character drives the slot. The other's model has a node there that carries a mesh, or has a descendant that does, and its own KEY leaves the node alone. In a shared pack the first character's track would move it. `driven_by` names the driver. |

Equal tracks are not findings. A slot that lands on a node with no mesh anywhere below it is not
a finding. Each finding gives the slot, both characters, both node names, how many KEY names
clash and up to 8 of them. `characters` lists each character's custom slots with the node name
in its own model and how many of its KEYs drive the slot.

## Limits

- The checks are static. They do not run puppet timing, play speed, fades, looping or the
  player's aim. They do not check the seam where a looping KEY wraps from `FRAMES` to frame 0.
- The spike and jump rules are heuristics. A fast authored motion of about one frame next to slow
  motion can trip them. Treat those findings as items to look at.
- `nearestEuler` chooses between the two Euler forms and whole turns. At pitch exactly ±90, yaw
  and roll only matter as a sum or difference. It does not choose within that family, so a pose
  keyed exactly at ±90 can still change yaw and roll together.
- `form_switch` is about blending. It does not say how a particular blend will look.
- The position check samples mesh-frame origins, not vertices or attached weapon meshes.
- The registry does not read puppet (`.pup`) or COG files. It assumes a character plays every
  KEY in its set. It compares tracks to 1e-6 and judges meshes from geoset 0.
- `lintKeyHeader` does not check `FLAGS`, markers or names; the binding audit covers markers and
  names.
