# Linux agent edition

This branch builds the Linux x64 Blender-Sith 1.0.2 extension. It preserves the
shared upstream and contributor fixes. Use `scripts/run_regression.sh` with an
explicit Blender executable and a fresh external output directory. The default
is a fresh background job, isolated preferences, two threads and a 300-second
deadline. Receipts include exact source hashes and completed regression results.

See [build and agent commands](docs/platform-builds.md) and the
[fork audit and attribution](docs/fork-audit-2026-11-01.md). The matching Windows
branch is `windows`; add-on Python files are identical across both branches.

The optional [Sith MCP companion](docs/MCP.md) supplies structured format and release tools. General Blender automation is covered by [Blender Lab alignment](docs/blender-alignment.md).
