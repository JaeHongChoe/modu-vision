"""Optional packs are installed inertly; failure never edits an active runtime."""
import hashlib
import json
from pathlib import Path

import pytest
from backend.tests.test_runtime_pack_inventory import fixture, module


def install_args(tmp_path):
    root, manifest = fixture(tmp_path)
    raw = module.canonical_bytes(manifest)
    store = tmp_path / 'installed'; store.mkdir()
    (store / 'active-runtime.txt').write_bytes(b'preserve current CPU runtime')
    return root, raw, store, dict(expected_sha256=hashlib.sha256(raw).hexdigest(),
        platform='linux', arch='x64', compatibility={'worker':1,'runtime':1})


def test_atomic_install_and_repeat_verify_real_payload_without_activation(tmp_path):
    root, raw, store, args = install_args(tmp_path)
    before = (root/'runtime.whl').read_bytes()
    first = module.install_pack(root, raw, store=store, **args)
    target = Path(first['installation_path'])
    assert first['state'] == 'installed_inactive_runtime_unqualified'
    assert first['installed'] is True and first['execution_verified'] is False
    assert first['signature_verified'] is False and first['activated'] is False
    assert (target/'payload/runtime.whl').read_bytes() == before
    assert json.loads((target/'receipt.json').read_text()) == first
    assert (target/'inventory.json').read_bytes() == raw
    assert module.install_pack(root, raw, store=store, **args) == first
    assert (root/'runtime.whl').read_bytes() == before
    assert (store/'active-runtime.txt').read_bytes() == b'preserve current CPU runtime'


def test_publication_does_not_replace_even_an_empty_competing_directory(tmp_path):
    source=tmp_path/'staging';source.mkdir();(source/'complete').write_bytes(b'payload')
    target=tmp_path/'competitor';target.mkdir();identity=target.stat().st_ino
    with pytest.raises(FileExistsError):module._publish_directory(source,target)
    assert target.stat().st_ino==identity and list(target.iterdir())==[]
    assert (source/'complete').read_bytes()==b'payload'


def test_target_race_after_last_check_preserves_competitor(tmp_path,monkeypatch):
    root,raw,store,args=install_args(tmp_path)
    publish=module._publish_directory;identities=[]
    def compete(source,target):
        target.mkdir();identities.append((target,target.stat().st_ino))
        publish(source,target)
    monkeypatch.setattr(module,'_publish_directory',compete)
    with pytest.raises(ValueError,match='unreviewed'):
        module.install_pack(root,raw,store=store,**args)
    target,inode=identities[0]
    assert target.stat().st_ino==inode and list(target.iterdir())==[]
    assert not list(store.glob('.pack-*'))
    assert not list(store.glob('.pack-*'))


@pytest.mark.parametrize('damage',['payload','receipt','inventory','extra','link'])
def test_existing_installation_must_verify_and_is_never_repaired_in_place(tmp_path, damage):
    root, raw, store, args = install_args(tmp_path)
    first = module.install_pack(root, raw, store=store, **args)
    target = Path(first['installation_path'])
    if damage=='payload': (target/'payload/runtime.whl').write_bytes(b'tampered')
    elif damage=='receipt':
        receipt=json.loads((target/'receipt.json').read_text());receipt['activated']=True
        (target/'receipt.json').write_text(json.dumps(receipt))
    elif damage=='inventory': (target/'inventory.json').write_bytes(b'{}')
    elif damage=='extra': (target/'unreviewed.py').write_bytes(b'never execute')
    else:
        (target/'payload/runtime.whl').unlink();(target/'payload/runtime.whl').symlink_to(root/'runtime.whl')
    with pytest.raises(ValueError): module.install_pack(root, raw, store=store, **args)
    assert (store/'active-runtime.txt').read_bytes() == b'preserve current CPU runtime'


def test_interrupted_copy_leaves_no_installed_or_active_candidate(tmp_path, monkeypatch):
    root, raw, store, args = install_args(tmp_path)
    def interrupt(*_): raise OSError('owned injected disk-full copy failure')
    monkeypatch.setattr(module, '_copy_payload', interrupt)
    with pytest.raises(OSError, match='disk-full'): module.install_pack(root, raw, store=store, **args)
    assert sorted(p.name for p in store.iterdir()) == ['active-runtime.txt']
    assert (root/'runtime.whl').is_file()


def test_pack_changed_during_copy_is_refused_before_publication(tmp_path, monkeypatch):
    root, raw, store, args = install_args(tmp_path)
    copy = module._copy_payload
    def changed(*values):
        copy(*values); (root/'runtime.whl').write_bytes(b'changed during staging')
    monkeypatch.setattr(module, '_copy_payload', changed)
    with pytest.raises(ValueError): module.install_pack(root, raw, store=store, **args)
    assert sorted(p.name for p in store.iterdir()) == ['active-runtime.txt']


def test_target_appearing_during_copy_is_preserved_even_when_empty(tmp_path, monkeypatch):
    root, raw, store, args = install_args(tmp_path)
    copy = module._copy_payload
    target = store / ('ocr-fixture-1.0.0-' + args['expected_sha256'])
    def occupied(*values):
        copy(*values); target.mkdir()
    monkeypatch.setattr(module, '_copy_payload', occupied)
    with pytest.raises(ValueError, match='unreviewed'):
        module.install_pack(root, raw, store=store, **args)
    assert list(target.iterdir()) == []


@pytest.mark.parametrize('damage',['linked_store','inside_source','target_mismatch','no_space'])
def test_install_boundary_and_observed_target_are_required(tmp_path, monkeypatch, damage):
    root, raw, store, args = install_args(tmp_path)
    if damage=='linked_store':
        alias=tmp_path/'alias';alias.symlink_to(store);store=alias
    elif damage=='inside_source': store=root
    elif damage=='target_mismatch': args['platform']='windows'
    else:
        monkeypatch.setattr(module.shutil,'disk_usage',lambda *_: type('Disk',(),{'free':0})())
    with pytest.raises(ValueError): module.install_pack(root, raw, store=store, **args)
    assert (root/'runtime.whl').is_file()
