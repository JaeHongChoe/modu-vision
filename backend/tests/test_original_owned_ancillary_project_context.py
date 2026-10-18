"""Real authoritative-context reads; no installed-native emitter attribution.

The existing owned fixture only supplies original application/admission objects.
No route, AccountStore method, DB connection, auth decision or carrier is replaced.
The stale legacy header is a disclosed request operand: shared authorization's
existing contract gives the same request's explicit context precedence over it.
"""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest


@pytest.mark.parametrize('case', ('matching-first', 'matching-second', 'stale-legacy'))
def test_original_ancillary_uses_authoritative_project_context(case, tmp_path, monkeypatch):
    from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects

    monkeypatch.chdir(tmp_path)
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    actor, (first, second) = _owned_projects(app, observer, 'Context first', 'Context second')
    protected = {Path(project['project_dir'])/'project.json':
                 (Path(project['project_dir'])/'project.json').read_bytes()
                 for project in (first, second)}

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            login = await client.post('/api/accounts/login', json={
                'username':'fixture-admin', 'password':'fixture-password-123'})
            assert login.status_code == 200 and login.json()['user']['id'] == actor['id']
            bearer = {'authorization':'Bearer '+login.json()['token']}
            contexts = {}
            for project in (first, second):
                current = await client.get('/api/project/current', headers={
                    **bearer, 'x-vision-project':project['id']})
                assert current.status_code == 200 and current.json()['id'] == project['id']
                contexts[project['id']] = current.headers['x-vision-context']
                context = json.loads(contexts[project['id']])
                assert context == {'workspace_id':app.state.context_registry.workspace_id,
                    'project_id':project['id'], 'actor_id':actor['id'], 'mode':'team'}
            assert not observer.snapshot()['uncertain']
            owning = first if case == 'matching-first' else second
            legacy = first if case == 'stale-legacy' else owning
            response = await client.get('/api/compute/selection', headers={
                **bearer, 'x-vision-project':legacy['id'],
                'x-vision-context':contexts[owning['id']]})
            assert response.status_code == 200 and response.json() == {'compute_profile_id':None}
            assert response.is_closed and response.is_stream_consumed
            assert json.loads(response.headers['x-vision-context']) == json.loads(contexts[owning['id']])
            attempt = admission._UI_ancillary_requests.records[-1]
            assert attempt.activity.ingress == {'type':'http', 'method':'GET', 'path':'/api/compute/selection'}
            assert dict(attempt.scope['headers'])[b'x-vision-project'] == legacy['id'].encode()
            assert attempt.scope['state']['scoped_project']['id'] == owning['id']
            row = observer._records[attempt.activity.token]
            assert row['carrier'] is attempt.activity and row['response_status'] == 200
            assert row['completed'] and row['normal_return'] and row['released']

    asyncio.run(original_sequence())
    assert all(path.read_bytes() == raw for path, raw in protected.items())
    reasons = list(observer._failures)
    print('original causal failure reasons:', reasons)
    print('original causal reason SHA256:', [hashlib.sha256(reason.encode()).hexdigest() for reason in reasons])
    # Binding a valid explicit B context to obsolete legacy A must not poison a
    # fully original successful B read or leave its owned DB resources pending.
    assert not observer.snapshot()['uncertain'], reasons
    admission.close()
    assert admission.drain(.03) == {'status':'managed_scopes_drained', 'active_scopes':0, 'unsupported':[]}
    assert all(observer.snapshot()[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))


@pytest.mark.parametrize('case', ('foreign-actor', 'foreign-workspace', 'memberless-project', 'invalid-context'))
def test_original_ancillary_invalid_authority_never_qualifies(case, tmp_path, monkeypatch):
    from backend.tests.test_original_owned_account_request_lifetime import _login_stack, _owned_projects

    monkeypatch.chdir(tmp_path)
    app, admission, observer = _login_stack(tmp_path, monkeypatch)
    actor, (first, second) = _owned_projects(app, observer, 'Refusal first', 'Refusal second')
    login_name = 'fixture-admin'
    if case == 'memberless-project':
        member = app.state.accounts.create_user('one-project-user', 'fixture-password-123')
        app.state.accounts.set_membership(first['id'], member['id'], 'viewer', actor['id'])
        assert app.state.accounts.project_role(member['id'], second['id']) is None
        login_name = member['username']
    protected = {Path(project['project_dir'])/'project.json':
                 (Path(project['project_dir'])/'project.json').read_bytes()
                 for project in (first, second)}

    async def original_sequence():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://owned.invalid') as client:
            login = await client.post('/api/accounts/login', json={
                'username':login_name, 'password':'fixture-password-123'})
            assert login.status_code == 200
            headers = {'authorization':'Bearer '+login.json()['token'], 'x-vision-project':first['id']}
            current = await client.get('/api/project/current', headers=headers)
            assert current.status_code == 200 and current.json()['id'] == first['id']
            context = json.loads(current.headers['x-vision-context'])
            if case == 'foreign-actor':
                context['actor_id'] = 'foreign-actor'
            elif case == 'foreign-workspace':
                context['workspace_id'] = 'foreign-workspace'
            elif case == 'memberless-project':
                context['project_id'] = second['id']
            headers['x-vision-context'] = '{' if case == 'invalid-context' else json.dumps(context)
            response = await client.get('/api/compute/selection', headers=headers)
            expected = {'detail':'Invalid explicit project context'} if case == 'invalid-context' else (
                {'detail':'Project permission required'} if case == 'memberless-project' else
                {'detail':'Explicit context does not match the authenticated account workspace'})
            assert response.status_code == (400 if case == 'invalid-context' else 403)
            assert response.json() == expected and response.is_closed and response.is_stream_consumed

    asyncio.run(original_sequence())
    assert all(path.read_bytes() == raw for path, raw in protected.items())
    attempts = admission._UI_ancillary_requests.records
    assert not any(attempt.settled for attempt in attempts)
    admission.close()
    refused = admission.drain(.03)
    assert refused['status'] == 'refused' and 'uncovered_request_protocol' in refused['unsupported']
    assert all(observer.snapshot()[name] is False for name in (
        'whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))
