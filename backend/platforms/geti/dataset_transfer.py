"""Copy verified Modu versions into fresh, owned Geti projects.

Only the pinned application's three explicit tasks are admitted. Semantic masks,
rotated geometry and specialist family truth retain their native workflow.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import uuid

from PIL import Image

SUPPORTED_TASKS = ('classification', 'detection', 'instance_segmentation')
_SUBSETS = {'train':'training', 'val':'validation', 'test':'testing'}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _bytes_hash(value):
    return hashlib.sha256(value).hexdigest()


@contextmanager
def _scope(project, annotations=None):
    from backend.engine.annotation_storage import (set_request_annotation_root, reset_request_annotation_root,
        set_request_project_root, reset_request_project_root)
    a = set_request_annotation_root(Path(annotations or project['annotations_dir']))
    p = set_request_project_root(Path(project['project_dir']))
    try:
        yield
    finally:
        reset_request_project_root(p)
        reset_request_annotation_root(a)


def _task(project, target_task=None):
    task = project.get('task')
    if target_task is not None:
        if task!='segmentation' or target_task!='instance_segmentation':
            raise ValueError('Only native segmentation can explicitly target instance_segmentation')
        return target_task
    if task not in SUPPORTED_TASKS:
        raise ValueError(f'Geti supports classification, detection and explicit instance_segmentation; {task} uses its native workflow')
    return task


def _recipe(context, task, preparation_id):
    if preparation_id is not None:
        raise ValueError('No owned Geti preparation_id resolver is registered; use the scoped fixed-input recipe')
    from backend.engine.input_preparation import canonical_recipe, recipe_sha256
    recipe = canonical_recipe(context.get('input_recipe'), task)
    return recipe, recipe_sha256(recipe) if recipe and recipe['operations'] else None


def prepared_image_bytes(image_path, recipe):
    """Use the existing deterministic numeric/FFT runtime, with no original writes."""
    if not recipe or not recipe['operations']:
        data = Path(image_path).read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            geometry = {'source_size':list(image.size), 'prepared_size':list(image.size), 'steps':[]}
        return data, geometry, Path(image_path).suffix.lower(), 'image/' + ('jpeg' if Path(image_path).suffix.lower() in ('.jpg','.jpeg') else Path(image_path).suffix.lower()[1:])
    import numpy as np
    from backend.engine.dicom_input import open_source_image
    from backend.engine.input_preparation import prepare_input
    with open_source_image(image_path) as image:
        rgb = np.asarray(image.convert('RGB')).copy()
    rgb, _, geometry = prepare_input(rgb, recipe, task='classification')
    output = io.BytesIO()
    Image.fromarray(rgb).save(output, format='PNG')
    return output.getvalue(), geometry, '.png', 'image/png'


def _points(points, width, height):
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError('Instance polygon requires at least three points')
    result=[]
    for point in points:
        x, y = (point['x'],point['y']) if isinstance(point,dict) else point
        if any(type(n) not in (int,float) or not math.isfinite(n) for n in (x,y)) or not (0 <= x <= width and 0 <= y <= height):
            raise ValueError('Polygon geometry exceeds source image')
        result.append({'x':float(x),'y':float(y)})
    return result


def _to_platform(annotations, task, width, height):
    result=[]
    for annotation in annotations:
        if (annotation.get('origin') or {}).get('kind') in {'machine','model_prediction','gpt'}:
            raise ValueError('Machine label drafts require explicit review before transfer')
        if annotation.get('direction_deg') is not None or annotation.get('rotated_bbox') is not None:
            raise ValueError('Rotated/directional truth requires its native geometry workflow')
        kind=annotation.get('type')
        if kind == 'tag' and annotation.get('is_normal'):
            continue
        label=annotation.get('label')
        if not isinstance(label,str) or not label:
            raise ValueError('Every transferred instance requires a named class')
        if task == 'detection' and kind == 'bbox':
            box=annotation.get('bbox')
            if not isinstance(box,list) or len(box)!=4:
                raise ValueError('Detection requires an explicit bbox')
            x1,y1,x2,y2=box
            if any(type(n) not in (int,float) or not math.isfinite(n) or int(n)!=n for n in box):
                raise ValueError('Geti rectangles require integer pixel edges; fractional boxes retain native workflow')
            if not (0<=x1<x2<=width and 0<=y1<y2<=height):
                raise ValueError('Detection bbox exceeds source image')
            shape={'type':'rectangle','x':int(x1),'y':int(y1),'width':int(x2-x1),'height':int(y2-y1)}
        elif task == 'instance_segmentation' and kind == 'polygon':
            shape={'type':'polygon','points':_points(annotation.get('polygon') or annotation.get('points'),width,height)}
        else:
            raise ValueError(f'Unsupported {task} label geometry {kind}; use the native workflow')
        result.append({'name':label, 'shape':shape, 'source_class_id':annotation.get('category_id')})
    return result


def _local_records(project, binding, staging, recipe, target_task=None):
    from backend.api import routes_dataset
    from backend.api.routes_dataset_versions import _read_manifest
    from backend.engine.dataset_summary import dataset_summary
    from backend.engine.grouped_dataset_views import _annotations
    from backend.engine.training_provenance import frozen_annotation_root
    from backend.engine import dataset_metadata
    task=target_task or project['task']
    source=Path(project['source_dataset_dir']).resolve()
    version, manifest=_read_manifest(project,binding['dataset_version_id'])
    inventory={row['source_path']:row for row in manifest['files'] if row['kind']=='image'}
    eligible={row['relative_path']:row for row in binding['team_data']['eligibility']}
    metadata={row['relative_path']:row for row in dataset_metadata.list_metadata(Path(project['project_dir']),source,Path(project['annotations_dir']))}
    assignments=routes_dataset._read_split_manifest(source)
    frozen=frozen_annotation_root(dict(binding),source,staging)
    records=[]
    with _scope(project,frozen):
        summary=dataset_summary(source,project['task'],assignments=assignments)
        for row in summary['items']:
            if row['relative_path'] not in eligible:
                continue
            image=Path(row['file_path']); original=inventory.get(str(image.resolve()))
            if original is None:
                raise ValueError('Admitted image is absent from immutable version')
            subset=_SUBSETS.get(row['split'])
            if subset is None:
                raise ValueError('Save exact train/validation/test assignments before preparing Geti; automatic repartition is disabled')
            payload, geometry, suffix, content_type=prepared_image_bytes(image,recipe)
            if _bytes_hash(image.read_bytes())!=original['sha256']:
                raise ValueError('Source image changed during dataset copy')
            width,height=geometry['source_size']
            if task=='classification':
                if len(row['labels'])!=1:
                    raise ValueError('Exclusive classification requires exactly one reviewed class per image')
                annotations=[{'name':row['labels'][0],'shape':{'type':'full_image'},'source_class_id':None}]
            else:
                annotations, mask=_annotations(source,image)
                if mask and not (task=='instance_segmentation' and annotations and all(
                        a.get('type')=='polygon' for a in annotations)):
                    raise ValueError('Semantic/brush raster masks do not identify separate instances; use native workflow')
                if annotations is None:
                    raise ValueError('Missing explicit object annotations')
                annotations=_to_platform(annotations,task,width,height)
            filename=f'{len(records):08d}-{image.stem}{suffix}'
            target=staging/'images'/filename; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(payload)
            records.append({'relative_path':row['relative_path'],'sha256':original['sha256'],'subset':subset,
                'label':row['labels'][0] if task=='classification' else None,'annotations':annotations,
                'uploaded_sha256':_bytes_hash(payload),'copied_path':str(target),'filename':filename,'content_type':content_type,
                'geometry':geometry,'group':metadata.get(row['relative_path'],{}).get('group',''),
                'image_uuid':eligible[row['relative_path']]['image_uuid']})
    if not records or any(not any(row['subset']==subset for row in records) for subset in _SUBSETS.values()):
        raise ValueError('Geti training needs nonempty frozen training, validation and independent testing sets')
    # Shared originals and exact duplicate pixels must not cross heldout boundaries.
    groups={}
    for row in records:
        for key in [('sha256',row['sha256']), *([('group',row['group'])] if row['group'] else [])]:
            previous=groups.setdefault(key,row['subset'])
            if previous!=row['subset']:
                raise ValueError('Common original group or duplicate image crosses frozen subsets')
    return records


def _canonical_annotations(value):
    return [{'labels':sorted(label['id'] for label in row['labels']), 'shape':row['shape']} for row in value]


def _all_items(client,path):
    rows=[]; offset=0
    while True:
        page=client.request('GET',f'{path}?limit=100&offset={offset}')
        batch=page['items']; total=page['pagination']['total']
        if not isinstance(batch,list) or type(total) is not int or total < len(rows)+len(batch):
            raise ValueError('Geti item readback pagination is malformed')
        rows.extend(batch)
        if len(rows)==total:
            return rows
        if not batch:
            raise ValueError('Geti item readback pagination stalled')
        offset+=len(batch)


def validate_platform_dataset(client, receipt, *, allow_label_edits=False):
    """Independent target readback before training or explicit label adoption."""
    prefix=f"/api/projects/{receipt['geti_project_id']}"
    project=client.request('GET',prefix)
    if project['task']['task_type']!=receipt['task']:
        raise ValueError('Geti task changed')
    classes={r['id']:r['name'] for r in project['task']['labels']}
    if classes!={r['geti_label_id']:r['name'] for r in receipt['class_mapping']}:
        raise ValueError('Geti class schema changed; prepare a fresh version')
    rows=_all_items(client,prefix+'/dataset/items')
    actual={row['id']:row['subset'] for row in rows}
    expected={row['media_id']:row['subset'] for row in receipt['source_mapping']}
    if len(rows)!=len(expected) or actual!=expected:
        raise ValueError('Geti media membership/subsets differ from frozen transfer')
    documents={}
    for row in receipt['source_mapping']:
        media=prefix+'/dataset/media/'+row['media_id']
        if _bytes_hash(client.request('GET',media+'/binary?raw=true'))!=row['uploaded_sha256']:
            raise ValueError('Geti image byte hash differs from copied version')
        annotation=client.request('GET',media+'/annotations')
        if annotation['subset']!=row['subset'] or not annotation['user_reviewed']:
            raise ValueError('Geti subset or reviewed labels differ from transfer; review predictions explicitly')
        if not allow_label_edits and _canonical_annotations(annotation['annotations'])!=_canonical_annotations(row['platform_annotations']):
            raise ValueError('Geti labels changed; explicitly adopt edits into a new Modu version before training')
        documents[row['media_id']]=annotation
    return documents


def _revision_body(receipt):
    body={key:receipt.get(key) for key in ('id','project_id','geti_project_id','task','dataset_version_id',
        'manifest_sha256','dataset_fingerprint','labelset_id','split_sha256','recipe_sha256','input_recipe',
        'class_mapping','source_mapping','training_binding')}
    if 'source_task' in receipt: body['source_task']=receipt['source_task']
    return body


def validate_transfer(project, receipt):
    from backend.engine.training_provenance import bind_training_version,validate_training_binding
    _task(project,receipt['task'] if receipt.get('source_task')=='segmentation' else None)
    if project['id']!=receipt['project_id'] or project['task']!=receipt.get('source_task',receipt['task']):
        raise ValueError('Transfer belongs to another project/task')
    if receipt.get('revision')!=_digest(_revision_body(receipt)):
        raise ValueError('Transfer receipt revision/hash changed')
    with _scope(project):
        current=bind_training_version(project,Path(project['source_dataset_dir']).resolve(),receipt['dataset_version_id'])
        if any(current.get(key)!=receipt['training_binding'].get(key) for key in current):
            raise ValueError('Source, split, active labelset or team eligibility changed since transfer')
        validate_training_binding(receipt['training_binding'])
    for row in receipt['source_mapping']:
        copied=Path(row['copied_path'])
        if copied.is_symlink() or not copied.is_file() or _bytes_hash(copied.read_bytes())!=row['uploaded_sha256']:
            raise ValueError('Frozen copied image byte hash changed')


def prepare_dataset(project, context, client, *, dataset_version_id=None, preparation_id=None, transfer_root=None):
    from backend.engine.training_provenance import bind_training_version
    task=_task(project,context.get('target_task')); recipe,recipe_hash=_recipe(context,task,preparation_id)
    source=Path(project['source_dataset_dir']).resolve(strict=True)
    root=Path(transfer_root) if transfer_root else Path(project['project_dir'])/'platforms'/'geti'/'transfers'
    if root.is_symlink(): raise ValueError('Geti transfer storage cannot be linked')
    root.mkdir(parents=True,exist_ok=True)
    staging=root/uuid.uuid4().hex; staging.mkdir()
    with _scope(project):
        binding=bind_training_version(project,source,dataset_version_id)
        records=_local_records(project,binding,staging,recipe,task)
        # Verify all source/label/split/policy state again before the first HTTP mutation.
        current=bind_training_version(project,source,binding['dataset_version_id'])
        if current!=binding: raise ValueError('Source revision changed during preparation')
    from backend.engine.class_overrides import sort_classes
    names=sort_classes({a['name'] for row in records for a in row['annotations']})
    if not names: raise ValueError('Geti requires an explicit nonempty class schema')
    target=client.request('POST','/api/projects',json={'name':f"{project.get('name','Modu')} · {binding['dataset_version_id']}",
        'task':{'task_type':task,'exclusive_labels':task=='classification','labels':[{'name':name,'color':f'#{(i*2654435761+0x336699)&0xFFFFFF:06x}'} for i,name in enumerate(names)]}})
    mapping={label['name']:label['id'] for label in target['task']['labels']}
    if set(mapping)!=set(names): raise ValueError('Geti project class readback differs')
    class_mapping=[{'modu_class_id':i,'name':name,'geti_label_id':mapping[name],
        'source_class_ids':sorted({a['source_class_id'] for row in records for a in row['annotations'] if a['name']==name and a['source_class_id'] is not None})} for i,name in enumerate(names)]
    prefix=f"/api/projects/{target['id']}"
    for row in records:
        media=client.request('POST',prefix+'/dataset/media',files={'file':(row['filename'],Path(row['copied_path']).read_bytes(),row['content_type'])})
        if [media['width'],media['height']]!=row['geometry']['prepared_size']:
            raise ValueError('Geti uploaded dimensions differ from fixed-input copy')
        row['media_id']=media['id']
        row['platform_annotations']=[{'labels':[{'id':mapping[a['name']]}],'shape':a['shape']} for a in row.pop('annotations')]
        client.request('POST',prefix+'/dataset/media/'+media['id']+'/annotations',json={'annotations':row['platform_annotations'],'subset':row['subset']})
    receipt={**{key:binding[key] for key in ('dataset_version_id','manifest_sha256','dataset_fingerprint','labelset_id','split_sha256')},
        'id':staging.name,'project_id':project['id'],'geti_project_id':target['id'],'task':task,'source_task':project['task'],
        'recipe_sha256':recipe_hash,'input_recipe':recipe,'preparation_id':preparation_id,
        'class_mapping':class_mapping,'source_mapping':records,'training_binding':binding,
        'counts':{**{subset:sum(r['subset']==subset for r in records) for subset in _SUBSETS.values()},'total':len(records)},
        'created_at':datetime.now(timezone.utc).isoformat(),'source_root':str(source),'transfer_root':str(staging)}
    receipt['revision']=_digest(_revision_body(receipt))
    validate_transfer(project,receipt)
    validate_platform_dataset(client,receipt)
    _atomic_json(staging/'receipt.json',receipt)
    return receipt


def _atomic_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',dir=path.parent,encoding='utf-8',delete=False) as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2); handle.flush(); os.fsync(handle.fileno()); temporary=Path(handle.name)
    try: os.replace(temporary,path)
    finally: temporary.unlink(missing_ok=True)


def _from_platform(annotations, task, class_mapping, size):
    names={row['geti_label_id']:row for row in class_mapping}
    result=[]
    for annotation in annotations:
        labels=annotation['labels']
        if len(labels)!=1 or labels[0]['id'] not in names: raise ValueError('Adoption requires one known class per shape')
        label=names[labels[0]['id']]; shape=annotation['shape']; kind=shape['type']
        item={'label':label['name'],'category_id':(label.get('source_class_ids') or [label['modu_class_id']+1])[0]}
        if task=='classification' and kind=='full_image': item['type']='tag'
        elif task=='detection' and kind=='rectangle':
            item.update(type='bbox',bbox=[shape['x'],shape['y'],shape['x']+shape['width'],shape['y']+shape['height']])
        elif task=='instance_segmentation' and kind=='polygon':
            item.update(type='polygon',polygon=[[p['x'],p['y']] for p in _points(shape['points'],*size)])
        else: raise ValueError('Geti label geometry cannot be adopted into this native task')
        result.append(item)
    if task=='classification' and len(result)!=1: raise ValueError('Classification adoption needs exactly one class')
    if task!='classification': _to_platform(result,task,*size)
    return result


def adopt_labels(project, context, client, receipt, *, expected_revision):
    """Publish a new independent label revision; select it through the existing labelset flow."""
    from backend.api.routes_dataset_versions import _VERSION_LOCK, _snapshot
    from backend.engine.project_labelsets import create_labelset, labelset_root
    from backend.engine.class_overrides import set_class_override
    from backend.api.routes_annotation import AnnotationSaveRequest, _save_annotations_impl
    from backend.engine import dataset_metadata
    from backend.engine.runtime_process_control import runtime_state_lock
    actor=context.get('actor')
    if not isinstance(actor,str) or not actor.strip(): raise ValueError('Explicit label adoption requires an authenticated reviewer')
    if expected_revision!=receipt.get('revision'): raise ValueError('Label adoption revision conflict')
    # Spatial recipes are unsupported for these tasks. Classification has no coordinates.
    with runtime_state_lock(Path(project['project_dir'])), _VERSION_LOCK, dataset_metadata.metadata_transaction(
            Path(project['project_dir']),Path(project['source_dataset_dir']),Path(project['annotations_dir'])):
        marker=Path(project['project_dir'])/'platforms'/'geti'/'adoptions'/f"{receipt['id']}.json"
        if marker.exists(): raise ValueError('Label adoption revision conflict: this transfer was already adopted')
        validate_transfer(project,receipt)
        documents=validate_platform_dataset(client,receipt,allow_label_edits=True)
        decoded={row['relative_path']:_from_platform(documents[row['media_id']]['annotations'],receipt['task'],receipt['class_mapping'],row['geometry']['source_size']) for row in receipt['source_mapping']}
        with _scope(project):
            labelset=create_labelset(Path(project['project_dir']),'Geti reviewed labels '+receipt['id'][:8])
        annotations=labelset_root(Path(project['project_dir']),labelset['id'])
        adopted={**project,'annotations_dir':str(annotations),'active_labelset_id':labelset['id']}
        with _scope(adopted):
            for row in receipt['source_mapping']:
                image=Path(project['source_dataset_dir'])/row['relative_path']; items=decoded[row['relative_path']]
                if receipt['task']=='classification':
                    set_class_override(Path(project['project_dir']),Path(project['source_dataset_dir']),image,items[0]['label'],actor,None)
                    dataset_metadata.annotation_changed(Path(project['project_dir']),Path(project['source_dataset_dir']),image,actor,annotations)
                else:
                    _save_annotations_impl(AnnotationSaveRequest(image_id=image.stem,image_path=str(image),annotations=items,
                        image_width=row['geometry']['source_size'][0],image_height=row['geometry']['source_size'][1],actor=actor))
                    dataset_metadata.annotation_changed(Path(project['project_dir']),Path(project['source_dataset_dir']),image,actor,annotations)
            # This version captures new labels, while old versions and the original active labelset stay byte-identical.
            version=_snapshot(adopted,Path(project['source_dataset_dir']).resolve(),'Geti reviewed label adoption',
                'Explicit human-reviewed Geti edits; independent revision from '+receipt['dataset_version_id'],'snapshot')
        validate_transfer(project,receipt)
        result={'labelset_id':labelset['id'],'dataset_version_id':version['id'],'previous_dataset_version_id':receipt['dataset_version_id'],
            'parent_revision':receipt['revision'],'revision':version['dataset_fingerprint'],'requires_labelset_selection':True,
            'annotation_state':'needs_review','actor':actor,'created_at':datetime.now(timezone.utc).isoformat()}
        _atomic_json(marker,result)
        return result


def validate_inspection_binding(project, receipt):
    """Retain frozen training truth while admitting additional inspection images.

    This is deliberately separate from validate_transfer: additions never become
    training members, and every existing member, label, subset, policy and copy
    still has to match the sealed training record.
    """
    from backend.api.routes_dataset_versions import _read_manifest, _safe_backup_path, _file_hash
    from backend.engine import team_data, dataset_metadata
    from backend.engine.class_overrides import read_class_overrides, effective_class
    from backend.engine.dataset_inventory import folder_label_split
    from backend.engine.grouped_dataset_views import _annotations
    from backend.api.routes_dataset import _read_split_manifest
    _task(project,receipt['task'] if receipt.get('source_task')=='segmentation' else None)
    if (project['id']!=receipt['project_id'] or project['task']!=receipt.get('source_task',receipt['task'])
            or project.get('active_labelset_id','default')!=receipt['labelset_id']
            or receipt.get('revision')!=_digest(_revision_body(receipt))):
        raise ValueError('Historical model training project, task, labelset or receipt changed')
    source=Path(project['source_dataset_dir']).resolve()
    directory,manifest=_read_manifest(project,receipt['dataset_version_id'])
    if manifest['source_dataset_dir']!=str(source) or manifest['content_digest']!=receipt['manifest_sha256']:
        raise ValueError('Historical model training source/version changed')
    for original in manifest['files']:
        if original['kind']=='label' and _file_hash(_safe_backup_path(directory,original))!=original['sha256']:
            raise ValueError('Frozen training labels/splits changed')
        if original['origin']=='source' and _file_hash(Path(original['source_path']),allow_symlink=True)!=original['sha256']:
            raise ValueError('Existing frozen training source changed')
    with _scope(project):
        current=team_data.training_binding(project,source)
        frozen=receipt['training_binding']['team_data']
        if any(current.get(key)!=frozen.get(key) for key in ('scope','book_sha256','policy_sha256','settings','gold')):
            raise ValueError('Frozen training labelbook/review policy changed')
        eligibility={row['relative_path']:row for row in current['eligibility']}
        if any(eligibility.get(row['relative_path'])!=row for row in frozen['eligibility']):
            raise ValueError('Existing frozen training eligibility/labels changed')
        metadata={row['relative_path']:row for row in dataset_metadata.list_metadata(Path(project['project_dir']),source,Path(project['annotations_dir']))}
        overrides=read_class_overrides(source); assignments=_read_split_manifest(source)
        names={row['geti_label_id']:row['name'] for row in receipt['class_mapping']}
        for row in receipt['source_mapping']:
            copied=Path(row['copied_path'])
            if copied.is_symlink() or not copied.is_file() or _bytes_hash(copied.read_bytes())!=row['uploaded_sha256']:
                raise ValueError('Frozen training copy byte hash changed')
            image=source/row['relative_path']; folder_label,folder_split=folder_label_split(Path(row['relative_path']),project['task'])
            if _SUBSETS.get(assignments.get(str(image),folder_split))!=row['subset']:
                raise ValueError('Existing frozen training subset changed')
            if metadata.get(row['relative_path'],{}).get('group','')!=row.get('group',''):
                raise ValueError('Existing frozen training original group changed')
            if project['task']=='classification':
                label,_=effective_class(image,folder_label,overrides)
                if label!=row['label']:
                    raise ValueError('Existing frozen training class label changed')
            else:
                annotations,_=_annotations(source,image)
                actual=_to_platform(annotations or [],receipt['task'],*row['geometry']['source_size'])
                expected=[{'name':names[a['labels'][0]['id']],'shape':a['shape']} for a in row['platform_annotations']]
                if [{'name':a['name'],'shape':a['shape']} for a in actual]!=expected:
                    raise ValueError('Existing frozen training object labels changed')
