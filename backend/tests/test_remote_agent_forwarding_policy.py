"""Existing SSH/SCP/rsync commands authenticate locally without forwarding agent.

The sole external command is OpenSSH's local ``-G`` configuration rendering;
it makes no connection, agent request, remote command or model invocation.
"""
import shlex
import shutil
import subprocess

import pytest

from backend.remote.profiles import ComputeProfile
from backend.remote.ssh_transport import SSHTransport


@pytest.fixture
def profile():
    return ComputeProfile(id='local-agent-policy', name='Controlled SSH option fixture',
        ssh_target='fixture@127.0.0.1', ssh_port=22, remote_root='/data/controlled',
        runtime_kind='python', runtime_value='/usr/bin/python3', gpu_selector=None)


def options(argv):
    return [argv[index+1] for index, part in enumerate(argv[:-1]) if part == '-o']


@pytest.mark.parametrize('builder', ['_ssh_base', '_scp_base'])
def test_all_original_transport_bases_explicitly_disable_agent_forwarding(profile, builder):
    argv = getattr(SSHTransport, builder)(profile)
    assert options(argv).count('ForwardAgent=no') == 1
    assert not any(value.lower().startswith('forwardagent=') and value.lower() != 'forwardagent=no'
                   for value in options(argv))
    assert '-A' not in argv
    assert 'BatchMode=yes' in options(argv)
    assert 'StrictHostKeyChecking=yes' in options(argv)


def test_original_rsync_transport_keeps_local_agent_policy(profile):
    argv = SSHTransport()._rsync_argv(profile, '/controlled/source', 'fixture@127.0.0.1:/data/controlled/dest')
    remote_shell = shlex.split(argv[argv.index('-e')+1])
    assert options(remote_shell).count('ForwardAgent=no') == 1
    assert '-A' not in remote_shell


def test_original_ssh_options_override_forwarding_enabled_by_host_configuration(profile, tmp_path):
    config = tmp_path/'controlled-ssh-config'
    config.write_text('Host *\n  ForwardAgent yes\n', encoding='utf-8')
    config.chmod(0o600)
    executable = shutil.which('ssh')
    assert executable is not None, 'The existing OpenSSH transport requires the local ssh executable'
    argv = SSHTransport._ssh_base(profile)
    result = subprocess.run([executable, '-G', '-F', str(config), *argv[1:]],
        capture_output=True, text=True, timeout=5, check=False)
    assert result.returncode == 0, result.stderr
    forwarding = [line.split(maxsplit=1)[1] for line in result.stdout.splitlines()
                  if line.startswith('forwardagent ')]
    assert forwarding == ['no']
