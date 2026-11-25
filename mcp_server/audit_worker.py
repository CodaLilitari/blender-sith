#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixed audit worker for the MCP companion. Not an arbitrary script execution endpoint.

The server writes one request that names an audit from a fixed set, the snapshot files of its job and
parameters it has already checked. The worker runs in the configured runtime (background Blender or
Python with bpy), because that runtime supplies the NumPy the add-on uses inside Blender. It loads the
configured add-on's native resource modules by path. It does not register the add-on, import bpy or
touch Blender data.

It writes three files beside the request:

- ``report.json``: the complete plain-data reports of the add-on's audit functions;
- ``result.json``: a bounded summary for the MCP result. Lists are cut to ``ROWS`` entries (face
  index lists to ``FACES``, the budget's geoset rows to ``GEOSETS``), worst entries first, and
  each cut list has a ``*_total`` count;
- ``progress.json``: the stage and the KEYs done so far, rewritten as the audit runs, so that a job
  stopped at its deadline can say how far it got.

The KEY reseat pass is the one audit that writes files, and only into ``reseated`` beside the request:
a reseated copy of each KEY that changes. ``result.json`` then lists every file there under
``outputs`` by name, with its size and SHA-256, and the server checks them before it reports them.
The pass also copies the stand KEY's snapshot into ``stand`` under the stand KEY's own name, because
``reseatKeyFiles`` records that name as the seat source.
"""
from dataclasses import asdict
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import sys
import time

SCHEMA = "blender-sith-mcp-audit-v1"
PACKAGE = "sith_mcp_audit"
ROWS = 64
FACES = 256
# Geoset rows in the budget summary: the retail limit. Each row keeps up to ROWS mesh rows, so the
# summary stays far below the server's report limit however many geosets the model has.
GEOSETS = 4
STATUS_ORDER = {"incompatible": 0, "unreadable": 0, "review": 1, "consistent": 2, "no_tracks": 3}


class Progress:
    """Small JSON progress record, replaced atomically."""

    def __init__(self, path, kind):
        self.path = Path(path)
        self.data = {"kind": kind, "stage": "starting", "keys_done": 0, "keys_total": None, "current": None}
        self.started = time.monotonic()

    def update(self, **fields):
        self.data.update(fields)
        self.data["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.data), encoding="utf-8")
        temporary.replace(self.path)


def load_addon(root):
    """Import the configured add-on as a package by path, without calling its register()."""
    spec = importlib.util.spec_from_file_location(PACKAGE, Path(root) / "__init__.py",
                                                  submodule_search_locations=[str(root)])
    addon = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = addon
    spec.loader.exec_module(addon)


def native(name):
    try:
        return importlib.import_module(PACKAGE + "." + name)
    except ModuleNotFoundError as exc:
        if exc.name == PACKAGE + "." + name:
            raise ValueError("The selected add-on lacks the native audit modules. Configure matching Blender-Sith source.") from exc
        raise


def runtime():
    bpy = sys.modules.get("bpy")
    import numpy
    return {"python": platform.python_version(), "numpy": numpy.__version__,
            "blender": bpy.app.version_string if bpy is not None else None}


def plain(value, notes):
    """JSON-safe copy: NumPy values to Python, tuples to lists, non-finite floats to null."""
    import numpy
    if isinstance(value, dict):
        return {str(key): plain(item, notes) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value) if isinstance(value, (set, frozenset)) else value
        return [plain(item, notes) for item in items]
    if isinstance(value, numpy.ndarray):
        return plain(value.tolist(), notes)
    if isinstance(value, (bool, numpy.bool_)):
        return bool(value)
    if isinstance(value, (int, numpy.integer)):
        return int(value)
    if isinstance(value, (float, numpy.floating)):
        value = float(value)
        if not math.isfinite(value):
            notes["nonfinite"] = notes.get("nonfinite", 0) + 1
            return None
        return value
    if value is None or isinstance(value, str):
        return value
    return str(value)


def cut(summary, name, rows, limit=ROWS):
    """Keep at most ``limit`` rows under ``name`` and their full count under ``name_total``."""
    rows = list(rows)
    summary[name] = rows[:limit]
    summary[name + "_total"] = len(rows)


def faces(entry, field):
    values = entry.get(field)
    if isinstance(values, list) and len(values) > FACES:
        entry[field + "_total"] = len(values)
        entry[field] = values[:FACES]


def pick(report, names):
    return {name: report[name] for name in names if name in report}


# ------------------------------------------------------------------------------------------ inputs

def load_model(path):
    loader = native("model.model3doLoader")
    try:
        model, version = loader.load3do(path)
    except (ValueError, AssertionError, EOFError, OSError) as exc:
        raise ValueError(f"3DO reader: {exc}") from exc
    if not model.meshHierarchy or not model.geosets or not model.geosets[0].meshes:
        raise ValueError("No 3DO geometry/hierarchy found")
    info = {"format_version": getattr(version, "value", str(version)), "geosets": len(model.geosets),
            "hierarchy_nodes": len(model.meshHierarchy), "meshes": len(model.geosets[0].meshes)}
    return model, info


def load_keys(paths, *, strict=True):
    """KEYs by file name in the given order. A KEY that fails to load is listed, not fatal.

    As in ``sith.audit.articulation.loadKeyFolder``, any error of one file is reported with it.
    """
    loader = native("key.keyLoader")
    keys, skipped = {}, []
    for path in paths:
        path = Path(path)
        try:
            keys[path.name] = loader.loadKey(path, strictTracks=strict)
        except Exception as exc:  # one unreadable KEY is reported, not fatal
            skipped.append({"key": path.name, "error": f"{type(exc).__name__}: {exc}"[:256]})
    return keys, skipped


def require_keys(keys, skipped):
    if not keys:
        first = skipped[0]["error"] if skipped else "no KEY files"
        raise ValueError(f"No KEY could be loaded ({first})")


def reference_pose(request, model, geoset):
    """The reference pose the request names: the rest pose, or a KEY's positions with or without rotations."""
    articulation = native("audit.articulation")
    reference = request["parameters"]["reference"]
    if reference["pose"] == "rest":
        return articulation.referencePose(model, geoset=geoset)
    loader = native("key.keyLoader")
    path = request["inputs"]["reference"]
    try:
        key = loader.loadKey(path)
    except Exception as exc:  # the reference is required: report why it cannot be read
        raise ValueError(f"Reference KEY reader: {type(exc).__name__}: {exc}") from exc
    key.name = reference["name"]
    return articulation.referencePose(model, key, reference["frame"], zeroRotations=reference["zero_rotations"],
                                      geoset=geoset)


# ------------------------------------------------------------------------------------------ audits

def audit_budget(request, progress):
    budget = native("audit.budget")
    progress.update(stage="loading model")
    model, info = load_model(request["inputs"]["model"])
    progress.update(stage="auditing")
    report = budget.auditBudget(model, request["parameters"]["target"])
    # The warnings and issue_counts come from the complete report; only the per-geoset rows are cut.
    warnings = budget.budgetWarnings(report)
    summary = dict(report)
    rows = []
    for row in report["geosets"][:GEOSETS]:
        row = dict(row)
        cut(row, "mesh_counts", row["mesh_counts"])
        rows.append(row)
    summary["geosets"] = rows
    summary["geosets_total"] = len(report["geosets"])
    summary["warnings"] = warnings
    return {"model": info}, {"budget": {**report, "warnings": warnings}}, {"budget": summary}


def audit_segmentation(request, progress):
    segmentation = native("audit.segmentation")
    parameters = request["parameters"]
    progress.update(stage="loading model")
    model, info = load_model(request["inputs"]["model"])
    reference = reference_pose(request, model, parameters["geoset"])
    inputs = {"model": info, "reference": reference.description}
    poses = None
    if request["inputs"].get("keys"):
        progress.update(stage="loading KEYs")
        keys, skipped = load_keys(request["inputs"]["keys"])
        require_keys(keys, skipped)
        poses = segmentation.testPoses(list(keys.values()), step=parameters["step"])
        inputs["keys"] = {"loaded": len(keys), "skipped": skipped, "poses": len(poses)}
    progress.update(stage="auditing")
    report = segmentation.auditSegmentation(model, geoset=parameters["geoset"],
                                            reference=None if parameters["reference"]["pose"] == "rest" else reference,
                                            poses=poses, detail=parameters["detail"])
    summary = pick(report, ("status", "geoset", "reference", "reference_diagnostic", "parameters", "issue_counts", "severity_counts",
                            "findings_truncated", "poses"))
    summary["findings"] = []
    for finding in report["findings"]:
        finding = dict(finding, meshes=[dict(mesh) for mesh in finding.get("meshes", [])])
        for mesh in finding["meshes"]:
            faces(mesh, "faces")
        summary["findings"].append(finding)
    flagged = []
    for mesh in report["meshes"]:
        if mesh["high_faces"] or mesh["review_faces"]:
            mesh = dict(mesh)
            faces(mesh, "high_faces")
            faces(mesh, "review_faces")
            flagged.append(mesh)
    cut(summary, "meshes_flagged", flagged)
    summary["meshes_total"] = len(report["meshes"])
    cut(summary, "parts", report["parts"])
    cut(summary, "unplaced_meshes", report["unplaced_meshes"])
    cut(summary, "shared_meshes", report["shared_meshes"])
    return inputs, {"segmentation": report}, {"segmentation": summary}


def audit_articulation(request, progress):
    articulation = native("audit.articulation")
    parameters = request["parameters"]
    progress.update(stage="loading model")
    model, info = load_model(request["inputs"]["model"])
    progress.update(stage="loading KEYs")
    keys, skipped = load_keys(request["inputs"]["keys"])
    require_keys(keys, skipped)
    reference = reference_pose(request, model, parameters["geoset"])
    settings = articulation.AnchorSettings().scaled(parameters["scale"])
    joints = articulation.KYLE_JOINTS if parameters["joints"] == "kyle" else None
    done = {"current": None, "keys": 0, "poses": 0}

    def onPose(name, frame, perPose):
        if name != done["current"]:
            if done["current"] is not None:
                done["keys"] += 1
            done["current"] = name
            progress.update(keys_done=done["keys"], poses_done=done["poses"], current=name)
        done["poses"] += 1

    progress.update(stage="joint gaps", keys_total=len(keys))
    report = articulation.auditArticulation(model, keys, step=parameters["step"], joints=joints, reference=reference,
                                            settings=settings, geoset=parameters["geoset"], onPose=onPose)
    full = {"articulation": report}
    summary = pick(report, ("status", "issue_counts", "findings", "findings_truncated", "totals", "geometry",
                            "parameters", "reading", "reference_diagnostic"))
    cut(summary, "joints", report["joints"])
    daylight = sorted((row for row in report["keys"] if row["poses_with_daylight"]),
                      key=lambda row: (-row["lines"], row["name"].casefold(), row["name"]))
    cut(summary, "keys_with_daylight", daylight)
    summaries = {"articulation": summary}
    if parameters["seat_offsets"]:
        progress.update(stage="seat offsets", keys_done=len(keys), current=None)
        seats = articulation.seatOffsets(model, keys, reference=None if parameters["reference"]["pose"] == "rest"
                                         else reference)
        full["seat_offsets"] = seats
        seated = pick(seats, ("status", "issue_counts", "findings", "findings_truncated", "parameters"))
        cut(seated, "nodes", seats["nodes"])
        rows = []
        for row in seats["keys"]:
            if row["off_seat"]:
                top = row["off_seat"][0]
                rows.append({"name": row["name"], "tracks": row["tracks"], "nodes_off_seat": len(row["off_seat"]),
                             "largest": {"node": top["node"], "name": top["name"], "offset": top["offset"],
                                         "frame": top["frame"]}})
        rows.sort(key=lambda row: (-row["largest"]["offset"], row["name"].casefold(), row["name"]))
        cut(seated, "keys_off_seat", rows)
        seated["keys_total"] = len(seats["keys"])
        summaries["seat_offsets"] = seated
    inputs = {"model": info, "keys": {"loaded": len(keys), "skipped": skipped}, "reference": reference.description}
    return inputs, full, summaries


def audit_ground(request, progress):
    ground = native("audit.ground")
    parameters = request["parameters"]
    progress.update(stage="loading model")
    model, info = load_model(request["inputs"]["model"])
    progress.update(stage="loading KEYs")
    keys, skipped = load_keys(request["inputs"]["keys"])
    require_keys(keys, skipped)
    floor = parameters["floor"]
    crouchFloor = None
    if floor == "engine":
        level = ground.thingFloorHeight(model, parameters["move_size"], height=parameters["thing_height"])
        crouchFloor = ground.thingFloorHeight(model, parameters["move_size"], crouching=True)
    elif floor == "rest":
        level = None
    else:
        level = float(floor)
    crouch = set(parameters["crouch_keys"])
    travel = parameters["travel"]
    sequences = [ground.keySequence(key, parameters["step"], direction=travel.get(name),
                                    floor=crouchFloor if name in crouch else None, name=name)
                 for name, key in keys.items()]
    progress.update(stage="floor contact", keys_total=len(keys))
    report = ground.auditFloorContact(model, sequences, floor=level, contacts=parameters["contacts"],
                                      step=parameters["step"], geoset=parameters["geoset"])
    summary = pick(report, ("status", "floor", "floor_source", "contacts", "slip_nodes", "step", "tolerance",
                            "float_tolerance", "contact_band", "stance_band", "slip_tolerance", "samples",
                            "issue_counts", "findings", "findings_truncated"))
    cut(summary, "nodes", report["nodes"])
    rows = []
    for row in report["sequences"]:
        over = sum(entry["over_tolerance"] for entry in row["slip"])
        if row["below_samples"] or row["floating_samples"] or over:
            rows.append({**pick(row, ("name", "samples", "floor", "travel", "lowest_min", "lowest_min_time",
                                      "lowest_max", "lowest_max_time", "contact_min", "contact_max",
                                      "below_samples", "floating_samples")), "slip_over_tolerance": over})
    rows.sort(key=lambda row: (row["name"].casefold(), row["name"]))
    cut(summary, "sequences_flagged", rows)
    summary["sequences_total"] = len(report["sequences"])
    if crouchFloor is not None:
        summary["crouch_floor"] = crouchFloor
    inputs = {"model": info, "keys": {"loaded": len(keys), "skipped": skipped}}
    return inputs, {"ground": report}, {"ground": summary}


def _lint_findings(name, header, pops):
    """Findings of one KEY, each with its KEY and check, in the order header, binding, pops."""
    out = []
    for check, report in (("header", header), ("binding", (header or {}).get("binding")), ("pops", pops)):
        for finding in (report or {}).get("findings", []):
            out.append({"key": name, "check": check, **finding})
    return out


def audit_lint(request, progress):
    keylint = native("audit.keylint")
    loader = native("key.keyLoader")
    parameters = request["parameters"]
    model, info = None, None
    if request["inputs"].get("model"):
        progress.update(stage="loading model")
        model, info = load_model(request["inputs"]["model"])
    paths = [Path(path) for path in request["inputs"]["keys"]]
    progress.update(stage="KEY lint", keys_total=len(paths))
    rows, perKey, counts = [], {}, {}
    for done, path in enumerate(paths):
        progress.update(keys_done=done, current=path.name)
        row = {"key": path.name}
        try:
            key = loader.loadKey(path, strictTracks=False)
        except Exception as exc:  # one unreadable KEY is reported, not fatal
            error = f"{type(exc).__name__}: {exc}"[:256]
            row.update(status="unreadable", error=error)
            counts["unreadable_key"] = counts.get("unreadable_key", 0) + 1
            rows.append((row, [{"key": path.name, "check": "load", "code": "unreadable_key", "detail": error}]))
            perKey[path.name] = {"error": error}
            continue
        header = keylint.lintKeyHeader(key, model, target=parameters["target"])
        pops = None
        if parameters["pops"]:
            try:
                pops = keylint.scanKeyPops(key, model)
            except ValueError as exc:
                pops = {"status": "not_scanned", "reason": str(exc)[:256], "issue_counts": {}, "findings": []}
        statuses = [header["status"]] + ([pops["status"]] if pops and pops["status"] != "not_scanned" else [])
        status = min(statuses, key=lambda value: STATUS_ORDER.get(value, 1))
        if pops and pops["status"] == "not_scanned" and STATUS_ORDER.get(status, 1) > 1:
            status = "review"
        keyCounts = dict(header["issue_counts"])
        for report in (header.get("binding"), pops):
            for code, total in ((report or {}).get("issue_counts") or {}).items():
                keyCounts[code] = keyCounts.get(code, 0) + total
        if pops and pops["status"] == "not_scanned":
            keyCounts["pops_not_scanned"] = 1
        for code, total in keyCounts.items():
            counts[code] = counts.get(code, 0) + total
        row.update(status=status, header=header["status"], pops=None if pops is None else pops["status"],
                   joints=header["declared_joints"], issue_counts=keyCounts)
        rows.append((row, _lint_findings(path.name, header, pops)))
        perKey[path.name] = {"header": header, "pops": pops}
    progress.update(keys_done=len(paths), current=None)

    # Worst KEYs first; at most 4 findings per KEY, then the remaining slots in the same order.
    order = sorted(range(len(rows)), key=lambda i: (STATUS_ORDER.get(rows[i][0]["status"], 1), i))
    findings, taken = [], [0] * len(rows)
    for share in (4, None):
        for i in order:
            found = rows[i][1]
            stop = len(found) if share is None else min(len(found), share)
            while taken[i] < stop and len(findings) < 32:
                findings.append(found[taken[i]])
                taken[i] += 1
    allFindings = sum(len(found) for _, found in rows)
    statuses = [row["status"] for row, _ in rows]
    status = ("incompatible" if "incompatible" in statuses else
              "review" if any(value in ("review", "unreadable") for value in statuses) else
              "no_tracks" if statuses and all(value == "no_tracks" for value in statuses) else "consistent")
    summary = {"status": status, "issue_counts": counts, "findings": findings,
               "findings_truncated": allFindings > len(findings),
               "totals": {"keys": len(rows), **{value: statuses.count(value) for value in
                          ("incompatible", "review", "consistent", "no_tracks", "unreadable")}},
               "recommendations": {"joints_64": sum(1 for row, _ in rows
                                                    if row.get("joints") not in (None, keylint.RECOMMENDED_JOINTS))},
               "parameters": {"target": parameters["target"], "pops": parameters["pops"],
                              "limits": asdict(keylint.PopLimits()) if parameters["pops"] else None}}
    flagged = [rows[i][0] for i in order if rows[i][0]["status"] != "consistent"]
    cut(summary, "keys_flagged", flagged)
    inputs = {"keys": {"loaded": len(rows) - statuses.count("unreadable"), "unreadable": statuses.count("unreadable")}}
    if info is not None:
        inputs["model"] = info
    full = {"lint": {**{name: summary[name] for name in ("status", "issue_counts", "totals", "recommendations",
                                                          "parameters")},
                     "keys": [row for row, _ in rows], "reports": perKey}}
    return inputs, full, {"lint": summary}


def audit_track_slots(request, progress):
    keylint = native("audit.keylint")
    parameters = request["parameters"]
    characters, inputs = [], {"characters": []}
    progress.update(stage="loading characters", keys_total=len(request["inputs"]["characters"]))
    for done, item in enumerate(request["inputs"]["characters"]):
        progress.update(keys_done=done, current=item["name"])
        model, info = load_model(item["model"])
        keys, skipped = load_keys(item["keys"])
        require_keys(keys, skipped)
        characters.append(keylint.Character(item["name"], model, list(keys.values())))
        inputs["characters"].append({"name": item["name"], "model": info,
                                     "keys": {"loaded": len(keys), "skipped": skipped}})
    progress.update(stage="track slots", keys_done=len(characters), current=None)
    custom = parameters["custom"]
    report = keylint.trackRegistry(characters, custom=custom if not isinstance(custom, list) else set(custom))
    summary = pick(report, ("status", "custom_rule", "issue_counts", "findings", "findings_truncated"))
    summary["characters"] = []
    for row in report["characters"]:
        row = dict(row)
        cut(row, "slots", row["slots"])
        summary["characters"].append(row)
    return inputs, {"track_slots": report}, {"track_slots": summary}


RESEAT_ORDER = {"unreadable": 0, "fail": 0, "skipped": 1, "review": 1, "reseated": 2, "unchanged": 3}


def audit_reseat(request, progress):
    """Reseat the snapshot KEYs with ``reseatKeyFiles``; the changed ones are written into ``reseated``."""
    reseat = native("key.keyReseat")
    loader = native("key.keyLoader")
    parameters = request["parameters"]
    progress.update(stage="loading model")
    model, info = load_model(request["inputs"]["model"])
    stand = None
    if parameters["stand"] is not None:
        # reseatKeyFiles names the seat source after the stand KEY's file name, in its report and in
        # the header comment of every KEY it writes; the snapshot is called reference.key.
        name = Path(parameters["stand"]["name"]).name
        if not name.lower().endswith(".key"):
            raise ValueError("The stand KEY needs a .key file name")
        stand = Path(request["result"]).with_name("stand") / name
        stand.parent.mkdir()
        stand.write_bytes(Path(request["inputs"]["reference"]).read_bytes())
        try:
            loader.loadKey(stand)
        except Exception as exc:  # the stand KEY is required: report why it cannot be read
            raise ValueError(f"Stand KEY reader: {type(exc).__name__}: {exc}") from exc
    paths = request["inputs"]["keys"]
    progress.update(stage="reseating", keys_total=len(paths))
    report = reseat.reseatKeyFiles(model, paths, Path(request["result"]).with_name("reseated"), standKeyPath=stand,
                                   standFrame=parameters["stand"]["frame"] if stand is not None else 0.0,
                                   waistTuck=parameters["waist_tuck"], waistLift=parameters["waist_lift"],
                                   check=parameters["check"])
    progress.update(keys_done=len(paths))

    # One row per KEY, failed checks and unreadable KEYs first. Unreadable KEYs are not in per_key.
    rows = [{"key": name, "status": entry["status"], "hip_rule": entry["hip_rule"], "check": entry.get("check"),
             "written": "written" in entry, "tracks": entry["tracks"]} for name, entry in report["per_key"].items()]
    rows += [{"key": Path(path).name, "status": "unreadable", "hip_rule": None, "check": None, "written": False,
              "tracks": {}} for path in paths if Path(path).name not in report["per_key"]]
    rows.sort(key=lambda row: (0 if row["check"] == "fail" else RESEAT_ORDER.get(row["status"], 1),
                               row["key"].casefold(), row["key"]))
    summary = pick(report, ("status", "seats", "keys", "unchanged_keys", "check", "issue_counts", "findings",
                            "findings_truncated"))
    cut(summary, "changed_keys", report["changed_keys"])
    cut(summary, "per_key", rows)
    outputs = []
    for path in map(Path, report["written"]):
        data = path.read_bytes()
        outputs.append({"name": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    inputs = {"model": info, "keys": {"count": len(paths), "unreadable": len(paths) - len(report["per_key"])},
              "seats": report["seats"]}
    # "outputs" goes to the top level of result.json, where the server looks for written files.
    return inputs, {"reseat": report}, {"reseat": summary, "outputs": outputs}


AUDITS = {"budget": audit_budget, "segmentation": audit_segmentation, "articulation": audit_articulation,
          "ground": audit_ground, "lint": audit_lint, "track_slots": audit_track_slots, "reseat": audit_reseat}


def run(request):
    if request.get("bpy_site"):
        sys.path.insert(0, request["bpy_site"])
    kind = request["kind"]
    if kind not in AUDITS:
        raise ValueError(f"Unknown audit {kind!r}")
    progress = Progress(request["progress"], kind)
    progress.update(stage="loading add-on")
    load_addon(request["addon"])
    started = time.monotonic()
    inputs, full, summaries = AUDITS[kind](request, progress)
    elapsed = round(time.monotonic() - started, 3)
    notes = {}
    common = {"schema": SCHEMA, "kind": kind, "runtime": runtime(), "audit_seconds": elapsed,
              "parameters": request["parameters"]}
    report = plain({**common, "inputs": inputs, **full}, notes)
    Path(request["report"]).write_text(json.dumps(report, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    result = plain({**common, "inputs": inputs, **summaries,
                    "limits": {"rows": ROWS, "face_indices": FACES, "findings": 32, "geosets": GEOSETS},
                    "full_report": Path(request["report"]).name}, notes)
    if notes:
        result["nonfinite_values_as_null"] = notes.get("nonfinite", 0)
    progress.update(stage="done")
    Path(request["result"]).write_text(json.dumps(result, indent=1, allow_nan=False) + "\n", encoding="utf-8")


def main():
    sys.dont_write_bytecode = True
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    try:
        run(request)
    except (ValueError, RuntimeError, ImportError) as exc:
        detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else f"{type(exc).__name__}: {exc}"
        Path(request["result"]).with_name("diagnostic.json").write_text(
            json.dumps({"detail": detail[:512]}), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
