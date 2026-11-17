"""Model-format-aware bridge to Geti's actual CPU inference service.

PyTorch exports are artifacts, never fed into Modu's native checkpoint loaders.
OpenVINO IR and ONNX use the pinned Geti OpenvinoAdapter with explicit variants.
"""
from __future__ import annotations

import copy
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import tempfile
import threading
import uuid
import zipfile

from .dataset_transfer import (_all_items, _atomic_json, _bytes_hash, _digest,
    _from_platform, _revision_body, prepared_image_bytes, validate_transfer, validate_platform_dataset)

_PREDICT_FORMATS = {'openvino','onnx'}
_EXPORT_PUBLICATION = threading.RLock()


def _uuid(value):
    if not isinstance(value,str) or str(uuid.UUID(value))!=value:
        raise ValueError('Model or variant identity is malformed')
    return value


def _bound_revision(client, receipt, model):
    training=model.get('training_info') or {}
    if training.get('status')!='successful' or model.get('files_deleted'):
        raise ValueError('Model has no successful retained training artifact')
    revision=_uuid(training.get('dataset_revision_id'))
    prefix=f"/api/projects/{receipt['geti_project_id']}/dataset_revisions/{revision}"
    rows=_all_items(client,prefix+'/items')
    expected={row['media_id']:row['subset'] for row in receipt['source_mapping']}
    if len(rows)!=len(expected) or {row['id']:row['subset'] for row in rows}!=expected:
        raise ValueError('Model dataset revision differs from frozen source/subset membership')
    expected_classes={row['geti_label_id']:row['name'] for row in receipt['class_mapping']}
    model_classes={row['id']:row['name'] for row in training.get('label_schema_revision',{}).get('labels',[])}
    if model_classes!=expected_classes:
        raise ValueError('Model label schema differs from transferred class identities')
    return revision


def list_platform_models(client, receipt):
    if receipt.get('revision')!=_digest(_revision_body(receipt)):
        raise ValueError('Transfer receipt changed')
    raw=client.request('GET',f"/api/projects/{receipt['geti_project_id']}/models")
    if not isinstance(raw,list): raise ValueError('Geti model catalog is malformed')
    models=[]
    for original in raw:
        model=copy.deepcopy(original)
        _uuid(model['id'])
        try:
            revision=_bound_revision(client,receipt,model)
            state='verified'; message=None
        except ValueError as exc:
            revision=(model.get('training_info') or {}).get('dataset_revision_id')
            state='unverified'; message=str(exc)
        model.update(platform='geti',task=receipt['task'],dataset_revision_id=revision,
            dataset_version_id=receipt['dataset_version_id'],dataset_fingerprint=receipt['dataset_fingerprint'],
            labelset_id=receipt['labelset_id'],recipe_sha256=receipt.get('recipe_sha256'),
            transfer_id=receipt['id'],binding_state=state)
        if message: model['binding_message']=message
        for variant in model.get('variants',[]):
            _uuid(variant['id'])
            variant['prediction_supported']=(state=='verified' and variant.get('format') in _PREDICT_FORMATS
                and not variant.get('files_deleted') and variant.get('weights_size',0)>0)
            if variant.get('format') not in _PREDICT_FORMATS:
                variant['prediction_message']='This format is export-only; select OpenVINO or ONNX for platform inference'
        models.append(model)
    return models


def _resolve(client,receipt,model_id,variant_id):
    _uuid(model_id); _uuid(variant_id)
    models=list_platform_models(client,receipt)
    model=next((m for m in models if m['id']==model_id),None)
    if model is None: raise ValueError('Model does not belong to this transferred project')
    variant=next((v for v in model['variants'] if v['id']==variant_id),None)
    if variant is None: raise ValueError('Variant does not belong to the selected model')
    if model['binding_state']!='verified': raise ValueError(model['binding_message'])
    if variant.get('files_deleted') or not variant.get('weights_size',0): raise ValueError('Model variant artifact is unavailable')
    return model,variant


def _artifact_members(payload,fmt):
    """Validate, hash, and retain the actual export without extracting executable files."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names=archive.namelist()
            if len(set(names))!=len(names) or len(names)>10000: raise ValueError('Model export contains duplicate/excessive members')
            if sum(info.file_size for info in archive.infolist()) > 4*1024**3: raise ValueError('Model export exceeds size limit')
            for info in archive.infolist():
                path=PurePosixPath(info.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in info.filename or (info.external_attr >> 16)&0o170000 == 0o120000:
                    raise ValueError('Model export contains an unsafe path')
            required={'openvino':{'model.xml','model.bin'},'onnx':{'model.onnx'},'pytorch':{'model.pt'}}.get(fmt)
            if required is None: raise ValueError('Unknown exported model format')
            if fmt=='pytorch' and not required.issubset(names):
                required={name for name in names if name.startswith('model/') and not name.endswith('/')}
            if not required or not required.issubset(names): raise ValueError('Model export is missing format-specific weights')
            return {name:_bytes_hash(archive.read(name)) for name in sorted(required)}
    except (zipfile.BadZipFile,KeyError) as exc:
        raise ValueError('Geti model export is not a valid format-specific archive') from exc


def export_platform_model(client,receipt,model_id,variant_id):
    model,variant=_resolve(client,receipt,model_id,variant_id)
    prefix=f"/api/projects/{receipt['geti_project_id']}/models/{model_id}/variants/{variant_id}"
    payload=client.request('GET',prefix+'/binary',timeout=180)
    if not isinstance(payload,bytes): raise ValueError('Geti model export is not binary')
    members=_artifact_members(payload,variant['format'])
    directory=Path(receipt['transfer_root'])/'model_exports'; directory.mkdir(parents=True,exist_ok=True)
    path=directory/f'{model_id}-{variant_id}.zip'; metadata=path.with_suffix('.json')
    content_digest=_digest(members)
    # Thread serialization plus the existing OS lock prevents two parent
    # processes from publishing a mismatched archive/metadata pair. Downloads
    # stay outside the critical section; the first verified archive is retained.
    from backend.engine.runtime_process_control import runtime_state_lock
    with _EXPORT_PUBLICATION, runtime_state_lock(directory):
        if path.is_symlink() or metadata.is_symlink():
            raise ValueError('Pinned model export publication is linked')
        if metadata.exists():
            previous=json.loads(metadata.read_text(encoding='utf-8'))
            if previous.get('weights_sha256')!=content_digest or previous.get('dataset_revision_id')!=model['dataset_revision_id']:
                raise ValueError('Same Geti model/variant identity now contains different weights/revision')
            retained=path.read_bytes() if path.is_file() else None
            if retained is None or _bytes_hash(retained)!=previous.get('export_sha256'):
                raise ValueError('Pinned model export archive changed')
            return {**previous,'data':retained}
        if path.exists():
            # A crash between the two atomic publications may leave an orphan
            # archive. Recover only identical verified weight contents.
            retained=path.read_bytes()
            if _digest(_artifact_members(retained,variant['format']))!=content_digest:
                raise ValueError('Unconfirmed retained model export contains different weights')
            payload=retained
        else:
            with tempfile.NamedTemporaryFile('wb',dir=directory,delete=False) as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno()); temporary=Path(handle.name)
            try: os.replace(temporary,path)
            finally: temporary.unlink(missing_ok=True)
        result={'model_id':model_id,'variant_id':variant_id,'format':variant['format'],'export_sha256':_bytes_hash(payload),
            'weights_sha256':content_digest,'members':members,'dataset_revision_id':model['dataset_revision_id'],
            'dataset_version_id':receipt['dataset_version_id'],'recipe_sha256':receipt.get('recipe_sha256')}
        _atomic_json(metadata,result)
        return {**result,'data':payload}


def _current_project(receipt):
    root=Path(receipt['training_binding']['version_dir']).parent.parent
    project=json.loads((root/'project.json').read_text(encoding='utf-8'))
    project['project_dir']=str(root)
    registry=root/'labelsets.json'
    if registry.is_file():
        from backend.engine.project_labelsets import labelset_root
        project['active_labelset_id']=json.loads(registry.read_text(encoding='utf-8'))['active_id']
        project['annotations_dir']=str(labelset_root(root,project['active_labelset_id']))
    return project


def predict_platform_model(client, receipt, model_id, variant_id, image_path):
    project=_current_project(receipt)
    validate_transfer(project,receipt)
    image=Path(image_path).resolve(strict=True)
    source=Path(project['source_dataset_dir']).resolve(strict=True)
    if not image.is_relative_to(source): raise ValueError('Prediction image is outside admitted source')
    relative=image.relative_to(source).as_posix()
    row=next((r for r in receipt['source_mapping'] if r['relative_path']==relative),None)
    if row is None: raise ValueError('Prediction image is absent from frozen transfer cohort; prepare a new version')
    if _bytes_hash(image.read_bytes())!=row['sha256']: raise ValueError('Prediction source image hash changed')
    payload,geometry,_,_=prepared_image_bytes(image,receipt.get('input_recipe'))
    if _bytes_hash(payload)!=row['uploaded_sha256'] or geometry!=row['geometry']:
        raise ValueError('Prediction fixed-input recipe differs from training copy')
    model,variant=_resolve(client,receipt,model_id,variant_id)
    if not variant['prediction_supported']: raise ValueError(variant.get('prediction_message','Variant cannot perform inference'))
    # Upstream media:predict reads these exact already-prepared bytes; it does not create or relabel media.
    validate_platform_dataset(client,receipt)
    artifact=export_platform_model(client,receipt,model_id,variant_id)
    raw=client.request('POST',f"/api/projects/{receipt['geti_project_id']}/dataset/media:predict",json={
        'model_id':model_id,'model_variant_id':variant_id,'media':[{'media_id':row['media_id'],'range':None}],'device':'cpu'},timeout=180)
    predictions=raw.get('predictions') if isinstance(raw,dict) else None
    if not isinstance(predictions,list) or len(predictions)!=1 or predictions[0].get('media',{}).get('id')!=row['media_id']:
        raise ValueError('Geti returned prediction for another media or no completed result')
    annotations=predictions[0].get('prediction')
    if not isinstance(annotations,list): raise ValueError('Geti prediction annotations are malformed')
    _from_platform(annotations,receipt['task'],receipt['class_mapping'],geometry['source_size'])
    names={c['geti_label_id']:c['name'] for c in receipt['class_mapping']}
    output=[]
    for annotation in annotations:
        confidences=annotation.get('confidences')
        value={'label':names[annotation['labels'][0]['id']],'shape':copy.deepcopy(annotation['shape'])}
        if confidences is not None:
            if len(confidences)!=1 or type(confidences[0]) not in (int,float) or not math.isfinite(confidences[0]) or not 0<=confidences[0]<=1:
                raise ValueError('Geti returned malformed confidence')
            value['confidence']=confidences[0]
        output.append(value)
    validate_transfer(project,receipt)
    return {**{key:value for key,value in artifact.items() if key not in ('data','members')},
        'image_sha256':row['sha256'],'recipe_sha256':receipt.get('recipe_sha256'),'coordinate_space':'original',
        'source_size':geometry['source_size'],'predictions':output,'raw':raw}


def predict_inspection_image(project,client,receipt,model_id,variant_id,image_path):
    """Inspect an admitted source image without adding it to Geti's training data."""
    from backend.engine.grouped_dataset_views import source_image_paths
    from .dataset_transfer import validate_inspection_binding
    validate_inspection_binding(project,receipt)
    image=Path(image_path).resolve(strict=True)
    source=Path(project['source_dataset_dir']).resolve(strict=True)
    if not image.is_relative_to(source) or image not in {path.resolve() for path in source_image_paths(source,project['task'])}:
        raise ValueError('Inspection image is outside admitted project source inventory')
    original_hash=_bytes_hash(image.read_bytes())
    payload,geometry,suffix,content_type=prepared_image_bytes(image,receipt.get('input_recipe'))
    model,variant=_resolve(client,receipt,model_id,variant_id)
    if not variant['prediction_supported']: raise ValueError(variant.get('prediction_message','Variant cannot perform inference'))
    validate_platform_dataset(client,receipt)
    artifact=export_platform_model(client,receipt,model_id,variant_id)
    raw=client.request('POST',f"/modu/projects/{receipt['geti_project_id']}/models/{model_id}/variants/{variant_id}/predict",
        files={'file':('inspection'+suffix,payload,content_type)},timeout=180)
    predictions=raw.get('predictions') if isinstance(raw,dict) else None
    if not isinstance(predictions,list) or len(predictions)!=1:
        raise ValueError('Geti returned no completed inspection prediction')
    _uuid(predictions[0].get('media',{}).get('id'))
    annotations=predictions[0].get('prediction')
    if not isinstance(annotations,list): raise ValueError('Geti inspection annotations are malformed')
    _from_platform(annotations,receipt['task'],receipt['class_mapping'],geometry['source_size'])
    names={row['geti_label_id']:row['name'] for row in receipt['class_mapping']}
    output=[]
    for annotation in annotations:
        value={'label':names[annotation['labels'][0]['id']],'shape':copy.deepcopy(annotation['shape'])}
        confidence=annotation.get('confidences')
        if confidence is not None:
            if len(confidence)!=1 or type(confidence[0]) not in (int,float) or not math.isfinite(confidence[0]) or not 0<=confidence[0]<=1:
                raise ValueError('Geti returned malformed confidence')
            value['confidence']=confidence[0]
        output.append(value)
    if _bytes_hash(image.read_bytes())!=original_hash: raise ValueError('Inspection source image changed during inference')
    validate_inspection_binding(project,receipt)
    return {**{key:value for key,value in artifact.items() if key not in ('data','members')},
        'image_sha256':original_hash,'recipe_sha256':receipt.get('recipe_sha256'),'coordinate_space':'original',
        'source_size':geometry['source_size'],'predictions':output,'raw':raw}
