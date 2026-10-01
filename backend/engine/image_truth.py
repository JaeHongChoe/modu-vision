"""Explicit reviewer truth bound to source bytes, labels and a task vocabulary.

Workflow approval and empty labels never supply truth. Declarations are append
only inside the existing project/labelset metadata transaction; a changed
binding makes the current effective truth UNKNOWN while retaining its history.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from backend.engine import dataset_metadata as dm
from backend.engine.class_semantics import class_role, class_semantics_record, recorded_roles


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def truth_scope(project, task, classes, class_roles=None):
    if not isinstance(task, str) or not task.strip() or len(task) > 100:
        raise ValueError('Truth requires an explicit task')
    if (not isinstance(classes, list) or not classes or len(classes) > 255
            or any(not isinstance(name, str) or not name.strip() or len(name) > 120 for name in classes)
            or len(set(classes)) != len(classes)):
        raise ValueError('Truth requires unique, nonempty class names')
    source = project.get('source_dataset_dir')
    if not source or not Path(source).is_dir():
        raise ValueError('Select a project source before declaring truth')
    if class_roles is not None:
        recorded_roles({'class_semantics': {'version':1, 'roles':class_roles}})
        if set(class_roles)-set(classes): raise ValueError('Class roles must belong to the truth class scope')
    return {'project_id': project['id'], 'source_dataset_path': str(Path(source).resolve()),
            'labelset_id': project.get('active_labelset_id', 'default'),
            'task': task.strip(), 'classes': list(classes), 'class_semantics': class_semantics_record(classes,class_roles,task=task)}


def image_binding(row):
    binding = {key: row.get(key) for key in ('image_uuid', 'content_hash', 'content_version', 'annotation_hash', 'mask_hash')}
    # An intentional relabel remains a new label revision even if bytes later
    # match the old file. Metadata tags, approval and truth declarations are
    # independent events and do not invalidate otherwise unchanged labels.
    label_actions = {'annotation_changed','external_annotation_changed','source_changed','version_restored'}
    binding['label_revision'] = max((event.get('revision',0) for event in row.get('audit',[]) if event.get('action') in label_actions),default=0)
    return binding


def _safe_image(project, image_path):
    source, image = dm._visible_path(project['source_dataset_dir'], image_path)
    if image.is_symlink() or not image.resolve().is_relative_to(source):
        raise ValueError('Truth image must belong to the current project source')
    return image


def _semantic_scope(scope):
    identity=copy.deepcopy(scope)
    # How a role was resolved is provenance, not a different meaning of truth.
    # Keep task, ordered vocabulary, role values and resolver version bound.
    identity.get('class_semantics',{}).pop('basis',None)
    return identity


def _truth_history(row,scope):
    expected=digest(_semantic_scope(scope))
    history=[record for records in row.get('explicit_truth',{}).values() for record in records
             if digest(_semantic_scope(record['scope']))==expected]
    return sorted(history,key=lambda record:(record.get('semantic_revision',0),record['declared_at'],record['revision'],record['sha256']))


def _mixed_participating_tasks(scope, values):
    if scope['task'] != 'mixed' or values is None:
        return []
    if (not isinstance(values, (list, tuple, set)) or len(values) < 2
            or any(not isinstance(task, str) or not task.strip() or task == 'mixed' for task in values)):
        raise ValueError('Mixed truth needs its participating model tasks')
    participants = sorted(set(values))
    if len(participants) < 2:
        raise ValueError('Mixed truth needs different participating model tasks')
    return participants


def _read_row(row, scope, participating_tasks=None):
    history = _truth_history(row,scope)
    family=('project_id','source_dataset_path','labelset_id','task')
    prior=[record['sha256'] for records in row.get('explicit_truth',{}).values() for record in records
           if all(record['scope'].get(key)==scope.get(key) for key in family)] if not history else []
    participants = _mixed_participating_tasks(scope, participating_tasks)
    dependencies = []
    dependency_records = []
    if participants:
        dependency_records = [record for records in row.get('explicit_truth',{}).values() for record in records
                              if record['scope'].get('task') in participants
                              and all(record['scope'].get(key)==scope.get(key) for key in family[:-1])]
        dependencies = [record['sha256'] for record in dependency_records]
        # A different task's declaration supplies no OK/NG truth here. Its
        # presence prevents descriptive labels from replacing human review.
        if not history: prior.extend(dependencies)
    record = copy.deepcopy(history[-1]) if history else None
    # label_revision on a declaration is the monotonic metadata row revision
    # captured before its audit event. Task-local revisions and wall clocks
    # cannot establish ordering between different truth scopes.
    mixed_revision = record.get('label_revision') if record else None
    newer_task_review = bool(record and dependency_records and (
        type(mixed_revision) is not int or any(type(review.get('label_revision')) is not int
        or review['label_revision'] > mixed_revision for review in dependency_records)))
    invalidated = bool(record and (record['binding'] != image_binding(row) or newer_task_review))
    result = {'image_path': row['file_path'], 'relative_path': row['relative_path'], 'image_uuid': row['image_uuid'],
              'image_revision': row['revision'], 'truth_revision': len(history), 'scope': scope,
              'binding': image_binding(row), 'verdict': 'UNKNOWN', 'defect_classes': [],
              'reviewer': None, 'invalidated': invalidated,
              'unknown_reason': 'source_or_labels_changed' if invalidated else 'explicit_truth_missing',
              'has_prior_scoped_declarations': bool(prior),
              'prior_scoped_declarations_sha256': digest(sorted(prior)) if prior else None,
              'declaration': record}
    if prior:result['unknown_reason']='task_class_scope_changed'
    if newer_task_review:result['unknown_reason']='participating_task_review_changed'
    if record and not invalidated:
        result.update(verdict=record['verdict'], defect_classes=record['defect_classes'], reviewer=record['reviewer'],
                      unknown_reason='explicit_unknown' if record['verdict'] == 'UNKNOWN' else None)
    # Only binding and declaration identity affect evidence; metadata tags and approval do not.
    evidence={key: result[key] for key in ('scope','binding','truth_revision','verdict','defect_classes','invalidated','declaration')}
    if prior:evidence['prior_scoped_declarations_sha256']=result['prior_scoped_declarations_sha256']
    if participants:
        # Keep these dependencies even when compatible mixed truth takes
        # precedence, so a concurrent task review invalidates the execution.
        result.update(participating_tasks=participants,
                      participating_declarations_sha256=digest(sorted(dependencies)))
        evidence.update({key:result[key] for key in ('participating_tasks','participating_declarations_sha256')})
    result['truth_sha256'] = digest(evidence)
    if participants:
        # View context is already hashed as a separate dependency above. Keep
        # stored declaration/semantic scopes unchanged for existing evidence.
        result['scope'] = {**scope, 'participating_tasks': participants}
    return result


def read_truth(project, image_path, *, task, classes, class_roles=None, participating_tasks=None):
    scope = truth_scope(project, task, classes, class_roles); image = _safe_image(project, image_path)
    with dm.metadata_transaction(project['project_dir'], project['source_dataset_dir'], project['annotations_dir']) as ledger:
        row = dm._ensure(ledger, project['project_dir'], project['source_dataset_dir'], image, project['annotations_dir'])
        return _read_row(row, scope, participating_tasks)


def declare_truth(project, image_path, *, task, classes, verdict, reviewer, expected_revision,
                  expected_image_revision, defect_classes=None, note='', class_roles=None, participating_tasks=None):
    scope = truth_scope(project, task, classes, class_roles); image = _safe_image(project, image_path)
    _mixed_participating_tasks(scope, participating_tasks)
    if verdict not in {'OK','NG','UNKNOWN'}:
        raise ValueError('Truth verdict must be OK, NG or UNKNOWN')
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer.strip()) > 100:
        raise ValueError('Truth requires a reviewer name')
    defects = defect_classes or []
    if (not isinstance(defects, list) or any(name not in classes for name in defects)
            or len(set(defects)) != len(defects) or (verdict == 'NG' and not defects)
            or (verdict != 'NG' and defects)):
        raise ValueError('NG truth requires defect classes from this class scope; OK/UNKNOWN have none')
    if any(scope['class_semantics']['roles'].get(name) != 'defect' for name in defects):
        raise ValueError('NG truth cannot name a normal or unknown class')
    if not isinstance(note, str) or len(note) > 2000:
        raise ValueError('Truth note must be under 2000 characters')
    with dm.metadata_transaction(project['project_dir'], project['source_dataset_dir'], project['annotations_dir']) as ledger:
        row = dm._ensure(ledger, project['project_dir'], project['source_dataset_dir'], image, project['annotations_dir'])
        history = _truth_history(row,scope)
        if row['revision'] != expected_image_revision or len(history) != expected_revision:
            raise dm.RevisionConflict(_read_row(row, scope, participating_tasks))
        record = {'revision': len(history) + 1, 'semantic_revision': len(history) + 1, 'scope': scope, 'binding': image_binding(row),
                  'label_revision': row['revision'], 'verdict': verdict, 'defect_classes': list(defects),
                  'reviewer': reviewer.strip(), 'note': note.strip(), 'declared_at': dm._now()}
        record['sha256'] = digest(record)
        row.setdefault('explicit_truth', {}).setdefault(digest(scope), []).append(record)
        dm._event(row, reviewer.strip(), 'explicit_truth_declared', {'truth_revision': record['revision'], 'truth_sha256': record['sha256']})
        return _read_row(row, scope, participating_tasks)
