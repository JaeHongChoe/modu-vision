"""Headerless browser assets retain cookie, project and file authorization."""
import io
import json
from pathlib import Path
from urllib.parse import urlencode
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.tests.test_local_location_recovery import fixture, choose, relogin, ORIGIN, BASE

MINT = '/api/local-host/project-assets'
DOWNLOAD = MINT+'/download'


def asset_fixture(tmp_path, monkeypatch):
    from backend.api import routes_dataset, routes_dataset_metadata, routes_mask_exchange
    f = fixture(tmp_path, monkeypatch)
    result = f.client.post(BASE+'/confirm', json={'candidate_id':choose(f)})
    assert result.status_code == 200, result.text
    session = relogin(f)
    selected = f.client.post('/api/local-host/projects/select', json={'registration_id':f.row['registration_id']})
    assert selected.status_code == 200, selected.text
    f.context = selected.json()['context']; f.session = session
    f.app.include_router(routes_dataset.router)
    f.app.include_router(routes_dataset_metadata.format_router)
    f.app.include_router(routes_mask_exchange.router)
    f.source = Path(f.project['project_dir'])/'source'; f.source.mkdir()
    f.png = f.source/'owned.png'; Image.new('RGB',(16,12),'red').save(f.png)
    f.jpg = f.source/'owned.jpg'; Image.new('RGB',(16,12),'blue').save(f.jpg)
    manifest = Path(f.project['project_dir'])/'project.json'
    config = json.loads(manifest.read_bytes()); config['source_dataset_dir'] = str(f.source)
    manifest.write_text(json.dumps(config))
    f.plain = TestClient(f.app, base_url=ORIGIN)
    f.plain.cookies.set('vision_session', session['token'])
    f.headers = {'X-Vision-Context':json.dumps(f.context),'X-Vision-Project':f.context['project_id']}
    return f


def mint(f):
    result = f.client.post(MINT, headers=f.headers, json={'registration_id':f.row['registration_id']})
    assert result.status_code == 200, result.text
    value = result.json()
    assert value['registration_id'] == f.row['registration_id'] and value['context'] == f.context
    assert 0 < value['expires_in'] <= 28800
    assert isinstance(value['ticket'],str) and value['ticket'] not in {f.session['token'],f.client.headers['X-Vision-Local-Tab'],f.session['csrf_token']}
    return value['ticket']


def url(f, ticket, path=None, **query):
    return (path or '/api/dataset/raw/owned.png')+'?'+urlencode({'file_path':str(f.png),**query,'local_asset_ticket':ticket})


def download(f,ticket,kind,export_id,**changes):
    return f.plain.post(DOWNLOAD,headers={'Origin':ORIGIN},
                        data={'ticket':ticket,'csrf_token':f.session['csrf_token'],
                              'kind':kind,'export_id':export_id,**changes})


def test_current_ticket_serves_real_png_jpeg_and_thumbnail_without_custom_headers(tmp_path,monkeypatch):
    f=asset_fixture(tmp_path,monkeypatch); ticket=mint(f)
    assert f.plain.get('/api/dataset/raw/owned.png',params={'file_path':str(f.png)}).status_code==403
    for path, image in [('/api/dataset/raw/owned.png',f.png),('/api/dataset/raw/owned.jpg',f.jpg),('/api/dataset/thumbnail/owned.png',f.png)]:
        response=f.plain.get(url(f,ticket,path,file_path=str(image)))
        assert response.status_code==200,response.text
        assert Image.open(io.BytesIO(response.content)).size==(16,12)
        assert response.headers['cache-control']=='no-store'
        assert response.headers['referrer-policy']=='no-referrer'


@pytest.mark.parametrize('kind,prefix', [('formats','annotations_'),('masks','masks_')])
def test_ticket_serves_only_owned_zip_through_production_download_handler(tmp_path,monkeypatch,kind,prefix):
    f=asset_fixture(tmp_path,monkeypatch); ticket=mint(f)
    export_id=prefix+'a'*32; export=Path(f.project['project_dir'])/'exports';export.mkdir(exist_ok=True)
    with zipfile.ZipFile(export/(export_id+'.zip'),'w') as archive:archive.writestr('owned.txt','disposable export')
    response=download(f,ticket,kind,export_id)
    assert response.status_code==200,response.text
    assert zipfile.ZipFile(io.BytesIO(response.content)).read('owned.txt')==b'disposable export'
    assert response.request.url.query==b''
    assert 'attachment' in response.headers['content-disposition']
    assert response.headers['x-content-type-options']=='nosniff'


def test_ticket_survives_issuing_native_tab_disposal_but_not_shared_cookie_relogin(tmp_path,monkeypatch):
    f=asset_fixture(tmp_path,monkeypatch); ticket=mint(f)
    assert f.client.post('/api/local-host/session/dispose',json={}).status_code==200
    assert f.plain.get(url(f,ticket)).status_code==200
    fresh=f.app.state.accounts.login('owner','isolated-test-password',transport='cookie')
    f.plain.cookies.set('vision_session',fresh['token'])
    assert f.plain.get(url(f,ticket)).status_code==403


@pytest.mark.parametrize('mutation', ['source','labelset','membership','registration','location','expired'])
def test_changed_bound_scope_refuses_before_project_loader(tmp_path,monkeypatch,mutation):
    from backend.api import routes_project
    f=asset_fixture(tmp_path,monkeypatch); ticket=mint(f)
    project=Path(f.project['project_dir'])
    if mutation=='source':
        path=project/'project.json'; value=json.loads(path.read_bytes());value['source_dataset_dir']=str(tmp_path/'other');path.write_text(json.dumps(value))
    elif mutation=='labelset':
        path=project/'labelsets.json';value=json.loads(path.read_bytes());value['active_id']='ls_'+'b'*12;path.write_text(json.dumps(value))
    elif mutation=='membership':
        with f.app.state.accounts._db() as db:f.app.state.accounts._bump_membership(db,f.row['permission_key'],f.owner['id'])
    elif mutation=='registration':
        with f.registry.db() as db:db.execute('UPDATE locations SET revision=revision+1')
    elif mutation=='location':
        with f.registry.db() as db:db.execute('UPDATE locations SET identity=?',(json.dumps([0,0]),))
    else:
        manager=f.app.state.project_assets; clock=manager.now
        monkeypatch.setattr(manager,'now',lambda:clock()+28801)
    monkeypatch.setattr(routes_project,'_load_project',lambda *_:pytest.fail('invalid ticket reached project loading'))
    response=f.plain.get(url(f,ticket))
    assert response.status_code in {403,409},response.text


@pytest.mark.parametrize('suffix,method', [
    ('/api/project/current','GET'),('/api/accounts/me','GET'),('/api/local-host/status','GET'),
    ('/api/dataset/raw/owned.png','POST'),('/api/data-workbench/derived/not-an-id/image','GET'),
    ('/api/dataset/formats/download/foreign','GET'),
])
def test_asset_reference_cannot_authorize_json_native_mutation_or_invalid_identifiers(tmp_path,monkeypatch,suffix,method):
    f=asset_fixture(tmp_path,monkeypatch); ticket=mint(f)
    response=f.plain.request(method,suffix,params={'local_asset_ticket':ticket})
    assert response.status_code in {400,403},response.text


@pytest.mark.parametrize('change', ['duplicate_ticket','duplicate_selector','extra_selector','foreign_file','conflicting_context','conflicting_tab','bearer',
                                  'conflicting_project','conflicting_workspace','conflicting_actor','duplicate_context','duplicate_tab'])
def test_strict_selector_and_header_conflicts_refuse(tmp_path,monkeypatch,change):
    f=asset_fixture(tmp_path,monkeypatch);ticket=mint(f);target=url(f,ticket);headers={}
    if change=='duplicate_ticket':target+='&local_asset_ticket='+ticket
    elif change=='duplicate_selector':target+='&file_path='+str(f.png)
    elif change=='extra_selector':target+='&dir_path='+str(f.source)
    elif change=='foreign_file':
        foreign=tmp_path/'foreign.png';Image.new('RGB',(2,2)).save(foreign);target=url(f,ticket,file_path=str(foreign))
    elif change=='conflicting_context':headers={'X-Vision-Context':json.dumps({**f.context,'project_id':'foreign'})}
    elif change=='conflicting_tab':headers={'X-Vision-Local-Tab':'foreign'}
    elif change in {'conflicting_project','conflicting_workspace','conflicting_actor'}:
        headers={'X-Vision-'+change.removeprefix('conflicting_'):'foreign'}
    elif change=='duplicate_context':headers=[('X-Vision-Context',json.dumps(f.context)),('X-Vision-Context',json.dumps(f.context))]
    elif change=='duplicate_tab':headers=[('X-Vision-Local-Tab',f.client.headers['X-Vision-Local-Tab'])]*2
    else:headers={'Authorization':'Bearer '+f.session['token']}
    response=f.plain.get(target,headers=headers)
    assert response.status_code in {400,403},response.text


def test_fresh_mint_deduplicates_only_current_scope_and_enforces_bound(tmp_path,monkeypatch):
    f=asset_fixture(tmp_path,monkeypatch);first=mint(f)
    assert mint(f)==first
    with f.app.state.accounts._db() as db:f.app.state.accounts._bump_membership(db,f.row['permission_key'],f.owner['id'])
    assert mint(f)!=first
    f.app.state.project_assets.limit=1
    with f.app.state.accounts._db() as db:f.app.state.accounts._bump_membership(db,f.row['permission_key'],f.owner['id'])
    assert f.client.post(MINT,headers=f.headers,json={'registration_id':f.row['registration_id']}).status_code==429


def test_ticket_deadline_does_not_extend_cookie_expiry_during_scope_io(tmp_path,monkeypatch):
    import hashlib
    import time
    from backend.local_host import project_assets
    f=asset_fixture(tmp_path,monkeypatch);manager=f.app.state.project_assets
    with f.app.state.accounts._db() as db:
        db.execute('UPDATE sessions SET expires=? WHERE token_hash=?',
                   (time.time()+120,hashlib.sha256(f.session['token'].encode()).hexdigest()))
    clock=[100.0];monkeypatch.setattr(manager,'now',lambda:clock[0])
    original=project_assets._read_scope
    def slow_scope(row):
        value=original(row);clock[0]+=60;return value
    monkeypatch.setattr(project_assets,'_read_scope',slow_scope)
    response=f.client.post(MINT,headers=f.headers,json={'registration_id':f.row['registration_id']})
    assert response.status_code==200,response.text
    assert 0<response.json()['expires_in']<=60


def test_read_membership_can_prepare_assets_without_mutation_permission(tmp_path,monkeypatch):
    f=asset_fixture(tmp_path,monkeypatch)
    with f.app.state.accounts._db() as db:
        db.execute("UPDATE members SET role='viewer' WHERE project_id=? AND user_id=?",(f.row['permission_key'],f.owner['id']))
        f.app.state.accounts._bump_membership(db,f.row['permission_key'],f.owner['id'])
    ticket=mint(f)
    assert f.plain.get(url(f,ticket)).status_code==200
    assert f.client.put('/api/project/update',headers=f.headers,json={'name':'forbidden'}).status_code==403
    export_id='annotations_'+'e'*32;exports=Path(f.project['project_dir'])/'exports';exports.mkdir(exist_ok=True)
    with zipfile.ZipFile(exports/(export_id+'.zip'),'w') as archive:archive.writestr('read.txt','read-only export')
    assert download(f,ticket,'formats',export_id).status_code==200


def test_ticket_context_overrides_account_default_for_same_logical_project_copies(tmp_path,monkeypatch):
    f=asset_fixture(tmp_path,monkeypatch);ticket=mint(f)
    copied=tmp_path/'copy';copied.mkdir()
    original=Path(f.project['project_dir'])
    config=json.loads((original/'project.json').read_bytes());config['project_dir']=str(copied)
    (copied/'project.json').write_text(json.dumps(config))
    (copied/'labelsets.json').write_bytes((original/'labelsets.json').read_bytes())
    registration=f.registry.register(f.app,f.owner,config)
    assert registration['workspace_id']!=f.row['workspace_id']
    f.app.state.accounts.select_project(f.owner['id'],registration['permission_key'])
    export_id='annotations_'+'c'*32
    for directory,content in ((original,'original'),(copied,'different copy')):
        exports=directory/'exports';exports.mkdir(exist_ok=True)
        with zipfile.ZipFile(exports/(export_id+'.zip'),'w') as archive:archive.writestr('owned.txt',content)
    response=download(f,ticket,'formats',export_id)
    assert response.status_code==200,response.text
    assert zipfile.ZipFile(io.BytesIO(response.content)).read('owned.txt')==b'original'


def test_refused_ticket_responses_are_private_even_without_a_valid_cookie(tmp_path,monkeypatch):
    f=asset_fixture(tmp_path,monkeypatch);ticket=mint(f)
    for reference in ('asset_'+'0'*48,ticket):
        f.plain.cookies.set('vision_session',f.session['token'] if reference!=ticket else 'invalid')
        response=f.plain.get(url(f,reference))
        assert response.status_code in {401,403}
        assert response.headers['cache-control']=='no-store'
        assert response.headers['referrer-policy']=='no-referrer'


@pytest.mark.parametrize('change', ['csrf','kind','identifier','duplicate','extra','origin','new_cookie','get','url_ticket'])
def test_fixed_zip_form_refuses_untrusted_body_authority(tmp_path,monkeypatch,change):
    f=asset_fixture(tmp_path,monkeypatch);ticket=mint(f);export_id='annotations_'+'d'*32
    body={'ticket':ticket,'csrf_token':f.session['csrf_token'],'kind':'formats','export_id':export_id}
    headers={'Origin':ORIGIN,'Content-Type':'application/x-www-form-urlencoded'}
    if change=='csrf':body['csrf_token']='wrong'
    if change=='kind':body['kind']='any-path'
    if change=='identifier':body['export_id']='../../foreign'
    if change=='extra':body['path']='/foreign'
    if change=='origin':headers['Origin']='https://evil.test'
    if change=='new_cookie':
        value=f.app.state.accounts.login('owner','isolated-test-password',transport='cookie');f.plain.cookies.set('vision_session',value['token']);body['csrf_token']=value['csrf_token']
    raw=urlencode(body)
    if change=='duplicate':raw+='&ticket='+ticket
    if change=='get':response=f.plain.get(DOWNLOAD)
    elif change=='url_ticket':response=f.plain.get('/api/dataset/formats/download/'+export_id,params={'local_asset_ticket':ticket})
    else:response=f.plain.post(DOWNLOAD,content=raw,headers=headers)
    assert response.status_code in {400,403,405,422},response.text
    assert response.headers['cache-control']=='no-store'
    assert response.headers['referrer-policy']=='no-referrer'


def test_zip_form_refuses_linked_export_namespace(tmp_path,monkeypatch):
    f=asset_fixture(tmp_path,monkeypatch);ticket=mint(f);export_id='annotations_'+'f'*32
    foreign=tmp_path/'foreign';foreign.mkdir()
    with zipfile.ZipFile(foreign/(export_id+'.zip'),'w') as archive:archive.writestr('foreign.txt','must not read')
    exports=Path(f.project['project_dir'])/'exports';exports.symlink_to(foreign,target_is_directory=True)
    response=download(f,ticket,'formats',export_id)
    assert response.status_code==403
    assert b'must not read' not in response.content


def test_zip_form_admission_covers_body_read_and_stream_cancellation(tmp_path,monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from starlette.responses import StreamingResponse
    from backend.local_host.location_recovery import RecoveryAdmissionMiddleware
    f=asset_fixture(tmp_path,monkeypatch);ticket=mint(f);co=f.registry.recovery
    async def run():
        reading=asyncio.Event();allow_body=asyncio.Event();streaming=asyncio.Event()
        raw=urlencode({'ticket':ticket,'csrf_token':f.session['csrf_token'],'kind':'formats','export_id':'annotations_'+'a'*32}).encode()
        async def receive():
            reading.set();await allow_body.wait();return {'type':'http.request','body':raw,'more_body':False}
        async def chunks():
            yield b'first';streaming.set();await asyncio.Future()
        async def downstream(scope,receive,send):await StreamingResponse(chunks())(scope,receive,send)
        async def send(message):pass
        scope={'type':'http','asgi':{'spec_version':'2.4'},'scheme':'https','method':'POST','path':DOWNLOAD,'query_string':b'',
               'headers':[(b'cookie',('vision_session='+f.session['token']).encode()),(b'origin',ORIGIN.encode()),(b'content-type',b'application/x-www-form-urlencoded')]}
        task=asyncio.create_task(RecoveryAdmissionMiddleware(downstream,co)(scope,receive,send))
        await asyncio.wait_for(reading.wait(),1)
        with pytest.raises(HTTPException):
            with co.admission.transition(f.owner['id']):pass
        allow_body.set();await asyncio.wait_for(streaming.wait(),1)
        with pytest.raises(HTTPException):
            with co.admission.transition(f.owner['id']):pass
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        with co.admission.transition(f.owner['id']):pass
    asyncio.run(run())


@pytest.mark.parametrize('relative',['project.json','runtime_service/service.json'])
def test_image_ticket_cannot_read_project_json_or_runtime_configuration(tmp_path,monkeypatch,relative):
    from backend.api import routes_project
    f=asset_fixture(tmp_path,monkeypatch);ticket=mint(f)
    target=Path(f.project['project_dir'])/relative
    if relative!='project.json':target.parent.mkdir();target.write_bytes(b'{"token":"synthetic-private-fixture"}')
    monkeypatch.setattr(routes_project,'_load_project',lambda *_:pytest.fail('non-image ticket reached project loader'))
    response=f.plain.get(url(f,ticket,file_path=str(target)))
    assert response.status_code==403,response.text


def test_image_ticket_cannot_alias_project_json_with_an_image_suffix(tmp_path,monkeypatch):
    from backend.api import routes_project
    f=asset_fixture(tmp_path,monkeypatch);ticket=mint(f)
    alias=f.source/'alias.png';alias.symlink_to(Path(f.project['project_dir'])/'project.json')
    monkeypatch.setattr(routes_project,'_load_project',lambda *_:pytest.fail('non-image alias reached project loader'))
    response=f.plain.get(url(f,ticket,file_path=str(alias)))
    assert response.status_code==403,response.text


@pytest.mark.parametrize('headers,body', [({'X-Vision-CSRF':'bad'},None),({'X-Vision-Local-Tab':'bad'},None),({}, {'registration_id':'foreign'}),({}, {'registration_id':'value','confirmed':True})])
def test_ticket_issuance_keeps_csrf_tab_registration_and_strict_body_guards(tmp_path,monkeypatch,headers,body):
    f=asset_fixture(tmp_path,monkeypatch)
    response=f.client.post(MINT,headers={**f.headers,**headers},json=body or {'registration_id':f.row['registration_id']})
    assert response.status_code in {400,403,404,422},response.text
