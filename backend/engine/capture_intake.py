"""Project-owned service capture candidates and explicit dataset branch adoption.

Service SQLite records are read only. Captures are snapshotted with their real
job/model/graph identity; predictions never become labels or normal truth.
Adoption copies a bounded source into a new owned source, retains its test split,
and leaves captured images excluded until the existing label/review workflow
explicitly includes them. Source switching is a separate user action.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from backend.engine.intake_sampling import IntakeSampler, IntakeSamplingPolicy, IntakeEvent
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import uuid

from PIL import Image
from backend.engine import dataset_metadata as dm
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
from backend.engine.flow_workspace import atomic_json
from backend.engine.image_truth import digest
from backend.engine.heldout_splits import normalized_assignments

COPY_LIMIT_BYTES = 2 * 1024**3
TRACKED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | {'.json','.txt','.xml','.csv','.yaml','.yml'}


def _scope(project):
    source = project.get('source_dataset_dir')
    if not source or not Path(source).is_dir() or Path(source).is_symlink(): raise ValueError('Select an unlinked active project source')
    return {'project_id':project['id'],'source_dataset_path':str(Path(source).resolve()),
            'task':project['task'],'labelset_id':project.get('active_labelset_id','default')}


def _owned(project, path):
    root = Path(project['project_dir']).resolve(); path = Path(path)
    if not path.resolve().is_relative_to(root): raise ValueError('Intake storage is outside the project')
    for candidate in (path,*path.parents):
        if candidate == root: break
        if candidate.is_symlink(): raise ValueError('Intake storage cannot contain symbolic links')
    return path


def _root(project):
    root = _owned(project,Path(project['dataset_dir'])/'capture_intake'); root.mkdir(parents=True,exist_ok=True)
    return root


def _index(project):
    path = _owned(project,_root(project)/'index.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'schema_version':1,'candidates':{},'versions':[]}


def _files(project):
    source = Path(project['source_dataset_dir']).resolve(); project_root = Path(project['project_dir']).resolve()
    rows = []
    for directory, names, files in os.walk(source,followlinks=False):
        names[:] = sorted(name for name in names if not name.startswith('.') and (Path(directory)/name).resolve()!=project_root)
        for name in sorted(files):
            path=Path(directory)/name
            if name.startswith('.') or path.suffix.lower() not in TRACKED_EXTENSIONS: continue
            if path.is_symlink() or not path.resolve().is_relative_to(source): raise ValueError('Source dataset cannot contain linked tracked files')
            rows.append({'relative_path':path.relative_to(source).as_posix(),'sha256':dm._hash(path),'size':path.stat().st_size})
    return rows


def _split(project):
    source=Path(project['source_dataset_dir']).resolve();key=hashlib.sha256(str(source).encode()).hexdigest()
    path=Path(project['dataset_dir'])/'splits'/f'{key}.json'
    if path.is_symlink() or not path.is_file(): raise ValueError('Save a fixed split before registering service captures')
    record=json.loads(path.read_text(encoding='utf-8'))
    if record.get('folder_path')!=str(source) or not isinstance(record.get('assignments'),dict): raise ValueError('Saved split source changed')
    record={**record,'assignments':normalized_assignments(source,record['assignments'])}
    if not any(part=='test' for part in record['assignments'].values()): raise ValueError('A fixed held-out test split is required for intake')
    return path,record


def _source_binding(project):
    split,_=_split(project)
    from backend.engine.grouped_dataset_views import source_image_paths
    source=Path(project['source_dataset_dir']).resolve(); rows=source_image_paths(source,project['task'],include_unused=True)
    labels=[]
    for image in rows:
        labels.append({'relative_path':image.relative_to(source).as_posix(),
                       'annotation_sha256':dm._annotation_hash(project['project_dir'],source,image,project['annotations_dir']),
                       'mask_sha256':dm._mask_hash(project['project_dir'],source,image,project['annotations_dir'])})
    with dm.metadata_transaction(project['project_dir'],source,project['annotations_dir']) as ledger:
        policy=copy.deepcopy(ledger.get('team_data',{}))
    return {'source_files_sha256':digest(_files(project)),'split_sha256':dm._hash(split),'labels_sha256':digest(labels),'policy_sha256':digest(policy)}


def _service_rows(project, job_ids, limit):
    state=_owned(project,Path(project['project_dir'])/'runtime_service'/'state')
    database=_owned(project,state/'inspection_service.sqlite3')
    if not database.is_file(): raise ValueError('No project-owned service capture database is available')
    if job_ids is not None and (not job_ids or len(job_ids)>5000 or any(not re.fullmatch(r'[0-9a-f]{32}',identifier) for identifier in job_ids)):
        raise ValueError('Select valid service job IDs')
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        if job_ids:
            rows=connection.execute('SELECT * FROM jobs WHERE job_id IN ('+','.join('?' for _ in job_ids)+') ORDER BY created_at,rowid',job_ids).fetchall()
            if len(rows)!=len(set(job_ids)): raise ValueError('Service job is absent from this project')
        else:
            rows=connection.execute("SELECT * FROM jobs WHERE state IN ('completed','error','delivery_error','delivery_pending') ORDER BY updated_at DESC,rowid DESC LIMIT ?",(limit,)).fetchall()
    return [dict(row) for row in rows]


def _candidate(project,index,identifier,*,check_original=True):
    if not re.fullmatch(r'capture_[0-9a-f]{32}',identifier) or identifier not in index['candidates']: raise ValueError('Capture candidate is unavailable')
    row=copy.deepcopy(index['candidates'][identifier])
    if row['scope']!=_scope(project): raise ValueError('Capture candidate source/task/labelset changed')
    if row.get('snapshot_path'):
        snapshot=_owned(project,_root(project)/row['snapshot_path'])
        if not snapshot.is_file() or dm._hash(snapshot)!=row['source_sha256']: raise ValueError('Capture snapshot changed')
        if check_original and dm._hash(Path(row['origin']['image_path']))!=row['source_sha256']: raise ValueError('Captured source changed after registration')
    return row


def _sampling_policy(project):
    path=_owned(project,_root(project)/'sampling-policy.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def save_sampling_policy(project, policy, *, expected_revision):
    try:parsed=IntakeSamplingPolicy(**policy)
    except TypeError as exc:raise ValueError('Sampling policy fields are invalid') from exc
    root=_root(project)
    with dm._file_lock(root/'intake.lock'):
        old=_sampling_policy(project)
        revision=old['policy']['revision'] if old else 0
        if type(expected_revision) is not int or expected_revision!=revision or parsed.revision!=revision+1:
            raise ValueError('Sampling policy revision changed; reopen before saving')
        if old and IntakeSamplingPolicy(**old['policy']).geometry()!=parsed.geometry():
            raise ValueError('Sampling window geometry cannot change in an existing project store')
        record={'schema_version':1,'policy':parsed.to_json(),'policy_ref':parsed.ref}
        history=_owned(project,root/'sampling-policies');history.mkdir(exist_ok=True)
        archived=_owned(project,history/f'r{parsed.revision:010d}.json')
        if archived.exists() and json.loads(archived.read_text(encoding='utf-8'))!=record:
            raise ValueError('Sampling policy revision already has different replay evidence')
        if not archived.exists():atomic_json(archived,record)
        atomic_json(root/'sampling-policy.json',record)
        return record


def sampling_status(project, *, limit=100):
    if type(limit) is not int or not 1<=limit<=500:raise ValueError('Sampling history limit must be 1–500')
    record=_sampling_policy(project);root=_root(project)
    held=[]
    for row in _index(project)['candidates'].values():
        if row.get('snapshot_path'):
            path=_owned(project,root/row['snapshot_path'])
            if path.is_file():held.append(path.stat().st_size)
    result={**(record or {'schema_version':1,'policy':None,'policy_ref':None}),
            'held_items':len(held),'held_bytes':sum(held),'receipts':[],'windows':[],
            'policy_history':[json.loads(_owned(project,path).read_text(encoding='utf-8')) for path in
                              sorted(_owned(project,root/'sampling-policies').glob('r*.json'))[-limit:]]}
    if record:
        policy=IntakeSamplingPolicy(**record['policy']);sampler=IntakeSampler(_owned(project,root/'sampling.sqlite3'),policy)
        from contextlib import closing
        with closing(sampler._connect()) as db:
            db.row_factory=__import__('sqlite3').Row
            result['receipts']=[dict(row) for row in db.execute('SELECT event_id, decision, reason, policy_ref, window, run_ref, size_bytes FROM receipts ORDER BY decided_at DESC,event_id LIMIT ?',(limit,))]
            windows=[row[0] for row in db.execute('SELECT DISTINCT window FROM receipts ORDER BY window DESC LIMIT 30')]
        for row in result['receipts']:row['run_ref']=json.loads(row['run_ref'])
        result['windows']=[sampler.status(window) for window in windows]
    return result


def _sample_service(project, policy, receipt, result, path):
    binding=json.loads(receipt.get('runtime_binding_json') or 'null') or {}
    steps=result.get('execution_steps') or []
    sampler=IntakeSampler(_owned(project,_root(project)/'sampling.sqlite3'),policy)
    from contextlib import closing
    with closing(sampler._connect()) as db:
        db.row_factory=sqlite3.Row
        previous=db.execute('SELECT event_id,decision,reason,policy_ref,window,run_ref,key,size_bytes,note FROM receipts WHERE event_id=?',
                            (receipt['job_id'],)).fetchone()
    if previous:
        immutable=dict(previous);immutable['run_ref']=json.loads(immutable['run_ref'])
        if (immutable['run_ref'].get('image_sha256')!=receipt.get('image_sha256')
                or not path.is_file() or path.is_symlink() or dm._hash(path)!=receipt.get('image_sha256')):
            raise ValueError('Captured source changed after sampling decision')
        return sampler,immutable
    stamp=receipt['created_at']
    captured=float(stamp) if isinstance(stamp,(int,float)) else datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
    from backend.engine.score_contract import compatible_scores, validate_score_spec
    scores=[{'value':crop['defect_score'],'spec':crop['score_spec']} for crop in result.get('crops',[])
            if isinstance(crop,dict) and crop.get('defect_score') is not None and crop.get('score_spec')]
    score=result.get('sampling_score') or (scores[0] if scores else None)
    for candidate in scores:
        try:
            if (policy.score_spec and policy.low_confidence_band and compatible_scores(validate_score_spec(candidate['spec']),policy.score_spec)
                    and policy.low_confidence_band[0]<=candidate['value']<=policy.low_confidence_band[1]):
                score=candidate;break
        except (TypeError,ValueError):continue
    event=IntakeEvent(event_id=receipt['job_id'],captured_at=captured,size_bytes=path.stat().st_size if path.is_file() else 0,
        product=binding.get('product_id') or 'unknown',lot=binding.get('lot_id') or 'unknown',camera=receipt['source'] or 'unknown',
        run_ref={'run_id':receipt['job_id'],'node_id':str(steps[-1].get('node_id') or 'legacy_unknown') if steps else 'legacy_unknown',
                 'recipe':str(binding.get('manifest_sha256') or result.get('graph_sha256') or 'legacy_unknown'),
                 'image_sha256':receipt.get('image_sha256')},
        verdict=receipt.get('verdict') or result.get('final_verdict'),
        flags=tuple(['model_disagreement'] if result.get('models_disagree') is True else []),
        score=score)
    sampler=IntakeSampler(_owned(project,_root(project)/'sampling.sqlite3'),policy)
    decided=asdict(sampler.decide(event))
    # Immutable selection evidence is separate from mutable retention status.
    immutable={key:decided[key] for key in ('event_id','decision','reason','policy_ref','window','run_ref','key','size_bytes','note')}
    return sampler,immutable


def register_service_jobs(project, *, job_ids=None, limit=100):
    if type(limit) is not int or not 1<=limit<=5000: raise ValueError('Read 1–5000 service jobs per intake')
    scope=_scope(project);rows=_service_rows(project,job_ids,limit);binding=_source_binding(project)
    root=_root(project);source=Path(project['source_dataset_dir']).resolve()
    from backend.engine.grouped_dataset_views import source_image_paths
    source_hashes={dm._hash(path):path.relative_to(source).as_posix() for path in source_image_paths(source,project['task'],include_unused=True)}
    registered=[];sampling_receipts=[]
    with dm._file_lock(root/'intake.lock'):
        index=_index(project)
        record=_sampling_policy(project);policy=IntakeSamplingPolicy(**record['policy']) if record else None
        for receipt in rows:
            if receipt['state'] in {'queued','running'}: raise ValueError('Wait for the service job to finish before intake')
            receipt_sha=digest(receipt)
            # A selected service event owns one immutable candidate even when
            # the source revision changes. Human adoption still checks its old binding.
            sampled_previous=next((row for row in index['candidates'].values()
                if row.get('sampling_receipt') and row['sampling_receipt']['event_id']==receipt['job_id']),None)
            if sampled_previous:
                sampling_receipts.append(copy.deepcopy(sampled_previous['sampling_receipt']))
                if sampled_previous['scope']==scope:registered.append(copy.deepcopy(sampled_previous))
                continue
            previous=next((row for row in index['candidates'].values() if row['scope']==scope and row['job_receipt_sha256']==receipt_sha and row['base_source_binding']==binding),None)
            if previous:
                registered.append(copy.deepcopy(previous))
                if previous.get('sampling_receipt'):sampling_receipts.append(previous['sampling_receipt'])
                continue
            result=json.loads(receipt.get('result_json') or 'null') or {}
            path=Path(receipt['image_path']);sampler=None;sampling=None
            if policy:
                sampler,sampling=_sample_service(project,policy,receipt,result,path);sampling_receipts.append(sampling)
                if sampling['decision']=='skipped':continue
            failure=receipt.get('error'); checksum=receipt.get('image_sha256') or None
            readable=False;extension='.image'
            try:
                if (path.is_symlink() or not path.is_file() or not any(path.resolve().is_relative_to(allowed) for allowed in (source,Path(project['project_dir']).resolve()/'runtime_service'/'state'/'uploads'))):
                    raise ValueError('Captured image is unavailable, linked or outside this project source/uploads')
                if dm._hash(path)!=checksum: raise ValueError('Captured source hash differs from the service job')
                with Image.open(path) as image:
                    if image.width*image.height>100_000_000: raise ValueError('Captured image exceeds the pixel limit')
                    extension={ 'PNG':'.png','JPEG':'.jpg','TIFF':'.tif','BMP':'.bmp','WEBP':'.webp'}.get(image.format,'.png');image.verify()
                readable=True
            except (ValueError,OSError) as exc:failure=failure or str(exc)
            duplicate=source_hashes.get(checksum)
            if not duplicate:
                duplicate=next((row['candidate_id'] for row in index['candidates'].values() if row['scope']==scope and row['source_sha256']==checksum and checksum and row['base_source_binding']==binding),None)
            identifier='capture_'+uuid.uuid4().hex;snapshot=None
            if readable:
                snapshot=f'candidates/{identifier}/image{extension}';target=_owned(project,root/snapshot);target.parent.mkdir(parents=True)
                try:
                    if sampler:
                        from backend.engine.artifact_retention import ArtifactRetention
                        retention=ArtifactRetention(project['project_dir'])
                        with retention.lock():
                            storage=retention.status(project);quota=storage['policy']['quota_bytes']
                            if quota is not None and storage['total_bytes']+path.stat().st_size>quota:
                                raise ValueError('Capture retention quota exhausted (recoverable trash still consumes bytes)')
                            shutil.copyfile(path,target)
                            if dm._hash(target)!=checksum:raise ValueError('Capture changed while snapshotting')
                    else:
                        shutil.copyfile(path,target)
                        if dm._hash(target)!=checksum:raise ValueError('Capture changed while snapshotting')
                except (OSError,ValueError) as exc:
                    if not sampler:raise
                    shutil.rmtree(target.parent,ignore_errors=True);snapshot=None;failure=str(exc);readable=False
                if sampler:
                    if snapshot:sampler.mark_retained(receipt['job_id'])
                    else:sampler.mark_retention_failed(receipt['job_id'],failure)
            if sampler and not readable:sampler.mark_retention_failed(receipt['job_id'],failure or 'Image unavailable')
            row={'sampling_receipt':sampling,'schema_version':1,'candidate_id':identifier,'scope':scope,'revision':1,'created_at':dm._now(),
                 'source_sha256':checksum,'snapshot_path':snapshot,'job_receipt_sha256':receipt_sha,'base_source_binding':binding,
                 'routing':'failed' if failure or not readable else 'duplicate' if duplicate else 'unknown',
                 'duplicate_of':duplicate,'failure':failure,'truth_verdict':'UNKNOWN','source_prediction':receipt.get('verdict') or result.get('final_verdict'),
                 'review_state':'pending','review':None,'history':[],'adoptions':[],
                 'origin':{'job_id':receipt['job_id'],'image_path':receipt['image_path'],'capture_source':receipt['source'],
                           'service_state':receipt['state'],'created_at':receipt['created_at'],'updated_at':receipt['updated_at'],
                           'runtime_identity':result.get('runtime_identity'), 'graph_sha256':result.get('graph_sha256'),
                           'node_evidence':result.get('execution_steps',[]),'roi_evidence':result.get('crops',[]),
                           'review_evidence':{'models_disagree':result.get('models_disagree') is True,
                             'review_required':result.get('review_required') is True or result.get('final_verdict')=='REVIEW',
                             'score':result.get('max_defect_score'), 'score_unit':result.get('score_unit')},
                           'runtime_binding':json.loads(receipt.get('runtime_binding_json') or 'null'),
                           'binding_provenance':receipt.get('binding_provenance','legacy_unknown'),
                           'error':receipt.get('error'),'job_receipt_sha256':receipt_sha}}
            index['candidates'][identifier]=row;registered.append(copy.deepcopy(row))
        atomic_json(root/'index.json',index)
    return {'candidates':registered,'total':len(registered),'scope':scope,'sampling_receipts':sampling_receipts}


def list_candidates(project):
    scope=_scope(project); index=_index(project);rows=[]
    for value in index['candidates'].values():
        if value['scope']!=scope:continue
        try:row=_candidate(project,index,value['candidate_id']);row['stale']=False
        except (ValueError,OSError) as exc:row=copy.deepcopy(value);row['stale']=True;row['stale_reason']=str(exc)
        rows.append(row)
    return {'candidates':sorted(rows,key=lambda row:row['created_at'],reverse=True),'total':len(rows),'scope':scope}


def review_queue(project, *, threshold=.5, margin=.05):
    """Rank persisted evidence without converting a prediction into truth."""
    if any(isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1 for value in (threshold,margin)):
        raise ValueError('Review threshold and margin must be finite fractions from 0 to 1')
    result=list_candidates(project)
    for row in result['candidates']:
        evidence=row['origin'].get('review_evidence') or {};reasons=[];rank=0
        if row.get('stale') or row['routing']=='failed':reasons.append('error');rank=max(rank,400)
        if evidence.get('models_disagree') is True:reasons.append('disagreement');rank=max(rank,300)
        if evidence.get('review_required') is True:reasons.append('review');rank=max(rank,200)
        score=evidence.get('score')
        if evidence.get('score_unit')=='fraction' and not isinstance(score,bool) and isinstance(score,(int,float)) and math.isfinite(score) and 0<=score<=1 and abs(score-threshold)<=margin:
            reasons.append('threshold');rank=max(rank,100)
        if row['routing']=='duplicate':reasons.append('duplicate')
        if row['truth_verdict']=='UNKNOWN':reasons.append('unknown_truth');rank+=10
        row.update(review_reasons=reasons,review_priority=rank)
    result['candidates'].sort(key=lambda row:(row['review_state']=='reviewed',-row['review_priority'],row['created_at'],row['candidate_id']))
    result.update(pending=sum(row['review_state']=='pending' for row in result['candidates']),threshold=threshold,margin=margin)
    return result


def candidate_image(project,identifier):
    row=_candidate(project,_index(project),identifier)
    if not row['snapshot_path']:raise ValueError('Capture image is unavailable')
    return _owned(project,_root(project)/row['snapshot_path'])


def review_candidate(project,identifier,*,expected_revision,actor,decision,note=''):
    if decision not in {'adopt','reject'} or not isinstance(actor,str) or not actor.strip() or len(actor)>100: raise ValueError('Choose adopt/reject and enter a reviewer')
    if not isinstance(note,str) or len(note)>2000:raise ValueError('Intake review note exceeds 2000 characters')
    root=_root(project)
    with dm._file_lock(root/'intake.lock'):
        index=_index(project);row=_candidate(project,index,identifier)
        if row['revision']!=expected_revision:raise dm.RevisionConflict(row)
        if decision=='adopt' and (row['routing'] in {'duplicate','failed'} or not row['snapshot_path']):raise ValueError('Duplicate or failed/unavailable captures cannot be adopted')
        row['revision']+=1;row['review_state']='reviewed';row['review']={'decision':decision,'actor':actor.strip(),'note':note.strip(),
          'at':dm._now(),'source_sha256':row['source_sha256'],'candidate_revision':row['revision']}
        row['history'].append(copy.deepcopy(row['review']));index['candidates'][identifier]=row;atomic_json(root/'index.json',index)
        return row


def _fixed_cohorts(project):
    from backend.engine.flow_evaluation import list_evidence
    rows = [{'cohort_id':row['cohort_id'],'cohort_sha256':row['record_sha256'],'input_sha256':row['input_sha256'],
             'truth_sha256':row['truth_sha256'],'scope':row['scope']} for row in list_evidence(project,'cohorts')]
    source=Path(project['source_dataset_dir'])
    if source.parent.parent.resolve()==(_root(project)/'versions').resolve():
        ancestor=read_version(project,source.parent.name)
        for row in ancestor.get('fixed_cohorts',[]):
            if not any(current['cohort_id']==row['cohort_id'] for current in rows):rows.append(copy.deepcopy(row))
    return rows


def adopt_candidates(project, identifiers, *, actor, name):
    if not identifiers or len(identifiers)>1000 or len(set(identifiers))!=len(identifiers):raise ValueError('Choose 1–1000 unique reviewed candidates')
    if not isinstance(actor,str) or not actor.strip() or len(actor)>100 or not isinstance(name,str) or not name.strip() or len(name)>200:raise ValueError('Enter a reviewer and version name')
    scope=_scope(project);root=_root(project);source=Path(project['source_dataset_dir']).resolve()
    with dm._file_lock(root/'intake.lock'):
        index=_index(project);candidates=[_candidate(project,index,identifier) for identifier in identifiers];binding=_source_binding(project)
        for row in candidates:
            if row['review_state']!='reviewed' or not row['review'] or row['review']['decision']!='adopt':raise ValueError('Every adopted candidate requires an explicit human intake review')
            if row['routing'] in {'failed','duplicate'}:raise ValueError('Duplicate/failed captures cannot be adopted')
            if row['base_source_binding']!=binding:raise ValueError('Active source, labels, split or policy changed since intake; register the current capture again')
        files=_files(project)
        if sum(row['size'] for row in files)>COPY_LIMIT_BYTES:raise ValueError('Owned intake branch exceeds the 2 GiB copy limit; use a smaller source scope')
        _,split=_split(project);identifier='intake_'+uuid.uuid4().hex;versions=_owned(project,root/'versions');versions.mkdir(exist_ok=True)
        directory=versions/identifier;staging=Path(tempfile.mkdtemp(prefix='.adopting-',dir=versions));final_source=directory/'source'
        copied_overlays=[];split_path=None;published=False
        try:
            branch=staging/'source';branch.mkdir()
            for row in files:
                original=source/row['relative_path'];target=branch/row['relative_path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,target)
                if dm._hash(target)!=row['sha256']:raise ValueError('Source changed during intake adoption')
            assignments=dict(split['assignments']);adopted=[]
            for row in candidates:
                snapshot=_owned(project,root/row['snapshot_path']);relative=f"images/train/capture_{row['candidate_id'][8:]}{snapshot.suffix}"
                target=branch/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(snapshot,target)
                if dm._hash(target)!=row['source_sha256']:raise ValueError('Capture snapshot changed during adoption')
                assignments[relative]='train';adopted.append({'candidate_id':row['candidate_id'],'relative_path':relative,'source_sha256':row['source_sha256'],
                  'truth_verdict':'UNKNOWN','usage_state':'not_used','workflow_state':'needs_review','review':row['review'],'origin':row['origin']})
            test_records=[row for row in files if assignments.get(row['relative_path'])=='test']
            with dm.metadata_transaction(project['project_dir'],source,project['annotations_dir']) as ledger:policy=copy.deepcopy(ledger.get('team_data',{}))
            record={'schema_version':1,'version_id':identifier,'name':name.strip(),'created_at':dm._now(),'actor':actor.strip(),
                    'scope':scope,'source_dataset_path':str(final_source),'parent_source_dataset_path':str(source),'parent_source_binding':binding,
                    'base_files':files,'adopted':adopted,'split_assignments':assignments,'fixed_test_records':test_records,
                    'fixed_test_sha256':digest(test_records),'fixed_cohorts':_fixed_cohorts(project),
                    'review_policy':policy,'review_policy_sha256':digest(policy),'activated':False,
                    'training_readiness':'Capture images require labels, review-policy approval and explicit inclusion',
                    'lineage':{'parent_source_dataset_path':str(source),'task':project['task'],'labelset_id':scope['labelset_id'],
                               'parent_model_rule':'Exact task, ordered classes and architecture are checked by the existing warm-start gate'}}
            if _source_binding(project)!=binding:raise ValueError('Active source changed during intake adoption')
            staging.rename(directory)
            # Copy image-parent annotation overlays with correct branch mask paths.
            from backend.engine.grouped_dataset_views import source_image_paths
            parents={image.parent for image in source_image_paths(source,project['task'],include_unused=True)}
            for parent in parents:
                overlay=dataset_annotation_dir(parent,Path(project['annotations_dir']),use_scope=False)
                new_overlay=dataset_annotation_dir(final_source/parent.relative_to(source),Path(project['annotations_dir']),use_scope=False)
                if not overlay.is_dir():continue
                if overlay.is_symlink() or any(path.is_symlink() for path in overlay.rglob('*')):raise ValueError('Label overlay cannot contain symbolic links')
                new_overlay.mkdir(parents=True,exist_ok=False);copied_overlays.append(new_overlay)
                for file in overlay.rglob('*'):
                    relative=file.relative_to(overlay)
                    if not file.is_file() or relative.parts[0]=='metadata' or file.name.startswith('.'):continue
                    target=new_overlay/relative;target.parent.mkdir(parents=True,exist_ok=True)
                    if file.suffix=='.json':
                        data=json.loads(file.read_text(encoding='utf-8'))
                        if isinstance(data.get('mask_file'),str) and Path(data['mask_file']).is_relative_to(overlay):data['mask_file']=str(new_overlay/Path(data['mask_file']).relative_to(overlay))
                        atomic_json(target,data)
                    else:shutil.copyfile(file,target)
            key=hashlib.sha256(str(final_source).encode()).hexdigest();split_path=_owned(project,Path(project['dataset_dir'])/'splits'/f'{key}.json')
            atomic_json(split_path,{'folder_path':str(final_source),'assignments':assignments,'seed':split.get('seed',42),'intake_version_id':identifier})
            with dm.metadata_transaction(project['project_dir'],final_source,project['annotations_dir']) as ledger:
                if policy:ledger['team_data']=policy
                for row in adopted:
                    metadata=dm._ensure(ledger,project['project_dir'],final_source,final_source/row['relative_path'],project['annotations_dir'])
                    metadata.update(workflow_state='needs_review',usage_state='not_used',capture_intake={'version_id':identifier,**copy.deepcopy(row)})
                    dm._event(metadata,actor.strip(),'capture_adopted_pending_label_review',{'candidate_id':row['candidate_id'],'usage_state':'not_used'})
            metadata_scope=dataset_annotation_dir(final_source,Path(project['annotations_dir']),use_scope=False)
            if metadata_scope not in copied_overlays:copied_overlays.append(metadata_scope)
            # Bind copied label/mask bytes and label revisions without copying
            # explicit truth declarations into the new source scope.
            from backend.engine.image_truth import image_binding
            record['copied_label_bindings']=[{'relative_path':image.relative_to(final_source).as_posix(),
                'binding':image_binding(dm.metadata_for_path(project['project_dir'],final_source,image,project['annotations_dir']))}
                for image in source_image_paths(final_source,project['task'],include_unused=True)]
            record['record_sha256']=digest(record);atomic_json(directory/'record.json',record)
            for row in candidates:
                value=index['candidates'][row['candidate_id']];value['adoptions'].append(identifier);value['revision']+=1
            index['versions'].append(identifier);atomic_json(root/'index.json',index);published=True
            return record
        finally:
            if staging.exists():shutil.rmtree(staging)
            if not published:
                if directory.exists():shutil.rmtree(directory)
                for overlay in copied_overlays:
                    if overlay.exists():shutil.rmtree(overlay)
                if split_path:split_path.unlink(missing_ok=True)


def read_version(project,identifier):
    if not re.fullmatch(r'intake_[0-9a-f]{32}',identifier):raise ValueError('Invalid intake version identifier')
    directory=_owned(project,_root(project)/'versions'/identifier);path=_owned(project,directory/'record.json')
    if not path.is_file():raise ValueError('Intake version is unavailable')
    record=json.loads(path.read_text(encoding='utf-8'));body={key:value for key,value in record.items() if key!='record_sha256'}
    if digest(body)!=record.get('record_sha256') or record['scope']['project_id']!=project['id']:raise ValueError('Intake version record changed')
    source=_owned(project,Path(record['source_dataset_path']))
    for row in [*record['base_files'],*record['adopted']]:
        if dm._hash(source/row['relative_path'])!=row.get('sha256',row.get('source_sha256')):raise ValueError('Intake version source bytes changed')
    return record


def list_versions(project):
    rows=[]
    for identifier in _index(project)['versions']:
        try:rows.append(read_version(project,identifier))
        except (ValueError,OSError):continue
    return {'versions':sorted(rows,key=lambda row:row['created_at'],reverse=True),'total':len(rows)}
