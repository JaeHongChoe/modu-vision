"""Disposable public recovery tests; only native UI answers are substituted."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ORIGIN = 'https://localhost:8443'
BASE = '/api/local-host/projects/location-recovery'


class Native:
    ready = {'platform': 'darwin'}
    closed = False
    def __init__(self):
        self.path = None
        self.confirmed = True
        self.after = {}
        self.calls = []
    async def call(self, authority, method, args, **kwargs):
        self.calls.append((method, args))
        callback = self.after.get(method)
        if callback: callback()
        if method == 'host.approve': return {'approved': True}
        if method == 'dialog.folder': return self.path
        if method == 'host.approveProjectLocation':
            return {'approved': self.confirmed, 'approval_digest': args['approval_digest']} if self.confirmed else {'approved': False}
        if method == 'session.disposeAll': return True
        return {}


def fixture(tmp_path, monkeypatch):
    from backend.api.routes_project import router
    from backend.api.shared_authorization import SharedAuthorizationMiddleware
    from backend.contracts.context import ContextRegistry
    from backend.engine.shared_accounts import AccountStore
    from backend.local_host.controller import LocalHostController
    from backend.local_host.projects import LocalProjectRegistry, install_local_projects
    from backend.local_host.routes import local_host_router
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'user-data'))
    app = FastAPI()
    app.state.browser_origins = (ORIGIN,)
    app.state.project_dir = str(tmp_path/'projects')
    app.state.context_registry = ContextRegistry(tmp_path/'projects')
    app.state.accounts = AccountStore(tmp_path/'auth'/'accounts.sqlite')
    owner = app.state.accounts.bootstrap('owner', 'isolated-test-password')
    session = app.state.accounts.login('owner', 'isolated-test-password', transport='cookie')
    app.include_router(router)
    app.add_middleware(SharedAuthorizationMiddleware, project_app=app)
    app.state.api_token = 'isolated'
    registry = LocalProjectRegistry(tmp_path/'registry')
    install_local_projects(app, registry)
    client = TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN})
    client.cookies.set('vision_session', session['token'])
    client.headers['X-Vision-CSRF'] = session['csrf_token']
    project = client.post('/api/project/create', json={'name': 'disposable'}).json()
    registered = registry.register(app, owner, project)
    native = Native(); native.path = project['project_dir']
    controller = LocalHostController(app, native, origin=ORIGIN, state_dir=tmp_path/'helper')
    app.state.local_host = controller
    app.include_router(local_host_router(controller))
    tab = client.post('/api/local-host/session').json()['tab_id']
    client.headers['X-Vision-Local-Tab'] = tab
    assert client.post('/api/local-host/approve').json()['approved']
    with registry.db() as db:
        info = Path(project['project_dir']).stat()
        db.execute('UPDATE locations SET identity=?', (json.dumps([info.st_dev + 1, info.st_ino]),))
    return SimpleNamespace(app=app, client=client, owner=owner, session=session, registry=registry,
                           row=registered, project=project, native=native, controller=controller)


def choose(f):
    response = f.client.post(BASE+'/choose', json={'registration_id': f.row['registration_id'], 'expected_revision': 1})
    assert response.status_code == 200, response.text
    return response.json()['candidate_id']


def stored(f):
    with f.registry.db() as db:
        return dict(db.execute('SELECT * FROM locations').fetchone())


def relogin(f):
    value = f.app.state.accounts.login('owner', 'isolated-test-password', transport='cookie')
    f.client.cookies.set('vision_session', value['token'])
    f.client.headers['X-Vision-CSRF'] = value['csrf_token']
    tab = f.client.post('/api/local-host/session').json()['tab_id']
    f.client.headers['X-Vision-Local-Tab'] = tab
    assert f.client.post('/api/local-host/approve').status_code == 200
    return value


def test_location_mismatch_is_structured_and_discovery_never_loads_project(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    before = stored(f)
    project_bytes = (Path(f.project['project_dir'])/'project.json').read_bytes()
    response = f.client.get('/api/project/current')
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'local_project_location_recovery_required'
    listed = f.client.get(BASE)
    assert listed.status_code == 200, listed.text
    assert listed.json()['registrations'][0]['registration_id'] == f.row['registration_id']
    assert stored(f) == before
    assert (Path(f.project['project_dir'])/'project.json').read_bytes() == project_bytes


def test_native_recovery_revokes_only_self_and_requires_new_explicit_selection(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    before = stored(f)
    manifest = Path(f.project['project_dir'])/'project.json'; original = manifest.read_bytes()
    second = f.app.state.accounts.login('owner', 'isolated-test-password', transport='cookie')
    result = f.client.post(BASE+'/confirm', json={'candidate_id': choose(f)})
    assert result.status_code == 200, result.text
    assert result.json()['requires_relogin'] is True and result.json()['autoactivated'] is False
    after = stored(f)
    assert after['revision'] == 2
    assert {k: before[k] for k in ('project_id','workspace_id','permission_key','path')} == {k: after[k] for k in ('project_id','workspace_id','permission_key','path')}
    assert manifest.read_bytes() == original
    for token in (f.session['token'], second['token']):
        with pytest.raises(ValueError): f.app.state.accounts.authenticate(token)
    assert not f.controller.tabs and not f.controller.grants.rows and not f.controller.runs
    receipt = result.json()['receipt_id']
    relogin(f)
    assert f.client.get('/api/project/current').status_code == 409
    readback = f.client.get(BASE+'/receipts/'+receipt)
    assert readback.status_code == 200 and readback.json()['phase'] == 'committed'
    assert f.client.post('/api/local-host/projects/select', json={'registration_id': f.row['registration_id']}).status_code == 200
    assert f.client.get('/api/project/current').status_code == 200
    assert stored(f)['revision'] == 2


@pytest.mark.parametrize('when', ['dialog.folder', 'host.approveProjectLocation'])
def test_external_session_revocation_wins_before_approved_retirement(tmp_path, monkeypatch, when):
    f = fixture(tmp_path, monkeypatch); before = stored(f)
    f.native.after[when] = lambda: f.app.state.accounts.logout(f.session['token'])
    if when == 'dialog.folder':
        result = f.client.post(BASE+'/choose', json={'registration_id':f.row['registration_id'], 'expected_revision':1})
    else:
        candidate = choose(f)
        result = f.client.post(BASE+'/confirm', json={'candidate_id':candidate})
    assert result.status_code == 401, result.text
    assert stored(f) == before


def test_second_effective_member_refuses_before_native_folder_access(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch); before = stored(f)
    other = f.app.state.accounts.create_user('other', 'isolated-other-password', actor=f.owner['id'])
    f.app.state.accounts.set_membership(f.row['permission_key'], other['id'], 'viewer', f.owner['id'])
    result = f.client.post(BASE+'/choose', json={'registration_id':f.row['registration_id'], 'expected_revision':1})
    assert result.status_code == 409
    assert result.json()['detail']['code'] == 'shared_registration_requires_administrator'
    assert all(method != 'dialog.folder' for method,args in f.native.calls)
    assert stored(f) == before


@pytest.mark.parametrize('change', ['manifest','symlink','different_path','cancel'])
def test_changed_or_unapproved_native_candidate_never_updates_registration(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch); before = stored(f)
    if change == 'different_path':
        other = tmp_path/'other'; other.mkdir(); f.native.path = str(other)
        response = f.client.post(BASE+'/choose', json={'registration_id':f.row['registration_id'],'expected_revision':1})
        assert response.status_code == 409
    else:
        candidate = choose(f)
        if change == 'manifest':
            manifest = Path(f.project['project_dir'])/'project.json'
            manifest.write_bytes(manifest.read_bytes()+b' ')
        if change == 'symlink':
            manifest = Path(f.project['project_dir'])/'project.json'; saved = manifest.with_suffix('.saved')
            manifest.rename(saved); manifest.symlink_to(saved)
        if change == 'cancel': f.native.confirmed = False
        response = f.client.post(BASE+'/confirm', json={'candidate_id':candidate})
        assert response.status_code in {200,409}
        assert not response.json().get('requires_relogin',False)
    assert stored(f) == before


@pytest.mark.parametrize('headers', [ {'Origin':'https://evil.test'}, {'X-Vision-CSRF':'invalid'},
    {'X-Vision-Local-Tab':'wrong'}, {'X-Vision-Project':'foreign'}, {'X-Vision-Remote-Authority':'remote'}])
def test_recovery_route_does_not_accept_foreign_or_context_authority(tmp_path, monkeypatch, headers):
    f = fixture(tmp_path, monkeypatch); before = stored(f)
    result = f.client.post(BASE+'/choose', headers=headers, json={'registration_id':f.row['registration_id'],'expected_revision':1})
    assert result.status_code in {400,403}, result.text
    assert stored(f) == before


def test_interrupted_retirement_requires_new_native_proof_and_carries_old_sessions(tmp_path, monkeypatch):
    f = fixture(tmp_path,monkeypatch); before = stored(f); old = hashlib.sha256(f.session['token'].encode()).hexdigest()
    native_call = f.native.call
    failed = [False]
    async def interrupted(authority,method,args,**kwargs):
        if method == 'session.disposeAll' and not failed[0]:
            failed[0] = True
            raise RuntimeError('disposable interrupted acknowledgement')
        return await native_call(authority,method,args,**kwargs)
    f.native.call = interrupted
    response = f.client.post(BASE+'/confirm',json={'candidate_id':choose(f)})
    assert response.status_code == 503
    assert stored(f) == before
    relogin(f)
    blocked=f.client.post('/api/local-host/projects/select',json={'registration_id':f.row['registration_id']})
    assert blocked.status_code == 409
    assert blocked.json()['detail']['code']=='local_project_location_recovery_required'
    f.controller.sessions.pop(old,None)  # A prior best-effort revocation may already have pruned it.
    retired=[]
    async def acknowledged(authority,method,args,**kwargs):
        if method == 'session.disposeAll': retired.append(authority['session_id'])
        return await native_call(authority,method,args,**kwargs)
    f.native.call = acknowledged
    response = f.client.post(BASE+'/confirm',json={'candidate_id':choose(f)})
    assert response.status_code == 200, response.text
    assert old in retired
    with f.registry.db() as db:
        assert [r['phase'] for r in db.execute('SELECT phase FROM location_recoveries ORDER BY rowid')] == ['superseded','committed']


def test_lost_response_replays_same_committed_receipt_after_fresh_login(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch); candidate=choose(f)
    result=f.client.post(BASE+'/confirm',json={'candidate_id':candidate})
    assert result.status_code==200
    relogin(f)
    retried=f.client.post(BASE+'/confirm',json={'candidate_id':candidate})
    assert retried.status_code==200, retried.text
    assert retried.json()==result.json()
    assert stored(f)['revision']==2


@pytest.mark.parametrize('change',['disable','demote','new_member'])
def test_owner_authority_change_after_retirement_prevents_publication(tmp_path,monkeypatch,change):
    f=fixture(tmp_path,monkeypatch); before=stored(f)
    other=f.app.state.accounts.create_user('other','isolated-other-password',actor=f.owner['id'])
    original=f.controller.retire_recovery_sessions
    async def retire(sessions,actor_id):
        await original(sessions,actor_id)
        with f.app.state.accounts._db() as db:
            if change=='disable': db.execute('UPDATE users SET disabled=1 WHERE id=?',(f.owner['id'],))
            if change=='demote':
                db.execute('UPDATE members SET role=? WHERE project_id=? AND user_id=?',('viewer',f.row['permission_key'],f.owner['id']))
                f.app.state.accounts._bump_membership(db,f.row['permission_key'],f.owner['id'])
            if change=='new_member':db.execute('INSERT INTO members VALUES(?,?,?)',(f.row['permission_key'],other['id'],'viewer'))
    f.controller.retire_recovery_sessions=retire
    response=f.client.post(BASE+'/confirm',json={'candidate_id':choose(f)})
    assert response.status_code in {403,409},response.text
    assert stored(f)==before


def test_admitted_request_prevents_retirement_before_any_persistent_mutation(tmp_path,monkeypatch):
    import concurrent.futures
    import threading
    f=fixture(tmp_path,monkeypatch); before=stored(f); candidate=choose(f)
    entered=threading.Event(); release=threading.Event(); original=f.registry.resolve
    def paused(*args,**kwargs):
        entered.set(); assert release.wait(10);return original(*args,**kwargs)
    f.registry.resolve=paused
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        request=pool.submit(f.client.get,'/api/project/current')
        try:
            assert entered.wait(5)
            result=f.client.post(BASE+'/confirm',json={'candidate_id':candidate})
            assert result.status_code==409 and result.json()['detail']['code']=='location_recovery_busy'
            assert stored(f)==before
            assert f.app.state.accounts.authenticate(f.session['token'])['id']==f.owner['id']
        finally:release.set()
        assert request.result().status_code==409


def test_cookie_websocket_admission_keeps_handshake_counted_until_registration(tmp_path,monkeypatch):
    import asyncio
    from backend.local_host.location_recovery import RecoveryAdmissionMiddleware
    f=fixture(tmp_path,monkeypatch); recovery=f.registry.recovery
    async def run():
        entered=asyncio.Event(); release=asyncio.Event(); messages=[]
        async def downstream(scope,receive,send):
            entered.set(); await release.wait()
            scope['state']['_local_recovery_handshake_complete']()
        middleware=RecoveryAdmissionMiddleware(downstream,recovery)
        scope={'type':'websocket','path':'/ws/telemetry','headers':[(b'cookie',('vision_session='+f.session['token']).encode())],'state':{}}
        async def receive():return {'type':'websocket.connect'}
        async def send(message):messages.append(message)
        task=asyncio.create_task(middleware(scope,receive,send))
        await asyncio.wait_for(entered.wait(),1)
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            with recovery.admission.transition(f.owner['id']):pass
        release.set();await task
        with recovery.admission.transition(f.owner['id']):pass
    asyncio.run(run())


@pytest.mark.parametrize('module_name,lock_name,rows_name',[
    ('backend.engine.labeling_tasks','_LOCK','_LIVE'),
    ('backend.api.routes_label_suggestions','_BATCH_LOCK','_LIVE_BATCHES'),
    ('backend.api.routes_automated_training','_LOCK','_EVENTS'),
    ('backend.api.routes_model_operations','_LOCK','_EVENTS'),
    ('backend.engine.runtime_optimization_jobs','_LOCK','_EVENTS'),
])
def test_unledgered_project_worker_refuses_before_sessions_or_receipts_change(tmp_path,monkeypatch,module_name,lock_name,rows_name):
    import importlib
    f=fixture(tmp_path,monkeypatch);before=stored(f);module=importlib.import_module(module_name)
    rows=getattr(module,rows_name);lock=getattr(module,lock_name)
    with lock: rows['disposable-blocker']=object()
    try:
        result=f.client.post(BASE+'/confirm',json={'candidate_id':choose(f)})
        assert result.status_code==409 and result.json()['detail']['code']=='location_recovery_busy'
        assert stored(f)==before
        assert f.app.state.accounts.authenticate(f.session['token'])['id']==f.owner['id']
        with f.registry.db() as db:assert db.execute('SELECT count(*) FROM location_recoveries').fetchone()[0]==0
    finally:
        with lock: rows.pop('disposable-blocker',None)


@pytest.mark.parametrize('directory',['operations_worker','runtime_service'])
def test_independent_writer_metadata_requires_manual_review_before_retirement(tmp_path,monkeypatch,directory):
    f=fixture(tmp_path,monkeypatch);before=stored(f)
    (Path(f.project['project_dir'])/directory).mkdir()
    response=f.client.post(BASE+'/confirm',json={'candidate_id':choose(f)})
    assert response.status_code==409
    assert response.json()['detail']['code']=='location_recovery_external_worker_review_required'
    assert stored(f)==before
    assert f.app.state.accounts.authenticate(f.session['token'])['id']==f.owner['id']


def test_failed_nofollow_exit_closes_candidate_descriptor(tmp_path,monkeypatch):
    from contextlib import contextmanager
    import os
    from backend.local_host import location_identity
    f=fixture(tmp_path,monkeypatch);original=location_identity.open_directory;calls=[0];duplicates=[];dup=os.dup
    @contextmanager
    def changed(path):
        calls[0]+=1;ordinal=calls[0]
        with original(path) as descriptor:yield descriptor
        if ordinal==1:raise ValueError('disposable concurrent path replacement')
    def capture_dup(fd):
        copied=dup(fd);duplicates.append(copied);return copied
    monkeypatch.setattr(location_identity,'open_directory',changed)
    monkeypatch.setattr(os,'dup',capture_dup)
    response=f.client.post(BASE+'/choose',json={'registration_id':f.row['registration_id'],'expected_revision':1})
    assert response.status_code==409
    assert len(duplicates)==1
    with pytest.raises(OSError):os.fstat(duplicates[0])


def test_concurrent_native_choices_cannot_exceed_candidate_bound(tmp_path,monkeypatch):
    import asyncio
    from fastapi import HTTPException,Request
    f=fixture(tmp_path,monkeypatch);co=f.registry.recovery;original=f.native.call
    async def run():
        entered=0;all_entered=asyncio.Event();release=asyncio.Event()
        async def held(authority,method,args,**kwargs):
            nonlocal entered
            if method=='dialog.folder':
                entered+=1
                if entered==33:all_entered.set()
                await release.wait()
            return await original(authority,method,args,**kwargs)
        f.native.call=held
        def request():
            headers={**dict(f.client.headers),'cookie':'vision_session='+f.session['token']}
            return Request({'type':'http','method':'POST','scheme':'https','path':BASE+'/choose','server':('localhost',8443),'headers':[(key.encode(),value.encode()) for key,value in headers.items()],'app':f.app,'state':{}})
        tasks=[asyncio.create_task(co.choose(request(),f.row['registration_id'],1)) for _ in range(33)]
        await asyncio.wait_for(all_entered.wait(),3);release.set()
        results=await asyncio.gather(*tasks,return_exceptions=True)
        assert sum(isinstance(value,dict) for value in results)==32
        assert sum(isinstance(value,HTTPException) and value.status_code==429 for value in results)==1
        assert len(co.candidates)==32
        for candidate in co.candidates.values():candidate.close()
    asyncio.run(run())


def test_streaming_request_keeps_admission_until_body_finishes_or_is_cancelled(tmp_path,monkeypatch):
    import asyncio
    from backend.local_host.location_recovery import RecoveryAdmissionMiddleware
    from starlette.responses import StreamingResponse
    from fastapi import HTTPException
    f=fixture(tmp_path,monkeypatch);co=f.registry.recovery
    async def run(cancel):
        first=asyncio.Event(); release=asyncio.Event()
        async def chunks():
            yield b'first';first.set();await release.wait();yield b'last'
        response=StreamingResponse(chunks())
        async def downstream(scope,receive,send):await response(scope,receive,send)
        async def receive():await asyncio.Future()
        async def send(message):pass
        middleware=RecoveryAdmissionMiddleware(downstream,co)
        scope={'type':'http','asgi':{'spec_version':'2.4'},'method':'GET','path':'/api/download','headers':[(b'cookie',('vision_session='+f.session['token']).encode())]}
        task=asyncio.create_task(middleware(scope,receive,send));await asyncio.wait_for(first.wait(),1)
        with pytest.raises(HTTPException):
            with co.admission.transition(f.owner['id']):pass
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        else:release.set();await task
        with co.admission.transition(f.owner['id']):pass
    asyncio.run(run(False));asyncio.run(run(True))


def test_existing_and_new_cookie_sockets_are_retired_before_publication(tmp_path,monkeypatch):
    from backend.api import websocket_telemetry
    f=fixture(tmp_path,monkeypatch)
    f.app.include_router(websocket_telemetry.router)
    monkeypatch.setattr(websocket_telemetry,'get_host_telemetry',lambda *_:SimpleNamespace(cpu_percent=0,memory_percent=0,gpu_name='fixture',gpu_memory_used_mb=0,device_type='cpu'))
    mismatched=stored(f)['identity'];info=Path(f.project['project_dir']).stat()
    with f.registry.db() as db:db.execute('UPDATE locations SET identity=?',(json.dumps([info.st_dev,info.st_ino]),))
    with f.client.websocket_connect('wss://localhost:8443/ws/telemetry',headers={'Origin':ORIGIN}) as socket:
        assert socket.receive_json()['event']=='hardware_stats'
        with f.registry.db() as db:db.execute('UPDATE locations SET identity=?',(mismatched,))
        result=f.client.post(BASE+'/confirm',json={'candidate_id':choose(f)})
        assert result.status_code==200,result.text
        assert socket.receive()['type']=='websocket.close'
    with pytest.raises(Exception):
        with f.client.websocket_connect('wss://localhost:8443/ws/telemetry',headers={'Origin':ORIGIN}):pass


def test_v2_default_resolution_cannot_follow_replaced_registration_symlink(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch)
    assert f.client.post(BASE+'/confirm',json={'candidate_id':choose(f)}).status_code==200
    relogin(f)
    assert f.client.post('/api/local-host/projects/select',json={'registration_id':f.row['registration_id']}).status_code==200
    path=Path(f.row['path']);held=path.with_name(path.name+'-held');path.rename(held)
    foreign=tmp_path/'foreign';foreign.mkdir();manifest=foreign/'project.json';raw=json.dumps({'id':f.row['project_id'],'name':'unapproved'}).encode();manifest.write_bytes(raw)
    path.symlink_to(foreign,target_is_directory=True)
    response=f.client.get('/api/project/current')
    assert response.status_code==409,response.text
    assert manifest.read_bytes()==raw


def test_direct_socket_send_is_drained_before_session_retirement_completes(tmp_path,monkeypatch):
    import asyncio
    from backend.api.websocket_telemetry import TelemetryBroadcaster
    f=fixture(tmp_path,monkeypatch)
    async def run():
        entered=asyncio.Event(); release=asyncio.Event();closed=[];bc=TelemetryBroadcaster()
        class Socket:
            scope={'app':f.app,'state':{'account_user':f.owner,'account_session_token':f.session['token'],'account_project_key':f.row['permission_key'],'scoped_project':f.project}}
            async def send_text(self,text):entered.set();await release.wait()
            async def close(self,**kwargs):closed.append(kwargs)
        socket=Socket();bc._active_connections.add(socket);bc._connection_sends[socket]=asyncio.Lock()
        sending=asyncio.create_task(bc.broadcast('hardware_stats',{}));await asyncio.wait_for(entered.wait(),1)
        retirement=asyncio.create_task(bc.retire_account_sessions(f.app,[hashlib.sha256(f.session['token'].encode()).hexdigest()]))
        await asyncio.sleep(0)
        assert not retirement.done(),'publication must wait for the authorized send to finish'
        release.set();await sending;await retirement;assert closed
    asyncio.run(run())


def test_manifest_directory_entry_replacement_is_not_a_matching_held_file(tmp_path,monkeypatch):
    from backend.local_host import location_identity
    from fastapi import HTTPException
    f=fixture(tmp_path,monkeypatch);row=stored(f);manifest=Path(row['path'])/'project.json';original=location_identity.capture
    def replaced(fd):
        identity=original(fd)
        manifest.rename(manifest.with_suffix('.old'))
        manifest.write_text(json.dumps({'id':row['project_id'],'name':'unapproved replacement'}))
        return identity
    monkeypatch.setattr(location_identity,'capture',replaced)
    with location_identity.open_directory(row['path']) as descriptor:
        with pytest.raises(HTTPException):f.registry.recovery.snapshot(row,descriptor)


def test_expiry_inside_publication_reservation_leaves_identity_pending(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch);before=stored(f);identifier=choose(f);co=f.registry.recovery;original=co.owner
    def expire(row,authority,**kwargs):
        value=original(row,authority,**kwargs)
        if kwargs.get('require_session') is False:co.candidates[identifier].expires=0
        return value
    monkeypatch.setattr(co,'owner',expire)
    response=f.client.post(BASE+'/confirm',json={'candidate_id':identifier})
    assert response.status_code==409,response.text
    assert stored(f)==before


def test_pending_receipt_fences_the_registration_for_every_actor(tmp_path,monkeypatch):
    from fastapi import HTTPException
    f=fixture(tmp_path,monkeypatch)
    with f.registry.db() as db:
        db.execute('INSERT INTO location_recoveries VALUES(?,?,?,?,?)',('pending',f.row['registration_id'],f.owner['id'],'retiring','{}'))
        db.execute('INSERT INTO location_reselection VALUES(?,?,?)',(f.row['registration_id'],f.owner['id'],'pending'))
    with pytest.raises(HTTPException):f.registry.recovery.check_access(stored(f),'different-enabled-member')


def test_active_socket_send_refuses_before_receipt_or_session_mutation(tmp_path,monkeypatch):
    import asyncio
    from backend.api import websocket_telemetry
    f=fixture(tmp_path,monkeypatch);before=stored(f);bc=websocket_telemetry.TelemetryBroadcaster()
    class Socket:
        scope={'app':f.app,'state':{'account_user':f.owner,'account_session_token':f.session['token']}}
    socket=Socket();lock=asyncio.Lock()
    asyncio.run(lock.acquire());bc._active_connections.add(socket);bc._connection_sends[socket]=lock
    monkeypatch.setattr(websocket_telemetry,'broadcaster',bc)
    async def not_drained(*args): raise RuntimeError('disposable undrained socket')
    monkeypatch.setattr(bc,'retire_account_sessions',not_drained)
    response=f.client.post(BASE+'/confirm',json={'candidate_id':choose(f)})
    assert response.status_code==409 and response.json()['detail']['code']=='location_recovery_busy'
    assert stored(f)==before
    assert f.app.state.accounts.authenticate(f.session['token'])['id']==f.owner['id']
    with f.registry.db() as db:assert db.execute('SELECT count(*) FROM location_recoveries').fetchone()[0]==0


def test_expiry_inside_retirement_reservation_precedes_all_durable_mutations(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch);before=stored(f);identifier=choose(f);co=f.registry.recovery;original=co.owner;checkpoint=co.retire_checkpoint;inside=[False]
    def retire(candidate,approval):
        inside[0]=True
        try:return checkpoint(candidate,approval)
        finally:inside[0]=False
    def expire(row,authority,**kwargs):
        value=original(row,authority,**kwargs)
        if inside[0] and kwargs.get('db') is not None:co.candidates[identifier].expires=0
        return value
    monkeypatch.setattr(co,'owner',expire);monkeypatch.setattr(co,'retire_checkpoint',retire)
    response=f.client.post(BASE+'/confirm',json={'candidate_id':identifier})
    assert response.status_code==409,response.text
    assert stored(f)==before
    assert f.app.state.accounts.authenticate(f.session['token'])['id']==f.owner['id']
    with f.registry.db() as db:assert db.execute('SELECT count(*) FROM location_recoveries').fetchone()[0]==0


def test_backend_native_selection_refuses_fifo_manifest_without_reading(tmp_path,monkeypatch):
    import os
    f=fixture(tmp_path,monkeypatch);before=stored(f);manifest=Path(f.row['path'])/'project.json'
    manifest.unlink();os.mkfifo(manifest)
    result=f.client.post(BASE+'/choose',json={'registration_id':f.row['registration_id'],'expected_revision':1})
    assert result.status_code==409
    assert stored(f)==before


def test_legacy_administrator_grant_counts_as_another_effective_member(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch);before=stored(f)
    other=f.app.state.accounts.create_user('other-admin','isolated-other-password',administrator=True,actor=f.owner['id'])
    with f.app.state.accounts._db() as db:db.execute('INSERT INTO legacy_admin_grants VALUES(?,?)',(f.row['permission_key'],other['id']))
    response=f.client.post(BASE+'/choose',json={'registration_id':f.row['registration_id'],'expected_revision':1})
    assert response.status_code==409 and response.json()['detail']['code']=='shared_registration_requires_administrator'
    assert all(method!='dialog.folder' for method,args in f.native.calls)
    assert stored(f)==before


@pytest.mark.parametrize('mode',['GET-context','GET-default','HEAD-old-proof','HEAD-no-proof','WS-old-proof','WS-no-proof'])
def test_old_tab_memory_cannot_use_another_tabs_new_shared_cookie_after_reselection(tmp_path,monkeypatch,mode):
    from backend.api import websocket_telemetry
    from starlette.websockets import WebSocketDisconnect
    f=fixture(tmp_path,monkeypatch);old_csrf=f.session['csrf_token'];old_tab=f.client.headers['X-Vision-Local-Tab']
    assert f.client.post(BASE+'/confirm',json={'candidate_id':choose(f)}).status_code==200
    new=relogin(f)
    assert f.client.post('/api/local-host/projects/select',json={'registration_id':f.row['registration_id']}).status_code==200
    headers={'Origin':ORIGIN,'X-Vision-CSRF':old_csrf,'X-Vision-Local-Tab':old_tab}
    if mode not in {'GET-default','HEAD-no-proof'}:headers['X-Vision-Context']=json.dumps(f.row['context'])
    if mode in {'GET-default','HEAD-no-proof'}:headers.pop('X-Vision-Local-Tab')
    old=TestClient(f.app,base_url=ORIGIN,headers=headers);old.cookies.set('vision_session',new['token'])
    if mode.startswith(('GET','HEAD')):
        target='/api/proof-probe' if mode.startswith('HEAD') else '/api/project/current'
        if mode.startswith('HEAD'):
            f.app.add_api_route(target,lambda: {'ok':True},methods=['GET','HEAD'])
        response=old.request('HEAD' if mode.startswith('HEAD') else 'GET',target)
        assert response.status_code==403,response.text
        if mode.startswith('GET'):assert response.json()['detail']['code']=='local_browser_memory_proof_required'
        assert f.client.request('HEAD' if mode.startswith('HEAD') else 'GET',target).status_code==200
    else:
        f.app.include_router(websocket_telemetry.router)
        monkeypatch.setattr(websocket_telemetry,'get_host_telemetry',lambda *_:SimpleNamespace(cpu_percent=0,memory_percent=0,gpu_name='fixture',gpu_memory_used_mb=0,device_type='cpu'))
        protocols=['vision-local-tab.'+old_tab] if mode=='WS-old-proof' else []
        with pytest.raises(WebSocketDisconnect):
            with old.websocket_connect('wss://localhost:8443/ws/telemetry',subprotocols=protocols):pass
        with f.client.websocket_connect('wss://localhost:8443/ws/telemetry',subprotocols=['vision-local-tab.'+f.client.headers['X-Vision-Local-Tab']]) as current:
            assert current.accepted_subprotocol=='vision-local-tab.'+f.client.headers['X-Vision-Local-Tab']
            assert current.receive_json()['event']=='hardware_stats'
    assert f.client.get('/api/project/current').status_code==200


def test_old_tab_cannot_mint_new_native_tab_through_proof_bootstrap_exemptions(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch);old_csrf=f.session['csrf_token']
    assert f.client.post(BASE+'/confirm',json={'candidate_id':choose(f)}).status_code==200
    new=relogin(f)
    old=TestClient(f.app,base_url=ORIGIN,headers={'Origin':ORIGIN,'X-Vision-CSRF':old_csrf});old.cookies.set('vision_session',new['token'])
    for path in ('/api/local-host/capabilities','/api/local-host/status'):
        value=old.get(path);assert value.status_code==200
        assert 'tab_id' not in value.json() and 'csrf_token' not in value.json()
    before=set(f.controller.tabs)
    assert old.post('/api/local-host/session').status_code==403
    assert set(f.controller.tabs)==before


def test_recovered_actor_tab_requirement_is_durable_and_explicit_bearer_remains_separate(tmp_path,monkeypatch):
    from backend.local_host.location_recovery import LocationRecovery
    f=fixture(tmp_path,monkeypatch)
    assert f.client.post(BASE+'/confirm',json={'candidate_id':choose(f)}).status_code==200
    relogin(f)
    assert f.client.post('/api/local-host/projects/select',json={'registration_id':f.row['registration_id']}).status_code==200
    assert f.registry.recovery.pending(f.row['registration_id'],f.owner['id']) is None
    reconstructed=LocationRecovery(f.app,f.registry)
    assert reconstructed.requires_browser_proof(f.owner['id']) is True
    assert reconstructed.requires_browser_proof('unrelated-actor') is False
    token=f.app.state.accounts.login('owner','isolated-test-password',transport='bearer')['token']
    explicit=TestClient(f.app,base_url=ORIGIN,headers={'Authorization':'Bearer '+token,'X-Vision-Context':json.dumps(f.row['context'])})
    assert explicit.get('/api/project/current').status_code==200
