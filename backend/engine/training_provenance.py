"""Binding for new jobs to verified immutable label/split/source manifests."""
from pathlib import Path
import hashlib
import json
import os
import tempfile


def bind_training_version(project, source, supplied_version=None):
    from backend.api.routes_dataset_versions import _snapshot,_read_manifest,_verify,_require_active_labelset
    if supplied_version is None:
        supplied_version=_snapshot(project,source,'Training input','Immutable version bound to a new training job','snapshot')['id']
    directory,manifest=_read_manifest(project,supplied_version)
    _require_active_labelset(project,manifest)
    verification=_verify(project,directory,manifest)
    if verification.get('status')!='verified' or verification.get('editable_changed_files'):
        from fastapi import HTTPException
        raise HTTPException(409,'Training dataset version does not match active source, labels, and split')
    split_rows=[row['sha256'] for row in manifest['files'] if row['origin']=='split']
    # Folder partitions and patch manifests are also exact split evidence.
    split_digest = split_rows[0] if len(split_rows)==1 else hashlib.sha256(json.dumps(
        [{key: row[key] for key in ('origin','relative_path','sha256')} for row in manifest['files']],
        sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'dataset_version_id':manifest['id'],'labelset_id':manifest.get('labelset_id','default'),
            'dataset_fingerprint':manifest['dataset_fingerprint'],'manifest_sha256':manifest['content_digest'],
            'split_sha256':split_digest, 'split_binding': 'saved_manifest' if split_rows else 'versioned_dataset_layout','version_dir':str(directory)}


def persist_model_binding(output,binding,*,checkpoint=True):
    if not binding:return
    output=Path(output)
    model=output/'best_model.pt'
    if checkpoint and model.is_file() and not model.is_symlink():
        import torch
        payload=torch.load(model,map_location='cpu',weights_only=True)
        if not isinstance(payload,dict):raise ValueError('Training checkpoint cannot accept verified provenance')
        payload['training_provenance']=binding
        with tempfile.NamedTemporaryFile(dir=output,prefix='bound-checkpoint-',suffix='.pt',delete=False) as writer:temporary=Path(writer.name)
        try:torch.save(payload,temporary);os.replace(temporary,model)
        finally:temporary.unlink(missing_ok=True)
    metadata=output/'model_meta.json'
    if metadata.is_file() and not metadata.is_symlink():
        payload=json.loads(metadata.read_text());payload['training_provenance']=binding
        if model.is_file() and not model.is_symlink():
            from backend.api.routes_dataset_versions import _file_hash
            payload['checkpoint_sha256']=_file_hash(model)
        temporary=metadata.with_suffix('.tmp');temporary.write_text(json.dumps(payload));os.replace(temporary,metadata)


def validate_training_binding(binding):
    if not binding:return
    directory=Path(binding['version_dir'])
    manifest=json.loads((directory/'manifest.json').read_text())
    from backend.api.routes_dataset_versions import _manifest_digest, _file_hash, _safe_backup_path
    if manifest.get('content_digest')!=binding['manifest_sha256'] or _manifest_digest(manifest)!=binding['manifest_sha256']:
        raise ValueError('Bound training version manifest changed')
    for row in manifest['files']:
        if row['kind']=='image':
            if _file_hash(Path(row['source_path']),allow_symlink=True)!=row['sha256']:raise ValueError('Training source image changed from bound version')
        elif _file_hash(_safe_backup_path(directory,row))!=row['sha256']:
            raise ValueError('Bound label/split backup changed')
        elif (row['origin']=='source' and not binding.get('family_task')
              and row['source_path'] not in binding.get('frozen_source_labels',[])
              and _file_hash(Path(row['source_path']),allow_symlink=True)!=row['sha256']):
            raise ValueError('Training source labels changed from bound version')
    family_inputs=binding.get('family_inputs',[])
    if family_inputs:
        digest=hashlib.sha256(json.dumps(family_inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if digest!=binding.get('family_inputs_sha256'):raise ValueError('Bound family input inventory changed')
    for row in family_inputs:
        expected_source=row.get('restored_source_sha256',row['sha256'])
        if _file_hash(Path(row['source_path']))!=expected_source:
            raise ValueError('Family training input changed from bound version')
        if row.get('snapshot_path'):
            backup=Path(row['snapshot_path'])
            if not backup.resolve().is_relative_to(directory.resolve()) or _file_hash(backup)!=row['sha256']:
                raise ValueError('Bound family manifest backup changed')
            if 'restored_source_sha256' in row:
                if binding.get('family_task')!='enhancement' or row['relative_path']!='pairs.json':
                    raise ValueError('Only relocated enhancement manifest paths may use a source alias')
                original=json.loads(backup.read_text());current=json.loads(Path(row['source_path']).read_text())
                omitted={'source_dataset_path','dataset_path','provenance'}
                if {k:v for k,v in original.items() if k not in omitted}!={k:v for k,v in current.items() if k not in omitted}:
                    raise ValueError('Relocated enhancement manifest content differs from frozen labels')


def bind_family_training(project,dataset,task,supplied_version=None):
    """Bind explicit family labels and actual pixels to the active source version.

    Family engines currently consume their original manifest paths. Checking those
    paths before and after training rejects label edits during a run; exact small
    label bytes are retained beside the immutable version for later auditing.
    """
    from backend.api.routes_dataset_versions import _file_hash
    import shutil
    configured=project.get('source_dataset_dir')
    if not configured:raise ValueError('Select the active project source before family training')
    source=Path(configured).expanduser().resolve()
    requested=Path(dataset).expanduser()
    dataset=requested.resolve()
    if requested.is_symlink() or not dataset.is_dir():raise ValueError('Family dataset must match the active project source')
    prepared_tasks={'enhancement','patch_classification','rotation','ocr','rotated_detection','defect_gan'}
    if task not in prepared_tasks and dataset!=source:raise ValueError('Family dataset must match the active project source')
    if task in {'patch_classification','rotation'}:
        owned=Path(project['dataset_dir']).resolve()
        if Path(project['dataset_dir']).is_symlink() or not dataset.is_relative_to(owned):
            raise ValueError('Prepared family data must belong to the active project dataset storage')
        if task=='patch_classification':
            from backend.engine.patch_classification import load_patch_manifest
            manifest=load_patch_manifest(dataset);paths=[row.image_path for row in manifest.patches]
            manifest_path=dataset/'patches.json'
        else:
            from backend.engine.rotation import load_rotation_manifest
            manifest=load_rotation_manifest(dataset);paths=[row.image_path for row in manifest.samples]
            manifest_path=dataset/'rotation.json'
        provenance=manifest.provenance
        configured_source=provenance.get('source_dataset_path')
        if not configured_source or Path(configured_source).resolve()!=source:
            raise ValueError('Prepared family data must link to the active project source')
        if task=='rotation':
            raw=json.loads(manifest_path.read_text())
            for row in raw.get('samples',[]):
                relative=row.get('source_relative_path',row.get('image',''))
                original=source/relative
                if (original.is_symlink() or not original.resolve().is_relative_to(source)
                        or _file_hash(original)!=row.get('source_sha256')):
                    raise ValueError('Rotation original source image changed')
    elif task=='ocr':
        from backend.engine.ocr import load_ocr_manifest
        manifest=load_ocr_manifest(dataset);paths=[row.image_path for row in manifest.samples]
        provenance=manifest.provenance;manifest_path=dataset/'ocr.json'
    elif task=='rotated_detection':
        from backend.engine.rotated_detection import load_rotated_manifest
        manifest=load_rotated_manifest(dataset);paths=[row.path for row in manifest.records]
        provenance=manifest.provenance;manifest_path=dataset/'rotated_boxes.json'
    elif task=='defect_gan':
        from backend.engine.defect_gan import load_defect_gan_manifest,MANIFEST_NAME
        manifest=load_defect_gan_manifest(dataset);paths=[dataset/row['image'] for row in manifest['samples']]
        manifest_path=dataset/MANIFEST_NAME;provenance={**manifest.get('provenance',{}),'manifest_sha256':_file_hash(manifest_path)}
    elif task=='enhancement':
        from backend.engine.enhancement import load_enhancement_manifest
        owned=Path(project['dataset_dir']).resolve()
        if Path(project['dataset_dir']).is_symlink() or not dataset.is_relative_to(owned):
            raise ValueError('Enhancement pairs must belong to the active project dataset storage')
        manifest=load_enhancement_manifest(dataset);provenance=manifest['provenance'];manifest_path=dataset/'pairs.json'
        if Path(provenance['source_dataset_path']).resolve()!=source:
            raise ValueError('Enhancement pairs must link to the active project source')
        paths=[dataset/row[key] for row in manifest['records'] for key in ('input','target')]
        for row in manifest['records']:
            original=source/row.get('source_relative_path','')
            if not original.resolve().is_relative_to(source) or _file_hash(original)!=row.get('source_sha256'):
                raise ValueError('Enhancement original source image changed')
    else:raise ValueError('Unsupported family training task')
    if dataset!=source and task in {'ocr','rotated_detection','defect_gan'}:
        owned=Path(project['dataset_dir']).resolve()
        if Path(project['dataset_dir']).is_symlink() or not dataset.is_relative_to(owned):
            raise ValueError('Prepared family inputs must belong to active project storage')
        if not provenance.get('source_dataset_path') or Path(provenance['source_dataset_path']).resolve()!=source:
            raise ValueError('Prepared family inputs must link to the active original source')
        mapping=provenance.get('source_map')
        if not isinstance(mapping,dict) or not mapping:raise ValueError('Prepared family inputs require original source mapping')
        for relative,row in mapping.items():
            copied=dataset/relative;original=source/row.get('source_relative_path','')
            if (copied.is_symlink() or original.is_symlink() or not copied.resolve().is_relative_to(dataset)
                    or not original.resolve().is_relative_to(source) or _file_hash(original)!=row.get('source_sha256')
                    or _file_hash(copied)!=row.get('source_sha256')):
                raise ValueError('Prepared family original source image changed')
    binding=bind_training_version(project,source,supplied_version)
    directory=Path(binding['version_dir'])
    rows=[]
    for path in sorted(set([manifest_path,*paths])):
        if path.is_symlink() or not path.resolve().is_relative_to(dataset):raise ValueError('Family input path escaped dataset')
        digest=_file_hash(path)
        if digest is None:raise ValueError('Family training input is unavailable')
        snapshot=None
        if path==manifest_path:
            backup=directory/'labels'/'family'/task/path.name
            backup.parent.mkdir(parents=True,exist_ok=True)
            if backup.exists() and _file_hash(backup)!=digest:raise ValueError('Bound family manifest differs from existing snapshot')
            if not backup.exists():shutil.copyfile(path,backup)
            snapshot=str(backup)
        rows.append({'relative_path':path.relative_to(dataset).as_posix(),'source_path':str(path),'sha256':digest,'snapshot_path':snapshot})
    binding.update(family_task=task,family_dataset_path=str(dataset),family_provenance=provenance,
                   family_inputs=rows,family_inputs_sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    validate_training_binding(binding)
    return binding


def frozen_annotation_root(binding,source,output):
    if not binding:return None
    import shutil
    from backend.engine.annotation_storage import dataset_annotation_dir
    version=Path(binding['version_dir'])
    manifest=json.loads((version/'manifest.json').read_text())
    root=Path(output)/'bound_annotations'
    target=dataset_annotation_dir(source,root,use_scope=False)
    frozen=[]
    for row in manifest['files']:
        source_label=None
        if row['origin']=='studio':destination=target/row['relative_path']
        elif row['origin']=='studio_scoped':destination=target.parent/row['relative_path']
        elif row['origin']=='source' and row['kind']=='label' and Path(row['relative_path']).suffix.lower()=='.json':
            backup=version/row['snapshot_path']
            try:label=json.loads(backup.read_text())
            except (ValueError,UnicodeError):continue
            if not isinstance(label,dict) or not isinstance(label.get('shapes'),list):continue
            annotations=[]
            for shape in label['shapes']:
                if not isinstance(shape,dict):continue
                item=dict(shape)
                item['is_normal']=bool(shape.get('is_normal') or shape.get('flags',{}).get('is_normal'))
                points=shape.get('points',[])
                if shape.get('shape_type')=='rectangle' and len(points)==2:
                    (x1,y1),(x2,y2)=points
                    item.update(type='bbox',bbox=[min(x1,x2),min(y1,y2),max(x1,x2),max(y1,y2)])
                elif len(points)>=3:item.update(type='polygon',polygon=points)
                annotations.append(item)
            # Existing preparation reads Studio overlays as annotations. Preserve
            # every original shape beside the normalized view; the version backup
            # retains the exact original JSON bytes.
            source_label={**label,'annotations':annotations,'frozen_source_sha256':row['sha256']}
            relative=Path(row['relative_path'])
            destination=dataset_annotation_dir(Path(source)/relative.parent,root,use_scope=False)/relative.name
            frozen.append(row['source_path'])
        else:continue
        destination.parent.mkdir(parents=True,exist_ok=True)
        if source_label is None:shutil.copyfile(version/row['snapshot_path'],destination)
        else:destination.write_text(json.dumps(source_label,ensure_ascii=False))
    binding['frozen_source_labels']=frozen
    return root
