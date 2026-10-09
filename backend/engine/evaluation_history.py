"""Immutable evaluation evidence and durable comparison execution state."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
from backend.engine.sqlite_wal import use_wal  # a concurrent WAL switch is retried, not failed
from backend.engine.sqlite_schema import add_missing_columns  # two openers never add a column twice
import time
import uuid
from pathlib import Path
from collections import defaultdict


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def binary_verdict(label, roles=None):
    """Normalize known manufacturing truth with recorded class roles; absent or review truth remains unknown."""
    if label is None or not str(label).strip() or str(label).strip().casefold() in ('review', 'unknown'):
        return None
    from backend.engine.class_semantics import class_role
    return {'normal': 'OK', 'defect': 'NG'}.get(class_role(label, roles))


def grouped_errors(rows, task=None, roles=None):
    """Group errors; class labels use the evaluated model's roles, binary flow verdicts never do."""
    groups = {name: defaultdict(lambda: {'samples': 0, 'errors': 0, 'misses': 0, 'overkill': 0, 'unknown_truth': 0})
              for name in ('product', 'lot', 'ground_truth')}
    for row in rows:
        truth = row.get('ground_truth', row.get('ground_truth_verdict'))
        prediction = row.get('prediction', row.get('predicted_class', row.get('predicted_label')))
        binary_prediction = False
        if isinstance(row.get('candidate'), dict):
            prediction = row['candidate'].get('verdict')
            binary_prediction = True
        if prediction is None:
            prediction = row.get('final_verdict')
            binary_prediction = 'final_verdict' in row
        candidate = row.get('candidate') if isinstance(row.get('candidate'), dict) else {}
        explicit_error = row.get('error') or candidate.get('error')
        binary_task = task in (None, 'classification', 'patch_classification', 'detection', 'segmentation', 'anomaly')
        # Flow comparisons already contain reviewed binary truth. It takes precedence
        # over the descriptive class label, including explicit unknown truth.
        flow_verdict = isinstance(row.get('candidate'), dict) or 'final_verdict' in row
        if 'ground_truth_verdict' in row:
            truth_binary = binary_verdict(row.get('ground_truth_verdict')) if binary_task else None
        else:
            truth_binary = binary_verdict(truth, roles) if binary_task else None
        prediction_binary = binary_verdict(prediction, None if binary_prediction else roles) if binary_task else None
        unknown = truth_binary is None if binary_task and flow_verdict else truth in (None, '')
        if flow_verdict and binary_task:
            mismatch = not unknown and prediction_binary != truth_binary
        elif isinstance(row.get('is_correct'), bool):
            mismatch = not unknown and not row['is_correct']
        else:
            mismatch = not unknown and prediction != truth
        error = bool(explicit_error) or mismatch
        for name in groups:
            value = truth if name == 'ground_truth' else row.get(name)
            # Missing metadata is visible as unassigned; never infer product/lot from folders.
            key = str(value) if value not in (None, '') else '(unassigned)'
            bucket = groups[name][key]
            bucket['samples'] += 1
            bucket['errors'] += int(error)
            bucket['unknown_truth'] += int(unknown)
            bucket['misses'] += int(truth_binary == 'NG' and prediction_binary == 'OK')
            bucket['overkill'] += int(truth_binary == 'OK' and prediction_binary == 'NG')
    return {key: dict(value) for key, value in groups.items()}


def _result_roles(result):
    semantics = result.get('class_semantics') if isinstance(result, dict) else None
    roles = semantics.get('roles') if isinstance(semantics, dict) else None
    return roles if isinstance(roles, dict) else None


class EvaluationHistory:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        if self.directory.is_symlink():
            raise ValueError('Evaluation history must not be a symbolic link')
        self.directory.mkdir(parents=True, exist_ok=True)

    def append(self, result, binding):
        evidence = {'evaluation_id': 'evaluation_' + uuid.uuid4().hex, 'created_at': time.time(),
                    'binding': binding, 'result': result,
                    'grouped_errors': grouped_errors(result.get('test_predictions', []), result.get('task', binding.get('task')),
                                                     _result_roles(result))}
        record = {**evidence, 'evidence_sha256': hashlib.sha256(canonical(evidence)).hexdigest()}
        path = self.directory / (record['evaluation_id'] + '.json')
        with path.open('xb') as writer:
            writer.write(canonical(record)); writer.flush(); os.fsync(writer.fileno())
        return record

    def get(self, evaluation_id):
        if not isinstance(evaluation_id, str) or len(evaluation_id) != 43 or not evaluation_id.startswith('evaluation_') or any(c not in '0123456789abcdef' for c in evaluation_id[11:]):
            raise ValueError('Invalid evaluation ID')
        path = self.directory / (evaluation_id + '.json')
        if path.is_symlink():
            raise ValueError('Evaluation integrity failure')
        record = json.loads(path.read_text(encoding='utf-8'))
        evidence = {k: v for k, v in record.items() if k != 'evidence_sha256'}
        if record.get('evaluation_id') != evaluation_id or hashlib.sha256(canonical(evidence)).hexdigest() != record.get('evidence_sha256'):
            raise ValueError('Evaluation integrity failure')
        return record

    def list(self, job_id=None, labelset_id=None):
        records = [self.get(path.stem) for path in self.directory.glob('evaluation_*.json')]
        return sorted((row for row in records if (job_id is None or row['result'].get('job_id') == job_id)
                       and (labelset_id is None or row['binding'].get('labelset_id','default')==labelset_id)), key=lambda row: row['created_at'], reverse=True)


def _evaluation_training_provenance(project_root, metadata):
    """Record coherent owned lineage; downstream evidence still verifies its files.

    This only reads original receipts. It never creates a training snapshot,
    rebinds historical records, or declares runtime/model-quality approval.
    """
    binding = metadata.get('training_provenance')
    if not isinstance(binding, dict) or not binding:
        return None
    try:
        # Evaluation evidence must stay detached from mutable caller metadata.
        recorded = json.loads(json.dumps(binding, ensure_ascii=False, allow_nan=False))
        requested = Path(project_root)
        if requested.is_symlink():
            return None
        root = requested.resolve()
        identifier = recorded['dataset_version_id']
        if (not isinstance(identifier, str) or not identifier.startswith('v_')
                or Path(identifier).name != identifier or identifier in ('.', '..')):
            return None
        version = root / 'versions' / identifier
        if ((root / 'versions').is_symlink() or version.is_symlink()
                or not isinstance(recorded['version_dir'], str)
                or recorded['version_dir'] != str(version.resolve())):
            return None

        def read_receipt(path):
            if path.is_symlink() or not path.is_file():
                raise ValueError('Evaluation lineage receipt is unavailable or linked')
            return json.loads(path.read_text(encoding='utf-8'))

        project = read_receipt(root / 'project.json')
        manifest = read_receipt(version / 'manifest.json')
        if not isinstance(project, dict) or not isinstance(manifest, dict):
            return None
        def sha256(value):
            return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)

        source = manifest['source_dataset_dir']
        fingerprint = recorded['dataset_fingerprint']
        labelset = recorded['labelset_id']
        if (type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1
                or not isinstance(project['id'], str) or not project['id']
                or not isinstance(source, str) or not Path(source).is_absolute()
                or str(Path(source).resolve()) != source
                or not isinstance(fingerprint, str) or not fingerprint.startswith('v1:') or not sha256(fingerprint[3:])
                or not isinstance(labelset, str) or not (labelset == 'default' or
                    (labelset.startswith('ls_') and len(labelset) == 15 and all(c in '0123456789abcdef' for c in labelset[3:])))):
            return None
        unsigned = {key: value for key, value in manifest.items() if key != 'content_digest'}
        digest = hashlib.sha256(canonical(unsigned)).hexdigest()
        if (manifest['id'] != identifier or manifest['project_id'] != project['id']
                or manifest['source_dataset_dir'] != project['source_dataset_dir']
                or manifest.get('labelset_id', 'default') != recorded['labelset_id']
                or manifest['dataset_fingerprint'] != recorded['dataset_fingerprint']
                or manifest['content_digest'] != digest or recorded['manifest_sha256'] != digest):
            return None
        rows = manifest['files']
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return None
        for row in rows:
            relative = row['relative_path']
            if (row['origin'] not in ('source', 'studio', 'studio_scoped', 'split')
                    or not isinstance(relative, str) or not relative or Path(relative).is_absolute()
                    or '..' in Path(relative).parts or not sha256(row['sha256'])
                    or type(row['size_bytes']) is not int or row['size_bytes'] < 0):
                return None
        split = [row['sha256'] for row in rows if row['origin'] == 'split']
        split_digest = split[0] if len(split) == 1 else hashlib.sha256(json.dumps(
            [{key: row[key] for key in ('origin', 'relative_path', 'sha256')} for row in rows],
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if (recorded['split_sha256'] != split_digest
                or recorded['split_binding'] != ('saved_manifest' if split else 'versioned_dataset_layout')):
            return None
        if 'team_data' in recorded:
            team = recorded['team_data']
            if not isinstance(team, dict) or not team:
                return None
            scope = team['scope']
            if (not isinstance(scope, dict) or scope.get('project_id') != project['id']
                    or scope.get('source') != manifest['source_dataset_dir']
                    or scope.get('labelset_id') != recorded['labelset_id']
                    or read_receipt(version / 'team-data.json') != team
                    or recorded['team_data_sha256'] != hashlib.sha256(canonical(team)).hexdigest()):
                return None
        return recorded
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        # Legacy or malformed lineage remains absent, so strict consumers report
        # the existing pending gap rather than accept a reconstructed binding.
        return None


def evaluation_model_context(project_root,metadata):
    from backend.engine.project_labelsets import load_labelsets
    thresholds={key:metadata[key] for key in ('optimal_threshold','threshold','probability_threshold','size_threshold','min_defect_area_px') if key in metadata}
    continuation=metadata.get('continuation') or metadata.get('warm_start') or {}
    parent=metadata.get('parent_job_id') or (continuation.get('parent_job_id') if isinstance(continuation,dict) else None)
    training = metadata.get('training_provenance')
    context = {'labelset_id':load_labelsets(Path(project_root))['active_id'],
               'training_labelset_id':training.get('labelset_id') if isinstance(training,dict) else None,
               'parent_job_id':parent,'threshold_settings':thresholds}
    lineage = _evaluation_training_provenance(project_root,metadata)
    if lineage is not None:
        context['training_provenance'] = lineage
    return context


class ComparisonJobs:
    def __init__(self, path, *, recover=True, read_only=False):
        self.path = Path(path); self.read_only = read_only
        if read_only: return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, total_images INTEGER DEFAULT 0, completed_images INTEGER DEFAULT 0, cancel_requested INTEGER DEFAULT 0, report_id TEXT, error TEXT, created_at REAL, updated_at REAL)')
            add_missing_columns(conn, 'jobs', {'owner_pid': 'INTEGER', 'owner_created_at': 'REAL', 'owner_command_sha256': 'TEXT'})
        if recover: self.recover()
    def connect(self):
        if self.read_only:
            conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            return conn
        conn = sqlite3.connect(self.path, timeout=10); conn.row_factory = sqlite3.Row
        use_wal(conn); return conn
    def create(self, payload):
        identifier = 'comparejob_' + uuid.uuid4().hex
        import psutil
        owner=psutil.Process(os.getpid())
        command_hash=__import__('hashlib').sha256(json.dumps(owner.cmdline()).encode()).hexdigest()
        with self.connect() as conn:
            conn.execute('INSERT INTO jobs(job_id,payload,status,created_at,updated_at,owner_pid,owner_created_at,owner_command_sha256) VALUES(?,?,?,?,?,?,?,?)', (identifier, json.dumps(payload), 'queued', time.time(), time.time(), os.getpid(),owner.create_time(),command_hash))
        return self.get(identifier)
    def get(self, identifier):
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM jobs WHERE job_id=?', (identifier,)).fetchone()
        if row is None: raise KeyError(identifier)
        result = dict(row); result['payload'] = json.loads(result['payload']); return result
    def list(self):
        with self.connect() as conn:
            ids = [row[0] for row in conn.execute('SELECT job_id FROM jobs ORDER BY created_at DESC')]
        return [self.get(identifier) for identifier in ids]
    def start(self, identifier, total):
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET status='running',total_images=?,updated_at=? WHERE job_id=? AND status='queued'", (total,time.time(),identifier))
    def progress(self, identifier, completed):
        with self.connect() as conn:
            conn.execute('UPDATE jobs SET completed_images=?,updated_at=? WHERE job_id=?', (completed,time.time(),identifier))
    def cancel(self, identifier):
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET cancel_requested=1, status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,updated_at=? WHERE job_id=? AND status IN ('queued','running')", (time.time(),identifier))
    def cancelled(self, identifier): return bool(self.get(identifier)['cancel_requested'])
    def finish(self, identifier, status, report_id=None, error=None):
        with self.connect() as conn:
            conn.execute('UPDATE jobs SET status=?,report_id=?,error=?,updated_at=? WHERE job_id=?', (status,report_id,error,time.time(),identifier))
    def recover(self):
        with self.connect() as conn:
            import psutil
            for row in conn.execute("SELECT job_id,owner_pid,owner_created_at,owner_command_sha256 FROM jobs WHERE status IN ('running','queued')"):
                try:
                    if row['owner_pid'] is not None:
                        owner=psutil.Process(row['owner_pid'])
                        command_hash=__import__('hashlib').sha256(json.dumps(owner.cmdline()).encode()).hexdigest()
                        if (row['owner_created_at'] is not None and abs(owner.create_time()-row['owner_created_at'])<.1
                                and command_hash==row['owner_command_sha256']):continue
                except psutil.Error:pass
                conn.execute("UPDATE jobs SET status='interrupted', error='Application stopped before completion; rerun the same bound models.',updated_at=? WHERE job_id=?", (time.time(),row['job_id']))


def archive_specialized_evaluation(project, checkpoint, source, result, *, task=None, dataset_path=None):
    """Archive actual family metrics without inventing OK/NG ground truth."""
    from backend.api.routes_model_comparisons import _fingerprint, _sha256
    from backend.engine.dataset_metadata import metadata_for_path
    from backend.engine.annotation_storage import scoped_annotation_root
    from backend.api import routes_dataset
    checkpoint=Path(checkpoint);source=Path(source).resolve();dataset=Path(dataset_path or source).resolve()
    task=task or result.get('task')
    if task not in ('ocr','rotated_detection','enhancement','defect_gan','rotation'):raise ValueError('Unsupported specialized evaluation task')
    if checkpoint.is_symlink() or not checkpoint.is_file() or not checkpoint.resolve().is_relative_to(Path(project['models_dir']).resolve()):raise ValueError('Evaluation checkpoint is outside the active project')
    model_hash=_sha256(checkpoint)
    reported_hash=result.get('model_sha256',result.get('checkpoint_sha256'))
    if reported_hash is not None and reported_hash!=model_hash:raise ValueError('Evaluated model hash changed before archival')
    payload=json.loads(json.dumps(result));payload.update(job_id=checkpoint.parent.name,task=task)
    provenance={}
    if task in ('ocr','rotated_detection','enhancement','rotation','defect_gan'):
        from backend.engine.specialized_models import specialized_dataset_provenance
        provenance=specialized_dataset_provenance(task,dataset)
        verify_specialized_evaluation_inputs(project,source,task,{'family_dataset_sha256':provenance['dataset_sha256'],'evaluation_dataset_path':str(dataset)})
        if payload.get('dataset_sha256') and provenance['dataset_sha256']!=payload['dataset_sha256']:
            raise ValueError('Evaluated family dataset changed before archival')
    def original_image(relative):
        from pathlib import PurePosixPath
        relative=PurePosixPath(relative)
        if relative.is_absolute() or not relative.parts or '..' in relative.parts:raise ValueError('Evaluation sample must use a relative image path')
        image=dataset/relative
        if image.is_symlink() or not image.is_file() or not image.resolve().is_relative_to(dataset):raise ValueError('Evaluation sample escaped its prepared dataset')
        mapped=provenance.get('source_map',{}).get(str(relative))
        original=source/mapped['source_relative_path'] if mapped else image
        if original.is_symlink() or not original.is_file() or not original.resolve().is_relative_to(source):raise ValueError('Evaluation sample escaped original source')
        if mapped and (_sha256(image)!=mapped['source_sha256'] or _sha256(original)!=mapped['source_sha256']):raise ValueError('Evaluation copied image differs from its original')
        return original,image
    predictions=[]
    if task=='ocr':
        for sample in payload.get('samples',[]):
            relative=sample.get('image')
            if not isinstance(relative,str):continue
            image,evaluation_image=original_image(relative)
            metadata=metadata_for_path(Path(project['project_dir']),source,image,scoped_annotation_root(routes_dataset.STUDIO_ANNOTATIONS_DIR))
            from backend.engine.evaluation_evidence import character_errors
            sample['character_evidence']=character_errors(sample.get('reference_text',''),sample.get('predicted_text',''))
            predictions.append({**sample,**{key:metadata.get(key) for key in ('image_uuid','file_path','content_hash','content_version','revision','product','lot','group','tags')},
                                'evaluation_file_path':str(evaluation_image),'ground_truth':sample.get('reference_text'),'predicted_class':sample.get('predicted_text')})
    else:
        # Aggregated geometry/reconstruction/generator diagnostics have no class verdicts.
        # Preserve their metrics as returned; grouping requires real per-image outcomes.
        predictions=payload.get('test_predictions',[])
        if provenance.get('source_map'):
            for sample in predictions:
                relative=sample.get('image')
                if not relative and sample.get('file_path'):
                    try:relative=str(Path(sample['file_path']).resolve().relative_to(dataset))
                    except ValueError:raise ValueError('Evaluation sample escaped its prepared dataset')
                if relative:
                    original,copied=original_image(relative)
                    metadata=metadata_for_path(Path(project['project_dir']),source,original,scoped_annotation_root(routes_dataset.STUDIO_ANNOTATIONS_DIR))
                    sample.update({key:metadata.get(key) for key in ('image_uuid','file_path','content_hash','content_version','revision','product','lot','group','tags')})
                    sample['evaluation_file_path']=str(copied)
    payload['test_predictions']=predictions
    from backend.engine.evaluation_evidence import evaluation_analysis
    payload['analysis']=evaluation_analysis(predictions,task)
    metadata_path=checkpoint.parent/'model_meta.json'
    metadata=json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.is_file() else {}
    binding={'source_dataset_path':str(source),'evaluation_dataset_path':str(dataset),
             'dataset_fingerprint':_fingerprint(source),'checkpoint_sha256':_sha256(checkpoint),
             'family_dataset_sha256':payload.get('dataset_sha256',payload.get('manifest_sha256')),
             'training_provenance':metadata.get('training_provenance'),
             'training_version_verified':bool(metadata.get('training_provenance'))}
    binding.update(evaluation_model_context(project['project_dir'],metadata))
    record=EvaluationHistory(Path(project['project_dir'])/'reports'/'evaluations').append(payload,binding)
    return {**payload,'evaluation_id':record['evaluation_id'],'binding':binding,'grouped_errors':record['grouped_errors']}


def verify_specialized_evaluation_inputs(project,source,task,binding):
    """Check the evaluated labels/pairs now, separately from historical training."""
    from backend.engine.specialized_models import specialized_dataset_provenance
    expected=binding.get('family_dataset_sha256')
    requested=binding.get('evaluation_dataset_path')
    if not isinstance(expected,str) or not expected or not isinstance(requested,str):
        raise ValueError('Specialized evaluation input identity is missing')
    dataset=Path(requested)
    if dataset.is_symlink():raise ValueError('Specialized evaluation dataset is linked')
    dataset=dataset.resolve()
    if dataset!=Path(source).resolve():
        owned=Path(project['dataset_dir'])
        if owned.is_symlink() or not dataset.is_relative_to(owned.resolve()):
            raise ValueError('Evaluated family inputs belong to another project')
    provenance=specialized_dataset_provenance(task,dataset)
    if provenance['dataset_sha256']!=expected:
        raise ValueError('Evaluated family inputs or labels changed after immutable evaluation')
    if dataset!=Path(source).resolve() and Path(provenance.get('source_dataset_path','')).resolve()!=Path(source).resolve():
        raise ValueError('Evaluated family inputs belong to another source')
