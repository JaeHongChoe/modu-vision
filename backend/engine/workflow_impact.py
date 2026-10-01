"""Read current dependencies without rewriting historical results or approvals."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
from fastapi import HTTPException


def _read(path):
    if path.is_symlink() or not path.is_file():return None
    try:
        value=json.loads(path.read_text());return value if isinstance(value,dict) else None
    except (OSError,ValueError):return None


def _hash(path):
    if path.is_symlink() or not path.is_file():return None
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def _data_state(project,binding):
    if not binding or not binding.get('version_dir'):return 'unverified','No immutable training label/split version'
    root=Path(project['project_dir']).resolve();directory=Path(binding['version_dir'])
    if directory.is_symlink() or not directory.resolve().is_relative_to(root/'versions'):
        return 'unverified','Bound version is outside this project'
    manifest=_read(directory/'manifest.json')
    if not manifest:return 'unverified','Bound dataset version is unavailable'
    try:
        from backend.api.routes_dataset_versions import _manifest_digest,_verify
        if manifest.get('project_id') != project['id']:
            return 'unverified','Bound version belongs to another project'
        if _manifest_digest(manifest)!=binding.get('manifest_sha256'):
            return 'changed','Bound dataset manifest hash changed'
        if manifest.get('labelset_id','default')!=project.get('active_labelset_id','default'):
            return 'changed','Active labelset differs from training labels'
        checked=_verify(project,directory,manifest)
        # Truth, tags and review audit are separate from the pixel/label input.
        # Preserve immutable backup checks while comparing review eligibility
        # through its own binding rather than every workflow ledger byte.
        editable=[name for name in checked['editable_changed_files']
                  if not (name.startswith(('studio/','studio_scoped/')) and 'metadata' in Path(name).parts[1:-1])]
        if checked['status']!='verified' or editable:
            return 'changed','Source, labels or split differ from training version'
        if binding.get('team_data'):
            from backend.engine.team_data import training_binding
            current=training_binding(project,Path(project['source_dataset_dir']))
            if any(current.get(key)!=binding['team_data'].get(key) for key in ('book_sha256','policy_sha256','eligibility_sha256')):
                return 'changed','Label guidance, review policy or training eligibility changed'
        return 'current','Source, labels and split match the bound training version'
    except (ValueError,OSError,KeyError,HTTPException) as exc:
        return 'changed',str(getattr(exc,'detail',exc))


def analyze(project):
    from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root
    from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
    annotation=set_request_annotation_root(Path(project['annotations_dir']))
    split=set_request_split_root(Path(project['dataset_dir'])/'splits')
    try:
        return _analyze(project)
    finally:
        reset_request_split_root(split)
        reset_request_annotation_root(annotation)


def _analyze(project):
    root=Path(project['project_dir']).resolve();source=project.get('source_dataset_dir')
    model_root=Path(project['models_dir'])
    if model_root.is_symlink() or not model_root.resolve().is_relative_to(root):raise ValueError('Model storage must belong to this project')
    models=[];actions=set()
    for path in sorted(model_root.rglob('model_meta.json')) if model_root.is_dir() else []:
        if any(p.is_symlink() for p in (path,*path.parents)) or not path.resolve().is_relative_to(root):continue
        metadata=_read(path)
        if not metadata:continue
        declared=metadata.get('source_dataset_path')
        scope_matches=not declared or not source or Path(declared).resolve()==Path(source).resolve()
        checkpoint=path.parent/'best_model.pt';actual=_hash(checkpoint);expected=metadata.get('checkpoint_sha256')
        state='changed' if not actual or expected and actual!=expected else 'current' if expected else 'unverified'
        data_state,reason=_data_state(project,metadata.get('training_provenance'))
        if not scope_matches:data_state,reason='different_source','Historical model belongs to a previous source; verify lineage before reuse'
        models.append({'job_id':path.parent.name,'task':metadata.get('task'),'checkpoint_sha256':actual,
            'checkpoint_state':state,'data_state':data_state,'reason':reason,'parent_job_id':metadata.get('parent_job_id'),
            'source_dataset_path':declared,'scope_matches':scope_matches,
            'dataset_version_id':(metadata.get('training_provenance') or {}).get('dataset_version_id')})
        if state=='changed' and scope_matches:actions.update(('evaluate_model','approve_model','export_package','verify_target'))
        if data_state=='changed':actions.update(('review_data','train_candidate','evaluate_model','compare_fixed_cohort','approve_model'))
        if data_state=='unverified':actions.add('verify_dataset_version')
    index={m['job_id']:m for m in models};flows=[]
    from backend.engine.flow_provenance import pipeline_sha256
    for path in sorted((root/'flowcharts'/'versions').glob('*.json')):
        record=_read(path)
        if not record:continue
        scope_matches=record.get('source_dataset_path')==source
        graph=record.get('pipeline',{});ids=list(dict.fromkeys(n.get('data',{}).get('model_job_id') for n in graph.get('nodes',[]) if n.get('data',{}).get('model_job_id')))
        try:
            actual=pipeline_sha256(graph);declared=record.get('pipeline_hash')
            changed=bool(declared and declared!=actual) or any(i not in index or index[i]['checkpoint_state']=='changed' for i in ids)
            stale_data=any(index[i]['data_state']=='changed' for i in ids if i in index)
            state='different_source' if not scope_matches else 'changed' if changed else 'revalidation_required' if stale_data else 'unverified'
        except (ValueError,TypeError):actual=None;state='changed'
        flows.append({'version_id':record.get('version_id'),'name':graph.get('name'),'graph_sha256':actual,'model_job_ids':ids,'state':state,'scope_matches':scope_matches,'source_dataset_path':record.get('source_dataset_path'),
                      'reason':'Run a whole-flow evaluation for this exact saved version and current truth'})
        if state in ('changed','revalidation_required'):actions.update(('evaluate_flow','export_package','verify_target'))
    evaluations=[]
    from backend.engine.evaluation_history import EvaluationHistory
    directory=root/'reports'/'evaluations'
    if directory.is_dir() and not directory.is_symlink():
        history=EvaluationHistory(directory)
        for path in sorted(directory.glob('evaluation_*.json')):
            try:
                record=history.get(path.stem);job=record.get('result',{}).get('job_id');model=index.get(job);bound=record.get('binding',{})
                state='changed' if model and bound.get('checkpoint_sha256')!=model['checkpoint_sha256'] else 'unverified'
                evaluations.append({'evaluation_id':path.stem,'job_id':job,'state':state})
            except (ValueError,OSError,KeyError):evaluations.append({'evaluation_id':path.stem,'state':'changed'})
    approvals=[];database=root/'model_deployments.sqlite3'
    if database.is_file() and not database.is_symlink():
        with sqlite3.connect(f'file:{database}?mode=ro',uri=True) as connection:
            connection.row_factory=sqlite3.Row
            for row in connection.execute('SELECT revisions.* FROM active_revisions JOIN revisions ON revisions.revision_id=active_revisions.revision_id'):
                scope_matches=row['source_dataset_path']==source
                model=index.get(row['job_id']);state='changed' if model and model['checkpoint_sha256']!=row['checkpoint_sha256'] else 'revalidation_required' if model and model['data_state']=='changed' else 'unverified'
                approvals.append({'revision_id':row['revision_id'],'job_id':row['job_id'],'state':state if scope_matches else 'different_source','reviewer':row['reviewer'],'scope_matches':scope_matches,'source_dataset_path':row['source_dataset_path']})
    flow_evaluations=[]
    if (root/'flow_evaluations'/'runs').is_dir():
        from backend.engine.flow_evaluation import list_evidence
        flow_evaluations=list_evidence(project,'runs')
        for flow in flows:
            matches=[row for row in flow_evaluations if row.get('version_id')==flow['version_id'] and row.get('graph_sha256')==flow['graph_sha256']]
            if flow['state']=='unverified' and any(row.get('validity',{}).get('valid') for row in matches):
                flow['state']='current'
                flow['reason']='Whole-flow evidence matches current inputs; deployment and quality approval are separate'
            elif flow['state'] in ('unverified','current') and any(row.get('validity',{}).get('valid') is False for row in matches):
                flow['state']='revalidation_required'
                flow['reason']='Saved whole-flow truth or inputs changed; evaluate this version again'
                actions.update(('evaluate_flow','export_package','verify_target'))
    packages=[]
    if (root/'exports'/'flows').is_dir():
        from backend.engine.product_delivery import package_library
        for row in package_library(project)['packages']:
            packages.append({'package_id':row['package_id'],'name':row['name'],'integrity':row['integrity'],'parity':row.get('parity'),
                'version_id':row.get('version_id'),'scope_matches':row['scope_matches'],'state':'different_source' if not row['scope_matches'] else 'changed' if row['integrity']!='verified' else 'revalidation_required' if any(f['version_id']==row.get('version_id') and f['state'] in ('changed','revalidation_required') for f in flows) else 'unverified'})
    return {'schema_version':1,'project_id':project['id'],'source_dataset_path':source,'labelset_id':project.get('active_labelset_id','default'),
        'models':models,'flows':flows,'model_evaluations':evaluations,'flow_evaluations':flow_evaluations,'approvals':approvals,'packages':packages,
        'required_actions':sorted(actions),'quality_approved':False,
        'limits':'Historical evidence is preserved. Matching bytes alone do not approve model quality or target execution.'}
