"""Read-only setup checks and persisted task inventory; never submit or download."""
from importlib.util import find_spec
import json
from pathlib import Path

FAMILY_STORAGE={'rotation':'rotation','ocr':'ocr','defect_gan':'defect-gan','enhancement':'enhancement','rotated_detection':'rotated-detection'}


def _package_available(name):
    try:return find_spec(name) is not None
    except (ImportError,ValueError):return False


def _cached_weights(model):
    if model.startswith('dinov3_') and _package_available('huggingface_hub'):
        from huggingface_hub import try_to_load_from_cache
        from backend.engine.model_backbones import DINO_MODELS
        if model not in DINO_MODELS:return None
        value=try_to_load_from_cache(f'timm/{DINO_MODELS[model]}','model.safetensors')
        return Path(value) if isinstance(value,str) else None
    if model.startswith('yolo'):
        import torch
        return Path(torch.hub.get_dir())/'checkpoints'/f'{model}.pt'
    if model in ('resnet18','convnext_tiny','efficientnet_b0','padim','patchcore','fasterrcnn'):
        import torch
        key={'padim':'resnet18','patchcore':'resnet18','fasterrcnn':'fasterrcnn'}.get(model,model)
        return next((Path(torch.hub.get_dir())/'checkpoints').glob(f'{key}*.pth'),None)
    return None


def model_readiness(task,model,device='cpu',checkpoint=None):
    dependencies=['torch','PIL']
    if model.startswith('dinov3_'):dependencies+=['timm','safetensors','huggingface_hub']
    if model.startswith('yolo'):dependencies+=['ultralytics']
    missing=[name for name in dependencies if not _package_available(name)]
    runtime={'device':device,'available':False,'reason':''}
    try:
        from backend.engine.runtime_device import resolve_runtime_device
        runtime['device']=str(resolve_runtime_device(device));runtime['available']=True
    except (ValueError,RuntimeError,ImportError) as exc:runtime['reason']=str(exc)
    required=model.startswith(('dinov3_','yolo')) or model in ('resnet18','convnext_tiny','efficientnet_b0','padim','patchcore','fasterrcnn')
    path=Path(checkpoint).expanduser() if checkpoint else _cached_weights(model) if required else None
    exists=path is not None and path.is_file() and path.stat().st_size>0
    weights={'required':required,'state':'file_available' if exists else 'missing' if required else 'not_required',
             'filename':path.name if exists else None,'content_verified':False}
    return {'task':task,'model':model,'ready':not missing and runtime['available'] and (not required or exists),
            'dependencies':{'required':dependencies,'missing':missing},'runtime':runtime,'weights':weights,
            'execution_verified':False,'quality_approved':False,
            'next_actions':(['install_dependencies'] if missing else [])+(['choose_device'] if not runtime['available'] else [])+(['import_official_weights'] if required and not exists else [])}


def persisted_task_rows(models,source,labelset):
    """A journal is state evidence, never proof that a saved checkpoint is usable."""
    models=Path(models);rows=[]
    if models.is_symlink():raise ValueError('Task storage cannot be linked')
    for family,kind in FAMILY_STORAGE.items():
        root=models/family
        if root.is_symlink():continue
        for folder in sorted(root.iterdir()) if root.is_dir() else []:
            if folder.is_symlink() or not folder.is_dir():continue
            names=('job_state.json','job.json') if family=='rotated_detection' else ('job.json',)
            path=next((folder/name for name in names if (folder/name).is_file() and not (folder/name).is_symlink()),None)
            if path is None:continue
            try:
                row=json.loads(path.read_text(encoding='utf-8'));identifier=row.get('job_id')
                if identifier!=folder.name or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):continue
                if row.get('source_dataset_path')!=source or (row.get('training_provenance') or {}).get('labelset_id','default')!=labelset:continue
                rows.append({**row,'kind':kind})
            except (OSError,ValueError,TypeError):continue
    return rows


def _weight_registry(project):
    models=Path(project['models_dir'])
    if models.is_symlink() or models.resolve()!=Path(project['project_dir']).resolve()/'models':raise ValueError('Invalid project weight storage')
    root=models/'pretrained'
    if root.is_symlink():raise ValueError('Weight imports cannot use linked storage')
    return root,root/'imports.json'


def imported_weight(project,task,model):
    from backend.engine.model_backbones import checkpoint_sha256
    root,registry=_weight_registry(project)
    if not registry.is_file() or registry.is_symlink():return None
    try:
        row=json.loads(registry.read_text(encoding='utf-8')).get(task+':'+model)
        if not isinstance(row,dict):return None
        path=Path(row['pretrained_checkpoint'])
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):return None
        return str(path) if checkpoint_sha256(path)==row['sha256'] else None
    except (ValueError,OSError,KeyError,TypeError):return None


def import_pretrained_weight(project,task,model,supplied,expected_sha256=None):
    import os,re,shutil,uuid
    source=Path(supplied).expanduser()
    if source.is_symlink() or not source.is_file() or source.stat().st_size==0:raise ValueError('Choose a regular nonempty pretrained file')
    if source.suffix.lower() not in ('.pt','.pth','.safetensors'):raise ValueError('Select .pt, .pth or .safetensors weights')
    if not re.fullmatch(r'[a-z0-9_]+',task) or not re.fullmatch(r'[a-z0-9_]+',model):raise ValueError('Invalid model import identity')
    from backend.engine.model_backbones import checkpoint_sha256
    digest=checkpoint_sha256(source)
    if expected_sha256 and (not re.fullmatch('[0-9a-f]{64}',expected_sha256.lower()) or digest!=expected_sha256.lower()):raise ValueError('Pretrained checksum differs from the expected SHA256')
    root,registry=_weight_registry(project);root.mkdir(parents=True,exist_ok=True)
    target=root/(model+'_'+digest+source.suffix.lower())
    if target.is_symlink():raise ValueError('Imported target cannot be linked')
    temporary=root/('.import_'+uuid.uuid4().hex)
    try:
        shutil.copyfile(source,temporary)
        if checkpoint_sha256(temporary)!=digest:raise ValueError('Pretrained file changed during import')
        os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)
    if registry.is_symlink():raise ValueError('Import registry cannot be linked')
    records=json.loads(registry.read_text(encoding='utf-8')) if registry.is_file() else {}
    row={'task':task,'model':model,'pretrained_checkpoint':str(target),'sha256':digest,
         'official_hash_compared':bool(expected_sha256),'content_verified':False,'execution_verified':False}
    records[task+':'+model]=row
    from backend.engine.specialized_training_jobs import _write
    _write(registry,records)
    return row
