"""Frozen, project-owned test inputs for the existing remote evaluation worker.

The cohort identifies pixels/truth/eligibility. A separate binding identifies the
completed checkpoint, original server, explicit device, and transfer archive.
"""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from threading import Event
import urllib.parse

from backend.remote.coordinator import ArtifactValidationError, _atomic_json, _sha256
from backend.remote.snapshot import build_snapshot, extract_snapshot, verify_snapshot_tree

MAX_IMAGES = 500
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_DESCRIPTOR_BYTES = 16 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('Invalid cohort relative path')
    parts = value.split('/')
    if value.startswith('/') or any(part in ('', '.', '..') for part in parts):
        raise ValueError('Cohort path escaped its owned directory')
    return Path(*PurePosixPath(value).parts)


def plain_file(path, root):
    path, root = Path(path), Path(root)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent == root or root in parent.parents):
        raise ValueError('Cohort input or ancestor is linked')
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Cohort input is missing or outside its source')
    return path


def _copy_verified(source, destination, expected, *, limit=MAX_IMAGE_BYTES):
    if source.stat().st_size > limit:
        raise ValueError('Cohort file exceeds transfer limit')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if _sha256(destination) != expected or _sha256(source) != expected:
        raise ValueError('Cohort source changed while freezing')


def _classes(meta, task):
    names = meta.get('classes')
    if task == 'anomaly':
        return ['good', 'anomaly']
    if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n or '/' in n or '\\' in n or n in ('.', '..') for n in names) or len(set(names)) != len(names):
        raise ValueError('Completed model has no unambiguous class mapping')
    if task == 'segmentation' and names[0] != 'background':
        raise ValueError('Segmentation class mapping must explicitly begin with background')
    if task == 'detection':
        from backend.engine.detection import foreground_class_names
        names = foreground_class_names(names)
    return names


def _frozen_view(source, version_dir, manifest, staging, selected):
    """Read every label from the version backup, never an external mask pointer."""
    from backend.engine.annotation_storage import dataset_annotation_dir
    shadow = staging / 'source'
    annotations = staging / 'annotations'
    source_rows = {r['relative_path']: r for r in manifest['files'] if r['origin'] == 'source'}
    for image in selected:
        relative = image.relative_to(source).as_posix()
        row = source_rows.get(relative)
        if row is None or row['kind'] != 'image':
            raise ValueError('Selected image is not in the version inventory')
        _copy_verified(plain_file(image, source), shadow / safe_relative(relative), row['sha256'])
    parent_map = {dataset_annotation_dir(source, annotations, use_scope=False).name: shadow}
    for row in manifest['files']:
        if row['origin'] == 'source' and row['kind'] == 'image':
            original_parent = (source / safe_relative(row['relative_path'])).parent
            parent_map[dataset_annotation_dir(original_parent, annotations, use_scope=False).name] = shadow / original_parent.relative_to(source)
    destinations={}
    for row in manifest['files']:
        if row['origin']=='source' and row['kind']=='image' and 'ground_truth' in Path(row['relative_path']).parts:
            destination=shadow/safe_relative(row['relative_path'])
            _copy_verified(plain_file(source/safe_relative(row['relative_path']),source),destination,row['sha256'])
            destinations[row['source_path']]=destination
        if row['kind'] != 'label' or row['origin'] == 'split':
            continue
        backup = plain_file(version_dir / safe_relative(row['snapshot_path']), version_dir)
        if row['origin'] == 'source':
            destination = shadow / safe_relative(row['relative_path'])
        elif row['origin'] == 'studio':
            destination = dataset_annotation_dir(shadow, annotations, use_scope=False) / safe_relative(row['relative_path'])
        elif row['origin'] == 'studio_scoped':
            relative = safe_relative(row['relative_path'])
            parent = parent_map.get(relative.parts[0])
            if parent is None:
                raise ValueError('Frozen annotation has no versioned image parent')
            destination = dataset_annotation_dir(parent, annotations, use_scope=False) / Path(*relative.parts[1:])
        else:
            raise ValueError('Invalid frozen label origin')
        _copy_verified(backup, destination, row['sha256'], limit=MAX_EXPANDED_BYTES)
        destinations[row['source_path']]=destination
    for destination in destinations.values():
        if destination.suffix.lower()=='.json':
            document=json.loads(destination.read_text(encoding='utf-8'))
            if isinstance(document,dict) and document.get('mask_file'):
                mask=Path(document['mask_file'])
                if not mask.is_absolute():mask=source/mask
                frozen_mask=destinations.get(str(mask.resolve()))
                if frozen_mask is None:raise ValueError('Mask pointer is outside the versioned source labels')
                document['mask_file']=str(frozen_mask)
                destination.write_bytes(canonical(document))
    return shadow, annotations


def _test_selection(source, task, version_dir, manifest):
    from backend.engine.grouped_dataset_views import source_image_paths
    from backend.engine.dataset_inventory import folder_label_split
    available = source_image_paths(source, task)
    if any(path.is_symlink() for path in source.rglob('*')) or source.is_symlink():
        raise ValueError('Linked cohort source inputs are unsupported')
    split_rows = [r for r in manifest['files'] if r['origin'] == 'split']
    if len(split_rows) > 1:
        raise ValueError('Ambiguous frozen split')
    if split_rows:
        row = split_rows[0]
        path = plain_file(version_dir / safe_relative(row['snapshot_path']), version_dir)
        if _sha256(path) != row['sha256']:
            raise ValueError('Frozen split checksum changed')
        data = json.loads(path.read_text(encoding='utf-8'))
        assignments = data.get('assignments')
        if data.get('folder_path') != str(source) or not isinstance(assignments, dict):
            raise ValueError('Frozen split belongs to a different source')
        assigned = {str(source / safe_relative(name)): partition for name, partition in assignments.items()}
        if any(partition not in ('train', 'val', 'test') for partition in assigned.values()):
            raise ValueError('Invalid frozen split partition')
        # Usage exclusions stay excluded; missing and hidden entries cannot become test.
        from backend.engine.dataset_usage import unused_image_paths
        unused = unused_image_paths(source)
        if set(assigned) - {str(p) for p in available} - unused:
            raise ValueError('Frozen split contains missing or ineligible images')
        selected = [p for p in available if assigned.get(str(p)) == 'test']
    else:
        selected = [p for p in available if folder_label_split(p.relative_to(source), task)[1] == 'test']
    if not 1 <= len(selected) <= MAX_IMAGES:
        raise ValueError('Common evaluation requires 1 to 500 saved test images; validation/automatic splits are unsupported')
    return selected, available


def freeze_cohort(project, version_id, source, task, meta, context):
    from fastapi import HTTPException
    from backend.api.routes_dataset_versions import _read_manifest, _verify, _require_active_labelset
    from backend.engine.annotation_storage import set_request_annotation_root, reset_request_annotation_root
    from backend.engine.grouped_dataset_views import _annotations, _mask_class_mapping, ManifestSegmentationDataset, is_anomaly_normal
    from backend.engine.dataset_loaders import DetectionDataset
    from backend.engine.annotation_formats import export_annotations
    from backend.engine.dicom_input import open_source_image
    import numpy as np
    from PIL import Image

    source = Path(source)
    version_dir, manifest = _read_manifest(project, version_id)
    _require_active_labelset(project, manifest)
    if task not in ('classification', 'detection', 'segmentation', 'anomaly') or manifest.get('task') != task or project.get('task') != task:
        raise HTTPException(422, 'Common cohort task differs from active project or completed model')
    if source.resolve() != Path(manifest['source_dataset_dir']).resolve() or source.resolve() != Path(project['source_dataset_dir']).resolve():
        raise HTTPException(409, 'Common cohort source differs from selected version or active project')
    verify = _verify(project, version_dir, manifest)
    if verify['status'] != 'verified' or verify['editable_changed_files']:
        raise HTTPException(409, 'Common cohort version has changed pixels, labels, or split')
    if (meta.get('training_provenance') or {}).get('labelset_id',meta.get('labelset_id','default')) != manifest.get('labelset_id', 'default'):
        raise HTTPException(409, 'Model label set differs from common cohort')
    if len(manifest['files'])>10000:raise ValueError('Version inventory exceeds cohort preparation bound')
    label_bytes=sum(plain_file(version_dir/safe_relative(r['snapshot_path']),version_dir).stat().st_size for r in manifest['files'] if r['kind']=='label')
    if label_bytes>MAX_EXPANDED_BYTES:raise ValueError('Frozen version labels exceed preparation limit')
    classes = _classes(meta, task)
    selected, available = _test_selection(source, task, version_dir, manifest)
    image_bytes=sum(p.stat().st_size for p in selected)
    mask_bytes=sum(plain_file(source/safe_relative(r['relative_path']),source).stat().st_size for r in manifest['files'] if r['origin']=='source' and r['kind']=='image' and 'ground_truth' in Path(r['relative_path']).parts)
    if any(p.stat().st_size>MAX_IMAGE_BYTES for p in selected) or image_bytes+label_bytes+mask_bytes>MAX_EXPANDED_BYTES:
        raise ValueError('Frozen pixels and labels exceed local preparation limit')
    # The actual frozen training snapshot provides content proof, never filenames.
    from backend.engine.dataset_inventory import is_inventory_path, folder_label_split
    old = getattr(context,'dataset_path',None)
    selection_hashes = set(getattr(context,'selection_sha256',()))
    for path in old.rglob('*') if old is not None else ():
        if path.is_file() and is_inventory_path(path.relative_to(old).parts, task):
            partition = folder_label_split(path.relative_to(old), task)[1]
            if partition is None:
                raise ValueError('Original training input lacks partition provenance')
            if partition in ('train', 'val'): selection_hashes.add(_sha256(path))
    if not selection_hashes:
        raise ValueError('Original training selection pixels are unavailable')
    if any(_sha256(path) in selection_hashes for path in selected):
        raise HTTPException(409, 'Common test pixels overlap original training or model selection inputs')
    root = Path(project['reports_dir']) / 'evaluation_cohorts'
    if root.is_symlink(): raise ValueError('Cohort output root is linked')
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.freezing-', dir=root) as temporary:
        staging = Path(temporary)
        shadow, annotations = _frozen_view(source, version_dir, manifest, staging, selected)
        data = staging / 'data'
        data.mkdir()
        samples, detection_rows = [], []
        token = set_request_annotation_root(annotations)
        try:
            for index, image in enumerate(selected):
                relative = image.relative_to(source).as_posix()
                frozen_image = shadow / safe_relative(relative)
                with open_source_image(frozen_image) as opened: width, height = opened.size
                name = f'{index:06d}' + image.suffix.lower()
                truth = None
                if task == 'classification':
                    truth = image.parent.name
                    if truth not in classes: raise ValueError('Cohort contains a class absent from the completed model')
                    target = Path('test') / truth / name
                elif task == 'detection':
                    target = Path('images/test') / name
                    regions, _ = _annotations(shadow, frozen_image)
                    if regions is None: raise ValueError('Missing frozen detection labels')
                    rows = []
                    for annotation in regions:
                        if annotation.get('type') == 'tag':
                            if not annotation.get('is_normal'): raise ValueError('Detection tag has no region')
                            continue
                        if annotation.get('label') not in classes: raise ValueError('Unknown detection class')
                        item = dict(annotation)
                        if item.get('type') == 'rotated_bbox':
                            import cv2
                            cx, cy, w, h, angle = item['rotated_bbox']
                            points = cv2.boxPoints(((cx, cy), (w, h), angle))
                            item.update(type='bbox', bbox=[max(0, float(points[:,0].min())), max(0, float(points[:,1].min())), min(width, float(points[:,0].max())), min(height, float(points[:,1].max()))])
                        rows.append(item)
                    detection_rows.append({'file_name': name, 'width': width, 'height': height, 'annotations': rows})
                    truth = {'regions': rows}
                elif task == 'segmentation':
                    target = Path('images/test') / name
                    mask_names=_mask_class_mapping(frozen_image)
                    if any(type(index) is not int or index>=len(classes) or classes[index]!=name for index,name in mask_names.items()):
                        raise ValueError('Saved mask class names differ from completed model mapping')
                    dataset = ManifestSegmentationDataset(shadow, [frozen_image], max_dim=max(width,height), class_names=classes)
                    _, mask = dataset[0]
                    raster = mask.numpy().astype(np.uint8)
                    mask_path = data / 'masks/test' / (Path(name).stem + '.png')
                    mask_path.parent.mkdir(parents=True, exist_ok=True)
                    Image.fromarray(raster).save(mask_path)
                    truth = {'mask_path': mask_path.relative_to(data).as_posix(), 'mask_sha256': _sha256(mask_path), 'classes': sorted(set(raster.ravel().tolist()))}
                else:
                    normal = is_anomaly_normal(image, source)
                    category = 'good' if normal else 'defect'
                    target = Path('test') / category / name
                    mask = next((p for p in (shadow/'ground_truth'/image.parent.name/(image.stem+'_mask.png'), shadow/'ground_truth'/image.parent.name/image.name) if p.is_file()), None)
                    truth = {'label': 'good' if normal else 'anomaly'}
                    if not normal and meta.get('anomaly_mode') == 'segmentation' and mask is None:
                        raise ValueError('Anomaly segmentation requires frozen masks for every defect image')
                    if mask is not None:
                        mask_target = data/'ground_truth'/category/(Path(name).stem+'_mask.png')
                        _copy_verified(mask, mask_target, _sha256(mask))
                        truth.update(mask_path=mask_target.relative_to(data).as_posix(), mask_sha256=_sha256(mask_target))
                _copy_verified(frozen_image, data/target, _sha256(image))
                samples.append({'sample_id': f'sample_{index:06d}', 'source_relative_path': relative,
                                'path': target.as_posix(), 'sha256': _sha256(data/target), 'size': image.stat().st_size,
                                'width': width, 'height': height, 'truth': truth})
            if task == 'anomaly':
                # The existing anomaly loader partitions a lone test folder. An
                # explicit empty validation folder keeps every frozen test item.
                (data/'val').mkdir()
                (data/'val/.saved-empty-partition').write_bytes(b'')
            if task == 'detection':
                coco_rows=[{**row,'annotations':[{k:v for k,v in a.items() if k!='direction_deg'} for a in row['annotations']]} for row in detection_rows]
                coco = export_annotations(coco_rows, 'coco')
                for annotation,native in zip(coco['annotations'],[a for row in detection_rows for a in row['annotations']]):
                    for key in ('rotated_bbox','direction_deg'):
                        if native.get(key) is not None:annotation[key]=native[key]
                if not coco['categories']: coco['categories']=[{'id':i+1,'name':n} for i,n in enumerate(classes)]
                # Dense IDs always follow the completed model's order, including absent classes.
                old_names = {r['id']: r['name'] for r in coco['categories']}
                for row in coco['annotations']: row['category_id'] = classes.index(old_names[row['category_id']])+1
                coco['categories']=[{'id':i+1,'name':n} for i,n in enumerate(classes)]
                (data/'annotations_test.json').write_bytes(canonical(coco))
                loader=DetectionDataset(images_dir=data/'images/test', annotation_file=data/'annotations_test.json', class_names=classes,max_dim=max(max(r['width'],r['height']) for r in samples))
                by_name={Path(row['path']).name:row for row in samples}
                for index in range(len(loader)):
                    _,target=loader[index]
                    sample=by_name[loader.images[loader.image_ids[index]]['file_name']]
                    sample['truth']['objects']=[{'label':classes[int(label)-1],'box':[float(v) for v in box]} for box,label in zip(target['boxes'],target['labels'])]
        finally:
            reset_request_annotation_root(token)
        _verify_loader_samples(data,task,classes,samples)
        from backend.api.routes_evaluation import _evaluation_class_semantics
        semantics = _evaluation_class_semantics(meta,task)
        body = {'schema_version':1,'task':task,'split':'test','classes':classes,'class_semantics':semantics,
                'ordered_samples':samples,'eligibility_sha256':digest([p.relative_to(source).as_posix() for p in available]),
                'truth_sha256':digest([row['truth'] for row in samples])}
        cohort_hash = digest(body)
        descriptor = {**body,'cohort_sha256':cohort_hash}
        if len(canonical(descriptor)) > MAX_DESCRIPTOR_BYTES: raise ValueError('Cohort descriptor exceeds limit')
        (data/'cohort.json').write_bytes(canonical(descriptor))
        snapshot = build_snapshot(data, staging/'snapshot', Event())
        expanded = sum(p.stat().st_size for p in data.rglob('*') if p.is_file())
        if expanded > MAX_EXPANDED_BYTES: raise ValueError('Cohort expanded data exceeds limit')
        final = root / ('cohort_'+cohort_hash)
        if final.is_symlink(): raise ValueError('Cohort directory is linked')
        if not final.exists():
            try: os.rename(staging/'snapshot', final)
            except FileExistsError: pass
        verify_snapshot_tree(final, snapshot.manifest_sha256, allow_archive=True)
        if _verify(project, version_dir, manifest)['status'] != 'verified' or _verify(project, version_dir, manifest)['editable_changed_files']:
            raise HTTPException(409, 'Common inputs changed while freezing')
    snapshot_archive = final/'snapshot.tar.gz'
    if not snapshot_archive.is_file():
        # Snapshot helper names its retained archive inputs.tar.gz.
        snapshot_archive = final/'input.tar.gz'
    if not snapshot_archive.is_file():
        snapshot_archive = next(final.glob('*.tar.gz'))
    return {'descriptor':descriptor,'source':source.resolve(),'directory':final,'archive':snapshot_archive,
            'dataset_version_id':version_id,'version_manifest_sha256':manifest['content_digest'],
            'labelset_id':manifest.get('labelset_id','default'),'project_id':project['id'],'project':dict(project),
            'snapshot_manifest_sha256':snapshot.manifest_sha256,'archive_sha256':_sha256(snapshot_archive),
            'archive_size':snapshot_archive.stat().st_size,'expanded_bytes':expanded,'image_count':len(samples)}



def _verify_loader_samples(data,task,classes,samples):
    from backend.engine.dataset_loaders import ClassificationDataset,DetectionDataset,SegmentationDataset,AnomalyDataset
    if task=='classification':
        loader=ClassificationDataset(data,split='test');paths=[str(p.resolve()) for p,_ in loader.samples]
    elif task=='detection':
        loader=DetectionDataset(images_dir=data/'images/test',annotation_file=data/'annotations_test.json',class_names=classes)
        paths=[str((loader.images_dir/loader.images[i]['file_name']).resolve()) for i in loader.image_ids]
    elif task=='segmentation':
        loader=SegmentationDataset(images_dir=data/'images/test',masks_dir=data/'masks/test')
        paths=[str(p.resolve()) for p,_ in loader.samples]
    else:
        loader=AnomalyDataset(data,split='test');paths=[str(p.resolve()) for p,_,_ in loader.samples]
    expected=[str((data/safe_relative(row['path'])).resolve()) for row in samples]
    if len(paths)!=len(set(paths)) or sorted(paths)!=sorted(expected):
        raise ValueError('Production loader sample readback differs from frozen test cohort')


def cohort_spec(cohort):
    return {key:cohort[key] for key in ('dataset_version_id','version_manifest_sha256','labelset_id','project_id',
            'snapshot_manifest_sha256','archive_sha256','archive_size','expanded_bytes','image_count')} | {
            'archive_path':'inputs/cohort.tar.gz','cohort_sha256':cohort['descriptor']['cohort_sha256'],
            'truth_sha256':cohort['descriptor']['truth_sha256'],'task':cohort['descriptor']['task'],'split':'test',
            'ordered_samples':cohort['descriptor']['ordered_samples']}


def validate_binding(spec):
    if spec.get('common_cohort_contract') != 1 or not isinstance(spec.get('evaluation_cohort'),dict):
        raise ValueError('Invalid common cohort contract')
    expected = digest({k:v for k,v in spec.items() if k not in ('evaluation_binding_sha256','resources')})
    if spec.get('evaluation_binding_sha256') != expected:
        raise ValueError('Common evaluation immutable binding changed')
    cohort = spec['evaluation_cohort']
    from backend.contracts.context import ProjectContext
    authority=ProjectContext.model_validate(spec.get('operation_authority'))
    if authority.project_id!=cohort.get('project_id'):
        raise ValueError('Common operation authority belongs to another project')
    if cohort.get('task') != spec['task'] or cohort.get('split') != 'test' or not 1 <= cohort.get('image_count',0) <= MAX_IMAGES:
        raise ValueError('Invalid common test cohort task or count')
    native_local_mps=spec.get('native_core_evaluation_contract')==1 and spec.get('execution_target')=='local' and spec.get('device')=='mps'
    if spec.get('device') not in ('cpu','cuda:0') and not native_local_mps:
        raise ValueError('Common evaluation requires explicit CPU or logical CUDA 0')
    return cohort


def extract_cohort(run_dir, spec):
    cohort = validate_binding(spec)
    archive = plain_file(run_dir/safe_relative(cohort['archive_path']),run_dir)
    if not cohort['archive_path'].startswith('inputs/') or archive.stat().st_size != cohort['archive_size'] or _sha256(archive) != cohort['archive_sha256']:
        raise ValueError('Common cohort archive checksum or size changed')
    if type(cohort.get('expanded_bytes')) is not int or not 0 < cohort['expanded_bytes'] <= MAX_EXPANDED_BYTES:
        raise ValueError('Invalid cohort expanded byte bound')
    if archive.stat().st_size>MAX_EXPANDED_BYTES+MAX_DESCRIPTOR_BYTES:raise ValueError('Compressed cohort archive exceeds limit')
    with tarfile.open(archive,'r:gz') as opened:
        total=0;count=0
        for member in opened:
            count+=1
            safe_relative(member.name)
            if not member.isfile() and not member.isdir(): raise ValueError('Linked or special cohort archive entry')
            if member.size < 0 or member.size > MAX_EXPANDED_BYTES: raise ValueError('Cohort archive member exceeds limit')
            total+=member.size
            if member.name in ('manifest.json','data/cohort.json') and member.size>MAX_DESCRIPTOR_BYTES:raise ValueError('Cohort archive descriptor exceeds limit')
            if total > cohort['expanded_bytes']+MAX_DESCRIPTOR_BYTES or count > 5000: raise ValueError('Cohort archive exceeds expanded limits')
    data = extract_snapshot(archive, run_dir/'evaluation_input', cohort['snapshot_manifest_sha256']).data_path
    descriptor_path=plain_file(data/'cohort.json',data)
    if descriptor_path.stat().st_size > MAX_DESCRIPTOR_BYTES: raise ValueError('Cohort descriptor exceeds limit')
    descriptor=json.loads(descriptor_path.read_text(encoding='utf-8'))
    body={k:v for k,v in descriptor.items() if k!='cohort_sha256'}
    if digest(body)!=cohort['cohort_sha256'] or descriptor.get('cohort_sha256')!=cohort['cohort_sha256'] or descriptor.get('ordered_samples')!=cohort['ordered_samples'] or descriptor.get('truth_sha256')!=cohort['truth_sha256']:
        raise ValueError('Common cohort descriptor binding changed')
    for row in descriptor['ordered_samples']:
        path=plain_file(data/safe_relative(row['path']),data)
        if path.stat().st_size!=row['size'] or _sha256(path)!=row['sha256']:
            raise ValueError('Frozen cohort image checksum changed')
        truth=row['truth']
        if isinstance(truth,dict) and truth.get('mask_path'):
            if _sha256(plain_file(data/safe_relative(truth['mask_path']),data))!=truth['mask_sha256']:
                raise ValueError('Frozen cohort truth checksum changed')
    if sum(p.stat().st_size for p in data.rglob('*') if p.is_file())!=cohort['expanded_bytes']:
        raise ValueError('Cohort expanded bytes changed')
    _verify_loader_samples(data,spec['task'],descriptor['classes'],descriptor['ordered_samples'])
    return data,descriptor


def _gpu_uuid_matches(actual,expected):
    """PyTorch omits NVML's GPU- prefix; never equate MIG or different UUIDs."""
    if not isinstance(actual,str) or not isinstance(expected,str) or not actual or not expected:return False
    def canonical_uuid(value):
        match=re.fullmatch(r'(?:GPU-)?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})',value)
        return match.group(1).lower() if match else value
    return canonical_uuid(actual)==canonical_uuid(expected)


def target_identity(spec):
    from backend.engine.runtime_device import resolve_runtime_device
    from backend.engine.runtime_device_identity import runtime_device_identity
    device=resolve_runtime_device(spec['device'])
    if str(device)=='mps' and spec.get('native_core_evaluation_contract')==1 and spec.get('execution_target')=='local':
        identity={'device':'mps','process_id':os.getpid(),'device_name':'Metal MPS'}
    else:identity=runtime_device_identity(str(device))
    if identity.get('device')!=spec['device'] or not isinstance(identity.get('process_id'),int) or identity['process_id']<=0:
        raise ValueError('Evaluation runtime identity is unavailable')
    if spec['device']=='cuda:0' and (not _gpu_uuid_matches(identity.get('gpu_uuid'),spec.get('expected_runtime_gpu_uuid'))):
        raise ValueError('Evaluation ran on a different or unproven selected GPU')
    return device,identity


def worker_evaluate(spec, checkpoint, metadata, data, descriptor, run_dir, cancel):
    from backend.api import routes_evaluation
    from backend.engine.zero_escape_analyzer import is_defect_label, compute_sample_defect_score
    if _sha256(checkpoint)!=spec['checkpoint_sha256'] or _sha256(checkpoint.parent/'model_meta.json')!=spec['metadata_sha256']:
        raise ValueError('Completed remote checkpoint differs from selected local artifacts')
    if _classes(metadata,spec['task'])!=descriptor['classes']:
        raise ValueError('Frozen cohort class mapping differs from completed checkpoint')
    import torch
    checkpoint_body=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if not isinstance(checkpoint_body,dict) or checkpoint_body.get('task')!=spec['task']:
        raise ValueError('Completed checkpoint task is absent or differs from metadata')
    if _classes(checkpoint_body,spec['task'])!=descriptor['classes']:
        raise ValueError('Completed checkpoint and metadata class order differs')
    if routes_evaluation._evaluation_class_semantics(metadata,spec['task'])!=descriptor['class_semantics']:
        raise ValueError('Completed checkpoint class roles differ from frozen cohort')
    if checkpoint_body.get('class_semantics') and routes_evaluation._evaluation_class_semantics(checkpoint_body,spec['task'])!=descriptor['class_semantics']:
        raise ValueError('Completed checkpoint class roles differ from metadata')
    device,identity=target_identity(spec)
    evaluator={'classification':routes_evaluation._evaluate_classification,'detection':routes_evaluation._evaluate_detection,
               'segmentation':routes_evaluation._evaluate_segmentation,'anomaly':routes_evaluation._evaluate_anomaly}[spec['task']]
    result=evaluator(checkpoint,metadata,data,device,cancel=cancel)
    _,final_identity=target_identity(spec)
    if final_identity!=identity: raise ValueError('Evaluation runtime identity changed during execution')
    by_path={str((data/safe_relative(r['path'])).resolve()):r for r in descriptor['ordered_samples']}
    artifacts=['outputs/eval_results.json']
    for row in result.get('test_predictions',[]):
        sample=by_path.get(row.get('file_path'))
        if sample is None: raise ValueError('Evaluator referenced image outside frozen cohort')
        row['file_path']='evaluation_input/data/'+sample['path']
        row.pop('thumbnail_url',None)
        row.update(sample_id=sample['sample_id'],input_sha256=sample['sha256'],truth_sha256=digest(sample['truth']))
        evidence=row.get('pixel_evidence')
        if spec['task']=='anomaly' and isinstance(evidence,dict):
            original=plain_file(Path(evidence['file_path']),checkpoint.parent)
            destination=run_dir/'outputs/evidence'/original.name
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(original,destination)
            relative=destination.relative_to(run_dir).as_posix()
            evidence['file_path']=relative
            if relative not in artifacts: artifacts.append(relative)
            source_mask=evidence.get('source_mask')
            if isinstance(source_mask,dict) and source_mask.get('path'):
                mask=plain_file(Path(source_mask['path']),data)
                source_mask['path']='evaluation_input/data/'+mask.relative_to(data).as_posix()
        row['is_defect']=is_defect_label(row.get('ground_truth'),descriptor['class_semantics']['roles'])
        row.setdefault('defect_score',compute_sample_defect_score(row,task=spec['task'],roles=descriptor['class_semantics']['roles']))
    for key,paths in result.get('confusion_matrix',{}).get('cell_samples',{}).items():
        if any(path not in by_path for path in paths): raise ValueError('Matrix referenced image outside frozen cohort')
        result['confusion_matrix']['cell_samples'][key]=['evaluation_input/data/'+by_path[path]['path'] for path in paths]
    result.update(job_id=spec['job_id'],task=spec['task'],evaluation_contract_version=routes_evaluation.EVALUATION_CONTRACT_VERSION,
                  class_semantics=descriptor['class_semantics'],evaluated_at=routes_evaluation.time.strftime('%Y-%m-%dT%H:%M:%SZ',routes_evaluation.time.gmtime()),
                  common_cohort={'cohort_sha256':descriptor['cohort_sha256'],'dataset_version_id':spec['evaluation_cohort']['dataset_version_id'],
                                 'image_count':len(by_path),'split':'test'},
                  execution_target=spec.get('execution_target','model_compute'),compute_profile_id=spec['compute_profile_id'],
                  compute_profile_name=spec['compute_profile_name'],compute_gpu_selector=spec['compute_gpu_selector'],
                  execution_profile_sha256=spec['execution_profile_sha256'],device=spec['device'],resolved_device=str(device),
                  runtime_device_identity=identity,remote_operation_id=run_dir.name,
                  evaluation_binding_sha256=spec['evaluation_binding_sha256'],
                  input_receipt={'checkpoint_sha256':spec['checkpoint_sha256'],'metadata_sha256':spec['metadata_sha256'],
                                 'training_snapshot_sha256':spec['input_manifest_sha256'],'archive_sha256':spec['evaluation_cohort']['archive_sha256'],
                                 'snapshot_manifest_sha256':spec['evaluation_cohort']['snapshot_manifest_sha256'],
                                 'version_manifest_sha256':spec['evaluation_cohort']['version_manifest_sha256']})
    return result,tuple(artifacts)


def _decode_mask(value):
    import numpy as np
    from PIL import Image
    if not isinstance(value,str) or not value.startswith('data:image/png;base64,') or len(value)>MAX_DESCRIPTOR_BYTES:
        raise ValueError('Missing or oversized segmentation pixel evidence')
    with Image.open(BytesIO(base64.b64decode(value.split(',',1)[1],validate=True))) as opened:
        pixels=np.asarray(opened).copy()
    if pixels.ndim!=2: raise ValueError('Segmentation evidence is not an index mask')
    return pixels


def validate_result(result,spec,cohort,artifacts):
    """Do not publish a latest cache until identity, samples, truth and pixels match."""
    import numpy as np
    import cv2
    from PIL import Image
    from backend.api.routes_evaluation import EVALUATION_CONTRACT_VERSION
    import math
    def finite_numbers(value):
        if isinstance(value,float) and not math.isfinite(value):raise ValueError('Common result contains nonfinite numeric evidence')
        if isinstance(value,dict):
            for nested in value.values():finite_numbers(nested)
        elif isinstance(value,list):
            for nested in value:finite_numbers(nested)
    finite_numbers(result)
    descriptor=cohort['descriptor'];samples=descriptor['ordered_samples']
    expected_common={'cohort_sha256':descriptor['cohort_sha256'],'dataset_version_id':cohort['dataset_version_id'],'image_count':len(samples),'split':'test'}
    if not isinstance(result,dict) or result.get('job_id')!=spec['job_id'] or result.get('task')!=spec['task'] or result.get('evaluation_contract_version')!=EVALUATION_CONTRACT_VERSION:
        raise ValueError('Common result belongs to a different job, task or contract')
    if result.get('common_cohort')!=expected_common or result.get('evaluation_binding_sha256')!=spec['evaluation_binding_sha256'] or result.get('class_semantics')!=descriptor['class_semantics']:
        raise ValueError('Common result cohort or class binding changed')
    for key in ('compute_profile_id','compute_profile_name','compute_gpu_selector','execution_profile_sha256','device'):
        if result.get(key)!=spec[key]: raise ValueError('Common result selected target binding changed')
    identity=result.get('runtime_device_identity')
    if result.get('execution_target')!=spec.get('execution_target','model_compute') or result.get('resolved_device')!=spec['device'] or not isinstance(identity,dict) or identity.get('device')!=spec['device'] or type(identity.get('process_id')) is not int or identity['process_id']<=0:
        raise ValueError('Common result runtime identity is absent or stale')
    if spec['device']=='cuda:0' and (not _gpu_uuid_matches(identity.get('gpu_uuid'),spec.get('expected_runtime_gpu_uuid'))):
        raise ValueError('Common result GPU UUID differs from selected resource')
    if result.get('input_receipt')!={'checkpoint_sha256':spec['checkpoint_sha256'],'metadata_sha256':spec['metadata_sha256'],
            'training_snapshot_sha256':spec['input_manifest_sha256'],'archive_sha256':spec['evaluation_cohort']['archive_sha256'],
            'snapshot_manifest_sha256':spec['evaluation_cohort']['snapshot_manifest_sha256'],
            'version_manifest_sha256':spec['evaluation_cohort']['version_manifest_sha256']}:
        raise ValueError('Common result input receipt changed')
    result_path=artifacts.get('outputs/eval_results.json')
    if result_path is None or result.get('remote_operation_id')!=result_path.parent.parent.name:
        raise ValueError('Common result operation receipt changed')
    metrics=result.get('metrics')
    if not isinstance(metrics,dict) or metrics.get('evaluated_split')!='test' or metrics.get('selection_overlap') is not False:
        raise ValueError('Common result does not prove an independent test partition')
    predictions=result.get('test_predictions');matrix=result.get('confusion_matrix')
    if not isinstance(predictions,list) or len(predictions)!=len(samples) or not isinstance(matrix,dict):
        raise ValueError('Common result sample count changed')
    expected={'evaluation_input/data/'+row['path']:row for row in samples}
    if len(expected)!=len(samples) or {row.get('file_path') for row in predictions if isinstance(row,dict)}!=set(expected):
        raise ValueError('Common result has duplicate, missing or foreign samples')
    classes=matrix.get('classes') or matrix.get('class_names')
    expected_classes=['background',*descriptor['classes']] if spec['task']=='detection' else descriptor['classes']
    if classes!=expected_classes: raise ValueError('Common result matrix class mapping changed')
    seen=set();expected_cells={f'{a}:{b}':[] for a in classes for b in classes}
    counts=[[0]*len(classes) for _ in classes]
    segmentation_predictions=[];segmentation_truth=[];detection_predictions=[];detection_truth=[];anomaly_maps=[];anomaly_masks=[]
    for row in predictions:
        sample=expected[row['file_path']]
        if row.get('sample_id')!=sample['sample_id'] or row['sample_id'] in seen or row.get('input_sha256')!=sample['sha256'] or row.get('truth_sha256')!=digest(sample['truth']):
            raise ValueError('Common result sample pixel or truth binding changed')
        seen.add(row['sample_id'])
        if row.get('ground_truth') not in classes or row.get('predicted_class') not in classes:
            raise ValueError('Common result contains an unknown class')
        if not isinstance(row.get('confidence'),(int,float)) or (spec['task']!='anomaly' and not 0<=row['confidence']<=1):
            raise ValueError('Common prediction confidence is invalid')
        if row.get('is_correct')!=(row['ground_truth']==row['predicted_class']):raise ValueError('Common prediction correctness changed')
        truth=sample['truth']
        if spec['task']=='classification' and row['ground_truth']!=truth:
            raise ValueError('Common classification truth changed')
        if spec['task']=='anomaly' and row['ground_truth']!=truth['label']:
            raise ValueError('Common anomaly truth changed')
        if spec['task']=='segmentation':
            evidence=row.get('pixel_evidence')
            if not isinstance(evidence,dict) or evidence.get('coordinate_space')!='model_input': raise ValueError('Common segmentation pixel evidence missing')
            predicted=_decode_mask(evidence.get('prediction_mask'));actual=_decode_mask(evidence.get('truth_mask'))
            with Image.open(cohort['directory']/'data'/safe_relative(truth['mask_path'])) as opened: original=np.asarray(opened).copy()
            resized=cv2.resize(original,(actual.shape[1],actual.shape[0]),interpolation=cv2.INTER_NEAREST)
            if list(actual.shape)!=list(reversed(spec['model_image_size'])) or actual.shape!=predicted.shape or evidence.get('shape')!=list(actual.shape) or not np.array_equal(actual,resized) or actual.max(initial=0)>=len(classes) or predicted.max(initial=0)>=len(classes):
                raise ValueError('Common segmentation truth pixels changed')
            from backend.engine.evaluation_evidence import pixel_errors
            if evidence.get('per_class')!=pixel_errors(predicted,actual,classes): raise ValueError('Common segmentation pixel counts changed')
            segmentation_predictions.append(predicted);segmentation_truth.append(actual)
            expected_truth=classes[int(np.bincount(actual[actual>0].ravel(),minlength=len(classes)).argmax())] if (actual>0).any() else classes[0]
            if row['ground_truth']!=expected_truth: raise ValueError('Common segmentation class truth changed')
        if spec['task']=='detection':
            evidence=row.get('object_evidence')
            if not isinstance(evidence,dict) or not isinstance(evidence.get('truth'),list): raise ValueError('Common detection region truth missing')
            observed_truth=evidence['truth'];expected_truth=truth['objects']
            if len(observed_truth)!=len(expected_truth) or any(a.get('label')!=b['label'] or not np.allclose(a.get('box',[]),b['box'],rtol=0,atol=1e-5) for a,b in zip(observed_truth,expected_truth)):
                raise ValueError('Common detection truth boxes changed')
            from backend.engine.evaluation_evidence import match_objects
            rebuilt=match_objects(evidence.get('predicted',[]),expected_truth,iou_threshold=evidence.get('iou_threshold',.5),confidence_threshold=evidence.get('confidence_threshold',.5))
            if any(evidence.get(key)!=rebuilt[key] for key in ('counts','per_class','matches','extra_prediction_indices','missing_truth_indices')):
                raise ValueError('Common detection object evidence counts changed')
            import torch
            def object_tensors(objects,predicted=False):
                values={'boxes':torch.tensor([r['box'] for r in objects],dtype=torch.float32).reshape(-1,4),
                        'labels':torch.tensor([classes.index(r['label']) for r in objects],dtype=torch.int64)}
                if predicted:values['scores']=torch.tensor([r['confidence'] for r in objects],dtype=torch.float32)
                return values
            detection_predictions.append(object_tensors(evidence.get('predicted',[]),True));detection_truth.append(object_tensors(expected_truth))
            expected_labels=sorted({a['label'] for a in truth['regions']})
            if row.get('ground_truth_classes')!=expected_labels: raise ValueError('Common detection class truth changed')
        if spec['task']=='anomaly':
            evidence=row.get('pixel_evidence')
            if not isinstance(evidence,dict) or evidence.get('file_path') not in artifacts: raise ValueError('Common anomaly pixel evidence missing')
            evidence_path=artifacts[evidence['file_path']]
            if _sha256(evidence_path)!=evidence.get('sha256'): raise ValueError('Common anomaly evidence checksum changed')
            import zipfile
            with zipfile.ZipFile(evidence_path) as archive:
                info=archive.infolist()
                if len(info)>MAX_IMAGES*2 or any(row.file_size>MAX_IMAGE_BYTES for row in info) or sum(row.file_size for row in info)>MAX_EXPANDED_BYTES:
                    raise ValueError('Common anomaly pixel archive exceeds expanded limits')
            with np.load(evidence_path,allow_pickle=False) as arrays:
                heatmap=arrays[evidence['heatmap_key']]
                if heatmap.ndim!=2 or not np.isfinite(heatmap).all(): raise ValueError('Invalid common anomaly heatmap')
                if row.get('score_spec')!=spec['model_score_spec'] or row.get('map_semantics')!=spec['model_map_semantics'] or row.get('anomaly_mode')!=spec['model_anomaly_mode']:
                    raise ValueError('Common anomaly calibrated score or map semantics changed')
                if evidence.get('mask_key'):
                    mask=arrays[evidence['mask_key']]
                    if mask.shape!=heatmap.shape: raise ValueError('Invalid common anomaly mask shape')
                    if truth.get('mask_path'):
                        with Image.open(cohort['directory']/'data'/safe_relative(truth['mask_path'])) as opened: original=np.asarray(opened.convert('L')).copy()
                        expected_mask=(cv2.resize(original,(mask.shape[1],mask.shape[0]),interpolation=cv2.INTER_NEAREST)>0).astype(np.uint8)
                        source_mask=evidence.get('source_mask') or {}
                        if source_mask.get('sha256')!=truth['mask_sha256'] or source_mask.get('path')!='evaluation_input/data/'+truth['mask_path']:
                            raise ValueError('Common anomaly mask source binding changed')
                    elif truth['label']=='good': expected_mask=np.zeros_like(mask)
                    else: raise ValueError('Common anomaly claims an unbound defect mask')
                    if not np.array_equal(mask,expected_mask): raise ValueError('Common anomaly truth pixels changed')
                    anomaly_maps.append(heatmap.copy());anomaly_masks.append(mask.copy())
            evidence['file_path']=str(evidence_path)
            if isinstance(evidence.get('source_mask'),dict) and truth.get('mask_path'):
                evidence['source_mask']['path']=str(cohort['directory']/'data'/safe_relative(truth['mask_path']))
        key=f"{row['ground_truth']}:{row['predicted_class']}"
        expected_cells[key].append(row['file_path'])
        counts[classes.index(row['ground_truth'])][classes.index(row['predicted_class'])]+=1
    cells=matrix.get('cell_samples')
    if not isinstance(cells,dict) or set(cells)!=set(expected_cells) or any(sorted(cells[k])!=sorted(v) for k,v in expected_cells.items()) or matrix.get('matrix')!=counts:
        raise ValueError('Common result matrix sample mapping changed')
    normalized=[[round(value/(sum(row) or 1),4) for value in row] for row in counts]
    if matrix.get('normalized_matrix')!=normalized:raise ValueError('Common result normalized matrix changed')
    if spec['task']=='classification':
        from backend.engine.classification import compute_classification_metrics
        calculated=compute_classification_metrics([classes.index(r['predicted_class']) for r in predictions],
                [classes.index(r['ground_truth']) for r in predictions],num_classes=len(classes),class_names=classes)
        keys=('accuracy','macro_precision','macro_recall','macro_f1','per_class')
    elif spec['task']=='segmentation':
        from backend.engine.segmentation import compute_segmentation_metrics
        calculated=compute_segmentation_metrics(np.array(segmentation_predictions),np.array(segmentation_truth),num_classes=len(classes),class_names=dict(enumerate(classes)))
        keys=('miou','mdice','pixel_accuracy','foreground_iou','per_class_iou','per_class_dice')
    elif spec['task']=='detection':
        from backend.engine.detection import evaluate_detections_map
        calculated=evaluate_detections_map(detection_predictions,detection_truth,num_classes=len(classes),class_names=dict(enumerate(classes)))
        keys=('mAP_50','mAP_50_95','class_ap50')
    else:
        from backend.engine.anomaly import compute_anomaly_metrics
        threshold=spec['model_score_spec']['threshold']
        if metrics.get('active_threshold')!=threshold or metrics.get('score_spec')!=spec['model_score_spec'] or metrics.get('score_basis')!='saved_model_calibration':
            raise ValueError('Common anomaly threshold differs from saved calibration')
        calculated=compute_anomaly_metrics([r['defect_score'] for r in predictions],[0 if r['ground_truth']=='good' else 1 for r in predictions],
            pixel_heatmaps=anomaly_maps,pixel_masks=anomaly_masks,fixed_threshold=threshold,threshold_comparison='gt' if spec['model_map_semantics']=='patch_score' else 'ge')
        keys=('image_auroc','pixel_auroc','f1_score')
    if any(metrics.get(key)!=calculated.get(key) for key in keys):raise ValueError('Common result metrics differ from bound sample evidence')
    for row in predictions:
        sample=expected[row['file_path']]
        image=plain_file(cohort['source']/safe_relative(sample['source_relative_path']),cohort['source'])
        if _sha256(image)!=sample['sha256']: raise ValueError('Common source pixels changed during remote execution')
        row['file_path']=str(image.resolve())
        row['thumbnail_url']=f'/api/dataset/thumbnail/{urllib.parse.quote(image.name)}?file_path={urllib.parse.quote(str(image.resolve()))}'
    matrix['cell_samples']={key:[str(cohort['source']/safe_relative(expected[path]['source_relative_path'])) for path in paths] for key,paths in cells.items()}
    predictions.sort(key=lambda row:row['sample_id'])
    return result


def execute_common(context,cohort,device,*,force_recompute=False):
    from backend.remote.operations import run_remote_operation_artifacts
    from backend.remote.profiles import get_profile_store
    from backend.remote.ssh_transport import SSHTransport
    from backend.remote import operations
    authority=operations.common_operation_authority(cohort['project_id'])
    if get_profile_store().get(context.profile.id)!=context.profile:
        raise ValueError('Original model compute profile was changed or removed')
    profile=context.profile
    if device not in ('cpu','cuda:0') or profile.allow_sharing or profile.distributed_processes!=1 or (device=='cpu' and profile.memory_budget_mb):
        raise ValueError('Common evaluation supports explicit CPU or logical CUDA 0 on one exclusive original target')
    if device=='cuda:0' and (not profile.gpu_selector or profile.gpu_selector=='all' or ',' in profile.gpu_selector):
        raise ValueError('Common CUDA evaluation requires exactly one selected physical GPU')
    expected_uuid=None
    transport=operations.SSHTransport()
    if device=='cuda:0':
        probe=transport.probe(profile)
        checks=probe.get('checks',{}) if isinstance(probe,dict) else {}
        devices=checks.get('device_inventory',{}).get('devices',[])
        selected=[row for row in devices if str(row.get('selector'))==profile.gpu_selector or row.get('uuid')==profile.gpu_selector]
        if not probe.get('ready') or len(selected)!=1 or not selected[0].get('uuid'):
            raise ValueError('Selected GPU inventory UUID is unavailable')
        expected_uuid=selected[0]['uuid']
        if profile.runtime_kind=='docker':
            visible=checks.get('visible_device_inventory',{}).get('devices',[])
            if len(visible)!=1 or visible[0].get('uuid')!=expected_uuid:
                raise ValueError('Owned container does not map selected GPU to logical CUDA 0')
    spec={'protocol_version':1,'operation':'evaluate','job_id':context.job_id,'task':context.task,
          'input_manifest_sha256':context.input_manifest_sha256,'evaluation_contract_version':2,
          'common_cohort_contract':1,'evaluation_cohort':cohort_spec(cohort),'operation_authority':authority,
          'checkpoint_sha256':_sha256(context.output_dir/'best_model.pt'),'metadata_sha256':_sha256(context.output_dir/'model_meta.json'),
          'compute_profile_id':profile.id,'compute_profile_name':profile.name,'compute_gpu_selector':profile.gpu_selector,
          'execution_profile_sha256':digest(profile.model_dump()),'expected_runtime_gpu_uuid':expected_uuid,'device':device,
          'model_image_size':json.loads((context.output_dir/'model_meta.json').read_text(encoding='utf-8')).get('image_size',[256,256])}
    if context.task=='anomaly':
        from backend.engine.score_contract import checkpoint_score_spec
        metadata=json.loads((context.output_dir/'model_meta.json').read_text(encoding='utf-8'))
        spec.update(model_score_spec=checkpoint_score_spec(context.output_dir/'best_model.pt'),
                    model_map_semantics=metadata.get('map_semantics','patch_score' if metadata.get('detector_type')=='dino_synthetic' else 'pixel_score'),
                    model_anomaly_mode=metadata.get('anomaly_mode','classification'))
    spec['evaluation_binding_sha256']=digest(spec)
    outputs=run_remote_operation_artifacts(context,'evaluate',{k:v for k,v in spec.items() if k not in ('protocol_version','operation','job_id','task','input_manifest_sha256')},
          transport=transport,force_new=force_recompute,input_files={'inputs/cohort.tar.gz':cohort['archive']})
    artifact=outputs.get('outputs/eval_results.json')
    if artifact is None: raise ArtifactValidationError('Common result artifact is missing')
    try:
        result=validate_result(json.loads(artifact.read_text(encoding='utf-8')),spec,cohort,outputs)
        if get_profile_store().get(profile.id)!=profile: raise ValueError('Selected target changed during execution')
        from backend.engine.grouped_dataset_views import source_image_paths
        if digest([p.relative_to(cohort['source']).as_posix() for p in source_image_paths(cohort['source'],context.task)])!=cohort['descriptor']['eligibility_sha256']:
            raise ValueError('Common cohort eligibility policy changed during execution')
        from backend.api.routes_dataset_versions import _read_manifest,_verify,_require_active_labelset
        from backend.api.routes_project import _load_project
        active=_load_project(Path(cohort['project']['project_dir']))
        directory,manifest=_read_manifest(active,cohort['dataset_version_id'])
        _require_active_labelset(active,manifest)
        verified=_verify(active,directory,manifest)
        if verified['status']!='verified' or verified['editable_changed_files']:raise ValueError('Common version changed during execution')
        from backend.remote.operations import remote_job_context
        remote_job_context(context.output_dir,context.job_id)
        if _sha256(context.output_dir/'best_model.pt')!=spec['checkpoint_sha256'] or _sha256(context.output_dir/'model_meta.json')!=spec['metadata_sha256']:
            raise ValueError('Selected completed model changed during execution')
    except (ValueError,KeyError,TypeError,OSError) as exc:
        from backend.remote.operations import record_common_verification
        record_common_verification(context,artifact.parent.parent.name,False,str(exc))
        raise ArtifactValidationError(f'Common evaluation receipt validation failed: {exc}') from exc
    from backend.remote.operations import record_common_verification
    record_common_verification(context,artifact.parent.parent.name,True)
    return result
