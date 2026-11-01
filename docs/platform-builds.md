# Linux and Windows builds

Blender-Sith is a Python extension, not a custom Blender executable. The
`linux-agentic` and `windows` branches share the same format implementation and
tests. Their manifest `platforms` value selects `linux-x64` or `windows-x64`.
Both use extension ID `sith` and version `1.0.2`; install one edition in a given
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

The result is `sith-1.0.2-linux-x64.zip` or
`sith-1.0.2-windows-x64.zip`, with a companion `.receipt.json` containing
archive SHA-256 and per-file raw/packaged SHA-256 values. The common branch
without a platform restriction builds a universal package. The ZIP contains
`blender_manifest.toml`, `__init__.py`, source subpackages and `LICENSE` at the
extension root. Only add-on Python/TOML source and the license are packaged.
LF normalization, stable ordering, fixed timestamps and uncompressed ZIP
members make bytes reproducible across operating systems. Existing packages
and receipts are never overwritten.

In Blender 5.0+, use Preferences > Add-ons > Install from Disk and select the
ZIP. The actual automated validation uses Blender 5.1.2; it does not establish
interactive installation or all other Blender-version behavior.

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
