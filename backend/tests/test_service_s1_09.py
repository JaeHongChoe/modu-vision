"""S1-09: owned processes behave the same on Windows and POSIX.

An owned worker runs outside the backend's session (POSIX) or process group and console (Windows), stops with only its
own processes, and a process that took over a recorded number is never signalled. The backend stops gracefully when
its supervisor closes stdin, which is the stop that runs the shutdown sequence on Windows. Every process signalled
here was started by the test itself and is stopped through its own handle.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _sleeper(**kwargs):
    return subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'], **kwargs)


def _console_members():
    """Processes attached to this test's console on Windows (empty when the test has no console)."""
    import ctypes
    buffer = (ctypes.c_uint32 * 4096)()
    count = ctypes.windll.kernel32.GetConsoleProcessList(buffer, 4096)
    return set(buffer[:count]) if 0 < count <= 4096 else set()


def test_owned_children_get_their_own_session_or_process_group_and_console():
    from backend.engine.runtime_process_control import session_isolation
    if os.name == 'nt':
        assert session_isolation() == {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    else:
        assert session_isolation() == {'start_new_session': True}


def test_a_real_owned_worker_in_a_spaced_korean_path_stops_with_only_its_own_processes(tmp_path, monkeypatch):
    # The basic trainer loads data in the worker process itself (no DataLoader child processes), so this run covers the
    # worker alone; DataLoader children of a worker are not exercised here.
    num_workers = 0
    from PIL import Image
    from backend.api import routes_training
    from backend.engine import local_training_worker
    base = tmp_path / '작업 폴더 with space'
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(base / '사용자 데이터'))
    source, output = base / '원본 이미지', base / '학습 결과 1'
    for split in ('train', 'val'):
        for label in ('OK', 'NG'):
            folder = source / split / label
            folder.mkdir(parents=True)
            for index in range(4):
                Image.new('RGB', (32, 32), 'red' if label == 'NG' else 'blue').save(folder / f'불량 {index}.png')
    bystander = _sleeper()  # unrelated to the run; it must outlive the cancellation
    manager = routes_training.TrainingJobManager()
    try:
        record = manager.start_job(f'job_s109_{num_workers}', 'classification', str(source), str(output), device='cpu',
                                   config_overrides={'pretrained': False, 'backbone': 'resnet18', 'epochs': 1000,
                                                     'image_size': 32, 'batch_size': 2, 'num_workers': num_workers})
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = output / 'status.json'
            if status.is_file() and json.loads(status.read_text(encoding='utf-8')).get('status') == 'running':
                break
            if record.thread is not None and not record.thread.is_alive():
                break
            time.sleep(0.05)
        log = output / 'local_worker.log'
        tail = log.read_text(encoding='utf-8', errors='replace')[-3000:] if log.is_file() else ''
        assert record.status in ('preparing', 'running'), f'{record.status} {record.error}\n{tail}'
        journal = json.loads((output / 'local_job.json').read_text(encoding='utf-8'))
        worker = psutil.Process(journal['owner_pid'])
        if os.name == 'nt':
            console = _console_members()
            # Without a console of its own (a service runner) this test cannot tell; session_isolation's flags are
            # checked by the first test. With one, the worker must not share it.
            if console:
                assert os.getpid() in console and worker.pid not in console, 'the worker has a console of its own'
        else:
            assert os.getsid(worker.pid) == worker.pid != os.getsid(0), 'the worker leads its own session'
        members = local_training_worker._owned_members(journal)
        assert members and members[0].pid == worker.pid, members
        assert bystander.pid not in {process.pid for process in members}
        assert manager.abort_job(record.job_id)
        record.thread.join(180)
        journal = json.loads((output / 'local_job.json').read_text(encoding='utf-8'))
        assert record.status == 'aborted' and journal['worker_exit_confirmed'] is True, (record.status, record.error)
        assert not worker.is_running() and local_training_worker._owned_members(journal) == []
        assert bystander.poll() is None, 'an unrelated process is never signalled'
        assert all(row['job_id'] != record.job_id for row in manager._leases.list()), 'the reservation is returned'
    finally:
        bystander.kill()
        bystander.wait(30)


def test_a_process_at_a_reused_number_is_never_signalled():
    from backend.engine import local_training_worker
    other = _sleeper()  # stands at the number a run recorded, but was not started by that run
    try:
        process = psutil.Process(other.pid)
        journal = {'job_id': 'job_reused', 'owner_pid': other.pid, 'owner_created_at': process.create_time() - 60,
                   'owner_token': uuid.uuid4().hex, 'owner_session': other.pid, 'owner_username': process.username(),
                   'owner_boot_id': local_training_worker._boot_id()}
        assert local_training_worker._liveness(journal) is False
        assert local_training_worker._stop_owned(journal, force=True) is True, 'nothing of that run is left to stop'
        time.sleep(0.3)
        assert other.poll() is None, 'the process at the reused number was not signalled'
    finally:
        other.kill()
        other.wait(30)


class _Listed:
    """A listed process; `denied` names the inspections the platform refuses for it."""
    def __init__(self, pid, username, token=None, *, denied=(), created=0.0, parent=1, exits=False):
        self.pid, self._username, self._token, self._denied, self._created = pid, username, token, set(denied), created
        self._parent, self._exits = parent, exits

    def ppid(self):  # Windows reads the parent from a process snapshot, without opening the process
        if self._exits:  # it exited after it was listed
            raise psutil.NoSuchProcess(self.pid)
        return self._parent

    def _check(self, name):
        if name in self._denied:
            raise psutil.AccessDenied(self.pid)

    def username(self):
        self._check('username')
        return self._username

    def status(self):
        self._check('status')
        return psutil.STATUS_RUNNING

    def environ(self):
        self._check('environ')
        return {'MODU_VISION_LOCAL_WORKER_TOKEN': self._token} if self._token else {}

    def create_time(self):
        self._check('create_time')
        return self._created


def _journal(token, now):
    from backend.engine import local_training_worker
    return {'owner_pid': 999_999, 'owner_created_at': now, 'owner_token': token, 'owner_session': 999_999,
            'owner_username': 'me', 'owner_boot_id': local_training_worker._boot_id()}


def _listing(monkeypatch, processes):
    from backend.engine import local_training_worker
    def gone(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(local_training_worker.psutil, 'Process', gone)
    monkeypatch.setattr(local_training_worker.psutil, 'process_iter', lambda attrs=None: iter(processes))


def test_windows_processes_this_backend_cannot_open_are_not_its_workers(monkeypatch):
    from backend.engine import local_training_worker
    token, now = uuid.uuid4().hex, time.time()
    member = _Listed(11, 'me', token, created=now + 1)
    processes = [_Listed(4, 'SYSTEM', denied={'username', 'status', 'environ'}),  # a service: its account cannot be read
                 _Listed(12, 'me', denied={'environ'}),                        # elevated, same account
                 member, _Listed(13, 'me', 'another-run', created=now + 1)]
    monkeypatch.setattr(local_training_worker, '_WINDOWS', True)
    _listing(monkeypatch, processes)
    assert local_training_worker._owned_members(_journal(token, now)) == [member]
    _listing(monkeypatch, processes[:2])
    assert local_training_worker._owned_members(_journal(token, now)) == [], 'denied processes alone prove nothing alive'


def test_a_windows_worker_this_backend_cannot_open_is_unknown_never_exited(monkeypatch):
    """A worker started while the app ran elevated outlives a later, non-elevated backend that cannot open it."""
    from backend.engine import local_training_worker
    token, now = uuid.uuid4().hex, time.time()
    monkeypatch.setattr(local_training_worker, '_WINDOWS', True)
    journal = _journal(token, now)
    elevated_leader = _Listed(journal['owner_pid'], 'me', denied={'username', 'status', 'environ'}, created=now)
    # the leader at the recorded number with the recorded start time, which this backend cannot open
    _listing(monkeypatch, [_Listed(4, 'SYSTEM', denied={'username'}), elevated_leader])
    monkeypatch.setattr(local_training_worker.psutil, 'Process', lambda pid: elevated_leader)
    assert local_training_worker._owned_members(journal) is None
    assert local_training_worker._liveness(journal) is None, 'its reservation is kept'
    # a process the leader started, which this backend cannot open, while the leader itself is gone
    _listing(monkeypatch, [_Listed(31, 'me', denied={'environ'}, parent=journal['owner_pid'], created=now + 1)])
    assert local_training_worker._owned_members(journal) is None
    # a denied process started by someone else is still not this worker's
    _listing(monkeypatch, [_Listed(32, 'me', denied={'environ'}, parent=777)])
    assert local_training_worker._owned_members(journal) == []


def test_on_posix_a_denied_process_in_the_workers_own_session_stays_unknown(monkeypatch):
    from backend.engine import local_training_worker
    token, now = uuid.uuid4().hex, time.time()
    monkeypatch.setattr(local_training_worker, '_WINDOWS', False)
    monkeypatch.setattr(local_training_worker.os, 'getsid', lambda pid: 999_999, raising=False)  # Windows has no getsid
    _listing(monkeypatch, [_Listed(21, 'me', token, created=now + 1), _Listed(22, 'me', denied={'environ'})])
    assert local_training_worker._owned_members(_journal(token, now)) is None


SERVICE = {'username', 'status', 'environ'}  # what a standard user cannot read of a service or an elevated process


def test_a_windows_parent_number_counts_only_for_processes_started_after_that_parent(monkeypatch):
    """Windows keeps a parent's number after the parent exits; a system process older than the worker that lists the
    worker's number as its parent is not the worker's, so the live worker stays stoppable and its exit is seen."""
    from backend.engine import local_training_worker
    token, now = uuid.uuid4().hex, time.time()
    monkeypatch.setattr(local_training_worker, '_WINDOWS', True)
    journal = _journal(token, now)
    worker = _Listed(journal['owner_pid'], 'me', token, created=now)
    stale = _Listed(41, 'SYSTEM', denied=SERVICE, created=now - 600, parent=journal['owner_pid'])
    _listing(monkeypatch, [stale, worker])
    assert local_training_worker._owned_members(journal) == [worker]
    _listing(monkeypatch, [stale])
    assert local_training_worker._owned_members(journal) == [], 'after the worker exits'
    # the same for a marked member's number: only what it started counts
    member = _Listed(11, 'me', token, created=now + 1)
    _listing(monkeypatch, [member, _Listed(42, 'SYSTEM', denied=SERVICE, created=now, parent=11)])
    assert local_training_worker._owned_members(journal) == [member]
    _listing(monkeypatch, [member, _Listed(43, 'me', denied={'environ'}, created=now + 2, parent=11)])
    assert local_training_worker._owned_members(journal) is None, "an unopenable child of a member is the run's"


def test_children_at_a_reused_windows_number_count_only_from_before_the_new_occupant(monkeypatch):
    from backend.engine import local_training_worker
    token, now = uuid.uuid4().hex, time.time()
    monkeypatch.setattr(local_training_worker, '_WINDOWS', True)
    journal = _journal(token, now)
    # another process that this backend cannot open took the recorded number after the worker exited
    occupant = _Listed(journal['owner_pid'], 'SYSTEM', denied=SERVICE, created=now + 50)
    its_child = _Listed(51, 'SYSTEM', denied=SERVICE, created=now + 60, parent=journal['owner_pid'])
    _listing(monkeypatch, [occupant, its_child])
    monkeypatch.setattr(local_training_worker.psutil, 'Process', lambda pid: occupant)
    assert local_training_worker._owned_members(journal) == []
    assert local_training_worker._liveness(journal) is False
    # a process the worker started before it exited, which this backend cannot open, is still the run's
    orphan = _Listed(52, 'me', denied={'environ'}, created=now + 10, parent=journal['owner_pid'])
    _listing(monkeypatch, [occupant, its_child, orphan])
    monkeypatch.setattr(local_training_worker.psutil, 'Process', lambda pid: occupant)  # the occupant still holds the number
    assert local_training_worker._owned_members(journal) is None


def test_after_a_reboot_the_old_worker_number_is_no_processes_parent(monkeypatch):
    """Windows has no boot identifier here; a worker from before this boot cannot be alive, and every process of this
    boot started after it, so the number it had proves nothing about them."""
    from backend.engine import local_training_worker
    token = uuid.uuid4().hex
    monkeypatch.setattr(local_training_worker, '_WINDOWS', True)
    before_boot = psutil.boot_time() - local_training_worker.BOOT_MARGIN_SECONDS - 3600
    journal = dict(_journal(token, before_boot), owner_boot_id=None)
    service_host = _Listed(61, 'SYSTEM', denied=SERVICE, created=time.time(), parent=journal['owner_pid'])
    _listing(monkeypatch, [service_host])
    assert local_training_worker._boot_evidence(journal) == 'estimated_earlier'
    assert local_training_worker._owned_members(journal) == []
    assert local_training_worker._liveness(journal) is False


def test_a_denied_process_that_exits_during_the_scan_is_skipped(monkeypatch):
    from backend.engine import local_training_worker
    token, now = uuid.uuid4().hex, time.time()
    monkeypatch.setattr(local_training_worker, '_WINDOWS', True)
    journal = _journal(token, now)
    _listing(monkeypatch, [_Listed(71, 'me', denied={'environ'}, created=now + 1, parent=journal['owner_pid'], exits=True)])
    assert local_training_worker._owned_members(journal) == []
    # a child of the worker whose start time cannot be read cannot be ordered: the state stays unknown
    _listing(monkeypatch, [_Listed(72, 'me', denied={'environ', 'create_time'}, parent=journal['owner_pid'])])
    assert local_training_worker._owned_members(journal) is None


def test_a_failing_detach_never_loses_the_stop(monkeypatch):
    """A detach that raises (a locked database, say) still makes the server exit."""
    from backend import main
    def locked():
        raise RuntimeError('database is locked')
    monkeypatch.setattr(main.training_job_manager, 'detach_all_for_shutdown', locked)
    monkeypatch.setattr(main.os, 'read', lambda fd, size: b'')  # the supervisor closed stdin
    server = type('Server', (), {'should_exit': False})()
    with pytest.raises(RuntimeError, match='locked'):
        main._stop_when_stdin_closes(server)
    assert server.should_exit is True


def _read_lines(stream, lines):
    for raw in iter(stream.readline, b''):
        lines.append(raw.decode('utf-8', 'replace'))


@pytest.mark.parametrize('opted_in', [True, False])
def test_the_backend_stops_gracefully_when_its_supervisor_closes_stdin(tmp_path, opted_in):
    env = {key: value for key, value in os.environ.items() if key != 'VISION_AI_STUDIO_STOP_ON_STDIN_EOF'}
    env.update(PYTHONPATH=str(ROOT), PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8',
               VISION_AI_STUDIO_USER_DATA_DIR=str(tmp_path / '사용자 데이터'), VISION_AI_STUDIO_API_TOKEN='a' * 64)
    if opted_in:
        env['VISION_AI_STUDIO_STOP_ON_STDIN_EOF'] = '1'
    log_path = tmp_path / 'backend.log'
    with log_path.open('wb') as log:
        backend = subprocess.Popen([sys.executable, str(ROOT / 'backend' / 'main.py'), '--host', '127.0.0.1', '--port', '0',
                                    '--project-dir', str(tmp_path / '프로젝트'), '--log-level', 'info'],
                                   cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
    lines = []
    threading.Thread(target=_read_lines, args=(backend.stdout, lines), daemon=True).start()
    try:
        deadline, port = time.monotonic() + 120, None
        while port is None and time.monotonic() < deadline and backend.poll() is None:
            port = next((line.split('=', 1)[1].strip() for line in lines if line.startswith('VISION_AI_STUDIO_PORT=')), None)
            time.sleep(0.05)
        assert port, log_path.read_text(encoding='utf-8', errors='replace')[-3000:]
        healthy = False
        while not healthy and time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
                    healthy = response.status == 200
            except OSError:
                time.sleep(0.1)
        assert healthy, log_path.read_text(encoding='utf-8', errors='replace')[-3000:]
        backend.stdin.close()
        if opted_in:
            assert backend.wait(60) == 0, log_path.read_text(encoding='utf-8', errors='replace')[-3000:]
            text = log_path.read_text(encoding='utf-8', errors='replace')
            assert 'The supervisor closed stdin' in text and 'Shutdown cleanup complete' in text, text[-3000:]
        else:
            time.sleep(3)
            assert backend.poll() is None, 'a server started without the supervisor keeps running when stdin closes'
    finally:
        if backend.poll() is None:
            backend.kill()
        backend.wait(30)


def test_a_job_file_held_open_by_a_reader_on_windows_is_replaced_once_the_reader_lets_go(tmp_path, monkeypatch):
    """Windows refuses to replace a file another process has open ('Access is denied'). A running job's status and cancel
    files are read many times a second by the other process, so a replacement waits briefly for the reader instead of
    failing the job; both the app's and the worker's atomic writers do this (simulated here; the real Windows behaviour
    is the Windows-only test below)."""
    from backend.engine import runtime_process_control
    from backend.remote import file_replace, worker
    real_replace, sleeps, attempts = os.replace, [], []

    def held_by_a_reader(times):
        def replace(source, destination):
            attempts.append(destination)
            if len(attempts) <= times:
                raise PermissionError(13, 'Access is denied')
            return real_replace(source, destination)
        return replace

    monkeypatch.setattr(file_replace, '_WINDOWS', True)
    monkeypatch.setattr(file_replace.time, 'sleep', sleeps.append)
    monkeypatch.setattr(file_replace.os, 'replace', held_by_a_reader(3))
    runtime_process_control.atomic_private_json(tmp_path / 'local_cancel.json', {'cancel': True})
    assert json.loads((tmp_path / 'local_cancel.json').read_text()) == {'cancel': True} and len(attempts) == 4
    attempts.clear()
    worker._atomic_json(tmp_path / 'status.json', {'status': 'running'})
    assert json.loads((tmp_path / 'status.json').read_text()) == {'status': 'running'} and len(attempts) == 4
    # a reader that never lets go still fails the write, after about two seconds, instead of hanging
    attempts.clear(); sleeps.clear()
    monkeypatch.setattr(file_replace.os, 'replace', held_by_a_reader(10_000))
    with pytest.raises(PermissionError):
        runtime_process_control.atomic_private_json(tmp_path / 'local_cancel.json', {'cancel': False})
    assert len(attempts) == file_replace.ATTEMPTS and 1.0 < sum(sleeps) < 4.0
    # elsewhere a refused replacement is a real error at once
    attempts.clear()
    monkeypatch.setattr(file_replace, '_WINDOWS', False)
    with pytest.raises(PermissionError):
        runtime_process_control.atomic_private_json(tmp_path / 'local_cancel.json', {'cancel': False})
    assert len(attempts) == 1


def test_a_job_file_read_during_a_replacement_on_windows_is_read_again(tmp_path, monkeypatch):
    """The app reads the worker's status file and the worker reads the app's cancel file; on Windows a read can meet a
    replacement in progress, which is read again instead of ending the job or the cancel watch."""
    from backend.remote import file_replace
    target = tmp_path / 'status.json'
    target.write_text('{"status": "running"}', encoding='utf-8')
    real_read, reads = Path.read_text, []

    def busy_twice(self, *args, **kwargs):
        reads.append(self)
        if len(reads) <= 2:
            raise PermissionError(13, 'Access is denied')
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(file_replace, '_WINDOWS', True)
    monkeypatch.setattr(file_replace.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(Path, 'read_text', busy_twice)
    assert json.loads(file_replace.read_text(target)) == {'status': 'running'} and len(reads) == 3
    # the worker's cancel watch reads its cancel file through the same retry
    from backend.engine import local_training_worker
    (tmp_path / 'local_cancel.json').write_text(json.dumps({'job_id': 'job_watch'}), encoding='utf-8')
    reads.clear()
    assert local_training_worker._cancelled(tmp_path, 'job_watch') is True and len(reads) == 3


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing semantics')
def test_windows_refuses_to_replace_a_file_a_reader_holds_and_the_retry_waits_it_out(tmp_path):
    """The real cause of the S0-06 failure: a file this process holds open through Python's open() (no delete sharing)
    cannot be replaced (WinError 5, 'Access is denied'); the retry replaces it once the reader lets go."""
    from backend.engine.runtime_process_control import atomic_private_json
    target, source = tmp_path / 'status.json', tmp_path / 'next.json'
    target.write_text('{"status": "running"}', encoding='utf-8')
    source.write_text('{"status": "completed"}', encoding='utf-8')
    reader = open(target, encoding='utf-8')  # what a status read does, for a moment
    try:
        with pytest.raises(PermissionError) as refused:
            os.replace(source, target)
        assert refused.value.winerror == 5, refused.value
        threading.Timer(0.2, reader.close).start()
        atomic_private_json(target, {'status': 'completed'})
    finally:
        reader.close()
    assert json.loads(target.read_text(encoding='utf-8')) == {'status': 'completed'}


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing semantics')
def test_a_status_file_read_while_it_is_replaced_never_fails_either_side_on_windows(tmp_path):
    """A real reader (the app's read helper) races a real writer (the worker's atomic writer) 2000 times."""
    from backend.remote.file_replace import read_text
    from backend.remote.worker import _atomic_json
    target = tmp_path / 'status.json'
    _atomic_json(target, {'n': 0})
    errors, done = [], threading.Event()

    def read():
        while not done.is_set():
            try:
                json.loads(read_text(target))
            except Exception as exc:
                errors.append(f'reader: {exc!r}')
            time.sleep(0.001)  # the app polls; a reader that never lets go would make the writer's retries fail instead

    thread = threading.Thread(target=read)
    thread.start()
    try:
        for n in range(1, 2001):
            _atomic_json(target, {'n': n})
    except Exception as exc:
        errors.append(f'writer: {exc!r}')
    finally:
        done.set()
        thread.join(10)
    assert not errors, errors[:5]
