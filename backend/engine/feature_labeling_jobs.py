"""Learn a review-only region classifier from a project's manual labels."""
from __future__ import annotations
import json
import re
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from backend.engine import foundation_labeling as foundation
from backend.engine.annotation_storage import (set_request_annotation_root, reset_request_annotation_root,
    set_request_project_root, reset_request_project_root)

_ID = re.compile(r'feature_[a-f0-9]{24}\Z')


def _root(project):
    path = Path(project['project_dir']) / 'feature_label_models'
    if path.is_symlink(): raise ValueError('Feature model directory cannot be a symbolic link')
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_feature_model(project, model_id):
    import torch
    if not _ID.fullmatch(model_id): raise ValueError('Invalid feature model ID')
    directory = _root(project) / model_id
    if directory.is_symlink(): raise ValueError('Feature model directory cannot be a symbolic link')
    meta_path = directory / 'model_meta.json'; checkpoint = directory / 'classifier.pt'
    if meta_path.is_symlink() or checkpoint.is_symlink(): raise ValueError('Feature model files cannot be symbolic links')
    meta = json.loads(meta_path.read_text())
    if (not isinstance(meta,dict) or meta.get('id')!=model_id or not isinstance(meta.get('classes'),list)
            or len(meta['classes'])<2 or not all(isinstance(name,str) and name for name in meta['classes'])):
        raise ValueError('Invalid feature classifier metadata')
    from backend.engine import labeling_tasks
    receipt=labeling_tasks.read(project,meta['job_id'])
    if (meta.get('status') != 'completed' or meta.get('project_id') != project['id'] or meta.get('task')!=project['task']
            or meta.get('source_dataset_dir') != project.get('source_dataset_dir')
            or meta.get('labelset_id') != project.get('active_labelset_id', 'default')
            or receipt.get('status')!='completed' or receipt.get('model_id')!=model_id
            or receipt.get('checkpoint_sha256')!=meta.get('checkpoint_sha256')):
        raise ValueError('Feature model is not completed or belongs to another project, dataset or label set')
    if foundation.file_sha256(checkpoint) != meta['checkpoint_sha256']:
        raise ValueError('Feature classifier checkpoint SHA256 mismatch')
    try:
        state = torch.load(checkpoint, map_location='cpu', weights_only=True)
        if (not isinstance(state,dict) or set(state) != {'weight', 'bias'}
                or state['weight'].ndim!=2 or state['weight'].shape[0] != len(meta['classes'])
                or state['bias'].shape!=(len(meta['classes']),) or not torch.isfinite(state['weight']).all()
                or not torch.isfinite(state['bias']).all()):
            raise ValueError('Invalid trained feature classifier state')
    except Exception as exc:raise ValueError('Cannot load a complete trained feature classifier state') from exc
    return {**meta, 'state': state}


def list_feature_models(project):
    result = []
    for path in _root(project).glob('feature_*'):
        try:
            model = load_feature_model(project, path.name); model.pop('state'); result.append(model)
        except (OSError, ValueError, RuntimeError, KeyError,TypeError,AttributeError): continue
    return result


def train_feature_model(project, options, job, cancel):
    import torch
    from backend.api import routes_annotation, routes_label_suggestions as suggestions
    from backend.api.routes_project import _write_json
    from backend.engine.project_labelsets import load_labelsets
    from backend.engine import labeling_tasks
    from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
    source = suggestions._source_path(project)
    baseline = suggestions._dataset_fingerprint(project)
    labelset = project.get('active_labelset_id', 'default')
    def verify():
        foundation.check_cancel(cancel)
        if (not suggestions._project_binding_is_current(project) or load_labelsets(Path(project['project_dir']))['active_id'] != labelset
                or suggestions._dataset_fingerprint(project) != baseline):
            raise ValueError('Source data, labels, project or active label set changed during feature training')
    setup = {'feature_backbone': options['backbone'], 'feature_checkpoint': options.get('pretrained_checkpoint'),
             'feature_sha256': options.get('pretrained_sha256')}
    device = foundation.resolve_device(options['device'])
    annotation_token = set_request_annotation_root(Path(project['annotations_dir']))
    project_token = set_request_project_root(Path(project['project_dir']))
    crops = []; labels = []; source_receipts = []
    try:
        requested = options.get('image_paths')
        images = [suggestions._image_path(project,path) for path in requested] if requested else sorted(
            path for path in source.rglob('*') if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS)
        if len(images)>5000: raise ValueError('Select at most 5000 source images for this local feature-fitting task')
        for image in images:
            verify()
            digest = foundation.file_sha256(image)
            rgb = foundation.read_rgb(image, digest)
            annotations = routes_annotation.get_annotations(image.stem, file_path=str(image)).get('annotations', [])
            for annotation in annotations:
                label = str(annotation.get('label', '')).strip()
                if not label: continue
                if label.casefold() in {'background','__background__','배경'}: label = '__background__'
                if annotation.get('type') == 'tag': box = [0,0,rgb.width,rgb.height]
                elif annotation.get('bbox'): box = annotation['bbox']
                elif annotation.get('polygon') or annotation.get('points'):
                    points = np.asarray(annotation.get('polygon') or annotation['points'])
                    box = [*points.min(0), *points.max(0)]
                elif annotation.get('mask_rle'):
                    mask=routes_annotation._extract_mask_from_data(annotation['mask_rle'])
                    ys,xs=np.where(mask>0)
                    if not len(xs):continue
                    box=[int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)]
                else: continue
                box = foundation._box(box, rgb.width, rgb.height)
                crops.append(rgb.crop(tuple(box)).resize((224,224),Image.Resampling.BILINEAR)); labels.append(label)
                source_receipts.append({'image_path': str(image), 'image_sha256': digest, 'box': box, 'label': label})
            if foundation.file_sha256(image) != digest: raise ValueError('Source image changed during feature extraction')
    finally:
        reset_request_project_root(project_token); reset_request_annotation_root(annotation_token)
    classes = sorted(set(labels), key=lambda name: (name != '__background__', name))
    if len(classes) < 2:
        raise ValueError('Label at least two classes of regions (for example target and explicit background) before training the suggestion model. Unlabeled pixels are not invented background truth.')
    verify()
    features = foundation.extract_features(crops, setup, device, cancel)
    targets = torch.tensor([classes.index(label) for label in labels], dtype=torch.long)
    encoder = foundation.feature_encoder(setup, device)
    parent = load_feature_model(project, options['parent_model_id']) if options.get('parent_model_id') else None
    if parent and (parent['classes'] != classes or parent['feature_metadata']['pretrained_sha256'] != encoder.model_metadata['pretrained_sha256']):
        raise ValueError('Parent classifier class order or feature checkpoint differs')
    state, history = foundation.fit_feature_classifier(features, targets, len(classes),
        epochs=options['epochs'], learning_rate=options['learning_rate'], parent_state=parent['state'] if parent else None, cancel=cancel)
    verify()
    if parent and load_feature_model(project,parent['id'])['checkpoint_sha256']!=parent['checkpoint_sha256']:
        raise ValueError('Parent classifier changed during refinement')
    model_id = 'feature_' + uuid.uuid4().hex[:24]
    directory = _root(project) / model_id; directory.mkdir()
    checkpoint = directory / 'classifier.pt'
    try:
        torch.save(state, checkpoint)
        verify()
        meta = {'id': model_id, 'status': 'completed', 'project_id': project['id'], 'task': project['task'],
                'job_id':job['id'],
                'source_dataset_dir': project.get('source_dataset_dir'), 'labelset_id': labelset,
                'labelset_version': baseline, 'classes': classes, 'feature_metadata': dict(encoder.model_metadata),
                'checkpoint_path': str(checkpoint), 'checkpoint_sha256': foundation.file_sha256(checkpoint),
                'parent_model_id': parent['id'] if parent else None, 'parent_checkpoint_sha256': parent['checkpoint_sha256'] if parent else None,
                'training': options, 'training_regions': len(crops), 'source_receipts': source_receipts,
                'loss_history': history, 'verification': 'functional feature fitting; no heldout quality approval'}
        _write_json(directory / 'model_meta.json', meta)
        job.update(model_id=model_id,checkpoint_sha256=meta['checkpoint_sha256'], classes=classes, training_regions=len(crops), loss=history[-1])
        labeling_tasks.write(project, job)
    except Exception:
        checkpoint.unlink(missing_ok=True)
        (directory / 'model_meta.json').unlink(missing_ok=True)
        directory.rmdir()
        raise
