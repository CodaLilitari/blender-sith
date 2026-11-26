# Short player models with stock animations

Kyle's animation tells his knee when to bend. A meshless joint can bring the ankle closer to the
knee before that motion reaches the boot. This lets a shorter player use the same numbered joints
and the same stock KEY. Two separate questions decide whether the result suits multiplayer.
Where does the player collide with a shot, and where does the boot land during a step?

## SMLiberator's model-only example

SMLiberator shared his unfinished `JK1-NPCPack.gob` and called its extra joints *ghost nodes*.
The inspected archive has 281 entries, including 43 models and zero KEY, PUP or COG files.
Max (`kymx.3do`), Pic (`kypi.3do`) and the Ugnaught (`kyug.3do`) each retain 18 Kyle slots
and add 16 meshless `PIVOT_*` parents. For example, the Ugnaught's left limb runs
`K_LCALF → PIVOT_LFOOT → K_LFOOT`. The helper's positive Z offset shortens the ankle's
distance below the knee. The KEY still drives the numbered foot slot. A 3DO mesh's `PIVOT`
field only moves that mesh's vertices, while a parent joint also moves its descendants.
[SMLiberator's example in Massassi Official](https://discord.com/channels/881034839401328680/881749278811377695/1575533495005220946)

For Blender inspection, import with `import3do(..., preserveOrder=True)` and keep the meshless parents in the
hierarchy. A direct import of each example into Blender 5.1.2 with Blender-Sith 1.3.0-coda.1
preserved all 34 node indices, names and parents. All 16 helpers came through as Empty
objects. Geometry was read from geoset 0.

**Check the slot numbers.** All three examples put `PIVOT_TORSO` at 18. Four original retail
clips have a track there (`kyfall`, `kyidle1b`, `kyidle2b` and `kysbsabr`). The name stored in
a KEY can say `gun01` while the engine still addresses model slot 18. For a new rig, keep this
slot available for the stock track, or leave it as a leaf without geometry and place the
body-bearing helper in an unused slot. Check the full intended puppet and any keys called by scripts.

To isolate this effect, the fork's pose evaluator sampled all integer frames of the four clips on
all three models, once with the original KEY and once with just track 18 muted in memory. On the
Ugnaught, the two head-joint origins were 0.10775 JKU apart at frame 94 of `kyidle2b`. This
measures the named full-weight layer in isolation. To find when it appears in play, exercise the
actual puppet's priorities and transitions. [KEY slot checks](keylint.md)

## Kyle's multiplayer hitbox

For an original JK multiplayer walkplayer with Kyle's `size` and `movesize` of 0.065 JKU,
the collision sphere can keep Kyle's radius at any visible body height. Its **centre** matters
too. On level ground, when the template omits an explicit `height`, the standing centre is
`max(insertOffset.z, movesize + 0.005)` above the floor. Crouching uses `movesize + 0.01`.
Max, Pic and the Ugnaught specify insertion heights of 0.072, 0.1045 and 0.101 JKU respectively.
Copying one of these offsets into a stock player template would lower the standing sphere.

For Kyle-equivalent collision, retain his template and `INSERT OFFSET -0.000050 0 0.120114`.
Position the art using the hierarchy. The 3DO's render `RADIUS` serves a different purpose.
On the released Biddy and Lilivane R2 rigs in original JK 1.01, six projectile positions inside
the boundary hit and five outside missed for each of Kyle, Biddy and Lilivane in vanilla and SBX.
Two connected clients also recorded equal damage for each defender from the same saber strike.
These measurements qualify those released profiles. A changed template or rig needs its own
in-game comparison. [Engine floor and audit coordinates](ground.md#where-the-floor-is)

## Choosing a static or animated fit

The R2 research comparison posed the existing gameplay bodies with original retail KEYs and with
their shipped private KEYs, at the same phases as stock Kyle. Standing alone gave a largest sole
height difference from Kyle of 0.0002 JKU for Biddy and 0.0049 for Lilivane with the stock clips.
The forward crouch walk exposed the mismatch.

| Largest sole-height difference from Kyle during the forward crouch walk | Biddy | Lilivane |
| --- | ---: | ---: |
| Original retail KEY | 0.0329 JKU | 0.0328 JKU |
| Released character-specific KEY | 0.0091 JKU | 0.0042 JKU |

Across 1,072 half-frame samples of eight grounded clips, even the best single fixed XYZ
correction shared by the hip and torso carriers left a worst-case sole mismatch of at least
0.0185 JKU for Biddy and 0.0166 for Lilivane. This bounds that one adjustment on those rigs.
Changing individual joint offsets or reauthoring the static hierarchy is a different experiment.
These are native-file pose measurements. The projectile and saber results above came from the
released, animated profiles in game.

In a separate original JK 1.01 multiplayer test, we selected Kyle, Biddy R2 and Lilivane R2
in turn with the stock `ky.pup`. Each player's active tracks included the stock stand
(`kystand0`), hit reaction (`kyhit0`), forward walk (`kywalkf`) and crouch walk (`kycrchgn`).
All three had type 1 collision, hit and movement radii of 0.065 JKU, and a standing centre
0.120114 JKU above the floor. The same six of eleven stationary shots hit each player and the
same five missed (mask 427). This confirms playback of those four stock clips on the two
64-node rigs and matching standing collision for the sampled shots. Sole heights and limb positions
through blends still need measurement. Crouching targets and peer saber hits require separate
stock-puppet trials.

SMLiberator's unchanged Ugnaught (`kyug.3do`) played the same four clips with both radii at
0.065 JKU and the same six hits in eleven shots placed relative to its own origin. On the same
floor its standing centre was 0.101000 JKU high, 0.019114 JKU lower than Kyle's.

1. Keep the reference 3DO and KEYs read-only. Pose the proposed static hierarchy with the original
   stock KEY, with the current character-specific KEY, and alongside Kyle at the same frames.
   An extra helper can be stationary during stock playback even when the private KEY animates it.
2. Check the lowest sole in idle, forward and backward walk, both crouch walks and transitions.
   Check horizontal slip against player travel while the boot is planted. One constant translation
   can correct a consistent height error. A changing error or a sliding planted foot calls for
   another adjustment. Swimming uses the body centre as a reference. Jumps and falls naturally
   leave the floor. [Ground contact and slip](ground.md#contact-slip)
3. Test the body seams as the knee bends. For two-handed weapons, compare both palms with the
   handle at the same frames. Check SBX against its own lightstaff clips and attachment locations.
   Measure the shot boundary with separate clients when multiplayer fairness is a requirement.
4. Keep fixed ghost joints when the pose and collision checks pass. Use model-specific KEY/PUP
   routing for the motions that need changing. Red's Lilivane moves the visible torso and hips
   together to keep her short legs grounded. Biddy's crouch corrections move his planted boot
   horizontally as his body travels over it. The released characters retain Kyle's insertion
   offset for standing collision. A stock-only replacement needs to reproduce both contact and gait.

SHA-256 of the inspected NPC example is
`d135544795d7d53b4418fc0a1f0a4811c54b65f55af77fc128f3c9a6da283ec1`.
