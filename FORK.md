# Blender-Sith Agentic

This community fork integrates [Blender-Sith by Crt Vavros / smlu](https://github.com/smlu/blender-sith) through [upstream commit `168dadc`](https://github.com/smlu/blender-sith/commit/168dadcb1f744da833ec13f0c115fc08652b05f6), which merged the [hexadecimal-property fix](https://github.com/smlu/blender-sith/pull/16). The fork also includes the [reviewed native pivot revision](https://github.com/smlu/blender-sith/pull/23) and the [native 3DO/KEY audit](docs/MCP.md#will-this-key-animate-the-intended-joint). This source tree adds [model and animation audits](#model-and-animation-audits) for building JK/MotS characters. Coda collaborates with Andrei on the fork and the JK/MotS Model Workshop. The resource core, native MAT writer, recovery and animation architecture come from upstream. Original history, copyright notices and GPL-3.0-or-later licensing are preserved.

The latest packaged release is **v1.2.0-coda.1**, extension version `1.2.0`, displayed as **Blender Sith (Coda)**. It includes editable native pivots and the model/animation binding API. It does not include the model and animation audits, the KEY reseat pass, the Sith Audits panel or the MCP audit tools; they are in this source tree, whose manifest still reads `1.2.0` until the next release. See the [package downloads and verification instructions](docs/platform-builds.md#latest-install-route).

Source distribution remains on [GitHub](https://github.com/CodaLilitari/blender-sith) and [GitLab](https://gitlab.com/codalilitari/blender-sith). Verify each host's exact revision before treating copies as equivalent. The extension ID is `sith`; use one edition in a given Blender extension repository. Blender itself is not forked.

## What this integration brings

- **Native MAT writing:** encode inspected baked images or a Material's complete cel bindings, with explicit encoding and mip policy. Indexed output requires the real CMP. The API default `RGBA32` is not a JK/MotS compatibility recommendation; RGB565 remains a separately tested accelerated-renderer choice. Procedural shaders still need an image bake. In this source tree, Indexed output can optionally be dithered with an ordered 4x4 pattern of two palette entries, limited by a mask, and one 3DO export can write an 8-bit Indexed and a 16-bit RGB565 edition with the same file names. Both are off by default; without them the output is unchanged. See [MAT dithering and editions](docs/audits/materials.md).
- **KEY export:** supported sparse axes, fractional timing, held segments and adaptive curve sampling, preserving explicit zero returns and authored Euler winding. Select `target='JKDF2'` or `'MOTS'`; the default is `IJIM`. Generic/CUSTOM output does not certify an engine contract. In this source tree, each rotation converted from quaternion, axis-angle or another Euler order is written as the Euler triple nearest the previous sample; YXZ Euler curves are still written as authored.
- **Import and resource safety:** strict parsing by default, explicit incomplete-file recovery with diagnostics, material/image ownership checks and validation before replacing output. Recovery does not reconstruct missing source records or palette colors.
- **Format fidelity:** UV-unit metadata, explicit/custom corner normals, supported reflected winding and normal scaling, linear vertex-color reads, float-preserving text, and the upstream 3DO 2.2 RGB writer/reader correction.
- **Testing boundaries:** real codecs run without Blender in the upstream core suite; scene adapters run in real background Blender. Fork automation retains bounded processes, isolated preferences, exact source/archive receipts and external output directories.

Use the explicit `model3doExporter`, `keyExporter` and `matExporter` modules shown in [agent usage](docs/agent-use.md). The optional [local MCP companion](docs/MCP.md) exposes model inspection with polygon counts by geoset, native model/animation binding checks, geometry-only conversion, regression and packaging, and in this source tree the model and KEY audits and the KEY reseat pass.

## Native pivots, attachments and bounds

New imports and the **Native Pivot** XYZ field use the same `3do_pivot` property on mesh and empty joints. The exporter also reads the older `3do_imported_pivot` property from saved scenes. A supported active Blender Pivot constraint takes precedence. The default export preserves native pivots. Enabling **Bake Pivots** writes zero pivots while preserving the exported surface and descendant joint poses. Check attachment frames separately when using the bake option. See the [pivot workflow and compatibility details](docs/blender-compatibility.md).

Automatic model radius follows assembled native rest geometry when no authored radius exists. An authored model `RADIUS` is preserved as a collision-default choice and need not enclose every visible point. Mesh culling radii must enclose their emitted local vertices. Do not enlarge an intentional collision radius to satisfy a visual-bound test. Bounds across animation poses remain a separate concern.

Red's reported radius workaround and archived weapon-attachment findings led to the original reproductions. Coda implemented and checked the fork repair; that credit does not relabel Red's report as an authored add-on patch. The [1.0.4 release record](docs/release-1.0.4-coda.1.md) preserves the historical measurements, including six-decimal serialized radius values. New float serialization requires new output hashes and numeric comparisons.

## Normals, colors and old scenes

The integrated exporter uses position, color and normal when merging vertices. Custom normals and explicitly sharp edges use corner normals and retain their necessary splits. Ordinary flat-shading flags retain geometric vertex normals plus separate face normals; they do not force every polygon corner into its own vertex. UVs remain independently indexed. Inspect native face/vertex normals and the actual exported vertex budget.

SMLiberator's [upstream issue 13](https://github.com/smlu/blender-sith/issues/13) documented the sharp-edge/automatic-merge problem and budget tradeoff. The original Coda patch implemented normal-aware merging; the later upstream representation distinguishes ordinary face shading from explicit/custom splits. Neither attribution claims the original discovery or an optional merge-control UI. The historical flat-cube result of 24 vertices is not a universal requirement for the current representation.

Vertex colors now come from Blender's linear RNA values for byte and float layers. Older scenes manually compensated for the former sRGB byte path need reassessment against native values and rendered appearance. Newly imported UV-domain metadata avoids multiplying pixel UVs by image dimensions twice; old scenes without that metadata require comparison with their source, not a guessed rescale. Importing custom vertex normals is an explicit option.

Visible modifiers, active shape-key deformation and unsupported transform/animation state can fail deliberately instead of silently exporting base geometry. Prepare a duplicate with the intended operations applied/baked. Supported reflected winding is handled, but arbitrary shear, parent inverses/delta transforms or nonuniform rotated-parent combinations are not a general rig-baking feature. Do not blindly apply transforms to an animation master.

## Hexadecimal metadata

The retained `HexProperty` correction normalizes before applying the field's digit budget. Bare and `0x`-prefixed values use uppercase canonical digits and the declared padding; malformed, negative and overflowing values preserve the previous value. Unset fields expose their declared defaults, including `FFFF` for KEY node types and `01` for hierarchy node type. Existing explicit values remain explicit.

Use registered properties and readback assertions; raw ID-property writes bypass the setter. A KEY node-type mask identifies type bits, not node indices. Metadata acceptance is not a playback test. See [agent usage](docs/agent-use.md#metadata-in-automation).

## Model and animation audits

This source tree adds checks for a JK/MotS character cut into rigid pieces that ride on Kyle's skeleton and play the stock animations. They read native 3DO and KEY files without Blender, never change their inputs and return plain-data reports in the style of the binding audit: a `status`, `issue_counts`, at most 32 findings and totals. The [audit reference](docs/audits/README.md) indexes them.

- **Posing:** `sith.audit.pose` poses a model with KEYs the way `rdPuppet_BuildJointMatrices` does: layer priorities, joint frames and world vertices. The other audits build on it.
- **Retail budget and detail levels:** `sith.audit.budget` counts vertices, texture vertices and faces per mesh, corners per face, geosets and nodes against the retail loader. Every 2.1 export counts the model it is about to write and warns, or stops with **Fail Over Retail Limits**. **Detail Levels** writes up to four identical geosets.
- **Cut lint:** `sith.audit.segmentation` finds surface a cut left in the wrong rigid piece. `sith.model.model3doEdit.removeFaces` removes faces and caps the opening.
- **Joint gaps and seat offsets:** `sith.audit.articulation` counts lines of sight through each joint in every sampled pose, and how far each KEY moves each node off its seat.
- **KEY lint:** `sith.audit.keylint` checks headers and track tables as the engine loads them, pops between entries, priorities, and track slots that clash when characters share a pack.
- **Floor, slip and camera:** `sith.audit.ground` measures floor contact against the engine floor, the slip of planted feet and how long a mesh reads from the third-person camera.
- **KEY reseat:** `sith.key.keyReseat` puts the joints that stock KEYs push off their seats back on them, without changing a rotation, and checks that nothing else moved.
- **Where they run:** from Python; from the **Sith Audits** panel in Blender (**Check Retail Limits**, **Colour Pieces**, **Audit Files**, **Reseat KEYs**; see [USAGE](USAGE.md#-auditing-models)); and through seven MCP tools (`audit_budget`, `audit_segmentation`, `audit_articulation`, `audit_ground`, `lint_keys`, `audit_track_slots`, `reseat_keys`).
- **Guides:** the [modeling workflow](docs/modeling-workflow.md) takes a character from references to a retail release, and the [agent guide](docs/agents.md) tells agents what to run and how to report it.

Each audit page names the problem in released or candidate characters that led to it: joints that opened in stock animations because the KEYs move them off their seats; arm surface left in the torso piece that stayed in the air when the arm moved; meshes that sit at the retail limits, which export can push over by splitting vertices; three extra geosets added outside Blender for every release; skin in bands in 8-bit MATs; pops between KEY entries; ground-contact tracks that collided with another character's adapter tracks; and a whip that read as a stub from the third-person camera. The audits port the ideas of the research scripts Red (Claude) used while building and testing Andrei's characters, with synthetic tests in place of game data. Coda maintains the fork and reviews every change before it merges.

Limits:

- The engine rules come from OpenJKDF2's reconstruction of retail JK.EXE 1.01, cited in each module. The audits do not run the game. Retail acceptance remains a test in the game, by a person or a test rig.
- Poses are sampled one KEY, or one set of given layers, at a time. Puppet timing, fades, physics and the player's aim are not run.
- Thresholds are JK units chosen for Kyle-sized characters from a few released characters. A count is a lead, not a verdict.
- The joint-gap audit, the seat offsets and the cut lint need the pose the pieces were cut in. A wrong reference pose gives no warning.
- Dithered MATs, the two editions and four-geoset exports written by the add-on have not been run in OpenJKDF2 or retail JK.EXE. The MCP audit worker has run through the Python+`bpy` route on Linux, not through a Blender executable, Windows or macOS.
- The tests build synthetic models and KEYs. No game data or character file is in the repository.

## Validation status and historical evidence

The [1.2.0 release record](docs/release-1.2.0-coda.1.md) identifies the packages and their validation evidence. The [1.1.0 record](docs/release-1.1.0-coda.1.md) covers the preceding release. Keep three results separate: upstream no-Blender `core`, real Blender `app`, and bounded fork/package checks. Record the source revision and local changes, exact ZIP hashes, measured runtimes, failures and skips. Configured CI is not a successful run. [Test boundaries](tests/README.md) and [agent commands](docs/agent-use.md) describe the checks.

The original fork started at upstream `ecc90131bcfdeabd400225e8a72ce94cbff1ec5c`. Its retained ten-check comparison on `bpy` 5.1.2 reported four passes/six failures upstream and ten passes in the focused fork. The [upstream receipt](tests/evidence/upstream-bpy-5.1.2.json), [original fork receipt](tests/evidence/coda-bpy-5.1.2.json) and [four-observation probe](tests/evidence/remaining-limitations-bpy-5.1.2.json) retain those source/harness identities. They do not describe today's core or prove current normal/color semantics.

The [2026-11-01 audit](docs/fork-audit-2026-11-01.md), [1.0.3 platform record](docs/release-1.0.3-coda.2.md), [KEY correction notes](docs/key-export-corrections.md), and [1.0.4 release record](docs/release-1.0.4-coda.1.md) remain historical. In particular, 1.0.4's 29-check packages cannot validate 1.1.0. Darth Martyr's material-index separator repair retains its original author/source attribution. Indexed MAT cel consumption without a CMP remains a structural repair, not recovered colors.

## Remaining model-delivery checks

- Blender imports geoset 0 only. Export writes geoset 0 or, with **Detail Levels**, up to four identical copies of it. Other geosets of a source model, and reduced detail levels, still need a format-aware writer or a separately checked process.
- Shared mesh datablocks can still become separate mesh records. Reuse requires accounting for object scale and properties; duplication alone does not make a model invalid.
- Strict geometry import may still contain missing-material placeholders. Recovery can omit invalid faces or retain incomplete records. Keep diagnostics and original sources, and verify actual resource closure.
- KEY targets complement the complete hierarchy/PUP allocation contract. Generic export, synthetic interpolation, one rendered pose and the KEY audits, which pose one KEY at a time, do not prove game animation or blending.
- 3DO 2.2 RGB serialization is repaired upstream, but JK/MotS retains its 2.1 resource lane. MAT encoding, texture dimensions, palette and renderer support remain explicit choices.
- Test actual game rendering, collision, attachments and connected multiplayer for the intended release claims. This project does not certify sector-based level authoring.

## License and attribution

Blender-Sith is Copyright (C) 2019–2026 Crt Vavros. Original and modified add-on code remains under [GPL-3.0-or-later](LICENSE). Coda's integration and automation do not transfer upstream authorship. No proprietary game data, generated character model, model weights, private transcript or third-party asset pack is included.
