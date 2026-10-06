"""Reviewer selection of a complete evaluated graph under an explicit policy.

Model approval, this graph review, package parity and device acceptance are
separate records. No prediction, UI click or graph activation supplies truth.
"""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import re
import uuid

from backend.engine import flow_evaluation
from backend.engine.dataset_metadata import metadata_transaction, _now
from backend.engine.flow_workspace import atomic_json
from backend.engine.image_truth import digest
from backend.engine.release_eligibility import verify_project_context, evidence_context
from backend.engine.runtime_process_control import runtime_state_lock

POLICY_FIELDS = {'policy_id','revision','minimum_normal','minimum_defect',
                 'maximum_escape_rate','maximum_overkill_rate','maximum_review_rate'}


def validate_policy(policy):
    if (not isinstance(policy,dict) or set(policy)!=POLICY_FIELDS
            or not isinstance(policy['policy_id'],str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',policy['policy_id'])
            or type(policy['revision']) is not int or policy['revision']<1):
        raise ValueError('An explicit complete process policy revision is required')
    for key in ('minimum_normal','minimum_defect'):
        if type(policy[key]) is not int or not 1<=policy[key]<=5000:
            raise ValueError('Policy needs explicit positive normal and defect sample counts')
    for key in POLICY_FIELDS-{'policy_id','revision','minimum_normal','minimum_defect'}:
        if type(policy[key]) not in (int,float) or not math.isfinite(policy[key]) or not 0<=policy[key]<=1:
            raise ValueError('Policy rates must be finite values between zero and one')
    return dict(policy)


def _root(project):
    root=Path(project['project_dir']).absolute()/'flow_approvals'
    if any(p.is_symlink() for p in (root,*root.parents)):
        raise ValueError('Whole-flow approval storage cannot be linked')
    root.mkdir(parents=True,exist_ok=True)
    return root


def _read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:
        raise ValueError('Whole-flow approval record is unavailable')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or digest({k:v for k,v in value.items() if k!='record_sha256'})!=value.get('record_sha256'):
        raise ValueError('Whole-flow approval record changed')
    return value


def _active(project):
    file=_root(project)/'active.json'
    return _read(file) if file.exists() or file.is_symlink() else None


def _record(project,revision):
    if not isinstance(revision,str) or not re.fullmatch(r'flowapproval_[0-9a-f]{32}',revision):
        raise ValueError('Invalid whole-flow approval revision')
    value=_read(_root(project)/(revision+'.json'))
    if (value.get('revision_id')!=revision or value.get('project_id')!=project['id']
            or value.get('schema_version')!=1 or value.get('contract')!='whole_flow_review_v1'
            or value.get('holdout_reviewed') is not True or value.get('package_qualified') is not False
            or value.get('device_accepted') is not False):
        raise ValueError('Whole-flow approval belongs to another subject')
    return value


def _policy_revision(project,policy):
    files=list(_root(project).glob('flowapproval_*.json'))
    if len(files)>1000:raise ValueError('Review history exceeds the bounded policy scan; archive through a reviewed migration')
    for path in files:
        previous=_record(project,path.stem)['policy']
        if (previous['policy_id'],previous['revision'])==(policy['policy_id'],policy['revision']) and previous!=policy:
            raise ValueError('Policy revision already names other thresholds; use a new revision')


def _authority(project,user_id,accounts):
    if user_id is not None and (accounts is None or accounts.project_role(user_id,project['id']) not in {'owner','reviewer'}):
        raise ValueError('Whole-flow review authority is unavailable or revoked')


def assess_flow(project,evaluation_id,policy):
    policy=validate_policy(policy)
    evaluation=flow_evaluation.read_evaluation(project,evaluation_id)
    if not evaluation['validity']['valid']:
        raise ValueError('Whole-flow evaluation is stale or changed; evaluate again')
    if evaluation['status']!='completed' or evaluation['errors']:
        raise ValueError('Whole-flow approval requires complete execution without errors')
    coverage=evaluation['coverage'];metrics=evaluation['metrics']
    if (coverage['unknown'] or coverage['invalidated'] or evaluation['unknown_truth']
            or coverage['total']!=coverage['known'] or coverage['total']!=len(evaluation['records'])):
        raise ValueError('Whole-flow approval requires explicit current truth for every input')
    if len({row['input_sha256'] for row in evaluation['records']})!=coverage['total']:
        raise ValueError('Duplicate input content cannot inflate the reviewed sample count')
    if (metrics['normal_count']<policy['minimum_normal'] or metrics['defect_count']<policy['minimum_defect']):
        raise ValueError('Heldout normal/defect counts do not meet the explicit process policy')
    for metric,bound in (('escape_rate','maximum_escape_rate'),('overkill_rate','maximum_overkill_rate'),('review_rate','maximum_review_rate')):
        rate=metrics[metric]
        if type(rate) not in (int,float) or not math.isfinite(rate) or not 0<=rate<=policy[bound]:
            raise ValueError('Whole-flow '+metric+' exceeds the reviewed policy or is unavailable')
    return evaluation


@contextmanager
def _locked(project):
    verify_project_context(project)
    with evidence_context(project), metadata_transaction(project['project_dir'],project['source_dataset_dir'],project['annotations_dir']), runtime_state_lock(project['project_dir']):
        verify_project_context(project)
        yield


def _reviewer(reviewer,reason):
    if (not isinstance(reviewer,str) or not 1<=len(reviewer.strip())<=100
            or not isinstance(reason,str) or not 8<=len(reason.strip())<=2000):
        raise ValueError('Explicit reviewer and meaningful review reason are required')


def _cas(project,expected):
    active=_active(project)
    if (active['revision_id'] if active else None)!=expected:
        raise ValueError('Whole-flow approval selection changed; reopen before selecting')
    return active


def _select(project,record,reviewer,reason):
    event={'schema_version':1,'event_id':uuid.uuid4().hex,'project_id':project['id'],
           'revision_id':record['revision_id'],'approval_sha256':record['record_sha256'],
           'reviewer':reviewer.strip(),'reason':reason.strip(),'selected_at':_now(),
           'prior_revision_id':(_active(project) or {}).get('revision_id')}
    event['record_sha256']=digest(event)
    # The durable active index is the commit point. The immutable approval itself
    # remains unchanged when a prior revision is selected or later invalidated.
    atomic_json(_root(project)/'active.json',event)


def approve_flow(project,*,evaluation_id,policy,reviewer,reason,holdout_reviewed,expected_revision,
                 authority_user_id=None,accounts=None):
    _reviewer(reviewer,reason)
    if holdout_reviewed is not True:raise ValueError('Explicit human heldout review confirmation is required')
    with _locked(project):
        _cas(project,expected_revision);_authority(project,authority_user_id,accounts)
        evaluation=assess_flow(project,evaluation_id,policy)
        _policy_revision(project,policy)
        record={'schema_version':1,'contract':'whole_flow_review_v1','revision_id':'flowapproval_'+uuid.uuid4().hex,
                'project_id':project['id'],'source_dataset_path':project['source_dataset_dir'],
                'labelset_id':project.get('active_labelset_id','default'),'evaluation_id':evaluation_id,
                'evaluation_sha256':evaluation['record_sha256'],'version_id':evaluation['version_id'],
                'graph_sha256':evaluation['graph_sha256'],'cohort_sha256':evaluation['cohort_sha256'],
                'truth_sha256':evaluation['truth_sha256'],'model_sha256':evaluation['model_sha256'],
                'policy':validate_policy(policy),'reviewer':reviewer.strip(),'reason':reason.strip(),
                'authority_user_id':authority_user_id,'created_at':_now(),'holdout_reviewed':True,
                'package_qualified':False,'device_accepted':False}
        record['record_sha256']=digest(record)
        path=_root(project)/(record['revision_id']+'.json');atomic_json(path,record)
        # Supported truth writers are fenced; external changes still require a
        # final revalidation before the selected approval can become visible.
        if assess_flow(project,evaluation_id,policy)['record_sha256']!=record['evaluation_sha256']:
            raise ValueError('Whole-flow evaluation changed while reviewing')
        _authority(project,authority_user_id,accounts);verify_project_context(project)
        _select(project,record,reviewer,reason)
        return {**record,'record_path':str(path),'validity':{'valid':True,'reasons':[]}}


def verified_approval(project,revision_id,*,accounts=None):
    verify_project_context(project);record=_record(project,revision_id)
    _authority(project,record.get('authority_user_id'),accounts)
    if (record['source_dataset_path']!=project['source_dataset_dir']
            or record['labelset_id']!=project.get('active_labelset_id','default')):
        raise ValueError('Whole-flow approval source or labelset changed')
    evaluation=assess_flow(project,record['evaluation_id'],record['policy'])
    for saved,current in (('evaluation_sha256','record_sha256'),('graph_sha256','graph_sha256'),
                          ('cohort_sha256','cohort_sha256'),('truth_sha256','truth_sha256'),('model_sha256','model_sha256')):
        if record[saved]!=evaluation[current]:raise ValueError('Whole-flow approved subject changed')
    return record


def current_approval(project,*,accounts=None):
    active=_active(project)
    if active is None:return None
    record=_record(project,active['revision_id'])
    if active['approval_sha256']!=record['record_sha256']:raise ValueError('Whole-flow selection changed')
    try:verified_approval(project,record['revision_id'],accounts=accounts);reasons=[]
    except (ValueError,OSError,KeyError) as exc:reasons=[str(exc)]
    return {**record,'validity':{'valid':not reasons,'reasons':reasons}}


def select_approval(project,revision_id,*,expected_revision,reviewer,reason,accounts=None):
    _reviewer(reviewer,reason)
    with _locked(project):
        _cas(project,expected_revision)
        record=verified_approval(project,revision_id,accounts=accounts)
        _select(project,record,reviewer,reason)
        return {**record,'validity':{'valid':True,'reasons':[]}}


def qualify_package(project,package,revision_id,*,device,accounts=None):
    """Bind graph review to current model approvals and exact cohort parity.

    Produces review metadata for a separately trusted release policy. It does
    not publish, activate a service, alter package bytes or accept field devices.
    """
    import hashlib
    from backend.engine.runtime_release_evidence import _digest
    from backend.engine.flow_package_runtime import verify_flow_package
    from backend.engine.flow_provenance import pipeline_sha256
    from backend.engine.release_eligibility import authorize_release_action
    from backend.engine.runtime_release_evidence import verify_release_evidence
    with _locked(project):
        record=verified_approval(project,revision_id,accounts=accounts)
        package=Path(package).absolute()
        if any(p.is_symlink() for p in (package,*package.parents)):
            raise ValueError('Whole-flow package cannot be linked')
        graph,checkpoints=verify_flow_package(package)
        if pipeline_sha256(graph)!=record['graph_sha256']:
            raise ValueError('Package graph differs from the reviewed full graph')
        evaluation=flow_evaluation.read_evaluation(project,record['evaluation_id'])
        expected={r['job_id']:r['checkpoint_sha256'] for r in evaluation['models']}
        if ({job:_digest(path) for job,path in checkpoints.items()}!=expected):
            raise ValueError('Package checkpoints differ from the reviewed full graph')
        model_approvals=authorize_release_action(package,project,action='stage')
        evidence=verify_release_evidence(package,device)
        if evidence['receipt_kind']=='measured_precision_cohort':
            from backend.engine.whole_flow_runtime_review import verified_runtime_review
            qualified=verified_runtime_review(project,package,revision_id,device=device,accounts=accounts)
            verified_approval(project,revision_id,accounts=accounts)
            if verify_release_evidence(package,device,expected_receipt_sha256=evidence['receipt_sha256'])!=evidence:
                raise ValueError('Package changed during converted whole-flow qualification')
            return qualified
        if evidence['receipt_kind']!='flow_parity':raise ValueError('Unknown whole-flow runtime evidence')
        parity_file=package/'parity_receipt.json'
        if parity_file.stat().st_size>2*1024*1024:raise ValueError('Package parity receipt is oversized')
        raw=parity_file.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=evidence['receipt_sha256']:
            raise ValueError('Package parity changed during whole-flow qualification')
        parity=json.loads(raw)
        if sorted(r['image_sha256'] for r in parity['images'])!=sorted(r['input_sha256'] for r in evaluation['records']):
            raise ValueError('Package parity inputs differ from the reviewed heldout cohort')
        verified_approval(project,revision_id,accounts=accounts)
        authorize_release_action(package,project,action='stage')
        if verify_release_evidence(package,device,expected_receipt_sha256=evidence['receipt_sha256'])!=evidence:
            raise ValueError('Package changed during whole-flow qualification')
        return {'contract':'whole_flow_review_v1','revision_id':record['revision_id'],
                'approval_sha256':record['record_sha256'],'graph_sha256':record['graph_sha256'],
                'evaluation_sha256':record['evaluation_sha256'],'policy_sha256':digest(record['policy']),
                'cohort_sha256':record['cohort_sha256'],'manifest_sha256':evidence['manifest_sha256'],
                'parity_receipt_sha256':evidence['receipt_sha256'],'device':device,
                'model_approval_revisions':model_approvals,'runtime_cohort_qualified':True,
                'device_accepted':False}
