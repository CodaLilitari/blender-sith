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
EXPECTED_TESTS = 29
REGRESSION_SCHEMA = "blender-sith-agentic-regression-v2"


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


def validate_native_result(receipt, report, expected_hashes):
    platform_info = receipt.get("platform", {})
    runtime = receipt.get("runtime", {})
    tests = report.get("tests", [])
    if (report.get("schema") != REGRESSION_SCHEMA
            or receipt.get("status") != "passed" or receipt.get("source_unchanged") is not True
            or platform_info != {"system": "Darwin", "machine": "arm64"}
            or runtime.get("route") != "blender"
            or runtime.get("blender") not in (BLENDER_VERSION, BLENDER_VERSION + " LTS")
            or receipt.get("regression") != {"passed": EXPECTED_TESTS, "failed": 0, "addon_source_unchanged": True}
            or receipt.get("source_before_sha256") != expected_hashes
            or receipt.get("source_after_sha256") != expected_hashes
            or report.get("addon_sha256") != expected_hashes or report.get("addon_source_unchanged") is not True
            or report.get("passed") != EXPECTED_TESTS or report.get("failed") != 0
            or len(tests) != EXPECTED_TESTS or any(t.get("passed") is not True for t in tests)):
        raise ValueError(f"Expected {EXPECTED_TESTS} passing checks on native ARM64 macOS Blender " + BLENDER_VERSION)


def companion_test_command(source):
    # Recursive discovery also imports tests/app, which requires Blender.
    modules = sorted(path.stem for path in (source / "tests").glob("test_*.py"))
    if not modules:
        raise ValueError("No companion unit tests found")
    return [sys.executable, "-B", "-m", "unittest", "-v", *modules]


def package_source_hashes(archive):
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate extension archive members")
        result = {name: hashlib.sha256(package.read(name)).hexdigest()
                  for name in names if name.endswith((".py", ".toml"))}
    if "__init__.py" not in result or "blender_manifest.toml" not in result:
        raise ValueError("Extension archive lacks its root package files")
    return result


def input_files(root):
    """Enumerate regular inputs, propagating incomplete scans and rejecting links."""
    def failed(error):
        raise error
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("Linked validation inputs are not permitted")
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise ValueError("Missing validation input: " + str(root))
    result = []
    for directory, dirs, files in os.walk(root, onerror=failed):
        dirs[:] = sorted(name for name in dirs if name not in ("__pycache__", ".git"))
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("Linked validation inputs are not permitted: " + str(path))
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix in (".pyc", ".pyo"):
                continue
            if not path.is_file():
                raise ValueError("Non-regular validation input: " + str(path))
            result.append(path)
    return result


UPSTREAM_INPUTS = ("tests", "scripts", "docs", "README.md", "USAGE.md", "LICENSE")


def harness_hashes(stage):
    return {path.relative_to(stage).as_posix(): digest(path)
            for name in UPSTREAM_INPUTS for path in input_files(stage / name)}


def addon_hashes(addon):
    return {path.relative_to(addon).as_posix(): digest(path)
            for path in input_files(addon) if path.suffix in (".py", ".toml")}


def stage_upstream_suite(source, archive, target):
    """Copy only test dependencies; the add-on comes exclusively from the ZIP."""
    source, target = source.resolve(), target.resolve()
    if target.is_relative_to(source) or any((p / ".git").exists() for p in (target, *target.parents)):
        raise ValueError("Upstream suite staging must be outside any checkout")
    target.mkdir()
    for name in UPSTREAM_INPUTS:
        original = source / name
        paths = input_files(original)
        if original.is_dir():
            (target / name).mkdir()
        for path in paths:
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    extract_package(archive, target / "sith")
    if addon_hashes(target / "sith") != package_source_hashes(archive):
        raise ValueError("Staged add-on differs from exact package")


def write_upstream_bootstrap(stage, suite, bootstrap, report):
    if suite not in ("core", "app"):
        raise ValueError("Expected upstream core or app suite")
    # Execute the copied helper so the receipt hashes include the code producing it.
    text = ("import runpy\nfrom pathlib import Path\n"
            f"helper = runpy.run_path({str(stage / 'scripts/ci_platform_validation.py')!r})\n"
            f"helper['upstream_suite_entrypoint'](Path({str(stage)!r}), {suite!r}, Path({str(report)!r}))\n")
    with bootstrap.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def upstream_suite_entrypoint(stage, suite, report_path):
    """Observe the unmodified upstream runner in its own Python/Blender process."""
    import importlib
    import runpy
    import traceback
    import unittest

    if sys.flags.optimize or suite not in ("core", "app"):
        raise ValueError("Upstream validation requires assertions and a named suite")
    sys.dont_write_bytecode = True
    stage = stage.resolve()
    sys.path.insert(0, str(stage))
    record = {"schema": "blender-sith-upstream-suite-v1", "status": "failed", "suite": suite,
              "platform": {"system": platform.system(), "machine": platform.machine()},
              "python": sys.version, "executable": sys.executable,
              "source_before_sha256": addon_hashes(stage / "sith"),
              "harness_before_sha256": harness_hashes(stage)}
    runs = []
    original_runner = unittest.TextTestRunner

    def test_ids(tests):
        for item in tests:
            if isinstance(item, unittest.TestSuite):
                yield from test_ids(item)
            else:
                yield item.id()

    class RecordingRunner(original_runner):
        def run(self, tests):
            ids = list(test_ids(tests))
            result = super().run(tests)
            runs.append({"discovered_tests": ids, "tests_run": result.testsRun,
                         "failures": [{"test": test.id(), "traceback": detail} for test, detail in result.failures],
                         "errors": [{"test": test.id(), "traceback": detail} for test, detail in result.errors],
                         "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
                         "expected_failures": [{"test": test.id(), "traceback": detail}
                                               for test, detail in result.expectedFailures],
                         "unexpected_successes": [test.id() for test in result.unexpectedSuccesses],
                         "successful": result.wasSuccessful()})
            return result

    try:
        in_blender = "bpy" in sys.modules
        if suite == "app":
            import bpy
            record["runtime"] = {"route": "blender", "blender": bpy.app.version_string,
                                 "background": bpy.app.background}
            if not in_blender or not bpy.app.background:
                raise ValueError("App validation requires native background Blender")
        else:
            record["runtime"] = {"route": "python"}
            if in_blender:
                raise ValueError("Core validation requires standalone Python")
        addon = importlib.import_module("sith")
        record["loaded_addon"] = str(Path(addon.__file__).resolve())
        if record["loaded_addon"] != str(stage / "sith/__init__.py"):
            raise ValueError("Upstream runner imported a different add-on")
        unittest.TextTestRunner = RecordingRunner
        sys.argv = [str(stage / "tests/run.py"), "--", "--suite", suite]
        runpy.run_path(str(stage / "tests/run.py"), run_name="__main__")
        record["runner_completed"] = True
    except BaseException:
        record["exception"] = traceback.format_exc()
        raise
    finally:
        unittest.TextTestRunner = original_runner
        record["runner_invocations"] = len(runs)
        if len(runs) == 1:
            record.update(runs[0])
        record["source_after_sha256"] = addon_hashes(stage / "sith")
        record["harness_after_sha256"] = harness_hashes(stage)
        record["source_unchanged"] = record["source_before_sha256"] == record["source_after_sha256"]
        record["harness_unchanged"] = record["harness_before_sha256"] == record["harness_after_sha256"]
        if (record.get("runner_completed") and len(runs) == 1 and record.get("successful")
                and record["tests_run"] == len(record["discovered_tests"])
                and record["tests_run"] > len(record["skipped"]) + len(record["expected_failures"])
                and record["source_unchanged"] and record["harness_unchanged"]):
            record["status"] = "passed"
        report_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if record["status"] != "passed":
        raise RuntimeError("Upstream suite did not validate unchanged package bytes")


def validate_upstream_result(report, expected_hashes, stage, suite):
    total = report.get("tests_run")
    skipped, expected = report.get("skipped", []), report.get("expected_failures", [])
    if (report.get("schema") != "blender-sith-upstream-suite-v1" or report.get("status") != "passed"
            or report.get("suite") != suite or report.get("runner_invocations") != 1
            or report.get("runner_completed") is not True or report.get("successful") is not True
            or type(total) is not int or total <= len(skipped) + len(expected)
            or total != len(report.get("discovered_tests", []))
            or report.get("failures") != [] or report.get("errors") != []
            or report.get("unexpected_successes") != []
            or report.get("source_before_sha256") != expected_hashes
            or report.get("source_after_sha256") != expected_hashes
            or report.get("source_unchanged") is not True or report.get("harness_unchanged") is not True
            or report.get("harness_before_sha256") != report.get("harness_after_sha256")
            or not report.get("harness_before_sha256")
            or report.get("loaded_addon") != str((stage / "sith/__init__.py").resolve())):
        raise ValueError("Upstream suite did not verify the complete unchanged exact package: " + suite)


def run_upstream_suites(source, archive, blender, output, evidence, env, system, machine, blender_version):
    stage = output / "upstream-suite"
    stage_upstream_suite(source, archive, stage)
    expected_hashes = package_source_hashes(archive)
    expected_harness = harness_hashes(stage)
    results = {}
    for suite in ("core", "app"):
        report_path = evidence / ("upstream-" + suite + ".json")
        bootstrap = evidence / ("upstream-" + suite + "-bootstrap.py")
        write_upstream_bootstrap(stage, suite, bootstrap, report_path)
        if suite == "core":
            command = [sys.executable, "-B", str(bootstrap)]
        else:
            command = [str(blender), "--background", "--factory-startup", "--offline-mode", "--threads", "2",
                       "--python-exit-code", "1", "--python", str(bootstrap)]
        run_logged(command, "upstream-" + suite, evidence, stage, env, 600)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        validate_upstream_result(report, expected_hashes, stage, suite)
        runtime = report.get("runtime", {})
        if (report.get("platform") != {"system": system, "machine": machine}
                or report["harness_before_sha256"] != expected_harness
                or (suite == "core" and runtime != {"route": "python"})
                or (suite == "app" and (runtime.get("route") != "blender" or runtime.get("background") is not True
                    or runtime.get("blender") not in (blender_version, blender_version + " LTS")))):
            raise ValueError("Upstream suite runtime or test inputs differ from the selected native job")
        results[suite] = {"status": "passed", "tests_run": report["tests_run"],
                          "skipped": report["skipped"], "expected_failures": report["expected_failures"],
                          "report": report_path.name, "report_sha256": digest(report_path),
                          "bootstrap_sha256": digest(bootstrap)}
    return results


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
    receipt = {"schema": "blender-sith-native-macos-ci-v2", "status": "failed",
               "commit": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"),
               "system": platform.system(), "machine": platform.machine(),
               "macos": platform.mac_ver()[0], "expected_blender": BLENDER_VERSION,
               "expected_checks": EXPECTED_TESTS, "dmg_url": BASE_URL + ASSET,
               "dmg_sha256": DMG_SHA256, "scope": "synthetic package validation; no UI or game-runtime claim"}
    mount = output / "mount"
    mount_attempted = False
    try:
        run_logged(companion_test_command(ROOT), "unit-tests", evidence, ROOT / "tests", env, 300)

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
        source_hashes = package_source_hashes(archive)
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
        validate_native_result(launcher_receipt, report, source_hashes)
        receipt["upstream_suites"] = run_upstream_suites(
            ROOT, archive, blender, output, evidence, env, "Darwin", "arm64", BLENDER_VERSION)
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
