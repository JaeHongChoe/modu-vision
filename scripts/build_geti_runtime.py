#!/usr/bin/env python3
"""Build a relocatable Geti runtime from explicit, already installed inputs.

No installers, package downloads, accounts or model requests run here. The
output must not exist, so rebuilding cannot replace a person's active runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'ddcf394d550bf1b84ca262888260043af361f5e7'
WEIGHT = 'efficientnet_b0-0752-0e386130.pth.zip'
WEIGHT_SHA = '79f2b405a15399a0772337bd7f58052cb88299a65ed838a43c1ea8d3e0253f70'


def sha(file):
    digest = hashlib.sha256()
    with Path(file).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def copy_tree(source, target):
    target.mkdir(parents=True, exist_ok=False)
    if sys.platform == 'darwin':
        # APFS clones avoid another full physical copy during package staging.
        subprocess.run(['cp', '-cR', str(source) + '/.', str(target)], check=True)
    else:
        shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)


def copy_overlay(source, target):
    """Local interpreter caches/descriptors are not reviewed runtime assets."""
    shutil.copytree(source, target, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', 'installed-runtime.json'))


def build(args):
    source = args.source.resolve(strict=True)
    base = args.python_root.resolve(strict=True)
    site = args.site_packages.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; choose a fresh staging directory')
    if output.is_relative_to(source) or output.is_relative_to(base) or output.is_relative_to(site):
        raise ValueError('Output must be separate from the input runtime')
    checked = subprocess.run([str(args.node), str(ROOT / 'scripts/geti-platform.cjs'), 'check', '--json',
                              '--source', str(source)], check=True, capture_output=True, text=True, encoding='utf-8')
    if not json.loads(checked.stdout)['valid']:
        raise ValueError('Pinned Geti source verification failed')
    python = base / 'bin/python3.14'
    if not python.is_file() or not (site / 'getitune-0.4.0.dist-info').is_dir():
        raise ValueError('Explicit Python3.14 and installed Geti CPU dependency inputs are required')
    if args.weight and sha(args.weight) != WEIGHT_SHA:
        raise ValueError('Declared starter model weight SHA256 differs')
    observed = subprocess.check_output([str(python), '-I', '-c',
        'import sys,platform,json;print(json.dumps([sys.version_info[:2],sys.platform,platform.machine()]))'], text=True)
    target = json.loads(observed)
    if target != [[3, 14], sys.platform, platform.machine()]:
        raise ValueError('Build the separate Python runtime on the actual target OS/architecture')
    output.mkdir(parents=True, exist_ok=False)
    copy_tree(base, output / 'python')
    portable_site = output / 'python/lib/python3.14/site-packages'
    portable_site.mkdir(parents=True, exist_ok=True)
    # Preserve dependency bytes and modes; only installation-owned editable
    # package pointers are rewritten to contained relative source paths.
    for child in site.iterdir():
        target = portable_site / child.name
        if target.exists() or target.is_symlink():
            raise ValueError(f'Duplicate base/site package entry: {child.name}')
        if child.is_symlink():
            target.symlink_to(child.readlink())
        elif child.is_dir():
            copy_tree(child, target)
        else:
            shutil.copy2(child, target)
    (portable_site / '__editable__.getitune-0.4.0.pth').write_text('../../../../source/library/src\n', encoding='utf-8')
    (portable_site / '_editable_impl_geti.pth').write_text('../../../../source/application/backend\n', encoding='utf-8')
    entries = subprocess.check_output(['git', '--no-replace-objects', '-C', str(source), 'ls-tree', '-rz', 'HEAD'])
    for entry in entries.split(b'\0'):
        if not entry:
            continue
        info, raw_name = entry.split(b'\t', 1)
        mode, kind, _ = info.decode().split()
        name = raw_name.decode('utf-8')
        if kind != 'blob' or name.startswith('/') or '..' in Path(name).parts:
            raise ValueError('Unsupported source tree object')
        target = output / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == '120000':
            target.symlink_to((source / name).readlink())
        else:
            shutil.copy2(source / name, target)
    overlay = ROOT / 'platforms/geti/runtime'
    copy_overlay(overlay, output / 'overlay')
    (output / 'weights/classification').mkdir(parents=True)
    if args.weight:
        shutil.copy2(args.weight, output / 'weights/classification' / WEIGHT)
    files, links = [], []
    for folder, directories, names in os.walk(output, followlinks=False):
        directory = Path(folder)
        for name in list(directories):
            file = directory / name
            if file.is_symlink():
                names.append(name)
                directories.remove(name)
        for name in sorted(names):
            file = directory / name
            relative = file.relative_to(output).as_posix()
            if file.is_symlink():
                if not file.resolve().is_relative_to(output):
                    raise ValueError(f'Runtime link escapes installation: {relative}')
                links.append({'path': relative, 'target': file.readlink().as_posix()})
            else:
                files.append({'path': relative, 'size': file.stat().st_size, 'sha256': sha(file)})
    inventory = {'version': 1, 'upstream_commit': COMMIT, 'files': files, 'links': links}
    raw = (json.dumps(inventory, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    (output / 'inventory.json').write_bytes(raw)
    descriptor = {'version': 1, 'upstream_commit': COMMIT, 'python': 'python/bin/python3.14',
                  'source_root': 'source', 'entrypoint': 'overlay/server.py', 'weights_root': 'weights',
                  'inventory': 'inventory.json', 'inventory_sha256': hashlib.sha256(raw).hexdigest(),
                  'platform': sys.platform, 'architecture': platform.machine()}
    (output / 'runtime.json').write_text(json.dumps(descriptor, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'files': len(files), 'links': len(links),
                      'bytes': sum(x['size'] for x in files), 'inventory_sha256': descriptor['inventory_sha256']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'platforms/geti/upstream')
    parser.add_argument('--python-root', type=Path, required=True)
    parser.add_argument('--site-packages', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--weight', type=Path)
    parser.add_argument('--node', type=Path, default=Path(shutil.which('node') or 'node'))
    build(parser.parse_args())
