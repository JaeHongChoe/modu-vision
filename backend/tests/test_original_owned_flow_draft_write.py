"""Real authenticated draft writes; no models, native app or synthetic router."""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _edited, _no_whole_credit


def _basis(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def test_real_draft_write_full_body_readback_and_normal_leave_drain(tmp_path, monkeypatch):
    """Missing real PUT ownership must fail only after durable business truth."""
    from backend.api import routes_flowchart as flows
    monkeypatch.chdir(tmp_path)
    app, admission, observer, _, project, graph, seed, _, audit = _stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    changed = _edited(graph)
    context = {'project_id': project['id'], 'source_dataset_path': project['source_dataset_dir'],
               'labelset_id': project.get('active_labelset_id', 'default')}
    payload = {'pipeline': changed.model_dump(), 'context': context, 'base_version_id': seed}
    checksum = flows.pipeline_sha256(changed)
    draft = flows._draft_file(project, context)

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            # The actual UI has already used this router. Warm it by a genuine
            # owned read, so cold lazy-cache refusal is not the causal variable.
            opened = await client.get('/api/flowchart/pipelines/'+seed, headers=headers)
            assert opened.status_code == 200 and opened.json() == graph.model_dump()
            before = _basis(root)
            assert admission.snapshot() == {'active_scopes': 0, 'unsupported': []}
            response = await client.put('/api/flowchart/draft', headers=headers, json=payload)
            assert response.status_code == 200
            value = response.json()
            assert set(value) == {'version', 'context', 'draft_sha256', 'saved_at_ns', 'pipeline',
                                  'base_version_id', 'active_version_id'}
            assert value == {**json.loads(draft.read_bytes()), 'active_version_id': None}
            assert value['version'] == 1 and value['context'] == context
            assert value['pipeline'] == payload['pipeline'] and value['draft_sha256'] == checksum
            assert value['base_version_id'] == seed and type(value['saved_at_ns']) is int and value['saved_at_ns'] > 0
            assert draft.is_file() and not draft.is_symlink() and draft.stat().st_nlink == 1
            assert not list(draft.parent.glob('*.tmp')) and not list(draft.parent.glob('.*.tmp'))
            after = _basis(root)
            assert {key: raw for key, raw in after.items() if key in before} == before
            assert set(after) - set(before) == {str(draft.relative_to(root))}
            assert audit.verify() == {'intact': True, 'rows': 0}
            rows = observer.snapshot()['records']
            assert len(rows) == 4 and rows[-1]['ingress'] == {
                'type': 'http', 'method': 'PUT', 'path': '/api/flowchart/draft'}
            assert all(row['completed'] and row['normal_return'] and row['released'] for row in rows)
            assert observer.snapshot()['failures'] == []
            return value, rows

    value, rows = asyncio.run(original_sequence())
    preclose = admission.snapshot()
    admission.close()
    drain = admission.drain(.03)
    (tmp_path/'actual-draft-write-proof.json').write_text(json.dumps({
        'source_only': True, 'native_Root14_attribution_proven': False, 'HTTP_status': 200,
        'persisted_record': value, 'draft_file_sha256': hashlib.sha256(draft.read_bytes()).hexdigest(),
        'request_records': rows, 'preclose': preclose, 'drain': drain,
        'failures': observer.snapshot()['failures']}, indent=2))
    # Causal RED: the real authenticated write, atomic durable bytes, original
    # closed HTTP row and empty poison list above must pass before this fails.
    assert drain == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    assert observer.snapshot()['all_settled'] and not observer.snapshot()['uncertain']
    attempt = admission._flow_requests.records[-1]
    assert attempt.name == 'save_flow_draft' and attempt.settled
    assert attempt.draft_stages == ['open', 'flushed', 'published', 'clean']
    assert attempt.draft_handle.closed and not Path(attempt.draft_temporary).exists()
    assert len(attempt.draft_reads) == 2 and all(handle.closed for handle, _ in attempt.draft_reads)
    _no_whole_credit(observer)


@pytest.mark.parametrize('fault,expected_status', [
    ('context', 409), ('invalid-base', 422), ('stale-base', 200),
    ('malformed-json', 403), ('copied-body', 200), ('copied-record', 200),
    ('model-dump-delegate', 200), ('temporary-opener-delegate', 200),
    ('write-helper-delegate', 200), ('publication-permission', 500), ('response-send', None),
    ('corrupt-active', 200), ('symlink-active', 200), ('foreign-version-source', 200),
])
def test_unproved_real_draft_write_keeps_business_result_and_refuses(tmp_path, monkeypatch, fault, expected_status):
    """Foreign code/identity or unfinished IO cannot inherit a genuine write."""
    from backend.api import routes_flowchart as flows
    import tempfile
    monkeypatch.chdir(tmp_path)
    app, admission, observer, _, project, graph, seed, _, audit = _stack(tmp_path, monkeypatch)
    context = {'project_id': project['id'], 'source_dataset_path': project['source_dataset_dir'],
               'labelset_id': project.get('active_labelset_id', 'default')}
    draft = flows._draft_file(project, context)
    draft.parent.mkdir(parents=True, exist_ok=True)
    pointer = flows._active_flow_file(Path(project['project_dir']))
    if fault == 'corrupt-active': pointer.write_bytes(b'{')
    elif fault == 'symlink-active':
        retained = pointer.with_name('retained-active.json')
        pointer.rename(retained); pointer.symlink_to(retained.name)
    elif fault == 'foreign-version-source':
        version_path = flows._version_dir(Path(project['project_dir']))/(seed+'.json')
        version = json.loads(version_path.read_bytes()); version['source_dataset_path'] = str(tmp_path/'foreign-source')
        flows._write_json(version_path, version)
    payload = {'pipeline': _edited(graph).model_dump(), 'context': dict(context), 'base_version_id': seed}
    if fault == 'context': payload['context']['labelset_id'] = 'foreign-labelset'
    if fault == 'invalid-base': payload['base_version_id'] = 'invalid-version'
    if fault == 'stale-base': payload['base_version_id'] = 'f'*32
    captured = []

    async def original_transport(scope, receive, send):
        async def external_send(message):
            if scope.get('path') == '/api/flowchart/draft':
                if message['type'] == 'http.response.start' and fault in {'copied-body', 'copied-record'}:
                    attempt = next(value for value in admission._flow_requests.pending.values() if value.scope is scope)
                    assert attempt.handler_done and not attempt.failed
                    captured.append(attempt)
                    if fault == 'copied-body': attempt.values['req'] = attempt.values['req'].model_copy(deep=True)
                    else: attempt.draft_record = dict(attempt.draft_record)
                if message['type'] == 'http.response.body' and fault == 'response-send':
                    raise OSError('actual draft response send failed')
            await send(message)
        await app(scope, receive, external_send)

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_transport,
                raise_app_exceptions=fault != 'publication-permission'), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            opened = await client.get('/api/flowchart/pipelines/'+seed, headers=headers)
            assert opened.status_code == 200 and opened.json() == graph.model_dump()
            assert admission.snapshot() == {'active_scopes': 0, 'unsupported': []}
            before = _basis(Path(project['project_dir']))
            if fault == 'model-dump-delegate':
                original = flows.FlowchartPipeline.model_dump
                def delegated(model, *args, **kwargs): return original(model, *args, **kwargs)
                monkeypatch.setattr(flows.FlowchartPipeline, 'model_dump', delegated)
            elif fault == 'temporary-opener-delegate':
                original = tempfile._mkstemp_inner
                def delegated(*args, **kwargs): return original(*args, **kwargs)
                monkeypatch.setattr(tempfile, '_mkstemp_inner', delegated)
            elif fault == 'write-helper-delegate':
                original = flows._write_json
                def delegated(*args, **kwargs): return original(*args, **kwargs)
                monkeypatch.setattr(flows, '_write_json', delegated)
            mode = draft.parent.stat().st_mode & 0o777
            if fault == 'publication-permission': draft.parent.chmod(0o500)
            try:
                if fault == 'response-send':
                    with pytest.raises(OSError, match='actual draft response send failed'):
                        await client.put('/api/flowchart/draft', headers=headers, json=payload)
                    response = None
                elif fault == 'malformed-json':
                    response = await client.put('/api/flowchart/draft',
                        headers={**headers, 'content-type':'application/json'}, content=b'{')
                else: response = await client.put('/api/flowchart/draft', headers=headers, json=payload)
            finally:
                if fault == 'publication-permission': draft.parent.chmod(mode)
            if response is not None:
                assert response.status_code == expected_status
                if expected_status == 200:
                    assert response.json() == {**json.loads(draft.read_bytes()), 'active_version_id': None}
                    assert response.json()['base_version_id'] == payload['base_version_id']
                elif fault == 'context': assert response.json() == {'detail':'임시 저장본의 프로젝트, 데이터셋 또는 라벨 목록이 바뀌었습니다. 플로우를 다시 연 뒤 시도하세요.'}
                elif fault == 'invalid-base': assert response.json() == {'detail':'base_version_id는 저장된 플로우 버전 id이거나 비어 있어야 합니다.'}
                elif fault == 'malformed-json': assert response.json() == {'detail':'Request data is outside this project or invalid'}
            if expected_status in {403,409,422,500}: assert _basis(Path(project['project_dir'])) == before
            return response

    response = asyncio.run(original_sequence())
    assert audit.verify() == {'intact':True, 'rows':0}
    assert all(handle.closed for attempt in admission._flow_requests.records for handle,_ in attempt.draft_reads)
    preclose = admission.snapshot(); admission.close(); drain = admission.drain(.03)
    (tmp_path/'actual-draft-refusal.json').write_text(json.dumps({'fault':fault,
        'HTTP_status':response.status_code if response is not None else None,
        'admission':preclose,'drain':drain,'failures':observer.snapshot()['failures'],
        'source_only':True,'native_Root14_cause_proven':False},indent=2))
    assert drain['status'] == 'refused' and 'uncovered_request_protocol' in drain['unsupported']
    if captured: assert captured[0].failed and not captured[0].settled
    _no_whole_credit(observer)


@pytest.mark.parametrize('active', [False, True])
def test_original_draft_initial_and_unchanged_saved_graph_keep_200_and_drain(tmp_path, monkeypatch, active):
    """The absent-pointer and same-checksum normal branches need their own joins."""
    from backend.api import routes_flowchart as flows
    monkeypatch.chdir(tmp_path)
    app, admission, observer, _, project, graph, seed, _, audit = _stack(tmp_path, monkeypatch)
    if not active: flows._active_flow_file(Path(project['project_dir'])).unlink()
    context = {'project_id': project['id'], 'source_dataset_path': project['source_dataset_dir'],
               'labelset_id': project.get('active_labelset_id', 'default')}
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid')as client:
            headers = await _login_current(client,app,project)
            response = await client.put('/api/flowchart/draft',headers=headers,json={
                'pipeline':graph.model_dump(),'context':context,'base_version_id':seed if active else 'none'})
            assert response.status_code == 200
            assert response.json() == {**json.loads(flows._draft_file(project,context).read_bytes()),
                'active_version_id':seed if active else None}
            return response
    asyncio.run(sequence())
    attempt = admission._flow_requests.records[-1]
    assert attempt.name == 'save_flow_draft' and attempt.settled
    assert len(attempt.draft_reads) == (2 if active else 0)
    assert attempt.draft_active == [seed if active else None]
    assert attempt.draft_stages == ['open','flushed','published','clean'] and attempt.draft_handle.closed
    assert all(handle.closed for handle,_ in attempt.draft_reads)
    assert audit.verify() == {'intact':True,'rows':0} and observer.snapshot()['failures'] == []
    admission.close()
    assert admission.drain(.03) == {'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    _no_whole_credit(observer)


@pytest.mark.parametrize('pointer_kind', ['dangling-symlink', 'directory'])
def test_nonfile_active_pointer_is_not_proved_absent_for_draft_ownership(tmp_path, monkeypatch, pointer_kind):
    """An unchanged business None result cannot prove a foreign pointer absent."""
    from backend.api import routes_flowchart as flows
    monkeypatch.chdir(tmp_path)
    app, admission, observer, _, project, graph, _, _, audit = _stack(tmp_path, monkeypatch)
    pointer = flows._active_flow_file(Path(project['project_dir']))
    pointer.unlink()
    if pointer_kind == 'dangling-symlink': pointer.symlink_to('never-created-active.json')
    else: pointer.mkdir()
    context = {'project_id':project['id'],'source_dataset_path':project['source_dataset_dir'],
        'labelset_id':project.get('active_labelset_id','default')}
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid')as client:
            headers = await _login_current(client,app,project)
            assert admission.snapshot() == {'active_scopes':0,'unsupported':[]}
            response = await client.put('/api/flowchart/draft',headers=headers,json={
                'pipeline':graph.model_dump(),'context':context,'base_version_id':'none'})
            assert response.status_code == 200
            assert response.json() == {**json.loads(flows._draft_file(project,context).read_bytes()),'active_version_id':None}
            assert pointer.is_symlink() if pointer_kind == 'dangling-symlink' else pointer.is_dir()
    asyncio.run(sequence())
    assert audit.verify() == {'intact':True,'rows':0}
    rows = observer.snapshot()['records']
    assert len(rows) == 3 and all(row['completed']and row['normal_return']and row['released']for row in rows)
    admission.close(); drain = admission.drain(.03)
    (tmp_path/'actual-unproved-pointer.json').write_text(json.dumps({'pointer_kind':pointer_kind,
        'HTTP_status':200,'drain':drain,'failures':observer.snapshot()['failures'],
        'source_only':True,'native_Root14_cause_proven':False},indent=2))
    assert drain['status'] == 'refused' and 'uncovered_request_protocol'in drain['unsupported']
    _no_whole_credit(observer)
