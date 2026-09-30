"""Remote receipt candidates enter the same source-safe human review pipeline."""
import hashlib,json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from backend.tests.test_label_candidate_api import workspace
from backend.engine.annotation_storage import dataset_annotation_dir


def completed_label_job(workspace,monkeypatch):
    from backend.api import routes_compute,routes_training
    from backend.engine.compute_label_results import capture_label_baseline
    from backend.engine.foundation_labeling import mask_candidate
    client,_,source=workspace;client.app.include_router(routes_compute.router)
    project=client.get('/api/project/current').json()
    baseline=capture_label_baseline(project,{})
    output=Path(project['models_dir'])/'job_label';output.mkdir()
    mask=np.zeros((60,80),bool);mask[20:25,30:38]=1
    candidates=[mask_candidate(mask,.9,'Scratch','mask',{'provider':'sam2','model_sha256':'a'*64})]
    rows=[{'image_path':item['relative_path'],'image_sha256':item['image_sha256'],'candidates':candidates if item['relative_path']=='target.png' else [],'review_state':'pending'} for item in baseline['images']]
    payload={'job_id':'job_label','input_manifest_sha256':'b'*64,'results':rows,'automatically_approved':False}
    target=output/'label_results.json';target.write_text(json.dumps(payload))
    (output/'remote_artifacts.json').write_text(json.dumps({'protocol_version':1,'operation':'label','job_id':'job_label','input_manifest_sha256':'b'*64,'artifacts':[{'path':'outputs/label_results.json','sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'size':target.stat().st_size}]}))
    record=SimpleNamespace(job_id='job_label',output_dir=str(output),task='labeling',status='completed',launch_spec={'operation':'label','label_baseline':baseline,'labeling':{}},remote_profile_id='remote')
    monkeypatch.setattr(routes_training.training_job_manager,'get_job',lambda identifier:record if identifier=='job_label' else None)
    monkeypatch.setattr(routes_training.training_job_manager,'get_active_job',lambda:None)
    return client,project,source,output


def test_remote_label_import_is_idempotent_and_explicit_accept_preserves_originals(workspace,monkeypatch):
    client,project,source,output=completed_label_job(workspace,monkeypatch)
    before={p.name:p.read_bytes() for p in source.iterdir() if p.is_file()}
    generated=client.post('/api/compute/jobs/job_label/label-proposals')
    assert generated.status_code==200,generated.text
    proposals=generated.json()['proposals'];proposal=next(p for p in proposals if p['image_id']=='target')
    assert proposal['status']=='pending' and proposal['backend']=='foundation'
    studio=dataset_annotation_dir(source,Path(project['annotations_dir']),use_scope=False)
    assert not (studio/'target.json').exists()
    again=client.post('/api/compute/jobs/job_label/label-proposals')
    assert [p['id'] for p in again.json()['proposals']]==[p['id'] for p in proposals]
    accepted=client.post('/api/label-suggestions/'+proposal['id']+'/review',json={'decision':'accept','candidate_ids':[proposal['candidates'][0]['id']]})
    assert accepted.status_code==200,accepted.text
    assert (studio/'target.json').exists() and (studio/'masks/target.png').exists()
    assert {p.name:p.read_bytes() for p in source.iterdir() if p.is_file()}==before
    assert client.post('/api/compute/jobs/job_label/label-proposals').status_code==200


@pytest.mark.parametrize('change',['result_bytes','source_labels'])
def test_import_refuses_changed_receipt_or_source(workspace,monkeypatch,change):
    client,_,source,output=completed_label_job(workspace,monkeypatch)
    if change=='result_bytes':(output/'label_results.json').write_text('{}')
    else:(source/'target.json').write_text(json.dumps({'shapes':[]}))
    response=client.post('/api/compute/jobs/job_label/label-proposals')
    assert response.status_code==409,response.text
    assert not list((output.parent.parent/'label_suggestions').glob('suggestion_*.json'))
