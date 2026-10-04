"""Existing macOS Keychain authentication without changing agent or SSH policy."""

from __future__ import annotations

import shlex
import subprocess
import sys

import pytest

from backend.remote.profiles import ComputeProfile
from backend.remote.ssh_transport import SSHTransport


def profile() -> ComputeProfile:
    return ComputeProfile(id="keychain-fixture", name="Existing identity fixture",
        ssh_target="labeler@fixture.invalid", ssh_port=2222, remote_root="/srv/modu/private",
        runtime_kind="python", runtime_value="python3")


def options(argv: list[str]) -> dict[str, str]:
    return dict(argv[index + 1].split("=", 1) for index, item in enumerate(argv[:-1]) if item == "-o")


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
@pytest.mark.parametrize("kind", ["ssh", "scp", "rsync"])
def test_platform_transport_preserves_target_and_authentication_bounds(monkeypatch, platform, kind):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("VISION_AI_STUDIO_SSH_CONTROL_PATH", "/private/tmp/keychain fixture-%C")
    selected = profile()
    transport = SSHTransport()
    if kind == "ssh":
        command = transport._ssh_base(selected)
        assert command[-2:] == ["--", selected.ssh_target]
        port_flag = "-p"
    elif kind == "scp":
        command = transport._scp_base(selected)
        assert command[-1:] == ["--"]
        port_flag = "-P"
    else:
        transfer = transport._rsync_argv(selected, "/fixture/source with spaces.tar", f"{selected.ssh_target}:/srv/modu/private/runs/job/snapshot.tar")
        assert transfer[-3:] == ["--", "/fixture/source with spaces.tar", f"{selected.ssh_target}:/srv/modu/private/runs/job/snapshot.tar"]
        assert "--checksum" in transfer and "--partial" in transfer and "--chmod=u=rw,go-rwx" in transfer
        command = shlex.split(transfer[transfer.index("-e") + 1])
        assert "--" not in command and selected.ssh_target not in command
        port_flag = "-p"
    expected = ["scp" if kind == "scp" else "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        port_flag, "2222", "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2"]
    if platform == "darwin":
        expected += ["-o", "UseKeychain=yes", "-o", "AddKeysToAgent=no"]
    expected += ["-o", "ControlPath=/private/tmp/keychain fixture-%C", "-o", "ControlMaster=no"]
    if kind == "ssh": expected += ["--", selected.ssh_target]
    elif kind == "scp": expected += ["--"]
    assert command == expected
    assert command.count("UseKeychain=yes") == (1 if platform == "darwin" else 0)
    assert command.count("AddKeysToAgent=no") == (1 if platform == "darwin" else 0)


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_exec_uses_platform_auth_without_changing_command_or_subprocess_admission(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("VISION_AI_STUDIO_SSH_CONTROL_PATH", raising=False)
    calls = []

    def authenticate(argv, **kwargs):
        calls.append((argv, kwargs))
        actual = options(argv)
        accepted = platform != "darwin" or (actual.get("UseKeychain") == "yes" and actual.get("AddKeysToAgent") == "no")
        return subprocess.CompletedProcess(argv, 0 if accepted else 255, "fixture-host\n" if accepted else "",
            "" if accepted else "Permission denied (publickey,password).")

    monkeypatch.setattr(subprocess, "run", authenticate)
    selected = profile()
    remote = ["printf", "%s\\n", "literal; $(not-a-command) with spaces"]
    result = SSHTransport().exec(selected, remote, timeout=7.25)
    assert result.returncode == 0 and result.stdout == "fixture-host\n"
    argv, invocation = calls[0]
    assert argv[argv.index("--") + 1] == selected.ssh_target
    assert shlex.split(argv[-1]) == remote
    assert invocation == {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace",
        "shell": False, "timeout": 7.25, "check": False}


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple OpenSSH Keychain config parser is macOS specific")
@pytest.mark.parametrize("kind", ["ssh", "scp", "rsync"])
def test_native_macos_openssh_effective_configuration_overrides_conflicting_user_defaults(monkeypatch, tmp_path, kind):
    monkeypatch.delenv("VISION_AI_STUDIO_SSH_CONTROL_PATH", raising=False)
    config = tmp_path / "fixture-ssh-config"
    config.write_text("Host *\n  UseKeychain no\n  AddKeysToAgent yes\n  BatchMode no\n  StrictHostKeyChecking no\n  Port 2200\n", encoding="utf-8")
    selected = profile()
    transport = SSHTransport()
    if kind == "ssh": command = transport._ssh_base(selected)
    elif kind == "scp":
        # SCP passes these identical -o settings to its SSH child. Ask the actual
        # Apple client to parse that child configuration, without connecting.
        command = ["ssh", *["-p" if token == "-P" else token for token in transport._scp_base(selected)[1:]], selected.ssh_target]
    else:
        transfer = transport._rsync_argv(selected, "/fixture/source", f"{selected.ssh_target}:/fixture/target")
        command = [*shlex.split(transfer[transfer.index("-e") + 1]), "--", selected.ssh_target]
    # Apple accepts UseKeychain but omits it from ssh -G's dump. Verify that it
    # was supplied, then use the native parser to verify accepted safe policy.
    assert options(command).get("UseKeychain") == "yes"
    assert options(command).get("AddKeysToAgent") == "no"
    result = subprocess.run([command[0], "-G", "-F", str(config), *command[1:]],
        capture_output=True, text=True, shell=False, timeout=5, check=False)
    assert result.returncode == 0, result.stderr
    parsed = dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)
    assert parsed["addkeystoagent"] == "false"
    assert parsed["batchmode"] == "yes" and parsed["stricthostkeychecking"] == "true"
    assert parsed["user"] == "labeler" and parsed["hostname"] == "fixture.invalid" and parsed["port"] == "2222"
    assert parsed["connecttimeout"] == "10" and parsed["connectionattempts"] == "1"
    assert parsed["serveraliveinterval"] == "5" and parsed["serveralivecountmax"] == "2"


@pytest.mark.parametrize("kind", ["ssh", "scp", "rsync"])
def test_invalid_control_binding_still_refuses_before_starting_a_process(monkeypatch, kind):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("VISION_AI_STUDIO_SSH_CONTROL_PATH", "/private/tmp/unbound-socket")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("invalid binding started a process"))
    transport = SSHTransport()
    with pytest.raises(ValueError, match="one %C"):
        if kind == "ssh": transport.exec(profile(), ["hostname"])
        elif kind == "scp": transport._scp_base(profile())
        else: transport._rsync_argv(profile(), "/fixture/source", "/fixture/target")
