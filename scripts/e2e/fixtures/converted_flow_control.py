"""Owned real CPU/IR fixture; initialized weights and reviews are synthetic controls."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.tests.test_model_deployments import _fixture, _approve
from backend.tests.runtime_release_fixture import (real_classification_checkpoints,
    synthetic_service_truth, synthetic_model_report, cohort_receipt, reviewed_graph_fixture)
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.engine.flow_package import build_flow_package
from backend.engine.product_delivery import record_package

root=Path(sys.argv[1]).resolve()
run=Path(os.environ.get('MV_E2E_RUN_DIR', 'missing')).resolve()
if (root.parent!=run/'ws' or not re.fullmatch(r'\d+-[a-f0-9]{8}-r\d+', root.name)
        or not (root/'projects').is_dir()):
    raise ValueError('Only an owned E2E workspace can host this conversion control')
client,project,source,_,models=_fixture(root/'converted-contract')
real_classification_checkpoints(models)
heldout=[source/'test/OK/ok_00.png',source/'test/NG/ng_00.png']
fingerprint=synthetic_service_truth(project,heldout,models=models)
report=synthetic_model_report(project,source,fingerprint,models)
approved=_approve(client,source,report['comparison_id'])
assert approved.status_code==200,approved.text
revision=approved.json()['revision']
graph=get_single_segmentation_flowchart('job_candidate')
for node in graph.nodes:
    if node.data.node_type=='inspection':node.data.task='classification'
package=build_flow_package(pipeline=graph,checkpoints={'job_candidate':models['job_candidate']},
    output_base_dir=Path(project['project_dir'])/'exports/flows',package_name='original_cpu_control',
    approved_revisions={'job_candidate':{k:revision[k] for k in ('revision_id','job_id','task','checkpoint_sha256')}})
parity=cohort_receipt(package['package_path'],graph,{'job_candidate':models['job_candidate']},heldout)
review=reviewed_graph_fixture(project,graph,heldout)
package_id=record_package(project,package['package_path'],parity={'status':parity['status']})
def hashes(directory):
    return {p.relative_to(directory).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file()}
print(json.dumps({'project':project,'source':str(source),'package':package,'package_id':package_id,
    'whole_flow_revision':review['revision_id'],'heldout':[str(p) for p in heldout],
    'images':hashes(source),'original_package_files':hashes(Path(package['package_path'])),
    'model_files':hashes(Path(project['models_dir'])),
    'synthetic_control':True,'maximum_escape_rate':1.,'quality_accepted':False,'device_accepted':False}))
