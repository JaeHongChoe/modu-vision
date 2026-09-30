"""Persistent folder/JSON interface for all ten real training families.

An output directory is one independent source/task workspace. It never activates
the desktop project. Native originals stay read only; preparation, immutable
versions, journals and delivery files all live under the chosen output directory.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import copy_context
from functools import wraps
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import threading
import time
import uuid
import psutil
from backend.engine.dataset_metadata import _file_lock

TASKS=('classification','detection','segmentation','anomaly','patch_classification',
       'rotation','ocr','rotated_detection','enhancement','defect_gan')
FAMILIES={'rotation','ocr','rotated_detection','enhancement','defect_gan'}
CONFIG_FIELDS={
    'classification':{'backbone','image_size','batch_size','learning_rate','weight_decay','patience','augmentation_profile','pretrained_checkpoint','pretrained_sha256'},
    'patch_classification':{'backbone','image_size','batch_size','learning_rate','weight_decay','patience','augmentation_profile','pretrained_checkpoint','pretrained_sha256'},
    'detection':{'backbone','image_size','batch_size','learning_rate','weight_decay','patience','augmentation_profile','pretrained_checkpoint','pretrained_sha256'},
    'segmentation':{'model_name','image_size','batch_size','learning_rate','weight_decay','patience','augmentation_profile','pretrained_checkpoint','pretrained_sha256'},
    'anomaly':{'anomaly_backbone','anomaly_method','patch_size','stride','patches_per_image','inference_batch_size','batch_size','learning_rate','weight_decay','pretrained_checkpoint','pretrained_sha256'},
    'rotation':{'architecture','width','image_size','batch_size','learning_rate','seed'},
    'ocr':{'architecture','image_size','image_width','batch_size','learning_rate','seed'},
    'rotated_detection':{'architecture','image_size','batch_size','learning_rate'},
    'enhancement':{'architecture','batch_size','learning_rate','seed'},
    'defect_gan':{'architecture','batch_size','base_channels','seed'},
}
ACTIVE={'queued','running','stopping'}
_EVENTS={}
_LOCK=threading.RLock()

def _write(path,value):
    from backend.engine.project_labelsets import _atomic_json
    _atomic_json(Path(path),value)

def _hash(path):return sha256(Path(path).read_bytes()).hexdigest()

def _json(value):
    """Convert native array/tensor output without inventing derived metrics."""
    import numpy as np
    import torch
    if isinstance(value,dict):return {str(key):_json(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):return [_json(item) for item in value]
    if isinstance(value,(np.ndarray,torch.Tensor)):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    if isinstance(value,Path):return str(value)
    return value

def _id(value):
    if not isinstance(value,str) or len(value)!=32 or any(c not in '0123456789abcdef' for c in value):raise ValueError('Invalid engine identity')
    return value

def _root(value):
    raw=Path(value).expanduser()
    if any(path.is_symlink() for path in [raw,*raw.parents]):raise ValueError('Engine output cannot contain symbolic links')
    return raw.resolve()

def _source(root,name):
    from backend.engine.annotation_formats import safe_name
    try:path=Path(root)/safe_name(name)
    except ValueError as exc:raise ValueError('Unsafe original source image path') from exc
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):raise ValueError('Image must be a regular file inside the source')
    return path

@contextmanager
def workspace_scope(project):
    from backend.engine.annotation_storage import set_request_project_root,reset_request_project_root,set_request_annotation_root,reset_request_annotation_root
    from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
    p=set_request_project_root(Path(project['project_dir']));a=set_request_annotation_root(Path(project['annotations_dir']))
    s=set_request_split_root(Path(project.get('engine_split_root',Path(project['dataset_dir'])/'splits')))
    try:yield
    finally:reset_request_split_root(s);reset_request_annotation_root(a);reset_request_project_root(p)

def capabilities():
    from backend.engine.automated_trials import _RUNNERS
    return {'schema_version':1,'tasks':{task:{'architectures':list(_RUNNERS[task].architectures),
        'search_dimensions':list(_RUNNERS[task].search_defaults or {'architectures':0,'learning_rates':0,'weight_decays':0,'image_sizes':0,'batch_sizes':0,'augmentation_profiles':0}),
        'metric_key':_RUNNERS[task].metric_key,'direction':_RUNNERS[task].direction,
        'config_fields':sorted(CONFIG_FIELDS[task]|{'objective','latency_weight'}),
        'labels':'native task layout or explicit samples JSON' if task in TASKS[:4] else 'source-linked region JSON' if task=='patch_classification' else 'optional image selection JSON' if task=='enhancement' else 'explicit samples JSON',
        'devices':['cpu','mps','cuda'],'modes':['quick','search','fast_retrain']} for task in TASKS},
        'label_formats':['samples','coco','labelme'],'progress':'JSONL event records; durable run journals',
        'output_files':['model.pt','model_meta.json','configuration.json','evaluation.json','predictions.json','artifacts.json']}

def _project(output,source,task):
    from backend.engine.project_labelsets import load_labelsets
    path=output/'project.json'
    if path.is_file():
        project=json.loads(path.read_text())
        if project.get('engine_task')!=task or project.get('source_dataset_dir')!=str(source):raise ValueError('Output workspace is bound to a different task or source')
        return project
    primary=task if task in TASKS[:4] else 'classification'
    project={'id':uuid.uuid4().hex,'name':f'Engine {task}','task':primary,'engine_task':task,'project_dir':str(output),
        'dataset_dir':str(output/'dataset'),'models_dir':str(output/'models'),'reports_dir':str(output/'reports'),
        'annotations_dir':str(output/'annotations'),'active_labelset_id':'default','source_dataset_dir':str(source),
        'description':'Independent folder/JSON engine workspace','active_preset':'fast',
        'created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'updated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    for name in ('dataset_dir','models_dir','reports_dir','annotations_dir'):Path(project[name]).mkdir(parents=True,exist_ok=True)
    load_labelsets(output);_write(path,project)
    return project

def _labels(document,source):
    from backend.engine.annotation_formats import import_annotations,_shape
    from backend.engine.dicom_input import open_source_image
    if document is None:return []
    if isinstance(document,list):rows=document
    elif isinstance(document,dict) and isinstance(document.get('samples'),list):rows=document['samples']
    elif isinstance(document,dict):
        format_=document.get('format') or ('coco' if 'categories' in document else 'labelme')
        rows=[{**row,'image':row['file_name']} for row in import_annotations(document,format_)]
        # Standard COCO/LabelMe lacks partition semantics; the engine requires
        # explicit assignments or existing train/val/test folder partitions.
        assignments=document.get('splits',{})
        rows=[{**row,'split':assignments.get(row['image'])} for row in rows]
    else:raise ValueError('External labels must be a samples list, COCO or LabelMe JSON')
    result=[];seen=set();partitions={}
    for raw in rows:
        if not isinstance(raw,dict):raise ValueError('Every label sample must be an object')
        row=dict(raw);name=row.get('image',row.get('file_name'));path=_source(source,name)
        if name in seen:raise ValueError('Duplicate external source image')
        seen.add(name);digest=_hash(path)
        if row.get('source_sha256') and row['source_sha256']!=digest:raise ValueError('External source image hash differs')
        split=row.get('split') or next((part for part in Path(name).parts[:-1] if part in {'train','val','test'}),None)
        if split not in {'train','val','test'}:raise ValueError('Every external label needs train/val/test split')
        if digest in partitions and partitions[digest]!=split:raise ValueError('Byte-identical source images cannot cross partitions')
        partitions[digest]=split
        with open_source_image(path) as image:width,height=image.size
        annotations=[_shape(item,width,height) for item in row.get('annotations',[])]
        result.append({**row,'image':name,'split':split,'source_sha256':digest,'annotations':annotations})
    if not result or not {'train','val','test'}.issubset({row['split'] for row in result}):raise ValueError('External labels need nonempty train, val and test partitions')
    return result

def _save_overlay(source,rows):
    from backend.engine.annotation_storage import dataset_annotation_dir
    from backend.api.routes_dataset import _write_split_manifest
    for row in rows:
        image=_source(source,row['image']);annotations=row['annotations']
        if not annotations and row.get('label'):
            label=row['label'];annotations=[{'type':'tag','label':label,'is_normal':label.casefold() in {'ok','good','normal','pass'}}]
        _write(dataset_annotation_dir(image.parent)/f'{image.stem}.json',{'annotations':annotations,'mask_file':None,'external_engine_labels':True})
    _write_split_manifest(source,{row['image']:row['split'] for row in rows},0)

def _prepare_pairs(source,dataset,rows):
    """Copy explicit before/after truth with both native byte hashes retained."""
    from backend.engine.enhancement import load_enhancement_manifest
    records=[];dataset.mkdir(parents=True)
    try:
        for index,row in enumerate(rows):
            input_=_source(source,row['image']);target=_source(source,row.get('target'))
            target_hash=_hash(target)
            if row.get('target_sha256') and row['target_sha256']!=target_hash:raise ValueError('Enhancement target source hash differs')
            item={'split':row['split'],'source_relative_path':row['image'],'source_sha256':row['source_sha256'],
                'target_source_relative_path':row['target'],'target_source_sha256':target_hash}
            for kind,path in [('input',input_),('target',target)]:
                relative=f"{kind}s/{index:06d}{path.suffix}";destination=dataset/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
                digest=_hash(destination)
                if digest!=_hash(path):raise ValueError('Enhancement source changed during paired preparation')
                item[kind]=relative;item[kind+'_sha256']=digest
            records.append(item)
        _write(dataset/'pairs.json',{'version':1,'task':'enhancement','mode':'explicit_native_pairs','source_dataset_path':str(source),'records':records})
        return load_enhancement_manifest(dataset)
    except Exception:shutil.rmtree(dataset,ignore_errors=True);raise

def prepare(*,task,source_dataset_path,output_dir,labels=None,labels_path=None,prepare_options=None):
    if task not in TASKS:raise ValueError('Unsupported training task')
    source=Path(source_dataset_path).expanduser().resolve();output=_root(output_dir)
    if not source.is_dir() or output.is_relative_to(source) or source.is_relative_to(output):raise ValueError('Engine output and original source must be separate directory trees')
    if labels is not None and labels_path is not None:raise ValueError('Choose inline labels or a labels file')
    if labels_path is not None:labels=json.loads(Path(labels_path).read_text())
    rows=_labels(labels,source);options=dict(prepare_options or {})
    allowed={'patch_size','stride','normal_class','minimum_overlap'} if task=='patch_classification' else {'seed','noise_sigma'} if task=='enhancement' else set()
    if set(options)-allowed:raise ValueError('Unsupported preparation option for this family')
    output.mkdir(parents=True,exist_ok=True)
    from backend.engine.dataset_metadata import _file_lock
    with _file_lock(output/'.engine.lock'):
        if any(row['status'] in ACTIVE for row in list_runs(output)):raise ValueError('Finish active engine training before preparing another label revision')
        project=dict(_project(output,source,task));identifier=uuid.uuid4().hex
        family_folder='patch' if task=='patch_classification' else task if task in FAMILIES else 'prepared'
        dataset=output/'dataset'/family_folder/identifier
        labelset='ls_'+identifier[:12]
        project.update(annotations_dir=str(output/'labelsets'/labelset/'annotations'),active_labelset_id=labelset,
            engine_split_root=str(output/'dataset'/'engine_splits'/labelset))
        with workspace_scope(project):
            if rows:_save_overlay(source,rows)
            if task=='rotation':
                from backend.engine.rotation import prepare_rotation_dataset
                prepare_rotation_dataset(source,dataset,rows)
            elif task in {'ocr','rotated_detection'}:
                from backend.engine.prepared_family_datasets import prepare_family_dataset
                if task=='ocr':rows=[{**row,'text':row.get('text',row.get('label'))} for row in rows]
                prepare_family_dataset(task,source,dataset,rows)
            elif task=='defect_gan':
                from backend.engine.defect_gan import prepare_defect_gan_dataset
                prepare_defect_gan_dataset(source,dataset,rows)
            elif task=='enhancement':
                from backend.engine.enhancement import prepare_enhancement
                if any(row.get('target') for row in rows):
                    if options:raise ValueError('Explicit enhancement pairs do not use synthetic noise preparation controls')
                    _prepare_pairs(source,dataset,rows)
                else:prepare_enhancement(source,dataset,image_paths=[row['image'] for row in rows] if rows else None,**options)
            elif task=='patch_classification':
                from backend.engine.patch_preparation import prepare_patch_dataset
                prepare_patch_dataset(source,dataset,assignments={row['image']:row['split'] for row in rows},**options)
            elif task in {'classification','anomaly'} and rows:
                mapping=[]
                for index,row in enumerate(rows):
                    label=row.get('label')
                    if not isinstance(label,str) or not label.strip() or '/' in label or '\\' in label or label in {'.','..'}:raise ValueError('Classification sample needs a safe class label')
                    if task=='anomaly':
                        normal=label.casefold() in {'ok','good','normal','pass'}
                        if normal and any(item.get('type')!='tag' and not item.get('is_normal') for item in row['annotations']):raise ValueError('Normal anomaly source cannot include defect regions')
                        if row['split']=='train' and not normal:raise ValueError('Anomaly training source must contain normal truth only')
                        label='good' if normal else 'anomaly'
                    original=_source(source,row['image']);relative=f"{row['split']}/{label}/{index:06d}{original.suffix}"
                    destination=dataset/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,destination)
                    if _hash(destination)!=row['source_sha256']:raise ValueError('Classification source changed during preparation')
                    mapping.append({'image':str(destination),'source_image':str(original),'source_sha256':row['source_sha256']})
                _write(dataset/'source_manifest.json',mapping)
            else:dataset=source
            from backend.engine.training_provenance import bind_family_training,bind_training_version
            binding=bind_family_training(project,dataset,task) if task in FAMILIES|{'patch_classification'} else bind_training_version(project,source)
            # The current owned split is also readable by the desktop. Historical
            # preparations keep their own immutable split roots for headless reuse.
            if Path(project['engine_split_root']).is_dir():shutil.copytree(project['engine_split_root'],output/'dataset'/'splits',dirs_exist_ok=True)
            from backend.engine.dataset_fingerprint import fingerprint_dataset
            record={'prepared_id':identifier,'task':task,'source_dataset_path':str(source),'dataset_path':str(dataset),
                'source_fingerprint':fingerprint_dataset(source),'dataset_fingerprint':fingerprint_dataset(dataset),'training_binding':binding,
                'source_images':{row['image']:row['source_sha256'] for row in rows},'labels':rows,'prepare_options':options,'project':project,'created_at':time.time()}
            from backend.engine.project_labelsets import load_labelsets
            registry=load_labelsets(output);registry['labelsets'].append({'id':labelset,'name':f'External preparation {identifier[:8]}','source_id':None,'created_at':time.time()});registry['active_id']=labelset
            _write(output/'labelsets.json',registry);_write(output/'project.json',project)
            _write(output/'preparations'/f'{identifier}.json',record);_write(output/'engine.json',{'schema_version':1,'task':task,'source_dataset_path':str(source),'prepared_id':identifier})
            return {key:value for key,value in record.items() if key not in {'project','labels'}}

def _prepared(output,identifier=None):
    output=_root(output);pointer=json.loads((output/'engine.json').read_text());identifier=_id(identifier or pointer['prepared_id'])
    path=output/'preparations'/f'{identifier}.json'
    if path.is_symlink():raise ValueError('Prepared engine record cannot be linked')
    record=json.loads(path.read_text())
    if record['prepared_id']!=identifier or record['task']!=pointer['task'] or record['source_dataset_path']!=pointer['source_dataset_path']:raise ValueError('Prepared engine identity differs')
    project=record['project'];dataset=Path(record['dataset_path']).resolve();source=Path(record['source_dataset_path']).resolve()
    if (Path(project['project_dir']).resolve()!=output or Path(project['models_dir']).resolve()!=output/'models'
            or project['source_dataset_dir']!=str(source) or dataset!=source and not dataset.is_relative_to(output/'dataset')):
        raise ValueError('Prepared engine storage left its output workspace')
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine.training_provenance import validate_training_binding
    with workspace_scope(record['project']):
        if fingerprint_dataset(record['source_dataset_path'])!=record['source_fingerprint']:raise ValueError('Original source changed after preparation')
        if fingerprint_dataset(record['dataset_path'])!=record['dataset_fingerprint']:raise ValueError('Prepared labels or dataset changed')
        validate_training_binding(record['training_binding'])
    return record

def create_run(*,output_dir,prepared_id=None,mode='quick',preset='fast',device='cpu',config=None,search_space=None,budget=None,epochs_per_trial=1,parent_job_id=None):
    from backend.engine.automated_trials import _space,validated_budget
    from backend.engine.runtime_device import resolve_runtime_device
    output=_root(output_dir);prepared=_prepared(output,prepared_id)
    if mode not in {'quick','search','fast_retrain'}:raise ValueError('Unsupported engine training mode')
    if preset not in {'fast','precision'}:raise ValueError('Unsupported training preset')
    if mode=='fast_retrain' and not parent_job_id:raise ValueError('Fast retraining needs a completed parent')
    if mode=='fast_retrain' and search_space:raise ValueError('Fast retraining reuses parent controls; choose search mode to vary candidates')
    if type(epochs_per_trial)is not int or not 1<=epochs_per_trial<=500:raise ValueError('Epochs must be bounded')
    controls=dict(config or {})
    if prepared['task']=='ocr' and 'image_height' in controls:controls['image_size']=controls.pop('image_height')
    if set(controls)-(CONFIG_FIELDS[prepared['task']]|{'objective','latency_weight'}):raise ValueError('Unsupported model/training configuration field for this family')
    if prepared['task']=='anomaly' and controls.get('anomaly_method','dino_synthetic')!='dino_synthetic':raise ValueError('Measured anomaly training uses the genuine pretrained DINO synthetic detector')
    next(_space(prepared['task'],search_space,controls,mode))
    budget=validated_budget(budget or {'max_trials':4 if mode=='search' else 1,'max_total_epochs':epochs_per_trial*(4 if mode=='search' else 1),'max_seconds':600},epochs_per_trial)
    identifier=uuid.uuid4().hex
    record={'schema_version':1,'run_id':identifier,'status':'queued','task':prepared['task'],'prepared_id':prepared['prepared_id'],
        'source_dataset_path':prepared['source_dataset_path'],'dataset_path':prepared['dataset_path'],'output_dir':str(output),
        'mode':mode,'preset':preset,'device':str(resolve_runtime_device(device)),'config':controls,'search_space':search_space,
        'budget':budget,
        'epochs_per_trial':epochs_per_trial,'parent_job_id':parent_job_id,'created_at':time.time(),'owner_pid':os.getpid(),
        'owner_created_at':psutil.Process().create_time(),'winner':None,'artifacts':{}}
    _write(output/'runs'/identifier/'run.json',record)
    return record

def _path(output,identifier):
    output=_root(output);identifier=_id(identifier);path=output/'runs'/identifier/'run.json'
    if any(item.is_symlink() for item in [path,path.parent,path.parent.parent]):raise ValueError('Engine job cannot be linked')
    return path

def read_run(output,identifier):
    path=_path(output,identifier);record=json.loads(path.read_text())
    if record.get('run_id')!=identifier or record.get('output_dir')!=str(_root(output)):raise ValueError('Engine job identity differs')
    if record['status'] in ACTIVE:
        try:
            if type(record.get('owner_pid')) is not int or record['owner_pid']<=0:raise OSError('No execution owner')
            process=psutil.Process(record['owner_pid'])
            if record.get('owner_created_at')!=process.create_time() or not process.is_running() or process.status()==psutil.STATUS_ZOMBIE:raise OSError('Execution owner changed')
        except (OSError,TypeError,psutil.Error):
            record.update(status='interrupted',winner=None,error='Training process ended before publishing a result');_write(path,record)
    if record['status']=='completed':
        for artifact in record['artifacts'].values():_verify_artifact(output,artifact)
        if 'predictions' in record['artifacts']:
            predictions=json.loads(Path(record['artifacts']['predictions']['path']).read_text())
            for artifact in predictions.get('output_files',[]):_verify_artifact(output,artifact)
    return record

def _verify_artifact(output,artifact):
    target=Path(artifact['path'])
    if target.is_symlink() or not target.resolve().is_relative_to(_root(output)) or not target.is_file() or target.stat().st_size!=artifact['bytes'] or _hash(target)!=artifact['sha256']:
        raise ValueError('Delivered engine artifact changed')

def list_runs(output):
    return sorted([read_run(output,path.parent.name) for path in _root(output).glob('runs/*/run.json')],key=lambda row:row['created_at'],reverse=True)

def cancel_run(output,identifier):
    with _LOCK,_file_lock(_path(output,identifier).parent/'.journal.lock'):
        record=read_run(output,identifier)
        if record['status'] in ACTIVE:
            directory=_path(output,identifier).parent;_write(directory/'cancel_requested.json',{'at':time.time()})
            _write(_root(output)/'models'/'automated_training'/identifier/'cancel_requested.json',{'at':time.time()})
            if (event:=_EVENTS.get(str(directory))) is not None:event.set()
            record.update(status='cancelled' if record['status']=='queued' else 'stopping',winner=None);_write(directory/'run.json',record)
        return record

def _artifact(path):return {'path':str(path),'sha256':_hash(path),'bytes':path.stat().st_size}

def execute_run(output,identifier,on_progress=None):
    from backend.engine.automated_trials import run_automated_training
    output=_root(output);path=_path(output,identifier)
    # The queued->running transition is a process-safe claim. Two CLI workers
    # cannot both fit a candidate into the same checkpoint directory.
    with _LOCK,_file_lock(path.parent/'.journal.lock'):
        record=read_run(output,identifier)
        if record['status']=='cancelled':return record
        if record['status']!='queued':raise ValueError('Engine job has already been started')
        record.update(status='running',owner_pid=os.getpid(),owner_created_at=psutil.Process().create_time());_write(path,record)
    event=threading.Event();_EVENTS[str(path.parent)]=event
    if (path.parent/'cancel_requested.json').is_file():event.set()
    try:
        prepared=_prepared(output,record['prepared_id'])
        def progress(search):
            record.update(search=search)
            if event.is_set() or (path.parent/'cancel_requested.json').is_file():event.set();record['status']='stopping'
            with _LOCK,_file_lock(path.parent/'.journal.lock'):_write(path,record)
            if on_progress:on_progress({'event':'progress','run_id':identifier,'status':record['status'],'trials':search['trials'],'epochs_consumed':search.get('epochs_consumed',0)})
        with workspace_scope(prepared['project']):
            search=run_automated_training(task=record['task'],dataset_path=prepared['dataset_path'],source_dataset_path=prepared['source_dataset_path'],
                models_dir=output/'models',mode=record['mode'],preset=record['preset'],device=record['device'],base_config=record['config'],
                search_space=record['search_space'],budget=record['budget'],epochs_per_trial=record['epochs_per_trial'],parent_job_id=record['parent_job_id'],
                training_binding=prepared['training_binding'],search_id=identifier,cancel_event=event,on_progress=progress)
        record.update(search=search,status=search['status'],winner=search['winner'],finished_at=time.time())
        with _LOCK,_file_lock(path.parent/'.journal.lock'):
            if event.is_set() or (path.parent/'cancel_requested.json').is_file():record.update(status='cancelled',winner=None)
            if record['status']=='completed':
                delivery=output/'artifacts'/identifier;delivery.mkdir(parents=True,exist_ok=True)
                checkpoint=Path(record['winner']['checkpoint_path']);shutil.copyfile(checkpoint,delivery/'model.pt');shutil.copyfile(checkpoint.with_name('model_meta.json'),delivery/'model_meta.json')
                recipe={key:record[key] for key in ('task','output_dir','prepared_id','source_dataset_path','mode','preset','device','config','search_space','budget','epochs_per_trial','parent_job_id')}
                recipe['config']=dict(record['winner']['config']);recipe['config'].pop('epochs',None)
                _write(delivery/'configuration.json',recipe)
                record['artifacts']={name:_artifact(delivery/file) for name,file in [('model','model.pt'),('metadata','model_meta.json'),('configuration','configuration.json')]}
                _write(delivery/'artifacts.json',record['artifacts'])
            _write(path,record)
        return record
    except Exception as exc:
        with _LOCK,_file_lock(path.parent/'.journal.lock'):
            record.update(status='cancelled' if event.is_set() or (path.parent/'cancel_requested.json').is_file() else 'failed',winner=None,error=str(exc),finished_at=time.time());_write(path,record)
        if on_progress:on_progress({'event':'error','run_id':identifier,'error':str(exc)})
        return record
    finally:_EVENTS.pop(str(path.parent),None)

def start_run(**options):
    record=create_run(**options);context=copy_context()
    thread=threading.Thread(target=lambda:context.run(execute_run,options['output_dir'],record['run_id']),name=f"engine-{record['run_id'][:8]}",daemon=True)
    thread.start();return record

def _model(output,identifier):
    record=read_run(output,identifier)
    if record['status']!='completed' or not record['winner']:raise ValueError('A completed engine run is required')
    prepared=_prepared(output,record['prepared_id']);checkpoint=Path(record['artifacts']['model']['path'])
    if _hash(checkpoint)!=record['winner']['checkpoint_sha256']:raise ValueError('Completed model checkpoint changed')
    return record,prepared,checkpoint

def configuration_recipe(document):
    if not isinstance(document,dict):raise ValueError('Reusable configuration must be a JSON object')
    accepted={'output_dir','prepared_id','mode','device','preset','epochs_per_trial','parent_job_id','config','search_space','budget'}
    return {key:value for key,value in document.items() if key in accepted}

def _publish(output,record,name,value):
    with _LOCK,_file_lock(_path(output,record['run_id']).parent/'.journal.lock'):
        current=read_run(output,record['run_id'])
        directory=_root(output)/'artifacts'/record['run_id']
        # Results are immutable revisions. Re-running prediction/evaluation can
        # replace the manifest pointer without invalidating the prior hash or a
        # reader that is still opening the previous output.
        path=directory/'revisions'/uuid.uuid4().hex/f'{name}.json';_write(path,_json(value))
        current['artifacts'][name]=_artifact(path);_write(_path(output,record['run_id']),current)
        _write(directory/'artifacts.json',current['artifacts'])
    return value

def _inference_lease(function):
    @wraps(function)
    def guarded(output,identifier,**options):
        from backend.engine.shared_scheduler import compute_lease_scope
        from backend.engine.runtime_device import resolve_runtime_device
        record,prepared,_=_model(output,identifier)
        options['device']=str(resolve_runtime_device(options.get('device','cpu')))
        with compute_lease_scope(f'engine_{function.__name__}_{uuid.uuid4().hex}',options['device'],task=record['task'],project_id=prepared['project']['id']):
            return function(output,identifier,**options)
    return guarded

def _link_evaluation_sources(result,prepared):
    source=Path(prepared['source_dataset_path']);dataset=Path(prepared['dataset_path']);mapping={}
    manifest=dataset/'source_manifest.json'
    if manifest.is_file():
        for row in json.loads(manifest.read_text()):
            for key in ('image','prepared_image','image_path','output_image'):
                if row.get(key) and row.get('source_image'):mapping[str(Path(row[key]).resolve())]=Path(row['source_image'])
    if prepared['task'] in FAMILIES:
        from backend.engine.specialized_models import specialized_dataset_provenance
        for relative,row in specialized_dataset_provenance(prepared['task'],dataset).get('source_map',{}).items():
            mapping[str((dataset/relative).resolve())]=source/row['source_relative_path']
    for key in ('test_predictions','samples'):
        for row in result.get(key,[]):
            if not isinstance(row,dict):continue
            raw=row.get('evaluation_file_path') or row.get('file_path') or row.get('image')
            if not raw:continue
            copied=Path(raw) if Path(raw).is_absolute() else dataset/raw
            original=mapping.get(str(copied.resolve()),copied)
            if not original.resolve().is_relative_to(source) or not original.is_file():raise ValueError('Evaluation output escaped original source mapping')
            row.update(file_path=str(original),source_relative_path=original.relative_to(source).as_posix(),source_sha256=_hash(original))
            if copied.resolve()!=original.resolve():row.update(evaluation_file_path=str(copied),evaluation_sha256=_hash(copied))
    return result

@_inference_lease
def evaluate(output,identifier,*,device='cpu',split='test'):
    from backend.engine.runtime_device import resolve_runtime_device
    from backend.engine.evaluation_history import EvaluationHistory
    record,prepared,checkpoint=_model(output,identifier);task=record['task'];dataset=prepared['dataset_path'];device=str(resolve_runtime_device(device))
    if split not in {'val','test'}:raise ValueError('Evaluation requires heldout val or test')
    with workspace_scope(prepared['project']):
        if task=='rotation':
            from backend.engine.rotation import evaluate_rotation_checkpoint
            result=evaluate_rotation_checkpoint(checkpoint,dataset,device=device,split=split)
        elif task=='ocr':
            from backend.engine.ocr import evaluate_ocr_checkpoint
            result=evaluate_ocr_checkpoint(checkpoint,dataset,device=device,split=split)
        elif task=='rotated_detection':
            from backend.engine.rotated_detection import evaluate_rotated_detector
            result=evaluate_rotated_detector(checkpoint,dataset,device=device,split=split)
        elif task=='enhancement':
            from backend.engine.enhancement import evaluate_enhancement
            result=evaluate_enhancement(checkpoint,dataset,device=device,split=split)
        elif task=='defect_gan':
            from backend.engine.defect_gan import evaluate_defect_generator
            result=evaluate_defect_generator(checkpoint,dataset,device=device,split=split)
        else:
            import torch
            from backend.api import routes_evaluation as core
            meta=json.loads(checkpoint.with_name('model_meta.json').read_text())
            evaluator={'classification':core._evaluate_classification,'patch_classification':core._evaluate_patch_classification,
                'detection':core._evaluate_detection,'segmentation':core._evaluate_segmentation,'anomaly':core._evaluate_anomaly}[task]
            if split!='test':raise ValueError('Primary task evaluator uses the saved test partition')
            result=evaluator(checkpoint,meta,Path(dataset),torch.device(device))
        result=_link_evaluation_sources(_json({**result,'task':task,'job_id':record['winner']['trial_id'],'run_id':identifier}),prepared)
        evidence=EvaluationHistory(_root(output)/'reports'/'evaluations').append(result,{**prepared['training_binding'],
            'source_dataset_path':prepared['source_dataset_path'],'checkpoint_sha256':_hash(checkpoint),'task':task})
        result['evaluation_id']=evidence['evaluation_id'];result['evidence_sha256']=evidence['evidence_sha256']
    return _publish(output,record,'evaluation',{'run_id':identifier,'evaluation':result})

@_inference_lease
def predict(output,identifier,*,images=None,device='cpu',threshold=.5):
    from backend.engine.runtime_device import resolve_runtime_device
    from backend.engine.dicom_input import open_source_image
    from backend.engine.grouped_dataset_views import source_image_paths
    from PIL import Image
    import numpy as np
    record,prepared,checkpoint=_model(output,identifier);source=Path(prepared['source_dataset_path']);task=record['task'];device=str(resolve_runtime_device(device))
    with workspace_scope(prepared['project']):
        paths=[_source(source,name) for name in images] if images is not None else source_image_paths(source,task,include_unused=True)
    if not paths:raise ValueError('Choose at least one original source image')
    rows=[];delivery=_root(output)/'artifacts'/identifier/'images'/uuid.uuid4().hex;delivery.mkdir(parents=True,exist_ok=True)
    for path in paths:
        digest=_hash(path);relative=path.relative_to(source).as_posix();filename=sha256(relative.encode()).hexdigest()[:20]+'.png'
        with open_source_image(path) as image:pixels=np.asarray(image.convert('RGB'))
        if task=='rotation':
            from backend.engine.rotation import predict_rotation_array
            prediction=predict_rotation_array(checkpoint,pixels,device=device);Image.fromarray(prediction.pop('aligned_image')).save(delivery/filename);prediction['aligned_image_path']=str(delivery/filename)
        elif task=='ocr':
            from backend.engine.ocr import predict_ocr_array
            prediction=predict_ocr_array(checkpoint,pixels,device=device)
        elif task=='rotated_detection':
            from backend.engine.rotated_detection import predict_rotated_array
            prediction=predict_rotated_array(checkpoint,pixels,device=device,threshold=threshold)
        elif task=='enhancement':
            from backend.engine.enhancement import predict_enhancement
            enhanced=predict_enhancement(checkpoint,pixels,device=device);Image.fromarray(enhanced).save(delivery/filename)
            prediction={'task':task,'enhanced_image_path':str(delivery/filename),'output_size':[enhanced.shape[1],enhanced.shape[0]]}
        elif task=='defect_gan':
            from backend.engine.defect_gan import generate_composited_candidates
            generated=generate_composited_candidates(checkpoint,path,delivery/(filename.removesuffix('.png')+'-'+uuid.uuid4().hex[:8]),regions=[{'id':'whole_source','bbox':[0,0,pixels.shape[1],pixels.shape[0]]}],count=1,seed=0,device=device)
            prediction={'task':task,'generated_review':_json(generated),'generation_semantics':'unconditional_generation_composited_into_whole_source_for_review','quality_state':'requires_human_review'}
        else:
            from backend.engine.trainer import infer
            prediction_ = infer(task,checkpoint,pixels,threshold=threshold,device=device)
            Image.fromarray(prediction_.visual_overlay).save(delivery/filename)
            prediction={'task':task,'predictions':prediction_.predictions,'confidence_score':prediction_.confidence_score,'latency_ms':prediction_.latency_ms,'overlay_path':str(delivery/filename)}
        if _hash(path)!=digest:raise ValueError('Original image changed during prediction')
        rows.append({'source_image':str(path),'source_relative_path':relative,'source_sha256':digest,'model_sha256':_hash(checkpoint),'prediction':_json(prediction)})
    outputs=[_artifact(path) for path in sorted(delivery.rglob('*')) if path.is_file()]
    return _publish(output,record,'predictions',{'run_id':identifier,'task':task,'predictions':rows,'output_files':outputs})
