"""Immutable portable package parity on one explicitly selected compute target.

Uses the existing operation coordinator, leases, reconnect journal and artifact
verification. Both engines run on that worker; the package runs independently.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile

from backend.remote.coordinator import ArtifactValidationError, _atomic_json, _sha256
from backend.remote.operations import RemoteJobContext, run_remote_operation_artifacts
from backend.remote.profiles import ComputeProfile

MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_IMAGE_BYTES = 64 * 1024 * 1024


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _device_evidence_error(report: dict, device: str) -> str | None:
    reference = (report.get('reference_runtime') or {}).get('runtime_device_identity')
    runner = report.get('packaged_runtime') or {}
    if (not isinstance(reference, dict) or reference.get('device') != device
            or type(reference.get('process_id')) is not int or reference['process_id'] <= 0
            or runner.get('independent_process') is not True):
        return 'Reference or independent package process identity is missing'
    if device.startswith('cuda') and not reference.get('gpu_uuid'):
        return 'Selected CUDA GPU UUID evidence is missing'
    keys = ('device', 'gpu_uuid', 'memory_budget_mb', 'cuda_visible_devices', 'nvidia_visible_devices')
    for row in report.get('images', []):
        if row.get('status') not in ('passed', 'mismatch'):
            continue
        observed = (row.get('packaged') or {}).get('runtime_device_identity')
        if (not isinstance(observed, dict) or any(observed.get(key) != reference.get(key) for key in keys)
                or type(observed.get('process_id')) is not int or observed['process_id'] <= 0
                or observed['process_id'] == reference['process_id']):
            return 'Package process ran on a different or unproven selected device'
    return None


def validate_parity_target(profile: ComputeProfile, device: str) -> None:
    if not isinstance(device, str) or not re.fullmatch(r'cpu|cuda(?::[0-9]+)?', device):
        raise ValueError('Remote package parity supports explicit CPU and CUDA devices only')
    if profile.distributed_processes != 1 or profile.allow_sharing:
        raise ValueError('Remote package parity requires one exclusive compute allocation; distributed/sharing modes are unsupported')
    if device == 'cpu' and profile.memory_budget_mb:
        raise ValueError('CPU parity requires a profile without a CUDA memory reservation')
    if device.startswith('cuda'):
        if not profile.gpu_selector:
            raise ValueError('CUDA parity requires an explicit selected GPU resource')
        if profile.memory_budget_mb is not None and profile.memory_budget_mb < 2:
            raise ValueError('Parity CUDA budget must cover both reference and package processes')
        selectors = profile.gpu_selector.split(',')
        index = int(device.split(':')[1]) if ':' in device else 0
        if profile.gpu_selector != 'all' and index >= len(selectors):
            raise ValueError('Parity device index exceeds GPUs visible in the selected profile')


def _package_archive(package: Path, destination: Path) -> None:
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    names = ['manifest.json', *[row['path'] for row in manifest['files']]]
    if len(names) > 1000 or sum((package / name).stat().st_size for name in names) > MAX_PACKAGE_BYTES:
        raise ValueError('Portable parity package exceeds transfer bounds')
    with destination.open('wb') as output, gzip.GzipFile(fileobj=output, mode='wb', mtime=0, filename='') as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for name in sorted(names):
                path = package / name
                info = tarfile.TarInfo(name)
                info.size = path.stat().st_size
                info.mode = 0o644
                with path.open('rb') as source:
                    archive.addfile(info, source)


def verify_package_on_compute(profile: ComputeProfile, project: dict, *, package_dir: Path,
                              pipeline, checkpoints: dict, images, device: str, transport=None) -> dict:
    from backend.engine import flow_package
    from backend.engine.flow_package_runtime import verify_flow_package
    validate_parity_target(profile, device)
    package = Path(package_dir).resolve()
    verify_flow_package(package)
    mismatch = flow_package._package_input_mismatch(package, pipeline, checkpoints)
    if mismatch:
        raise ArtifactValidationError(mismatch)
    frozen = flow_package._frozen_parity_inputs(images, minimum=2)
    if sum(Path(row['path']).stat().st_size for row in frozen) > 256 * 1024 * 1024:
        raise ValueError('Portable parity cohort exceeds total transfer bounds')
    identity = flow_package._parity_identity(package)
    if identity['package_runtime_device'] != device:
        raise ValueError('Remote parity device differs from the immutable package runtime')
    target = {'compute_profile_id': profile.id, 'compute_profile_name': profile.name,
              'compute_gpu_selector': profile.gpu_selector, 'execution_profile_sha256': _digest(profile.model_dump())}
    report_root = Path(project['reports_dir'])
    if report_root.is_symlink() or report_root.resolve() != (Path(project['project_dir']) / 'reports').resolve():
        raise ArtifactValidationError('Remote parity reports must belong to the active project')
    inputs, rows = {}, []
    for row in frozen:
        image = Path(row['path'])
        if image.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError('Portable parity image exceeds transfer bounds')
        relative = f"inputs/images/{row['index']:03d}{image.suffix.lower()}"
        rows.append({'index': row['index'], 'path': relative, 'image_id': row['image_id'],
                     'sha256': row['sha256'], 'size': image.stat().st_size})
        inputs[relative] = image
    with tempfile.TemporaryDirectory(prefix='remote-package-parity-') as temporary:
        archive = Path(temporary) / 'package.tar.gz'
        _package_archive(package, archive)
        binding = {'package': identity, 'archive_sha256': _sha256(archive), 'archive_size': archive.stat().st_size,
                   'images': rows, 'device': device, 'target': target}
        digest = _digest(binding)
        context = RemoteJobContext('job_package_' + digest[:24], 'classification', report_root / 'remote_package_parity',
                                   Path(project.get('dataset_dir', Path(project['project_dir']) / 'dataset')),
                                   profile, digest, portable=True)
        inputs['inputs/package.tar.gz'] = archive
        outputs = run_remote_operation_artifacts(context, 'package_parity', {'parity_binding': binding, 'device': device},
                                                 input_files=inputs, transport=transport, timeout_seconds=3600)
    output = outputs.get('outputs/parity_report.json')
    if output is None:
        raise ArtifactValidationError('Remote package parity report is missing')
    report = json.loads(output.read_text(encoding='utf-8'))
    if (not isinstance(report, dict) or report.get('input_manifest_sha256') != digest
            or report.get('contract') != flow_package.PARITY_CONTRACT or report.get('scope') != 'cohort'
            or report.get('status') not in ('passed', 'mismatch', 'failed')
            or not isinstance(report.get('images'), list) or any(not isinstance(row, dict) for row in report['images'])
            or any(report.get(key) != value for key, value in {**identity, **target}.items())
            or report.get('device') != device or report.get('resolved_device') != device
            or report.get('execution_target') != 'selected_compute'
            or report.get('cohort_sha256') != flow_package.parity_cohort_sha256(row['sha256'] for row in frozen)
            or report.get('image_count') != len(frozen) or len(report.get('images', [])) != len(frozen)):
        raise ArtifactValidationError('Remote parity report has a different package, input, profile or device binding')
    completed = sum(row.get('status') in ('passed', 'mismatch') for row in report['images'])
    if (type(report.get('completed_count')) is not int or report['completed_count'] != completed
            or any(row.get('status') not in ('passed', 'mismatch', 'failed', 'not_run') for row in report['images'])
            or report['status'] == 'passed' and (completed != len(frozen)
                or any(row.get('status') != 'passed' or row.get('mismatched_fields') for row in report['images'])
                or report.get('mismatched_fields') or report.get('error'))):
        raise ArtifactValidationError('Remote parity passed/completed evidence is incomplete or inconsistent')
    operation_dir = output.parent.parent
    files = {row['path']: row['sha256'] for row in json.loads((package / 'manifest.json').read_text(encoding='utf-8'))['files']}
    runner = report.get('packaged_runtime') or {}
    if (report.get('remote_operation_id') != operation_dir.name
            or report.get('worker_spec_sha256') != _sha256(operation_dir / 'spec.json')
            or report.get('worker_code_archive_sha256') != _sha256(operation_dir / 'remote_code.tar.gz')
            or (report.get('reference_runtime') or {}).get('engine_sha256') != files['backend/engine/flowchart_engine.py']
            or runner.get('package_runner_sha256') != files['run_flow.py']
            or runner.get('package_runtime_sha256') != files['backend/engine/flow_package_runtime.py']):
        raise ArtifactValidationError('Remote parity runtime/source or owned operation proof differs')
    if report.get('status') in ('passed', 'mismatch'):
        identity_error = _device_evidence_error(report, device)
        if identity_error:
            raise ArtifactValidationError(identity_error)
    for expected, row in zip(rows, report['images']):
        if (row.get('index') != expected['index'] or row.get('image_sha256') != expected['sha256']
                or row.get('image_id') != expected['image_id']):
            raise ArtifactValidationError('Remote parity image receipt differs from the frozen cohort')
    verify_flow_package(package)
    if flow_package._parity_identity(package) != identity or flow_package._package_input_mismatch(package, pipeline, checkpoints):
        raise ArtifactValidationError('Source package or checkpoint changed during remote parity')
    for original, row in zip(frozen, report['images']):
        if _sha256(Path(original['path'])) != original['sha256']:
            raise ArtifactValidationError('Source parity image changed during remote execution')
        row['image_path'] = original['path']
    return report


def _unpack(package_archive: Path, root: Path) -> None:
    with tarfile.open(package_archive, 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 1000 or sum(member.size for member in members) > MAX_PACKAGE_BYTES:
            raise ValueError('Remote package archive exceeds extraction bounds')
        seen = set()
        for member in members:
            name = member.name
            if (not member.isfile() or member.size < 0 or name in seen or '\\' in name
                    or PurePosixPath(name).is_absolute() or any(part in ('', '.', '..') for part in name.split('/'))):
                raise ValueError('Remote package archive contains an unsafe or duplicate member')
            seen.add(name)
            target = root.joinpath(*PurePosixPath(name).parts)
            if any(path.is_symlink() for path in (target, *target.parents)):
                raise ValueError('Remote package extraction cannot follow links')
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open('xb') as destination:
                import shutil
                shutil.copyfileobj(source, destination)


def run_package_parity(spec_path: Path) -> dict:
    from backend.remote import worker
    from backend.engine import flow_package
    from backend.engine.flow_package_runtime import verify_flow_package
    from backend.engine.runtime_device_identity import runtime_device_identity
    from backend.engine.edge_runtime import enforce_edge_device
    spec_path = Path(spec_path).absolute()
    run = spec_path.parent
    started = worker._start_operation(spec_path, 'package_parity')
    if isinstance(started, dict):
        return started
    status = started
    previous = {key: os.environ.get(key) for key in ('VISION_PACKAGE_PARITY_IDENTITY', 'VISION_PACKAGE_PARITY_CUDA_BUDGET_MB')}
    try:
        spec = worker._read_operation_spec(spec_path, 'package_parity')
        binding = spec['parity_binding']
        if _digest(binding) != spec['input_manifest_sha256'] or binding['device'] != spec['device']:
            raise ArtifactValidationError('Remote package parity immutable binding differs')
        archive = worker._run_relative_file(run, 'inputs/package.tar.gz', 'package archive')
        if archive.is_symlink() or archive.stat().st_size != binding['archive_size'] or _sha256(archive) != binding['archive_sha256']:
            raise ArtifactValidationError('Remote package archive hash or size differs')
        package = run / 'package'
        _unpack(archive, package)
        pipeline, checkpoints = verify_flow_package(package)
        if flow_package._parity_identity(package) != binding['package']:
            raise ArtifactValidationError('Remote package manifest identity differs')
        enforce_edge_device(package, spec['device'])
        images = []
        if not 2 <= len(binding['images']) <= flow_package.MAX_PARITY_IMAGES:
            raise ValueError('Remote parity requires a bounded frozen cohort')
        for index, row in enumerate(binding['images']):
            if row['index'] != index or not row['path'].startswith('inputs/images/'):
                raise ValueError('Remote parity image order/path is invalid')
            image = worker._run_relative_file(run, row['path'], 'parity image')
            if (image.is_symlink() or not 0 < row['size'] <= MAX_IMAGE_BYTES or image.stat().st_size != row['size']
                    or _sha256(image) != row['sha256']):
                raise ArtifactValidationError('Remote parity input hash or size differs')
            images.append({'path': str(image), 'image_id': row['image_id']})
        worker.apply_memory_budget(spec)
        os.environ['VISION_PACKAGE_PARITY_IDENTITY'] = '1'
        budget = (spec.get('resources') or {}).get('memory_budget_mb')
        if budget:
            os.environ['VISION_PACKAGE_PARITY_CUDA_BUDGET_MB'] = str(budget // 2)
        else:
            os.environ.pop('VISION_PACKAGE_PARITY_CUDA_BUDGET_MB', None)
        reference_identity = runtime_device_identity(spec['device'])
        if (run / 'cancel').exists():
            return status.update(status='aborted')
        status.update(status='running', device=spec['device'])
        report = flow_package.verify_flow_parity_cohort(package_dir=package, pipeline=pipeline, checkpoints=checkpoints,
                                                       images=images, device=spec['device'], timeout_per_image=300)
        report.update(execution_target='selected_compute', input_manifest_sha256=spec['input_manifest_sha256'],
                      remote_operation_id=run.name, worker_spec_sha256=_sha256(spec_path),
                      worker_code_archive_sha256=_sha256(run / 'code.tar.gz'), **binding['target'])
        report['reference_runtime']['runtime_device_identity'] = reference_identity
        identity_error = _device_evidence_error(report, spec['device'])
        if identity_error:
            report.update(status='failed', error=identity_error)
        if (run / 'cancel').exists():
            return status.update(status='aborted')
        _atomic_json(run / 'outputs' / 'parity_report.json', report)
        manifest = worker._operation_artifact_manifest(run, spec, 'package_parity', ('outputs/parity_report.json',))
        _atomic_json(run / 'artifacts.json', manifest)
        return status.update(status='completed')
    except Exception as exc:
        return worker._failed_status(status, exc, run)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
