"""Prediction images use admitted source identity without requiring Library activation."""
import hashlib
import json
from pathlib import Path
import uuid

import pytest
from PIL import Image

from tests.test_geti_platform_authority import gateway


def image_gateway(tmp_path):
    app, client = gateway(tmp_path)
    project = app.state.fixture_project
    root = Path(project['source_dataset_dir'])
    root.mkdir()
    for name, color in [('red/a.png','red'),('red/b.png','blue'),('incoming/c.png','green')]:
        file=root/name;file.parent.mkdir(exist_ok=True);Image.new('RGB',(12,10),color).save(file)
    rows=[]
    for index,name in enumerate(('red/a.png','red/b.png'),1):
        rows.append({'relative_path':name,'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest(),
            'media_id':f'00000000-0000-4000-8000-{index:012d}','image_uuid':'native-'+str(index),'uploaded_sha256':'0'*64,'subset':'training'})
    receipt={'id':'sealed-receipt','project_id':project['id'],'task':'classification','labelset_id':'default',
        'source_root':str(root),'source_mapping':rows,'counts':{'training':2,'validation':0,'testing':0,'total':2}}
    app.state.geti_store.save_dataset('scope-project-a',receipt)
    project_root=Path(project['project_dir']);project_root.mkdir()
    (project_root/'project.json').write_text(json.dumps(project))
    (project_root/'labelsets.json').write_text(json.dumps({'schema_version':1,'active_id':'default','labelsets':[{'id':'default','name':'기본 라벨'}]}))
    return app,client,project,root,receipt


def test_images_preserve_frozen_ids_hashes_and_admit_new_source_without_library_or_child(tmp_path):
    app,client,project,root,receipt=image_gateway(tmp_path)
    foreign=tmp_path/'foreign.png';Image.new('RGB',(12,10),'white').save(foreign)
    (root/'red/escaped.png').symlink_to(foreign)
    Image.new('RGB',(12,10),'white').save(root/'red/.hidden.png')
    source_before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.is_symlink()}
    reply=client.get('/api/platform/geti/images?offset=0&limit=2')
    assert reply.status_code==200,reply.text
    first=reply.json();assert first['pagination']=={'offset':0,'limit':2,'total':3}
    assert [r['relative_path'] for r in first['items']]==['incoming/c.png','red/a.png']
    new,transferred=first['items']
    assert new['transferred'] is False and str(uuid.UUID(new['image_uuid']))==new['image_uuid']
    assert new['source_path']==str(root/'incoming/c.png') and new['sha256']==hashlib.sha256((root/'incoming/c.png').read_bytes()).hexdigest()
    assert transferred=={'image_uuid':receipt['source_mapping'][0]['media_id'],'relative_path':'red/a.png','file_name':'a.png',
        'source_path':str(root/'red/a.png'),'sha256':receipt['source_mapping'][0]['sha256'],'transferred':True}
    second=client.get('/api/platform/geti/images?offset=2&limit=2').json()
    assert [r['relative_path'] for r in second['items']]==['red/b.png']
    assert second['pagination']=={'offset':2,'limit':2,'total':3}
    assert client.get('/api/platform/geti/images?offset=99&limit=2').json()['items']==[]
    assert client.get('/api/platform/geti/images?offset=0&limit=1').json()['items'][0]['image_uuid']==new['image_uuid']
    assert app.state.geti_store.get_dataset('scope-project-a')==receipt
    assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.is_symlink()}==source_before
    assert app.state.geti_runtime.starts==0
    assert not list(Path(project['project_dir']).rglob('*library*'))


@pytest.mark.parametrize('query',['offset=-1','limit=0','limit=201'])
def test_image_pagination_bounds_refuse_before_source_or_child(tmp_path,query):
    app,client,*_=image_gateway(tmp_path)
    assert client.get('/api/platform/geti/images?'+query).status_code==422
    assert app.state.geti_runtime.starts==0


@pytest.mark.parametrize('field,value',[('task','anomaly'),('source_dataset_dir','other-source'),('active_labelset_id','other-labels')])
def test_images_reject_stale_parent_binding_without_discarding_receipt(tmp_path,field,value):
    app,client,project,root,receipt=image_gateway(tmp_path)
    project[field]=str(tmp_path/value) if field=='source_dataset_dir' else value
    assert client.get('/api/platform/geti/images').status_code==409
    assert app.state.geti_store.get_dataset('scope-project-a')==receipt
    assert app.state.geti_runtime.starts==0


def test_images_cannot_choose_other_project_source_and_revoked_membership_denies(tmp_path):
    app,client,project,root,receipt=image_gateway(tmp_path)
    other=tmp_path/'other';other.mkdir();Image.new('RGB',(12,10),'white').save(other/'secret.png')
    response=client.get('/api/platform/geti/images',params={'project_id':'foreign','folder_path':str(other)})
    assert response.status_code==200,response.text
    assert all(Path(row['source_path']).is_relative_to(root) for row in response.json()['items'])
    app.state.accounts.role=None
    assert client.get('/api/platform/geti/images').status_code==403
    assert app.state.geti_runtime.starts==0


def test_changed_transferred_original_is_not_advertised_with_its_old_hash(tmp_path):
    app,client,_,root,receipt=image_gateway(tmp_path)
    Image.new('RGB',(12,10),'black').save(root/'red/a.png')
    assert client.get('/api/platform/geti/images').status_code==409
    assert app.state.geti_store.get_dataset('scope-project-a')==receipt
    assert app.state.geti_runtime.starts==0


def test_image_read_rechecks_membership_before_returning_paths(tmp_path,monkeypatch):
    from backend.api import routes_platform_geti
    app,client,*_=image_gateway(tmp_path)
    original=routes_platform_geti._source_image_sha256
    def revoke_after_read(*args,**kwargs):
        result=original(*args,**kwargs);app.state.accounts.role=None;return result
    monkeypatch.setattr(routes_platform_geti,'_source_image_sha256',revoke_after_read)
    assert client.get('/api/platform/geti/images').status_code==403
    assert app.state.geti_runtime.starts==0


@pytest.mark.parametrize('change',['manifest-task','manifest-source','labelset','receipt'])
def test_image_read_rechecks_live_context_and_receipt_without_writing(tmp_path,monkeypatch,change):
    from backend.api import routes_platform_geti
    app,client,project,root,receipt=image_gateway(tmp_path)
    original=routes_platform_geti._source_image_sha256
    def change_after_read(*args,**kwargs):
        result=original(*args,**kwargs)
        if change.startswith('manifest'):
            manifest={**project}
            manifest['task' if change=='manifest-task' else 'source_dataset_dir']='anomaly' if change=='manifest-task' else str(tmp_path/'other')
            (Path(project['project_dir'])/'project.json').write_text(json.dumps(manifest))
        elif change=='labelset':
            (Path(project['project_dir'])/'labelsets.json').write_text(json.dumps({'active_id':'other-labels'}))
        else: app.state.geti_store.save_dataset('scope-project-a',{**receipt,'id':'new-receipt'})
        return result
    monkeypatch.setattr(routes_platform_geti,'_source_image_sha256',change_after_read)
    assert client.get('/api/platform/geti/images').status_code==409
    assert app.state.geti_runtime.starts==0


def test_image_read_rechecks_session_and_exact_physical_location_membership(tmp_path,monkeypatch):
    from backend.api import routes_platform_geti
    app,client,*_=image_gateway(tmp_path)
    @app.middleware('http')
    async def location_session(request,next):
        request.state.account_project_key='registration-owned'
        request.state.account_session_token='owned-session'
        return await next(request)
    app.state.accounts.authenticate=lambda token:{'id':'actor-a'}
    app.state.accounts.project_role_exact=lambda actor,key:app.state.accounts.role if key=='registration-owned' else None
    original=routes_platform_geti._source_image_sha256
    def expire_after_read(*args,**kwargs):
        result=original(*args,**kwargs)
        def expired(token):raise ValueError('Session expired')
        app.state.accounts.authenticate=expired
        return result
    monkeypatch.setattr(routes_platform_geti,'_source_image_sha256',expire_after_read)
    assert client.get('/api/platform/geti/images').status_code==401
    app.state.accounts.authenticate=lambda token:{'id':'actor-a'}
    app.state.accounts.project_role_exact=lambda actor,key:None
    assert client.get('/api/platform/geti/images').status_code==403
    assert app.state.geti_runtime.starts==0


def test_missing_owned_receipt_cannot_borrow_another_scope(tmp_path):
    app,client,_,_,receipt=image_gateway(tmp_path)
    from backend.platforms.geti.bindings import PlatformStore
    isolated=PlatformStore(tmp_path/'other-ledger')
    isolated.save_dataset('scope-project-b',receipt)
    app.state.geti_store=isolated
    assert client.get('/api/platform/geti/images').status_code==409
    assert app.state.geti_runtime.starts==0
