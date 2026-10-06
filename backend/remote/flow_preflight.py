"""Frozen portable dependency checks on the selected runtime; never inference."""
from pathlib import Path
import json,platform,tempfile
from backend.remote.coordinator import ArtifactValidationError,_sha256
from backend.remote.package_parity import _digest,_package_archive,_unpack
from backend.engine import flow_preflight as checks


def selected_target(profile,device):
    if profile is None:raise ValueError('Selected preflight profile is unavailable')
    if device not in ('cpu','cuda:0') or profile.distributed_processes!=1 or profile.allow_sharing:
        raise ValueError('Selected preflight requires one exclusive CPU or logical CUDA 0 target')
    if device=='cpu' and profile.memory_budget_mb:raise ValueError('CPU preflight cannot reserve CUDA memory')
    return {'kind':'selected_compute','compute_profile_id':profile.id,'compute_profile_name':profile.name,
            'compute_gpu_selector':profile.gpu_selector,'execution_profile_sha256':_digest(profile.model_dump()),'device':device}


def validate_report(report,binding,pipeline=None):
    from backend.remote.evaluation_cohort import _gpu_uuid_matches
    if (report.get('report_sha256')!=checks._report_sha(report) or report.get('input_manifest_sha256')!=_digest(binding)
            or report.get('target_identity')!=binding['target'] or report.get('recipe_release')!=binding['release']
            or report.get('package_identity')!=binding['package'] or report.get('model_inference_executed') is not False
            or report.get('environment_hash')!=checks.environment_hash(report.get('environment') or {})):
        raise ValueError('Selected preflight report binding or environment differs')
    runtime=report.get('runtime') or {}
    if runtime.get('device')!=binding['target']['device'] or type(runtime.get('process_id')) is not int or runtime['process_id']<=0:
        raise ValueError('Selected preflight runtime identity differs')
    if runtime['device']=='cuda:0' and not _gpu_uuid_matches(runtime.get('gpu_uuid'),binding['expected_gpu_uuid']):
        raise ValueError('Selected preflight physical GPU differs')
    if report.get('status') not in ('ready','blocked','unverified'):raise ValueError('Selected preflight status is invalid')
    rows=report.get('requirements');counts=report.get('counts')
    if not isinstance(rows,list) or not all(isinstance(row,dict) for row in rows) or counts!={state:sum(row.get('state')==state for row in rows) for state in checks.STATES}:
        raise ValueError('Selected preflight dependency counts differ')
    requirements=[checks.RecipeRequirement(**row) for row in rows]
    expected_status='blocked' if any(row.state in checks.BLOCKING for row in requirements) else 'unverified' if any(row.state=='unverified' for row in requirements) else 'ready'
    if report['status']!=expected_status:raise ValueError('Selected preflight status differs from its dependencies')
    if pipeline is not None:
        node_ids={node.id for node in pipeline.nodes}
        if any(row.node_id is not None and row.node_id not in node_ids for row in requirements):raise ValueError('Selected preflight names another node')
        blocked=checks.blocked_nodes(pipeline,requirements)
        decision=any(node.id in blocked for node in pipeline.nodes if node.data.node_type=='decision')
        if report.get('blocked_nodes')!=blocked or report.get('decision_blocked')!=decision:raise ValueError('Selected preflight graph blocking differs')
    models=[row for row in rows if row.get('kind')=='model' and ':' in row.get('artifact_ref','')]
    if {row['artifact_ref'].split(':',1)[1] for row in models}!=set(binding['package']['checkpoints']):
        raise ValueError('Selected preflight model requirements differ')
    for row in models:
        if row['state']=='ready' and row['evidence_ref']!='sha256:'+binding['package']['checkpoints'][row['artifact_ref'].split(':',1)[1]]:
            raise ValueError('Selected preflight model evidence differs')


def selected_flow_preflight(profile,project,pipeline,checkpoints,release,device):
    from backend.engine import flow_package
    from backend.engine.flow_package_runtime import verify_flow_package
    from backend.engine.spatial_calibration import project_calibration_store
    from backend.engine.fixture_flow import project_fixtures
    from backend.remote import operations
    target=selected_target(profile,device);expected=None
    if device=='cuda:0':
        if not profile.gpu_selector or profile.gpu_selector=='all' or ',' in profile.gpu_selector:
            raise ValueError('Selected preflight CUDA requires one physical GPU')
        probe=operations.SSHTransport().probe(profile);inventory=(probe.get('checks') or {}).get('device_inventory',{}).get('devices',[])
        selected=[row for row in inventory if str(row.get('selector'))==profile.gpu_selector or row.get('uuid')==profile.gpu_selector]
        if not probe.get('ready') or len(selected)!=1 or not selected[0].get('uuid'):raise ValueError('Selected preflight GPU UUID is unavailable')
        expected=selected[0]['uuid']
    reports=Path(project['reports_dir']);root=Path(project['project_dir'])
    if reports.is_symlink() or reports.resolve()!=(root/'reports').resolve():raise ValueError('Selected preflight storage belongs to another project')
    original={job:_sha256(path) for job,path in checkpoints.items()}
    with tempfile.TemporaryDirectory(prefix='selected-preflight-') as temporary:
        temporary=Path(temporary).resolve()
        made=flow_package.build_flow_package(pipeline=pipeline,checkpoints=checkpoints,output_base_dir=temporary,
            package_name='preflight',runtime_config={'device':device},calibrations=project_calibration_store(project).load,
            fixtures=project_fixtures(project).load)
        package=Path(made['package_path']);verify_flow_package(package)
        mismatch=flow_package._package_input_mismatch(package,pipeline,checkpoints)
        if mismatch:raise ArtifactValidationError(mismatch)
        archive=temporary/'package.tar.gz';_package_archive(package,archive)
        binding={'package':flow_package._parity_identity(package),'archive_sha256':_sha256(archive),'archive_size':archive.stat().st_size,
                 'release':release,'target':target,'expected_gpu_uuid':expected}
        digest=_digest(binding);context=operations.RemoteJobContext('job_preflight_'+digest[:24],'classification',reports/'remote_preflight',
            Path(project['dataset_dir']),profile,digest,portable=True)
        paths=operations.run_remote_operation_artifacts(context,'flow_preflight',{'preflight_binding':binding,'device':device,
            'expected_runtime_gpu_uuid':expected},input_files={'inputs/package.tar.gz':archive},timeout_seconds=300)
    result=paths.get('outputs/preflight_report.json')
    if result is None:raise ArtifactValidationError('Selected preflight report is missing')
    report=json.loads(result.read_text());validate_report(report,binding,pipeline)
    if report.get('worker_spec_sha256')!=_sha256(result.parent.parent/'spec.json') or report.get('worker_code_archive_sha256')!=_sha256(result.parent.parent/'remote_code.tar.gz'):
        raise ArtifactValidationError('Selected preflight worker source or spec differs')
    if any(_sha256(checkpoints[job])!=value for job,value in original.items()):raise ArtifactValidationError('Selected preflight model changed during execution')
    return report


def run_flow_preflight(spec_path):
    from backend.remote import worker
    from backend.engine import flow_package
    from backend.engine.flow_package_runtime import verify_flow_package
    from backend.engine.flow_provenance import pipeline_sha256
    from backend.remote.evaluation_cohort import target_identity
    spec_path=Path(spec_path).absolute();root=spec_path.parent;status=worker._start_operation(spec_path,'flow_preflight')
    if isinstance(status,dict):return status
    try:
        spec=worker._read_operation_spec(spec_path,'flow_preflight');binding=spec['preflight_binding']
        if _digest(binding)!=spec['input_manifest_sha256'] or binding['target']['device']!=spec['device']:
            raise ArtifactValidationError('Selected preflight input binding differs')
        archive=worker._run_relative_file(root,'inputs/package.tar.gz','preflight archive')
        if archive.stat().st_size!=binding['archive_size'] or _sha256(archive)!=binding['archive_sha256']:
            raise ArtifactValidationError('Selected preflight archive differs')
        package=root/'package';_unpack(archive,package);pipeline,_=verify_flow_package(package)
        if flow_package._parity_identity(package)!=binding['package'] or pipeline_sha256(pipeline)!=binding['release']['pipeline_sha256']:
            raise ArtifactValidationError('Selected preflight package or graph differs')
        worker.apply_memory_budget(spec);device,runtime=target_identity(spec)
        import torch
        runtime.update(torch_version=torch.__version__,python_version=platform.python_version(),platform=platform.platform())
        if (root/'cancel').exists():return status.update(status='aborted')
        report=checks.package_preflight(package,device=str(device))
        report.update(recipe_release=binding['release'],target_identity=binding['target'],package_identity=binding['package'],runtime=runtime,
            input_manifest_sha256=spec['input_manifest_sha256'],worker_spec_sha256=_sha256(spec_path),
            worker_code_archive_sha256=_sha256(root/'code.tar.gz'),model_inference_executed=False)
        report['report_sha256']=checks._report_sha(report);validate_report(report,binding,pipeline);verify_flow_package(package)
        if _sha256(archive)!=binding['archive_sha256']:raise ArtifactValidationError('Selected preflight input changed')
        if (root/'cancel').exists():return status.update(status='aborted')
        worker._atomic_json(root/'outputs/preflight_report.json',report)
        worker._atomic_json(root/'artifacts.json',worker._operation_artifact_manifest(root,spec,'flow_preflight',('outputs/preflight_report.json',)))
        return status.update(status='completed',device=str(device))
    except Exception as exc:return worker._failed_status(status,exc,root)
