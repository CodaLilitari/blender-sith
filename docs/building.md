# Repeatable extension packages

This fork keeps two Python 3.11+ entry points with a shared checkout-membership
query and different archive contracts. Neither installs or publishes anything.

| Entry point | Output contract |
| --- | --- |
| `make_addon.py --output ZIP` | General upstream utility: selected contents of `sith/`, unchanged source bytes, DEFLATE level 9, atomic replacement of an existing ZIP. |
| `build_extension.py --source-dir SOURCE --output-dir DIRECTORY` | Fork release builder: selected Python/TOML files plus original `LICENSE`, CRLF-to-LF normalization, ZIP_STORED, version/platform filename and provenance receipt; existing outputs are refused. |

Run the general packager from the checkout root using one of these commands.

```sh
python -B scripts/make_addon.py --output /external/sith-packages/sith.zip
```

```powershell
python -B scripts/make_addon.py --output C:\builds\sith-packages\sith.zip
```

The shell wrapper `scripts/make_addon.sh` preserves both routes: any explicit
`--source-dir` or `--output-dir` argument selects `build_extension.py`; other
arguments, including `--output ZIP`, select `make_addon.py`. Blender's own
`extension build` remains an alternative. Always select an external destination
for generated validation and release artifacts.

For the fork's release ZIP and receipt:

```sh
python -B scripts/build_extension.py --source-dir /src/blender-sith --output-dir /external/new-package-directory
```

Keep both Python scripts together: the release builder loads the adjacent
`make_addon.py` for its bounded index-membership query. Its archive writer and
receipt format are unchanged. The required source `LICENSE`, `sith/__init__.py`
and `sith/blender_manifest.toml` must all be tracked when building a checkout.

## Package membership

In a Git checkout, the package contains existing regular files tracked in the
index below `sith/`, subject to the existing exclusions. This includes newly
staged files and current working-tree edits to tracked files; it does not require
a clean checkout or package committed blobs instead. **An untracked scratch
`.py` is excluded.** Add an intended new module or resource to the index before
packaging. Both `__init__.py` and `blender_manifest.toml` must be tracked.
Directories with no tracked descendants are pruned before traversal. Missing or
deleted non-required tracked files retain the existing skip behaviour.

The nearest ancestor `.git` directory or file identifies a checkout, including
linked worktrees and submodules. The read-only `git ls-files` query has a
15-second timeout. If Git is unavailable, its index cannot be read, or its normal
repository trust checks fail, packaging stops without replacing an old archive.
It does not silently fall back to including untracked files. No Git configuration
is changed by the packager.

A clean downloaded/extracted source archive without a `.git` ancestor uses the
existing filesystem scan and needs only Python's standard library, **not Git**.
Without an index there is no tracked/untracked distinction, so an ordinary
scratch `.py` in such a source tree will be included. Keep archive-build sources
clean and inspect ZIP membership. The fallback is automatic; do not remove a
checkout's `.git` metadata merely to bypass a membership failure.

The general packager's checkout and source-archive routes retain arbitrary
legitimate resources such as icons or schemas, and
retain the same exclusion rules: `__pycache__`, `.pytest_cache`, `.git`, `.vs` and
`.vscode` directories; `__test.py`, `.directory` and `.DS_Store`; bytecode and
`.blend*` files. Symlinks and Windows reparse directories (including junctions)
are excluded, so directory aliases cannot pull outside content into the ZIP.
This helper does not
implement Blender manifest `[build]` path/exclusion semantics. Its archive layout
is the contents of `sith/` at ZIP root, as before; it does not inject other
repository files.

The fork release builder retains its stricter existing resource policy: selected
`.py`/`.toml` files and the original `LICENSE` are included; unsupported selected
resources fail instead of silently disappearing. Bytecode/cache files are
ignored, and encountered source symlinks or junctions are rejected. Membership
filters untracked files before resource classification. Source archives retain
the same explicit resource policy but have no index to distinguish ordinary
scratch Python files.

Native Linux/macOS CI resolves membership **before** creating Git-free staging
trees. Linux stages selected members once, then copies that clean snapshot for
its platform variants; macOS uses the same staging helper directly. Manifest
adaptation still occurs only in the external staged copy, with its existing
source/adapted hashes. Copying an entire dirty checkout into a source-archive
directory would lose the membership boundary and is not an equivalent route.

## General ZIP determinism and failure handling

Members are sorted by relative path with the existing case-insensitive ordering
and a case-sensitive tie-break. Every entry has timestamp `1980-01-01 00:00:00`,
Unix regular-file permissions `0644`, and DEFLATE level 9. Source timestamps,
permissions and filesystem enumeration order therefore do not affect the ZIP.
Source bytes are copied unchanged, including line endings and binary resources.
Different Git line-ending configurations can produce different input bytes;
compression output can also differ between zlib implementations. Byte identity
is promised for the same member paths/bytes and compression implementation, not
across arbitrary source checkouts or toolchain versions.

Sources are never modified. Output must remain outside the add-on source tree;
use a new external output directory for validation artifacts. Source-scan errors
and archive-write failures leave an existing destination intact and clean up the
temporary ZIP. A successful build atomically replaces the destination, preserving
the existing utility's behaviour. Record a SHA-256 separately if required; this
change adds no new receipt format.

The fork release writer separately retains its case-sensitive member ordering,
fixed timestamp/permissions, ZIP_STORED payloads, CRLF-to-LF normalization and
schema-version-1 receipt with raw and packaged member hashes. Its destination
must be outside the complete source tree and every Git checkout, and neither
an existing archive nor an existing receipt is overwritten. For unchanged
selected source bytes, this membership integration does not change release ZIP
bytes or the receipt schema. The two entry points intentionally do not produce
identical ZIP files.

Run the packaging tests with temporary storage outside a checkout:

```sh
python -B -m unittest discover -s tests/core -p test_packaging.py -v
```

The tests cover repeat bytes after timestamp/permission changes, fixed archive
metadata, unchanged source/resource bytes, checkout membership and untracked
scratch exclusion, source-archive operation without Git, failed/timed-out index
queries, existing exclusions, and atomic failure handling. The real checkout
fixtures skip explicitly if Git is absent; source-archive tests still run.

The fork also keeps the release contract and membership/staging integration
checks (no Blender or network required):

```sh
python -B -m unittest discover -s tests -p test_build_extension.py -v
python -B -m unittest discover -s tests -p test_package_membership.py -v
```

The integration fixture uses an external temporary Git index without commits:
working edits and newly staged modules must ship, untracked modules must not,
direct and CI-staged release ZIP bytes must agree, and source bytes and the
existing receipt/no-overwrite contract must remain intact. POSIX wrapper checks
exercise both CLI routes separately.

Validate the exact result separately with the intended Blender installation:

```sh
/opt/blender/blender --background --factory-startup --offline-mode \
  --command extension validate /external/sith-packages/sith.zip
```

A repeatable ZIP is not evidence of Blender validation or game compatibility.
