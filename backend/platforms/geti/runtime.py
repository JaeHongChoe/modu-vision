"""Lazy parent-owned Geti child processes, one private instance per storage key."""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import secrets
import signal
import shutil
import socket
import subprocess
import threading
import time
import psutil

from .bindings import opaque_key
from .client import GetiClient, GetiError

PINNED_SHA = 'ddcf394d550bf1b84ca262888260043af361f5e7'
REPO_ROOT = Path(__file__).resolve().parents[3]
OVERLAY_ROOT = REPO_ROOT / 'platforms/geti/runtime'


class GetiRuntimeManager:
    def __init__(self, root: Path, *, source_root=None, python=None):
        self.root = Path(root).resolve()
        descriptor_path = os.environ.get('MODU_GETI_RUNTIME_DESCRIPTOR')
        if not descriptor_path and python is None and source_root is None and not os.environ.get('MODU_GETI_PYTHON'):
            descriptor_path = next((str(path) for path in [REPO_ROOT / 'dist-geti/runtime.json', OVERLAY_ROOT / 'installed-runtime.json'] if path.is_file()), None)
        self.descriptor_path = Path(descriptor_path).expanduser().absolute() if descriptor_path else None
        self._explicit_python = python or os.environ.get('MODU_GETI_PYTHON')
        self._explicit_source = source_root or os.environ.get('MODU_GETI_SOURCE_ROOT')
        self._explicit_entrypoint = os.environ.get('MODU_GETI_ENTRYPOINT')
        self.weights_root = None
        descriptor = {}
        descriptor_root = OVERLAY_ROOT
        if descriptor_path:
            descriptor_file = Path(descriptor_path).expanduser().resolve()
            descriptor_root = descriptor_file.parent
            try: descriptor = json.loads(descriptor_file.read_text())
            except (OSError, ValueError): descriptor = {}
        def descriptor_value(key):
            value = descriptor.get(key)
            if not value: return None
            path = Path(value)
            return path if path.is_absolute() else descriptor_root / path
        self.source_root = Path(source_root or os.environ.get('MODU_GETI_SOURCE_ROOT') or descriptor_value('source_root') or REPO_ROOT / 'platforms/geti/upstream').resolve()
        configured_python = python or os.environ.get('MODU_GETI_PYTHON') or descriptor_value('python')
        self.python = Path(configured_python).expanduser().absolute() if configured_python else None
        self.entrypoint = Path(os.environ.get('MODU_GETI_ENTRYPOINT') or descriptor_value('entrypoint') or OVERLAY_ROOT / 'server.py').resolve()
        self._instances = {}
        self._errors = {}
        self._lock = threading.RLock()
        self._verified = False
        self.activity = None
        atexit.register(self.shutdown)

    def _verify(self):
        if self._verified: return
        installation = None
        if self.descriptor_path:
            from .installation import read_installation
            try:
                installation = read_installation(self.descriptor_path)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise RuntimeError('Installed Geti runtime integrity verification failed. 플랫폼 실행 환경을 다시 설치하세요.') from exc
            if not self._explicit_python: self.python = Path(installation['python'])
            if not self._explicit_source: self.source_root = Path(installation['source_root'])
            if not self._explicit_entrypoint: self.entrypoint = Path(installation['entrypoint'])
            self.weights_root = Path(installation['weights_root'])
        if not self.python or not self.python.is_file():
            raise RuntimeError('Geti Python 3.14 runtime is not installed. 플랫폼 실행 환경을 설치하세요.')
        result = subprocess.run([str(self.python), '-I', '-B', '-c', 'import sys; print("%d.%d" % sys.version_info[:2])'], capture_output=True, text=True, timeout=15)
        if result.returncode or result.stdout.strip() != '3.14':
            raise RuntimeError('Geti requires a separate Python 3.14 runtime.')
        if not (self.source_root / 'application/backend/app/main.py').is_file() or not (self.source_root / 'library/src/getitune').is_dir():
            raise RuntimeError('Pinned whole Geti application/library source is unavailable.')
        if not installation or self.source_root != Path(installation['source_root']):
            source = subprocess.run(['git', '-C', str(self.source_root), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=15)
            dirty = subprocess.run(['git', '-C', str(self.source_root), 'status', '--porcelain', '--untracked-files=no'], capture_output=True, text=True, timeout=15)
            if source.returncode or source.stdout.strip() != PINNED_SHA or dirty.returncode or dirty.stdout.strip():
                raise RuntimeError('Pinned Geti source identity or tracked-source integrity check failed.')
        if not self.entrypoint.is_file(): raise RuntimeError('Reviewed Geti runtime overlay is missing.')
        self._verified = True

    def status(self, project_key: str) -> dict:
        opaque_key(project_key)
        with self._lock:
            handle = self._instances.get(project_key)
            if handle and handle['process'].poll() is None:
                return {'state': 'running', 'generation': handle['generation']}
            if handle:
                self._errors[project_key] = '플랫폼 프로세스가 종료됐습니다. 이전 작업을 확인한 뒤 다시 시작하세요.'
            if project_key in self._errors:
                return {'state': 'error', 'message': self._errors[project_key]}
            try: self._verify()
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                return {'state': 'unavailable', 'message': str(exc) if isinstance(exc, RuntimeError) else '플랫폼 실행 환경을 확인하세요.'}
            return {'state': 'stopped'}

    def client_if_running(self, project_key):
        """Private observation must never turn a status poll into a new child."""
        opaque_key(project_key)
        with self._lock:
            handle = self._instances.get(project_key)
            return handle['client'] if handle and handle['process'].poll() is None else None

    def active_work(self, project_root=None):
        return self.activity.active_work(project_root) if self.activity is not None else []

    def client(self, project_key: str) -> GetiClient:
        opaque_key(project_key)
        with self._lock:
            handle = self._instances.get(project_key)
            if handle and handle['process'].poll() is None: return handle['client']
            if handle: self.stop(project_key)
            self._verify()
            instance = self.root / project_key
            if instance.is_symlink(): raise RuntimeError('Platform instance root cannot be a symlink.')
            marker = instance / 'instance.json'
            if instance.exists() and any(instance.iterdir()) and not marker.is_file():
                raise RuntimeError('Refusing an existing unowned platform data root.')
            instance.mkdir(parents=True, exist_ok=True, mode=0o700)
            if marker.exists():
                receipt = json.loads(marker.read_text())
                if receipt.get('project_key') != project_key or receipt.get('source_sha') != PINNED_SHA:
                    raise RuntimeError('Platform instance ownership does not match this project.')
            else:
                marker.write_text(json.dumps({'project_key': project_key, 'source_sha': PINNED_SHA, 'instance_id': secrets.token_hex(16)}))
            # A second parent must not attach to or terminate this parent's child.
            lockfile = (instance / 'owner.lock').open('a+')
            try:
                if os.name == 'nt':
                    import msvcrt
                    lockfile.seek(0)
                    lockfile.write('0')
                    lockfile.flush()
                    lockfile.seek(0)
                    msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                lockfile.close()
                raise RuntimeError('Platform instance already has an active parent owner.') from exc
            with socket.socket() as port_socket:
                port_socket.bind(('127.0.0.1', 0))
                port = port_socket.getsockname()[1]
            secret = secrets.token_urlsafe(48)
            env = {key: value for key, value in os.environ.items() if key in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}}
            private_home = instance / 'home'
            private_home.mkdir(exist_ok=True)
            if self.weights_root:
                for original in self.weights_root.rglob('*'):
                    if not original.is_file() or original.is_symlink(): continue
                    target = instance / 'data/pretrained_weights' / original.relative_to(self.weights_root)
                    if target.exists(): continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target)
            env.update({'HOME': str(private_home), 'USERPROFILE': str(private_home), 'DATA_DIR': str(instance / 'data'),
                        'LOG_DIR': str(instance / 'logs'), 'HOST': '127.0.0.1', 'PORT': str(port), 'GPU_SLOTS': '1',
                        'MODU_GETI_SECRET': secret, 'MODU_GETI_SOURCE_ROOT': str(self.source_root),
                        'MODU_GETI_PARENT_PIPE': '1', 'MODU_GETI_INSTANCE_GENERATION': secrets.token_hex(16),
                        'PYTHONDONTWRITEBYTECODE': '1',
                        'PYTHONPATH': str(self.entrypoint.parent) + os.pathsep + str(self.source_root / 'library/src'),
                        'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
                        'DO_NOT_TRACK': '1', 'OTEL_SDK_DISABLED': 'true', 'WANDB_MODE': 'disabled',
                        'CUDA_VISIBLE_DEVICES': '', 'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2',
                        'XDG_CACHE_HOME': str(instance / 'cache'), 'TORCH_HOME': str(instance / 'cache/torch'),
                        'HF_HOME': str(instance / 'cache/huggingface'), 'PYTHONUNBUFFERED': '1'})
            if getattr(__import__('sys'), 'frozen', False):
                for library_var in ('LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'):
                    original_library = os.environ.get(library_var + '_ORIG')
                    if original_library: env[library_var] = original_library
            log = (instance / 'runtime.log').open('ab')
            try:
                process = subprocess.Popen([str(self.python), '-B', str(self.entrypoint)], cwd=self.source_root / 'application/backend', env=env,
                                           stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                           pass_fds=(lockfile.fileno(),) if os.name != 'nt' else (),
                                           start_new_session=os.name != 'nt', creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
            except BaseException:
                log.close()
                lockfile.close()
                raise
            client = GetiClient(port, secret)
            record = {'generation': env['MODU_GETI_INSTANCE_GENERATION'], 'pid': process.pid, 'process_group': process.pid,
                      'created_at': psutil.Process(process.pid).create_time()}
            temporary = instance / 'process.tmp'
            temporary.write_text(json.dumps(record))
            temporary.replace(instance / 'process.json')
            self._instances[project_key] = {'process': process, 'client': client, 'log': log, 'lockfile': lockfile, **record}
            deadline = time.monotonic() + 90
            try:
                while time.monotonic() < deadline:
                    if process.poll() is not None: raise RuntimeError('플랫폼을 시작하지 못했습니다. 설치 환경과 소유 실행 로그를 확인하세요.')
                    try:
                        if client.request('GET', '/health', timeout=1).get('status') == 'ok':
                            self._errors.pop(project_key, None)
                            return client
                    except GetiError: pass
                    time.sleep(0.15)
                raise RuntimeError('플랫폼 시작 시간이 초과됐습니다. 설치 환경을 확인하세요.')
            except BaseException as exc:
                self.stop(project_key)
                self._errors[project_key] = str(exc)
                raise

    def stop(self, project_key: str):
        opaque_key(project_key)
        with self._lock:
            handle = self._instances.pop(project_key, None)
            if not handle: return
            process = handle['process']
            was_running = process.poll() is None
            if process.poll() is None:
                # EOF is the reviewed child's graceful shutdown signal. Keep
                # its workers alive while lifespan flushes durable evidence.
                if process.stdin: process.stdin.close()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    if os.name == 'nt': process.kill()
                    else: os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            # Scheduler grandchildren share the owned process group. A finished main
            # process alone does not establish that those workers have exited.
            # Verify every live member's generation before killing a leftover
            # group. A stale/reused PID can never authorize another process.
            record = {name: handle[name] for name in ('generation', 'pid', 'process_group', 'created_at')}
            remaining = self._generation_processes(record)
            for member in remaining:
                try: member.kill()
                except psutil.NoSuchProcess: pass
            if remaining: psutil.wait_procs(remaining, timeout=3)
            handle['log'].close()
            if process.stdin: process.stdin.close()
            handle['lockfile'].close()
            return self._record_exit(project_key, record)

    def _generation_processes(self, record):
        if os.name == 'nt':
            try:
                main = psutil.Process(record['pid'])
                candidates = [main, *main.children(recursive=True)] if main.create_time() == record['created_at'] else []
            except psutil.NoSuchProcess: candidates = []
        else:
            candidates = []
            for process in psutil.process_iter():
                try:
                    if os.getpgid(process.pid) == record['process_group']: candidates.append(process)
                except (ProcessLookupError, PermissionError, psutil.NoSuchProcess): continue
        live = []
        for process in candidates:
            try:
                if process.status() == psutil.STATUS_ZOMBIE: continue
                if process.create_time() < record['created_at'] or process.environ().get('MODU_GETI_INSTANCE_GENERATION') != record['generation']:
                    raise RuntimeError('Runtime process ownership could not be verified; no unrelated process was stopped.')
                live.append(process)
            except psutil.NoSuchProcess: continue
            except psutil.AccessDenied as exc: raise RuntimeError('Runtime worker exit could not be verified.') from exc
        return live

    def _record_exit(self, key, record):
        if self._generation_processes(record): raise RuntimeError('Runtime workers have not all exited yet.')
        evidence = {'generation': record['generation'], 'worker_exited': True, 'finished_at': time.time(),
                    'reason': 'owned process group exited', 'proof': 'os-process-identity-and-generation'}
        folder = self.root / key / 'exits'
        folder.mkdir(exist_ok=True)
        temporary = folder / (record['generation'] + '.tmp')
        with temporary.open('w') as output:
            json.dump(evidence, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(folder / (record['generation'] + '.json'))
        self._errors.pop(key, None)
        if self.activity is not None: self.activity.settle_generation(key, evidence)
        return evidence

    def exit_evidence(self, project_key, generation):
        opaque_key(project_key)
        opaque_key(generation)
        path = self.root / project_key / 'exits' / (generation + '.json')
        if path.is_file() and not path.is_symlink(): return json.loads(path.read_text())
        return None

    def recover(self, project_key):
        opaque_key(project_key)
        with self._lock:
            if project_key in self._instances: return self.stop(project_key)
            instance = self.root / project_key
            if instance.is_symlink(): raise RuntimeError('Runtime ownership is not a real directory.')
            try:
                marker = json.loads((instance / 'instance.json').read_text())
                record = json.loads((instance / 'process.json').read_text())
            except (OSError, ValueError) as exc: raise RuntimeError('No owned runtime generation is available to recover.') from exc
            if marker.get('project_key') != project_key or marker.get('source_sha') != PINNED_SHA:
                raise RuntimeError('Runtime ownership does not match this project.')
            opaque_key(record['generation'])
            lockfile = (instance / 'owner.lock').open('a+')
            try:
                if os.name == 'nt':
                    import msvcrt
                    if not lockfile.seek(0, os.SEEK_END):
                        lockfile.write('0')
                        lockfile.flush()
                    lockfile.seek(0)
                    try: msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as exc: raise RuntimeError('이전 플랫폼 소유자가 아직 실행 중이거나 종료 중입니다. 잠시 뒤 다시 확인하세요.') from exc
                else:
                    import fcntl
                    try: fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as exc: raise RuntimeError('이전 플랫폼 소유자가 아직 실행 중이거나 종료 중입니다. 잠시 뒤 다시 확인하세요.') from exc
                members = self._generation_processes(record)
                for process in members:
                    try: process.kill()
                    except psutil.NoSuchProcess: pass
                if members: psutil.wait_procs(members, timeout=3)
                return self._record_exit(project_key, record)
            finally: lockfile.close()

    def shutdown(self):
        with self._lock:
            for key in list(self._instances): self.stop(key)
        if self.activity is not None: self.activity.shutdown()
