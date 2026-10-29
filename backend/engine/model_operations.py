"""Persisted, project-owned data-to-model cycles with explicit activation policies."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from backend.engine.sqlite_wal import use_wal  # a concurrent WAL switch is retried, not failed
import threading
import time
from types import SimpleNamespace
import uuid

from fastapi import HTTPException


class OperationsBusy(ValueError):
    code = 'operations_busy'


class PolicyRevisionConflict(ValueError):
    code = 'policy_revision_conflict'

    def __init__(self, expected_revision, current_revision):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(f'Operations policy changed from revision {expected_revision} to {current_revision}; the old cycle cannot update it')


class OperationsStore:
    def __init__(self, project_dir):
        root=Path(project_dir)
        if root.is_symlink() or not root.is_dir():raise ValueError('Invalid operations project')
        self.path=root/'model_operations.sqlite3'
        if self.path.is_symlink():raise ValueError('Operations database is linked')
        with self.connect() as conn:
            conn.executescript('CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);CREATE TABLE IF NOT EXISTS cycles(cycle_id TEXT PRIMARY KEY,payload TEXT NOT NULL);')
            conn.execute('CREATE TABLE IF NOT EXISTS quality_assessments(assessment_id TEXT PRIMARY KEY,assessment_key TEXT UNIQUE NOT NULL,payload TEXT NOT NULL)')
    def connect(self):
        conn=sqlite3.connect(self.path,timeout=30);use_wal(conn,30);return conn
    def policy(self):
        with self.connect() as conn:row=conn.execute('SELECT payload FROM policy WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None
    def save_policy(self, value, *, expected_revision=None):
        payload = json.dumps(value, allow_nan=False)
        with self.connect() as conn:
            if expected_revision is not None:
                # Read and write share the SQLite writer transaction, including
                # an absent/legacy policy whose initial revision is zero.
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT payload FROM policy WHERE id=1').fetchone()
                current_revision = json.loads(row[0]).get('revision', 0) if row else 0
                if current_revision != expected_revision:
                    raise PolicyRevisionConflict(expected_revision, current_revision)
            conn.execute('INSERT INTO policy VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (payload,))
    def save(self, value):
        with self.connect() as conn:conn.execute('INSERT INTO cycles VALUES(?,?) ON CONFLICT(cycle_id) DO UPDATE SET payload=excluded.payload',(value['cycle_id'],json.dumps(value,allow_nan=False)))
        return value
    def get(self, identifier):
        with self.connect() as conn:row=conn.execute('SELECT payload FROM cycles WHERE cycle_id=?',(identifier,)).fetchone()
        if not row:raise KeyError(identifier)
        return json.loads(row[0])
    def history(self):
        with self.connect() as conn:rows=conn.execute('SELECT payload FROM cycles').fetchall()
        return sorted([json.loads(row[0]) for row in rows],key=lambda r:r['created_at'],reverse=True)
    def save_assessment(self,value):
        with self.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO quality_assessments VALUES(?,?,?)',
                (value['assessment_id'],value['assessment_key'],json.dumps(value,allow_nan=False)))
            row=conn.execute('SELECT assessment_id FROM quality_assessments WHERE assessment_key=?',(value['assessment_key'],)).fetchone()
        return self.assessment(row[0])
    def assessment(self,identifier):
        import re
        from backend.engine.image_truth import digest
        if not isinstance(identifier,str) or not re.fullmatch(r'quality_[a-f0-9]{32}',identifier):raise ValueError('Invalid quality assessment identifier')
        with self.connect() as conn:row=conn.execute('SELECT payload FROM quality_assessments WHERE assessment_id=?',(identifier,)).fetchone()
        if row is None:raise KeyError(identifier)
        value=json.loads(row[0])
        if value.get('assessment_id')!=identifier or value.get('record_sha256')!=digest({k:v for k,v in value.items() if k!='record_sha256'}):
            raise ValueError('Quality assessment record changed')
        return value
    def assessments(self):
        with self.connect() as conn:rows=conn.execute('SELECT assessment_id FROM quality_assessments ORDER BY rowid DESC LIMIT 256').fetchall()
        return [self.assessment(row[0]) for row in rows]


@contextmanager
def project_scope(project):
    from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root,set_request_project_root,reset_request_project_root
    from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
    annotation=set_request_annotation_root(Path(project['annotations_dir']))
    root=set_request_project_root(Path(project['project_dir']))
    splits=set_request_split_root(Path(project['dataset_dir'])/'splits')
    try:yield
    finally:
        reset_request_split_root(splits);reset_request_project_root(root);reset_request_annotation_root(annotation)


def _sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as reader:
        for chunk in iter(lambda:reader.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def _inventory(project):
    from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
    source=Path(project['source_dataset_dir']).resolve();inventory={}
    from backend.engine.dataset_usage import unused_image_paths
    excluded=unused_image_paths(source)
    for folder,dirs,files in os.walk(source,followlinks=False):
        dirs[:]=[name for name in sorted(dirs) if not name.startswith('.') and not (Path(folder)/name).is_symlink() and (Path(folder)/name).resolve()!=Path(project['project_dir']).resolve()]
        for name in sorted(files):
            path=Path(folder)/name
            if not name.startswith('.') and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS and not path.is_symlink() and str(path.resolve()) not in excluded:
                inventory[str(path)]=_sha(path)
    return inventory


def _image_binding(project,path):
    from backend.engine.dataset_metadata import metadata_for_path
    row=metadata_for_path(Path(project['project_dir']),Path(project['source_dataset_dir']),Path(path),Path(project['annotations_dir']))
    return {key:row.get(key) for key in ('content_hash','annotation_hash','mask_hash')}


def _holdout(project):
    from backend.api.routes_dataset import list_dataset_images
    page=list_dataset_images(folder_path=project['source_dataset_dir'],task=project['task'],limit=500,offset=0,split='test',class_name=None)
    rows=list(page['items'])
    while len(rows)<page['total']:
        more=list_dataset_images(folder_path=project['source_dataset_dir'],task=project['task'],limit=500,offset=len(rows),split='test',class_name=None)
        if not more['items']:raise ValueError('Test inventory changed while binding holdout')
        rows.extend(more['items'])
    if not rows:raise ValueError('Save a nonempty independent test split before configuring operations')
    return {row['file_path']:{**_image_binding(project,row['file_path']),'ground_truth':row.get('label')} for row in rows}


def _input_version(project, policy):
    from backend.api.routes_model_comparisons import _fingerprint
    values=[_fingerprint(Path(project['source_dataset_dir']).resolve())]
    if policy.get('family_dataset_path'):
        dataset=Path(policy['family_dataset_path'])
        if dataset.is_symlink() or not dataset.resolve().is_relative_to(Path(project['dataset_dir']).resolve()):raise ValueError('Prepared inputs belong to another project')
        # Task loaders verify source links and split identities before training;
        # inventory detects reviewed changes without changing those manifests.
        for folder,dirs,files in os.walk(dataset,followlinks=False):
            dirs[:]=[name for name in dirs if not (Path(folder)/name).is_symlink()]
            for name in sorted(files):
                path=Path(folder)/name
                if path.is_symlink():raise ValueError('Prepared inputs contain linked files')
                values.append(str(path.relative_to(dataset))+':'+_sha(path))
    return hashlib.sha256(json.dumps(sorted(values)).encode()).hexdigest()


@contextmanager
def _cycle_lock(project,name='operations.lock'):
    path=Path(project['project_dir'])/name
    if path.is_symlink():raise ValueError('Operations lock is linked')
    with path.open('a+b') as writer:
        try:
            if os.name=='nt':
                import msvcrt
                writer.write(b'0');writer.flush();writer.seek(0);msvcrt.locking(writer.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(writer.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:raise OperationsBusy('This project already has an operations cycle') from exc
        try:yield
        finally:
            if os.name=='nt':
                writer.seek(0);msvcrt.locking(writer.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(writer.fileno(),fcntl.LOCK_UN)


def _checkpoint(project,task,identifier):
    from backend.api.routes_model_comparisons import _model
    if task in ('classification','patch_classification','segmentation','detection','anomaly','ocr','rotated_detection','enhancement','rotation'):
        record=_model(project,Path(project['source_dataset_dir']).resolve(),task,identifier)
        if record:return Path(record['checkpoint_path'])
    from backend.engine.automated_trials import _RUNNERS
    from backend.engine.specialized_models import require_completed_checkpoint
    spec=_RUNNERS.get(task)
    if not spec or not spec.family or not isinstance(identifier,str) or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('Choose a completed source-bound parent model')
    directory=Path(project['models_dir'])/spec.family/identifier
    if directory.is_symlink():raise ValueError('Parent model storage is linked')
    checkpoint=directory/'best_model.pt';require_completed_checkpoint(checkpoint)
    metadata=json.loads((directory/'model_meta.json').read_text(encoding='utf-8'))
    if metadata.get('task')!=task or Path(metadata.get('source_dataset_path','')).resolve()!=Path(project['source_dataset_dir']).resolve():raise ValueError('Parent model belongs to another family or source')
    if not checkpoint.is_file() or checkpoint.is_symlink():raise ValueError('Completed parent checkpoint is missing')
    return checkpoint


_UNREVIEWABLE_PARENT='자동 라벨 초안은 2단계 초안 검토에서 채택할 수 있어야 합니다. 이 프로젝트의 작업으로 이 데이터에서 완료된 학습 모델(job_*)을 시작 모델로 선택하세요.'


def _reviewable_parent(project,policy,checkpoint=None):
    """The step 2 catalog row review resolves for this policy's parent, or None when a draft from it could never be adopted.

    review_suggestion looks the model up in _model_catalog (completed job_* models of the project's task on this exact
    source), so a specialized or lineage-only parent, or a policy task other than the project's, yields no reviewable draft."""
    from backend.api.routes_label_suggestions import _model_catalog
    if policy['task']!=project['task']:return None
    model=next((item for item in _model_catalog(project) if item['job_id']==policy['parent_job_id']),None)
    if model and checkpoint is not None and Path(model['checkpoint_path']).resolve()!=Path(checkpoint).resolve():return None
    return model


def configure_program(project, supplied):
    from backend.api.routes_model_operations import OperationsPolicy
    policy=OperationsPolicy.model_validate(supplied).model_dump()
    if (policy['auto_approve'] or policy['auto_deploy']) and not policy['approval_policy_authorized']:
        raise ValueError('Automatic activation needs explicit authorization of its reviewed holdout policy')
    if policy['auto_deploy'] and not policy['auto_approve']:raise ValueError('Automatic deployment requires automatic quality approval')
    if policy['auto_deploy'] and not policy['pipeline_version_id']:raise ValueError('Choose the saved flow version to update and deploy')
    if policy['auto_deploy']:
        if policy['inference_device'].startswith('openvino:'):raise ValueError('Automatic candidate deployment requires a Torch device; approve a converted precision release separately')
        from backend.engine.runtime_device import resolve_runtime_device
        policy['inference_device']=str(resolve_runtime_device(policy['inference_device']))
    if policy['auto_label'] and policy['task'] not in ('classification','detection','segmentation','ocr','rotated_detection'):
        raise ValueError('High confidence automatic labels require a calibrated classification, detection, segmentation, OCR or rotated detection model')
    with _cycle_lock(project), project_scope(project):
        if policy['auto_label'] and _reviewable_parent(project,policy) is None:raise ValueError(_UNREVIEWABLE_PARENT)
        checkpoint=_checkpoint(project,policy['task'],policy['parent_job_id'])
        store=OperationsStore(project['project_dir']);previous=store.policy()
        if any(row['status']=='running' for row in store.history()):raise OperationsBusy('Cancel the active cycle before changing its policy')
        policy.update(project_id=project['id'],labelset_id=project.get('active_labelset_id','default'),source_dataset_path=str(Path(project['source_dataset_dir']).resolve()),
                      parent_checkpoint_sha256=_sha(checkpoint),holdout=_holdout(project),seen={} if policy['process_existing'] else _inventory(project),
                      revision=(previous or {}).get('revision',0)+1,created_at=time.time())
        policy['seen_labels']={} if policy['process_existing'] else {path:_image_binding(project,path) for path in policy['seen']}
        policy['input_version']=_input_version(project,policy)
        if policy['auto_deploy']:
            pipeline,record=_authorized_flow(project,policy)
            from backend.engine.flow_provenance import pipeline_sha256
            policy['pipeline_id']=pipeline.id
            policy['pipeline_sha256']=pipeline_sha256(pipeline)
        store.save_policy(policy, expected_revision=(previous or {}).get('revision', 0));return policy


def _authorized_flow(project,policy):
    from backend.api.routes_flowchart import get_saved_pipeline_version,_version_dir
    request=SimpleNamespace(state=SimpleNamespace(scoped_project=project,account_user=None))
    identifier=policy.get('pipeline_version_id')
    if not identifier:raise ValueError('Choose an immutable saved flow version')
    pipeline=get_saved_pipeline_version(identifier,request=request)
    record=json.loads((_version_dir(Path(project['project_dir']))/f'{identifier}.json').read_text(encoding='utf-8'))
    if record.get('source_dataset_path')!=str(Path(project['source_dataset_dir']).resolve()):raise ValueError('Saved flow belongs to another source')
    from backend.engine.flow_provenance import pipeline_sha256
    if policy.get('pipeline_sha256') and pipeline_sha256(pipeline)!=policy['pipeline_sha256']:raise ValueError('Authorized saved flow content changed')
    if policy.get('pipeline_id') and pipeline.id!=policy['pipeline_id']:raise ValueError('Authorized saved flow identity changed')
    return pipeline,record


def _auto_label(project,policy,path,checkpoint,batch_id,fingerprint,cycle_id):
    """Write one pending draft for a new, unlabeled image into the step 2 draft store; nothing is adopted here.

    The proposal carries the field set routes_label_suggestions._generate_proposal writes, so review_suggestion adopts it
    (with a backup and an origin) or refuses it exactly like a draft a person generated. Returns the per-image entry
    {'image_path','image_sha256','checkpoint_sha256','candidate_count','confidence_threshold','status','proposal_id'?,'batch_id'?}
    with status 'drafted' (a pending proposal was written) or 'zero_candidates' (no prediction reached the threshold; nothing written)."""
    from backend.api import routes_annotation
    from backend.api.routes_dataset_versions import _VERSION_LOCK
    from backend.api.routes_label_suggestions import _candidate_annotations,_proposal_path,_sha256
    from backend.api.routes_project import _write_json
    from backend.engine.annotation_storage import dataset_annotation_dir
    from backend.engine.dataset_metadata import metadata_for_path
    from backend.engine.trainer import infer
    task=policy['task'];path=Path(path);before=_sha(path);model_hash=_sha(checkpoint);threshold=policy['confidence_threshold']
    entry={'image_path':str(path),'image_sha256':before,'checkpoint_sha256':model_hash,'candidate_count':0,'confidence_threshold':threshold}
    # Captured before inference, as the step 2 generator does: review refuses the draft once any of them changed.
    metadata=metadata_for_path(Path(project['project_dir']),Path(project['source_dataset_dir']),path,Path(project['annotations_dir']))
    labelme=path.with_suffix('.json');studio_json=dataset_annotation_dir(path.parent,Path(project['annotations_dir']),use_scope=False)/f'{path.stem}.json'
    labelme_hash=_sha256(labelme);studio_hash=_sha256(studio_json)
    if routes_annotation.get_annotations(path.stem,file_path=str(path)).get('annotations'):raise ValueError('Image, model or labels changed during automatic labeling')
    suggestion_id=f'suggestion_{uuid.uuid4().hex[:24]}';started=time.monotonic()
    if task=='ocr':
        from backend.engine.ocr import predict_ocr
        result=predict_ocr(checkpoint,path,device=policy['label_device'])
        found=[({'type':'tag','label':result['text'],'category_id':1},float(result['confidence']))] if result['text'] else []
    elif task=='rotated_detection':
        from backend.engine.rotated_detection import predict_rotated_box
        result=predict_rotated_box(checkpoint,path,device=policy['label_device'],threshold=threshold)
        items=result.get('detections') or result.get('predictions') or [result]
        found=[({'type':'rotated_bbox','label':item.get('label','defect'),'rotated_bbox':[item['box'][key] for key in ('cx','cy','width','height','angle_deg')],'category_id':index},
                float(item.get('confidence',item.get('score',0)))) for index,item in enumerate(items,1) if item.get('box')]
    if task in ('ocr','rotated_detection'):
        # Candidate ids follow the suggestion id, as _candidate_annotations mints them, so adoption and undo match by id.
        candidates=[{'id':f'{suggestion_id}_c{index}','confidence':round(confidence,4),'annotation':{**annotation,'id':f'{suggestion_id}_c{index}'}}
                    for index,(annotation,confidence) in enumerate(found,1)]
    else:
        result=infer(task,checkpoint,path,threshold=threshold,device=policy['label_device'])
        candidates=_candidate_annotations(task,result.predictions,result.confidence_score,suggestion_id)
    latency_ms=round((time.monotonic()-started)*1000,2)
    kept=[candidate for candidate in candidates if float(candidate['confidence'])>=threshold]
    if not kept:return {**entry,'status':'zero_candidates'}
    from PIL import Image
    with Image.open(path) as image:width,height=image.size
    with _VERSION_LOCK:
        if (_sha(path)!=before or _sha(checkpoint)!=model_hash or _sha256(labelme)!=labelme_hash or _sha256(studio_json)!=studio_hash
                or routes_annotation.get_annotations(path.stem,file_path=str(path)).get('annotations')):
            raise ValueError('Image, model or labels changed during automatic labeling')
        proposal={'id':suggestion_id,'project_id':project['id'],'status':'pending','created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                  'image_path':str(path),'image_id':path.stem,'image_width':width,'image_height':height,
                  'image_uuid':metadata['image_uuid'],'image_revision':metadata['revision'],
                  'image_sha256':before,'labelme_sha256':labelme_hash,'studio_sha256':studio_hash,
                  'job_id':policy['parent_job_id'],'task':task,'checkpoint_sha256':model_hash,'dataset_fingerprint':fingerprint,
                  'threshold':threshold,'confidence':round(max(float(candidate['confidence']) for candidate in kept),4),'latency_ms':latency_ms,
                  'candidates':kept,'accepted_candidate_ids':[],'backup_version_id':None,
                  'device':policy['label_device'],'size_bounds':{},'keywords':[],'backend':'trained_model','batch_id':batch_id,'cycle_id':cycle_id}
        _write_json(_proposal_path(project,suggestion_id),proposal)
    return {**entry,'status':'drafted','proposal_id':suggestion_id,'batch_id':batch_id,'candidate_count':len(kept)}


def _existing_drafts(project):
    """Step 2 drafts still pending or rejected, keyed by (image path, image bytes, checkpoint bytes) → (id, status):
    the same draft is never written twice, and a draft a person rejected is not offered again for the same bytes."""
    from backend.api.routes_label_suggestions import _SUGGESTION_ID,_proposal_root,_read_proposal
    found={}
    for file in sorted(_proposal_root(project).glob('suggestion_*.json')):
        if not _SUGGESTION_ID.fullmatch(file.stem):continue
        try:item=_read_proposal(project,file.stem)
        except HTTPException:continue
        if item.get('status') in ('pending','rejected'):
            key=(item.get('image_path'),item.get('image_sha256'),item.get('checkpoint_sha256'))
            if item['status']=='pending' or key not in found:found[key]=(item['id'],item['status'])
    return found


def _draft_labels(project,policy,new,checkpoint,cycle_id,step,result):
    """Draft the cycle's new images into one step 2 batch; the summary is result['label_drafts'] (the cycle's result dict).

    Summary: {'batch_id','review_fingerprint','drafted','zero_candidates','duplicate_pending','rejected_before','drafted_before','already_labeled','images'}
    where images holds one entry per new image: the _auto_label entry for a drafted or zero-candidate image, or
    {'image_path','image_sha256','checkpoint_sha256','candidate_count':0,'confidence_threshold','status':...,'proposal_id'?}
    with status 'duplicate_pending' (a pending draft for these image and checkpoint bytes already waits in step 2),
    'rejected_before' (a person rejected a draft for these bytes), 'drafted_before' (an earlier cycle already ran this
    model on these bytes, with or without candidates; policy['drafted'] remembers it, since a cycle that stops for
    review leaves the image "new" until its labels change) or 'already_labeled' (the image has labels; drafts never
    append to them). Only images actually run are batch entries, so entry statuses stay generated / zero_candidates /
    failed. The summary is placed in the result before any draft is written and filled in place (step saves the cycle
    per image), so a cycle that fails or is cancelled after some drafts still journals their batch_id,
    review_fingerprint and counts; those drafts stay pending and the next cycle counts them as duplicate_pending
    instead of drafting them again."""
    from backend.api import routes_annotation
    from backend.api.routes_label_suggestions import _dataset_fingerprint,_persist_batch,_source_path
    model_hash=_sha(checkpoint);existing=_existing_drafts(project);handled=policy.setdefault('drafted',{});stamp=lambda:time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    summary=result['label_drafts']={'batch_id':None,'review_fingerprint':None,'drafted':0,'zero_candidates':0,'duplicate_pending':0,'rejected_before':0,'drafted_before':0,'already_labeled':0,'images':[]}
    queued=[];digests={}
    for path in new:
        digest=_sha(path);digests[path]=digest;entry={'image_path':path,'image_sha256':digest,'checkpoint_sha256':model_hash,'candidate_count':0,'confidence_threshold':policy['confidence_threshold']}
        known=existing.get((path,digest,model_hash));before=handled.get(path) or {}
        if known and known[1]=='pending':summary['images'].append({**entry,'status':'duplicate_pending','proposal_id':known[0]});summary['duplicate_pending']+=1
        elif known:summary['images'].append({**entry,'status':'rejected_before','proposal_id':known[0]});summary['rejected_before']+=1
        elif before.get('image_sha256')==digest and before.get('checkpoint_sha256')==model_hash:summary['images'].append({**entry,'status':'drafted_before'});summary['drafted_before']+=1
        elif routes_annotation.get_annotations(Path(path).stem,file_path=path).get('annotations'):summary['images'].append({**entry,'status':'already_labeled'});summary['already_labeled']+=1
        else:queued.append(path)
    if not queued:return summary
    # One fingerprint per cycle: review compares the dataset against the batch's copy, so nothing in this cycle may
    # change labels or the split after this point (the cycle stops before enrollment when drafts were made).
    fingerprint=_dataset_fingerprint(project)
    batch={'id':'batch_'+uuid.uuid4().hex[:24],'project_id':project['id'],'source_dataset_dir':str(_source_path(project)),
           'job_id':policy['parent_job_id'],'task':policy['task'],'threshold':policy['confidence_threshold'],'keywords':[],
           'device':policy['label_device'],'size_bounds':{},'review_fingerprint':fingerprint,'checkpoint_sha256':model_hash,
           'status':'running','created_at':stamp(),'finished_at':None,'total':len(queued),'processed':0,'generated':0,'zero_candidates':0,'failed':0,
           'entries':[{'image_path':path,'image_id':Path(path).stem,'status':'queued'} for path in queued],'cycle_id':cycle_id}
    _persist_batch(project,batch);summary.update(batch_id=batch['id'],review_fingerprint=fingerprint)
    entry=None
    try:
        for entry in batch['entries']:
            step('automatic_labels',image_path=entry['image_path'])
            row=_auto_label(project,policy,entry['image_path'],checkpoint,batch['id'],fingerprint,cycle_id)
            entry.update(status='generated' if row['status']=='drafted' else 'zero_candidates',candidate_count=row['candidate_count'])
            if row.get('proposal_id'):entry['proposal_id']=row['proposal_id']
            batch['processed']+=1;batch['generated' if row['status']=='drafted' else 'zero_candidates']+=1
            summary[row['status']]+=1;summary['images'].append(row);_persist_batch(project,batch)
            handled[entry['image_path']]={'image_sha256':digests[entry['image_path']],'checkpoint_sha256':model_hash,'status':row['status'],'cycle_id':cycle_id}
    except BaseException as exc:
        # Finalized before the phase ends, so a reader never has to flip a batch it cannot see to 'interrupted'.
        if entry is not None and entry['status']=='queued':entry.update(status='failed',error=str(exc));batch['processed']+=1;batch['failed']+=1
        batch.update(status='cancelled' if isinstance(exc,InterruptedError) else 'failed',error=str(exc),finished_at=stamp());_persist_batch(project,batch)
        raise
    batch.update(status='completed',finished_at=stamp());_persist_batch(project,batch)
    return summary


def _enroll_new_training_images(project, policy, new):
    from backend.api import routes_dataset
    source=Path(project['source_dataset_dir']).resolve()
    existing=routes_dataset._read_split_manifest(source)
    inventory=_inventory(project)
    assignments={}
    for path in inventory:
        relative=Path(path).relative_to(source).as_posix()
        partition=existing.get(path)
        if partition is None:
            partition=next((part for part in Path(relative).parts[:-1] if part in ('train','val','test')),None)
        if path in policy['holdout']:partition='test'
        if path in new and partition is None:
            draw=int(hashlib.sha256(inventory[path].encode()).hexdigest()[:8],16)/0xffffffff
            partition='val' if draw<policy['new_data_validation_fraction'] else 'train'
        if partition:assignments[relative]=partition
    # Persist only owned split assignments. Frozen test identities stay intact.
    routes_dataset._write_split_manifest(source,assignments,0)
    if _holdout(project)!=policy['holdout']:raise ValueError('New data enrollment changed the frozen holdout')
    return {partition:sum(value==partition for value in assignments.values()) for partition in ('train','val','test')}


def _train(project,policy,event):
    from backend.engine.automated_trials import run_automated_training
    from backend.engine.training_provenance import bind_training_version,bind_family_training
    source=Path(project['source_dataset_dir']);dataset=Path(policy['family_dataset_path'] or source)
    if dataset!=source and (dataset.is_symlink() or not dataset.resolve().is_relative_to(Path(project['dataset_dir']).resolve())):raise ValueError('Training inputs must belong to this project')
    binding=bind_family_training(project,dataset,policy['task']) if policy['family_dataset_path'] else bind_training_version(project,source)
    return run_automated_training(task=policy['task'],dataset_path=dataset,source_dataset_path=source,models_dir=Path(project['models_dir']),
        preset=policy['preset'],device=policy['training_device'],mode='fast_retrain',budget=policy['budget'],base_config=policy['base_config'],
        epochs_per_trial=policy['epochs_per_trial'],parent_job_id=policy['parent_job_id'],cancel_event=event,training_binding=binding)


def _candidate_job(result):
    winner=result.get('winner') or {}
    path=winner.get('checkpoint_path')
    return Path(path).parent.name if path else winner.get('job_id')


def _evaluate(project,policy,candidate,event):
    from backend.api.routes_evaluation_history import reevaluate,ReevaluateRequest
    request=SimpleNamespace(state=SimpleNamespace(scoped_project=project,account_user=None))
    evidence={}
    for role,identifier in (('incumbent',policy['parent_job_id']),('candidate',candidate)):
        if event.is_set():raise InterruptedError('Operations cycle cancelled')
        evidence[role]=reevaluate(ReevaluateRequest(source_dataset_path=project['source_dataset_dir'],task=policy['task'],job_id=identifier,dataset_path=policy['family_dataset_path']),request)
    if policy['task'] in ('classification','detection','segmentation','anomaly','patch_classification'):
        from backend.api.routes_model_comparisons import _run_comparison,ComparisonRequest
        evidence['comparison']=_run_comparison(ComparisonRequest(source_dataset_path=project['source_dataset_dir'],task=policy['task'],
            incumbent_job_id=policy['parent_job_id'],candidate_job_id=candidate,full_test=True),project,Path(project['source_dataset_dir']).resolve(),cancelled=event.is_set)
    return evidence


def _approve_and_deploy(project,policy,evidence,candidate,event,on_update):
    if event.is_set():raise InterruptedError('Operations cycle cancelled before approval')
    from backend.api.routes_model_deployments import qualify_candidate_policy
    qualification=qualify_candidate_policy(project,policy,evidence['candidate']['evaluation_id'],
        comparison_id=(evidence.get('comparison') or {}).get('comparison_id'),
        incumbent_evaluation_id=evidence['incumbent']['evaluation_id'],activate=True,
        authorization={'kind':'operations_policy','policy_revision':policy['revision']},expected_job_id=candidate)
    if qualification['status']!='qualified':raise ValueError('; '.join(qualification['reasons']))
    if qualification['qualification']['job_id']!=candidate:raise ValueError('Qualified policy evidence names another candidate')
    approval={'revision':qualification['qualification'],'decision_source':'policy','field_runtime_applied':False}
    on_update({'approval':approval})
    if event.is_set():return {'approval':approval,'deployment':None,'activation_state':'approved_deployment_cancelled','cancellation_requested':True}
    deployment=_deploy_candidate(project,policy,candidate,approval,event) if policy['auto_deploy'] else None
    on_update({'deployment':deployment})
    return {'approval':approval,'deployment':deployment,'activation_state':'deployed' if deployment else 'approved','cancellation_requested':event.is_set()}


def _deploy_candidate(project,policy,candidate,approval,event):
    if event.is_set():raise InterruptedError('Operations cycle cancelled before deployment')
    from backend.api.routes_flowchart import _recipe_file,_active_flow_file,_FLOW_SAVE_LOCK,_restore_flow_file,save_pipeline
    from backend.engine.specialized_models import flow_model_task
    from backend.engine.flow_package import build_flow_package,verify_flow_parity_cohort,write_parity_receipt
    from backend.api.routes_model_deployments import verified_release_revision
    from backend.engine.managed_service import ManagedService
    pipeline,record=_authorized_flow(project,policy)
    replacements=0;checkpoints={};approvals={}
    for node in pipeline.nodes:
        task=flow_model_task(node)
        if not task:continue
        if node.data.model_job_id==policy['parent_job_id'] and task==policy['task']:
            node.data.model_job_id=candidate;replacements+=1
        checkpoint=_checkpoint(project,task,node.data.model_job_id);checkpoints[node.data.model_job_id]=checkpoint
        if node.data.model_job_id==candidate:revision=approval['revision']['revision_id']
        else:
            from backend.api.routes_model_deployments import _store,_active
            with _store(project) as conn:active=_active(conn,Path(project['source_dataset_dir']).resolve(),task)
            revision=active['revision_id'] if active else ''
        verified=verified_release_revision(project,revision,source=Path(project['source_dataset_dir']).resolve(),task=task,job_id=node.data.model_job_id,checkpoint=checkpoint)
        if not verified:raise ValueError('Every model in the automatic deployment flow needs a current approval')
        approvals[node.data.model_job_id]=verified
    if not replacements:raise ValueError('Selected saved flow does not contain the configured parent model')
    destination=Path(project['reports_dir'])/'operations_packages'/uuid.uuid4().hex
    from backend.engine.spatial_calibration import project_calibration_store
    built=build_flow_package(pipeline=pipeline,checkpoints=checkpoints,output_base_dir=destination.parent,package_name=destination.name,approved_revisions=approvals,
        calibrations=project_calibration_store(project).load)
    # The whole frozen holdout (sorted, at most 16 images) on the service device; one image is not acceptance.
    holdout=sorted(policy['holdout'])[:16]
    if len(holdout)<2:raise ValueError('Automatic deployment needs at least two held-out images for package parity')
    parity=verify_flow_parity_cohort(package_dir=destination,pipeline=pipeline,checkpoints=checkpoints,images=[{'path':path} for path in holdout],device=policy['inference_device'],scope='cohort')
    write_parity_receipt(destination,parity)
    if parity['status']!='passed':raise ValueError('Candidate flow and its executable package differ')
    if event.is_set():raise InterruptedError('Operations cycle cancelled before deployment')
    request=SimpleNamespace(state=SimpleNamespace(scoped_project=project,account_user=None))
    recipe=_recipe_file(record['recipe_task'],project['source_dataset_dir'],Path(project['project_dir']))
    active=_active_flow_file(Path(project['project_dir']))
    with _FLOW_SAVE_LOCK:
        previous_recipe=recipe.read_bytes() if recipe.exists() else None
        previous_active=active.read_bytes() if active.exists() else None
        saved=save_pipeline(pipeline,recipe_task=record['recipe_task'],source_dataset_path=project['source_dataset_dir'],request=request)
        try:
            if event.is_set():raise InterruptedError('Operations cycle cancelled before service application')
            local=ManagedService(project['project_dir']).apply(str(destination),policy['inference_device'],policy['reviewer'],project)
        except Exception:
            _restore_flow_file(recipe,previous_recipe);_restore_flow_file(active,previous_active)
            raise
    return {'package_path':str(destination),'local':local,'candidate_job_id':candidate,'replaced_nodes':replacements,
            'source_flow_version_id':policy['pipeline_version_id'],'flow_version_id':saved['version_id'],'parity':parity}


def run_cycle(project,event,*,training_fn=None,evaluation_fn=None):
    # Capture the policy and publish the running journal under the same
    # admission lock used to authorize configuration changes.
    with _cycle_lock(project):
        return _run_cycle_locked(project,event,training_fn=training_fn,evaluation_fn=evaluation_fn)


def _run_cycle_locked(project,event,*,training_fn=None,evaluation_fn=None):
    store=OperationsStore(project['project_dir']);policy=store.policy()
    if not policy:raise ValueError('Configure an operations policy first')
    cycle={'cycle_id':'cycle_'+uuid.uuid4().hex,'status':'running','phase':'discover','created_at':time.time(),'updated_at':time.time(),'owner_pid':os.getpid(),'owner_created_at':__import__('psutil').Process(os.getpid()).create_time(),
           'policy_revision':policy['revision'],'labelset_id':policy['labelset_id'],'events':[],'result':{},'error':None}
    def step(phase,**evidence):
        cycle.update(phase=phase,updated_at=time.time());cycle['events'].append({'at':time.time(),'phase':phase,**evidence});store.save(cycle)
        if event.is_set():raise InterruptedError('Operations cycle cancelled')
    with project_scope(project):
        try:
            step('discover')
            if policy['labelset_id']!=project.get('active_labelset_id','default'):raise ValueError('Active label set differs from the configured policy')
            checkpoint=_checkpoint(project,policy['task'],policy['parent_job_id'])
            if _sha(checkpoint)!=policy['parent_checkpoint_sha256']:raise ValueError('Configured parent checkpoint changed')
            if _holdout(project)!=policy['holdout']:raise ValueError('Independent holdout images, labels or split changed')
            inventory=_inventory(project);new=[path for path,digest in inventory.items() if path not in policy['holdout'] and (policy['seen'].get(path)!=digest or policy.get('seen_labels',{}).get(path)!=_image_binding(project,path))]
            input_changed=_input_version(project,policy)!=policy.get('input_version')
            holdout_hashes={row['content_hash'] for row in policy['holdout'].values()}
            if any(inventory[path] in holdout_hashes for path in new):raise ValueError('New training data duplicates an independent holdout image')
            if not new and not (input_changed and policy['auto_retrain']):cycle['status']='idle';return store.save(cycle)
            cycle['result']['new_images']=[{'image_path':path,'sha256':inventory[path]} for path in new]
            if policy['auto_label'] and _reviewable_parent(project,policy,checkpoint) is None:
                # Never write a draft step 2 cannot adopt: the person fixes the parent, and the next cycle retries these images.
                cycle['result']['label_drafts']={'batch_id':None,'review_fingerprint':None,'drafted':0,'zero_candidates':0,'duplicate_pending':0,'already_labeled':0,'images':[],'skipped_reason':_UNREVIEWABLE_PARENT}
                raise ValueError(_UNREVIEWABLE_PARENT)
            step('automatic_labels',new_images=len(new))
            # The summary lands in cycle['result']['label_drafts'] before drafting, so it survives a failure mid-batch.
            drafts=_draft_labels(project,policy,new,checkpoint,cycle['cycle_id'],step,cycle['result']) if policy['auto_label'] else None
            # What this cycle drafted is remembered now: the cycle may stop for review below, before the policy's
            # normal save, and the next cycle must not run the same model on the same bytes again.
            if drafts is not None:store.save_policy(policy,expected_revision=cycle['policy_revision'])
            # Drafts are not labels: while any of this cycle's images waits in step 2, the cycle stops for review.
            waiting=bool(drafts and (drafts['drafted'] or drafts['duplicate_pending']))
            if policy['auto_retrain']:
                from backend.engine.dataset_metadata import metadata_for_path
                required=[]
                for path in new:
                    row=metadata_for_path(Path(project['project_dir']),Path(project['source_dataset_dir']),Path(path),Path(project['annotations_dir']))
                    if policy['require_label_review'] and row.get('workflow_state')!='approved':required.append(path)
                if required:
                    cycle['status']='needs_review';cycle['result']['pending_review_images']=required
                    return store.save(cycle)
                if waiting:
                    # Enrollment rewrites the split manifest, which the batch's review_fingerprint covers; the drafts
                    # would become unadoptable and unreviewed images would reach the training split.
                    cycle['status']='needs_review';return store.save(cycle)
                step('retraining')
                if not policy['family_dataset_path']:cycle['result']['split_counts']=_enroll_new_training_images(project,policy,new)
                training=(training_fn or _train)(project,policy,event);cycle['result']['training']=training
                if training.get('status')!='completed':raise InterruptedError('Retraining did not complete') if training.get('status')=='cancelled' else ValueError('Retraining failed')
                candidate=_candidate_job(training)
                if not candidate:raise ValueError('Completed retraining has no checkpoint winner')
                step('fixed_holdout_evaluation',candidate_job_id=candidate)
                evidence=(evaluation_fn or _evaluate)(project,policy,candidate,event);cycle['result']['evaluation']=evidence
                if policy['auto_approve']:
                    step('quality_approval')
                    def record_activation(values):
                        cycle['result'].update(values);store.save(cycle)
                    cycle['result'].update(_approve_and_deploy(project,policy,evidence,candidate,event,record_activation))
                else:
                    from backend.engine.operations_review import freeze_subject
                    cycle['result']['candidate_job_id']=candidate
                    cycle['result']['review_handoff']=freeze_subject(project,policy,candidate,evidence)
                cycle['status']=cycle['result'].get('activation_state','completed') if policy['auto_approve'] else 'awaiting_approval'
            else:cycle['status']='needs_review' if waiting else 'completed'
            policy['seen'].update({path:inventory[path] for path in new})
            policy.setdefault('seen_labels',{}).update({path:_image_binding(project,path) for path in new})
            policy['input_version']=_input_version(project,policy);store.save_policy(policy, expected_revision=cycle['policy_revision'])
            if cycle['result'].get('activation_state'):
                cycle.update(phase='finish',updated_at=time.time());cycle['events'].append({'at':time.time(),'phase':'finish','cancellation_requested':event.is_set()})
            else:step('finish')
            return store.save(cycle)
        except InterruptedError as exc:cycle.update(status='approved_deployment_cancelled' if cycle['result'].get('approval') else 'cancelled',error=str(exc))
        except PolicyRevisionConflict as exc:
            cycle.update(status='interrupted',error=str(exc),error_code=exc.code,
                         policy_conflict={'expected_revision':exc.expected_revision,'current_revision':exc.current_revision})
        except Exception as exc:cycle.update(status='blocked' if cycle['phase']=='discover' else 'failed',error=str(getattr(exc,'detail',exc)))
        cycle['updated_at']=time.time();return store.save(cycle)
