# Blender Sith Coda 1.1.0 - upstream integration

Distribution **v1.1.0-coda.1** contains Windows x64, Linux x64, Apple Silicon macOS and universal extension ZIPs. Each archive has 37 members; its 36 non-manifest members are identical across editions.

The implementation integrates upstream `01ff4bd85498074e6f936d9adbbcc93817ca712d` with Coda's reviewed hexadecimal metadata and native-pivot changes, bounded automation and deterministic packaging. Original history and licensing are preserved. The tested implementation is `25955b7976d966a12feff273087b58cb7c82d4e3`; final documentation/platform commits are listed in `BUILD-REPORT.json`.

## Changes

Upstream supplies the resource/application split, native baked-image MAT writing with explicit encoding/palette/cel/mip policy, binary metadata, strict import and opt-in diagnostic recovery. KEY export supports adaptive sampling, fractional timing, held segments and authored Euler winding. Select `JKDF2` or `MOTS`; the default is `IJIM`. Scripts now use explicit exporter modules instead of removed package-root shortcuts.

Normals, linear vertex colors, UV-domain metadata and float-preserving text require inspection of older saved scenes. Native pivots and attachment frames survive fresh import/export and saved/reloaded scenes. Authored collision radii, automatic rest bounds and local mesh culling bounds remain separate concepts. Deterministic packaging reports unreadable directories. The shell wrapper supports upstream ZIP-file arguments and Coda directory arguments. The optional MCP worker follows the current palette lookup API.

## Exact-package validation

| Route | Core | Blender application | Coda regression |
| --- | --- | --- | --- |
| Windows Python 3.13 / bpy 5.1.2 | 264 run, 262 passed, 2 capability skips | 768 passed | 29 passed |
| Linux x64 / Blender 5.1.2 | 264 passed | 768 passed | 29 passed |
| macOS 15.8.2 ARM64 / Blender 5.2.3 LTS | 264 passed | 768 passed | 29 passed |
| Universal ZIP / Windows Python+bpy | Same implementation, no separate claim | Same implementation | 29 passed |

[Native package CI run 43385708162](https://github.com/CodaLilitari/blender-sith/actions/runs/43385708162) passed both jobs. [Upstream matrix run 43385680632](https://github.com/CodaLilitari/blender-sith/actions/runs/43385680632) passed seven jobs. Independently built CI ZIPs match the release bytes. Blender's official extension validator passed all four ZIPs. Private source models were checked separately without distributing them.

Per-package receipts, Windows/Linux/macOS validation summaries, `BUILD-REPORT.json`, `VALIDATION.md` and `SHA256SUMS.txt` accompany the ZIPs.

## Limits and attribution

These are background source/package tests, not interactive viewport, game rendering, multiplayer or Intel Mac certification. Scene round trips cover geoset 0. Shared mesh datablocks may become duplicated native records. Blends which already lost pivots need intact-source re-import or separately verified repair. Recovery does not reconstruct missing records; texture/material closure remains model-specific.

Blender-Sith is by Crt Vavros / smlu under GPL-3.0-or-later. Red's radius and attachment reports motivated reproductions; Coda implemented and checked the integration with Andrei. Original contributors, including Darth Martyr's material-table repair, retain attribution. No private model, transcript or proprietary game data is included.
