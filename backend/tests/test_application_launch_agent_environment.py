"""The original owned-main spawn may use one validated local SSH agent socket.

No SSH requests, key bodies, model workers or native applications are used.
The signed inert installer/lease fixture is real; only the final Popen boundary
is intercepted so the actual environment decision is observable without spawn.
"""
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile

import pytest

from backend.tests.test_application_launch_lease import (
    controlled_canary_publication, installed, reserve,
)


class SpawnBoundary(RuntimeError):
    pass


@pytest.fixture
def local_agent():
    # AF_UNIX paths have a short platform limit. Use a fresh private real path,
    # independent of pytest's deliberately long retained artifact directory.
    base = Path('/var/tmp') if sys.platform == 'darwin' else Path('/tmp')
    with tempfile.TemporaryDirectory(prefix='mv-agent-', dir=base.resolve()) as name:
        parent = Path(name)
        parent.chmod(0o700)
        path = parent/'agent.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(path))
            path.chmod(0o600)
            server.listen(1)
            yield path


@pytest.fixture
def launch_boundary(tmp_path, monkeypatch):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path)
    supervisor = reserve(root, value)
    calls = []

    def stop_before_spawn(argv, **options):
        calls.append((list(argv), dict(options)))
        raise SpawnBoundary('controlled original Popen boundary; no child created')

    monkeypatch.setattr(lease.subprocess, 'Popen', stop_before_spawn)
    try:
        yield lease, supervisor, calls
    finally:
        supervisor.close()


@pytest.mark.parametrize('socket_mode', [0o600, 0o666])
def test_same_uid_private_agent_reaches_original_main_environment(
        launch_boundary, local_agent, monkeypatch, socket_mode):
    lease, supervisor, calls = launch_boundary
    local_agent.chmod(socket_mode)
    # macOS launchd uses /var/... with a root-owned /var -> /private/var
    # ancestor. The private immediate parent/socket must not themselves link.
    supplied = Path('/var/tmp')/local_agent.parent.name/local_agent.name if sys.platform == 'darwin' else local_agent
    monkeypatch.setenv('SSH_AUTH_SOCK', str(supplied))
    monkeypatch.setenv('FOREIGN_LAUNCH_ENV', 'must-not-reach-owned-main')
    monkeypatch.setenv('SSH_AGENT_PID', '123456')
    monkeypatch.setenv('VISION_AI_STUDIO_SSH_CONTROL_PATH', '/foreign/control-%C')
    with pytest.raises(SpawnBoundary):
        supervisor.start()
    assert len(calls) == 1
    environment = calls[0][1]['env']
    assert environment.get('SSH_AUTH_SOCK') == str(local_agent)
    assert 'FOREIGN_LAUNCH_ENV' not in environment
    assert 'SSH_AGENT_PID' not in environment
    assert 'VISION_AI_STUDIO_SSH_CONTROL_PATH' not in environment
    assert calls[0][1]['close_fds'] is True
    assert supervisor._process is None


@pytest.mark.parametrize('damage', ['stale', 'socket_symlink', 'parent_symlink',
                                    'public_parent', 'non_socket', 'foreign_owner'])
def test_invalid_agent_is_refused_before_original_main_spawn(
        launch_boundary, local_agent, monkeypatch, damage):
    lease, supervisor, calls = launch_boundary
    path = local_agent
    if damage == 'stale':
        path.unlink()
    elif damage == 'socket_symlink':
        path = local_agent.parent/'linked-agent'
        path.symlink_to(local_agent)
    elif damage == 'parent_symlink':
        path = local_agent.parent/'linked-parent'/'agent.sock'
        path.parent.symlink_to(local_agent.parent, target_is_directory=True)
    elif damage == 'public_parent':
        local_agent.parent.chmod(0o755)
    elif damage == 'non_socket':
        path = local_agent.parent/'not-agent'
        path.write_bytes(b'controlled ordinary data, not an agent or key')
        path.chmod(0o600)
    else:
        # Creating a genuinely foreign-owned socket would require privilege.
        # Substitute only this lstat ownership field, preserving real socket
        # type/path/permissions; the real spawn decision must still refuse it.
        original_lstat = Path.lstat

        def wrong_owner(candidate):
            value = original_lstat(candidate)
            if candidate == local_agent:
                fields = list(value)
                fields[4] = os.geteuid()+1
                return os.stat_result(fields)
            return value

        monkeypatch.setattr(Path, 'lstat', wrong_owner)
    monkeypatch.setenv('SSH_AUTH_SOCK', str(path))
    with pytest.raises(lease.LaunchLeaseError, match='SSH agent'):
        supervisor.start()
    assert calls == []
    assert supervisor._process is None


def test_absent_agent_keeps_original_environment_and_filters_foreign_values(launch_boundary, monkeypatch):
    lease, supervisor, calls = launch_boundary
    monkeypatch.delenv('SSH_AUTH_SOCK', raising=False)
    monkeypatch.setenv('FOREIGN_LAUNCH_ENV', 'must-not-reach-owned-main')
    monkeypatch.setenv('SSH_AGENT_PID', '123456')
    with pytest.raises(SpawnBoundary):
        supervisor.start()
    environment = calls[0][1]['env']
    assert 'SSH_AUTH_SOCK' not in environment
    assert 'FOREIGN_LAUNCH_ENV' not in environment
    assert 'SSH_AGENT_PID' not in environment
    assert set(environment) == {
        'PATH', 'LANG', 'VISION_AI_STUDIO_USER_DATA_DIR',
        'VISION_APPLICATION_LAUNCH_NONCE', 'VISION_APPLICATION_GENERATION',
        'VISION_APPLICATION_DATABASE_GENERATION', 'HOME', 'USERPROFILE',
        'CFFIXED_USER_HOME', 'TMPDIR', 'TMP', 'TEMP', 'XDG_CACHE_HOME',
        'XDG_CONFIG_HOME', 'TORCH_HOME', 'HF_HOME', 'MPLCONFIGDIR',
        'YOLO_CONFIG_DIR', 'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE',
        'HF_DATASETS_OFFLINE', 'CUDA_VISIBLE_DEVICES', 'NVIDIA_VISIBLE_DEVICES',
        'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
    }
    assert environment['PATH'] == os.defpath
    assert environment['LANG'] == 'C.UTF-8'
    assert environment['VISION_AI_STUDIO_USER_DATA_DIR'] == str(supervisor.root)
    assert environment['VISION_APPLICATION_LAUNCH_NONCE'] == supervisor.nonce
    assert Path(environment['HOME']).parent == supervisor.root/lease.RUNTIME_HOMES
    assert Path(environment['HOME']).name == supervisor.nonce
    assert calls[0][1]['close_fds'] is True
    assert stat.S_IMODE(Path(environment['HOME']).lstat().st_mode) == 0o700
