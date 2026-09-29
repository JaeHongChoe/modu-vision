"""Compute profile and SSH transport contracts."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import create_app
from backend.remote.profiles import ComputeProfile, ProfileStore
from backend.remote.ssh_transport import SSHTransferCancelled, SSHTransport


def profile_data(**overrides):
    data = {
        "id": "server-42",
        "name": "Server 42",
        "ssh_target": "operator@gpu.example.test",
        "ssh_port": 22,
        "remote_root": "/data/home/kai/modu-vision",
        "runtime_kind": "python",
        "runtime_value": "/data/home/kai/venvs/modu/bin/python",
        "gpu_selector": None,
    }
    data.update(overrides)
    return data


def test_profile_store_persists_selection_and_clears_deleted_selection(tmp_path):
    store = ProfileStore(tmp_path / "compute_profiles.json")
    profile = ComputeProfile(**profile_data())

    assert store.list() == []
    assert store.get_selected() is None
    assert store.save(profile) == profile
    assert store.set_selected(profile.id) == profile.id

    reloaded = ProfileStore(tmp_path / "compute_profiles.json")
    assert reloaded.get(profile.id) == profile
    assert reloaded.get_selected() == profile.id
    saved = json.loads((tmp_path / "compute_profiles.json").read_text())
    assert saved["profiles"] == [profile_data()]
    assert not any(key in saved["profiles"][0] for key in ("password", "private_key", "token"))

    assert reloaded.delete(profile.id) is True
    assert store.get_selected() is None
    assert store.delete(profile.id) is False


@pytest.mark.parametrize(
    "override",
    [
        {"ssh_target": "-oProxyCommand=bad"},
        {"ssh_target": "kai@host; touch /tmp/owned"},
        {"ssh_port": 0},
        {"remote_root": "relative/root"},
        {"remote_root": "/data/../tmp"},
        {"runtime_value": "python3;touch /tmp/owned"},
        {"runtime_kind": "shell"},
        {"password": "secret"},
        {"private_key": "/tmp/key"},
    ],
)
def test_profile_rejects_invalid_or_credential_fields(override):
    with pytest.raises(ValidationError):
        ComputeProfile(**profile_data(**override))


def test_profile_store_rejects_selection_of_missing_profile(tmp_path):
    store = ProfileStore(tmp_path / "compute_profiles.json")
    with pytest.raises(KeyError):
        store.set_selected("missing")


def test_api_crud_selection_and_desktop_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path))
    app = create_app(project_dir=str(tmp_path / "projects"))
    client = TestClient(app)
    headers = {"X-Vision-Token": app.state.api_token}

    assert client.get("/api/compute/profiles").status_code == 401
    created = client.post("/api/compute/profiles", json={k: v for k, v in profile_data().items() if k != "id"}, headers=headers)
    assert created.status_code == 201
    profile = created.json()["profile"]
    assert profile["name"] == "Server 42"
    assert profile["id"]
    assert client.get("/api/compute/profiles", headers=headers).json() == {"profiles": [profile]}

    selected = client.put("/api/compute/selection", json={"compute_profile_id": profile["id"]}, headers=headers)
    assert selected.status_code == 200
    assert selected.json() == {"compute_profile_id": profile["id"]}
    assert client.get("/api/compute/selection", headers=headers).json() == selected.json()
    assert client.delete(f"/api/compute/profiles/{profile['id']}", headers=headers).status_code == 204
    assert client.get("/api/compute/selection", headers=headers).json() == {"compute_profile_id": None}
    assert client.put("/api/compute/selection", json={"compute_profile_id": "missing"}, headers=headers).status_code == 404


def test_api_rejects_credentials_and_invalid_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path))
    app = create_app(project_dir=str(tmp_path / "projects"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})

    for bad in ({"password": "secret"}, {"remote_root": "/tmp/../etc"}):
        payload = {k: v for k, v in profile_data(**bad).items() if k != "id"}
        assert client.post("/api/compute/profiles", json=payload).status_code == 422
    assert client.get("/api/compute/profiles").json() == {"profiles": []}


def test_app_startup_reconnects_durable_remote_jobs(monkeypatch, tmp_path):
    from backend.api.routes_training import training_job_manager

    recovered = []
    monkeypatch.setattr("backend.remote.coordinator.recover_remote_jobs", recovered.append)
    with TestClient(create_app(project_dir=str(tmp_path / "projects"))):
        pass
    assert recovered == [training_job_manager]


def test_ssh_exec_uses_strict_options_and_quotes_remote_arguments(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    transport = SSHTransport()
    profile = ComputeProfile(**profile_data())

    result = transport.exec(profile, ["python3", "-c", "print('a; b')"])

    assert result.stdout == "ok"
    argv, kwargs = calls[0]
    assert argv[:7] == [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-p", "22"
    ]
    assert argv[7] == "--"
    assert argv[8] == profile.ssh_target
    assert shlex.split(argv[9]) == ["python3", "-c", "print('a; b')"]
    assert kwargs["shell"] is False


@pytest.mark.parametrize("path", ["../escape", "/absolute", "runs/../escape", "runs/job/../../escape", "other/file", "runs/job/bad name"])
def test_ssh_transfer_rejects_paths_outside_a_run(tmp_path, path):
    profile = ComputeProfile(**profile_data())
    transport = SSHTransport()
    source = tmp_path / "snapshot.tar"
    source.write_bytes(b"test")
    with pytest.raises(ValueError):
        transport.upload(profile, source, path)
    with pytest.raises(ValueError):
        transport.download(profile, path, tmp_path / "out.tar")


def test_upload_cancel_stops_scp_process_group(monkeypatch, tmp_path):
    scp = tmp_path / "scp"
    marker = tmp_path / "orphan_child_ran"
    scp.write_text('#!/bin/sh\n(sleep 1; touch "$CHILD_MARKER") &\nwait\n')
    scp.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("CHILD_MARKER", str(marker))
    monkeypatch.setattr(
        SSHTransport, "exec",
        lambda self, profile, argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )
    source = tmp_path / "snapshot.tar"
    source.write_bytes(b"snapshot")
    profile = ComputeProfile(**profile_data())
    cancel = threading.Event()
    timer = threading.Timer(0.25, cancel.set)
    timer.start()

    started = time.monotonic()
    try:
        with pytest.raises(SSHTransferCancelled):
            SSHTransport().upload(profile, source, "runs/job-1/snapshot.tar", cancel=cancel)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 2
    time.sleep(1.1)
    assert not marker.exists()


def test_upload_restricts_run_directories_and_file_permissions(monkeypatch, tmp_path):
    scp = tmp_path / "scp"
    scp.write_text("#!/bin/sh\nexit 0\n")
    scp.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    commands = []

    def fake_exec(self, _profile, argv, **kwargs):
        commands.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(SSHTransport, "exec", fake_exec)
    source = tmp_path / "snapshot.tar"
    source.write_bytes(b"snapshot")
    profile = ComputeProfile(**profile_data())
    SSHTransport().upload(profile, source, "runs/job-1/snapshot.tar")

    runs = f"{profile.remote_root}/runs"
    run = f"{runs}/job-1"
    target = f"{run}/snapshot.tar"
    assert ["chmod", "700", runs, run] in commands
    assert ["install", "-m", "600", "/dev/null", target] in commands
    assert ["chmod", "600", target] in commands
    assert not any(cmd[0] == "chmod" and profile.remote_root in cmd[2:] for cmd in commands)


def test_launch_restricts_only_run_directories(monkeypatch):
    commands = []

    def fake_exec(self, _profile, argv, **kwargs):
        commands.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="12345\n", stderr="")

    monkeypatch.setattr(SSHTransport, "exec", fake_exec)
    profile = ComputeProfile(**profile_data())
    SSHTransport().launch(profile, ["-m", "backend.remote.worker", "train"], "job-1")

    runs = f"{profile.remote_root}/runs"
    assert ["chmod", "700", runs, f"{runs}/job-1"] in commands
    assert not any(cmd[0] == "chmod" and profile.remote_root in cmd[2:] for cmd in commands)
    launch_script = next(cmd[2] for cmd in commands if cmd[:2] == ["sh", "-c"])
    assert "umask 077;" in launch_script


def test_probe_runs_runtime_read_only_and_reports_device(monkeypatch, tmp_path):
    profile = ComputeProfile(**profile_data(remote_root=str(tmp_path), runtime_value=sys.executable))

    def local_exec(self, _profile, argv, *, timeout=30):
        env = dict(os.environ, TORCH_HOME=str(tmp_path / "empty-torch-cache"))
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False, env=env)

    monkeypatch.setattr(SSHTransport, "exec", local_exec)
    result = SSHTransport().probe(profile)

    assert result["ready"] is False
    assert result["device_type"] in ("cpu", "cuda")
    assert isinstance(result["checks"]["free_bytes"], int)
    assert result["checks"]["free_bytes"] > 0
    assert result["checks"]["runtime_dependencies"]["torch"] is True
    assert result["checks"]["pretrained_weights"]["resnet18"]["ok"] is False


def test_python_probe_hides_gpu_without_selector(monkeypatch):
    profile = ComputeProfile(**profile_data())
    commands = []

    def fake_exec(self, _profile, argv, *, timeout=30):
        commands.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="probe stopped")

    monkeypatch.setattr(SSHTransport, "exec", fake_exec)
    SSHTransport().probe(profile)
    assert "CUDA_VISIBLE_DEVICES=" in commands[0]


def test_probe_reports_broken_installed_dependency(monkeypatch, tmp_path):
    (tmp_path / "torch.py").write_text("raise RuntimeError('broken torch install')\n")
    profile = ComputeProfile(**profile_data(remote_root=str(tmp_path), runtime_value=sys.executable))

    def local_exec(self, _profile, argv, *, timeout=30):
        env = dict(os.environ, PYTHONPATH=str(tmp_path))
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False, env=env)

    monkeypatch.setattr(SSHTransport, "exec", local_exec)
    result = SSHTransport().probe(profile)

    assert result["ready"] is False
    assert result["checks"]["runtime_dependencies"]["torch"] is False


@pytest.mark.parametrize("missing_module", ["fastapi", "pydantic"])
def test_probe_requires_stage4_evaluation_imports(monkeypatch, missing_module):
    profile = ComputeProfile(**profile_data())
    checks = {
        "protocol_version": 1,
        "runtime_dependencies": {
            "torch": True, "torchvision": True, "cv2": True, "numpy": True,
            "PIL": True, "sklearn": True, "psutil": True,
            "fastapi": True, "pydantic": True,
        },
        "remote_root_exists": True,
        "free_bytes": 2_000_000_000,
        "device_type": "cpu",
        "device_name": "CPU",
    }
    checks["runtime_dependencies"][missing_module] = False

    def fake_exec(self, _profile, _argv, *, timeout=30):
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(checks), stderr="")

    monkeypatch.setattr(SSHTransport, "exec", fake_exec)
    result = SSHTransport().probe(profile)

    assert result["ready"] is False


def test_probe_blocks_when_pretrained_checkpoint_is_missing(monkeypatch):
    profile = ComputeProfile(**profile_data())
    checks = {
        "protocol_version": 1,
        "runtime_dependencies": {
            "torch": True, "torchvision": True, "cv2": True, "numpy": True,
            "PIL": True, "sklearn": True, "psutil": True,
            "fastapi": True, "pydantic": True,
        },
        "pretrained_weights": {"resnet18": {"ok": False}},
        "remote_root_exists": True,
        "free_bytes": 2_000_000_000,
        "device_type": "cpu",
        "device_name": "CPU",
    }

    def fake_exec(self, _profile, _argv, *, timeout=30):
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(checks), stderr="")

    monkeypatch.setattr(SSHTransport, "exec", fake_exec)
    assert SSHTransport().probe(profile)["ready"] is False


def test_probe_parses_last_json_line_after_container_banner(monkeypatch):
    profile = ComputeProfile(**profile_data(runtime_kind="docker", runtime_value="worker:latest"))
    checks = {
        "protocol_version": 1,
        "runtime_dependencies": {
            name: True for name in (
                "torch", "torchvision", "cv2", "numpy", "PIL", "sklearn",
                "psutil", "fastapi", "pydantic",
            )
        },
        "pretrained_weights": {
            name: {"ok": True} for name in (
                "resnet18", "convnext_tiny", "efficientnet_b0",
                "fasterrcnn_mobilenet_v3_large_fpn", "fasterrcnn_resnet50_fpn_v2",
                "deeplabv3_resnet50", "deeplabv3_mobilenet_v3_large",
            )
        },
        "remote_root_exists": True,
        "free_bytes": 2_000_000_000,
        "device_type": "cpu",
        "device_name": "CPU",
    }

    commands = []

    def fake_exec(self, _profile, _argv, *, timeout=30):
        commands.append(list(_argv))
        if _argv == ["id", "-u"]:
            return subprocess.CompletedProcess([], 0, stdout="1017\n", stderr="")
        if _argv == ["id", "-g"]:
            return subprocess.CompletedProcess([], 0, stdout="1004\n", stderr="")
        return subprocess.CompletedProcess([], 0, stdout="CUDA runtime notice\n\n" + json.dumps(checks) + "\n", stderr="")

    monkeypatch.setattr(SSHTransport, "exec", fake_exec)
    assert SSHTransport().probe(profile)["ready"] is True
    docker_command = next(argv for argv in commands if argv[:2] == ["docker", "run"])
    assert docker_command[docker_command.index("--user") + 1] == "1017:1004"


def test_docker_read_only_probe_has_ephemeral_tmp_directory():
    profile = ComputeProfile(**profile_data(runtime_kind="docker", runtime_value="worker:latest"))
    command = SSHTransport().runtime_argv(profile, ["-c", "print(1)"], gpu=False)

    assert "--read-only" in command
    assert command[command.index("--tmpfs") + 1] == "/tmp:rw,nosuid,nodev,size=64m"
    assert "--gpus" not in command


def test_launch_and_cancel_confine_run_id_and_use_quoted_commands(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="12345\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    profile = ComputeProfile(**profile_data())
    transport = SSHTransport()
    assert transport.launch(profile, ["/data/home/kai/modu-vision/runs/run-1/worker.py", "train"], "run-1") == "12345"
    transport.touch_cancel(profile, "run-1")
    launch_command = next(command[-1] for command in calls if "nohup" in command[-1])
    assert "nohup" in launch_command
    assert "CUDA_VISIBLE_DEVICES=" in launch_command
    assert "/runs/run-1/cancel" in calls[-1][-1]
    with pytest.raises(ValueError):
        transport.launch(profile, ["worker.py"], "../escape")
    with pytest.raises(ValueError):
        transport.touch_cancel(profile, "../escape")


def test_docker_launch_uses_ssh_users_uid_for_writable_run_files(monkeypatch):
    commands = []

    def fake_exec(self, _profile, argv, *, timeout=30):
        commands.append(list(argv))
        if argv == ["id", "-u"]:
            output = "1017\n"
        elif argv == ["id", "-g"]:
            output = "1004\n"
        else:
            output = "container-id\n"
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")

    monkeypatch.setattr(SSHTransport, "exec", fake_exec)
    profile = ComputeProfile(**profile_data(runtime_kind="docker", runtime_value="worker:latest"))

    assert SSHTransport().launch(profile, ["-m", "backend.remote.worker", "train"], "run-1") == "container-id"
    docker_command = next(argv for argv in commands if argv[:2] == ["docker", "run"])
    assert docker_command[docker_command.index("--user") + 1] == "1017:1004"
    assert "--gpus" not in docker_command
    assert f"HOME={profile.remote_root}/runs/run-1" in docker_command
    assert f"XDG_CACHE_HOME={profile.remote_root}/runs/run-1/.cache" in docker_command


def test_python_runtime_honors_selected_gpu():
    profile = ComputeProfile(**profile_data(gpu_selector="1"))
    command = SSHTransport().runtime_argv(profile, ["-m", "backend.remote.worker"], run_id="run-1")
    assert "CUDA_VISIBLE_DEVICES=1" in command


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected"),
    [
        (0, "/modu-vision-job-1 true\n", "", True),
        (0, "/modu-vision-job-1 false\n", "", False),
        (1, "", "Error: No such object: abcdef123456", False),
        (0, "/other-job true\n", "", False),
        (255, "", "ssh: disconnected", None),
    ],
)
def test_docker_is_running_distinguishes_job_exit_from_disconnect(monkeypatch, returncode, stdout, stderr, expected):
    profile = ComputeProfile(**profile_data(runtime_kind="docker", runtime_value="worker:latest"))
    commands = []

    def fake_exec(self, _profile, argv, *, timeout=30):
        commands.append(list(argv))
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(SSHTransport, "exec", fake_exec)
    assert SSHTransport().is_running(profile, "job-1", "abcdef123456") is expected
    assert commands[0][:3] == ["docker", "container", "inspect"]


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "expected"),
    [
        (0, "S python -m backend.remote.worker train --spec /data/home/kai/modu-vision/runs/job-1/spec.json\n", "", True),
        (0, "Z python -m backend.remote.worker train --spec /data/home/kai/modu-vision/runs/job-1/spec.json\n", "", False),
        (1, "", "", False),
        (0, "S unrelated-process\n", "", False),
        (255, "", "ssh: disconnected", None),
    ],
)
def test_python_is_running_checks_pid_and_run_identity(monkeypatch, returncode, stdout, stderr, expected):
    profile = ComputeProfile(**profile_data())

    def fake_exec(self, _profile, argv, *, timeout=30):
        assert argv[:3] == ["ps", "-ww", "-p"]
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(SSHTransport, "exec", fake_exec)
    assert SSHTransport().is_running(profile, "job-1", "12345") is expected
