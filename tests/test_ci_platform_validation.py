# SPDX-License-Identifier: GPL-3.0-or-later
"""Receipt and isolated upstream-suite tests; no Blender or network required."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/ci_platform_validation.py'
spec = importlib.util.spec_from_file_location('ci_platform_test', SCRIPT)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class PlatformValidationTests(unittest.TestCase):
    def test_macos_receipt_is_bound_to_exact_package(self):
        hashes = {'__init__.py': 'a' * 64, 'blender_manifest.toml': 'b' * 64}
        receipt = {'status': 'passed', 'source_unchanged': True,
                   'source_before_sha256': hashes, 'source_after_sha256': hashes,
                   'platform': {'system': 'Darwin', 'machine': 'arm64'},
                   'runtime': {'route': 'blender', 'blender': ci.BLENDER_VERSION},
                   'regression': {'passed': 29, 'failed': 0, 'addon_source_unchanged': True}}
        report = {'schema': 'blender-sith-agentic-regression-v2',
                  'passed': 29, 'failed': 0, 'addon_source_unchanged': True,
                  'addon_sha256': hashes, 'tests': [{'passed': True} for _ in range(29)]}
        ci.validate_native_result(receipt, report, hashes)
        for target, field in ((receipt, 'source_before_sha256'), (receipt, 'source_after_sha256'),
                              (report, 'addon_sha256'), (report, 'addon_source_unchanged'), (report, 'schema')):
            invalid = copy.deepcopy(target)
            invalid[field] = ({} if field.endswith('sha256') else
                              'blender-sith-agentic-regression-v1' if field == 'schema' else False)
            with self.subTest(field=field), self.assertRaises(ValueError):
                ci.validate_native_result(invalid if target is receipt else receipt,
                                          invalid if target is report else report, hashes)

    def fixture(self, root, test_body='self.assertEqual(sith.VALUE, "package")', *, skipped=False, empty=False):
        source = root / 'source'
        for name in ('tests/core', 'tests/app', 'scripts', 'docs', 'sith'):
            (source / name).mkdir(parents=True, exist_ok=True)
        for name in ('README.md', 'USAGE.md', 'LICENSE'):
            (source / name).write_text(name, encoding='utf-8')
        (source / 'sith/__init__.py').write_text('raise RuntimeError("checkout imported")\n', encoding='utf-8')
        (source / 'tests/core/__init__.py').write_text('', encoding='utf-8')
        if not empty:
            (source / 'tests/core/test_sample.py').write_text(
                'import unittest\nimport sith\nclass Sample(unittest.TestCase):\n'
                + ('    @unittest.skip("fixture skip")\n' if skipped else '')
                + '    def test_package(self):\n        ' + test_body + '\n', encoding='utf-8')
        # This is the actual upstream entry point, not a replacement test runner.
        shutil.copy2(SCRIPT.parents[1] / 'tests/run.py', source / 'tests/run.py')
        shutil.copy2(SCRIPT, source / 'scripts/ci_platform_validation.py')
        archive = root / 'package.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('__init__.py', 'VALUE = "package"\n')
            package.writestr('blender_manifest.toml', 'id = "sith"\nversion = "1.1.0"\n')
            package.writestr('LICENSE', 'license\n')
        return source, archive

    def run_fixture(self, root, **kwargs):
        source, archive = self.fixture(root, **kwargs)
        stage = root / 'stage'
        ci.stage_upstream_suite(source, archive, stage)
        report = root / 'upstream-core.json'
        bootstrap = root / 'bootstrap.py'
        ci.write_upstream_bootstrap(stage, 'core', bootstrap, report)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONOPTIMIZE', None)
        result = subprocess.run([sys.executable, '-B', str(bootstrap)], cwd=root,
                                env=env, capture_output=True, text=True, timeout=20)
        return result, json.loads(report.read_text(encoding='utf-8')), stage, archive

    def test_real_runner_loads_package_and_records_actual_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            result, report, stage, archive = self.run_fixture(Path(directory))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(report['tests_run'], 1)
            self.assertEqual(report['discovered_tests'], ['core.test_sample.Sample.test_package'])
            self.assertEqual(report['skipped'], [])
            self.assertEqual(report['loaded_addon'], str((stage / 'sith/__init__.py').resolve()))
            ci.validate_upstream_result(report, ci.package_source_hashes(archive), stage, 'core')
            for mutation in ('source_before_sha256', 'source_after_sha256', 'loaded_addon',
                             'tests_run', 'status', 'harness_unchanged'):
                invalid = copy.deepcopy(report)
                invalid[mutation] = {} if mutation.endswith('sha256') else None
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    ci.validate_upstream_result(invalid, ci.package_source_hashes(archive), stage, 'core')

    def test_failure_skip_and_source_mutation_are_not_success(self):
        cases = ({'test_body': 'self.fail("intentional red fixture")'},
                 {'empty': True},
                 {'skipped': True},
                 {'test_body': '__import__("pathlib").Path(sith.__file__).write_text("changed")'},
                 {'test_body': '__import__("pathlib").Path(__file__).write_text("changed")'})
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                result, report, stage, archive = self.run_fixture(Path(directory), **case)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(report['status'], 'failed')
                with self.assertRaises(ValueError):
                    ci.validate_upstream_result(report, ci.package_source_hashes(archive), stage, 'core')
                if case.get('skipped'):
                    self.assertEqual(report['skipped'][0]['reason'], 'fixture skip')
                if 'sith.__file__' in case.get('test_body', ''):
                    self.assertNotEqual(report['source_before_sha256'], report['source_after_sha256'])
                if 'Path(__file__)' in case.get('test_body', ''):
                    self.assertFalse(report['harness_unchanged'])

    def test_incomplete_input_scan_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def incomplete(path, *, onerror):
                yield str(path), [], []
                onerror(PermissionError('synthetic unreadable validation input'))
            with patch.object(ci.os, 'walk', incomplete), self.assertRaises(PermissionError):
                ci.input_files(root)

    def test_mixed_skips_are_reported_without_inflating_passes(self):
        body = ('self.assertTrue(True)\n'
                '    @unittest.skip("platform-specific fixture")\n'
                '    def test_skipped(self):\n        self.fail("must not run")')
        with tempfile.TemporaryDirectory() as directory:
            result, report, stage, archive = self.run_fixture(Path(directory), test_body=body)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(report['tests_run'], 2)
            self.assertEqual(len(report['skipped']), 1)
            self.assertEqual(report['skipped'][0]['reason'], 'platform-specific fixture')
            ci.validate_upstream_result(report, ci.package_source_hashes(archive), stage, 'core')

    def test_staging_copies_test_inputs_but_never_checkout_addon_or_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, archive = self.fixture(root)
            (source / 'tests/__pycache__').mkdir()
            (source / 'tests/__pycache__/cache.pyc').write_bytes(b'not source')
            (source / 'tests/tv').mkdir()
            (source / 'tests/tv/fixture.bin').write_bytes(b'fixture\x00')
            stage = root / 'stage'
            ci.stage_upstream_suite(source, archive, stage)
            self.assertEqual((stage / 'sith/__init__.py').read_text(), 'VALUE = "package"\n')
            self.assertEqual((stage / 'tests/tv/fixture.bin').read_bytes(), b'fixture\x00')
            self.assertFalse((stage / 'tests/__pycache__').exists())
            self.assertFalse((stage / '.git').exists())
            with self.assertRaises(FileExistsError):
                ci.stage_upstream_suite(source, archive, stage)

    def test_companion_command_does_not_discover_blender_tests(self):
        command = ci.companion_test_command(SCRIPT.parents[1])
        self.assertNotIn('discover', command)
        self.assertIn('test_ci_platform_validation', command)
        self.assertFalse(any('app.' in part or 'core.' in part for part in command))


if __name__ == '__main__':
    unittest.main()
