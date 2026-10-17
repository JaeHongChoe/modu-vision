"""Tests first: genuine ASGI / original model-free CPU partial cancellation.

Root alone executes this source in a fresh successor of immutable R760.
The fixed ROI and patch-split node perform real original pixel work. A required
model node remains outside the requested ancestor scope: no checkpoint, model
construction, training, GPU, SSH, native ownership or quality claim is made.
The profile hook observes the exact original nested code and delays its return;
it replaces no handler, engine, executor, result, auth, DB or ownership method.
"""
import asyncio
from contextlib import contextmanager
from pathlib import Path
import sys
import threading

import httpx


PREPARE = '/api/flowchart/partial-runs/prepare'
RUN = '/api/flowchart/run'


def _stack(tmp_path, monkeypatch):
    from PIL import Image
    from backend.api import routes_flowchart as flows
    from backend.engine.flowchart_engine import (
        FlowNode, FlowNodeData, FlowEdge, get_fixed_roi_flowchart,
        ordered_linear_nodes, debug_ancestor_ids,
    )
    from backend.tests.test_original_owned_flow_lifetime import _stack as original_stack

    app, admission, observer, actor, project, _, _, _, audit = original_stack(tmp_path, monkeypatch)
    root = Path(project['project_dir'])
    source = Path(project['source_dataset_dir'])
    image = source/'partial-cancel-original-pixels.png'
    Image.new('RGB', (64, 64), (40, 80, 120)).save(image)
    graph = get_fixed_roi_flowchart()
    graph.execution_config.max_workers = 1
    graph.execution_config.device_slots = 1
    roi = next(n for n in graph.nodes if n.data.node_type == 'fixed_roi')
    roi.data.params['roi_bbox'] = [0, 0, 32, 32]
    model = next(n for n in graph.nodes if n.data.node_type == 'inspection')
    split = FlowNode(id='node_partial_split', position={'x':480, 'y':160},
        data=FlowNodeData(label='Original CPU patch split', node_type='patch_split',
                         params={'patch_width':16, 'patch_height':16, 'overlap':0}))
    graph.nodes.insert(graph.nodes.index(model), split)
    graph.edges = [edge for edge in graph.edges if edge.id != 'fixed-inspect'] + [
        FlowEdge(id='fixed-partial-split', source=roi.id, target=split.id, payload_type='roi'),
        FlowEdge(id='partial-split-inspect', source=split.id, target=model.id, payload_type='roi')]
    assert model.data.model_job_id is None
    assert {n.id for n in ordered_linear_nodes(graph)} == {n.id for n in graph.nodes}
    assert debug_ancestor_ids(graph, split.id) == {'node_input', roi.id, split.id}
    seed = flows._save_version(graph, 'segmentation', str(source), root)
    recipe = flows._recipe_file('segmentation', str(source), root)
    flows._write_pipeline(recipe, graph)
    flows._write_json(flows._active_flow_file(root), {
        'version_id':seed, 'project_id':project['id'],
        'labelset_id':project.get('active_labelset_id', 'default'),
        'recipe_task':'segmentation', 'source_dataset_path':str(source)})
    protected = [root/'project.json', recipe, flows._active_flow_file(root), image,
                 *sorted(flows._version_dir(root).glob('*.json'))]
    basis = {path:path.read_bytes() for path in protected}
    body = {'pipeline':graph.model_dump(), 'image_path':str(image),
            'image_id':'owned-partial-cancel-pixels', 'project_id':project['id'],
            'execution_target':'local', 'device':'cpu', 'stop_node_id':split.id}
    assert observer.snapshot()['records'] == []
    return app, admission, observer, project, graph, body, roi.id, split.id, basis, audit


def _unchanged(basis, audit):
    assert all(path.read_bytes() == raw for path, raw in basis.items())
    assert audit.verify() == {'intact':True, 'rows':0}


def _no_native_claim(observer):
    # This functional source test is not a new native typed-partial witness.
    assert all(observer.snapshot()[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


async def _prepare(client, headers, body):
    response = await client.post(PREPARE, headers=headers, json=body)
    # Expected first R760 causal RED: original auth/context/setup passed, then
    # the genuinely absent prepare API returns 404, rather than an import error.
    assert response.status_code == 200, response.text
    row = response.json()
    assert type(row['run_id']) is str and row['run_id']
    assert row['state'] == 'prepared' and row['execution_finished'] is False
    assert row['project_id'] == body['project_id']
    assert row['stop_node_id'] == body['stop_node_id']
    assert row['execution_target'] == 'local' and row['device'] == 'cpu'
    return row['run_id']


async def _terminal(client, headers, run_id):
    response = await client.get('/api/flowchart/partial-runs/'+run_id, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['run_id'] == run_id
    return body


async def _join_original_run(task):
    """Await the original request; never cancel a Task as a substitute for join."""
    if task is None:
        return None
    # The sole test barrier is always released before this call. Pixel work is
    # finite (one 32x32 ROI/four 16x16 patches), with no model/network wait.
    done, _ = await asyncio.wait((task,), timeout=10)
    if task not in done:
        # Keep the real request alive and await it before reporting the timeout.
        # Its asyncio cancellation would not prove the AnyIO worker stopped.
        await asyncio.shield(task)
        raise AssertionError('Original finite CPU request exceeded its 10s join diagnostic bound')
    return await task


class _OriginalNodeBarrier:
    def __init__(self, node_id):
        from backend.engine import flowchart_engine as module
        # Exact existing observer -> tracked_execution -> original execute
        # chain, used only to choose a profile event, never to grant authority.
        original = module.FlowchartEngine.execute.__wrapped__.__wrapped__
        assert original.__code__.co_filename == module.__file__
        codes = [c for c in original.__code__.co_consts
                 if isinstance(c, type(original.__code__)) and c.co_name == 'process_node']
        assert len(codes) == 1
        self.code = codes[0]
        self.node_id = node_id
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.paused = False
        self.timeout = False
        self.calls = []
        self.returns = []
        self.errors = []
        self.previous = None

    def profile(self, frame, event, result):
        if self.previous is not None:
            self.previous(frame, event, result)
        if frame.f_code is not self.code:
            return
        node = frame.f_locals.get('selected_node')
        if node is None:
            self.errors.append('original selected_node missing')
            return
        if event == 'call':
            self.calls.append(node.id)
        elif event == 'return':
            if not (type(result) is tuple and len(result) == 7
                    and any(step.node_id == node.id and step.status == 'passed' for step in result[3])):
                self.errors.append('original node did not return its actual passed trace')
                return
            self.returns.append(node.id)
            with self.lock:
                pause = node.id == self.node_id and not self.paused
                if pause:
                    self.paused = True
            if pause:
                self.entered.set()
                if not self.release.wait(8):
                    self.timeout = True

    @contextmanager
    def installed(self):
        # Install before the first ASGI request creates its AnyIO sync worker.
        # Existing callable objects/code/results remain byte-identical.
        self.previous = threading.getprofile()
        previous_current = sys.getprofile()
        threading.setprofile(self.profile)
        try:
            yield self
        finally:
            self.release.set()
            threading.setprofile(self.previous)
            assert sys.getprofile() is previous_current

    async def wait(self):
        assert await asyncio.to_thread(self.entered.wait, 5), 'Original CPU node did not reach its bounded barrier'

    def verify(self):
        assert self.paused and not self.timeout and not self.errors


def test_real_CPU_partial_cancel_waits_for_original_worker_and_never_caches(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.tests.test_original_owned_flow_lifetime import _login_current
    app, _, observer, project, graph, body, roi, split, basis, audit = _stack(tmp_path, monkeypatch)
    barrier = _OriginalNodeBarrier(roi)
    cache = flows._DEBUG_RUN_CACHE
    cached_before = set(cache._rows)

    async def sequence():
        task = None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            try:
                headers = await _login_current(client, app, project)
                run_id = await _prepare(client, headers, body)
                task = asyncio.create_task(client.post(RUN, headers=headers,
                    json={**body, 'partial_run_id':run_id}))
                await barrier.wait()
                assert flows._CPU_ENGINE.execution_resources()['active_executions'] == 1
                assert not task.done() and barrier.calls == [roi] and barrier.returns == [roi]
                duplicate = await client.post(RUN, headers=headers, json={**body, 'partial_run_id':run_id})
                assert duplicate.status_code == 409
                cancelled = await client.post('/api/flowchart/partial-runs/'+run_id+'/cancel', headers=headers)
                assert cancelled.status_code == 200 and cancelled.json()['state'] == 'cancelling'
                waiting = await _terminal(client, headers, run_id)
                assert waiting['state'] == 'cancelling' and waiting['execution_finished'] is False
                assert not task.done() and split not in barrier.calls
                barrier.release.set()
                response = await _join_original_run(task)
                assert response.status_code == 200, response.text
                result = response.json()
                assert result['status'] == 'cancelled' and result['partial_run_id'] == run_id
                assert result['is_ok'] is False and result['final_verdict'] == 'REVIEW'
                terminal = await _terminal(client, headers, run_id)
                assert terminal['state'] == 'cancelled' and terminal['execution_finished'] is True
                assert flows._CPU_ENGINE.execution_resources()['active_executions'] == 0
                assert split not in barrier.calls and set(cache._rows) == cached_before
                # Same input can run again with a fresh server capability. A
                # cancelled attempt must not have published a normal cache hit.
                retry_id = await _prepare(client, headers, body)
                assert retry_id != run_id
                retry = await client.post(RUN, headers=headers, json={**body, 'partial_run_id':retry_id})
                assert retry.status_code == 200, retry.text
                retried = retry.json()
                assert retried['status'] == 'partial' and retried['stop_node_id'] == split
                assert retried['is_ok'] is False and retried['final_verdict'] == 'REVIEW'
                assert retried['debug_cache']['status'] == 'miss'
                assert barrier.calls == [roi, roi, split]
                assert (await _terminal(client, headers, retry_id))['state'] == 'completed'
            finally:
                barrier.release.set()
                await _join_original_run(task)

    with barrier.installed():
        asyncio.run(sequence())
    barrier.verify()
    assert flows._CPU_ENGINE.execution_resources()['active_executions'] == 0
    assert graph.model_dump() == body['pipeline']
    _unchanged(basis, audit)
    _no_native_claim(observer)


def test_prepared_partial_capability_is_project_bound_and_cancel_before_run_starts_nothing(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.tests.test_original_owned_account_request_lifetime import _owned_projects
    from backend.tests.test_original_owned_flow_lifetime import _login_current
    app, _, observer, project, _, body, roi, _, basis, audit = _stack(tmp_path, monkeypatch)
    _, (foreign,) = _owned_projects(app, observer, 'Different original partial project')
    before = set(flows._DEBUG_RUN_CACHE._rows)
    observe_only = _OriginalNodeBarrier(roi)
    observe_only.release.set()

    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            run_id = await _prepare(client, headers, body)
            other = {key:value for key,value in headers.items() if key != 'x-vision-context'}
            other['x-vision-project'] = foreign['id']
            for method, path in [('GET', '/api/flowchart/partial-runs/'+run_id),
                                 ('POST', '/api/flowchart/partial-runs/'+run_id+'/cancel')]:
                response = await client.request(method, path, headers=other)
                assert response.status_code == 404
            assert (await _terminal(client, headers, run_id))['state'] == 'prepared'
            cancelled = await client.post('/api/flowchart/partial-runs/'+run_id+'/cancel', headers=headers)
            assert cancelled.status_code == 200
            terminal = await _terminal(client, headers, run_id)
            assert terminal['state'] == 'cancelled' and terminal['execution_finished'] is True
            assert (await client.post(RUN, headers=headers,
                json={**body, 'partial_run_id':run_id})).status_code == 409

    with observe_only.installed():
        asyncio.run(sequence())
    assert observe_only.calls == [] and observe_only.returns == []
    assert not observe_only.errors and not observe_only.timeout
    assert flows._CPU_ENGINE.execution_resources()['active_executions'] == 0
    assert set(flows._DEBUG_RUN_CACHE._rows) == before
    _unchanged(basis, audit)
    _no_native_claim(observer)


def test_partial_capability_rejects_changed_graph_and_is_one_use_after_completion(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.tests.test_original_owned_flow_lifetime import _login_current
    app, _, observer, project, graph, body, _, split, basis, audit = _stack(tmp_path, monkeypatch)

    async def sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            run_id = await _prepare(client, headers, body)
            changed = graph.model_copy(deep=True)
            next(node for node in changed.nodes if node.id == split).data.params['patch_width'] = 32
            rejected = await client.post(RUN, headers=headers,
                json={**body, 'pipeline':changed.model_dump(), 'partial_run_id':run_id})
            assert rejected.status_code == 409
            assert (await _terminal(client, headers, run_id))['state'] == 'prepared'
            response = await client.post(RUN, headers=headers, json={**body, 'partial_run_id':run_id})
            assert response.status_code == 200, response.text
            result = response.json()
            assert result['status'] == 'partial' and result['stop_node_id'] == split
            assert result['is_ok'] is False and result['final_verdict'] == 'REVIEW'
            assert next(step for step in result['execution_steps'] if step['node_id'] == split)['output_count'] == 4
            completed = await _terminal(client, headers, run_id)
            assert completed['state'] == 'completed' and completed['execution_finished'] is True
            assert (await client.post(RUN, headers=headers,
                json={**body, 'partial_run_id':run_id})).status_code == 409
            # Once original completion wins, a late cancel cannot rewrite it.
            assert (await client.post('/api/flowchart/partial-runs/'+run_id+'/cancel', headers=headers)).status_code == 409
            assert await _terminal(client, headers, run_id) == completed

    asyncio.run(sequence())
    assert flows._CPU_ENGINE.execution_resources()['active_executions'] == 0
    _unchanged(basis, audit)
    _no_native_claim(observer)


def test_partial_cancel_after_real_stop_node_before_publication_never_adopts_result(tmp_path, monkeypatch):
    from backend.api import routes_flowchart as flows
    from backend.tests.test_original_owned_flow_lifetime import _login_current
    app, _, observer, project, _, body, roi, split, basis, audit = _stack(tmp_path, monkeypatch)
    barrier = _OriginalNodeBarrier(split)
    cached_before = set(flows._DEBUG_RUN_CACHE._rows)

    async def sequence():
        task = None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            try:
                headers = await _login_current(client, app, project)
                run_id = await _prepare(client, headers, body)
                task = asyncio.create_task(client.post(RUN, headers=headers,
                    json={**body, 'partial_run_id':run_id}))
                await barrier.wait()
                assert barrier.calls == barrier.returns == [roi, split]
                assert not task.done() and flows._CPU_ENGINE.execution_resources()['active_executions'] == 1
                cancel = await client.post('/api/flowchart/partial-runs/'+run_id+'/cancel', headers=headers)
                assert cancel.status_code == 200 and cancel.json()['state'] == 'cancelling'
                barrier.release.set()
                response = await _join_original_run(task)
                assert response.status_code == 200 and response.json()['status'] == 'cancelled'
                final = await _terminal(client, headers, run_id)
                assert final['state'] == 'cancelled' and final['execution_finished'] is True
                assert set(flows._DEBUG_RUN_CACHE._rows) == cached_before
                again = await client.post('/api/flowchart/partial-runs/'+run_id+'/cancel', headers=headers)
                assert again.status_code == 200 and again.json()['state'] == 'cancelled'
                assert await _terminal(client, headers, run_id) == final
            finally:
                barrier.release.set()
                await _join_original_run(task)

    with barrier.installed():
        asyncio.run(sequence())
    barrier.verify()
    assert flows._CPU_ENGINE.execution_resources()['active_executions'] == 0
    _unchanged(basis, audit)
    _no_native_claim(observer)
