# Blender Sith Coda 1.0.3 — Windows, macOS and Linux

Release **v1.0.3-coda.2**, validated 2026-11-09. Extension version remains **1.0.3**.
This distribution refresh adds the current Apple Silicon package and synchronizes
GitHub and GitLab. Windows, Linux and universal ZIP bytes are unchanged from
v1.0.3-coda.1; old releases are preserved.

Download from [GitHub](https://github.com/CodaLilitari/blender-sith/releases/tag/v1.0.3-coda.2)
or [GitLab](https://gitlab.com/codalilitari/blender-sith/-/releases/v1.0.3-coda.2).
These are Blender extensions, not Blender executables. In Blender 5.0+, choose
Preferences > Add-ons > Install from Disk and select the appropriate ZIP. Use
one edition of extension ID `sith` in an extension repository.

| Package | Direct execution evidence | Result |
| --- | --- | --- |
| `sith-1.0.3-windows-x64.zip` | Windows AMD64, native Python 3.13.15 with bpy 5.1.2 | 16/16 synthetic checks |
| `sith-1.0.3-linux-x64.zip` | Native Linux x86_64 in WSL, Blender 5.1.2 | 16/16 synthetic checks |
| `sith-1.0.3-macos-arm64.zip` | Native Apple Silicon, macOS 15.8.2, Blender 5.2.3 LTS | 16/16 synthetic checks |
| `sith-1.0.3-universal.zip` | Windows AMD64, native Python 3.13.15 with bpy 5.1.2 | 16/16 synthetic checks |

All four exact ZIPs pass Blender 5.1.2's official extension validator on Linux.
The Mac ZIP also passes the native Mac Blender 5.2.3 LTS validator. The 52-test
unit suite passed on each host: Windows with seven platform/capability skips,
Linux with one PowerShell skip, macOS with no skips. Archive contents and every
tested source hash were matched to the release ZIPs. All 26 non-manifest files
are identical across the four packages. Each ZIP contains 27 entries.

The [successful native Mac workflow](https://github.com/CodaLilitari/blender-sith/actions/runs/42592772833)
tested source `08f47b963e31ed3eb6ae288c1897b913bb2c099c`, verified the official
Blender DMG's SHA-256 and ran the exact Mac package. Its independently built ZIP
matches this release byte for byte. MACOS-VALIDATION.json retains that evidence;
WINDOWS-VALIDATION.json and LINUX-VALIDATION.json retain the local launcher receipts.
BUILD-REPORT.json records common/platform source commits, package identities and
common payload hashes. SHA256SUMS.txt covers every attached package and evidence
file except itself. Each ZIP has its own per-file build receipt.

The implementation retains corner-normal preservation, hexadecimal metadata
normalization, indexed MAT cel/mip alignment and the 1.0.3 KEY fixes for explicitly
keyed zero coordinates and continuous quaternion turns. Original Blender-Sith
authorship by Crt Vavros / smlu and GPL-3.0-or-later licensing are retained;
Darth Martyr's material-table delimiter contribution remains credited.

macOS means Apple Silicon. Official Intel Mac Blender ends at 4.5 LTS, below
this extension's Blender 5.0 minimum; this release does not claim Intel support.
The universal ZIP removes platform metadata restrictions but does not expand
the tested runtime/architecture matrix. Background checks do not establish
interactive installation, viewport behaviour, retail game playback or multiplayer.
The optional MCP companion is separate and is not needed to install the add-on.
