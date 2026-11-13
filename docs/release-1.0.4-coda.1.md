# Blender Sith Coda 1.0.4 — radius and native pivots

Release **v1.0.4-coda.1**, validated November 12, 2026. Extension version **1.0.4**.
Download from [GitHub](https://github.com/CodaLilitari/blender-sith/releases/tag/v1.0.4-coda.1)
or [GitLab](https://gitlab.com/codalilitari/blender-sith/-/releases/v1.0.4-coda.1).
Older releases are preserved. These ZIPs are Blender extensions, not Blender executables.
In Blender 5.0+, use Preferences > Add-ons > Install from Disk and choose one edition of `sith`.

## Repairs and reproduced evidence

Automatic model radius now measures the assembled exported rest geometry about the native
origin, with parent transforms and each mesh's own pivot. It no longer uses half a local
bounding-box diagonal or applies exported scale twice. Explicit radius helpers remain authoritative.
New 3DO imports retain native pivots, including empty hierarchy nodes; re-export preserves
attachment transforms as well as visible surfaces. Authored PivotConstraint overrides retain priority.
Imported mesh radii use the exported native vertex coordinates after the pivot is separated.

Red's supplied scene reproduced the radius failure: **0.081336** versus an independently
measured native rest bound of **0.120722308148**. The fixed serialized value is **0.120722**,
within six-decimal quantization; her **0.133282** workaround remains conservative.
The archived native model has 40 nodes and 15 nonzero pivots. Before repair, all 15 pivots
were lost and the right-hand attachment shifted about **0.0109580 JKU** despite equivalent
visible geometry. Two repaired import/export cycles preserve all 15 pivots; the largest
attachment-transform error is about **5.23e-9 JKU**. Coda independently reproduced the
failure and implemented the fixes; Red retains credit for the reported build and investigation.
No private model, image, game asset or conversation is bundled; public regressions use synthetic fixtures.

## Exact package validation

| Package | Direct execution evidence | Result |
| --- | --- | --- |
| `sith-1.0.4-windows-x64.zip` | Windows AMD64, Python 3.13.15 with bpy 5.1.2 | 29/29 synthetic checks |
| `sith-1.0.4-linux-x64.zip` | Native Ubuntu x86_64 CI, Blender 5.1.2 | 29/29 synthetic checks |
| `sith-1.0.4-macos-arm64.zip` | Native Apple Silicon macOS 15.8.2, Blender 5.2.3 LTS | 29/29 synthetic checks |
| `sith-1.0.4-universal.zip` | Windows AMD64, Python 3.13.15 with bpy 5.1.2 | 29/29 synthetic checks |

All four exact ZIPs pass Blender 5.1.2's official extension validator on Linux.
The Mac ZIP also passes Blender 5.2.3 LTS's native validator. The 58-test unit suite
passes on all three hosts: Windows has 7 capability/platform skips,
Linux has 0 and macOS has 0.
All tested source hashes match the release ZIPs. Each ZIP has 27 entries;
all 26 non-manifest files are identical across the four editions.

The [successful native platform workflow](https://github.com/CodaLilitari/blender-sith/actions/runs/43360583035) tests source `edb3a4b662196d8704438a6344cbeaad295708f2`.
It verifies official Blender download checksums and builds independently reproducible
packages that match these release bytes. The first local Linux attempt returned no
validation result because the WSL service failed; the native Linux CI result supersedes
that unavailable route. WINDOWS-VALIDATION.json, LINUX-VALIDATION.json and
MACOS-VALIDATION.json retain the exact platform evidence. BUILD-REPORT.json records
source commits, unit results, package hashes and payload identity; SHA256SUMS.txt covers
every release asset except itself. Each ZIP also has a per-file build receipt.

## Scope and attribution

Existing blends that already discarded native pivots need a fresh 3DO import or a
separately verified rig repair. Blender scene import still exposes geoset 0; the
automatic radius routine considers all exported geosets but this does not add scene
support for importing every geoset. These are rest-pose geometric bounds with ordinary
native numeric rounding, not an outward-rounding guarantee, animated bounds or a
complete collision contract. Background tests do not establish interactive installation,
viewport behavior, retail game playback or connected multiplayer.

macOS here means Apple Silicon. The universal package removes the platform metadata
restriction but does not expand the tested architecture matrix. The extension requires
Blender 5.0+; no Intel Mac support is claimed. The optional MCP companion remains separate.
Corner normals, hexadecimal metadata, indexed MAT alignment and the previous KEY fixes
are retained. Original Blender-Sith authorship by Crt Vavros / smlu, Darth Martyr's
material-table contribution and GPL-3.0-or-later licensing remain credited.
