# Windows, Linux, macOS and universal packages

Blender-Sith is a Python extension, not a custom Blender executable. Release
`v1.0.4-coda.1` uses extension ID `sith`, extension version `1.0.4`, and display
name **Blender Sith (Coda)**. The release suffix identifies the distribution;
it does not change the version in Blender's extension manifest. Install one
edition in a given Blender extension repository.

## Latest install route

For `v1.0.4-coda.1`, download the appropriate extension ZIP from the
[GitHub release](https://github.com/CodaLilitari/blender-sith/releases/tag/v1.0.4-coda.1)
or [GitLab release](https://gitlab.com/codalilitari/blender-sith/-/releases/v1.0.4-coda.1).
Choose a named extension asset, rather than the hosting service's source-code ZIP:

| Platform | Extension ZIP | Manifest restriction |
| --- | --- | --- |
| Windows x64 | `sith-1.0.4-windows-x64.zip` | `windows-x64` |
| Linux x64 | `sith-1.0.4-linux-x64.zip` | `linux-x64` |
| macOS Apple Silicon | `sith-1.0.4-macos-arm64.zip` | `macos-arm64` |
| Universal Python package | `sith-1.0.4-universal.zip` | No platform restriction |

The four editions share the format implementation and tests. The universal ZIP
contains the same Python extension without a platform restriction; it is not a
compiled universal Blender binary or evidence of testing on every architecture.
Use the release's package receipts and validation results to check the exact
artifact and runtime tested. The exact 1.0.4 packages passed 29 synthetic checks on Windows, Linux and
Apple Silicon; its [release record](release-1.0.4-coda.1.md) identifies the runtimes and scope.
The earlier [1.0.3 validation](release-1.0.3-coda.2.md) remains historical.

In Blender 5.0+, open Preferences > Add-ons > Install from Disk and select the
extension ZIP. The extension ID is also used by upstream Blender-Sith, so replace
or disable the previous edition in that extension repository before using this
one. The optional [MCP companion](MCP.md) is separate and is not required for
ordinary Blender installation.

## Build an installable ZIP

Use Python 3.11 or newer. No dependency installation or Git subprocess is
required by the packager. Run from any directory, supplying an external
destination outside every Git checkout:

```sh
python3 /path/to/blender-sith/scripts/build_extension.py --output-dir /tmp/sith-package
```

```powershell
python C:\src\blender-sith\scripts\build_extension.py --output-dir C:\builds\sith-package
```

The packager reads `sith/blender_manifest.toml` from the selected source tree.
A source with no `platforms` entry builds `sith-1.0.4-universal.zip`; a source
restricted to `linux-x64`, `windows-x64`, or `macos-arm64` builds the corresponding
named ZIP in the table above. Use `--source-dir /path/to/source` to select another
explicit source tree. A companion `.receipt.json` records the archive SHA-256
and per-file raw/packaged SHA-256 values. The ZIP contains
`blender_manifest.toml`, `__init__.py`, source subpackages and `LICENSE` at the
extension root. Only add-on Python/TOML source and the license are packaged.
Unknown files under the add-on root fail the build instead of silently disappearing;
Python bytecode/cache directories are ignored. Explicit empty platform lists are
rejected; omit `platforms` to build without platform restrictions.
LF normalization, stable ordering, fixed timestamps and uncompressed ZIP
members make bytes reproducible across operating systems. Existing packages
and receipts are never overwritten.

The current regression suite includes automatic-radius and native-pivot checks. Release validation must
identify the exact runtime versions and package hashes exercised; older 10- or
13-check reports remain historical evidence for their recorded source. Automated
validation does not establish interactive installation or every Blender-version
behavior.

## Linux agent workflow

```sh
sh /path/to/blender-sith/scripts/run_regression.sh \
  --blender /path/to/blender \
  --output /tmp/sith-regression-001 --threads 2 --timeout 300
```

Each job is a fresh background process with factory settings, isolated user
configuration, an actual process timeout, and a two-thread default. This makes
short agent jobs bounded and repeatable without changing desktop preferences.
Place runtimes, dependency environments, build archives and results physically
outside Git checkouts. On WSL, use Linux-native storage for repeated working
assets and large jobs; no symlinks or junctions back into the source tree.

The output contains logs, `receipt.json`, and `results/regression.json` with
synthetic exported fixtures. A zero exit requires a valid completed report,
all assertions passing, and unchanged source hashes. Timeout or child-process
failure returns nonzero. CPU limits are configurable; no render-speed claim is
made.

## Windows workflow

```powershell
& C:\src\blender-sith\scripts\run_regression.ps1 `
  --blender 'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe' `
  --output C:\builds\sith-regression-001 --threads 2 --timeout 300
```

An existing native Python installation with compatible `bpy` is also supported:

```powershell
python C:\src\blender-sith\scripts\run_regression.py `
  --python 'C:\Python313\python.exe' --bpy-site 'C:\runtimes\bpy-site' `
  --output C:\builds\sith-regression-002
```

The launcher does not download Blender or install dependencies. Explicit
executable paths handle installations outside PATH. Use `--addon` to point at
an extracted package's root and validate the exact distributable source.
These commands run synthetic add-on tests; retail and multiplayer checks are
separate, as described in [FORK.md](../FORK.md).

## macOS package scope

The Mac ZIP contains the same Python add-on as the Linux and Windows editions,
with Apple Silicon listed in its manifest. It does not contain
a Blender executable or compiled universal binary. Packaging and source equivalence
are separate from native execution. Consult the release record for native ARM64 macOS validation of the exact ZIP.
Interactive installation, viewport use and game playback remain separate checks.
Official Intel Mac builds end at Blender 4.5 LTS, below this add-on's Blender 5.0
minimum. Supporting Intel would require a separately validated custom Blender 5+
build; the Mac release does not claim that support. See [Blender alignment](blender-alignment.md).

The optional [MCP companion](MCP.md) is distributed separately from the extension
ZIPs. Blender users do not need the MCP SDK to install the add-on.

To repeat native Mac and Linux validation, manually dispatch **Native platform validation**
on the common `master` branch in GitHub Actions. It uses the standard `macos-15`
ARM64 runner with Blender 5.2.3 LTS and standard `ubuntu-24.04` x86_64 with
Blender 5.1.2. Both jobs verify official download checksums and retain exact
packages, receipts and logs. Linux validates all four package schemas. The job has no release-publishing step and only runs
in a public repository. It does not run automatically on pushes or tags.
