# Blender Sith (Coda) 1.2.0

Install the ZIP for Windows x64, Linux x64 or Apple Silicon macOS through Blender’s **Preferences → Add-ons → Install from Disk**. A universal ZIP is also available. The extension ID remains `sith`. [GitHub release](https://github.com/CodaLilitari/blender-sith/releases/tag/v1.2.0-coda.1) · [GitLab release](https://gitlab.com/codalilitari/blender-sith/-/releases/v1.2.0-coda.1)

## Changes since 1.1.0

- **Native pivots.** The Native Pivot XYZ field lets modellers author the same static mesh or attachment frame that the importer preserves. Older scenes using `3do_imported_pivot` remain readable. **Bake Pivots** is opt-in and writes zero native pivots while preserving the exported surface and descendant joint poses. Check attachment frames when choosing this option. [Pivot workflow](blender-pipeline.md)
- **Model and animation checks.** The native binding API identifies the joint a KEY track addresses in a 3DO and flags missing slots, conflicting labels and target limits. The optional MCP companion exposes this check and reports native triangle, quad and n-gon counts by geoset. The add-on ZIP contains the native API. Install the optional companion from this release’s matching source. [Worked example and setup](MCP.md#will-this-key-animate-the-intended-joint)
- **Geometry-only import.** Scripted imports can explicitly disable neighboring MAT/CMP lookup. The ordinary Blender import retains material discovery by default.
- **Development tools.** The isolated app-suite launcher runs the existing Blender tests. Package builds and platform staging select tracked source files in a checkout. Reproducible ZIPs include per-file hash receipts. [CLI guide](agent-use.md) · [Packaging](building.md)

## Verification

Match your ZIP to `SHA256SUMS.txt` before installing. Each distribution includes its own receipt. The release assets also include `BUILD-REPORT.json`, `VALIDATION.md` and separate Windows, Linux and macOS evidence tied to the exact package hashes. The macOS-specific ZIP targets Apple Silicon. The binding audit checks native files; game playback and attachment appearance still require in-engine testing.

Blender-Sith is by Crt Vavros / smlu and its contributors under GPL-3.0-or-later. Red’s model and attachment reports informed the fork’s pivot work. Coda maintains the fork with Andrei. [Upstream and fork provenance](../FORK.md)
