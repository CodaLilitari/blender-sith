# SPDX-License-Identifier: GPL-3.0-or-later
"""Check whether an animation track gained control of body geometry in another 3DO.

Run python -m sith.audit.slotownership --help for the asset-level command.
"""
import argparse
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
from tempfile import TemporaryDirectory

import numpy as np

from .pose import keyTrack, rotationMatrix
from ..key.keyLoader import loadKey
from ..model.model3doLoader import load3do


def _subtrees(model):
    """One inclusive descendant set per numerical slot. Validate the parent chains."""
    nodes = model.meshHierarchy
    if not nodes or any(n.idx != i for i, n in enumerate(nodes)):
        raise ValueError("Expected a nonempty, contiguous hierarchy in numerical slot order")
    branches = [set() for _ in nodes]
    for node in nodes:
        index = node.idx
        while index != -1:
            if not 0 <= index < len(nodes) or node.idx in branches[index]:
                raise ValueError(f"Invalid parent chain at slot {node.idx}")
            branches[index].add(node.idx)
            index = nodes[index].parentIdx
    return branches


def _at_bind(track, node):
    """A stationary, off-bind entry is enough to move the entire branch."""
    for row in track.keyframes:
        if not np.allclose(row.position, node.position, rtol=0, atol=1e-8):
            return False
        if not np.allclose(rotationMatrix(row.orientation), rotationMatrix(node.rotation),
                           rtol=0, atol=1e-8):
            return False
        if int(row.flags) & 1 and any(row.deltaPosition):
            return False
        if int(row.flags) & 2 and any(row.deltaRotation):
            return False
    return True


def auditSlotOwnership(reference, model, keys, bodySlots=(), bodyNodes=()):
    """Compare numerical track ownership. keys maps labels to parsed, effective KEYs.

    Reference mesh-bearing slots are protected when they still lead to geometry on the
    target. bodySlots or unique, case-insensitive bodyNodes can declare additional
    target body geometry on nonstandard slots. Names resolve independently on each model.
    A mesh-bearing extra branch without a known body joint is reported for inspection.
    """
    if not keys:
        raise ValueError('Supply at least one effective KEY')
    source = _subtrees(reference)
    target = _subtrees(model)
    ref_nodes, nodes = reference.meshHierarchy, model.meshHierarchy
    visible = {n.idx for n in nodes if n.meshIdx >= 0}
    source_visible = {n.idx for n in ref_nodes if n.meshIdx >= 0}
    body = {i for i in source_visible if i < len(nodes) and target[i] & visible}
    extra_body = set(bodySlots)
    for name in bodyNodes:
        matches = [n.idx for n in nodes if n.name.casefold() == name.casefold()]
        if len(matches) != 1:
            reason = 'Missing' if not matches else 'Ambiguous'
            raise ValueError(f"{reason} target body node {name!r} (matched slots {matches})")
        extra_body.add(matches[0])
    for i in extra_body:
        if not 0 <= i < len(nodes) or not target[i] & visible:
            raise ValueError(f"Body slot {i} is outside the target or has no mesh in its branch")
    findings = []
    for name, key in sorted(keys.items()):
        for slot in sorted({n.idx for n in key.nodes}):
            track = keyTrack(key, slot)
            if track is None or slot >= len(nodes):
                continue
            actual = target[slot]
            expected = source[slot] if slot < len(source) else set()
            affected = (actual & body) - expected
            attachment = not expected & source_visible and bool(actual & visible)
            if attachment:
                affected.update(actual & extra_body)
            if not (affected or attachment):
                continue
            node = nodes[slot]
            if _at_bind(track, node):
                continue
            findings.append({
                "key": name, "slot": slot, "key_label": track.meshName, "target_node": node.name,
                "classification": "foreign_body_motion" if affected else "review_attachment",
                "new_body_slots": sorted(affected), "visible_branch_slots": sorted(actual & visible),
            })
    return {"keys_checked": len(keys), "reference_nodes": len(source), "target_nodes": len(target),
            "declared_body_slots": [{"slot": i, "name": nodes[i].name} for i in sorted(extra_body)],
            "status": ("conflict" if any(f["classification"] == "foreign_body_motion" for f in findings)
                       else "review" if findings else "clear"), "findings": findings}


def puppetKeyUses(text):
    """List authored action rows from every PUP mode, including the second fidget.

    Auditing every mode includes inherited rows at their definition. Preserve repeated uses.
    """
    uses = {}
    mode = None
    for row in text.splitlines():
        line = row.partition("#")[0].strip()
        header = re.match(r"^mode\s*=\s*(\d+)\b", line, re.I)
        if header:
            mode = int(header[1])
            continue
        fields = line.split()
        if len(fields) < 2 or not fields[1].lower().endswith(".key"):
            continue
        if mode is None:
            raise ValueError(f"Animation row before a PUP mode: {line}")
        path = fields[1].replace("\\", "/").casefold()
        if path.startswith("/") or ":" in path or any(s in ("", ".", "..") for s in path.split("/")):
            raise ValueError(f"Expected a relative KEY resource name: {fields[1]}")
        uses.setdefault(path, []).append({"mode": mode, "action": fields[0]})
    if not uses:
        raise ValueError("PUP contains no explicit .key action rows")
    return uses


def _resolve_key(directory, resource):
    path = directory
    for part in resource.split("/"):
        matches = [p for p in path.iterdir() if p.name.casefold() == part]
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous PUP resource {resource} inside {directory}")
        path = matches[0]
    if not path.is_file():
        raise ValueError(f"PUP resource is not a file: {resource}")
    return path


def _receipt(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _target_model(path, member):
    if member is None:
        if path.suffix.casefold() == ".gob":
            raise ValueError("Use --model-member with the complete internal 3DO path")
        return load3do(path)[0], _receipt(path)

    wanted = member.replace("\\", "/").casefold()
    if not wanted.endswith(".3do") or ":" in wanted or any(p in ("", ".", "..") for p in wanted.split("/")):
        raise ValueError(f"Expected a relative 3DO resource path: {member}")
    entry = struct.Struct("<II128s")
    with path.open("rb") as gob:
        size = gob.seek(0, 2)
        gob.seek(0)
        header = gob.read(12)
        if len(header) != 12 or header[:8] != b"GOB \x14\x00\x00\x00":
            raise ValueError("Expected a JK/MotS version 20 GOB archive")
        directory, = struct.unpack_from("<I", header, 8)
        if not 12 <= directory <= size - 4:
            raise ValueError("Invalid GOB directory offset")
        gob.seek(directory)
        count, = struct.unpack("<I", gob.read(4))
        directory_end = directory + 4 + count * entry.size
        if directory_end > size:
            raise ValueError("Truncated GOB directory")
        selected = None
        for _ in range(count):
            offset, length, name = entry.unpack(gob.read(entry.size))
            name = name.partition(b"\0")[0].decode("latin1").replace("\\", "/")
            if name.casefold() == wanted:
                if selected is not None:
                    raise ValueError(f"Ambiguous GOB member: {member}")
                if (offset < 12 or offset + length > size or
                        (offset < directory_end and offset + length > directory)):
                    raise ValueError(f"Invalid byte range for GOB member: {member}")
                selected = offset, length, name
        if selected is None:
            raise ValueError(f"Missing exact GOB member: {member}")
        offset, length, name = selected
        gob.seek(offset)
        data = gob.read(length)
        gob.seek(0)
        archive_hash = hashlib.sha256()
        for chunk in iter(lambda: gob.read(1024 * 1024), b""):
            archive_hash.update(chunk)
    with TemporaryDirectory() as tmp:
        # A fixed private basename keeps archive-supplied paths off the filesystem.
        temp_model = Path(tmp) / "member.3do"
        temp_model.write_bytes(data)
        parsed = load3do(temp_model)[0]
    return parsed, {"path": name, "sha256": hashlib.sha256(data).hexdigest(),
                    "archive": {"path": str(path), "sha256": archive_hash.hexdigest()}}


def auditFiles(reference, model, pup=None, keyDir=None, directKeys=(), bodySlots=(), modelMember=None,
               bodyNodes=()):
    """Audit an exact loose or packed model and return its provenance and PUP action names."""
    if (pup is None) != (keyDir is None):
        raise ValueError("Use --pup and --key-dir together")
    uses = {}
    files = {}
    if pup is not None:
        uses = puppetKeyUses(pup.read_text(encoding="latin1"))
        files = {name: _resolve_key(keyDir, name) for name in uses}
    for path in directKeys:
        name = path.name.casefold()
        if name in files and path.resolve() != files[name].resolve():
            raise ValueError(f"Two different paths supplied for {name}")
        files[name] = path
    if not files:
        raise ValueError("Supply a PUP with its effective KEY directory, or at least one direct KEY")
    ref_model = load3do(reference)[0]
    target_model, target_receipt = _target_model(model, modelMember)
    report = auditSlotOwnership(ref_model, target_model, {n: loadKey(p) for n, p in files.items()},
                                bodySlots, bodyNodes)
    report.update(reference=_receipt(reference), model=target_receipt, pup=_receipt(pup) if pup else None,
                  keys={n: dict(**_receipt(p), uses=uses.get(n, [])) for n, p in sorted(files.items())})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="3DO the active KEYs were authored against")
    parser.add_argument("--model", type=Path, required=True, help="exact 3DO or JK/MotS GOB used in this role")
    parser.add_argument("--model-member", help="complete internal 3DO path when --model is a GOB")
    parser.add_argument("--pup", type=Path, help="effective gameplay puppet, with every authored mode")
    parser.add_argument("--key-dir", type=Path, help="effective KEY directory after package precedence")
    parser.add_argument("--key", type=Path, action="append", default=[], help="also check a direct-action KEY (repeatable)")
    parser.add_argument("--body-slot", type=int, action="append", default=[], help="also protect this target body joint")
    parser.add_argument("--body-node", action="append", default=[], metavar="NAME",
                        help="protect a unique target body node by name (case-insensitive, repeatable)")
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args(argv)
    try:
        # Some native readers report import warnings on stdout; keep the JSON channel clean.
        with redirect_stdout(sys.stderr):
            report = auditFiles(args.reference, args.model, args.pup, args.key_dir, args.key, args.body_slot,
                                args.model_member, args.body_node)
        result = json.dumps(report, indent=2) + "\n"
        if args.output:
            if args.output.exists():
                raise ValueError(f"Choose a new output file: {args.output}")
            with args.output.open("x", encoding="utf-8") as f:
                f.write(result)
        else:
            sys.stdout.write(result)
    except (OSError, ValueError, AssertionError, EOFError) as exc:
        parser.error(str(exc))
    return int(report["status"] == "conflict")


if __name__ == "__main__":
    raise SystemExit(main())
