"""AutoDL child dispatch through the configured SSH job lifecycle."""
from __future__ import annotations

import itertools
import json
from pathlib import Path
import time


class RemoteTrialUncertain(RuntimeError):
    """An owned detached trial needs reconciliation; its reservation stays held."""


def validate_remote_search(profile, task, preset, device, base, search_space, budget, *, warm_start=False):
    if device not in ('cpu', 'cuda', 'cuda:0'):
        raise ValueError('Remote AutoDL device must be CPU or CUDA; MPS belongs to a local Mac')
    if profile.distributed_processes > 1:
        raise ValueError('Measured remote AutoDL currently needs one worker process; DDP trials are unsupported')
    if base.get('resume_checkpoint'):
        raise ValueError('Remote exact resume needs a verified training-state transfer; local checkpoint paths are unsupported')
    if (base.get('recipe') or {}).get('adapter') == 'ultralytics_yolo_obb':
        raise ValueError('Remote YOLO OBB requires verified transfer of the local recipe model file')
    from backend.engine.automated_trials import _space
    candidates = list(itertools.islice(_space(task, search_space, base, 'search'), budget.get('max_trials', 4)))
    from backend.engine.automated_trials import validate_trial_controls
    for candidate in candidates:validate_trial_controls(task,preset,candidate)
    from backend.remote.ssh_transport import SSHTransport, require_training_runtime
    readiness = SSHTransport().probe(profile)
    for candidate in candidates:
        require_training_runtime(readiness, task, preset, candidate, warm_start=warm_start)
    checks = readiness.get('checks') or {}
    if device.startswith('cuda') and checks.get('device_type') != 'cuda':
        raise ValueError('Selected remote worker has no probe-observed CUDA device')
    observed = checks.get('device_inventory', {}).get('devices', [])
    from backend.api.routes_training import training_job_manager
    if observed: training_job_manager._leases.configure_devices(training_job_manager._lease_host(profile), observed)
    if profile.allow_sharing and not observed: raise ValueError('GPU sharing requires probe-observed physical device capacity')
    memory = budget.get('max_memory_mb')
    if profile.memory_budget_mb and memory and memory > profile.memory_budget_mb:
        raise ValueError('AutoDL memory budget exceeds the selected profile allocation')
    return profile.model_copy(update={'memory_budget_mb': memory or profile.memory_budget_mb})


def run_remote_candidate(context, *, profile, search_id, source_dataset_path, training_binding, owner,
                         remaining_seconds, configuration_parent=None):
    from backend.api.routes_training import training_job_manager
    from backend.remote.coordinator import make_remote_runner
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    spec = {'preparation': 'none', 'operation': 'train', 'config_overrides': context.config,
            'device': context.device, 'dataset_binding': training_binding,
            'family_dataset_path': str(context.dataset_path), 'measured_candidate': True,
            'parent_search_id': search_id, 'trial_id': context.output_dir.name,
            'automated_training': {'search_id': search_id, 'trial_id': context.output_dir.name,
                                   'configuration_parent': configuration_parent},
            'max_runtime_s': remaining_seconds, **(owner or {})}
    native_family = context.task in {'rotation', 'ocr', 'rotated_detection', 'enhancement', 'defect_gan'}
    remote_id = 'job_' + context.output_dir.name if native_family else context.output_dir.name
    if native_family: spec['local_model_id'] = context.output_dir.name
    if context.warm_start:
        parent = context.warm_start
        spec['warm_start'] = {**vars(parent), 'checkpoint_path': str(parent.checkpoint_path), 'classes': list(parent.classes)}
    record = training_job_manager.start_remote_job(job_id=remote_id, task=context.task,
        dataset_path=str(context.dataset_path), output_dir=str(context.output_dir), preset=context.preset,
        remote_profile_id=profile.id, profile=profile, remote_runner=make_remote_runner(profile, spec),
        source_dataset_path=str(source_dataset_path), dataset_fingerprint=fingerprint_dataset(Path(source_dataset_path)),
        launch_spec=spec, dataset_binding=training_binding, warm_start=context.warm_start)
    cancel_sent = False
    while record.status in ('queued', 'preparing', 'running', 'stopping'):
        if context.cancel_event.is_set() and not cancel_sent:
            training_job_manager.abort_job(remote_id); cancel_sent = True
        context.on_progress({'epoch': record.current_epoch, 'remote_job_id': remote_id,
                             'compute_profile_id': profile.id, 'phase': record.phase})
        if record.thread is not None: record.thread.join(timeout=.1)
        else: time.sleep(.1)
        if record.status == 'stopping' and record.thread is not None and not record.thread.is_alive():
            raise RemoteTrialUncertain(f'Remote trial {remote_id} cancellation is awaiting worker reconciliation')
    if record.status == 'disconnected':
        if context.cancel_event.is_set(): training_job_manager.abort_job(remote_id)
        raise RemoteTrialUncertain(f'Remote trial {remote_id} disconnected; reconcile its reserved worker before continuing')
    if record.status == 'aborted' or context.cancel_event.is_set(): raise InterruptedError('Owned remote candidate cancelled')
    if record.status != 'completed': raise RuntimeError(f'Remote candidate failed: {record.error or record.status}')
    # Wait for the owned manager thread to publish its final verified receipt.
    if record.thread is not None:
        while record.thread.is_alive():record.thread.join(timeout=.1)
    metadata = json.loads((context.output_dir / 'model_meta.json').read_text(encoding='utf-8'))
    measurement = metadata.get('measured_candidate')
    if not isinstance(measurement, dict): raise ValueError('Remote worker returned no measured validation and latency receipt')
    return {**measurement, 'checkpoint_path': str(context.output_dir / 'best_model.pt'),
            'remote_job_id': remote_id, 'compute_profile_id': profile.id}
