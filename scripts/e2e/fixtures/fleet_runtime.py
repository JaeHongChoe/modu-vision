"""Two approved synthetic CPU releases for owned actual agent QA; no quality claim."""
from pathlib import Path
import hashlib,json,os,re,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from backend.tests.test_model_deployments import _fixture,_report,_approve
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.engine.flow_package import build_flow_package
from backend.tests.runtime_release_fixture import cohort_receipt,real_classification_checkpoints
from backend.engine.managed_service import ManagedService
root=Path(sys.argv[1]).resolve();run=Path(os.environ.get('MV_E2E_RUN_DIR','missing')).resolve()
if root.parent!=run/'ws' or not re.fullmatch(r'\d+-[a-f0-9]{8}-r\d+',root.name) or not (root/'projects').is_dir():raise ValueError('Only an owned E2E workspace can host field fixtures')
client,project,source,fingerprint,models=_fixture(root/'operator-contract');real_classification_checkpoints(models)
images=sorted(source.rglob('*.png'));packages=[];releases=[]
for i,(baseline,candidate) in enumerate([('job_base','job_candidate'),('job_candidate','job_third')]):
    report=_report(project,source,fingerprint,models,incumbent=baseline,candidate=candidate,comparison_id='comparison_'+str(i+1)*32)
    approved=_approve(client,source,report['comparison_id']);assert approved.status_code==200,approved.text
    revision=approved.json()['revision'];graph=get_single_segmentation_flowchart(candidate)
    for node in graph.nodes:
        if node.data.node_type=='inspection':node.data.task='classification'
    built=build_flow_package(pipeline=graph,checkpoints={candidate:models[candidate]},output_base_dir=Path(project['project_dir'])/'exports',package_name='owned_fleet_cpu_'+str(i),approved_revisions={candidate:{k:revision[k] for k in ('revision_id','job_id','task','checkpoint_sha256')}})
    cohort_receipt(built['package_path'],graph,{candidate:models[candidate]},[source/'test'/'OK'/'ok_00.png',source/'test'/'NG'/'ng_00.png'])
    releases.append(ManagedService(project['project_dir']).stage(built['package_path'],project,device='cpu'));packages.append(built)
print(json.dumps({'project':project,'source':str(source),'images':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in images},'package':packages[0],'first_release':releases[0],'candidate_package':packages[1],'candidate_release':releases[1],'models':{n:{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for n,p in models.items()},'checkpoint_kind':'untrained_deterministic','approval_setup':'controlled_synthetic_report_not_quality_acceptance','cohort_count_per_release':2}))
