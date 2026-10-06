"""Read-only bridge from an owned manual candidate cycle to existing review.

This view never approves a model, changes truth, resumes training or applies a
service. Current approval and package/device execution are separate authorities.
"""
import json
from pathlib import Path
import re
from fastapi import HTTPException
from backend.engine.image_truth import digest

CORE_TASKS={'classification','patch_classification','detection','segmentation','anomaly'}


def freeze_subject(project,policy,candidate,evidence):
    if policy['task'] not in CORE_TASKS:return None
    from backend.api import routes_model_deployments as approval
    report=evidence.get('comparison') or {};identifier=report.get('comparison_id')
    if not isinstance(identifier,str) or not re.fullmatch('comparison_[0-9a-f]{32}',identifier):
        raise ValueError('Manual review requires a persisted comparison')
    path=approval._report_dir(project)/(identifier+'.json')
    if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file():
        raise ValueError('Manual comparison is unavailable or linked')
    saved=json.loads(path.read_text(encoding='utf-8'))
    model=approval._model(project,Path(project['source_dataset_dir']),policy['task'],candidate)
    if (saved!=report or saved.get('project_id')!=project['id']
            or saved.get('source_dataset_path')!=project['source_dataset_dir']
            or saved.get('task')!=policy['task'] or saved.get('status')!='completed'
            or saved.get('candidate_job_id')!=candidate
            or saved.get('incumbent_job_id')!=policy['parent_job_id'] or model is None):
        raise ValueError('Manual comparison does not bind the completed candidate and current source')
    checkpoint_sha=approval._sha256(Path(model['checkpoint_path']))
    if saved.get('model_sha256',{}).get('candidate')!=checkpoint_sha:
        raise ValueError('Manual candidate checkpoint changed')
    record={'contract':'manual_operations_review_v1','project_id':project['id'],
        'source_dataset_path':project['source_dataset_dir'],'labelset_id':policy['labelset_id'],
        'task':policy['task'],'candidate_job_id':candidate,'candidate_checkpoint_sha256':checkpoint_sha,
        'comparison_id':identifier,'comparison_sha256':approval._sha256(path)}
    record['subject_sha256']=digest(record);return record


def read_handoff(project,cycle_id):
    from backend.engine.model_operations import OperationsStore
    from backend.api import routes_model_deployments as approval
    if not re.fullmatch('cycle_[0-9a-f]{32}',cycle_id):raise ValueError('Invalid operations cycle')
    cycle=OperationsStore(project['project_dir']).get(cycle_id)
    subject=cycle.get('result',{}).get('review_handoff')
    result={'cycle_id':cycle_id,'state':'unbound_history','subject':subject,'reasons':[],
        'next_step':None,'automatic_action':'none','service_applied':False,'device_accepted':False,
        'approval_revision_id':None}
    if not subject:
        result['reasons']=['This cycle has no frozen core-model manual review subject; inspect its original evidence']
        return result
    try:
        if not isinstance(subject,dict):
            result['subject']=None
            raise ValueError('Manual review subject is malformed')
        if (subject.get('contract')!='manual_operations_review_v1'
                or subject.get('subject_sha256')!=digest({k:v for k,v in subject.items() if k!='subject_sha256'})
                or subject['project_id']!=project['id'] or subject['source_dataset_path']!=project['source_dataset_dir']
                or subject['labelset_id']!=project.get('active_labelset_id','default') or subject['task'] not in CORE_TASKS):
            raise ValueError('Manual review source, labelset or subject changed')
        source=Path(project['source_dataset_dir']);identifier=subject['comparison_id']
        if not re.fullmatch('comparison_[0-9a-f]{32}',identifier):raise ValueError('Manual comparison identity changed')
        report=approval._report_dir(project)/(identifier+'.json')
        if any(p.is_symlink() for p in (report,*report.parents)) or approval._sha256(report)!=subject['comparison_sha256']:
            raise ValueError('Manual comparison bytes changed')
        model=approval._model(project,source,subject['task'],subject['candidate_job_id'])
        if model is None or approval._sha256(Path(model['checkpoint_path']))!=subject['candidate_checkpoint_sha256']:
            raise ValueError('Manual candidate checkpoint changed')
        with approval._store(project) as connection:active=approval._active(connection,source,subject['task'])
        if (active and active['job_id']==subject['candidate_job_id'] and active['comparison_id']==identifier
                and active['comparison_sha256']==subject['comparison_sha256']
                and approval.verified_approval_revision(project,active['revision_id'],expected_task=subject['task'])):
            result.update(state='model_approval_current',next_step=6,approval_revision_id=active['revision_id'])
        else:
            assessment=approval._assess(project,source,subject['task'],identifier,active)
            if assessment['status']!='ready':raise ValueError('; '.join(assessment['reasons']))
            result.update(state='awaiting_human_review',next_step=4)
    except (ValueError,OSError,KeyError,TypeError,HTTPException) as exc:
        result.update(state='revalidation_required',reasons=[str(exc.detail if isinstance(exc,HTTPException) else exc)])
    return result
