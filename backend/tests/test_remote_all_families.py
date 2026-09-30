import hashlib
import json
import threading
import pytest
import torch
from backend.remote.snapshot import build_snapshot
from backend.remote.worker import run_train,_read_train_spec


def _run_spec(tmp_path,task,source):
    snapshot=build_snapshot(source,tmp_path/'snapshot',threading.Event())
    run=tmp_path/'run';run.mkdir();(run/'snapshot.tar.gz').write_bytes(snapshot.archive_path.read_bytes())
    spec=run/'spec.json';spec.write_text(json.dumps({'protocol_version':1,'operation':'train','job_id':'job_owned',
        'task':task,'preset':'fast','device':'cpu','snapshot_archive':'snapshot.tar.gz',
        'input_manifest_sha256':snapshot.manifest_sha256,'config_overrides':{'epochs':1,'image_size':64,'batch_size':4}}))
    return run,spec


@pytest.mark.parametrize('task',['patch_classification','rotation','ocr','rotated_detection','enhancement','defect_gan'])
def test_worker_protocol_accepts_every_real_specialist_family(tmp_path,task):
    source=tmp_path/'source';source.mkdir();(source/'sample').write_bytes(b'fixture')
    run,spec=_run_spec(tmp_path,task,source)
    assert _read_train_spec(spec,run)['task']==task


def test_remote_rotation_runs_real_cpu_training_and_hashes_artifacts(tmp_path):
    from backend.tests.test_rotation import rotation_data
    from backend.engine.rotation import write_rotation_manifest
    source=tmp_path/'source';write_rotation_manifest(source,rotation_data(source))
    run,spec=_run_spec(tmp_path,'rotation',source);torch.set_num_threads(1)
    result=run_train(spec)
    assert result['status']=='completed',result.get('error')
    _infer_completed_worker(tmp_path,run,'rotation',source/'test_90_0.png')
    metadata=json.loads((run/'outputs'/'model_meta.json').read_text())
    assert metadata['task']=='rotation'
    assert metadata['training_config']['epochs']==1
    manifest=json.loads((run/'artifacts.json').read_text())
    for row in manifest['artifacts']:
        assert hashlib.sha256((run/row['path']).read_bytes()).hexdigest()==row['sha256']


def test_remote_foundation_labeling_reports_missing_prerequisite_without_fake_output(tmp_path):
    from backend.remote.worker import run_label
    from PIL import Image
    source=tmp_path/'source';source.mkdir();Image.new('RGB',(16,16)).save(source/'image.png')
    run,spec=_run_spec(tmp_path,'labeling',source)
    value=json.loads(spec.read_text());value.update(operation='label',labeling={'setup':{},'points':[{'x':2,'y':2,'label':1}]})
    spec.write_text(json.dumps(value))
    result=run_label(spec)
    assert result['status']=='failed'
    assert 'SAM2' in result['error']
    assert not (run/'artifacts.json').exists()


def test_remote_worker_trains_one_actual_model_on_two_cpu_ranks(tmp_path):
    from PIL import Image
    source=tmp_path/'source'
    for split in ('train','val'):
        for label,color in (('NG',(200,20,20)),('OK',(20,200,20))):
            folder=source/split/label;folder.mkdir(parents=True)
            for index in range(2):Image.new('RGB',(64,64),color).save(folder/f'{index}.png')
    run,spec=_run_spec(tmp_path,'classification',source)
    values=json.loads(spec.read_text());values['distributed']={'processes':2};values['config_overrides'].update(backbone='resnet18',pretrained=False,batch_size=2,augmentation_profile='none')
    spec.write_text(json.dumps(values))
    result=run_train(spec)
    assert result['status']=='completed',result.get('error')
    payload=torch.load(run/'outputs'/'best_model.pt',map_location='cpu',weights_only=True)
    assert all(not key.startswith('module.') for key in payload['model_state_dict'])
    assert result['distributed']=={'processes':2,'backend':'gloo'}
    assert (run/'distributed_progress'/'status.json').exists()


@pytest.mark.parametrize('task',['patch_classification','ocr','rotated_detection','enhancement','defect_gan'])
def test_every_added_family_fits_actual_cpu_worker_and_returns_hashed_artifacts(tmp_path,task):
    from backend.engine.prepared_family_datasets import prepare_family_dataset
    from PIL import Image
    import numpy as np
    source=tmp_path/'source';owned=tmp_path/'owned';config={'epochs':1,'batch_size':2}
    if task=='patch_classification':
        from backend.tests.test_patch_classification import _manifest
        source.mkdir();_manifest(source);owned=source
        config.update(image_size=64,backbone='resnet18',pretrained=False,augmentation_profile='none')
    elif task=='ocr':
        from backend.tests.test_ocr import _labeled_images
        owned=prepare_family_dataset(task,source,owned,_labeled_images(source)).root
        config.update(image_size=32,image_width=64)
    elif task=='rotated_detection':
        from backend.tests.test_rotated_detection import _write_dataset
        rows=_write_dataset(source)['samples'];(source/'rotated_boxes.json').unlink()
        owned=prepare_family_dataset(task,source,owned,rows).root;config.update(image_size=64)
    elif task=='enhancement':
        from backend.engine.enhancement import prepare_enhancement
        source.mkdir()
        for index in range(6):Image.fromarray(np.random.default_rng(index).integers(30,210,(40,48,3),dtype=np.uint8)).save(source/f'{index}.png')
        prepare_enhancement(source,owned)
    else:
        from backend.tests.test_defect_gan import _source
        from backend.engine.defect_gan import prepare_defect_gan_dataset
        source,rows=_source(tmp_path);rows[2]['split']='val';rows[3]['split']='test'
        prepare_defect_gan_dataset(source,owned,rows);config.update(base_channels=8)
    torch.set_num_threads(1)
    original={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()}
    run,spec=_run_spec(tmp_path,task,owned)
    values=json.loads(spec.read_text());values['config_overrides']=config
    if owned!=source:
        snapshot=build_snapshot(source,tmp_path/'source_snapshot',threading.Event())
        (run/'source.tar.gz').write_bytes(snapshot.archive_path.read_bytes())
        values['source_snapshot']={'archive':'source.tar.gz','canonical_root':str(source),'manifest_sha256':snapshot.manifest_sha256}
        moved=tmp_path/'original_unmounted';source.rename(moved)
    else:moved=source
    spec.write_text(json.dumps(values));result=run_train(spec)
    assert result['status']=='completed',result
    metadata=json.loads((run/'outputs/model_meta.json').read_text());assert metadata['task']==task
    for row in json.loads((run/'artifacts.json').read_text())['artifacts']:
        assert hashlib.sha256((run/row['path']).read_bytes()).hexdigest()==row['sha256']
    assert original=={p.relative_to(moved).as_posix():p.read_bytes() for p in moved.rglob('*') if p.is_file()}
    _infer_completed_worker(tmp_path,run,task,next(moved.rglob('*.png')))


def test_real_distributed_cancel_terminates_only_its_worker_group(tmp_path):
    import subprocess,sys,time,psutil
    from PIL import Image
    source=tmp_path/'source'
    for split in ('train','val'):
        for label,color in (('NG',(180,30,30)),('OK',(30,180,30))):
            folder=source/split/label;folder.mkdir(parents=True)
            for index in range(2):Image.new('RGB',(64,64),color).save(folder/f'{index}.png')
    run,spec=_run_spec(tmp_path,'classification',source)
    values=json.loads(spec.read_text());values['distributed']={'processes':2};values['config_overrides'].update(epochs=50,backbone='resnet18',pretrained=False,batch_size=2,augmentation_profile='none');spec.write_text(json.dumps(values))
    unrelated=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    progress_seen=threading.Event()
    def request_cancel():
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if (run/'distributed_progress/status.json').exists():
                progress_seen.set();(run/'cancel').touch();return
            time.sleep(.02)
        (run/'cancel').touch()
    requester=threading.Thread(target=request_cancel,daemon=True);requester.start()
    try:
        result=run_train(spec);requester.join(1)
        assert progress_seen.is_set(),result
        assert result['status']=='aborted',result
        assert unrelated.poll() is None
        owned=[]
        for process in psutil.process_iter(['pid','cmdline']):
            command=process.info['cmdline'] or []
            if str(spec) in command:owned.append(process.info['pid'])
        assert not owned,owned
    finally:
        unrelated.terminate();unrelated.wait(timeout=5)


def _infer_completed_worker(tmp_path,run,task,image):
    from backend.remote.worker import run_infer
    runs=tmp_path/'runs';runs.mkdir(exist_ok=True)
    target=runs/'job_owned';run.rename(target)
    operation=runs/('op_'+task);(operation/'inputs').mkdir(parents=True)
    transferred=operation/'inputs/image.png';transferred.write_bytes(image.read_bytes())
    training=json.loads((target/'spec.json').read_text())
    spec={'protocol_version':1,'operation':'infer','job_id':'job_owned','task':task,'device':'cpu',
          'image_path':'inputs/image.png','image_sha256':hashlib.sha256(transferred.read_bytes()).hexdigest(),
          'input_manifest_sha256':training['input_manifest_sha256'],'image_id':'selected','threshold':.5}
    path=operation/'spec.json';path.write_text(json.dumps(spec));result=run_infer(path)
    assert result['status']=='completed',result
    payload=json.loads((operation/'outputs/result.json').read_text())
    assert payload['image_sha256']==spec['image_sha256']
    for row in json.loads((operation/'artifacts.json').read_text())['artifacts']:
        assert hashlib.sha256((operation/row['path']).read_bytes()).hexdigest()==row['sha256']
    if task=='defect_gan':assert payload['predictions']['automatically_approved'] is False
    # Existing assertions use the original local path after this helper.
    target.rename(run)
