"""Immutable whole-flow evaluations over a frozen, explicit test cohort.

The graph executor remains the sole authority for final decisions. Model scores
are retained as node/ROI evidence and never substituted for Blob, routing or
decision rules. Histories retain their original metrics when evidence changes.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from backend.engine import dataset_metadata as dm
from backend.engine.flowchart_engine import FlowchartEngine, FlowchartPipeline, ordered_linear_nodes, verified_checkpoint_scope
from backend.engine.flow_provenance import pipeline_sha256
from backend.engine.flow_workspace import atomic_json, catalog_class_vocabulary
from backend.engine.image_truth import digest, read_truth, truth_scope
from backend.engine.specialized_models import SPECIALIZED_TASKS, flow_model_task, resolve_specialized_checkpoint
from backend.engine.class_semantics import class_role, recorded_roles, class_semantics_record
from backend.engine.heldout_splits import normalized_assignments


def _root(project):
    root = Path(project['project_dir']) / 'flow_evaluations'
    if root.is_symlink(): raise ValueError('Evaluation storage cannot be linked')
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_record(root, identifier, prefix):
    if not re.fullmatch(prefix + r'_[0-9a-f]{32}', identifier): raise ValueError('Invalid evidence identifier')
    path = root / identifier / 'record.json'
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file(): raise ValueError('Evidence record is unavailable')
    record = json.loads(path.read_text(encoding='utf-8'))
    expected = record.get('record_sha256'); value = {key: row for key,row in record.items() if key != 'record_sha256'}
    if digest(value) != expected: raise ValueError('Evidence record hash changed')
    return record


def saved_graph(project, version_id):
    if not re.fullmatch(r'[0-9a-f]{32}', version_id): raise ValueError('Invalid saved flow version')
    file = Path(project['project_dir']) / 'flowcharts' / 'versions' / f'{version_id}.json'
    if file.is_symlink() or not file.is_file(): raise ValueError('Saved flow version is unavailable')
    record = json.loads(file.read_text(encoding='utf-8'))
    source = str(Path(project.get('source_dataset_dir') or '').resolve())
    if (record.get('version_id') != version_id or record.get('source_dataset_path') != source
            or record.get('labelset_id', project.get('active_labelset_id','default')) != project.get('active_labelset_id','default')):
        raise ValueError('Saved flow belongs to another source or labelset')
    pipeline = FlowchartPipeline.model_validate(record['pipeline']); ordered_linear_nodes(pipeline)
    return pipeline, record


def verified_models(project, pipeline):
    """Reuse completed checkpoint, source and remote-download provenance guards."""
    from backend.engine.checkpoint_paths import trusted_checkpoint
    from backend.api.routes_evaluation import _matches_source_dataset
    from backend.api.routes_training import training_job_manager
    from backend.remote.operations import verify_downloaded_checkpoint
    import torch
    result = {}
    for node in pipeline.nodes:
        task = flow_model_task(node)
        if task is None: continue
        job_id = node.data.model_job_id
        if not job_id: raise ValueError(f'Model job is missing for {node.data.label}')
        record = training_job_manager.get_job(job_id)
        if record is not None and record.status != 'completed': raise ValueError(f'Model {job_id} is not completed')
        if task in SPECIALIZED_TASKS:
            checkpoint, metadata = resolve_specialized_checkpoint(project['models_dir'], job_id, task, project['source_dataset_dir'])
        else:
            checkpoint = trusted_checkpoint(job_id, project_models_dir=project['models_dir'])
            if checkpoint is None or not _matches_source_dataset(checkpoint.parent, project['source_dataset_dir'], task):
                raise ValueError(f'Model {job_id} does not match the active source/task')
            payload = torch.load(checkpoint, map_location='cpu', weights_only=True)
            if payload.get('task') != task or 'model_state_dict' not in payload: raise ValueError(f'Model {job_id} has an incompatible checkpoint')
            metadata_path = checkpoint.parent/'model_meta.json'
            metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.is_file() else {}
            classes = catalog_class_vocabulary({**payload,'task':task}).get('class_names') or []
            # Class identities and channel order belong to the checkpoint.
            # Validate each explicitly recorded alias before the merge can
            # hide a conflicting field; absent legacy vocabulary inherits it.
            for record in (payload, metadata):
                for key in ('classes', 'class_names'):
                    if record.get(key) is not None:
                        names = catalog_class_vocabulary({'task': task, key: record[key]}).get('class_names')
                        if names != classes:
                            raise ValueError(f'Model {job_id} class vocabulary differs between checkpoint and metadata')
            metadata = {**payload, **metadata}
            if (class_semantics_record(classes,recorded_roles(payload,task=task,classes=classes),task=task)['roles']
                    != class_semantics_record(classes,recorded_roles(metadata,task=task,classes=classes),task=task)['roles']):
                raise ValueError(f'Model {job_id} class roles differ between checkpoint and metadata')
            verify_downloaded_checkpoint(checkpoint.parent, job_id)
        result[(job_id, task)] = {'path':str(checkpoint.resolve()), 'metadata':metadata}
    return result


def _model_snapshot(models):
    rows = []
    for (job_id, task), model in sorted(models.items()):
        checkpoint = Path(model['path'])
        if checkpoint.is_symlink() or not checkpoint.is_file(): raise ValueError('Model checkpoint unavailable')
        metadata = model.get('metadata', {})
        rows.append({'job_id':job_id,'task':task,'checkpoint_path':str(checkpoint.resolve()),'checkpoint_sha256':dm._hash(checkpoint),
                     'class_names':catalog_class_vocabulary({**metadata,'task':task}).get('class_names'),
                     'training_provenance':copy.deepcopy(metadata.get('training_provenance') or {}),
                     'class_semantics':class_semantics_record(catalog_class_vocabulary({**metadata,'task':task}).get('class_names') or [], recorded_roles(metadata,task=task,classes=catalog_class_vocabulary({**metadata,'task':task}).get('class_names') or []),task=task),
                     'metadata_sha256':dm._hash(checkpoint.parent/'model_meta.json')})
    return rows


def graph_truth_scope(project, pipeline, models):
    tasks = set(); names = []; roles = {}; uses_recorded_roles = False
    for node in pipeline.nodes:
        task = flow_model_task(node)
        if task is None or node.data.node_type == 'preprocess': continue
        tasks.add(task)
        metadata = models[(node.data.model_job_id,task)].get('metadata',{})
        classes = node.data.params.get('class_names') or catalog_class_vocabulary({**metadata,'task':task}).get('class_names')
        if not classes and task == 'anomaly': classes = ['good','anomaly']
        if not classes and task == 'ocr': classes = ['text_match','text_mismatch']
        if not classes: raise ValueError(f'Record class names for {node.data.label} before declaring flow truth')
        model_roles = recorded_roles(metadata,task=task,classes=classes)
        resolved_roles=class_semantics_record(classes,model_roles,task=task)['roles']
        uses_recorded_roles = uses_recorded_roles or model_roles is not None
        for name in classes:
            role = resolved_roles[name]
            if name in roles and roles[name] != role: raise ValueError('Flow models have conflicting class roles: '+name)
            roles[name] = role
        names.extend(name for name in classes if name not in names)
    return truth_scope(project, next(iter(tasks)) if len(tasks)==1 else 'mixed', names,
                       roles if uses_recorded_roles or len(tasks)>1 else None)


def _participating_tasks(pipeline):
    return sorted({task for node in pipeline.nodes if node.data.node_type != 'preprocess'
                   and (task := flow_model_task(node)) is not None})


def describe_scope(project, version_id, *, model_provider=None):
    graph,_ = saved_graph(project,version_id); models = (model_provider or verified_models)(project,graph)
    return graph_truth_scope(project,graph,models)


def _split(project):
    source = Path(project['source_dataset_dir']).resolve()
    key = hashlib.sha256(str(source).encode()).hexdigest()
    file = Path(project['dataset_dir']) / 'splits' / f'{key}.json'
    if file.is_symlink() or not file.is_file(): raise ValueError('Save an explicit test split before freezing a held-out cohort')
    record = json.loads(file.read_text(encoding='utf-8'))
    if record.get('folder_path') != str(source) or not isinstance(record.get('assignments'),dict): raise ValueError('Saved test split belongs to another source')
    return file, normalized_assignments(source, record['assignments'])


def freeze_cohort(project, version_id, *, relative_paths=None, name='Held-out test cohort', model_provider=None):
    graph,_ = saved_graph(project,version_id); models = (model_provider or verified_models)(project,graph)
    scope = graph_truth_scope(project,graph,models)
    participating_tasks = _participating_tasks(graph) if scope['task'] == 'mixed' else None
    split_file, assignments = _split(project); source = Path(project['source_dataset_dir']).resolve()
    # Gold samples of the label review (E05) are its reference, not test truth, unless the dataset policy keeps them.
    from backend.engine.annotation_quality import gold_image_paths
    gold = gold_image_paths(project)
    selected = sorted(relative_paths if relative_paths is not None else
                      [name for name,part in assignments.items() if part=='test' and str((source/name).resolve()) not in gold])
    if not selected or len(selected)>5000 or len(selected)!=len(set(selected)): raise ValueError('Choose 1–5000 unique held-out test images')
    if not isinstance(name,str) or not name.strip() or len(name)>200: raise ValueError('Enter a cohort name under 200 characters')
    selected_hashes = set(); snapshots = []
    cohort_id = f'cohort_{uuid.uuid4().hex}'; root = _root(project)/'cohorts'; root.mkdir(exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.freezing-',dir=root))
    try:
        images = staging/'images'; images.mkdir()
        for relative in selected:
            path = Path(relative)
            if (path.is_absolute() or '..' in path.parts or '\\' in relative or assignments.get(relative) != 'test'):
                raise ValueError('Every cohort image must belong to the saved held-out test split')
            image = source/path
            if str(image.resolve()) in gold:
                raise ValueError(f'{relative} is a gold sample of the label review; gold images are not test truth unless the dataset policy keeps them')
            # Keep the resolver basis identical to the graph's recorded roles.
            roles = scope['class_semantics']['roles'] if any(b=='explicit' for b in scope['class_semantics']['basis'].values()) else None
            truth = read_truth(project,str(image),task=scope['task'],classes=scope['classes'],class_roles=roles,
                               participating_tasks=participating_tasks)
            checksum = truth['binding']['content_hash']; selected_hashes.add(checksum)
            frozen_name = f'{checksum}{image.suffix.lower()}'
            target = images/frozen_name
            if not target.exists(): shutil.copyfile(image,target)
            if dm._hash(target) != checksum or dm._hash(image) != checksum: raise ValueError('Source image changed while freezing')
            snapshots.append({'relative_path':relative,'image_path':str(image),'input_sha256':checksum,
                              'frozen_image':f'images/{frozen_name}', 'truth':truth})
        # File-content duplicates across train/val/test violate independence.
        for relative, partition in assignments.items():
            if partition == 'test': continue
            candidate = source/relative
            if candidate.is_file() and candidate.resolve().is_relative_to(source) and dm._hash(candidate) in selected_hashes:
                raise ValueError(f'Duplicate image leakage between held-out test and {partition}: {relative}')
        if dm._hash(split_file) is None: raise ValueError('Saved split is unavailable')
        record = {'schema_version':1,'cohort_id':cohort_id,'name':name.strip(),'created_at':dm._now(),'scope':scope,
                  'frozen_from_version_id':version_id,'split':'test','split_sha256':dm._hash(split_file),
                  'split_path':str(split_file),'samples':snapshots,'count':len(snapshots),
                  'truth_sha256':digest([row['truth']['truth_sha256'] for row in snapshots]),
                  'input_sha256':digest([{'relative_path':row['relative_path'],'sha256':row['input_sha256']} for row in snapshots])}
        if participating_tasks:record['participating_tasks']=participating_tasks
        record['record_sha256'] = digest(record); atomic_json(staging/'record.json',record)
        staging.rename(root/cohort_id)
        return record
    finally:
        if staging.exists(): shutil.rmtree(staging)


def read_cohort(project, cohort_id):
    record = _safe_record(_root(project)/'cohorts',cohort_id,'cohort')
    if record['scope']['project_id'] != project['id']: raise ValueError('Cohort belongs to another project')
    return record


def _cohort_changes(project, cohort):
    reasons = []
    scope = cohort['scope']
    participating_tasks = cohort.get('participating_tasks') if scope['task'] == 'mixed' else None
    if scope['task'] == 'mixed' and (not isinstance(participating_tasks,list) or len(set(participating_tasks))<2):
        return ['mixed_truth_task_dependencies_missing']
    if (scope['source_dataset_path'] != str(Path(project.get('source_dataset_dir') or '').resolve())
            or scope['labelset_id'] != project.get('active_labelset_id','default')):
        return ['source_or_labelset_changed']
    if dm._hash(Path(cohort['split_path'])) != cohort['split_sha256']: reasons.append('held_out_split_changed')
    from backend.engine.annotation_quality import gold_image_paths
    gold = gold_image_paths(project)
    reasons += [f"gold_image:{sample['relative_path']}" for sample in cohort['samples'] if str(Path(sample['image_path']).resolve()) in gold]
    root = _root(project)/'cohorts'/cohort['cohort_id']
    for sample in cohort['samples']:
        if dm._hash(root/sample['frozen_image']) != sample['input_sha256']: reasons.append(f"frozen_input_changed:{sample['relative_path']}")
        try:
            roles = scope['class_semantics']['roles'] if any(b=='explicit' for b in scope['class_semantics']['basis'].values()) else None
            current = read_truth(project,sample['image_path'],task=scope['task'],classes=scope['classes'],class_roles=roles,
                                 participating_tasks=participating_tasks)
            if current['truth_sha256'] != sample['truth']['truth_sha256']: reasons.append(f"truth_or_source_changed:{sample['relative_path']}")
        except (ValueError,OSError): reasons.append(f"source_unavailable:{sample['relative_path']}")
    return reasons


def evaluate_flow(project, version_id, cohort_id, *, engine=None, model_provider=None):
    graph,_ = saved_graph(project,version_id); models = (model_provider or verified_models)(project,graph)
    snapshots = _model_snapshot(models); cohort = read_cohort(project,cohort_id)
    if graph_truth_scope(project,graph,models) != cohort['scope']: raise ValueError('Flow task/classes differ from the frozen truth scope')
    if cohort['scope']['task'] == 'mixed' and cohort.get('participating_tasks') != _participating_tasks(graph):
        raise ValueError('Flow participating tasks differ from the frozen truth dependencies')
    changed = _cohort_changes(project,cohort)
    if changed: raise ValueError('Frozen cohort truth/source changed; freeze a new cohort: '+', '.join(changed[:10]))
    graph_hash = pipeline_sha256(graph); engine = engine or FlowchartEngine(device='cpu')
    confusion = {truth:{verdict:0 for verdict in ('OK','NG','REVIEW')} for truth in ('OK','NG')}
    records = []; escapes = []; overkills = []; unknown = []; errors = []
    with verified_checkpoint_scope({key:Path(model['path']) for key,model in models.items()}):
        for sample in cohort['samples']:
            image = _root(project)/'cohorts'/cohort_id/sample['frozen_image']
            try:
                result = engine.execute(pipeline=graph,image_path=str(image),image_id=sample['relative_path'])
                verdict = result['final_verdict']
                if result.get('status') == 'partial': verdict = 'REVIEW'
                error = result.get('error_message')
            except Exception as exc:
                verdict = 'REVIEW'; error = f'{type(exc).__name__}: {exc}'
                result = {'status':'error','rejection_reason':error,'execution_steps':[],'crops':[]}
            row = {'relative_path':sample['relative_path'],'image_path':sample['image_path'],'input_sha256':sample['input_sha256'],
                   'truth_sha256':sample['truth']['truth_sha256'],'truth':sample['truth']['verdict'],
                   'truth_reason':sample['truth']['unknown_reason'],'decision':verdict,'execution_status':result['status'],
                   'rejection_reason':result['rejection_reason'],'node_evidence':result['execution_steps'],
                   'roi_evidence':result['crops'],'routed_output_node_id':result.get('routed_output_node_id'),
                   'latency_ms':result.get('total_latency_ms'),'error':error}
            records.append(row)
            if error: errors.append(row)
            if row['truth'] == 'UNKNOWN': unknown.append(row); continue
            confusion[row['truth']][verdict] += 1
            if row['truth'] == 'NG' and verdict == 'OK': escapes.append(row)
            if row['truth'] == 'OK' and verdict == 'NG': overkills.append(row)
    changes = _cohort_changes(project,cohort)
    if _model_snapshot(models) != snapshots: changes.append('checkpoint_or_model_scope_changed_during_run')
    if pipeline_sha256(saved_graph(project,version_id)[0]) != graph_hash: changes.append('saved_graph_changed_during_run')
    normal = sum(confusion['OK'].values()); defect = sum(confusion['NG'].values()); known = normal+defect
    review = sum(row['decision']=='REVIEW' for row in records)
    record = {'schema_version':1,'evaluation_id':f'eval_{uuid.uuid4().hex}', 'created_at':dm._now(),
              'status':'invalidated' if changes else 'completed_with_errors' if errors else 'completed',
              'scope':cohort['scope'],'version_id':version_id,'graph_sha256':graph_hash,'pipeline':graph.model_dump(),
              'cohort_id':cohort_id,'cohort_sha256':cohort['record_sha256'],'truth_sha256':cohort['truth_sha256'],
              'input_sha256':cohort['input_sha256'],'split_sha256':cohort['split_sha256'],'models':snapshots,
              'model_sha256':digest(snapshots),'device':str(engine.device),'confusion':confusion,
              'coverage':{'total':len(records),'known':known,'unknown':len(unknown),
                          'invalidated':sum(bool(r['truth']['invalidated']) for r in cohort['samples']), 'known_fraction':known/len(records)},
              'metrics':{'escape_rate':len(escapes)/defect if defect else None,
                         'escape_unavailable_reason':None if defect else 'No explicit defect truth in held-out cohort',
                         'overkill_rate':len(overkills)/normal if normal else None,
                         'overkill_unavailable_reason':None if normal else 'No explicit normal truth in held-out cohort',
                         'review_rate':review/len(records),'normal_count':normal,'defect_count':defect},
              'records':records,'escapes':escapes,'overkills':overkills,'unknown_truth':unknown,'errors':errors,
              'invalidated_reasons':changes}
    record['record_sha256'] = digest(record)
    atomic_json(_root(project)/'runs'/record['evaluation_id']/'record.json',record)
    return {**record,'validity':{'valid':not changes,'reasons':changes}}


def read_evaluation(project, evaluation_id):
    record = _safe_record(_root(project)/'runs',evaluation_id,'eval')
    reasons = list(record.get('invalidated_reasons',[]))
    try:
        cohort = read_cohort(project,record['cohort_id']); reasons.extend(_cohort_changes(project,cohort))
        if pipeline_sha256(saved_graph(project,record['version_id'])[0]) != record['graph_sha256']: reasons.append('saved_graph_changed')
        for model in record['models']:
            if dm._hash(Path(model['checkpoint_path'])) != model['checkpoint_sha256']: reasons.append('checkpoint_changed:'+model['job_id'])
            if dm._hash(Path(model['checkpoint_path']).parent/'model_meta.json') != model.get('metadata_sha256'): reasons.append('model_metadata_changed:'+model['job_id'])
    except (ValueError,OSError,KeyError): reasons.append('evidence_unavailable')
    return {**record,'validity':{'valid':not reasons,'reasons':list(dict.fromkeys(reasons))}}


def list_evidence(project, kind):
    if kind not in {'cohorts','runs'}: raise ValueError('Invalid evidence kind')
    root = _root(project)/kind
    result = []
    for file in root.glob('*/record.json') if root.exists() else []:
        try:
            row = read_cohort(project,file.parent.name) if kind=='cohorts' else read_evaluation(project,file.parent.name)
            if row['scope']['source_dataset_path'] != str(Path(project.get('source_dataset_dir') or '').resolve()): continue
            if row['scope']['labelset_id'] != project.get('active_labelset_id','default'): continue
            listed = {key:value for key,value in row.items() if key not in {'samples','records','pipeline','escapes','overkills','unknown_truth','errors'}}
            if kind == 'runs' and row.get('pipeline'):
                from backend.engine.flow_provenance import semantic_sha256
                listed['semantic_sha256'] = semantic_sha256(row['pipeline'])  # the rules evaluated, apart from the layout
            result.append(listed)
        except (ValueError,OSError,KeyError): continue
    return sorted(result,key=lambda row:row['created_at'],reverse=True)


def create_review_queue(project, evaluation_id):
    from backend.engine import data_workbench as dw
    record = read_evaluation(project,evaluation_id)
    if not record['validity']['valid']: raise ValueError('Whole-flow evaluation changed; evaluate again before creating a review queue')
    rows = []
    for row in record['records']:
        rows.append({'file_path':row['image_path'],'image_sha256':row['input_sha256'],
                     'ground_truth_verdict':row['truth'],'candidate':{'verdict':row['decision']},
                     'is_correct':False if row['truth'] in {'OK','NG'} and row['truth'] != row['decision'] else None,
                     'error':row['error'],'review_required':row['decision']=='REVIEW','unknown_truth':row['truth']=='UNKNOWN',
                     'origin_evidence':{'truth_sha256':row['truth_sha256'],'decision':row['decision'],'truth':row['truth'],
                                        'node_evidence':row['node_evidence'],'roi_evidence':row['roi_evidence']}})
    origin = {'flow_evaluation_id':evaluation_id,'evidence_sha256':record['record_sha256'],
              'version_id':record['version_id'],'cohort_id':record['cohort_id'],'graph_sha256':record['graph_sha256'],'step':5}
    return dw.create_review_queue(project['project_dir'],project['source_dataset_dir'],project['task'],
                                  project.get('active_labelset_id','default'),rows,origin,margin=0)
