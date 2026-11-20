# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded integration of checkout membership, release bytes and CI staging."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load('membership_release_test', 'build_extension.py')
general = load('membership_general_test', 'make_addon.py')
ci = load('membership_ci_test', 'ci_platform_validation.py')


class PackageMembershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sith membership integration ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source with spaces'
        self.addon = self.source / 'sith'
        self.addon.mkdir(parents=True)
        (self.source / 'LICENSE').write_bytes(b'Original license\r\n')
        (self.addon / 'blender_manifest.toml').write_bytes(b'id = "sith"\nversion = "1.2.3"\n')
        (self.addon / '__init__.py').write_bytes(b'# init\r\n')
        (self.addon / 'module.py').write_bytes(b'# indexed bytes\r\n')

    def checkout(self, *, license=True):
        if not shutil.which('git'):
            self.skipTest('External checkout fixture requires Git')
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True, capture_output=True, timeout=15)
        paths = ['sith/__init__.py', 'sith/blender_manifest.toml', 'sith/module.py']
        if license:
            paths.append('LICENSE')
        subprocess.run(['git', '-C', str(self.source), 'add', '--', *paths],
                       check=True, capture_output=True, timeout=15)

    def test_direct_and_staged_release_use_index_membership_without_changing_release_bytes(self):
        self.checkout()
        # Existing staged membership selects current working bytes, not index blobs.
        (self.addon / 'module.py').write_bytes(b'# working edits\r\n')
        (self.addon / 'new_module.py').write_bytes(b'# newly staged\r\n')
        subprocess.run(['git', '-C', str(self.source), 'add', '--', 'sith/new_module.py'],
                       check=True, capture_output=True, timeout=15)
        (self.addon / 'scratch.py').write_bytes(b'# untracked scratch\n')
        scratch = self.addon / 'untracked scratch directory'
        scratch.mkdir()
        (scratch / 'private.py').write_bytes(b'# untracked nested scratch\n')
        before = {p.relative_to(self.addon).as_posix(): p.read_bytes()
                  for p in self.addon.rglob('*') if p.is_file()}
        self.assertTrue(hasattr(ci, 'stage_extension_source'), 'CI staging must apply membership before dropping Git metadata')
        scandir = os.scandir

        def bounded_scan(path):
            if Path(path) == scratch:
                raise PermissionError('Untracked source directory must be pruned')
            return scandir(path)

        with patch.object(os, 'scandir', side_effect=bounded_scan):
            direct, receipt_path = release.build_extension(self.source, self.root / 'direct')
            stage = ci.stage_extension_source(self.source, self.root / 'stage')
            general.create_package(self.addon, self.root / 'general.zip')
        staged, _ = release.build_extension(stage, self.root / 'staged-package')
        self.assertEqual(direct.read_bytes(), staged.read_bytes())
        expected = {'LICENSE', '__init__.py', 'blender_manifest.toml', 'module.py', 'new_module.py'}
        with zipfile.ZipFile(direct) as archive:
            self.assertEqual(set(archive.namelist()), expected)
            self.assertEqual(archive.read('module.py'), b'# working edits\n')
            for info in archive.infolist():
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.external_attr >> 16, 0o100644)
        with zipfile.ZipFile(self.root / 'general.zip') as archive:
            self.assertEqual(set(archive.namelist()), expected - {'LICENSE'})
            self.assertEqual(archive.read('module.py'), b'# working edits\r\n')
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt['schema_version'], 1)
        self.assertEqual(receipt['normalization'], 'CRLF to LF')
        self.assertEqual(receipt['archive']['sha256'], hashlib.sha256(direct.read_bytes()).hexdigest())
        record = next(item for item in receipt['files'] if item['archive_path'] == 'module.py')
        self.assertEqual(record['raw_sha256'], hashlib.sha256(b'# working edits\r\n').hexdigest())
        self.assertEqual(record['packaged_sha256'], hashlib.sha256(b'# working edits\n').hexdigest())
        self.assertFalse((stage / '.git').exists())
        self.assertFalse((stage / 'sith/scratch.py').exists())
        self.assertEqual(before, {p.relative_to(self.addon).as_posix(): p.read_bytes()
                                  for p in self.addon.rglob('*') if p.is_file()})
        # The fork's no-overwrite release contract remains distinct from the general ZIP utility.
        original = direct.read_bytes()
        with self.assertRaises(FileExistsError):
            release.build_extension(self.source, direct.parent)
        self.assertEqual(direct.read_bytes(), original)

    def test_release_checkout_without_git_fails_before_outputs(self):
        self.checkout()
        with patch.dict(os.environ, {'PATH': ''}), self.assertRaisesRegex(ValueError, 'Git'):
            release.build_extension(self.source, self.root / 'no-git')
        self.assertFalse((self.root / 'no-git').exists())

    def test_release_requires_tracked_original_license(self):
        self.checkout(license=False)
        with self.assertRaisesRegex(ValueError, 'not tracked'):
            release.build_extension(self.source, self.root / 'untracked-license')
        self.assertFalse((self.root / 'untracked-license').exists())

    def test_source_archive_fallback_needs_no_git_and_keeps_ordinary_python_files(self):
        (self.addon / 'scratch.py').write_bytes(b'# no index exists here\n')
        with patch.dict(os.environ, {'PATH': ''}):
            archive, _ = release.build_extension(self.source, self.root / 'archive-build')
        with zipfile.ZipFile(archive) as package:
            self.assertIn('scratch.py', package.namelist())


if __name__ == '__main__':
    unittest.main(verbosity=2)
