"""Read preparation and continuation prerequisites without creating a job/version."""
from pathlib import Path

SPECIALIST_TASKS={'patch_classification','rotation','ocr','rotated_detection','enhancement','defect_gan'}


def eligible_preparation_paths(source,task,requested=None):
    from backend.engine.grouped_dataset_views import source_image_paths
    source=Path(source).resolve()
    paths=source_image_paths(source,task)
    eligible={str(path.resolve()) for path in paths}
    if requested is not None:
        for relative in requested:
            if not isinstance(relative,str) or str((source/relative).resolve()) not in eligible:
                raise ValueError('Specialist preparation image is not eligible in the active project review policy')
    return paths


def resolve_training_input(project,task,supplied=None):
    configured=project.get('source_dataset_dir')
    if not configured:raise ValueError('Select the active project original source first')
    source=Path(configured).expanduser().resolve()
    if not source.is_dir():raise ValueError('Active project original source is unavailable')
    requested=Path(supplied or source).expanduser();dataset=requested.resolve()
    if task not in SPECIALIST_TASKS:
        if dataset!=source:raise ValueError('Training input must match the active project source')
        return dataset
    owned=Path(project['dataset_dir'])
    # OCR, rotated detection and GAN retain their original explicit-manifest
    # compatibility. New copies must always belong to this project's storage.
    if requested.is_symlink() or owned.is_symlink() or (dataset!=source and not dataset.is_relative_to(owned.resolve())):
        raise ValueError('Prepared input must belong to the active project')
    if task in {'patch_classification','rotation','enhancement'} and not dataset.is_relative_to(owned.resolve()):
        raise ValueError('Prepared input must belong to the active project')
    if task in {'ocr','rotated_detection'}:
        from backend.engine.prepared_family_datasets import resolve_family_dataset
        manifest=resolve_family_dataset(project,task,dataset)
        return manifest.root
    if task=='patch_classification':
        from backend.engine.patch_classification import load_patch_manifest
        provenance=load_patch_manifest(dataset).provenance
    elif task=='rotation':
        from backend.engine.rotation import load_rotation_manifest
        provenance=load_rotation_manifest(dataset).provenance
    elif task=='enhancement':
        from backend.engine.enhancement import load_enhancement_manifest
        provenance=load_enhancement_manifest(dataset)['provenance']
    else:
        from backend.engine.defect_gan import load_defect_gan_manifest
        manifest=load_defect_gan_manifest(dataset)
        provenance=manifest.get('provenance',{})
        if dataset==source:return dataset
    if not provenance.get('source_dataset_path') or Path(provenance['source_dataset_path']).resolve()!=source:
        raise ValueError('Prepared input differs from the active project original source')
    return dataset


def resolve_training_parent(project,identifier,task,dataset,preset,options):
    if not identifier:return None
    source=project['source_dataset_dir']
    if task in SPECIALIST_TASKS-{'patch_classification'}:
        from backend.engine.specialized_warm_start import resolve_family_parent
        config=dict(options)
        if task=='ocr' and isinstance(config.get('image_size'),int):config['image_size']=(config['image_size'],config.get('image_width',128))
        return resolve_family_parent(project['models_dir'],identifier,task,source,dataset,config)
    from backend.engine.warm_start import resolve_warm_start_parent,architecture_for,training_classes
    parent=resolve_warm_start_parent(identifier,project['models_dir'],source,task,architecture_for(task,preset,options))
    if parent.classes!=training_classes(task,dataset):raise ValueError('Parent classes differ from the prepared input')
    return parent


def remote_weight_readiness(probe,task,model,options,checkpoint=None,parent=None):
    """File/hash availability is distinct from loading or approving model quality."""
    if parent:return {'state':'parent_verified','sha256':parent.checkpoint_sha256,'content_verified':False}
    required=model.startswith(('dinov3_','yolo')) or model in {'padim','patchcore','resnet18','convnext_tiny','efficientnet_b0','fasterrcnn'}
    if not required or options.get('pretrained') is False:return {'state':'not_required','content_verified':False}
    if checkpoint:
        from backend.engine.model_backbones import _verified_weights
        path,digest=_verified_weights(checkpoint,options.get('pretrained_sha256'))
        if path.is_symlink() or path.stat().st_size==0:raise ValueError('Pretrained weights require a regular nonempty file')
        return {'state':'transfer_ready','filename':path.name,'sha256':digest,'content_verified':False}
    weights=(probe.get('checks') or {}).get('pretrained_weights') or {}
    key='resnet18' if model in {'padim','patchcore'} else model
    if model=='fasterrcnn':key='fasterrcnn_mobilenet_v3_large_fpn' if options.get('preset','fast')=='fast' else 'fasterrcnn_resnet50_fpn_v2'
    row=weights.get(key) or {}
    if row.get('ok'):return {'state':'server_file_available','filename':row.get('file'),'sha256':row.get('sha256'),'content_verified':False}
    return {'state':'missing','content_verified':False}
