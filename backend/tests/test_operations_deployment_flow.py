"""Saved flow/version and executable package boundaries of an operations apply."""
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import pytest
import torch
from backend.tests.test_model_deployments import _fixture,_report,_approve
from backend.engine.classification.model import create_classification_model
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.engine.model_operations import configure_program,_deploy_candidate,project_scope
from backend.api.routes_flowchart import save_pipeline,get_active_pipeline
from backend.engine.managed_service import ManagedService


def fixture(tmp_path):
    client,project,source,fingerprint,models=_fixture(tmp_path)
    for identifier,checkpoint in models.items():
        model=create_classification_model('resnet18',2,pretrained=False)
        with torch.no_grad():
            for parameter in model.parameters():parameter.zero_()
            model.fc.bias[0]=10
        meta={'task':'classification','backbone':'resnet18','classes':['OK','NG'],'image_size':[32,32]}
        torch.save({**meta,'model_state_dict':model.state_dict()},checkpoint)
        checkpoint.with_name('model_meta.json').write_text(json.dumps(meta))
    request=SimpleNamespace(state=SimpleNamespace(scoped_project=project,account_user=None))
    pipeline=get_single_segmentation_flowchart('job_base')
    next(n for n in pipeline.nodes if n.data.node_type=='inspection').data.task='classification'
    saved=save_pipeline(pipeline,recipe_task='classification',source_dataset_path=str(source),request=request)
    policy=configure_program(project,{'task':'classification','parent_job_id':'job_base','reviewer':'qa','auto_retrain':True,
        'auto_approve':True,'auto_deploy':True,'approval_policy_authorized':True,'pipeline_version_id':saved['version_id']})
    report=_report(project,source,fingerprint,models)
    approval=_approve(client,source,report['comparison_id']);assert approval.status_code==200,approval.text
    return project,source,policy,approval.json(),request,saved


def test_automatic_model_approval_cannot_apply_an_unreviewed_candidate_graph(tmp_path):
    project,source,policy,approval,request,saved=fixture(tmp_path)
    # The chosen version is independent of the workspace's current recipe tab.
    project['task']='segmentation'
    active=Path(project['project_dir'])/'flowcharts'/'active.json';before=active.read_bytes()
    with project_scope(project),pytest.raises(ValueError,match='complete.*graph|whole.flow|Whole.flow'):
        _deploy_candidate(project,policy,'job_candidate',approval,threading.Event())
    assert active.read_bytes()==before
    reopened=get_active_pipeline(str(source),request)
    assert next(n for n in reopened.nodes if n.data.node_type=='inspection').data.model_job_id=='job_base'
    service=ManagedService(project['project_dir'])
    assert service.ledger.active() is None
    assert service.readback()['status']=='stopped'


def test_failed_service_application_restores_prior_graph_pointer_and_recipe(tmp_path,monkeypatch):
    project,source,policy,approval,request,saved=fixture(tmp_path)
    active=Path(project['project_dir'])/'flowcharts'/'active.json';before=active.read_bytes()
    monkeypatch.setattr(ManagedService,'apply',lambda *args:(_ for _ in ()).throw(ValueError('Controlled application failure')))
    with project_scope(project),pytest.raises(ValueError,match='Controlled application failure'):
        _deploy_candidate(project,policy,'job_candidate',approval,threading.Event())
    assert active.read_bytes()==before
    assert next(n for n in get_active_pipeline(str(source),request).nodes if n.data.node_type=='inspection').data.model_job_id=='job_base'
