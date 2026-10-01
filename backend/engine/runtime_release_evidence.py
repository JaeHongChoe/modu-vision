"""Release acceptance binds a completed cohort receipt to exact runtime bytes."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
from backend.engine.flow_package_runtime import verify_flow_package


def verify_release_evidence(package,device,*,expected_receipt_sha256=None):
    root=Path(package)
    verify_flow_package(root)
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest.get('runtime_acceptance_sha256'):
        path=root/'runtime_acceptance.json';raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        if digest!=manifest['runtime_acceptance_sha256'] or (expected_receipt_sha256 is not None and digest!=expected_receipt_sha256):raise ValueError('Measured precision receipt checksum differs from trusted release')
        evidence=verify_measured_precision_evidence(root,device,acceptance=json.loads(raw))
        return {**evidence,'receipt_sha256':digest,'receipt_kind':'measured_precision_cohort','manifest_sha256':_digest(root/'manifest.json')}
    path=root/'parity_receipt.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size>2*1024*1024:
        raise ValueError('Release requires an unlinked completed cohort parity receipt')
    raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if expected_receipt_sha256 is not None and digest!=expected_receipt_sha256:
        raise ValueError('Release parity receipt checksum differs from approved policy')
    receipt=json.loads(raw);manifest=json.loads((root/'manifest.json').read_text())
    if receipt.get('schema_version')!=1 or receipt.get('contract')!='flow_parity_v1':raise ValueError('Unknown release parity receipt contract')
    if receipt.get('status')!='passed' or receipt.get('scope')!='cohort' or receipt.get('limitation'):
        raise ValueError('Release requires passed cohort parity; single-image compatibility is limited')
    if receipt.get('device')!=device or receipt.get('resolved_device')!=device:raise ValueError('Release cohort parity device differs from actual runtime device')
    manifest_sha=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()
    if receipt.get('manifest_sha256')!=manifest_sha:raise ValueError('Release cohort manifest checksum differs')
    files={row['path']:row['sha256'] for row in manifest['files']}
    models={row['job_id']:files[row['checkpoint']] for row in manifest['models']}
    if receipt.get('graph_sha256')!=files['pipeline.json'] or receipt.get('checkpoints')!=models:
        raise ValueError('Release cohort graph or checkpoint identity differs')
    count=receipt.get('image_count');rows=receipt.get('images')
    if (type(count) is not int or not 2<=count<=64 or not isinstance(rows,list) or len(rows)!=count
            or receipt.get('completed_count')!=count or receipt.get('mismatched_fields') or receipt.get('error')):
        raise ValueError('Release cohort must be complete with 2 to 64 matching inputs')
    hashes=[];paths=set()
    for index,row in enumerate(rows):
        image_sha=row.get('image_sha256');image_path=row.get('image_path')
        if (row.get('index')!=index or row.get('status')!='passed' or row.get('mismatched_fields')
                or not isinstance(image_sha,str) or not re.fullmatch('[0-9a-f]{64}',image_sha)
                or not isinstance(image_path,str) or not image_path or image_path in paths
                or not isinstance(row.get('reference'),dict) or not isinstance(row.get('packaged'),dict)):
            raise ValueError('Release cohort contains invalid or unmatched image evidence')
        hashes.append(image_sha);paths.add(image_path)
    cohort=hashlib.sha256(json.dumps(sorted(hashes),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    if receipt.get('cohort_sha256')!=cohort:raise ValueError('Release cohort input hash differs')
    runner=receipt.get('packaged_runtime',{});reference=receipt.get('reference_runtime',{})
    if (runner.get('independent_process') is not True or runner.get('kind') not in ('isolated_python_runner','frozen_package_dispatcher')
            or runner.get('package_runner_sha256')!=files['run_flow.py']
            or runner.get('package_runtime_sha256')!=files['backend/engine/flow_package_runtime.py']
            or reference.get('kind')!='in_process_app_engine' or reference.get('device')!=device):
        raise ValueError('Release cohort requires independent exported-runtime provenance')
    return {'receipt_kind':'flow_parity','receipt_sha256':digest,'cohort_sha256':cohort,'image_count':count,'device':device,'manifest_sha256':manifest_sha}


def _digest(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def precision_cohort_inputs(receipt,*,check_source=False):
    """Validate frozen inputs; this check alone never certifies a native runtime."""
    from pathlib import PurePosixPath
    rows=receipt.get('validation_images') if isinstance(receipt,dict) else None
    if not isinstance(rows,list) or not 2<=len(rows)<=64:raise ValueError('Measured precision acceptance requires 2 to 64 heldout images; rerun conversion with a fixed cohort')
    paths=set()
    source=Path(receipt.get('source_dataset_path') or '')
    if not source.is_absolute():raise ValueError('Precision source identity requires an absolute source path')
    for row in [*receipt.get('calibration_images',[]),*rows]:
        relative=row.get('relative_path');sha=row.get('sha256')
        if (not isinstance(relative,str) or '\\' in relative or PurePosixPath(relative).is_absolute()
                or not PurePosixPath(relative).parts or '..' in PurePosixPath(relative).parts
                or not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha)):
            raise ValueError('Invalid precision cohort input path or checksum')
        if row in rows:
            if row.get('split') not in ('val','test'):raise ValueError('Precision acceptance requires heldout val/test inputs')
            if relative in paths:raise ValueError('Precision heldout input paths must be distinct')
            paths.add(relative)
        if check_source:
            image=source/relative
            if any(parent.is_symlink() for parent in (image,*image.parents)) or not image.is_file() or _digest(image)!=sha:
                raise ValueError('Precision original image changed before or during approval')
    if check_source and receipt.get('split_manifest_sha256'):
        split=receipt.get('split_manifest_path')
        if not split:raise ValueError('Precision receipt needs the exact split_manifest_path; rerun measured conversion')
        path=Path(split)
        if path.is_symlink() or not path.is_file() or _digest(path)!=receipt['split_manifest_sha256']:
            raise ValueError('Precision saved split changed during approval')
    frozen=[{'relative_path':row['relative_path'],'sha256':row['sha256'],'split':row['split']} for row in rows]
    cohort=hashlib.sha256(json.dumps(frozen,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'image_count':len(rows),'cohort_sha256':cohort,'images':frozen}


def _verify_precision_candidate_runtime(result):
    runtime=result.get('model_runtime',{})
    if (not isinstance(runtime,dict) or runtime.get('backend')!='openvino' or runtime.get('device')!='CPU'
            or type(runtime.get('compiled_models')) is not int or runtime['compiled_models']<1):
        raise ValueError('Precision candidate must record actual OpenVINO CPU model execution')


def _verify_precision_source_identity(receipt,source_manifest_sha256):
    if (not isinstance(receipt.get('source_fingerprint'),str) or not re.fullmatch('v1:[0-9a-f]{64}',receipt['source_fingerprint'])
            or not isinstance(source_manifest_sha256,str) or not re.fullmatch('[0-9a-f]{64}',source_manifest_sha256)):
        raise ValueError('Precision source dataset and original package identities are missing; rerun measured conversion')


def verify_measured_precision_evidence(package,device,*,acceptance=None,check_source=False):
    """Verify saved real reference/candidate outputs and measured native drift.

    Disagreement remains explicit and requires separately reviewed acceptance.
    No flow_parity_v1 pass is synthesized for an accepted precision difference.
    """
    import math
    from backend.engine.flow_package_runtime import compare_flow_results
    root=Path(package);verify_flow_package(root)
    manifest=json.loads((root/'manifest.json').read_text());files={row['path']:row['sha256'] for row in manifest['files']}
    if device!='openvino:CPU' or manifest['runtime']['device']!=device:raise ValueError('Measured precision cohort requires its exact accepted OpenVINO CPU runtime device')
    info=json.loads((root/'openvino_models.json').read_text());receipt=info.get('input_receipt')
    cohort=precision_cohort_inputs(receipt,check_source=check_source)
    _verify_precision_source_identity(receipt,info.get('source_manifest_sha256'))
    if check_source:
        from backend.api.routes_model_deployments import _fingerprint
        if _fingerprint(Path(receipt['source_dataset_path']))!=receipt['source_fingerprint']:raise ValueError('Precision source/data/evaluation identity changed during approval')
    if info.get('calibration_sha256')!=[row['sha256'] for row in receipt['calibration_images']] or info.get('validation_sha256')!=[row['sha256'] for row in receipt['validation_images']]:raise ValueError('Precision original input hash catalog differs')
    if _digest(root/'heldout_flow_results.json')!=info.get('heldout_flow_results_sha256'):raise ValueError('Precision heldout output catalog changed')
    heldout=json.loads((root/'heldout_flow_results.json').read_text())
    if len(heldout)!=cohort['image_count']:raise ValueError('Precision full-flow heldout evidence is incomplete')
    disagreement=[]
    for index,(row,image) in enumerate(zip(heldout,cohort['images'])):
        relative=f'heldout/heldout_{index:04d}.json'
        if row.get('image_sha256')!=image['sha256'] or row.get('result_path')!=relative or files.get(relative)!=row.get('result_sha256') or _digest(root/relative)!=row['result_sha256']:
            raise ValueError('Precision full-flow output does not match the exact original image/artifact')
        actual=json.loads((root/relative).read_text())
        if actual.get('image_sha256')!=image['sha256']:raise ValueError('Precision full-flow input identity differs')
        for side in ('reference','candidate'):
            result=actual.get(side)
            if (not isinstance(result,dict) or result.get('status') not in ('success','review') or result.get('final_verdict') not in ('OK','NG','REVIEW')
                    or any(step.get('status') in ('error','warning_untrained') for step in result.get('execution_steps',[]))):
                raise ValueError('Precision requires actual complete reference/candidate execution; failed inference cannot qualify')
            if row.get(side)!={key:result[key] for key in ('final_verdict','roi_count')}:raise ValueError('Precision full-flow summary differs from saved actual outputs')
        _verify_precision_candidate_runtime(actual['candidate'])
        comparison=compare_flow_results(actual['reference'],actual['candidate'])
        if comparison!=actual.get('comparison') or comparison!=row.get('comparison'):raise ValueError('Precision saved full-flow comparison differs from actual outputs')
        if comparison['status']!='passed':disagreement.append({'index':index,'image_sha256':image['sha256'],'mismatched_fields':comparison['mismatched_fields']})
    checkpoints={row['job_id']:files[row['checkpoint']] for row in manifest['models']}
    for model in info['models']:
        if model.get('precision')=='int8' and (not receipt['calibration_images']
                or any(row.get('split')!='train' for row in receipt['calibration_images'])
                or {row['sha256'] for row in receipt['calibration_images']} & {row['sha256'] for row in cohort['images']}):
            raise ValueError('INT8 measured acceptance requires separate saved training calibration and heldout inputs')
        conversion=json.loads((root/model['directory']/'conversion.json').read_text())
        if model.get('device')!='CPU' or model.get('checkpoint_sha256')!=checkpoints.get(model['job_id']) or any(model.get(key)!=value for key,value in conversion.items()):raise ValueError('Precision model/device/conversion identity differs')
        metrics=model.get('metrics',{})
        for metric in ('max_absolute_error','mean_absolute_error'):
            value=metrics.get(metric)
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('Precision conversion drift must be finite, measured and nonnegative')
        if metrics.get('validation_image_count')!=cohort['image_count']:raise ValueError('Precision measured conversion cohort count differs')
    result={**cohort,'device':device,'graph_sha256':files['pipeline.json'],'checkpoints':checkpoints,'full_flow_disagreements':disagreement,
            'heldout_flow_results_sha256':info['heldout_flow_results_sha256'],'source_manifest_sha256':info['source_manifest_sha256']}
    if acceptance is not None:
        if (acceptance.get('schema_version')!=1 or acceptance.get('contract')!='measured_precision_cohort_v1' or acceptance.get('acceptance_scope')!='precision_and_runtime'
                or acceptance.get('scope')!='cohort' or acceptance.get('holdout_reviewed') is not True
                or not isinstance(acceptance.get('reviewer'),str) or not acceptance['reviewer'].strip()
                or not isinstance(acceptance.get('reason'),str) or len(acceptance['reason'].strip())<8):raise ValueError('Measured precision requires an explicit reviewed cohort acceptance contract')
        for key,value in result.items():
            if acceptance.get(key)!=value:raise ValueError('Measured precision acceptance differs from exact cohort/runtime/graph/model evidence: '+key)
        bound=acceptance.get('maximum_absolute_drift')
        if type(bound) not in (int,float) or not math.isfinite(bound) or bound<0 or any(model['metrics']['max_absolute_error']>bound for model in info['models']):raise ValueError('Measured precision drift exceeds its explicit reviewed bound')
        if acceptance.get('runtime_configuration')!=manifest['runtime'] or acceptance.get('models')!=info['models'] or acceptance.get('input_receipt')!=receipt or acceptance.get('approval_revisions')!=manifest.get('release',{}).get('approval_revisions'):raise ValueError('Measured precision accepted release identity differs')
    return result
