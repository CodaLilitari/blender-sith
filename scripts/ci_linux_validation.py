#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Manual native Linux CI: validate four ZIPs and run the exact Linux package.

All runtime downloads, staged sources and results stay beneath RUNNER_TEMP,
outside the checkout. The official Blender archive is pinned and checked
against its official checksum list. No Git, install or publication actions.
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
import subprocess
import sys
import tarfile
import tomllib

from ci_platform_validation import (checked_output, checksum_for_asset, companion_test_command, digest,
                                    extract_package, package_source_hashes, REGRESSION_SCHEMA,
                                    run_logged, run_upstream_suites)

ROOT = Path(__file__).resolve().parents[1]
BLENDER_VERSION = '5.1.2'
ASSET = 'blender-5.1.2-linux-x64.tar.xz'
BASE_URL = 'https://ftp.blender.org/release/Blender5.1/'
# Read from the official blender-5.1.2.sha256 over HTTPS on 2026-11-12.
ARCHIVE_SHA256 = 'aaccb355f50183979b698bcce7467103a76261b5fa59f4972295842662a285fb'
PLATFORMS = ('windows-x64', 'linux-x64', 'macos-arm64', 'universal')
EXPECTED_TESTS = 29


def platform_manifest(raw, variant):
    text = raw.decode('utf-8').replace('\r\n', '\n')
    manifest = tomllib.loads(text)
    if variant not in PLATFORMS or manifest.get('id') != 'sith' or 'platforms' in manifest:
        raise ValueError('Expected a supported variant and unrestricted common sith manifest')
    lines = text.splitlines(keepends=True)
    indices = [i for i, line in enumerate(lines) if re.match(r'^version\s*=', line)]
    if len(indices) != 1 or not lines[indices[0]].endswith('\n'):
        raise ValueError('Expected one newline-terminated top-level version assignment')
    if variant != 'universal':
        lines.insert(indices[0] + 1, f'platforms = ["{variant}"]\n')
    return ''.join(lines).encode('utf-8')


def validate_native_result(receipt, report, expected_hashes):
    tests = report.get('tests', [])
    runtime = receipt.get('runtime', {})
    if (report.get('schema') != REGRESSION_SCHEMA
            or receipt.get('status') != 'passed' or receipt.get('source_unchanged') is not True
            or receipt.get('platform') != {'system': 'Linux', 'machine': 'x86_64'}
            or runtime.get('route') != 'blender' or runtime.get('blender') != BLENDER_VERSION
            or receipt.get('regression') != {'passed': EXPECTED_TESTS, 'failed': 0, 'addon_source_unchanged': True}
            or receipt.get('source_before_sha256') != expected_hashes
            or receipt.get('source_after_sha256') != expected_hashes
            or report.get('addon_sha256') != expected_hashes or report.get('addon_source_unchanged') is not True
            or report.get('passed') != EXPECTED_TESTS or report.get('failed') != 0
            or len(tests) != EXPECTED_TESTS or any(t.get('passed') is not True for t in tests)):
        raise ValueError(f'Expected {EXPECTED_TESTS} checks on the exact Linux x86_64 package with Blender {BLENDER_VERSION}')


def extract_runtime(archive, target):
    target.mkdir()
    prefix = ASSET.removesuffix('.tar.xz')
    with tarfile.open(archive, 'r:xz') as package:
        for member in package.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != prefix or '\\' in member.name:
                raise ValueError('Runtime archive member outside expected top-level directory')
        package.extractall(target, filter='data')
    executable = target / prefix / 'blender'
    if not executable.is_file() or executable.is_symlink():
        raise ValueError('Runtime archive lacks its expected Blender executable')
    return executable


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        parser.error('This job requires native Linux x86_64')
    if not os.environ.get('RUNNER_TEMP'):
        parser.error('RUNNER_TEMP must name the external job root')
    output = checked_output(args.output, Path(os.environ['RUNNER_TEMP']), ROOT)
    output.mkdir(parents=True)
    evidence = output / 'evidence'
    evidence.mkdir()
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', OMP_NUM_THREADS='2',
               OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2', TMPDIR=str(output))
    for name in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONOPTIMIZE'):
        env.pop(name, None)
    for suffix in ('CONFIG', 'SCRIPTS', 'DATAFILES', 'EXTENSIONS'):
        directory = output / 'config' / suffix.lower()
        directory.mkdir(parents=True)
        env['BLENDER_USER_' + suffix] = str(directory)
    receipt = {'schema': 'blender-sith-native-linux-ci-v2', 'status': 'failed',
               'commit': os.environ.get('GITHUB_SHA'), 'run_id': os.environ.get('GITHUB_RUN_ID'),
               'system': platform.system(), 'machine': platform.machine(),
               'expected_blender': BLENDER_VERSION, 'expected_checks': EXPECTED_TESTS,
               'runtime_url': BASE_URL + ASSET, 'runtime_sha256': ARCHIVE_SHA256,
               'scope': 'all four ZIP schemas; native exact Linux package; no UI or game-runtime claim'}
    try:
        run_logged(companion_test_command(ROOT), 'unit-tests', evidence, ROOT / 'tests', env, 300)
        addon = ROOT / 'sith'
        if addon.is_symlink() or any(p.is_symlink() for p in addon.rglob('*')):
            raise ValueError('Source symlinks are not permitted')
        packages = evidence / 'packages'
        raw_manifest = (addon / 'blender_manifest.toml').read_bytes()
        receipt['source_manifest_sha256'] = hashlib.sha256(raw_manifest).hexdigest()
        package_records = []
        source_hashes = {}
        for variant in PLATFORMS:
            stage = output / ('staged-' + variant)
            stage.mkdir()
            shutil.copytree(addon, stage / 'sith', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
            shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE')
            staged_manifest = platform_manifest(raw_manifest, variant)
            (stage / 'sith' / 'blender_manifest.toml').write_bytes(staged_manifest)
            version = tomllib.loads(staged_manifest.decode('utf-8'))['version']
            run_logged([sys.executable, '-B', str(ROOT / 'scripts' / 'build_extension.py'),
                        '--source-dir', str(stage), '--output-dir', str(packages)],
                       'build-' + variant, evidence, output, env, 60)
            stem = f'sith-{version}-{variant}'
            archive = packages / (stem + '.zip')
            build_receipt = json.loads((packages / (stem + '.receipt.json')).read_text(encoding='utf-8'))
            archive_hash = digest(archive)
            if archive_hash != build_receipt['archive']['sha256']:
                raise ValueError('Package receipt and archive hash disagree: ' + variant)
            source_hashes[variant] = package_source_hashes(archive)
            package_records.append({'platform': variant, 'name': archive.name,
                                    'sha256': archive_hash, 'size': archive.stat().st_size})
        receipt['packages'] = package_records
        downloads = output / 'downloads'
        downloads.mkdir()
        checksum_path = downloads / ('blender-' + BLENDER_VERSION + '.sha256')
        curl = ['/usr/bin/curl', '--fail', '--show-error', '--location', '--proto', '=https',
                '--proto-redir', '=https', '--connect-timeout', '30', '--retry', '2', '--retry-max-time', '600']
        run_logged(curl + ['--max-time', '60', '--output', str(checksum_path), BASE_URL + checksum_path.name],
                   'download-checksums', evidence, output, env, 90)
        if checksum_for_asset(checksum_path.read_text(encoding='utf-8'), ASSET) != ARCHIVE_SHA256:
            raise ValueError('Official checksum list differs from the reviewed pinned digest')
        shutil.copy2(checksum_path, evidence / checksum_path.name)
        runtime_archive = downloads / ASSET
        run_logged(curl + ['--max-time', '600', '--output', str(runtime_archive), BASE_URL + ASSET],
                   'download-blender', evidence, output, env, 650)
        if digest(runtime_archive) != ARCHIVE_SHA256:
            raise ValueError('Official Blender archive SHA-256 mismatch')
        receipt['runtime_verified'] = True
        blender = extract_runtime(runtime_archive, output / 'runtime')
        validations = []
        for package_record in package_records:
            archive = packages / package_record['name']
            run_logged([str(blender), '--background', '--factory-startup', '--offline-mode', '--threads', '2',
                        '--command', 'extension', 'validate', str(archive)],
                       'validate-' + package_record['platform'], evidence, output, env, 120)
            validations.append({'platform': package_record['platform'], 'archive': archive.name,
                                'sha256': package_record['sha256'], 'exit_code': 0})
        receipt['schema_validation'] = validations
        linux_package = next(p for p in package_records if p['platform'] == 'linux-x64')
        archive = packages / linux_package['name']
        extracted = output / 'exact-linux-package'
        extract_package(archive, extracted)
        regression = evidence / 'regression'
        run_logged([sys.executable, '-B', str(ROOT / 'scripts' / 'run_regression.py'),
                    '--blender', str(blender), '--addon', str(extracted), '--output', str(regression),
                    '--threads', '2', '--timeout', '300'], 'native-regression', evidence, output, env, 360)
        launcher = json.loads((regression / 'receipt.json').read_text(encoding='utf-8'))
        report = json.loads((regression / 'results' / 'regression.json').read_text(encoding='utf-8'))
        validate_native_result(launcher, report, source_hashes['linux-x64'])
        receipt['upstream_suites'] = run_upstream_suites(
            ROOT, archive, blender, output, evidence, env, 'Linux', 'x86_64', BLENDER_VERSION)
        if any(digest(packages / p['name']) != p['sha256'] for p in package_records):
            raise ValueError('A schema-validated or tested archive changed')
        receipt.update(status='passed', archive=linux_package, runtime=launcher['runtime'], regression=launcher['regression'])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as exc:
        receipt['error'] = type(exc).__name__ + ': ' + str(exc)
        print(receipt['error'], file=sys.stderr)
    finally:
        (evidence / 'platform-validation.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': receipt['status'], 'evidence': str(evidence)}))
    return 0 if receipt['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
