"""Read-only previews using the same RGB and target readers as training."""
import base64
import io
from hashlib import sha256
from pathlib import Path
import numpy as np
from PIL import Image
from backend.engine.input_preparation import bind_recipe, canonical_recipe, prepare_input


def _png(pixels):
    stream=io.BytesIO();Image.fromarray(pixels).save(stream,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode('ascii')


def preview_input(request, *, task, image_path, input_recipe, image_size=None):
    from backend.api.routes_project import get_current_project
    from backend.engine.dataset_loaders import _read_image_rgb
    project=get_current_project(request)
    if not project.get('source_dataset_dir'):
        raise ValueError('Connect the active source before input preparation')
    source=Path(project['source_dataset_dir']).resolve();image=Path(image_path)
    if (not image.is_absolute() or image.is_symlink() or any(parent.is_symlink() for parent in image.parents)
            or not image.resolve().is_relative_to(source) or not image.is_file()):
        raise ValueError('Preview image must be a regular file inside the active source')
    if image.stat().st_size > 32*1024*1024:
        raise ValueError('Preview image exceeds 32 MiB')
    rules=canonical_recipe(input_recipe,task)
    source_sha256=sha256(image.read_bytes()).hexdigest()
    rgb=_read_image_rgb(image)
    if rgb.shape[0]*rgb.shape[1]>25_000_000:
        raise ValueError('Preview image exceeds 25 million pixels')
    target=None
    if task=='segmentation':
        from backend.engine.grouped_dataset_views import ManifestSegmentationDataset
        dataset=ManifestSegmentationDataset(source,[image],max_dim=max(rgb.shape[:2]))
        _,target_tensor=dataset[0];target=target_tensor.numpy().astype(np.uint8)
    prepared,mask,geometry=prepare_input(rgb,rules,task=task,target=target,image_size=image_size)
    if sha256(image.read_bytes()).hexdigest()!=source_sha256:
        raise ValueError("Preview source changed while reading pixels and targets")
    def overlay(pixels,labels):
        if labels is None:return None
        result=pixels.copy();selected=labels>0
        result[selected]=(pixels[selected]*.4+np.array([34,211,238])*.6).astype(np.uint8)
        return _png(result)
    return {**bind_recipe(rules,task),'source_sha256':source_sha256,'source_size':geometry['source_size'],
            'prepared_size':geometry['prepared_size'],'geometry':geometry,
            'original_image':_png(rgb),'prepared_image':_png(prepared),
            'original_target':overlay(rgb,target),'prepared_target':overlay(prepared,mask),
            'target_status':'synchronized_mask' if target is not None else 'classification_label_unchanged'}
