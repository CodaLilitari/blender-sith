# Blender-Sith Agentic

This community fork integrates [Blender-Sith by Crt Vavros / smlu](https://github.com/smlu/blender-sith) at upstream [`01ff4bd85498074e6f936d9adbbcc93817ca712d`](https://github.com/smlu/blender-sith/commit/01ff4bd85498074e6f936d9adbbcc93817ca712d), Coda's reviewed [hexadecimal-property change (PR16)](https://github.com/smlu/blender-sith/pull/16), [native-pivot change (PR23)](https://github.com/smlu/blender-sith/pull/23), and retained automation/package tooling. Original history, copyright notices and GPL-3.0-or-later licensing are preserved. Coda collaborates with Andrei on the fork and JK/MotS Model Workshop; the broader resource core, native MAT writer, recovery and animation architecture remain attributed to upstream.

**Release:** extension `1.1.0`, display name **Blender Sith (Coda)**, distribution `v1.1.0-coda.1`. Exact-package validation passed; see the 1.1.0 release record and matching package receipts. See [matching packages](docs/platform-builds.md#latest-install-route). Checkouts, release ZIPs and installed extensions have separately recorded identities.

Source distribution remains on [GitHub](https://github.com/CodaLilitari/blender-sith) and [GitLab](https://gitlab.com/codalilitari/blender-sith). Verify each host's exact revision before treating copies as equivalent. The extension ID is `sith`; use one edition in a given Blender extension repository. Blender itself is not forked.

## What this integration brings

- **Native MAT writing:** encode inspected baked images or a Material's complete cel bindings, with explicit encoding and mip policy. Indexed output requires the real CMP. The API default `RGBA32` is not a JK/MotS compatibility recommendation; RGB565 remains a separately tested accelerated-renderer choice. Procedural shaders still need an image bake.
- **KEY export:** supported sparse axes, fractional timing, held segments and adaptive curve sampling, preserving explicit zero returns and authored Euler winding. Select `target='JKDF2'` or `'MOTS'`; the default is `IJIM`. Generic/CUSTOM output does not certify an engine contract.
- **Import and resource safety:** strict parsing by default, explicit incomplete-file recovery with diagnostics, material/image ownership checks and validation before replacing output. Recovery does not reconstruct missing source records or palette colors.
- **Format fidelity:** UV-unit metadata, explicit/custom corner normals, supported reflected winding and normal scaling, linear vertex-color reads, float-preserving text, and the upstream 3DO 2.2 RGB writer/reader correction.
- **Testing boundaries:** real codecs run without Blender in the upstream core suite; scene adapters run in real background Blender. Fork automation retains bounded processes, isolated preferences, exact source/archive receipts and external output directories.

Root-level `sith.export3do` and `sith.exportKey` are no longer the scripting API. Use explicit `model3doExporter`, `keyExporter` and `matExporter` modules as shown in [agent usage](docs/agent-use.md). The optional [local MCP companion](docs/MCP.md) remains separate from the Blender extension. It provides bounded inspection, geometry-only conversion, regression and packaging; it is not the textured native-import route or an interactive scene editor.

## Native pivots, attachments and bounds

New imports preserve native pivots as metadata; export compensates for their temporary mesh bake and restores the native values. Explicit authored PivotConstraint editing takes precedence. Visible geometry and weapon attachment matrices need separate checks: a surface-preserving bake can still move an attachment. Older saved blends which already lost pivots require re-import from an intact native source or a separately verified rig repair.

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

## Validation status and historical evidence

The [1.1.0 release record](docs/release-1.1.0-coda.1.md) records completed package and native validation. Keep three results separate: upstream no-Blender `core`, real Blender `app`, and bounded fork/package checks. Record the source revision and local changes, exact ZIP hashes, measured runtimes, failures and skips. Configured CI is not a successful run. [Test boundaries](tests/README.md) and [agent commands](docs/agent-use.md) describe the checks.

The original fork started at upstream `ecc90131bcfdeabd400225e8a72ce94cbff1ec5c`. Its retained ten-check comparison on `bpy` 5.1.2 reported four passes/six failures upstream and ten passes in the focused fork. The [upstream receipt](tests/evidence/upstream-bpy-5.1.2.json), [original fork receipt](tests/evidence/coda-bpy-5.1.2.json) and [four-observation probe](tests/evidence/remaining-limitations-bpy-5.1.2.json) retain those source/harness identities. They do not describe today's core or prove current normal/color semantics.

The [2026-11-01 audit](docs/fork-audit-2026-11-01.md), [1.0.3 platform record](docs/release-1.0.3-coda.2.md), [KEY correction notes](docs/key-export-corrections.md), and [1.0.4 release record](docs/release-1.0.4-coda.1.md) remain historical. In particular, 1.0.4's 29-check packages cannot validate 1.1.0. Darth Martyr's material-index separator repair retains its original author/source attribution. Indexed MAT cel consumption without a CMP remains a structural repair, not recovered colors.

## Remaining model-delivery checks

- Blender scene import/export still covers geoset 0. Preserve other LODs through a format-aware writer or separately checked process.
- Shared mesh datablocks can still become separate mesh records. Reuse requires accounting for object scale and properties; duplication alone does not make a model invalid.
- Strict geometry import may still contain missing-material placeholders. Recovery can omit invalid faces or retain incomplete records. Keep diagnostics and original sources, and verify actual resource closure.
- KEY targets complement the complete hierarchy/PUP allocation contract. Generic export, synthetic interpolation and one rendered pose do not prove game animation or blending.
- 3DO 2.2 RGB serialization is repaired upstream, but JK/MotS retains its 2.1 resource lane. MAT encoding, texture dimensions, palette and renderer support remain explicit choices.
- Test actual game rendering, collision, attachments and connected multiplayer for the intended release claims. This project does not certify sector-based level authoring.

## License and attribution

Blender-Sith is Copyright (C) 2019–2026 Crt Vavros. Original and modified add-on code remains under [GPL-3.0-or-later](LICENSE). Coda's integration and automation do not transfer upstream authorship. No proprietary game data, generated character model, model weights, private transcript or third-party asset pack is included.
