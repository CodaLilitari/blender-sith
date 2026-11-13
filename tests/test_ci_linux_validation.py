# SPDX-License-Identifier: GPL-3.0-or-later
"""Linux CI receipt and archive gates; no runtime download or Blender needed."""
import copy
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('ci_linux_validation', SCRIPTS / 'ci_linux_validation.py')
ci = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(SCRIPTS))
try:
    spec.loader.exec_module(ci)
finally:
    sys.path.pop(0)


class LinuxValidationTests(unittest.TestCase):
    def native_result(self):
        hashes = {'__init__.py': 'a' * 64, 'blender_manifest.toml': 'b' * 64}
        receipt = {'status': 'passed', 'source_unchanged': True,
                   'source_before_sha256': hashes, 'source_after_sha256': hashes,
                   'platform': {'system': 'Linux', 'machine': 'x86_64'},
                   'runtime': {'route': 'blender', 'blender': '5.1.2'},
                   'regression': {'passed': 29, 'failed': 0, 'addon_source_unchanged': True}}
        report = {'schema': 'blender-sith-agentic-regression-v2',
                  'passed': 29, 'failed': 0, 'addon_source_unchanged': True,
                  'addon_sha256': hashes, 'tests': [{'passed': True} for _ in range(29)]}
        return receipt, report, hashes

    def test_four_manifest_variants(self):
        raw = b'id = "sith"\r\nversion = "1.0.4"\r\nname = "Sith"\r\n'
        for platform in ci.PLATFORMS:
            with self.subTest(platform=platform):
                result = ci.platform_manifest(raw, platform)
                self.assertNotIn(b'\r', result)
                self.assertIn(b'version = "1.0.4"\n', result)
                if platform == 'universal':
                    self.assertNotIn(b'platforms', result)
                else:
                    self.assertIn(f'platforms = ["{platform}"]\n'.encode(), result)

    def test_manifest_rejects_restricted_source_and_unknown_platform(self):
        raw = b'id = "sith"\nversion = "1.0.4"\n'
        for data, platform in ((raw + b'platforms = ["linux-x64"]\n', 'linux-x64'),
                               (raw, 'linux-arm64'), (raw.replace(b'sith', b'other'), 'universal')):
            with self.subTest(data=data, platform=platform), self.assertRaises(ValueError):
                ci.platform_manifest(data, platform)

    def test_exact_native_receipt_passes(self):
        ci.validate_native_result(*self.native_result())

    def test_other_platform_or_runtime_fails(self):
        for section, key, value in (('platform', 'system', 'Windows'), ('platform', 'machine', 'aarch64'),
                                    ('runtime', 'route', 'python-bpy'), ('runtime', 'blender', '5.2.3')):
            receipt, report, hashes = self.native_result()
            receipt[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                ci.validate_native_result(receipt, report, hashes)

    def test_incomplete_checks_or_wrong_package_cannot_pass(self):
        for mutation in ('test_count', 'failed_test', 'source_after', 'report_hashes', 'source_changed', 'old_schema'):
            receipt, report, hashes = copy.deepcopy(self.native_result())
            if mutation == 'test_count': report['tests'].pop()
            if mutation == 'failed_test': report['tests'][0]['passed'] = False
            if mutation == 'source_after': receipt['source_after_sha256'] = {}
            if mutation == 'report_hashes': report['addon_sha256'] = {}
            if mutation == 'source_changed': receipt['source_unchanged'] = False
            if mutation == 'old_schema': report['schema'] = 'blender-sith-agentic-regression-v1'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                ci.validate_native_result(receipt, report, hashes)

    def test_runtime_extraction_confined_to_expected_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'runtime.tar.xz'
            prefix = 'blender-5.1.2-linux-x64'
            with tarfile.open(archive, 'w:xz') as package:
                member = tarfile.TarInfo(prefix + '/blender'); member.size = 5; member.mode = 0o755
                package.addfile(member, io.BytesIO(b'fake\n'))
            executable = ci.extract_runtime(archive, root / 'good')
            self.assertEqual(executable.read_bytes(), b'fake\n')
            for idx, name in enumerate(('../escape', '/absolute', prefix + '/../escape', 'other/blender')):
                with tarfile.open(archive, 'w:xz') as package:
                    member = tarfile.TarInfo(name); member.size = 1
                    package.addfile(member, io.BytesIO(b'x'))
                with self.subTest(name=name), self.assertRaises(ValueError):
                    ci.extract_runtime(archive, root / ('bad' + str(idx)))


if __name__ == '__main__':
    unittest.main()
