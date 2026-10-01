"""Selected target and exact execution/model handoff contracts; no SSH jobs launched."""
from types import SimpleNamespace
from pathlib import Path
import pytest


def test_catalog_advertises_all_remote_runner_families():
    from backend.engine.model_catalog import model_family_catalog
    from backend.engine.automated_trials import _RUNNERS
    families = {row['task']: row for row in model_family_catalog()['families']}
    assert set(families) == set(_RUNNERS)
    assert all(row['remote_training'] for row in families.values())


def test_task_inventory_retains_saved_model_and_prepared_input(tmp_path):
    from backend.api.routes_training_workspace import task_record
    record=SimpleNamespace(job_id='job_'+'a'*32,task='ocr',status='completed',phase='completed',
        source_dataset_path=str(tmp_path/'source'),dataset_path=str(tmp_path/'prepared'),dataset_binding={'labelset_id':'reviewed'},
        remote_profile_id='worker',current_epoch=2,total_epochs=2,error=None,
        launch_spec={'local_model_id':'a'*32},warm_start=None)
    row=task_record(record)
    assert row['execution_job_id']=='job_'+'a'*32
    assert row['model_id']=='a'*32
    assert row['dataset_path']==str(tmp_path/'prepared')
    assert row['training_provenance']['labelset_id']=='reviewed'


def test_selected_server_preflight_checks_model_without_jobs_or_downloads(tmp_path,monkeypatch):
    from backend.api import routes_training_workspace as route
    from backend.remote.profiles import ComputeProfile
    profile=ComputeProfile(id='worker',name='Worker',ssh_target='worker',ssh_port=22,remote_root='/workspace',runtime_kind='python',runtime_value='python3')
    monkeypatch.setattr(route,'get_current_project',lambda request:{'models_dir':str(tmp_path/'models'),'project_dir':str(tmp_path)})
    monkeypatch.setattr(route,'selected_profile',lambda identifier:profile)
    monkeypatch.setattr(route,'probe_target',lambda profile:{'runtime_ready':True,'checks':{'runtime_dependencies':{},'cuda_device_count':0}})
    monkeypatch.setattr(route,'model_readiness',lambda *args:pytest.fail('Local environment cannot approve a selected server'))
    value=route.readiness(route.ReadinessRequest(task='classification',model='dinov3_vits16',compute_profile_id='worker',device='cpu'),None)
    assert not value['ready'] and value['target']['id']=='worker'
    assert 'timm' in ' '.join(value['next_actions'])
    assert value['execution_verified'] is False and value['training_started'] is False


def test_specialist_portable_parent_preserves_signature_and_hash(tmp_path):
    import hashlib,json,torch
    from backend.engine.warm_start import WarmStartParent,portable_parent,restore_portable_parent
    directory=tmp_path/'rotation'/('a'*32);directory.mkdir(parents=True)
    model=torch.nn.Linear(2,2)
    payload={'task':'rotation','architecture':'small_cnn_angle_v1','width':8,'image_size':64,'model_state_dict':model.state_dict()}
    checkpoint=directory/'best_model.pt';torch.save(payload,checkpoint)
    digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    (directory/'job_receipt.json').write_text(json.dumps({'job_id':'a'*32,'task':'rotation','status':'completed','checkpoint_sha256':digest}))
    parent=WarmStartParent('a'*32,checkpoint,digest,'rotation','rotation:small_cnn_angle_v1:8:64:circle360',('upright_correction',),'v1:fixture')
    target=tmp_path/'transfer';envelope=portable_parent(parent,target)
    restored=restore_portable_parent(target,envelope,'rotation')
    assert restored.architecture==parent.architecture and restored.classes==('upright_correction',)
    assert restored.checkpoint_sha256==digest
    (target/'parent.pt').write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash'):restore_portable_parent(target,envelope,'rotation')


def test_server_preflight_does_not_mark_missing_pretrained_bytes_ready(tmp_path,monkeypatch):
    from backend.api import routes_training_workspace as route
    from backend.remote.profiles import ComputeProfile
    profile=ComputeProfile(id='worker',name='Worker',ssh_target='worker',ssh_port=22,remote_root='/workspace',runtime_kind='python',runtime_value='python3')
    monkeypatch.setattr(route,'get_current_project',lambda request:{'models_dir':str(tmp_path/'models'),'project_dir':str(tmp_path)})
    monkeypatch.setattr(route,'selected_profile',lambda identifier:profile)
    monkeypatch.setattr(route,'probe_target',lambda profile:{'runtime_ready':True,'checks':{'runtime_dependencies':dict.fromkeys(('timm','safetensors','huggingface_hub'),True),'model_dependencies':{'dinov3_vits16':True},'pretrained_weights':{},'cuda_device_count':0}})
    value=route.readiness(route.ReadinessRequest(task='classification',model='dinov3_vits16',compute_profile_id='worker'),None)
    assert not value['ready']
    assert value['weights']['state']=='missing'
    assert any('weight' in action.lower() for action in value['next_actions'])


def test_prepared_input_preflight_rejects_other_project_without_versions(tmp_path):
    from backend.engine.model_execution import resolve_training_input
    own=tmp_path/'project';(own/'datasets').mkdir(parents=True)
    source=tmp_path/'source';source.mkdir()
    foreign=tmp_path/'foreign';foreign.mkdir()
    project={'project_dir':str(own),'dataset_dir':str(own/'datasets'),'source_dataset_dir':str(source)}
    with pytest.raises(ValueError,match='active project'):
        resolve_training_input(project,'rotation',foreign)
    assert list((own/'datasets').iterdir())==[]



@pytest.mark.parametrize('family',['ocr','rotated_detection','rotation','defect_gan','enhancement'])
def test_specialist_preparation_rejects_excluded_project_image_before_copy(tmp_path,monkeypatch,family):
    from PIL import Image
    from backend.engine import grouped_dataset_views
    source=tmp_path/'source';source.mkdir()
    for index in range(3):Image.new('RGB',(24,24),(40+index*60,10,0)).save(source/f'{index}.png')
    output=tmp_path/'prepared'
    monkeypatch.setattr(grouped_dataset_views,'source_image_paths',lambda source,task:[])
    if family in {'ocr','rotated_detection'}:
        from backend.engine.prepared_family_datasets import prepare_family_dataset
        rows=[{'image':f'{index}.png','split':split,**({'text':'A'} if family=='ocr' else {'label':'defect','box':{'cx':12,'cy':12,'width':8,'height':8,'angle_deg':0}})} for index,split in enumerate(('train','val','test'))]
        execute=lambda:prepare_family_dataset(family,source,output,rows)
    elif family=='rotation':
        from backend.engine.rotation import prepare_rotation_dataset
        execute=lambda:prepare_rotation_dataset(source,output,[{'image':f'{index}.png','correction_deg':0,'split':split} for index,split in enumerate(('train','val','test'))])
    elif family=='defect_gan':
        from backend.engine.defect_gan import prepare_defect_gan_dataset
        execute=lambda:prepare_defect_gan_dataset(source,output,[{'image':f'{index}.png','bbox':[0,0,16,16],'split':'train'} for index in range(3)])
    else:
        from backend.engine.enhancement import prepare_enhancement
        execute=lambda:prepare_enhancement(source,output,image_paths=[f'{index}.png' for index in range(3)])
    with pytest.raises(ValueError,match='eligible'):execute()
    assert not output.exists()
