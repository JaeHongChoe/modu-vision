"""Bridge from an owned manual candidate cycle to existing explicit review.

Readback and inactive candidate preparation never approve a model/graph, change
truth, resume training or apply a service. Those are separate authorities.
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


def read_handoff(project,cycle_id,*,accounts=None):
    from backend.engine.model_operations import OperationsStore
    from backend.api import routes_model_deployments as approval
    if not re.fullmatch('cycle_[0-9a-f]{32}',cycle_id):raise ValueError('Invalid operations cycle')
    cycle=OperationsStore(project['project_dir']).get(cycle_id)
    subject=cycle.get('result',{}).get('review_handoff')
    result={'cycle_id':cycle_id,'state':'unbound_history','subject':subject,'reasons':[],
        'next_step':None,'automatic_action':'none','service_applied':False,'device_accepted':False,
        'approval_revision_id':None,'prepared_flow':None,'delivery':None}
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
        prepared=cycle.get('result',{}).get('prepared_flow')
        if prepared is not None:
            if result['state']!='model_approval_current':raise ValueError('Prepared candidate graph requires current model approval')
            _verify_prepared(project,subject,prepared,result['approval_revision_id'])
            result.update(prepared_flow=prepared,next_step=5)
            from backend.engine.operations_delivery import current_delivery
            result['delivery']=current_delivery(project,prepared,accounts=accounts)
            if result['delivery']['whole_flow_current']:result['next_step']=6
    except (ValueError,OSError,KeyError,TypeError,HTTPException) as exc:
        result.update(state='revalidation_required',reasons=[str(exc.detail if isinstance(exc,HTTPException) else exc)])
    return result


def _version_path(project,identifier):
    from backend.api.routes_flowchart import _version_dir
    if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier):
        raise ValueError('Choose an immutable saved source flow version')
    path=_version_dir(Path(project['project_dir']))/(identifier+'.json')
    if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file():
        raise ValueError('Candidate/source flow version is missing or linked')
    return path


def _verify_prepared(project,subject,prepared,approval_revision):
    from backend.api.routes_model_deployments import _sha256
    from backend.engine.flowchart_engine import FlowchartPipeline
    from backend.engine.flow_provenance import pipeline_sha256
    if (not isinstance(prepared,dict) or prepared.get('contract')!='operations_candidate_flow_v1'
            or prepared.get('subject_sha256')!=subject['subject_sha256']
            or prepared.get('candidate_checkpoint_sha256')!=subject['candidate_checkpoint_sha256']
            or prepared.get('candidate_job_id')!=subject['candidate_job_id']
            or prepared.get('approval_revision_id')!=approval_revision
            or prepared.get('flow_activated') is not False or prepared.get('service_applied') is not False
            or prepared.get('receipt_sha256')!=digest({k:v for k,v in prepared.items() if k!='receipt_sha256'})):
        raise ValueError('Prepared candidate graph authority or receipt changed')
    source=_version_path(project,prepared['source_version_id'])
    version=_version_path(project,prepared['version_id'])
    if _sha256(source)!=prepared['source_version_sha256'] or _sha256(version)!=prepared['version_sha256']:
        raise ValueError('Prepared candidate graph or original saved version bytes changed')
    record=json.loads(version.read_bytes());pipeline=FlowchartPipeline.model_validate(record['pipeline'])
    if (record.get('source_dataset_path')!=project['source_dataset_dir']
            or pipeline_sha256(pipeline)!=prepared['graph_sha256']):
        raise ValueError('Prepared candidate graph source/content changed')


def prepare_candidate_flow(project,cycle_id,*,source_version_id,expected_graph_sha256,
                           expected_subject_sha256,reviewer,reason):
    """Prepare a separate saved version; the active recipe/service stay intact."""
    from types import SimpleNamespace
    import time
    from backend.engine.model_operations import OperationsStore,_cycle_lock,project_scope,_holdout,_checkpoint,_sha
    from backend.api.routes_flowchart import _FLOW_SAVE_LOCK,_save_version,get_saved_pipeline_version,_save_class_model_metadata
    from backend.engine.flow_class_validation import validate_recorded_flow_classes
    from backend.engine.flow_provenance import pipeline_sha256
    from backend.engine.specialized_models import flow_model_task
    if not isinstance(reason,str) or not 10<=len(reason.strip())<=2000:
        raise ValueError('Enter an explicit candidate graph preparation reason of 10–2000 characters')
    if not isinstance(reviewer,str) or not 1<=len(reviewer.strip())<=100:
        raise ValueError('Candidate graph preparation requires an explicit actor')
    with _cycle_lock(project),project_scope(project),_FLOW_SAVE_LOCK:
        store=OperationsStore(project['project_dir']);cycle=store.get(cycle_id);policy=store.policy()
        handoff=read_handoff(project,cycle_id);subject=handoff.get('subject')
        if (handoff['state']!='model_approval_current' or not subject
                or subject['subject_sha256']!=expected_subject_sha256):
            raise ValueError('Candidate comparison/model approval changed; reopen its current review')
        if (not policy or policy['revision']!=cycle.get('policy_revision')
                or policy['task']!=subject['task'] or policy['labelset_id']!=subject['labelset_id']
                or policy['holdout']!=_holdout(project)
                or _sha(_checkpoint(project,policy['task'],policy['parent_job_id']))!=policy['parent_checkpoint_sha256']):
            raise ValueError('Operations policy, parent or frozen holdout changed')
        if handoff['prepared_flow']:
            previous=handoff['prepared_flow']
            if previous['source_version_id']!=source_version_id or previous['source_graph_sha256']!=expected_graph_sha256:
                raise ValueError('This cycle already has a different independently prepared flow')
            return previous
        path=_version_path(project,source_version_id);source_sha=_sha(path)
        source_record=json.loads(path.read_bytes())
        request=SimpleNamespace(state=SimpleNamespace(scoped_project=project,account_user=None))
        pipeline=get_saved_pipeline_version(source_version_id,request=request)
        if pipeline_sha256(pipeline)!=expected_graph_sha256:
            raise ValueError('Selected source graph changed since it was shown')
        replaced=0
        for node in pipeline.nodes:
            if flow_model_task(node)==subject['task'] and node.data.model_job_id==policy['parent_job_id']:
                node.data.model_job_id=subject['candidate_job_id'];replaced+=1
        if not replaced:raise ValueError('Selected source graph does not contain this cycle incumbent model')
        validate_recorded_flow_classes(pipeline,lambda node:_save_class_model_metadata(node,project,project['source_dataset_dir']))
        version_id=None
        try:
            version_id=_save_version(pipeline,source_record['recipe_task'],project['source_dataset_dir'],Path(project['project_dir']))
            # Recheck the live model authority, source and held-out identities
            # after the independent version write, before recording it as ready.
            fresh=read_handoff(project,cycle_id)
            if (fresh['state']!='model_approval_current' or fresh['subject']!=subject
                    or fresh['approval_revision_id']!=handoff['approval_revision_id']
                    or _sha(path)!=source_sha or _holdout(project)!=policy['holdout']):
                raise ValueError('Candidate graph authority/source/holdout changed during preparation')
            prepared={'contract':'operations_candidate_flow_v1','version_id':version_id,
                'version_sha256':_sha(_version_path(project,version_id)),'graph_sha256':pipeline_sha256(pipeline),
                'source_version_id':source_version_id,'source_version_sha256':source_sha,'source_graph_sha256':expected_graph_sha256,
                'subject_sha256':subject['subject_sha256'],'candidate_job_id':subject['candidate_job_id'],
                'candidate_checkpoint_sha256':subject['candidate_checkpoint_sha256'],
                'approval_revision_id':handoff['approval_revision_id'],'replaced_nodes':replaced,
                'actor':reviewer.strip(),'reason':reason.strip(),'prepared_at':time.time(),
                'flow_activated':False,'service_applied':False}
            prepared['receipt_sha256']=digest(prepared)
            cycle.setdefault('result',{})['prepared_flow']=prepared
            cycle.update(status='awaiting_flow_review',updated_at=time.time())
            cycle.setdefault('events',[]).append({'phase':'candidate_flow_prepared','at':time.time(),
                'version_id':version_id,'receipt_sha256':prepared['receipt_sha256'],'actor':reviewer.strip()})
            store.save(cycle)
            return prepared
        except Exception:
            if version_id is not None:_version_path(project,version_id).unlink()
            raise
