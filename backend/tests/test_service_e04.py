"""E04: every inspection-rule change is recorded with the authenticated actor, the rule difference from the version it
replaced, the reason and the runtime release then running. A layout-only move is recorded as such and keeps whole-flow
evidence; a save based on a version that is no longer active, or one whose record cannot be written, records nothing.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.engine.config_audit import ConfigAuditStore, actor_from_request
from backend.engine.flow_provenance import semantic_delta, semantic_sha256
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.tests.test_whole_flow_evaluation import workspace  # noqa: F401 (the whole-flow evaluation fixture)


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.api import routes_flowchart
    from backend.main import create_app
    monkeypatch.setattr(routes_flowchart, 'DEFAULT_PIPELINE_FILE', tmp_path / 'legacy' / 'pipeline.json')
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    source = tmp_path / 'images'
    source.mkdir()
    app = create_app(project_dir=str(tmp_path / 'workspaces'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    created = client.post('/api/project/create', json={'name': 'Line 3', 'project_dir': str(tmp_path / 'project'), 'task': 'segmentation'})
    assert created.status_code == 200
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    return client, f'/api/flowchart/pipeline?recipe_task=segmentation&source_dataset_path={source}', tmp_path / 'project'


def test_a_rule_save_records_the_session_actor_the_rule_difference_and_the_reason(tmp_path, monkeypatch):
    client, path, project_dir = _client(tmp_path, monkeypatch)
    flow = get_single_segmentation_flowchart()
    first = client.post(path + '&change_reason=first+flow', json=flow.model_dump())
    assert first.status_code == 200 and first.json()['layout_only'] is False
    flow.nodes[1].data.threshold = 0.7
    # A client naming an actor (a query parameter, a header) cannot change who is recorded.
    second = client.post(path + f"&change_reason=tighter+threshold&actor=mallory&expected_version_id={first.json()['version_id']}",
                         json=flow.model_dump(), headers={'X-Vision-Actor': 'mallory'})
    assert second.status_code == 200, second.text
    listed = client.get('/api/flowchart/changes').json()
    assert listed['integrity'] == {'intact': True, 'rows': 2}
    latest, earliest = listed['changes']
    assert earliest['parent_revision'] is None and earliest['reason'] == 'first flow'
    assert any(change['kind'] == 'node_added' for change in earliest['semantic_delta']['changes'])
    assert latest['actor'] == {'kind': 'local', 'id': 'this-computer', 'name': 'this computer'}
    assert (latest['action'], latest['parent_revision'], latest['next_revision']) == ('save', first.json()['version_id'], second.json()['version_id'])
    assert latest['semantic_delta']['changes'] == [{'kind': 'node_changed', 'node_id': 'node_inspect', 'field': 'threshold', 'before': 0.5, 'after': 0.7}]
    assert latest['reason'] == 'tighter threshold' and latest['subject']['recipe_task'] == 'segmentation'
    assert latest['observed_runtime_release'] is None, 'no runtime release is applied in this project'
    # Activating the first version again is a rule change too.
    activated = client.put(f"/api/flowchart/pipelines/{first.json()['version_id']}/activate?source_dataset_path={path.split('source_dataset_path=')[1]}&change_reason=roll+back")
    assert activated.status_code == 200, activated.text
    back = client.get('/api/flowchart/changes').json()['changes'][0]
    assert back['action'] == 'activate' and back['semantic_delta']['changes'][0]['after'] == 0.5 and back['reason'] == 'roll back'


def test_a_team_account_is_the_recorded_actor():
    team = SimpleNamespace(state=SimpleNamespace(account_user={'id': 'u-7', 'username': 'reviewer.kim'}))
    assert actor_from_request(team) == {'kind': 'account', 'id': 'u-7', 'name': 'reviewer.kim'}
    assert actor_from_request(None)['kind'] == 'local'


def test_a_stale_or_failed_save_records_nothing_and_keeps_the_active_flow(tmp_path, monkeypatch):
    client, path, project_dir = _client(tmp_path, monkeypatch)
    flow = get_single_segmentation_flowchart()
    first = client.post(path, json=flow.model_dump()).json()['version_id']
    flow.nodes[1].data.threshold = 0.6
    other = client.post(path + f'&expected_version_id={first}', json=flow.model_dump()).json()['version_id']
    # An editor that opened the first version saves after another save landed: refused, nothing recorded.
    flow.nodes[1].data.threshold = 0.9
    stale = client.post(path + f'&expected_version_id={first}', json=flow.model_dump())
    assert stale.status_code == 409 and 'reopen' in stale.json()['detail']
    versions = sorted(p.stem for p in (project_dir / 'flowcharts' / 'versions').glob('*.json'))
    assert len(versions) == 2 and len(client.get('/api/flowchart/changes').json()['changes']) == 2
    # The record cannot be written: the save is undone, and neither a version nor a record is left.
    def refuse(self, **kwargs):
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(ConfigAuditStore, 'append', refuse)
    failed = client.post(path + f'&expected_version_id={other}', json=flow.model_dump())
    assert failed.status_code == 500
    assert sorted(p.stem for p in (project_dir / 'flowcharts' / 'versions').glob('*.json')) == versions
    assert json.loads((project_dir / 'flowcharts' / 'active.json').read_text())['version_id'] == other
    monkeypatch.undo()
    assert len(ConfigAuditStore(project_dir).list()) == 2


def test_a_layout_move_is_recorded_as_layout_only(tmp_path, monkeypatch):
    client, path, project_dir = _client(tmp_path, monkeypatch)
    flow = get_single_segmentation_flowchart()
    first = client.post(path, json=flow.model_dump()).json()['version_id']
    flow.nodes[1].position = {'x': 999, 'y': 12}
    flow.nodes[1].data.label = 'Renamed model'
    moved = client.post(path + f'&expected_version_id={first}', json=flow.model_dump())
    assert moved.json()['layout_only'] is True
    change = client.get('/api/flowchart/changes').json()['changes'][0]
    assert change['layout_only'] is True and change['semantic_delta']['changes'] == []
    assert change['semantic_delta']['semantic_sha256_before'] == change['semantic_delta']['semantic_sha256_after']


def test_the_record_is_append_only_and_a_change_made_outside_the_app_is_detected(tmp_path):
    store = ConfigAuditStore(tmp_path)
    flow = get_single_segmentation_flowchart()
    for reason in ('one', 'two', 'three'):
        store.append(actor={'kind': 'local', 'id': 'this-computer'}, subject={'kind': 'flow'}, action='save', parent_revision=None,
                     next_revision='a' * 32, semantic_delta=semantic_delta(None, flow), reason=reason)
    with closing(sqlite3.connect(store.path)) as db:
        for statement in ("UPDATE configuration_changes SET reason='edited'", 'DELETE FROM configuration_changes'):
            with pytest.raises(sqlite3.DatabaseError, match='append-only'):
                db.execute(statement)
        db.execute('DROP TRIGGER configuration_changes_no_update')
        db.execute("UPDATE configuration_changes SET reason='edited' WHERE reason='two'")
        db.commit()
    assert store.verify()['intact'] is False
    with pytest.raises(ValueError):
        store.append(actor={}, subject={}, action='delete', parent_revision=None, next_revision='b', semantic_delta={}, reason=None)


def test_layout_only_versions_keep_whole_flow_evidence_and_rule_changes_do_not(workspace, monkeypatch):
    from backend.engine import flow_evaluation, workflow_impact
    from backend.tests.test_whole_flow_evaluation import declare, model_provider, real_engine
    import backend.engine.image_truth as truth
    project, graph, version, checkpoint = workspace
    # The model as training records it, so its checkpoint counts as current.
    (checkpoint.parent / 'model_meta.json').write_text(json.dumps({'task': 'segmentation', 'checkpoint_sha256': workflow_impact._hash(checkpoint),
                                                                   'source_dataset_path': project['source_dataset_dir']}))
    declare(truth, project, 'defect', 'NG', classes=['crack'])
    cohort = flow_evaluation.freeze_cohort(project, version, model_provider=model_provider(checkpoint))
    result = flow_evaluation.evaluate_flow(project, version, cohort['cohort_id'], engine=real_engine(monkeypatch), model_provider=model_provider(checkpoint))
    assert result['status'] == 'completed'
    folder = Path(project['project_dir']) / 'flowcharts' / 'versions'
    moved = graph.model_copy(deep=True)
    moved.nodes[1].position = {'x': 640, 'y': 40}
    changed = graph.model_copy(deep=True)
    changed.nodes[1].data.threshold = 0.8
    for version_id, pipeline in (('2' * 32, moved), ('3' * 32, changed)):
        (folder / f'{version_id}.json').write_text(json.dumps({'version_id': version_id, 'recipe_task': 'segmentation',
            'source_dataset_path': project['source_dataset_dir'], 'pipeline': pipeline.model_dump()}))
    assert semantic_sha256(moved) == semantic_sha256(graph) != semantic_sha256(changed)
    [run] = flow_evaluation.list_evidence(project, 'runs')
    assert run['semantic_sha256'] == semantic_sha256(graph)
    flows = {flow['version_id']: flow for flow in workflow_impact.analyze(project)['flows']}
    assert flows['1' * 32]['state'] == 'current', flows['1' * 32]
    assert flows['2' * 32]['state'] == 'current', 'the moved copy keeps the evaluated version\'s evidence'
    assert flows['3' * 32]['state'] == 'unverified', 'a threshold change needs its own evaluation'


def test_an_editor_saves_against_the_version_it_showed_and_sees_the_rule_difference_first(tmp_path, monkeypatch):
    client, path, project_dir = _client(tmp_path, monkeypatch)
    source = path.split('source_dataset_path=')[1]
    assert client.get('/api/flowchart/pipeline/active-version').json() == {'version_id': None}
    flow = get_single_segmentation_flowchart()
    # "none": the editor started with no active flow; a second editor that also started then is refused.
    first = client.post(path + '&expected_version_id=none', json=flow.model_dump()).json()['version_id']
    assert client.post(path + '&expected_version_id=none', json=flow.model_dump()).status_code == 409
    record = client.get(f'/api/flowchart/pipeline/active/record?source_dataset_path={source}').json()
    assert record['version_id'] == first and record['pipeline']['id'] == flow.id
    flow.nodes[1].data.threshold = 0.65
    preview = client.post(f'/api/flowchart/pipeline/diff?expected_version_id={first}', json=flow.model_dump()).json()
    assert preview['parent_revision'] == first and preview['stale'] is False and preview['layout_only'] is False
    assert preview['semantic_delta']['changes'] == [{'kind': 'node_changed', 'node_id': 'node_inspect', 'field': 'threshold', 'before': 0.5, 'after': 0.65}]
    assert len(client.get('/api/flowchart/changes').json()['changes']) == 1, 'a preview records nothing'
    second = client.post(path + f'&expected_version_id={first}&change_reason=tighter', json=flow.model_dump()).json()['version_id']
    assert client.post('/api/flowchart/pipeline/diff?expected_version_id=' + first, json=flow.model_dump()).json()['stale'] is True
    # Activating a version shown before another save landed is refused too, and records nothing.
    stale = client.put(f'/api/flowchart/pipelines/{first}/activate?source_dataset_path={source}&expected_version_id={first}')
    assert stale.status_code == 409 and len(client.get('/api/flowchart/changes').json()['changes']) == 2
    assert client.put(f'/api/flowchart/pipelines/{first}/activate?source_dataset_path={source}&expected_version_id={second}&change_reason=back').status_code == 200
    assert client.get('/api/flowchart/changes').json()['active_version_id'] == first
    for bad in ('NONE', 'x' * 32, '1' * 31):
        assert client.post(path + f'&expected_version_id={bad}', json=flow.model_dump()).status_code == 422


def test_a_restored_draft_keeps_the_version_it_started_from(tmp_path, monkeypatch):
    client, path, project_dir = _client(tmp_path, monkeypatch)
    flow = get_single_segmentation_flowchart()
    first = client.post(path, json=flow.model_dump()).json()['version_id']
    project = client.get('/api/project/current').json()
    context = {'project_id': project['id'], 'source_dataset_path': str(Path(project['source_dataset_dir']).resolve()), 'labelset_id': 'default'}
    flow.nodes[1].data.threshold = 0.9
    assert client.put('/api/flowchart/draft', json={'pipeline': flow.model_dump(), 'context': context, 'base_version_id': first}).status_code == 200
    assert client.get('/api/flowchart/draft').json()['base_version_id'] == first
    assert client.put('/api/flowchart/draft', json={'pipeline': flow.model_dump(), 'context': context, 'base_version_id': '../x'}).status_code == 422


def test_the_change_list_shows_the_running_release_until_a_new_one_is_acknowledged(tmp_path, monkeypatch):
    from backend.engine.runtime_deployment import DeploymentLedger
    client, path, project_dir = _client(tmp_path, monkeypatch)
    assert client.get('/api/flowchart/changes').json()['runtime'] == {'active': None, 'pending': None}
    ledger = DeploymentLedger(project_dir / 'runtime_service')
    release = {'manifest_sha256': 'a' * 64, 'device': 'cpu'}
    ledger.apply(release, lambda value: {'status': 'ready', **value}, reviewer='qa')
    runtime = client.get('/api/flowchart/changes').json()['runtime']
    assert runtime['active']['manifest_sha256'] == 'a' * 64 and runtime['active']['acknowledged'] is True and runtime['pending'] is None
    # A new release being applied is shown as pending; the acknowledged one is still what runs.
    with closing(sqlite3.connect(ledger.path)) as db:
        db.execute("INSERT INTO update_operations VALUES('op1', ?, NULL, 'applying', NULL, NULL, 'qa', 1, 1)", (json.dumps({'manifest_sha256': 'b' * 64}),))
        db.commit()
    runtime = client.get('/api/flowchart/changes').json()['runtime']
    assert runtime['active']['manifest_sha256'] == 'a' * 64
    assert runtime['pending'] == {'operation_id': 'op1', 'status': 'applying', 'manifest_sha256': 'b' * 64}
    flow = get_single_segmentation_flowchart()
    client.post(path + '&change_reason=new+rules', json=flow.model_dump())
    [change] = client.get('/api/flowchart/changes').json()['changes']
    assert change['observed_runtime_release']['manifest_sha256'] == 'a' * 64, 'the release that ran when the rule was saved'
