#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicitly configured, local stdio MCP tools for Blender-Sith."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
from typing import Any
import uuid

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.3.0"
MAX_INPUT = 64 * 1024 * 1024
MAX_LOG = 1024 * 1024
MAX_REPORT = 256 * 1024
# KEY folders: regular .key files directly in the folder, per folder and job.
MAX_KEY_FILES = 512
MAX_KEY_BYTES = 64 * 1024 * 1024
# Everything one audit job snapshots (models, KEYs and reference KEY together).
MAX_JOB_INPUT = 256 * 1024 * 1024
MAX_CHARACTERS = 8
STOCK_MOVE_SIZE = 0.065   # the stock walkplayer template's movesize, which sets the engine floor
AUDIT_SCHEMA = "blender-sith-mcp-audit-v1"
# Accepted report status values, by result section.
AUDIT_STATUSES = {
    "budget": {"within_limits", "over_limits", "counts_only"},
    "segmentation": {"clean", "review", "suspect"},
    "articulation": {"clean", "review", "no_joints"},
    "seat_offsets": {"on_seat", "off_seat", "no_tracks"},
    "ground": {"clear", "review", "empty"},
    "lint": {"consistent", "review", "incompatible", "no_tracks"},
    "track_slots": {"consistent", "review", "incompatible"},
    "reseat": {"fail", "review", "reseated", "unchanged"},
}
AUDIT_TOOLS = ["audit_budget", "audit_segmentation", "audit_articulation", "audit_ground", "lint_keys",
               "audit_track_slots", "reseat_keys"]


def load_helper(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_helper("sith_mcp_runner", ROOT / "scripts" / "run_regression.py")


def store_blender_alias(path, info):
    local = os.environ.get("LOCALAPPDATA")
    return (os.name == "nt" and local
            and path.parent == Path(local) / "Microsoft" / "WindowsApps"
            and path.name.lower() in {"blender.exe", "blender-launcher.exe"}
            and getattr(info, "st_reparse_tag", None) == 0x8000001B)  # IO_REPARSE_TAG_APPEXECLINK


def checked_path(value, *, directory=False, exists=True, blender_alias=False):
    """Reject relative paths, special files, and every link/junction in a route."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Expected an absolute local path")
    supplied = Path(value)
    if not supplied.is_absolute() or ".." in supplied.parts:
        raise ValueError("Paths must be absolute and contain no '..' components")
    path = Path(os.path.abspath(supplied))
    # On Windows reject device/UNC namespaces and alternate data streams.
    if os.name == "nt" and (str(path).startswith("\\\\") or ":" in str(path)[2:]):
        raise ValueError("UNC/device paths and alternate data streams are unsupported")
    is_alias = False
    for part in (path, *path.parents):
        if os.path.lexists(part):
            info = part.lstat()
            if blender_alias and part == path and store_blender_alias(path, info):
                is_alias = True
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("Symlink/junction routes are forbidden")
    if exists:
        if directory and not path.is_dir():
            raise ValueError("Expected an existing directory")
        if not directory and not is_alias and not path.is_file():
            raise ValueError("Expected an existing regular file")
    return path if is_alias else path.resolve()


def bounded_json(path):
    if not path.is_file() or path.stat().st_size > MAX_REPORT:
        raise ValueError("Missing or oversized child report")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Child report must be an object")
    return value


def number(name, value, low, high):
    """A finite int or float (not bool) within [low, high]."""
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a finite number from {low:g} to {high:g}")
    return float(value)


def whole(name, value, low, high):
    """An int (not bool) within [low, high]."""
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be a whole number from {low} to {high}")
    return value


def choice(name, value, choices):
    if value not in choices:
        raise ValueError(f"{name} must be one of: {', '.join(map(str, choices))}")
    return value


def flag(name, value):
    if type(value) is not bool:
        raise ValueError(f"{name} must be true or false")
    return value


def file_names(name, values):
    """Plain .key file names, each once: no folders, links or wildcards."""
    if values is None:
        return None
    if not isinstance(values, list) or not values or len(values) > MAX_KEY_FILES:
        raise ValueError(f"{name} must list 1 to {MAX_KEY_FILES} KEY file names")
    seen = set()
    for value in values:
        if (not isinstance(value, str) or not value.lower().endswith(".key") or "\x00" in value
                or any(mark in value for mark in ("/", "\\", ":")) or value in (".", "..")):
            raise ValueError(f"{name} must hold plain .key file names, not paths: {value!r}")
        if value.casefold() in seen:
            raise ValueError(f"{name} names {value!r} twice")
        seen.add(value.casefold())
    return list(values)


class Service:
    def __init__(self, config):
        allowed = {"addon", "blender", "python", "bpy_site", "read_roots", "job_root", "timeout", "threads"}
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValueError("Unknown configuration fields")
        if bool(config.get("blender")) == bool(config.get("python")):
            raise ValueError("Configure exactly one of blender or python")
        self.addon = checked_path(config.get("addon", str(ROOT / "sith")), directory=True)
        checked_path(str(self.addon / "__init__.py"))
        self.route = "blender" if config.get("blender") else "python"
        self.executable = checked_path(config[self.route], blender_alias=self.route == "blender")
        self.bpy_site = None
        if config.get("bpy_site"):
            if self.route != "python":
                raise ValueError("bpy_site requires the python route")
            self.bpy_site = checked_path(config["bpy_site"], directory=True)
        roots = config.get("read_roots")
        if not isinstance(roots, list) or not roots:
            raise ValueError("Configure at least one explicit read root")
        self.read_roots = tuple(checked_path(p, directory=True) for p in roots)
        self.job_root = checked_path(config.get("job_root"), directory=True, exists=False)
        for candidate in (self.job_root, *self.job_root.parents):
            if os.path.lexists(candidate / ".git"):
                raise ValueError("Job root must be outside every Git checkout")
        if any(self.job_root == p or p in self.job_root.parents for p in (ROOT, self.addon)):
            raise ValueError("Job root must be outside source directories")
        if any(self.job_root == p or self.job_root in p.parents for p in (ROOT, self.addon)):
            raise ValueError("Job root must not contain source directories")
        self.timeout = config.get("timeout", 120)
        self.threads = config.get("threads", 2)
        if type(self.timeout) not in (int, float) or not math.isfinite(self.timeout) or not 1 <= self.timeout <= 1800:
            raise ValueError("timeout must be finite seconds between 1 and 1800")
        if type(self.threads) is not int or not 1 <= self.threads <= 64:
            raise ValueError("threads must be between 1 and 64")
        self.job_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def source(self, value, extension=".3do"):
        path = checked_path(value)
        if not any(path.is_relative_to(root) for root in self.read_roots):
            raise ValueError("Input is outside configured read roots")
        if path.suffix.lower() != extension:
            raise ValueError(f"Input must be a {extension} file")
        if path.stat().st_size > MAX_INPUT:
            raise ValueError("Input exceeds the 64 MiB limit")
        return path

    @staticmethod
    def snapshot(source, destination):
        with source.open("rb") as stream:
            data = stream.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            raise ValueError("Input grew beyond the 64 MiB limit")
        destination.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def key_folder(self, value, names=None):
        """List the regular .key files directly in an allowlisted folder, before any job exists.

        Subfolders are not searched. Links, junctions and other special .key entries are skipped
        and counted. ``names`` selects files by name, ignoring case. The selection must hold 1 to
        MAX_KEY_FILES files of at most MAX_KEY_BYTES together. KEY names that differ only in case
        are refused: the engine looks KEYs up without case.
        """
        folder = checked_path(value, directory=True)
        if not any(folder.is_relative_to(root) for root in self.read_roots):
            raise ValueError("KEY folder is outside configured read roots")
        found, skipped = {}, {"links": 0, "not_regular": 0}
        with os.scandir(folder) as entries:
            for entry in entries:
                if not entry.name.lower().endswith(".key"):
                    continue
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                    skipped["links"] += 1
                elif not stat.S_ISREG(info.st_mode):
                    skipped["not_regular"] += 1
                else:
                    found.setdefault(entry.name.casefold(), []).append((entry.name, info.st_size))
        wanted = sorted(found) if names is None else [name.casefold() for name in file_names("keys", names)]
        chosen = []
        for key in wanted:
            if key not in found:
                raise ValueError(f"KEY folder has no regular file named {names[wanted.index(key)]!r}")
            if len(found[key]) > 1:
                raise ValueError("KEY names differ only in case: " + ", ".join(sorted(n for n, _ in found[key])))
            chosen.append(found[key][0])
        chosen.sort(key=lambda item: (item[0].casefold(), item[0]))
        if not chosen:
            raise ValueError("KEY folder has no regular .key files")
        if len(chosen) > MAX_KEY_FILES:
            raise ValueError(f"Select at most {MAX_KEY_FILES} KEYs; the folder has {len(chosen)}")
        if sum(size for _, size in chosen) > MAX_KEY_BYTES:
            raise ValueError("Selected KEYs exceed the 64 MiB folder limit")
        return {"folder": folder, "files": [(name, folder / name, size) for name, size in chosen], "skipped": skipped}

    def snapshot_keys(self, listing, destination):
        """Copy a listed KEY selection into a new job folder, re-checking each file and the limits."""
        destination.mkdir(parents=True)
        files, total = [], 0
        for name, path, _ in listing["files"]:
            source = checked_path(str(path))
            digest = self.snapshot(source, destination / name)
            size = (destination / name).stat().st_size
            total += size
            if total > MAX_KEY_BYTES:
                raise ValueError("KEY folder grew beyond the 64 MiB limit")
            files.append({"name": name, "size": size, "sha256": digest})
        manifest = {"folder": str(listing["folder"]), "count": len(files), "bytes": total,
                    "skipped": listing["skipped"], "files": files}
        return manifest, [str(destination / item["name"]) for item in files]

    def reference_input(self, reference_key, reference_frame, zero_rotations):
        """Validate the reference pose: the rest pose, or a KEY with a frame and an explicit rotation rule."""
        frame = number("reference_frame", reference_frame, -1e6, 1e6)
        if zero_rotations is not None:
            flag("zero_rotations", zero_rotations)
        if reference_key is None:
            if zero_rotations:
                raise ValueError("zero_rotations needs reference_key: the rest pose has no KEY to zero")
            if frame != 0:
                raise ValueError("reference_frame needs reference_key")
            return None, {"pose": "rest"}
        if zero_rotations is None:
            raise ValueError("With reference_key, state zero_rotations: true for characters cut with straight joints at "
                             "the stand KEY's seats (Red's Kyle-skeleton builds), false to keep the KEY's rotations")
        path = self.source(reference_key, ".key")
        return path, {"pose": "key", "name": path.name, "frame": frame, "zero_rotations": zero_rotations}

    @contextmanager
    def job(self, operation):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Another job is running; retry after it completes")
        try:
            checked_path(str(self.job_root), directory=True)
            for parent in (self.job_root, *self.job_root.parents):
                if os.path.lexists(parent / ".git"):
                    raise ValueError("Job root acquired a Git ancestor")
            path = self.job_root / (operation + "-" + uuid.uuid4().hex)
            path.mkdir(exist_ok=False)
            yield path
        finally:
            self.lock.release()

    def info(self):
        return {"server": "blender-sith", "version": VERSION, "transport": "stdio",
                "runtime_route": self.route, "timeout_seconds": self.timeout, "threads": self.threads,
                "read_roots": [str(p) for p in self.read_roots], "job_root": str(self.job_root),
                "input_limit_bytes": MAX_INPUT, "key_folder_limits": {"files": MAX_KEY_FILES, "bytes": MAX_KEY_BYTES},
                "job_input_limit_bytes": MAX_JOB_INPUT, "report_limit_bytes": MAX_REPORT,
                "tools": ["server_info", "inspect_3do", "audit_model_key", "convert_3do_to_blend", "run_regression", "build_extension",
                          *AUDIT_TOOLS],
                "limits": ["Local trusted-user service, not an OS sandbox", "No interactive session or arbitrary Python tool",
                           "Geometry-only import: no implicit MAT/CMP reads; JK 2.1 UV normalization needs texture dimensions",
                           "No game-runtime compatibility claim",
                           "Audits are geometric checks of native files; a human or a game test must still accept a model",
                           "Audit jobs share the configured timeout; a full joint-gap sweep of 91 KEYs can take minutes"]}

    def environment(self, job):
        return runner.isolated_environment(job, self.threads, prefix="prefs-")

    def execute(self, command, job, *, timeout=None):
        """Capture bounded logs; never forward Blender stdout to the MCP wire."""
        receipt = {"status": "launch_failed", "exit_code": None}
        started = time.monotonic()
        process = None
        readers = []

        def drain(pipe, path):
            kept = 0
            with pipe, path.open("xb") as log:
                while block := pipe.read(65536):
                    if kept < MAX_LOG:
                        chunk = block[:MAX_LOG - kept]
                        log.write(chunk)
                        kept += len(chunk)

        try:
            process = subprocess.Popen(command, cwd=job, env=self.environment(job), stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=os.name != "nt")
            for stream, name in ((process.stdout, "stdout.log"), (process.stderr, "stderr.log")):
                reader = threading.Thread(target=drain, args=(stream, job / name), daemon=True)
                reader.start()
                readers.append(reader)
            receipt["exit_code"] = process.wait(timeout=timeout or self.timeout)
            receipt["status"] = "completed" if process.returncode == 0 else "process_failed"
        except subprocess.TimeoutExpired:
            receipt["status"] = "timeout"
            receipt["owned_tree_cleanup_succeeded"] = runner.terminate_tree(process)
            receipt["exit_code"] = process.returncode
        except BaseException:
            if process is not None and process.poll() is None:
                runner.terminate_tree(process)
            raise
        finally:
            for reader in readers:
                reader.join(timeout=2)
            receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
            (job / "process.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return receipt

    def runtime_command(self, worker, request):
        if self.route == "blender":
            return [str(self.executable), "--background", "--factory-startup", "--offline-mode", "--disable-autoexec", "--threads", str(self.threads),
                    "--python-exit-code", "1", "--python", str(worker), "--", str(request)]
        return [str(self.executable), "-B", str(worker), str(request)]

    def model_job(self, path, convert, key_path=None, key_target="JKDF2"):
        source = self.source(path)
        key = None
        if key_path is not None:
            if convert or key_target not in {"JKDF2", "MOTS"}:
                raise ValueError("Choose JKDF2 or MOTS for the KEY binding audit")
            key = self.source(key_path, ".key")
        operation = "audit" if key else ("convert" if convert else "inspect")
        with self.job(operation) as job:
            snapshot = job / "input.3do"
            input_hash = self.snapshot(source, snapshot)
            key_fields = {}
            if key:
                key_hash = self.snapshot(key, job / "input.key")
                key_fields = {"key": str(job / "input.key"), "key_name": key.name, "key_target": key_target}
            before = runner.source_hashes(self.addon)
            request = job / "request.json"
            request.write_text(json.dumps({**key_fields, "addon": str(self.addon), "input": str(snapshot), "output": str(job / "model.blend") if convert else None,
                                           "report": str(job / "model.json"), "threads": self.threads,
                                           "bpy_site": str(self.bpy_site) if self.bpy_site else None}), encoding="utf-8")
            process = self.execute(self.runtime_command(ROOT / "mcp_server" / "worker.py", request), job)
            result = {"job_id": job.name, "job_directory": str(job), "input_sha256": input_hash, **process}
            if key:
                result["key_sha256"] = key_hash
            if process["status"] == "process_failed" and (job / "diagnostic.json").is_file():
                result["diagnostic"] = bounded_json(job / "diagnostic.json").get("detail")
            result["source_unchanged"] = before == runner.source_hashes(self.addon)
            if process["status"] == "completed":
                try:
                    result["model"] = bounded_json(job / "model.json")
                    if result["model"].get("schema") != "blender-sith-mcp-model-v1":
                        raise ValueError("Wrong model report schema")
                    if key and result["model"].get("binding", {}).get("status") not in {"consistent", "review", "incompatible", "no_tracks"}:
                        raise ValueError("Missing KEY binding result")
                    if convert:
                        output = checked_path(str(job / "model.blend"))
                        result["output"] = {"path": str(output), "sha256": runner.digest(output), "size": output.stat().st_size}
                    result["status"] = "passed" if result["source_unchanged"] else "source_changed"
                except (ValueError, OSError):
                    result["status"] = "invalid_report"
            (job / "receipt.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return result

    def regression(self):
        with self.job("regression") as job:
            output = job / "regression"
            command = [sys.executable, "-B", str(ROOT / "scripts" / "run_regression.py"), "--" + self.route, str(self.executable),
                       "--addon", str(self.addon), "--output", str(output), "--timeout", str(self.timeout), "--threads", str(self.threads)]
            if self.bpy_site:
                command += ["--bpy-site", str(self.bpy_site)]
            process = self.execute(command, job, timeout=self.timeout + 45)
            result = {"job_id": job.name, "job_directory": str(job), **process}
            if process["status"] == "completed":
                try:
                    receipt = bounded_json(output / "receipt.json")
                    if receipt.get("status") != "passed" or receipt.get("source_unchanged") is not True:
                        raise ValueError("Regression did not pass")
                    result.update(status="passed", regression=receipt.get("regression"), runtime=receipt.get("runtime"))
                except (ValueError, OSError):
                    result["status"] = "invalid_report"
            return result

    def package(self):
        if (self.addon.name != "sith" or not (self.addon.parent / "LICENSE").is_file()
                or not (self.addon / "blender_manifest.toml").is_file()):
            raise ValueError("Packaging requires the configured source checkout's sith directory, manifest and sibling LICENSE")
        with self.job("package") as job:
            process = self.execute([sys.executable, "-B", str(ROOT / "scripts" / "build_extension.py"),
                                    "--source-dir", str(self.addon.parent), "--output-dir", str(job / "package")], job)
            result = {"job_id": job.name, "job_directory": str(job), "source": str(self.addon),
                      "validator": {"status": "not_run", "reason": "Package creation did not complete"}, **process}
            if process["status"] == "completed":
                receipts = list((job / "package").glob("*.receipt.json"))
                if len(receipts) != 1:
                    result["status"] = "invalid_report"
                else:
                    receipt = bounded_json(receipts[0])
                    archive = job / "package" / receipt["archive"]["name"]
                    if archive.parent != job / "package" or runner.digest(archive) != receipt["archive"]["sha256"]:
                        result["status"] = "invalid_report"
                    else:
                        result.update(status="passed", archive={**receipt["archive"], "path": str(archive)}, receipt=str(receipts[0]))
                        if self.route != "blender":
                            result["validator"] = {"status": "not_run", "reason": "Python+bpy route has no configured Blender CLI executable"}
                        else:
                            validation_job = job / "official-validation"
                            validation_job.mkdir(exist_ok=False)
                            command = [str(self.executable), "--background", "--factory-startup", "--offline-mode",
                                       "--disable-autoexec", "--threads", str(self.threads), "--command", "extension", "validate", str(archive)]
                            try:
                                validation = self.execute(command, validation_job)
                            except OSError as exc:
                                validation = {"status": "launch_failed", "exit_code": None, "error_type": type(exc).__name__}
                            result["validator"] = {**validation, "method": "blender_builtin_extension_validate", "logs_directory": str(validation_job)}
                            if validation["status"] == "completed":
                                result["validator"]["status"] = "passed"
                            else:
                                result["status"] = "validation_failed"
            (job / "receipt.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return result

    @staticmethod
    def written_files(folder, claimed):
        """Check the files a job says it wrote into ``folder`` and return the server's own record of them.

        Each entry gives a file name, a size and a SHA-256. The name must be a regular file directly
        in ``folder``, with no link on its route, and the size and digest must match. ``folder`` must
        hold nothing else, so the list is complete. Entries carry no paths, so a long job root
        cannot push the result past ``MAX_REPORT``.
        """
        folder = checked_path(str(folder), directory=True)
        if not isinstance(claimed, list) or len(claimed) > MAX_KEY_FILES:
            raise ValueError("Missing or oversized output list")
        outputs = {}
        for item in claimed:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ValueError("Malformed output entry")
            path = checked_path(str(folder / item["name"]))
            if path.parent != folder or path.name != item["name"] or path.name in outputs:
                raise ValueError("Output outside the job's output folder")
            size, digest = path.stat().st_size, runner.digest(path)
            if item.get("size") != size or item.get("sha256") != digest:
                raise ValueError("Output record does not match the file")
            outputs[path.name] = {"name": path.name, "path": str(path), "size": size, "sha256": digest}
        if {entry.name for entry in folder.iterdir()} != set(outputs):
            raise ValueError("The output folder holds files the result does not list")
        return list(outputs.values())

    def audit_job(self, kind, parameters, sections, *, model=None, keys=None, reference=None, characters=(),
                  outputs=None):
        """Snapshot the named inputs, run one fixed audit in the configured runtime, accept a bounded result.

        ``model`` and ``reference`` are checked paths, ``keys`` a ``key_folder`` listing and
        ``characters`` (name, model path, listing) triples. ``sections`` maps each result section the
        audit must return to its accepted status values. The worker writes the complete report to
        ``report.json`` and the bounded summary returned here to ``result.json``.

        ``outputs`` names the one folder of the job that the audit writes files into (the reseat
        pass: ``reseated``). The result must list every file there, and ``written_files`` checks
        them before they are returned under ``outputs``. A result of any other audit that lists
        outputs is refused.
        """
        listings = ([keys] if keys is not None else []) + [listing for _, _, listing in characters]
        paths = [path for path in (model, reference) if path is not None] + [path for _, path, _ in characters]
        sizes = [path.stat().st_size for path in paths] + [size for listing in listings for _, _, size in listing["files"]]
        if sum(sizes) > MAX_JOB_INPUT:
            raise ValueError("The inputs of one audit job exceed the 256 MiB limit")
        with self.job("audit-" + kind) as job:
            inputs, request_inputs = {}, {}
            if model is not None:
                inputs["model"] = {"source": str(model), "sha256": self.snapshot(model, job / "input.3do"),
                                   "size": (job / "input.3do").stat().st_size}
                request_inputs["model"] = str(job / "input.3do")
            if reference is not None:
                inputs["reference_key"] = {"source": str(reference), "sha256": self.snapshot(reference, job / "reference.key"),
                                           "size": (job / "reference.key").stat().st_size}
                request_inputs["reference"] = str(job / "reference.key")
            if keys is not None:
                inputs["keys"], request_inputs["keys"] = self.snapshot_keys(keys, job / "keys")
            if characters:
                inputs["characters"], request_inputs["characters"] = [], []
                for index, (name, path, listing) in enumerate(characters):
                    folder = job / "characters" / str(index)
                    folder.mkdir(parents=True)
                    manifest, paths = self.snapshot_keys(listing, folder / "keys")
                    digest = self.snapshot(path, folder / "input.3do")
                    inputs["characters"].append({"name": name, "model": {"source": str(path), "sha256": digest,
                                                 "size": (folder / "input.3do").stat().st_size}, "keys": manifest})
                    request_inputs["characters"].append({"name": name, "model": str(folder / "input.3do"), "keys": paths})
            total = sum(path.stat().st_size for path in job.rglob("*") if path.is_file())
            if total > MAX_JOB_INPUT:
                raise ValueError("The inputs of one audit job grew beyond the 256 MiB limit")
            before = runner.source_hashes(self.addon)
            request = job / "request.json"
            request.write_text(json.dumps({"kind": kind, "addon": str(self.addon), "threads": self.threads,
                                           "bpy_site": str(self.bpy_site) if self.bpy_site else None,
                                           "parameters": parameters, "inputs": request_inputs,
                                           "report": str(job / "report.json"), "result": str(job / "result.json"),
                                           "progress": str(job / "progress.json")}), encoding="utf-8")
            process = self.execute(self.runtime_command(ROOT / "mcp_server" / "audit_worker.py", request), job)
            result = {"job_id": job.name, "job_directory": str(job), "audit_kind": kind, "inputs": inputs, **process}
            if process["status"] == "process_failed" and (job / "diagnostic.json").is_file():
                result["diagnostic"] = bounded_json(job / "diagnostic.json").get("detail")
            if process["status"] != "completed" and (job / "progress.json").is_file():
                try:
                    result["progress"] = bounded_json(job / "progress.json")
                except (ValueError, OSError):
                    pass
            result["source_unchanged"] = before == runner.source_hashes(self.addon)
            if process["status"] == "completed":
                try:
                    audit = bounded_json(job / "result.json")
                    if audit.get("schema") != AUDIT_SCHEMA or audit.get("kind") != kind:
                        raise ValueError("Wrong audit result schema")
                    for section, accepted in sections.items():
                        if not isinstance(audit.get(section), dict) or audit[section].get("status") not in accepted:
                            raise ValueError("Missing audit section " + section)
                    claimed = audit.pop("outputs", None)
                    if outputs is None and claimed is not None:
                        raise ValueError("This audit writes no files")
                    written = self.written_files(job / outputs, claimed) if outputs is not None else None
                    report = checked_path(str(job / "report.json"))
                    result["audit"] = audit
                    if written is not None:
                        result["outputs"] = written
                    result["report"] = {"path": str(report), "sha256": runner.digest(report), "size": report.stat().st_size}
                    result["status"] = "passed" if result["source_unchanged"] else "source_changed"
                except (ValueError, OSError):
                    result["status"] = "invalid_report"
            (job / "receipt.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            return result

    def audit_budget(self, path, target="JKDF2"):
        choice("target", target, ("JKDF2", "MOTS", "IJIM", "GRIM"))
        return self.audit_job("budget", {"target": target}, {"budget": AUDIT_STATUSES["budget"]}, model=self.source(path))

    def audit_segmentation(self, model_path, reference_key=None, reference_frame=0.0, zero_rotations=None,
                           key_dir=None, keys=None, step=2.0, geoset=0, detail="review"):
        step = number("step", step, 0.05, 1000)
        geoset = whole("geoset", geoset, 0, 63)
        choice("detail", detail, ("high", "review", "info"))
        if keys is not None and key_dir is None:
            raise ValueError("keys selects files of key_dir; give key_dir")
        reference, pose = self.reference_input(reference_key, reference_frame, zero_rotations)
        model = self.source(model_path)
        listing = self.key_folder(key_dir, keys) if key_dir is not None else None
        parameters = {"reference": pose, "step": step, "geoset": geoset, "detail": detail}
        return self.audit_job("segmentation", parameters, {"segmentation": AUDIT_STATUSES["segmentation"]},
                              model=model, keys=listing, reference=reference)

    def audit_articulation(self, model_path, key_dir, keys=None, step=2.0, reference_key=None, reference_frame=0.0,
                           zero_rotations=None, joints="derived", scale=1.0, seat_offsets=True, geoset=0):
        step = number("step", step, 0.05, 1000)
        scale = number("scale", scale, 0.01, 100)
        geoset = whole("geoset", geoset, 0, 63)
        choice("joints", joints, ("derived", "kyle"))
        flag("seat_offsets", seat_offsets)
        reference, pose = self.reference_input(reference_key, reference_frame, zero_rotations)
        model = self.source(model_path)
        listing = self.key_folder(key_dir, keys)
        sections = {"articulation": AUDIT_STATUSES["articulation"]}
        if seat_offsets:
            sections["seat_offsets"] = AUDIT_STATUSES["seat_offsets"]
        parameters = {"reference": pose, "step": step, "joints": joints, "scale": scale, "seat_offsets": seat_offsets,
                      "geoset": geoset}
        return self.audit_job("articulation", parameters, sections, model=model, keys=listing, reference=reference)

    def audit_ground(self, model_path, key_dir, keys=None, step=1.0, floor="engine", move_size=STOCK_MOVE_SIZE,
                     thing_height=0.0, crouch_keys=None, contacts=None, travel=None, geoset=0):
        step = number("step", step, 0.05, 1000)
        geoset = whole("geoset", geoset, 0, 63)
        if floor not in ("engine", "rest"):
            if type(floor) not in (int, float):
                raise ValueError("floor must be 'engine', 'rest' or a height in JK units")
            floor = number("floor", floor, -1000, 1000)
        move_size = number("move_size", move_size, 0, 100)
        thing_height = number("thing_height", thing_height, 0, 100)
        if floor != "engine" and (crouch_keys or move_size != STOCK_MOVE_SIZE or thing_height):
            raise ValueError("crouch_keys, move_size and thing_height set the engine floor; use floor='engine'")
        crouch = file_names("crouch_keys", crouch_keys) or []
        if contacts is not None:
            if (not isinstance(contacts, list) or not contacts or len(contacts) > 64
                    or any(type(c) not in (int, str) or (type(c) is int and c < 0) for c in contacts)):
                raise ValueError("contacts must list 1 to 64 node names or indices")
        directions = {}
        if travel is not None:
            if not isinstance(travel, dict) or len(travel) > MAX_KEY_FILES:
                raise ValueError("travel maps KEY file names to (x, y) directions")
            file_names("travel", list(travel))
            for name, direction in travel.items():
                if (not isinstance(direction, list) or len(direction) != 2
                        or any(type(v) not in (int, float) or not math.isfinite(v) for v in direction)
                        or not any(direction)):
                    raise ValueError(f"travel[{name!r}] must be a nonzero (x, y) direction")
                directions[name] = [float(v) for v in direction]
        model = self.source(model_path)
        listing = self.key_folder(key_dir, keys)
        selected = {name.casefold(): name for name, _, _ in listing["files"]}
        for name in [*crouch, *directions]:
            if name.casefold() not in selected:
                raise ValueError(f"{name!r} is not among the selected KEYs")
        parameters = {"step": step, "floor": floor, "move_size": move_size, "thing_height": thing_height,
                      "crouch_keys": [selected[name.casefold()] for name in crouch], "contacts": contacts,
                      "travel": {selected[name.casefold()]: value for name, value in directions.items()},
                      "geoset": geoset}
        return self.audit_job("ground", parameters, {"ground": AUDIT_STATUSES["ground"]}, model=model, keys=listing)

    def lint_keys(self, key_dir, keys=None, model_path=None, target="JKDF2", pops=True):
        choice("target", target, ("JKDF2", "MOTS"))
        flag("pops", pops)
        model = self.source(model_path) if model_path is not None else None
        listing = self.key_folder(key_dir, keys)
        return self.audit_job("lint", {"target": target, "pops": pops}, {"lint": AUDIT_STATUSES["lint"]},
                              model=model, keys=listing)

    def audit_track_slots(self, characters, custom=18):
        if not isinstance(characters, list) or not 2 <= len(characters) <= MAX_CHARACTERS:
            raise ValueError(f"Give 2 to {MAX_CHARACTERS} characters")
        if type(custom) is int:
            whole("custom", custom, 0, 1024)
        elif isinstance(custom, list):
            if not custom or len(custom) > 64 or any(type(s) is not int or not 0 <= s <= 1024 for s in custom):
                raise ValueError("custom slots must be 1 to 64 slot numbers from 0 to 1024")
        elif custom != "names":
            raise ValueError("custom must be a first slot number, 'names' or a list of slot numbers")
        entries, names = [], set()
        for item in characters:
            if not isinstance(item, dict) or set(item) - {"name", "model_path", "key_dir", "keys"}:
                raise ValueError("Each character is {name, model_path, key_dir, keys (optional)}")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 64 or "\x00" in name:
                raise ValueError("Each character needs a name of 1 to 64 characters")
            if name.casefold() in names:
                raise ValueError(f"Character name {name!r} is given twice")
            names.add(name.casefold())
            entries.append((name, self.source(item.get("model_path")), self.key_folder(item.get("key_dir"), item.get("keys"))))
        return self.audit_job("track_slots", {"custom": custom}, {"track_slots": AUDIT_STATUSES["track_slots"]},
                              characters=entries)

    def reseat_keys(self, model_path, key_dir, keys=None, stand_key=None, stand_frame=0.0, rest_seats=False,
                    waist_tuck=0.0025, waist_lift=0.0, check=True):
        """Reseat KEY snapshots in a new job; the worker writes the changed ones into its ``reseated`` folder.

        The seats come from a stand KEY or, when asked for explicitly, from the model's rest
        positions, as ``reseatKeyFiles`` requires.
        """
        stand_frame = number("stand_frame", stand_frame, 0, 1e6)
        waist_tuck = number("waist_tuck", waist_tuck, 0, 0.01)
        waist_lift = number("waist_lift", waist_lift, 0, 0.01)
        flag("rest_seats", rest_seats)
        flag("check", check)
        if stand_key is None:
            if not rest_seats:
                raise ValueError("Give stand_key, the KEY whose positions are the seats (usually the stand KEY "
                                 "kystand1.key at frame 0), or rest_seats=true for the model's rest positions")
            if stand_frame != 0:
                raise ValueError("stand_frame needs stand_key")
            stand, seats = None, None
        else:
            if rest_seats:
                raise ValueError("Give stand_key or rest_seats=true, not both")
            stand = self.source(stand_key, ".key")
            seats = {"name": stand.name, "frame": stand_frame}
        model = self.source(model_path)
        listing = self.key_folder(key_dir, keys)
        parameters = {"stand": seats, "waist_tuck": waist_tuck, "waist_lift": waist_lift, "check": check}
        return self.audit_job("reseat", parameters, {"reseat": AUDIT_STATUSES["reseat"]}, model=model, keys=listing,
                              reference=stand, outputs="reseated")


def create_server(service):
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    server = MCPServer("blender-sith", version=VERSION, instructions="Operate only on explicitly authorized 3DO and KEY inputs. Outputs are new jobs. Inspect status, receipts and limitations before claiming success. An audit's status passed means the audit ran; its verdict is in audit.<section>.status, and a count is a lead to look at, not a verdict.")

    def invoke(fn, *args):
        try:
            return fn(*args)
        except (ValueError, OSError, RuntimeError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    def server_info() -> dict[str, Any]:
        """Return configured scope and limits; does not launch Blender."""
        return service.info()

    @server.tool()
    def inspect_3do(path: str) -> dict[str, Any]:
        """Parse one allowlisted .3do in isolated Blender and return bounded geometry counts, materials and hierarchy counts. No referenced files are read."""
        return invoke(service.model_job, path, False)

    @server.tool()
    def audit_model_key(model_path: str, key_path: str, key_target: str = "JKDF2") -> dict[str, Any]:
        """Audit native 3DO hierarchy indices against one KEY's tracks and JKDF2 or MOTS KEY export limits. Inspect model.binding for findings. Reads two named files and creates a receipt."""
        return invoke(service.model_job, model_path, False, key_path, key_target)

    @server.tool()
    def convert_3do_to_blend(path: str) -> dict[str, Any]:
        """Convert one authorized .3do to a NEW geometry-only .blend job artifact. No textures/palette are loaded; only geoset zero is imported. Never overwrite inputs."""
        return invoke(service.model_job, path, True)

    @server.tool()
    def run_regression() -> dict[str, Any]:
        """Run the bundled synthetic 3DO/KEY/MAT regressions using the configured Blender runtime. Creates logs and machine-readable receipts."""
        return invoke(service.regression)

    @server.tool()
    def build_extension() -> dict[str, Any]:
        """Build the configured source checkout's sith extension reproducibly with SHA-256 receipt. A configured Blender CLI also runs its built-in extension validator; Python+bpy explicitly reports validation not_run. Does not publish or install."""
        return invoke(service.package)

    @server.tool()
    def audit_budget(path: str, target: str = "JKDF2") -> dict[str, Any]:
        """Count one allowlisted .3do against the retail JKDF2/MOTS limits (512 vertices, 768 texture vertices and 512 faces per mesh, 24 corners per face, 4 geosets, 64 nodes). target JKDF2 or MOTS gives a verdict; IJIM or GRIM gives counts only. Counts the file as stored. Read audit.budget.status and audit.budget.warnings."""
        return invoke(service.audit_budget, path, target)

    @server.tool()
    def audit_segmentation(model_path: str, reference_key: str | None = None, reference_frame: float = 0.0,
                           zero_rotations: bool | None = None, key_dir: str | None = None, keys: list[str] | None = None,
                           step: float = 2.0, geoset: int = 0, detail: str = "review") -> dict[str, Any]:
        """Cut lint: find surface a cut left in the wrong rigid piece (ghosts) of one .3do. The reference must be the pose the pieces were cut in: the rest pose by default, or reference_key (an allowlisted .key) at reference_frame. For Red's Kyle-skeleton characters pass the stand KEY they were cut against with zero_rotations=true (stock kystand1 for Darth Lilitari; Biddy's own kystand1); reference_diagnostic reports how many joint frames differ from rest and flags a KEY that drives no model nodes. key_dir (optional, with keys as a subset) adds test poses every step frames for separation. Read audit.segmentation.status (clean, review, suspect) and findings; a finding is a lead, not a verdict."""
        return invoke(service.audit_segmentation, model_path, reference_key, reference_frame, zero_rotations,
                      key_dir, keys, step, geoset, detail)

    @server.tool()
    def audit_articulation(model_path: str, key_dir: str, keys: list[str] | None = None, step: float = 2.0,
                           reference_key: str | None = None, reference_frame: float = 0.0,
                           zero_rotations: bool | None = None, joints: str = "derived", scale: float = 1.0,
                           seat_offsets: bool = True, geoset: int = 0) -> dict[str, Any]:
        """Joint-gap sweep: daylight through each joint in every sampled pose of the KEYs in key_dir (regular .key files; keys selects a subset by file name), plus each KEY's seat offsets. Counts depend on the reference pose, and reference_diagnostic compares it with rest: the rest pose by default, or reference_key at reference_frame with an explicit zero_rotations. For Red's Kyle-skeleton characters pass the stand KEY they were cut against with zero_rotations=true (stock kystand1 for Darth Lilitari; Biddy's own kystand1). joints 'kyle' uses Kyle's 15 joints; scale multiplies the anchor lengths for characters of another size. Slow: about 80 s for 91 KEYs on a 14,600-face model at step 2, after the model loads; the job stops at the configured timeout with status timeout and its progress. Use keys or a larger step for a quicker run. A count is a lead, not a verdict."""
        return invoke(service.audit_articulation, model_path, key_dir, keys, step, reference_key, reference_frame,
                      zero_rotations, joints, scale, seat_offsets, geoset)

    @server.tool()
    def audit_ground(model_path: str, key_dir: str, keys: list[str] | None = None, step: float = 1.0,
                     floor: str | float = "engine", move_size: float = STOCK_MOVE_SIZE, thing_height: float = 0.0,
                     crouch_keys: list[str] | None = None, contacts: list[str | int] | None = None,
                     travel: dict[str, list[float]] | None = None, geoset: int = 0) -> dict[str, Any]:
        """Floor contact (below_floor, floating) and contact slip of one .3do over each KEY in key_dir played alone every step frames. floor 'engine' is where physics holds the thing: move_size and thing_height are the player template's movesize and height (stock walkplayer: 0.065 and 0), and crouch_keys play on the crouch floor. floor 'rest' is the rest pose's lowest point; a number is a height. contacts (node names or indices, such as the feet) adds slip; travel maps movement-controlled KEY names to their (x, y) direction, for example kywalkf.key [0, 1]. Jumps, falls, deaths and swims float or sink by design."""
        return invoke(service.audit_ground, model_path, key_dir, keys, step, floor, move_size, thing_height,
                      crouch_keys, contacts, travel, geoset)

    @server.tool()
    def lint_keys(key_dir: str, keys: list[str] | None = None, model_path: str | None = None, target: str = "JKDF2",
                  pops: bool = True) -> dict[str, Any]:
        """Lint the KEYs in key_dir for JKDF2 or MOTS: header and track table as the engine loads them (tracks past JOINTS, out-of-order and late entries, repeated tracks) and, with pops, Euler flips, wide steps, form switches and speed spikes between entries. KEYs load the way the engine reads them, keeping tracks the strict loader refuses. model_path adds the binding audit, joints the KEY cannot drive and position jumps. Read audit.lint.status, totals and keys_flagged."""
        return invoke(service.lint_keys, key_dir, keys, model_path, target, pops)

    @server.tool()
    def audit_track_slots(characters: list[dict[str, Any]], custom: int | str | list[int] = 18) -> dict[str, Any]:
        """Check whether several characters' KEYs can share one pack. characters lists 2 to 8 entries {name, model_path, key_dir, keys (optional subset)}. For every KEY file name two characters both ship, custom slots (default: 18 and up, past Kyle's skeleton; 'names' for slots the models name differently; or a list of slots) that both drive differently are slot_collision, and a slot one drives where the other's model has a visible node its own KEY leaves alone is slot_intrusion."""
        return invoke(service.audit_track_slots, characters, custom)

    @server.tool()
    def reseat_keys(model_path: str, key_dir: str, keys: list[str] | None = None, stand_key: str | None = None,
                    stand_frame: float = 0.0, rest_seats: bool = False, waist_tuck: float = 0.0025,
                    waist_lift: float = 0.0, check: bool = True) -> dict[str, Any]:
        """Reseat the KEYs in key_dir (regular .key files; keys selects a subset) for one .3do: the hip piece and the seated joints (shoulders, forearms, hands, neck, head) go back on the seats that stock KEYs push them off. No rotation changes, and the torso and legs stay where the KEY put them. Seats come from stand_key (an allowlisted .key, usually the stand KEY kystand1.key) at stand_frame, or with rest_seats=true from the model's rest positions, only when its rest pose matches the stand KEY. waist_tuck (JK units) is the most of a leftover thigh push that goes to the waist as an overlap; waist_lift (default 0; Lilivane R3 used 0.0015) lets the hip follow the torso up where a waist gap is left and opens the hip joints as much. Reseated copies are written only for KEYs that change, only inside the job directory (job/reseated); inputs are never changed. outputs lists each written file with the size and SHA-256 the server checked. Install only the written files, after review. Reseating stock KEYs changes them for every model in that install. check poses each written KEY against its source; audit.reseat.status fail means a written KEY failed and must not be installed. The check does not show that joints close: run audit_articulation before and after on the same KEYs and step. Kyle's hip layout only (k_hip, k_torso, k_lthigh, k_rthigh and the seated joints, each named once); run it before adding custom tracks that move the hip's frames."""
        return invoke(service.reseat_keys, model_path, key_dir, keys, stand_key, stand_frame, rest_seats,
                      waist_tuck, waist_lift, check)

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Explicit local JSON configuration")
    args = parser.parse_args()
    try:
        config = bounded_json(checked_path(str(args.config)))
        service = Service(config)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    create_server(service).run(transport="stdio")


if __name__ == "__main__":
    main()
