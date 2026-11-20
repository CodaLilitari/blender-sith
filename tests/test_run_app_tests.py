# SPDX-License-Identifier: GPL-3.0-or-later
"""The optional launcher must isolate settings and bound actual child processes."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sith_test_launcher', ROOT / 'scripts/run_regression.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def python_executable():
    if Path(sys.executable).name.lower().startswith('python'):
        return sys.executable
    for name in ('python.exe', f'python{sys.version_info.major}.{sys.version_info.minor}', 'python3'):
        candidate = Path(sys.prefix) / 'bin' / name
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError('Standalone Python executable was not found')


class IsolatedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(runner, 'run_isolated', None)),
                        'The launcher must run an existing command with isolated settings')
        temporary = tempfile.TemporaryDirectory(prefix='sith launcher test ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.python = python_executable()

    def test_normal_child_uses_empty_temporary_settings_and_leaves_parent_alone(self):
        report = self.root / 'child.json'
        user_settings = self.root / 'user settings'
        user_settings.mkdir()
        (user_settings / 'startup.blend').write_bytes(b'untouched user settings')
        script = '''import json, os, pathlib, sys
paths = {k: v for k, v in os.environ.items() if k.startswith('BLENDER_USER_')}
assert len(paths) == 4
assert all(pathlib.Path(p).is_dir() and not list(pathlib.Path(p).iterdir()) for p in paths.values())
assert 'PYTHONPATH' not in os.environ and 'PYTHONHOME' not in os.environ
assert 'PYTHONOPTIMIZE' not in os.environ
assert sys.dont_write_bytecode
pathlib.Path(sys.argv[1]).write_text(json.dumps({'paths': paths, 'cwd': os.getcwd()}))
'''
        with patch.dict(os.environ, {'BLENDER_USER_CONFIG': str(user_settings),
                                     'BLENDER_USER_RESOURCES': str(user_settings),
                                     'PYTHONPATH': 'ambient injection', 'PYTHONHOME': 'invalid home',
                                     'PYTHONOPTIMIZE': '2'}):
            self.assertEqual(runner.run_isolated([self.python, '-c', script, str(report)], 10), 0)
            self.assertEqual(os.environ['BLENDER_USER_CONFIG'], str(user_settings))
            self.assertEqual(os.environ['PYTHONHOME'], 'invalid home')
        data = json.loads(report.read_text())
        self.assertEqual(set(data['paths']), {'BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS',
                                             'BLENDER_USER_DATAFILES', 'BLENDER_USER_EXTENSIONS'})
        # getcwd() can use the physical spelling of an aliased temporary directory.
        self.assertTrue(all(Path(p).parent.resolve() == Path(data['cwd']).resolve()
                            for p in data['paths'].values()))
        self.assertFalse(Path(data['cwd']).exists())
        self.assertFalse(Path(data['cwd']).is_relative_to(ROOT))
        self.assertEqual((user_settings / 'startup.blend').read_bytes(), b'untouched user settings')

    def test_returns_actual_nonzero_child_status(self):
        self.assertEqual(runner.run_isolated([self.python, '-c', 'raise SystemExit(23)'], 10), 23)

    def test_executable_path_is_relative_to_caller_before_changing_directory(self):
        marker = self.root / 'relative-executable-ran'
        caller = self.root / 'nested' / 'caller'
        caller.mkdir(parents=True)
        relative = os.path.relpath(self.python, caller)
        if os.name != 'nt':
            portable = caller / 'portable'
            portable.mkdir()
            (portable / 'python').symlink_to(self.python)
            relative = './portable/python'
        code = 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("executed")'
        with contextlib.chdir(caller):
            self.assertEqual(runner.run_isolated([relative, '-c', code, str(marker)], 10), 0)
        self.assertEqual(marker.read_text(), 'executed')

    def test_bare_successful_path_lookup_is_absolutized(self):
        with contextlib.chdir(self.root), patch.object(runner.shutil, 'which', return_value='local-python'):
            self.assertEqual(runner.resolve_executable('local-python'), str(self.root / 'local-python'))

    @unittest.skipIf(os.name == 'nt', 'POSIX empty PATH component')
    def test_empty_path_component_keeps_callers_executable(self):
        (self.root / 'local-python').symlink_to(self.python)
        marker = self.root / 'path-entry-ran'
        code = 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("executed")'
        with contextlib.chdir(self.root), patch.dict(os.environ, {'PATH': ':' + os.defpath}):
            self.assertEqual(runner.run_isolated(['local-python', '-c', code, str(marker)], 10), 0)
        self.assertEqual(marker.read_text(), 'executed')

    def test_post_launch_oserror_cleans_up_owned_process(self):
        from unittest.mock import Mock
        process = Mock(returncode=-9)
        process.wait.side_effect = OSError('synthetic supervision failure')
        with patch.object(runner.subprocess, 'Popen', return_value=process), \
                patch.object(runner, 'terminate_tree', return_value=True) as cleanup, \
                contextlib.redirect_stderr(io.StringIO()):
            result = runner.run_process(['unused'], cwd=self.root, env={}, timeout=1)
        cleanup.assert_called_once_with(process)
        self.assertEqual(result['status'], 'process_failed')
        self.assertEqual(result['error_type'], 'OSError')
        self.assertIs(result['owned_tree_cleanup_succeeded'], True)

    def test_unexpected_supervision_error_cleans_up_before_reraising(self):
        from unittest.mock import Mock
        process = Mock()
        process.wait.side_effect = RuntimeError('synthetic supervision failure')
        with patch.object(runner.subprocess, 'Popen', return_value=process), \
                patch.object(runner, 'terminate_tree', return_value=True) as cleanup:
            with self.assertRaisesRegex(RuntimeError, 'supervision'):
                runner.run_process(['unused'], cwd=self.root, env={}, timeout=1)
        cleanup.assert_called_once_with(process)

    def test_supervision_failure_cannot_report_success(self):
        for code in (0, None):
            with self.subTest(exit_code=code), patch.object(
                    runner, 'run_process', return_value={'status': 'process_failed', 'exit_code': code}):
                self.assertEqual(runner.run_isolated([self.python], 1), 1)

    def test_timeout_stops_child_before_its_later_write(self):
        marker = self.root / 'should-not-exist'
        code = 'import time,pathlib,sys; time.sleep(2); pathlib.Path(sys.argv[1]).write_text("late")'
        started = time.monotonic()
        self.assertEqual(runner.run_isolated([self.python, '-c', code, str(marker)], .2), 124)
        self.assertLess(time.monotonic() - started, 5)
        time.sleep(2.1)
        self.assertFalse(marker.exists())

    def test_timeout_also_stops_spawned_descendant(self):
        ready, marker = self.root / 'ready', self.root / 'descendant-write'
        grandchild = 'import time,pathlib,sys; time.sleep(3); pathlib.Path(sys.argv[1]).write_text("late")'
        parent = ('import subprocess,sys,pathlib,time; '
                  'subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2]]); '
                  'pathlib.Path(sys.argv[3]).write_text("started"); time.sleep(20)')
        self.assertEqual(runner.run_isolated([self.python, '-c', parent, grandchild, str(marker), str(ready)], 1), 124)
        self.assertTrue(ready.is_file(), 'The fixture must actually start its descendant')
        time.sleep(3.1)
        self.assertFalse(marker.exists())

    def test_missing_executable_fails_without_launching_another_program(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.run_isolated([str(self.root / 'missing-blender')], 10), 127)

    def test_invalid_deadlines_are_rejected_before_starting_blender(self):
        for value in ('0', '-1', 'nan', 'inf'):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(argparse.ArgumentTypeError):
                    runner.positive_seconds(value)


class AppEntryTests(unittest.TestCase):
    def test_routes_the_existing_app_suite_with_safe_blender_flags(self):
        entry = ROOT / 'scripts/run_app_tests.py'
        self.assertTrue(entry.is_file(), 'The fork needs its isolated app-suite entry point')
        spec = importlib.util.spec_from_file_location('sith_app_entry', entry)
        app = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'run_regression': runner}):
            spec.loader.exec_module(app)
        with patch.object(runner, 'run_isolated', return_value=23) as run:
            self.assertEqual(app.main(['--blender', './portable/blender', '--timeout', '12']), 23)
        command, timeout = run.call_args.args
        self.assertEqual(timeout, 12)
        self.assertEqual(command, ['./portable/blender', '--background', '--factory-startup',
                                   '--offline-mode', '--disable-autoexec', '--threads', '2',
                                   '--python-exit-code', '1', '--python',
                                   str(ROOT / 'tests/run.py'), '--', '--suite', 'app'])


if __name__ == '__main__':
    unittest.main()
