"""Real preflights of this computer as a worker (S1-05).

A family's stage on a device becomes ``verified`` only after a preflight ran it for real on this worker: a tiny
synthetic dataset, the production trainer, evaluator, inference and export on that device, nothing downloaded. The
result is kept per worker with the runtime it ran on (``runtime_digest``: Python, OS, architecture, the app version,
the machine-learning packages, the accelerator runtime and every backend source). A preflight that passed on another
runtime does not count: an upgraded package or changed source is ``unverified`` again until its own preflight passes. A
failed preflight is kept with its reason and never counts as verified. Each stage must run on the requested device kind
(a trainer or inference that falls back to another device fails the stage), and the evidence names the small
architecture that was checked, which is not necessarily the family's default.

The preflight runs in a child process started in its own run folder (never in the installed app's folder), holding the
app-wide local compute reservation for its whole run. The child exits by itself when the app goes away (its stdin pipe
closes) or its deadline passes, and run folders left by an app that stopped mid-run are swept at the next start.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import shutil
import sys
import threading
import time
from importlib import metadata
from pathlib import Path
from typing import Any, Optional

from backend.engine.runtime_process_control import atomic_private_json

ROOT = Path(__file__).resolve().parents[2]
# The packages a stage result depends on; a different version is a different runtime.
RUNTIME_PACKAGES = ('torch', 'torchvision', 'numpy', 'pillow', 'opencv-python', 'opencv-python-headless', 'timm',
                    'ultralytics', 'onnx', 'onnxruntime', 'scikit-learn', 'scipy')
_LOCK = threading.Lock()
_SOURCE_HASHES: dict[str, tuple[int, int, bytes]] = {}


def app_version() -> Optional[str]:
    version = os.environ.get('VISION_AI_APP_VERSION')
    if version:
        return version
    try:
        return json.loads((ROOT / 'package.json').read_text(encoding='utf-8')).get('version')
    except (OSError, ValueError):
        return None


def _code_digest() -> Optional[str]:
    """sha256 over every backend source a stage may run: all of backend/ (family model code, evaluators, routes),
    without the tests; absent in a build without sources. A file's hash is reused while its size and modification time
    are unchanged, so the status poll stays cheap."""
    backend = ROOT / 'backend'
    paths = sorted(path for path in backend.rglob('*.py')
                   if path.relative_to(backend).parts[0] != 'tests' and '__pycache__' not in path.parts)
    if not paths:
        return None
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        stat = path.stat()
        cached = _SOURCE_HASHES.get(relative)
        if cached is None or cached[:2] != (stat.st_size, stat.st_mtime_ns):
            cached = (stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).digest())
            _SOURCE_HASHES[relative] = cached
        digest.update(relative.encode())
        digest.update(cached[2])
    return digest.hexdigest()


def _accelerator_runtime() -> dict[str, Any]:
    """The CUDA and cuDNN versions torch was built with, and the NVIDIA kernel driver where the OS shows it (Linux)."""
    runtime: dict[str, Any] = {'torch_cuda': None, 'cudnn': None, 'nvidia_driver': None}
    try:
        import torch
        runtime['torch_cuda'] = torch.version.cuda
        runtime['cudnn'] = torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else None
    except Exception:  # no torch (or a broken one) is its own runtime; the stages will say why they fail
        pass
    try:
        text = Path('/proc/driver/nvidia/version').read_text(encoding='utf-8', errors='replace')
        runtime['nvidia_driver'] = text.split('Kernel Module', 1)[1].split()[0]
    except (OSError, IndexError):
        pass
    return runtime


def runtime_fingerprint() -> dict[str, Any]:
    packages = {}
    for name in RUNTIME_PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    # The OS release and version: a macOS update changes the MPS operators, a kernel or driver update the CUDA stack.
    return {'python': platform.python_version(), 'implementation': sys.implementation.name, 'os': platform.system(),
            'os_release': platform.release(), 'os_version': platform.version(), 'arch': platform.machine(),
            'app_version': app_version(), 'packages': packages, 'accelerator': _accelerator_runtime(), 'code': _code_digest()}


def runtime_digest(fingerprint: Optional[dict[str, Any]] = None) -> str:
    value = runtime_fingerprint() if fingerprint is None else fingerprint
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def default_store_path() -> Path:
    user_data = Path(os.environ.get('VISION_AI_STUDIO_USER_DATA_DIR') or Path.home() / '.modu_vision')
    return user_data / 'worker_preflight.json'


def _plain(value: Any) -> Any:
    """Evidence as strict JSON: non-finite numbers become None (a NaN metric is not a measurement)."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class PreflightStore:
    """Preflight results per worker and runtime: {worker_id: {runtime_digest: {key: result}}} with key task:stage:device."""

    def __init__(self, path: Optional[Path | str] = None):
        self.path = Path(path) if path is not None else default_store_path()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        if not _valid_store(data):
            raise ValueError(f'Unreadable preflight record: {self.path}')
        return data

    def _quarantine(self) -> None:
        """Keep an unreadable record aside (never overwritten) so a new one can be started; GET reported it."""
        self.path.replace(self.path.with_name(f'{self.path.name}.unreadable-{time.strftime("%Y%m%d-%H%M%S")}-{os.getpid()}'))

    def record(self, worker_id: str, digest: str, task: str, stage: str, device: str, *, passed: bool, reason: str,
               seconds: float, evidence: Optional[dict] = None, now: Optional[float] = None) -> dict:
        result = {'passed': bool(passed), 'reason': reason, 'seconds': round(float(seconds), 2),
                  'at': time.time() if now is None else now, 'evidence': _plain(evidence or {})}
        with _LOCK:
            try:
                data = self._read()
            except ValueError:
                self._quarantine()
                data = {}
            data = data or {'protocol_version': 1, 'workers': {}}
            runtimes = data['workers'].setdefault(worker_id, {})
            # Only the current runtime's results are kept; an older runtime's are superseded, never merged.
            runtimes = data['workers'][worker_id] = {digest: runtimes.get(digest, {})}
            runtimes[digest][f'{task}:{stage}:{device}'] = result
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_private_json(self.path, data)
        return result

    def results(self, worker_id: str, digest: str) -> dict[str, dict]:
        with _LOCK:
            data = self._read()
        return dict(((data.get('workers') or {}).get(worker_id) or {}).get(digest) or {})

    def verified(self, worker_id: str, digest: str) -> dict[str, float]:
        """The keys whose last preflight on this runtime passed, with the time it passed."""
        return {key: row['at'] for key, row in self.results(worker_id, digest).items() if row.get('passed') is True}


def _valid_store(data: Any) -> bool:
    """{protocol_version: 1, workers: {worker: {digest: {key: {passed: bool, at: number, ...}}}}} and nothing else."""
    if not isinstance(data, dict) or data.get('protocol_version') != 1 or not isinstance(data.get('workers'), dict):
        return False
    for runtimes in data['workers'].values():
        if not isinstance(runtimes, dict):
            return False
        for rows in runtimes.values():
            if not isinstance(rows, dict):
                return False
            for row in rows.values():
                if (not isinstance(row, dict) or not isinstance(row.get('passed'), bool) or isinstance(row.get('at'), bool)
                        or not isinstance(row.get('at'), (int, float))):
                    return False
    return True


# --- Stage runners -------------------------------------------------------------------------------------------------
# Tiny, download-free settings: no pretrained weights, 32-64 px images, one epoch. A preflight shows the stage runs on
# this worker and device; it says nothing about model quality.
PREFLIGHT_TASKS = {
    'classification': {'pretrained': False, 'backbone': 'resnet18', 'epochs': 1, 'image_size': 32, 'batch_size': 2},
    'anomaly': {'pretrained': False, 'image_size': 32, 'anomaly_method': 'padim', 'batch_size': 2},
    'segmentation': {'pretrained': False, 'model_name': 'unet', 'epochs': 1, 'image_size': 32, 'batch_size': 2},
    'detection': {'pretrained': False, 'backbone': 'yolo26n', 'epochs': 1, 'image_size': 64, 'batch_size': 2},
}
PREFLIGHT_STAGES = ('train', 'evaluate', 'infer', 'export')
# Model steps of an exported flow that must actually inspect the image (an inspection always answers one result; a
# detector may find nothing, which is still a real answer).
_MODEL_STEP_TYPES = {'inspection': 1, 'detection_crop': 0}


def preflight_architecture(task: str) -> str:
    """The architecture a preflight of ``task`` trains (recorded with every stage; not the family's default model)."""
    config = PREFLIGHT_TASKS[task]
    return str(config.get('backbone') or config.get('model_name') or config.get('anomaly_method'))


def _same_kind(used: str, requested: str) -> bool:
    return bool(used) and used.split(':')[0] == requested.split(':')[0]


def _image(path: Path, shade: int, mark: Optional[tuple] = None, size: int = 48) -> None:
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (size, size), (shade, shade, shade))
    pen = ImageDraw.Draw(image)
    pen.rectangle((4 + shade % 7, 4, 20 + shade % 7, 20), fill=(min(255, shade + 40),) * 3)
    if mark:
        pen.ellipse(mark, fill=(20, 20, 20))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def build_dataset(task: str, root: Path) -> tuple[Path, Path]:
    """A tiny synthetic dataset for ``task`` and one image to infer on; every class is in every split."""
    if task == 'classification':
        for split, count in (('train', 4), ('val', 2), ('test', 2)):
            for label, mark in (('OK', None), ('NG', (26, 26, 40, 40))):
                for index in range(count):
                    _image(root / split / label / f'{label}_{split}_{index}.png', 150 + 9 * index + (3 if split == 'val' else 6 if split == 'test' else 0), mark)
        return root, root / 'test' / 'NG' / 'NG_test_0.png'
    if task == 'anomaly':
        for folder, count, mark in (('train/good', 6, None), ('test/good', 2, None), ('test/defect', 2, (26, 26, 40, 40))):
            for index in range(count):
                _image(root / folder / f'{folder.replace("/", "_")}_{index}.png', 160 + 11 * index + len(folder), mark)
        return root, root / 'test' / 'defect' / 'test_defect_0.png'
    from backend.engine.synthetic_generator import generate_synthetic_dataset
    generate_synthetic_dataset(output_dir=root, num_samples=12, task=task, image_size=(64, 64), seed=7)
    dataset = root / task
    image = sorted((dataset / 'images' / 'val').glob('*'))[0]
    return dataset, image


def _evaluate(task: str, model: Path, dataset: Path, device: str) -> dict:
    import torch
    from backend.api import routes_evaluation
    meta = json.loads((model.parent / 'model_meta.json').read_text(encoding='utf-8'))
    evaluator = {'classification': routes_evaluation._evaluate_classification, 'anomaly': routes_evaluation._evaluate_anomaly,
                 'segmentation': routes_evaluation._evaluate_segmentation, 'detection': routes_evaluation._evaluate_detection}[task]
    metrics = evaluator(model, meta, dataset, torch.device(device))
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError('the evaluator returned no metrics')
    scalars = metrics.get('metrics') if isinstance(metrics.get('metrics'), dict) else metrics
    summary = {key: value for key, value in scalars.items() if isinstance(value, (int, float, str, bool)) or value is None}
    if not summary:
        raise ValueError('the evaluator returned no metric values')
    return dict(list(summary.items())[:12])


def _export(task: str, model: Path, image: Path, workdir: Path, device: str) -> dict:
    from backend.engine import flow_package, flow_package_runtime
    from backend.engine.flowchart_engine import get_fixed_roi_flowchart, get_single_detection_flowchart, get_single_segmentation_flowchart
    job_id = model.parent.name
    if task == 'detection':
        pipeline = get_single_detection_flowchart(job_id)
    elif task == 'segmentation':
        pipeline = get_single_segmentation_flowchart(job_id)
    else:
        pipeline = get_fixed_roi_flowchart(inspection_task=task, job_id=job_id)
        if task == 'anomaly':
            from backend.engine.score_contract import checkpoint_score_spec
            spec = checkpoint_score_spec(model)
            for node in pipeline.nodes:
                if node.data.node_type == 'inspection':
                    node.data.score_spec, node.data.threshold = spec, spec['threshold']
    built = flow_package.build_flow_package(pipeline=pipeline, checkpoints={job_id: model}, output_base_dir=workdir / 'exports',
                                            package_name=f'preflight_{task}')
    package = Path(built['package_path'])
    result = flow_package_runtime.run_flow_package(package, image, device=device)
    verdict = result.get('final_verdict')
    # REVIEW is the engine's answer for an image it could not inspect (a refused node, an empty ROI): not a pass.
    if verdict not in ('OK', 'NG'):
        raise ValueError(f'the exported package did not inspect the image (verdict {verdict!r}: {result.get("rejection_reason")})')
    steps = {row.get('node_id'): row for row in result.get('execution_steps') or [] if isinstance(row, dict)}
    checked = {}
    for node in pipeline.nodes:
        least = _MODEL_STEP_TYPES.get(node.data.node_type)
        if least is None:
            continue
        step = steps.get(node.id) or {}
        # warning_untrained (a checkpoint the package could not resolve), skipped or review_required are not passes.
        if step.get('status') not in ('passed', 'flagged_ng') or int(step.get('output_count') or 0) < least:
            raise ValueError(f'the exported model step {node.id} ended {step.get("status")!r} with {step.get("output_count")} '
                             f'results: {step.get("skip_reason")}')
        checked[node.id] = step['status']
    if not checked:
        raise ValueError('the exported package ran no model step')
    used = str((result.get('execution_resources') or {}).get('device') or '')
    if not _same_kind(used, device):
        raise ValueError(f'the exported package ran on {used or "an unrecorded device"}, not on {device}')
    return {'package': package.name, 'verdict': verdict, 'model_steps': checked, 'device': used}


def run_stages(task: str, device: str, workdir: Path, stages: tuple[str, ...] = PREFLIGHT_STAGES) -> dict[str, dict]:
    """Run ``stages`` of ``task`` for real on ``device`` in ``workdir``; each later stage uses the trained model. A
    device this process cannot use fails every stage with that reason, and a stage that ran elsewhere fails."""
    diagnostics = bool(os.environ.get('MODU_PREFLIGHT_TRACE_SECONDS'))
    if diagnostics:
        print('PREFLIGHT_PREPARE_IMPORTS', flush=True)
    import torch
    from backend.engine.runtime_device import resolve_runtime_device
    architecture = preflight_architecture(task)
    try:
        resolve_runtime_device(device)
    except Exception as exc:  # strict here: the trainer and inference would fall back to another device
        reason = f'{device} is not usable in the preflight process: {exc}'[:500]
        return {stage: {'passed': False, 'reason': reason, 'seconds': 0.0, 'evidence': {'architecture': architecture}}
                for stage in PREFLIGHT_STAGES if stage in stages}
    from backend.engine.device import get_device
    from backend.engine.trainer import UnifiedAutoMLTrainer, infer
    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    results: dict[str, dict] = {}
    model: Optional[Path] = None
    dataset, image = build_dataset(task, workdir / 'data')
    for stage in PREFLIGHT_STAGES:
        if stage not in stages and not (stage == 'train' and stages):
            continue
        started = time.monotonic()
        if diagnostics:
            print(f'PREFLIGHT_STAGE_START {stage}', flush=True)
        if stage != 'train' and model is None:
            results[stage] = {'passed': False, 'reason': 'not run: training did not produce a model', 'seconds': 0.0}
            continue
        try:
            if stage == 'train':
                output = workdir / 'models' / 'job_preflight'
                output.mkdir(parents=True)
                trained = UnifiedAutoMLTrainer(task=task, dataset_path=str(dataset), output_dir=str(output), preset='fast',
                                               device=device, config_overrides=dict(PREFLIGHT_TASKS[task])).train(job_id=output.name)
                if trained.get('status') != 'completed' or not (output / 'best_model.pt').is_file():
                    raise ValueError(f"training ended {trained.get('status')!r}: {trained.get('error')}")
                used = str(json.loads((output / 'model_meta.json').read_text(encoding='utf-8')).get('device') or '')
                if not _same_kind(used, device):
                    raise ValueError(f'training ran on {used or "an unrecorded device"}, not on {device}')
                model, evidence = output / 'best_model.pt', {'best_metric': trained.get('best_metric'), 'device': used}
            elif stage == 'evaluate':
                evidence = {**_evaluate(task, model, dataset, device), 'device': device}
            elif stage == 'infer':
                used = str(get_device(device))  # the device inference itself picks for this request
                if not _same_kind(used, device):
                    raise ValueError(f'inference would run on {used}, not on {device}')
                answer = infer(task, model, image, device=device)
                evidence = {'confidence': float(answer.confidence_score), 'latency_ms': float(answer.latency_ms), 'device': used}
            else:
                evidence = _export(task, model, image, workdir, device)
            results[stage] = {'passed': True, 'reason': 'passed', 'seconds': time.monotonic() - started,
                              'evidence': {**evidence, 'architecture': architecture}}
        except Exception as exc:  # the stage's own failure is the result; later stages say why they did not run
            results[stage] = {'passed': False, 'reason': f'{type(exc).__name__}: {exc}'[:500], 'seconds': time.monotonic() - started,
                              'evidence': {'architecture': architecture}}
        if diagnostics:
            print(f'PREFLIGHT_STAGE_END {stage} {"passed" if results[stage]["passed"] else "failed"}', flush=True)
    return {stage: row for stage, row in results.items() if stage in stages}


# --- The preflight child and its owner -----------------------------------------------------------------------------
RESULT_FILE = 'preflight-result.json'
PREFLIGHT_TIMEOUT_SECONDS = 900.0
# A run folder older than this belongs to no live preflight (the parent kills its child at the timeout and the child
# exits at its own deadline), so a start may sweep it.
STALE_RUN_SECONDS = PREFLIGHT_TIMEOUT_SECONDS + 300.0
_CHILDREN: set = set()  # this app's running preflight children, stopped through their handles at shutdown
_STOPPED_BY_APP: set = set()  # children stopped because the app quit: their run is interrupted, not a failed runtime
_CHILDREN_LOCK = threading.Lock()


class PreflightRefused(RuntimeError):
    """A preflight that is not started (unsupported, not installed, another one running); the message says why."""


def plan(task: str, device: str, stages: Optional[list[str]] = None, *, capabilities: Any = None) -> tuple[str, ...]:
    """The stages to preflight, refused with the support decision's own reason; nothing falls back to another device."""
    from backend.contracts.capabilities import decide, probe_local
    if task not in PREFLIGHT_TASKS:
        raise PreflightRefused(f'{task} 모델군의 사전 점검은 아직 제공되지 않습니다.')
    wanted = tuple(dict.fromkeys(stages or PREFLIGHT_STAGES))
    unknown = [stage for stage in wanted if stage not in PREFLIGHT_STAGES]
    if unknown:
        raise PreflightRefused(f'사전 점검할 수 없는 단계입니다: {", ".join(unknown)}')
    capabilities = capabilities or probe_local()
    for stage in wanted:
        decision = decide(capabilities, task, stage, device)
        if decision.state in ('unsupported', 'not_installed'):
            raise PreflightRefused(decision.reason)
    return wanted


def default_runs_root(store: Optional[PreflightStore] = None) -> Path:
    return (store or PreflightStore()).path.parent / 'worker_preflight_runs'


def sweep_stale_runs(root: Optional[Path] = None, *, older_than: float = STALE_RUN_SECONDS, now: Optional[float] = None) -> list[str]:
    """Remove run folders an app left behind when it stopped mid-run (only folders older than any live run can be).
    A folder that cannot be removed now (a file still open on Windows) is left for the next sweep."""
    root = Path(root) if root is not None else default_runs_root()
    removed = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return removed
    moment = time.time() if now is None else now
    for entry in entries:
        try:
            if entry.is_dir() and not entry.is_symlink() and moment - entry.stat().st_mtime > older_than:
                shutil.rmtree(entry)
                removed.append(entry.name)
        except OSError:
            continue
    return removed


def stop_running_preflights(wait: float = 5.0) -> int:
    """Kill this app's running preflight children through their own handles (app shutdown); the number stopped."""
    with _CHILDREN_LOCK:
        children = list(_CHILDREN)
        _STOPPED_BY_APP.update(children)
    for child in children:
        if child.poll() is None:
            child.kill()
    for child in children:
        try:
            child.wait(timeout=wait)
        except Exception:  # a child that does not exit in time is reported by the caller's log, never signalled again
            pass
    return len(children)


def _child_environment() -> dict[str, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    if not getattr(sys, 'frozen', False):
        # The child starts in its run folder, so the source tree is found through the import path, not the cwd.
        env['PYTHONPATH'] = os.pathsep.join(part for part in (str(ROOT), env.get('PYTHONPATH', '')) if part)
    return env


def run_preflight(task: str, device: str, stages: tuple[str, ...], *, store: Optional[PreflightStore] = None,
                  worker_id: str = 'local', timeout: Optional[float] = None, workdir_root: Optional[Path] = None,
                  _writer_ticket=None) -> dict:
    """Run one preflight in a child process this app owns and record each stage's result for this runtime."""
    from backend.engine.application_launch_handshake import create_preflight_writer_ticket, _preflight_ticket_transport
    from backend.engine.runtime_deadline import _writer_transport
    own_ticket = _writer_ticket is None
    ticket = create_preflight_writer_ticket(device=device) if own_ticket else _writer_ticket
    if own_ticket: ticket.claim()
    try:
        # No new OPEN admission for an already dispatched original ticket.
        with _preflight_ticket_transport(ticket, device=device) as transport:
            entered = False
            body_error = None
            try:
                with _writer_transport(transport) as inherited:
                    entered = True
                    try:
                        return _run_preflight_admitted(task, device, stages, store=store, worker_id=worker_id,
                            timeout=timeout, workdir_root=workdir_root, ticket=ticket, inherited=inherited)
                    except BaseException as exc:
                        body_error = exc
                        raise
            except BaseException as exc:
                # The nested transport owns another duplicate. Failed entry or
                # an exception replacing the body's result/error cannot prove
                # that its cleanup close returned.
                # Keep original custody without probing or retrying that fd.
                if not entered or exc is not body_error: ticket.retain_unconfirmed()
                raise
    finally:
        if own_ticket and ticket._phase != 'unresolved': ticket.finish()


def _run_preflight_admitted(task, device, stages, *, store, worker_id, timeout, workdir_root, ticket, inherited):
    import subprocess
    import tempfile
    from backend.engine.process_isolation import session_isolation
    store = store or PreflightStore()
    root = Path(workdir_root) if workdir_root is not None else default_runs_root(store)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()  # the exporter refuses a linked output folder (e.g. a data folder under macOS's /var -> /private/var)
    sweep_stale_runs(root)
    limit = PREFLIGHT_TIMEOUT_SECONDS if timeout is None else timeout
    started = time.monotonic()
    # A cleanup error (a checkpoint still held open on Windows) never loses the recorded results; the sweep retries.
    with tempfile.TemporaryDirectory(prefix=f'{task}-', dir=root, ignore_cleanup_errors=True) as folder:
        run = Path(folder)
        command = [sys.executable, '-m', 'backend.engine.worker_preflight', '--task', task, '--device', device,
                   '--stages', ','.join(stages), '--workdir', folder, '--exit-with-parent', '--deadline', f'{limit + 30:.0f}']
        timed_out = False
        with open(run / 'preflight.log', 'w', encoding='utf-8') as log:
            # cwd is the run folder: a stage that writes relative folders writes them here, never into the installed app.
            child = subprocess.Popen(command, cwd=folder, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                     env=_child_environment(), **({'close_fds': True, 'pass_fds': inherited} if inherited else {}),
                                     **session_isolation())
            with _CHILDREN_LOCK:
                _CHILDREN.add(child)
            try:
                child.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                timed_out = True
                ticket.unconfirmed()
                child.kill()  # only this preflight's own child, through its handle
                child.wait()
            finally:
                with _CHILDREN_LOCK:
                    _CHILDREN.discard(child)
                    stopped_by_app = child in _STOPPED_BY_APP
                    _STOPPED_BY_APP.discard(child)
                child.stdin.close()
        if stopped_by_app:
            ticket.unconfirmed()
            # The app quit mid-run: nothing is recorded, so earlier results of this runtime keep their state.
            return {'task': task, 'device': device, 'runtime_digest': runtime_digest(), 'architecture': preflight_architecture(task),
                    'results': {}, 'interrupted': '앱이 종료되어 사전 점검이 중단되었습니다. 결과는 기록하지 않았습니다.'}
        try:
            answer = json.loads((run / RESULT_FILE).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            answer = None
        if answer is None or timed_out:
            ticket.unconfirmed()
            try:
                tail = (run / 'preflight.log').read_text(encoding='utf-8', errors='replace')[-400:].strip()
            except OSError as exc:  # the run folder itself was removed meanwhile: still a recorded failure
                tail = f'the run log is gone ({type(exc).__name__})'
            why = (f'timed out after {limit:.0f} s' if timed_out
                   else f'the preflight process ended (exit {child.returncode}) without a result: {tail}')
            answer = {'runtime_digest': runtime_digest(),
                      'results': {stage: {'passed': False, 'reason': why, 'seconds': time.monotonic() - started} for stage in stages}}
        if child.returncode != 0: ticket.unconfirmed()
        recorded = {}
        for stage, row in answer['results'].items():
            recorded[stage] = store.record(worker_id, answer['runtime_digest'], task, stage, device, passed=row['passed'] is True,
                                           reason=str(row.get('reason') or ''), seconds=float(row.get('seconds') or 0),
                                           evidence=row.get('evidence'))
    return {'task': task, 'device': device, 'runtime_digest': answer['runtime_digest'], 'architecture': preflight_architecture(task),
            'results': recorded}


def _exit_when_parent_goes(deadline: Optional[float]) -> None:
    """In the child: exit at once when the app's end of stdin closes (the app quit, crashed or was killed) or at the
    deadline, so no preflight outlives its app or runs on without a limit."""
    # First native SciPy imports can hang while a Windows pipe read is blocked,
    # even on a duplicated descriptor. Keep the owned descriptor, but do not
    # leave a pending read during native initialization. Windows pipe support
    # in Python >= 3.12 returns BlockingIOError when the open pipe is empty.
    watch_fd = os.dup(0)
    try:
        if platform.system() == 'Windows':
            os.set_blocking(watch_fd, False)
    except BaseException:
        os.close(watch_fd)
        raise

    def watch_stdin():
        try:
            while True:
                try:
                    if not os.read(watch_fd, 65536):
                        break
                except BlockingIOError:
                    time.sleep(0.05)
        except OSError:
            pass
        finally:
            try:
                os.close(watch_fd)
            except OSError:
                pass
        os._exit(75)
    try:
        threading.Thread(target=watch_stdin, name='preflight-parent-watch', daemon=True).start()
    except BaseException:
        os.close(watch_fd)
        raise
    if deadline:
        timer = threading.Timer(deadline, lambda: os._exit(76))
        timer.daemon = True
        timer.start()


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description='Run one worker preflight (the app starts this; it is not a user command).')
    parser.add_argument('--task', required=True, choices=sorted(PREFLIGHT_TASKS))
    parser.add_argument('--device', required=True)
    parser.add_argument('--stages', required=True)
    parser.add_argument('--workdir', required=True, type=Path)
    parser.add_argument('--exit-with-parent', action='store_true', help='exit when stdin closes (the app owns this process)')
    parser.add_argument('--deadline', type=float, default=None, help='seconds after which this process exits')
    options = parser.parse_args(argv)
    if options.exit_with_parent:
        _exit_when_parent_goes(options.deadline)
    stages = tuple(item for item in options.stages.split(',') if item)
    # Explicit CI diagnostics are enabled before the expensive stage imports.
    # Normal application runs keep their existing logging and time budgets.
    trace_seconds = os.environ.get('MODU_PREFLIGHT_TRACE_SECONDS')
    if trace_seconds:
        import faulthandler
        interval = float(trace_seconds)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError('Preflight trace interval must be a finite positive number')
        faulthandler.dump_traceback_later(interval, repeat=True)
        print(f'PREFLIGHT_CHILD_START task={options.task} device={options.device}', flush=True)
    try:
        results = run_stages(options.task, options.device, options.workdir, stages)
    finally:
        if trace_seconds:
            faulthandler.cancel_dump_traceback_later()
    # The result goes to a file in the run folder, not the shared stdout, which library output could interleave with.
    target = options.workdir / RESULT_FILE
    staging = target.with_suffix('.partial')
    staging.write_text(json.dumps({'runtime_digest': runtime_digest(), 'results': _plain(results)}), encoding='utf-8')
    os.replace(staging, target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
