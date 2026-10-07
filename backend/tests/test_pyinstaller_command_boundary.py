"""The actual build preserves full compiler arguments beyond Windows CreateProcess limits."""
import json
import subprocess
from pathlib import Path
import uuid

from scripts import build_backend_binary as build


def prepare(monkeypatch):
    root = build.ROOT_DIR
    paths = {path for path, _ in build.export_resource_files(root)} | {
        root / 'scripts' / 'frozen_backend_entry.py', root / 'requirements.txt'}
    inventory = {'schema_version': 1, 'dependencies': [], 'build_identity_sha256': 'fixture',
                 'resources': [{'path': str(path.relative_to(root)), 'sha256': build.sha256(path)}
                               for path in sorted(paths)]}
    def inventory_for(root, *, supplier_manifest=None):
        # Keep the compiler-boundary fixture aligned with the actual inventory
        # API; this case deliberately does not provision supplier license data.
        assert supplier_manifest is None
        return inventory
    monkeypatch.setattr(build, 'dependency_inventory', inventory_for)
    monkeypatch.setattr(build, 'check_pyinstaller', lambda: True)
    monkeypatch.setattr(build.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(uuid, 'uuid4', lambda: uuid.UUID(hex='a' * 32))
    return inventory


def test_build_binary_does_not_send_unbounded_compiler_arguments_to_createprocess(tmp_path, monkeypatch):
    inventory = prepare(monkeypatch)
    output = tmp_path / '빌드 space 출력'
    commands = []
    full_arguments = []
    def boundary(command, **kwargs):
        commands.append(command)
        length = len(subprocess.list2cmdline(command).encode('utf-16-le')) // 2 + 1
        if length > 32767:
            raise OSError(206, 'Windows CreateProcess command line exceeds 32767 UTF-16 characters')
        if '--backend-dir' in command:
            assert kwargs['check'] is True
            acceptance = Path(command[command.index('--output') + 1])
            acceptance.write_text(json.dumps({'status': 'passed', 'frozen': True}), encoding='utf-8')
        else:
            arguments = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
            full_arguments.extend(arguments)
            folder = output / 'vision_ai_backend'
            folder.mkdir()
            (folder / 'vision_ai_backend.exe').write_bytes(b'fixture delivered binary')
            toc=output/'.build/work/vision_ai_backend/PYZ-00.toc'
            toc.parent.mkdir(parents=True,exist_ok=True);toc.write_text('[]')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(build.subprocess, 'run', boundary)
    build.build_binary(output, accept=True)
    snapshot = next((output / '.build').glob('source-*'))
    original = build.pyinstaller_command(snapshot, output.resolve(), 'Windows')
    assert len(subprocess.list2cmdline(original).encode('utf-16-le')) // 2 + 1 > 32767
    assert full_arguments == original[3:]
    assert len(commands) == 2
    assert len(subprocess.list2cmdline(commands[0])) < 2048
    release = json.loads((output / 'vision_ai_backend' / 'backend-release.json').read_text(encoding='utf-8'))
    assert release['inventory']['resources'] == inventory['resources']
    assert release['acceptance'] == {'status': 'passed', 'frozen': True}
    assert release['executable_sha256'] == build.sha256(output / 'vision_ai_backend' / 'vision_ai_backend.exe')


def test_full_build_child_public_api_receives_exact_utf8_arguments(tmp_path, monkeypatch):
    import os
    import sys
    inventory = prepare(monkeypatch)
    output = tmp_path / '빌드 space 출력'
    shim = tmp_path / 'isolated shim'
    package = shim / 'PyInstaller'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    captured = tmp_path / 'child-arguments.json'
    (package / '__main__.py').write_text(
        "import json, os\nfrom pathlib import Path\n"
        "def run(arguments):\n"
        "    Path(os.environ['FIXTURE_ARGUMENTS']).write_text(json.dumps(arguments, ensure_ascii=False), encoding='utf-8')\n"
        "    output = Path(next(value.split('=', 1)[1] for value in arguments if value.startswith('--distpath=')))\n"
        "    folder = output / 'vision_ai_backend'\n"
        "    folder.mkdir()\n"
        "    (folder / 'vision_ai_backend.exe').write_bytes(b'isolated compiler fixture')\n"
        "    toc = output / '.build/work/vision_ai_backend/PYZ-00.toc'\n"
        "    toc.parent.mkdir(parents=True, exist_ok=True); toc.write_text('[]')\n", encoding='utf-8')
    real_run = subprocess.run
    calls = []
    def execute(command, **kwargs):
        calls.append(command)
        if '--backend-dir' in command:
            assert command[0] == sys.executable
            assert command[1] == str(build.ROOT_DIR / 'scripts' / 'release_backend_acceptance.py')
            assert kwargs['check'] is True
            Path(command[command.index('--output') + 1]).write_text(
                json.dumps({'status': 'passed', 'frozen': True}), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0)
        assert kwargs.get('shell', False) is False
        return real_run(command, **kwargs, env={**os.environ, 'PYTHONPATH': str(shim),
                                               'FIXTURE_ARGUMENTS': str(captured), 'PYTHONDONTWRITEBYTECODE': '1'})
    monkeypatch.setattr(build.subprocess, 'run', execute)
    build.build_binary(output, accept=True)
    snapshot = next((output / '.build').glob('source-*'))
    original = build.pyinstaller_command(snapshot, output.resolve(), 'Windows')
    received = json.loads(captured.read_text(encoding='utf-8'))
    assert received == original[3:]
    argument_file = Path(calls[0][-1])
    assert argument_file.parent == output / '.build'
    assert '빌드'.encode('utf-8') in argument_file.read_bytes()
    assert json.loads(argument_file.read_text(encoding='utf-8')) == received
    assert calls[0][0] == sys.executable
    assert len(calls[0]) == 4
    assert len(subprocess.list2cmdline(calls[0]).encode('utf-16-le')) // 2 + 1 < 2048
    assert received.count('--add-data') == len(build.export_resource_files(snapshot)) + 2
    assert str(snapshot/'scripts/frozen_backend_entry.py')+';scripts' in received
    assert '--hidden-import=backend.engine.application_launch_execution' in received
    assert '--hidden-import=backend.engine.service_bootstrap' in received
    assert '--collect-all=onnxruntime' in received
    assert '--exclude-module=pytest' in received
    release = json.loads((output / 'vision_ai_backend' / 'backend-release.json').read_text(encoding='utf-8'))
    assert release['inventory']['resources'] == inventory['resources']
    assert release['acceptance']['status'] == 'passed'


def test_child_compiler_failure_keeps_exit_code_and_never_runs_acceptance(tmp_path, monkeypatch):
    import os
    import pytest
    prepare(monkeypatch)
    shim = tmp_path / 'shim'
    package = shim / 'PyInstaller'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / '__main__.py').write_text('def run(arguments):\n    raise SystemExit(37)\n', encoding='utf-8')
    calls = []
    real_run = subprocess.run
    def execute(command, **kwargs):
        calls.append(command)
        return real_run(command, **kwargs, env={**os.environ, 'PYTHONPATH': str(shim), 'PYTHONDONTWRITEBYTECODE': '1'})
    monkeypatch.setattr(build.subprocess, 'run', execute)
    with pytest.raises(SystemExit) as raised:
        build.build_binary(tmp_path / 'output', accept=True)
    assert raised.value.code == 37
    assert len(calls) == 1
    assert not list((tmp_path / 'output').glob('**/backend-release.json'))
