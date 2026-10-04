"""Production policy admission and cycle writes never revert accepted revisions."""
import hashlib
import threading
from pathlib import Path

from backend.engine import model_operations as ops
from backend.api.routes_model_operations import OperationsPolicy


def fixture(tmp_path, monkeypatch):
    for name in ('source', 'annotations', 'dataset', 'models'):
        (tmp_path / name).mkdir()
    checkpoint = tmp_path / 'model.pt'
    checkpoint.write_bytes(b'fixture; no model execution')
    project = {'id': 'p', 'project_dir': str(tmp_path), 'annotations_dir': str(tmp_path / 'annotations'),
               'dataset_dir': str(tmp_path / 'dataset'), 'models_dir': str(tmp_path / 'models'),
               'source_dataset_dir': str(tmp_path / 'source'), 'task': 'classification', 'active_labelset_id': 'default'}
    policy = OperationsPolicy(parent_job_id='parent', reviewer='old').model_dump()
    policy.update(revision=1, labelset_id='default', parent_checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                  holdout={}, seen={}, seen_labels={}, input_version='v')
    store = ops.OperationsStore(tmp_path)
    store.save_policy(policy)
    monkeypatch.setattr(ops, '_checkpoint', lambda *a: checkpoint)
    monkeypatch.setattr(ops, '_holdout', lambda *a: {})
    monkeypatch.setattr(ops, '_inventory', lambda *a: {str(tmp_path / 'source' / 'new.png'): 'digest'})
    monkeypatch.setattr(ops, '_input_version', lambda *a: 'v')
    monkeypatch.setattr(ops, '_image_binding', lambda *a: {})
    return project, store


def test_configuration_cannot_enter_between_cycle_policy_capture_and_journal(tmp_path, monkeypatch):
    project, store = fixture(tmp_path, monkeypatch)
    original = ops.OperationsStore.policy
    captured = threading.Event()
    release = threading.Event()
    result = []
    def pause_after_read(self):
        value = original(self)
        if threading.current_thread().name == 'fixture-cycle' and not captured.is_set():
            captured.set()
            assert release.wait(5)
        return value
    monkeypatch.setattr(ops.OperationsStore, 'policy', pause_after_read)
    worker = threading.Thread(name='fixture-cycle', target=lambda: result.append(ops.run_cycle(project, threading.Event())))
    worker.start()
    try:
        assert captured.wait(5)
        error = None
        try:
            ops.configure_program(project, {'parent_job_id': 'parent', 'reviewer': 'new'})
        except ValueError as exc:
            error = exc
        assert error is not None, 'configuration was accepted before running journal and can be reverted by old cycle'
        assert getattr(error, 'code', None) == 'operations_busy'
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert result[0]['status'] == 'completed'
    assert store.policy()['revision'] == 1
    assert store.policy()['reviewer'] == 'old'


def test_final_cycle_update_refuses_newer_policy_and_records_conflict(tmp_path, monkeypatch):
    project, store = fixture(tmp_path, monkeypatch)
    original = ops.OperationsStore.save_policy
    def replace_before_cycle_save(self, policy, **kwargs):
        newer = {**policy, 'revision': 2, 'reviewer': 'new', 'auto_approve': False}
        original(self, newer)
        return original(self, policy, **kwargs)
    monkeypatch.setattr(ops.OperationsStore, 'save_policy', replace_before_cycle_save)
    result = ops.run_cycle(project, threading.Event())
    assert store.policy()['revision'] == 2, 'stale cycle overwrote accepted newer policy'
    assert store.policy()['reviewer'] == 'new'
    assert result['status'] == 'interrupted'
    assert result['error_code'] == 'policy_revision_conflict'
    assert result['policy_revision'] == 1
    assert result['policy_conflict'] == {'expected_revision': 1, 'current_revision': 2}
    assert store.get(result['cycle_id'])['status'] == 'interrupted'


def test_policy_cas_serializes_two_writers_and_keeps_winner_intent(tmp_path, monkeypatch):
    import concurrent.futures
    project, store = fixture(tmp_path, monkeypatch)
    previous = store.policy()
    barrier = threading.Barrier(2)
    def write(reviewer):
        barrier.wait(timeout=5)
        try:
            ops.OperationsStore(tmp_path).save_policy({**previous, 'revision': 2, 'reviewer': reviewer}, expected_revision=1)
            return ('saved', reviewer)
        except ops.PolicyRevisionConflict as exc:
            return (exc.code, reviewer)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, ['first', 'second']))
    assert sorted(row[0] for row in outcomes) == ['policy_revision_conflict', 'saved']
    assert store.policy()['reviewer'] == next(reviewer for status, reviewer in outcomes if status == 'saved')
    assert store.policy()['revision'] == 2


def test_legacy_save_and_missing_revision_configuration_remain_compatible(tmp_path, monkeypatch):
    project, store = fixture(tmp_path, monkeypatch)
    legacy = store.policy()
    legacy.pop('revision')
    store.save_policy(legacy)
    result = ops.configure_program(project, {'parent_job_id': 'parent', 'reviewer': 'legacy-upgrade'})
    assert result['revision'] == 1
    assert store.policy()['reviewer'] == 'legacy-upgrade'
    store.save_policy({**result, 'reviewer': 'direct-legacy-caller'})
    assert store.policy()['reviewer'] == 'direct-legacy-caller'


def test_cancelled_cycle_releases_admission_and_does_not_change_policy(tmp_path, monkeypatch):
    project, store = fixture(tmp_path, monkeypatch)
    before = store.policy()
    event = threading.Event()
    event.set()
    result = ops.run_cycle(project, event)
    assert result['status'] == 'cancelled'
    assert store.policy() == before
    configured = ops.configure_program(project, {'parent_job_id': 'parent', 'reviewer': 'after-cancel'})
    assert configured['revision'] == 2


def test_terminal_cycle_allows_new_configuration_without_reverting_journal(tmp_path, monkeypatch):
    project, store = fixture(tmp_path, monkeypatch)
    result = ops.run_cycle(project, threading.Event())
    assert result['status'] == 'completed'
    assert 'approval' not in result['result']
    configured = ops.configure_program(project, {'parent_job_id': 'parent', 'reviewer': 'after-terminal'})
    assert configured['revision'] == 2
    assert store.get(result['cycle_id'])['policy_revision'] == 1
    assert store.get(result['cycle_id'])['status'] == 'completed'


def test_policy_api_returns_typed_conflict_then_recovers_after_admission_release(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import routes_model_operations as routes
    project, store = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(routes, 'project', lambda request: project)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        with ops._cycle_lock(project):
            response = client.put('/api/model-operations/policy', json={'parent_job_id': 'parent', 'reviewer': 'new'})
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'operations_busy'
        response = client.put('/api/model-operations/policy', json={'parent_job_id': 'parent', 'reviewer': 'new'})
        assert response.status_code == 200
        assert response.json()['revision'] == 2
