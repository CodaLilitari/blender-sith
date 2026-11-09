#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded native macOS validation of an exact Apple Silicon extension package.

Manual CI only: all downloads, staged sources, packages, logs and fixtures live
in a new directory beneath RUNNER_TEMP, outside the checkout. The official
Blender DMG is checksum-verified and mounted read-only; desktop preferences are
not changed. This script does not publish, install the extension, or invoke Git.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BLENDER_VERSION = "5.2.3"
ASSET = "blender-5.2.3-macos-arm64.dmg"
BASE_URL = "https://ftp.blender.org/release/Blender5.2/"
# Verified against the official blender-5.2.3.sha256 on 2026-11-09.
DMG_SHA256 = "acc05374485a272342d4b323c15ac01953e7f697da2f1bad8af9c3bf087267a4"
EXPECTED_TESTS = 16


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checksum_for_asset(text, filename):
    matches = []
    for line in text.splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)", line)
        if match and match[2] == filename:
            matches.append(match[1].lower())
    if len(matches) != 1:
        raise ValueError("Official checksum list must contain exactly one matching asset")
    return matches[0]


def macos_manifest(raw):
    text = raw.decode("utf-8").replace("\r\n", "\n")
    manifest = tomllib.loads(text)
    if manifest.get("id") != "sith" or "platforms" in manifest:
        raise ValueError("Stage this variant from the common unrestricted sith manifest")
    lines = text.splitlines(keepends=True)
    indices = [i for i, line in enumerate(lines) if re.match(r"^version\s*=", line)]
    if len(indices) != 1 or not lines[indices[0]].endswith("\n"):
        raise ValueError("Expected one newline-terminated top-level version assignment")
    lines.insert(indices[0] + 1, 'platforms = ["macos-arm64"]\n')
    return "".join(lines).encode("utf-8")


def checked_output(path, runner_temp, source):
    lexical = Path(os.path.abspath(path))
    resolved = lexical.resolve()
    runner_temp = runner_temp.resolve()
    for candidate in (lexical, resolved):
        if candidate == source or candidate.is_relative_to(source):
            raise ValueError("Output must be outside the checkout")
        if any(os.path.lexists(parent / ".git") for parent in (candidate, *candidate.parents)):
            raise ValueError("Output must have no Git ancestor")
    if resolved == runner_temp or not resolved.is_relative_to(runner_temp):
        raise ValueError("Output must be a child of RUNNER_TEMP")
    if resolved.exists():
        raise ValueError("Output must be a new directory")
    return resolved


def validate_native_result(receipt, report):
    platform_info = receipt.get("platform", {})
    runtime = receipt.get("runtime", {})
    tests = report.get("tests", [])
    if (receipt.get("status") != "passed" or receipt.get("source_unchanged") is not True
            or platform_info != {"system": "Darwin", "machine": "arm64"}
            or runtime.get("route") != "blender" or runtime.get("blender") != BLENDER_VERSION
            or receipt.get("regression") != {"passed": EXPECTED_TESTS, "failed": 0, "addon_source_unchanged": True}
            or report.get("passed") != EXPECTED_TESTS or report.get("failed") != 0
            or len(tests) != EXPECTED_TESTS or any(t.get("passed") is not True for t in tests)):
        raise ValueError("Expected sixteen passing checks on native ARM64 macOS Blender " + BLENDER_VERSION)


def run_logged(command, name, evidence, cwd, env, timeout, *, check=True):
    logs = evidence / "logs"
    logs.mkdir(exist_ok=True)
    record = {"command": list(map(str, command)), "timeout_seconds": timeout}
    started = time.monotonic()
    try:
        with (logs / (name + ".stdout.log")).open("wb") as stdout, (logs / (name + ".stderr.log")).open("wb") as stderr:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
            try:
                record["exit_code"] = process.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
                record["timed_out_or_interrupted"] = True
                raise
    finally:
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (logs / (name + ".json")).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if check and record["exit_code"] != 0:
        raise RuntimeError(name + " failed; inspect its retained logs")
    return record["exit_code"]


def extract_package(archive, target):
    target.mkdir()
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None:
            raise ValueError("Extension ZIP CRC validation failed")
        for item in package.infolist():
            name = PurePosixPath(item.filename)
            if (name.is_absolute() or ".." in name.parts or "\\" in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("Unsafe extension archive member")
        package.extractall(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("This job requires native ARM64 macOS; emulation is not validation")
    if not os.environ.get("RUNNER_TEMP"):
        parser.error("RUNNER_TEMP must name the external job root")
    output = checked_output(args.output, Path(os.environ["RUNNER_TEMP"]), ROOT)
    output.mkdir(parents=True)
    evidence = output / "evidence"
    evidence.mkdir()
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", OMP_NUM_THREADS="2",
               OPENBLAS_NUM_THREADS="2", MKL_NUM_THREADS="2", TMPDIR=str(output))
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE"):
        env.pop(name, None)
    for suffix in ("CONFIG", "SCRIPTS", "DATAFILES", "EXTENSIONS"):
        directory = output / "config" / suffix.lower()
        directory.mkdir(parents=True)
        env["BLENDER_USER_" + suffix] = str(directory)
    receipt = {"schema": "blender-sith-native-macos-ci-v1", "status": "failed",
               "commit": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"),
               "system": platform.system(), "machine": platform.machine(),
               "macos": platform.mac_ver()[0], "expected_blender": BLENDER_VERSION,
               "expected_checks": EXPECTED_TESTS, "dmg_url": BASE_URL + ASSET,
               "dmg_sha256": DMG_SHA256, "scope": "synthetic package validation; no UI or game-runtime claim"}
    mount = output / "mount"
    mount_attempted = False
    try:
        run_logged([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(ROOT / "tests"),
                    "-p", "test_*.py", "-v"], "unit-tests", evidence, output, env, 300)

        stage = output / "staged-source"
        stage.mkdir()
        addon = ROOT / "sith"
        if addon.is_symlink() or any(p.is_symlink() for p in addon.rglob("*")):
            raise ValueError("Source symlinks are not permitted")
        shutil.copytree(addon, stage / "sith", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
        manifest_path = stage / "sith" / "blender_manifest.toml"
        raw_manifest = manifest_path.read_bytes()
        manifest_path.write_bytes(macos_manifest(raw_manifest))
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        receipt["manifest_adaptation"] = "Insert macos-arm64 platforms immediately after version; normalize CRLF to LF"
        receipt["source_manifest_sha256"] = hashlib.sha256(raw_manifest).hexdigest()
        receipt["staged_manifest_sha256"] = digest(manifest_path)
        packages = evidence / "packages"
        run_logged([sys.executable, "-B", str(ROOT / "scripts" / "build_extension.py"),
                    "--source-dir", str(stage), "--output-dir", str(packages)],
                   "build-package", evidence, output, env, 60)
        stem = "sith-" + manifest["version"] + "-macos-arm64"
        archive = packages / (stem + ".zip")
        package_receipt = json.loads((packages / (stem + ".receipt.json")).read_text(encoding="utf-8"))
        archive_hash = digest(archive)
        if archive_hash != package_receipt["archive"]["sha256"]:
            raise ValueError("Package receipt and archive hash disagree")
        receipt["archive"] = {"name": archive.name, "sha256": archive_hash, "size": archive.stat().st_size}
        extracted = output / "exact-package"
        extract_package(archive, extracted)

        downloads = output / "downloads"
        downloads.mkdir()
        checksum_path = downloads / ("blender-" + BLENDER_VERSION + ".sha256")
        curl = ["/usr/bin/curl", "--fail", "--show-error", "--location", "--proto", "=https",
                "--proto-redir", "=https", "--connect-timeout", "30", "--retry", "2", "--retry-max-time", "600"]
        run_logged(curl + ["--max-time", "60", "--output", str(checksum_path), BASE_URL + checksum_path.name],
                   "download-checksums", evidence, output, env, 90)
        if checksum_for_asset(checksum_path.read_text(encoding="utf-8"), ASSET) != DMG_SHA256:
            raise ValueError("Official checksum list differs from the reviewed pinned digest")
        shutil.copy2(checksum_path, evidence / checksum_path.name)
        dmg = downloads / ASSET
        run_logged(curl + ["--max-time", "600", "--output", str(dmg), BASE_URL + ASSET],
                   "download-blender", evidence, output, env, 650)
        if digest(dmg) != DMG_SHA256:
            raise ValueError("Blender DMG SHA-256 mismatch")
        receipt["dmg_verified"] = True
        mount.mkdir()
        mount_attempted = True
        run_logged(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen",
                    "-mountpoint", str(mount), "-plist", str(dmg)], "mount-dmg", evidence, output, env, 90)
        blender = mount / "Blender.app" / "Contents" / "MacOS" / "Blender"
        if not blender.is_file():
            raise ValueError("Official DMG did not expose the expected Blender executable")
        run_logged([str(blender), "--background", "--factory-startup", "--offline-mode", "--threads", "2",
                    "--command", "extension", "validate", str(archive)],
                   "extension-validate", evidence, output, env, 120)
        regression = evidence / "regression"
        run_logged([sys.executable, "-B", str(ROOT / "scripts" / "run_regression.py"),
                    "--blender", str(blender), "--addon", str(extracted), "--output", str(regression),
                    "--threads", "2", "--timeout", "300"], "native-regression", evidence, output, env, 360)
        launcher_receipt = json.loads((regression / "receipt.json").read_text(encoding="utf-8"))
        report = json.loads((regression / "results" / "regression.json").read_text(encoding="utf-8"))
        validate_native_result(launcher_receipt, report)
        if digest(archive) != archive_hash:
            raise ValueError("Tested archive changed during validation")
        receipt.update(status="passed", runtime=launcher_receipt["runtime"], regression=launcher_receipt["regression"])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        receipt["error"] = type(exc).__name__ + ": " + str(exc)
        print(receipt["error"], file=sys.stderr)
    finally:
        if mount_attempted:
            detached = False
            for label, extra in [("detach-dmg", []), ("force-detach-dmg", ["-force"])]:
                try:
                    detached = run_logged(["/usr/bin/hdiutil", "detach", *extra, str(mount)], label,
                                          evidence, output, env, 30, check=False) == 0
                except (OSError, subprocess.SubprocessError):
                    pass
                if detached:
                    break
            receipt["dmg_detached"] = detached
            if not detached:
                receipt["status"] = "failed"
                receipt["cleanup_error"] = "Could not confirm bounded detach of the owned mount"
        (evidence / "platform-validation.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "evidence": str(evidence)}))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
