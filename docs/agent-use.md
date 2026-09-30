# Background use and regression checks

Use Blender-Sith through Blender's normal UI for scene inspection, or run a fresh background Blender process for repeatable export checks. An MCP connection can drive the same Python API when interactive inspection is useful; this fork does not require or install an MCP server.

## Run the synthetic checks

From the repository root, using a supported Blender executable:

```text
blender --background --factory-startup --python-exit-code 1 --python tests/background_regression.py -- --output regression-results
```

If an existing compatible Python environment already provides `bpy`, the same suite supports:

```text
python tests/background_regression.py --output regression-results
```

Or explicitly select an existing dependency directory:

```text
python tests/background_regression.py --bpy-site /path/to/existing/python/site-packages --output regression-results
```

`--output` must be a new directory. The suite refuses an interactive Blender process, creates only synthetic objects in the disposable process, registers the add-on in that process, and writes synthetic `.3do`, `.key` and `regression.json` files. It does not save desktop preferences or install an extension. Source hashes before and after make unintended add-on edits visible.

Exit status 0 means all regression assertions passed and source hashes remained unchanged. Status 1 means at least one check failed; inspect each result, not only the total. Unexpected setup errors can also cause a nonzero exit. `regression.json` includes the Blender version, per-check results, add-on hashes and fixture hashes. It deliberately omits absolute machine paths.

To compare with another checkout, use the identical harness:

```text
python tests/background_regression.py --addon /path/to/upstream/sith --output upstream-results
```

For the audited base `ecc90131`, the expected comparison is four passes and six failures. Passing controls cover smooth geometry, UV/material/color channels, hierarchy, and animated KEY output. Failing checks exercise the normal and hexadecimal-metadata fixes. An expected upstream nonzero exit is still a failing test result, not a successful export certification.

## Export a prepared model with a short script

Keep the editable `.blend` and prepare an export copy. Apply the modifiers whose geometry or normals you intend to export, resolve scale, and inspect node names, indices and pivots. Explicitly select the intended root object in the script rather than depending on the active UI selection.

After loading the add-on package and registering it in a fresh process, its existing API is:

```python
from pathlib import Path
import bpy
from sith.model import export3do
from sith.model.model3doLoader import Model3doFileVersion

root = bpy.data.objects["ExportRoot"]
output = Path("candidate.3do")
if output.exists():
    raise FileExistsError(output)

export3do(root, str(output), Model3doFileVersion.Version2_1,
          uvAbsolute=False, exportVertexColors=False, sync_mesh_list=True)
```

The import above assumes the repository root (the directory containing `sith`) is on `sys.path` and `sith.register()` has already run. Blender Extension installation uses a namespaced package; use that package's actual import name instead. The regression script demonstrates process-local loading by exact path and avoids global installation.

`uvAbsolute=False` writes the source UV numbers; `True` scales by the Blender material's image dimensions. Choose based on the target format and renderer contract, not simply the image's pixel resolution. Native GL paths may have an effective UV denominator different from stored MAT dimensions. Calibrate an asymmetric checker and preserve known-good mapping before doing a full texture bake.

`sync_mesh_list=True` follows the exporter's existing mesh/hierarchy ordering behavior. Validate the exported hierarchy and KEY allocation contract; Python execution without an exception is not a game test. Compare neutral pose and movement in the actual single-player and multiplayer configuration you intend to distribute.

## Metadata in automation

```python
bpy.context.scene.sith_key_types = "0xFFFF"
assert bpy.context.scene.sith_key_types == "FFFF"
root.sith_model3do_hnode_type = "0x0001"
assert root.sith_model3do_hnode_type == "01"
```

Use registered properties, not raw ID-property assignments such as `scene["sith_key_types"] = ...`. Invalid input is rejected by preserving the previous value, so scripts should read back critical metadata and assert the intended value.

See [FORK.md](../FORK.md) for precise changes, upstream provenance and known limitations.
