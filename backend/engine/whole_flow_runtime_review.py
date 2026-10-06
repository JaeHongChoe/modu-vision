"""Explicit converted whole-flow review against the original current truth.

Precision drift acceptance alone grants no graph-quality authority. This module
reassesses actual converted outputs under the base reviewed process policy and
retains a separate immutable human review. It never alters a package or device.
"""
import json
from pathlib import Path
import re
import uuid

from backend.engine import whole_flow_approval as base
from backend.engine.flow_workspace import atomic_json
from backend.engine.image_truth import digest
from backend.engine.dataset_metadata import _now


def _read(path):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file():
        raise ValueError('Converted full-flow evidence must be an unlinked regular file')
    with path.open('rb') as handle:raw=handle.read(2*1024**2+1)
    if len(raw)>2*1024**2:raise ValueError('Converted full-flow evidence is oversized')
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('Duplicate converted full-flow evidence key')
            value[key]=item
        return value
    value=json.loads(raw,object_pairs_hook=unique)
    if not isinstance(value,dict):raise ValueError('Converted full-flow evidence must be an object')
    return value


def _root(project):
    root=base._root(project)/'runtime_reviews'
    if root.is_symlink():raise ValueError('Runtime review storage cannot be linked')
    root.mkdir(exist_ok=True)
    return root


def _subject(project,package,revision,device,accounts):
    from backend.engine.flow_package_runtime import verify_flow_package
    from backend.engine.flow_provenance import pipeline_sha256
    from backend.engine.release_eligibility import authorize_release_action
    from backend.engine.runtime_release_evidence import verify_release_evidence,verify_measured_precision_evidence,_digest
    package=Path(package).absolute()
    if any(p.is_symlink() for p in (package,*package.parents)):raise ValueError('Runtime review package cannot be linked')
    reviewed=base.verified_approval(project,revision,accounts=accounts)
    evaluation=base.flow_evaluation.read_evaluation(project,reviewed['evaluation_id'])
    graph,checkpoints=verify_flow_package(package)
    if (pipeline_sha256(graph)!=reviewed['graph_sha256'] or
            {job:_digest(path) for job,path in checkpoints.items()}!={r['job_id']:r['checkpoint_sha256'] for r in evaluation['models']}):
        raise ValueError('Converted package differs from the reviewed graph or checkpoints')
    approvals=authorize_release_action(package,project,action='stage')
    evidence=verify_release_evidence(package,device)
    if evidence['receipt_kind']!='measured_precision_cohort':
        raise ValueError('Separate runtime review requires a measured precision release')
    acceptance=_read(package/'runtime_acceptance.json')
    if acceptance.get('input_receipt',{}).get('source_dataset_path')!=project['source_dataset_dir']:
        raise ValueError('Converted input source differs from the reviewed project')
    measured=verify_measured_precision_evidence(package,device,acceptance=acceptance,check_source=True)
    rows={r['input_sha256']:r for r in evaluation['records']}
    images=measured['images']
    if (len(images)!=len(rows) or len({r['sha256'] for r in images})!=len(rows)
            or {r['sha256'] for r in images}!=set(rows)):
        raise ValueError('Converted heldout cohort differs from the reviewed full-flow cohort')
    confusion={truth:{decision:0 for decision in ('OK','NG','REVIEW')} for truth in ('OK','NG')}
    outputs=[]
    for index,image in enumerate(images):
        original=rows[image['sha256']]
        if image['relative_path']!=original['relative_path']:
            raise ValueError('Converted heldout original input path differs')
        actual=_read(package/'heldout'/f'heldout_{index:04d}.json')
        if actual.get('image_sha256')!=image['sha256'] or actual.get('reference',{}).get('final_verdict')!=original['decision']:
            raise ValueError('Converted reference decision differs from the reviewed full flow')
        truth=original['truth'];decision=actual.get('candidate',{}).get('final_verdict')
        if truth not in confusion or decision not in confusion[truth]:
            raise ValueError('Converted flow lacks explicit current truth or a complete decision')
        confusion[truth][decision]+=1
        outputs.append({'relative_path':original['relative_path'],'image_sha256':image['sha256'],'truth_sha256':original['truth_sha256'],
            'truth':truth,'decision':decision,'output_sha256':_digest(package/'heldout'/f'heldout_{index:04d}.json')})
    normal=sum(confusion['OK'].values());defect=sum(confusion['NG'].values())
    policy=reviewed['policy']
    if normal<policy['minimum_normal'] or defect<policy['minimum_defect']:
        raise ValueError('Converted normal/defect counts do not meet the reviewed process policy')
    metrics={'escape_rate':confusion['NG']['OK']/defect,'overkill_rate':confusion['OK']['NG']/normal,
        'review_rate':sum(r['REVIEW'] for r in confusion.values())/len(images),
        'normal_count':normal,'defect_count':defect,'confusion':confusion}
    for metric,bound in [('escape_rate','maximum_escape_rate'),('overkill_rate','maximum_overkill_rate'),('review_rate','maximum_review_rate')]:
        if metrics[metric]>policy[bound]:raise ValueError('Converted whole-flow '+metric+' exceeds the reviewed process policy')
    return {'base_revision_id':revision,'approval_sha256':reviewed['record_sha256'],
        'evaluation_sha256':reviewed['evaluation_sha256'],'truth_sha256':reviewed['truth_sha256'],
        'graph_sha256':reviewed['graph_sha256'],'cohort_sha256':reviewed['cohort_sha256'],
        'policy':policy,'policy_sha256':digest(policy),'manifest_sha256':evidence['manifest_sha256'],
        'runtime_acceptance_sha256':evidence['receipt_sha256'],'runtime_cohort_sha256':measured['cohort_sha256'],
        'device':device,'model_approval_revisions':approvals,'metrics':metrics,'outputs':outputs}


def _selection_path(project,subject):
    key={k:subject[k] for k in ('base_revision_id','manifest_sha256','device')}
    return _root(project)/('active_'+digest(key)+'.json')


def _selected(project,subject):
    path=_selection_path(project,subject)
    if not path.exists() and not path.is_symlink():return None
    active=base._read(path)
    identifier=active.get('revision_id')
    if not isinstance(identifier,str) or not re.fullmatch('runtimeflow_[0-9a-f]{32}',identifier):
        raise ValueError('Invalid selected runtime review revision')
    record=base._read(_root(project)/(identifier+'.json'))
    if (record.get('revision_id')!=identifier or record.get('schema_version')!=1
            or record.get('contract')!='whole_flow_runtime_review_v1' or record.get('project_id')!=project['id']
            or record.get('holdout_reviewed') is not True or record.get('device_accepted') is not False
            or active.get('approval_sha256')!=record['record_sha256']):
        raise ValueError('Selected runtime review belongs to another subject')
    return record


def approve_runtime_flow(project,package,revision_id,*,device,reviewer,reason,holdout_reviewed,
                         expected_revision,authority_user_id=None,accounts=None):
    base._reviewer(reviewer,reason)
    if holdout_reviewed is not True:raise ValueError('Explicit converted full-flow heldout review is required')
    with base._locked(project):
        base._authority(project,authority_user_id,accounts)
        subject=_subject(project,package,revision_id,device,accounts)
        previous=_selected(project,subject)
        if (previous['revision_id'] if previous else None)!=expected_revision:
            raise ValueError('Runtime review selection changed; reopen before reviewing')
        record={'schema_version':1,'contract':'whole_flow_runtime_review_v1','project_id':project['id'],
            'revision_id':'runtimeflow_'+uuid.uuid4().hex,'subject':subject,'reviewer':reviewer.strip(),
            'reason':reason.strip(),'holdout_reviewed':True,'device_accepted':False,
            'authority_user_id':authority_user_id,'created_at':_now()}
        record['record_sha256']=digest(record)
        atomic_json(_root(project)/(record['revision_id']+'.json'),record)
        if _subject(project,package,revision_id,device,accounts)!=subject:
            raise ValueError('Converted full-flow subject changed while reviewing')
        base._authority(project,authority_user_id,accounts)
        selected={'revision_id':record['revision_id'],'approval_sha256':record['record_sha256']}
        selected['record_sha256']=digest(selected)
        atomic_json(_selection_path(project,subject),selected)
        return {**record,'validity':{'valid':True,'reasons':[]}}


def verified_runtime_review(project,package,revision_id,*,device,accounts=None):
    subject=_subject(project,package,revision_id,device,accounts)
    record=_selected(project,subject)
    if record is None or record['subject']!=subject:
        raise ValueError('A separate full-flow runtime review is missing or stale')
    base._authority(project,record.get('authority_user_id'),accounts)
    return {'contract':'whole_flow_runtime_review_v1','revision_id':revision_id,
        **{k:subject[k] for k in ('approval_sha256','graph_sha256','evaluation_sha256','policy_sha256',
            'cohort_sha256','manifest_sha256','runtime_acceptance_sha256','device','model_approval_revisions')},
        'runtime_review_revision_id':record['revision_id'],'runtime_review_sha256':record['record_sha256'],
        'candidate_metrics_sha256':digest(subject['metrics']),
        'runtime_cohort_qualified':True,'device_accepted':False}


def preview_runtime_flow(project,package,revision_id,*,device,accounts=None):
    with base._locked(project):
        subject=_subject(project,package,revision_id,device,accounts)
        selected=_selected(project,subject)
        valid=bool(selected and selected['subject']==subject)
        if valid:
            try:base._authority(project,selected.get('authority_user_id'),accounts)
            except ValueError:valid=False
        return {**subject,'review_revision_id':selected['revision_id'] if selected else None,
            'review_valid':valid,'device_accepted':False}
