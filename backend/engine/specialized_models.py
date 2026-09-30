"""Project-owned specialized checkpoints shared by flow, evaluation and packaging."""
from __future__ import annotations
import json
import hashlib
import re
from pathlib import Path
import torch

SPECIALIZED_TASKS = ('ocr','rotated_detection','enhancement','rotation')
FLOW_TASKS = ('detection','classification','segmentation','anomaly','patch_classification',*SPECIALIZED_TASKS)

def require_completed_checkpoint(checkpoint):
    """Legacy candidates have no journal; recorded jobs must actually complete."""
    directory=Path(checkpoint).parent
    for name in ('job.json','job_state.json','job_receipt.json'):
        journal=directory/name
        if journal.exists():
            if journal.is_symlink():raise ValueError('Model needs a completed training job')
            record=json.loads(journal.read_text())
            if record.get('status')!='completed':raise ValueError('Model needs a completed training job')

def flow_model_task(node):
    if node.data.node_type=='detection_crop': return node.data.task if node.data.task=='rotated_detection' else 'detection'
    if node.data.node_type=='inspection': return node.data.task
    if node.data.node_type=='preprocess' and node.data.params.get('operation')=='enhancement': return 'enhancement'
    if node.data.node_type=='preprocess' and node.data.params.get('operation')=='learned_rotation': return 'rotation'
    return None

def valid_flow_job(job_id,task):
    from backend.engine.checkpoint_paths import is_job_id
    return is_job_id(job_id) or (task in SPECIALIZED_TASKS and isinstance(job_id,str) and re.fullmatch(r'[0-9a-f]{32}',job_id) is not None)

def specialized_dataset_provenance(task,source):
    if task=='defect_gan':
        from backend.engine.defect_gan import load_defect_gan_manifest
        payload=load_defect_gan_manifest(source)
        return {'dataset_sha256':hashlib.sha256((Path(source)/'defect_gan.json').read_bytes()).hexdigest(),
                'source_dataset_path':payload.get('source_dataset_path',str(source))}
    if task=='rotation':
        from backend.engine.rotation import load_rotation_manifest
        return load_rotation_manifest(source).provenance
    if task=='ocr':
        from backend.engine.ocr import load_ocr_manifest
        return load_ocr_manifest(source).provenance
    if task=='rotated_detection':
        from backend.engine.rotated_detection import load_rotated_manifest
        return load_rotated_manifest(source).provenance
    from backend.engine.enhancement import load_enhancement_manifest
    manifest=load_enhancement_manifest(source)
    return manifest.provenance if hasattr(manifest,'provenance') else manifest['provenance']


def _verify_historical_source(root, source, payload, metadata):
    """Labels may advance; the checkpoint's frozen images and backups may not."""
    from backend.api.routes_dataset_versions import _manifest_digest, _file_hash, _safe_backup_path
    original = payload.get('training_provenance')
    binding = metadata.get('training_provenance', original)
    if not isinstance(original, dict) or not isinstance(binding, dict):
        raise ValueError('Specialized label revision requires an immutable training binding')
    if binding.get('dataset_version_id') != original.get('dataset_version_id'):
        raise ValueError('Specialized training binding version changed')
    if (binding.get('manifest_sha256') != original.get('manifest_sha256')
            and binding.get('archive_restored_from_manifest_sha256') != original.get('manifest_sha256')):
        raise ValueError('Specialized training binding alias is invalid')
    requested_directory = Path(binding['version_dir'])
    directory = requested_directory.resolve()
    if (requested_directory.is_symlink() or (root.parent / 'versions').is_symlink()
            or not directory.is_relative_to(root.parent / 'versions')):
        raise ValueError('Specialized training binding leaves its owning project')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if (manifest.get('id') != binding['dataset_version_id']
            or _manifest_digest(manifest) != binding['manifest_sha256']
            or manifest.get('content_digest') != binding['manifest_sha256']):
        raise ValueError('Specialized frozen training manifest changed')
    if Path(manifest['source_dataset_dir']).resolve() != Path(source).resolve():
        raise ValueError('Specialized original source lineage differs')
    for row in manifest['files']:
        if row['kind'] == 'image':
            if _file_hash(Path(row['source_path'])) != row['sha256']:
                raise ValueError('Specialized bound source image changed')
        elif _file_hash(_safe_backup_path(directory, row)) != row['sha256']:
            raise ValueError('Specialized frozen label/split backup changed')
    original_inputs = {(row['relative_path'], row['sha256']) for row in original.get('family_inputs', [])}
    current_inputs = {(row['relative_path'], row['sha256']) for row in binding.get('family_inputs', [])}
    if original_inputs != current_inputs:
        raise ValueError('Specialized frozen family input binding changed')
    for row in binding.get('family_inputs', []):
        backup = row.get('snapshot_path')
        if backup:
            path = Path(backup)
            if not path.resolve().is_relative_to(directory) or _file_hash(path) != row['sha256']:
                raise ValueError('Specialized frozen family manifest backup changed')
        elif _file_hash(Path(row['source_path'])) != row['sha256']:
            raise ValueError('Specialized original family image changed')

def resolve_specialized_checkpoint(project_models_dir,job_id,task,source_dataset_path=None):
    if task not in SPECIALIZED_TASKS or not valid_flow_job(job_id,task): raise ValueError('Invalid specialized model task or job ID')
    root=Path(project_models_dir).resolve()
    directory=root/task/job_id
    checkpoint=directory/'best_model.pt'
    require_completed_checkpoint(checkpoint)
    if any(path.is_symlink() for path in (Path(project_models_dir),root/task,directory,checkpoint)) or not checkpoint.is_file() or not checkpoint.resolve().is_relative_to(root):
        raise ValueError('Specialized checkpoint is unavailable in the active project')
    payload=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if not isinstance(payload,dict) or payload.get('task')!=task or 'model_state_dict' not in payload:
        raise ValueError('Specialized checkpoint task or weights are incompatible')
    metadata_path=directory/'model_meta.json'
    metadata=json.loads(metadata_path.read_text()) if metadata_path.is_file() and not metadata_path.is_symlink() else payload
    if metadata.get('task')!=task: raise ValueError('Specialized model metadata task differs')
    if metadata.get('checkpoint_sha256') and hashlib.sha256(checkpoint.read_bytes()).hexdigest()!=metadata['checkpoint_sha256']:
        raise ValueError('Specialized checkpoint hash changed')
    if source_dataset_path is not None:
        if payload.get('training_provenance'):
            _verify_historical_source(root,source_dataset_path,payload,metadata)
        if task=='rotation':
            from backend.engine.rotation import load_rotation_manifest
            prepared = load_rotation_manifest(metadata.get('dataset_path', source_dataset_path))
            if Path(prepared.provenance.get('source_dataset_path',prepared.root)).resolve()!=Path(source_dataset_path).resolve():
                raise ValueError('Rotation source dataset differs from active project')
            current_provenance=prepared.provenance
            current=current_provenance['dataset_sha256']
        elif task=='enhancement':
            from backend.engine.enhancement import load_enhancement_manifest
            manifest=load_enhancement_manifest(metadata.get('dataset_path',payload.get('dataset_path','')))
            configured=Path(source_dataset_path).resolve()
            if Path(manifest['provenance']['source_dataset_path']).resolve()!=configured: raise ValueError('Enhancement source dataset differs from active project')
            for row in manifest['records']:
                if ('source_image' in row or 'source_relative_path' in row) and 'source_sha256' in row:
                    original=Path(row.get('source_image',row.get('source_relative_path')))
                    if not original.is_absolute(): original=configured/original
                    if original.is_symlink() or not original.resolve().is_relative_to(configured) or hashlib.sha256(original.read_bytes()).hexdigest()!=row['source_sha256']: raise ValueError('Enhancement original source hash changed')
            current=manifest['provenance']['dataset_sha256']
        else:
            dataset=metadata.get('dataset_path',source_dataset_path)
            current_provenance=specialized_dataset_provenance(task,dataset)
            if Path(dataset).resolve()!=Path(source_dataset_path).resolve() and current_provenance.get('source_dataset_path')!=str(Path(source_dataset_path).resolve()):
                raise ValueError('Prepared specialist source differs from active project')
            current=current_provenance['dataset_sha256']
        recorded=metadata.get('dataset_sha256') or metadata.get('provenance',{}).get('dataset_sha256') or metadata.get('dataset_provenance',{}).get('dataset_sha256')
        immutable=(payload.get('dataset_sha256') or payload.get('provenance',{}).get('dataset_sha256')
                   or payload.get('dataset_provenance',{}).get('dataset_sha256')
                   or payload.get('training_provenance',{}).get('family_provenance',{}).get('dataset_sha256'))
        if recorded and immutable and recorded!=immutable:raise ValueError('Specialized metadata differs from immutable checkpoint provenance')
        expected=immutable or recorded
        if current!=expected:
            if task=='enhancement':raise ValueError('Specialized dataset provenance differs from checkpoint')
            if not payload.get('training_provenance'):
                _verify_historical_source(root,source_dataset_path,payload,metadata)
            trained=(payload.get('dataset_provenance') or metadata.get('provenance') or metadata.get('dataset_provenance') or {})
            if current_provenance['source_sha256']!=trained.get('source_sha256'):
                raise ValueError('Specialized original source image inventory changed')
            if task=='ocr':
                from backend.engine.ocr import load_ocr_manifest
                if not set(load_ocr_manifest(source_dataset_path).alphabet).issubset(payload['alphabet']):
                    raise ValueError('Specialized OCR alphabet is incompatible with checkpoint')
            elif task=='rotated_detection':
                from backend.engine.rotated_detection import load_rotated_manifest
                if not set(load_rotated_manifest(source_dataset_path).class_names).issubset(metadata.get('class_names',[metadata.get('class_name')])):
                    raise ValueError('Specialized rotated classes are incompatible with checkpoint')
    return checkpoint,metadata
