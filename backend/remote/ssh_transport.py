"""OpenSSH transport for app-owned remote run directories."""

from __future__ import annotations

import json
import csv
import io
import errno
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from uuid import uuid4
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
from importlib.metadata import version, PackageNotFoundError
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
versions = {}
distribution_names = {'PIL': 'Pillow', 'sklearn': 'scikit-learn', 'huggingface_hub': 'huggingface-hub'}
for name in modules:
    try:
        importlib.import_module(name)
        dependencies[name] = True
    except Exception:
        dependencies[name] = False
    try:
        versions[name] = version(distribution_names.get(name, name))
    except PackageNotFoundError:
        try:
            versions[name] = version('opencv-python-headless' if name == 'cv2' else 'opencv-python') if name == 'cv2' else None
        except PackageNotFoundError:
            versions[name] = None
dependency_lock = None
lock_name = os.environ.get('MODU_VISION_MODEL_DEPENDENCIES_LOCK')
if lock_name:
    lock = Path(lock_name)
    try:
        data = lock.read_bytes()
        pins = dict(line.split('==', 1) for line in data.decode().splitlines() if line and not line.startswith('#') and '==' in line)
        installed = {name: version(name) for name in pins}
        dependency_lock = {'sha256': hashlib.sha256(data).hexdigest(), 'pins': pins, 'installed': installed,
                           'matches': pins == installed}
    except (OSError, ValueError, PackageNotFoundError) as exc:
        dependency_lock = {'matches': False, 'error': type(exc).__name__ + ': ' + str(exc)}
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
cuda_device_count=0
if dependencies['torch']:
    import torch
    if torch.cuda.is_available():
        cuda_device_count=torch.cuda.device_count()
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
# Inspect known cache locations without fetching or constructing a model.
# These hashes identify observed bytes; the training loader still validates
# structure and never treats this probe as model quality approval.
def cached_weight(name, path):
    digest = None
    if isinstance(path, str):
        checkpoint = Path(path)
        if checkpoint.is_file() and checkpoint.stat().st_size:
            hasher = hashlib.sha256()
            with checkpoint.open('rb') as reader:
                for block in iter(lambda: reader.read(1024 * 1024), b''):
                    hasher.update(block)
            digest = hasher.hexdigest()
    pretrained_weights[name] = {'ok': bool(digest), 'file': Path(path).name if isinstance(path, str) else None,
                                'sha256': digest, 'content_verified': False}
if dependencies['huggingface_hub']:
    from huggingface_hub import try_to_load_from_cache
    for name, architecture in {'dinov3_vits16':'vit_small_patch16_dinov3.lvd1689m',
                              'dinov3_vitb16':'vit_base_patch16_dinov3.lvd1689m',
                              'dinov3_vitl16':'vit_large_patch16_dinov3.lvd1689m'}.items():
        cached_weight(name, try_to_load_from_cache('timm/'+architecture, 'model.safetensors'))
if dependencies['torch']:
    for name in ('yolo26n', 'yolo26s'):
        cached_weight(name, str(Path(torch.hub.get_dir()) / 'checkpoints' / (name+'.pt')))
scratch.cleanup()
print(json.dumps({
    'protocol_version': 1,
    'runtime_dependencies': dependencies,
    'runtime_versions': versions,
    'dependency_lock': dependency_lock,
    'owned_process_control': {'private_session': bool(shutil.which('setsid')), 'pidfd': hasattr(os, 'pidfd_open')},
    'model_dependencies': model_dependencies,
    'pretrained_weights': pretrained_weights,
    'remote_root_exists': root_exists,
    'free_bytes': free_bytes,
    'device_type': device_type,
    'device_name': device_name,
    'device_inventory':device_inventory(),
    'cuda_device_count':cuda_device_count,
}))
"""
_PROBE_SCRIPT=(Path(__file__).resolve().parents[1]/'engine'/'compute_inventory.py').read_text()+'\n'+_PROBE_SCRIPT


class SSHTransportError(RuntimeError):
    """An SSH, SCP, or runtime operation failed."""


class SSHTransferCancelled(SSHTransportError):
    """The caller stopped a transfer before it completed."""


def require_training_runtime(readiness, task, preset, overrides=None, *, warm_start=False):
    """Gate only the selected architecture; run-owned weights are transferred separately."""
    if not readiness.get('runtime_ready', readiness.get('ready', False)):
        raise ValueError(readiness.get('message') or 'Compute runtime is unavailable')
    if task in {'rotation','ocr','rotated_detection','enhancement','defect_gan','labeling'}:
        return
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
    versions = checks.get('runtime_versions')
    minima = {'timm': (1, 0, 24), 'safetensors': (0, 4, 0), 'huggingface_hub': (0, 24, 0), 'ultralytics': (8, 4, 41)}
    if versions is not None:
        unsupported = [name for name in required if tuple(int(p) for p in re.findall(r'\d+', str(versions.get(name) or ''))[:3]) < minima[name]]
        if unsupported:
            raise ValueError('Selected model requires supported remote dependency versions: ' + ', '.join(unsupported))
    if checks.get('dependency_lock') is not None and checks['dependency_lock'].get('matches') is not True:
        raise ValueError('Remote model dependency lock does not match the installed environment')
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


def _control_options() -> list[str]:
    path = os.environ.get('VISION_AI_STUDIO_SSH_CONTROL_PATH')
    if not path:
        return []
    if not path.startswith('/') or path.count('%C') != 1 or '%' in path.replace('%C', '') or '\x00' in path:
        raise ValueError('SSH control path must be absolute and contain one %C user/host/port binding')
    return ['-o', f'ControlPath={path}', '-o', 'ControlMaster=no']


class SSHTransport:
    """Uses OpenSSH key/agent authentication and strict known-host checking."""

    def supports_resumable_transfer(self, profile: ComputeProfile) -> bool:
        """Rsync is a host transport capability, independent of the worker image."""
        if not shutil.which('rsync'):
            return False
        try:
            result = self.exec(profile, ['rsync', '--version'], timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and result.stdout.lstrip().startswith('rsync ')

    def _rsync_argv(self, profile: ComputeProfile, source: str, target: str) -> list[str]:
        return ['rsync', '--times', '--perms', '--checksum', '--partial', '--partial-dir=.transfer-partials',
                '--chmod=u=rw,go-rwx', '--rsync-path=umask 077; rsync', '-e', shlex.join(self._ssh_base(profile)[:-2]),
                '--', source, target]

    @staticmethod
    def _ssh_base(profile: ComputeProfile) -> list[str]:
        return [
            "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-p", str(profile.ssh_port),
            "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
            *_control_options(),
            "--", profile.ssh_target,
        ]

    @staticmethod
    def _scp_base(profile: ComputeProfile) -> list[str]:
        return [
            "scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-P", str(profile.ssh_port),
            "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
            *_control_options(),
            "--",
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
            command += ['--env', 'MODU_VISION_RUNTIME_KIND=docker']
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
            if profile.runtime_kind == 'docker':
                # NVIDIA indexes inside a selected container start at zero.
                # Reservation selectors refer to the SSH host's indexes.
                visible = checks.get('device_inventory') or {'devices': []}
                checks['visible_device_inventory'] = visible
                checks['device_inventory'] = self._host_device_inventory(profile, visible)
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
                'transfer': {'mode': 'rsync' if self.supports_resumable_transfer(profile) else 'scp',
                             'resumable': self.supports_resumable_transfer(profile)},
                "message": "Ready" if ready else (
                    "Selected GPU is unavailable to the runtime" if not gpu_ready else
                    'Missing runtime dependencies: ' + ', '.join(missing) if missing else
                    "Workspace or free space check failed"
                ),
            }
        except (ValueError, KeyError, TypeError) as exc:
            return {"ready": False, "checks": {"ssh": True, "runtime": False}, "message": f"Invalid probe response: {exc}"}

    def _host_device_inventory(self, profile, visible):
        try:
            result = self.exec(profile, ['nvidia-smi', '--query-gpu=index,uuid,memory.total',
                                         '--format=csv,noheader,nounits'], timeout=10)
            if result.returncode != 0:
                raise ValueError('Host CUDA inventory could not be read')
            rows = []
            for values in csv.reader(io.StringIO(result.stdout)):
                selector, identifier, capacity = (part.strip() for part in values)
                memory = int(float(capacity))
                if not selector.isdecimal() or not identifier.startswith('GPU-') or memory <= 0:
                    raise ValueError('Host CUDA inventory is invalid')
                rows.append({'selector': selector, 'uuid': identifier, 'memory_mb': memory,
                             'parent_uuid': None, 'kind': 'cuda'})
            rows.extend(row for row in visible.get('devices', []) if row.get('kind') == 'mig')
            return {**visible, 'devices': rows}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {'devices': [], 'cpu': {'available': True}, 'mig_supported': False,
                    'prerequisite': 'Host CUDA identity inventory is unavailable; sharing cannot be allocated'}

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
        resumable = self.supports_resumable_transfer(profile)
        if not resumable:
            _checked(self.exec(profile, ["install", "-m", "600", "/dev/null", remote]), "protect upload destination")
        if cancel is not None and cancel.is_set():
            raise SSHTransferCancelled("upload cancelled")
        argv = self._rsync_argv(profile, str(source), f'{profile.ssh_target}:{remote}') if resumable else [
            *self._scp_base(profile), str(source), f"{profile.ssh_target}:{remote}"]
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
            self._rsync_argv(profile, f'{profile.ssh_target}:{remote}', str(destination))
            if self.supports_resumable_transfer(profile) else [*self._scp_base(profile), f"{profile.ssh_target}:{remote}", str(destination)],
            capture_output=True, text=True, shell=False, timeout=300, check=False,
        )
        # Downloads write at the local receiver. Keep an exhausted local
        # artifact volume distinct from uncertain SSH/worker connectivity.
        error = result.stderr.lower()
        if result.returncode not in (0, 255):
            if 'no space left on device' in error:
                raise OSError(errno.ENOSPC, 'Local storage capacity is insufficient for the downloaded artifact')
            if 'disk quota exceeded' in error:
                raise OSError(errno.EDQUOT, 'Local storage quota is insufficient for the downloaded artifact')
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
            # A private session plus a launch-time inherited marker binds all
            # child workers to this run for liveness and cancellation.
            worker_token = uuid4().hex
            command = shlex.join(['setsid', 'env', f'MODU_VISION_WORKER_TOKEN={worker_token}',
                                 *self.runtime_argv(profile, argv, run_id=run_id)])
            script = f"umask 077; cd {shlex.quote(run_dir)} || exit 1; nohup {command} > {shlex.quote(run_dir + '/worker.log')} 2>&1 < /dev/null & echo $!:{worker_token}"
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

    def recover_handle(self, profile, run_id, *, job_id, operation, spec_sha256):
        """Recover a lost launch acknowledgment from the run's bound receipt."""
        if not isinstance(spec_sha256, str) or not re.fullmatch('[a-f0-9]{64}', spec_sha256):
            return None
        try:
            result = self.exec(profile, ['cat', _run_path(profile, run_id) + '/worker_identity.json'], timeout=10)
            if result.returncode != 0:
                return None
            identity = json.loads(result.stdout)
            if (identity.get('protocol_version') != 1 or identity.get('run_id') != run_id
                    or identity.get('job_id') != job_id or identity.get('operation') != operation
                    or identity.get('spec_sha256') != spec_sha256 or identity.get('control_kind') != profile.runtime_kind):
                return None
            handle = identity.get('control_handle')
            if profile.runtime_kind == 'docker':
                return handle if isinstance(handle, str) and re.fullmatch('[a-fA-F0-9]{12,64}', handle) else None
            pid, token = str(handle).split(':', 1)
            if (pid.isdecimal() and int(pid) > 0 and re.fullmatch('[a-f0-9]{32}', token)
                    and identity.get('pid') == identity.get('group') == identity.get('session') == int(pid)
                    and identity.get('token') == token):
                return handle
        except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
            pass
        return None

    def _control_python(self, profile: ComputeProfile, run_id: str, handle: str, action: str):
        run_dir = _run_path(profile, run_id)
        parts = handle.split(':', 1)
        pid = parts[0]
        if not pid.isdecimal() or int(pid) <= 0 or (len(parts) == 2 and not re.fullmatch('[0-9a-f]{32}', parts[1])):
            return None
        # The source is embedded so a lost/unfinished code transfer cannot load
        # another installed application's module for a control operation.
        source = (Path(__file__).with_name('process_control.py')).read_text()
        try:
            return self.exec(profile, [profile.runtime_value, '-B', '-c', source,
                                      run_dir, pid, action, *parts[1:]], timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None

    def stop_owned(self, profile: ComputeProfile, run_id: str, handle: str, *, force: bool = False) -> bool | None:
        """Signal proven owned processes only; uncertainty never authorizes a kill."""
        _run_path(profile, run_id)
        if profile.runtime_kind == 'python':
            result = self._control_python(profile, run_id, handle, 'kill' if force else 'terminate')
            return True if result is not None and result.returncode == 0 else None
        running = self.is_running(profile, run_id, handle)
        if running is False:
            return True  # Already confirmed exited; no signal is needed.
        if running is not True:
            return None
        command = ['docker', 'kill', '--signal', 'KILL' if force else 'TERM', handle]
        try:
            result = self.exec(profile, command, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return True if result.returncode == 0 else None

    def is_running(self, profile: ComputeProfile, run_id: str, handle: str) -> bool | None:
        """True if this run is active, False if exited, None if status is unknown."""
        run_dir = _run_path(profile, run_id)
        if profile.runtime_kind == "docker":
            if not re.fullmatch(r"[A-Fa-f0-9]{12,64}\Z", handle):
                return None
            command = ["docker", "container", "inspect", "--format", "{{.Name}} {{.State.Running}}", handle]
        else:
            if ':' in handle:
                group = self._control_python(profile, run_id, handle, 'probe')
                if group is None or group.returncode != 4:
                    return {0: True, 1: False}.get(group.returncode) if group is not None else None
                # Before status initialization there can be no training
                # children; reconcile a failed launch through its exact PID.
                handle = handle.split(':', 1)[0]
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
                return None  # An identity mismatch cannot confirm worker death.
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
