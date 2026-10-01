"""Emergency intent adds authenticated authority and immutable audit, never bypasses eligibility."""
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.engine.fleet import FleetRegistry
from backend.engine.managed_service import ManagedService
from backend.main import create_app
from backend.tests.test_service_release_eligibility import bound_context, _package, _Transport
from backend.tests.test_model_deployments import _fixture, _report, _approve, _params
from backend.api import routes_model_deployments as deployments


def _shared_sessions(tmp_path, project):
    app = create_app(project_dir=str(tmp_path/'shared-projects'), shared_auth_dir=str(tmp_path/'auth'))
    accounts = app.state.accounts
    admin = accounts.bootstrap('admin', 'fixture password 123')
    accounts.register_project(project['id'], project['project_dir'], admin['id'])
    sessions = {}
    identities = {'admin':admin}
    for role in ('owner', 'reviewer', 'viewer'):
        account = accounts.create_user(role, 'fixture password 123')
        accounts.set_membership(project['id'], account['id'], role, admin['id'])
        accounts.select_project(account['id'], project['id'])
        identities[role] = account
    for name in identities:
        client = TestClient(app)
        login = client.post('/api/accounts/login', json={'username':name, 'password':'fixture password 123'})
        assert login.status_code == 200, login.text
        client.headers['Authorization'] = 'Bearer '+login.json()['token']
        sessions[name] = client
    return sessions, identities


@pytest.fixture
def emergency_scope(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    desktop, project, source, _, _ = _fixture(tmp_path)
    sessions, identities = _shared_sessions(tmp_path, project)
    registry = FleetRegistry(project['project_dir'])
    target = registry.save_target(name='No-network fixture', url='https://example.invalid', token='fixture-token-12345678')
    monkeypatch.setattr(FleetRegistry, 'client', lambda *args: pytest.fail('Denied request must never contact agent'))
    return desktop, project, sessions, identities, registry, target


@pytest.mark.parametrize('role', ['viewer', 'reviewer'])
def test_non_owner_emergency_denial_is_authenticated_and_audited(emergency_scope, role):
    _, _, sessions, identities, registry, target = emergency_scope
    response = sessions[role].post('/api/fleet/targets/'+target['target_id']+'/emergency-rollback',
        json={'deployment_id':'unselected', 'reason':'Production incident investigation'})
    assert response.status_code == 403, response.text
    events = registry.emergency_events(target['target_id'])
    assert [event['event'] for event in events] == ['attempted', 'denied']
    assert all(event['actor_id'] == identities[role]['id'] for event in events)
    assert all(event['actor_name'] == role for event in events)
    assert events[-1]['detail'] == 'owner_or_administrator_required'
    assert events[0]['reason'] == 'Production incident investigation'


@pytest.mark.parametrize('reason', ['', '  ', '\n\t'])
def test_blank_emergency_reason_is_rejected_and_audited(emergency_scope, reason):
    _, _, sessions, _, registry, target = emergency_scope
    response = sessions['owner'].post('/api/fleet/targets/'+target['target_id']+'/emergency-rollback',
        json={'deployment_id':'unselected', 'reason':reason})
    assert response.status_code == 422, response.text
    events = registry.emergency_events(target['target_id'])
    assert events[-1]['event'] == 'denied'
    assert events[-1]['detail'] == 'emergency_reason_required'


def test_desktop_capability_is_explicit_and_caller_cannot_supply_actor(emergency_scope):
    desktop, _, sessions, _, registry, target = emergency_scope
    local = desktop.get('/api/fleet/capabilities')
    assert local.status_code == 200, local.text
    assert local.json()['authentication'] == 'desktop_process_capability'
    assert local.json()['actor_role'] == 'local_owner'
    assert local.json()['can_emergency_rollback'] is True
    assert sessions['viewer'].get('/api/fleet/capabilities').json()['can_emergency_rollback'] is False
    assert sessions['admin'].get('/api/fleet/capabilities').json()['can_emergency_rollback'] is True
    malformed = sessions['owner'].post('/api/fleet/targets/'+target['target_id']+'/emergency-rollback',
        json={'deployment_id':'unselected', 'reason':'Incident', 'reviewer':'forged-admin'})
    assert malformed.status_code == 422
    no_token = TestClient(desktop.app).get('/api/fleet/capabilities')
    assert no_token.status_code == 401


def test_emergency_owner_admin_local_success_and_revocation_preserve_live_gates(bound_context, tmp_path, monkeypatch):
    desktop, project, source, models, first_report = bound_context
    first = _approve(desktop, source, first_report['comparison_id'])
    assert first.status_code == 200, first.text
    second_report = _report(project, source, deployments._fingerprint(source), models,
        incumbent='job_candidate', candidate='job_third', comparison_id='comparison_'+'b'*32)
    second = _approve(desktop, source, second_report['comparison_id'])
    assert second.status_code == 200, second.text
    package = _package(tmp_path, source, models, second.json()['revision'])
    release = ManagedService(project['project_dir']).stage(package, project, device='cpu')
    registry = FleetRegistry(project['project_dir'])
    target = registry.save_target(name='Mock agent', url='https://example.invalid', token='fixture-token-12345678')
    transport = _Transport()
    monkeypatch.setattr(FleetRegistry, 'client', lambda *args: transport)
    deployed = registry.apply(target['target_id'], release, reviewer='fixture')
    sessions, identities = _shared_sessions(tmp_path, project)
    url = '/api/fleet/targets/'+target['target_id']+'/emergency-rollback'
    payload = {'deployment_id':deployed['deployment_id'], 'reason':'  Production incident reviewed  '}
    for name, client in [('owner', sessions['owner']), ('admin', sessions['admin']), ('local_owner', desktop)]:
        response = client.post(url, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()['emergency']['reason'] == 'Production incident reviewed'
        events = registry.emergency_events(target['target_id'])
        assert events[-1]['event'] == 'committed'
        assert events[-1]['result_deployment_id'] == response.json()['deployment_id']
        assert events[-1]['actor_name'] == (name if name != 'local_owner' else 'Desktop process capability')
        assert response.json()['reviewer'] == events[-1]['actor_name']
    original_events = registry.emergency_events(target['target_id'])
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with registry.connect() as conn: conn.execute("UPDATE emergency_rollback_events SET reason='rewrite'")
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with registry.connect() as conn: conn.execute('DELETE FROM emergency_rollback_events')
    assert registry.emergency_events(target['target_id']) == original_events
    revoked = desktop.post('/api/model-deployments/rollback', json={**_params(source),
        'target_revision_id':first.json()['revision']['revision_id'], 'reviewer':'fixture', 'reason':'Revoke candidate after incident review'})
    assert revoked.status_code == 200, revoked.text
    calls_before = list(transport.calls)
    blocked = sessions['owner'].post(url, json=payload)
    assert blocked.status_code == 409, blocked.text
    assert transport.calls == calls_before
    events = registry.emergency_events(target['target_id'])
    assert events[-1]['event'] == 'rejected'
    assert 'revoked' in events[-1]['detail']


def test_emergency_admission_does_not_relax_other_fleet_routes(emergency_scope):
    _, _, sessions, _, registry, target = emergency_scope
    viewer = sessions['viewer']
    root = '/api/fleet/targets/'+target['target_id']
    for path, body in [(root+'/rollback', {'deployment_id':'unused', 'reviewer':'forged'}),
                       (root+'/deploy', {'package_path':'unused', 'reviewer':'forged'}),
                       (root+'/emergency-rollback/extra', {'deployment_id':'unused', 'reason':'Incident'})]:
        assert viewer.post(path, json=body).status_code == 403
    assert registry.emergency_events(target['target_id']) == []
    forbidden_project = viewer.post(root+'/emergency-rollback', headers={'X-Vision-Project':'unassigned-project'},
        json={'deployment_id':'unused', 'reason':'Incident'})
    assert forbidden_project.status_code == 403
    assert registry.emergency_events(target['target_id']) == []


@pytest.mark.parametrize('extra', [{}, {'reason':None}, {'reason':123}, {'reason':[]}])
def test_missing_or_malformed_reason_never_dispatches(emergency_scope, extra):
    _, _, sessions, _, registry, target = emergency_scope
    response = sessions['owner'].post('/api/fleet/targets/'+target['target_id']+'/emergency-rollback',
        json={'deployment_id':'unused', **extra})
    assert response.status_code == 422, response.text
    assert registry.emergency_events(target['target_id']) == []  # Invalid schema never becomes an action.
