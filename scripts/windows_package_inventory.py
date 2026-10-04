"""Secret-free CI build inventory; preserves partial diagnostics on failed builds."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def file_row(path, root):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return {'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size,
            'sha256': digest.hexdigest()}


def inventory(root, cache):
    root = Path(root).resolve()
    files = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).decode('utf-8').split('\0')
    sources = [file_row(root / name, root) for name in files if name and (root / name).is_file()]
    artifacts = [file_row(path, root) for path in sorted((root / 'release').glob('*.exe'))]
    helper_root = Path(cache)
    helpers = [file_row(path, helper_root) for path in sorted(helper_root.rglob('*'))
               if path.is_file() and not path.is_symlink() and path.suffix.lower() in {'.exe', '.dll', '.7z'}]
    versions = {}
    for name in ('PyInstaller', 'pyinstaller-hooks-contrib', 'altgraph', 'pefile', 'pywin32-ctypes', 'torch', 'torchvision', 'timm', 'onnxruntime'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = 'unavailable'
    def tool(command):
        try:
            return subprocess.check_output(command, text=True, encoding='utf-8', timeout=20).strip()
        except (OSError, subprocess.SubprocessError):
            return 'unavailable'
    receipt = {'schema_version': 1, 'status': 'partial', 'target': 'Windows Server2025 x64 hosted; unsigned development artifacts',
               'source_sha': os.environ.get('GITHUB_SHA'), 'run_id': os.environ.get('GITHUB_RUN_ID'),
               'os': {'system': platform.system(), 'release': platform.release(), 'version': platform.version(), 'architecture': platform.machine()},
               'python': platform.python_version(), 'node': tool(['node', '--version']), 'build_versions': versions,
               'npm_locked_versions': {name: json.loads((root / 'package-lock.json').read_bytes())['packages']['node_modules/' + name]['version']
                                       for name in ('electron', 'electron-builder', '@playwright/test')},
               'sources': sources, 'artifacts': artifacts, 'builder_helpers': helpers,
               'signature_status': 'unsigned/unverified', 'release_ready': False,
               'qualification': {'Windows11_clean_install': 'unverified', 'installation': 'not_run', 'update': 'unverified', 'hardware': 'unverified'}}
    report = root / 'packaged-evidence' / 'packaged-receipt.json'
    if report.is_file():
        receipt['packaged_evidence'] = file_row(report, root)
        result = json.loads(report.read_bytes())
        if result.get('status') == 'failed' or result.get('cleanup') == 'failed':
            receipt.update(status='failed', failure_stage='packaged')
        elif result.get('status') == 'passed':
            if len(artifacts) != 2 or not any('Setup' in row['path'] for row in artifacts) or not any('Setup' not in row['path'] for row in artifacts):
                raise ValueError('Require exactly one NSIS and one portable executable')
            receipt['status'] = 'passed'
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--builder-cache', required=True)
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = inventory(args.root, args.builder_cache)
    except Exception as exc:
        # Exception text may contain external input; retain only a typed failure.
        result = {'schema_version': 1, 'status': 'failed', 'failure_type': type(exc).__name__}
    target.write_text(json.dumps(result, indent=2), encoding='utf-8')
    sys.exit(0 if result['status'] == 'passed' else 1)
