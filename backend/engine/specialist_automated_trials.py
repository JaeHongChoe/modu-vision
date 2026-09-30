"""Measured adapters over the real specialist fit and heldout pipelines.

Each registry exposes only parameters consumed by its trainer. Generator MMD is
a declared diagnostic; it is not a learned classifier accuracy or approval.
"""
from pathlib import Path
import time
import torch

def _measure(context, forward):
    from backend.engine.automated_trials import _sync
    timings=[]
    with torch.inference_mode():
        forward();_sync(context.device)
        for _ in range(3):
            if context.cancel_event.is_set():raise InterruptedError('Specialist measurement cancelled')
            _sync(context.device);start=time.perf_counter();forward();_sync(context.device)
            timings.append((time.perf_counter()-start)*1000)
    return sum(timings)/len(timings)

def _result(context,metrics,forward,scope='heldout_image_model_forward_only'):
    return {'metrics':metrics,'checkpoint_path':str(context.output_dir/'best_model.pt'),
            'latency_ms':_measure(context,forward),'epochs_completed':context.config['epochs'],'latency_scope':scope}

def rotation_trial(context):
    from backend.engine.rotation import train_rotation,load_rotation_model,load_rotation_manifest,RotationDataset
    config=context.config
    trained=train_rotation(context.dataset_path,context.output_dir,epochs=config['epochs'],batch_size=config['batch_size'],
        image_size=config['image_size'],width=config['width'],learning_rate=config['learning_rate'],device=context.device,
        seed=config.get('seed',17),cancel_event=context.cancel_event,on_progress=context.on_progress,warm_start=context.warm_start)
    model,payload=load_rotation_model(context.output_dir/'best_model.pt',context.device)
    heldout=RotationDataset(load_rotation_manifest(context.dataset_path),'val',payload['image_size'])
    image=heldout[0][0].unsqueeze(0).to(context.device)
    return _result(context,{key:payload['validation'][key] for key in ('loss','angular_mae_deg','within_10_deg')},lambda:model(image))

def ocr_trial(context):
    from backend.engine.ocr import train_ocr,_load_model,load_ocr_manifest,OCRDataset
    config=context.config;size=(config['image_size'],config['image_width'])
    train_ocr(context.dataset_path,context.output_dir,epochs=config['epochs'],batch_size=config['batch_size'],image_size=size,
        learning_rate=config['learning_rate'],device=context.device,seed=config.get('seed',0),cancel_event=context.cancel_event,
        on_progress=context.on_progress,warm_start=context.warm_start)
    model,payload,_=_load_model(context.output_dir/'best_model.pt',context.device)
    heldout=OCRDataset(load_ocr_manifest(context.dataset_path),split='val',image_size=size)
    image=heldout[0][0].unsqueeze(0).to(context.device)
    return _result(context,{key:payload['validation'][key] for key in ('loss','character_error_rate','exact_match_accuracy')},lambda:model(image))

def rotated_trial(context):
    from backend.engine.rotated_detection import train_rotated_detector,RotatedTrainingCancelled,_load_checkpoint,load_rotated_manifest,RotatedBoxDataset,RotatedMultiDataset,evaluate_rotated_detector
    config=context.config
    try:
        train_rotated_detector(context.dataset_path,context.output_dir,epochs=config['epochs'],batch_size=config['batch_size'],
            image_size=config['image_size'],learning_rate=config['learning_rate'],device=context.device,
            cancel_event=context.cancel_event,warm_start=context.warm_start)
    except RotatedTrainingCancelled as exc:raise InterruptedError('Rotated training cancelled') from exc
    checkpoint=context.output_dir/'best_model.pt';model,meta,_=_load_checkpoint(checkpoint,context.device)
    manifest=load_rotated_manifest(context.dataset_path)
    heldout=RotatedMultiDataset(manifest,'val',meta['image_size'],meta['max_objects']) if manifest.version==2 else RotatedBoxDataset(manifest,split='val',image_size=meta['image_size'])
    image=heldout[0][0].unsqueeze(0).to(context.device)
    metrics=evaluate_rotated_detector(checkpoint,context.dataset_path,split='val',device=context.device)
    return _result(context,{key:metrics[key] for key in ('mean_oriented_iou','mean_angle_error_deg')},lambda:model(image))

def enhancement_trial(context):
    from backend.engine.enhancement import train_enhancement,_load,load_enhancement_manifest,_Pairs,_evaluate
    config=context.config
    def progress(epoch,epochs,loss):context.on_progress({'epoch':epoch,'epochs':epochs,'loss':loss})
    train_enhancement(context.dataset_path,context.output_dir,epochs=config['epochs'],batch_size=config['batch_size'],
        learning_rate=config['learning_rate'],device=context.device,seed=config.get('seed',0),cancel_event=context.cancel_event,
        on_progress=progress,warm_start=context.warm_start)
    model,_,device=_load(context.output_dir/'best_model.pt',context.device)
    heldout=_Pairs(load_enhancement_manifest(context.dataset_path),'val')
    metrics=_evaluate(model,heldout,device);image=heldout[0][0].unsqueeze(0).to(device)
    return _result(context,{key:metrics[key] for key in ('input_mse','output_mse','input_psnr','output_psnr')},lambda:model(image),
                   'heldout_training_geometry_model_forward_only')

def gan_trial(context):
    from backend.engine.defect_gan import train_defect_gan,evaluate_defect_generator,DefectGenerator,LATENT_SIZE
    config=context.config
    train_defect_gan(context.dataset_path,context.output_dir,epochs=config['epochs'],batch_size=config['batch_size'],
        base_channels=config['base_channels'],seed=config.get('seed',0),device=context.device,cancel_event=context.cancel_event,
        on_progress=context.on_progress,warm_start=context.warm_start)
    checkpoint=context.output_dir/'best_model.pt'
    metrics=evaluate_defect_generator(checkpoint,context.dataset_path,split='val',count=8,seed=config.get('seed',0),device=context.device)
    payload=torch.load(checkpoint,map_location='cpu',weights_only=True)
    model=DefectGenerator(payload['base_channels']).to(context.device).eval();model.load_state_dict(payload['generator_state_dict'])
    latent=torch.randn(1,LATENT_SIZE,1,1,generator=torch.Generator().manual_seed(0)).to(context.device)
    return _result(context,{'rgb_statistics_mmd':metrics['rgb_statistics_mmd']},lambda:model(latent),'generator_forward_only_heldout_RGB_MMD_objective')

def register_specialist_runners():
    from backend.engine.automated_trials import register_task_runner
    register_task_runner('rotation',rotation_trial,['small_cnn_angle_v1'],family='rotation',architecture_key='architecture',
        metric_key='angular_mae_deg',search_defaults={'architectures':['small_cnn_angle_v1'],'widths':[8,16,32],
            'learning_rates':[3e-4,1e-3],'image_sizes':[64,128],'batch_sizes':[8]})
    register_task_runner('ocr',ocr_trial,['small_cnn_bigru_ctc'],family='ocr',architecture_key='architecture',metric_key='character_error_rate',
        search_defaults={'architectures':['small_cnn_bigru_ctc'],'learning_rates':[3e-4,1e-3],'image_sizes':[32],'image_widths':[64,128],'batch_sizes':[8]})
    register_task_runner('rotated_detection',rotated_trial,['rotated_detector'],family='rotated_detection',architecture_key='architecture',
        metric_key='mean_oriented_iou',direction='max',search_defaults={'architectures':['rotated_detector'],'learning_rates':[3e-4,1e-3],
            'image_sizes':[64,128],'batch_sizes':[8]})
    register_task_runner('enhancement',enhancement_trial,['rgb_residual_cnn'],family='enhancement',architecture_key='architecture',
        metric_key='output_mse',search_defaults={'architectures':['rgb_residual_cnn'],'learning_rates':[3e-4,1e-3],'batch_sizes':[4,8]})
    register_task_runner('defect_gan',gan_trial,['dcgan_defect_crop'],family='defect_gan',architecture_key='architecture',metric_key='rgb_statistics_mmd',
        search_defaults={'architectures':['dcgan_defect_crop'],'batch_sizes':[4,8],'base_channels':[8,16]})
