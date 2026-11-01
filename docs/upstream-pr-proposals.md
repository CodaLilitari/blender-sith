# Proposed upstream Blender-Sith PR series

These are seven proposed titles and bodies for upstream review. No PR has been submitted, and this document does not establish isolated submission branches. Implementation descriptions were checked against the combined working source; each extracted change still needs its own review and test run.

**PR structure and public-source review: Lilitari; implementation and execution: Coda with Andrei.** Lilitari reviewed the public source snapshots, not the subsequently developed local macOS/MCP implementation or local test execution. Original Blender-Sith authorship and GPL-3.0-or-later licensing remain with Crt Vavros / smlu and the respective contributors.

Upstream preparation should omit fork branding, changed maintainer/name fields, mirror links, release identifiers and platform-branch policy from the proposed diffs. Retain upstream's unrestricted manifest for this pure-Python add-on; platform package examples do not require platform restrictions upstream. Keep implementation, focused tests and necessary usage documentation. PRs 1-4 and 6 are independently reviewable; PR 5's complete regression matrix is easiest to land after 1-4; PR 7 depends on 5 and 6.

**Combined release evidence, not seven isolated PR results:** release `v1.0.2-coda.1` records exact source commits and hashes in `BUILD-REPORT.json` and `BUILD-NOTES.md`. The final automation suite ran 52 tests on Windows (8 skips) and 52 on Linux (1 skip). The exact Linux and Windows packages passed 13 synthetic cases on native Blender 5.1.2/Linux and native `bpy` 5.1.2/Windows. The exact Linux package also passed all 13 under Blender 5.2.3 LTS, which accepted all three platform archives through its official extension validator. Windows exercised all five MCP tools over real SDK 2.3.0 stdio transport and rejected traversal. Linux Blender 5.2.3 exercised inspection, conversion and packaging through `Service`, including the integrated official validator and independent artifact hashes; this is not a Linux transport test. No native macOS execution is established. The release Mac target is Apple Silicon; upstream's unrestricted manifest does not create an official Intel Blender 5 runtime. See [Blender alignment](blender-alignment.md).

## PR 1

**Title:** 3DO exporter: preserve corner normals at hard and custom-normal boundaries

**Body:**

The exporter currently merges vertices by position and color and writes BMesh vertex normals. Corners sharing those attributes can nevertheless have different flat-face, sharp-edge or custom normals. Merging them loses information because 3DO stores one normal per vertex. Two disconnected triangles can collapse six vertices to four; a flat cube can become eight smoothed vertices.

Read `Mesh.corner_normals` in source polygon/loop order and use `(position, color, normal)` as the output vertex key. Merge only matching triples, while retaining independently indexed UVs and the existing material, color and face-property paths. A smooth cube remains eight vertices; flat and fully sharp cubes require 24. That increase represents normal discontinuities rather than accidental duplicate geometry.

The focused test set should retain disconnected coincident faces, flat/smooth/sharp cubes, custom corner normals and mixed n-gon/disconnected geometry, plus UV, material and color controls. Compare serialized normals within the writer's six-decimal precision and verify unchanged source geometry. These cases passed within the combined 13-case native Linux/Windows 5.1.2 runs. Before submission, extract this change into its own branch and record fresh focused results.

This addresses the normal-loss problem reported by SMLiberator in upstream issue #13 without claiming its original discovery. It adds no merge-control UI. Modifier evaluation, object transforms, radius calculation, shared-mesh reuse, additional-geoset import and 3DO 2.2 behavior are unchanged. File-level normal preservation does not certify game-renderer lighting; exported vertex budgets still need inspection.

Dependencies: none beyond the selected upstream base. Separate this from the HexProperty change even though both originated in one fork commit. PR structure and public-source review: Lilitari; implementation and execution: Coda with Andrei.

## PR 2

**Title:** HexProperty: normalize scripted hex input and honor declared defaults

**Body:**

A four-digit hexadecimal property can receive valid scripted input such as `0xFFFF`, but RNA's raw string-length limit runs before the custom setter normalizes it. Unset properties also return an empty string instead of their declared default. These behaviors make scripted metadata assignment inconsistent with the values the fields are intended to represent.

Disable the raw RNA string limit for these properties and enforce the configured digit limit after normalization. Parse hexadecimal input, reject negative values, emit uppercase digits, preserve optional even-digit padding, and keep the previous valid value when normalization fails or exceeds the canonical digit budget. Normalize declared defaults once and return them when no explicit ID property exists. Empty input follows the default-value path; it is not universally an invalid-input no-op.

Tests cover `FFFF`, prefixed values, zero padding, zero, empty input, unset defaults, malformed input, negative values and overflow without RNA callback errors. Retain hierarchy type/flag and animated KEY round trips through registered properties. The combined native 5.1.2 matrix passed these cases on Linux and Windows. Before submission, extract this change into its own branch and record fresh focused results. If additional lowercase-input assertions are added during extraction, identify them as newly executed coverage rather than attributing them to the earlier receipt.

Direct writes to Blender's raw ID-property dictionary bypass the setter. Explicit stored values remain explicit; this is not a migration of arbitrary saved data. The change does not redefine KEY/PUP/3DO metadata semantics, retarget animations or change hierarchy indices. Retail-game and multiplayer behavior remain separate checks.

Dependencies: none. Keep this independently reviewable from corner-normal serialization. PR structure and public-source review: Lilitari; implementation and execution: Coda with Andrei.

## PR 3

**Title:** 3DO writer: separate material indices from long filenames

**Body:**

The material resource table currently relies on a minimum-width filename field to provide whitespace after an index colon. Once a filename fills or exceeds that width, the padding disappears and the index and name become one token. Readers that split material rows on whitespace then fail to recover the filename correctly.

Write an explicit space between the index colon and the existing right-aligned name field. Short names retain alignment; exact-width and longer names remain separate tokens. The change is limited to the material-table row formatter and leaves material order, names and numeric indices intact.

Preserve Darth Martyr's original implementation commit, `a25543f5ba45c5ad203a65ba11193c640b37e255`, as an unsquashed authorship-preserving cherry-pick with its source trailer. Add the regression test in a separate follow-up commit. Do not squash the original implementation into a Coda-authored change or relabel its contribution. The public source is [Darth Martyr's delimiter fix](https://github.com/sithmartyr/blender-sith/commit/a25543f5ba45c5ad203a65ba11193c640b37e255).

The focused case writes filenames of 9, 15 and 33 characters, checks whitespace-tokenized rows, and reparses a 3DO 2.1 file to verify all names survive. It passed within the combined native Linux/Windows Blender 5.1.2 matrix. Before submission, prepare the isolated branch and document its commit sequence and fresh test results.

This does not change material-name normalization, resolve collisions after lowercasing or extension conversion, alter MAT decoding, or modify texture lookup and UV behavior. It makes no claim about every third-party reader.

Dependencies: none. Original implementation: Darth Martyr. PR structure and public-source review: Lilitari; regression implementation and execution: Coda with Andrei.

## PR 4

**Title:** MAT importer: consume indexed mip data when no CMP is supplied

**Body:**

When an indexed MAT has no supplied CMP palette, its colors cannot be decoded. The fallback still needs to advance through the cel's pixel payload. Previously it retained dimensions without consuming that payload, so a subsequent cel header was read from the preceding cel's pixel bytes.

In the missing-palette path, compute the expected byte count for every mip level and consume exactly that many bytes before proceeding. Raise `EOFError` on a short skipped read. Preserve unavailable pixel arrays as `None`, cel dimensions and the existing transparency metadata. The palette-present decoding path and non-indexed decoding remain unchanged; no automatic palette discovery is introduced.

The synthetic fixture contains two indexed cels, sized 4x4 and 2x2, each with two mip levels. Run it with and without a palette; verify dimensions, final stream position and transparency. Check decoded channel data when a palette exists and unavailable pixel arrays otherwise. Truncate the final skipped payload and require `EOFError`. These palette-present and palette-free cases passed in the combined 13-case native Linux/Windows 5.1.2 matrix. Before submission, extract this change into its own branch and record fresh focused results.

Missing palettes still prevent color reconstruction. This patch neither infers palette contents nor certifies all damaged files, historical MAT variants or game resource lookup. It preserves upstream transparency work rather than replacing the importer with an older fork implementation. Synthetic parsing and placeholder checks do not establish retail rendering.

Dependencies: none beyond the selected upstream MAT implementation. Keep this fix separate from the material-row delimiter because it repairs a different reader path. PR structure and public-source review: Lilitari; implementation and execution: Coda with Andrei.

## PR 5

**Title:** Automation: add a bounded headless regression runner with JSON receipts

**Body:**

Add a reusable command-line entry point for synthetic Blender-Sith checks, usable by maintainers, CI and local agents without MCP. Provide Python, shell and PowerShell entry points. Require an explicitly selected Blender executable or Python interpreter with an existing compatible `bpy`; optional `--bpy-site` supplies an explicit dependency directory. The runner downloads and installs nothing.

Start a fresh background/factory process with isolated Blender user directories, sanitized Python injection/optimization variables, two threads by default and a 300-second default deadline. The Blender CLI route includes `--offline-mode`; this preference is not an OS network sandbox. Output must be a new directory outside the source and outside every Git checkout, checking lexical and resolved routes. That strict rule remains mandatory, not an unimplemented optional flag.

A zero exit requires successful process completion, a valid nonempty report with consistent totals, matching harness/add-on hashes, passing assertions and unchanged source/harness bytes. Retain logs and a portable launcher receipt. Timeout/interruption cleanup targets the owned process group/tree and records best-effort cleanup success rather than guaranteeing termination.

Tests exercise report rejection, failure exits, missing executables, changed source, output restrictions, reused paths, spaces, environment isolation, assertion optimization and owned-tree timeout cleanup. Wrapper tests have host-specific prerequisites. The combined runtime and unit results above provide context. Before submission, prepare the isolated branch and record its fresh scope, skips and offline-mode evidence.

This is process orchestration, not protection against hostile local users or unsafe parsers. Thread limits are not speed benchmarks. Synthetic serialization checks do not certify interactive installation, retail gameplay or multiplayer.

Dependencies: land PRs 1-4 first for a green complete matrix, or explicitly select unaffected cases while reviewing the runner. MCP remains optional. PR structure and public-source review: Lilitari; implementation and execution: Coda with Andrei.

## PR 6

**Title:** Build: add deterministic extension archives and per-file provenance receipts

**Body:**

Add a Python 3.11+ packager that maps source bytes to reproducible extension ZIPs and receipts. Blender's official extension builder and validator remain the compatibility authority; this tool adds stable bytes and per-file provenance rather than replacing manifest rules.

Use sorted root-relative members, fixed timestamps and regular-file permissions, stored ZIP entries and CRLF-to-LF normalization. Record raw and packaged hashes/sizes plus final archive hash/size. Include the original `LICENSE` at the extension root. Exclusive creation refuses existing archives or receipts. Reject source symlinks/junctions and output beneath the source or any Git ancestor, including routes through checkout links.

Membership explicitly allows add-on `.py`/`.toml` files plus `LICENSE`, ignores bytecode/cache content, and now fails on unclassified extension resources. Future icons, presets or schemas therefore require a deliberate allowlist update. Manifest `[build]` paths/exclusions are not implemented; do not imply compatibility with those semantics. Keep upstream's unrestricted pure-Python manifest; archive naming can still be tested against explicit platform fixtures without changing upstream distribution policy.

Tests check member layout, normalization, hashes, stable bytes despite metadata differences, naming, invalid manifests, missing files, no-overwrite behavior, unfamiliar resources and link/output rejection. Link-fixture tests use capability-based skips when the host cannot create them. Validate exact finished archives with Blender's official CLI as an additional interoperability check. Before submission, prepare the isolated branch and identify its fresh receipts; combined-suite counts are not isolated packaging results.

The packager alone does not install, publish or run Blender. Its strict external-output policy is intentional and documented. A receipt proves an observed byte mapping, not source trust, runtime correctness or native platform acceptance.

Dependencies: none of the four format fixes or the regression launcher. PR structure and public-source review: Lilitari; implementation and execution: Coda with Andrei.

## PR 7

**Title:** MCP: add an optional local companion for bounded Sith-format operations

**Body:**

Add a separate stdio companion for clients needing structured Sith-format operations. Ordinary extension installation requires no MCP SDK and starts no server. Keep reusable operation logic in `Service`, the fixed worker and existing command-line helpers; SDK tool wrappers dispatch those operations.

Expose five tools: `server_info`, `inspect_3do`, `convert_3do_to_blend`, `run_regression` and `build_extension`. `server_info` returns configured root paths and limits without launching or probing Blender; roots are actual paths, not redacted aliases. Inspection/conversion/regression use the configured trusted add-on. `build_extension` packages the companion checkout's `sith` directory, manifest and license, not the configured add-on directory.

Require explicit read roots and external job storage, reject relative/traversal/link routes, snapshot an authorized regular 3DO, serialize jobs and bound input, logs, reports and execution time. Fresh model processes disable automatic script execution and implicit neighboring MAT/CMP discovery. Conversion imports geoset zero as geometry/placeholder materials; JK 2.1 UVs remain unnormalized without texture dimensions. Retained logs and results can contain local paths.

The current package operation invokes Blender's official validator when configured with a Blender CLI; validation failure prevents success. Python-plus-`bpy` explicitly reports validator `not_run`. Controlled CLI operations include Blender's offline-mode preference. The combined Linux 5.2.3 results above cover these paths; this remains a preference control, not an OS network sandbox.

Combined-source checkpoints exercised all five tools over real SDK 2.3.0 stdio on Windows, including traversal rejection, and native Linux `Service` jobs. Those Linux calls do not establish Linux transport coverage. Unit cases cover bounds, roots, serialization, failures, reports and validator behavior. Before submission, prepare the isolated branch and record its fresh Windows/Linux runtime and protocol results; no native Mac execution is claimed.

Official Blender Lab MCP already covers general Blender operations. This companion is format-focused, with no automatic bridge, installation or tested coexistence claim. Keep its SDK 2.3.0 environment separate from the inspected official project's SDK-below-2 environment. It exposes no arbitrary Python, shell or scene-editing endpoint, and is not an OS sandbox.

Dependencies: PRs 5 and 6, plus reviewed upstream format behavior. PR structure and public-source review: Lilitari; implementation and execution: Coda with Andrei.
