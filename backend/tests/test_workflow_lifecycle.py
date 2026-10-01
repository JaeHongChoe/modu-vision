import json
from pathlib import Path
import pytest


def test_evaluation_snapshots_are_immutable_and_group_only_real_metadata(tmp_path):
    from backend.engine.evaluation_history import EvaluationHistory, grouped_errors
    history = EvaluationHistory(tmp_path)
    payload = {'job_id': 'job_one', 'task': 'classification', 'test_predictions': [
        {'file_path': '/source/a.png', 'ground_truth': 'OK', 'prediction': 'NG', 'product': 'part-A', 'lot': 'lot-1'},
        {'file_path': '/source/b.png', 'ground_truth': 'OK', 'prediction': 'OK'},
    ]}
    first = history.append(payload, {'dataset_fingerprint': 'v1:a', 'checkpoint_sha256': 'a'*64})
    payload['test_predictions'][0]['prediction'] = 'OK'
    second = history.append(payload, {'dataset_fingerprint': 'v1:a', 'checkpoint_sha256': 'a'*64})
    assert first['evaluation_id'] != second['evaluation_id']
    assert history.get(first['evaluation_id'])['result']['test_predictions'][0]['prediction'] == 'NG'
    assert grouped_errors(history.get(first['evaluation_id'])['result']['test_predictions'])['product']['part-A']['errors'] == 1
    path = tmp_path / (first['evaluation_id'] + '.json')
    path.write_text('{}')
    with pytest.raises(ValueError, match='integrity'):
        history.get(first['evaluation_id'])


def test_comparison_cancel_and_recovery_are_durable(tmp_path):
    from backend.engine.evaluation_history import ComparisonJobs
    jobs = ComparisonJobs(tmp_path / 'jobs.sqlite3')
    job = jobs.create({'source_dataset_path': '/source', 'full_test': True})
    jobs.start(job['job_id'], 30)
    jobs.progress(job['job_id'], 7)
    assert ComparisonJobs(tmp_path / 'jobs.sqlite3').get(job['job_id'])['completed_images'] == 7
    jobs.cancel(job['job_id'])
    assert jobs.cancelled(job['job_id'])
    jobs.finish(job['job_id'], 'cancelled')
    assert jobs.get(job['job_id'])['status'] == 'cancelled'


def test_shared_resource_leases_exclude_other_process_and_keep_uncertain_remote(tmp_path):
    from backend.engine.shared_scheduler import ResourceLeases
    first = ResourceLeases(tmp_path / 'leases.sqlite3', owner='process-a', lease_seconds=.01)
    second = ResourceLeases(tmp_path / 'leases.sqlite3', owner='process-b', lease_seconds=.01)
    assert first.acquire('job_a', 'server:22', '0', remote=True)
    assert not second.acquire('job_b', 'server:22', '0', remote=True)
    assert second.acquire('job_c', 'server:22', '1', remote=True)
    first.mark_uncertain('job_a')
    first.release('job_a', terminal=False)
    assert not second.acquire('job_b', 'server:22', 'all', remote=True)
    first.release('job_a', terminal=True)
    second.release('job_c', terminal=True)
    assert second.acquire('job_b', 'server:22', 'all', remote=True)


def test_runtime_apply_ack_mismatch_preserves_previous_release(tmp_path):
    from backend.engine.runtime_deployment import DeploymentLedger
    ledger = DeploymentLedger(tmp_path)
    before = ledger.apply({'manifest_sha256': 'a'*64, 'package_path': '/release/a'},
                          lambda release: {**release, 'status': 'ready'}, reviewer='operator')
    with pytest.raises(ValueError, match='acknowledgment'):
        ledger.apply({'manifest_sha256': 'b'*64, 'package_path': '/release/b'},
                     lambda release: {'status': 'ready', 'manifest_sha256': 'c'*64}, reviewer='operator')
    assert ledger.active()['deployment_id'] == before['deployment_id']
    restored = ledger.rollback(before['deployment_id'], lambda release: {**release, 'status': 'ready'}, reviewer='operator')
    assert restored['restored_from'] == before['deployment_id']


def test_failed_apply_requires_exact_ready_device_ack_from_runtime_rollback(tmp_path):
    from backend.engine.runtime_deployment import DeploymentLedger
    ledger=DeploymentLedger(tmp_path)
    ledger.apply({'manifest_sha256':'a'*64,'device':'cpu'},lambda release:{**release,'status':'ready'},reviewer='qa')
    def fail_and_bad_restore(release):
        if release['manifest_sha256']=='b'*64:return {**release,'status':'disconnected'}
        return {**release,'device':'cuda','status':'ready'}
    with pytest.raises(ValueError,match='rollback acknowledgment failed'):
        ledger.apply({'manifest_sha256':'b'*64,'device':'cpu'},fail_and_bad_restore,reviewer='qa')


def test_runtime_device_unavailable_is_error_not_cpu_fallback(monkeypatch):
    from backend.engine.runtime_device import resolve_runtime_device
    import torch
    monkeypatch.setattr(torch.cuda,'is_available',lambda:False)
    with pytest.raises(ValueError,match='unavailable'):resolve_runtime_device('cuda')
    assert str(resolve_runtime_device('cpu'))=='cpu'


def test_service_runtime_switch_reopens_same_verified_identity(tmp_path):
    from backend.main import create_app  # Shared TestClient/httpx compatibility.
    from backend.engine.inspection_service import create_service_app
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import get_single_detection_flowchart
    from fastapi.testclient import TestClient
    import hashlib
    releases=tmp_path/'releases'; releases.mkdir()
    packages=[]
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.tests.runtime_release_fixture import real_classification_checkpoints,cohort_receipt,bind_policy
    from PIL import Image
    images=[]
    for number,color in enumerate(('white','black')):
        image=tmp_path/f'parity_{number}.png';Image.new('RGB',(32,32),color).save(image);images.append(image)
    for number in range(2):
        model=tmp_path/f'job_{number}'/'best_model.pt';model.parent.mkdir()
        real_classification_checkpoints({f'job_{number}':model})
        revision={'revision_id':str(number)*32,'job_id':f'job_{number}','task':'classification','checkpoint_sha256':hashlib.sha256(model.read_bytes()).hexdigest()}
        graph=get_single_segmentation_flowchart(job_id=f'job_{number}')
        for node in graph.nodes:
            if node.data.node_type=='inspection':node.data.task='classification'
        result=build_flow_package(pipeline=graph,checkpoints={f'job_{number}':model},output_base_dir=releases,package_name=f'release_{number}',approved_revisions={revision['job_id']:revision})
        package=Path(result['package_path'])
        cohort_receipt(package,graph,{f'job_{number}':model},images)
        policy=releases/f'policy_{number}.json';policy.write_text(json.dumps(bind_policy(result['release_policy'],package)))
        packages.append((package,policy,result['release_policy']['manifest_sha256']))
    state=tmp_path/'state'
    app=create_service_app(packages[0][0],state,token='secret',auto_worker=False,release_policy=packages[0][1],runtime_root=releases)
    with TestClient(app) as client:
        client.headers.update({'X-Vision-Token':'secret'})
        response=client.post('/v1/runtime/apply',json={'package_path':str(packages[1][0]),'release_policy':str(packages[1][1]),'manifest_sha256':packages[1][2],'device':'cpu'})
        assert response.status_code==200,response.text
        assert client.get('/v1/runtime').json()['manifest_sha256']==packages[1][2]
    restarted=create_service_app(packages[0][0],state,token='secret',auto_worker=False,release_policy=packages[0][1],runtime_root=releases)
    with TestClient(restarted) as client:
        client.headers.update({'X-Vision-Token':'secret'})
        assert client.get('/v1/runtime').json()['manifest_sha256']==packages[1][2]


def test_remote_warmstart_pins_parent_hash_and_signature(tmp_path):
    from backend.engine.warm_start import WarmStartParent, portable_parent, restore_portable_parent
    import hashlib
    import torch
    checkpoint=tmp_path/'parent.pt'
    torch.save({'task':'classification','backbone':'resnet18','classes':['OK','NG'],'model_state_dict':{'weight':torch.zeros(2,3)}},checkpoint)
    parent=WarmStartParent('job_parent',checkpoint,hashlib.sha256(checkpoint.read_bytes()).hexdigest(),'classification','classification:resnet18',('OK','NG'),'v1:source')
    envelope=portable_parent(parent,tmp_path/'transfer')
    restored=restore_portable_parent(tmp_path/'transfer',envelope,'classification')
    assert restored.lineage()==parent.lineage()
    (tmp_path/'transfer'/'parent.pt').write_bytes(b'tamper')
    with pytest.raises(ValueError,match='hash'):restore_portable_parent(tmp_path/'transfer',envelope,'classification')


def test_new_training_records_exact_version_labelset_and_checkpoint_binding(tmp_path,monkeypatch):
    import torch
    from PIL import Image
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.api import routes_training
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    monkeypatch.chdir(tmp_path)
    manager=routes_training.TrainingJobManager(local_execution='embedded');monkeypatch.setattr(routes_training,'training_job_manager',manager)
    class Trainer:
        def __init__(self,**kwargs):self.output=Path(kwargs['output_dir'])
        def train(self,job_id):
            torch.save({'task':'classification','classes':['OK','NG'],'backbone':'resnet18','model_state_dict':{'weight':torch.zeros(2,3)}},self.output/'best_model.pt')
            (self.output/'model_meta.json').write_text(json.dumps({'task':'classification','classes':['OK','NG'],'backbone':'resnet18'}))
            return {'status':'completed'}
    monkeypatch.setattr(routes_training,'UnifiedAutoMLTrainer',Trainer)
    source=tmp_path/'source'
    for split in ['train','val']:
        for label in ['OK','NG']:
            folder=source/split/label;folder.mkdir(parents=True);Image.new('RGB',(16,16)).save(folder/'one.png')
    app=create_app(project_dir=str(tmp_path/'registry'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=client.post('/api/project/create',json={'name':'Bound training','task':'classification'}).json()
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    response=client.post('/api/training/start',json={'task':'classification','dataset_path':str(source),'device':'cpu'})
    assert response.status_code==200,response.text
    payload=response.json();record=manager.get_job(payload['job_id']);record.thread.join(5)
    assert record.status=='completed'
    binding=payload['training_provenance']
    assert binding['dataset_version_id'].startswith('v_')
    assert binding['labelset_id']=='default'
    assert len(binding['split_sha256'])==64
    output=Path(project['models_dir'])/payload['job_id']
    assert json.loads((output/'job_receipt.json').read_text())['training_provenance']==binding
    assert json.loads((output/'model_meta.json').read_text())['training_provenance']==binding
    assert torch.load(output/'best_model.pt',weights_only=True)['training_provenance']==binding


def test_unknown_ground_truth_never_counts_as_fabricated_error():
    from backend.engine.evaluation_history import grouped_errors
    result=grouped_errors([{'product':'part-A','candidate':{'verdict':'OK'},'ground_truth_verdict':None}])
    assert result['product']['part-A']['errors']==0
    assert result['product']['part-A']['unknown_truth']==1


def test_grouped_binary_metrics_use_actual_class_semantics_and_preserve_class_errors():
    from backend.engine.evaluation_history import grouped_errors
    rows = [
        {'product':'part-A','ground_truth':'Scratch','predicted_class':'OK','is_correct':False},
        {'product':'part-A','ground_truth':'정상','predicted_class':'Scratch','is_correct':False},
        {'product':'part-A','ground_truth':'Scratch','predicted_class':'Dent','is_correct':False},
        {'product':'part-A','ground_truth':'scratch','predicted_class':'normal','is_correct':False},
        {'product':'part-A','ground_truth':'normal','predicted_class':'scratch','is_correct':False},
    ]
    counts = grouped_errors(rows, task='classification')['product']['part-A']
    assert counts == {'samples':5,'errors':5,'misses':2,'overkill':2,'unknown_truth':0}
    comparison = grouped_errors([
        {'product':'part-A','ground_truth':'background','ground_truth_verdict':'OK','candidate':{'verdict':'OK'}},
        {'product':'part-A','ground_truth':'Scratch','ground_truth_verdict':None,'candidate':{'verdict':'NG'}},
    ], task='segmentation')['product']['part-A']
    assert comparison == {'samples':2,'errors':0,'misses':0,'overkill':0,'unknown_truth':1}


def test_ocr_grouped_errors_do_not_treat_transcript_as_binary_verdict(tmp_path):
    from backend.engine.evaluation_history import EvaluationHistory
    rows = [{'product':'part-A','ground_truth':'NG','predicted_class':'OK'}]
    history = EvaluationHistory(tmp_path)
    record = history.append({'task':'ocr','test_predictions':rows}, {'task':'ocr'})
    assert record['grouped_errors']['product']['part-A'] == {
        'samples':1,'errors':1,'misses':0,'overkill':0,'unknown_truth':0,
    }


def test_shared_lease_blocks_real_second_process(tmp_path):
    import subprocess
    import sys
    from backend.engine.shared_scheduler import ResourceLeases
    path=tmp_path/'leases.sqlite3';leases=ResourceLeases(path,owner='first')
    assert leases.acquire('job_a','test-server','0',remote=True)
    script="from backend.engine.shared_scheduler import ResourceLeases;import sys;print(ResourceLeases(sys.argv[1],owner='second').acquire('job_b','test-server','0',remote=True))"
    completed=subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True,text=True,check=True)
    assert completed.stdout.strip()=='False'
    leases.release('job_a',terminal=True)


def test_family_binding_validates_live_manifest_and_freezes_its_exact_bytes(tmp_path,monkeypatch):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    from backend.engine.ocr import write_ocr_manifest
    from backend.engine.training_provenance import bind_family_training,validate_training_binding
    from PIL import Image
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    source=tmp_path/'source';source.mkdir()
    rows=[]
    for index,split in enumerate(('train','val','test')):
        image=f'{index}.png';Image.new('RGB',(16,16),(index*60,0,0)).save(source/image)
        rows.append({'image':image,'text':'A','split':split})
    write_ocr_manifest(source,rows)
    app=create_app(project_dir=str(tmp_path/'registry'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=client.post('/api/project/create',json={'name':'Family binding'}).json()
    project=client.put('/api/project/update',json={'source_dataset_dir':str(source)}).json()
    binding=bind_family_training(project,source,'ocr')
    manifest_row=next(row for row in binding['family_inputs'] if row['relative_path']=='ocr.json')
    assert Path(manifest_row['snapshot_path']).read_bytes()==(source/'ocr.json').read_bytes()
    validate_training_binding(binding)
    (source/'ocr.json').write_text('{}')
    with pytest.raises(ValueError,match='Family training input changed'):validate_training_binding(binding)
    with pytest.raises(ValueError,match='active project'):bind_family_training(project,tmp_path/'other','ocr')


def test_gpu_lease_context_renews_while_running_and_releases_on_error(tmp_path):
    from backend.engine.shared_scheduler import ResourceLeases,compute_lease_scope
    import time
    first=ResourceLeases(tmp_path/'leases.sqlite3',owner='first',lease_seconds=.06)
    second=ResourceLeases(tmp_path/'leases.sqlite3',owner='second',lease_seconds=.06)
    with pytest.raises(RuntimeError,match='fixture failure'):
        with compute_lease_scope('family_a','cuda',leases=first):
            time.sleep(.15)
            assert not second.acquire('family_b','local-compute','all')
            raise RuntimeError('fixture failure')
    assert second.acquire('family_b','local-compute','all')


def test_binding_refreshes_family_checkpoint_hash_after_inserting_lineage(tmp_path):
    import torch,hashlib
    from backend.engine.training_provenance import persist_model_binding
    model=tmp_path/'best_model.pt';torch.save({'task':'ocr','model_state_dict':{}},model)
    old=hashlib.sha256(model.read_bytes()).hexdigest()
    (tmp_path/'model_meta.json').write_text(json.dumps({'task':'ocr','checkpoint_sha256':old}))
    persist_model_binding(tmp_path,{'dataset_version_id':'v_fixture'})
    meta=json.loads((tmp_path/'model_meta.json').read_text())
    assert meta['checkpoint_sha256']==hashlib.sha256(model.read_bytes()).hexdigest()!=old


def test_source_labelme_labels_are_frozen_before_preparation(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    from backend.engine.training_provenance import bind_training_version,frozen_annotation_root
    from backend.engine.annotation_storage import dataset_annotation_dir
    from PIL import Image
    source=tmp_path/'source';source.mkdir();Image.new('RGB',(16,16)).save(source/'one.png')
    original={'imagePath':'one.png','imageWidth':16,'imageHeight':16,'shapes':[{'label':'old','shape_type':'rectangle','points':[[1,1],[8,8]]}]}
    (source/'one.json').write_text(json.dumps(original))
    app=create_app(project_dir=str(tmp_path/'registry'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    client.post('/api/project/create',json={'name':'Frozen source labels','task':'detection'})
    project=client.put('/api/project/update',json={'source_dataset_dir':str(source)}).json()
    binding=bind_training_version(project,source)
    output=tmp_path/'job';output.mkdir()
    root=frozen_annotation_root(binding,source,output)
    copied=dataset_annotation_dir(source,root,use_scope=False)/'one.json'
    assert json.loads(copied.read_text())['shapes']==original['shapes']
    assert json.loads(copied.read_text())['annotations'][0]['bbox']==[1,1,8,8]
    (source/'one.json').write_text('{}')
    assert json.loads(copied.read_text())['shapes']==original['shapes']


def test_explicit_family_reevaluation_accepts_revised_compatible_labels_and_preserves_training_version(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    from backend.engine.ocr import SmallCTCOCR,write_ocr_manifest
    from backend.engine.training_provenance import bind_family_training,persist_model_binding
    from PIL import Image
    import torch,uuid
    source=tmp_path/'source';source.mkdir();rows=[]
    for index,split in enumerate(('train','val','test')):
        image=f'{index}.png';Image.new('RGB',(32,16),(index*70,0,0)).save(source/image)
        rows.append({'image':image,'text':'AB','split':split})
    original=write_ocr_manifest(source,rows)
    app=create_app(project_dir=str(tmp_path/'registry'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    client.post('/api/project/create',json={'name':'Corrected OCR labels'})
    project=client.put('/api/project/update',json={'source_dataset_dir':str(source)}).json()
    binding=bind_family_training(project,source,'ocr')
    job=uuid.uuid4().hex;output=Path(project['models_dir'])/'ocr'/job;output.mkdir(parents=True)
    torch.save({'task':'ocr','version':1,'alphabet':'AB','image_size':[16,32],
                'model_state_dict':SmallCTCOCR(2).state_dict(),'dataset_provenance':original.provenance},output/'best_model.pt')
    (output/'model_meta.json').write_text(json.dumps({'task':'ocr','source_dataset_path':str(source),
        'alphabet':'AB','dataset_provenance':original.provenance}))
    persist_model_binding(output,binding)
    rows[2]['text']='BA';revised=write_ocr_manifest(source,rows)
    result=client.post('/api/evaluation/reevaluate',json={'source_dataset_path':str(source),'task':'ocr','job_id':job})
    assert result.status_code==200,result.text
    evidence=result.json()
    assert evidence['samples'][0]['reference_text']=='BA'
    assert evidence['binding']['training_provenance']['dataset_version_id']==binding['dataset_version_id']
    assert evidence['binding']['family_dataset_sha256']==revised.provenance['dataset_sha256']!=original.provenance['dataset_sha256']
    assert evidence['evaluation_id']
    approval=client.post('/api/model-deployments/specialized-approve',json={
        'source_dataset_path':str(source),'task':'ocr','evaluation_id':evidence['evaluation_id'],
        'reviewer':'fixture reviewer','reason':'Fixture lifecycle binding reviewed, no quality claim',
        'holdout_reviewed':True,'minimum_sample_count':1,'minimum_metrics':{'exact_match_accuracy':0},
    })
    assert approval.status_code==200,approval.text
    from backend.api.routes_model_deployments import verified_approval_revision,verified_release_revision
    revision=approval.json()['revision'];checkpoint=output/'best_model.pt'
    assert verified_approval_revision(project,revision['revision_id']) is not None
    assert verified_release_revision(project,revision['revision_id'],source=source,task='ocr',job_id=job,checkpoint=checkpoint) is not None
    assert torch.load(checkpoint,weights_only=True)['training_provenance']['dataset_version_id']==binding['dataset_version_id']
    Image.new('RGB',(32,16),(255,0,0)).save(source/'2.png')
    assert verified_approval_revision(project,revision['revision_id']) is None


def test_specialized_approval_requires_real_heldout_evidence_and_quality_bounds(tmp_path,monkeypatch):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    from PIL import Image
    from backend.engine.evaluation_history import EvaluationHistory
    from backend.api import routes_model_deployments,routes_model_comparisons
    from backend.engine.ocr import write_ocr_manifest
    import hashlib
    source=tmp_path/'source';source.mkdir();ocr_rows=[]
    for index,split in enumerate(('train','val','test')):
        image=f'{index}.png';Image.new('RGB',(8,8),(index*60,0,0)).save(source/image)
        ocr_rows.append({'image':image,'text':'A','split':split})
    ocr_manifest=write_ocr_manifest(source,ocr_rows)
    app=create_app(project_dir=str(tmp_path/'registry'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=client.post('/api/project/create',json={'name':'OCR release','task':'classification'}).json();client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    job='a'*32;model=Path(project['models_dir'])/'ocr'/job/'best_model.pt';model.parent.mkdir(parents=True);model.write_bytes(b'fixture')
    model_info={'checkpoint_path':str(model),'training_dataset_fingerprint':'v1:training'}
    monkeypatch.setattr(routes_model_deployments,'_model',lambda *args:model_info)
    fingerprint=routes_model_comparisons._fingerprint(source)
    record=EvaluationHistory(Path(project['reports_dir'])/'evaluations').append({'job_id':job,'task':'ocr','split':'test','sample_count':1,'exact_match_accuracy':.75,'character_error_rate':.1,'samples':[{'reference_text':'A','predicted_text':'A'}]}, {'source_dataset_path':str(source),'evaluation_dataset_path':str(source),'family_dataset_sha256':ocr_manifest.provenance['dataset_sha256'],'dataset_fingerprint':fingerprint,'checkpoint_sha256':hashlib.sha256(model.read_bytes()).hexdigest()})
    payload={'source_dataset_path':str(source),'task':'ocr','evaluation_id':record['evaluation_id'],'reviewer':'qa','reason':'Reviewed independent transcript holdout','holdout_reviewed':True,'minimum_sample_count':1,'minimum_metrics':{'exact_match_accuracy':.9}}
    rejected=client.post('/api/model-deployments/specialized-approve',json=payload)
    assert rejected.status_code==409,rejected.text
    payload['minimum_metrics']={'exact_match_accuracy':.7}
    accepted=client.post('/api/model-deployments/specialized-approve',json=payload)
    assert accepted.status_code==200,accepted.text
    revision=accepted.json()['revision']
    assert revision['task']=='ocr'
    assert routes_model_deployments.verified_approval_revision({**project,'source_dataset_dir':str(source)},revision['revision_id']) is not None


def test_enhancement_replacement_requires_identical_pair_target_and_split_identity(tmp_path,monkeypatch):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    from backend.engine.evaluation_history import EvaluationHistory
    from backend.api import routes_model_deployments,routes_model_comparisons
    from backend.engine.enhancement import prepare_enhancement
    from PIL import Image
    import hashlib,uuid
    source=tmp_path/'source';source.mkdir()
    for index in range(3):Image.new('RGB',(16,16),(index*60,0,0)).save(source/f'{index}.png')
    app=create_app(project_dir=str(tmp_path/'registry'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    client.post('/api/project/create',json={'name':'Exact pair holdout'})
    project=client.put('/api/project/update',json={'source_dataset_dir':str(source)}).json()
    pairs=[prepare_enhancement(source,Path(project['dataset_dir'])/f'pairs_{index}',seed=index,noise_sigma=10+index) for index in range(2)]
    models={}
    for _ in range(2):
        job=uuid.uuid4().hex;model=Path(project['models_dir'])/'enhancement'/job/'best_model.pt';model.parent.mkdir(parents=True);model.write_bytes(job.encode());models[job]=model
    monkeypatch.setattr(routes_model_deployments,'_model',lambda project,source,task,job:{'checkpoint_path':str(models[job]),'training_dataset_fingerprint':'v1:fixture'})
    jobs=list(models);history=EvaluationHistory(Path(project['reports_dir'])/'evaluations')
    def evidence(index,pair_index):
        pair=pairs[pair_index]
        return history.append({'job_id':jobs[index],'task':'enhancement','split':'test','sample_count':1,'improved':True,'output_psnr':25+index},
            {'source_dataset_path':str(source),'dataset_fingerprint':routes_model_comparisons._fingerprint(source),
             'checkpoint_sha256':hashlib.sha256(models[jobs[index]].read_bytes()).hexdigest(),'family_dataset_sha256':pair['provenance']['dataset_sha256'],'evaluation_dataset_path':pair['dataset_path']})
    baseline=evidence(0,0);candidate=evidence(1,1)
    payload={'source_dataset_path':str(source),'task':'enhancement','reviewer':'qa','reason':'Held-out paired pixels reviewed',
             'holdout_reviewed':True,'minimum_sample_count':1,'minimum_metrics':{'output_psnr':20}}
    first=client.post('/api/model-deployments/specialized-approve',json={**payload,'evaluation_id':baseline['evaluation_id']})
    assert first.status_code==200,first.text
    replacement={**payload,'evaluation_id':candidate['evaluation_id'],'incumbent_evaluation_id':baseline['evaluation_id']}
    rejected=client.post('/api/model-deployments/specialized-approve',json=replacement)
    assert rejected.status_code==409,rejected.text
    same=evidence(1,0)
    accepted=client.post('/api/model-deployments/specialized-approve',json={**replacement,'evaluation_id':same['evaluation_id']})
    assert accepted.status_code==200,accepted.text
    pair_manifest=Path(pairs[0]['dataset_path'])/'pairs.json';content=json.loads(pair_manifest.read_text());content['noise_sigma']=49;pair_manifest.write_text(json.dumps(content))
    assert routes_model_deployments.verified_approval_revision(project,accepted.json()['revision']['revision_id']) is None
