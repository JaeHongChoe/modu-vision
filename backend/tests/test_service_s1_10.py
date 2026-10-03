"""S1-10: job ledger events after a cursor, scoped to the request's workspace and project.

A client takes a cursor, reloads its snapshot, and later reads exactly the events recorded after the cursor, once
each and in ledger order. A cursor of another project or ledger, or one whose event the ledger no longer holds, is
answered with a reset and a fresh cursor instead of a silent gap.
"""
import time

import pytest


def _finished(manager, job_id):
    """The job ended and its thread finished: the thread records the end in the ledger after the status changes."""
    deadline = time.monotonic() + 60  # a loaded or slow runner can take seconds even for a stub launch
    while time.monotonic() < deadline:
        record = manager.get_job(job_id)
        if record is not None and record.status not in ('queued', 'preparing', 'running'):
            if record.thread is not None:
                record.thread.join(30)
                assert not record.thread.is_alive(), f'{job_id} did not finish recording its end'
            return record
        time.sleep(0.02)
    raise AssertionError(f'{job_id} did not finish')


@pytest.fixture
def app(tmp_path, monkeypatch):
    from backend.api import routes_training
    from backend.tests.test_service_s1_05 import _devices_app
    api, body = _devices_app(tmp_path, monkeypatch, lambda *args, **kwargs: {'status': 'aborted', 'worker_exit_confirmed': True})
    return api, body, routes_training.training_job_manager


def _events(api, cursor=None, **params):
    answer = api.get('/api/job-events', params={**({'after': cursor} if cursor else {}), **params})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_a_client_catches_up_with_exactly_the_events_recorded_after_its_cursor(app):
    api, body, manager = app
    first = _events(api)
    assert (first['reset'], first['reason'], first['events']) == (True, 'initial', [])
    job_id = api.post('/api/training/start', json=body).json()['job_id']
    _finished(manager, job_id)
    caught_up = _events(api, first['cursor'])
    assert caught_up['reset'] is False and caught_up['more'] is False
    events = caught_up['events']
    assert events and {event['job_id'] for event in events} == {job_id}
    assert [event['seq'] for event in events] == sorted(event['seq'] for event in events)
    assert events[-1]['to_state'] == 'aborted' and all(event['id'] == f"{job_id}:{event['seq']}" for event in events)
    assert _events(api, caught_up['cursor'])['events'] == [], 'nothing is delivered twice'


def test_a_client_far_behind_reads_every_event_once_across_pages(app):
    api, body, manager = app
    start = _events(api)['cursor']
    for _ in range(2):
        _finished(manager, api.post('/api/training/start', json=body).json()['job_id'])
    everything = _events(api, start)['events']
    cursor, paged, pages = start, [], 0
    while True:
        page = _events(api, cursor, limit=2)
        paged += page['events']
        cursor, pages = page['cursor'], pages + 1
        if not page['more']:
            break
    assert [event['id'] for event in paged] == [event['id'] for event in everything] and pages > 1


def test_another_projects_events_are_never_read_and_its_cursor_is_not_continued(app):
    api, body, manager = app
    devices_cursor = _events(api)['cursor']
    devices_job = api.post('/api/training/start', json=body).json()['job_id']
    _finished(manager, devices_job)
    assert api.post('/api/project/create', json={'name': 'Other'}).status_code == 200
    other = _events(api, devices_cursor)
    assert (other['reset'], other['reason'], other['events']) == (True, 'cursor_expired', []), 'a cursor of another project'
    assert all(event['job_id'] != devices_job for event in _events(api, other['cursor'])['events'])


def test_a_cursor_whose_event_the_ledger_does_not_hold_is_answered_with_a_reset(app):
    api, body, manager = app
    _finished(manager, api.post('/api/training/start', json=body).json()['job_id'])
    cursor = _events(api)['cursor']
    version, stream, position, anchor = cursor.split('.')
    for broken in (f'{version}.{stream}.{position}.{"0" * 16}',   # another ledger's event at this position (a restore)
                   f'{version}.{stream}.{int(position) + 50}.{anchor}',  # beyond the ledger's newest event
                   'v1.not-this-stream.1.0', 'garbage'):
        answer = _events(api, broken)
        assert (answer['reset'], answer['reason']) == (True, 'cursor_expired'), broken
        assert answer['cursor'] == cursor, 'the fresh cursor is the newest position of this stream'


def test_events_are_a_read_for_every_project_role_in_team_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.contracts.authentication import permission_action
    assert permission_action('/api/job-events', 'GET') == 'project.read'
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    from backend.main import create_app
    app = create_app(str(tmp_path / 'shared-registry'), shared_auth_dir=str(tmp_path / 'accounts'))
    store = app.state.accounts
    admin = store.bootstrap('owner', 'long password 123')
    viewer_account = store.create_user('viewer', 'long password 456')
    client = lambda name, password: TestClient(app, headers={'Authorization': 'Bearer ' + store.login(name, password)['token']})
    owner = client('owner', 'long password 123')
    project = owner.post('/api/project/create', json={'name': 'Events', 'task': 'classification'}).json()
    store.set_membership(project['id'], viewer_account['id'], 'viewer', admin['id'])
    store.select_project(viewer_account['id'], project['id'])
    viewer_answer = client('viewer', 'long password 456').get('/api/job-events')
    assert viewer_answer.status_code == 200 and viewer_answer.json()['reset'] is True, viewer_answer.text
    owner.post('/api/project/create', json={'name': 'Elsewhere', 'task': 'classification'})
    elsewhere = owner.get('/api/job-events', params={'after': viewer_answer.json()['cursor']})
    assert elsewhere.status_code == 200 and elsewhere.json()['reason'] == 'cursor_expired', 'a cursor of another project'


def test_a_project_stream_never_delivers_another_projects_events(app):
    """Project B runs two jobs; B's stream reads their events, while project A's stream, continued from its own cursor,
    reads none of them."""
    api, body, manager = app
    project_a = api.get('/api/project/current').json()
    a_first = _events(api)
    assert api.post('/api/project/create', json={'name': 'Other'}).status_code == 200  # B is current now
    assert api.put('/api/project/update', json={'source_dataset_dir': body['dataset_path']}).status_code == 200
    b_first = _events(api)
    jobs = []
    for _ in range(2):
        started = api.post('/api/training/start', json=body)
        assert started.status_code == 200, started.text
        jobs.append(started.json()['job_id'])
        _finished(manager, jobs[-1])
    b_after = _events(api, b_first['cursor'])
    assert b_after['reset'] is False and {event['job_id'] for event in b_after['events']} == set(jobs), 'B reads its own events'
    assert api.post('/api/project/open', json={'project_dir': project_a['project_dir']}).status_code == 200
    a_next = _events(api, a_first['cursor'])
    assert a_next['reset'] is False and a_next['events'] == [], a_next['events']


def test_a_cursor_position_beyond_the_ledger_number_range_is_a_reset_not_an_error(app):
    api, body, manager = app
    cursor = _events(api)['cursor']
    version, stream, _, anchor = cursor.split('.')
    answer = _events(api, f'{version}.{stream}.{2 ** 63}.{anchor}')
    assert answer['reset'] is True and answer['reason'] == 'cursor_expired'
