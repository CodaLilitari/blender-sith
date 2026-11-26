# Which part of the character does this animation track move?

A character can stand correctly until its second idle plays. A JK KEY addresses a numerical hierarchy slot. If a replacement model puts an upper-body carrier at the slot an animation uses for a prop, the game applies the prop motion to the torso and every mesh it carries. The track and node labels can differ harmlessly or even match accidentally.

## Check the bytes the game will use

For each role, record the archive SHA-256, the internal path and hash of the extracted 3DO, the PUP selected by the player/template, and the KEY directory after mod precedence. Use the reference body on which those animations were authored. Keep the character picker, the ordinary in-game player and the cosmetic-only package as three separate cases. A GOB can contain both `3do/name.3do` and `3do/world/name.3do`. Read the static model table and direct-load paths to tell which one is active. When building a cosmetic subset, reopen the output GOB and hash its final internal member. A matching basename alone cannot establish its role.

From a fork checkout containing `sith/audit/slotownership.py`, with the core dependencies installed, run:

```console
python -m sith.audit.slotownership --reference extracted/stock/ky.3do --model final-cosmetic.gob --model-member 3do/player.3do --pup extracted/stock/ky.pup --key-dir extracted/effective-keys --output cosmetic-audit.json
```

The tool is a source-level command. Running it from this checkout leaves a separately installed Blender extension intact. Use a fresh output filename. Supply the complete internal path for a packed model. The report records the actual member path and SHA-256 alongside the containing archive path and SHA-256. Lookup accepts different capitalization and slash direction. Missing or ambiguous paths are input errors. For a loose 3DO, supply its path as `--model` and omit `--model-member`.

The program reads one PUP's explicit rows across every mode and records each use. Inherited actions are covered where first defined. Named KEY resources must resolve in the supplied directory. Add a repeated `--key path/to/action.key` for direct COG calls or a picker-only run animation. A direct KEY can also be checked by itself. The JSON includes the reference, PUP and KEY hashes. The command returns 1 for an ownership conflict, 0 when no conflict was found, or 2 for an input error. Inspect the JSON for attachments that need review even when the exit status is 0.

The Python entry point `auditSlotOwnership(reference, model, keys, bodySlots=(), bodyNodes=())` accepts parsed native objects and a dictionary of effective KEYs. For each active track it compares the body slots below its numerical index in the source and target parent trees. An off-bind constant entry can move a branch just as a changing rotation can. A track that gains control of a reference body joint leading to target geometry is a conflict. A mesh on a formerly empty endpoint is reported for inspection; a sword is a legitimate example. An unused slot or a childless marker with no mesh is harmless. Use a reference with the same canonical body indices. Private KEYs intentionally authored for a custom rig need their own reference.

## Protect a face or limb across revisions

Once you identify a target-only facial mesh or limb carrier, pass its hierarchy name as `--body-node NAME`. Repeat the option to protect multiple parts. The checker resolves the exact name without regard to case on the selected model, requires a unique match with geometry in its branch, and records the resolved name and slot in `declared_body_slots`. A missing, ambiguous or empty declaration returns input error 2. `--body-slot N` remains available for a verified, fixed slot in a particular file.

```console
python -m sith.audit.slotownership --reference stock/ky.3do --model arena.gob --model-member 3do/biddybl.3do --pup stock/ky.pup --key-dir effective-keys --body-node bb_snout --output biddy-arena.json
```

In two cosmetic packages the same `bb_snout` declaration resolved first to 18, where four stock equipment tracks moved the face, then to 27, where the snout stayed attached to the head. Separate full-gameplay and SBX versions of the body use 19 and 21 for the snout. Run each against its actual puppet and reference rig. Include a real equipped mesh or secondary grip in the private animation contract when the mod animates it intentionally. For a short character, compare sole height, stride and the mesh on either side of each leg joint through the affected frames. Correct numerical ownership alone cannot measure ground contact.

## Saber idle example

In the observed retail JK player PUP, the armed-gun and saber second fidgets are `kyidle2b.key` and `kyidle1b.key`. Both write slot 18; the original Kyle body ends at slot 17. The affected cosmetic package had copied a character-picker model with a torso carrier at 18. Against the actual stock puppet, four of its 62 unique KEYs transferred that slot's motion into the body: the two fidgets, `kyfall.key` and `kysbsabr.key`. In the saber fidget the torso was displaced about 0.055 game units relative to the hips. The separate gameplay model kept slot 18 as an empty leaf. Using that verified member as the cosmetic root removed all four ownership conflicts while preserving the stock animation files.

A full-body joint-gap sweep on the privately animated world model had previously found zero openings at its tested waist anchors. It did not exercise the picker body with this cosmetic package's actual keys. This ownership audit diagnoses a different cause from a short waist lining. A clear result still needs pose and engine checks. Exercise both fidgets through a full cycle, an idle transition, falling, and the relevant action clips. Inspect the actual actor from below and from a matched front or side camera. Check all rendered detail levels if their geometry differs.

For geometric openings with the right numerical owners, use the [articulation audit](articulation.md). For name mismatches, file limits and invalid KEY tables, use [model/KEY binding](../MCP.md#will-this-key-animate-the-intended-joint).
