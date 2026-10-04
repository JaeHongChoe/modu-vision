"""Deployment preflight (E07): every dependency a saved flow needs on its target, mapped to the node that needs it, with
what to do when it is missing.

Requirements (``RecipeRequirement``):

- ``model``: a model node's completed model (task and job id) and its checkpoint; the checkpoint SHA-256 is the evidence.
- ``calibration``: a measurement node's spatial calibration artifact (E03).
- ``runtime``: a Python distribution the flow needs on the target: the base runtime for the whole flow (``node_id``
  None), and for each model node PyTorch plus what its model family adds (timm for DINOv3 models, ultralytics for YOLO
  models), with the minimum version the package declares.
- ``device``: the device each model node runs on.

A requirement is ``ready``, ``missing``, ``mismatch`` (present but not the one named), ``unavailable`` (not installed or
not usable where the check ran) or ``unverified`` (the target is another computer; the package's own preflight checks it
there). One that is not ready and not unverified blocks its node and every node after it; nodes on other paths are not
blocked, and nothing stands in for a blocked node (no untrained weights, no other device). A requirement of the whole
flow blocks every node.

The report names the flow release it checked, the target and a hash of the environment it ran in; a report whose
release, target or environment is no longer the current one is stale and is not a deployment's evidence. Model weight
terms are recorded as a reference to the license matrix (docs/model-license-matrix.md), not judged here.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

KINDS = ('model', 'calibration', 'runtime', 'device')
STATES = ('ready', 'missing', 'mismatch', 'unavailable', 'unverified')
BLOCKING = ('missing', 'mismatch', 'unavailable')
BASE_RUNTIME = ('numpy>=1.26', 'Pillow>=10.4', 'opencv-python-headless>=4.10', 'pydantic>=2.8')
MODEL_RUNTIME = ('torch>=2.4', 'torchvision>=0.19')
TRACKED = ('torch', 'torchvision', 'timm', 'ultralytics', 'openvino', 'onnxruntime', 'numpy', 'Pillow',
           'opencv-python', 'opencv-python-headless', 'pydantic')
# Where a distribution can stand in for another (the headless OpenCV build and the full one provide the same module).
_ALTERNATIVES = {'opencv-python-headless': ('opencv-python-headless', 'opencv-python', 'opencv-contrib-python')}
_REQUIREMENT = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)>=([0-9]+(?:\.[0-9]+)*)$')


@dataclass(frozen=True)
class RecipeRequirement:
    node_id: Optional[str]          # None: the whole flow
    kind: str
    artifact_ref: str
    version_range: Optional[str]
    platform: Optional[str]
    license_ref: Optional[str]
    state: str
    evidence_ref: Optional[str]
    remedy: Optional[str]

    def __post_init__(self):
        if self.kind not in KINDS or self.state not in STATES:
            raise ValueError(f'unknown requirement kind or state: {self.kind}, {self.state}')


# ---------------------------------------------------------------------------------------------------------------- environment

def _machine() -> str:
    machine = platform.machine().lower()
    return {'amd64': 'x86_64', 'x64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)


def _os() -> str:
    return {'darwin': 'macos'}.get(platform.system().lower(), platform.system().lower())


def installed_version(name: str) -> Optional[str]:
    for candidate in _ALTERNATIVES.get(name, (name,)):
        try:
            return metadata.version(candidate)
        except metadata.PackageNotFoundError:
            continue
    return None


def _numbers(version: str) -> tuple[int, ...]:
    digits = re.match(r'\d+(?:\.\d+)*', version)
    return tuple(int(value) for value in digits[0].split('.')) if digits else ()


def satisfies(version: Optional[str], minimum: str) -> bool:
    if version is None:
        return False
    have, need = _numbers(version), _numbers(minimum)
    width = max(len(have), len(need))
    return have + (0,) * (width - len(have)) >= need + (0,) * (width - len(need))


def local_devices() -> list[str]:
    """The devices this computer can run a flow on now (OpenVINO devices are checked only when a target names one)."""
    devices = ['cpu']
    try:
        import torch
        if torch.cuda.is_available():
            devices += [f'cuda:{index}' for index in range(torch.cuda.device_count())]
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            devices.append('mps')
    except Exception:  # no usable PyTorch: the CPU entry stays, the model runtime requirement says why
        pass
    return devices


def environment() -> dict:
    """What the check ran in: platform, Python, the tracked distributions and the devices."""
    return {'os': _os(), 'architecture': _machine(), 'python': platform.python_version(),
            'distributions': {name: installed_version(name) for name in TRACKED}, 'devices': local_devices()}


def environment_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------------------------- targets

def normalize_target(target: Mapping[str, Any]) -> dict:
    """``this_computer`` (with its device) or ``edge`` (profile, os, architecture, device)."""
    kind = target.get('kind')
    device = target.get('device') or 'cpu'
    if not isinstance(device, str) or not re.fullmatch(r'cpu|mps|cuda(?::[0-9]+)?|openvino:[A-Za-z0-9_.-]+', device):
        raise ValueError('the target device is cpu, mps, cuda[:index] or openvino:<device>')
    if kind == 'this_computer':
        return {'kind': kind, 'device': device}
    if kind == 'edge':
        from backend.engine.edge_runtime import SUPPORTED_TARGETS
        profile, os_name, arch = target.get('profile'), target.get('os'), target.get('architecture')
        if profile not in ('edge_cpu', 'edge_cuda') or os_name not in SUPPORTED_TARGETS or arch not in SUPPORTED_TARGETS[os_name]:
            raise ValueError('an edge target names its profile (edge_cpu or edge_cuda), os and architecture')
        if (profile == 'edge_cpu') != (device == 'cpu'):
            raise ValueError('an edge_cpu target runs on cpu; an edge_cuda target on cuda')
        return {'kind': kind, 'profile': profile, 'os': os_name, 'architecture': arch, 'device': device}
    raise ValueError('the target is this_computer or edge')


# ---------------------------------------------------------------------------------------------------------------- models

def checkpoint_requirements(checkpoint: Path) -> set[str]:
    """The distributions a checkpoint's model family adds to the base runtime (from its metadata and its record)."""
    import pickle
    records = []
    meta = Path(checkpoint).with_name('model_meta.json')
    if meta.is_file() and not meta.is_symlink():
        try:
            records.append(json.loads(meta.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
    try:
        import torch
        records.append(torch.load(checkpoint, map_location='cpu', weights_only=True))
    except (ImportError, OSError, ValueError, RuntimeError, EOFError, IndexError, KeyError, pickle.UnpicklingError):
        pass  # a checksum-only legacy checkpoint adds nothing; its model requirement says whether it is usable
    extra: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        names = [str(record.get(key, '')).lower() for key in ('backbone', 'model_name', 'architecture')]
        if any('dinov3' in name for name in names):
            extra.add('timm>=1.0.24')
        if any(name.startswith('yolo') or 'detection:yolo' in name for name in names):
            extra.add('ultralytics>=8.4.41')
    return extra


def license_ref(task: str, checkpoint: Optional[Path]) -> Optional[str]:
    """The license matrix row of a trained model's kind (its weights' terms are reviewed there, not here)."""
    needs = checkpoint_requirements(checkpoint) if checkpoint is not None else set()
    if any(need.startswith('ultralytics') for need in needs):
        return 'model-license-matrix:yolo_derived'
    if any(need.startswith('timm') for need in needs):
        return 'model-license-matrix:dinov3_derived'
    if task in ('ocr', 'enhancement', 'rotation', 'rotated_detection'):
        return 'model-license-matrix:first_party_scratch'
    return 'model-license-matrix:torchvision_derived' if checkpoint is not None else None


# ---------------------------------------------------------------------------------------------------------------- requirements

ModelResolver = Callable[[Any, str, Optional[str]], Mapping[str, Any]]


def _runtime(node_id: Optional[str], requirement: str, here: bool, target: dict, license_note: Optional[str] = None) -> RecipeRequirement:
    name, minimum = _REQUIREMENT.fullmatch(requirement).groups()
    where = f"{target.get('os', _os())}/{target.get('architecture', _machine())}"
    if not here:
        return RecipeRequirement(node_id, 'runtime', name, f'>={minimum}', where, license_note, 'unverified', None,
                                 'install the package on the target and run its preflight there (run_flow.py --preflight)')
    version = installed_version(name)
    if satisfies(version, minimum):
        return RecipeRequirement(node_id, 'runtime', name, f'>={minimum}', where, license_note, 'ready', f'{name}=={version}', None)
    found = f'{version} is installed' if version else 'it is not installed'
    return RecipeRequirement(node_id, 'runtime', name, f'>={minimum}', where, license_note, 'unavailable', None,
                             f'install {name}>={minimum} in the runtime environment ({found}); the node does not run without it')


def _device(node_id: str, target: dict, here: bool) -> RecipeRequirement:
    device = target['device']
    where = f"{target.get('os', _os())}/{target.get('architecture', _machine())}"
    if not here:
        return RecipeRequirement(node_id, 'device', device, None, where, None, 'unverified', None,
                                 'run the package preflight on the target to confirm the device')
    try:
        if device.startswith('openvino:'):
            from backend.engine.openvino_runtime import require_openvino_device
            require_openvino_device(device.split(':', 1)[1])
            usable = True
        else:
            usable = device in local_devices() or (device == 'cuda' and 'cuda:0' in local_devices())
    except Exception:
        usable = False
    if usable:
        return RecipeRequirement(node_id, 'device', device, None, where, None, 'ready', device, None)
    return RecipeRequirement(node_id, 'device', device, None, where, None, 'unavailable', None,
                             f'{device} is not available here; choose a target whose device is present (no other device is used instead)')


def collect_requirements(pipeline, *, target: Mapping[str, Any], resolve_model: ModelResolver,
                         resolve_calibration: Callable[[str], Any], here: bool) -> list[RecipeRequirement]:
    """Every requirement of ``pipeline`` on ``target``. ``here``: the check runs on the target itself, so runtime and
    device requirements are checked; otherwise they are unverified."""
    from backend.engine.specialized_models import flow_model_task
    target = normalize_target(target) if 'kind' in target else dict(target)
    requirements = [_runtime(None, requirement, here, target) for requirement in BASE_RUNTIME]
    for node in pipeline.nodes:
        task = flow_model_task(node)
        if task is not None:
            job_id = node.data.model_job_id
            resolved = dict(resolve_model(node, task, job_id))
            checkpoint = resolved.get('checkpoint')
            state = resolved.get('state', 'missing')
            remedy = None if state == 'ready' else resolved.get('remedy') or (
                f'train a {task} model in step 3 or connect a completed one to node {node.id}' if not job_id
                else f'model {job_id} cannot be used: {resolved.get("detail") or state}; retrain it or connect another completed {task} model')
            lic = license_ref(task, checkpoint) if checkpoint else None
            requirements.append(RecipeRequirement(node.id, 'model', f'{task}:{job_id or "none"}', None, None, lic, state,
                                                  resolved.get('evidence_ref'), remedy))
            needs = set(MODEL_RUNTIME) | (checkpoint_requirements(checkpoint) if checkpoint else set())
            requirements += [_runtime(node.id, need, here, target) for need in sorted(needs)]
            requirements.append(_device(node.id, target, here))
        if node.data.node_type == 'measurement' and (node.data.params or {}).get('calibration_ref'):
            ref = node.data.params['calibration_ref']
            try:
                found = resolve_calibration(ref)
            except Exception:
                found = None
            requirements.append(RecipeRequirement(
                node.id, 'calibration', ref, None, None, None, 'ready' if found is not None else 'missing',
                ref if found is not None else None,
                None if found is not None else f'calibration {ref} is not in this project: recreate it in the measurement node '
                                                'or choose a calibration the project has (mm limits cannot be judged without it)'))
    return requirements


def blocked_nodes(pipeline, requirements: Iterable[RecipeRequirement]) -> dict[str, list[str]]:
    """Every node a failing requirement stops, with the requirements that stop it (its own or an upstream node's)."""
    downstream: dict[str, set[str]] = {node.id: set() for node in pipeline.nodes}
    for edge in pipeline.edges:
        downstream.setdefault(edge.source, set()).add(edge.target)
    blocked: dict[str, list[str]] = {}
    for requirement in requirements:
        if requirement.state not in BLOCKING:
            continue
        cause = f'{requirement.kind}:{requirement.artifact_ref}' + (f'@{requirement.node_id}' if requirement.node_id else '')
        start = [node.id for node in pipeline.nodes] if requirement.node_id is None else [requirement.node_id]
        seen, stack = set(), list(start)
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(downstream.get(current, ()))
        for node_id in seen:
            blocked.setdefault(node_id, [])
            if cause not in blocked[node_id]:
                blocked[node_id].append(cause)
    return {node_id: causes for node_id, causes in sorted(blocked.items())}


def build_report(pipeline, requirements: list[RecipeRequirement], *, release: Mapping[str, Any], target: Mapping[str, Any],
                 environment_value: Optional[Mapping[str, Any]]) -> dict:
    blocked = blocked_nodes(pipeline, requirements)
    decision = [node.id for node in pipeline.nodes if node.data.node_type == 'decision']
    states = [requirement.state for requirement in requirements]
    status = 'blocked' if any(state in BLOCKING for state in states) else 'unverified' if 'unverified' in states else 'ready'
    report = {'schema_version': 1, 'report_id': uuid.uuid4().hex, 'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'recipe_release': dict(release), 'target_identity': dict(target), 'status': status,
              'environment_hash': environment_hash(environment_value) if environment_value is not None else None,
              'environment': dict(environment_value) if environment_value is not None else None,
              'requirements': [asdict(requirement) for requirement in requirements],
              'counts': {state: states.count(state) for state in STATES},
              'blocked_nodes': blocked, 'decision_blocked': any(node_id in blocked for node_id in decision)}
    report['report_sha256'] = _report_sha(report)
    return report


def _report_sha(report: Mapping[str, Any]) -> str:
    body = {key: value for key, value in report.items() if key not in ('report_sha256', 'stale', 'stale_reasons')}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def staleness(report: Mapping[str, Any], *, release: Mapping[str, Any], target: Optional[Mapping[str, Any]],
              environment_value: Optional[Mapping[str, Any]], current_requirements: Optional[Iterable[RecipeRequirement]] = None) -> list[str]:
    """Why a saved report is not the current evidence: another release, another target, or (for a check that ran on
    the target) another environment: a runtime pack or a version changed, a device appeared or went."""
    reasons = []
    if dict(report.get('recipe_release') or {}) != dict(release):
        reasons.append('release_changed')
    if target is not None and dict(report.get('target_identity') or {}) != dict(target):
        reasons.append('target_changed')
    if report.get('environment_hash') is not None and environment_value is not None \
            and report['environment_hash'] != environment_hash(environment_value):
        reasons.append('environment_changed')
    if current_requirements is not None:
        def artifacts(rows):
            return {(row.get('node_id'), row.get('kind'), row.get('artifact_ref')): (row.get('state'), row.get('evidence_ref'))
                    for row in rows if row.get('kind') in ('model', 'calibration')}
        if artifacts(report.get('requirements') or []) != artifacts(asdict(row) for row in current_requirements):
            reasons.append('artifacts_changed')
    return reasons


# ---------------------------------------------------------------------------------------------------------------- storage

class PreflightStore:
    """Reports as files (``<report_id>.json``), each hash-checked when read."""

    def __init__(self, folder: Path | str):
        self.folder = Path(folder)

    def save(self, report: Mapping[str, Any]) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / f"{report['report_id']}.json"
        if target.exists() or target.is_symlink():
            raise ValueError('a preflight report is written once')
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.folder, prefix='.preflight-', delete=False) as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
            staged = Path(handle.name)
        os.replace(staged, target)
        return target

    def load(self, report_id: str) -> dict:
        if not isinstance(report_id, str) or not re.fullmatch(r'[0-9a-f]{32}', report_id):
            raise ValueError('no such preflight report')
        path = self.folder / f'{report_id}.json'
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(report_id)
        report = json.loads(path.read_text(encoding='utf-8'))
        if _report_sha(report) != report.get('report_sha256'):
            raise ValueError('the preflight report was changed after it was made')
        return report

    def list(self) -> list[dict]:
        rows = []
        for path in self.folder.glob('*.json') if self.folder.is_dir() else []:
            try:
                report = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            rows.append({key: report.get(key) for key in ('report_id', 'checked_at', 'status', 'recipe_release', 'target_identity', 'counts')})
        return sorted(rows, key=lambda row: (row['checked_at'] or '', row['report_id'] or ''), reverse=True)


# ---------------------------------------------------------------------------------------------------------------- package

def _package_requirements(root: Path, manifest: dict, pipeline, target: dict) -> list[RecipeRequirement]:
    """The same local artifact verification for a fresh check and for reopening its saved evidence."""
    from backend.engine.flow_package_runtime import verify_flow_package
    from backend.engine.spatial_calibration import package_calibrations
    files = {row['path']: row for row in manifest.get('files', []) if isinstance(row, dict) and isinstance(row.get('path'), str)}
    try:
        verify_flow_package(root)
        package_problem = None
    except (ValueError, OSError) as exc:
        package_problem = str(exc)
    def resolve_model(node, task, job_id):
        checkpoint = root / 'models' / str(job_id) / 'best_model.pt'
        row = files.get(f'models/{job_id}/best_model.pt')
        if not job_id or row is None or not checkpoint.is_file():
            return {'state': 'missing', 'detail': 'the package has no checkpoint for it'}
        digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        if digest != row.get('sha256'):
            return {'state': 'mismatch', 'detail': 'the checkpoint differs from the manifest', 'checkpoint': None,
                    'remedy': 'export the package again; a changed checkpoint is never run'}
        return {'state': 'ready', 'checkpoint': checkpoint, 'evidence_ref': f'sha256:{digest}'}

    calibrations = package_calibrations(root)
    requirements = collect_requirements(pipeline, target=target, resolve_model=resolve_model,
                                        resolve_calibration=calibrations.load, here=True)
    if package_problem is not None:
        requirements.insert(0, RecipeRequirement(None, 'model', 'package-files', None, None, None, 'mismatch', None,
                                                 f'the package does not verify ({package_problem}); export it again'))
    return requirements


def package_preflight(package_dir: Path | str, *, device: Optional[str] = None) -> dict:
    """The exported package checked on the computer it is on: its files, every node's model and calibration, the
    runtime it needs and the device. The report is kept in the package's preflight folder (beside, not inside, the
    verified files) and returned."""
    from backend.engine.flowchart_engine import FlowchartPipeline
    root = Path(package_dir).resolve()
    manifest_path = root / 'manifest.json'
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    pipeline = FlowchartPipeline.model_validate(json.loads((root / 'pipeline.json').read_text(encoding='utf-8')))
    target = {'kind': 'this_computer', 'device': device or (manifest.get('runtime') or {}).get('device') or 'cpu'}
    requirements = _package_requirements(root, manifest, pipeline, target)
    value = environment()
    report = build_report(pipeline, requirements, release={'kind': 'package', 'manifest_sha256': manifest_sha},
                          target={**target, 'os': value['os'], 'architecture': value['architecture']}, environment_value=value)
    PreflightStore(root / 'preflight').save(report)
    return report


def latest_package_preflight(package_dir: Path | str, *, device: Optional[str] = None) -> Optional[dict]:
    """The newest saved package report, with whether it still speaks for this package, computer and device."""
    root = Path(package_dir).resolve()
    store = PreflightStore(root / 'preflight')
    rows = store.list()
    if not rows:
        return None
    report = store.load(rows[0]['report_id'])
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    value = environment()
    target = {'kind': 'this_computer', 'device': device or (manifest.get('runtime') or {}).get('device') or 'cpu',
              'os': value['os'], 'architecture': value['architecture']}
    release = {'kind': 'package', 'manifest_sha256': hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest()}
    from backend.engine.flowchart_engine import FlowchartPipeline
    pipeline = FlowchartPipeline.model_validate(json.loads((root / 'pipeline.json').read_text(encoding='utf-8')))
    requirements = _package_requirements(root, manifest, pipeline, target)
    reasons = staleness(report, release=release, target=target, environment_value=value, current_requirements=requirements)
    return {**report, 'stale': bool(reasons), 'stale_reasons': reasons}
