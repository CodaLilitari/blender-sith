# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the optional MCP companion; SDK/Blender are not required."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sith_mcp_test", ROOT / "mcp_server" / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

# Synthetic two-part character (JK units): a lower box under node 0 and an upper box under node 1
# that overlap by 2 * O at the seat between them. Hand-written text, not the add-on's writer.
R, D, H, O = 0.01, 0.012, 0.02, 0.001


def box(lo, hi):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
               (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    return corners, [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def grid(size):
    """size x size vertices and (size - 1)^2 quads in one flat mesh."""
    vertices = [(0.01 * x, 0.01 * y, 0.0) for y in range(size) for x in range(size)]
    quads = [(r * size + c, r * size + c + 1, (r + 1) * size + c + 1, (r + 1) * size + c)
             for r in range(size - 1) for c in range(size - 1)]
    return vertices, quads


def model_text(meshes, nodes):
    """meshes: [(name, vertices, polygons)]; nodes: [(name, parent, mesh, position)] -> 3DO 2.1 text."""
    lines = ["# Synthetic test model", "SECTION: HEADER", "3DO 2.1", "SECTION: MODELRESOURCE", "MATERIALS 0",
             "SECTION: GEOMETRYDEF", "RADIUS 1", "INSERT OFFSET 0 0 0", "GEOSETS 1", "GEOSET 0", f"MESHES {len(meshes)}"]
    for m, (name, vertices, polygons) in enumerate(meshes):
        lines += [f"MESH {m}", f"NAME {name}", "RADIUS 1", "GEOMETRYMODE 4", "LIGHTINGMODE 3", "TEXTUREMODE 3",
                  f"VERTICES {len(vertices)}"]
        lines += [f"{i}: {x!r} {y!r} {z!r} 0" for i, (x, y, z) in enumerate(vertices)]
        lines += ["TEXTURE VERTICES 1", "0: 0 0", "VERTEX NORMALS"] + [f"{i}: 0 0 1" for i in range(len(vertices))]
        lines += [f"FACES {len(polygons)}"]
        lines += [f"{i}: -1 0 4 3 3 0 {len(p)} " + " ".join(f"{v},0" for v in p) for i, p in enumerate(polygons)]
        lines += ["FACE NORMALS"] + [f"{i}: 0 0 1" for i in range(len(polygons))]
    lines += ["SECTION: HIERARCHYDEF", f"HIERARCHY NODES {len(nodes)}"]
    for i, (name, parent, mesh, (x, y, z)) in enumerate(nodes):
        children = [j for j, node in enumerate(nodes) if node[1] == i]
        siblings = [j for j, node in enumerate(nodes) if node[1] == parent and j > i]
        lines.append(f"{i}: 0x0 0x0 {mesh} {parent} {children[0] if children else -1} {siblings[0] if siblings else -1} "
                     f"{len(children)} {x!r} {y!r} {z!r} 0 0 0 0 0 0 {name}")
    return "\n".join(lines) + "\n"


def two_boxes():
    return model_text([("lower", *box((-R, -D, -H), (R, D, O))), ("upper", *box((-R, -D, -O), (R, D, H)))],
                      [("hip", -1, 0, (0, 0, 0)), ("torso", 0, 1, (0, 0, 0))])


def key_text(tracks, joints=2, frames=10):
    """tracks: [(node, [(frame, (x, y, z))])] -> text KEY with position-only entries."""
    lines = ["# Synthetic test KEY", "SECTION: HEADER", "FLAGS 0x0", "TYPE 0x0", f"FRAMES {frames}", "FPS 15",
             f"JOINTS {joints}", "SECTION: KEYFRAME NODES", f"NODES {len(tracks)}"]
    for node, entries in tracks:
        lines += [f"NODE {node}", f"MESH NAME n{node}", f"ENTRIES {len(entries)}"]
        lines += [f"{k}: {frame!r} 0x0 {x!r} {y!r} {z!r} 0 0 0\n   0 0 0 0 0 0"
                  for k, (frame, (x, y, z)) in enumerate(entries)]
    return "\n".join(lines) + "\n"


def lift_key(lift, start=2):
    """The upper box seated until ``start``, then lifted by ``lift`` along +z."""
    return key_text([(0, [(0, (0, 0, 0))]), (1, [(0, (0, 0, 0)), (start, (0, 0, lift))])])


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sith-mcp-test-")
        self.root = Path(self.temp.name).resolve()
        self.read = self.root / "inputs"
        self.read.mkdir()
        self.input = self.read / "sample.3do"
        self.input.write_text("not a model", encoding="utf-8")
        addon = self.root / "checkout-source" / "sith"
        addon.mkdir(parents=True)
        (addon.parent / "LICENSE").write_text("fixture")
        (addon / "blender_manifest.toml").write_text('id = "sith"\nversion = "0.0.7"\n')
        (addon / "__init__.py").write_text("# minimal trusted unit-test addon\n", encoding="utf-8")
        self.config = {"python": str(Path(sys.executable).resolve()), "read_roots": [str(self.read)],
                       "addon": str(addon), "job_root": str(self.root / "jobs"), "timeout": 30}
        self.service = server.Service(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_native_surface_budget_counts_polygons_before_conversion(self):
        from types import SimpleNamespace
        worker = server.load_helper("sith_worker", ROOT / "mcp_server" / "worker.py")
        geo = SimpleNamespace(meshes=[SimpleNamespace(faces=[
            SimpleNamespace(vertexIdxs=range(n)) for n in (2, 3, 4, 5)])])
        self.assertEqual(worker.geoset_topology(1, geo), {"index": 1, "meshes": 1, "triangles": 1, "quads": 1,
                         "ngons": 1, "fewer_than_three_corners": 1, "triangle_equivalents": 6})

    def test_info_does_not_launch_runtime(self):
        info = self.service.info()
        self.assertEqual(info["transport"], "stdio")
        self.assertEqual(len(info["tools"]), 12)
        self.assertTrue(set(server.AUDIT_TOOLS) <= set(info["tools"]))
        self.assertIn("No game-runtime compatibility claim", info["limits"])
        self.assertTrue(any("human or a game test" in line for line in info["limits"]))
        self.assertEqual(info["key_folder_limits"], {"files": 512, "bytes": 64 * 1024 * 1024})
        self.assertFalse(list((self.root / "jobs").iterdir()))

    def test_allowlist_accepts_exact_input(self):
        self.assertEqual(self.service.source(str(self.input)), self.input)

    def test_outside_and_prefix_neighbor_rejected(self):
        other = self.root / "inputs-neighbor"
        other.mkdir()
        path = other / "sample.3do"
        path.write_text("x")
        with self.assertRaisesRegex(ValueError, "outside"):
            self.service.source(str(path))

    def test_traversal_relative_and_wrong_type_rejected(self):
        for path in ("sample.3do", str(self.read / ".." / "inputs" / "sample.3do"), str(self.read)):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.service.source(path)
        text = self.read / "script.py"
        text.write_text("print('x')")
        with self.assertRaisesRegex(ValueError, ".3do"):
            self.service.source(str(text))

    def test_symlink_escape_rejected(self):
        link = self.read / "link.3do"
        try:
            link.symlink_to(self.input)
        except OSError as exc:
            self.skipTest("Host cannot create symlinks: " + str(exc))
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.service.source(str(link))

    def test_key_is_checked_before_job_creation(self):
        outside = self.root / "outside.key"
        outside.write_text("fixture")
        for key_path, target in ((str(outside), "JKDF2"), (str(self.input), "MOTS"), (str(self.input), "OTHER")):
            with self.subTest(key_path=key_path, target=target), self.assertRaises(ValueError):
                self.service.model_job(str(self.input), False, key_path, target)
        self.assertFalse(list(self.service.job_root.iterdir()))

    @unittest.skipUnless(os.name == "nt", "Windows App Execution Aliases")
    def test_store_exception_only_for_configured_blender_alias(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        folder = self.root / "Microsoft" / "WindowsApps"
        folder.mkdir(parents=True)
        alias = folder / "blender-launcher.exe"
        alias.touch()
        original = Path.lstat
        tag = SimpleNamespace(st_mode=0, st_file_attributes=0x400, st_reparse_tag=0x8000001B)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            with patch.object(Path, "lstat", lambda p: tag if p == alias else original(p)):
                self.assertEqual(server.checked_path(str(alias), blender_alias=True), alias)
                with self.assertRaisesRegex(ValueError, "Symlink"):
                    server.checked_path(str(alias))
                tag.st_reparse_tag = 0xA000000C  # conventional symlink
                with self.assertRaisesRegex(ValueError, "Symlink"):
                    server.checked_path(str(alias), blender_alias=True)

    def test_installed_layout_cannot_silently_package_server_checkout(self):
        (self.service.addon.parent / "LICENSE").unlink()
        with self.assertRaisesRegex(ValueError, "configured source"):
            self.service.package()

    def test_size_limit(self):
        from unittest.mock import patch
        with patch.object(server, "MAX_INPUT", 4), self.assertRaisesRegex(ValueError, "64 MiB"):
            self.service.source(str(self.input))

    def test_configuration_rejects_unbounded_or_ambiguous_options(self):
        for updates in ({"blender": sys.executable}, {"timeout": float("nan")}, {"timeout": 0},
                        {"timeout": 1801}, {"threads": True}, {"threads": 65}, {"shell": "x"}, {"read_roots": []}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                server.Service({**self.config, **updates})

    def test_job_root_git_ancestor_rejected_at_start_and_runtime(self):
        root = self.root / "checkout"
        root.mkdir()
        (root / ".git").write_text("gitdir: elsewhere")
        with self.assertRaisesRegex(ValueError, "Git"):
            server.Service({**self.config, "job_root": str(root / "outputs")})
        (self.root / "jobs" / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "Git"):
            with self.service.job("test"):
                pass

    def test_jobs_unique_and_serialized(self):
        with self.service.job("test") as first:
            with self.assertRaisesRegex(RuntimeError, "running"):
                with self.service.job("test"):
                    pass
        with self.service.job("test") as second:
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir())

    def test_environment_isolates_and_clears_python_injection(self):
        from unittest.mock import patch
        with self.service.job("test") as job, patch.dict(os.environ, {"PYTHONPATH": "bad", "PYTHONHOME": "bad", "PYTHONOPTIMIZE": "2", "BLENDER_USER_RESOURCES": "ambient resources"}):
            env = self.service.environment(job)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("PYTHONOPTIMIZE", env)
        self.assertFalse("BLENDER_USER_RESOURCES" in env, "Ambient Blender resource override survived")
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertTrue(Path(env["BLENDER_USER_CONFIG"]).is_relative_to(job))

    def test_process_output_is_bounded_and_captured(self):
        from unittest.mock import patch
        # Check truncation without benchmarking the host's external filesystem.
        with patch.object(server, "MAX_LOG", 4096), self.service.job("test") as job:
            result = self.service.execute([sys.executable, "-c", "import sys; sys.stdout.write('x'*8192); sys.stderr.write('err')"], job)
        self.assertEqual(result["status"], "completed")
        self.assertEqual((job / "stdout.log").stat().st_size, 4096)
        self.assertEqual((job / "stderr.log").read_text(), "err")

    def test_timeout_is_failure_with_cleanup_receipt(self):
        with self.service.job("test") as job:
            result = self.service.execute([sys.executable, "-c", "import time; time.sleep(60)"], job, timeout=0.2)
        self.assertEqual(result["status"], "timeout")
        self.assertIs(result["owned_tree_cleanup_succeeded"], True)
        self.assertEqual(json.loads((job / "process.json").read_text())["status"], "timeout")

    def test_nonzero_process_is_not_success(self):
        with self.service.job("test") as job:
            result = self.service.execute([sys.executable, "-c", "raise SystemExit(7)"], job)
        self.assertEqual(result["status"], "process_failed")
        self.assertEqual(result["exit_code"], 7)

    def test_success_exit_without_report_is_not_success(self):
        from unittest.mock import patch
        with patch.object(self.service, "runtime_command", return_value=[sys.executable, "-c", "pass"]):
            result = self.service.model_job(str(self.input), False)
        self.assertEqual(result["status"], "invalid_report")
        self.assertEqual(self.input.read_text(), "not a model")

    def test_blender_command_disables_autoexec(self):
        self.service.route = "blender"
        command = self.service.runtime_command(Path("worker.py"), Path("request.json"))
        self.assertIn("--factory-startup", command)
        self.assertIn("--disable-autoexec", command)
        self.assertIn("--background", command)
        self.assertIn("--offline-mode", command)

    def test_regression_blender_command_uses_offline_mode(self):
        from contextlib import redirect_stdout
        import io
        from unittest.mock import Mock, patch
        process = Mock(returncode=0)
        process.wait.return_value = 0
        runner_processes = Mock(wraps=subprocess)
        runner_processes.Popen.return_value = process
        with patch.object(server.runner, "subprocess", runner_processes), redirect_stdout(io.StringIO()):
            code = server.runner.main(["--blender", str(Path(sys.executable).resolve()), "--addon", str(self.service.addon),
                                       "--output", str(self.root / "regression-output")])
        # Deliberately no child report: an otherwise successful exit still fails.
        self.assertEqual(code, 1)
        self.assertIn("--offline-mode", runner_processes.Popen.call_args.args[0])

    def package_with_fake_validator(self, validation_status="completed"):
        from unittest.mock import patch
        calls = []

        def execute(command, job, **kwargs):
            calls.append((command, job))
            if "--source-dir" in command:
                output = job / "package"
                output.mkdir()
                archive = output / "sith-test.zip"
                archive.write_bytes(b"synthetic-test-archive")
                (output / "sith-test.receipt.json").write_text(json.dumps({"archive": {
                    "name": archive.name, "sha256": server.runner.digest(archive), "size": archive.stat().st_size}}))
                return {"status": "completed", "exit_code": 0}
            return {"status": validation_status, "exit_code": 0 if validation_status == "completed" else 1}

        with patch.object(self.service, "execute", side_effect=execute):
            return self.service.package(), calls

    def test_build_reads_the_configured_checkout_bytes(self):
        import zipfile
        result = self.service.package()
        self.assertEqual(result["status"], "passed", result)
        with zipfile.ZipFile(result["archive"]["path"]) as archive:
            self.assertEqual(archive.read("__init__.py"), b"# minimal trusted unit-test addon\n")
            self.assertEqual(archive.read("LICENSE"), b"fixture")
            self.assertIn(b'0.0.7', archive.read("blender_manifest.toml"))

    def test_package_python_route_explicitly_skips_official_validator(self):
        result, calls = self.package_with_fake_validator()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["validator"]["status"], "not_run")
        self.assertIn("no configured Blender CLI", result["validator"]["reason"])
        self.assertEqual(len(calls), 1)
        command, _ = calls[0]
        self.assertEqual(command[command.index("--source-dir") + 1], str(self.service.addon.parent))
        self.assertEqual(result["source"], str(self.service.addon))

    def test_package_blender_route_validates_exact_archive_in_isolated_job(self):
        self.service.route = "blender"
        result, calls = self.package_with_fake_validator()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["validator"]["status"], "passed")
        self.assertEqual(len(calls), 2)
        command, validation_job = calls[1]
        self.assertEqual(command[-4:], ["--command", "extension", "validate", result["archive"]["path"]])
        self.assertIn("--offline-mode", command)
        self.assertIn("--disable-autoexec", command)
        self.assertEqual(validation_job.parent, calls[0][1])
        self.assertNotEqual(validation_job, calls[0][1])

    def test_package_validator_failure_cannot_pass(self):
        self.service.route = "blender"
        for status in ("process_failed", "timeout", "launch_failed"):
            with self.subTest(status=status):
                result, _ = self.package_with_fake_validator(status)
                self.assertEqual(result["status"], "validation_failed")
                self.assertEqual(result["validator"]["status"], status)
                self.assertIn("archive", result)

    def test_report_limit(self):
        path = self.root / "report.json"
        path.write_text("[]")
        with self.assertRaises(ValueError):
            server.bounded_json(path)
        path.write_bytes(b" " * (server.MAX_REPORT + 1))
        with self.assertRaises(ValueError):
            server.bounded_json(path)


class AuditInputTests(unittest.TestCase):
    """KEY folders and audit parameters are checked before any job exists."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sith-mcp-audit-input-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.read = self.root / "inputs"
        self.keys = self.read / "key"
        self.keys.mkdir(parents=True)
        for name in ("b.key", "A.key", "c.KEY"):
            (self.keys / name).write_text(lift_key(0.0))
        (self.keys / "notes.txt").write_text("not a KEY")
        (self.keys / "nested").mkdir()
        (self.keys / "nested" / "inner.key").write_text(lift_key(0.0))
        self.model = self.read / "model.3do"
        self.model.write_text(two_boxes())
        self.service = server.Service({"python": str(Path(sys.executable).resolve()), "read_roots": [str(self.read)],
                                       "addon": str(ROOT / "sith"), "job_root": str(self.root / "jobs"), "timeout": 30})

    def assertNoJob(self):
        self.assertFalse(list(self.service.job_root.iterdir()))

    def test_folder_lists_regular_key_files_in_order_and_counts_links(self):
        try:
            (self.keys / "link.key").symlink_to(self.keys / "b.key")
        except OSError as exc:
            self.skipTest("Host cannot create symlinks: " + str(exc))
        listing = self.service.key_folder(str(self.keys))
        self.assertEqual([name for name, _, _ in listing["files"]], ["A.key", "b.key", "c.KEY"])
        self.assertEqual(listing["skipped"], {"links": 1, "not_regular": 0})
        self.assertEqual(listing["files"][0][2], (self.keys / "A.key").stat().st_size)
        subset = self.service.key_folder(str(self.keys), ["C.key", "a.KEY"])
        self.assertEqual([name for name, _, _ in subset["files"]], ["A.key", "c.KEY"])
        self.assertNoJob()

    def test_folder_routes_names_and_case_clashes_are_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "x.key").write_text(lift_key(0.0))
        cases = [(str(outside), None), (str(self.model), None), (str(self.keys / "missing"), None),
                 ("key", None), (str(self.keys), ["missing.key"]), (str(self.keys), ["nested/inner.key"]),
                 (str(self.keys), ["..\\b.key"]), (str(self.keys), ["b.key", "B.KEY"]), (str(self.keys), []),
                 (str(self.keys), ["notes.txt"]), (str(self.keys), "b.key"), (str(self.read), None)]
        for folder, names in cases:
            with self.subTest(folder=folder, names=names), self.assertRaises(ValueError):
                self.service.key_folder(folder, names)
        (self.keys / "B.key").write_text(lift_key(0.0))
        if len({p.name for p in self.keys.iterdir()}) == 7:   # a case-sensitive file system
            with self.assertRaisesRegex(ValueError, "differ only in case"):
                self.service.key_folder(str(self.keys))
            self.assertEqual(len(self.service.key_folder(str(self.keys), ["A.key"])["files"]), 1)
        self.assertNoJob()

    def test_folder_count_and_size_limits(self):
        from unittest.mock import patch
        with patch.object(server, "MAX_KEY_FILES", 2), self.assertRaisesRegex(ValueError, "at most 2"):
            self.service.key_folder(str(self.keys))
        with patch.object(server, "MAX_KEY_BYTES", 10), self.assertRaisesRegex(ValueError, "64 MiB"):
            self.service.key_folder(str(self.keys))
        self.assertNoJob()

    def test_folder_symlink_route_is_refused(self):
        link = self.read / "linked"
        try:
            link.symlink_to(self.keys, target_is_directory=True)
        except OSError as exc:
            self.skipTest("Host cannot create symlinks: " + str(exc))
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.service.key_folder(str(link))

    def test_parameters_are_checked_before_job_creation(self):
        model, keys, stand = str(self.model), str(self.keys), str(self.keys / "A.key")
        two = [{"name": "A", "model_path": model, "key_dir": keys}, {"name": "B", "model_path": model, "key_dir": keys}]
        calls = [
            ("target must be one of", lambda: self.service.audit_budget(model, "IJIM2")),
            ("must be a .3do file", lambda: self.service.audit_budget(stand)),
            ("step must be a finite number", lambda: self.service.audit_articulation(model, keys, step=0)),
            ("step must be a finite number", lambda: self.service.audit_articulation(model, keys, step=float("nan"))),
            ("step must be a finite number", lambda: self.service.audit_articulation(model, keys, step=True)),
            ("joints must be one of", lambda: self.service.audit_articulation(model, keys, joints="all")),
            ("scale must be a finite number", lambda: self.service.audit_articulation(model, keys, scale=0)),
            ("seat_offsets must be true or false", lambda: self.service.audit_articulation(model, keys, seat_offsets="yes")),
            ("geoset must be a whole number", lambda: self.service.audit_articulation(model, keys, geoset=-1)),
            ("state zero_rotations", lambda: self.service.audit_articulation(model, keys, reference_key=stand)),
            ("zero_rotations needs reference_key", lambda: self.service.audit_articulation(model, keys, zero_rotations=True)),
            ("reference_frame needs reference_key", lambda: self.service.audit_articulation(model, keys, reference_frame=3.0)),
            ("zero_rotations must be true or false",
             lambda: self.service.audit_articulation(model, keys, reference_key=stand, zero_rotations=1)),
            ("must be a .key file", lambda: self.service.audit_articulation(model, keys, reference_key=model, zero_rotations=True)),
            ("give key_dir", lambda: self.service.audit_segmentation(model, keys=["A.key"])),
            ("detail must be one of", lambda: self.service.audit_segmentation(model, detail="all")),
            ("floor must be", lambda: self.service.audit_ground(model, keys, floor="moon")),
            ("use floor='engine'", lambda: self.service.audit_ground(model, keys, floor="rest", crouch_keys=["A.key"])),
            ("use floor='engine'", lambda: self.service.audit_ground(model, keys, floor=0.0, move_size=0.07)),
            ("not among the selected KEYs", lambda: self.service.audit_ground(model, keys, crouch_keys=["kycrchgn.key"])),
            ("nonzero", lambda: self.service.audit_ground(model, keys, travel={"A.key": [0, 0]})),
            ("nonzero", lambda: self.service.audit_ground(model, keys, travel={"A.key": [0, 1, 0]})),
            ("not among the selected KEYs", lambda: self.service.audit_ground(model, keys, travel={"missing.key": [0, 1]})),
            ("contacts must list", lambda: self.service.audit_ground(model, keys, contacts=[])),
            ("contacts must list", lambda: self.service.audit_ground(model, keys, contacts=[True])),
            ("target must be one of", lambda: self.service.lint_keys(keys, target="IJIM")),
            ("pops must be true or false", lambda: self.service.lint_keys(keys, pops=None)),
            ("regular file", lambda: self.service.lint_keys(keys, model_path=keys)),
            ("2 to 8 characters", lambda: self.service.audit_track_slots(two[:1])),
            ("given twice", lambda: self.service.audit_track_slots([two[0], {**two[1], "name": "a"}])),
            ("Each character is", lambda: self.service.audit_track_slots([two[0], {**two[1], "x": 1}])),
            ("custom must be", lambda: self.service.audit_track_slots(two, custom="all")),
            ("custom slots must be", lambda: self.service.audit_track_slots(two, custom=[-1])),
        ]
        for index, (message, call) in enumerate(calls):
            with self.subTest(case=index, message=message), self.assertRaisesRegex(ValueError, message):
                call()
        self.assertNoJob()

    def test_reference_rule_is_explicit(self):
        self.assertEqual(self.service.reference_input(None, 0.0, None), (None, {"pose": "rest"}))
        self.assertEqual(self.service.reference_input(None, 0, False)[1], {"pose": "rest"})
        path, pose = self.service.reference_input(str(self.keys / "A.key"), 2, True)
        self.assertEqual(path, self.keys / "A.key")
        self.assertEqual(pose, {"pose": "key", "name": "A.key", "frame": 2.0, "zero_rotations": True})
        with self.assertRaisesRegex(ValueError, "state zero_rotations"):
            self.service.reference_input(str(self.keys / "A.key"), 0.0, None)

    def test_job_inputs_are_bounded_together(self):
        from unittest.mock import patch
        listing = self.service.key_folder(str(self.keys))
        with patch.object(server, "MAX_JOB_INPUT", 100), self.assertRaisesRegex(ValueError, "256 MiB"):
            self.service.audit_job("lint", {"target": "JKDF2", "pops": False}, {"lint": server.AUDIT_STATUSES["lint"]},
                                   model=self.model, keys=listing)
        self.assertNoJob()


class AuditJobTests(unittest.TestCase):
    """Each audit through the fixed worker, with the real add-on in this Python (no bpy import)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sith-mcp-audit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.read = self.root / "inputs"
        self.keys = self.read / "key"
        self.keys.mkdir(parents=True)
        self.model = self.read / "boxes.3do"
        self.model.write_text(two_boxes())
        (self.keys / "lift.key").write_text(lift_key(0.004))
        (self.keys / "still.key").write_text(lift_key(0.0))
        (self.keys / "stand.key").write_text(key_text([(0, [(0, (0, 0, 0))]), (1, [(0, (0, 0, 0))])]))
        # Track 2 lies past JOINTS 2: the strict loader refuses it, the engine (and the lint) read it.
        (self.keys / "past.key").write_text(key_text([(0, [(0, (0, 0, 0))]), (2, [(0, (0, 0, 0))])]))
        self.before = {p: p.read_bytes() for p in self.read.rglob("*") if p.is_file()}
        self.service = server.Service({"python": str(Path(sys.executable).resolve()), "read_roots": [str(self.read)],
                                       "addon": str(ROOT / "sith"), "job_root": str(self.root / "jobs"), "timeout": 120})

    def tearDown(self):
        for path, data in self.before.items():
            self.assertEqual(path.read_bytes(), data, f"{path.name} changed")

    def assertPassed(self, result):
        self.assertEqual(result["status"], "passed", result.get("diagnostic") or result)
        job = Path(result["job_directory"])
        self.assertEqual(job.parent, self.service.job_root)
        self.assertTrue(result["source_unchanged"])
        self.assertEqual(json.loads((job / "receipt.json").read_text()), result)
        self.assertEqual(result["report"]["sha256"], sha256(job / "report.json"))
        self.assertEqual(result["audit"]["schema"], server.AUDIT_SCHEMA)
        self.assertLessEqual((job / "result.json").stat().st_size, server.MAX_REPORT)
        full = json.loads((job / "report.json").read_text())
        self.assertEqual(full["kind"], result["audit_kind"])
        return result["audit"], full

    def test_budget_counts_the_snapshot_and_returns_warnings(self):
        big = self.read / "grid.3do"
        big.write_text(model_text([("grid", *grid(25))], [("root", -1, 0, (0, 0, 0))]))
        audit, full = self.assertPassed(self.service.audit_budget(str(big), "MOTS"))
        budget = audit["budget"]
        self.assertEqual(budget["status"], "over_limits")
        self.assertEqual(budget["issue_counts"], {"too_many_vertices": 1, "too_many_faces": 1})
        self.assertEqual(budget["warnings"][0], "Mesh 0 'grid' has 625 vertices; MOTS allows 512 per mesh (113 over) in geoset 0")
        self.assertEqual(full["budget"]["geosets"][0]["vertices"], 625)
        self.assertEqual(audit["full_report"], "report.json")
        self.assertEqual(audit["inputs"]["model"]["hierarchy_nodes"], 1)

    def test_articulation_and_seat_offsets_with_a_stand_reference(self):
        result = self.service.audit_articulation(str(self.model), str(self.keys), step=1.0,
                                                 reference_key=str(self.keys / "stand.key"), zero_rotations=True)
        audit, full = self.assertPassed(result)
        manifest = result["inputs"]["keys"]
        self.assertEqual([f["name"] for f in manifest["files"]], ["lift.key", "past.key", "stand.key", "still.key"])
        for item in manifest["files"]:
            self.assertEqual(item["sha256"], sha256(self.keys / item["name"]))
        self.assertEqual(result["inputs"]["model"]["sha256"], sha256(self.model))
        self.assertEqual(result["inputs"]["reference_key"]["sha256"], sha256(self.keys / "stand.key"))
        self.assertEqual(audit["inputs"]["keys"]["loaded"], 3)
        self.assertEqual([s["key"] for s in audit["inputs"]["keys"]["skipped"]], ["past.key"])
        self.assertIn("stand.key frame 0: KEY positions with zero rotation", audit["inputs"]["reference"])
        gaps = audit["articulation"]
        self.assertEqual(gaps["status"], "review")
        self.assertEqual([row["name"] for row in gaps["keys_with_daylight"]], ["lift.key"])
        self.assertTrue(all(f["key"] == "lift.key" for f in gaps["findings"] if f["code"] == "daylight"))
        seats = audit["seat_offsets"]
        self.assertEqual(seats["status"], "off_seat")
        self.assertEqual([row["name"] for row in seats["keys_off_seat"]], ["lift.key"])
        self.assertAlmostEqual(seats["keys_off_seat"][0]["largest"]["offset"], 0.004, places=9)
        self.assertEqual(len(full["articulation"]["keys"]), 3)

    def test_articulation_key_subset_and_rows_are_bounded(self):
        for i in range(70):
            (self.keys / f"lift{i:02d}.key").write_text(lift_key(0.003 + i * 1e-5))
        result = self.service.audit_articulation(str(self.model), str(self.keys), step=5.0)
        audit, full = self.assertPassed(result)
        self.assertEqual(audit["seat_offsets"]["keys_off_seat_total"], 71)
        self.assertEqual(len(audit["seat_offsets"]["keys_off_seat"]), 64)
        self.assertEqual([row["name"] for row in audit["seat_offsets"]["keys_off_seat"][:2]], ["lift.key", "lift69.key"])
        self.assertEqual(len(full["seat_offsets"]["keys"]), 73)
        subset = self.service.audit_articulation(str(self.model), str(self.keys), keys=["still.key", "LIFT.key"],
                                                 seat_offsets=False)
        audit, _ = self.assertPassed(subset)
        self.assertNotIn("seat_offsets", audit)
        self.assertEqual([f["name"] for f in subset["inputs"]["keys"]["files"]], ["lift.key", "still.key"])
        self.assertEqual(audit["articulation"]["totals"]["keys"], 2)

    def test_lint_reads_tracks_the_strict_loader_refuses(self):
        result = self.service.lint_keys(str(self.keys), model_path=str(self.model))
        audit, full = self.assertPassed(result)
        lint = audit["lint"]
        self.assertEqual(lint["status"], "incompatible")
        self.assertEqual(lint["totals"]["keys"], 4)
        self.assertEqual(lint["keys_flagged"][0]["key"], "past.key")
        self.assertEqual(lint["keys_flagged"][0]["status"], "incompatible")
        self.assertIn("track_past_joints", lint["issue_counts"])
        self.assertEqual(lint["recommendations"]["joints_64"], 4)
        self.assertIn("past.key", full["lint"]["reports"])
        self.assertEqual(full["lint"]["reports"]["past.key"]["header"]["status"], "incompatible")

    def test_ground_below_the_rest_floor(self):
        (self.keys / "sink.key").write_text(key_text([(0, [(0, (0, 0, -0.01))]), (1, [(0, (0, 0, 0))])]))
        result = self.service.audit_ground(str(self.model), str(self.keys), keys=["sink.key", "still.key"],
                                           floor="rest", contacts=["hip"], travel={"STILL.key": [0, 1]})
        audit, full = self.assertPassed(result)
        ground = audit["ground"]
        self.assertEqual(ground["status"], "review")
        below = [f for f in ground["findings"] if f["code"] == "below_floor"]
        self.assertEqual([f["sequence"] for f in below], ["sink.key"])
        self.assertAlmostEqual(below[0]["depth"], 0.01, places=9)
        self.assertEqual(ground["floor_source"], "rest_pose")
        self.assertEqual(audit["parameters"]["travel"], {"still.key": [0.0, 1.0]})
        self.assertEqual(ground["sequences_total"], 2)

    def test_ground_engine_floor_with_crouch_keys(self):
        result = self.service.audit_ground(str(self.model), str(self.keys), keys=["still.key"], crouch_keys=["still.key"])
        audit, _ = self.assertPassed(result)
        self.assertAlmostEqual(audit["ground"]["crouch_floor"], -0.075, places=9)   # insert z 0 - (0.065 + 0.01)
        self.assertAlmostEqual(audit["ground"]["floor"], -0.07, places=9)           # insert z 0 - (0.065 + 0.005)

    def test_segmentation_with_rest_and_stand_references(self):
        audit, _ = self.assertPassed(self.service.audit_segmentation(str(self.model)))
        self.assertIn(audit["segmentation"]["status"], {"clean", "review", "suspect"})
        self.assertEqual(audit["segmentation"]["reference"], "rest")
        result = self.service.audit_segmentation(str(self.model), reference_key=str(self.keys / "stand.key"),
                                                 zero_rotations=True, key_dir=str(self.keys), keys=["lift.key"], step=1.0)
        audit, _ = self.assertPassed(result)
        self.assertEqual(audit["segmentation"]["reference"], "given")
        self.assertEqual(audit["segmentation"]["poses"], 10)
        self.assertEqual(audit["inputs"]["keys"]["poses"], 10)

    def test_track_slots_collide_in_a_shared_pack(self):
        other = self.read / "other"
        other.mkdir()
        (other / "lift.key").write_text(lift_key(0.002))
        characters = [{"name": "First", "model_path": str(self.model), "key_dir": str(self.keys), "keys": ["lift.key"]},
                      {"name": "Second", "model_path": str(self.model), "key_dir": str(other)}]
        result = self.service.audit_track_slots(characters, custom=0)
        audit, _ = self.assertPassed(result)
        slots = audit["track_slots"]
        self.assertEqual(slots["status"], "incompatible")
        self.assertEqual(slots["findings"][0]["code"], "slot_collision")
        self.assertEqual(slots["findings"][0]["slot"], 1)
        self.assertEqual([c["name"] for c in result["inputs"]["characters"]], ["First", "Second"])
        self.assertEqual(result["inputs"]["characters"][1]["keys"]["files"][0]["sha256"], sha256(other / "lift.key"))

    def test_unreadable_model_fails_with_a_diagnostic(self):
        bad = self.read / "bad.3do"
        bad.write_text("not a model")
        result = self.service.audit_budget(str(bad))
        self.assertEqual(result["status"], "process_failed")
        self.assertIn("3DO", result["diagnostic"])
        self.assertNotIn("audit", result)
        self.assertEqual(result["progress"]["stage"], "loading model")

    def test_timeout_keeps_the_progress_record(self):
        from unittest.mock import patch
        script = ("import json, sys, time; from pathlib import Path; r = json.loads(Path(sys.argv[1]).read_text()); "
                  "Path(r['progress']).write_text(json.dumps({'stage': 'joint gaps', 'keys_done': 1, 'keys_total': 3})); "
                  "time.sleep(60)")
        self.service.timeout = 1
        with patch.object(self.service, "runtime_command",
                          side_effect=lambda worker, request: [sys.executable, "-c", script, str(request)]):
            result = self.service.audit_articulation(str(self.model), str(self.keys))
        self.assertEqual(result["status"], "timeout")
        self.assertIs(result["owned_tree_cleanup_succeeded"], True)
        self.assertEqual(result["progress"]["keys_done"], 1)
        self.assertNotIn("audit", result)

    def test_result_schema_status_and_full_report_are_required(self):
        from unittest.mock import patch
        good = {"schema": server.AUDIT_SCHEMA, "kind": "budget", "budget": {"status": "within_limits"}}
        cases = [({**good, "budget": {"status": "fine"}}, True), ({**good, "kind": "lint"}, True),
                 ({**good, "schema": "other"}, True), (good, False), ({"schema": server.AUDIT_SCHEMA, "kind": "budget"}, True)]
        for payload, write_report in cases:
            script = ("import json, sys; from pathlib import Path; r = json.loads(Path(sys.argv[1]).read_text()); "
                      f"Path(r['result']).write_text(json.dumps({payload!r}))"
                      + ("; Path(r['report']).write_text('{}')" if write_report else ""))
            with self.subTest(payload=payload), patch.object(
                    self.service, "runtime_command",
                    side_effect=lambda worker, request, script=script: [sys.executable, "-c", script, str(request)]):
                self.assertEqual(self.service.audit_budget(str(self.model))["status"], "invalid_report")


class AuditWorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = server.load_helper("sith_audit_worker", ROOT / "mcp_server" / "audit_worker.py")

    def test_plain_values_are_json_safe(self):
        import numpy as np
        notes = {}
        value = self.worker.plain({"a": (np.int64(3), np.float32(0.5)), "b": [float("inf"), np.nan], "c": {1, 0},
                                   "d": np.arange(3), 4: np.bool_(True)}, notes)
        self.assertEqual(value, {"a": [3, 0.5], "b": [None, None], "c": [0, 1], "d": [0, 1, 2], "4": True})
        self.assertEqual(notes, {"nonfinite": 2})
        json.dumps(value, allow_nan=False)

    def test_cut_keeps_rows_and_totals(self):
        summary = {}
        self.worker.cut(summary, "rows", range(100))
        self.assertEqual((len(summary["rows"]), summary["rows_total"]), (self.worker.ROWS, 100))
        entry = {"faces": list(range(self.worker.FACES + 5))}
        self.worker.faces(entry, "faces")
        self.assertEqual((len(entry["faces"]), entry["faces_total"]), (self.worker.FACES, self.worker.FACES + 5))

    def test_worker_never_imports_blender(self):
        import ast
        tree = ast.parse((ROOT / "mcp_server" / "audit_worker.py").read_text(encoding="utf-8"))
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module}
        self.assertFalse(imported & {"bpy", "bmesh", "mathutils", "bpy_extras"}, imported)
        self.assertEqual(set(self.worker.AUDITS), {"budget", "segmentation", "articulation", "ground", "lint",
                                                   "track_slots"})


if __name__ == "__main__":
    unittest.main()
