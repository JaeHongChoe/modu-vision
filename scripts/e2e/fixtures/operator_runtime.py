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
    from backend.tests.runtime_release_fixture import real_classification_checkpoints, cohort_receipt, reviewed_graph_fixture, synthetic_service_truth,synthetic_model_report
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    client,project,source,fingerprint,models=_fixture(root/'operator-contract')
    real_classification_checkpoints(models)
    fingerprint=synthetic_service_truth(project,[source/'test/OK/ok_00.png',source/'test/NG/ng_00.png'],models=models)
    comparison='comparison_'+'e'*32
    synthetic_model_report(project,source,fingerprint,models,comparison_id=comparison)
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
    # This setup proves representative OK/NG process parity. The operator UI
    # cases exercise configuration and sockets; they do not qualify all 32
    # synthetic images or model quality. Retain the full source inventory.
    parity_images=[source/'test'/'OK'/'ok_00.png',source/'test'/'NG'/'ng_00.png']
    assert all(path.is_file() for path in parity_images), parity_images
    parity=cohort_receipt(package['package_path'],graph,{'job_candidate':models['job_candidate']},parity_images)
    graph_review=reviewed_graph_fixture(project,graph,parity_images)
    service=ManagedService(project['project_dir'])
    try:
        active=service.apply(package['package_path'],'cpu','synthetic-contract-reviewer',project)
    finally:service.stop()
    return {'project':project,'source':str(source),'images':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in images},
        'package':package,'active':active,'parity_status':parity['status'],'parity_cohort':[str(p) for p in parity_images],
        'parity_scope':'two_synthetic_heldout_OK_NG_images_not_full_quality_cohort','port':service.config['port'],
        'checkpoint_kind':'untrained_deterministic','approval_setup':'controlled_synthetic_report_not_quality_acceptance',
        'whole_flow_review':graph_review['revision_id'],'synthetic_policy_maximum_escape_rate':1.}

if __name__=='__main__':
    if sys.argv[1]=='stop':
        service=ManagedService(sys.argv[2]);service.stop();print(json.dumps(service.readback()))
    else:print(json.dumps(seed(sys.argv[1])))
