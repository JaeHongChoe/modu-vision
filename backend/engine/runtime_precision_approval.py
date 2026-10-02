"""Explicit precision acceptance creates a fresh release, preserving candidates."""
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid


def approve_precision_package(candidate,output,*,revisions,reviewer,reason,maximum_absolute_drift,holdout_reviewed):
    from backend.engine.flow_package_runtime import verify_flow_package,_sha256
    root=Path(candidate).resolve();_,checkpoints=verify_flow_package(root)
    from backend.engine.runtime_release_evidence import verify_measured_precision_evidence
    original_manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    device=original_manifest['runtime']['device']
    measured=verify_measured_precision_evidence(root,device,check_source=True)
    if not isinstance(reviewer,str) or not reviewer.strip() or not isinstance(reason,str) or len(reason.strip())<8 or holdout_reviewed is not True:
        raise ValueError('Explicit reviewer, meaningful reason and reviewed holdout are required')
    if type(maximum_absolute_drift) not in (int,float) or not math.isfinite(maximum_absolute_drift) or maximum_absolute_drift<0:
        raise ValueError('Maximum absolute drift must be explicit, finite and nonnegative')
    info=json.loads((root/'openvino_models.json').read_text(encoding='utf-8'))
    receipt=info.get('input_receipt')
    if not receipt or not receipt.get('validation_images') or any(row.get('split') not in ('val','test') for row in receipt['validation_images']):
        raise ValueError('Precision approval requires actual saved heldout validation image evidence')
    if info.get('calibration_sha256')!=[row['sha256'] for row in receipt['calibration_images']] or info.get('validation_sha256')!=[row['sha256'] for row in receipt['validation_images']]:
        raise ValueError('Conversion calibration/heldout input hashes differ from approval evidence')
    heldout_file=root/'heldout_flow_results.json'
    if not heldout_file.is_file() or _sha256(heldout_file)!=info.get('heldout_flow_results_sha256'):raise ValueError('Actual heldout full-flow evidence is missing or changed')
    heldout=json.loads(heldout_file.read_text(encoding='utf-8'))
    if len(heldout)!=len(receipt['validation_images']) or [row['image_sha256'] for row in heldout]!=info['validation_sha256']:raise ValueError('Heldout full-flow evidence differs from selected original images')
    for row in heldout:
        if _sha256(root/row['result_path'])!=row['result_sha256']:raise ValueError('Actual heldout full-flow output changed')
    if set(revisions)!=set(checkpoints):raise ValueError('Every compiled model needs its current active checkpoint approval')
    for row in info['models']:
        conversion=json.loads((root/row['directory']/'conversion.json').read_text(encoding='utf-8'))
        if any(row.get(key)!=value for key,value in conversion.items()):raise ValueError('Saved measured conversion evidence differs from the model catalog')
        revision=revisions[row['job_id']]
        drift=row['metrics'].get('max_absolute_error')
        if type(drift) not in (int,float) or not math.isfinite(drift) or drift>maximum_absolute_drift:
            raise ValueError('Measured conversion drift exceeds the explicit acceptance bound')
        if row['metrics'].get('validation_image_count')!=len(receipt['validation_images']):raise ValueError('Heldout conversion sample count differs')
        if revision.get('checkpoint_sha256')!=_sha256(checkpoints[row['job_id']]) or row['checkpoint_sha256']!=revision['checkpoint_sha256'] or revision.get('task')!=row['task']:
            raise ValueError('Runtime source checkpoint differs from its active approved revision')
    target=Path(output)
    if target.exists() or target.resolve().is_relative_to(root) or any(p.is_symlink() for p in (target,*target.parents)):raise ValueError('Use a fresh owned approved release directory')
    target.parent.mkdir(parents=True,exist_ok=True)
    original_manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'));source_hash=_sha256(root/'manifest.json')
    with tempfile.TemporaryDirectory(prefix='.precision-approval-',dir=target.parent) as temporary:
        staging=Path(temporary)/'release';staging.mkdir()
        for row in original_manifest['files']:
            destination=staging/row['path'];destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/row['path'],destination,follow_symlinks=False)
        acceptance={'schema_version':1,'acceptance_id':uuid.uuid4().hex,'reviewer':reviewer.strip(),'reason':reason.strip(),
            'holdout_reviewed':True,'maximum_absolute_drift':maximum_absolute_drift,'created_at':time.time(),
            'candidate_manifest_sha256':source_hash,'source_manifest_sha256':info['source_manifest_sha256'],
            'input_receipt':receipt,'models':info['models'],'runtime_configuration':original_manifest['runtime'],
            'heldout_flow_results_sha256':info['heldout_flow_results_sha256'],
            'approval_revisions':[revisions[job_id] for job_id in sorted(revisions)],'acceptance_scope':'precision_and_runtime',
            'contract':'measured_precision_cohort_v1','scope':'cohort',**measured}
        (staging/'runtime_acceptance.json').write_text(json.dumps(acceptance,indent=2)+'\n',encoding='utf-8')
        manifest={**original_manifest,'release':{'approval_revisions':acceptance['approval_revisions']},'runtime_acceptance_sha256':_sha256(staging/'runtime_acceptance.json')}
        manifest['files']=[{'path':p.relative_to(staging).as_posix(),'size':p.stat().st_size,'sha256':_sha256(p)} for p in sorted(staging.rglob('*')) if p.is_file()]
        (staging/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        verify_measured_precision_evidence(staging,device,acceptance=acceptance)
        verify_measured_precision_evidence(root,device,check_source=True)
        if _sha256(root/'manifest.json')!=source_hash:raise ValueError('Candidate changed during approval')
        os.rename(staging,target)
    policy={'schema_version':1,'manifest_sha256':_sha256(target/'manifest.json'),'approval_revisions':acceptance['approval_revisions'],
            'runtime_acceptance_sha256':manifest['runtime_acceptance_sha256'],'device':device}
    return {'status':'approved','package_path':str(target.resolve()),'runtime_acceptance':acceptance,'release_policy':policy}
