"""Hash-bound portable native recipes; reuse the owned operation transport/leases."""
import hashlib
import json
from pathlib import Path, PurePosixPath
from backend.engine.evaluation_history import canonical


def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()


def verify_inputs(root,descriptor):
    if not isinstance(descriptor,list) or not 2<=len(descriptor)<=10000:raise ValueError('Recipe input manifest exceeds bounds')
    seen=set();total=0;files={}
    for row in descriptor:
        name=row.get('path') if isinstance(row,dict) else None
        if (not isinstance(name,str) or not name.startswith('inputs/') or '\\' in name
                or any(part in ('','.','..') for part in name.split('/')) or name in seen):
            raise ValueError('Invalid recipe input reference')
        seen.add(name);path=Path(root)/PurePosixPath(name)
        if any(Path(root).joinpath(*PurePosixPath(name).parts[:i]).is_symlink() for i in range(1,len(PurePosixPath(name).parts)+1)):
            raise ValueError('Recipe input is linked')
        if not path.is_file() or path.stat().st_size!=row.get('size') or sha(path)!=row.get('sha256'):
            raise ValueError('Recipe input hash or size differs')
        total+=row['size'];files[name]=path
        if total>2*1024**3 or row['size']>512*1024**2:raise ValueError('Recipe inputs exceed transfer limits')
    return files


def run_recipe_worker(spec_path):
    from backend.remote import worker
    from backend.engine.execution_recipe import run_recipe,request_model
    from backend.engine.runtime_device_identity import runtime_device_identity
    from backend.engine.source_aliases import source_alias_scope
    spec_path=Path(spec_path).absolute();root=spec_path.parent
    status=worker._start_operation(spec_path,'recipe')
    if isinstance(status,dict):return status
    try:
        spec=worker._read_operation_spec(spec_path,'recipe');worker.apply_memory_budget(spec)
        binding=spec['recipe'];digest=hashlib.sha256(canonical(binding)).hexdigest()
        if spec.get('recipe_contract')!=1 or digest!=spec['input_manifest_sha256'] or binding['task']!=spec['task']:
            raise ValueError('Recipe identity differs')
        params=request_model(binding['task'],binding['stage']).model_validate(binding['params']).model_dump(mode='json')
        files=verify_inputs(root,binding['inputs']);checkpoint=files['inputs/model/best_model.pt']
        metadata=json.loads(files['inputs/model/model_meta.json'].read_text())
        if metadata.get('task')!=binding['task'] or metadata.get('checkpoint_sha256',sha(checkpoint))!=sha(checkpoint):
            raise ValueError('Recipe model task or checkpoint differs')
        import torch
        payload=torch.load(checkpoint,map_location='cpu',weights_only=True)
        if payload.get('task',metadata['task'])!=binding['task']:raise ValueError('Recipe checkpoint task differs')
        inputs={}
        if binding['image_ref']:inputs['image']=files[binding['image_ref']]
        if binding['dataset_ref']:inputs['dataset']=root/binding['dataset_ref']
        if binding.get('core_evaluation'):
            info=binding['core_evaluation']
            if (info.get('execution_target')!='selected_compute' or info.get('device')!=spec['device']
                    or info.get('execution_profile_sha256')!=spec.get('execution_profile_sha256')
                    or info.get('compute_profile_id')!=spec.get('compute_profile_id')):
                raise ValueError('Core cohort target differs from selected worker')
            inputs.update(core_evaluation=info,cohort_archive=files['inputs/cohort.tar.gz'],expected_runtime_gpu_uuid=spec.get('expected_runtime_gpu_uuid'))
        device=spec['device'];aliases={binding['source_root']:root/'inputs/original'} if binding['source_root'] else {}
        # Verify selected physical CUDA UUID using the same strict common-cohort
        # evidence as evaluation. Local MPS is never accepted as a remote alias.
        from backend.remote.evaluation_cohort import target_identity
        actual,runtime=target_identity(spec)
        import platform
        runtime.update(torch_version=torch.__version__,python_version=platform.python_version(),platform=platform.platform())
        status.update(job_id=spec['job_id'],status='running',device=str(actual))
        with source_alias_scope(aliases):
            result=run_recipe(binding['task'],binding['stage'],checkpoint,inputs,params,root/'outputs',device)
        verify_inputs(root,binding['inputs'])
        if (root/'cancel').exists():return status.update(status='aborted')
        body={'recipe_sha256':digest,'result':result,'runtime':runtime,'checkpoint_sha256':sha(checkpoint)}
        worker._atomic_json(root/'outputs/result.json',body)
        artifacts=tuple(p.relative_to(root).as_posix() for p in (root/'outputs').rglob('*') if p.is_file())
        worker._atomic_json(root/'artifacts.json',worker._operation_artifact_manifest(root,spec,'recipe',artifacts))
        return status.update(status='completed',device=str(actual))
    except Exception as exc:return worker._failed_status(status,exc,root)


def execute_remote(profile,project,binding,files,device):
    from backend.remote import operations
    from backend.remote.coordinator import ArtifactValidationError
    from backend.remote.evaluation_cohort import _gpu_uuid_matches
    digest=hashlib.sha256(canonical(binding)).hexdigest();expected=None
    if profile.distributed_processes!=1 or profile.allow_sharing:raise ValueError('Native recipes require one exclusive execution target')
    if device not in ('cpu','cuda:0'):raise ValueError('Remote recipe requires CPU or logical CUDA 0')
    if device=='cuda:0':
        if not profile.gpu_selector or profile.gpu_selector=='all' or ',' in profile.gpu_selector:
            raise ValueError('Recipe CUDA requires one selected physical GPU')
        probe=operations.SSHTransport().probe(profile);rows=(probe.get('checks') or {}).get('device_inventory',{}).get('devices',[])
        selected=[r for r in rows if str(r.get('selector'))==profile.gpu_selector or r.get('uuid')==profile.gpu_selector]
        if not probe.get('ready') or len(selected)!=1 or not selected[0].get('uuid'):raise ValueError('Selected recipe GPU UUID is unavailable')
        expected=selected[0]['uuid']
    context=operations.RemoteJobContext('job_recipe_'+digest[:24],binding['task'],Path(project['reports_dir'])/'execution_recipes',
        Path(project['dataset_dir']),profile,digest,portable=True)
    paths=operations.run_remote_operation_artifacts(context,'recipe',{'recipe_contract':1,'recipe':binding,'device':device,
        'expected_runtime_gpu_uuid':expected,'compute_profile_id':profile.id,
        'execution_profile_sha256':hashlib.sha256(canonical(profile.model_dump())).hexdigest()},input_files=files)
    report=paths.get('outputs/result.json')
    if report is None:raise ArtifactValidationError('Recipe result is missing')
    result=json.loads(report.read_text());runtime=result.get('runtime',{})
    if result.get('recipe_sha256')!=digest or result.get('checkpoint_sha256')!=sha(files['inputs/model/best_model.pt']) or runtime.get('device')!=device:
        raise ArtifactValidationError('Recipe result has another model, input or device binding')
    if device=='cuda:0' and not _gpu_uuid_matches(runtime.get('gpu_uuid'),expected):raise ArtifactValidationError('Recipe result has another physical GPU')
    if type(runtime.get('process_id')) is not int or runtime['process_id']<=0:raise ArtifactValidationError('Recipe runtime process identity is unavailable')
    for row in binding['inputs']:
        if sha(files[row['path']])!=row['sha256']:raise ArtifactValidationError('Recipe input changed during execution')
    return result['result'],runtime,paths
