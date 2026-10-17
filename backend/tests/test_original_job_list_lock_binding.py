"""Real jobs HTTP must retain qualification refusal for unknown lock/data inputs.

These explicit negative fixtures change only the challenged provider or job
mapping. The original manager/handler/ownership-checker functions stay intact.
The factory canary replaces ONLY ancillary.threading with a disclosed namespace;
it does not replace the real global threading module or grant ownership.
No model, training job/worker, native launch, controller or lease is executed/adopted.
"""
import asyncio
from _thread import allocate_lock
from pathlib import Path
import threading
from types import SimpleNamespace

import httpx
import pytest


class _UnknownDelegatedLock:
    """Unknown CM even though the object underneath is an actual builtin lock."""
    def __init__(self):
        self.lock = allocate_lock()

    def __enter__(self):
        self.lock.acquire()
        return self

    def __exit__(self, *error):
        self.lock.release()

    def locked(self):
        return self.lock.locked()


def _factory_canary(monkeypatch, ancillary):
    namespace = SimpleNamespace(Lock=_UnknownDelegatedLock,
        RLock=threading.RLock, current_thread=threading.current_thread)
    assert namespace.RLock is threading.RLock
    assert namespace.current_thread is threading.current_thread
    monkeypatch.setattr(ancillary, 'threading', namespace)
    return namespace


def _stack(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'runtime'))
    monkeypatch.setenv('MODU_FLOW_TEMPLATE_DIR', str(tmp_path/'templates'))
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    _, (project,) = _owned_projects(app, observer, 'Owned empty jobs qualification')
    return app, admission, observer, project


def _request(app, project, before_request=None):
    from backend.tests.test_original_owned_flow_lifetime import _login_current
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if before_request is not None:before_request()
            return await client.get('/api/compute/jobs', headers=headers)
    return asyncio.run(request())


def _refused(admission, observer):
    admission.close()
    result = admission.drain(.03)
    assert result['status'] == 'refused'
    assert 'uncovered_request_protocol' in result['unsupported']
    snapshot = observer.snapshot()
    assert not snapshot['all_settled']
    assert all(snapshot[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))
    # Re-reading never clears the original refusal or turns it into success.
    assert 'uncovered_request_protocol' in admission.snapshot()['unsupported']
    return snapshot


def test_unknown_prebound_job_lock_cannot_be_adopted_by_mutable_factory(tmp_path, monkeypatch):
    from backend.api import routes_training
    from backend.engine import original_owned_UI_ancillary_request as ancillary
    manager = routes_training.training_job_manager
    original_function = type(manager).list_jobs
    original_body = original_function.__wrapped__
    canary = _UnknownDelegatedLock()
    monkeypatch.setattr(manager, '_lock', canary)
    namespace = _factory_canary(monkeypatch, ancillary)
    app, admission, observer, project = _stack(tmp_path, monkeypatch)
    owner = admission._UI_ancillary_requests
    assert owner.job_lock is canary and ancillary.threading is namespace
    project_file = Path(project['project_dir'])/'project.json';before = project_file.read_bytes()
    response = _request(app, project)
    # Business reads can succeed normally while qualification correctly refuses.
    assert response.status_code == 200 and response.content == b'{"jobs":[]}'
    assert type(manager).list_jobs is original_function and original_function.__wrapped__ is original_body
    assert not owner.original() and not any(attempt.name == 'jobs' and attempt.settled for attempt in owner.records)
    _refused(admission, observer)
    assert project_file.read_bytes() == before


def test_new_builtin_job_lock_after_bind_cannot_replace_captured_lock(tmp_path, monkeypatch):
    from backend.api import routes_training
    app, admission, observer, project = _stack(tmp_path, monkeypatch)
    owner = admission._UI_ancillary_requests;manager = routes_training.training_job_manager
    captured = owner.job_lock
    response = _request(app, project,
        before_request=lambda: monkeypatch.setattr(manager, '_lock', allocate_lock()))
    assert manager._lock is not captured and owner.job_lock is captured
    assert response.status_code == 200 and response.content == b'{"jobs":[]}'
    assert not owner.original() and not any(attempt.name == 'jobs' and attempt.settled for attempt in owner.records)
    _refused(admission, observer)


def test_actual_builtin_job_lock_does_not_depend_on_mutable_factory(tmp_path, monkeypatch):
    from backend.api import routes_training
    from backend.engine import original_owned_UI_ancillary_request as ancillary
    app, admission, observer, project = _stack(tmp_path, monkeypatch)
    owner = admission._UI_ancillary_requests;captured = routes_training.training_job_manager._lock
    _factory_canary(monkeypatch, ancillary)
    response = _request(app, project)
    assert response.status_code == 200 and response.content == b'{"jobs":[]}'
    assert owner.job_lock is captured and owner.original()
    jobs = [attempt for attempt in owner.records if attempt.name == 'jobs']
    assert len(jobs) == 1 and jobs[0].settled and not jobs[0].failed
    admission.close()
    assert admission.drain(.03) == dict(status='managed_scopes_drained', active_scopes=0, unsupported=[])
    assert observer.snapshot()['all_settled'] and not observer.snapshot()['uncertain']


def test_original_job_list_body_exception_propagates_and_never_settles(tmp_path, monkeypatch):
    from backend.api import routes_training
    manager = routes_training.training_job_manager
    original_function = type(manager).list_jobs;original_body = original_function.__wrapped__
    failure = RuntimeError('explicit invalid job mapping fixture')

    class BrokenJobMapping(dict):
        """Explicit invalid data canary, not a replacement manager/body/checker."""
        def values(self):
            raise failure

    monkeypatch.setattr(manager, '_jobs', BrokenJobMapping())
    app, admission, observer, project = _stack(tmp_path, monkeypatch)
    owner = admission._UI_ancillary_requests
    assert owner.job_records is manager._jobs
    with pytest.raises(RuntimeError) as caught:
        _request(app, project)
    assert caught.value is failure
    assert type(manager).list_jobs is original_function and original_function.__wrapped__ is original_body
    attempts = [attempt for attempt in owner.records if attempt.name == 'jobs']
    assert len(attempts) == 1 and attempts[0].failed and not attempts[0].settled
    assert attempts[0].job_live is None and not attempts[0].job_lists
    snapshot = _refused(admission, observer)
    assert snapshot['uncertain']
    assert snapshot['failures'][0] == 'owned_ancillary_job_list_failed'
    assert any(row['ingress']['path'] == '/api/compute/jobs' and not row['completed']
        and row['error_type'] == 'RuntimeError' for row in snapshot['records'])
