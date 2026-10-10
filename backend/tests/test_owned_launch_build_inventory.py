"""An owned launch capability is an exact frozen source declaration, not acceptance."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_backend_binary as build


ROOT = Path(__file__).resolve().parents[2]
PATHS = (
    'scripts/frozen_backend_entry.py',
    'backend/engine/application_launch_controller.py',
    'backend/engine/application_launch_handshake.py',
    'backend/engine/application_launch_lease.py',
)
FIELD = 'owned_application_launch_controller_protocol'
DISPATCH = """import multiprocessing, runpy, sys
if __name__ == '__main__':
    multiprocessing.freeze_support()
    if len(sys.argv)>1 and sys.argv[1]=='--owned-application-launch-controller':
        from backend.engine.application_launch_controller import main
        raise SystemExit(main(sys.argv[2:]))
    from backend.engine.application_launch_handshake import early_backend_bootstrap
    early_backend_bootstrap()
    runpy.run_module('backend.main', run_name='__main__')
"""


def checkout(tmp_path, monkeypatch):
    root = tmp_path/'checkout'
    for name in PATHS:
        file = root/name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(DISPATCH if name == PATHS[0] else '"""Owned protocol source fixture."""\n')
    for name in ('requirements.txt', 'scripts/package_license_texts.py',
                 'scripts/license_inventory.py', 'scripts/build_backend_binary.py'):
        (root/name).write_text('')
    # Dependency and compiler discovery are unrelated to this source-only gate.
    # No dependency installation, binary execution, or PyInstaller build occurs.
    monkeypatch.setattr(build, 'DEPENDENCIES', ())
    monkeypatch.setattr(build, 'check_pyinstaller', lambda: False)
    monkeypatch.setattr(build, 'export_resource_files', lambda base: [
        (file, 'backend/engine') for file in (Path(base)/'backend/engine').glob('*.py')])
    return root


def test_exact_dispatch_and_four_checksum_bound_sources_advertise_protocol(tmp_path, monkeypatch):
    root = checkout(tmp_path, monkeypatch)
    inventory = build.dependency_inventory(root)
    assert inventory[FIELD] == 1
    assert type(inventory[FIELD]) is int
    resources = {row['path']: row['sha256'] for row in inventory['resources']}
    for name in PATHS:
        assert resources[name] == hashlib.sha256((root/name).read_bytes()).hexdigest()
    unhashed = {key: value for key, value in inventory.items() if key != 'build_identity_sha256'}
    assert inventory['build_identity_sha256'] == hashlib.sha256(
        json.dumps(unhashed, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    build.validate_inventory(inventory)


@pytest.mark.parametrize('missing', PATHS)
def test_missing_prerequisite_cannot_advertise_launch_protocol(tmp_path, monkeypatch, missing):
    root = checkout(tmp_path, monkeypatch)
    (root/missing).unlink()
    if missing == PATHS[0]:
        with pytest.raises(FileNotFoundError):
            build.dependency_inventory(root)
    else:
        assert FIELD not in build.dependency_inventory(root)


@pytest.mark.parametrize('entry', [
    "import runpy\nrunpy.run_module('backend.main', run_name='__main__')\n",
    "# --owned-application-launch-controller; application_launch_controller main\n"
        "import runpy\nrunpy.run_module('backend.main', run_name='__main__')\n",
    DISPATCH.replace("--owned-application-launch-controller", "--offline-application-update"),
    DISPATCH.replace('backend.engine.application_launch_controller', 'backend.engine.runtime_update'),
    DISPATCH.replace("    multiprocessing.freeze_support()", "    runpy.run_module('backend.main', run_name='__main__')"),
    DISPATCH.replace("    multiprocessing.freeze_support()", "    runpy.run_module('backend.main')"),
    DISPATCH.replace("    multiprocessing.freeze_support()", "    raise SystemExit(0)"),
    DISPATCH.replace('    early_backend_bootstrap()\n', ''),
    'def unused_dispatch():\n'+''.join('    '+line+'\n' for line in DISPATCH.splitlines()),
])
def test_older_fallback_or_nonexecuted_dispatch_does_not_advertise_protocol(tmp_path, monkeypatch, entry):
    root = checkout(tmp_path, monkeypatch)
    (root/PATHS[0]).write_text(entry)
    assert FIELD not in build.dependency_inventory(root)


@pytest.mark.parametrize('change', ['absent', 'duplicate', 'uppercase', 'empty', 'extra', 'alias'])
def test_forged_declaration_without_exact_resource_pins_refuses_inventory(tmp_path, monkeypatch, change):
    root = checkout(tmp_path, monkeypatch)
    inventory = build.dependency_inventory(root)
    inventory[FIELD] = 1
    row = next(row for row in inventory['resources'] if row['path'] == PATHS[2])
    if change == 'absent': inventory['resources'].remove(row)
    elif change == 'duplicate': inventory['resources'].append(copy.deepcopy(row))
    elif change == 'uppercase': row['sha256'] = row['sha256'].upper()
    elif change == 'empty': row['sha256'] = ''
    elif change == 'extra': row['fallback'] = True
    elif change == 'alias': row['path'] = './'+row['path']
    with pytest.raises(ValueError, match='launch.*protocol|protocol.*launch'):
        build.validate_inventory(inventory)


@pytest.mark.parametrize('protocol', [True, False, 1.0, 0, 2, '1', None])
def test_unknown_or_coerced_protocol_declaration_is_not_a_launch_capability(tmp_path, monkeypatch, protocol):
    inventory = build.dependency_inventory(checkout(tmp_path, monkeypatch))
    inventory[FIELD] = protocol
    with pytest.raises(ValueError, match='launch.*protocol|protocol.*launch'):
        build.validate_inventory(inventory)


def test_changed_dispatch_after_resource_hashing_cannot_advertise_protocol(tmp_path, monkeypatch):
    root = checkout(tmp_path, monkeypatch)
    original = build.sha256
    def change_after_hash(file):
        digest = original(file)
        if Path(file) == root/PATHS[0]:
            Path(file).write_text(DISPATCH+'# changed after inventory hashing\n')
        return digest
    monkeypatch.setattr(build, 'sha256', change_after_hash)
    assert FIELD not in build.dependency_inventory(root)


def test_declared_protocol_resources_survive_exact_frozen_snapshot(tmp_path, monkeypatch):
    root = checkout(tmp_path, monkeypatch)
    inventory = build.dependency_inventory(root)
    assert inventory[FIELD] == 1
    snapshot = build.snapshot_sources(root, tmp_path/'frozen-sources', inventory['resources'])
    for name in PATHS:
        assert (snapshot/name).read_bytes() == (root/name).read_bytes()


def test_current_checkout_declares_exact_controller_bootstrap_resources(monkeypatch):
    monkeypatch.setattr(build, 'DEPENDENCIES', ())
    monkeypatch.setattr(build, 'check_pyinstaller', lambda: False)
    inventory = build.dependency_inventory(ROOT)
    assert inventory[FIELD] == 1
    for name in PATHS:
        rows = [row for row in inventory['resources'] if row['path'] == name]
        assert rows == [{'path': name, 'sha256': hashlib.sha256((ROOT/name).read_bytes()).hexdigest()}]
    build.validate_inventory(inventory)


@pytest.mark.parametrize('change', ['old', 'dormant', 'wrong-target', 'late', 'missing-resource', 'duplicate-resource', 'float'])
def test_cpu_protocol_is_distinct_and_cannot_probe_older_or_forged_inventory(tmp_path,monkeypatch,change):
    root=checkout(tmp_path,monkeypatch)
    worker="""    if len(sys.argv)>1 and sys.argv[1]=='--owned-application-cpu-worker':
        from backend.engine.application_launch_execution import frozen_worker_main
        raise SystemExit(frozen_worker_main(sys.argv[2:]))
"""
    entry=DISPATCH.replace('    from backend.engine.application_launch_handshake',worker+'    from backend.engine.application_launch_handshake')
    for name in build.OWNED_APPLICATION_CPU_RESOURCES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():path.write_text('# CPU protocol prerequisite fixture\n')
    if change=='old':entry=DISPATCH
    elif change=='dormant':entry=DISPATCH+'\ndef unused():\n'+worker
    elif change=='wrong-target':entry=entry.replace('application_launch_execution import frozen_worker_main','flow_package_runtime import frozen_worker_main')
    elif change=='late':entry=DISPATCH+worker
    (root/PATHS[0]).write_text(entry)
    field=build.OWNED_APPLICATION_CPU_PROTOCOL
    if change=='missing-resource':(root/build.OWNED_APPLICATION_CPU_RESOURCES[-1]).unlink()
    inventory=build.dependency_inventory(root)
    if change in {'old','dormant','wrong-target','late','missing-resource'}:
        assert field not in inventory
    else:
        assert inventory[field]==1
        if change=='float':inventory[field]=1.0
        else:inventory['resources'].append(copy.deepcopy(next(row for row in inventory['resources'] if row['path']==build.OWNED_APPLICATION_CPU_RESOURCES[-1])))
        with pytest.raises(ValueError):build.validate_inventory(inventory)



def test_compiler_child_disables_auto_install_without_mutating_parent_or_arguments(tmp_path, monkeypatch):
    """Exercise the actual compiler subprocess and JSON argument transport."""
    import os
    import sys
    import types

    root = checkout(tmp_path, monkeypatch)
    inventory = build.dependency_inventory(root)
    monkeypatch.setattr(build, 'ROOT_DIR', root)
    monkeypatch.setattr(build, 'ENTRY_POINT', root/'scripts/frozen_backend_entry.py')
    monkeypatch.setattr(build, 'check_pyinstaller', lambda: True)
    monkeypatch.setattr(build, 'dependency_inventory', lambda base, **kwargs: inventory)
    # The compiler and license data are fixtures; subprocess.run, source
    # snapshotting, command construction and argument transport remain real.
    licenses = types.ModuleType('scripts.package_license_texts')
    licenses.collect_frozen_licenses = lambda *args, **kwargs: {}
    monkeypatch.setitem(sys.modules, 'scripts.package_license_texts', licenses)

    expected = {
        'YOLO_AUTOINSTALL': 'false',
        'PIP_NO_INDEX': '1',
        'PIP_DISABLE_PIP_VERSION_CHECK': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
        'HF_HUB_OFFLINE': '1',
        'HF_DATASETS_OFFLINE': '1',
        'TRANSFORMERS_OFFLINE': '1',
    }
    for name in expected:
        monkeypatch.setenv(name, 'true' if name == 'YOLO_AUTOINSTALL' else '0')
    monkeypatch.setenv('OWNED_COMPILER_ENV_SENTINEL', 'parent-value-preserved')
    proof_path = tmp_path/'compiler-child-proof.json'
    monkeypatch.setenv('OWNED_COMPILER_ENV_PROOF', str(proof_path))
    compiler_root = tmp_path/'compiler-fixture'
    package = compiler_root/'PyInstaller'
    package.mkdir(parents=True)
    (package/'__init__.py').write_text('', encoding='utf-8')
    (package/'__main__.py').write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "def run(arguments):\n"
        "    names = ('YOLO_AUTOINSTALL', 'PIP_NO_INDEX', 'PIP_DISABLE_PIP_VERSION_CHECK',\n"
        "             'PYTHONDONTWRITEBYTECODE', 'HF_HUB_OFFLINE', 'HF_DATASETS_OFFLINE', 'TRANSFORMERS_OFFLINE')\n"
        "    proof = {'arguments': arguments, 'environment': {name: os.environ.get(name) for name in names},\n"
        "             'sentinel': os.environ.get('OWNED_COMPILER_ENV_SENTINEL')}\n"
        "    Path(os.environ['OWNED_COMPILER_ENV_PROOF']).write_text(json.dumps(proof), encoding='utf-8')\n"
        "    output = Path(next(arg.split('=', 1)[1] for arg in arguments if arg.startswith('--distpath=')))\n"
        "    directory = output/'vision_ai_backend'\n"
        "    directory.mkdir()\n"
        "    executable = directory/('vision_ai_backend.exe' if os.name == 'nt' else 'vision_ai_backend')\n"
        "    executable.write_bytes(b'Owned compiler fixture; never execute this file.\\n')\n",
        encoding='utf-8')
    monkeypatch.setenv('PYTHONPATH', str(compiler_root))
    parent_environment = dict(os.environ)
    output = tmp_path/'dist'

    build.build_binary(output, accept=False)

    proof = json.loads(proof_path.read_text(encoding='utf-8'))
    assert proof['environment'] == expected
    assert proof['sentinel'] == 'parent-value-preserved'
    parent_unchanged = dict(os.environ) == parent_environment
    assert parent_unchanged, 'Compiler build mutated its parent environment'
    assert os.environ['YOLO_AUTOINSTALL'] == 'true'
    snapshots = list((output/'.build').glob('source-*'))
    assert len(snapshots) == 1
    command = build.pyinstaller_command(snapshots[0], output, build.platform.system())
    argument_files = list((output/'.build').glob('pyinstaller-arguments-*.json'))
    assert len(argument_files) == 1
    assert proof['arguments'] == json.loads(argument_files[0].read_text(encoding='utf-8')) == command[3:]
    assert not (package/'__pycache__').exists()
