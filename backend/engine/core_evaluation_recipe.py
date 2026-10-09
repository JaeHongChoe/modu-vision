"""Explicit-target core evaluation reuses frozen cohort and metric validation."""
import json,re,shutil
from pathlib import Path
from types import SimpleNamespace
from threading import Event
from pydantic import BaseModel,ConfigDict
from backend.remote.evaluation_cohort import canonical,digest,cohort_spec,freeze_cohort,extract_cohort,worker_evaluate,validate_result
from backend.remote.recipe import sha

class CoreEvaluationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id:str
    dataset_path:str
    evaluation_dataset_version_id:str

def training_selection_hashes(directory,manifest,task):
    from backend.engine.dataset_inventory import folder_label_split,is_inventory_path
    from backend.remote.evaluation_cohort import safe_relative,plain_file
    split_rows=[row for row in manifest['files'] if row['origin']=='split']
    if len(split_rows)>1:raise ValueError('Training manifest has an ambiguous frozen split')
    assignments=None
    if split_rows:
        row=split_rows[0];path=plain_file(Path(directory)/safe_relative(row['snapshot_path']),Path(directory))
        if sha(path)!=row['sha256']:raise ValueError('Saved training split changed')
        value=json.loads(path.read_text());assignments=value.get('assignments')
        if value.get('folder_path')!=manifest['source_dataset_dir'] or not isinstance(assignments,dict):
            raise ValueError('Training split belongs to another source')
        if any(part not in ('train','val','test') for part in assignments.values()):raise ValueError('Invalid saved training split')
    hashes=set()
    for row in manifest['files']:
        relative=safe_relative(row['relative_path'])
        if row['origin']!='source' or row['kind']!='image' or not is_inventory_path(relative.parts,task):continue
        partition=assignments.get(relative.as_posix()) if assignments is not None else folder_label_split(relative,task)[1]
        if partition in ('train','val'):hashes.add(row['sha256'])
    if not hashes:raise ValueError('Saved training selection pixels are unavailable')
    return hashes

def freeze_core_recipe(project,checkpoint,options,target,profile,device):
    from backend.remote.operations import remote_job_context,common_operation_authority
    checkpoint=Path(checkpoint);metadata=json.loads(checkpoint.with_name('model_meta.json').read_text())
    context=remote_job_context(checkpoint.parent,options['job_id'])
    if context is not None:
        training_sha=context.input_manifest_sha256
    else:
        training_sha=(metadata.get('training_provenance') or {}).get('manifest_sha256')
        if not isinstance(training_sha,str) or not re.fullmatch('[0-9a-f]{64}',training_sha):
            raise ValueError('Explicit core evaluation requires a saved training manifest identity')
        from backend.api.routes_dataset_versions import _read_manifest,_require_active_labelset
        provenance=metadata['training_provenance']
        training_directory,training_manifest=_read_manifest(project,provenance['dataset_version_id'])
        _require_active_labelset(project,training_manifest)
        if training_manifest['content_digest']!=training_sha or training_manifest['task']!=metadata['task']:
            raise ValueError('Saved training manifest differs from completed model')
        receipt=json.loads(checkpoint.with_name('job_receipt.json').read_text())
        if Path(training_manifest['source_dataset_dir']).resolve()!=Path(receipt['source_dataset_path']).resolve():
            raise ValueError('Saved training version belongs to another model source')
        if receipt.get('training_provenance')!=provenance:
            raise ValueError('Completed job training provenance differs from model')
        import torch
        payload=torch.load(checkpoint,map_location='cpu',weights_only=True)
        if payload.get('training_provenance')!=provenance:
            raise ValueError('Checkpoint training provenance differs from model')
        # Desktop classifiers can train directly from source. Partition and
        # pixel hashes come from the bound saved manifest, not mutable folders
        # or caller-supplied claims about what the trainer saw.
        context=SimpleNamespace(selection_sha256=training_selection_hashes(training_directory,training_manifest,metadata['task']))
    cohort=freeze_cohort(project,options['evaluation_dataset_version_id'],Path(options['dataset_path']),metadata['task'],metadata,context)
    return {'cohort':cohort_spec(cohort),'descriptor':cohort['descriptor'],'training_snapshot_sha256':training_sha,
        'operation_authority':common_operation_authority(project['id']),
        'execution_target':target,'device':device,'job_id':options['job_id'],
        'compute_profile_id':profile.id if profile else None,'compute_profile_name':profile.name if profile else None,
        'compute_gpu_selector':profile.gpu_selector if profile else None,'execution_profile_sha256':digest(profile.model_dump()) if profile else None},cohort['archive']

def evaluation_spec(info,checkpoint,device,expected_uuid=None):
    checkpoint=Path(checkpoint);meta=json.loads(checkpoint.with_name('model_meta.json').read_text())
    if device!=info['device']:raise ValueError('Core evaluation device changed')
    spec={'protocol_version':1,'operation':'evaluate','job_id':info['job_id'],'task':meta['task'],
        'input_manifest_sha256':info['training_snapshot_sha256'],'evaluation_contract_version':2,
        'common_cohort_contract':1,'native_core_evaluation_contract':1,'evaluation_cohort':info['cohort'],
        'operation_authority':info['operation_authority'],'checkpoint_sha256':sha(checkpoint),'metadata_sha256':sha(checkpoint.with_name('model_meta.json')),
        **{key:info[key] for key in ('execution_target','compute_profile_id','compute_profile_name','compute_gpu_selector','execution_profile_sha256')},
        'expected_runtime_gpu_uuid':expected_uuid,'device':device,'model_image_size':meta.get('image_size',[256,256])}
    if meta['task']=='anomaly':
        from backend.engine.score_contract import checkpoint_score_spec
        spec.update(model_score_spec=checkpoint_score_spec(checkpoint),model_map_semantics=meta.get('map_semantics','patch_score' if meta.get('detector_type')=='dino_synthetic' else 'pixel_score'),model_anomaly_mode=meta.get('anomaly_mode','classification'))
    spec['evaluation_binding_sha256']=digest(spec)
    return spec

def run_core_evaluation(checkpoint,inputs,output,device):
    from backend.remote.worker import _atomic_json
    output=Path(output);run=output.parent;run.mkdir(parents=True,exist_ok=True)
    archive=run/'inputs/cohort.tar.gz';archive.parent.mkdir(exist_ok=True)
    original=Path(inputs['cohort_archive'])
    if original.resolve()!=archive.resolve():shutil.copyfile(original,archive)
    uuid=inputs.get('expected_runtime_gpu_uuid')
    if device=='cuda:0' and uuid is None:
        from backend.engine.runtime_device_identity import runtime_device_identity
        uuid=runtime_device_identity(device)['gpu_uuid']
    spec=evaluation_spec(inputs['core_evaluation'],checkpoint,device,uuid)
    data,descriptor=extract_cohort(run,spec)
    if descriptor!=inputs['core_evaluation']['descriptor']:raise ValueError('Core cohort descriptor changed')
    meta=json.loads(Path(checkpoint).with_name('model_meta.json').read_text())
    result,_=worker_evaluate(spec,Path(checkpoint),meta,data,descriptor,run,Event())
    result['quality_approved']=False
    _atomic_json(output/'eval_results.json',result)
    return result

def archive_core_evaluation(project,checkpoint,info,result,artifacts,runtime,output):
    from backend.api.routes_dataset_versions import _read_manifest,_verify,_require_active_labelset
    from backend.engine.evaluation_history import EvaluationHistory,evaluation_model_context
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine.dataset_metadata import metadata_for_path
    from backend.engine.annotation_storage import scoped_annotation_root
    from backend.api import routes_dataset
    if result.get('quality_approved') is not False:raise ValueError('Core evaluation cannot invent quality approval')
    identity=result.get('runtime_device_identity',{})
    if any(runtime.get(key)!=value for key,value in identity.items()):raise ValueError('Core evaluation runtime differs from verified worker')
    spec=evaluation_spec(info,checkpoint,info['device'],runtime.get('gpu_uuid'))
    version_dir,manifest=_read_manifest(project,info['cohort']['dataset_version_id']);_require_active_labelset(project,manifest)
    verification=_verify(project,version_dir,manifest)
    if verification['status']!='verified' or verification['editable_changed_files'] or manifest['content_digest']!=info['cohort']['version_manifest_sha256']:
        raise ValueError('Core cohort source, truth or split changed during execution')
    # Re-extract only the bounded hash-verified input for independent geometry
    # and metric checks; the remote result cannot supply its own truth.
    output=Path(output);archive=output/'inputs/cohort.tar.gz';archive.parent.mkdir(parents=True,exist_ok=True)
    source_archive=Path(project['reports_dir'])/'evaluation_cohorts'/('cohort_'+info['descriptor']['cohort_sha256'])/'input.tar.gz'
    if not source_archive.is_file():
        candidates=list(source_archive.parent.glob('*.tar.gz'))
        if len(candidates)!=1:raise ValueError('Frozen core cohort archive is unavailable')
        source_archive=candidates[0]
    if source_archive.resolve()!=archive.resolve():shutil.copyfile(source_archive,archive)
    data,descriptor=extract_cohort(output,spec)
    if descriptor!=info['descriptor']:raise ValueError('Core cohort descriptor changed before adoption')
    cohort={'descriptor':descriptor,'dataset_version_id':info['cohort']['dataset_version_id'],'directory':data.parent,
        'source':Path(project['source_dataset_dir']).resolve()}
    result=validate_result(result,spec,cohort,artifacts)
    meta=json.loads(Path(checkpoint).with_name('model_meta.json').read_text())
    binding={'source_dataset_path':str(cohort['source']),'dataset_fingerprint':fingerprint_dataset(cohort['source'],
        studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,split_manifest=routes_dataset._split_manifest_file(cohort['source'])),
        'checkpoint_sha256':sha(checkpoint),**evaluation_model_context(Path(project['project_dir']),meta),
        **{key:result[key] for key in ('common_cohort','evaluation_binding_sha256','execution_target','compute_profile_id','execution_profile_sha256','device','runtime_device_identity','input_receipt')}}
    for row in result['test_predictions']:
        metadata=metadata_for_path(Path(project['project_dir']),cohort['source'],Path(row['file_path']),scoped_annotation_root(routes_dataset.STUDIO_ANNOTATIONS_DIR))
        row.update({key:metadata[key] for key in ('image_uuid','content_hash','content_version','revision','tags','product','lot','group','workflow_state') if key in metadata})
        evidence=row.get('pixel_evidence')
        if isinstance(evidence,dict) and evidence.get('file_path') in artifacts:evidence['file_path']=str(artifacts[evidence['file_path']])
    record=EvaluationHistory(Path(project['reports_dir'])/'evaluations').append(result,binding)
    result.update(evaluation_id=record['evaluation_id'],binding=binding,grouped_errors=record['grouped_errors'])
    return result
