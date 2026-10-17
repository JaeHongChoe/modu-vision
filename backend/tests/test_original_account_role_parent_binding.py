"""Unknown wrappers/bodies must not inherit the canonical account role path.

Deliberate provider/code/closure mutations below are explicit negative inputs.
All business reads still use the real account store and original HTTP routes;
no observer, caller-frame checker, response or qualification result is mocked.
"""
import asyncio
from functools import wraps
from types import FunctionType

import httpx
import pytest


def _delegating_role_body(self, user_id, project_id):
    # This code is an explicit foreign-body canary, installed into the original
    # function only in its named negative case. Its globals are then the real
    # shared_accounts module, where that case publishes the original copy.
    return _ROLE_BODY_CANARY(self, user_id, project_id)


def _delegating_project_for(self, user_id, project_id=None):
    return _PROJECT_FOR_BODY_CANARY(self, user_id, project_id)


@pytest.mark.parametrize('mutation', (
    'role-wrapper-before-bind',
    'role-wrapper-after-bind',
    'role-body-code-before-bind',
    'role-body-copy-and-closure-before-bind',
    'role-body-code-after-bind',
    'role-wrapped-reference-after-bind',
    'role-closure-after-bind',
    'unknown-project-for-intermediate-before-bind',
))
def test_unknown_account_role_parent_or_body_refuses_qualification(mutation, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'runtime'))
    monkeypatch.setenv('MODU_FLOW_TEMPLATE_DIR', str(tmp_path/'templates'))
    from backend.engine import shared_accounts
    from backend.engine import original_owned_UI_ancillary_request as ancillary
    from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects

    store_type = shared_accounts.AccountStore
    canonical = ancillary._RESOURCES['global_project_role']
    original_body = canonical.__wrapped__
    original_body_code = original_body.__code__
    body_copy = FunctionType(original_body_code, original_body.__globals__)

    @wraps(canonical)
    def foreign_role(store, user_id, project_id):
        return canonical(store, user_id, project_id)

    if mutation == 'role-wrapper-before-bind':
        monkeypatch.setattr(store_type, 'project_role', foreign_role)
    elif mutation == 'role-body-code-before-bind':
        monkeypatch.setattr(shared_accounts, '_ROLE_BODY_CANARY', body_copy, raising=False)
        monkeypatch.setattr(original_body, '__code__', _delegating_role_body.__code__)
    elif mutation == 'role-body-copy-and-closure-before-bind':
        monkeypatch.setattr(canonical, '__wrapped__', body_copy)
        monkeypatch.setattr(canonical.__closure__[0], 'cell_contents', body_copy)
    elif mutation == 'unknown-project-for-intermediate-before-bind':
        original_project_for = store_type.project_for
        original_copy = FunctionType(original_project_for.__code__, original_project_for.__globals__)
        monkeypatch.setattr(shared_accounts, '_PROJECT_FOR_BODY_CANARY', original_copy, raising=False)
        monkeypatch.setattr(original_project_for, '__code__', _delegating_project_for.__code__)

    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    actor, projects = _owned_projects(app, observer, 'Owned role first', 'Owned role second')
    owner = admission._account_requests

    def mutate_after_original_login():
        if mutation == 'role-wrapper-after-bind':
            monkeypatch.setattr(store_type, 'project_role', foreign_role)
        elif mutation == 'role-body-code-after-bind':
            monkeypatch.setattr(shared_accounts, '_ROLE_BODY_CANARY', body_copy, raising=False)
            monkeypatch.setattr(original_body, '__code__', _delegating_role_body.__code__)
        elif mutation == 'role-wrapped-reference-after-bind':
            monkeypatch.setattr(canonical, '__wrapped__', body_copy)
        elif mutation == 'role-closure-after-bind':
            assert canonical.__code__.co_freevars == ('function',)
            monkeypatch.setattr(canonical.__closure__[0], 'cell_contents', body_copy)

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            login = await client.post('/api/accounts/login', json={
                'username':'fixture-admin', 'password':'fixture-password-123'})
            assert login.status_code == 200
            mutate_after_original_login()
            response = await client.get('/api/accounts/me', headers={
                'authorization':'Bearer '+login.json()['token']})
            assert response.status_code == 200 and response.is_closed and response.is_stream_consumed
            result = response.json()
            assert result['user']['id'] == actor['id']
            assert {row['id'] for row in result['projects']} == {row['id'] for row in projects}
            assert result['selected_project_id'] == projects[-1]['id']

    asyncio.run(original_sequence())
    admission.close()
    drain = admission.drain(.03)
    assert drain['status'] == 'refused'
    assert 'uncovered_request_protocol' in drain['unsupported']
    assert not any(getattr(attempt, 'name', None) == 'me' and attempt.settled for attempt in owner.records)
    snapshot = observer.snapshot()
    assert len(snapshot['records']) == 2
    assert all(row['normal_return'] and row['completed'] and row['released'] for row in snapshot['records'])
    assert not snapshot['all_settled']
    assert all(snapshot[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))
    assert 'uncovered_request_protocol' in admission.snapshot()['unsupported']
