"""S6-03 local inventory verifies bytes without importing or installing packs."""
import hashlib
import json
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('runtime_pack', Path(__file__).parents[2] / 'scripts/runtime_pack.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(tmp_path):
    root = tmp_path / 'pack'; root.mkdir()
    (root / 'runtime.whl').write_bytes(b'never executed, an inventory fixture only')
    descriptor = {'id':'ocr-fixture', 'version':'1.0.0', 'kind':'ocr', 'platform':'linux', 'arch':'x64',
                  'source':'https://example.invalid/reviewed-runtime', 'license':'MIT',
                  'driver_minimum':None, 'compatibility':{'worker':1,'runtime':1},
                  'files':['runtime.whl']}
    manifest = module.inventory_pack(root, descriptor)
    return root, manifest


def test_inventory_reports_actual_storage_and_does_not_execute_or_install(tmp_path):
    root, manifest = fixture(tmp_path)
    assert manifest['total_bytes'] == (root/'runtime.whl').stat().st_size
    raw = module.canonical_bytes(manifest); pin = hashlib.sha256(raw).hexdigest()
    receipt = module.verify_pack(root, raw, expected_sha256=pin, platform='linux', arch='x64',
                                 compatibility={'worker':1,'runtime':1}, free_bytes=100000)
    assert receipt['state'] == 'inventory_verified_runtime_unqualified'
    assert receipt['installed'] is False and receipt['execution_verified'] is False
    assert receipt['estimated_staging_bytes'] == manifest['total_bytes'] * 3
    assert list(root.iterdir()) == [root/'runtime.whl']


@pytest.mark.parametrize('damage',['extra','missing','bytes','symlink'])
def test_missing_extra_mutated_and_linked_payloads_are_refused(tmp_path, damage):
    root, manifest = fixture(tmp_path)
    raw = module.canonical_bytes(manifest); pin = hashlib.sha256(raw).hexdigest()
    if damage == 'extra': (root/'extra.py').write_text('raise AssertionError("not executed")')
    if damage == 'missing': (root/'runtime.whl').unlink()
    if damage == 'bytes': (root/'runtime.whl').write_bytes(b'mutated')
    if damage == 'symlink':
        original = tmp_path/'outside.whl'; original.write_bytes((root/'runtime.whl').read_bytes())
        (root/'runtime.whl').unlink(); (root/'runtime.whl').symlink_to(original)
    with pytest.raises(ValueError):
        module.verify_pack(root, raw, expected_sha256=pin, platform='linux', arch='x64',
                           compatibility={'worker':1,'runtime':1}, free_bytes=100000)


@pytest.mark.parametrize('change',[{'platform':'windows'}, {'arch':'arm64'}, {'compatibility':{'worker':2,'runtime':1}},
                                  {'free_bytes':0}, {'expected_sha256':'0'*64}])
def test_target_protocol_storage_and_manifest_pin_are_required(tmp_path, change):
    root, manifest = fixture(tmp_path); raw = module.canonical_bytes(manifest)
    args = dict(expected_sha256=hashlib.sha256(raw).hexdigest(),platform='linux',arch='x64',
                compatibility={'worker':1,'runtime':1},free_bytes=100000)
    args.update(change)
    with pytest.raises(ValueError): module.verify_pack(root, raw, **args)


def test_cuda_inventory_requires_explicit_numeric_driver_and_never_guesses_cuda(tmp_path):
    root, manifest = fixture(tmp_path)
    manifest.update(kind='nvidia',driver_minimum='560.28.03')
    raw = module.canonical_bytes(manifest)
    args = dict(expected_sha256=hashlib.sha256(raw).hexdigest(),platform='linux',arch='x64',
                compatibility={'worker':1,'runtime':1},free_bytes=100000)
    for driver in [None,'unknown','559.99','999999999999999.0']:
        with pytest.raises(ValueError): module.verify_pack(root, raw, driver_version=driver, **args)
    result = module.verify_pack(root, raw, driver_version='560.35.03', **args)
    assert result['execution_verified'] is False


def test_traversal_noncanonical_json_and_unreviewed_source_are_refused(tmp_path):
    root, manifest = fixture(tmp_path)
    manifest['files'][0]['path'] = '../outside.whl'
    raw = module.canonical_bytes(manifest)
    with pytest.raises(ValueError):
        module.verify_pack(root,raw,expected_sha256=hashlib.sha256(raw).hexdigest(),platform='linux',arch='x64',
                           compatibility={'worker':1,'runtime':1},free_bytes=100000)
    manifest['files'][0]['path']='runtime.whl'; manifest['source']='http://unreviewed.invalid/'
    descriptor={k:v for k,v in manifest.items() if k in module.DESCRIPTOR_FIELDS}; descriptor['files']=['runtime.whl']
    with pytest.raises(ValueError): module.inventory_pack(root,descriptor)
    manifest['source']='https://example.invalid/reviewed-runtime'
    raw=json.dumps(manifest,indent=2).encode()
    with pytest.raises(ValueError):
        module.verify_pack(root,raw,expected_sha256=hashlib.sha256(raw).hexdigest(),platform='linux',arch='x64',
                           compatibility={'worker':1,'runtime':1},free_bytes=100000)
