"""OpenSSH transport for app-owned remote run directories."""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Sequence

from backend.remote.profiles import ComputeProfile


_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_REMOTE_PART = re.compile(r"[A-Za-z0-9_.-]+\Z")
_REQUIRED_WEIGHTS = (
    "resnet18", "convnext_tiny", "efficientnet_b0",
    "fasterrcnn_mobilenet_v3_large_fpn", "fasterrcnn_resnet50_fpn_v2",
    "deeplabv3_resnet50", "deeplabv3_mobilenet_v3_large",
)
_PROBE_SCRIPT = r"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import urlparse

root = sys.argv[1]
scratch = tempfile.TemporaryDirectory(prefix='modu-vision-probe-')
os.environ['YOLO_CONFIG_DIR'] = str(Path(scratch.name) / 'ultralytics')
modules = ('torch', 'torchvision', 'cv2', 'numpy', 'PIL', 'sklearn', 'psutil', 'fastapi', 'pydantic',
           'timm', 'safetensors', 'huggingface_hub', 'ultralytics')
dependencies = {}
for name in modules:
    try:
        importlib.import_module(name)
        dependencies[name] = True
    except Exception:
        dependencies[name] = False
model_dependencies = {}
if dependencies['timm']:
    import timm
    supported = tuple(int(p) for p in re.findall(r'\d+', timm.__version__)[:3]) >= (1, 0, 24)
    model_dependencies['dinov3_vits16'] = supported and bool(timm.is_model('vit_small_patch16_dinov3.lvd1689m'))
    model_dependencies['dinov3_vitb16'] = supported and bool(timm.is_model('vit_base_patch16_dinov3.lvd1689m'))
    model_dependencies['dinov3_vitl16'] = supported and bool(timm.is_model('vit_large_patch16_dinov3.lvd1689m'))
if dependencies['ultralytics']:
    from importlib.metadata import version
    parts = tuple(int(p) for p in re.findall(r'\d+', version('ultralytics'))[:3])
    model_dependencies['yolo26n'] = parts >= (8, 4, 41)
    model_dependencies['yolo26s'] = parts >= (8, 4, 41)
root_exists = os.path.isdir(root)
free_bytes = shutil.disk_usage(root).free if root_exists else 0
device_type = 'cpu'
device_name = 'CPU'
if dependencies['torch']:
    import torch
    if torch.cuda.is_available():
        device_type = 'cuda'
        device_name = torch.cuda.get_device_name(0)
pretrained_weights = {}
if dependencies['torch'] and dependencies['torchvision']:
    from torchvision import models
    from torchvision.models import detection, segmentation
    specifications = {
        'resnet18': models.ResNet18_Weights.DEFAULT,
        'convnext_tiny': models.ConvNeXt_Tiny_Weights.DEFAULT,
        'efficientnet_b0': models.EfficientNet_B0_Weights.DEFAULT,
        'fasterrcnn_mobilenet_v3_large_fpn': detection.FasterRCNN_MobileNet_V3_Large_FPN_Weights.DEFAULT,
        'fasterrcnn_resnet50_fpn_v2': detection.FasterRCNN_ResNet50_FPN_V2_Weights.DEFAULT,
        'deeplabv3_resnet50': segmentation.DeepLabV3_ResNet50_Weights.DEFAULT,
        'deeplabv3_mobilenet_v3_large': segmentation.DeepLabV3_MobileNet_V3_Large_Weights.DEFAULT,
    }
    cache = Path(torch.hub.get_dir()) / 'checkpoints'
    manifest_name = os.environ.get('MODU_VISION_WEIGHTS_MANIFEST') or str(cache.parent.parent / 'weights-manifest.json')
    try:
        manifest = json.loads(Path(manifest_name).read_text(encoding='utf-8'))
    except Exception:
        manifest = {}
    for name, specification in specifications.items():
        filename = Path(urlparse(specification.url).path).name
        checkpoint = cache / filename
        digest = None
        if checkpoint.is_file():
            hasher = hashlib.sha256()
            with checkpoint.open('rb') as reader:
                for block in iter(lambda: reader.read(1024 * 1024), b''):
                    hasher.update(block)
            digest = hasher.hexdigest()
        match = re.search(r'-([0-9a-f]{8,64})\.(?:pth|pt)$', filename)
        prefix_matches = bool(digest and match and digest.startswith(match.group(1)))
        recorded = manifest.get(name, {}) if isinstance(manifest, dict) else {}
        full_matches = recorded.get('file') == filename and recorded.get('sha256') == digest
        pretrained_weights[name] = {
            'ok': bool(prefix_matches and full_matches),
            'file': filename,
            'sha256': digest,
        }
scratch.cleanup()
print(json.dumps({
    'protocol_version': 1,
    'runtime_dependencies': dependencies,
    'model_dependencies': model_dependencies,
    'pretrained_weights': pretrained_weights,
    'remote_root_exists': root_exists,
    'free_bytes': free_bytes,
    'device_type': device_type,
    'device_name': device_name,
}))
"""


class SSHTransportError(RuntimeError):
    """An SSH, SCP, or runtime operation failed."""


class SSHTransferCancelled(SSHTransportError):
    """The caller stopped a transfer before it completed."""


def require_training_runtime(readiness, task, preset, overrides=None, *, warm_start=False):
    """Gate only the selected architecture; run-owned weights are transferred separately."""
    if not readiness.get('runtime_ready', readiness.get('ready', False)):
        raise ValueError(readiness.get('message') or 'Compute runtime is unavailable')
    from backend.engine.trainer import PRESET_CONFIGS
    from backend.engine.model_backbones import is_dino_backbone, canonical_dino_name
    options = overrides or {}
    config = PRESET_CONFIGS[preset]
    if task in ('classification', 'patch_classification'):
        model = options.get('backbone', config.backbone_classification)
    elif task == 'segmentation':
        model = options.get('model_name', config.backbone_segmentation)
    elif task == 'detection':
        model = options.get('backbone', config.backbone_detection)
    elif task in ('anomaly', 'anomaly_detection') and options.get('anomaly_method') == 'dino_synthetic':
        model = options.get('anomaly_backbone', 'dinov3_vits16')
    else:
        model = 'resnet18'
    checks = readiness.get('checks', {})
    dependencies = checks.get('runtime_dependencies', {})
    required = ('timm', 'safetensors', 'huggingface_hub') if is_dino_backbone(str(model)) else ('ultralytics',) if str(model).startswith('yolo') else ()
    missing = [name for name in required if not dependencies.get(name, False)]
    if missing:
        raise ValueError('Selected model requires installed remote dependencies: ' + ', '.join(missing))
    canonical = canonical_dino_name(str(model)) if is_dino_backbone(str(model)) else model
    if required and checks.get('model_dependencies') is not None and not checks['model_dependencies'].get(canonical, False):
        raise ValueError(f'Remote runtime does not implement {canonical}; update timm>=1.0.24 or ultralytics>=8.4.41')
    if not required and not warm_start and options.get('pretrained', True):
        if task in ('anomaly', 'anomaly_detection'):
            model = 'resnet18'
        elif task == 'detection' and model == 'fasterrcnn':
            model = 'fasterrcnn_mobilenet_v3_large_fpn' if preset == 'fast' else 'fasterrcnn_resnet50_fpn_v2'
        if model in _REQUIRED_WEIGHTS and not checks.get('pretrained_weights', {}).get(model, {}).get('ok'):
            raise ValueError(f'Remote verified pretrained weights are missing: {model}')


def _validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a simple identifier")
    return run_id


def _run_path(profile: ComputeProfile, run_id: str) -> str:
    return f"{profile.remote_root}/runs/{_validate_run_id(run_id)}"


def _remote_path(profile: ComputeProfile, relative: str) -> str:
    """Accept only normalized files below runs/<id>."""
    if not isinstance(relative, str) or relative.startswith("/") or "//" in relative or relative.endswith("/"):
        raise ValueError("remote path must be a normalized path inside a run")
    parts = relative.split("/")
    if len(parts) < 3 or parts[0] != "runs" or not _RUN_ID.fullmatch(parts[1]):
        raise ValueError("remote path must be inside runs/<run_id>")
    if any(part in (".", "..") or not _REMOTE_PART.fullmatch(part) for part in parts[2:]):
        raise ValueError("remote path contains an unsafe component")
    return str(PurePosixPath(profile.remote_root) / relative)


def _checked(result: subprocess.CompletedProcess[str], operation: str) -> subprocess.CompletedProcess[str]:
    if result.returncode != 0:
        raise SSHTransportError(f"{operation} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result


def _stop_transfer(process: subprocess.Popen[str]) -> None:
    """Stop SCP and its children, then reap the local process."""
    if process.poll() is None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, text=True, shell=False, timeout=5, check=False,
            )
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.communicate()


class SSHTransport:
    """Uses OpenSSH key/agent authentication and strict known-host checking."""

    @staticmethod
    def _ssh_base(profile: ComputeProfile) -> list[str]:
        return [
            "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-p", str(profile.ssh_port), "--", profile.ssh_target,
        ]

    @staticmethod
    def _scp_base(profile: ComputeProfile) -> list[str]:
        return [
            "scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-P", str(profile.ssh_port), "--",
        ]

    def exec(
        self, profile: ComputeProfile, argv: Sequence[str], *, timeout: float = 30
    ) -> subprocess.CompletedProcess[str]:
        if isinstance(argv, (str, bytes)) or not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
            raise ValueError("argv must be a non-empty string sequence")
        command = shlex.join(list(argv))
        return subprocess.run(
            [*self._ssh_base(profile), command],
            capture_output=True, text=True, shell=False, timeout=timeout, check=False,
        )

    def _ensure_private_directories(self, profile: ComputeProfile, last_directory: str) -> None:
        """Create run paths at 0700 without changing the provisioned root."""
        root = PurePosixPath(profile.remote_root)
        relative = PurePosixPath(last_directory).relative_to(root)
        if len(relative.parts) < 2 or relative.parts[:1] != ("runs",):
            raise ValueError("directory must be inside a run")
        directories = [str(root.joinpath(*relative.parts[:index])) for index in range(1, len(relative.parts) + 1)]
        _checked(self.exec(profile, ["test", "-d", profile.remote_root]), "find remote workspace")
        _checked(self.exec(profile, ["mkdir", "-p", "-m", "700", *directories]), "create private run directory")
        _checked(self.exec(profile, ["chmod", "700", *directories]), "protect run directory")

    def _remote_user(self, profile: ComputeProfile) -> str:
        """Return the SSH account's numeric UID:GID for matching Docker access."""
        uid = _checked(self.exec(profile, ["id", "-u"]), "read remote UID").stdout.strip()
        gid = _checked(self.exec(profile, ["id", "-g"]), "read remote GID").stdout.strip()
        if not uid.isdecimal() or not gid.isdecimal():
            raise SSHTransportError("remote UID or GID is invalid")
        return f"{uid}:{gid}"

    def runtime_argv(
        self,
        profile: ComputeProfile,
        argv: Sequence[str],
        *,
        gpu: bool = True,
        detached: bool = False,
        run_id: str | None = None,
        user: str | None = None,
    ) -> list[str]:
        """Build an interpreter command; argv begins with Python options/module."""
        if isinstance(argv, (str, bytes)) or any(not isinstance(arg, str) for arg in argv):
            raise ValueError("runtime argv must contain strings")
        run_dir = _run_path(profile, run_id) if run_id is not None else profile.remote_root
        python_path = f"{run_dir}/code" if run_id is not None else None
        if profile.runtime_kind == "python":
            command = [profile.runtime_value, *argv]
            environment = []
            if python_path:
                environment.append(f"PYTHONPATH={python_path}")
            if not gpu or not profile.gpu_selector:
                environment.append("CUDA_VISIBLE_DEVICES=")
            elif profile.gpu_selector != "all":
                environment.append(f"CUDA_VISIBLE_DEVICES={profile.gpu_selector}")
            return ["env", *environment, *command] if environment else command

        command = ["docker", "run", "--rm"]
        if detached:
            if run_id is None:
                raise ValueError("detached Docker runtime requires run_id")
            command += ["--detach", "--name", f"modu-vision-{run_id}"]
        else:
            command += ["--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m"]
        command += [
            "--network", "none",
            "--mount", f"type=bind,source={profile.remote_root},target={profile.remote_root}" + (",readonly" if not detached else ""),
            "--workdir", run_dir,
            "--env", "PYTHONDONTWRITEBYTECODE=1",
        ]
        if detached:
            # The host UID has no passwd entry inside a generic image. Without
            # HOME, Path.home() resolves to / and route imports try to create
            # their cache at an unwritable filesystem root.
            command += ["--env", f"HOME={run_dir}", "--env", f"XDG_CACHE_HOME={run_dir}/.cache"]
        if user is not None:
            if not re.fullmatch(r"[0-9]+:[0-9]+\Z", user):
                raise ValueError("Docker user must be numeric UID:GID")
            command += ["--user", user]
        if python_path:
            command += ["--env", f"PYTHONPATH={python_path}"]
        if gpu:
            selector = profile.gpu_selector or "all"
            command += ["--gpus", selector if selector == "all" else f"device={selector}"]
        return [*command, profile.runtime_value, "python", "-B", *argv]

    def probe(self, profile: ComputeProfile) -> dict:
        """Check SSH, runtime imports, device, and free space without a job."""
        user = None
        if profile.runtime_kind == "docker":
            try:
                user = self._remote_user(profile)
            except (SSHTransportError, OSError, subprocess.TimeoutExpired) as exc:
                return {"ready": False, "checks": {"ssh": False}, "message": str(exc)}
        argv = self.runtime_argv(
            profile, ["-B", "-c", _PROBE_SCRIPT, profile.remote_root],
            gpu=profile.gpu_selector is not None, user=user,
        )
        try:
            result = self.exec(profile, argv, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ready": False, "checks": {"ssh": False}, "message": str(exc)}
        if result.returncode != 0:
            return {
                "ready": False,
                "checks": {"ssh": False if result.returncode == 255 else True, "runtime": False},
                "message": result.stderr.strip() or f"Runtime exited with code {result.returncode}",
            }
        try:
            output_lines = result.stdout.strip().splitlines()
            checks = json.loads(output_lines[-1] if output_lines else "")
            if not isinstance(checks, dict):
                raise ValueError("probe output must be a JSON object")
            if checks.get("protocol_version") != 1:
                return {
                    "ready": False,
                    "checks": {"ssh": True, "runtime": True, **checks},
                    "message": "Incompatible remote worker protocol",
                }
            dependencies = checks["runtime_dependencies"]
            required = (
                "torch", "torchvision", "cv2", "numpy", "PIL", "sklearn",
                "psutil", "fastapi", "pydantic",
            )
            pretrained_weights = checks.get("pretrained_weights") or {}
            gpu_ready = profile.gpu_selector is None or checks["device_type"] == "cuda"
            runtime_ready = bool(
                checks["remote_root_exists"]
                and checks["free_bytes"] >= 1_000_000_000
                and all(dependencies.get(module, False) for module in required)
                and gpu_ready
            )
            # The selected task gates its own architectures and weights. An unused
            # legacy cache must not make a valid DINO/YOLO transfer unavailable.
            ready = runtime_ready
            missing = [name for name in required if not dependencies.get(name, False)]
            return {
                "ready": ready,
                'runtime_ready': runtime_ready,
                "device_name": checks["device_name"],
                "device_type": checks["device_type"],
                "checks": {"ssh": True, "runtime": True, **checks},
                "message": "Ready" if ready else (
                    "Selected GPU is unavailable to the runtime" if not gpu_ready else
                    'Missing runtime dependencies: ' + ', '.join(missing) if missing else
                    "Workspace or free space check failed"
                ),
            }
        except (ValueError, KeyError, TypeError) as exc:
            return {"ready": False, "checks": {"ssh": True, "runtime": False}, "message": f"Invalid probe response: {exc}"}

    def upload(
        self, profile: ComputeProfile, local: Path | str, remote_relative: str,
        *, cancel: threading.Event | None = None,
    ) -> subprocess.CompletedProcess[str]:
        remote = _remote_path(profile, remote_relative)
        source = Path(local).resolve(strict=True)
        if not source.is_file():
            raise ValueError("upload source must be a file")
        if cancel is not None and cancel.is_set():
            raise SSHTransferCancelled("upload cancelled")
        self._ensure_private_directories(profile, str(PurePosixPath(remote).parent))
        _checked(self.exec(profile, ["install", "-m", "600", "/dev/null", remote]), "protect upload destination")
        if cancel is not None and cancel.is_set():
            raise SSHTransferCancelled("upload cancelled")
        argv = [*self._scp_base(profile), str(source), f"{profile.ssh_target}:{remote}"]
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, shell=False, start_new_session=os.name == "posix",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + 3600
        while True:
            if cancel is not None and cancel.is_set():
                _stop_transfer(process)
                raise SSHTransferCancelled("upload cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_transfer(process)
                raise SSHTransportError("upload timed out")
            try:
                stdout, stderr = process.communicate(timeout=min(0.15, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if cancel is not None and cancel.is_set():
            raise SSHTransferCancelled("upload cancelled")
        result = _checked(subprocess.CompletedProcess(argv, process.returncode, stdout, stderr), "upload")
        _checked(self.exec(profile, ["chmod", "600", remote]), "protect uploaded file")
        return result

    def download(
        self, profile: ComputeProfile, remote_relative: str, local: Path | str
    ) -> subprocess.CompletedProcess[str]:
        remote = _remote_path(profile, remote_relative)
        destination = Path(local).expanduser().absolute()
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [*self._scp_base(profile), f"{profile.ssh_target}:{remote}", str(destination)],
            capture_output=True, text=True, shell=False, timeout=300, check=False,
        )
        return _checked(result, "download")

    def launch(self, profile: ComputeProfile, argv: Sequence[str], run_id: str) -> str:
        """Start a worker that outlives SSH; return its PID or container ID."""
        run_dir = _run_path(profile, run_id)
        self._ensure_private_directories(profile, run_dir)
        if profile.runtime_kind == "docker":
            user = self._remote_user(profile)
            result = self.exec(
                profile,
                self.runtime_argv(
                    profile, argv, run_id=run_id, detached=True,
                    gpu=profile.gpu_selector is not None, user=user,
                ),
                timeout=60,
            )
        else:
            command = shlex.join(self.runtime_argv(profile, argv, run_id=run_id))
            script = f"umask 077; cd {shlex.quote(run_dir)} || exit 1; nohup {command} > {shlex.quote(run_dir + '/worker.log')} 2>&1 < /dev/null & echo $!"
            result = self.exec(profile, ["sh", "-c", script], timeout=60)
        _checked(result, "launch")
        identity = result.stdout.strip()
        if not identity:
            raise SSHTransportError("launch did not return a process identity")
        return identity

    def touch_cancel(self, profile: ComputeProfile, run_id: str) -> subprocess.CompletedProcess[str]:
        """Signal only the worker under this profile's run directory."""
        run_dir = _run_path(profile, run_id)
        return _checked(self.exec(profile, ["touch", f"{run_dir}/cancel"]), "cancel signal")

    def is_running(self, profile: ComputeProfile, run_id: str, handle: str) -> bool | None:
        """True if this run is active, False if exited, None if status is unknown."""
        run_dir = _run_path(profile, run_id)
        if profile.runtime_kind == "docker":
            if not re.fullmatch(r"[A-Fa-f0-9]{12,64}\Z", handle):
                return None
            command = ["docker", "container", "inspect", "--format", "{{.Name}} {{.State.Running}}", handle]
        else:
            if not handle.isdecimal() or int(handle) <= 0:
                return None
            command = ["ps", "-ww", "-p", handle, "-o", "stat=", "-o", "args="]
        try:
            result = self.exec(profile, command, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 255:
            return None
        if profile.runtime_kind == "docker":
            if result.returncode != 0:
                error = result.stderr.lower()
                return False if "no such object" in error or "no such container" in error else None
            fields = result.stdout.strip().split()
            if len(fields) != 2:
                return None
            if fields[0] != f"/modu-vision-{run_id}":
                return False
            return {"true": True, "false": False}.get(fields[1].lower())
        if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
            return False
        if result.returncode != 0:
            return None
        fields = result.stdout.strip().split(maxsplit=1)
        if len(fields) != 2:
            return None
        state, arguments = fields
        if state.startswith("Z"):
            return False
        return bool("backend.remote.worker" in arguments and run_dir in arguments)
