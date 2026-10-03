"""Headless folder/JSON execution proves real fit, persistence and UI isolation."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

ROOT=Path(__file__).resolve().parents[2]

def capture_receipt(run):
    """Optional durable acceptance evidence; ordinary pytest keeps its temp scope."""
    requested=os.environ.get('P08_CAPTURE_EVIDENCE')
    if not requested:return
    path=Path(requested)
    report=json.loads(path.read_text()) if path.is_file() else {'evidence_scope':'generated_functional_CPU_all_families_not_industrial_quality_approval','families':[]}
    metadata=json.loads(Path(run['artifacts']['metadata']['path']).read_text())
    row={'task':run['task'],'status':run['status'],'device':run['device'],'run_id':run['run_id'],'winner':run['winner'],
        'artifacts':run['artifacts'],'pretrained':metadata.get('pretrained'),'pretrained_sha256':metadata.get('pretrained_sha256'),
        'input_size':metadata.get('image_size'),'classes':metadata.get('classes')}
    report['families']=[existing for existing in report['families'] if existing['task']!=run['task']]+[row]
    report['families'].sort(key=lambda item:item['task']);path.write_text(json.dumps(report,indent=2,allow_nan=False))

def fixture(tmp_path):
    source=tmp_path/'native';source.mkdir()
    rows=[]
    for index,split in enumerate(('train','val','test','train')):
        pixels=np.random.default_rng(index).integers(0,255,(32,48,3),dtype=np.uint8)
        image=f'{index}.png';Image.fromarray(pixels).save(source/image)
        rows.append({'image':image,'split':split,'correction_deg':index*10.,'text':'A',
            'label':'scratch','bbox':[5,5,25,25],
            'box':{'cx':24,'cy':16,'width':12,'height':8,'angle_deg':0},
            'annotations':[{'type':'bbox','label':'scratch','bbox':[5,5,17,17]}]})
    labels=tmp_path/'labels.json';labels.write_text(json.dumps({'samples':rows}))
    return source,labels,rows

def cli(*args):
    result=subprocess.run([sys.executable,'-m','backend.training_cli',*map(str,args)],cwd=ROOT,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,(result.stdout,result.stderr)
    return [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]

def test_real_rotation_cli_outputs_reusable_config_evaluation_and_predictions(tmp_path):
    source,labels,_=fixture(tmp_path);output=tmp_path/'output'
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
    prepared=cli('prepare','--task','rotation','--source',source,'--labels',labels,'--output',output)[-1]
    assert prepared['source_dataset_path']==str(source)
    events=cli('train','--output',output,'--mode','quick','--epochs','1','--config-json',json.dumps({'width':8,'image_size':32,'batch_size':1}))
    run=events[-1];assert run['status']=='completed',run
    assert any(row.get('event')=='progress' and row.get('trials') for row in events)
    manifest=run['artifacts'];assert Path(manifest['model']['path']).is_file()
    assert manifest['model']['sha256']==hashlib.sha256(Path(manifest['model']['path']).read_bytes()).hexdigest()
    assert json.loads(Path(manifest['configuration']['path']).read_text())['config']['width']==8
    evaluation=cli('evaluate','--output',output,'--run-id',run['run_id'])[-1]
    assert evaluation['evaluation']['sample_count']==1
    predictions=cli('predict','--output',output,'--run-id',run['run_id'],'--image','2.png')[-1]
    assert predictions['predictions'][0]['source_sha256']==hashes['2.png']
    assert predictions['predictions'][0]['prediction']['source_size']==[48,32]
    reopened=cli('status','--output',output,'--run-id',run['run_id'])[-1]
    assert reopened['status']=='completed'
    child=cli('train','--config-path',manifest['configuration']['path'],'--mode','fast_retrain','--parent',run['winner']['trial_id'])[-1]
    assert child['status']=='completed',child
    assert child['search']['configuration_parent']['parent_job_id']==run['winner']['trial_id']
    assert hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
    capture_receipt(run)

def test_actual_background_cli_owner_and_repeat_result_publication(tmp_path):
    import time
    source,labels,_=fixture(tmp_path);output=tmp_path/'output'
    cli('prepare','--task','rotation','--source',source,'--labels',labels,'--output',output)
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app=create_app(project_dir=str(tmp_path/'desktop'));api=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    opened=api.post('/api/project/open',json={'project_dir':str(output)})
    assert opened.status_code==200,opened.text
    assert opened.json()['source_dataset_dir']==str(source)
    assert len(api.get('/api/rotation/datasets').json()['datasets'])==1
    queued=cli('train','--output',output,'--background','--epochs','1','--config-json',json.dumps({'width':8,'image_size':32,'batch_size':1}))[-1]
    assert queued['owner_pid']==queued['process_pid'] and queued['owner_pid']!=os.getpid()
    from backend.engine import training_engine as engine
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        run=engine.read_run(output,queued['run_id'])
        if run['status'] not in engine.ACTIVE:break
        time.sleep(.05)
    assert run['status']=='completed',run
    assert run['owner_pid']==queued['process_pid']
    assert '"event": "progress"' in (output/'runs'/run['run_id']/'progress.jsonl').read_text()
    first=engine.predict(output,run['run_id'],images=['2.png']);prior=engine.read_run(output,run['run_id'])['artifacts']['predictions']
    second=engine.predict(output,run['run_id'],images=['1.png']);latest=engine.read_run(output,run['run_id'])['artifacts']['predictions']
    assert first['predictions'][0]['source_relative_path']=='2.png' and second['predictions'][0]['source_relative_path']=='1.png'
    assert prior['path']!=latest['path'] and hashlib.sha256(Path(prior['path']).read_bytes()).hexdigest()==prior['sha256']
    engine.evaluate(output,run['run_id']);first_evaluation=engine.read_run(output,run['run_id'])['artifacts']['evaluation']
    engine.evaluate(output,run['run_id']);readback=engine.read_run(output,run['run_id'])
    assert readback['status']=='completed' and readback['artifacts']['evaluation']['path']!=first_evaluation['path']
    assert hashlib.sha256(Path(first_evaluation['path']).read_bytes()).hexdigest()==first_evaluation['sha256']
    original_metadata=Path(run['winner']['checkpoint_path']).with_name('model_meta.json')
    original_metadata.write_text('{}')
    assert engine.predict(output,run['run_id'],images=['0.png'])['predictions']
    image_output=engine.read_run(output,run['run_id'])['artifacts']['predictions']
    generated=json.loads(Path(image_output['path']).read_text())['output_files'][0]
    Path(generated['path']).write_bytes(b'tampered delivered pixels')
    with pytest.raises(ValueError,match='artifact changed'):engine.read_run(output,run['run_id'])

def test_every_family_has_actual_prepare_contract_and_cli_help(tmp_path):
    from backend.engine.training_engine import capabilities,prepare
    source,labels,rows=fixture(tmp_path)
    tasks={'classification','detection','segmentation','anomaly','patch_classification','rotation','ocr','rotated_detection','enhancement','defect_gan'}
    assert set(capabilities()['tasks'])==tasks
    for task in tasks:
        selected=[dict(row) for row in rows]
        if task=='classification':
            selected += [{**rows[0],'image':'normal.png','label':'OK','split':'train'}]
            Image.fromarray(np.ones((32,48,3),np.uint8)*20).save(source/'normal.png')
        if task=='anomaly':
            for row in selected:
                row['label']='OK' if row['split']=='train' else 'scratch'
                if row['label']=='OK':row['annotations']=[]
        options={'patch_size':32,'stride':16} if task=='patch_classification' else {}
        value=prepare(task=task,source_dataset_path=str(source),output_dir=str(tmp_path/task),labels={'samples':selected},prepare_options=options)
        assert value['task']==task and value['source_dataset_path']==str(source)
        assert Path(value['dataset_path']).is_dir()
        if task=='classification':(source/'normal.png').unlink()
    help_=subprocess.run([sys.executable,'-m','backend.training_cli','train','--help'],cwd=ROOT,capture_output=True,text=True)
    assert help_.returncode==0 and '--config-path' in help_.stdout and '--max-trials' in help_.stdout

def test_engine_rejects_escaped_labels_and_source_edits_and_marks_orphan(tmp_path):
    from backend.engine.training_engine import prepare,create_run,read_run,execute_run,cancel_run
    source,_,rows=fixture(tmp_path);output=tmp_path/'output'
    with pytest.raises(ValueError,match='source'):
        prepare(task='rotation',source_dataset_path=str(source),output_dir=str(output),labels={'samples':[{**rows[0],'image':'../outside.png'}]})
    prepared=prepare(task='rotation',source_dataset_path=str(source),output_dir=str(output),labels={'samples':rows})
    run=create_run(output_dir=str(output),config={'width':8,'image_size':32},epochs_per_trial=1,mode='quick')
    assert read_run(output,run['run_id'])['status']=='queued'
    assert cancel_run(output,run['run_id'])['status']=='cancelled'
    assert execute_run(output,run['run_id'])['status']=='cancelled'
    other=create_run(output_dir=str(output),mode='quick',epochs_per_trial=1)
    path=output/'runs'/other['run_id']/'run.json';raw=json.loads(path.read_text());raw.update(status='running',owner_pid=2147483647);path.write_text(json.dumps(raw))
    assert read_run(output,other['run_id'])['status']=='interrupted'
    (source/'0.png').write_bytes(b'changed')
    with pytest.raises(ValueError,match='source'):
        create_run(output_dir=str(output),mode='quick',epochs_per_trial=1)

@pytest.mark.parametrize('task,config',[
    ('rotation',{'width':8,'image_size':32,'batch_size':1}),
    ('ocr',{'image_size':32,'image_width':64,'batch_size':1}),
    ('rotated_detection',{'image_size':32,'batch_size':1}),
    ('enhancement',{'batch_size':1}),
    ('defect_gan',{'base_channels':8,'batch_size':2}),
])
def test_all_specialist_rest_real_fit_evaluation_prediction_and_gui_isolation(tmp_path,task,config):
    import torch
    from backend.main import create_app
    from fastapi.testclient import TestClient
    torch.set_num_threads(1)
    app=create_app(project_dir=str(tmp_path/'projects'))
    api=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=api.post('/api/project/create',json={'name':'GUI stays selected'}).json()
    source,labels,rows=fixture(tmp_path);output=str(tmp_path/'headless')
    prepared=api.post('/api/engine/prepare',json={'task':task,'source_dataset_path':str(source),'output_dir':output,'labels':{'samples':rows}})
    assert prepared.status_code==200,prepared.text
    trained=api.post('/api/engine/train',json={'output_dir':output,'config':config,'epochs_per_trial':1,'background':False})
    assert trained.status_code==200,trained.text
    run=trained.json();assert run['status']=='completed',run
    assert api.get('/api/project/current').json()['id']==project['id']
    readback=api.get('/api/engine/status',params={'output_dir':output,'run_id':run['run_id']})
    assert readback.status_code==200 and readback.json()['status']=='completed'
    evaluated=api.post('/api/engine/evaluate',json={'output_dir':output,'run_id':run['run_id']})
    assert evaluated.status_code==200,evaluated.text
    assert evaluated.json()['evaluation']['evaluation_id']
    predicted=api.post('/api/engine/predict',json={'output_dir':output,'run_id':run['run_id'],'images':['2.png']})
    assert predicted.status_code==200,predicted.text
    assert predicted.json()['predictions'][0]['source_relative_path']=='2.png'
    delivered=api.get('/api/engine/artifacts',params={'output_dir':output,'run_id':run['run_id']}).json()['artifacts']
    assert set(delivered)=={'model','metadata','configuration','evaluation','predictions'}
    downloaded=api.get('/api/engine/artifacts/model',params={'output_dir':output,'run_id':run['run_id']})
    assert downloaded.status_code==200 and hashlib.sha256(downloaded.content).hexdigest()==delivered['model']['sha256']
    for index,artifact in enumerate(predicted.json()['output_files']):
        pixels=api.get(f'/api/engine/image-artifacts/{index}',params={'output_dir':output,'run_id':run['run_id']})
        assert pixels.status_code==200 and hashlib.sha256(pixels.content).hexdigest()==artifact['sha256']
    capture_receipt(api.get('/api/engine/status',params={'output_dir':output,'run_id':run['run_id']}).json())

def test_failed_label_revision_keeps_old_preparation_and_cooperative_rest_cancel(tmp_path):
    import torch
    import time
    from backend.main import create_app
    from fastapi.testclient import TestClient
    from backend.engine import training_engine as engine
    torch.set_num_threads(1)
    source,_,rows=fixture(tmp_path);output=str(tmp_path/'output')
    first=engine.prepare(task='rotation',source_dataset_path=str(source),output_dir=output,labels={'samples':rows})
    invalid=[{**row,'correction_deg':1000} for row in rows]
    with pytest.raises(ValueError):engine.prepare(task='rotation',source_dataset_path=str(source),output_dir=output,labels={'samples':invalid})
    assert engine._prepared(output)['prepared_id']==first['prepared_id']
    revised=[{**row,'correction_deg':0} for row in rows]
    second=engine.prepare(task='rotation',source_dataset_path=str(source),output_dir=output,labels={'samples':revised})
    assert second['prepared_id']!=first['prepared_id'] and engine._prepared(output,first['prepared_id'])['prepared_id']==first['prepared_id']
    app=create_app(project_dir=str(tmp_path/'projects'));api=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    accepted=api.post('/api/engine/train',json={'output_dir':output,'background':True,'epochs_per_trial':500,'config':{'image_size':32,'width':8,'batch_size':1}})
    assert accepted.status_code==200,accepted.text
    run_id=accepted.json()['run_id'];params={'output_dir':output,'run_id':run_id};deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        status=api.get('/api/engine/status',params=params).json()
        if status.get('search',{}).get('trials',[{}]) and status.get('search',{}).get('trials',[{}])[-1].get('progress',{}).get('epoch',0)>0:break
        time.sleep(.01)
    else:pytest.fail('Actual CPU training never published progress')
    with pytest.raises(ValueError,match='already been started'):engine.execute_run(output,run_id)
    cancelled=api.post('/api/engine/cancel',json=params)
    assert cancelled.status_code==200,cancelled.text
    while time.monotonic()<deadline:
        status=api.get('/api/engine/status',params=params).json()
        if status['status'] not in {'running','queued','stopping'}:break
        time.sleep(.01)
    assert status['status']=='cancelled' and status['winner'] is None and not status['artifacts'],status

def test_engine_rest_search_config_reuse_and_invalid_controls(tmp_path):
    import torch
    from backend.main import create_app
    from fastapi.testclient import TestClient
    torch.set_num_threads(1)
    source,_,rows=fixture(tmp_path);output=str(tmp_path/'output');app=create_app(project_dir=str(tmp_path/'projects'))
    api=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    assert api.post('/api/engine/prepare',json={'task':'rotation','source_dataset_path':str(source),'output_dir':output,'labels':{'samples':rows}}).status_code==200
    invalid=[{'config':{'weight_decay':.01}},{'budget':{'max_trials':0}},{'search_space':{'architectures':['fabricated_model']}},{'device':'cuda:99999'}]
    for changes in invalid:
        rejected=api.post('/api/engine/train',json={'output_dir':output,'background':False,**changes})
        assert rejected.status_code==422,rejected.text
    response=api.post('/api/engine/train',json={'output_dir':output,'background':False,'mode':'search','epochs_per_trial':1,
        'budget':{'max_trials':2,'max_total_epochs':2,'max_seconds':60},'search_space':{'widths':[8,16],'image_sizes':[32],'batch_sizes':[1],'learning_rates':[.001]}})
    assert response.status_code==200,response.text
    run=response.json();assert run['status']=='completed' and {trial['config']['width'] for trial in run['search']['trials']}=={8,16}
    recipe=run['artifacts']['configuration']['path']
    reused=api.post('/api/engine/train',json={'output_dir':output,'config_path':recipe,'mode':'fast_retrain','parent_job_id':run['winner']['trial_id'],'background':False})
    assert reused.status_code==200 and reused.json()['status']=='completed',reused.text

def test_shared_engine_authorization_keeps_outputs_and_sources_in_selected_project(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app=create_app(project_dir=str(tmp_path/'projects'),shared_auth_dir=str(tmp_path/'accounts'))
    desktop=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    assert desktop.post('/api/accounts/bootstrap',json={'username':'admin','password':'long password 123'}).status_code==200
    api=TestClient(app);token=api.post('/api/accounts/login',json={'username':'admin','password':'long password 123'}).json()['token'];api.headers['Authorization']='Bearer '+token
    project=api.post('/api/project/create',json={'name':'Engine shared scope'}).json();source,_,rows=fixture(tmp_path)
    assert api.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    output=str(Path(project['project_dir'])/'engine-output')
    missing=api.get('/api/engine/status',params={'output_dir':str(Path(project['project_dir'])/'missing'),'run_id':'a'*32})
    assert missing.status_code==404,missing.text
    request={'task':'rotation','source_dataset_path':str(source),'output_dir':output,'labels':{'samples':rows}}
    assert api.post('/api/engine/prepare',json=request).status_code==200
    assert api.post('/api/engine/prepare',json={**request,'output_dir':str(tmp_path/'foreign-output')}).status_code==403
    user=api.post('/api/accounts/users',json={'username':'viewer','password':'long password 456','administrator':False}).json()
    assert api.put(f"/api/accounts/projects/{project['id']}/members",json={'user_id':user['id'],'role':'viewer'}).status_code==200
    viewer=TestClient(app);session=viewer.post('/api/accounts/login',json={'username':'viewer','password':'long password 456'}).json();viewer.headers['Authorization']='Bearer '+session['token']
    assert viewer.post('/api/accounts/select-project',json={'project_id':project['id']}).status_code==200
    assert viewer.post('/api/engine/train',json={'output_dir':output}).status_code==403
    assert viewer.get('/api/engine/capabilities').status_code==200
    assert api.put(f"/api/accounts/projects/{project['id']}/members",json={'user_id':user['id'],'role':'trainer'}).status_code==200
    assert viewer.post('/api/engine/train',json={'output_dir':output,'epochs_per_trial':1,'config':{'width':8,'image_size':32},'background':False}).json()['status']=='completed'

def test_external_native_enhancement_pairs_keep_explicit_splits_and_hashes(tmp_path):
    import torch
    from backend.engine import training_engine as engine
    torch.set_num_threads(1);source=tmp_path/'pairs';source.mkdir();rows=[]
    for index,split in enumerate(('train','val','test')):
        clean=np.random.default_rng(index).integers(30,200,(32,48,3),dtype=np.uint8);noisy=np.clip(clean.astype(int)+10,0,255).astype(np.uint8)
        for name,pixels in [('noisy',noisy),('clean',clean)]:Image.fromarray(pixels).save(source/f'{name}{index}.png')
        rows.append({'image':f'noisy{index}.png','target':f'clean{index}.png','split':split})
    hashes={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in source.iterdir()};output=tmp_path/'output'
    prepared=engine.prepare(task='enhancement',source_dataset_path=str(source),output_dir=str(output),labels={'samples':rows})
    manifest=json.loads((Path(prepared['dataset_path'])/'pairs.json').read_text())
    assert manifest['mode']=='explicit_native_pairs' and [row['split'] for row in manifest['records']]==['train','val','test']
    assert [row['target_source_sha256'] for row in manifest['records']]==[hashes[f'clean{index}.png'] for index in range(3)]
    queued=engine.create_run(output_dir=str(output),mode='quick',epochs_per_trial=1,config={'batch_size':1})
    run=engine.execute_run(output,queued['run_id']);assert run['status']=='completed',run
    assert engine.evaluate(output,run['run_id'])['evaluation']['sample_count']==1
    assert hashes=={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}

def test_real_cached_pretrained_classification_external_labels_cli(tmp_path):
    checkpoint=os.environ.get('VISION_QA_DINOV3_CHECKPOINT')
    cache=Path(checkpoint).expanduser() if checkpoint else None
    if cache is None or not cache.is_file():pytest.skip('Set VISION_QA_DINOV3_CHECKPOINT to an approved local pretrained file for this optional CPU gate; no model download')
    source=tmp_path/'flat';source.mkdir();rows=[]
    for index,(split,label) in enumerate((partition,label) for partition in ('train','val','test') for label in ('OK','scratch')):
        name=f'{index}.png';Image.fromarray(np.random.default_rng(index).integers(0,255,(32,48,3),dtype=np.uint8)).save(source/name)
        rows.append({'image':name,'split':split,'label':label})
    labels=tmp_path/'truth.json';labels.write_text(json.dumps({'samples':rows}));output=tmp_path/'delivery'
    cli('prepare','--task','classification','--source',source,'--labels',labels,'--output',output)
    configuration={'backbone':'dinov3_vits16','pretrained_checkpoint':str(cache),'pretrained_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),
        'image_size':[32,48],'batch_size':2,'augmentation_profile':'none'}
    run=cli('train','--output',output,'--epochs','1','--config-json',json.dumps(configuration))[-1]
    assert run['status']=='completed',run
    meta=json.loads(Path(run['artifacts']['metadata']['path']).read_text())
    assert meta['pretrained'] is True and meta['pretrained_sha256']==configuration['pretrained_sha256']
    assert meta['classes']==['OK','scratch'] and meta['image_size']==[32,48]
    evaluated=cli('evaluate','--output',output,'--run-id',run['run_id'])[-1]
    assert len(evaluated['evaluation']['test_predictions'])==2
    assert all(Path(row['file_path']).is_relative_to(source) and Path(row['evaluation_file_path']).is_relative_to(output) for row in evaluated['evaluation']['test_predictions'])
    predicted=cli('predict','--output',output,'--run-id',run['run_id'],'--image','5.png')[-1]
    assert predicted['predictions'][0]['prediction']['predictions']['predicted_class'] in {'OK','scratch'}
    capture_receipt(cli('status','--output',output,'--run-id',run['run_id'])[-1])

@pytest.mark.parametrize('task',['patch_classification','segmentation','anomaly','detection'])
def test_real_cached_primary_rest_fit_and_downstream(task,tmp_path):
    import torch
    from backend.main import create_app
    from fastapi.testclient import TestClient
    torch.set_num_threads(1)
    checkpoint=os.environ.get('VISION_QA_DINOV3_CHECKPOINT')
    dino=Path(checkpoint).expanduser() if checkpoint else None
    yolo=Path(torch.hub.get_dir())/'checkpoints'/'yolo26n.pt'
    cache=yolo if task=='detection' else dino
    if cache is None or not cache.is_file():pytest.skip('Approved local pretrained file unavailable; set VISION_QA_DINOV3_CHECKPOINT for DINOv3; no model download in this CPU gate')
    source,_,rows=fixture(tmp_path);output=str(tmp_path/'output')
    config={'pretrained_checkpoint':str(cache),'pretrained_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),'image_size':32,'batch_size':2,'augmentation_profile':'none'}
    if task=='anomaly':
        config={'pretrained_checkpoint':str(cache),'pretrained_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),'patch_size':32,'stride':16,'patches_per_image':2,'batch_size':2}
        for row in rows:
            row['label']='OK' if row['split']=='train' else 'scratch'
            if row['label']=='OK':row['annotations']=[]
        for index,split in [(4,'val'),(5,'test')]:
            name=f'{index}.png';Image.fromarray(np.random.default_rng(index).integers(0,255,(32,48,3),dtype=np.uint8)).save(source/name)
            rows.append({'image':name,'split':split,'label':'OK'})
    app=create_app(project_dir=str(tmp_path/'projects'));api=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    prepared=api.post('/api/engine/prepare',json={'task':task,'source_dataset_path':str(source),'output_dir':output,'labels':{'samples':rows},'prepare_options':{'patch_size':32,'stride':16} if task=='patch_classification' else {}})
    assert prepared.status_code==200,prepared.text
    trained=api.post('/api/engine/train',json={'output_dir':output,'config':config,'epochs_per_trial':1,'background':False})
    assert trained.status_code==200,trained.text
    run=trained.json();assert run['status']=='completed',run
    evaluated=api.post('/api/engine/evaluate',json={'output_dir':output,'run_id':run['run_id']})
    assert evaluated.status_code==200,evaluated.text
    predicted=api.post('/api/engine/predict',json={'output_dir':output,'run_id':run['run_id'],'images':['2.png']})
    assert predicted.status_code==200,predicted.text
    assert predicted.json()['predictions'][0]['source_image']==str(source/'2.png')
    opened=api.post('/api/project/open',json={'project_dir':output});assert opened.status_code==200,opened.text
    persisted=api.get('/api/training/status',params={'job_id':run['winner']['trial_id']})
    assert persisted.status_code==200 and persisted.json()['device_name']=='cpu',persisted.text
    capture_receipt(api.get('/api/engine/status',params={'output_dir':output,'run_id':run['run_id']}).json())
