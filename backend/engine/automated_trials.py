"""Budgeted, measured training trials with immutable inputs and extensible runners.

No candidate metric or latency is fabricated. A winner always names a completed
checkpoint measured on validation data. GPU deadlines here are cooperative training
budgets; they are not hard inference deadlines.
"""
from __future__ import annotations

from contextvars import copy_context
from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import sha256
import itertools
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

import torch


@dataclass
class TrialContext:
    task: str
    dataset_path: Path
    output_dir: Path
    config: dict
    preset: str
    device: str
    warm_start: object
    cancel_event: threading.Event
    on_progress: object


@dataclass(frozen=True)
class TaskRunner:
    run: object
    architectures: tuple[str, ...]
    metric_key: str = 'val_loss'
    direction: str = 'min'
    family: str | None = None
    architecture_key: str | None = None
    search_defaults: dict | None = None


_RUNNERS = {}


def validate_trial_controls(task,preset,options):
    from backend.engine.model_backbones import validate_training_controls
    validate_training_controls(task,preset,options)
    if options.get('resume_checkpoint'):raise ValueError('Use direct training exact resume; measured candidates need new outputs')
    recipe=options.get('recipe') or {}
    if task=='ocr' and recipe.get('mode','crop')!='crop':
        raise ValueError('AutoDL multiline OCR latency measurement is unsupported; use its direct workbench')
    if task=='rotated_detection' and recipe.get('adapter','fixed_slot_cnn')!='fixed_slot_cnn':
        raise ValueError('AutoDL native YOLO OBB measurement is unsupported; use its direct workbench')


def register_task_runner(task, runner, architectures, *, metric_key='val_loss', direction='min',
                         family=None, architecture_key=None, search_defaults=None):
    """Register a real adapter returning {metrics,latency_ms,checkpoint_path}.

    Specialists can provide heldout CER/angle/MSE objectives without modifying
    the trial coordinator. Metrics from different tasks are never compared.
    """
    if not callable(runner) or not architectures or direction not in ('min', 'max'):
        raise ValueError('Invalid automated training task runner')
    _RUNNERS[task] = TaskRunner(runner, tuple(architectures), metric_key, direction,
                               family, architecture_key, search_defaults)


def _write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp'); temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8'); temporary.replace(path)


def _fingerprint(source):
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    return fingerprint_dataset(source)


def _sync(device):
    if str(device).startswith('cuda'): torch.cuda.synchronize(device)
    elif str(device) == 'mps': torch.mps.synchronize()


def _latency(checkpoint, context):
    from backend.engine.exporter import load_checkpoint_and_reconstruct_model
    from backend.engine.dataset_loaders import ClassificationDataset, SegmentationDataset
    from backend.engine.grouped_dataset_views import load_manifest_dataset
    model, meta, anomaly = load_checkpoint_and_reconstruct_model(checkpoint)
    model.to(context.device).eval(); size = tuple(meta['image_size'])
    if context.task == 'patch_classification':
        from backend.engine.patch_classification import PatchClassificationDataset
        dataset = PatchClassificationDataset(context.dataset_path, split='val', image_size=size)
    elif context.task == 'classification':
        dataset = ClassificationDataset(context.dataset_path, split='val', image_size=size)
    elif context.task == 'segmentation':
        dataset = load_manifest_dataset('segmentation', context.dataset_path, 'val', image_size=size)
        if dataset is None: dataset = SegmentationDataset(context.dataset_path, split='val', image_size=size)
    elif context.task == 'anomaly':
        from backend.engine.dataset_loaders import AnomalyDataset
        native=meta.get('detector_type')=='dino_synthetic'
        size=None if native else size
        dataset=load_manifest_dataset('anomaly',context.dataset_path,'val',image_size=size)
        if dataset is None:dataset=AnomalyDataset(context.dataset_path,split='val',image_size=size,max_dim=0 if native else 512)
        model=anomaly.to(context.device).eval()
    else:
        from backend.engine.trainer import _build_detection_datasets
        _, dataset = _build_detection_datasets(context.dataset_path, None, size)
    if not len(dataset): raise ValueError('Measured trial latency needs actual heldout image input')
    image = dataset[0][0].to(context.device)
    if context.task=='anomaly' and meta.get('detector_type')=='dino_synthetic':image=image.cpu()
    def forward():
        if context.task == 'anomaly': return model.predict_anomaly_map(image)
        return model([image]) if context.task == 'detection' else model(image.unsqueeze(0))
    timings = []
    with torch.no_grad():
        forward(); _sync(context.device)
        for _ in range(3):
            if context.cancel_event.is_set(): raise InterruptedError('Automated training cancelled during measurement')
            _sync(context.device); start = time.perf_counter(); forward(); _sync(context.device)
            timings.append((time.perf_counter() - start) * 1000)
    return sum(timings) / len(timings)


def _supervised_trial(context):
    from backend.engine.trainer import UnifiedAutoMLTrainer, TrainingCallback
    class Callback(TrainingCallback):
        def on_step_end(self, step, total_steps, current_loss, epoch):
            if context.cancel_event.is_set(): trainer.abort()
            context.on_progress({'epoch': epoch + 1, 'batch': step + 1, 'batches': total_steps, 'loss': current_loss})
        def on_epoch_end(self, epoch, total_epochs, train_loss, val_loss, lr, metrics):
            self.epochs = epoch + 1
            context.on_progress({'epoch': epoch + 1, 'epochs': total_epochs, 'loss': train_loss, 'val_loss': val_loss, 'learning_rate': lr})
        epochs = 0
    callback = Callback()
    trainer = UnifiedAutoMLTrainer(task=context.task, dataset_path=context.dataset_path, output_dir=context.output_dir,
        preset=context.preset, device=context.device, config_overrides=context.config, warm_start=context.warm_start, callback=callback)
    stop = threading.Event()
    def monitor():
        while not stop.wait(.05):
            if context.cancel_event.is_set(): trainer.abort(); return
    thread = threading.Thread(target=monitor, daemon=True); thread.start()
    try:
        result = trainer.train(context.output_dir.name)
        if result['status'] != 'completed' or context.cancel_event.is_set(): raise InterruptedError('Automated training child cancelled')
        checkpoint = Path(result['model_path'])
        payload = torch.load(checkpoint, map_location='cpu', weights_only=True)
        metrics={'val_loss':float(payload['best_metric'])}
        if context.task=='anomaly':
            auroc=payload.get('validation',{}).get('image_auroc')
            if not isinstance(auroc,(float,int)) or not math.isfinite(auroc):
                raise ValueError('Anomaly trial selection requires actual normal and defect heldout truth with finite image AUROC')
            metrics={'image_auroc':float(auroc)}
        return {'metrics': metrics, 'latency_ms': _latency(checkpoint, context),
                'checkpoint_path': str(checkpoint), 'epochs_completed': callback.epochs,
                'latency_scope': 'native_heldout_anomaly_map_and_score' if context.task=='anomaly' else 'heldout_image_model_forward_only'}
    finally: stop.set(); thread.join(timeout=1)


for _task in ('classification', 'patch_classification', 'segmentation'):
    register_task_runner(_task, _supervised_trial, ('dinov3_vits16', 'dinov3_vitb16'))
register_task_runner('detection', _supervised_trial, ('yolo26n', 'yolo26s'))
register_task_runner('anomaly',_supervised_trial,('dinov3_vits16','dinov3_vitb16'),architecture_key='anomaly_backbone',
    metric_key='image_auroc',direction='max',search_defaults={'architectures':['dinov3_vits16','dinov3_vitb16'],
        'learning_rates':[3e-4,1e-3],'patch_sizes':[256,384],'batch_sizes':[8]})


def _space(task, search_space, base, mode):
    spec = _RUNNERS[task]; supplied = search_space or {}
    default_space = {'architectures': list(spec.architectures), 'learning_rates': [3e-4, 1e-3],
                     'weight_decays': [1e-4, .01], 'image_sizes': [256], 'batch_sizes': [8],
                     'augmentation_profiles': ['none', 'industrial']}
    configured = spec.search_defaults if spec.search_defaults is not None else default_space
    allowed = set(configured)
    if set(supplied) - allowed: raise ValueError('Unknown automated training search dimension')
    names = tuple(configured); defaults = [configured[name] for name in names]
    key_map = {'architectures': spec.architecture_key or ('model_name' if task == 'segmentation' else 'backbone'),
               'learning_rates': 'learning_rate', 'weight_decays': 'weight_decay', 'image_sizes': 'image_size',
               'batch_sizes': 'batch_size', 'augmentation_profiles': 'augmentation_profile',
               'widths': 'width', 'image_widths': 'image_width', 'base_channels': 'base_channels','patch_sizes':'patch_size'}
    if allowed - set(key_map): raise ValueError('Invalid registered search dimension')
    keys = tuple(key_map[name] for name in names)
    if mode in ('quick', 'fast_retrain'):
        defaults = [[base.get(key, value[0])] for key, value in zip(keys, defaults)]
    values = [supplied.get(name, value) for name, value in zip(names, defaults)]
    if 'image_sizes' in names and not spec.family and mode in ('quick','fast_retrain'):
        index=names.index('image_sizes')
        values[index]=[tuple(value) if isinstance(value,(list,tuple)) else value for value in values[index]]
    for name, choices in zip(names, values):
        if not isinstance(choices, list) or not 1 <= len(choices) <= 16: raise ValueError('Search dimensions must be nonempty bounded lists')
    choices = dict(zip(names, values))
    if any(name not in spec.architectures for name in choices.get('architectures', [])): raise ValueError('Search architecture must be a compatible pretrained DINOv3/YOLO model or registered specialist')
    for value in choices.get('learning_rates', []):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 < value <= 1: raise ValueError('Search learning rates must be positive and bounded')
    for value in choices.get('weight_decays', []):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1: raise ValueError('Search weight decay must be finite in [0,1]')
    minimum_size=16 if spec.family else 32
    for value in choices.get('image_sizes', []):
        dimensions=value if isinstance(value,tuple) and not spec.family and mode in ('quick','fast_retrain') else (value,)
        if len(dimensions) not in (1,2) or any(type(n)is not int or not minimum_size<=n<=2048 or n%16 for n in dimensions):
            raise ValueError('Search image sizes must be bounded multiples of 16')
    if any(type(n) is not int or not 1 <= n <= 128 for n in choices.get('batch_sizes', [])): raise ValueError('Search batch sizes must be positive and bounded')
    if any(p not in ('none', 'photometric', 'industrial') for p in choices.get('augmentation_profiles', [])): raise ValueError('Invalid search augmentation profile')
    for name in ('widths', 'image_widths', 'base_channels','patch_sizes'):
        if any(type(n) is not int or not 8 <= n <= 1024 for n in choices.get(name, [])): raise ValueError('Search structure widths must be positive and bounded')
    # Diagonal candidates cover different structures/settings early; exhaustive
    # product fills subsequent unique combinations only within the budget.
    diagonals = [tuple(v[i % len(v)] for v in values) for i in range(max(map(len, values)))]
    seen = set()
    for choice in itertools.chain(diagonals, itertools.product(*values)):
        if choice in seen: continue
        seen.add(choice); config={**base, **dict(zip(keys, choice))}
        if isinstance(config.get('image_size'),tuple):config['image_size']=list(config['image_size'])
        yield config


def run_measured_candidate(*, task, dataset_path, output_dir, job_id, config_overrides=None, preset='fast',
                           device='cpu', cancel_event=None, on_progress=None, warm_start=None):
    """Fit one real candidate; caller owns compute allocation, lineage and receipt.

    Shared by bounded local searches and remote family dispatch. This function
    never creates a nested scheduler lease or substitutes a different device.
    """
    if task not in _RUNNERS:raise ValueError('No measured candidate runner registered for this task')
    from backend.engine.runtime_device import resolve_runtime_device
    dataset=Path(dataset_path).expanduser().resolve();output=Path(output_dir).expanduser()
    if output.is_symlink() or output.name!=job_id or output.resolve().is_relative_to(dataset):
        raise ValueError('Measured candidate needs its own output outside training data')
    options=dict(config_overrides or {})
    if task=='anomaly':options.setdefault('anomaly_method','dino_synthetic')
    if task=='ocr':
        size=options.get('image_size',options.get('image_height',32))
        if isinstance(size,(tuple,list)):options['image_size'],options['image_width']=size
        else:options['image_size']=size
    epochs=options.get('epochs',1)
    if type(epochs)is not int or not 1<=epochs<=500:raise ValueError('Measured candidate epochs must be bounded')
    config={**next(_space(task,None,options,'quick')),'epochs':epochs}
    event=cancel_event or threading.Event()
    if event.is_set():raise InterruptedError('Measured candidate cancelled')
    context=TrialContext(task,dataset,output,config,preset,str(resolve_runtime_device(device)),warm_start,event,on_progress or (lambda values:None))
    spec=_RUNNERS[task];result=spec.run(context)
    if event.is_set():raise InterruptedError('Measured candidate cancelled')
    checkpoint=Path(result['checkpoint_path'])
    if checkpoint.is_symlink() or not checkpoint.is_file() or checkpoint.resolve()!=(output/'best_model.pt').resolve():
        raise ValueError('Measured checkpoint escaped its candidate output')
    objective=result['metrics'].get(spec.metric_key);latency=result['latency_ms']
    if not isinstance(objective,(int,float)) or not math.isfinite(objective) or not isinstance(latency,(int,float)) or not math.isfinite(latency) or latency<=0:
        raise ValueError('Candidate needs finite measured validation and latency')
    payload=torch.load(checkpoint,map_location='cpu',weights_only=True);payload['training_config']=config
    measurement={key:result.get(key) for key in ('metrics','latency_ms','epochs_completed','latency_scope')}
    payload['measured_candidate']=measurement
    temporary=checkpoint.with_suffix('.tmp');torch.save(payload,temporary);temporary.replace(checkpoint)
    metadata_path=checkpoint.with_name('model_meta.json');metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
    digest=sha256(checkpoint.read_bytes()).hexdigest();metadata.update(training_config=config,checkpoint_sha256=digest,dataset_path=str(dataset),measured_candidate=measurement);_write(metadata_path,metadata)
    return {**result,'status':'completed','job_id':job_id,'task':task,'model_path':str(checkpoint),
            'checkpoint_sha256':digest,'best_metric':float(objective),'metric_key':spec.metric_key,
            'training_config':config,'metadata':metadata}


def validated_budget(budget,epochs_per_trial):
    """One pre-submission budget gate shared by GUI, CLI and external REST."""
    limits = {'max_trials': 4, 'max_total_epochs': 8, 'max_seconds': 600, **(budget or {})}
    if set(limits) - {'max_trials', 'max_total_epochs', 'max_seconds', 'max_memory_mb'}: raise ValueError('Unknown training budget field')
    if type(limits['max_trials']) is not int or not 1 <= limits['max_trials'] <= 32 or type(limits['max_total_epochs']) is not int or not 1 <= limits['max_total_epochs'] <= 512 or isinstance(limits['max_seconds'], bool) or not isinstance(limits['max_seconds'], (int, float)) or not math.isfinite(limits['max_seconds']) or not 0 < limits['max_seconds'] <= 86400:
        raise ValueError('Training budget must be positive and bounded')
    memory=limits.get('max_memory_mb')
    if memory is not None and (type(memory) is not int or not 1 <= memory <= 1048576):raise ValueError('Memory budget must be a positive bounded integer in MB')
    if type(epochs_per_trial) is not int or not 1 <= epochs_per_trial <= limits['max_total_epochs']: raise ValueError('Epochs per trial must fit the total epoch budget')
    return limits


def run_automated_training(*, task, dataset_path, models_dir, preset='fast', device='cpu', mode='search',
        budget=None, search_space=None, base_config=None, epochs_per_trial=2, parent_job_id=None,
        cancel_event=None, on_progress=None, search_id=None, training_binding=None, source_dataset_path=None,owner_instance=None,
        compute_profile_id=None,remote_owner=None,seed=0,reuse_search_id=None):
    if task not in _RUNNERS: raise ValueError('No automated training runner registered for this task')
    if mode not in ('quick', 'search', 'fast_retrain'): raise ValueError('Unknown automated training mode')
    limits=validated_budget(budget,epochs_per_trial)
    if preset not in ('fast', 'precision'): raise ValueError('Invalid automated training preset')
    if type(seed)is not int or not 0<=seed<=2147483647:raise ValueError('Search seed must be a bounded nonnegative integer')
    validate_trial_controls(task,preset,base_config or {})
    source = Path(dataset_path).expanduser().resolve(); models = Path(models_dir).expanduser().resolve()
    canonical = Path(source_dataset_path).expanduser().resolve() if source_dataset_path else source
    spec = _RUNNERS[task]; architecture_key = spec.architecture_key or ('model_name' if task == 'segmentation' else 'backbone')
    if not source.is_dir() or models == source or models.is_relative_to(source): raise ValueError('Trial model output must be outside source data')
    from backend.engine.runtime_device import resolve_runtime_device
    remote_profile = None
    if compute_profile_id:
        from backend.remote.profiles import get_profile_store
        remote_profile = get_profile_store().get(compute_profile_id)
        if remote_profile is None: raise ValueError('Selected remote compute profile is unavailable')
        from backend.engine.remote_automated_trials import validate_remote_search
        remote_profile = validate_remote_search(remote_profile,task,preset,device,base_config or {},search_space or {},limits,warm_start=bool(parent_job_id))
    else: device = str(resolve_runtime_device(device))
    event = cancel_event or threading.Event()
    base = dict(base_config or {}); parent = None; configuration_parent = None
    if task=='anomaly':base.setdefault('anomaly_method','dino_synthetic')
    if mode == 'fast_retrain' and not parent_job_id: raise ValueError('Fast retraining requires a compatible completed parent')
    if parent_job_id:
        from backend.engine.warm_start import resolve_warm_start_parent, architecture_for
        from backend.engine.checkpoint_paths import is_job_id
        if not spec.family and not is_job_id(parent_job_id): raise ValueError('Invalid automated training parent job ID')
        if spec.family and (len(parent_job_id)!=32 or any(c not in '0123456789abcdef' for c in parent_job_id)): raise ValueError('Invalid automated training parent job ID')
        directory = models / spec.family / parent_job_id if spec.family else models / parent_job_id
        if directory.is_symlink(): raise ValueError('Parent directory cannot be linked')
        metadata = json.loads((directory / 'model_meta.json').read_text(encoding='utf-8'))
        config = metadata.get('training_config')
        if not isinstance(config, dict): raise ValueError('Parent has no recorded reusable training configuration')
        if metadata.get('automated_training') or spec.family:
            payload = torch.load(directory / 'best_model.pt', map_location='cpu', weights_only=True)
            if payload.get('training_config') is not None and payload['training_config'] != config:
                raise ValueError('Parent reusable configuration differs from its immutable checkpoint')
        base = {**config, **base}; base[architecture_key] = config.get(architecture_key, metadata.get(architecture_key, spec.architectures[0]))
        if spec.family:
            from backend.engine.specialized_warm_start import resolve_family_parent
            parent_options = dict(base)
            if task == 'ocr':
                height = base.get('image_size', base.get('image_height', 32))
                if isinstance(height, (list, tuple)):
                    base['image_size'], base['image_width'] = height
                else:base['image_size']=height
                parent_options['image_size'] = (base.get('image_size', 32), base.get('image_width', 128))
            parent = resolve_family_parent(models, parent_job_id, task, canonical, source, parent_options)
        else:
            parent = resolve_warm_start_parent(parent_job_id, models, canonical, task, architecture_for(task, preset, base))
        configuration_parent = {'parent_job_id': parent_job_id, 'configuration_sha256': sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(), 'parent_checkpoint_sha256': parent.checkpoint_sha256}
    if parent is not None and search_space and 'architectures' in search_space and any(a != base.get(architecture_key) for a in search_space['architectures']):
        raise ValueError('Parent weight reuse requires its exact compatible architecture')
    if parent is not None:
        search_space = {**(search_space or {}), 'architectures': [base[architecture_key]]}
    candidates = _space(task, search_space, base, mode)
    # Validate all dimensions before creating a search journal or output.
    first = next(candidates)
    search_id = search_id or uuid.uuid4().hex
    if len(search_id) != 32 or any(c not in '0123456789abcdef' for c in search_id): raise ValueError('Invalid search identity')
    output = models / 'automated_training' / search_id
    if output.exists() and (output / 'search.json').exists(): raise ValueError('Search identity already exists')
    record = {'search_id': search_id, 'task': task, 'status': 'running', 'mode': mode, 'preset': preset,
        'owner_pid':os.getpid(),'owner_kind':'api' if owner_instance else 'engine','owner_instance':owner_instance or uuid.uuid4().hex,
        'device': device, 'budget': limits, 'memory_scope': 'cuda_process_allocated' if str(device).startswith('cuda') else 'mps_process_allocated' if str(device)=='mps' else 'backend_process_rss', 'epochs_per_trial': epochs_per_trial, 'trials': [], 'winner': None,
        'compute_profile_id':compute_profile_id,'remote_owner':remote_owner,
        'seed':seed,'reuse_search_id':reuse_search_id,
        'dataset_path': str(source), 'source_dataset_path': str(canonical),
        'dataset_fingerprint': _fingerprint(source), 'source_dataset_fingerprint': _fingerprint(canonical), 'training_provenance': training_binding,
        'configuration_parent': configuration_parent, 'created_at': time.time(), 'stop_reason': None,
        'objective': base.pop('objective', 'val_loss'), 'latency_weight': base.pop('latency_weight', 0.)}
    if remote_profile:record['memory_scope']='remote_worker_allocation_limit'
    if record['objective'] not in ('val_loss', 'loss_latency') or not isinstance(record['latency_weight'], (int, float)) or not math.isfinite(record['latency_weight']) or record['latency_weight'] < 0:
        raise ValueError('Invalid measured training objective or latency weight')
    reusable={}
    def config_key(config):return json.dumps(config,sort_keys=True,separators=(',',':'))
    if reuse_search_id:
        previous=read_search(models,reuse_search_id)
        keys=('task','preset','device','compute_profile_id','remote_owner','seed','dataset_fingerprint','source_dataset_fingerprint',
              'training_provenance','configuration_parent','epochs_per_trial','objective','latency_weight')
        if any(previous.get(key)!=record.get(key) for key in keys):
            raise ValueError('Completed trial reuse identity differs: snapshot, seed, objective, recipe, or compute target changed')
        for trial in previous.get('trials',[]):
            if trial.get('status')!='completed':continue
            checkpoint=Path(trial['checkpoint_path'])
            if (checkpoint.is_symlink() or not checkpoint.resolve().is_relative_to(models) or not checkpoint.is_file()
                    or sha256(checkpoint.read_bytes()).hexdigest()!=trial['checkpoint_sha256']):
                raise ValueError('Reusable measured checkpoint hash changed')
            reusable[config_key(trial['config'])]=trial
    _write(output / 'search.json', record); start = time.monotonic(); consumed = 0
    def persist():
        record.update(epochs_consumed=consumed, duration_seconds=time.monotonic()-start, memory_used_mb=memory_used_mb())
        _write(output / 'search.json', record)
        if on_progress: on_progress(json.loads(json.dumps(record)))
    def memory_used_mb():
        if remote_profile:return None # No local-coordinator RSS is presented as worker memory.
        if str(device).startswith('cuda'):return torch.cuda.memory_allocated(device)/1048576
        if str(device)=='mps':return torch.mps.current_allocated_memory()/1048576
        import psutil
        return psutil.Process().memory_info().rss/1048576
    def memory_exceeded():
        used=memory_used_mb()
        return used is not None and limits.get('max_memory_mb') is not None and used>limits['max_memory_mb']
    try:
        from backend.engine.shared_scheduler import compute_lease_scope
        with nullcontext() if remote_profile else compute_lease_scope(search_id, device,memory_budget_mb=limits.get('max_memory_mb') or 0,task=task):
            for candidate_index,config in enumerate(itertools.chain([first], candidates)):
                if (output/'cancel_requested.json').is_file():event.set()
                if event.is_set(): record.update(status='cancelled', winner=None, stop_reason='cancelled'); break
                if len(record['trials']) >= (1 if mode != 'search' else limits['max_trials']): record['stop_reason'] = 'trial_budget'; break
                config = {**config, 'epochs': epochs_per_trial, 'seed': (seed + candidate_index) % 2147483648}
                config.pop('objective', None); config.pop('latency_weight', None)
                if old:=reusable.get(config_key(config)):
                    record['trials'].append({**old,'reused_from_search_id':reuse_search_id});persist();continue
                if consumed + epochs_per_trial > limits['max_total_epochs']: record['stop_reason'] = 'epoch_budget'; break
                remaining = limits['max_seconds'] - (time.monotonic() - start)
                if remaining <= 0: record['stop_reason'] = 'time_budget'; break
                if memory_exceeded():record['stop_reason']='memory_budget';break
                if _fingerprint(source) != record['dataset_fingerprint'] or _fingerprint(canonical) != record['source_dataset_fingerprint']: raise ValueError('Automated training source or labels changed')
                if training_binding:
                    from backend.engine.training_provenance import validate_training_binding
                    validate_training_binding(training_binding)
                trial_id = uuid.uuid4().hex if spec.family else f'job_{int(time.time())}_{uuid.uuid4().hex[:6]}'
                trial_dir = models / spec.family / trial_id if spec.family else models / trial_id
                trial = {'trial_id': trial_id, 'config': config, 'status': 'running', 'metrics': {}, 'latency_ms': None}
                _write(trial_dir/'job_receipt.json',{'job_id':trial_id,'task':task,'status':'running',
                    'source_dataset_path':str(canonical),'dataset_path':str(source),'output_dir':str(trial_dir),
                    'training_provenance':training_binding,'search_id':search_id,'total_epochs':epochs_per_trial,'device':device})
                record['trials'].append(trial); persist(); child_event = threading.Event(); stop_monitor = threading.Event()
                def monitor():
                    while not stop_monitor.wait(.02):
                        if (output/'cancel_requested.json').is_file():event.set()
                        if memory_exceeded():record['stop_reason']='memory_budget';child_event.set();return
                        if event.is_set() or time.monotonic() - start >= limits['max_seconds']: child_event.set(); return
                monitor_thread = threading.Thread(target=monitor, daemon=True); monitor_thread.start()
                def progress(values):
                    trial['progress'] = values; persist()
                context = TrialContext(task, source, trial_dir, config, preset, device, parent, child_event, progress)
                try:
                    if remote_profile:
                        from backend.engine.remote_automated_trials import run_remote_candidate
                        measured = run_remote_candidate(context, profile=remote_profile, search_id=search_id,
                            source_dataset_path=canonical, training_binding=training_binding, owner=remote_owner,
                            remaining_seconds=remaining, configuration_parent=configuration_parent)
                    else: measured = _RUNNERS[task].run(context)
                    if event.is_set() or child_event.is_set(): raise InterruptedError('Automated training cancelled or budget elapsed')
                    if _fingerprint(source) != record['dataset_fingerprint'] or _fingerprint(canonical) != record['source_dataset_fingerprint']: raise ValueError('Automated training source or labels changed')
                    checkpoint = Path(measured['checkpoint_path'])
                    if checkpoint.is_symlink() or not checkpoint.is_file() or checkpoint.resolve() != (trial_dir / 'best_model.pt').resolve(): raise ValueError('Trial checkpoint escaped its candidate output')
                    value = measured['metrics'].get(_RUNNERS[task].metric_key); latency = measured['latency_ms']
                    if not isinstance(value, (int, float)) or not math.isfinite(value) or not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency <= 0:
                        raise ValueError('Winner requires finite measured validation objective and latency')
                    objective = float(value) * (1 if _RUNNERS[task].direction == 'min' else -1)
                    if record['objective'] == 'loss_latency': objective += record['latency_weight'] * latency
                    if not remote_profile:
                        payload = torch.load(checkpoint, map_location='cpu', weights_only=True)
                        payload['training_config'] = config
                        payload['automated_training'] = {'search_id': search_id, 'trial_id': trial_id, 'configuration_parent': configuration_parent, 'metrics': measured['metrics'], 'latency_ms': latency}
                        temporary = checkpoint.with_suffix('.tmp'); torch.save(payload, temporary); temporary.replace(checkpoint)
                    metadata_path = checkpoint.with_name('model_meta.json'); metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                    if not remote_profile:
                        metadata.update(automated_training={'search_id':search_id,'trial_id':trial_id,'configuration_parent':configuration_parent,'metrics':measured['metrics'],'latency_ms':latency}, training_config=config,
                                        source_dataset_path=str(canonical), dataset_path=str(source)); _write(metadata_path, metadata)
                    if training_binding and not remote_profile:
                        from backend.engine.training_provenance import persist_model_binding
                        persist_model_binding(trial_dir, training_binding)
                    digest = sha256(checkpoint.read_bytes()).hexdigest()
                    if not remote_profile:
                        metadata = json.loads(metadata_path.read_text(encoding='utf-8')); metadata['checkpoint_sha256'] = digest; _write(metadata_path, metadata)
                    receipt = {'job_id': trial_id, 'task': task, 'status': 'completed',
                        'source_dataset_path': str(canonical), 'dataset_path': str(source),
                        'output_dir':str(trial_dir),'current_epoch':measured.get('epochs_completed',epochs_per_trial),'total_epochs':epochs_per_trial,
                        'dataset_fingerprint': training_binding['dataset_fingerprint'] if training_binding else record['source_dataset_fingerprint'],
                        'checkpoint_sha256': digest, 'training_provenance': training_binding, 'search_id': search_id,'device':device,
                        **({'compute_profile_id':compute_profile_id,'remote_job_id':measured.get('remote_job_id')} if remote_profile else {})}
                    if remote_profile:
                        receipt = {**json.loads((trial_dir/'job_receipt.json').read_text(encoding='utf-8')),'search_id':search_id,
                                   'checkpoint_sha256':digest,'current_epoch':measured.get('epochs_completed',epochs_per_trial)}
                    _write(trial_dir/'job_receipt.json',receipt)
                    trial.update(status='completed', metrics=measured['metrics'], latency_ms=latency, latency_scope=measured.get('latency_scope'),
                                 objective=objective, checkpoint_path=str(checkpoint), checkpoint_sha256=digest,
                                 **({'compute_profile_id':compute_profile_id,'remote_job_id':measured.get('remote_job_id')} if remote_profile else {}))
                    consumed += measured.get('epochs_completed', epochs_per_trial)
                except InterruptedError:
                    trial['status'] = 'cancelled'; consumed += epochs_per_trial
                    for name in ('best_model.pt', 'model_meta.json', 'job_receipt.json'): (trial_dir / name).unlink(missing_ok=True)
                    record['stop_reason'] = 'cancelled' if event.is_set() else 'memory_budget' if record.get('stop_reason')=='memory_budget' else 'time_budget'
                    if event.is_set(): record['status'] = 'cancelled'
                    break
                except (ValueError, OSError, RuntimeError, KeyError, TypeError, ImportError) as exc:
                    from backend.engine.remote_automated_trials import RemoteTrialUncertain
                    if isinstance(exc, RemoteTrialUncertain):
                        trial.update(status='disconnected',error=str(exc),compute_profile_id=compute_profile_id)
                        record.update(status='interrupted',stop_reason='remote_reconciliation_required',error=str(exc));break
                    trial.update(status='failed', error=str(exc)); consumed += epochs_per_trial
                    for name in ('best_model.pt', 'model_meta.json', 'job_receipt.json'): (trial_dir / name).unlink(missing_ok=True)
                finally:
                    stop_monitor.set(); monitor_thread.join(timeout=1); persist()
            completed = [t for t in record['trials'] if t['status'] == 'completed']
            if event.is_set(): record.update(status='cancelled',winner=None,stop_reason='cancelled')
            if record['status'] not in ('cancelled','interrupted'):
                if _fingerprint(source) != record['dataset_fingerprint'] or _fingerprint(canonical) != record['source_dataset_fingerprint']: raise ValueError('Automated training source or labels changed')
                record['winner'] = min(completed, key=lambda t: (t['objective'], t['trial_id'])) if completed else None
                record['status'] = 'completed' if completed else 'failed'
    except (ValueError, OSError, RuntimeError) as exc:
        record.update(status='failed', winner=None, error=str(exc))
    record.update(epochs_consumed=consumed, duration_seconds=time.monotonic() - start, finished_at=time.time()); persist()
    return record


def read_search(models_dir, search_id):
    if not isinstance(search_id, str) or len(search_id) != 32 or any(c not in '0123456789abcdef' for c in search_id): raise ValueError('Invalid search ID')
    path = Path(models_dir) / 'automated_training' / search_id / 'search.json'
    if any(p.is_symlink() for p in (path.parent.parent, path.parent, path)) or not path.is_file(): raise FileNotFoundError('Search unavailable in active project')
    return json.loads(path.read_text(encoding='utf-8'))


from backend.engine.specialist_automated_trials import register_specialist_runners
register_specialist_runners()
