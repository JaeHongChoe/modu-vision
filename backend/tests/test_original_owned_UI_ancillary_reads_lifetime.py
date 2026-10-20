"""External Root-only tests-first draft for finite original UI reads.

No product implementation or acceptance is supplied here. Each case creates a
fresh real owned store/app/admission, then uses the original shared auth/current
ASGI stack and one original UI request. No route, account/DB, profile store,
OpenVINO discovery, image decoder, package reader or lifetime witness is mocked.
The existing _login_stack supplier only provides the actual admission object.

The small PNG and draft record are disclosed data setup before all requests,
using original serialization helpers. No checkpoint/model is loaded or run.
Runtime-capabilities retains its original optional device-discovery subprocess
and its original 15000 ms bound; a 200 response alone grants no child credit.
"""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest


_CASES = (
    'mount-compute-profiles', 'mount-compute-selection',
    'mount-draft-absent', 'mount-draft-present', 'save-confirm-diff',
    'selected-image-info', 'export-runtime-capabilities', 'export-edge-targets',
    'export-test-images-empty', 'export-image-fallback', 'export-package-library-empty',
)


@pytest.mark.parametrize('case', _CASES)
def test_original_UI_ancillary_read_preserves_data_and_drains(case, tmp_path, monkeypatch):
    from PIL import Image
    from backend.api import routes_flowchart as flows
    from backend.engine.flow_provenance import pipeline_sha256
    from backend.tests.test_original_owned_flow_lifetime import (
        _stack, _login_current, _edited, _no_whole_credit,
    )

    monkeypatch.chdir(tmp_path)
    app, admission, observer, actor, project, graph, seed, recipe, audit = _stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    source = Path(project['source_dataset_dir'])
    image = source/'owned-ancillary-image.png'
    Image.new('RGB', (8, 6), (30, 70, 90)).save(image)
    draft_file = None
    if case == 'mount-draft-present':
        context = flows._draft_context(project)
        draft_file = flows._draft_file(project, context)
        # Original-format user draft data, not a claimed HTTP save or witness.
        flows._write_json(draft_file, {
            'version':1, 'context':context, 'draft_sha256':pipeline_sha256(graph),
            'saved_at_ns':1, 'pipeline':graph.model_dump(), 'base_version_id':seed,
        })
    protected = (image, recipe, flows._active_flow_file(root),
                 flows._version_dir(root)/f'{seed}.json', root/'project.json')
    if draft_file is not None:
        protected = (*protected, draft_file)
    basis = {path:path.read_bytes() for path in protected}
    image_sha = hashlib.sha256(basis[image]).hexdigest()
    assert observer.snapshot()['records'] == []

    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if case == 'mount-compute-profiles':
                response = await client.get('/api/compute/profiles', headers=headers)
                assert response.status_code == 200
                assert set(response.json()) == {'profiles'}
                assert type(response.json()['profiles']) is list
            elif case == 'mount-compute-selection':
                response = await client.get('/api/compute/selection', headers=headers)
                assert response.status_code == 200
                assert set(response.json()) == {'compute_profile_id'}
                selected = response.json()['compute_profile_id']
                assert selected is None or type(selected) is str
            elif case in {'mount-draft-absent', 'mount-draft-present'}:
                response = await client.get('/api/flowchart/draft', headers=headers)
                if case == 'mount-draft-absent':
                    # The real UI tries draft first. An absent draft is a normal
                    # 404 result, then it reads the separately proved active flow.
                    assert response.status_code == 404
                    assert response.json() == {'detail':'No draft for this project, source and labelset'}
                    assert not flows._draft_file(project, flows._draft_context(project)).exists()
                else:
                    assert response.status_code == 200
                    body = response.json()
                    assert body['context'] == flows._draft_context(project)
                    assert body['pipeline'] == graph.model_dump()
                    assert body['draft_sha256'] == pipeline_sha256(graph)
                    assert body['base_version_id'] == body['active_version_id'] == seed
            elif case == 'save-confirm-diff':
                response = await client.post('/api/flowchart/pipeline/diff', headers=headers,
                    params={'expected_version_id':seed}, json=_edited(graph).model_dump())
                assert response.status_code == 200
                body = response.json()
                assert set(body) == {'parent_revision','stale','semantic_delta','layout_only'}
                assert body['parent_revision'] == seed and body['stale'] is False
                assert body['layout_only'] is False and body['semantic_delta']
            elif case == 'selected-image-info':
                response = await client.get('/api/flow-workspace/image-info', headers=headers,
                    params={'image_path':str(image)})
                assert response.status_code == 200
                assert response.json() == {'width':8, 'height':6, 'sha256':image_sha}
            elif case == 'export-runtime-capabilities':
                response = await client.get('/api/export/runtime-capabilities', headers=headers)
                assert response.status_code == 200
                body = response.json()
                assert set(body) == {'torch_devices','openvino','native_sdk',
                                     'hardware_acceptance','optimization_precisions'}
                assert body['torch_devices'][0] == 'cpu'
                assert type(body['openvino']['available']) is bool
                assert type(body['openvino']['devices']) is list
                assert body['hardware_acceptance'] == 'requires_target_execution'
            elif case == 'export-edge-targets':
                response = await client.get('/api/export/edge-targets', headers=headers)
                assert response.status_code == 200
                body = response.json()
                assert body['profile'] == 'edge_cpu' and body['device'] == 'cpu'
                assert body['supported']['macos'] == ['arm64']
                assert body['python'] == {'minimum':'3.10','maximum_exclusive':'3.14'}
            elif case in {'export-test-images-empty', 'export-image-fallback'}:
                params = {'folder_path':str(source), 'task':'segmentation', 'limit':32}
                if case == 'export-test-images-empty':
                    params['split'] = 'test'
                response = await client.get('/api/dataset/images', headers=headers, params=params)
                assert response.status_code == 200
                body = response.json()
                assert body['limit'] == 32 and body['offset'] == 0
                if case == 'export-test-images-empty':
                    assert body['total'] == 0 and body['items'] == []
                else:
                    assert body['total'] == 1 and len(body['items']) == 1
                    assert body['items'][0]['file_path'] == str(image)
                    assert body['items'][0]['width'] == 8 and body['items'][0]['height'] == 6
            elif case == 'export-package-library-empty':
                response = await client.get('/api/product-delivery/packages', headers=headers)
                assert response.status_code == 200
                assert response.json() == {'packages':[], 'selected_package_id':None,
                    'scope':{'project_id':project['id'], 'source_dataset_path':str(source),
                             'task':'segmentation'}}
                assert (root/'delivery').is_dir()
                assert not (root/'delivery'/'library.json').exists()
            else:
                raise AssertionError('Unknown fixed original UI case')
            return response.request.method, response.request.url.path

    method, path = asyncio.run(original_request())
    assert all(file.read_bytes() == raw for file, raw in basis.items())
    assert audit.verify() == {'intact':True, 'rows':0}
    assert {file.name for file in flows._version_dir(root).iterdir()} == {seed+'.json'}
    assert app.state.accounts.project_for(actor['id'])['id'] == project['id']
    rows = observer.snapshot()['records']
    assert len(rows) == 3 and len({row['identity'] for row in rows}) == 3
    assert rows[-1]['ingress'] == {'type':'http','method':method,'path':path}
    assert all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in rows)
    # Causal gate comes after original HTTP/body/data assertions. Unsupported
    # paths must not be made green by deleting entries or sharing a status bit.
    admission.close()
    assert admission.drain(.03) == {
        'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}
    assert observer.snapshot()['all_settled']
    assert all(row['completed'] for row in observer.snapshot()['records'])
    _no_whole_credit(observer)



def test_original_injected_metadata_Request_alias_cannot_inherit_lifetime(tmp_path, monkeypatch):
    """A same-scope replacement is not the original injected FastAPI Request."""
    from starlette.requests import Request
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    monkeypatch.chdir(tmp_path)
    app, admission, observer, _, project, _, _, _, audit = _stack(tmp_path, monkeypatch)
    altered = []
    async def original_transport(scope, receive, send):
        async def original_send(message):
            if scope.get('path') == '/api/export/edge-targets' and message['type'] == 'http.response.start':
                attempts = [a for a in admission._UI_ancillary_requests.pending.values() if a.scope is scope]
                assert len(attempts) == 1 and attempts[0].handler_done
                attempt = attempts[0]
                original_request = attempt.values['_owned_http_request']
                assert original_request is attempt.request and original_request.scope is scope
                replacement = Request(scope)
                assert replacement is not original_request and replacement.scope is scope
                attempt.values['_owned_http_request'] = replacement
                altered.append(attempt)
            await send(message)
        await app(scope, receive, original_send)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_transport),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/export/edge-targets', headers=headers)
            assert response.status_code == 200
            assert response.json()['profile'] == 'edge_cpu' and response.json()['device'] == 'cpu'
    asyncio.run(original_sequence())
    assert len(altered) == 1
    assert audit.verify() == {'intact':True,'rows':0}
    rows = observer.snapshot()['records']
    assert len(rows) == 3 and all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in rows)
    _refused(admission)
    _no_whole_credit(observer)


def test_original_PIL_open_then_real_data_error_closes_handle_and_refuses(tmp_path, monkeypatch):
    """Real temporary input becomes unreadable after its actual PNG was opened.

    The fault injector runs at the original bytes-reader seam, after the
    unchanged decoder and observer retained the real PNG and fp. It moves only
    this fresh fixture image and puts a directory at that name.
    The original hash reader raises IsADirectoryError. No success/error response,
    PIL object, handler or close operation is fabricated; original bytes remain
    at the retained sibling path. This is a data-error cleanup/refusal control,
    not evidence that a source delegate was accepted as original.
    """
    from PIL import Image
    from backend.api import routes_flow_workspace as workspace
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    monkeypatch.chdir(tmp_path)
    app, admission, observer, _, project, _, _, _, audit = _stack(tmp_path, monkeypatch)
    image = Path(project['source_dataset_dir'])/'owned-real-open-error.png'
    retained = image.with_name('retained-original-real-open-error.png')
    Image.new('RGB', (8, 6), (30, 70, 90)).save(image)
    original_bytes = image.read_bytes()
    original_reader = workspace.original_UI_ancillary_read_bytes
    captured = []
    def actual_read_then_data_fault(path, *, role):
        attempts = [a for a in admission._UI_ancillary_requests.pending.values()
                    if a.name == 'image_info']
        assert len(attempts) == 1 and attempts[0].worker_live
        assert len(attempts[0].images) == 1
        opened, handle, observed_path = attempts[0].images[0]
        assert handle is not None and not handle.closed
        assert Path(path) == observed_path == image and opened.size == (8, 6)
        image.rename(retained)
        image.mkdir()
        captured.append((opened, handle))
        return original_reader(path, role=role)
    monkeypatch.setattr(workspace, 'original_UI_ancillary_read_bytes', actual_read_then_data_fault)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/flow-workspace/image-info', headers=headers,
                params={'image_path':str(image)})
            assert response.status_code == 422
            assert response.json() == {'detail':'Image cannot be decoded'}
    asyncio.run(original_sequence())
    assert len(captured) == 1
    opened, handle = captured[0]
    assert handle.closed and opened.fp is None
    assert image.is_dir() and retained.read_bytes() == original_bytes
    assert audit.verify() == {'intact':True,'rows':0}
    assert len(observer.snapshot()['records']) == 3
    _refused(admission)
    _no_whole_credit(observer)


def test_original_joined_discovery_Popen_instance_delegate_cannot_settle(tmp_path, monkeypatch):
    """After real discovery joins, a replaced instance method loses its binding.

    No constructor, source class, process factory, observer or successful result
    is replaced. Mutation occurs only at actual original ASGI response.start,
    after the real Popen's natural return and both output handles' closure.
    The original discovery15s bound and original JSON body remain unchanged.
    """
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    from backend.tests.test_original_owned_account_request_lifetime import _refused
    monkeypatch.chdir(tmp_path)
    app, admission, observer, _, project, _, _, _, audit = _stack(tmp_path, monkeypatch)
    altered = []
    async def original_transport(scope, receive, send):
        async def original_send(message):
            if scope.get('path') == '/api/export/runtime-capabilities' and message['type'] == 'http.response.start':
                attempts = [a for a in admission._UI_ancillary_requests.pending.values() if a.scope is scope]
                assert len(attempts) == 1
                attempt = attempts[0]
                assert attempt.handler_done and not attempt.worker_live and not attempt.process_live
                assert len(attempt.processes) == 1
                process, stdout, stderr, natural_returncode, confirmed = attempt.processes[0]
                assert type(natural_returncode) is int and natural_returncode == process.returncode
                assert confirmed is True and stdout.closed and stderr.closed
                assert process.wait.__self__ is process
                original_wait = process.wait
                def delegated_wait(*args, **kwargs):
                    return original_wait(*args, **kwargs)
                monkeypatch.setattr(process, 'wait', delegated_wait)
                assert process.wait is delegated_wait
                altered.append((process, stdout, stderr, natural_returncode))
            await send(message)
        await app(scope, receive, original_send)
    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=original_transport),
                                     base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/export/runtime-capabilities', headers=headers)
            assert response.status_code == 200
            body = response.json()
            assert body['torch_devices'][0] == 'cpu'
            assert type(body['openvino']['available']) is bool and type(body['openvino']['devices']) is list
            assert body['hardware_acceptance'] == 'requires_target_execution'
    asyncio.run(original_sequence())
    assert len(altered) == 1
    process, stdout, stderr, natural_returncode = altered[0]
    assert process.returncode == natural_returncode and stdout.closed and stderr.closed
    assert audit.verify() == {'intact':True,'rows':0}
    rows = observer.snapshot()['records']
    assert len(rows) == 3 and all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in rows)
    _refused(admission)
    _no_whole_credit(observer)



def test_original_ancillary_inclusion_instance_delegate_cannot_settle(tmp_path, monkeypatch):
    """Modern effective-context source retains the original bound methods.

    Only the export inclusion instance's original method is replaced after app
    construction. The delegate calls its unchanged real original method; every
    original ASGI/auth/current/HTTP/business result still runs normally.
    This control targets the modern IncludedRouter lane used by Root's RED.
    """
    from types import MethodType
    from backend.api import routes_export
    from backend.engine.original_owned_flow_request import _FlowIncluded
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    monkeypatch.chdir(tmp_path)
    app, admission, observer, _, project, _, _, _, audit = _stack(tmp_path, monkeypatch)
    owner = admission._UI_ancillary_requests
    inclusion = owner.included.get(routes_export.__name__)
    assert type(inclusion) is _FlowIncluded, 'This composition uses the actual shared modern IncludedRouter lane'
    assert inclusion is admission._flow_execution_requests.export_included
    original = inclusion.original
    assert type(original) is MethodType and original.__self__ is inclusion
    def original_delegate(self):
        assert self is inclusion
        return original()
    monkeypatch.setattr(inclusion, 'original', MethodType(original_delegate, inclusion))
    assert inclusion.original.__self__ is inclusion
    assert inclusion.original.__func__ is original_delegate
    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/export/edge-targets', headers=headers)
            assert response.status_code == 200
            body = response.json()
            assert body['profile']=='edge_cpu' and body['device']=='cpu'
            assert body['supported']['macos']==['arm64']
            assert body['python']=={'minimum':'3.10','maximum_exclusive':'3.14'}
    asyncio.run(sequence())
    assert audit.verify()=={'intact':True,'rows':0}
    rows = observer.snapshot()['records']
    assert len(rows)==3 and all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in rows)
    admission.close()
    assert admission.drain(.03)=={
        'status':'refused','active_scopes':0,'unsupported':['uncovered_request_protocol']}
    _no_whole_credit(observer)
