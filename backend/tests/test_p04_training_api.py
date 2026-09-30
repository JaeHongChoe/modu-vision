import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.tests.test_rotation import rotation_data


def client(tmp_path):
    from backend.main import create_app
    from backend.api.routes_rotation import router as rotation_router
    from backend.api.routes_automated_training import router as search_router
    app = create_app(project_dir=str(tmp_path / 'projects'))
    if not any(r.path == '/api/rotation/train' for r in app.routes): app.include_router(rotation_router)
    if not any(r.path == '/api/automated-training/start' for r in app.routes): app.include_router(search_router)
    return TestClient(app, headers={'X-Vision-Token': app.state.api_token})


def test_owned_ocr_manual_parent_configuration_is_reused_by_real_auto_runner(tmp_path):
    from backend.tests.test_ocr import _labeled_images
    import torch
    torch.set_num_threads(1)
    api=client(tmp_path);project=api.post('/api/project/create',json={'name':'OCR config'}).json()
    source=tmp_path/'source';rows=_labeled_images(source)
    before={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()}
    api.put('/api/project/update',json={'source_dataset_dir':str(source)})
    response=api.post('/api/ocr/prepare',json={'source_dataset_path':str(source),'samples':rows})
    assert response.status_code==200,response.text
    owned=response.json()['dataset_path']
    manual=api.post('/api/ocr/train',json={'dataset_path':owned,'epochs':1,'image_height':32,'image_width':64,'batch_size':2})
    assert manual.status_code==200,manual.text
    parent=manual.json()['job_id'];checkpoint=Path(project['models_dir'])/'ocr'/parent/'best_model.pt';original=checkpoint.read_bytes()
    discovered=api.get('/api/automated-training/parents',params={'task':'ocr','dataset_path':str(source),'family_dataset_path':owned})
    assert discovered.status_code==200 and discovered.json()['parents'][0]['job_id']==parent,discovered.text
    trained=api.post('/api/automated-training/start',json={'task':'ocr','dataset_path':str(source),'family_dataset_path':owned,
        'mode':'fast_retrain','parent_job_id':parent,'epochs_per_trial':1,'background':False})
    assert trained.status_code==200,trained.text
    result=trained.json();assert result['status']=='completed',result
    assert result['winner']['config']['image_width']==64
    assert checkpoint.read_bytes()==original
    evaluated=api.post('/api/ocr/evaluate',json={'job_id':result['winner']['trial_id'],'dataset_path':owned})
    assert evaluated.status_code==200 and evaluated.json()['evaluation_id'],evaluated.text
    assert before=={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()}


def test_orphan_search_reopens_as_interrupted_and_cancel_is_terminal(tmp_path):
    from backend.engine.automated_trials import _write
    api=client(tmp_path);project=api.post('/api/project/create',json={'name':'Interrupted trials'}).json()
    root=Path(project['models_dir'])/'automated_training'
    for index,(file,status) in enumerate((('submission.json','queued'),('search.json','running'),('search.json','stopping'))):
        identifier=f'{index+1:032x}'
        trial_id=f'{index+100:032x}';candidate=Path(project['models_dir'])/'rotation'/trial_id;candidate.mkdir(parents=True)
        _write(root/identifier/file,{'search_id':identifier,'status':status,'task':'rotation','mode':'search','owner_pid':999999,
            'source_dataset_path':'/source','dataset_path':'/owned','created_at':1,'trials':[{'trial_id':trial_id,'status':'running'}],'winner':None})
        reopened=api.get('/api/automated-training/jobs/'+identifier)
        assert reopened.status_code==200,reopened.text
        assert reopened.json()['status']=='interrupted'
        assert reopened.json()['trials'][0]['status']=='interrupted'
        assert json.loads((root/identifier/file).read_text())['status']=='interrupted'
        assert json.loads((candidate/'job_receipt.json').read_text())['status']=='interrupted'
        assert api.post('/api/automated-training/jobs/'+identifier+'/cancel').json()['status']=='interrupted'
    assert all(row['status']=='interrupted' for row in api.get('/api/automated-training/jobs').json()['jobs'])


def test_rotation_api_owned_preparation_training_evaluation_export_and_project_scope(tmp_path):
    api = client(tmp_path); project = api.post('/api/project/create', json={'name': 'Upright'}).json()
    source = tmp_path / 'original'; rows = rotation_data(source)
    api.put('/api/project/update', json={'source_dataset_dir': str(source)})
    prepared = api.post('/api/rotation/prepare', json={'source_dataset_path': str(source), 'samples': rows})
    assert prepared.status_code == 200, prepared.text
    prepared_path = Path(prepared.json()['dataset_path'])
    assert prepared_path.is_relative_to(Path(project['dataset_dir']))
    assert not (source / 'rotation.json').exists()
    trained = api.post('/api/rotation/train', json={'dataset_path': str(prepared_path), 'epochs': 1, 'image_size': 32})
    assert trained.status_code == 200, trained.text
    job = trained.json()['job_id']
    assert api.get('/api/rotation/models').json()['models'][0]['job_id'] == job
    parents=api.get('/api/rotation/warm-start-parents',params={'dataset_path':str(prepared_path),'image_size':32,'width':16})
    assert parents.status_code==200 and parents.json()['total']==1,parents.text
    warm=api.post('/api/rotation/train',json={'dataset_path':str(prepared_path),'epochs':1,'image_size':32,'warm_start_job_id':job})
    assert warm.status_code==200,warm.text
    assert warm.json()['result']['warm_start']['parent_job_id']==job
    evaluated = api.post('/api/rotation/evaluate', json={'job_id': job, 'dataset_path': str(prepared_path), 'split': 'test'})
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()['evaluation_id'] and evaluated.json()['angular_mae_deg'] >= 0
    assert evaluated.json()['binding']['family_dataset_sha256']==prepared.json()['provenance']['dataset_sha256']
    exported = api.post('/api/rotation/export', json={'job_id': job})
    assert exported.status_code == 200, exported.text
    api.post('/api/project/create', json={'name': 'Other'})
    assert api.get('/api/rotation/models').json()['models'] == []
    assert api.post('/api/rotation/predict', json={'job_id': job, 'image_path': str(source / rows[0]['image'])}).status_code == 404


def test_measured_trials_api_binds_snapshot_persists_winner_and_excludes_other_project(tmp_path, monkeypatch):
    from backend.tests.test_automated_trials import data, TinyProbe
    import backend.engine.trainer as trainer
    import backend.engine.exporter as exporter
    import torch
    monkeypatch.setattr(trainer, 'create_classification_model', lambda backbone, **kwargs: TinyProbe(backbone))
    def reconstruct(path):
        payload=torch.load(path,weights_only=True);model=TinyProbe(payload['backbone'])
        model.load_state_dict(payload['model_state_dict']);return model.eval(),payload,None
    monkeypatch.setattr(exporter,'load_checkpoint_and_reconstruct_model',reconstruct)
    api=client(tmp_path);api.post('/api/project/create',json={'name':'Trials'})
    source=tmp_path/'data';data(source);api.put('/api/project/update',json={'source_dataset_dir':str(source)})
    result=api.post('/api/automated-training/start',json={'task':'classification','dataset_path':str(source),'background':False,
        'epochs_per_trial':1,'budget':{'max_trials':2,'max_total_epochs':2,'max_seconds':60},
        'search_space':{'architectures':['dinov3_vits16','dinov3_vitb16'],'image_sizes':[32],'batch_sizes':[2]}})
    assert result.status_code==200,result.text
    saved=result.json();assert saved['status']=='completed' and saved['winner']
    assert saved['training_provenance']['dataset_version_id']
    assert api.get('/api/automated-training/jobs/'+saved['search_id']).json()['winner']['checkpoint_sha256']==saved['winner']['checkpoint_sha256']
    api.post('/api/project/create',json={'name':'Other'})
    assert api.get('/api/automated-training/jobs/'+saved['search_id']).status_code==404


def test_search_cancel_after_measured_callback_has_no_published_search_winner(tmp_path, monkeypatch):
    from backend.tests.test_automated_trials import data,TinyProbe
    from backend.engine.automated_trials import run_automated_training
    import backend.engine.trainer as trainer
    import backend.engine.exporter as exporter
    import threading,torch
    monkeypatch.setattr(trainer,'create_classification_model',lambda backbone,**kwargs:TinyProbe(backbone))
    monkeypatch.setattr(exporter,'load_checkpoint_and_reconstruct_model',lambda path:(TinyProbe('dinov3_vits16'),torch.load(path,weights_only=True),None))
    source=tmp_path/'data';data(source);event=threading.Event()
    def cancel_after_measurement(record):
        if record['trials'] and record['trials'][-1]['status']=='completed':event.set()
    result=run_automated_training(task='classification',dataset_path=source,models_dir=tmp_path/'models',mode='quick',epochs_per_trial=1,
        base_config={'image_size':32},cancel_event=event,on_progress=cancel_after_measurement)
    assert result['status']=='cancelled' and result['winner'] is None


def test_prepared_patch_search_keeps_canonical_source_and_uses_owned_pixels(tmp_path, monkeypatch):
    from PIL import Image
    import torch
    import backend.engine.automated_trials as trials
    observed = []
    def measured(context):
        observed.append(context.dataset_path)
        context.output_dir.mkdir(parents=True,exist_ok=True)
        checkpoint=context.output_dir/'best_model.pt'
        meta={'task':'patch_classification','classes':['OK','chip'],'backbone':'dinov3_vits16'}
        torch.save({**meta,'model_state_dict':{'test_boundary':torch.zeros(1)}},checkpoint)
        (context.output_dir/'model_meta.json').write_text(json.dumps(meta))
        return {'checkpoint_path':str(checkpoint),'metrics':{'val_loss':.5},'latency_ms':1.,'epochs_completed':1}
    monkeypatch.setitem(trials._RUNNERS,'patch_classification',trials.TaskRunner(measured,('dinov3_vits16',)))
    api=client(tmp_path);project=api.post('/api/project/create',json={'name':'Prepared trials'}).json()
    source=tmp_path/'source';source.mkdir()
    for i in range(3):
        path=source/f'{i}.png';Image.new('RGB',(32,32),(i,0,0)).save(path)
        path.with_suffix('.json').write_text(json.dumps({'imagePath':path.name,'imageWidth':32,'imageHeight':32,'shapes':[
            {'label':'chip','shape_type':'rectangle','points':[[0,0],[8,8]]}]}))
    api.put('/api/project/update',json={'source_dataset_dir':str(source)})
    prepared=api.post('/api/patch-classification/prepare',json={'patch_size':16,'stride':16}).json()
    reopened=api.get('/api/patch-classification/datasets')
    assert reopened.status_code==200 and reopened.json()['datasets'][0]['dataset_path']==prepared['dataset_path']
    body={'task':'patch_classification','dataset_path':str(source),'family_dataset_path':prepared['dataset_path'],
          'mode':'quick','background':False,'epochs_per_trial':1,'budget':{'max_trials':1,'max_total_epochs':1,'max_seconds':60}}
    result=api.post('/api/automated-training/start',json=body)
    assert result.status_code==200,result.text
    record=result.json();assert record['status']=='completed',record
    assert observed==[Path(prepared['dataset_path'])]
    assert record['source_dataset_path']==str(source) and record['dataset_path']==prepared['dataset_path']
    receipt=json.loads(Path(record['winner']['checkpoint_path']).with_name('job_receipt.json').read_text())
    assert receipt['source_dataset_path']==str(source) and receipt['dataset_path']==prepared['dataset_path']
    assert receipt['training_provenance']['family_task']=='patch_classification'
    reopened_job=api.get('/api/training/status',params={'job_id':record['winner']['trial_id']}).json()
    assert reopened_job['status']=='completed' and reopened_job['task']=='patch_classification',reopened_job
    body['family_dataset_path']=str(source)
    assert api.post('/api/automated-training/start',json=body).status_code==422
