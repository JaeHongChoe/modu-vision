"""A reviewed recoverable move must retain the preview's exact subject."""
import re

import pytest
from backend.engine.artifact_retention import ArtifactRetention
from backend.tests.test_service_s5_09 import project


@pytest.mark.parametrize('change',['file_content','directory_membership','empty_directory','policy','project','source','mtime','replacement','pin'])
def test_reviewed_preview_refuses_changed_subject_before_any_move(tmp_path,change):
    _,value,root,_,_=project(tmp_path);store=ArtifactRetention(root)
    store.configure(retention_days=0,trash_days=30)
    artifact=root/'reports'/'reviewed';artifact.mkdir();file=artifact/'one';file.write_bytes(b'old bytes')
    preview=store.move_to_trash(['reports/reviewed'],project=value,dry_run=True)
    assert re.fullmatch('[a-f0-9]{64}',preview.get('preview_sha256',''))
    if change=='file_content':file.write_bytes(b'new bytes')
    elif change=='directory_membership':(artifact/'two').write_bytes(b'added')
    elif change=='empty_directory':(artifact/'empty').mkdir()
    elif change=='policy':store.configure(retention_days=0,trash_days=7)
    elif change=='project':value={**value,'id':'another-project'}
    elif change=='source':value={**value,'source_dataset_dir':str(tmp_path/'other-source')}
    elif change=='pin':store.pin('manual:another',['reports/unrelated'],reason='review hold')
    elif change=='replacement':
        import os
        other=artifact/'replacement';other.write_bytes(file.read_bytes());os.replace(other,file)
    elif change=='mtime':
        import os
        stat=file.stat();os.utime(file,ns=(stat.st_atime_ns,stat.st_mtime_ns-1000000000))
    before={p.name:p.read_bytes() for p in artifact.iterdir() if p.is_file()}
    with pytest.raises(ValueError,match='preview changed'):
        store.move_to_trash(['reports/reviewed'],project=value,expected_preview_sha256=preview['preview_sha256'])
    assert artifact.is_dir() and before=={p.name:p.read_bytes() for p in artifact.iterdir() if p.is_file()}
    assert store.status(value)['trash']==[]


def test_preview_reopens_and_exact_selected_bytes_move_once_then_restore(tmp_path):
    _,value,root,_,_=project(tmp_path);store=ArtifactRetention(root);store.configure(retention_days=0,trash_days=30)
    path=root/'reports'/'original';path.write_bytes(b'owned reviewed content')
    preview=store.move_to_trash(['reports/original'],project=value,dry_run=True)
    assert re.fullmatch('[a-f0-9]{64}',preview.get('preview_sha256',''))
    fresh=ArtifactRetention(root)
    assert fresh.move_to_trash(['reports/original'],project=value,dry_run=True)['preview_sha256']==preview['preview_sha256']
    moved=fresh.move_to_trash(['reports/original'],project=value,expected_preview_sha256=preview['preview_sha256'])
    assert not path.exists() and len(moved['trashed'])==1
    fresh.restore_trash(moved['trashed'][0]['trash_id']);assert path.read_bytes()==b'owned reviewed content'


@pytest.mark.parametrize('invalid',[True,1,'short','A'*64,'0'*64])
def test_invalid_or_foreign_review_digest_preserves_original(tmp_path,invalid):
    _,value,root,_,_=project(tmp_path);store=ArtifactRetention(root);store.configure(retention_days=0,trash_days=30)
    path=root/'reports'/'original';path.write_bytes(b'owned bytes')
    with pytest.raises(ValueError,match='preview'):
        store.move_to_trash(['reports/original'],project=value,expected_preview_sha256=invalid)
    assert path.read_bytes()==b'owned bytes' and store.status(value)['trash']==[]


def test_change_to_any_selected_path_refuses_all_movements_and_new_preview_can_retry(tmp_path):
    _,value,root,_,_=project(tmp_path);store=ArtifactRetention(root);store.configure(retention_days=0,trash_days=30)
    first=root/'reports'/'first';first.write_bytes(b'first')
    second=root/'reports'/'second';second.write_bytes(b'second')
    paths=['reports/first','reports/second']
    preview=store.move_to_trash(paths,project=value,dry_run=True)
    second.write_bytes(b'update')
    with pytest.raises(ValueError,match='preview changed'):
        store.move_to_trash(paths,project=value,expected_preview_sha256=preview['preview_sha256'])
    assert first.read_bytes()==b'first' and second.read_bytes()==b'update' and store.status(value)['trash']==[]
    fresh=store.move_to_trash(list(reversed(paths)),project=value,dry_run=True)
    moved=store.move_to_trash(paths,project=value,expected_preview_sha256=fresh['preview_sha256'])
    assert len(moved['trashed'])==2
    for item in moved['trashed']:store.restore_trash(item['trash_id'])
    assert first.read_bytes()==b'first' and second.read_bytes()==b'update'


@pytest.mark.parametrize('invalid',[True,1,'short','A'*64])
def test_public_route_rejects_invalid_preview_before_mutation(tmp_path,invalid):
    api,_,root,_,_=project(tmp_path)
    file=root/'reports'/'route';file.write_bytes(b'route bytes')
    response=api.post('/api/project/retention/trash',json={'paths':['reports/route'],'dry_run':False,'expected_preview_sha256':invalid})
    assert response.status_code==422 and file.read_bytes()==b'route bytes'
    assert api.get('/api/project/retention').json()['trash']==[]


def test_public_route_round_trips_digest_and_preserves_stale_bytes_before_fresh_retry(tmp_path):
    api,_,root,_,_=project(tmp_path)
    assert api.put('/api/project/retention/policy',json={'retention_days':0,'trash_days':7}).status_code==200
    file=root/'reports'/'route';file.write_bytes(b'route old')
    body={'paths':['reports/route'],'dry_run':True}
    preview=api.post('/api/project/retention/trash',json=body).json()
    assert re.fullmatch('[a-f0-9]{64}',preview['preview_sha256'])
    file.write_bytes(b'route new')
    stale=api.post('/api/project/retention/trash',json={**body,'dry_run':False,'expected_preview_sha256':preview['preview_sha256']})
    assert stale.status_code==409 and 'preview changed' in stale.json()['detail']
    assert file.read_bytes()==b'route new' and api.get('/api/project/retention').json()['trash']==[]
    fresh=api.post('/api/project/retention/trash',json=body).json()
    assert fresh['preview_sha256']!=preview['preview_sha256']
    moved=api.post('/api/project/retention/trash',json={**body,'dry_run':False,'expected_preview_sha256':fresh['preview_sha256']})
    assert moved.status_code==200 and len(moved.json()['trashed'])==1 and not file.exists()
    assert api.post('/api/project/retention/restore-trash',json={'trash_id':moved.json()['trashed'][0]['trash_id']}).status_code==200
    assert file.read_bytes()==b'route new'
