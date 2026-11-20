# Background use and validation

This guide covers Blender Sith (Coda) 1.2.0, including upstream `168dadc` and editable native pivots. Use the matching tagged source for the MCP tools and command-line launchers. See the [release record](release-1.2.0-coda.1.md) and [fork provenance](../FORK.md).

Choose one exact add-on and runtime before starting. Record their versions, paths and hashes; do not assume a checkout, installed extension and ambient Python `sith` are the same copy. When using the JK/MotS model workflow, its shared current-install resolver returns the verified installed add-on/runtime and optional bounded regression argv. A missing manifest is not permission to use an older checkout. A deliberately requested development/upstream comparison uses its own explicit source and receipt without changing that default.

Use a disposable background process, isolated Blender preferences, two threads by default and an actual subprocess deadline. Keep runtimes, dependencies, copied source, caches and outputs physically outside Git checkouts without junctions or links back into them. `-B` prevents Python cache writes, not cache reads; clean or isolate caches when source identity matters.

## Check a replacement model against an animation

With matching 1.2.0 source, call the optional companion's `audit_model_key` with the 3DO path, KEY path and `JKDF2` or `MOTS` KEY target. Read `model.binding.status` and the indexed findings before replacing either file. A completed job can report an incompatible pair. For source polygon budgets, `inspect_3do` reports triangles, quads and n-gons by geoset. The same native binding function is available to ordinary Blender Python scripts. See the [worked example and reproducer](MCP.md#will-this-key-animate-the-intended-joint).

## Author or bake a native pivot

In 1.2.0, set `joint.sith_model3do_pivot = (x, y, z)` on a Mesh or Empty joint to author its static native frame. Existing scenes with `3do_imported_pivot` remain readable. The default 3DO export preserves the native pivot. To write a zero pivot, enable **Bake Pivots** or pass `bakePivots=True` to `model3doExporter.export3do`. Compare the attachment frame as well as the visible surface when choosing between them. See the [Blender pipeline](blender-pipeline.md).

## Run the bounded fork checks

Prefer the launcher, with an explicit absolute runtime, add-on and new external output:

```sh
python /path/to/source/scripts/run_regression.py \
  --addon /path/to/verified/sith \
  --blender /path/to/blender \
  --output /external/new-regression-job --threads 2 --timeout 300
```

For an existing compatible Python/`bpy` environment, replace `--blender` with `--python /path/to/environment/python` and optional `--bpy-site /path/to/existing/site-packages`. Preserve the selected virtual-environment interpreter spelling. The launcher does not install dependencies, enable desktop preferences or download Blender. Do not omit `--addon`: the launcher's own checkout is not necessarily the installed package.

The launcher isolates configuration, supervises the owned child process, verifies a completed report and source hashes, and refuses existing output or output within source/add-on/Git trees. Keep `receipt.json`, logs and `results/regression.json`. A zero exit means the asserted synthetic contracts passed for those exact bytes; it does not establish game appearance or multiplayer.

Direct `tests/background_regression.py` entry points remain for established automation, with equivalent supervision and explicit `--addon`. They are not a reason to omit timeouts or run inside an artist's interactive scene. Historical 10/13/16/29-check totals belong to their original harnesses; report the actual candidate total rather than reusing them.

## Run upstream core and application suites

The integrated upstream suites complement the fork harness:

```sh
python tests/run.py --suite core
python scripts/run_app_tests.py --blender /path/to/blender --timeout 600
```

Run these commands from the matching full-source checkout with Python 3.11+ and the dependencies in `tests/requirements.txt`. The app launcher starts background Blender with temporary preferences, offline mode, script auto-execution disabled and two threads. It relays the suite exit code and returns 124 on timeout, 130 on interruption and 127 when Blender cannot start. For the fork's source-hash and JSON receipts, use the bounded fork checks above. The installed extension contains the add-on code; the full checkout supplies the test suites.

The `core` suite exercises real codecs without fake Blender modules. `app` and `all` require real background Blender 5.0+; `all` runs both suites. A standalone core pass does not test scene adapters, `exportMat` or rendering. [tests/README.md](../tests/README.md) gives current coverage and limitations. Record source identity, runtime, totals, failures and skips; completed release checks are recorded separately from unexecuted game acceptance.

## Import an existing textured model

Use the verified source layout whose absolute `addon_dir` is a directory named `sith`. In a fresh process, insert its parent, verify the actual imported module and register before accessing scene properties:

```python
import sys
from pathlib import Path

addon_dir = Path(addon_dir).resolve()  # Explicit verified job input.
sys.path.insert(0, str(addon_dir.parent))
sys.dont_write_bytecode = True

import bpy
import sith
from sith.model import model3doImporter, model3doLoader, model3doExporter
from sith.key import keyExporter
from sith.material import matExporter

assert bpy.app.background
assert Path(sith.__file__).resolve() == addon_dir / '__init__.py'
sith.register()

native, version = model3doLoader.load3do(str(model_path))
warnings = []
root = model3doImporter.import3do(
    str(model_path), mat_dirs=[str(mat_dir)], cmp_file=str(cmp_path),
    uvAbsolute_2_1=True, importVertexColors=True, importVertexNormals=True,
    preserveOrder=True, clearScene=True, allowErrors=False, warnings=warnings,
)
```

Supply exact absolute assets and hash them. A Blender-installed extension has a namespaced module; use that package's verified actual import name instead when operating through that installation. The explicit layout above is for a process-local source load. Root-level `sith.export3do` and `sith.exportKey` are not available.

The importer searches model-relative folders before extra material directories. Isolate the intended package, resolve collisions, hash the actual MATs, provide the real CMP and inspect all decoded images/material assignments. A successful model parse may still use placeholders for missing resources. Record the source geoset count: the scene contains geoset 0 only.

Strict parsing precedes clearing the scene. If recovery is requested, preserve the original and strict error, then use `allowErrors=True` in a separate inspection job and retain warnings/the Blender diagnostic report. Recovery can omit invalid faces or retain incomplete records; it cannot reconstruct missing records or palette pixels. Binary parsing remains strict. Cleanup of newly created data is not an undo of `clearScene=True`, so use a disposable or checkpointed scene.

## Export a prepared model and MAT bundle

Keep the editable master. Prepare a named export copy with deliberate topology, hierarchy indices, units, pivots and animation. Visible modifiers, active shape keys and unsupported transforms are rejected rather than silently exporting base geometry. Bake/apply the intended effects on that copy and compare it with the source.

For a prepared JK/MotS model with normalized Blender UVs and real baked images:

```python
output = Path(model_output_path)  # Absolute candidate path in a new external folder.
if output.exists():
    raise FileExistsError(output)
warnings = []
model3doExporter.export3do(
    root, str(output), model3doLoader.Model3doFileVersion.Version2_1,
    uvAbsolute=True, exportVertexColors=True, sync_mesh_list=False,
    warnings=warnings, exportTextures=True, textureEncoding='INDEXED',
    texturePalette=str(cmp_path), overwriteTextures=False,
    generateMipmaps=True, textureDirectory=str(mat_output_directory),
)
```

Ensure the output parent exists first. Use an explicit absolute material directory. `sync_mesh_list=False` retains independent mesh/node ordering, so establish valid hierarchy indices before export. Do not infer the KEY allocation from mesh count.

The exporter uses UV-domain metadata to avoid scaling imported pixel coordinates twice. An older scene without that metadata requires comparison with native source and actual image dimensions. Use an asymmetric grid on the intended renderer; texture size alone does not determine the runtime UV normalization contract.

`exportTextures=True` encodes real image pixels; procedural shaders need baking first. Explicitly choose `INDEXED` and the intended game's/level's CMP, or a separately tested format such as RGB565. The default `RGBA32` is not a JK/MotS compatibility choice. RGB565 discards alpha; inspect warnings and the intended transparency behavior.

A standalone baked Image or Material can also be written:

```python
warnings = []
wrote = matExporter.exportMat(
    material_or_image, str(mat_output_path), encoding='INDEXED',
    palettePath=str(cmp_path), overwrite=False, generateMipmaps=True,
    warnings=warnings,
)
if not wrote:
    raise RuntimeError('An existing MAT was retained; choose a new candidate path')
```

For multiple cels, pass the Material with contiguous image bindings `_sith_mat_cel_0`, `_sith_mat_cel_1`, and so on. Passing one Image exports one cel. A palette-free placeholder is not a valid pixel source. Decode every emitted cel/mip with the actual CMP and compare its colors, alpha, dimensions and mapping.

With overwrite disabled, existing MATs may be retained while a new model is written. Capture this as reuse, not new pixel output; prefer a fresh candidate folder. Bundle publication prepares geometry/images first and attempts rollback on failure. Preserve originals independently; OS-level rollback failure can leave a recovery staging directory.

## Export the intended KEY target

```python
keyExporter.exportKey(root, bpy.context.scene, str(key_output_path), target='JKDF2')
# Use target='MOTS' for Mysteries of the Sith.
```

The default `IJIM` is different. JKDF2 and MOTS validate at most 64 joints, 8 markers and 31-character resource/joint names. `CUSTOM` removes game-specific limits, not shared format checks; it is not an escape hatch for retail compatibility. Keep the full native hierarchy/PUP allocation contract and reachable animation set consistent.

Adaptive sampling can add fractional entries for supported curves. Evaluate the serialized KEY at actual frame times and between entries, not at the row index. Check explicit zero returns, holds, Bezier motion, wrap boundaries, authored Euler turns and event markers. Record FPS and any explicit override. Unsupported NLA, drivers or constraints require deliberate preparation; a quaternion orientation cannot recover an omitted revolution.

## Metadata in automation

```python
bpy.context.scene.sith_key_types = '0xFFFF'
assert bpy.context.scene.sith_key_types == 'FFFF'
root.sith_model3do_hnode_type = '0x0001'
assert root.sith_model3do_hnode_type == '01'
```

Use registered properties. Raw ID-property writes bypass the setter; rejected input preserves the previous value. The KEY node mask refers to type bits, not node numbers.

## Read back before making a game claim

Read the actual 3DO/KEY/MAT outputs independently. Check material closure, all native geosets, UV units, hierarchy and pivot/attachment matrices. Ordinary flat shading uses geometric vertex plus face normals; custom/explicit sharp corners retain splits. Linear RNA color values replace the older byte-layer sRGB path, and text serialization preserves more numeric precision. Recheck old manually compensated scenes and compare values rather than requiring old six-decimal text.

Mesh culling bounds must enclose emitted local geometry. An automatic model bound and an authored collision `RADIUS` are distinct; retain an intentional authored radius instead of treating it as a failed visual bound. Re-import an intact source when an old scene already lost pivot metadata. The upstream 2.2 RGB formatting repair does not change JK/MotS's 2.1 lane.

Synthetic correctness, a Blender view, actual engine rendering, animation blending and connected multiplayer establish different things. Record the exact tested scope and leave unexecuted cases pending.
