"""Automated model qualification from immutable evaluation and configured policy.

These are policy decisions, not reviewer attestations. Runtime/package/device
acceptance remains separately measured and is never created by this module.
"""
import json
import math
from pathlib import Path
import uuid
from fastapi import HTTPException

from backend.engine.image_truth import digest

CONTRACT = 'model_policy_qualification_v1'
CLASSIC = {'classification','patch_classification','detection','segmentation','anomaly'}


class QualificationInputNeeded(ValueError):
    pass


def policy_subject(policy):
    keys=('revision','project_id','source_dataset_path','labelset_id','task','parent_job_id',
          'parent_checkpoint_sha256','holdout','minimum_sample_count','minimum_metrics','maximum_metrics',
          'approval_policy_authorized','auto_approve','flow_policy')
    return {key:policy.get(key) for key in keys}


def configured_policy(project, supplied=None):
    from backend.engine.model_operations import OperationsStore
    current=OperationsStore(project['project_dir']).policy()
    if not current or current.get('auto_approve') is not True or current.get('approval_policy_authorized') is not True:
        raise QualificationInputNeeded('Configure an explicitly authorized quality policy before qualification')
    if supplied is not None and policy_subject(current)!=policy_subject(supplied):
        raise ValueError('Configured quality policy revision changed')
    if (current.get('project_id')!=project['id'] or current.get('source_dataset_path')!=project['source_dataset_dir']
            or current.get('labelset_id')!=project.get('active_labelset_id','default')
            or type(current.get('revision')) is not int or current['revision']<1):
        raise ValueError('Quality policy source, project or labelset changed')
    return current


def _comparison_for_evaluation(project,policy,evaluation_id):
    """Read the latest exact persisted comparison; never run inference or choose by whether it passes."""
    from backend.api import routes_model_deployments as routes
    from backend.engine.evaluation_history import EvaluationHistory
    evaluation=EvaluationHistory(Path(project['reports_dir'])/'evaluations').get(evaluation_id)
    result,binding=evaluation['result'],evaluation['binding']
    expected={row.get('file_path') for row in result.get('test_predictions',[])}
    files=list(routes._report_dir(project).glob('comparison_*.json'))
    if len(files)>1000:raise QualificationInputNeeded('Comparison history exceeds the bounded policy lookup')
    for file in sorted(files,key=lambda path:(path.stat().st_mtime_ns,path.name),reverse=True):
        try:report,_=routes._read_report(project,Path(project['source_dataset_dir']),policy['task'],file.stem)
        except (HTTPException,ValueError,OSError):continue
        images=report.get('images');hashes=report.get('model_sha256')
        if not isinstance(images,list) or any(not isinstance(row,dict) for row in images) or not isinstance(hashes,dict):continue
        if (report.get('candidate_job_id')==result.get('job_id')
                and hashes.get('candidate')==binding.get('checkpoint_sha256')
                and report.get('dataset_fingerprint')==binding.get('dataset_fingerprint')
                and {row.get('file_path') for row in images}==expected):return file.stem
    raise QualificationInputNeeded('A persisted independent incumbent/candidate comparison is required')


def _assess(project, policy, evaluation_id, comparison_id, incumbent_evaluation_id, baseline):
    from backend.api import routes_model_deployments as routes
    from backend.engine.evaluation_history import EvaluationHistory, verify_specialized_evaluation_inputs
    from backend.engine.model_operations import project_scope, _holdout
    source=Path(project['source_dataset_dir']);task=policy['task']
    with project_scope(project):
        if _holdout(project)!=policy.get('holdout'):
            raise ValueError('Independent holdout images, labels or split changed')
        evidence=EvaluationHistory(Path(project['reports_dir'])/'evaluations').get(evaluation_id)
        result,binding=evidence['result'],evidence['binding']
        if (result.get('task')!=task or binding.get('source_dataset_path')!=str(source)
                or binding.get('dataset_fingerprint')!=routes._fingerprint(source)
                or binding.get('labelset_id','default')!=project.get('active_labelset_id','default')):
            raise ValueError('Evaluation source, task, labels or split changed')
        model=routes._model(project,source,task,result.get('job_id'))
        if model is None or routes._sha256(Path(model['checkpoint_path']))!=binding.get('checkpoint_sha256'):
            raise ValueError('Evaluation model checkpoint changed')
        metadata_path=Path(model['checkpoint_path']).with_name('model_meta.json')
        metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
        thresholds={key:metadata[key] for key in ('optimal_threshold','threshold','probability_threshold','size_threshold','min_defect_area_px') if key in metadata}
        if binding.get('threshold_settings',{})!=thresholds:
            raise ValueError('Evaluation threshold settings changed')
        if task in CLASSIC:
            if not comparison_id:raise QualificationInputNeeded('A persisted independent incumbent/candidate comparison is required')
            assessment=routes._assess(project,source,task,comparison_id,baseline)
            if assessment['status']!='ready':raise ValueError('; '.join(assessment['reasons']))
            report,_=routes._read_report(project,source,task,comparison_id)
            if (report['candidate_job_id']!=result['job_id'] or report['model_sha256']['candidate']!=binding['checkpoint_sha256']):
                raise ValueError('Comparison and evaluation name different model checkpoints')
            rows=result.get('test_predictions',[])
            if ({r.get('file_path') for r in rows}!={r['file_path'] for r in report['images']}
                    or len(rows)!=len(report['images']) or any(r.get('file_path') not in policy['holdout'] for r in rows)):
                raise ValueError('Measured metrics and comparison must use the exact independent test cohort')
            count=len(rows);metrics=result.get('metrics',{})
            if metrics.get('evaluated_split')!='test' or metrics.get('selection_overlap') is not False:
                raise ValueError('Measured model metrics do not establish an independent test split')
            comparison_sha256=assessment['comparison_sha256']
        else:
            if not policy.get('minimum_metrics') and not policy.get('maximum_metrics'):
                raise QualificationInputNeeded('Configure explicit measured quality metric bounds')
            verify_specialized_evaluation_inputs(project,source,task,binding)
            count=result.get('sample_count');metrics=result
            if result.get('split')!='test':raise ValueError('Qualification requires an independent test split')
            if task=='ocr' and (len(result.get('samples',[]))!=count or any(not isinstance(r.get('reference_text'),str) for r in result['samples'])):
                raise ValueError('OCR qualification requires held-out reference text for every sample')
            if task=='enhancement' and result.get('improved') is not True:raise ValueError('Enhancement did not improve the independent holdout')
            comparison_sha256=None
            if baseline and baseline['job_id']!=result['job_id']:
                if not incumbent_evaluation_id:raise QualificationInputNeeded('A persisted current incumbent evaluation is required')
                prior=EvaluationHistory(Path(project['reports_dir'])/'evaluations').get(incumbent_evaluation_id)
                prior_binding,prior_result=prior['binding'],prior['result']
                verify_specialized_evaluation_inputs(project,source,task,prior_binding)
                if (prior_result.get('job_id')!=baseline['job_id'] or prior_result.get('task')!=task
                        or prior_result.get('split')!='test' or prior_result.get('sample_count')!=count
                        or prior_binding.get('source_dataset_path')!=str(source)
                        or prior_binding.get('dataset_fingerprint')!=binding['dataset_fingerprint']
                        or not binding.get('family_dataset_sha256') or prior_binding.get('family_dataset_sha256')!=binding['family_dataset_sha256']
                        or prior_binding.get('checkpoint_sha256')!=baseline['checkpoint_sha256']):
                    raise ValueError('Candidate and incumbent must use the same exact independent test cohort')
                for key in policy['minimum_metrics']:
                    if type(prior_result.get(key)) not in (int,float) or metrics.get(key,float('-inf'))<prior_result[key]:
                        raise ValueError('Candidate regresses against incumbent: '+key)
                for key in policy['maximum_metrics']:
                    if type(prior_result.get(key)) not in (int,float) or metrics.get(key,float('inf'))>prior_result[key]:
                        raise ValueError('Candidate regresses against incumbent: '+key)
        minimum=policy.get('minimum_sample_count')
        if type(minimum) is not int or minimum<1 or type(count) is not int or count<minimum:
            raise ValueError('Independent test count is below the configured policy minimum')
        for limits,direction in ((policy['minimum_metrics'],'minimum'),(policy['maximum_metrics'],'maximum')):
            for key,bound in limits.items():
                value=metrics.get(key)
                if (type(value) not in (int,float) or type(bound) not in (int,float) or not math.isfinite(value) or not math.isfinite(bound)
                        or direction=='minimum' and value<bound or direction=='maximum' and value>bound):
                    raise ValueError('Measured '+key+' fails the configured '+direction)
        path=Path(project['reports_dir'])/'evaluations'/(evaluation_id+'.json')
        return {'evaluation_sha256':routes._sha256(path),'evaluation_binding':binding,
                'job_id':result['job_id'],'checkpoint_sha256':binding['checkpoint_sha256'],
                'model_metadata_sha256':routes._sha256(metadata_path),'threshold_settings':thresholds,'split':'test',
                'cohort_sha256':digest({key:policy['holdout'][key] for key in sorted(policy['holdout'])}),
                'training_dataset_fingerprint':model.get('training_dataset_fingerprint') or 'unverified',
                'evaluation_dataset_fingerprint':binding['dataset_fingerprint'],'sample_count':count,
                'metrics':{key:value for key,value in metrics.items() if type(value) in (int,float) and math.isfinite(value)},
                'comparison_sha256':comparison_sha256,
                'checks':{'independent_holdout':True,'current_source_labels_split':True,'model_checkpoint':True,
                          'configured_metric_bounds':True if policy.get('minimum_metrics') or policy.get('maximum_metrics') else None,
                          'no_regression':True if task in CLASSIC or baseline else None,'explicit_truth':True}}


def _qualify_candidate_policy(project,policy,evaluation_id,*,comparison_id=None,incumbent_evaluation_id=None,
                             authorization=None,activate=False,recheck=None,expected_job_id=None):
    from backend.api import routes_model_deployments as routes
    from backend.engine.release_eligibility import release_authority
    try:
        policy=configured_policy(project,policy)
        with release_authority(project),routes._store(project) as conn:
            conn.execute('BEGIN IMMEDIATE')
            policy=configured_policy(project,policy)
            if recheck:recheck()
            if policy['task'] in CLASSIC and comparison_id is None:
                comparison_id=_comparison_for_evaluation(project,policy,evaluation_id)
            key=digest({'authorization':authorization,'policy':policy_subject(policy),'evaluation_id':evaluation_id,
                        'comparison_id':comparison_id,'incumbent_evaluation_id':incumbent_evaluation_id})
            existing=conn.execute('SELECT payload FROM policy_qualifications WHERE qualification_key=?',(key,)).fetchone()
            if existing:
                record=json.loads(existing[0])
                # Idempotence still revalidates the exact persisted decision subject.
                if (verify_policy_qualification(project,record) is None
                        or expected_job_id is not None and record['job_id']!=expected_job_id):raise ValueError('Saved policy qualification is stale or names another model')
                return {'status':'qualified','decision_source':'policy','reasons':[],'qualification':record}
            baseline=routes._active(conn,Path(project['source_dataset_dir']),policy['task'])
            assessed=_assess(project,policy,evaluation_id,comparison_id,incumbent_evaluation_id,baseline)
            receipt=(authorization or {}).get('receipt',{})
            if (expected_job_id is not None and assessed['job_id']!=expected_job_id
                    or receipt and receipt.get('job_id')!=assessed['job_id']
                    or receipt.get('evaluation_binding') is not None and receipt['evaluation_binding']!=assessed['evaluation_binding']):
                raise ValueError('Authorized task receipt names another evaluation model or binding')
            record={'schema_version':1,'contract':CONTRACT,'decision_source':'policy','policy_version':CONTRACT,
                    'revision_id':uuid.uuid4().hex,'qualification_key':key,'project_id':project['id'],
                    'source_dataset_path':project['source_dataset_dir'],'labelset_id':project.get('active_labelset_id','default'),
                    'task':policy['task'],'policy_revision':policy['revision'],'policy':policy_subject(policy),'policy_sha256':digest(policy_subject(policy)),
                    'evaluation_id':evaluation_id,'comparison_id':comparison_id or evaluation_id,
                    'incumbent_evaluation_id':incumbent_evaluation_id,'assessment_baseline':baseline,
                    'authorization':authorization,'created_at':routes._now(),'action':'qualify_policy',
                    'parent_revision_id':baseline['revision_id'] if baseline else None,'restored_from_revision_id':None,
                    'package_qualified':False,'device_accepted':False,**assessed}
            record['record_sha256']=digest(record)
            if recheck:recheck()
            configured_policy(project,policy)
            if _assess(project,policy,evaluation_id,comparison_id,incumbent_evaluation_id,baseline)!=assessed:
                raise ValueError('Evaluation changed during policy qualification')
            conn.execute('INSERT INTO policy_qualifications VALUES(?,?,?)',(record['revision_id'],key,json.dumps(record,allow_nan=False)))
            if activate:
                conn.execute('INSERT INTO policy_active_revisions VALUES(?,?,?) ON CONFLICT(source_dataset_path,task) DO UPDATE SET revision_id=excluded.revision_id',
                             (project['source_dataset_dir'],policy['task'],record['revision_id']))
            return {'status':'qualified','decision_source':'policy','reasons':[],'qualification':record}
    except QualificationInputNeeded as exc:return {'status':'input_needed','decision_source':'policy','reasons':[str(exc)]}
    except HTTPException as exc:
        if exc.status_code in (401,403):raise
        return {'status':'not_qualified','decision_source':'policy','reasons':[str(exc.detail)]}
    except (ValueError,OSError,KeyError,TypeError) as exc:return {'status':'not_qualified','decision_source':'policy','reasons':[str(exc)]}


def verify_policy_qualification(project,record):
    try:
        if (record.get('contract')!=CONTRACT or record.get('decision_source')!='policy'
                or record.get('schema_version')!=1 or record.get('action')!='qualify_policy'
                or not isinstance(record.get('policy'),dict)
                or record.get('policy_version')!=CONTRACT or record.get('project_id')!=project['id']
                or record.get('source_dataset_path')!=project['source_dataset_dir']
                or record.get('labelset_id')!=project.get('active_labelset_id','default')
                or record.get('policy_sha256')!=digest(record['policy'])
                or record.get('policy_revision')!=record['policy'].get('revision')
                or record.get('record_sha256')!=digest({k:v for k,v in record.items() if k!='record_sha256'})
                or {'reviewer','holdout_reviewed','holdout_review_verified'} & record.keys()
                or record.get('package_qualified') is not False or record.get('device_accepted') is not False):return None
        policy=record['policy']
        if policy.get('approval_policy_authorized') is not True or policy.get('auto_approve') is not True:return None
        from backend.engine.release_eligibility import verify_project_context
        verify_project_context(project)
        comparison_id=record['comparison_id'] if record['task'] in CLASSIC else None
        assessed=_assess(project,policy,record['evaluation_id'],comparison_id,record['incumbent_evaluation_id'],record['assessment_baseline'])
        if any(record.get(key)!=value for key,value in assessed.items()):return None
        receipt=(record.get('authorization') or {}).get('receipt') or {}
        if receipt and (receipt.get('job_id')!=assessed['job_id'] or receipt.get('evaluation_binding',assessed['evaluation_binding'])!=assessed['evaluation_binding']):return None
        return record
    except (ValueError,OSError,KeyError,TypeError,HTTPException):return None


def persist_quality_assessment(project,*,subject_kind,evaluation_id,result,authorization=None):
    """Every outcome is durable; failed criteria never disappear behind completed inference."""
    from backend.engine.model_operations import OperationsStore
    from backend.api.routes_model_deployments import _now
    store=OperationsStore(project['project_dir']);configured=store.policy()
    measured={}
    try:
        if subject_kind=='model':
            from backend.engine.evaluation_history import EvaluationHistory
            evidence=EvaluationHistory(Path(project['reports_dir'])/'evaluations').get(evaluation_id)
            measured={'evidence_available':True,'evidence_sha256':evidence['evidence_sha256'],
                      'binding':evidence['binding'],'job_id':evidence['result'].get('job_id'),
                      'metrics':evidence['result'].get('metrics',evidence['result'])}
        else:
            from backend.engine.flow_evaluation import read_evaluation
            evidence=read_evaluation(project,evaluation_id)
            measured={'evidence_available':True,'metrics':evidence['metrics'],
                      **{key:evidence[key] for key in ('record_sha256','version_id','cohort_id','graph_sha256','model_sha256','truth_sha256')}}
    except (ValueError,OSError,KeyError,TypeError) as exc:
        measured={'evidence_available':False,'reason':str(exc)}
    nonfinite=[]
    def finite(value,path='measured'):
        if type(value) is float and not math.isfinite(value):nonfinite.append(path);return None
        if isinstance(value,dict):return {key:finite(child,path+'.'+key) for key,child in value.items()}
        if isinstance(value,list):return [finite(child,path+'[]') for child in value]
        return value
    measured=finite(measured)
    if nonfinite:measured['nonfinite_fields']=nonfinite
    qualification=result.get('qualification') or {}
    subject={'contract':'automated_quality_assessment_v1','schema_version':1,'decision_source':'policy',
             'project_id':project['id'],'source_dataset_path':project['source_dataset_dir'],
             'labelset_id':project.get('active_labelset_id','default'),'subject_kind':subject_kind,
             'evaluation_id':evaluation_id,'authorization':authorization,
             'task_id':(authorization or {}).get('task_id'),'step_id':(authorization or {}).get('step_id'),
             'policy_version':qualification.get('policy_version',CONTRACT if subject_kind=='model' else 'whole-flow-policy-qualification-v1'),
             'policy_revision':qualification.get('policy_revision',(configured or {}).get('revision')),
             'policy_sha256':digest(policy_subject(configured)) if configured else None,
             'status':result['status'],'reasons':list(result.get('reasons') or []),
             'quality_qualified':result['status']=='qualified','measured':measured,
             'qualification_revision_id':qualification.get('revision_id'),
             'qualification_sha256':qualification.get('record_sha256')}
    key=digest(subject)
    record={**subject,'assessment_id':'quality_'+uuid.uuid4().hex,'assessment_key':key,'assessed_at':_now()}
    record['record_sha256']=digest(record);saved=store.save_assessment(record)
    return {**result,'assessment_id':saved['assessment_id'],'assessment':saved}


def qualify_candidate_policy(project,policy,evaluation_id,**options):
    result=_qualify_candidate_policy(project,policy,evaluation_id,**options)
    return persist_quality_assessment(project,subject_kind='model',evaluation_id=evaluation_id,result=result,
                                      authorization=options.get('authorization'))
