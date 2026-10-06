"""Fresh central release commands must use current saved approval evidence."""
import json
from pathlib import Path

import pytest

from backend.api import routes_export, routes_model_deployments as deployments
from backend.engine import image_truth
from backend.engine.annotation_storage import (
    set_request_annotation_root, reset_request_annotation_root,
    set_request_project_root, reset_request_project_root,
)
from backend.engine.comparison_truth import verify_truth
from backend.engine.fleet import FleetRegistry
from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import FlowchartEngine, get_single_segmentation_flowchart
from backend.engine.managed_service import ManagedService
from backend.tests.runtime_release_fixture import real_classification_checkpoints, cohort_receipt, synthetic_service_truth, reviewed_graph_fixture, synthetic_model_report
from backend.tests.test_model_deployments import _fixture, _report, _approve, _params


def _bound_context(tmp_path,monkeypatch,*,reviewed=False):
    monkeypatch.chdir(tmp_path)
    client, project, source, fingerprint, models = _fixture(tmp_path)
    real_classification_checkpoints(models)
    if reviewed:synthetic_service_truth(project,[source/'test/OK/ok_00.png',source/'test/NG/ng_00.png'],models=models)
    for checkpoint in models.values():
        (checkpoint.parent / 'dataset').mkdir(exist_ok=True)
    annotation_token = set_request_annotation_root(Path(project['annotations_dir']))
    project_token = set_request_project_root(Path(project['project_dir']))
    from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
    split_token=set_request_split_root(Path(project['dataset_dir'])/'splits')
    # Deterministic predictions isolate evidence freshness from model quality.
    with monkeypatch.context() as patch:
        patch.setattr(FlowchartEngine, 'execute', lambda self, **kw: {'final_verdict': Path(kw['image_path']).parent.name})
        response = client.post('/api/evaluation/model-comparisons', json={
            'source_dataset_path': str(source), 'task': 'classification',
            'incumbent_job_id': 'job_base', 'candidate_job_id': 'job_candidate', 'full_test': True})
    assert response.status_code == 200, response.text
    report = response.json()
    assert len(report['truth_binding']['images']) == 16
    try:
        yield client, project, source, models, report
    finally:
        reset_request_split_root(split_token)
        reset_request_annotation_root(annotation_token)
        reset_request_project_root(project_token)


@pytest.fixture
def bound_context(tmp_path,monkeypatch):
    yield from _bound_context(tmp_path,monkeypatch)


@pytest.fixture
def reviewed_context(tmp_path,monkeypatch):
    yield from _bound_context(tmp_path,monkeypatch,reviewed=True)


def _change_truth(project, source):
    image = source / 'test/OK/ok_00.png'
    current = image_truth.read_truth(project, str(image), task='classification', classes=['OK', 'NG'])
    return image_truth.declare_truth(project, str(image), task='classification', classes=['OK', 'NG'],
        verdict='UNKNOWN', reviewer='fixture reviewer', expected_revision=current['truth_revision'],
        expected_image_revision=current['image_revision'])


def _package(tmp_path, source, models, revision, name='release'):
    job = revision['job_id']
    graph = get_single_segmentation_flowchart(job_id=job)
    next(node for node in graph.nodes if node.data.node_type == 'inspection').data.task = 'classification'
    result = build_flow_package(pipeline=graph, checkpoints={job: models[job]}, output_base_dir=tmp_path/'packages',
        package_name=name, approved_revisions={job: {key: revision[key] for key in ('revision_id', 'job_id', 'task', 'checkpoint_sha256')}})
    package = Path(result['package_path'])
    cohort_receipt(package, graph, {job: models[job]}, [source/'test/OK/ok_00.png', source/'test/NG/ng_00.png'])
    return package


def _review_package(project,package):
    from backend.engine.flow_package_runtime import verify_flow_package
    graph,_=verify_flow_package(Path(package))
    parity=json.loads((Path(package)/'parity_receipt.json').read_text())
    return reviewed_graph_fixture(project,graph,[row['image_path'] for row in parity['images']])


def test_changed_reviewed_truth_blocks_approval(bound_context):
    client, project, source, models, report = bound_context
    _change_truth(project, source)
    with pytest.raises(ValueError, match='truth changed'):
        verify_truth(project, source, report['truth_binding'])
    assert deployments._fingerprint(source) == report['dataset_fingerprint']
    assessment = client.get('/api/model-deployments/assess/'+report['comparison_id'], params=_params(source))
    assert assessment.json()['status'] == 'needs_review'
    assert any('truth' in reason.lower() for reason in assessment.json()['reasons'])
    assert _approve(client, source, report['comparison_id']).status_code == 409


@pytest.mark.parametrize('boundary', ['verify', 'export', 'stage'])
def test_truth_changed_after_approval_blocks_new_release(bound_context, tmp_path, boundary):
    client, project, source, models, report = bound_context
    response = _approve(client, source, report['comparison_id'])
    assert response.status_code == 200, response.text
    revision = response.json()['revision']
    package = _package(tmp_path, source, models, revision) if boundary == 'stage' else None
    _change_truth(project, source)
    if boundary == 'verify':
        assert deployments.verified_approval_revision(project, revision['revision_id']) is None
    elif boundary == 'stage':
        with pytest.raises(ValueError, match='stale|truth'):
            ManagedService(project['project_dir']).stage(package, project, device='cpu')
        registry = FleetRegistry(project['project_dir'])
        target = registry.save_target(name='Rejected deployment', url='https://example.invalid', token='fixture-token-no-network')
        response = client.post('/api/fleet/targets/'+target['target_id']+'/deploy', json={
            'package_path':str(package), 'device':'cpu', 'reviewer':'fixture'})
        assert response.status_code == 409, response.text
        failure = registry.failures(target['target_id'])[0]
        assert failure['action'] == 'apply'
        assert 'stale' in failure['reason']
    else:
        graph = get_single_segmentation_flowchart(job_id='job_candidate')
        next(node for node in graph.nodes if node.data.node_type == 'inspection').data.task = 'classification'
        saved = client.post('/api/flowchart/pipeline', params={'recipe_task':'classification', 'source_dataset_path':str(source)}, json=graph.model_dump())
        assert saved.status_code == 200, saved.text
        exported = client.post('/api/export/flow', json={'source_dataset_path':str(source), 'recipe_task':'classification',
            'approval_revision_ids':{'job_candidate':revision['revision_id']}, 'package_name':'stale'})
        assert exported.status_code == 409, exported.text


class _Transport:
    def __init__(self): self.calls = []; self.current = None; self.response = None
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def post(self, url, **kwargs):
        self.calls.append(('POST', url))
        if url == '/agent/v1/apply': self.current = {'status':'ready', **kwargs['json']}
        self.response = {'status':'staged','manifest_sha256':kwargs['headers']['X-Manifest-SHA256']} if url == '/agent/v1/releases' else self.current
        return self
    def get(self, url): self.calls.append(('GET', url)); self.response=self.current; return self
    def raise_for_status(self): pass
    def json(self): return dict(self.response)


def test_central_rollback_rechecks_revocation_and_preserves_valid_history(reviewed_context, tmp_path, monkeypatch):
    client, project, source, models, first_report = reviewed_context
    service = ManagedService(project['project_dir'])
    registry = FleetRegistry(project['project_dir'])
    target = registry.save_target(name='Transport fixture', url='https://example.invalid', token='fixture-token-no-network')
    transport = _Transport()
    monkeypatch.setattr(FleetRegistry, 'client', lambda self, identifier: transport)
    revisions = []; releases = []; deployed = []
    for index, (baseline, candidate) in enumerate([('job_base','job_candidate'), ('job_candidate','job_third')]):
        report = first_report if index == 0 else synthetic_model_report(project, source, deployments._fingerprint(source), models,
            incumbent=baseline, candidate=candidate, comparison_id='comparison_'+'b'*32)
        approved = _approve(client, source, report['comparison_id'])
        assert approved.status_code == 200, approved.text
        revision = approved.json()['revision']
        package = _package(tmp_path, source, models, revision, 'release'+str(index))
        _review_package(project,package)
        release = service.stage(package, project, device='cpu')
        revisions.append(revision); releases.append(release)
        deployed.append(registry.apply(target['target_id'], release, reviewer='fixture'))
    valid = client.post('/api/fleet/targets/'+target['target_id']+'/rollback', json={
        'deployment_id':deployed[0]['deployment_id'], 'reviewer':'fixture'})
    assert valid.status_code == 200, valid.text  # Historical approval need not be active.
    revoked = client.post('/api/model-deployments/rollback', json={**_params(source),
        'target_revision_id':revisions[0]['revision_id'], 'reviewer':'fixture', 'reason':'Revoke second candidate after review'})
    assert revoked.status_code == 200, revoked.text
    calls_before = list(transport.calls)
    rejected = client.post('/api/fleet/targets/'+target['target_id']+'/rollback', json={
        'deployment_id':deployed[1]['deployment_id'], 'reviewer':'fixture'})
    assert rejected.status_code == 409, rejected.text
    assert transport.calls == calls_before
    rejected_attempt = registry.failures(target['target_id'])[0]
    assert rejected_attempt['action'] == 'rollback'
    assert rejected_attempt['manifest_sha256'] == releases[1]['manifest_sha256']
    assert 'revoked' in rejected_attempt['reason']
    with pytest.raises(ValueError, match='revoked|stale'):
        registry.apply(target['target_id'], releases[1], reviewer='fixture')
    assert transport.calls == calls_before
    # An already sealed offline package remains cryptographically verifiable.
    from backend.engine.inspection_service import _verify_release_policy
    from backend.engine.flow_package_runtime import verify_flow_package
    release = releases[1]
    package = Path(release['package_path'])
    _verify_release_policy(package, verify_flow_package(package)[1], Path(release['release_policy']), device='cpu')


@pytest.mark.parametrize('mutation', ['source', 'label', 'split', 'class_role'])
def test_changed_comparison_inputs_block_approve_and_revision(bound_context, mutation):
    client, project, source, models, report = bound_context
    approved = _approve(client, source, report['comparison_id'])
    assert approved.status_code == 200, approved.text
    if mutation == 'source':
        from PIL import Image
        Image.new('RGB', (8, 8), 'red').save(source/'test/OK/ok_00.png')
    elif mutation == 'label':
        (source/'test/OK/ok_00.txt').write_text('changed label')
    elif mutation == 'split':
        from backend.api import routes_dataset
        path = routes_dataset._split_manifest_file(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'test': []}))
    else:
        path = models['job_candidate'].parent/'model_meta.json'
        path.write_text(json.dumps({'task':'classification', 'classes':['OK','NG'],
                                   'class_semantics':{'version':1, 'roles':{'OK':'defect','NG':'normal'}}}))
    assert deployments.verified_approval_revision(project, approved.json()['revision']['revision_id']) is None
    assert _approve(client, source, report['comparison_id']).status_code == 409


def test_approval_rechecks_truth_changed_after_assessment(bound_context, monkeypatch):
    client, project, source, models, report = bound_context
    assess = deployments._assess
    changed = False
    def assess_then_edit(*args, **kwargs):
        nonlocal changed
        result = assess(*args, **kwargs)
        if not changed:
            changed = True
            _change_truth(project, source)
        return result
    monkeypatch.setattr(deployments, '_assess', assess_then_edit)
    assert _approve(client, source, report['comparison_id']).status_code == 409
    assert client.get('/api/model-deployments/active', params=_params(source)).json()['active'] is None


def test_stage_rechecks_truth_changed_while_copying(reviewed_context, tmp_path, monkeypatch):
    client, project, source, models, report = reviewed_context
    approved = _approve(client, source, report['comparison_id'])
    assert approved.status_code == 200, approved.text
    package = _package(tmp_path, source, models, approved.json()['revision'])
    _review_package(project,package)
    import shutil
    copy = shutil.copyfile
    changed = False
    def copy_then_edit(*args, **kwargs):
        nonlocal changed
        result = copy(*args, **kwargs)
        if not changed:
            changed = True
            _change_truth(project, source)
        return result
    monkeypatch.setattr(shutil, 'copyfile', copy_then_edit)
    service = ManagedService(project['project_dir'])
    with pytest.raises(ValueError, match='stale|truth'):
        service.stage(package, project, device='cpu')
    assert not list(service.releases.glob('*.policy.json'))


def test_export_rechecks_truth_changed_during_real_cpu_parity(bound_context, monkeypatch):
    client, project, source, models, report = bound_context
    approved = _approve(client, source, report['comparison_id'])
    assert approved.status_code == 200, approved.text
    graph = get_single_segmentation_flowchart(job_id='job_candidate')
    next(node for node in graph.nodes if node.data.node_type == 'inspection').data.task = 'classification'
    saved = client.post('/api/flowchart/pipeline', params={'recipe_task':'classification', 'source_dataset_path':str(source)}, json=graph.model_dump())
    assert saved.status_code == 200, saved.text
    parity = routes_export.flow_package_engine.verify_flow_parity_cohort
    def parity_then_edit(**kwargs):
        result = parity(**kwargs)
        assert result['status'] == 'passed', result
        _change_truth(project, source)
        return result
    monkeypatch.setattr(routes_export.flow_package_engine, 'verify_flow_parity_cohort', parity_then_edit)
    exported = client.post('/api/export/flow', json={'source_dataset_path':str(source), 'recipe_task':'classification',
        'approval_revision_ids':{'job_candidate':approved.json()['revision']['revision_id']}, 'package_name':'race',
        'parity_images':[{'path':str(source/'test/OK/ok_00.png')}, {'path':str(source/'test/NG/ng_00.png')}], 'parity_device':'cpu'})
    assert exported.status_code == 409, exported.text
    assert 'stale' in exported.json()['detail']


def test_legacy_comparison_cannot_ignore_new_reviewed_truth(bound_context):
    client, project, source, models, report = bound_context
    legacy = _report(project, source, deployments._fingerprint(source), models, comparison_id='comparison_'+'c'*32)
    approved = _approve(client, source, legacy['comparison_id'])
    assert approved.status_code == 200, approved.text
    _change_truth(project, source)
    assert deployments.verified_approval_revision(project, approved.json()['revision']['revision_id']) is None
    assert _approve(client, source, legacy['comparison_id']).status_code == 409


def test_stale_project_context_cannot_authorize_old_source(bound_context, tmp_path):
    client, project, source, models, report = bound_context
    approved = _approve(client, source, report['comparison_id'])
    assert approved.status_code == 200, approved.text
    package = _package(tmp_path, source, models, approved.json()['revision'])
    replacement = tmp_path/'replacement'; replacement.mkdir()
    changed = client.put('/api/project/update', json={'source_dataset_dir':str(replacement)})
    assert changed.status_code == 200, changed.text
    with pytest.raises(ValueError, match='project|source'):
        ManagedService(project['project_dir']).stage(package, project, device='cpu')


def test_malformed_comparison_rows_fail_as_review_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client, project, source, fingerprint, models = _fixture(tmp_path)
    report = _report(project, source, fingerprint, models)
    report['images'][0] = 'corrupt image evidence'
    (Path(project['reports_dir'])/'model_comparisons'/f"{report['comparison_id']}.json").write_text(json.dumps(report))
    response = _approve(client, source, report['comparison_id'])
    assert response.status_code == 409, response.text


def test_project_switch_while_waiting_for_authority_blocks_approval(bound_context, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from backend.engine import runtime_process_control
    client, project, source, models, report = bound_context
    replacement = tmp_path/'replacement'; replacement.mkdir()
    lock = runtime_process_control.runtime_state_lock
    changed = False
    @contextmanager
    def switch_before_lock(root):
        nonlocal changed
        if Path(root) == Path(project['project_dir']) and not changed:
            changed = True
            manifest = Path(root)/'project.json'
            current = json.loads(manifest.read_text())
            current['source_dataset_dir'] = str(replacement)
            manifest.write_text(json.dumps(current))
        with lock(root):
            yield
    monkeypatch.setattr(runtime_process_control, 'runtime_state_lock', switch_before_lock)
    response = _approve(client, source, report['comparison_id'])
    assert response.status_code == 409, response.text
    with deployments._store(project) as connection:
        assert connection.execute('SELECT COUNT(*) FROM revisions').fetchone()[0] == 0


@pytest.mark.parametrize('mutation', ['source', 'labelset'])
def test_project_mutation_is_fenced_during_approval(bound_context, tmp_path, monkeypatch, mutation):
    from concurrent.futures import ThreadPoolExecutor
    client, project, source, models, report = bound_context
    if mutation == 'source':
        replacement = tmp_path/'replacement'; replacement.mkdir()
        def mutate(): return client.put('/api/project/update', json={'source_dataset_dir':str(replacement)})
    else:
        created = client.post('/api/project/labelsets', json={'name':'Other labels'})
        assert created.status_code == 200, created.text
        def mutate(): return client.put('/api/project/labelsets/'+created.json()['id']+'/activate')
    assess = deployments._assess
    attempted = False
    def assess_with_concurrent_writer(*args, **kwargs):
        nonlocal attempted
        result = assess(*args, **kwargs)
        if not attempted:
            attempted = True
            with ThreadPoolExecutor(max_workers=1) as executor:
                changed = executor.submit(mutate).result(timeout=5)
            assert changed.status_code == 409, changed.text
        return result
    monkeypatch.setattr(deployments, '_assess', assess_with_concurrent_writer)
    approved = _approve(client, source, report['comparison_id'])
    assert approved.status_code == 200, approved.text
    assert mutate().status_code == 200


def test_legacy_comparison_rejects_incompatible_current_class_roles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client, project, source, fingerprint, models = _fixture(tmp_path)
    report = _report(project, source, fingerprint, models)
    models['job_candidate'].with_name('model_meta.json').write_text(json.dumps({
        'task':'classification', 'classes':['OK','NG'],
        'class_semantics':{'version':1, 'roles':{'OK':'defect','NG':'normal'}}}))
    assert _approve(client, source, report['comparison_id']).status_code == 409


def test_malformed_nested_truth_binding_is_review_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client, project, source, fingerprint, models = _fixture(tmp_path)
    report = _report(project, source, fingerprint, models)
    for row in report['images']: row['truth_sha256'] = 'fixture'
    report['truth_binding'] = {'images':[{'file_path':row['file_path'], 'truth_sha256':'fixture'} for row in report['images']],
        'scope':{'class_semantics':{'basis':[]}}, 'metadata_sha256':{}}
    (Path(project['reports_dir'])/'model_comparisons'/f"{report['comparison_id']}.json").write_text(json.dumps(report))
    assert _approve(client, source, report['comparison_id']).status_code == 409
