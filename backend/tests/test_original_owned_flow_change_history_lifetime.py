"""Actual history HTTP/read lifetimes, without save or deployment authority.

The saved unbound graph and metadata-only audit/ledger rows are local fixtures.
No model, device, runtime service, apply/recover or remote action is invoked.
SQLite keeps its original schema/PRAGMA effects and separate read transactions;
these controls do not claim an atomic or immutable deployment-state snapshot.
"""
import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from types import FunctionType

import httpx
import pytest

from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit

PATH='/api/flowchart/changes'
RAW9=('st_dev','st_ino','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns','st_uid','st_gid')


def _pins(paths):
    result={}
    for path in paths:
        if not path.exists():
            assert not path.is_symlink()
            result[path]=None
            continue
        before=tuple(getattr(path.lstat(),name)for name in RAW9)
        data=path.read_bytes()
        assert tuple(getattr(path.lstat(),name)for name in RAW9)==before
        result[path]=(before,hashlib.sha256(data).hexdigest())
    return result


def _protected(stack):
    from backend.api import routes_flowchart as flows
    _,_,_,_,project,_,seed,recipe,_=stack
    root=Path(project['project_dir'])
    return (root/'project.json',recipe,flows._active_flow_file(root),flows._version_dir(root)/(seed+'.json'))


def _closed(connection):
    with pytest.raises(sqlite3.ProgrammingError):
        connection.in_transaction


def _get(stack,*,limit=100,after_start=None):
    app,admission,observer,_,project,*_=stack
    before=_pins(_protected(stack))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid')as client:
            headers=await _login_current(client,app,project)
            async def downstream(scope,receive,send):
                async def captured(message):
                    if message['type']=='http.response.start'and after_start is not None:after_start(admission)
                    await send(message)
                await app(scope,receive,captured)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=downstream),base_url='http://owned.invalid')as target:
                response=await target.get(PATH,headers=headers,params={'limit':limit})
            assert response.status_code==200 and 0<len(response.content)<=65536
            assert json.loads(response.headers['x-vision-context'])==json.loads(headers['x-vision-context'])
            return response
    response=asyncio.run(scenario())
    assert _pins(_protected(stack))==before
    row,=[row for row in observer._records.values()if row['ingress']=={'type':'http','method':'GET','path':PATH}]
    assert all(row[key]for key in ('completed','normal_return','released','start_forwarded','terminal_forwarded'))
    assert row['response_status']==200 and not row['framing_error']and not row['transport_failed']
    assert row['send_attempt']is None and row['error_type']is None
    _no_whole_credit(observer)
    return response


def _drained(stack,audit_connections,runtime_connections):
    _,admission,observer,*_=stack
    attempt,=[item for item in admission._flow_requests.records if item.name=='list_configuration_changes']
    assert attempt.handler_done and not attempt.failed and attempt.history_live is None
    assert [row[0]for row in attempt.history_calls]==['list','verify','runtime']
    assert [len(row[5])for row in attempt.history_calls]==[audit_connections,audit_connections,runtime_connections]
    if runtime_connections:
        assert [row[0]for row in attempt.history_calls[2][5]]==['__init__','diagnostics','active','history']
    for call in attempt.history_calls:
        for _,_,connection in call[5]:_closed(connection)
    assert all(row['handle'].closed for row in attempt.history_reads if row['handle']is not None)
    assert attempt.audit_store is attempt.audit_db is attempt.audit_result is None
    assert not attempt.audit_live and not attempt.publications
    admission.close()
    assert admission.drain(.03)=={'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    assert observer.snapshot()['failures']==[]and not observer.snapshot()['uncertain']


def _refused(stack):
    _,admission,observer,*_=stack
    admission.close()
    assert admission.drain(.03)['status']=='refused'
    _no_whole_credit(observer)


def test_original_empty_history_and_both_real_audit_connections_close(tmp_path,monkeypatch):
    stack=_stack(tmp_path,monkeypatch)
    response=_get(stack,limit=1)
    assert response.json()=={'changes':[],'integrity':{'intact':True,'rows':0},
        'active_version_id':stack[6],'runtime':{'active':None,'pending':None}}
    _drained(stack,1,0)


def test_original_absent_audit_and_active_pointer_keep_no_read_branches(tmp_path,monkeypatch):
    from backend.api import routes_flowchart as flows
    stack=_stack(tmp_path,monkeypatch)
    stack[8].path.unlink()
    flows._active_flow_file(Path(stack[4]['project_dir'])).unlink()
    assert _get(stack).json()=={'changes':[],'integrity':{'intact':True,'rows':0},
        'active_version_id':None,'runtime':{'active':None,'pending':None}}
    _drained(stack,0,0)


def _append(stack,reason):
    _,_,_,actor,project,_,seed,_,audit=stack
    return audit.append(actor={'kind':'account','id':actor['id'],'name':actor['username']},
        subject={'project_id':project['id']},action='save',parent_revision=seed,next_revision=seed,
        semantic_delta={'layout_only':False,'fixture':reason},reason=reason)


def test_actual_populated_list_limit_and_original_verify_are_preserved(tmp_path,monkeypatch):
    stack=_stack(tmp_path,monkeypatch)
    _append(stack,'first fixture row')
    latest=_append(stack,'second fixture row')
    body=_get(stack,limit=1).json()
    assert body['changes']==[latest]and body['integrity']=={'intact':True,'rows':2}
    _drained(stack,1,0)


def test_original_tamper_warning_is_owned_without_turning_it_into_acceptance(tmp_path,monkeypatch):
    stack=_stack(tmp_path,monkeypatch)
    saved=_append(stack,'fixture row to damage')
    with closing(sqlite3.connect(stack[8].path))as database,database:
        database.execute('DROP TRIGGER configuration_changes_no_update')
        database.execute("UPDATE configuration_changes SET reason='external alteration'")
    body=_get(stack).json()
    assert body['changes'][0]['reason']=='external alteration'
    assert body['integrity']=={'intact':False,'rows':1,'broken_at':saved['change_id']}
    _drained(stack,1,0)


def test_present_ledger_reads_actual_four_connections_and_metadata_projection(tmp_path,monkeypatch):
    from backend.engine.runtime_deployment import DeploymentLedger
    stack=_stack(tmp_path,monkeypatch)
    ledger=DeploymentLedger(Path(stack[4]['project_dir'])/'runtime_service')
    release={'manifest_sha256':'a'*64}
    with closing(ledger.connect())as database,database:
        database.execute('INSERT INTO deployments VALUES(?,?,?,?,?,?)',
            ('metadata-only-release',json.dumps(release),json.dumps({'status':'ready'}),'fixture',None,1.0))
        database.execute('INSERT INTO active VALUES(1,?)',('metadata-only-release',))
        database.execute('INSERT INTO update_operations VALUES(?,?,?,?,?,?,?,?,?)',
            ('metadata-only-pending',json.dumps({'manifest_sha256':'b'*64}),None,'applying',None,None,'fixture',2.0,2.0))
    # This constructs metadata only; no apply(), recover(), runtime or model is called.
    body=_get(stack).json()
    assert body['runtime']=={
        'active':{'deployment_id':'metadata-only-release','manifest_sha256':'a'*64,'acknowledged':True},
        'pending':{'operation_id':'metadata-only-pending','status':'applying','manifest_sha256':'b'*64}}
    _drained(stack,1,4)


@pytest.mark.parametrize('method_name',['list','verify'])
def test_pre_factory_same_code_audit_clone_cannot_inherit_definition(tmp_path,monkeypatch,method_name):
    from backend.engine.config_audit import ConfigAuditStore
    original=getattr(ConfigAuditStore,method_name)
    replacement=FunctionType(original.__code__,original.__globals__,original.__name__,original.__defaults__,original.__closure__)
    monkeypatch.setattr(ConfigAuditStore,method_name,replacement)
    stack=_stack(tmp_path,monkeypatch)
    assert _get(stack).json()['integrity']=={'intact':True,'rows':0}
    _refused(stack)


def test_pre_factory_ledger_delegate_keeps_reply_but_cannot_inherit_proof(tmp_path,monkeypatch):
    from backend.engine.runtime_deployment import DeploymentLedger
    original=DeploymentLedger.diagnostics
    def delegated(store):return original(store)
    monkeypatch.setattr(DeploymentLedger,'diagnostics',delegated)
    stack=_stack(tmp_path,monkeypatch)
    DeploymentLedger(Path(stack[4]['project_dir'])/'runtime_service')
    assert _get(stack).json()['runtime']=={'active':None,'pending':None}
    _refused(stack)


def test_linked_audit_keeps_original_reply_and_refuses_path_authority(tmp_path,monkeypatch):
    stack=_stack(tmp_path,monkeypatch)
    source=stack[8].path
    foreign=tmp_path/'outside-project-audit.sqlite3'
    source.rename(foreign)
    source.symlink_to(foreign)
    assert _get(stack).json()['changes']==[]
    assert source.is_symlink()
    _refused(stack)


def test_unreadable_runtime_keeps_original_marker_and_refuses_coverage(tmp_path,monkeypatch):
    stack=_stack(tmp_path,monkeypatch)
    path=Path(stack[4]['project_dir'])/'runtime_service'/'runtime_deployments.sqlite3'
    path.parent.mkdir()
    path.write_bytes(b'not a SQLite database')
    assert _get(stack).json()['runtime']=={'active':None,'pending':None,'unreadable':True}
    _refused(stack)


def test_mutated_actual_return_after_serialization_cannot_complete_lifetime(tmp_path,monkeypatch):
    stack=_stack(tmp_path,monkeypatch)
    def mutate(admission):
        attempt,=[item for item in admission._flow_requests.records if item.name=='list_configuration_changes']
        attempt.history_calls[0][1].append({'changed_after_original_return':True})
    assert _get(stack,after_start=mutate).json()['changes']==[]
    _refused(stack)


def test_post_response_open_connection_witness_fails_original_closure_predicate(tmp_path,monkeypatch):
    """Adversarial retained-witness control, not an HTTP leaked-connection claim."""
    stack=_stack(tmp_path,monkeypatch)
    assert _get(stack).json()['integrity']=={'intact':True,'rows':0}
    attempt,=[item for item in stack[1]._flow_requests.records if item.name=='list_configuration_changes']
    assert attempt.history_response_original() is True
    original=attempt.history_calls[0]
    label,store,closed_connection=original[5][0]
    _closed(closed_connection)
    # This new original connection is created, queried and closed on this test
    # thread. The genuine worker's already-closed witnesses are never reopened
    # or given SQL/close calls from a different thread.
    connection=stack[8]._connect()
    try:
        assert type(connection)is sqlite3.Connection
        assert connection.execute('SELECT COUNT(*) FROM configuration_changes').fetchone()[0]==0
        assert attempt.history_response_original() is True
        attempt.history_calls[0]=(*original[:5],((label,store,connection),))
        assert attempt.history_response_original() is False
        attempt.history_calls[0]=original
        assert attempt.history_response_original() is True
    finally:
        attempt.history_calls[0]=original
        connection.close()
    _closed(connection)
    _drained(stack,1,0)


def test_foreign_context_keeps_original_403_and_never_enters_history(tmp_path,monkeypatch):
    stack=_stack(tmp_path,monkeypatch)
    app,admission,observer,_,project,*_=stack
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid')as client:
            headers=await _login_current(client,app,project)
            context=json.loads(headers['x-vision-context']);context['actor_id']='f'*32
            headers['x-vision-context']=json.dumps(context)
            response=await client.get(PATH,headers=headers)
            assert response.status_code==403
            assert response.json()=={'detail':'Explicit context does not match the authenticated account workspace'}
    asyncio.run(scenario())
    attempt,=[item for item in admission._flow_requests.records if item.name=='list_configuration_changes']
    assert not attempt.history_calls and attempt.history_store is None
    _refused(stack)


def test_history_send_failure_is_not_a_normal_completed_response(tmp_path,monkeypatch):
    stack=_stack(tmp_path,monkeypatch)
    app,_,observer,_,project,*_=stack
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid')as client:
            headers=await _login_current(client,app,project)
        scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET',
            'scheme':'http','path':PATH,'raw_path':PATH.encode(),'query_string':b'',
            'root_path':'','headers':[(key.encode(),value.encode())for key,value in headers.items()],
            'client':('127.0.0.1',1),'server':('owned.invalid',80)}
        used=False
        async def receive():
            nonlocal used
            if not used:used=True;return {'type':'http.request','body':b'','more_body':False}
            return {'type':'http.disconnect'}
        async def send(message):
            if message['type']=='http.response.body':raise OSError('original history send failed')
        await app(scope,receive,send)
    with pytest.raises(OSError,match='original history send failed'):asyncio.run(scenario())
    row,=[row for row in observer._records.values()if row['ingress']=={'type':'http','method':'GET','path':PATH}]
    assert row['response_status']==200 and row['transport_failed']and not row['normal_return']
    _refused(stack)
