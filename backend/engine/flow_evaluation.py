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
from backend.engine.heldout_splits import folder_split_assignments, folder_split_fingerprint, normalized_assignments


def _root(project):
    root = Path(project['project_dir']) / 'flow_evaluations'
    if root.is_symlink(): raise ValueError('평가 기록 폴더가 다른 위치로 연결되어 있습니다. 프로젝트 폴더의 링크를 실제 폴더로 바꾼 뒤 다시 평가하세요.')
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_record(root, identifier, prefix):
    if not re.fullmatch(prefix + r'_[0-9a-f]{32}', identifier): raise ValueError('평가 기록 ID가 올바르지 않습니다. 목록에서 다시 고르세요.')
    path = root / identifier / 'record.json'
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file(): raise ValueError('평가 기록을 찾지 못했습니다. 목록을 새로 고친 뒤 다시 고르세요.')
    record = json.loads(path.read_text(encoding='utf-8'))
    expected = record.get('record_sha256'); value = {key: row for key,row in record.items() if key != 'record_sha256'}
    if digest(value) != expected: raise ValueError('평가 기록 파일이 저장 뒤 바뀌었습니다. 평가를 다시 실행하세요.')
    return record


def saved_graph(project, version_id):
    if not re.fullmatch(r'[0-9a-f]{32}', version_id): raise ValueError('저장된 흐름 버전 ID가 올바르지 않습니다. 목록에서 다시 고르세요.')
    file = Path(project['project_dir']) / 'flowcharts' / 'versions' / f'{version_id}.json'
    if file.is_symlink() or not file.is_file(): raise ValueError('저장된 흐름 버전을 찾지 못했습니다. 5단계에서 버전 목록을 새로 고친 뒤 다시 고르세요.')
    record = json.loads(file.read_text(encoding='utf-8'))
    source = str(Path(project.get('source_dataset_dir') or '').resolve())
    if (record.get('version_id') != version_id or record.get('source_dataset_path') != source
            or record.get('labelset_id', project.get('active_labelset_id','default')) != project.get('active_labelset_id','default')):
        raise ValueError('이 흐름 버전은 다른 데이터 원본이나 라벨 세트에서 저장한 것입니다. 현재 원본·라벨 세트에서 저장한 버전을 고르세요.')
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
        if not job_id: raise ValueError(f'"{node.data.label}" 노드에 모델이 연결되지 않았습니다. 편집에서 모델을 고른 뒤 저장하세요.')
        record = training_job_manager.get_job(job_id)
        if record is not None and record.status != 'completed': raise ValueError(f'모델 {job_id}의 학습이 끝나지 않았습니다. 3단계에서 학습이 끝난 뒤 평가하세요.')
        if task in SPECIALIZED_TASKS:
            checkpoint, metadata = resolve_specialized_checkpoint(project['models_dir'], job_id, task, project['source_dataset_dir'])
        else:
            checkpoint = trusted_checkpoint(job_id, project_models_dir=project['models_dir'])
            if checkpoint is None or not _matches_source_dataset(checkpoint.parent, project['source_dataset_dir'], task):
                raise ValueError(f'모델 {job_id}은(는) 지금 데이터로 학습한 모델이 아닙니다(학습 뒤 데이터가 바뀌었거나 다른 원본·작업 종류). 현재 모델로 흐름을 다시 저장하거나 3단계에서 다시 학습하세요.')
            payload = torch.load(checkpoint, map_location='cpu', weights_only=True)
            if payload.get('task') != task or 'model_state_dict' not in payload: raise ValueError(f'모델 {job_id}의 체크포인트 형식이 이 작업과 맞지 않습니다. 같은 작업 종류의 모델을 고르세요.')
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
                            raise ValueError(f'모델 {job_id}의 클래스 목록이 체크포인트와 기록에서 다릅니다. 3단계에서 모델을 다시 학습하세요.')
            metadata = {**payload, **metadata}
            if (class_semantics_record(classes,recorded_roles(payload,task=task,classes=classes),task=task)['roles']
                    != class_semantics_record(classes,recorded_roles(metadata,task=task,classes=classes),task=task)['roles']):
                raise ValueError(f'모델 {job_id}의 클래스 역할(정상·불량)이 체크포인트와 기록에서 다릅니다. 3단계에서 모델을 다시 학습하세요.')
            verify_downloaded_checkpoint(checkpoint.parent, job_id)
        result[(job_id, task)] = {'path':str(checkpoint.resolve()), 'metadata':metadata}
    return result


def _model_snapshot(models):
    rows = []
    for (job_id, task), model in sorted(models.items()):
        checkpoint = Path(model['path'])
        if checkpoint.is_symlink() or not checkpoint.is_file(): raise ValueError('모델 체크포인트 파일을 찾지 못했습니다. 3단계에서 모델이 남아 있는지 확인하세요.')
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
        if not classes: raise ValueError(f'"{node.data.label}" 노드의 클래스 이름이 기록되지 않아 정답을 정할 수 없습니다. 클래스 이름을 기록한 모델로 흐름을 다시 저장하세요.')
        model_roles = recorded_roles(metadata,task=task,classes=classes)
        resolved_roles=class_semantics_record(classes,model_roles,task=task)['roles']
        uses_recorded_roles = uses_recorded_roles or model_roles is not None
        for name in classes:
            role = resolved_roles[name]
            if name in roles and roles[name] != role: raise ValueError('흐름의 모델들이 같은 클래스를 다른 역할로 씁니다: '+name+'. 클래스 역할을 하나로 맞춘 뒤 흐름을 다시 저장하세요.')
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
    """(split file or None, assignments): the saved split when there is one, else the source's own train/val/test
    folders, which a user laid out by hand and which the loaders already honour."""
    source = Path(project['source_dataset_dir']).resolve()
    key = hashlib.sha256(str(source).encode()).hexdigest()
    file = Path(project['dataset_dir']) / 'splits' / f'{key}.json'
    if file.is_symlink() or not file.is_file():
        assignments = folder_split_assignments(source)
        if not any(part == 'test' for part in assignments.values()):
            raise ValueError('시험 분할이 저장되지 않았습니다. 1단계에서 분할을 저장하거나 원본의 test/ 폴더에 시험 이미지를 둔 뒤 다시 고정하세요.')
        return None, assignments
    record = json.loads(file.read_text(encoding='utf-8'))
    if record.get('folder_path') != str(source) or not isinstance(record.get('assignments'),dict): raise ValueError('저장된 시험 분할이 다른 데이터 원본의 것입니다. 1단계에서 현재 원본으로 분할을 다시 저장하세요.')
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
    if not selected or len(selected)>5000 or len(selected)!=len(set(selected)): raise ValueError('시험 이미지를 1~5000장, 중복 없이 고르세요.')
    if not isinstance(name,str) or not name.strip() or len(name)>200: raise ValueError('시험 묶음 이름을 200자 이내로 입력하세요.')
    selected_hashes = set(); snapshots = []
    cohort_id = f'cohort_{uuid.uuid4().hex}'; root = _root(project)/'cohorts'; root.mkdir(exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.freezing-',dir=root))
    try:
        images = staging/'images'; images.mkdir()
        for relative in selected:
            path = Path(relative)
            if (path.is_absolute() or '..' in path.parts or '\\' in relative or assignments.get(relative) != 'test'):
                raise ValueError('시험 묶음의 이미지는 모두 저장된 시험 분할에 있어야 합니다. 시험 분할의 이미지만 고르세요.')
            image = source/path
            if str(image.resolve()) in gold:
                raise ValueError(f'{relative}은(는) 라벨 검수의 기준 샘플(gold sample)이라 시험 정답으로 쓰지 않습니다. 데이터 정책에서 기준 샘플을 시험에 포함하도록 바꾸거나 이 이미지를 빼고 고정하세요.')
            # Keep the resolver basis identical to the graph's recorded roles.
            roles = scope['class_semantics']['roles'] if any(b=='explicit' for b in scope['class_semantics']['basis'].values()) else None
            truth = read_truth(project,str(image),task=scope['task'],classes=scope['classes'],class_roles=roles,
                               participating_tasks=participating_tasks)
            checksum = truth['binding']['content_hash']; selected_hashes.add(checksum)
            frozen_name = f'{checksum}{image.suffix.lower()}'
            target = images/frozen_name
            if not target.exists(): shutil.copyfile(image,target)
            if dm._hash(target) != checksum or dm._hash(image) != checksum: raise ValueError('고정하는 동안 원본 이미지가 바뀌었습니다. 다시 고정하세요.')
            snapshots.append({'relative_path':relative,'image_path':str(image),'input_sha256':checksum,
                              'frozen_image':f'images/{frozen_name}', 'truth':truth})
        # File-content duplicates across train/val/test violate independence.
        for relative, partition in assignments.items():
            if partition == 'test': continue
            candidate = source/relative
            if candidate.is_file() and candidate.resolve().is_relative_to(source) and dm._hash(candidate) in selected_hashes:
                raise ValueError(f'시험 이미지와 같은 내용의 이미지가 {partition} 분할에도 있습니다: {relative}. 1단계에서 중복 이미지를 한쪽에만 두고 분할을 다시 저장하세요.')
        if split_file is not None and dm._hash(split_file) is None: raise ValueError('저장된 분할을 읽지 못했습니다. 1단계에서 분할을 다시 저장하세요.')
        split_kind = 'file' if split_file is not None else 'folders'
        split_sha256 = dm._hash(split_file) if split_file is not None else folder_split_fingerprint(assignments)
        record = {'schema_version':1,'cohort_id':cohort_id,'name':name.strip(),'created_at':dm._now(),'scope':scope,
                  'frozen_from_version_id':version_id,'split':'test','split_kind':split_kind,'split_sha256':split_sha256,
                  'split_path':str(split_file if split_file is not None else source),'samples':snapshots,'count':len(snapshots),
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
    if record['scope']['project_id'] != project['id']: raise ValueError('이 시험 묶음은 다른 프로젝트의 것입니다. 현재 프로젝트에서 고정한 묶음을 고르세요.')
    return record


def _split_fingerprint(cohort):
    """What the cohort's split looks like now: the saved file's hash, or the folder membership's digest."""
    if cohort.get('split_kind') == 'folders': return folder_split_fingerprint(folder_split_assignments(Path(cohort['split_path'])))
    return dm._hash(Path(cohort['split_path']))


def _cohort_changes(project, cohort):
    reasons = []
    scope = cohort['scope']
    participating_tasks = cohort.get('participating_tasks') if scope['task'] == 'mixed' else None
    if scope['task'] == 'mixed' and (not isinstance(participating_tasks,list) or len(set(participating_tasks))<2):
        return ['mixed_truth_task_dependencies_missing']
    if (scope['source_dataset_path'] != str(Path(project.get('source_dataset_dir') or '').resolve())
            or scope['labelset_id'] != project.get('active_labelset_id','default')):
        return ['source_or_labelset_changed']
    if _split_fingerprint(cohort) != cohort['split_sha256']: reasons.append('held_out_split_changed')
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
    if graph_truth_scope(project,graph,models) != cohort['scope']: raise ValueError('흐름의 작업 종류·클래스가 고정한 정답 범위와 다릅니다. 시험 묶음을 새로 고정한 뒤 평가하세요.')
    if cohort['scope']['task'] == 'mixed' and cohort.get('participating_tasks') != _participating_tasks(graph):
        raise ValueError('흐름에 참여하는 모델 종류가 고정한 정답 범위와 다릅니다. 시험 묶음을 새로 고정한 뒤 평가하세요.')
    changed = _cohort_changes(project,cohort)
    if changed: raise ValueError('고정한 시험 묶음의 정답이나 원본이 바뀌었습니다. 묶음을 새로 고정한 뒤 평가하세요: '+', '.join(changed[:10]))
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
    if kind not in {'cohorts','runs'}: raise ValueError('평가 기록 종류가 올바르지 않습니다. 목록에서 다시 고르세요.')
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
    if not record['validity']['valid']: raise ValueError('전체 흐름 평가가 바뀌었습니다. 다시 평가한 뒤 검토 목록을 만드세요.')
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


def _label_verdict(label, roles):
    """OK for a normal-role class, NG naming a defect-role class; anomaly folders map to the single class of that role."""
    if label in roles:
        role = roles[label]
        return ('OK', []) if role == 'normal' else ('NG', [label]) if role == 'defect' else None
    wanted = 'normal' if label == 'good' else 'defect' if label == 'defect' else None
    names = [name for name, role in roles.items() if role == wanted] if wanted else []
    if len(names) != 1: return None
    return ('OK', []) if wanted == 'normal' else ('NG', names)


_SHAPE_TYPES = {'bbox', 'rotated_bbox', 'polygon', 'brush_mask'}


def _shape_label_verdict(rows, roles):
    """A detection image's truth from the labels a person made (drawn or adopted from a draft) on it, only when they say
    something positive: a box of a defect-role class makes it NG with those classes; an explicit normal mark with no box
    makes it OK; no label, or boxes of classes without a defect role, leave it unknown (an empty label is never OK)."""
    if not isinstance(rows, list):
        return None
    defects = sorted({row.get('label') for row in rows if isinstance(row, dict) and row.get('type') in _SHAPE_TYPES
                      and roles.get(row.get('label')) == 'defect'})
    if defects:
        return 'NG', defects
    shapes = any(isinstance(row, dict) and row.get('type') in _SHAPE_TYPES for row in rows)
    normal = any(isinstance(row, dict) and (row.get('is_normal') is True or (row.get('type') == 'tag' and roles.get(row.get('label')) == 'normal'))
                 for row in rows)
    return ('OK', []) if normal and not shapes else None


def confirm_truth_from_labels(project, cohort_id, version_id, *, reviewer, note=None, model_provider=None, labels_for=None):
    """A reviewer confirms a frozen cohort's folder labels as its explicit truth in one step. Every image still gets
    its own dated declaration under the reviewer's name; an image whose folder names no class of the scope stays
    unknown, and a declaration that already says the same is kept."""
    from pathlib import PurePath
    from backend.engine import image_truth
    from backend.engine.dataset_inventory import folder_label_split
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError('정답 확정에는 검토자 이름이 필요합니다.')
    cohort = read_cohort(project, cohort_id)
    scope = describe_scope(project, version_id, model_provider=model_provider)
    task, classes, roles = scope['task'], scope['classes'], scope['class_semantics']['roles']
    if task == 'mixed':
        raise ValueError('여러 종류의 모델이 섞인 흐름은 폴더 라벨로 정답을 정할 수 없습니다. 이미지별로 검토하세요.')
    # Detection reads the boxes a person made on each image (labels_for is the app's own label reader); the other tasks
    # read the image's folder name. Either way the reviewer's press is what declares the truth.
    shapes = task == 'detection'
    if shapes and labels_for is None:
        raise ValueError('검출 흐름의 정답은 이미지의 라벨로 확정합니다. 라벨을 읽지 못해 확정하지 않았습니다. 다시 시도하세요.')
    note = note or ('이미지의 라벨을 검토해 정답으로 확정' if shapes else '폴더 라벨을 검토해 정답으로 확정')
    declared = kept = 0; unknown = []
    from backend.engine.class_overrides import read_class_overrides, effective_class
    overrides = read_class_overrides(Path(project['source_dataset_dir'])) if task == 'classification' else {}
    for sample in cohort['samples']:
        if shapes:
            decided = _shape_label_verdict(labels_for(sample['image_path']), roles)
        else:
            label, _ = folder_label_split(PurePath(sample['relative_path']), task)
            label, _ = effective_class(Path(sample['image_path']), label, overrides)  # a class chosen in step 2 wins
            decided = _label_verdict(label, roles) if label else None
        if decided is None:
            unknown.append(sample['relative_path']); continue
        verdict, defects = decided
        current = image_truth.read_truth(project, sample['image_path'], task=task, classes=classes, class_roles=roles)
        if current['verdict'] == verdict and current['defect_classes'] == defects and not current['invalidated']:
            kept += 1; continue
        image_truth.declare_truth(project, sample['image_path'], task=task, classes=classes, verdict=verdict, defect_classes=defects,
                                  reviewer=reviewer, expected_revision=current['truth_revision'],
                                  expected_image_revision=current['image_revision'], note=note, class_roles=roles)
        declared += 1
    return {'cohort_id': cohort_id, 'total': len(cohort['samples']), 'declared': declared, 'kept': kept,
            'skipped': len(unknown), 'unknown': unknown[:50]}

