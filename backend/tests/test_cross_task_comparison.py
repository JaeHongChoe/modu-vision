import hashlib
import json
from pathlib import Path
import torch
from PIL import Image
from backend.tests.test_model_comparisons import _client,_checkpoint
from backend.engine.dataset_fingerprint import fingerprint_dataset


def test_classification_and_segmentation_share_exact_cohort_and_reopen_report(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);client=_client(tmp_path);source=tmp_path/'source'
    for i,category in enumerate(('OK','NG')):
        directory=source/'test'/category;directory.mkdir(parents=True);Image.new('RGB',(32,32),(i*150,30,20)).save(directory/f'{category}.png')
    project=client.post('/api/project/create',json={'name':'cross','task':'classification'}).json()
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    models=Path(project['models_dir']);fingerprint=fingerprint_dataset(source)
    _checkpoint(models/'job_cls',source,fingerprint,0)
    from backend.engine.segmentation import build_segmentation_model
    model=build_segmentation_model(model_name='unet',num_classes=2,preset='fast',pretrained=False)
    with torch.no_grad():
        for p in model.parameters():p.zero_()
        last=[m for m in model.modules() if isinstance(m,torch.nn.Conv2d)][-1];last.bias[1]=10
    directory=models/'job_seg';directory.mkdir()
    torch.save({'model_state_dict':model.state_dict(),'model_name':'unet','classes':['background','defect']},directory/'best_model.pt')
    (directory/'model_meta.json').write_text(json.dumps({'task':'segmentation','model_name':'unet','classes':['background','defect'],'image_size':[32,32]}))
    (directory/'job_receipt.json').write_text(json.dumps({'status':'completed','task':'segmentation','source_dataset_path':str(source),'dataset_fingerprint':fingerprint,'dataset_path':str(source)}))
    response=client.post('/api/evaluation/model-comparisons',json={'source_dataset_path':str(source),'task':'classification','incumbent_job_id':'job_cls','candidate_job_id':'job_seg','incumbent_task':'classification','candidate_task':'segmentation','full_test':True})
    assert response.status_code==200,response.text
    report=response.json();assert report['candidate_task']=='segmentation'
    assert report['summary']['disagreements']==2
    assert report['summary']['comparable_images']==2
    assert report['labelset_id']=='default'
    for row in report['images']:assert row['image_sha256']==hashlib.sha256(Path(row['file_path']).read_bytes()).hexdigest()
    reopened=client.get('/api/evaluation/model-comparisons/'+report['comparison_id'],params={'source_dataset_path':str(source),'task':'classification'})
    assert reopened.json()==report
    catalog=client.get('/api/evaluation/model-comparisons/models/all',params={'source_dataset_path':str(source)})
    assert catalog.status_code==200,catalog.text
    assert {r['task'] for r in catalog.json()['models']}=={'classification','segmentation'}
    queued=client.post('/api/evaluation/model-comparisons/jobs',json={'source_dataset_path':str(source),'task':'classification','incumbent_job_id':'job_cls','candidate_job_id':'job_seg','incumbent_task':'classification','candidate_task':'segmentation','full_test':True})
    assert queued.status_code==202,queued.text
    import time
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        state=client.get('/api/evaluation/model-comparisons/jobs/'+queued.json()['job_id'],params={'source_dataset_path':str(source),'task':'classification'}).json()
        if state['status'] not in ('queued','running'):break
        time.sleep(.02)
    assert state['status']=='completed',state
