"""Narrow ancestor reuse for registered owned intake branches.

This verifies one frozen test cohort and original truth scope. It does not alias
normal truth, mutable paths, unrelated sources, or unbound legacy checkpoints.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path

import torch
from backend.engine import capture_intake as intake, dataset_metadata as dm
from backend.engine.image_truth import image_binding


def project_for_source(models_dir, source):
    root=Path(models_dir).resolve().parent;path=root/'project.json'
    if path.is_symlink() or not path.is_file():raise ValueError('Intake ancestor source reuse requires a saved owning project')
    project=json.loads(path.read_text())
    if (Path(project.get('project_dir','')).resolve()!=root or Path(project.get('models_dir','')).resolve()!=root/'models'
            or Path(project.get('source_dataset_dir','')).resolve()!=Path(source).resolve()):
        raise ValueError('Intake ancestor source is not the active owning project source')
    return project


def _chain(project, source, ancestor):
    source=Path(source).resolve();ancestor=Path(ancestor).resolve();rows=[];seen=set()
    while source!=ancestor:
        if len(rows)>=16 or source in seen:raise ValueError('Intake ancestor chain is cyclic or too deep')
        seen.add(source);root=intake._root(project)
        if source.name!='source' or source.parent.parent.resolve()!=(root/'versions').resolve():
            raise ValueError('Parent source is not a registered intake ancestor')
        identifier=source.parent.name
        if identifier not in intake._index(project)['versions']:raise ValueError('Intake ancestor version is not registered')
        record=intake.read_version(project,identifier)
        if (Path(record['source_dataset_path']).resolve()!=source or record['scope']['task']!=project['task']
                or record['scope']['labelset_id']!=project.get('active_labelset_id','default')):
            raise ValueError('Intake ancestor task/labelset/source scope changed')
        if not record.get('copied_label_bindings'):raise ValueError('Intake ancestor copied-label revision binding is missing')
        branch={**project,'source_dataset_dir':str(source)};_,split=intake._split(branch)
        if split['assignments']!=record['split_assignments']:raise ValueError('Intake held-out split changed')
        parent=Path(record['parent_source_dataset_path'])
        if parent.is_symlink() or not parent.is_dir():raise ValueError('Original intake ancestor source is unavailable or linked')
        parent_project={**project,'source_dataset_dir':str(parent)};_,original_split=intake._split(parent_project)
        if dm._hash(intake._split(parent_project)[0])!=record['parent_source_binding']['split_sha256']:
            raise ValueError('Original ancestor held-out split changed')
        expected=dict(original_split['assignments'])
        for adopted in record['adopted']:
            relative=adopted['relative_path']
            if relative in expected or record['split_assignments'].get(relative)!='train' or adopted['truth_verdict']!='UNKNOWN':
                raise ValueError('Adopted captures must remain new train-only unknown samples')
            if not adopted.get('review') or adopted['review'].get('decision')!='adopt':raise ValueError('Intake ancestor capture review is missing')
            expected[relative]='train'
        if expected!=record['split_assignments']:raise ValueError('Intake split has unregistered images or partitions')
        if intake._files(parent_project)!=record['base_files']:raise ValueError('Original ancestor source or source labels changed')
        actual={row['relative_path']:row['sha256'] for row in intake._files(branch)}
        expected_files={row['relative_path']:row['sha256'] for row in record['base_files']}
        expected_files.update({row['relative_path']:row['source_sha256'] for row in record['adopted']})
        if actual!=expected_files:raise ValueError('Owned intake source inventory changed')
        bindings={row['relative_path']:row['binding'] for row in record['copied_label_bindings']}
        for relative,part in original_split['assignments'].items():
            if part!='test':continue
            if relative not in bindings:raise ValueError('Intake test label binding is missing')
            current=dm.metadata_for_path(project['project_dir'],source,source/relative,project['annotations_dir'])
            if image_binding(current)!=bindings[relative]:raise ValueError('Intake test label, mask, source or revision changed')
        rows.append(record);source=parent.resolve()
    if not rows:raise ValueError('Requested model is not an intake ancestor')
    return rows


def _frozen_images(project, source, ancestor, records, task, classes, roles):
    from backend.engine.flow_evaluation import read_cohort,_cohort_changes,_root
    references=records[0].get('fixed_cohorts',[])
    errors=[]
    for reference in references:
        scope=reference.get('scope',{})
        if scope.get('task')!=task or scope.get('classes')!=classes:
            continue
        try:
            truth_source=Path(scope['source_dataset_path']).resolve()
            evidence_chain=records+(_chain(project,ancestor,truth_source) if truth_source!=ancestor else [])
            cohort=read_cohort(project,reference['cohort_id'])
            if cohort['scope']['class_semantics']['roles']!=roles:raise ValueError('Frozen ancestor cohort class roles changed')
            if any(cohort[key]!=reference[ref] for key,ref in [('record_sha256','cohort_sha256'),('input_sha256','input_sha256'),('truth_sha256','truth_sha256')]):
                raise ValueError('Frozen intake cohort reference changed')
            if _cohort_changes({**project,'source_dataset_dir':str(truth_source)},cohort):
                raise ValueError('Frozen ancestor cohort input or explicit truth changed')
            rows=[]
            for sample in cohort['samples']:
                relative=sample['relative_path'];image=source/relative
                if any(row['split_assignments'].get(relative)!='test' for row in evidence_chain):raise ValueError('Frozen test sample moved out of test')
                if image.is_symlink() or dm._hash(image)!=sample['input_sha256']:raise ValueError('Copied fixed test pixels changed')
                truth=sample['truth']
                frozen=intake._owned(project,_root(project)/'cohorts'/cohort['cohort_id']/sample['frozen_image'])
                rows.append({'image_id':image.stem,'file_name':image.name,'file_path':str(image),
                    'evaluation_file_path':str(frozen),'image_sha256':sample['input_sha256'],
                    'ground_truth_label':None,'ground_truth_verdict':truth['verdict'] if truth['verdict'] in ('OK','NG') else None,
                    'truth_sha256':truth['truth_sha256'],'truth_scope':copy.deepcopy(cohort['scope']),
                    'ancestor_image_path':sample['image_path']})
            return {'cohort_id':cohort['cohort_id'],'cohort_sha256':cohort['record_sha256'],'input_sha256':cohort['input_sha256'],
                'truth_sha256':cohort['truth_sha256'],'ancestor_source_dataset_path':str(ancestor),
                'truth_source_dataset_path':str(truth_source),
                'classes':classes,'class_roles':roles,
                'version_ids':[row['version_id'] for row in evidence_chain],'version_sha256':[row['record_sha256'] for row in evidence_chain],'images':rows}
        except (ValueError,OSError,KeyError) as exc:errors.append(str(exc))
    raise ValueError('An unchanged frozen held-out cohort with the exact ancestor task/classes/truth is required'+(': '+ '; '.join(errors[:3]) if errors else ''))


def _bound_model(project, checkpoint, task):
    checkpoint=intake._owned(project,checkpoint);job=checkpoint.parent
    if checkpoint.is_symlink() or job.is_symlink() or job.parent.resolve()!=Path(project['models_dir']).resolve():
        raise ValueError('Intake ancestor checkpoint leaves its owning model store')
    from backend.engine.checkpoint_paths import completed_job_receipt
    receipt=completed_job_receipt(job)
    metadata_path=intake._owned(project,job/'model_meta.json')
    if not receipt or not metadata_path.is_file():raise ValueError('Intake ancestor completed model receipt/metadata missing')
    metadata=json.loads(metadata_path.read_text())
    try:payload=torch.load(checkpoint,map_location='cpu',weights_only=True)
    except Exception as exc:raise ValueError('Intake ancestor checkpoint is unreadable') from exc
    checksum=dm._hash(checkpoint);classes=metadata.get('classes') or metadata.get('class_names')
    if (not isinstance(classes,list) or not classes or not isinstance(payload,dict)
            or payload.get('task')!=task or metadata.get('task')!=task or receipt.get('task')!=task
            or (payload.get('classes') or payload.get('class_names'))!=classes
            or receipt.get('job_id')!=job.name or not payload.get('model_state_dict')):
        raise ValueError('Intake ancestor model task/classes/checkpoint signature changed')
    if any(value.get('checkpoint_sha256')!=checksum for value in (receipt,metadata)):
        raise ValueError('Intake ancestor checkpoint hash changed or is unpinned')
    binding=payload.get('training_provenance')
    if not isinstance(binding,dict) or binding!=metadata.get('training_provenance') or binding!=receipt.get('training_provenance'):
        raise ValueError('Intake ancestor needs the exact immutable model training data binding')
    if binding.get('dataset_fingerprint')!=receipt.get('dataset_fingerprint') or binding.get('labelset_id','default')!=project.get('active_labelset_id','default'):
        raise ValueError('Intake ancestor dataset/labelset binding changed')
    ancestor=Path(receipt.get('source_dataset_path','')).resolve()
    from backend.engine.specialized_models import _verify_historical_source
    _verify_historical_source(Path(project['models_dir']).resolve(),ancestor,payload,metadata)
    from backend.engine.class_semantics import class_semantics_record,recorded_roles
    roles=class_semantics_record(classes,recorded_roles(metadata,task=task,classes=classes),task=task)['roles']
    if roles!=class_semantics_record(classes,recorded_roles(payload,task=task,classes=classes),task=task)['roles']:
        raise ValueError('Intake model class roles differ between checkpoint and metadata')
    return ancestor,classes,roles


def verify_current_model(project, source, checkpoint, task, cohort):
    ancestor,classes,roles=_bound_model(project,checkpoint,task)
    if ancestor!=Path(source).resolve():raise ValueError('Candidate checkpoint does not belong to the current intake source')
    if classes!=cohort['classes'] or roles!=cohort['class_roles']:raise ValueError('Candidate task, ordered classes or class roles differ from the frozen ancestor cohort')


def verify_ancestor_model(project, source, checkpoint, task):
    """Verify immutable model data plus the exact registered source ancestry."""
    ancestor,classes,roles=_bound_model(project,checkpoint,task)
    records=_chain(project,source,ancestor)
    from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root,set_request_project_root,reset_request_project_root
    from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
    from backend.engine.warm_start import training_classes
    annotation=set_request_annotation_root(Path(project['annotations_dir']));split=set_request_split_root(Path(project['dataset_dir'])/'splits');owner=set_request_project_root(Path(project['project_dir']))
    try:
        if tuple(classes)!=training_classes(task,source):raise ValueError('Intake ancestor ordered classes differ from current training classes')
    finally:reset_request_project_root(owner);reset_request_split_root(split);reset_request_annotation_root(annotation)
    return _frozen_images(project,Path(source).resolve(),ancestor,records,task,classes,roles)
