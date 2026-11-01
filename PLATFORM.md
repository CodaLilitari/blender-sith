# Windows edition

This branch builds the Windows x64 Blender-Sith 1.0.2 extension. It preserves the
same upstream and contributor fixes as `linux-agentic`. Install the built ZIP
through Blender Preferences > Add-ons > Install from Disk. For reproducible
validation, `scripts/run_regression.ps1` supports an explicit Blender executable
or native Python with an existing compatible bpy installation.

See [build and Windows commands](docs/platform-builds.md) and the
[fork audit and attribution](docs/fork-audit-2026-11-01.md). Add-on Python files
are identical across both platform branches.
