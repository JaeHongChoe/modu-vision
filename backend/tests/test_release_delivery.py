"""Release checks bind executable bytes to inventories and require real targets."""
import hashlib
import json
import platform
import os
import subprocess
import sys
from pathlib import Path
import pytest
from scripts import build_backend_binary as build

ROOT = Path(__file__).resolve().parents[2]


def test_build_collects_dynamic_model_dependencies_and_uses_same_layout_on_all_targets(tmp_path):
    for target in ('Windows', 'Darwin', 'Linux'):
        command = build.pyinstaller_command(ROOT, tmp_path, target)
        assert '--onedir' in command
        assert '--collect-all=timm' in command
        assert '--collect-all=ultralytics' in command
        assert '--collect-all=onnxruntime' in command
        assert '--hidden-import=backend.engine.service_bootstrap' in command
        assert command[-1] == str(ROOT / 'scripts' / 'frozen_backend_entry.py')


def test_offline_inventory_discloses_missing_optional_weights_and_exact_versions():
    inventory = build.dependency_inventory(ROOT)
    versions = {row['distribution']: row['version'] for row in inventory['dependencies']}
    assert versions['torch']
    assert versions['timm']
    assert versions['ultralytics']
    assert inventory['offline']['pretrained_weights_bundled'] is False
    assert inventory['offline']['network_downloads_required_for_uncached_training'] is True
    assert len(inventory['build_identity_sha256']) == 64


def test_release_validator_rejects_changed_binary_before_execution(tmp_path):
    executable = tmp_path / 'vision_ai_backend'
    executable.write_bytes(b'changed binary')
    inventory = {'schema_version': 1, 'platform': platform.system(),
                 'architecture': platform.machine(), 'build_identity_sha256': 'a' * 64}
    (tmp_path / 'backend-release.json').write_text(json.dumps({
        'schema_version': 1, 'executable': executable.name,
        'executable_sha256': hashlib.sha256(b'original binary').hexdigest(),
        'inventory': inventory,
    }))
    result = subprocess.run(['node', str(ROOT / 'scripts' / 'release-readiness.cjs'),
                             '--backend-dir', str(tmp_path), '--platform', platform.system(),
                             '--arch', platform.machine()], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'executable checksum' in result.stdout + result.stderr


def test_release_validator_refuses_cross_target_runtime_receipts(tmp_path):
    result = subprocess.run(['node', str(ROOT / 'scripts' / 'release-readiness.cjs'),
                             '--backend-dir', str(tmp_path), '--platform', 'Windows', '--arch', 'x64'],
                            capture_output=True, text=True)
    if platform.system() != 'Windows':
        assert result.returncode != 0
        assert 'requires_target' in result.stdout


def test_freezing_uses_an_immutable_source_snapshot_during_concurrent_edits(tmp_path):
    root=tmp_path/'checkout';(root/'backend').mkdir(parents=True)
    source=root/'backend'/'entry.py';source.write_text('original source')
    expected=hashlib.sha256(b'original source').hexdigest()
    frozen=build.snapshot_sources(root,tmp_path/'snapshot',[{'path':'backend/entry.py','sha256':expected}])
    source.write_text('edited after snapshot')
    assert (frozen/'backend'/'entry.py').read_text()=='original source'
    with pytest.raises(ValueError,match='changed'):
        build.snapshot_sources(root,tmp_path/'second',[{'path':'backend/entry.py','sha256':expected}])


def test_release_build_blocks_installed_versions_below_declared_requirements():
    inventory={'dependencies':[{'module':'fastapi','required':True,'available':True,'satisfies_requirement':False}]}
    with pytest.raises(RuntimeError,match='requirements'):
        build.validate_inventory(inventory)


def test_frozen_entry_dispatches_owned_basic_worker_cli_before_desktop_startup():
    result=subprocess.run([sys.executable,str(ROOT/'scripts'/'frozen_backend_entry.py'),
                           '--basic-training-worker','basic-execute','--help'],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT)},capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert '--spec' in result.stdout
    assert 'VISION_AI_STUDIO_PORT=' not in result.stdout


def test_known_image_acceptance_rejects_unbound_or_failed_execution():
    from scripts.release_backend_acceptance import verify_known_image_execution
    runtime={'manifest_sha256':'a'*64,'device':'cpu','runtime_build':{'mode':'frozen','status':'identified','build_identity_sha256':'b'*64}}
    for altered in ({**runtime,'runtime_build':{'mode':'source','status':'identified','build_identity_sha256':'b'*64}},
                    {**runtime,'runtime_build':{'mode':'frozen','status':'identified','build_identity_sha256':'c'*64}}):
        with pytest.raises(ValueError,match='runtime build'):
            verify_known_image_execution(altered,{},'a'*64,'d'*64,'cpu','b'*64)
    with pytest.raises(ValueError,match='completed image'):
        verify_known_image_execution(runtime,{'state':'error'},'a'*64,'d'*64,'cpu','b'*64)


@pytest.mark.parametrize('inventory_separator', ['/', '\\'])
def test_frozen_data_tree_builds_an_importable_remote_worker_archive(tmp_path, monkeypatch, inventory_separator):
    """The frozen coordinator sends source files, rather than its PYZ imports."""
    import shutil
    import tarfile
    from backend.remote import coordinator
    frozen = tmp_path / 'frozen'
    for source, destination in build.export_resource_files(ROOT):
        target = frozen / destination / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    monkeypatch.setattr(coordinator, '__file__', str(frozen / 'backend/remote/coordinator.py'))
    from scripts.frozen_backend_entry import remote_bundle_diagnostic
    inventory = {'resources': [{'path': str(source.relative_to(ROOT)).replace('/', inventory_separator),
                                'sha256': hashlib.sha256((frozen / destination / source.name).read_bytes()).hexdigest()}
                               for source, destination in build.export_resource_files(ROOT)
                               if source.is_relative_to(ROOT / 'backend')]}
    archive = coordinator._bundle_backend(tmp_path)
    extracted = tmp_path / 'received'
    with tarfile.open(archive) as reader:
        names = set(reader.getnames())
        required = {'backend/training_cli.py', 'backend/api/routes_evaluation.py',
                    'backend/engine/trainer.py', 'backend/remote/__init__.py',
                    'backend/remote/worker.py'}
        assert required <= names, f'Frozen remote archive lacks {sorted(required - names)}'
        reader.extractall(extracted, filter='data')
    result = subprocess.run([sys.executable, '-c', 'import backend.remote.worker'],
                            cwd=extracted, env={**os.environ, 'PYTHONPATH': str(extracted)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    diagnostic = remote_bundle_diagnostic(inventory)
    assert diagnostic['status'] == 'ready'
    assert diagnostic['source_file_count'] == len(inventory['resources'])
    (frozen / 'backend/remote/worker.py').write_text('changed packaged worker')
    with pytest.raises(ValueError, match='checksum'):
        remote_bundle_diagnostic(inventory)
