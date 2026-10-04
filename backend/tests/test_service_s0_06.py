"""S0-06: reproduce recovery and security candidates in isolated fixtures.

Each case records an AuditCase(candidate, setup, observed, status, remediation).
A candidate is only called a defect after its fixture reproduces it.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil
import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_training
from backend.engine.local_training_worker import recover_local_jobs


@dataclass(frozen=True)
class AuditCase:
    candidate: str
    setup: str
    observed: str
    # confirmed_defect: reproduced in a fixture | code_path_confirmed: traced, not executed |
    # candidate_not_reproduced | design_limit | not_a_defect
    status: str
    remediation: str


TINY_TRAINING = {'pretrained': False, 'backbone': 'resnet18', 'epochs': 1, 'image_size': 32,
                 'batch_size': 2, 'num_workers': 0}


@pytest.fixture
def local_data(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    source = tmp_path / 'source'
    for split in ('train', 'val'):
        for label in ('OK', 'NG'):
            directory = source / split / label
            directory.mkdir(parents=True)
            for index in range(2):
                Image.new('RGB', (32, 32), 'red' if label == 'NG' else 'blue').save(directory / f'{index}.png')
    return source, tmp_path


def _completed_job(source, root, job_id):
    manager = routes_training.TrainingJobManager()
    record = manager.start_job(job_id, 'classification', str(source), str(root / job_id), device='cpu',
                               config_overrides=TINY_TRAINING)
    record.thread.join(90)
    # a failed worker keeps its private traceback beside its output; show it, so a CI failure names its cause
    trace = root / job_id / 'worker_error.log'
    assert record.status == 'completed', (record.error, trace.read_text(encoding='utf-8')[-3000:] if trace.is_file() else 'no worker_error.log')
    assert not manager._leases.list()
    index = Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']) / 'local_jobs' / f'{job_id}.json'
    journal = json.loads(index.read_text())
    assert journal['status'] == 'completed' and journal['worker_exit_confirmed'] is True
    return index, journal


def _restart_and_assert_available(source, root, restored_id):
    restarted = routes_training.TrainingJobManager()
    recover_local_jobs(restarted)
    restored = restarted.get_job(restored_id)
    if restored is not None and restored.thread is not None:
        restored.thread.join(30)
    assert restored is None or restored.status == 'completed', restored and (restored.status, restored.error)
    assert not restarted.is_training
    assert not [row for row in restarted._leases.list() if row['job_id'] == restored_id]
    follow_up = restarted.start_job(f'{restored_id}_next', 'classification', str(source), str(root / f'{restored_id}_next'),
                                    device='cpu', config_overrides=TINY_TRAINING)
    follow_up.thread.join(90)
    trace = Path(follow_up.output_dir) / 'worker_error.log'  # the follow-up's private traceback, if its worker failed
    assert follow_up.status == 'completed', (follow_up.error, trace.read_text(encoding='utf-8')[-3000:] if trace.is_file() else 'no worker_error.log')
    return restored


def test_restart_with_reused_owner_pid_does_not_block_new_training(local_data):
    """AuditCase: local journal + PID reuse after a completed job."""
    source, root = local_data
    index, journal = _completed_job(source, root, 'job_s006_reused_pid')
    bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    try:
        # After a reboot or long uptime the recorded owner PID can belong to an
        # unrelated process with a different creation time.
        assert abs(psutil.Process(bystander.pid).create_time() - journal['owner_created_at']) >= .01
        index.write_text(json.dumps({**journal, 'owner_pid': bystander.pid}))
        _restart_and_assert_available(source, root, 'job_s006_reused_pid')
        assert bystander.poll() is None, 'the unrelated process is never signalled'
    finally:
        bystander.kill()
        bystander.wait(5)


def _unconfirmed(journal):
    return {key: value for key, value in journal.items() if key not in ('worker_exit_confirmed', 'worker_exit_code')}


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process-group reservation')
def test_reused_leader_pid_before_exit_confirmation_is_resolved(local_data):
    """AuditCase: the observer crashed before confirming exit and the PID was reused."""
    source, root = local_data
    index, journal = _completed_job(source, root, 'job_s006_reused_unconfirmed')
    bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    try:
        index.write_text(json.dumps({**_unconfirmed(journal), 'owner_pid': bystander.pid}))
        _restart_and_assert_available(source, root, 'job_s006_reused_unconfirmed')
        assert bystander.poll() is None, 'the unrelated process is never signalled'
    finally:
        bystander.kill()
        bystander.wait(5)


@pytest.fixture
def unmarked_session():
    # The runner's session can contain inaccessible service processes. Own the
    # complete unrelated session used by these collision controls instead.
    environment = {key: value for key, value in os.environ.items()
                   if key != 'MODU_VISION_LOCAL_WORKER_TOKEN'}
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'],
                               start_new_session=True, env=environment)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(5)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX session identifiers')
@pytest.mark.parametrize('variant', ['boot_id_changed', 'legacy_journal_without_boot_id'])
def test_reboot_session_collision_before_exit_confirmation(local_data, variant, unmarked_session):
    """AuditCase: observer crashed before confirming exit, then the machine rebooted."""
    source, root = local_data
    index, journal = _completed_job(source, root, f'job_s006_rebooted_{variant}')
    stale = _unconfirmed(journal)
    gone = subprocess.Popen([sys.executable, '-c', 'pass'])
    gone.wait(5)
    # The recorded session now matches unrelated, unmarked processes.
    stale.update(owner_pid=gone.pid, owner_session=unmarked_session.pid)
    if variant == 'boot_id_changed':
        stale['owner_boot_id'] = 'an-earlier-boot'
    else:
        stale.pop('owner_boot_id', None)
        stale['owner_created_at'] = psutil.boot_time() - 3600
    index.write_text(json.dumps(stale))
    _restart_and_assert_available(source, root, f'job_s006_rebooted_{variant}')
    assert unmarked_session.poll() is None, 'the unrelated session is never signalled'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX sessions')
def test_live_leader_with_a_stepped_clock_is_not_declared_exited(tmp_path):
    """A wall-clock step changes a live leader's reported start time on Linux."""
    from backend.engine.local_training_worker import _boot_id, _liveness
    token = 'harness-token'
    leader = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True,
                              env={**os.environ, 'MODU_VISION_LOCAL_WORKER_TOKEN': token})
    try:
        process = psutil.Process(leader.pid)
        journal = {'owner_pid': leader.pid, 'owner_created_at': process.create_time() - 5, 'owner_token': token,
                   'owner_session': leader.pid, 'owner_username': process.username(), 'owner_boot_id': _boot_id()}
        assert _liveness(journal) is True
        assert _liveness({**journal, 'owner_token': 'another-run'}) is False, 'a different run at this PID means ours ended'
        assert leader.poll() is None
    finally:
        leader.kill()
        leader.wait(5)


def _marked_session(token):
    script = ("import subprocess,sys,time; c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
              "print(c.pid, flush=True); time.sleep(60)")
    leader = subprocess.Popen([sys.executable, '-c', script], start_new_session=True, stdout=subprocess.PIPE, text=True,
                              env={**os.environ, 'MODU_VISION_LOCAL_WORKER_TOKEN': token})
    return leader, int(leader.stdout.readline())


@pytest.mark.skipif(os.name == 'nt', reason='POSIX sessions')
@pytest.mark.parametrize('leader_alive', [True, False])
def test_estimated_boot_time_never_overrides_live_marked_workers(monkeypatch, leader_alive):
    """AuditCase: legacy journal without a boot id while boot time estimation moved."""
    from backend.engine import local_training_worker as worker
    token = 'private-run-token'
    leader, child_pid = _marked_session(token)
    try:
        process = psutil.Process(leader.pid)
        journal = {'owner_pid': leader.pid, 'owner_created_at': process.create_time(), 'owner_token': token,
                   'owner_session': leader.pid, 'owner_username': process.username()}  # no owner_boot_id
        if not leader_alive:
            leader.kill()
            leader.wait(5)  # the marked child stays in the original session as an orphan
        monkeypatch.setattr(worker.psutil, 'boot_time', lambda: journal['owner_created_at'] + 601)
        members = worker._owned_members(journal)
        assert members and child_pid in [member.pid for member in members], 'live marked workers win over an estimate'
        assert worker._liveness(journal) is True
        assert worker._AttachedProcess(journal).poll() is None
        # A changed boot identifier remains conclusive evidence.
        monkeypatch.setattr(worker, '_boot_id', lambda: 'current-boot')
        assert worker._owned_members({**journal, 'owner_boot_id': 'earlier-boot'}) == []
        assert psutil.pid_exists(child_pid), 'judging ownership never signals a process'
    finally:
        for pid in (child_pid, leader.pid):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        leader.wait(5)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX sessions')
def test_estimated_earlier_boot_with_only_unrelated_session_members_is_exited_without_signals(monkeypatch, unmarked_session):
    from backend.engine import local_training_worker as worker
    gone = subprocess.Popen([sys.executable, '-c', 'pass'])
    gone.wait(5)
    journal = {'owner_pid': gone.pid, 'owner_created_at': 1000.0, 'owner_token': 'old-run', 'owner_session': unmarked_session.pid,
               'owner_username': psutil.Process().username()}
    monkeypatch.setattr(worker.psutil, 'boot_time', lambda: journal['owner_created_at'] + 601)
    assert worker._owned_members(journal) == []
    # Unknown inspection remains unknown even with an earlier-boot estimate.
    unreadable = psutil.Process(unmarked_session.pid)
    def denied_environment():
        raise psutil.AccessDenied(unmarked_session.pid)
    with monkeypatch.context() as denied:
        process_type = worker.psutil.Process
        denied.setattr(unreadable, 'environ', denied_environment)
        denied.setattr(worker.psutil, 'Process', lambda pid: unreadable if pid == unmarked_session.pid else process_type(pid))
        denied.setattr(worker.psutil, 'process_iter', lambda attrs: [unreadable])
        assert worker._owned_members(journal) is None
    assert unmarked_session.poll() is None, 'the unrelated session is never signalled'
    monkeypatch.setattr(worker.psutil, 'boot_time', lambda: journal['owner_created_at'] - 10)
    assert worker._owned_members(journal) is None, 'without an earlier-boot estimate an unmarked session stays unknown'


def test_reconnect_after_confirmed_exit_clears_the_active_record(local_data):
    """AuditCase: reconnecting a disconnected job whose exit is already confirmed."""
    source, root = local_data
    _completed_job(source, root, 'job_s006_reconnect')
    restarted = routes_training.TrainingJobManager()
    stale = routes_training.JobRecord('job_s006_reconnect', 'classification', 'fast', str(source),
                                      str(root / 'job_s006_reconnect'), 'disconnected')
    restarted.restore_local_job(stale)
    assert restarted.is_training
    record = restarted.reconnect_local_job('job_s006_reconnect')
    assert record is not None and record.status == 'completed'
    assert not restarted.is_training


def test_restart_restores_confirmed_terminal_history_without_reattaching(local_data):
    """AuditCase: every historical journal is re-monitored on each start-up."""
    source, root = local_data
    _completed_job(source, root, 'job_s006_history')
    receipt = root / 'job_s006_history' / 'job_receipt.json'
    before = receipt.read_bytes(), receipt.stat().st_mtime_ns
    restored = _restart_and_assert_available(source, root, 'job_s006_history')
    assert restored is not None and restored.status == 'completed'
    assert restored.thread is None, 'a confirmed terminal job needs no observer thread'
    assert (receipt.read_bytes(), receipt.stat().st_mtime_ns) == before, 'terminal receipts are not rewritten'


ROOT = Path(__file__).resolve().parents[2]


def _frozen_entry(*arguments):
    # The real dispatcher script; a frozen executable runs the same code path.
    return subprocess.run([sys.executable, str(ROOT / 'scripts' / 'frozen_backend_entry.py'), *arguments],
                          cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT)}, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize(('module', 'marker'), [
    ('backend.engine.operations_worker', 'Watch one owned project'),
    ('backend.engine.worker_preflight', 'Run one worker preflight'),
    ('backend.training_cli', 'execute'),
])
def test_frozen_entry_runs_internal_module_launches(module, marker):
    """AuditCase: watcher and recipe children are launched as `<exe> -m <module>`."""
    result = _frozen_entry('-m', module, '--help')
    assert result.returncode == 0, result.stderr[-2000:]
    assert marker in result.stdout
    assert 'Vision AI Studio Backend Daemon' not in result.stdout, 'must not fall through to the API server'


def test_frozen_entry_refuses_modules_outside_the_internal_allowlist():
    result = _frozen_entry('-m', 'http.server', '--help')
    assert result.returncode == 2
    assert 'Unsupported frozen module' in result.stderr


def _queued_inspection(tmp_path):
    from backend.engine.inspection_service import InspectionStore
    store = InspectionStore(tmp_path / 'state')
    image = tmp_path / 'input.png'
    Image.new('RGB', (8, 8), 'gray').save(image)
    return store, store.enqueue(image, 'upload')


def test_inspection_crash_loop_is_quarantined_for_review(tmp_path):
    """AuditCase: a worker that dies mid-inspection on every start is requeued forever."""
    store, job_id = _queued_inspection(tmp_path)
    for _ in range(10):
        if store.claim() is None:
            break
        store.recover()  # the process died before finish(); the next start recovers
    row = store.get(job_id)
    assert row['state'] == 'error', row
    assert row['verdict'] == 'REVIEW', 'an unfinished inspection is never OK'
    assert row['attempts'] <= 3
    assert any('quarantined' in (event['message'] or '') for event in store.events(job_id))
    assert store.retry(job_id), 'operators can still request one more attempt'
    assert store.claim()['job_id'] == job_id


def test_operator_retries_do_not_count_as_interruptions(tmp_path):
    store, job_id = _queued_inspection(tmp_path)
    for _ in range(2):
        store.claim()
        store.finish(job_id, error='ValueError: unreadable image')
        assert store.retry(job_id)
    store.claim()
    store.recover()
    assert store.get(job_id)['state'] == 'queued', 'one interruption after operator retries is still retried'


def test_single_interrupted_inspection_is_still_requeued(tmp_path):
    store, job_id = _queued_inspection(tmp_path)
    assert store.claim()['job_id'] == job_id
    store.recover()
    assert store.get(job_id)['state'] == 'queued'


AUDIT_CASES = [
    AuditCase(
        candidate='Local journal and PID reuse blocks new training after restart',
        setup='completed CPU job; index journal edited to simulate a reused owner PID (exit confirmed and unconfirmed), '
              'a reboot (changed boot id, and a legacy journal without one), a stepped clock and a reconnect',
        observed='before fix: disconnected job, uncertain lease, new training refused, observers re-attached to history; '
                 'after fix: available in every case and reconnect clears the active record; the stepped-clock case is a '
                 'regression guard for the new reuse branch (HEAD returned uncertain there)',
        status='confirmed_defect',
        remediation='local_training_worker: boot id recorded at launch; a different run token at the leader PID proves the owned '
                    'group ended (POSIX); confirmed terminal journals restore as history through restore_local_job',
    ),
    AuditCase(
        candidate='Frozen executable cannot run internal `-m` children',
        setup='real scripts/frozen_backend_entry.py with `-m backend.engine.operations_worker --help` and `-m backend.training_cli --help`',
        observed='before fix: fell through to the API server parser; after fix: module help, modules outside the allowlist exit 2',
        status='confirmed_defect',
        remediation='frozen_backend_entry: allowlisted `-m` dispatch; a rebuilt PyInstaller binary still needs its own verification',
    ),
    AuditCase(
        candidate='Frozen executable children started with `-c`',
        setup='code path: gan_package_runtime.py:94 and openvino_runtime.py:28/266 launch sys.executable -c',
        observed='in a frozen build these would reach the API server parser; not executed in a frozen build',
        status='candidate_not_reproduced',
        remediation='handoff to the owners of those runtimes: use a dedicated frozen dispatcher flag',
    ),
    AuditCase(
        candidate='Inspection that kills its worker is retried forever',
        setup='InspectionStore job claimed and recovered ten times without finish(); operator retries after ordinary errors',
        observed='before fix: queued with attempts=10; after fix: error/REVIEW after 3 consecutive interrupted attempts, '
                 'operator retries reset the count and are not counted as interruptions',
        status='confirmed_defect',
        remediation='inspection_service: interrupted column and quarantine in recover(); bounded delivery retries remain S5-02 scope',
    ),
    AuditCase(
        candidate='Renderer CSP blocks the HTTPS shared project server',
        setup='built renderer in the browser harness; fetch/WebSocket/image to an https/wss origin and plain http to another host',
        observed='before fix: connect-src and img-src violations for https/wss; after fix: none, plain http to other hosts still blocked',
        status='confirmed_defect',
        remediation='index.html admits https:/wss:; the Electron main process cancels renderer https/wss requests to any origin '
                    'other than the connected shared server (verified in the Electron harness)',
    ),
    AuditCase(
        candidate='Renderer without sandbox and unrestricted shell opening over IPC',
        setup='Electron harness with shell functions stubbed in the main process',
        observed='before fix: sandbox false (observed); the HEAD handler passed any file:// URL or existing path to the shell '
                 '(code path ipc.ts:92-123, not executed); after fix: sandbox true, only https/http links, folders and document '
                 'types open; scripts, bundles (by name or Contents/Info.plist), aliases, UNC/device/host file paths, relative '
                 'paths and other schemes are refused; every handler checks the main frame',
        status='confirmed_defect',
        remediation='src/main/index.ts sandbox:true; src/main/ipc.ts classifyOpenTarget and sender checks',
    ),
    AuditCase(
        candidate='Desktop shutdown on Windows leaves backend descendants running',
        setup='code path: supervisor.ts stops the backend with proc.kill, and the exit handler clears the taskkill fallback',
        observed='Windows-only behaviour; not executed on this macOS host',
        status='code_path_confirmed',
        remediation='deferred to S1-09 (Windows process ownership) with a Windows runner',
    ),
    AuditCase(
        candidate='Checkpoint loading with unsafe deserialization',
        setup='code path only, no exploit fixture: user-supplied pretrained_checkpoint (routes_training.py:723, '
              'routes_patch_classification.py:26/93, routes_label_suggestions.py:97, training_workspace.import_pretrained_weight '
              'without content checks and with an optional hash) reaching model_backbones.py:203 (ultralytics torch_safe_load, '
              'weights_only=False); weights_only=False in trainer.py:908, routes_evaluation.py:452/660/807/928, exporter.py:70; '
              'padim.py:264 and patchcore.py:250 rely on the torch default, unsafe below torch 2.6 which requirements still allow',
        observed='on a shared server a trainer-role account could supply a checkpoint that executes code when loaded',
        status='code_path_confirmed',
        remediation='handoff to the owners of these loaders: weights_only=True with an explicit allowlist or safetensors, '
                    'a torch>=2.6 floor, and content validation on import',
    ),
]
