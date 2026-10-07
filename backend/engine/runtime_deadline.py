"""Own a deadline and fence surviving observed process-group work.

Observed group reconciliation is not complete or escaped process-tree proof.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import weakref
from contextlib import contextmanager
from typing import Any


_EXECUTIONS = weakref.WeakKeyDictionary()
_EXECUTIONS_LOCK = threading.Lock()


class _ProcessWorkspace:
    """Keep private request/cache files until the owned result is confirmed."""
    def __init__(self, prefix, directory):
        import tempfile
        from pathlib import Path
        self.path = Path(tempfile.mkdtemp(prefix=prefix,dir=directory)).resolve()
        self.path.chmod(0o700)
        self._identity = self.path.lstat()
        self._fd = os.open(self.path,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0)) if os.name=='posix' else None
        self._started = self._finished = False

    def _matches(self):
        import stat
        try:
            original=os.fstat(self._fd) if self._fd is not None else self._identity;current=self.path.lstat()
            return stat.S_ISDIR(current.st_mode) and (original.st_dev,original.st_ino)==(current.st_dev,current.st_ino)
        except OSError:return False

    def started(self):
        if not self._matches():raise RuntimeError('Owned workspace identity changed before execution')
        self._started=True

    def finished(self, outcome):
        if not self._matches():raise RuntimeError('Owned workspace identity changed after execution')
        if not isinstance(outcome,dict):return
        if outcome.get('status')=='completed' and type(outcome.get('returncode')) is int:
            self._finished=True
        elif outcome.get('status') in ('timeout','cancelled'):
            deadline=outcome.get('deadline')
            self._finished=isinstance(deadline,dict) and deadline.get('leader_exit_confirmed') is True and deadline.get('terminated') is True

    def close(self):
        try:
            # Without a retained directory handle, do not authorize removal or
            # write a recovery marker through a possibly reused path identity.
            # Native Windows cleanup remains separately unqualified.
            if self._fd is None:return
            # An unconfirmed result/exception cannot authorize removal. A moved
            # or replaced directory is never followed or treated as original.
            if not self._matches():return
            if self._started and not self._finished:
                import json
                value={'schema_version':1,'status':'recovery_required','execution_attempt_started':True,
                    'reason':'owned_process_result_unconfirmed','complete_process_tree_verified':False,
                    'automatic_cleanup_performed':False}
                try:
                    fd=os.open('workspace-retention.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600,dir_fd=self._fd)
                    with os.fdopen(fd,'w') as out:json.dump(value,out);out.flush();os.fsync(out.fileno())
                except OSError:pass  # Preserve the workspace and original exception; never replace an existing marker.
            else:
                import shutil
                import stat
                if not shutil.rmtree.avoids_symlink_attacks or os.listdir not in os.supports_fd:return
                # Clear only entries of the retained original directory. Do
                # not remove its root by resolving a path again: a replacement
                # between identity check and deletion cannot become ours.
                try:
                    for name in os.listdir(self._fd):
                        entry=os.stat(name,dir_fd=self._fd,follow_symlinks=False)
                        if stat.S_ISDIR(entry.st_mode):shutil.rmtree(name,dir_fd=self._fd)
                        else:os.unlink(name,dir_fd=self._fd)
                except OSError:pass  # Preserve remaining owned bytes and any original caller error.
        finally:
            if self._fd is not None:
                fd,self._fd=self._fd,None
                os.close(fd)


@contextmanager
def owned_process_workspace(*,prefix,directory=None):
    """Own one private directory; uncertain work has no automatic cleanup path."""
    workspace=_ProcessWorkspace(prefix,directory)
    try:yield workspace
    finally:workspace.close()


def _optional_psutil():
    # Generation exports do not require psutil. Its existing installation can
    # supply stronger positive identities; stdlib observation still fences a
    # surviving group without granting identity/exit authority from a scan.
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def _group_members(group):
    """Read-only POSIX group presence, never a cached-group signal."""
    from pathlib import Path
    if Path('/proc/self/stat').is_file():
        rows = {}
        for index, directory in enumerate(Path('/proc').iterdir()):
            if index >= 65536: raise RuntimeError('Process observation exceeds its bound')
            if not directory.name.isdecimal(): continue
            try:
                raw = (directory/'stat').read_text()[:8192]
                fields = raw[raw.rfind(')')+2:].split()
                if int(fields[2]) == group:
                    rows[int(directory.name)] = {'state':fields[0], 'session':int(fields[3])}
            except (FileNotFoundError, ProcessLookupError): continue
        return rows
    result = subprocess.run(['/bin/ps','-axo','pid=,pgid=,stat='], capture_output=True,
        timeout=.5, check=True, env={'PATH':os.defpath,'LANG':'C'})
    if len(result.stdout) > 2*1024*1024: raise RuntimeError('Process observation exceeds its bound')
    rows = {}
    for line in result.stdout.decode('ascii',errors='strict').splitlines():
        pid, pgid, state = line.split()
        if int(pgid) == group:
            try: rows[int(pid)] = {'state':state,'session':os.getsid(int(pid))}
            except ProcessLookupError: continue
    return rows


class _OwnedGroup:
    """A bounded local record; unknown identity can only retain ownership."""
    def __init__(self, process, module):
        self.process, self.module = process, module
        self.group = self.session = None
        self.leader = None
        self.members = {}
        self.remaining = set()
        self.unknown = set()
        self.observation_failed = False
        self.termination_attempted = False
        self.termination_failed = False
        if os.name == 'posix':
            try:
                group, session = os.getpgid(process.pid), os.getsid(process.pid)
                if group == session == process.pid: self.group, self.session = group, session
                else: self.observation_failed = True
            except OSError: self.observation_failed = True
        elif module is None:
            # An unsupported group observer cannot attest descendant cleanup.
            self.observation_failed = True
        if module is not None and process.poll() is None:
            try:
                parent = module.Process(process.pid)
                self.leader = (parent.create_time(), tuple(parent.cmdline()))
            except module.Error: pass

    def leader_matches(self):
        if self.process.poll() is not None: return False
        if os.name == 'posix' and self.group is None: return False
        errors = (OSError,) if self.module is None else (OSError,self.module.Error)
        try:
            if self.group is not None and (os.getpgid(self.process.pid) != self.group
                    or os.getsid(self.process.pid) != self.session): return False
            if self.module is not None:
                current = self.module.Process(self.process.pid)
                if self.leader is None or (current.create_time(),tuple(current.cmdline())) != self.leader: return False
            return True
        except errors: return False

    def member_state(self, identity):
        if self.module is None: return 'unknown'
        try:
            current = self.module.Process(identity['pid'])
            if current.create_time() != identity['birth'] or tuple(current.cmdline()) != tuple(identity['command']):
                return 'unknown'
            return 'exited' if current.status() == self.module.STATUS_ZOMBIE else 'live'
        except self.module.NoSuchProcess: return 'exited'
        except self.module.Error: return 'unknown'

    def observe(self):
        live_leader = self.leader_matches()
        rows = {}
        if self.group is not None:
            try:
                rows = _group_members(self.group)
                if len(rows) > 1000: raise RuntimeError('Owned group observation exceeds its bound')
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                self.observation_failed = True
        elif self.module is not None and live_leader:
            try: rows = {p.pid:{'state':'?', 'session':None} for p in self.module.Process(self.process.pid).children(recursive=True)}
            except self.module.Error: self.observation_failed = True
        for pid, row in rows.items():
            if row['state'].startswith('Z'): continue
            if pid == self.process.pid:
                if not live_leader: self.unknown.add(pid)
                continue
            if live_leader and self.module is not None and (self.group is None or row['session'] == self.session):
                try:
                    member = self.module.Process(pid)
                    identity = {'pid':pid, 'birth':member.create_time(), 'command':member.cmdline()}
                    if self.leader_matches() and identity['birth'] >= self.leader[0]: self.members[pid] = identity
                except self.module.Error: pass
            if not live_leader and pid not in self.members: self.unknown.add(pid)
            if self.group is not None and row['session'] != self.session: self.unknown.add(pid)
        self.remaining = set()
        for pid, identity in self.members.items():
            state = self.member_state(identity)
            if state == 'live': self.remaining.add(pid)
            elif state == 'unknown': self.unknown.add(pid)
        return self.remaining or self.unknown or self.observation_failed

    def reconcile(self):
        # A previously unknown member never becomes enrolled by disappearing.
        if self.unknown or self.observation_failed or not self.members: return False
        return not self.observe() and self.process.poll() is not None

    def evidence(self):
        return {'scope':'observed_original_process_group' if os.name=='posix' else 'observed_descendants',
            'group':self.group,'session':self.session,
            'leader_exit_confirmed':self.process.poll() is not None,
            'registered_members':list(self.members.values()),'remaining_members':sorted(self.remaining),
            'unknown_members':sorted(self.unknown),'observation_failed':self.observation_failed,
            'group_exit_verified':False,'process_tree_exit_verified':False,'recovery_required':True,
            'termination_attempted':self.termination_attempted,'termination_failed':self.termination_failed}


def _retain_execution(event, ownership):
    if event is None: return
    with _EXECUTIONS_LOCK: reference = _EXECUTIONS.get(event)
    handle = reference() if reference is not None else None
    if handle is not None:
        with handle._state_lock:
            if handle._active is event: handle._quarantine = ownership


def _observe_owned(ownership, event):
    try: return ownership.observe()
    except BaseException:
        ownership.observation_failed = True
        _retain_execution(event,ownership)
        raise


class CancellableExecution:
    """Serialize a handle and cancel only its active call, never queued future work."""
    def __init__(self):
        self._execution_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active = None
        self._quarantine = None

    @contextmanager
    def running(self):
        with self._execution_lock:
            with self._state_lock: retained = self._quarantine
            if retained is not None:
                if not retained.reconcile(): raise RuntimeError('Owned process group remains unreconciled')
                with self._state_lock:
                    self._quarantine = None
                    self._active = None
            event = threading.Event()
            with self._state_lock:
                self._active = event
            with _EXECUTIONS_LOCK: _EXECUTIONS[event] = weakref.ref(self)
            try:
                yield event
            finally:
                with self._state_lock:
                    if self._quarantine is None: self._active = None
                with _EXECUTIONS_LOCK: _EXECUTIONS.pop(event,None)

    def cancel(self):
        with self._state_lock:
            if self._active is None:
                return False
            self._active.set()
            return True


def validate_deadline(value):
    if value is not None and (type(value) is not int or not 1 <= value <= 86400000):
        raise ValueError('Inference deadline_ms must be an integer from 1 to 86400000')
    return value


def _terminate_owned(process, ownership=None):
    if process.poll() is None:
        if ownership is not None and not ownership.leader_matches():
            ownership.unknown.add(process.pid)
            return False
        if ownership is not None: ownership.termination_attempted = True
        if os.name == 'nt':
            # Popen retains the original Windows process handle. A numeric
            # taskkill /T lookup cannot authorize a reused PID or descendants.
            process.kill()
        else:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        if process.poll() is None:
            process.kill()
    process.wait(timeout=5)
    return True


def execute_owned_process(command, *, deadline_ms, env=None, cwd=None,cancel_event=None) -> dict[str, Any]:
    """Budget initialization and refuse success for observed surviving work."""
    validate_deadline(deadline_ms)
    started=time.monotonic()
    from backend.engine.process_isolation import session_isolation  # packaged runtimes have no backend.remote
    # Files avoid a child blocking on a filled output pipe. Inference results use
    # a separate bounded JSON file, not arbitrary model logging on stdout.
    import tempfile
    module = _optional_psutil()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        # Own session on POSIX; own process group and no console window on Windows.
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr,env=env,cwd=cwd,**session_isolation())
        ownership = _OwnedGroup(process,module)
        cancelled=False
        timed_out=False
        try:
            while process.poll() is None:
                cancelled=cancel_event is not None and cancel_event.is_set()
                if cancelled or (deadline_ms is not None and (time.monotonic()-started)*1000>=deadline_ms):raise subprocess.TimeoutExpired(command,deadline_ms)
                _observe_owned(ownership,cancel_event)
                remaining = .025 if deadline_ms is None else min(.025,max(.0001,deadline_ms/1000-(time.monotonic()-started)))
                try:process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:pass
        except subprocess.TimeoutExpired:
            timed_out=True
            try: _terminate_owned(process,ownership)
            except BaseException as error:
                ownership.termination_failed = ownership.observation_failed = True
                _retain_execution(cancel_event,ownership)
                if not isinstance(error,Exception):
                    raise
        except BaseException:
            # Ctrl+C or another interrupted wait must not leave inference behind.
            try: _terminate_owned(process,ownership)
            except BaseException:
                ownership.termination_failed = ownership.observation_failed = True
            if ownership.observation_failed: _retain_execution(cancel_event,ownership)
            try:
                if _observe_owned(ownership,cancel_event): _retain_execution(cancel_event,ownership)
            except BaseException: pass  # Retain ownership and preserve the original interruption.
            raise
        unreconciled = _observe_owned(ownership,cancel_event)
        if unreconciled: _retain_execution(cancel_event,ownership)
        stdout.seek(0);stderr.seek(0)
        output=stdout.read(65536).decode('utf-8',errors='replace')
        errors=stderr.read(65536).decode('utf-8',errors='replace')
        if unreconciled:
            # Preserve only helper-owned bounded diagnostics. Caller temporary
            # directories and package roots remain the caller's responsibility.
            import json
            from pathlib import Path
            diagnostics=Path(tempfile.mkdtemp(prefix='vision-unreconciled-group-'))
            diagnostics.chmod(0o700)
            for name,value in (('stdout.txt',output),('stderr.txt',errors),('ownership.json',json.dumps(ownership.evidence(),sort_keys=True))):
                path=diagnostics/name;path.write_text(value,encoding='utf-8');path.chmod(0o600)
            return {'status':'uncertain','returncode':1,'leader_returncode':process.returncode,
                'final_verdict':'REVIEW','rejection_reason':'OWNED_PROCESS_GROUP_UNRECONCILED',
                'roi_count':0,'defective_roi_count':0,'crops':[],'execution_steps':[],
                'stdout':output,'stderr':errors or 'Owned process group remains unreconciled',
                'pid':process.pid,'elapsed_ms':round((time.monotonic()-started)*1000,3),
                'ownership':ownership.evidence(),'private_diagnostics':str(diagnostics)}
        if timed_out:
            return {'status':'cancelled' if cancelled else 'timeout','final_verdict':'REVIEW','rejection_reason':'CANCELLED' if cancelled else 'INFERENCE_DEADLINE_EXCEEDED',
                'roi_count':0,'defective_roi_count':0,'crops':[],'execution_steps':[],
                'deadline':{'deadline_ms':deadline_ms,'elapsed_ms':round((time.monotonic()-started)*1000,3),
                            'pid':process.pid,'terminated':True,
                            'termination_scope':'owned_process_handle' if os.name=='nt' else 'owned_process_group',
                            'leader_exit_confirmed':process.poll() is not None,'process_tree_exit_verified':False}}
        return {'status':'completed','returncode':process.returncode,
                'stdout':output,'stderr':errors,
                'pid':process.pid,'elapsed_ms':round((time.monotonic()-started)*1000,3)}
