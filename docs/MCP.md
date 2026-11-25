# Blender-Sith MCP companion

This optional local stdio server gives an MCP-capable agent 13 structured tools: six general tools (server information, inspection, the KEY binding audit, conversion, regression and packaging), six model and KEY [audit tools](#audit-tools) and the KEY reseat pass. It uses the official Python MCP SDK and runs each job as a fresh process in the configured runtime: background Blender, or Python with `bpy`. Installing the ordinary Blender extension does not start a server, open a port, or add dependencies to Blender.

The audit tools and `reseat_keys` are in this source tree, not in the 1.2.0 release. Configure the `addon` setting with matching source.

For general scene interaction, use this format-specific companion alongside the [official Blender Lab MCP server](https://www.blender.org/lab/mcp-server/). Keep the two servers in separate external virtual environments: the audited official server uses the MCP SDK 1.x line while this companion pins SDK 2.3.0. This companion does not duplicate a general Python execution or interactive-scene tool. See [Blender alignment](blender-alignment.md) for the integration boundary. Publishing both configurations is not evidence of a tested joint session, native installation, or official Blender endorsement.

| Tool | Behavior |
| --- | --- |
| `server_info` | Read configured roots, resource limits and supported operations without launching Blender. |
| `inspect_3do(path)` | Parse one allowlisted 3DO and return format version, geoset/mesh/vertex/face/hierarchy counts, and up to 100 material names. The first 32 geosets also report native triangles, quads, n-gons and triangle equivalents. It does not load referenced files. |
| `audit_model_key(model_path, key_path, key_target)` | Compare a 3DO hierarchy with one text KEY. Return track-index/name discrepancies, source and skipped marker counts, JKDF2 or MOTS KEY export-limit findings and hashes of both inputs. |
| `convert_3do_to_blend(path)` | Import geoset zero into a new `.blend` in a unique job directory. Geometry and placeholder materials only; no texture/palette lookup. JK 2.1 UV coordinates remain unnormalized without texture dimensions. |
| `run_regression` | Run the bundled synthetic 3DO/KEY/MAT regression harness with logs and a verified launcher receipt. |
| `build_extension` | Build the **configured source checkout's** `sith` directory with the deterministic packager. Return ZIP, SHA-256 and receipt. With a configured Blender executable, also run Blender's built-in extension validator on that exact ZIP. This does not publish or install anything. |
| `audit_budget(path, target="JKDF2")` | Count one 3DO as stored against the retail limits: 512 vertices, 768 texture vertices and 512 faces per mesh, 24 corners per face, 4 geosets, 64 nodes. `JKDF2` or `MOTS` gives `within_limits` or `over_limits`; `IJIM` or `GRIM` gives `counts_only`. |
| `audit_segmentation(model_path, ...)` | Cut lint: surface a cut left in the wrong rigid piece. Optional test poses from a KEY folder. |
| `audit_articulation(model_path, key_dir, ...)` | Joint-gap sweep: daylight through each joint in every sampled pose of each KEY, plus each KEY's seat offsets. The slowest tool. |
| `audit_ground(model_path, key_dir, ...)` | Floor contact against the engine floor by default, and contact slip of planted feet, for each KEY played alone. |
| `lint_keys(key_dir, ...)` | KEY header and track table as the engine loads them, the header against the target's limits, and pops at and between entries. With `model_path`, also the binding audit. |
| `audit_track_slots(characters, custom=18)` | Whether 2 to 8 characters' KEYs can share one pack: `slot_collision` and `slot_intrusion`. |
| `reseat_keys(model_path, key_dir, ...)` | Reseat the KEYs of a folder for one 3DO. Writes reseated copies of the KEYs that change, only inside the job directory. |

The `addon` setting selects the source for parsing, conversion, regression, packaging and the audits. Packaging requires `sith/blender_manifest.toml` and a top-level `LICENSE` in that checkout. Use matching source for the binding API, the geometry-only import option and the audit modules: an add-on without them gives `process_failed` with a diagnostic.

## Will this KEY animate the intended joint?

A replacement 3DO has `root` at hierarchy slot 0 and `head` at slot 1. Two KEY files both call their single track `head`. Before opening a scene, the modeller can ask `audit_model_key` which slot each file addresses.

```json
{"model_path":"/srv/authorized-models/triangle.3do","key_path":"/srv/authorized-models/head.key","key_target":"MOTS"}
```

Read `model.binding` in the result. These outcomes use the repository's synthetic two-joint model.

| KEY entry | Binding status | What the modeller learns |
| --- | --- | --- |
| `NODE 1`, `MESH NAME head` | `consistent` | The indexed slot exists and its model name matches. A sparse KEY can animate just one joint. |
| `NODE 0`, `MESH NAME head` | `review` | Slot 0 belongs to `root`. Check the intended destination. A name discrepancy can be intentional. |
| `NODE 2` with three declared KEY joints | `incompatible` | This model has no slot 2. Changing the label alone cannot supply it. |

The check reads both native files and applies the selected KEY target's numeric and export-limit checks. A completed job can contain an incompatible pair. An empty track list has its own `no_tracks` status when there are no other findings. The report shows at most 32 findings plus totals for every issue. Pose quality and in-game playback still require testing.

The same comparison works in an existing Blender Python script or Blender Lab session with this add-on loaded. Import `auditModelKey` from the add-on's `key.modelBinding` module and pass the objects returned by `load3do(path)[0]` and `loadKey(path)`. The companion handles isolated file reads, input hashes and structured results for MCP clients.

`build_extension` reports an explicit `validator` result. With the Blender executable route, it runs `blender --command extension validate ZIP` in a fresh bounded process with separate preferences and logs; a rejected archive or validator process failure makes the top-level status `validation_failed`. With the Python+bpy route, packaging can pass while `validator.status` remains `not_run` with the reason that no Blender CLI executable was configured. Importing `bpy` is not a substitute for official extension validation.

## Audit tools

The six audit tools and `reseat_keys` run the add-on's native audit and reseat modules on snapshots of their inputs. Each job runs `mcp_server/audit_worker.py`, a fixed worker, in the configured runtime. It loads the configured add-on's resource modules by path and does not register the add-on, import `bpy` or touch Blender data. Each page in the [audit reference](audits/README.md) explains what an audit checks and how to read it; this section covers the tools.

### Inputs

- `model_path` and `reference_key`/`stand_key` are regular files beneath a read root, as for the other tools.
- `key_dir` is a folder beneath a read root. Only regular `.key` files directly in it are used; subfolders are not searched. Links, junctions and other special entries are skipped and counted in `inputs.keys.skipped` (`links`, `not_regular`). KEY names that differ only in case are refused, because the engine looks KEYs up without case.
- `keys` selects files of `key_dir` by plain file name, matched without case. Paths, wildcards and repeated names are refused.
- Limits: at most 512 KEY files and 64 MiB per selection, 64 MiB per file, and 256 MiB for everything one job copies.
- Ranges: `step` 0.05 to 1000 frames; `scale` 0.01 to 100; `geoset` 0 to 63; `reference_frame` -1,000,000 to 1,000,000; `stand_frame` 0 to 1,000,000; a numeric `floor` -1000 to 1000; `move_size` and `thing_height` 0 to 100; `waist_tuck` and `waist_lift` 0 to 0.01; `contacts` 1 to 64 node names or indices; `custom` a first slot 0 to 1024, `"names"`, or 1 to 64 slot numbers from 0 to 1024; `characters` 2 to 8, each with a name of 1 to 64 characters that no other character has, ignoring case.

Every parameter is checked before a job is created. A refused call returns a tool error and creates no job. A runtime that cannot be started also returns a tool error.

### The reference pose

`audit_segmentation` and `audit_articulation` measure from the pose the pieces were cut in. The default is the rest pose. `reference_key` gives a KEY's pose at `reference_frame` instead, and then `zero_rotations` must be stated: `true` for characters cut with every joint straight at the stand KEY's seats, `false` to keep the KEY's rotations. A `reference_key` without `zero_rotations` is refused with that explanation. `zero_rotations: true` or a nonzero `reference_frame` without `reference_key` is refused. For Red's Kyle-skeleton characters, pass the stand KEY they were cut against with `zero_rotations: true`: stock `kystand1` for Darth Lilitari, and Biddy Blas's own `kystand1`. A reseat can rewrite a character's own copy of `kystand1` too, so after a reseat keep passing the stand KEY the pieces were cut against, not the reseated copy. The report compares the chosen joint frames with rest and flags a reference KEY that drives no model nodes. A plausible result can still come from the wrong cut pose.

### Tool parameters

| Tool | Parameters (defaults) |
| --- | --- |
| `audit_budget` | `path`, `target` (`"JKDF2"`; also `MOTS`, `IJIM`, `GRIM`) |
| `audit_segmentation` | `model_path`, `reference_key` (none), `reference_frame` (0), `zero_rotations` (none), `key_dir` (none; adds test poses for separation), `keys` (all; needs `key_dir`), `step` (2), `geoset` (0), `detail` (`"review"`; also `high`, `info`) |
| `audit_articulation` | `model_path`, `key_dir`, `keys` (all), `step` (2), `reference_key` (none), `reference_frame` (0), `zero_rotations` (none), `joints` (`"derived"`, or `"kyle"` for Kyle's 15 joints), `scale` (1; multiplies the anchor lengths for a character of another size), `seat_offsets` (true), `geoset` (0) |
| `audit_ground` | `model_path`, `key_dir`, `keys` (all), `step` (1), `floor` (`"engine"`; also `"rest"` or a height), `move_size` (0.065), `thing_height` (0), `crouch_keys` (none), `contacts` (none), `travel` (none), `geoset` (0) |
| `lint_keys` | `key_dir`, `keys` (all), `model_path` (none), `target` (`"JKDF2"` or `"MOTS"`), `pops` (true) |
| `audit_track_slots` | `characters` (a list of `{name, model_path, key_dir, keys}`, `keys` optional), `custom` (18) |
| `reseat_keys` | `model_path`, `key_dir`, `keys` (all), `stand_key` (none), `stand_frame` (0), `rest_seats` (false), `waist_tuck` (0.0025), `waist_lift` (0), `check` (true) |

- `audit_ground`: `floor: "engine"` is where physics holds a standing player, from the template's `move_size` and `thing_height` (stock `walkplayer`: 0.065 and 0). KEYs named in `crouch_keys` play on the crouch floor; there are none by default. `crouch_keys`, `move_size` and `thing_height` are refused with any other floor. `contacts` adds contact slip for those nodes. `travel` maps movement-controlled KEY names to their (x, y) direction, for example `"kywalkf.key": [0, 1]`. Every name in `crouch_keys` and `travel` must be among the selected KEYs.
- `lint_keys` loads each KEY the way the engine reads it (`strictTracks=False`), so it sees tracks the default loader refuses. It checks each header against the `target`'s limits (JOINTS, markers, names, FRAMES and FPS) with or without `model_path`; `model_path` adds the full binding audit, `joints_below_model` and position jumps.
- `reseat_keys` takes its seats from `stand_key` at `stand_frame`, or with `rest_seats: true` from the model's rest positions. Give one of the two. Use the rest positions only when the rest pose matches the stand KEY.

Each tool plays every KEY alone. The joint sweep of `audit_articulation`, `audit_ground` and the test poses of `audit_segmentation` sample each KEY at `0, step, 2*step ...` up to `FRAMES - 1` at most, and the seat offsets stop below `FRAMES`. Sampling the last interval, up to `FRAMES` (`toEnd=True`), runs only from Python. So do `auditCameraReading`, `keyPriorityReport`, layered poses, carried weapon meshes, the joint-gap cameras and anchor settings other than `scale`, the cut-lint thresholds and joint lists, and `removeFaces`. `pieceColours` runs from Python and from **Colour Pieces** in Blender; neither it nor `removeFaces` is an MCP tool.

### What `reseat_keys` writes

`reseat_keys` writes a reseated copy of each KEY that changes, only into the job's `reseated` folder. It never changes an input. The worker lists each written file by name with its size and SHA-256. The server checks that each one is a regular file directly in that folder with those values, and that the folder holds nothing else. It then returns its own record of each file under `outputs`: `name`, `path`, `size` and `sha256`. Any other result is `invalid_report`.

Install only the written files, after review. A GOB's KEYs replace the stock ones for every model in that install, so reseated stock KEYs change the other characters too. With `check: true` every written KEY is posed against its source; `audit.reseat.status` `fail` means a written KEY failed and must not be installed. The check shows that nothing else moved, not that the joints close: run `audit_articulation` before and after on the same KEYs, `step` and reference. The hip rule needs Kyle's layout (`k_hip`, `k_torso`, `k_lthigh`, `k_rthigh` and the seated joints, each named once). Run it before adding custom tracks that move the hip's frames.

### Results

A job's `status` is `passed`, `source_changed`, `invalid_report`, `process_failed` or `timeout`. `passed` means the audit ran and its result was accepted. It says nothing about the model: the verdict is `audit.<section>.status`.

| Section | Statuses |
| --- | --- |
| `budget` | `within_limits`, `over_limits`, `counts_only` |
| `segmentation` | `clean`, `review`, `suspect` |
| `articulation` | `clean`, `review`, `no_joints` |
| `seat_offsets` | `on_seat`, `off_seat`, `no_tracks` |
| `ground` | `clear`, `review`, `empty` |
| `lint` | `consistent`, `review`, `incompatible`, `no_tracks` |
| `track_slots` | `consistent`, `incompatible` |
| `reseat` | `fail`, `review`, `reseated`, `unchanged` |

A result has these fields:

- `job_id`, `job_directory`, `audit_kind`, `exit_code`, `elapsed_seconds`, `source_unchanged` and `status`; after a timeout also `owned_tree_cleanup_succeeded`.
- `inputs`: the source path, SHA-256 and size of the model and the reference KEY; the KEY folder with its `count`, `bytes`, `skipped` and each file's `name`, `size` and `sha256`; or, for `audit_track_slots`, each character's model and KEYs.
- `audit`: `schema` (`blender-sith-mcp-audit-v1`), `kind`, `runtime` (Python, NumPy and Blender versions), `audit_seconds`, `parameters`, `inputs` (model counts; KEYs loaded, and those that could not be read with their errors; the reference used), one section per check, `limits` (`rows` 64, `face_indices` 256, `findings` 32, `geosets` 4) and `full_report`.
- `report`: the `path`, `sha256` and `size` of the complete `report.json` in the job directory.
- `outputs`: `reseat_keys` only, as above.
- `progress` when the job did not complete: its `stage`, `keys_done`, `keys_total`, `current` and `elapsed_seconds`. `diagnostic` on `process_failed`.

The summary keeps at most 64 rows of each list it cuts and gives the full count beside it as `<list>_total`. Face index lists keep 256 entries. The budget keeps the first 4 geoset rows, with `geosets_total`. The complete reports are in `report.json`.

| Section | Rows |
| --- | --- |
| `budget` | The whole report, except that `geosets` keeps the first 4 rows (`geosets_total` gives the count), each with at most 64 `mesh_counts` rows and `mesh_counts_total`. `warnings` (one line per finding from `budgetWarnings`) and `issue_counts` cover every geoset; a per-mesh line names every geoset whose mesh has the same name and count. |
| `segmentation` | `findings`, `meshes_flagged` with the `high_faces` and `review_faces` to select, `parts`, `unplaced_meshes`, `shared_meshes`. |
| `articulation` | `joints`, and `keys_with_daylight`, most lines first. |
| `seat_offsets` | `nodes`, and `keys_off_seat`, largest offset first, with `keys_total`. |
| `ground` | `nodes`, `sequences_flagged` with `sequences_total`, and `crouch_floor` with the engine floor. |
| `lint` | `keys_flagged`, worst first; `totals` by status; `recommendations.joints_64`; at most 32 findings, four per KEY first, each with its `key` and `check` (`header`, `binding`, `pops` or `load`); `parameters.limits`, the `PopLimits` used, `entryAngle` and `entryDistance` included. |
| `track_slots` | Each character's custom `slots`. |
| `reseat` | `changed_keys`; `per_key`, failed checks and unreadable KEYs first; the worst `check` values; `seats`. |

A KEY that cannot be read is listed with its error, not fatal. The audits list it under `audit.inputs.keys.skipped` (for `audit_track_slots`, under each character); `lint_keys` gives it an `unreadable_key` finding and counts it as `review`; `reseat_keys` lists it in `per_key` with the status `unreadable`. (The top-level `inputs.keys.skipped` is different: it counts the links and special entries the server did not copy.) When no KEY can be read, `audit_articulation`, `audit_ground` and `audit_segmentation` with `key_dir` end as `process_failed` with a diagnostic, and so does `audit_track_slots` when one character has no readable KEY. `lint_keys` and `reseat_keys` still end as `passed`, with section status `review`: read the lint's `totals.unreadable`, or the `unreadable` rows of the reseat's `per_key`.

The job directory holds `request.json`, the input copies (`input.3do`, `reference.key`, `keys/`, `characters/<n>/`), `report.json`, `result.json`, `progress.json`, `receipt.json`, `process.json`, `stdout.log`, `stderr.log`, `diagnostic.json` after a failure, and the isolated Blender preference folders. `reseat_keys` adds `stand/` and `reseated/`.

### Time

Audit jobs share the configured `timeout`. A full joint-gap sweep is slow: about 80 seconds for 91 KEYs on a 14,600-face model at step 2, after the model loads. A job that reaches the deadline stops with status `timeout` and its `progress`. Use `keys` or a larger `step` for a quicker run, or configure a `timeout` of about 300 seconds for full sweeps, and set the host's tool timeout above it.

### Examples

A joint-gap sweep of a character cut at the stock stand KEY's seats:

```json
{"model_path": "/srv/authorized-models/mychar/mychar.3do", "key_dir": "/srv/authorized-models/mychar/key",
 "reference_key": "/srv/authorized-models/stock/kystand1.key", "zero_rotations": true}
```

Floor contact and slip, with the crouch floor and the travel of the movement-controlled KEYs:

```json
{"model_path": "/srv/authorized-models/mychar/mychar.3do", "key_dir": "/srv/authorized-models/mychar/key",
 "crouch_keys": ["kycrchgn.key", "kycrchbk.key"], "contacts": ["k_lfoot", "k_rfoot"],
 "travel": {"kywalkf.key": [0, 1], "kybackp0.key": [0, -1], "kystrafl.key": [-1, 0], "kystrafr.key": [1, 0]}}
```

Two characters that will share one pack:

```json
{"characters": [
  {"name": "First", "model_path": "/srv/authorized-models/first/first.3do", "key_dir": "/srv/authorized-models/first/key"},
  {"name": "Second", "model_path": "/srv/authorized-models/second/second.3do", "key_dir": "/srv/authorized-models/second/key"}]}
```

A reseat of a character's copy of the stock KEYs, seated on the stand KEY at frame 0:

```json
{"model_path": "/srv/authorized-models/mychar/mychar.3do", "key_dir": "/srv/authorized-models/mychar/key",
 "stand_key": "/srv/authorized-models/stock/kystand1.key"}
```

## Installation

Use Python 3.11+ for the server, with the official `mcp==2.3.0` SDK. Keep the virtual environment and jobs physically outside every Git checkout. The Blender runtime is a separate explicit executable, or a Python interpreter with a compatible `bpy` distribution. Blender itself does not need the MCP SDK.

`mcp_server/requirements-windows-py313.lock.txt` records every installed dependency version from the verified Windows Python 3.13 environment. It is a platform-specific reproducibility record; use `requirements.txt` when resolving dependencies for Linux or macOS.

```sh
python3 -m venv /srv/blender-sith-env
/srv/blender-sith-env/bin/python -m pip install -r /src/blender-sith/mcp_server/requirements.txt
```

Create an explicit configuration outside the checkout, for example `/srv/blender-sith-config.json`:

```json
{
  "addon": "/src/blender-sith/sith",
  "blender": "/opt/blender/blender",
  "read_roots": ["/srv/authorized-models"],
  "job_root": "/srv/blender-sith-jobs",
  "timeout": 120,
  "threads": 2
}
```

The Blender executable setting also accepts a Microsoft Store App Execution Alias named `blender.exe` or `blender-launcher.exe` directly under `%LOCALAPPDATA%/Microsoft/WindowsApps`. Other reparse routes retain the ordinary checks.

The `timeout` applies to every job. A full joint-gap sweep can need more than 120 seconds; use about 300 for full sweeps (see [Time](#time)).

Use absolute existing paths for `addon`, the runtime, and `read_roots`. Name the runtime's real file: a link, such as a `python3` link to `python3.13`, is refused like any other link route. The job root is created if needed. On Windows use ordinary drive paths and JSON backslash escaping (or forward slashes); UNC/device/alternate-data-stream paths are intentionally unsupported. For the Python+bpy route, replace `blender` with `python` and optionally set `bpy_site` to an existing compatible dependency directory. Do not configure both runtimes. On macOS use the executable inside Blender.app; native macOS execution must be validated separately.

An MCP host entry uses the server environment's Python and this command (adapt the host's enclosing configuration syntax):

```json
{
  "mcpServers": {
    "blender-sith": {
      "command": "/srv/blender-sith-env/bin/python",
      "args": ["-B", "/src/blender-sith/mcp_server/server.py", "--config", "/srv/blender-sith-config.json"]
    }
  }
}
```

Start it through your host after reviewing the configured roots. There is no default account, API key, cloud service or HTTP listener. This repository does not modify your host configuration automatically. Set the host's tool timeout above the configured job timeout; regression cleanup can need up to 45 additional seconds.

## Scope and failure handling

Inputs must be regular `.3do` files (and named `.key` files, or for the audit tools a KEY folder) beneath an explicit read root, no larger than 64 MiB each. Relative paths, parent traversal and symlink/junction routes are rejected. A job snapshots only its named inputs into a unique external directory before parsing. Geometry-only conversion passes `loadExternalMaterials=False` to the public importer. Interactive imports retain material discovery by default. Outputs never replace the input; `reseat_keys` writes only into its job's `reseated` folder. Source/add-on hashes are checked after model and audit operations, and the regression launcher verifies its own report and source hashes.

Jobs are serialized per server; a concurrent request returns an error instead of launching a second Blender. The configured deadline terminates the owned subprocess tree. Blender preferences are isolated, user Python injection/optimization variables are removed, and thread counts default to two. Each outer stdout/stderr log retains at most 1 MiB; structured child reports are limited to 256 KiB. Keep job directories as receipts or remove completed ones yourself; there is no automatic deletion tool or retention policy. Regression creates its own detailed logs inside its result directory.

Controlled Blender CLI invocations (model worker, audit worker, regression launcher and built-in extension validator) include `--offline-mode`. This sets Blender's online-access preference for the process; it is not an OS network sandbox or a claim that arbitrary installed Python code cannot open sockets. The separate Python+bpy route has no Blender CLI flags.

Tool results contain a `status`. Only `passed` means the requested operation completed and its report was accepted. A process exit of zero alone is insufficient. The result includes a job directory for logs, input/output hashes where relevant, and bounded structured results. Logs and local receipts may contain input names and absolute local paths; review before publishing them.

This is a local trusted-user service, **not an OS security sandbox**. The configured runtime, add-on, companion source and writable job directory must remain trusted and unchanged by other users during a call. Filesystem checks are not a defense against a hostile user racing filesystem changes. Blender/parsers execute with the server user's permissions; there is no hard memory or disk quota. Cancelled host requests may finish their bounded job before returning. The service exposes no arbitrary Python, shell, scene-editing, network, publishing, blend-file loading or deletion tool.

Geometry-only conversion is a useful starting point, not a textured round trip or retail-game acceptance test. It inherits the upstream importer restriction to geoset zero, and it does not normalize JK 2.1 UVs using absent texture sizes. `inspect_3do` summarizes all parsed geosets. Validation evidence for Windows/Linux and macOS limitations belongs in the release build notes.

## Verification

Run `python -B -m unittest discover -s tests -p test_mcp_server.py -v` with `TEMP`/`TMP` (Windows) or `TMPDIR` (Unix) set to external scratch storage. These unit tests do not require the SDK or Blender. The companion supplies a real stdio integration check using the official SDK client:

```sh
/srv/blender-sith-env/bin/python -B /src/blender-sith/mcp_server/smoke_test.py \
  --config /srv/blender-sith-config.json \
  --input /srv/authorized-models/synthetic-cube.3do \
  --evidence /srv/blender-sith-smoke.json
```

This checks that the catalog holds exactly the 13 tools, exercises inspection, `audit_budget` and conversion, independently verifies the produced blend hash, runs the regressions, builds an extension and verifies traversal rejection. It requires an authorized valid synthetic 3DO and creates new jobs. Its evidence file must be new and outside Git. Keep its absolute-path-bearing evidence local or sanitize it before publication.

Exercise the paired-file audit separately with the same SDK environment and config:

```sh
/srv/blender-sith-env/bin/python -B /src/blender-sith/mcp_server/binding_smoke.py \
  --config /srv/blender-sith-config.json --evidence /srv/new-binding-proof
```

The script creates a disposable config allowing only its synthetic inputs, exercises 22 cases including absent joints, sparse tracks, source marker counts, invalid timing and target limits, verifies both hashes and checks rejection of an out-of-scope KEY. The findings are in `summary.json`. Topology counts use native surface corners before Blender conversion. A quad contributes two triangle equivalents and a five-corner polygon contributes three.

Exercise the audit tools and `reseat_keys` the same way:

```sh
/srv/blender-sith-env/bin/python -B /src/blender-sith/mcp_server/audit_smoke.py \
  --config /srv/blender-sith-config.json --evidence /srv/new-audit-proof
```

It writes small synthetic models and KEYs into the new evidence folder and a disposable config that allows only them. It runs 8 audit cases, at least one for each audit tool and one reseat. It checks the expected section statuses (the cut-lint case accepts any), the input and report hashes, and that only `reseat_keys` returns `outputs`, inside its job's `reseated` folder. It then checks 4 refusals: a KEY folder outside the read roots, a reference KEY without `zero_rotations`, a path in `keys`, and a reseat with no seats. The results are in `summary.json` and `checks.json`.

Protocol documentation: [official Python SDK](https://py.sdk.modelcontextprotocol.io/) and [stdio server guide](https://modelcontextprotocol.io/docs/develop/build-server). The SDK is an optional host dependency with its own MIT license; Blender-Sith and this companion remain GPL-3.0-or-later.
