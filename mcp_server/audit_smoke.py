#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the audit tools and reseat_keys through the official stdio client, with synthetic inputs only."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
R, D, H, O = 0.01, 0.012, 0.02, 0.001   # two boxes (JK units) that overlap by 2 * O at their seat


def box(lo, hi):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
               (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    return corners, [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def model_text(meshes, nodes):
    """meshes: [(name, vertices, polygons)]; nodes: [(name, parent, mesh)] at the origin -> 3DO 2.1 text."""
    lines = ["# Synthetic audit smoke model", "SECTION: HEADER", "3DO 2.1", "SECTION: MODELRESOURCE", "MATERIALS 0",
             "SECTION: GEOMETRYDEF", "RADIUS 1", "INSERT OFFSET 0 0 0", "GEOSETS 1", "GEOSET 0", f"MESHES {len(meshes)}"]
    for m, (name, vertices, polygons) in enumerate(meshes):
        lines += [f"MESH {m}", f"NAME {name}", "RADIUS 1", "GEOMETRYMODE 4", "LIGHTINGMODE 3", "TEXTUREMODE 3",
                  f"VERTICES {len(vertices)}", *(f"{i}: {x!r} {y!r} {z!r} 0" for i, (x, y, z) in enumerate(vertices)),
                  "TEXTURE VERTICES 1", "0: 0 0", "VERTEX NORMALS", *(f"{i}: 0 0 1" for i in range(len(vertices))),
                  f"FACES {len(polygons)}",
                  *(f"{i}: -1 0 4 3 3 0 {len(p)} " + " ".join(f"{v},0" for v in p) for i, p in enumerate(polygons)),
                  "FACE NORMALS", *(f"{i}: 0 0 1" for i in range(len(polygons)))]
    lines += ["SECTION: HIERARCHYDEF", f"HIERARCHY NODES {len(nodes)}"]
    for i, (name, parent, mesh) in enumerate(nodes):
        children = [j for j, node in enumerate(nodes) if node[1] == i]
        siblings = [j for j, node in enumerate(nodes) if node[1] == parent and j > i]
        lines.append(f"{i}: 0x0 0x0 {mesh} {parent} {children[0] if children else -1} "
                     f"{siblings[0] if siblings else -1} {len(children)} 0 0 0 0 0 0 0 0 0 {name}")
    return "\n".join(lines) + "\n"


def key_text(tracks, joints=2, frames=10):
    """tracks: [(node, [(frame, (x, y, z))])] -> text KEY with position-only entries."""
    lines = ["# Synthetic audit smoke KEY", "SECTION: HEADER", "FLAGS 0x0", "TYPE 0x0", f"FRAMES {frames}", "FPS 15",
             f"JOINTS {joints}", "SECTION: KEYFRAME NODES", f"NODES {len(tracks)}"]
    for node, entries in tracks:
        lines += [f"NODE {node}", f"MESH NAME n{node}", f"ENTRIES {len(entries)}",
                  *(f"{k}: {f!r} 0x0 {x!r} {y!r} {z!r} 0 0 0\n   0 0 0 0 0 0" for k, (f, (x, y, z)) in enumerate(entries))]
    return "\n".join(lines) + "\n"


def lift(height):
    return key_text([(0, [(0, (0, 0, 0))]), (1, [(0, (0, 0, 0)), (2, (0, 0, height))])])


# Kyle's layout, which reseat_keys needs: (name, parent).
KYLE = [("$$$dummy", -1), ("$$$dummy", 0), ("k_hip", 0), ("k_torso", 1), ("k_lthigh", 2), ("k_rthigh", 2),
        ("k_lshouldr", 3), ("k_lforearm", 6), ("k_lhand", 7), ("k_rshouldr", 3), ("k_rforearm", 9), ("k_rhand", 10),
        ("k_neck", 3), ("k_head", 12)]


def kyle_key(drop, frames):
    """Kyle's joints on their seats, except that the hip sits ``drop`` low and the thighs push up into it by as much."""
    return key_text([(i, [(0, (0, 0, -drop if i == 2 else drop if i in (4, 5) else 0))]) for i in range(len(KYLE))],
                    joints=len(KYLE), frames=frames)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def check(config_path, evidence):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    inputs, keys, other = evidence / "inputs", evidence / "inputs" / "key", evidence / "inputs" / "other"
    for folder in (keys, other):
        folder.mkdir(parents=True)
    model = inputs / "boxes.3do"
    model.write_text(model_text([("lower", *box((-R, -D, -H), (R, D, O))), ("upper", *box((-R, -D, -O), (R, D, H)))],
                                [("hip", -1, 0), ("torso", 0, 1)]), encoding="utf-8")
    size = 25
    grid = inputs / "grid.3do"
    grid.write_text(model_text([("grid", [(0.01 * x, 0.01 * y, 0.0) for y in range(size) for x in range(size)],
                                 [(r * size + c, r * size + c + 1, (r + 1) * size + c + 1, (r + 1) * size + c)
                                  for r in range(size - 1) for c in range(size - 1)])], [("root", -1, 0)]),
                    encoding="utf-8")
    files = {"lift.key": lift(0.004), "still.key": lift(0.0),
             "stand.key": key_text([(0, [(0, (0, 0, 0))]), (1, [(0, (0, 0, 0))])]),
             "sink.key": key_text([(0, [(0, (0, 0, -0.01))]), (1, [(0, (0, 0, 0))])]),
             # Track 2 lies past JOINTS 2: refused by the strict loader, read by the engine and the lint.
             "past.key": key_text([(0, [(0, (0, 0, 0))]), (2, [(0, (0, 0, 0))])])}
    for name, text in files.items():
        (keys / name).write_text(text, encoding="utf-8")
    (other / "lift.key").write_text(lift(0.002), encoding="utf-8")
    kyle, kyle_keys = inputs / "kyle.3do", inputs / "kyle_key"
    kyle_keys.mkdir()
    kyle.write_text(model_text([("lower", *box((-R, -D, -H), (R, D, O)))],
                               [(name, parent, 0 if i == 0 else -1) for i, (name, parent) in enumerate(KYLE)]),
                    encoding="utf-8")
    (kyle_keys / "kystand1.key").write_text(kyle_key(0.0, 1), encoding="utf-8")
    (kyle_keys / "saber.key").write_text(kyle_key(0.004, 4), encoding="utf-8")
    # This disposable config authorizes only the synthetic inputs created here.
    config.update(read_roots=[str(inputs)], job_root=str(evidence / "jobs"))
    scoped = evidence / "config.json"
    scoped.write_text(json.dumps(config), encoding="utf-8")
    params = StdioServerParameters(command=sys.executable,
                                   args=["-B", str(ROOT / "mcp_server/server.py"), "--config", str(scoped)])
    stand = str(keys / "stand.key")
    cases = [
        ("budget", "audit_budget", {"path": str(model)}, {"budget": "within_limits"}),
        ("budget_over", "audit_budget", {"path": str(grid), "target": "MOTS"}, {"budget": "over_limits"}),
        ("segmentation", "audit_segmentation", {"model_path": str(model), "reference_key": stand,
                                                "zero_rotations": True}, {"segmentation": None}),
        ("articulation", "audit_articulation", {"model_path": str(model), "key_dir": str(keys), "step": 1.0,
                                                "reference_key": stand, "zero_rotations": True},
         {"articulation": "review", "seat_offsets": "off_seat"}),
        ("ground", "audit_ground", {"model_path": str(model), "key_dir": str(keys), "keys": ["sink.key", "still.key"],
                                    "floor": "rest"}, {"ground": "review"}),
        ("lint", "lint_keys", {"key_dir": str(keys), "model_path": str(model)}, {"lint": "incompatible"}),
        ("track_slots", "audit_track_slots", {"characters": [
            {"name": "First", "model_path": str(model), "key_dir": str(keys), "keys": ["lift.key"]},
            {"name": "Second", "model_path": str(model), "key_dir": str(other)}], "custom": 0},
         {"track_slots": "incompatible"}),
        ("reseat", "reseat_keys", {"model_path": str(kyle), "key_dir": str(kyle_keys),
                                   "stand_key": str(kyle_keys / "kystand1.key")}, {"reseat": "reseated"}),
    ]
    summary, failures = {}, []
    async with Client(params) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        missing = {"audit_budget", "audit_segmentation", "audit_articulation", "audit_ground", "lint_keys",
                   "audit_track_slots", "reseat_keys"} - tools
        if missing:
            raise AssertionError(f"Missing audit tools: {sorted(missing)}")
        for label, tool, arguments, expected in cases:
            call = await client.call_tool(tool, arguments)
            data = call.structured_content or {}
            if call.is_error or data.get("status") != "passed":
                failures.append(f"{label} job failed: {data.get('diagnostic') or data.get('job_directory') or call.content}")
                summary[label] = {"status": "job_failed"}
                continue
            audit = data["audit"]
            statuses = {section: audit[section]["status"] for section in expected}
            listing = data["inputs"].get("keys", {})
            # Only reseat_keys writes files: the changed KEY, in its job's reseated folder.
            outputs = data.get("outputs", [])
            if (any(want is not None and statuses[section] != want for section, want in expected.items())
                    or not data["source_unchanged"]
                    or sha256(data["report"]["path"]) != data["report"]["sha256"]
                    or any(item["sha256"] != sha256(Path(listing["folder"]) / item["name"])
                           for item in listing.get("files", []))
                    or ("model" in data["inputs"] and data["inputs"]["model"]["sha256"] != sha256(data["inputs"]["model"]["source"]))
                    or [item["name"] for item in outputs] != (["saber.key"] if tool == "reseat_keys" else [])
                    or any(Path(item["path"]).parent != Path(data["job_directory"]) / "reseated"
                           or sha256(item["path"]) != item["sha256"] for item in outputs)):
                failures.append(f"{label}: unexpected result {statuses}")
            summary[label] = {"statuses": statuses, "job": data["job_id"], "outputs": [item["name"] for item in outputs]}
        denials = {
            "outside_key_folder": ("lint_keys", {"key_dir": str(evidence)}),
            "reference_without_rotation_rule": ("audit_articulation", {"model_path": str(model), "key_dir": str(keys),
                                                                       "reference_key": stand}),
            "path_in_key_subset": ("lint_keys", {"key_dir": str(inputs), "keys": ["key/lift.key"]}),
            "reseat_without_seats": ("reseat_keys", {"model_path": str(kyle), "key_dir": str(kyle_keys)}),
        }
        for label, (tool, arguments) in denials.items():
            denied = await client.call_tool(tool, arguments)
            if not denied.is_error:
                failures.append(f"{label} was accepted")
            summary[label] = "rejected" if denied.is_error else "accepted"
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (evidence / "checks.json").write_text(json.dumps({"cases": len(cases) + len(denials), "failed": len(failures),
                                                      "failures": failures}, indent=2) + "\n", encoding="utf-8")
    for name, result in summary.items():
        print(name, result, flush=True)
    if failures:
        raise AssertionError("\n".join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Local config specifying runtime and matching add-on")
    parser.add_argument("--evidence", type=Path, required=True, help="New external directory for fixtures and jobs")
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    if any((p / ".git").exists() for p in (evidence, *evidence.parents)):
        parser.error("Choose an evidence directory outside Git")
    evidence.mkdir(parents=True, exist_ok=False)
    asyncio.run(asyncio.wait_for(check(args.config, evidence), timeout=600))


if __name__ == "__main__":
    main()
