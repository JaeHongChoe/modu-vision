"""Actual saved activation supersedes an older original draft; no model/native work."""
import asyncio
import hashlib
import json
from pathlib import Path
import time

import httpx
import pytest

def _basis(root):
    result={}
    for path in sorted(root.rglob('*')):
        assert not path.is_symlink()
        if path.is_file():
            st=path.stat();raw=path.read_bytes();after=path.stat()
            token=lambda v:[v.st_dev,v.st_ino,v.st_mode,v.st_nlink,v.st_size,v.st_mtime_ns,v.st_ctime_ns,v.st_uid,v.st_gid]
            assert token(st)==token(after)
            result[str(path.relative_to(root))]={'raw9':token(after),'sha256':hashlib.sha256(raw).hexdigest()}
    return result

def test_original_newer_activation_supersedes_valid_draft_without_poisoning_owned_read(tmp_path,monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.engine.flow_provenance import pipeline_sha256
    from backend.tests.test_original_owned_flow_lifetime import _stack,_login_current,_edited,_save_params
    monkeypatch.chdir(tmp_path)
    app,admission,observer,actor,project,graph,seed,recipe,audit=_stack(tmp_path,monkeypatch)
    root=Path(project['project_dir']);context=flows._draft_context(project)
    draft_file=flows._draft_file(project,context)
    # Original serialization is disclosed fixture preparation, not a claimed HTTP save.
    draft={'version':1,'context':context,'draft_sha256':pipeline_sha256(graph),
        'saved_at_ns':time.time_ns(),'pipeline':graph.model_dump(),'base_version_id':seed}
    flows._write_json(draft_file,draft)
    assert observer.snapshot()['records']==[] and observer.snapshot()['failures']==[]
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid')as client:
            headers=await _login_current(client,app,project)
            saved=await client.post('/api/flowchart/pipeline',headers=headers,
                params=_save_params(project,seed),json=_edited(graph).model_dump())
            assert saved.status_code==200 and saved.json()['status']=='saved'
            activated=saved.json()['version_id'];assert activated!=seed
            active=json.loads(flows._active_flow_file(root).read_bytes())
            assert active['version_id']==activated and active['activated_at_ns']>draft['saved_at_ns']
            assert observer.snapshot()['failures']==[] and admission.snapshot()=={'active_scopes':0,'unsupported':[]}
            assert audit.verify()=={'intact':True,'rows':1}
            before=_basis(root)
            response=await client.get('/api/flowchart/draft',headers=headers)
            assert response.status_code==404
            assert response.json()=={'detail':'A newer saved flow supersedes this draft'}
            after=_basis(root)
            assert before==after and json.loads(draft_file.read_bytes())==draft
            return response.json(),before,after,activated
    body,before,after,activated=asyncio.run(sequence())
    snapshot=observer.snapshot();rows=snapshot['records']
    assert len(rows)==4 and rows[-1]['ingress']=={'type':'http','method':'GET','path':'/api/flowchart/draft'}
    assert all(row['released'] and row['normal_return'] and row['completed']for row in rows)
    attempt=admission._UI_ancillary_requests.records[-1]
    assert attempt.name=='get_flow_draft'
    files=[{'path':str(path.relative_to(root)),'role':role,'closed':handle.closed}
           for handle,path,role in attempt.files]
    assert files and all(row['closed']for row in files)
    preclose=admission.snapshot();admission.close();drain=admission.drain(.03)
    proof={'source_only':True,'HTTP_status':404,'body':body,'activated_version':activated,
        'draft':draft,'before':before,'after':after,'files':files,
        'failures':snapshot['failures'],'records':rows,'preclose_admission':preclose,'drain':drain,
        'whole_writer_coverage':False,'native_Root13_cause_proven':False}
    (tmp_path/'actual-superseded-draft-proof.json').write_text(json.dumps(proof,indent=2))
    if snapshot['failures']:
        assert snapshot['failures']==['owned_ancillary_handler_return','owned_ancillary_unconfirmed_lifetime']
        assert preclose=={'active_scopes':1,'unsupported':['uncovered_request_protocol']}
    # Definitive RED only after exact normal business result, byte custody,
    # closed original reads, exact original refusal classes and actual ASGI rows.
    assert drain=={'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    assert observer.snapshot()['failures']==[] and observer.snapshot()['all_settled']


def _data_fixture(tmp_path,monkeypatch):
    """Original saved JSON setup before every observed request; no fake clock."""
    from backend.api import routes_flowchart as flows
    from backend.tests.test_original_owned_flow_lifetime import _stack,_edited
    monkeypatch.chdir(tmp_path)
    app,admission,observer,_,project,graph,seed,_,audit=_stack(tmp_path,monkeypatch)
    root=Path(project['project_dir']);context=flows._draft_context(project)
    draft={'version':1,'context':context,'draft_sha256':flows.pipeline_sha256(graph),
        'saved_at_ns':time.time_ns(),'pipeline':graph.model_dump(),'base_version_id':seed}
    draft_file=flows._draft_file(project,context);flows._write_json(draft_file,draft)
    identifier=flows._save_version(_edited(graph),'segmentation',project['source_dataset_dir'],root)
    active={'version_id':identifier,'project_id':project['id'],'labelset_id':context['labelset_id'],
        'source_dataset_path':context['source_dataset_path'],'activated_at_ns':time.time_ns()}
    assert active['activated_at_ns']>draft['saved_at_ns']
    pointer=flows._active_flow_file(root);flows._write_json(pointer,active)
    assert observer.snapshot()['records']==[] and observer.snapshot()['failures']==[]
    return app,admission,observer,project,draft_file,draft,pointer,active,audit


@pytest.mark.parametrize('fault',[
    'draft-hash','version-json','version-source','active-project','missing-version',
    'boolean-draft-time','symlink-version','sentinel-version','nonhex-version',
])
def test_unproved_supersession_keeps_original_business_result_and_strict_refusal(tmp_path,monkeypatch,fault):
    from backend.api import routes_flowchart as flows
    from backend.tests.test_original_owned_flow_lifetime import _login_current,_no_whole_credit
    app,admission,observer,project,draft_file,draft,pointer,active,audit=_data_fixture(tmp_path,monkeypatch)
    root=Path(project['project_dir']);version_file=flows._version_dir(root)/(active['version_id']+'.json')
    expected_status=404
    if fault=='draft-hash':
        draft['draft_sha256']='0'*64;flows._write_json(draft_file,draft);expected_status=409
    elif fault=='version-json':version_file.write_text('{invalid JSON')
    elif fault=='version-source':
        version=json.loads(version_file.read_bytes());version['source_dataset_path']=str(root/'foreign')
        flows._write_json(version_file,version)
    elif fault=='active-project':active['project_id']='foreign-project';flows._write_json(pointer,active)
    elif fault=='missing-version':version_file.rename(version_file.with_suffix('.retained-json'))
    elif fault=='boolean-draft-time':draft['saved_at_ns']=False;flows._write_json(draft_file,draft)
    elif fault=='symlink-version':
        retained=version_file.with_suffix('.retained-json');version_file.rename(retained);version_file.symlink_to(retained.name)
    elif fault in {'sentinel-version','nonhex-version'}:
        identifier='none'if fault=='sentinel-version'else 'g'*32
        version=json.loads(version_file.read_bytes());version['version_id']=identifier
        flows._write_json(version_file.with_name(identifier+'.json'),version)
        active['version_id']=identifier;flows._write_json(pointer,active)
    else:raise AssertionError('Unknown fixed fault')
    # For the symlink case retain target and link identities separately.
    paths=[path for path in root.rglob('*')if path.is_file() and not path.is_symlink()]
    before={path:path.read_bytes()for path in paths}
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid')as client:
            headers=await _login_current(client,app,project)
            return await client.get('/api/flowchart/draft',headers=headers)
    response=asyncio.run(original_request())
    assert response.status_code==expected_status
    if expected_status==404:assert response.json()=={'detail':'A newer saved flow supersedes this draft'}
    else:assert response.json()=={'detail':'저장된 임시 저장본이 올바르지 않습니다: Draft context or hash differs'}
    assert all(path.read_bytes()==raw for path,raw in before.items())
    assert audit.verify()=={'intact':True,'rows':0}
    snapshot=observer.snapshot();attempt=admission._UI_ancillary_requests.records[-1]
    before_close=admission.snapshot();admission.close();drain=admission.drain(.03)
    (tmp_path/'actual-strict-negative.json').write_text(json.dumps({'fault':fault,'HTTP_status':response.status_code,
        'body':response.json(),'failures':snapshot['failures'],'records':snapshot['records'],
        'helper_observations':len(attempt.draft_active_reads),'closed_reads':len(attempt.files),
        'admission':before_close,'drain':drain,'native_cause_proven':False},indent=2))
    assert snapshot['failures']==['owned_ancillary_handler_return','owned_ancillary_unconfirmed_lifetime']
    assert all(handle.closed for handle,_,_ in attempt.files)
    assert len(attempt.draft_active_reads)==(1 if fault in {'version-source','boolean-draft-time','symlink-version','sentinel-version','nonhex-version'}else 0)
    assert len(snapshot['records'])==3 and all(row['normal_return']and row['released']for row in snapshot['records'])
    assert before_close=={'active_scopes':1,'unsupported':['uncovered_request_protocol']}
    assert drain=={'status':'refused','active_scopes':1,'unsupported':['uncovered_request_protocol']}
    _no_whole_credit(observer)


def test_foreign_response_sender_cannot_repeat_the_genuine_helper_observation(tmp_path,monkeypatch):
    from backend.engine.original_owned_UI_ancillary_request import observe_original_UI_draft_active_return
    from backend.tests.test_original_owned_flow_lifetime import _login_current,_no_whole_credit
    app,admission,observer,project,_,_,_,_,audit=_data_fixture(tmp_path,monkeypatch)
    captured=[]
    async def actual_transport(scope,receive,send):
        async def external_send(message):
            if scope.get('path')=='/api/flowchart/draft' and message['type']=='http.response.start':
                attempt=next(value for value in admission._UI_ancillary_requests.pending.values()if value.scope is scope)
                assert attempt.handler_done and not attempt.failed and len(attempt.draft_active_reads)==1
                held=attempt.draft_active_reads[0]
                # Original observer, real original arguments, foreign caller frame.
                # Repeating it cannot create a second authorized observation.
                observe_original_UI_draft_active_return(*held)
                observe_original_UI_draft_active_return(*held)
                captured.append(attempt)
            await send(message)
        await app(scope,receive,external_send)
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=actual_transport),base_url='http://owned.invalid')as client:
            headers=await _login_current(client,app,project)
            return await client.get('/api/flowchart/draft',headers=headers)
    response=asyncio.run(original_request())
    assert response.status_code==404 and response.json()=={'detail':'A newer saved flow supersedes this draft'}
    assert len(captured)==1 and len(captured[0].draft_active_reads)==1 and captured[0].failed
    assert observer.snapshot()['failures']==[
        'owned_ancillary_draft_active_return_entry','owned_ancillary_draft_active_return','owned_ancillary_unconfirmed_lifetime']
    assert audit.verify()=={'intact':True,'rows':0}
    admission.close()
    assert admission.drain(.03)=={'status':'refused','active_scopes':1,'unsupported':['uncovered_request_protocol']}
    _no_whole_credit(observer)


def test_later_activation_of_the_same_valid_graph_keeps_the_original_draft_200(tmp_path,monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.tests.test_original_owned_flow_lifetime import _login_current,_no_whole_credit
    app,admission,observer,project,_,draft,_,active,audit=_data_fixture(tmp_path,monkeypatch)
    version_file=flows._version_dir(Path(project['project_dir']))/(active['version_id']+'.json')
    version=json.loads(version_file.read_bytes());version['pipeline']=draft['pipeline'];flows._write_json(version_file,version)
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid')as client:
            headers=await _login_current(client,app,project)
            before=_basis(Path(project['project_dir']))
            return await client.get('/api/flowchart/draft',headers=headers),before
    response,before=asyncio.run(original_request())
    assert response.status_code==200
    assert response.json()=={**draft,'active_version_id':active['version_id']}
    assert before==_basis(Path(project['project_dir'])) and audit.verify()=={'intact':True,'rows':0}
    attempt=admission._UI_ancillary_requests.records[-1]
    assert len(attempt.draft_active_reads)==2 and len(attempt.files)==6
    assert all(handle.closed for handle,_,_ in attempt.files)
    assert observer.snapshot()['failures']==[]
    admission.close()
    assert admission.drain(.03)=={'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    _no_whole_credit(observer)
