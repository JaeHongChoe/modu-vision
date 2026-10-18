"""Real same-namespace prior drafts keep their base without activating it.

The change caught here is an ownership false negative after a genuine A->B
save, not a relaxation of the full-version compare-and-swap contract. No native
request identity or historical A labelset provenance is claimed.
"""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest

from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _edited, _no_whole_credit


def _basis(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


async def _history(client, app, project, graph, A):
    """Original auth and real A draft -> B activation; no ownership substitutions."""
    headers = await _login_current(client, app, project)
    opened = await client.get('/api/flowchart/pipelines/'+A, headers=headers)
    assert opened.status_code == 200
    context = {'project_id':project['id'],'source_dataset_path':project['source_dataset_dir'],
               'labelset_id':project.get('active_labelset_id','default')}
    old = graph.model_copy(deep=True);old.name = 'Owned existing A draft'
    made = await client.put('/api/flowchart/draft',headers=headers,json={
        'pipeline':old.model_dump(),'context':context,'base_version_id':A})
    assert made.status_code == 200 and made.json()['base_version_id'] == A
    saved = await client.post('/api/flowchart/pipeline',headers=headers,params={
        'recipe_task':'segmentation','source_dataset_path':project['source_dataset_dir'],
        'expected_version_id':A,'change_reason':'Original A to B history'},json=_edited(graph).model_dump())
    assert saved.status_code == 200 and saved.json()['version_id'] != A
    opened_B = await client.get('/api/flowchart/pipeline/active/record',headers=headers,
        params={'source_dataset_path':project['source_dataset_dir']})
    assert opened_B.status_code == 200 and opened_B.json()['version_id'] == saved.json()['version_id']
    return headers,context,old,saved.json()['version_id']


def test_real_existing_divergent_draft_based_on_prior_A_preserves_active_B_and_drains(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    monkeypatch.chdir(tmp_path)
    app, admission, observer, _, project, graph, A, recipe, audit = _stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    context = {'project_id': project['id'], 'source_dataset_path': project['source_dataset_dir'],
               'labelset_id': project.get('active_labelset_id', 'default')}
    draft_file = flows._draft_file(project, context)
    original_A = (flows._version_dir(root)/(A+'.json')).read_bytes()
    prior = graph.model_copy(deep=True)
    prior.name = 'Owned current namespace draft based on A'
    changed = prior.model_copy(deep=True)
    changed.nodes[0].data.label = 'Owned edited divergent draft'
    B_graph = _edited(graph)

    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            opened = await client.get('/api/flowchart/pipelines/'+A, headers=headers)
            assert opened.status_code == 200 and opened.json() == graph.model_dump()
            original_draft = await client.put('/api/flowchart/draft', headers=headers, json={
                'pipeline': prior.model_dump(), 'context': context, 'base_version_id': A})
            assert original_draft.status_code == 200
            prior_record = original_draft.json()
            assert prior_record['active_version_id'] is None and prior_record['base_version_id'] == A
            prior_bytes = draft_file.read_bytes()
            assert json.loads(prior_bytes) == {k:v for k,v in prior_record.items() if k!='active_version_id'}
            assert admission._flow_requests.records[-1].settled
            saved_B = await client.post('/api/flowchart/pipeline', headers=headers,
                params={'recipe_task':'segmentation', 'source_dataset_path':project['source_dataset_dir'],
                        'expected_version_id':A, 'change_reason':'Independent active B'}, json=B_graph.model_dump())
            assert saved_B.status_code == 200
            B = saved_B.json()['version_id']
            assert B != A and len(B) == 32
            active_B = await client.get('/api/flowchart/pipeline/active/record', headers=headers,
                params={'source_dataset_path':project['source_dataset_dir']})
            assert active_B.status_code == 200 and active_B.json() == {'version_id':B,'pipeline':B_graph.model_dump()}
            assert observer.snapshot()['failures'] == []
            assert admission.snapshot() == {'active_scopes':0,'unsupported':[]}
            before = _basis(root)
            response = await client.put('/api/flowchart/draft', headers=headers, json={
                'pipeline':changed.model_dump(), 'context':context, 'base_version_id':A})
            assert response.status_code == 200
            value = response.json()
            assert value['pipeline'] == changed.model_dump() and value['context'] == context
            assert value['base_version_id'] == A and value['active_version_id'] is None
            assert value == {**json.loads(draft_file.read_bytes()),'active_version_id':None}
            assert (flows._version_dir(root)/(A+'.json')).read_bytes() == original_A
            assert json.loads(flows._active_flow_file(root).read_bytes())['version_id'] == B
            after = _basis(root)
            assert set(after) == set(before)
            relative = str(draft_file.relative_to(root))
            assert {k:v for k,v in after.items() if k != relative} == {k:v for k,v in before.items() if k != relative}
            assert after[relative] != before[relative] == prior_bytes
            assert json.loads(recipe.read_bytes()) == B_graph.model_dump()
            assert audit.verify() == {'intact':True,'rows':1}
            rows = observer.snapshot()['records']
            assert all(row['completed'] and row['normal_return'] and row['released'] for row in rows)
            return {'A':A,'B':B,'prior_draft_sha256':hashlib.sha256(prior_bytes).hexdigest(),
                    'draft_sha256':hashlib.sha256(after[relative]).hexdigest(),'persisted':value,
                    'rows':rows,'failures':observer.snapshot()['failures']}

    proof = asyncio.run(sequence())
    admission.close()
    drain = admission.drain(.03)
    proof.update(source_only=True, native_request_attribution=False,
                 historical_A_labelset_provenance=False, drain=drain)
    (tmp_path/'actual-existing-divergent-draft.json').write_text(json.dumps(proof,indent=2))
    # RED must reach this assertion after genuine normal 200 and durable bytes.
    assert drain == {'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    assert observer.snapshot()['failures'] == [] and observer.snapshot()['all_settled'] and not observer.snapshot()['uncertain']
    attempt = admission._flow_requests.records[-1]
    assert attempt.name == 'save_flow_draft' and attempt.settled
    assert attempt.draft_stages == ['open','flushed','published','clean']
    assert attempt.draft_handle.closed and all(handle.closed for handle,_ in attempt.draft_reads)
    _no_whole_credit(observer)


def test_real_full_save_based_on_A_after_B_is_still_409_and_preserves_active_B(tmp_path, monkeypatch):
    """Draft persistence never authorizes a stale full-version publication."""
    from backend.api import routes_flowchart as flows
    monkeypatch.chdir(tmp_path)
    app, admission, observer, _, project, graph, A, _, audit = _stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])

    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid') as client:
            headers = await _login_current(client,app,project)
            opened = await client.get('/api/flowchart/pipelines/'+A,headers=headers)
            assert opened.status_code == 200
            params={'recipe_task':'segmentation','source_dataset_path':project['source_dataset_dir'],
                    'expected_version_id':A,'change_reason':'Original CAS control'}
            B = await client.post('/api/flowchart/pipeline',headers=headers,params=params,json=_edited(graph).model_dump())
            assert B.status_code == 200
            before = _basis(root)
            response = await client.post('/api/flowchart/pipeline',headers=headers,params=params,json=graph.model_dump())
            assert response.status_code == 409
            assert response.json() == {'detail':'The active flow changed since this one was opened; reopen it before saving.'}
            assert _basis(root) == before
            assert json.loads(flows._active_flow_file(root).read_bytes())['version_id'] == B.json()['version_id']
            assert audit.verify() == {'intact':True,'rows':1}
            return response.json()
    value = asyncio.run(sequence())
    admission.close()
    drain = admission.drain(.03)
    (tmp_path/'actual-stale-full-save-409.json').write_text(json.dumps({
        'source_only':True,'HTTP_status':409,'body':value,'drain':drain,
        'failures':observer.snapshot()['failures'],'native_request_attribution':False},indent=2))
    _no_whole_credit(observer)


@pytest.mark.parametrize('fault',[
    'missing-draft','malformed-draft','draft-hash','draft-project','draft-source','draft-labelset','draft-base',
    'symlink-draft','missing-A','malformed-A','A-source','A-identifier','A-project','A-labelset','symlink-A','hardlink-A',
    'A-missing-fields','A-future-time',
    'copied-prior-proof',
])
def test_unproved_prior_namespace_or_saved_A_stays_refused(tmp_path,monkeypatch,fault):
    from backend.api import routes_flowchart as flows
    monkeypatch.chdir(tmp_path)
    app,admission,observer,_,project,graph,A,_,audit = _stack(tmp_path,monkeypatch)
    root=Path(project['project_dir']);armed=False

    async def original_transport(scope,receive,send):
        async def external_send(message):
            if armed and fault=='copied-prior-proof' and scope.get('path')=='/api/flowchart/draft' and message['type']=='http.response.start':
                attempt=next(row for row in admission._flow_requests.pending.values() if row.scope is scope)
                assert attempt.handler_done and not attempt.failed and attempt.draft_prior is not None
                attempt.draft_prior=dict(attempt.draft_prior)
            await send(message)
        await app(scope,receive,external_send)

    async def sequence():
        nonlocal armed
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_transport),base_url='http://owned.invalid')as client:
            headers,context,old,B=await _history(client,app,project,graph,A)
            assert observer.snapshot()['failures']==[] and admission.snapshot()=={'active_scopes':0,'unsupported':[]}
            draft=flows._draft_file(project,context);version=flows._version_dir(root)/(A+'.json')
            if fault=='missing-draft':draft.unlink()
            elif fault=='malformed-draft':draft.write_bytes(b'{')
            elif fault=='symlink-draft':
                retained=draft.with_name('retained-draft.json');draft.rename(retained);draft.symlink_to(retained.name)
            elif fault.startswith('draft-'):
                value=json.loads(draft.read_bytes())
                if fault=='draft-hash':value['draft_sha256']='0'*64
                elif fault=='draft-base':value['base_version_id']='f'*32
                else:value['context'][{'draft-project':'project_id','draft-source':'source_dataset_path','draft-labelset':'labelset_id'}[fault]]='foreign'
                draft.write_text(json.dumps(value))
            elif fault=='missing-A':version.unlink()
            elif fault=='malformed-A':version.write_bytes(b'{')
            elif fault=='symlink-A':
                retained=version.with_name('retained-A.json');version.rename(retained);version.symlink_to(retained.name)
            elif fault=='hardlink-A':
                version.with_name('retained-hardlink-A.json').hardlink_to(version)
            elif fault in {'A-missing-fields','A-future-time'}:
                value=json.loads(version.read_bytes())
                if fault=='A-missing-fields':value.pop('saved_at_ns')
                else:value['saved_at_ns']=json.loads(draft.read_bytes())['saved_at_ns']+1
                version.write_text(json.dumps(value))
            elif fault.startswith('A-'):
                value=json.loads(version.read_bytes());key={'A-source':'source_dataset_path','A-identifier':'version_id','A-project':'project_id','A-labelset':'labelset_id'}[fault]
                value[key]='f'*32 if fault=='A-identifier'else'foreign';version.write_text(json.dumps(value))
            changed=old.model_copy(deep=True);changed.nodes[0].data.label='Owned draft edit under negative control'
            before=_basis(root);armed=True
            response=await client.put('/api/flowchart/draft',headers=headers,json={
                'pipeline':changed.model_dump(),'context':context,'base_version_id':A})
            armed=False
            assert response.status_code==200 and response.json()['base_version_id']==A
            assert response.json()=={**json.loads(draft.read_bytes()),'active_version_id':None}
            assert json.loads(flows._active_flow_file(root).read_bytes())['version_id']==B
            after=_basis(root);key=str(draft.relative_to(root))
            assert {k:v for k,v in before.items()if k!=key}=={k:v for k,v in after.items()if k!=key}
            assert audit.verify()=={'intact':True,'rows':1}
    asyncio.run(sequence())
    admission.close();drain=admission.drain(.03)
    (tmp_path/'actual-prior-refusal.json').write_text(json.dumps({'fault':fault,'drain':drain,
        'failures':observer.snapshot()['failures'],'HTTP_status':200,'source_only':True,
        'native_request_attribution':False,'historical_A_labelset_provenance':False},indent=2))
    assert drain['status']=='refused' and 'uncovered_request_protocol'in drain['unsupported']
    expected=['owned_flow_draft_active_return','owned_flow_handler_return','owned_flow_unconfirmed_lifetime']
    if fault=='copied-prior-proof':expected=['owned_flow_effect_exit','owned_flow_selected_account_middleware_return','owned_flow_unconfirmed_lifetime']
    elif fault=='symlink-draft':expected=['owned_flow_draft_publication_entry','owned_flow_draft_atomic_open',
        'owned_flow_draft_atomic_flushed','owned_flow_draft_atomic_published','owned_flow_draft_atomic_clean',
        'owned_flow_draft_publication_exit','owned_flow_draft_read_entry','owned_flow_draft_active_return',
        'owned_flow_handler_return','owned_flow_unconfirmed_lifetime']
    assert observer.snapshot()['failures']==expected
    assert all(handle.closed for row in admission._flow_requests.records for handle,_ in row.draft_reads)
    _no_whole_credit(observer)


def test_two_real_simultaneous_prior_base_draft_writes_keep_original_LWW_and_close(tmp_path,monkeypatch):
    """The prior draft witness is local to its atomic writer, not globally immutable."""
    from backend.api import routes_flowchart as flows
    monkeypatch.chdir(tmp_path)
    app,admission,observer,_,project,graph,A,_,audit=_stack(tmp_path,monkeypatch)
    root=Path(project['project_dir']);armed=False;arrived=[];ready=None
    async def original_transport(scope,receive,send):
        async def external_receive():
            message=await receive()
            if armed and scope.get('method')=='PUT' and scope.get('path')=='/api/flowchart/draft' and message['type']=='http.request' and not message.get('more_body',False):
                arrived.append(scope)
                if len(arrived)==2:ready.set()
                await asyncio.wait_for(ready.wait(),timeout=10)
            return message
        await app(scope,external_receive,send)
    async def sequence():
        nonlocal armed,ready
        ready=asyncio.Event()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_transport),base_url='http://owned.invalid')as client:
            headers,context,old,B=await _history(client,app,project,graph,A)
            assert observer.snapshot()['failures']==[]
            draft=flows._draft_file(project,context);before=_basis(root)
            changed=[]
            for label in ['First original concurrent draft','Second original concurrent draft']:
                value=old.model_copy(deep=True);value.nodes[0].data.label=label;changed.append(value)
            armed=True
            replies=await asyncio.gather(*(client.put('/api/flowchart/draft',headers=headers,json={
                'pipeline':value.model_dump(),'context':context,'base_version_id':A})for value in changed))
            armed=False
            assert len(arrived)==2
            for value,response in zip(changed,replies):
                assert response.status_code==200 and response.json()['pipeline']==value.model_dump()
                assert response.json()['base_version_id']==A and response.json()['active_version_id']is None
            persisted=json.loads(draft.read_bytes())
            records=[{k:v for k,v in response.json().items()if k!='active_version_id'}for response in replies]
            assert persisted in records and persisted==max(records,key=lambda value:value['saved_at_ns'])
            assert json.loads(flows._active_flow_file(root).read_bytes())['version_id']==B
            after=_basis(root);key=str(draft.relative_to(root))
            assert {k:v for k,v in before.items()if k!=key}=={k:v for k,v in after.items()if k!=key}
            assert audit.verify()=={'intact':True,'rows':1}
            rows=sorted(admission._flow_requests.records[-2:],key=lambda row:row.draft_record['saved_at_ns'])
            assert all(row.settled and row.draft_prior is not None for row in rows)
            assert rows[1].draft_prior['draft']['value']==rows[0].draft_record
            assert all(handle.closed for row in rows for handle in row.draft_prior_binding[2])
    asyncio.run(sequence())
    admission.close();drain=admission.drain(.03)
    (tmp_path/'actual-concurrent-prior-drafts.json').write_text(json.dumps({'source_only':True,
        'external_transport_body_barrier_only':True,'drain':drain,'failures':observer.snapshot()['failures'],
        'native_request_attribution':False,'historical_A_labelset_provenance':False},indent=2))
    assert drain=={'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    assert observer.snapshot()['failures']==[] and observer.snapshot()['all_settled']
    _no_whole_credit(observer)


@pytest.mark.parametrize('prior_kind',['draft','saved-A'])
def test_deep_optional_prior_JSON_keeps_real_business_put_200_and_refuses_witness(tmp_path,monkeypatch,prior_kind):
    """An optional witness parser must never replace the original writer result."""
    from backend.api import routes_flowchart as flows
    monkeypatch.chdir(tmp_path)
    app,admission,observer,_,project,graph,A,_,audit=_stack(tmp_path,monkeypatch)
    root=Path(project['project_dir'])
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://owned.invalid')as client:
            headers,context,old,B=await _history(client,app,project,graph,A)
            draft=flows._draft_file(project,context)
            target=draft if prior_kind=='draft'else flows._version_dir(root)/(A+'.json')
            # CPython's C JSON recursion budget need not equal its Python-frame
            # recursion setting. Characterize this literal input first.
            depth=16384
            nested='['*depth+'0'+']'*depth
            assert len(nested.encode())==depth*2+1 and len(nested.encode())<4*1024*1024
            with pytest.raises(RecursionError):json.loads(nested)
            target.write_text(nested)
            changed=old.model_copy(deep=True);changed.nodes[0].data.label='Owned write after optional deep prior'
            before=_basis(root)
            response=await client.put('/api/flowchart/draft',headers=headers,json={
                'pipeline':changed.model_dump(),'context':context,'base_version_id':A})
            (tmp_path/'actual-deep-prior-HTTP-result.json').write_text(json.dumps({
                'source_only':True,'prior_kind':prior_kind,'prior_bytes':len(nested.encode()),
                'original_interpreter_recursion_limit':sys.getrecursionlimit(),'HTTP_status':response.status_code,
                'response_body':response.text,'failures':observer.snapshot()['failures'],
                'native_request_attribution':False},indent=2))
            assert response.status_code==200
            assert response.json()=={**json.loads(draft.read_bytes()),'active_version_id':None}
            assert response.json()['pipeline']==changed.model_dump() and response.json()['base_version_id']==A
            after=_basis(root);key=str(draft.relative_to(root))
            assert {k:v for k,v in before.items()if k!=key}=={k:v for k,v in after.items()if k!=key}
            assert json.loads(flows._active_flow_file(root).read_bytes())['version_id']==B
            assert audit.verify()=={'intact':True,'rows':1}
    asyncio.run(sequence())
    admission.close();drain=admission.drain(.03)
    assert drain=={'status':'refused','active_scopes':1,'unsupported':['uncovered_request_protocol']}
    assert observer.snapshot()['failures']==['owned_flow_draft_active_return','owned_flow_handler_return','owned_flow_unconfirmed_lifetime']
    assert all(handle.closed for row in admission._flow_requests.records for handle,_ in row.draft_reads)
    _no_whole_credit(observer)
