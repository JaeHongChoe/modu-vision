"""Actual pretrained CPU data→parent retrain→fixed-cohort approval boundary."""
import hashlib
import json
from pathlib import Path
import threading
import os
import socket
import subprocess
import sys
import time
from dataclasses import replace
import httpx
import numpy as np
import pytest
import torch
from PIL import Image
from fastapi.testclient import TestClient


def test_actual_cached_pretrained_operations_cycle_retrains_and_awaits_approval(tmp_path,monkeypatch):
    from backend.main import create_app
    from backend.engine.model_operations import configure_program,run_cycle,OperationsStore,project_scope
    from backend.engine.automated_trials import run_automated_training
    from backend.engine.training_provenance import bind_training_version
    checkpoint=os.environ.get('VISION_QA_DINOV3_CHECKPOINT')
    cache=Path(checkpoint).expanduser() if checkpoint else None
    if cache is None or not cache.is_file():pytest.skip('Set VISION_QA_DINOV3_CHECKPOINT to an approved local pretrained file for this optional CPU gate; no model download')
    torch.set_num_threads(1);source=tmp_path/'native'
    for index,(split,label) in enumerate((split,label) for split in ('train','val','test') for label in ('OK','scratch')):
        path=source/split/label/f'{index}.png';path.parent.mkdir(parents=True,exist_ok=True)
        Image.fromarray(np.random.default_rng(index).integers(0,255,(32,48,3),dtype=np.uint8)).save(path)
    app=create_app(project_dir=str(tmp_path/'projects'));api=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=api.post('/api/project/create',json={'name':'Actual operations CPU','task':'classification'}).json()
    assert api.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    project=api.get('/api/project/current').json()
    config={'pretrained_checkpoint':str(cache),'pretrained_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),
        'backbone':'dinov3_vits16','image_size':[32,48],'batch_size':2,'augmentation_profile':'none'}
    with project_scope(project):
        binding=bind_training_version(project,source)
        parent=run_automated_training(task='classification',dataset_path=source,models_dir=project['models_dir'],mode='quick',
            base_config=config,epochs_per_trial=1,training_binding=binding)
    assert parent['status']=='completed',parent
    parent_id=parent['winner']['trial_id'];parent_path=Path(parent['winner']['checkpoint_path']);parent_hash=hashlib.sha256(parent_path.read_bytes()).hexdigest()
    policy=configure_program(project,{'parent_job_id':parent_id,'task':'classification','auto_retrain':True,'require_label_review':False,
        'auto_label':False,'auto_approve':False,'reviewer':'controlled-functional-fixture','epochs_per_trial':1,
        'budget':{'max_trials':1,'max_total_epochs':1,'max_seconds':60}})
    fresh=source/'train'/'scratch'/'fresh.png';Image.fromarray(np.random.default_rng(17).integers(0,255,(32,48,3),dtype=np.uint8)).save(fresh)
    truth=fresh.with_suffix('.json');truth.write_text(json.dumps({'version':'5.0.0','imagePath':fresh.name,'imageWidth':48,'imageHeight':32,
        'shapes':[{'label':'scratch','shape_type':'tag','points':[],'flags':{'studio_tag':True,'is_normal':False}}]}))
    originals={path.relative_to(source).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in source.rglob('*') if path.is_file()}
    # A separate loopback service keeps the real incumbent available while the
    # candidate optimizer is active. The batch barrier makes overlap deterministic;
    # it is a concurrency acceptance check, not a throughput measurement.
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.engine.flow_package import build_flow_package
    from backend.engine import automated_trials
    pipeline=get_single_segmentation_flowchart(parent_id)
    for node in pipeline.nodes:
        if node.data.node_type=='inspection':node.data.task='classification'
    package=build_flow_package(pipeline=pipeline,checkpoints={parent_id:parent_path},output_base_dir=tmp_path/'exports',package_name='functional_incumbent')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    service_log=(tmp_path/'service.log').open('wb');token='functional-test-token'
    process=subprocess.Popen([sys.executable,'-m','backend.engine.inspection_service','--package',package['package_path'],
        '--state-dir',str(tmp_path/'service-state'),'--input-root',str(source),'--token',token,'--port',str(port),'--device','cpu'],
        cwd=Path(__file__).resolve().parents[2],env={**os.environ,'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1'},stdout=service_log,stderr=service_log)
    entered=threading.Event();release=threading.Event();cycles=[];errors=[]
    original=automated_trials._RUNNERS['classification']
    def observed_runner(context):
        def observed_progress(progress):
            context.on_progress(progress)
            if 'batch' in progress and not entered.is_set():
                entered.set()
                if not release.wait(30):raise RuntimeError('Functional service overlap check timed out')
        return original.run(replace(context,on_progress=observed_progress))
    monkeypatch.setitem(automated_trials._RUNNERS,'classification',replace(original,run=observed_runner))
    def execute_cycle():
        try:cycles.append(run_cycle(project,threading.Event()))
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=execute_cycle)
    service_result=None
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'X-Vision-Token':token},timeout=10) as service:
            deadline=time.monotonic()+30
            while True:
                assert process.poll() is None,(tmp_path/'service.log').read_text()
                try:
                    if service.get('/health').status_code==200:break
                except httpx.TransportError:pass
                assert time.monotonic()<deadline,'Functional service startup timed out'
                time.sleep(.05)
            thread.start();assert entered.wait(30),'Candidate did not execute an actual optimizer batch'
            assert thread.is_alive()
            request=service.post('/v1/jobs/file',json={'image_path':str(source/'test'/'OK'/'4.png')})
            assert request.status_code==202,request.text
            service_id=request.json()['job_id'];deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                service_result=service.get(f'/v1/jobs/{service_id}').json()
                if service_result['state'] in {'completed','failed','review'}:break
                time.sleep(.05)
            assert service_result['state']=='completed',service_result
            assert service_result['verdict'] in {'OK','NG'},service_result
            assert service_result['result']['execution_steps']
            assert thread.is_alive() and not release.is_set()
            states=[event['state'] for event in service.get(f'/v1/jobs/{service_id}/events').json()['events']]
            assert states==['queued','running','completed']
            release.set();thread.join(timeout=60)
            assert not thread.is_alive() and not errors,errors
    finally:
        release.set()
        if thread.ident is not None:thread.join(timeout=60)
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
        service_log.close()
    cycle=cycles[0]
    assert cycle['status']=='awaiting_approval',cycle
    result=cycle['result'];training=result['training'];candidate=training['winner']
    assert training['mode']=='fast_retrain' and training['configuration_parent']['parent_job_id']==parent_id
    assert candidate['config']==parent['winner']['config']
    candidate_meta=json.loads(Path(candidate['checkpoint_path']).with_name('model_meta.json').read_text())
    assert candidate_meta['training_provenance']['dataset_version_id']!=binding['dataset_version_id']
    assert candidate_meta['training_provenance']['manifest_sha256']
    evaluation=result['evaluation']
    assert evaluation['incumbent']['evaluation_id'] and evaluation['candidate']['evaluation_id']
    comparison=evaluation['comparison']
    assert comparison['full_test'] is True and comparison['selected_image_count']==comparison['total_test_images']==2
    assert set(policy['holdout'])=={row['file_path'] for row in comparison['images']}
    assert 'approval' not in result and 'deployment' not in result
    assert OperationsStore(project['project_dir']).get(cycle['cycle_id'])['status']=='awaiting_approval'
    assert api.get('/api/model-operations').json()['cycles'][0]['status']=='awaiting_approval'
    assert hashlib.sha256(parent_path.read_bytes()).hexdigest()==parent_hash
    assert originals=={path.relative_to(source).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in source.rglob('*') if path.is_file()}
    if path:=os.environ.get('P10_CAPTURE_EVIDENCE'):
        Path(path).write_text(json.dumps({'evidence_scope':'actual_pretrained_functional_CPU_not_quality_approval',
            'parent_job_id':parent_id,'parent_checkpoint_sha256':parent_hash,'cycle':cycle,
            'independent_service':{'pid':process.pid,'package_manifest_sha256':hashlib.sha256((Path(package['package_path'])/'manifest.json').read_bytes()).hexdigest(),'job':service_result,
                'overlap':'actual_candidate_batch_progress_then_barrier_while_independent_incumbent_HTTP_inference_completed'},
            'source_sha256':originals},indent=2,allow_nan=False))
