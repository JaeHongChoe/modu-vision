"""Immutable, scoped advisory rankings from saved full classification evidence.

Only the normalized-margin arithmetic is ported from cleanlab. No inference,
independence assertion, label correction or approval occurs here.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import re
import time
import uuid
from pathlib import Path
import numpy as np
from backend.vendor.cleanlab import normalized_margin
from backend.engine import data_workbench as dw
from backend.engine.evaluation_history import EvaluationHistory
from backend.remote.evaluation_cohort import digest

ALGORITHM={'name':'normalized_margin','contract':'cleanlab-2.9.0-normalized-margin-v1','upstream_version':'2.9.0','implementation':'selected_numpy_source_port'}
LIMITS={'max_samples':50000,'max_classes':1000,'max_probability_values':2000000,'max_queue_items':100}


def _scope(project,source):
    return {'project_id':project['id'],'source':str(Path(source).resolve()),'task':project['task'],'labelset_id':project.get('active_labelset_id','default')}


def analysis_path(project,source,identifier,*,create=False):
    if not isinstance(identifier,str) or not re.fullmatch(r'labelquality_[0-9a-f]{32}',identifier):
        raise ValueError('Invalid label quality analysis ID')
    root=dw._storage(project['project_dir'],source,create=create)/'label_quality'
    if root.is_symlink():raise ValueError('Label quality storage cannot be linked')
    return root/(identifier+'.json')


def _hash(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None


def _record(project,source,identifier):
    record=EvaluationHistory(Path(project['reports_dir'])/'evaluations').get(identifier)
    binding=record['binding'];result=record['result'];common=result.get('common_cohort')
    if project['task']!='classification' or result.get('task')!='classification' or binding.get('task','classification')!='classification':
        raise ValueError('Label quality requires a saved classification evaluation')
    if binding.get('source_dataset_path')!=str(Path(source).resolve()) or binding.get('labelset_id','default')!=project.get('active_labelset_id','default'):
        raise ValueError('Saved evaluation scope changed')
    if (not isinstance(common,dict) or common.get('split')!='test' or not _hash(common.get('cohort_sha256'))
            or binding.get('common_cohort')!=common or result.get('metrics',{}).get('evaluated_split')!='test'
            or result.get('metrics',{}).get('selection_overlap') is not False):
        raise ValueError('Saved common-cohort heldout evidence is required; legacy or validation evaluations are unsupported')
    receipt=result.get('input_receipt')
    if (not isinstance(receipt,dict) or binding.get('input_receipt')!=receipt
            or any(not _hash(receipt.get(key)) for key in ('checkpoint_sha256','metadata_sha256','training_snapshot_sha256','archive_sha256','snapshot_manifest_sha256','version_manifest_sha256'))
            or binding.get('checkpoint_sha256')!=receipt['checkpoint_sha256']
            or not _hash(result.get('evaluation_binding_sha256'))
            or binding.get('evaluation_binding_sha256')!=result['evaluation_binding_sha256']):
        raise ValueError('Saved checkpoint, training version or evaluation binding evidence is missing or changed')
    return record


def _descriptor(project,record):
    from backend.remote.snapshot import verify_snapshot_tree
    common=record['result']['common_cohort'];receipt=record['result']['input_receipt']
    root=Path(project['reports_dir'])/'evaluation_cohorts'/('cohort_'+common['cohort_sha256'])
    if root.is_symlink() or root.parent.is_symlink():raise ValueError('Saved common cohort cannot be linked')
    verify_snapshot_tree(root,receipt['snapshot_manifest_sha256'],allow_archive=True)
    path=root/'data'/'cohort.json'
    if path.is_symlink() or path.stat().st_size>16*1024*1024:raise ValueError('Saved common cohort descriptor is invalid')
    descriptor=json.loads(path.read_text(encoding='utf-8'))
    if digest({k:v for k,v in descriptor.items() if k!='cohort_sha256'})!=common['cohort_sha256'] or descriptor.get('cohort_sha256')!=common['cohort_sha256'] or descriptor.get('task')!='classification' or descriptor.get('split')!='test':
        raise ValueError('Saved common cohort identity changed')
    samples=descriptor['ordered_samples']
    if descriptor.get('truth_sha256')!=digest([r['truth'] for r in samples]) or common.get('image_count')!=len(samples):
        raise ValueError('Saved cohort truth or sample count changed')
    archives=[p for p in root.glob('*.tar.gz') if p.is_file() and not p.is_symlink()]
    if len(archives)!=1 or dw.sha256(archives[0])!=receipt['archive_sha256']:
        raise ValueError('Saved common cohort archive changed')
    return descriptor


def _basis(project,source,record,descriptor,*,historical=False):
    """Hard source identity fences, with historical-only soft label/revision drift."""
    from backend.api.routes_dataset_versions import _read_manifest,_verify,_require_active_labelset
    from backend.engine.dataset_metadata import metadata_for_path,metadata_transaction
    common=record['result']['common_cohort']
    version_dir,manifest=_read_manifest(project,common['dataset_version_id'])
    _require_active_labelset(project,manifest)
    if manifest.get('task')!='classification' or manifest['content_digest']!=record['result']['input_receipt']['version_manifest_sha256']:
        raise ValueError('Saved evaluation version identity changed')
    verification=_verify(project,version_dir,manifest)
    # Historical review permits edits to the current labels, never corruption
    # of the immutable label/split backups that establish the saved version.
    if any(name.startswith('backup/') for name in verification['changed_files']):
        raise ValueError('Saved evaluation version backup changed')
    changed=bool(verification['changed_files'] or verification['editable_changed_files'])
    image_files={'source/'+r['relative_path'] for r in manifest['files'] if r['origin']=='source' and r['kind']=='image'}
    from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
    added_or_changed_pixels=any(name.startswith('source/') and Path(name).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS for name in verification['changed_files'])
    if image_files.intersection(verification['changed_files']) or added_or_changed_pixels:
        raise ValueError('Saved source image bytes changed')
    samples={r['sample_id']:r for r in descriptor['ordered_samples']}
    items=[]
    with metadata_transaction(Path(project['project_dir']),source,Path(project['annotations_dir'])):
        for row in record['result']['test_predictions']:
            sample=samples.get(row.get('sample_id'))
            if sample is None:raise ValueError('Saved evaluation sample identity changed')
            relative=sample['source_relative_path'];image=dw._source_image(source,Path(source)/relative)
            if str(image)!=row.get('file_path') or dw.sha256(image)!=sample['sha256'] or row.get('input_sha256')!=sample['sha256'] or row.get('content_hash')!=sample['sha256']:
                raise ValueError('Saved evaluation source bytes or identity changed')
            current=metadata_for_path(Path(project['project_dir']),source,image,Path(project['annotations_dir']))
            if not row.get('image_uuid') or row['image_uuid']!=current['image_uuid']:
                raise ValueError('Saved evaluation source identity changed')
            if type(row.get('revision')) is not int:raise ValueError('Saved image revision is missing')
            item_changed=current['revision']!=row['revision'];changed=changed or item_changed
            items.append(item_changed)
    if changed and not historical:raise ValueError('Saved label quality basis changed; save a fresh common-cohort evaluation')
    return changed,items


def _rank(record,descriptor):
    classes=descriptor.get('classes');matrix=record['result'].get('confusion_matrix',{})
    if (not isinstance(classes,list) or not 2<=len(classes)<=LIMITS['max_classes']
            or any(not isinstance(c,str) or not c for c in classes) or len(set(classes))!=len(classes)
            or matrix.get('classes',matrix.get('class_names'))!=classes
            or ('class_names' in matrix and matrix['class_names']!=classes)):
        raise ValueError('Full probability class order is missing or changed')
    rows=record['result'].get('test_predictions');samples=descriptor['ordered_samples']
    if not isinstance(rows,list) or not 0<len(rows)<=LIMITS['max_samples'] or len(rows)!=len(samples) or len(rows)*len(classes)>LIMITS['max_probability_values']:
        raise ValueError('Full probability matrix sample count is invalid or exceeds bounds')
    expected={s['sample_id']:s for s in samples};seen=set();paths=set();labels=[];probabilities=[]
    for row in rows:
        sample=expected.get(row.get('sample_id'));scores=row.get('class_scores')
        if sample is None or row['sample_id'] in seen or row.get('file_path') in paths:
            raise ValueError('Full probability matrix contains duplicate or foreign samples')
        seen.add(row['sample_id']);paths.add(row.get('file_path'))
        if row.get('ground_truth')!=sample['truth'] or row.get('truth_sha256')!=digest(sample['truth']) or sample['truth'] not in classes:
            raise ValueError('Saved classification truth binding changed')
        if not isinstance(scores,dict) or set(scores)!=set(classes):
            raise ValueError('Full class probability matrix and strict class order are required')
        values=[scores[name] for name in classes]
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in values) or not math.isclose(sum(values),1.,rel_tol=0,abs_tol=1e-5):
            raise ValueError('Full class probabilities must be finite, in [0,1], and sum to one')
        predicted=classes[int(np.argmax(values))]
        confidence=row.get('confidence')
        if row.get('predicted_class')!=predicted or type(confidence) not in (int,float) or not math.isfinite(confidence) or not math.isclose(confidence,max(values),rel_tol=0,abs_tol=5.1e-5):
            raise ValueError('Stored top1 class or confidence differs from full probabilities')
        if row.get('is_correct') is not (row['ground_truth']==predicted):raise ValueError('Stored classification correctness changed')
        labels.append(classes.index(sample['truth']));probabilities.append(values)
    scores=normalized_margin(np.asarray(labels,dtype=int),np.asarray(probabilities,dtype=float))
    items=[{'sample_id':row['sample_id'],'image_uuid':row['image_uuid'],'revision':row['revision'],'file_path':row['file_path'],
            'relative_path':expected[row['sample_id']]['source_relative_path'],'source_sha256':row['input_sha256'],
            'truth_sha256':row['truth_sha256'],'given_class':row['ground_truth'],'predicted_class':row['predicted_class'],
            'quality_score':float(score),'reasons':['label_quality']} for row,score in zip(rows,scores)]
    return classes,sorted(items,key=lambda r:(r['quality_score'],r['relative_path'],r['sample_id']))


def create_analysis(project,source,evaluation_id):
    record=_record(project,source,evaluation_id);descriptor=_descriptor(project,record)
    classes,items=_rank(record,descriptor);_basis(project,source,record,descriptor)
    analysis={'schema_version':1,'id':'labelquality_'+uuid.uuid4().hex,'created_at':time.time(),'scope':_scope(project,source),
              'evaluation_id':evaluation_id,'job_id':record['result'].get('job_id'),'evaluation_evidence_sha256':record['evidence_sha256'],'algorithm':ALGORITHM,
              'classes':classes,'items':items,'advisory_only':True,'group_independence':'unverified',
              'independence_limits':'Saved train/validation and exact-pixel exclusions do not establish historical LOT/group independence.',
              'label_edit_policy':'No automatic label correction or approval; historical review may continue after label edits.',
              'common_cohort':record['result']['common_cohort'],'input_receipt':record['result']['input_receipt']}
    analysis['analysis_sha256']=hashlib.sha256(dw.canonical(analysis)).hexdigest()
    dw._write(analysis_path(project,source,analysis['id'],create=True),analysis,True)
    return {**analysis,'basis_changed':False}


def read_analysis(project,source,identifier,*,historical=False):
    path=analysis_path(project,source,identifier)
    if path.is_symlink() or not path.is_file():raise ValueError('Label quality analysis is absent from active source scope')
    analysis=json.loads(path.read_text(encoding='utf-8'))
    unsigned={k:v for k,v in analysis.items() if k!='analysis_sha256'}
    if analysis.get('id')!=identifier or hashlib.sha256(dw.canonical(unsigned)).hexdigest()!=analysis.get('analysis_sha256'):
        raise ValueError('Label quality analysis integrity changed')
    if analysis.get('scope')!=_scope(project,source):raise ValueError('Label quality analysis scope changed')
    record=_record(project,source,analysis['evaluation_id'])
    if record['evidence_sha256']!=analysis['evaluation_evidence_sha256']:raise ValueError('Label quality evaluation evidence changed')
    descriptor=_descriptor(project,record)
    changed,flags=_basis(project,source,record,descriptor,historical=historical)
    by_id={r['sample_id']:flag for r,flag in zip(record['result']['test_predictions'],flags)}
    result=copy.deepcopy(analysis);result['basis_changed']=changed
    for row in result['items']:row['basis_changed']=bool(changed or by_id[row['sample_id']])
    return result
