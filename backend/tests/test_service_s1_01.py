"""Project identity is frozen at the authenticated production request boundary."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sqlite3
import pytest
from fastapi import HTTPException

from fastapi.testclient import TestClient
from PIL import Image

from backend.main import create_app


def _local(tmp_path):
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    projects = [client.post('/api/project/create', json={'name': name, 'task': 'detection'}).json()
                for name in ('First', 'Second')]
    return app, client, projects


def test_local_explicit_project_id_does_not_use_last_global_selection(tmp_path):
    app, client, (first, second) = _local(tmp_path)
    response = client.get('/api/project/current', headers={'X-Vision-Project': first['id']})
    assert response.status_code == 200, response.text
    assert response.json()['id'] == first['id']
    assert app.state.current_project['id'] == second['id']
    assert client.get('/api/project/current').json()['id'] == second['id']


def test_parallel_production_annotations_stay_in_explicit_project(tmp_path):
    _, client, projects = _local(tmp_path)
    image = tmp_path / 'shared-source' / 'same.png'
    image.parent.mkdir(); Image.new('RGB', (16, 16), 'white').save(image)
    for project in projects:
        assert client.post('/api/project/open', json={'project_dir': project['project_dir']}).status_code == 200
        assert client.put('/api/project/update', json={'source_dataset_dir': str(image.parent)}).status_code == 200
        assert client.post('/api/annotations/save', json={
            'image_id': 'same', 'image_path': str(image), 'image_width': 16, 'image_height': 16,
            'annotations': [{'type': 'bbox', 'label': project['name'], 'bbox': [1, 1, 5, 5]}],
        }).status_code == 200
    def read(project):
        response = client.get('/api/annotations/same', params={'file_path': str(image)},
                              headers={'X-Vision-Project': project['id']})
        assert response.status_code == 200, response.text
        return response.json()['annotations'][0]['label']
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(read, projects * 4)) == [p['name'] for p in projects * 4]


def test_context_api_is_stable_across_restart_and_server_resolves_actor(tmp_path):
    _, client, (first, _) = _local(tmp_path)
    response = client.get('/api/context', headers={'X-Vision-Project': first['id']})
    assert response.status_code == 200, response.text
    context = response.json()['project_context']
    assert context['project_id'] == first['id'] and context['mode'] == 'local'
    assert context['workspace_id'] and context['actor_id']
    assert str(tmp_path) not in json.dumps(context)
    restarted = create_app(project_dir=str(tmp_path / 'projects'))
    other = TestClient(restarted, headers={'X-Vision-Token': restarted.state.api_token})
    assert other.get('/api/context', headers={'X-Vision-Project': first['id']}).json()['project_context'] == context
    forged = {**context, 'actor_id': 'caller-admin'}
    assert other.get('/api/context', headers={'X-Vision-Context': json.dumps(forged)}).status_code == 403


def test_artifact_ids_verify_revision_hash_and_project_scope_without_changing_history(tmp_path):
    _, client, (first, second) = _local(tmp_path)
    model = Path(first['models_dir']) / 'legacy-job' / 'metadata.json'; model.parent.mkdir()
    model.write_bytes(b'{"job_id":"legacy-job"}')
    flow = Path(first['project_dir']) / 'flowcharts' / 'versions' / 'legacy-flow.json'; flow.parent.mkdir(parents=True)
    flow.write_bytes(b'{"version_id":"legacy-flow"}')
    before = {p: p.read_bytes() for p in [model, flow, Path(first['project_dir']) / 'project.json']}
    headers = {'X-Vision-Project': first['id']}
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    registered = client.post('/api/context/artifacts', headers=headers, json={
        'kind': 'model', 'relative_path': 'legacy-job/metadata.json', 'sha256': digest,
    })
    assert registered.status_code == 200, registered.text
    ref = registered.json()['artifact_ref']
    assert ref['revision'] == 1 and ref['sha256'] == digest and ref['id']
    assert str(tmp_path) not in registered.text
    path = '/api/context/artifacts/' + ref['id']
    params = {'revision': ref['revision'], 'sha256': ref['sha256']}
    assert client.get(path + '/content', params=params, headers=headers).content == before[model]
    assert client.get(path, params=params, headers={'X-Vision-Project': second['id']}).status_code == 404
    assert client.get(path, params={**params, 'revision': 2}, headers=headers).status_code == 409
    assert client.post('/api/context/artifacts', headers=headers, json={
        'kind': 'model', 'relative_path': '../../project.json', 'sha256': digest,
    }).status_code == 422
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_authenticated_parallel_async_routes_and_events_capture_origin_not_latest_selection(tmp_path):
    import httpx
    from fastapi import Request
    from backend.api.websocket_telemetry import TelemetryBroadcaster
    from backend.contracts.context import current_project_context, get_project_context
    from backend.engine.annotation_storage import request_project_root
    _, _, projects = _local(tmp_path)
    app = create_app(project_dir=str(tmp_path / 'team-projects'), shared_auth_dir=str(tmp_path / 'auth'))
    accounts = app.state.accounts
    owner = accounts.bootstrap('owner', 'fixture password 123')
    other = accounts.create_user('another', 'fixture password 456')
    for project in projects:
        accounts.register_project(project['id'], project['project_dir'], owner['id'])
        accounts.set_membership(project['id'], other['id'], 'owner', owner['id'])
    users = [owner, other]
    sessions = [accounts.login(u['username'], 'fixture password 123' if u is owner else 'fixture password 456') for u in users]
    broadcaster = TelemetryBroadcaster()
    arrivals = 0
    gate = asyncio.Event()
    @app.get('/api/_s1_context_probe')
    async def probe(request: Request):
        nonlocal arrivals
        context = get_project_context(request)
        arrivals += 1
        if arrivals == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), 5)
        await asyncio.sleep(0)
        broadcaster.broadcast_sync('context_probe', {'job_id': 'fixture-' + context.project_id})
        return {'project_context': current_project_context.get().model_dump(), 'root': str(request_project_root())}
    async def exercise():
        broadcaster._running = True
        broadcaster._loop = asyncio.get_running_loop()
        broadcaster._queue = asyncio.Queue()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            headers = [{'Authorization': 'Bearer ' + session['token'], 'X-Vision-Project': project['id']}
                       for session, project in zip(sessions, projects)]
            contexts = [(await client.get('/api/context', headers=h)).json()['project_context'] for h in headers]
            # The legacy main-process selector points elsewhere after capture.
            for i, h in enumerate(headers):
                h.update({'X-Vision-Context': json.dumps(contexts[i]), 'X-Vision-Project': projects[1-i]['id']})
            results = await asyncio.gather(*(client.get('/api/_s1_context_probe', headers=h) for h in headers))
            assert all(r.status_code == 200 for r in results)
            assert [r.json()['project_context'] for r in results] == contexts
            assert [r.json()['root'] for r in results] == [p['project_dir'] for p in projects]
            await asyncio.sleep(0)
            events = [broadcaster._queue.get_nowait() for _ in range(2)]
            assert {e['job_id']: e['project_context'] for e in events} == {
                'fixture-' + c['project_id']: c for c in contexts}
            assert current_project_context.get() is None
            forged = {**contexts[0], 'actor_id': other['id']}
            denial = await client.get('/api/context', headers={**headers[0], 'X-Vision-Context': json.dumps(forged)})
            assert denial.status_code == 403
    asyncio.run(exercise())


def test_renderer_request_captures_explicit_context_before_async_port_resolution():
    root = Path(__file__).resolve().parents[2]
    script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const Module = require('node:module');
const filename = process.cwd() + '/src/renderer/services/api.ts';
const loaded = new Module(filename); loaded.paths = module.paths;
loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}
}).outputText, filename);
const client = loaded.exports;
let releasePort; const calls = [];
global.window = {api: {getBackendPort: () => new Promise(resolve => {releasePort = resolve;})}};
global.fetch = async (url, options) => {
  calls.push({url, context: new Headers(options.headers).get('X-Vision-Context')});
  return {ok: true, status: 200, headers: new Headers(), json: async () => ({ok: true})};
};
(async () => {
 const context = {workspace_id: 'workspace', project_id: 'first', actor_id: 'actor', mode: 'local'};
 const pending = client.request('/api/context', {projectContext: context});
 context.project_id = 'later'; client.setSharedApiBase('https://later.invalid'); releasePort(8123);
 await pending;
 assert.equal(calls[0].url, 'http://127.0.0.1:8123/api/context');
 assert.deepEqual(JSON.parse(calls[0].context), {workspace_id:'workspace', project_id:'first', actor_id:'actor', mode:'local'});
})().catch(error => {console.error(error); process.exitCode=1;});
'''
    result = subprocess.run(['node', '-e', script], cwd=root, text=True, encoding='utf-8', errors='replace', capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_invalid_artifact_id_is_a_validation_response(tmp_path):
    _, client, (first, _) = _local(tmp_path)
    response = client.get('/api/context/artifacts/not%20an%20id',
        headers={'X-Vision-Project': first['id']}, params={'revision': 1, 'sha256': 'a' * 64})
    assert response.status_code == 422


def test_managed_artifact_reference_publication_and_release_share_registry_transaction(tmp_path):
    app, client, (first, second) = _local(tmp_path)
    registry = app.state.context_registry
    first_key = registry.project_key(registry.context(first,None))
    second_key = registry.project_key(registry.context(second,None))
    with pytest.raises(RuntimeError):
        with registry.transaction() as db:
            ref = registry.register_managed_artifact(first_key, 'source', 'a' * 64, 'a' * 64, 7, db=db)
            assert registry.managed_reference(first_key, ref, db=db)['size_bytes'] == 7
            raise RuntimeError('Simulated storage publication failure')
    with registry.transaction() as db:
        assert db.execute('SELECT count(*) FROM artifacts').fetchone()[0] == 0
        ref = registry.register_managed_artifact(first_key, 'source', 'a' * 64, 'a' * 64, 7, db=db)
    assert registry.managed_reference(first_key, ref)['storage_key'] == ref.sha256
    with pytest.raises(HTTPException) as denied:
        registry.managed_reference(second_key, ref)
    assert denied.value.status_code == 404
    response = client.get('/api/context/artifacts/' + ref.id,
        headers={'X-Vision-Project': first['id']}, params={'revision': ref.revision, 'sha256': ref.sha256})
    assert response.status_code == 409
    assert 'managed' in response.json()['detail'].lower()
    with registry.transaction() as db:
        registry.release_managed_artifact(first_key, ref, db=db)
        assert db.execute('SELECT count(*) FROM artifacts').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM managed_artifacts').fetchone()[0] == 0


def test_archive_copies_preserve_ids_and_explicit_contexts_isolate_labels_refs_and_history(tmp_path):
    from backend.tests.test_project_archive import _project_with_model_flow_and_run
    client, original, source, image, job_id, version_id, _ = _project_with_model_flow_and_run(tmp_path)
    original_context = client.get('/api/context').json()['project_context']
    original_header = {'X-Vision-Context': json.dumps(original_context)}
    model = Path(original['models_dir']) / job_id / 'best_model.pt'
    original_model_bytes = model.read_bytes()
    original_flow = Path(original['project_dir']) / 'flowcharts' / 'versions' / (version_id + '.json')
    original_flow_bytes = original_flow.read_bytes()
    backed = client.post('/api/project/backup', json={'destination_dir': str(tmp_path/'backups')})
    assert backed.status_code == 200, backed.text
    archive = Path(backed.json()['archive_path']); archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    copies, contexts = [], []
    for name in ('copy-a', 'copy-b'):
        restored = client.post('/api/project/restore', json={'archive_path': str(archive), 'target_dir': str(tmp_path/name)})
        assert restored.status_code == 200, restored.text
        project = restored.json(); copies.append(project)
        context = client.get('/api/context').json()['project_context']; contexts.append(context)
        assert project['id'] == original['id'] == context['project_id']
        assert (Path(project['models_dir']) / job_id / 'best_model.pt').read_bytes() == original_model_bytes
        assert json.loads((Path(project['models_dir']) / job_id / 'job_receipt.json').read_text(encoding='utf-8'))['job_id'] == job_id
        assert json.loads((Path(project['project_dir']) / 'flowcharts/versions' / (version_id+'.json')).read_text(encoding='utf-8'))['version_id'] == version_id
    assert len({c['workspace_id'] for c in [original_context,*contexts]}) == 3
    assert client.get('/api/project/current',headers={'X-Vision-Project':original['id']}).status_code == 409
    assert client.get('/api/project/current',headers=original_header).json()['project_dir'] == original['project_dir']
    refs=[]
    for index,(project,context) in enumerate(zip(copies,contexts)):
        header={'X-Vision-Context':json.dumps(context)}
        restored_image=Path(project['source_dataset_dir'])/'test/part.png'
        assert client.post('/api/annotations/save',headers=header,json={'image_id':'part','image_path':str(restored_image),
            'image_width':24,'image_height':24,'annotations':[{'type':'bbox','label':f'copy-{index}','bbox':[1,1,5,5]}]}).status_code == 200
        registered=client.post('/api/context/artifacts',headers=header,json={'kind':'model',
            'relative_path':job_id+'/best_model.pt','sha256':hashlib.sha256(original_model_bytes).hexdigest()})
        assert registered.status_code == 200,registered.text
        refs.append(registered.json()['artifact_ref'])
    assert refs[0]['id'] != refs[1]['id']
    def read(index):
        project,context=copies[index],contexts[index]
        response=client.get('/api/annotations/part',headers={'X-Vision-Context':json.dumps(context)},
            params={'file_path':str(Path(project['source_dataset_dir'])/'test/part.png')})
        assert response.status_code == 200,response.text
        return response.json()['annotations'][0]['label']
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(read,[0,1]*3)) == ['copy-0','copy-1']*3
    ref=refs[0]
    assert client.get('/api/context/artifacts/'+ref['id'],headers={'X-Vision-Context':json.dumps(contexts[1])},
        params={'revision':ref['revision'],'sha256':ref['sha256']}).status_code == 404
    restarted=create_app(project_dir=str(tmp_path/'projects'))
    restarted_client=TestClient(restarted,headers={'X-Vision-Token':restarted.state.api_token})
    for project,context in zip(copies,contexts):
        response=restarted_client.get('/api/project/current',headers={'X-Vision-Context':json.dumps(context)})
        assert response.status_code == 200,response.text
        assert response.json()['project_dir'] == project['project_dir']
        assert json.loads(response.headers['X-Vision-Context']) == context
    assert image.read_bytes() == (Path(copies[0]['source_dataset_dir'])/'test/part.png').read_bytes()
    assert model.read_bytes() == original_model_bytes and original_flow.read_bytes() == original_flow_bytes
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == archive_hash


@pytest.mark.parametrize('header', ['not-json', '{}', 'x' * 4097, '[' * 1500 + ']' * 1500])
def test_invalid_explicit_context_never_falls_back_to_active_project(tmp_path, header):
    _, client, _ = _local(tmp_path)
    response=client.get('/api/project/current',headers={'X-Vision-Context':header})
    assert response.status_code == 400,response.text


def test_restore_after_original_location_disappears_does_not_rebind_captured_context(tmp_path):
    from backend.tests.test_project_archive import _project_with_model_flow_and_run
    client, original, _, _, _, _, _ = _project_with_model_flow_and_run(tmp_path)
    context = client.get('/api/context').json()['project_context']
    backed = client.post('/api/project/backup',json={'destination_dir':str(tmp_path/'backups')})
    assert backed.status_code == 200,backed.text
    original_path = Path(original['project_dir'])
    held = tmp_path/'unavailable-original'
    original_bytes = (original_path/'project.json').read_bytes()
    original_path.rename(held)
    restored = client.post('/api/project/restore',json={
        'archive_path':backed.json()['archive_path'],'target_dir':str(tmp_path/'new-restoration')})
    assert restored.status_code == 200,restored.text
    new_context = json.loads(restored.headers['X-Vision-Context'])
    assert new_context['project_id'] == context['project_id']
    assert new_context['workspace_id'] != context['workspace_id']
    stale = client.get('/api/project/current',headers={'X-Vision-Context':json.dumps(context)})
    assert stale.status_code == 404,stale.text
    assert client.get('/api/project/current').json()['project_dir'] == restored.json()['project_dir']
    assert (held/'project.json').read_bytes() == original_bytes


def test_unknown_project_and_artifact_symlink_escape_fail_closed(tmp_path):
    _,client,(first,_)=_local(tmp_path)
    assert client.get('/api/project/current',headers={'X-Vision-Project':'not-registered'}).status_code == 404
    outside=tmp_path/'outside.bin';outside.write_bytes(b'private fixture outside project')
    link=Path(first['models_dir'])/'escape.bin';link.symlink_to(outside)
    response=client.post('/api/context/artifacts',headers={'X-Vision-Project':first['id']},json={
        'kind':'model','relative_path':'escape.bin','sha256':hashlib.sha256(outside.read_bytes()).hexdigest()})
    assert response.status_code == 422,response.text
    assert outside.read_bytes() == b'private fixture outside project'


def test_registry_namespace_migration_retains_legacy_ref_rows_and_is_idempotent(tmp_path):
    from backend.contracts.context import ArtifactRef,ContextRegistry
    _,_,(first,_)=_local(tmp_path)
    model=Path(first['models_dir'])/'legacy.json';model.write_bytes(b'legacy model metadata')
    root=tmp_path/'v1-registry';root.mkdir()
    digest=hashlib.sha256(model.read_bytes()).hexdigest()
    with sqlite3.connect(root/'.context.sqlite3') as db:
        db.executescript('CREATE TABLE identities(name TEXT PRIMARY KEY,value TEXT NOT NULL);'
            'CREATE TABLE projects(id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE);'
            'CREATE TABLE artifacts(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,relative_path TEXT NOT NULL,revision INTEGER NOT NULL,sha256 TEXT NOT NULL,UNIQUE(project_id,kind,relative_path));')
        db.executemany('INSERT INTO identities VALUES(?,?)',[('workspace_id','legacy-workspace'),('local_actor_id','legacy-actor')])
        db.execute('INSERT INTO projects VALUES(?,?)',(first['id'],first['project_dir']))
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',('legacy-ref',first['id'],'model','legacy.json',3,digest))
    for _ in range(2):
        registry=ContextRegistry(root)
        context=registry.context(first,None)
        assert context.workspace_id == 'legacy-workspace' and context.actor_id == 'legacy-actor'
        assert registry.project_key(context) == first['id']
        assert registry.resolve_artifact(first,ArtifactRef(id='legacy-ref',revision=3,sha256=digest)) == model
        with registry.transaction() as db:
            assert db.execute('SELECT count(*) FROM project_locations').fetchone()[0] == 1
            assert db.execute('SELECT id,project_id,revision,sha256 FROM artifacts').fetchone() == ('legacy-ref',first['id'],3,digest)


def test_parallel_training_submissions_and_context_free_callbacks_keep_origin(tmp_path, monkeypatch):
    """Exercise submission/preparation without starting a trainer or allocating compute."""
    import queue
    import threading
    from types import SimpleNamespace
    from backend.api import routes_training, websocket_telemetry
    from backend.contracts.context import current_project_context, ProjectContext
    from backend.tests.test_project_annotation_isolation import _source
    app, client, projects = _local(tmp_path)
    source, image = _source(tmp_path)
    second = source / 'second.png'
    Image.new('RGB', (32, 24), 'white').save(second)
    (source / 'second.json').write_bytes((source / 'sample.json').read_bytes())
    contexts = []
    for project in projects:
        headers = {'X-Vision-Project': project['id']}
        assert client.put('/api/project/update', headers=headers, json={'source_dataset_dir':str(source)}).status_code == 200
        assert client.post('/api/annotations/save', headers=headers, json={
            'image_id':'sample','image_path':str(image),'image_width':32,'image_height':24,
            'annotations':[{'type':'bbox','label':project['name'],'bbox':[2,2,9,10]}]}).status_code == 200
        contexts.append(client.get('/api/context',headers=headers).json()['project_context'])
    events = queue.Queue()
    broadcaster = websocket_telemetry.TelemetryBroadcaster()
    broadcaster._running = True
    broadcaster._queue = events
    broadcaster._loop = SimpleNamespace(call_soon_threadsafe=lambda function, payload:function(payload))
    monkeypatch.setattr(websocket_telemetry,'broadcaster',broadcaster)
    submissions = {}
    gate = threading.Barrier(2)
    def submit_without_training(**kwargs):
        context = current_project_context.get()
        submissions[context.project_id] = (kwargs, websocket_telemetry.WebSocketTelemetryCallback(kwargs['job_id']))
        gate.wait(timeout=10)
        return SimpleNamespace(job_id=kwargs['job_id'])
    monkeypatch.setattr(routes_training.training_job_manager,'start_job',submit_without_training)
    def submit(context):
        response = client.post('/api/training/start',headers={'X-Vision-Context':json.dumps(context)},json={
            'task':'segmentation','dataset_path':str(source),'preset':'fast'})
        assert response.status_code == 200,response.text
        return response.json()['job_id']
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = list(executor.map(submit,contexts))
    assert current_project_context.get() is None
    assert app.state.current_project['id'] == projects[1]['id']
    # Actual callbacks created at the production submission boundary run later
    # on worker threads with no ContextVar inherited from the HTTP request.
    def worker(project):
        assert current_project_context.get() is None
        kwargs, callback = submissions[project['id']]
        assert Path(kwargs['output_dir']).is_relative_to(Path(project['models_dir']))
        kwargs['prepare_dataset'](threading.Event())
        rows = json.loads((Path(kwargs['dataset_path'])/'source_manifest.json').read_text(encoding='utf-8'))
        row = next(row for row in rows if row['source_image']==str(image.resolve()))
        assert json.loads(Path(row['source_json']).read_text(encoding='utf-8'))['annotations'][0]['label'] == project['name']
        callback.on_training_start({'epochs':1})
        assert current_project_context.get() is None
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(worker,projects))
    payloads = [events.get_nowait() for _ in projects]
    assert {p['job_id']:p.get('project_context') for p in payloads} == dict(zip(jobs,contexts))
    for payload in payloads:
        for context in contexts:
            recipient = SimpleNamespace(scope={'state':{'project_context':ProjectContext(**context)}})
            assert broadcaster._can_receive(recipient,payload) == (payload['project_context']['project_id']==context['project_id'])


def test_websocket_explicit_context_selects_origin_even_when_legacy_pointer_differs(tmp_path, monkeypatch):
    from urllib.parse import urlencode
    from backend.api import websocket_telemetry
    app, client, (first, second) = _local(tmp_path)
    context = client.get('/api/context',headers={'X-Vision-Project':first['id']}).json()['project_context']
    broadcaster = websocket_telemetry.TelemetryBroadcaster()
    monkeypatch.setattr(websocket_telemetry,'broadcaster',broadcaster)
    url = '/ws/telemetry?' + urlencode({'project_context':json.dumps(context)})
    with client.websocket_connect(url,headers={'X-Vision-Project':second['id']}) as ws:
        ws.receive_json()
        connection = next(iter(broadcaster._active_connections))
        assert connection.scope['state']['project_context'].model_dump() == context
        assert broadcaster._can_receive(connection,{'event':'step_progress','project_context':context})
    assert app.state.current_project['id'] == second['id']


def test_renderer_socket_rebinds_only_committed_context_and_ignores_old_frames():
    root = Path(__file__).resolve().parents[2]
    script = r'''
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),assert=require('node:assert/strict');
const root=process.cwd(),ts=require(path.join(root,'node_modules/typescript'));
function load(relative,mocks={}){const name=path.join(root,relative),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(path.dirname(name));const req=m.require.bind(m);m.require=k=>mocks[k]||req(k);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const api=load('src/renderer/services/api.ts');
const context=(id,workspace='workspace')=>({workspace_id:workspace,project_id:id,actor_id:'actor',mode:'local'});
const sockets=[];
global.WebSocket=class {static OPEN=1;static CONNECTING=0;readyState=0;constructor(url){this.url=url;sockets.push(this);}send(){}close(){this.readyState=3;this.onclose?.();}};
global.window={api:{getBackendPort:async()=>8123}};
const telemetry=load('src/renderer/services/websocket.ts',{'./api':api}).telemetryService;
const received=[];telemetry.subscribe((event,data)=>received.push({event,data}));
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 api.setProjectContext(context('first'));await telemetry.connect();
 assert.deepEqual(JSON.parse(new URL(sockets[0].url).searchParams.get('project_context')),context('first'));
 const old=sockets[0],lateMessage=old.onmessage,lateClose=old.onclose;old.readyState=1;old.onopen();
 api.setProjectContext(context('second'));await settle();
 assert.equal(sockets.length,2);const current=sockets[1];
 assert.deepEqual(JSON.parse(new URL(current.url).searchParams.get('project_context')),context('second'));
 current.readyState=1;current.onopen();
 lateMessage({data:JSON.stringify({event:'step_progress',data:{job_id:'old'},project_context:context('first')})});lateClose();
 assert.equal(telemetry.getStatus(),true);assert.equal(received.length,0);
 current.onmessage({data:JSON.stringify({event:'step_progress',data:{job_id:'new'},project_context:context('second')})});assert.equal(received[0].data.job_id,'new');
 api.setProjectContext(context('second'));await settle();assert.equal(sockets.length,2);
 api.setProjectContext(context('second','restored-copy'));await settle();assert.equal(sockets.length,3);
 assert.equal(JSON.parse(new URL(sockets[2].url).searchParams.get('project_context')).workspace_id,'restored-copy');
 telemetry.disconnect();await settle();assert.equal(telemetry.getStatus(),false);
 // A retired async port lookup must not create a socket for the old scope.
 api.setCachedPort(null);const ports=[];window.api.getBackendPort=()=>new Promise(resolve=>ports.push(resolve));
 api.setProjectContext(context('pending-first'));const pending=telemetry.connect();
 api.setProjectContext(context('pending-second'));assert.equal(ports.length,2);
 ports[0](8123);ports[1](8123);await pending;await settle();assert.equal(sockets.length,4);
 assert.equal(JSON.parse(new URL(sockets[3].url).searchParams.get('project_context')).project_id,'pending-second');
 telemetry.disconnect();api.setCachedPort(null);const cancelled=telemetry.connect();
 telemetry.disconnect();ports[2](8123);await cancelled;await settle();assert.equal(sockets.length,4);
})().catch(error=>{console.error(error);telemetry.disconnect();process.exitCode=1;});
'''
    result = subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
    assert result.returncode == 0,result.stdout + result.stderr


def test_team_websocket_context_checks_membership_actor_and_legacy_header(tmp_path, monkeypatch):
    from urllib.parse import urlencode
    from starlette.websockets import WebSocketDisconnect
    from backend.api import websocket_telemetry
    _, _, (first, second) = _local(tmp_path)
    app = create_app(project_dir=str(tmp_path/'team'),shared_auth_dir=str(tmp_path/'auth'))
    accounts = app.state.accounts
    owner = accounts.bootstrap('owner','fixture password 123')
    reader = accounts.create_user('reader','fixture password 456')
    for project in (first,second):
        accounts.register_project(project['id'],project['project_dir'],owner['id'])
    accounts.set_membership(first['id'],reader['id'],'viewer',owner['id'])
    session = accounts.login('reader','fixture password 456')
    headers = {'Authorization':'Bearer '+session['token'],'X-Vision-Project':first['id']}
    client = TestClient(app)
    response = client.get('/api/context',headers=headers)
    assert response.status_code == 200,response.text
    context = response.json()['project_context']
    broadcaster = websocket_telemetry.TelemetryBroadcaster()
    monkeypatch.setattr(websocket_telemetry,'broadcaster',broadcaster)
    def url(value):return '/ws/telemetry?'+urlencode({'project_context':json.dumps(value)})
    with client.websocket_connect(url(context),headers={**headers,'X-Vision-Project':second['id']}) as ws:
        ws.receive_json()
        connection = next(iter(broadcaster._active_connections))
        assert connection.scope['state']['project_context'].model_dump() == context
        # Producer identity can differ; project membership is rechecked per frame.
        assert broadcaster._can_receive(connection,{'event':'hardware_stats','project_context':{**context,'actor_id':owner['id']}})
        assert not broadcaster._can_receive(connection,{'event':'hardware_stats','project_context':{**context,'project_id':second['id']}})
    for rejected in ({**context,'actor_id':owner['id']},{**context,'project_id':second['id']},
                     {**context,'workspace_id':'other-workspace'}):
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect(url(rejected),headers=headers):pass
        assert denied.value.code == 1008


@pytest.mark.parametrize('query', ['project_context=', 'project_context=not-json',
    'project_context=%7B%7D', 'project_context='+('x'*4097),
    'project_context=null&project_context=null', 'x='+('x'*16385)])
def test_websocket_invalid_explicit_query_never_falls_back(tmp_path, query):
    from starlette.websockets import WebSocketDisconnect
    _,client,_ = _local(tmp_path)
    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect('/ws/telemetry?'+query):pass
    assert denied.value.code == 1008


def test_websocket_conflicting_explicit_header_and_query_rejected(tmp_path):
    from urllib.parse import urlencode
    from starlette.websockets import WebSocketDisconnect
    _,client,(first,second) = _local(tmp_path)
    contexts = [client.get('/api/context',headers={'X-Vision-Project':p['id']}).json()['project_context'] for p in (first,second)]
    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect('/ws/telemetry?'+urlencode({'project_context':json.dumps(contexts[0])}),
                                      headers={'X-Vision-Context':json.dumps(contexts[1])}):pass
    assert denied.value.code == 1008


def test_renderer_discovery_candidates_commit_only_after_ui_acceptance():
    root = Path(__file__).resolve().parents[2]
    script = r'''
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),assert=require('node:assert/strict');
const root=process.cwd(),ts=require(path.join(root,'node_modules/typescript')),name=path.join(root,'src/renderer/services/api.ts');
const moduleFile=new Module(name,module);moduleFile.filename=name;moduleFile.paths=Module._nodeModulePaths(path.dirname(name));moduleFile._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);const client=moduleFile.exports;
const context=id=>({workspace_id:'workspace',project_id:id,actor_id:'actor',mode:'local'});
let server='second';const calls=[];global.window={api:{getBackendPort:async()=>8123}};
global.fetch=async(url,options)=>{const captured=JSON.parse(new Headers(options.headers).get('X-Vision-Context')||'null');calls.push(captured);const received=url.endsWith('/api/project/current')?context(server):captured;return {ok:true,status:200,headers:new Headers({'X-Vision-Context':JSON.stringify(received)}),json:async()=>({id:received.project_id,project_dir:'/fixture/'+received.project_id})};};
(async()=>{
 client.setProjectContext(context('first'));
 const candidate=await client.api.project.getCurrent();
 assert.equal(candidate.id,'second');assert.equal(client.getProjectContext().project_id,'first','Discovery must not change the visible project authority');
 await client.request('/api/annotations/save',{method:'POST',body:'{}'});assert.equal(calls.at(-1).project_id,'first');
 await client.api.project.getCurrent(); // saveOpenEdits can perform another discovery.
 let visible='first';client.api.project.acceptContext(candidate,()=>{visible=candidate.id;});
 assert.equal(visible,'second');assert.equal(client.getProjectContext().project_id,'second');
 server='third';const stale=await client.api.project.getCurrent();client.setProjectContext(context('fourth'));
 assert.throws(()=>client.api.project.acceptContext(stale,()=>{visible='third';}));assert.equal(visible,'second');assert.equal(client.getProjectContext().project_id,'fourth');
 server='fifth';const transportStale=await client.api.project.getCurrent();client.setSharedApiBase('https://later.invalid');
 assert.throws(()=>client.api.project.acceptContext(transportStale,()=>{visible='fifth';}));assert.equal(visible,'second');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
    assert result.returncode == 0,result.stdout + result.stderr
