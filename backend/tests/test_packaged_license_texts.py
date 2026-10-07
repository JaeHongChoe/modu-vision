"""Distributed license bytes are separate from a public release decision."""
import hashlib
import json
from pathlib import Path

import pytest


def frozen(tmp_path):
    build = tmp_path/'backend'; internal = build/'_internal'
    info = internal/'example-1.2.dist-info'; info.mkdir(parents=True)
    (build/'vision_ai_backend').write_bytes(b'controlled executable')
    (info/'METADATA').write_text('Name: example\nVersion: 1.2\nLicense: MIT\n')
    (info/'licenses').mkdir()
    text = info/'licenses'/'LICENSE'; text.write_bytes(b'MIT license\nCopyright example\n')
    return build, text


def test_frozen_license_bundle_preserves_original_bytes_and_reports_missing_module_metadata(tmp_path):
    from scripts.package_license_texts import collect_frozen_licenses, verify_license_bundle
    build, original = frozen(tmp_path)
    toc = tmp_path/'PYZ-00.toc'; toc.write_text(repr([('unmapped_vendor_module', '/controlled.py', 'PYMODULE')]))
    before = original.read_bytes()
    destination = tmp_path/'licenses'
    receipt = collect_frozen_licenses(build, toc, destination)
    assert receipt['status']=='partial'
    assert receipt['missing']==['unmapped_module:unmapped_vendor_module']
    row = receipt['files'][0]
    assert (destination/row['path']).read_bytes()==before
    assert row['sha256']==hashlib.sha256(before).hexdigest()
    assert original.read_bytes()==before
    assert '/controlled' not in json.dumps(receipt) and str(tmp_path) not in json.dumps(receipt)
    verify_license_bundle(destination)
    (destination/row['path']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'): verify_license_bundle(destination)


def test_frozen_missing_text_never_synthesizes_license_from_identifier(tmp_path):
    from scripts.package_license_texts import collect_frozen_licenses
    build, original = frozen(tmp_path); original.unlink()
    toc = tmp_path/'PYZ-00.toc'; toc.write_text('[]')
    receipt = collect_frozen_licenses(build, toc, tmp_path/'licenses')
    assert receipt['status']=='partial'
    assert receipt['missing']==['license_text:example@1.2']
    assert receipt['files']==[]


@pytest.mark.parametrize('kind',['leaf','parent','output'])
def test_frozen_bundle_refuses_linked_inputs_or_existing_destination(tmp_path,kind):
    from scripts.package_license_texts import collect_frozen_licenses
    build, original = frozen(tmp_path)
    toc=tmp_path/'PYZ-00.toc';toc.write_text('[]')
    destination=tmp_path/'licenses'
    if kind=='leaf':
        other=tmp_path/'other';other.write_bytes(original.read_bytes());original.unlink();original.symlink_to(other)
    elif kind=='parent':
        folder=original.parent;other=folder.with_name('actual');folder.rename(other);folder.symlink_to(other,target_is_directory=True)
    else:destination.mkdir()
    with pytest.raises(ValueError,match='linked|exists'):collect_frozen_licenses(build,toc,destination)
    assert not (destination/'manifest.json').exists()


def test_bundle_verifier_refuses_traversal_and_unlisted_license_bytes(tmp_path):
    from scripts.package_license_texts import collect_frozen_licenses,verify_license_bundle
    build,_=frozen(tmp_path);toc=tmp_path/'PYZ-00.toc';toc.write_text('[]');destination=tmp_path/'licenses'
    collect_frozen_licenses(build,toc,destination)
    (destination/'unlisted.txt').write_text('not recorded')
    with pytest.raises(ValueError,match='unlisted'):verify_license_bundle(destination)
    (destination/'unlisted.txt').unlink()
    manifest=destination/'manifest.json';receipt=json.loads(manifest.read_text());receipt['files'][0]['path']='../outside'
    manifest.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='path'):verify_license_bundle(destination)
