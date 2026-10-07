"""Owned compute recovery separates process death, cancellation and uncertainty."""
from __future__ import annotations

import errno
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.remote import coordinator, worker
from backend.remote.profiles import ComputeProfile
from backend.remote.ssh_transport import SSHTransport
# These FakeRemote lifecycle controls do not execute pretrained weights.
# Reuse the bounded transfer fixture; actual model execution is qualified separately.
from backend.tests.test_remote_coordinator import bounded_fake_foundation


@pytest.fixture
def owned_run(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    monkeypatch.setattr(coordinator, 'POLL_INTERVAL_SECONDS', 0)
    profile = ComputeProfile(id='test', name='Test', ssh_target='host', ssh_port=22,
                             remote_root=str(tmp_path / 'server'), runtime_kind='python', runtime_value='python3')
    output = tmp_path / 'output'
    output.mkdir()
    record = SimpleNamespace(job_id='job_owned', output_dir=str(output), preparation_cancel=threading.Event(),
                             phase='running', best_metric=None)
    journal = {'protocol_version': 1, 'job_id': record.job_id, 'operation': 'train', 'state': 'launched',
               'remote_handle': '12345', 'profile': profile.model_dump(), 'dataset_path': str(tmp_path / 'data'),
               'output_dir': str(output), 'task': 'classification'}
    coordinator._save_journal(journal)
    return record, profile, journal


class StaleWorker:
    def __init__(self, running):
        self.running = running
        self.reads = 0
        self.signals = []

    def exec(self, profile, argv, **kwargs):
        self.reads += 1
        if self.reads > 4:
            return subprocess.CompletedProcess(argv, 255, '', 'network unavailable')
        if argv[-1].endswith('terminal_status.json'):
            return subprocess.CompletedProcess(argv, 1, '', '')
        return subprocess.CompletedProcess(argv, 0, json.dumps({'protocol_version': 1, 'job_id': 'job_owned',
                                        'operation': 'train', 'status': 'running'}), '')

    def is_running(self, profile, run_id, handle):
        return self.running

    def touch_cancel(self, profile, run_id):
        return subprocess.CompletedProcess([], 0, '', '')

    def stop_owned(self, profile, run_id, handle, *, force=False):
        self.signals.append((run_id, handle, force))
        if force:
            self.running = False
        return True


def test_stale_running_receipt_cannot_hide_confirmed_owned_worker_death(owned_run):
    record, profile, journal = owned_run
    result = coordinator.run_remote_training(record, profile, transport=StaleWorker(False), resume=True)
    assert result['status'] == 'failed'
    assert 'exited' in result['error']
    assert json.loads((Path(record.output_dir) / 'remote_job.json').read_text())['state'] == 'failed'


def test_unknown_liveness_preserves_nonterminal_claim(owned_run):
    record, profile, journal = owned_run
    result = coordinator.run_remote_training(record, profile, transport=StaleWorker(None), resume=True)
    assert result['status'] == 'disconnected'
    assert json.loads((Path(record.output_dir) / 'remote_job.json').read_text())['state'] == 'launched'


@pytest.mark.parametrize('state', ['completed', 'aborted', 'failed'])
@pytest.mark.parametrize('running', [True, None])
def test_terminal_publication_keeps_claim_until_owned_worker_exit(owned_run, monkeypatch, state, running):
    record, profile, journal = owned_run
    class PublishedWorker(StaleWorker):
        def exec(self, profile, argv, **kwargs):
            if argv[-1].endswith('training_state_artifact.json'):
                return subprocess.CompletedProcess(argv, 1, '', 'No completed epoch state')
            return subprocess.CompletedProcess(argv, 0, json.dumps({'protocol_version': 1,
                'job_id': record.job_id, 'operation': 'train', 'status': state}), '')
        def stop_owned(self, profile, run_id, handle, *, force=False):
            self.signals.append((run_id, handle, force))
            return True  # Publication and signals do not prove exit.
    monkeypatch.setattr(coordinator, 'TERMINAL_EXIT_GRACE_SECONDS', 0, raising=False)
    monkeypatch.setattr(coordinator, 'TERMINAL_EXIT_TERMINATE_SECONDS', 0, raising=False)
    monkeypatch.setattr(coordinator, 'TERMINAL_EXIT_CONFIRM_SECONDS', 0, raising=False)
    monkeypatch.setattr(coordinator, '_copy_artifacts', lambda *args: None)
    remote = PublishedWorker(running)
    result = coordinator.run_remote_training(record, profile, transport=remote, resume=True)
    assert result['status'] == 'disconnected'
    assert json.loads((Path(record.output_dir) / 'remote_job.json').read_text())['state'] == 'launched'
    assert remote.signals == ([('job_owned', '12345', False), ('job_owned', '12345', True)] if running else [])


def test_terminal_owned_cleanup_preserves_published_outcome_after_confirmed_exit(owned_run, monkeypatch):
    record, profile, journal = owned_run
    class PublishedWorker(StaleWorker):
        def exec(self, profile, argv, **kwargs):
            if argv[-1].endswith('training_state_artifact.json'):
                return subprocess.CompletedProcess(argv, 1, '', 'No completed epoch state')
            return subprocess.CompletedProcess(argv, 0, json.dumps({'protocol_version': 1,
                'job_id': record.job_id, 'operation': 'train', 'status': 'aborted'}), '')
    monkeypatch.setattr(coordinator, 'TERMINAL_EXIT_GRACE_SECONDS', 0, raising=False)
    monkeypatch.setattr(coordinator, 'TERMINAL_EXIT_TERMINATE_SECONDS', 0, raising=False)
    remote = PublishedWorker(True)
    result = coordinator.run_remote_training(record, profile, transport=remote, resume=True)
    assert result['status'] == 'aborted' and result['worker_exit_confirmed'] is True
    assert remote.signals == [('job_owned', '12345', False), ('job_owned', '12345', True)]


def test_training_lost_launch_acknowledgment_recovers_bound_worker_without_relaunch(tmp_path, monkeypatch):
    from backend.tests.test_remote_coordinator import _setup, FakeRemote
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    _, output, profile, record = _setup(tmp_path)
    class LostAcknowledgment(FakeRemote):
        def launch(self, selected, argv, run_id):
            super().launch(selected, argv, run_id)
            run = self.root / 'runs' / run_id
            spec = json.loads((run / 'spec.json').read_text())
            token = 'a' * 32
            (run / 'worker_identity.json').write_text(json.dumps({'protocol_version': 1, 'run_id': run_id,
                'job_id': spec['job_id'], 'operation': 'train', 'spec_sha256': coordinator._sha256(run / 'spec.json'),
                'control_kind': 'python', 'control_handle': '123:' + token, 'pid': 123, 'group': 123, 'session': 123, 'token': token}))
            raise ConnectionError('Owned worker launched; acknowledgment was lost')
        def recover_handle(self, *args, **kwargs):
            return SSHTransport.recover_handle(self, *args, **kwargs)
    remote = LostAcknowledgment(Path(profile.remote_root))
    first = coordinator.run_remote_training(record, profile, transport=remote)
    assert first['status'] == 'disconnected'
    assert not json.loads((output / 'remote_job.json').read_text()).get('remote_handle')
    second = coordinator.run_remote_training(record, profile, transport=remote, resume=True)
    assert second['status'] == 'completed' and second['worker_exit_confirmed'] is True
    saved = json.loads((output / 'remote_job.json').read_text())
    assert saved['launch_acknowledgment_recovered'] is True and saved['remote_handle'] == '123:' + 'a' * 32
    assert remote.launches == 1


@pytest.mark.parametrize('damage', [None, 'job_id', 'run_id', 'operation', 'spec_sha256', 'control_kind', 'group', 'token'])
def test_recovered_handle_requires_exact_run_spec_and_private_session(owned_run, monkeypatch, damage):
    record, profile, _ = owned_run
    token = 'a' * 32; spec_hash = 'b' * 64
    receipt = {'protocol_version': 1, 'run_id': record.job_id, 'job_id': record.job_id, 'operation': 'train',
        'spec_sha256': spec_hash, 'control_kind': 'python', 'control_handle': '123:' + token,
        'pid': 123, 'group': 123, 'session': 123, 'token': token}
    if damage:
        receipt[damage] = 'foreign' if damage != 'group' else 999
    monkeypatch.setattr(SSHTransport, 'exec', lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(receipt), ''))
    result = SSHTransport().recover_handle(profile, record.job_id, job_id=record.job_id, operation='train', spec_sha256=spec_hash)
    assert result == (None if damage else '123:' + token)


@pytest.mark.parametrize('state', ['completed', 'aborted', 'failed'])
@pytest.mark.parametrize('running', [True, None])
def test_operation_terminal_publication_retains_uncertain_resource_lease(tmp_path, monkeypatch, state, running):
    from backend.remote import operations
    from backend.engine.shared_scheduler import shared_leases
    from backend.tests.test_remote_operations import _completed_remote, FakeEvaluationRemote
    context, _ = _completed_remote(tmp_path, monkeypatch)
    class PublishedOperation(FakeEvaluationRemote):
        def launch(self, profile, argv, run_id):
            handle = super().launch(profile, argv, run_id)
            path = self.root / 'runs' / run_id / 'status.json'
            value = json.loads(path.read_text()); value['status'] = state
            path.write_text(json.dumps(value))
            return handle
        def is_running(self, profile, run_id, handle):
            return running
        def stop_owned(self, profile, run_id, handle, *, force=False):
            return True
    for setting in ('TERMINAL_EXIT_GRACE_SECONDS', 'TERMINAL_EXIT_TERMINATE_SECONDS', 'TERMINAL_EXIT_CONFIRM_SECONDS'):
        monkeypatch.setattr(coordinator, setting, 0, raising=False)
    with pytest.raises(coordinator.RemoteDisconnected):
        operations.run_remote_evaluation(context, transport=PublishedOperation(Path(context.profile.remote_root)))
    claims = shared_leases().list()
    assert len(claims) == 1 and claims[0]['uncertain'] == 1


def test_durable_cancel_survives_fresh_event_and_escalates_only_owned_handle(owned_run, monkeypatch):
    record, profile, journal = owned_run
    coordinator.request_remote_cancellation(record)
    assert record.preparation_cancel.is_set() is False
    saved = json.loads((Path(record.output_dir) / 'remote_job.json').read_text())
    assert saved['cancel_requested_at'] > 0
    monkeypatch.setattr(coordinator, 'CANCEL_GRACE_SECONDS', 0)
    monkeypatch.setattr(coordinator, 'CANCEL_TERMINATE_SECONDS', 0)
    remote = StaleWorker(True)
    remote.reads = -100
    result = coordinator.run_remote_training(record, profile, transport=remote, resume=True)
    assert result['status'] == 'aborted'
    assert remote.signals == [('job_owned', '12345', False), ('job_owned', '12345', True)]


def test_confirmed_dead_worker_is_terminal_when_local_journal_disk_is_full(owned_run, monkeypatch):
    record, profile, journal = owned_run
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(coordinator, '_save_journal', full)
    result = coordinator.run_remote_training(record, profile, transport=StaleWorker(False), resume=True)
    assert result['status'] == 'failed'
    assert result['journal_persisted'] is False


def test_worker_publishes_failed_terminal_receipt_in_reserved_blocks_when_disk_full(tmp_path, monkeypatch):
    status = worker._StatusWriter(tmp_path, 'job_owned')
    status.update(status='running')
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(worker, '_atomic_json', full)
    result = worker._failed_status(status, OSError(errno.ENOSPC, 'No space left on device'), tmp_path)
    assert result['status'] == 'failed'
    terminal = json.loads((tmp_path / 'terminal_status.json').read_text())
    assert terminal['job_id'] == 'job_owned' and terminal['status'] == 'failed'
    assert json.loads((tmp_path / 'status.json').read_text())['status'] == 'running'


def test_cancellation_never_signals_pid_with_another_owned_identity(owned_run, monkeypatch):
    record, profile, journal = owned_run
    commands = []
    def execute(profile, argv, **kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 3, '', 'Worker identity differs')
    monkeypatch.setattr(SSHTransport, 'exec', staticmethod(execute))
    assert SSHTransport().stop_owned(profile, record.job_id, journal['remote_handle'], force=True) is None
    assert len(commands) == 1
    assert 'killall' not in ' '.join(commands[0])


def test_ssh_connections_have_explicit_connect_and_keepalive_bounds(owned_run):
    _, profile, _ = owned_run
    for command in (SSHTransport._ssh_base(profile), SSHTransport._scp_base(profile)):
        assert 'ConnectTimeout=10' in command
        assert 'ServerAliveInterval=5' in command
        assert 'ServerAliveCountMax=2' in command


def test_optional_ssh_control_path_is_bound_to_user_host_and_port(owned_run, monkeypatch):
    _, profile, _ = owned_run
    monkeypatch.setenv('VISION_AI_STUDIO_SSH_CONTROL_PATH', '/private/tmp/modu-stage42-%C')
    for command in (SSHTransport._ssh_base(profile), SSHTransport._scp_base(profile)):
        assert 'ControlPath=/private/tmp/modu-stage42-%C' in command
    monkeypatch.setenv('VISION_AI_STUDIO_SSH_CONTROL_PATH', '/private/tmp/shared-socket')
    with pytest.raises(ValueError, match='%C'):
        SSHTransport._ssh_base(profile)


def test_dead_worker_terminal_reserve_is_read_instead_of_stale_running(owned_run):
    record, profile, journal = owned_run
    class FullDiskWorker(StaleWorker):
        def exec(self, profile, argv, **kwargs):
            result = super().exec(profile, argv, **kwargs)
            if argv[-1].endswith('terminal_status.json'):
                return subprocess.CompletedProcess(argv, 0, json.dumps({'protocol_version': 1, 'job_id': record.job_id,
                    'operation': 'train', 'status': 'failed', 'error': 'No space left on device'}), '')
            return result
    result = coordinator.run_remote_training(record, profile, transport=FullDiskWorker(False), resume=True)
    assert result['status'] == 'failed'
    assert result['error'] == 'No space left on device'


def _process(proc, pid, group, token, *, start='100', command=None):
    directory = proc / str(pid)
    directory.mkdir(parents=True)
    fields = ['S', '1', str(group), str(group)] + ['0'] * 15 + [start]
    (directory / 'stat').write_text(f'{pid} (python worker) ' + ' '.join(fields))
    (directory / 'cmdline').write_bytes(b'\0'.join(command or [b'python', b'dataloader']))
    (directory / 'environ').write_bytes(('MODU_VISION_WORKER_TOKEN=' + token).encode())


def test_liveness_preserves_owned_children_after_leader_exit_and_rejects_pid_reuse(tmp_path):
    from backend.remote.process_control import owned_members
    run = tmp_path / 'run'
    run.mkdir()
    proc = tmp_path / 'proc'
    token = 'a' * 32
    (run / 'worker_identity.json').write_text(json.dumps({'pid': 123, 'group': 123, 'session': 123,
                                                        'token': token, 'start_ticks': '100'}))
    _process(proc, 124, 123, token)
    assert [row['pid'] for row in owned_members(run, 123, proc, expected_token=token)] == [124]
    _process(proc, 123, 123, token, start='200', command=[b'python', b'backend.remote.worker', str(run / 'spec.json').encode()])
    assert owned_members(run, 123, proc, expected_token=token) is None


def test_liveness_rejects_unmarked_process_in_owned_session(tmp_path):
    from backend.remote.process_control import owned_members
    run = tmp_path / 'run'
    run.mkdir()
    proc = tmp_path / 'proc'
    (run / 'worker_identity.json').write_text(json.dumps({'pid': 123, 'group': 123, 'session': 123,
                                                        'token': 'owned', 'start_ticks': '100'}))
    _process(proc, 124, 123, 'unrelated')
    assert owned_members(run, 123, proc) is None


def test_interrupted_upload_reconnects_from_durable_transfers_without_duplicate_launch(tmp_path, monkeypatch):
    from backend.tests.test_remote_coordinator import FakeRemote, _setup
    from backend.remote.ssh_transport import SSHTransportError
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    _, output, profile, record = _setup(tmp_path)
    class InterruptedTransfer(FakeRemote):
        def __init__(self, root):
            super().__init__(root)
            self.interrupted = False
        def upload(self, *args, **kwargs):
            result = super().upload(*args, **kwargs)
            if not self.interrupted:
                self.interrupted = True
                raise SSHTransportError('connection dropped after partial upload')
            return result
    remote = InterruptedTransfer(Path(profile.remote_root))
    result = coordinator.run_remote_training(record, profile, transport=remote, config_overrides={'pretrained': False})
    assert result['status'] == 'disconnected'
    saved = json.loads((output / 'remote_job.json').read_text())
    assert saved['state'] == 'transferring' and saved['transfers']
    assert remote.launches == 0
    assert coordinator.reconnect_remote_training(record, transport=remote)['status'] == 'completed'
    assert remote.launches == 1


def test_changed_upload_source_blocks_reconnect_before_launch(tmp_path, monkeypatch):
    from backend.tests.test_remote_coordinator import FakeRemote, _setup
    from backend.remote.ssh_transport import SSHTransportError
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    _, output, profile, record = _setup(tmp_path)
    class InterruptedTransfer(FakeRemote):
        def upload(self, *args, **kwargs):
            raise SSHTransportError('connection dropped')
    coordinator.run_remote_training(record, profile, transport=InterruptedTransfer(Path(profile.remote_root)), config_overrides={'pretrained': False})
    (output / 'remote_code.tar.gz').write_bytes(b'changed source')
    remote = FakeRemote(Path(profile.remote_root))
    result = coordinator.reconnect_remote_training(record, transport=remote)
    assert result['status'] == 'failed'
    assert remote.launches == 0


def test_resumable_upload_uses_partial_basis_without_truncating_remote_file(owned_run, tmp_path, monkeypatch):
    _, profile, _ = owned_run
    source = tmp_path / 'archive.tar'
    source.write_bytes(b'complete source')
    remote_commands = []
    transfer_commands = []
    transport = SSHTransport()
    monkeypatch.setattr(transport, 'supports_resumable_transfer', lambda p: True)
    def execute(profile, argv, **kwargs):
        remote_commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(transport, 'exec', execute)
    class Transfer:
        returncode = 0
        def __init__(self, argv, **kwargs):
            transfer_commands.append(argv)
        def communicate(self, **kwargs):
            return '', ''
    monkeypatch.setattr(subprocess, 'Popen', Transfer)
    transport.upload(profile, source, 'runs/job_owned/archive.tar')
    command = transfer_commands[0]
    assert command[0] == 'rsync'
    assert '--partial' in command and '--partial-dir=.transfer-partials' in command and '--checksum' in command
    assert not any(row[0] == 'install' for row in remote_commands)


def test_remote_model_image_installs_pinned_default_architecture_providers():
    root = Path(__file__).resolve().parents[2]
    manifest = root / 'build' / 'remote' / 'requirements-models.lock'
    pins = {row.split('==')[0]: row.split('==')[1] for row in manifest.read_text().splitlines() if '==' in row}
    assert pins['timm'] == '1.0.24'
    assert pins['ultralytics'] == '8.4.41'
    assert 'safetensors' in pins and 'huggingface-hub' in pins
    docker = (manifest.parent / 'Dockerfile').read_text()
    assert 'COPY requirements-models.lock' in docker and '-r /opt/modu-vision/requirements-models.lock' in docker


def test_model_dependency_lock_is_readable_by_numeric_container_worker():
    root = Path(__file__).resolve().parents[2]
    docker = (root / 'build' / 'remote' / 'Dockerfile').read_text()
    assert 'chmod 644 /opt/modu-vision/requirements-models.lock' in docker


def test_resumable_transfer_permission_option_is_accepted_by_system_rsync(tmp_path):
    import shutil
    if not shutil.which('rsync'):
        pytest.skip('No rsync transport installed')
    profile = ComputeProfile(id='permission-test', name='Permission test', ssh_target='host', ssh_port=22,
                             remote_root='/private-remote', runtime_kind='python', runtime_value='python3')
    source = tmp_path / 'source.bin'
    source.write_bytes(b'partial-transfer bytes')
    destination = tmp_path / 'destination.bin'
    command = SSHTransport()._rsync_argv(profile, str(source), str(destination))
    option = next(arg for arg in command if arg.startswith('--chmod='))
    assert '--perms' in command
    result = subprocess.run(['rsync', '--perms', option, str(source), str(destination)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert destination.read_bytes() == source.read_bytes()
    assert destination.stat().st_mode & 0o777 == 0o600


def test_docker_probe_keeps_host_gpu_indexes_for_reservations(monkeypatch):
    profile = ComputeProfile(id='mapped', name='Mapped', ssh_target='host', ssh_port=22,
                             remote_root='/private-remote', runtime_kind='docker', runtime_value='worker:qa', gpu_selector='2')
    dependencies = {name: True for name in ('torch', 'torchvision', 'cv2', 'numpy', 'PIL', 'sklearn', 'psutil', 'fastapi', 'pydantic')}
    visible = {'devices': [{'selector': '0', 'uuid': 'GPU-2222', 'memory_mb': 46068, 'parent_uuid': None, 'kind': 'cuda'}]}
    checks = {'protocol_version': 1, 'runtime_dependencies': dependencies, 'remote_root_exists': True,
              'free_bytes': 10_000_000_000, 'device_type': 'cuda', 'device_name': 'QA GPU', 'device_inventory': visible, 'cuda_device_count': 1}
    transport = SSHTransport()
    def execute(selected, argv, **kwargs):
        output = '1000' if argv[0] == 'id' else '0, GPU-0000, 46068\n2, GPU-2222, 46068\n' if argv[0] == 'nvidia-smi' else json.dumps(checks)
        return subprocess.CompletedProcess(argv, 0, output, '')
    monkeypatch.setattr(transport, 'exec', execute)
    monkeypatch.setattr(transport, 'supports_resumable_transfer', lambda profile: False)
    result = transport.probe(profile)
    assert next(row['selector'] for row in result['checks']['device_inventory']['devices'] if row['uuid'] == 'GPU-2222') == '2'
    assert result['checks']['visible_device_inventory'] == visible


def test_remote_operation_reconciles_stale_running_status_after_owned_death(tmp_path, monkeypatch):
    from backend.tests.test_remote_operations import _completed_remote, FakeRemote
    from backend.remote import operations
    context, _ = _completed_remote(tmp_path, monkeypatch)
    monkeypatch.setattr(operations, 'OP_POLL_INTERVAL_SECONDS', .001)
    class DeadOperation(FakeRemote):
        def launch(self, selected, argv, run_id):
            run = self.root / 'runs' / run_id
            spec = json.loads((run / 'spec.json').read_text())
            (run / 'status.json').write_text(json.dumps({'protocol_version': 1, 'job_id': spec['job_id'],
                                                       'operation': 'evaluate', 'status': 'running'}))
            return 'dead-owned-operation'
        def is_running(self, profile, run_id, handle):
            return False
    with pytest.raises(RuntimeError, match='exited before publishing status'):
        operations.run_remote_operation_artifacts(context, 'evaluate', {}, transport=DeadOperation(Path(context.profile.remote_root)), timeout_seconds=.01)


def test_local_disk_full_after_remote_terminal_is_failure_not_connection_uncertainty(owned_run, monkeypatch):
    record, profile, _ = owned_run
    class Completed(StaleWorker):
        def exec(self, selected, argv, **kwargs):
            if argv[-1].endswith('training_state_artifact.json'):
                return subprocess.CompletedProcess(argv, 1, '', 'No completed epoch state')
            return subprocess.CompletedProcess(argv, 0, json.dumps({'protocol_version': 1, 'job_id': record.job_id,
                                             'operation': 'train', 'status': 'completed'}), '')
    def full(*args):
        raise OSError(errno.ENOSPC, 'No space left for received checkpoint')
    monkeypatch.setattr(coordinator, '_copy_artifacts', full)
    monkeypatch.setattr(coordinator, '_save_journal', full)
    result = coordinator.run_remote_training(record, profile, transport=Completed(False), resume=True)
    assert result['status'] == 'failed'
    assert 'storage' in result['error'].lower()
    assert result['journal_persisted'] is False


def test_download_reports_local_receiver_capacity_error(tmp_path, monkeypatch):
    profile = ComputeProfile(id='full', name='Full', ssh_target='host', ssh_port=22,
                             remote_root='/private-remote', runtime_kind='python', runtime_value='python3')
    transport = SSHTransport()
    monkeypatch.setattr(transport, 'supports_resumable_transfer', lambda profile: True)
    monkeypatch.setattr(subprocess, 'run', lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, '',
                        'rsync: [receiver] write failed on received checkpoint: No space left on device (28)'))
    with pytest.raises(OSError) as caught:
        transport.download(profile, 'runs/job_owned/outputs/best_model.pt', tmp_path / 'model.pt')
    assert caught.value.errno == errno.ENOSPC


def test_selected_model_rejects_importable_but_unsupported_provider_version():
    from backend.remote.ssh_transport import require_training_runtime
    readiness = {'ready': True, 'checks': {'runtime_dependencies': {'timm': True, 'safetensors': True, 'huggingface_hub': True},
                  'runtime_versions': {'timm': '1.0.23', 'safetensors': '0.7.0', 'huggingface_hub': '1.1.4'},
                  'model_dependencies': {'dinov3_vits16': True}}}
    with pytest.raises(ValueError, match='timm'):
        require_training_runtime(readiness, 'classification', 'fast')


def test_cancel_intent_recovers_from_user_index_when_output_disk_is_full(owned_run, monkeypatch):
    record, profile, journal = owned_run
    original = coordinator._atomic_json
    def output_full(path, payload):
        if path.parent == Path(record.output_dir):
            raise OSError(errno.ENOSPC, 'No space left on device')
        original(path, payload)
    monkeypatch.setattr(coordinator, '_atomic_json', output_full)
    coordinator.request_remote_cancellation(record)
    result = coordinator.run_remote_training(record, profile, transport=StaleWorker(False), resume=True)
    assert result['status'] == 'aborted'


def test_manager_abort_persists_intent_before_worker_observes_cancel(tmp_path, monkeypatch):
    from backend.api.routes_training import TrainingJobManager
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    profile = ComputeProfile(id='owned', name='Owned', ssh_target='host', ssh_port=22,
                             remote_root=str(tmp_path / 'server'), runtime_kind='python', runtime_value='python3')
    manager = TrainingJobManager()
    running = threading.Event()
    observed = []
    def runner(record):
        running.set()
        assert record.preparation_cancel.wait(3)
        observed.append(json.loads((Path(record.output_dir) / 'remote_job.json').read_text()).get('cancel_requested_at'))
        return {'status': 'aborted'}
    record = manager.start_remote_job(job_id='job_cancel_intent', task='classification', dataset_path=str(tmp_path / 'data'),
                                      output_dir=str(tmp_path / 'output'), remote_profile_id=profile.id,
                                      remote_runner=runner, profile=profile, launch_spec={'preparation': 'none'})
    assert running.wait(2)
    try:
        assert manager.abort_job(record.job_id)
        record.thread.join(3)
        assert observed and observed[0] > 0
        assert record.status == 'aborted'
    finally:
        record.preparation_cancel.set()
        record.thread.join(3)


def test_manager_adopts_transfer_reservation_without_queueing_behind_itself(tmp_path, monkeypatch):
    from backend.api.routes_training import TrainingJobManager
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    profile = ComputeProfile(id='owned', name='Owned', ssh_target='host', ssh_port=22,
                             remote_root=str(tmp_path / 'server'), runtime_kind='python', runtime_value='python3')
    manager = TrainingJobManager()
    assert manager._leases.acquire('job_transfer_recover', manager._lease_host(profile), 'all', remote=True)
    manager._leases.mark_uncertain('job_transfer_recover')
    started = threading.Event()
    record = manager.start_remote_job(job_id='job_transfer_recover', task='classification', dataset_path=str(tmp_path / 'data'),
                                      output_dir=str(tmp_path / 'output'), remote_profile_id=profile.id,
                                      remote_runner=lambda row: (started.set() or {'status': 'aborted'}), profile=profile,
                                      launch_spec={'preparation': 'none'}, recovery_state='transferring')
    assert started.wait(2)
    assert record.thread is not None
    record.thread.join(3)
    assert record.status == 'aborted'
