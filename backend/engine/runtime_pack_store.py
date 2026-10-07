"""Current-project runtime packs: explicit pin admission and inactive storage."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

from backend.contracts.capabilities import PROTOCOL_VERSION, RUNTIME_PROTOCOL_VERSION
from backend.engine import runtime_pack
from backend.engine.runtime_process_control import atomic_private_json, runtime_state_lock


def observed_target():
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {'amd64':'x64', 'x86_64':'x64', 'aarch64':'arm64', 'arm64':'arm64'}.get(machine, machine)
    return {'platform':system, 'arch':arch, 'python':platform.python_version(),
        'python_abi':getattr(sys.implementation, 'cache_tag', None),
        'compatibility':{'worker':PROTOCOL_VERSION, 'runtime':RUNTIME_PROTOCOL_VERSION}}


def _driver():
    command = shutil.which('nvidia-smi')
    if not command: return None
    try:
        result = subprocess.run([command, '--query-gpu=driver_version', '--format=csv,noheader'],
            capture_output=True, text=True, timeout=5, check=True)
        versions = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        if len(versions) != 1: return None
        version = versions.pop(); runtime_pack._driver(version); return version
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _unlinked(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Runtime pack project storage or input cannot be linked')
    return path


def _root(project):
    root = _unlinked(Path(project['project_dir']).absolute())
    if not root.is_dir(): raise ValueError('Open an existing project')
    return root.resolve()


def _store(project, *, create=False):
    root = _root(project)
    identifier = project.get('id')
    if not isinstance(identifier, str) or not identifier or len(identifier) > 128:
        raise ValueError('Runtime pack project scope is unavailable')
    key = hashlib.sha256(identifier.encode()).hexdigest()
    store = root / 'delivery' / 'runtime-packs' / key
    _unlinked(store)
    if create:
        store.mkdir(parents=True, exist_ok=True)
        _unlinked(store)
    if store.exists() and not store.is_dir(): raise ValueError('Runtime pack store must be a directory')
    return store


def _admissions(project, store):
    path = store / 'admissions.json'
    if not path.exists() and not path.is_symlink():
        return {'schema_version':1, 'project_id':project['id'], 'packs':{}}
    value = json.loads(runtime_pack.read_record(path))
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'project_id', 'packs'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['project_id'] != project['id'] or not isinstance(value['packs'], dict)
            or len(value['packs']) > 64):
        raise ValueError('Runtime pack admission scope or registry is invalid')
    for pin, row in value['packs'].items():
        if (not isinstance(pin, str) or not re.fullmatch(r'[a-f0-9]{64}', pin)
                or not isinstance(row, dict) or set(row) != {'id', 'version', 'inventory_sha256'}
                or row['inventory_sha256'] != pin
                or not isinstance(row['id'], str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', row['id'])
                or not isinstance(row['version'], str) or not re.fullmatch(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}', row['version'])):
            raise ValueError('Runtime pack admission registry is invalid')
    return value


def _abi(files):
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename, InvalidWheelFilename
    wheels = [row['path'] for row in files if row['path'].endswith('.whl')]
    if not wheels: return {'state':'not_declared', 'compatible':False, 'wheel_count':0}
    available = set(sys_tags()); matches = True
    for name in wheels:
        try: matches = matches and bool(parse_wheel_filename(Path(name).name)[3] & available)
        except InvalidWheelFilename: matches = False
    return {'state':'wheel_tags_match' if matches else 'wheel_tags_incompatible',
        'compatible':matches, 'wheel_count':len(wheels)}


def _row(project, store, saved, target, driver):
    destination = store / f"{saved['id']}-{saved['version']}-{saved['inventory_sha256']}"
    base = {**saved, 'installation_relative_path':destination.relative_to(_root(project)).as_posix(),
        'integrity':'failed', 'installed':False, 'activated':False,
        'signature_verified':False, 'execution_verified':False, 'target_compatible':False,
        'python_abi':{'state':'not_inspected', 'compatible':False, 'wheel_count':0}}
    try:
        inspected = runtime_pack.inspect_installation(destination, expected_sha256=saved['inventory_sha256'],
            platform=target['platform'], arch=target['arch'], compatibility=target['compatibility'], driver_version=driver)
        if (inspected['id'], inspected['version']) != (saved['id'], saved['version']):
            raise ValueError('Runtime pack identity differs from admission')
        base.update({key:value for key,value in inspected.items() if key not in {'installation_path', 'files', '_receipt'}})
        base['python_abi'] = _abi(inspected['files'])
        # Wheel tags are necessary, not sufficient for safe activation. Code or
        # checkpoints without wheels have no implicit Python ABI qualification.
    except (ValueError, OSError, KeyError, TypeError) as exc:
        base['error'] = str(exc)
    return base


def inventory(project):
    target = observed_target(); store = _store(project)
    if not store.exists(): return {'project_id':project['id'], 'target':target, 'packs':[], 'activation_supported':False}
    admissions = _admissions(project, store)
    driver = _driver()
    rows = [_row(project, store, saved, target, driver) for saved in admissions['packs'].values()]
    return {'project_id':project['id'], 'target':target, 'packs':rows, 'activation_supported':False}


def install(project, *, source_dir, inventory_path, expected_sha256):
    root = _root(project)
    source = _unlinked(Path(source_dir).absolute())
    document = _unlinked(Path(inventory_path).absolute())
    if not source.resolve().is_relative_to(root) or not document.resolve().is_relative_to(root):
        raise ValueError('Runtime pack inputs must belong to the active project folder')
    raw = runtime_pack.read_record(document)
    target = observed_target(); manifest = json.loads(raw)
    driver = _driver() if isinstance(manifest, dict) and manifest.get('kind') == 'nvidia' else None
    store = _store(project)
    if source.resolve().is_relative_to(store) or store.is_relative_to(source.resolve()):
        raise ValueError('Installation store and source pack must be disjoint')
    store = _store(project, create=True)
    with runtime_state_lock(store):
        admissions = _admissions(project, store)
        if len(admissions['packs']) >= 64 and expected_sha256 not in admissions['packs']:
            raise ValueError('This project already has 64 admitted runtime packs')
        receipt = runtime_pack.install_pack(source, raw, store=store, expected_sha256=expected_sha256,
            platform=target['platform'], arch=target['arch'], compatibility=target['compatibility'], driver_version=driver)
        saved = {'id':receipt['id'], 'version':manifest['version'], 'inventory_sha256':expected_sha256}
        admissions['packs'][expected_sha256] = saved
        # A interrupted admission never rewrites a published immutable pack.
        # Explicit retry revalidates it before recovering this separate pin.
        atomic_private_json(store/'admissions.json', admissions)
        return _row(project, store, saved, target, driver)
