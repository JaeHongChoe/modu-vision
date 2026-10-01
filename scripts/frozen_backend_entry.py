"""Frozen launcher with an offline diagnostic mode before server imports."""
from __future__ import annotations
import hashlib
import importlib
import json
import multiprocessing
from pathlib import Path
import runpy
import sys
import tarfile
import tempfile


def remote_bundle_diagnostic(inventory):
    """Exercise the real source archive producer without SSH or user data."""
    from backend.remote.coordinator import _bundle_backend
    expected = {}
    for row in inventory['resources']:
        name = row['path'].replace('\\', '/')
        if name.startswith('backend/') and name.endswith('.py'):
            expected[name] = row['sha256']
    required = {'backend/training_cli.py', 'backend/api/routes_evaluation.py',
                'backend/engine/trainer.py', 'backend/remote/__init__.py',
                'backend/remote/worker.py'}
    if not required <= expected.keys():
        raise ValueError('Frozen inventory lacks remote worker source prerequisites')
    hashes = {}
    with tempfile.TemporaryDirectory(prefix='vision-remote-bundle-diagnostic-') as temporary:
        archive = _bundle_backend(Path(temporary))
        with tarfile.open(archive) as reader:
            for member in reader:
                if not member.isfile() or member.name not in expected or member.name in hashes:
                    raise ValueError('Frozen remote archive has unexpected source files')
                hashes[member.name] = hashlib.sha256(reader.extractfile(member).read()).hexdigest()
    if hashes != expected:
        raise ValueError('Frozen remote archive source checksum or required files differ from inventory')
    return {'status': 'ready', 'source_file_count': len(hashes),
            'source_inventory_sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True,
                                                                separators=(',', ':')).encode()).hexdigest()}


# Internal children are launched as `<executable> -m <module>` in both source
# and frozen builds, which keeps their command lines identifiable by owners.
# Only these modules may be run this way from the frozen executable.
FROZEN_MODULES = frozenset({'backend.engine.operations_worker', 'backend.training_cli'})


def run_internal_module(argv):
    if len(argv) < 2 or argv[1] not in FROZEN_MODULES:
        requested = argv[1] if len(argv) > 1 else ''
        print(f'Unsupported frozen module request: {requested!r}', file=sys.stderr)
        return 2
    sys.argv = [sys.argv[0], *argv[2:]]
    runpy.run_module(argv[1], run_name='__main__', alter_sys=True)
    return 0


def diagnostics():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    inventory_path = root / 'backend-build-inventory.json'
    inventory = json.loads(inventory_path.read_text())
    dependencies = []
    for row in inventory['dependencies']:
        result = dict(row)
        if row['available']:
            try:
                importlib.import_module(row['module'])
                result['import_status'] = 'ready'
            except Exception as exc:
                result['import_status'] = 'failed'
                result['error_type'] = type(exc).__name__
        else: result['import_status'] = 'unavailable'
        dependencies.append(result)
    try:
        remote_bundle = remote_bundle_diagnostic(inventory)
    except Exception as exc:
        remote_bundle = {'status': 'failed', 'error_type': type(exc).__name__, 'error': str(exc)}
    required_ready = (all(row['import_status'] == 'ready' for row in dependencies if row['required'])
                      and remote_bundle['status'] == 'ready')
    print(json.dumps({'schema_version': 1, 'frozen': bool(getattr(sys, 'frozen', False)),
                      'status': 'ready' if required_ready else 'failed',
                      'build_identity_sha256': inventory['build_identity_sha256'],
                      'inventory_sha256': hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
                      'dependencies': dependencies, 'offline': inventory['offline'],
                      'remote_code_bundle': remote_bundle}), flush=True)
    return 0 if required_ready else 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    if sys.argv[1:] == ['--backend-diagnostics']:
        raise SystemExit(diagnostics())
    if len(sys.argv)>1 and sys.argv[1] in ('--flow-package-runner','--flow-package-worker'):
        from backend.engine.frozen_package_dispatch import main
        raise SystemExit(main(sys.argv[2:],worker=sys.argv[1]=='--flow-package-worker'))
    if len(sys.argv)>1 and sys.argv[1]=='-m':
        raise SystemExit(run_internal_module(sys.argv[1:]))
    if len(sys.argv)>1 and sys.argv[1]=='--basic-training-worker':
        del sys.argv[1]
        runpy.run_module('backend.training_cli',run_name='__main__')
        raise SystemExit(0)
    runpy.run_module('backend.main', run_name='__main__')
