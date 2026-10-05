"""Owned operator UI fixture: untrained CPU weights and controlled review records.

The actual managed service executes the package. Approval/report setup is a
synthetic contract fixture and never represents product or human acceptance.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.engine.managed_service import ManagedService


def seed(root):
    root=Path(root).resolve()
    run=Path(os.environ.get('MV_E2E_RUN_DIR','missing')).resolve()
    if root.parent!=run/'ws' or not re.fullmatch(r'\d+-[a-f0-9]{8}-r\d+',root.name) or not (root/'projects').is_dir():
        raise ValueError('Only an owned E2E workspace can host the operator fixture')
    from backend.tests.test_model_deployments import _fixture, _report, _approve
    from backend.tests.runtime_release_fixture import real_classification_checkpoints, cohort_receipt
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    client,project,source,fingerprint,models=_fixture(root/'operator-contract')
    real_classification_checkpoints(models)
    comparison='comparison_'+'e'*32
    _report(project,source,fingerprint,models,comparison_id=comparison)
    approval=_approve(client,source,comparison)
    assert approval.status_code==200,approval.text
    revision=approval.json()['revision']
    graph=get_single_segmentation_flowchart('job_candidate')
    for node in graph.nodes:
        if node.data.node_type=='inspection':node.data.task='classification'
    package=build_flow_package(pipeline=graph,checkpoints={'job_candidate':models['job_candidate']},
        output_base_dir=Path(project['project_dir'])/'exports',package_name='operator_cpu_fixture',
        approved_revisions={'job_candidate':{k:revision[k] for k in ('revision_id','job_id','task','checkpoint_sha256')}})
    images=sorted(source.rglob('*.png'))
    parity=cohort_receipt(package['package_path'],graph,{'job_candidate':models['job_candidate']},images)
    service=ManagedService(project['project_dir'])
    try:
        active=service.apply(package['package_path'],'cpu','synthetic-contract-reviewer',project)
    finally:service.stop()
    return {'project':project,'source':str(source),'images':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in images},
        'package':package,'active':active,'parity_status':parity['status'],'port':service.config['port'],
        'checkpoint_kind':'untrained_deterministic','approval_setup':'controlled_synthetic_report_not_quality_acceptance'}

if __name__=='__main__':
    if sys.argv[1]=='stop':
        service=ManagedService(sys.argv[2]);service.stop();print(json.dumps(service.readback()))
    else:print(json.dumps(seed(sys.argv[1])))
