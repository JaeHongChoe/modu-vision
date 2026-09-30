"""Actual specialist trials, applied search fields and immutable parents."""
from pathlib import Path
import pytest
import torch


def test_public_measured_candidate_runs_real_fit_and_pins_reusable_configuration(tmp_path):
    import json
    from backend.tests.test_rotation import rotation_data
    from backend.engine.rotation import prepare_rotation_dataset
    from backend.engine.automated_trials import run_measured_candidate
    source=tmp_path/'source';rows=rotation_data(source);prepared=prepare_rotation_dataset(source,tmp_path/'owned',rows)
    result=run_measured_candidate(task='rotation',dataset_path=prepared.root,output_dir=tmp_path/'candidate',job_id='candidate',
        config_overrides={'epochs':1,'image_size':32,'width':8,'batch_size':2},device='cpu')
    assert result['status']=='completed' and result['best_metric']>=0 and result['latency_ms']>0
    checkpoint=torch.load(result['model_path'],weights_only=True)
    metadata=json.loads(Path(result['model_path']).with_name('model_meta.json').read_text())
    assert checkpoint['training_config']==metadata['training_config']==result['training_config']
    assert metadata['checkpoint_sha256']==result['checkpoint_sha256']


def test_anomaly_adapter_measures_actual_heldout_truth_with_encoder_boundary_double(tmp_path,monkeypatch):
    import numpy as np
    from PIL import Image
    from backend.tests.test_dino_synthetic_anomaly import TinyTaskModel
    from backend.engine.anomaly import dino_synthetic
    from backend.engine.automated_trials import run_automated_training
    monkeypatch.setattr(dino_synthetic,'DinoTaskModel',TinyTaskModel)
    source=tmp_path/'source';torch.set_num_threads(1)
    for index,(split,label) in enumerate((('train','good'),('train','good'),('val','good'),('val','anomaly'))):
        path=source/split/label/f'{index}.png';path.parent.mkdir(parents=True,exist_ok=True)
        Image.fromarray(np.random.default_rng(index).integers(20,230,(64,80,3),dtype=np.uint8)).save(path)
    result=run_automated_training(task='anomaly',dataset_path=source,models_dir=tmp_path/'models',
        base_config={'stride':16,'patches_per_image':2},epochs_per_trial=1,
        budget={'max_trials':2,'max_total_epochs':2,'max_seconds':60},
        search_space={'architectures':['dinov3_vits16'],'patch_sizes':[32,48],'batch_sizes':[2],'learning_rates':[.003]})
    assert result['status']=='completed',result
    assert {row['config']['patch_size'] for row in result['trials']}=={32,48}
    assert all(0<=row['metrics']['image_auroc']<=1 and row['latency_ms']>0 for row in result['trials'])
    assert all(row['latency_scope']=='native_heldout_anomaly_map_and_score' for row in result['trials'])

@pytest.mark.parametrize('task', ['ocr','rotated_detection','enhancement','defect_gan'])
def test_real_specialist_quick_run_and_immutable_parent_retrain(tmp_path,task):
    from backend.engine.automated_trials import run_automated_training
    from backend.engine.prepared_family_datasets import prepare_family_dataset
    source=tmp_path/'source';owned=tmp_path/'owned';models=tmp_path/'models'
    if task=='ocr':
        from backend.tests.test_ocr import _labeled_images
        rows=_labeled_images(source)
        prepared=prepare_family_dataset(task,source,owned,rows).root
        space={'image_sizes':[32],'image_widths':[64],'batch_sizes':[2],'learning_rates':[.003]}
    elif task=='rotated_detection':
        from backend.tests.test_rotated_detection import _write_dataset
        rows=_write_dataset(source)['samples'];(source/'rotated_boxes.json').unlink()
        prepared=prepare_family_dataset(task,source,owned,rows).root
        space={'image_sizes':[64],'batch_sizes':[2],'learning_rates':[.003]}
    elif task=='enhancement':
        import numpy as np
        from PIL import Image
        from backend.engine.enhancement import prepare_enhancement
        source.mkdir()
        for index in range(6):
            pixels=np.random.default_rng(index).integers(30,210,(40,48,3),dtype=np.uint8)
            Image.fromarray(pixels).save(source/f'{index}.png')
        prepare_enhancement(source,owned);prepared=owned
        space={'batch_sizes':[2],'learning_rates':[.003]}
    else:
        from backend.tests.test_defect_gan import _source
        from backend.engine.defect_gan import prepare_defect_gan_dataset
        source,rows=_source(tmp_path)
        rows[2]['split']='val';rows[3]['split']='test'
        prepare_defect_gan_dataset(source,owned,rows);prepared=owned
        space={'batch_sizes':[2],'base_channels':[8]}
    torch.set_num_threads(1)
    before={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()}
    run=run_automated_training(task=task,dataset_path=prepared,source_dataset_path=source,models_dir=models,
        mode='quick',search_space=space,epochs_per_trial=1,budget={'max_trials':1,'max_total_epochs':1,'max_seconds':60})
    assert run['status']=='completed',run
    parent=run['winner'];checkpoint=Path(parent['checkpoint_path']);parent_bytes=checkpoint.read_bytes()
    assert parent['latency_ms']>0 and parent['metrics']
    assert checkpoint.parent.parent.name==task
    reused=run_automated_training(task=task,dataset_path=prepared,source_dataset_path=source,models_dir=models,
        mode='fast_retrain',parent_job_id=parent['trial_id'],epochs_per_trial=1)
    assert reused['status']=='completed',reused
    assert reused['winner']['config']==parent['config']
    assert checkpoint.read_bytes()==parent_bytes
    assert before=={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()}

def test_registered_specialists_declare_only_applied_search_dimensions():
    from backend.engine.automated_trials import _RUNNERS
    for task in ('rotation','ocr','rotated_detection','enhancement','defect_gan','anomaly'):
        assert task in _RUNNERS, task
        spec=_RUNNERS[task]
        assert spec.search_defaults and 'augmentation_profiles' not in spec.search_defaults
        assert 'weight_decays' not in spec.search_defaults
    assert _RUNNERS['rotation'].search_defaults['widths']==[8,16,32]
    assert 'learning_rates' not in _RUNNERS['defect_gan'].search_defaults

def test_rotation_search_fits_distinct_structures_and_fast_retrain_reuses_real_parent(tmp_path):
    from backend.tests.test_rotation import rotation_data
    from backend.engine.rotation import prepare_rotation_dataset
    from backend.engine.automated_trials import run_automated_training
    source=tmp_path/'source';rows=rotation_data(source)
    prepared=prepare_rotation_dataset(source,tmp_path/'owned',rows)
    torch.set_num_threads(1)
    result=run_automated_training(task='rotation',dataset_path=prepared.root,source_dataset_path=source,models_dir=tmp_path/'models',
        epochs_per_trial=1,budget={'max_trials':2,'max_total_epochs':2,'max_seconds':60},
        search_space={'widths':[8,16],'image_sizes':[32],'batch_sizes':[2],'learning_rates':[.003]})
    assert result['status']=='completed',result
    assert {trial['config']['width'] for trial in result['trials']}=={8,16}
    assert all(t['status']=='completed' and t['metrics']['angular_mae_deg']>=0 and t['latency_ms']>0 for t in result['trials'])
    parent=result['winner'];before=Path(parent['checkpoint_path']).read_bytes()
    assert Path(parent['checkpoint_path']).parent.parent.name=='rotation'
    reused=run_automated_training(task='rotation',dataset_path=prepared.root,source_dataset_path=source,models_dir=tmp_path/'models',
        mode='fast_retrain',parent_job_id=parent['trial_id'],epochs_per_trial=1)
    assert reused['status']=='completed',reused
    assert reused['winner']['config']['width']==parent['config']['width']
    assert reused['configuration_parent']['parent_checkpoint_sha256']==parent['checkpoint_sha256']
    assert Path(parent['checkpoint_path']).read_bytes()==before
    with pytest.raises(ValueError,match='Unknown.*dimension'):
        run_automated_training(task='rotation',dataset_path=prepared.root,models_dir=tmp_path/'unsupported',
            search_space={'augmentation_profiles':['industrial']})
