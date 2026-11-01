# Linux, Windows and macOS packages

Blender-Sith is a Python extension, not a custom Blender executable. The
`linux-agentic`, `windows` and `macos` branches share the same format implementation
and tests. Their manifests select `linux-x64`, `windows-x64`, or
`macos-arm64` respectively.
All use extension ID `sith` and version `1.0.2`; install one edition in a given
Blender extension repository.

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

The result is `sith-1.0.2-linux-x64.zip`, `sith-1.0.2-windows-x64.zip`, or
`sith-1.0.2-macos-arm64.zip`, with a companion `.receipt.json` containing
archive SHA-256 and per-file raw/packaged SHA-256 values. The common branch
without a platform restriction builds a universal package. The ZIP contains
`blender_manifest.toml`, `__init__.py`, source subpackages and `LICENSE` at the
extension root. Only add-on Python/TOML source and the license are packaged.
Unknown files under the add-on root fail the build instead of silently disappearing;
Python bytecode/cache directories are ignored. Explicit empty platform lists are
rejected; omit `platforms` to build without platform restrictions.
LF normalization, stable ordering, fixed timestamps and uncompressed ZIP
members make bytes reproducible across operating systems. Existing packages
and receipts are never overwritten.

In Blender 5.0+, use Preferences > Add-ons > Install from Disk and select the
ZIP. The release build notes identify the exact runtime versions and package
hashes exercised. Automated validation does not establish interactive installation
or every Blender-version behavior.

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
are checked, but native macOS installation and execution have not been tested.
Linux execution of its Python payload is a source check, not a macOS runtime check.
Official Intel Mac builds end at Blender 4.5 LTS, below this add-on's Blender 5.0
minimum. Supporting Intel would require a separately validated custom Blender 5+
build; the Mac release does not claim that support. See [Blender alignment](blender-alignment.md).

The optional [MCP companion](MCP.md) is distributed separately from the extension
ZIPs. Blender users do not need the MCP SDK to install the add-on.
