"""Compatibility gates and recoverable manifest migration preserve user state."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from backend.api import routes_project


def manifest(root, schema=None):
    root.mkdir()
    value = {'id':'project','name':'Inspection','task':'classification',
             'project_dir':'/old/workspace','dataset_dir':'/old/workspace/dataset',
             'models_dir':'/old/workspace/models','reports_dir':'/old/workspace/reports',
             'annotations_dir':'/old/workspace/annotations','created_at':'now','updated_at':'now',
             'extension':{'future_metadata':[1,2]}}
    if schema is not None: value['schema_version']=schema
    (root/'project.json').write_text(json.dumps(value))
    return value


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_future_schema_rejected_before_any_write(tmp_path):
    root=tmp_path/'future';manifest(root,999);before=tree(root)
    with pytest.raises(HTTPException,match='') as failure: routes_project._load_project(root)
    assert failure.value.status_code==422
    assert tree(root)==before


def test_project_normalization_permission_failure_identifies_underlying_io_operation(tmp_path, monkeypatch):
    root=tmp_path/'project';manifest(root,1)
    original=(root/'project.json').read_bytes()
    def denied_read(*args, **kwargs):
        failure=PermissionError(13, 'Permission denied')
        failure.winerror=32
        raise failure
    monkeypatch.setattr(routes_project,'normalize_legacy_manifest',denied_read)
    with pytest.raises(HTTPException) as failure:routes_project._load_project(root)
    assert failure.value.status_code==422
    assert 'stage=legacy-normalization' in failure.value.detail
    assert 'errno=13' in failure.value.detail and 'winerror=32' in failure.value.detail
    assert 'source=denied_read' in failure.value.detail
    assert (root/'project.json').read_bytes()==original
    assert not (root/'labelsets.json').exists()


def test_legacy_schema_has_original_backup_and_preserves_unknown_fields(tmp_path):
    root=tmp_path/'legacy';manifest(root);original=(root/'project.json').read_bytes()
    result=routes_project._load_project(root)
    assert result['schema_version']==1
    saved=json.loads((root/'project.json').read_text())
    assert saved['extension']=={'future_metadata':[1,2]}
    receipts=list((root/'.migrations').glob('*/receipt.json'));assert len(receipts)==1
    receipt=json.loads(receipts[0].read_text());assert receipt['status']=='applied'
    assert (receipts[0].parent/'project.original.json').read_bytes()==original
    assert receipt['original_sha256']==hashlib.sha256(original).hexdigest()
    routes_project._load_project(root)
    assert len(list((root/'.migrations').glob('*/receipt.json')))==1


def test_preview_zero_writes_and_stale_apply_is_rejected(tmp_path):
    from backend.engine.project_migration import preview_migration,apply_migration,MigrationError
    root=tmp_path/'project';manifest(root);before=tree(root)
    preview=preview_migration(root);assert preview['migration_required']
    assert tree(root)==before
    (root/'project.json').write_text(json.dumps({**json.loads((root/'project.json').read_text()),'name':'Changed'}))
    changed=tree(root)
    with pytest.raises(MigrationError,match='changed'):apply_migration(root,preview['manifest_sha256'])
    assert tree(root)==changed


def test_interrupted_apply_recovers_without_overwriting_later_edits(tmp_path,monkeypatch):
    from backend.engine import project_migration as migration
    root=tmp_path/'project';manifest(root);original=(root/'project.json').read_bytes()
    writer=migration._atomic_json
    def fail_receipt(path,value):
        if path.name=='receipt.json' and value['status']=='applied':raise OSError('disk failure')
        return writer(path,value)
    monkeypatch.setattr(migration,'_atomic_json',fail_receipt)
    with pytest.raises(migration.MigrationError,match='disk failure'):migration.apply_migration(root)
    assert (root/'project.json').read_bytes()==original
    monkeypatch.setattr(migration,'_atomic_json',writer)
    assert migration.apply_migration(root)['status']=='applied'
    assert migration.apply_migration(root)['status']=='current'


def test_failed_legacy_overlay_keeps_previous_active_project(tmp_path,monkeypatch):
    root=tmp_path/'project';manifest(root,1);project=routes_project._load_project(root)
    state=SimpleNamespace(project_dir=tmp_path/'history',current_project={'id':'previous'})
    request=SimpleNamespace(state=SimpleNamespace(),app=SimpleNamespace(state=state))
    monkeypatch.setattr(routes_project,'migrate_legacy_dataset_overlay',lambda p:(_ for _ in ()).throw(ValueError('invalid overlay')))
    with pytest.raises(HTTPException):routes_project._activate_project(request,project)
    assert state.current_project=={'id':'previous'}
    assert not (state.project_dir/'.active_project.json').exists()


def test_invalid_backup_is_a_controlled_archive_error(tmp_path):
    from backend.engine.project_archive import restore_archive,ArchiveError
    archive=tmp_path/'broken.zip';archive.write_bytes(b'not a zip')
    with pytest.raises(ArchiveError):restore_archive(archive,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


def test_receipt_after_process_exit_recovers_only_exact_normalized_output(tmp_path):
    from backend.engine.project_migration import apply_migration,MigrationError
    root=tmp_path/'project';manifest(root)
    applied=apply_migration(root);record=root/'.migrations'/applied['receipt']['migration_id']/'receipt.json'
    receipt=json.loads(record.read_text());receipt['status']='prepared';record.write_text(json.dumps(receipt))
    recovered=apply_migration(root);assert recovered['receipt']['recovered']
    receipt['status']='prepared';record.write_text(json.dumps(receipt))
    saved=json.loads((root/'project.json').read_text());saved['name']='Later edit';(root/'project.json').write_text(json.dumps(saved))
    before=(root/'project.json').read_bytes()
    with pytest.raises(MigrationError,match='current edits'):apply_migration(root)
    assert (root/'project.json').read_bytes()==before


def test_project_open_does_not_inventory_live_artifacts_but_explicit_preview_still_refuses(tmp_path, monkeypatch):
    from backend.engine.project_migration import preview_migration, MigrationError
    root=tmp_path/'live';manifest(root,1)
    model=root/'models'/'completed';model.mkdir(parents=True)
    checkpoint=model/'best_model.pt';checkpoint.write_bytes(b'preserved completed model')
    def live_control_reader(*args, **kwargs):
        raise PermissionError(13, 'live coordination reader denied')
    monkeypatch.setattr('backend.engine.migration_inventory.project_snapshot',live_control_reader)
    loaded=routes_project._load_project(root)
    assert loaded['schema_version']==1 and loaded['id']=='project'
    assert checkpoint.read_bytes()==b'preserved completed model'
    with pytest.raises(MigrationError,match='live coordination reader denied'):
        preview_migration(root)


def test_project_open_refuses_linked_ancestor_before_any_write(tmp_path):
    root=tmp_path/'owned'/'project';root.parent.mkdir();manifest(root,1)
    linked=tmp_path/'linked';linked.symlink_to(root.parent,target_is_directory=True)
    before=tree(root)
    with pytest.raises(HTTPException) as failure:routes_project._load_project(linked/'project')
    assert failure.value.status_code==422
    assert tree(root)==before
