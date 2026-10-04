"""Build a portable, checksum-bound snapshot of one saved inspection flow."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping

from backend.engine.checkpoint_paths import is_job_id
from backend.engine.specialized_models import FLOW_TASKS, flow_model_task, valid_flow_job
from backend.engine.flowchart_engine import FlowchartEngine, FlowchartPipeline, ordered_linear_nodes
from backend.engine.flow_package_runtime import compare_flow_results, verify_flow_package
from backend.engine.industrial_adapters import read_image_safely_rgb
from backend.engine.edge_runtime import create_edge_profile


_RUNNER = """#!/usr/bin/env python3
from backend.engine.flow_package_runtime import main

if __name__ == '__main__':
    raise SystemExit(main())
"""

_SERVICE_RUNNER = """#!/usr/bin/env python3
from backend.engine.inspection_service import main

if __name__ == '__main__':
    raise SystemExit(main())
"""

_EDGE_RUNNER = """#!/usr/bin/env python3
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parent / 'backend' / 'engine' / 'edge_runtime.py'), run_name='__main__')
"""

_EDGE_README = """# Generic CPU Edge deployment

This package declares one target OS and CPU architecture in
`edge_deployment.json`. It includes the entire saved flow and exact model
checkpoints. The execution device is fixed to CPU. Python >=3.10,<3.14 and
compatible dependency wheels are required. Target declarations do not certify
an Edge board, latency, vendor SDK, quantization, or model quality.

Run these commands from this package directory on the declared target:

    python edge.py preflight --skip-dependencies
    python edge.py install

The first command checks target, Python, and every manifest file using only
Python's standard library. Installation creates a new `.edge_venv` environment,
installs CPU PyTorch wheels on Linux/Windows, installs requirements, then runs
the full dependency preflight. An existing environment is never overwritten.
Network access to package indexes and compatible wheels are required for setup;
inference uses the bundled checkpoints offline. If pip has no compatible wheel,
installation fails with its diagnostic; emulated CPUs and cross-install are
unsupported. Provision an approved compatible Python/runtime on your target.

On Linux/macOS use the environment's interpreter:

    .edge_venv/bin/python edge.py preflight
    .edge_venv/bin/python edge.py run -- --image /absolute/path/image.png --output result.json
    VISION_INSPECTION_TOKEN=your-secret .edge_venv/bin/python edge.py serve -- --state-dir /absolute/private/state

On Windows PowerShell use:

    .\\.edge_venv\\Scripts\\python.exe edge.py preflight
    .\\.edge_venv\\Scripts\\python.exe edge.py run -- --image C:\\inputs\\image.png --output result.json
    $env:VISION_INSPECTION_TOKEN = 'your-secret'
    .\\.edge_venv\\Scripts\\python.exe edge.py serve -- --state-dir C:\\private\\state

`run` and `serve` recheck integrity, target and usable dependency versions before
launching. They reject package/device overrides. The regular flow runner and
service also enforce the declared host and CPU device. The service's HTTP and
release approval contracts are described below. Keep the package manifest hash
or approved release policy in a separate trusted location: checksums detect
changed content against a manifest, and are not a vendor or signing certificate.

"""

_REQUIREMENTS = """numpy>=1.26
Pillow>=10.4
opencv-python-headless>=4.10
pydantic>=2.8
psutil>=6.0
scikit-learn>=1.5
torch>=2.4
torchvision>=0.19
fastapi>=0.115
uvicorn>=0.30
httpx>=0.28
"""

_README = """# Modu Vision offline flow package

This directory includes the saved graph, its exact PyTorch checkpoints, and the
same graph engine source used by the exporting app. The source dataset is not
included. The manifest binds each file to a SHA-256 checksum.

Install dependencies in a Python 3.10+ environment (choose the correct PyTorch
wheel for the target CPU/GPU first):

    python -m pip install -r requirements.txt

Verify the package before use:

    python run_flow.py --verify-only

Check this computer for everything the flow needs (each node's model and
calibration, the Python packages its model families need, the device) and keep
the report in the preflight folder; a missing item names its node and what to do,
and the exit code is 4 while anything blocks a node:

    python run_flow.py --preflight
    python run_flow.py --show-preflight

Inspect one image and save all node evidence:

    python run_flow.py --image /absolute/path/to/image.jpg --output result.json

The runner defaults to CPU for portability. Use `--device cuda` or `--device mps`
only on a compatible host; unavailable devices fail explicitly. Recheck image-by-image verdicts against the
source app before using this package for an operational decision.

Run the standalone, persistent HTTP inspection service with a private state
directory and API token (the desktop app may be closed):

    VISION_INSPECTION_TOKEN=your-secret python serve_flow.py --state-dir /absolute/private/state

POST a file path to `/v1/jobs/file` or binary image bytes to `/v1/jobs/upload`,
then poll `/v1/jobs/{job_id}`. Include `X-Vision-Token` in every request. The
service binds to 127.0.0.1 by default. It records failures as REVIEW and
reclaims interrupted rows after restart. Use `--input-root` to restrict file
path submissions to one inspected directory. `--inbox` watches a file folder;
`--camera-source` accepts an OpenCV device index, video file, or RTSP URL;
`--result-webhook-url` sends model results to a MES/device endpoint. Until the
endpoint acknowledges delivery, the operational verdict remains REVIEW. A
failed delivery can be retried via `/v1/jobs/{job_id}/retry-delivery`.

For a controlled release, export the saved flow with every model's active
approval revision ID. Save the returned release_policy JSON in a trusted file
outside this package and start the service with
`--require-approved-release --release-policy /absolute/path/to/release-policy.json`.
Startup checks the
whole package manifest and each approved checkpoint against that file.

HTTP clients for Node.js 18+ and .NET 8 are included under `clients/`. Set
`VISION_INSPECTION_TOKEN` to the service token before running a client. Uploads
and job polling use the same HTTP API; no Python process is needed on the client
machine. The .NET source must be built in a .NET 8 console project.
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _model_jobs(pipeline: FlowchartPipeline) -> dict[str, str]:
    jobs: dict[str, str] = {}
    for node in pipeline.nodes:
        task = flow_model_task(node)
        if task is None: continue
        job_id = node.data.model_job_id
        if not valid_flow_job(job_id, task) or task not in FLOW_TASKS:
            raise ValueError(f"Invalid model job or task for node {node.id}")
        if job_id in jobs and jobs[job_id] != task:
            raise ValueError(f"Model job {job_id} has conflicting tasks")
        jobs[job_id] = task
    return jobs


def _runtime_requirements(checkpoints: Mapping[str, Path]) -> str:
    """Add only the optional adapters that the packaged checkpoints require (the same rule the preflight checks)."""
    from backend.engine.flow_preflight import checkpoint_requirements
    extra: set[str] = set()
    for checkpoint in checkpoints.values():
        extra |= checkpoint_requirements(Path(checkpoint))
    return _REQUIREMENTS + "".join(f"{requirement}\n" for requirement in sorted(extra))


def build_flow_package(
    *,
    pipeline: FlowchartPipeline,
    checkpoints: Mapping[str, Path],
    output_base_dir: Path,
    package_name: str,
    approved_revisions: Mapping[str, Mapping[str, str]] | None = None,
    deployment_profile: str = "standard",
    target_os: str | None = None,
    target_arch: str | None = None,
    runtime_config: dict | None = None,
    calibrations=None,
    fixtures=None,
) -> dict[str, Any]:
    """Create a new package, never overwriting an existing release. ``calibrations`` finds the spatial calibration
    artifacts the flow's measurement nodes name (E03); each is copied into the package, and a flow that names one it
    cannot find is refused (its mm limits could never be judged)."""
    from backend.engine.runtime_configuration import runtime_options
    configured_runtime=runtime_options(runtime_config)
    if deployment_profile=='edge_cpu' and configured_runtime['device']!='cpu':
        raise ValueError('CPU Edge packages require the CPU runtime device')
    if not isinstance(package_name, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}", package_name):
        raise ValueError("Invalid package name")
    if deployment_profile not in ("standard", "edge_cpu",'edge_cuda'):
        raise ValueError("Unsupported flow deployment profile")
    if deployment_profile == "standard" and (target_os is not None or target_arch is not None):
        raise ValueError("Target OS/architecture requires the edge_cpu deployment profile")
    # Validate the declaration before reserving a directory or reading models.
    if deployment_profile=='edge_cuda' and not configured_runtime['device'].startswith('cuda'):
        configured_runtime['device']='cuda:0'
    deployment = create_edge_profile(target_os, target_arch, _REQUIREMENTS,device=configured_runtime['device']) if deployment_profile in ('edge_cpu','edge_cuda') else None
    ordered_linear_nodes(pipeline)
    from backend.engine.spatial_calibration import calibration_refs, current_scope
    resolve_calibration = calibrations or (current_scope().resolve if current_scope() else None)
    packaged_calibrations = {}
    for ref in sorted(calibration_refs(pipeline)):
        found = resolve_calibration(ref) if resolve_calibration else None
        if found is None:
            raise ValueError(f'The flow measures with calibration {ref}, which this project does not have')
        packaged_calibrations[ref] = found
    from backend.engine.fixture_flow import fixture_refs, current_fixture_resolver
    resolve_fixture = fixtures or current_fixture_resolver()
    packaged_fixtures = {}
    for ref in sorted(fixture_refs(pipeline)):
        artifact = resolve_fixture(ref) if resolve_fixture else None
        if artifact is None:
            raise ValueError(f"Missing or stale fixture reference {ref}")
        for node in pipeline.nodes:
            fixture = node.data.params.get("fixture") if node.data.node_type == "fixed_roi" else None
            if fixture and fixture["reference_ref"] == ref and fixture["reference_revision"] != artifact.reference.revision:
                raise ValueError("Fixture reference revision differs from the saved graph")
        packaged_fixtures[ref] = artifact
    jobs = _model_jobs(pipeline)
    if set(checkpoints) != set(jobs):
        raise ValueError("Checkpoint jobs do not match the saved flow")
    for job_id, checkpoint in checkpoints.items():
        from backend.engine.specialized_models import require_completed_checkpoint
        require_completed_checkpoint(checkpoint)
        path = Path(checkpoint)
        if path.is_symlink() or not path.is_file() or path.name != "best_model.pt":
            raise ValueError(f"Unsafe or missing checkpoint for {job_id}")

    requirements = _runtime_requirements(checkpoints)
    if deployment is not None:
        deployment = create_edge_profile(target_os, target_arch, requirements,device=configured_runtime['device'])

    release_revisions: list[dict[str, str]] | None = None
    if approved_revisions is not None:
        if set(approved_revisions) != set(jobs):
            raise ValueError("Every flow model needs an approved revision")
        release_revisions = []
        for job_id, task in sorted(jobs.items()):
            revision = approved_revisions[job_id]
            if (not isinstance(revision, Mapping)
                    or revision.get("job_id") != job_id or revision.get("task") != task
                    or not isinstance(revision.get("revision_id"), str)
                    or not re.fullmatch(r"[0-9a-f]{32}", revision["revision_id"])
                    or not isinstance(revision.get("checkpoint_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", revision["checkpoint_sha256"])):
                raise ValueError(f"Invalid approved revision for {job_id}")
            if _sha256(Path(checkpoints[job_id])) != revision["checkpoint_sha256"]:
                raise ValueError(f"Approved checkpoint SHA-256 does not match {job_id}")
            release_revisions.append({
                "revision_id": revision["revision_id"], "job_id": job_id, "task": task,
                "checkpoint_sha256": revision["checkpoint_sha256"],
            })

    base = Path(output_base_dir).expanduser()
    if any(path.is_symlink() for path in (base, *base.parents)):
        raise ValueError("Output directory is a symbolic link")
    base.mkdir(parents=True, exist_ok=True)
    base = base.resolve()
    source_root = Path(__file__).resolve().parents[2]
    source_engine = source_root / "backend" / "engine"
    staging = Path(tempfile.mkdtemp(prefix=f".{package_name}.", dir=base))
    try:
        (staging / "pipeline.json").write_text(
            json.dumps(pipeline.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        if packaged_calibrations:
            from backend.engine.spatial_calibration import package_calibrations
            store = package_calibrations(staging)
            for calibration in packaged_calibrations.values():
                store.save(calibration)
        if packaged_fixtures:
            from backend.engine.fixture_flow import package_fixtures
            fixture_store = package_fixtures(staging)
            for artifact in packaged_fixtures.values():
                fixture_store.copy_artifact(artifact)
        (staging / "run_flow.py").write_text(_RUNNER, encoding="utf-8")
        (staging / "serve_flow.py").write_text(_SERVICE_RUNNER, encoding="utf-8")
        (staging / "requirements.txt").write_text(requirements, encoding="utf-8")
        (staging / 'runtime_config.json').write_text(json.dumps(configured_runtime,indent=2)+'\n',encoding='utf-8')
        from backend.engine.native_sdk import copy_native_sdk
        copy_native_sdk(source_root/'native_runtime', staging/'native_runtime')
        (staging / "README_DEPLOY.md").write_text(_README, encoding="utf-8")
        if deployment is not None:
            (staging / "edge.py").write_text(_EDGE_RUNNER, encoding="utf-8")
            (staging / "edge.py").chmod(0o755)
            (staging / "edge_deployment.json").write_text(
                json.dumps(deployment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            readme=_EDGE_README if deployment['device']=='cpu' else '# Edge target runtime\n\nThe declared device and native architecture are enforced by edge.py preflight. CUDA/Jetson requires vendor PyTorch matched to the installed JetPack/CUDA release before edge.py install. The installer preserves that vendor runtime in an isolated environment. No board acceptance or latency claim is implied.\n\n'
            (staging / "README_DEPLOY.md").write_text(readme + _README, encoding="utf-8")
        for name in ("inspection-service-client.mjs", "InspectionServiceClient.cs"):
            client = source_root / "examples" / name
            if client.is_symlink() or not client.is_file():
                raise ValueError(f"Missing HTTP integration client: {name}")
            (staging / "clients").mkdir(exist_ok=True)
            shutil.copyfile(client, staging / "clients" / name, follow_symlinks=False)
        (staging / "backend").mkdir()
        (staging / "backend" / "__init__.py").write_text(
            "\"\"\"Bundled Modu Vision runtime; do not import a host backend package.\"\"\"\n",
            encoding="utf-8",
        )
        for job_id, checkpoint in sorted(checkpoints.items()):
            destination = staging / "models" / job_id / "best_model.pt"
            destination.parent.mkdir(parents=True)
            shutil.copyfile(checkpoint, destination, follow_symlinks=False)
            if _sha256(destination) != _sha256(Path(checkpoint)):
                raise ValueError(f"Checkpoint changed during copy: {job_id}")
            if release_revisions is not None and _sha256(destination) != approved_revisions[job_id]["checkpoint_sha256"]:
                raise ValueError(f"Approved checkpoint SHA-256 changed during copy: {job_id}")
            metadata = Path(checkpoint).parent / "model_meta.json"
            if metadata.exists():
                if metadata.is_symlink() or not metadata.is_file():
                    raise ValueError(f"Unsafe model metadata for {job_id}")
                parsed = json.loads(metadata.read_text(encoding="utf-8"))
                if not isinstance(parsed, dict) or parsed.get("task") != jobs[job_id]:
                    raise ValueError(f"Model metadata task does not match {job_id}")
                shutil.copyfile(metadata, destination.with_name("model_meta.json"), follow_symlinks=False)
        for source in sorted(source_engine.rglob("*.py")):
            if source.is_symlink() or any(parent.is_symlink() for parent in source.parents if source_engine in parent.parents):
                raise ValueError(f"Symlinked engine source is not allowed: {source}")
            destination = staging / "backend" / "engine" / source.relative_to(source_engine)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination, follow_symlinks=False)

        # Runtime state publication uses this dependency-pure Windows file helper,
        # including capture-policy validation and standalone service startup.
        # Bundle its package marker and helper, without the remote worker/scheduler.
        for name in ("__init__.py", "file_replace.py"):
            source = source_root / "backend" / "remote" / name
            linked = lambda: source.is_symlink() or any(
                parent.is_symlink() for parent in source.parents if source_root in parent.parents)
            if linked() or not source.is_file():
                raise ValueError(f"Unsafe or missing portable runtime source: {name}")
            digest = _sha256(source)
            destination = staging / "backend" / "remote" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination, follow_symlinks=False)
            if linked() or destination.is_symlink() or _sha256(source) != digest or _sha256(destination) != digest:
                raise ValueError(f"Portable runtime source changed during copy: {name}")

        files = []
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                files.append({
                    "path": path.relative_to(staging).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                })
        manifest = {
            "schema_version": 1,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pipeline_id": pipeline.id,
            "models": [
                {"job_id": job_id, "task": task, "checkpoint": f"models/{job_id}/best_model.pt"}
                for job_id, task in sorted(jobs.items())
            ],
            "files": files,
        }
        if packaged_fixtures:
            manifest["fixtures"] = sorted(packaged_fixtures)
        if packaged_calibrations:
            manifest["calibrations"] = sorted(packaged_calibrations)
        if release_revisions is not None:
            manifest["release"] = {"approval_revisions": release_revisions}
        if deployment is not None:
            manifest["deployment"] = deployment
        manifest['runtime']=configured_runtime
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        verify_flow_package(staging)
        target = base / package_name
        if target.exists() or target.is_symlink():
            target = base / f"{package_name}_{time.time_ns()}"
        os.rename(staging, target)
        result = {
            "status": "success", "package_path": str(target), "package_name": target.name,
            "pipeline_id": pipeline.id, "model_job_ids": sorted(jobs), "total_files": len(files) + 1,
        }
        if release_revisions is not None:
            result["release_policy"] = {
                "schema_version": 1,
                "manifest_sha256": _sha256(target / "manifest.json"),
                "approval_revisions": release_revisions,
            }
        if deployment is not None:
            result["deployment"] = deployment
        return result
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


PARITY_CONTRACT = "flow_parity_v1"
PARITY_RECEIPT = "parity_receipt.json"
MAX_PARITY_IMAGES = 64
# compare_flow_results tolerances, recorded with every report.
PARITY_TOLERANCE = {"defect_score_abs": 1e-4, "float_abs": 1e-4, "raster_abs": 1e-6}
SINGLE_IMAGE_LIMITATION = ("One CPU image only: this is not a cohort or target-device acceptance and cannot "
                           "stand in for one.")
# Frozen applications cannot run run_flow.py with their own binary; they dispatch to
# the package runtime through this early command-line option of the frozen backend.
FROZEN_PACKAGE_RUNNER_FLAG = "--flow-package-runner"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


def parity_cohort_sha256(image_sha256s) -> str:
    """Order-independent identity of the frozen parity inputs."""
    return _canonical_sha256(sorted(image_sha256s))


def _frozen_parity_inputs(images, *, minimum: int) -> list[dict[str, Any]]:
    rows = list(images or [])
    if not minimum <= len(rows) <= MAX_PARITY_IMAGES:
        raise ValueError(f"Parity cohort needs {minimum if minimum > 1 else 1} to {MAX_PARITY_IMAGES} images"
                         if minimum == 1 else f"Parity cohort needs 2 to {MAX_PARITY_IMAGES} images")
    frozen, seen = [], set()
    for index, row in enumerate(rows):
        raw = row.get("path") if isinstance(row, Mapping) else row
        image_id = row.get("image_id") if isinstance(row, Mapping) else None
        image = Path(str(raw)).expanduser().resolve()
        if str(image) in seen:
            raise ValueError(f"Parity cohort contains a duplicate image: {image.name}")
        seen.add(str(image))
        if not image.is_file():
            raise ValueError(f"Parity image is missing: {image}")
        try:
            probe = read_image_safely_rgb(image, max_dim=32)
            if probe.size == 0:
                raise ValueError("Empty image")
        except (OSError, ValueError) as exc:
            raise ValueError(f"Parity image is not readable: {image}") from exc
        frozen.append({"index": index, "path": str(image), "image_id": image_id, "sha256": _file_sha256(image)})
    return frozen


def _parity_identity(package: Path) -> dict[str, Any]:
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    files = {row["path"]: row["sha256"] for row in manifest.get("files", []) if isinstance(row, dict)}
    runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
    return {
        "manifest_sha256": _file_sha256(package / "manifest.json"),
        "graph_sha256": files.get("pipeline.json"),
        "checkpoints": {row["job_id"]: files.get(row["checkpoint"]) for row in manifest.get("models", [])
                        if isinstance(row, dict) and "job_id" in row and "checkpoint" in row},
        "package_runtime_device": runtime.get("device", "cpu"),
    }


_EXECUTABLE_DIGESTS: dict[tuple[str, int, int], str] = {}


def _executable_sha256(path: str) -> str | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (os.path.realpath(path), stat.st_size, stat.st_mtime_ns)
    if key not in _EXECUTABLE_DIGESTS:
        _EXECUTABLE_DIGESTS[key] = _file_sha256(Path(key[0]))
    return _EXECUTABLE_DIGESTS[key]


def _packaged_runtime_identity(package: Path) -> dict[str, Any]:
    """Which separate process executes the package, with the runner and runtime digests it loads."""
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    files = {row["path"]: row["sha256"] for row in manifest.get("files", []) if isinstance(row, dict)}
    frozen = bool(getattr(sys, "frozen", False))
    import platform
    return {
        "kind": "frozen_package_dispatcher" if frozen else "isolated_python_runner",
        "independent_process": True,
        "executable": sys.executable, "executable_sha256": _executable_sha256(sys.executable),
        "python_version": None if frozen else platform.python_version(),
        "dispatcher_flag": FROZEN_PACKAGE_RUNNER_FLAG if frozen else None,
        "package_runner_sha256": files.get("run_flow.py"),
        "package_runtime_sha256": files.get("backend/engine/flow_package_runtime.py"),
    }


def _packaged_runner_command(package: Path, item: Mapping[str, Any], output: Path, device: str):
    """Command for one isolated package execution: run_flow.py from source, the dispatcher when frozen."""
    runtime = _packaged_runtime_identity(package)
    arguments = ["--image", str(item["path"]), "--output", str(output), "--device", device]
    if item.get("image_id"):
        arguments.extend(("--image-id", str(item["image_id"])))
    if runtime["kind"] == "frozen_package_dispatcher":
        # The dispatcher re-verifies the package against this digest before running its own runtime.
        command = [sys.executable, FROZEN_PACKAGE_RUNNER_FLAG, "--package", str(package),
                   "--manifest-sha256", _file_sha256(package / "manifest.json"), *arguments]
    else:
        command = [sys.executable, str(package / "run_flow.py"), *arguments]
    return command, runtime


def _package_input_mismatch(package: Path, pipeline: FlowchartPipeline, checkpoints: Mapping[str, Path]) -> str | None:
    """Why the app-side graph or checkpoints are not exactly the packaged ones, if they differ."""
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    files = {row["path"]: row["sha256"] for row in manifest.get("files", []) if isinstance(row, dict)}
    packaged = FlowchartPipeline.model_validate(json.loads((package / "pipeline.json").read_text(encoding="utf-8")))
    if packaged.model_dump() != pipeline.model_dump():
        return "The flow graph differs from the packaged graph"
    models = {row["job_id"]: row for row in manifest.get("models", []) if isinstance(row, dict) and "job_id" in row}
    if set(models) != set(checkpoints):
        return "The flow checkpoints differ from the packaged models"
    tasks = {node.data.model_job_id: flow_model_task(node) for node in pipeline.nodes if flow_model_task(node) is not None}
    for job_id, row in models.items():
        source = Path(checkpoints[job_id])
        if tasks.get(job_id) != row.get("task"):
            return f"Model {job_id} has a different task than the packaged model"
        if not source.is_file() or _file_sha256(source) != files.get(row.get("checkpoint")):
            return f"The source checkpoint for {job_id} differs from the packaged checkpoint"
    return None


def _run_packaged_image(package: Path, item: Mapping[str, Any], device: str, timeout: float) -> dict[str, Any]:
    """Execute one image in the package's isolated runner, never the app's import path."""
    with tempfile.TemporaryDirectory(prefix="modu-flow-parity-") as temporary:
        output = Path(temporary) / "result.json"
        command, _ = _packaged_runner_command(package, item, output, device)
        completed = subprocess.run(
            command, cwd=temporary, env={**os.environ, "PYTHONPATH": "", "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            raise ValueError(f"Packaged flow execution failed: {completed.stderr.strip()[-1000:]}")
        return json.loads(output.read_text(encoding="utf-8"))


def _flow_evidence(result: Mapping[str, Any]) -> dict[str, Any]:
    """Decision, route, ROI and raster digests that both executions must share."""
    return {
        **({'runtime_device_identity': result['runtime_device_identity']} if 'runtime_device_identity' in result else {}),
        "final_verdict": result.get("final_verdict"),
        "routed_output_node_id": result.get("routed_output_node_id"),
        "roi_count": result.get("roi_count"),
        "branch_path": [{"node_id": step.get("node_id"), "status": step.get("status"),
                         "branch_verdict": step.get("branch_verdict"), "selected_edge_ids": step.get("selected_edge_ids")}
                        for step in result.get("execution_steps", []) or []],
        "rois": [{"roi_id": crop.get("roi_id"), "source_node_id": crop.get("source_node_id"), "bbox": crop.get("bbox"),
                  "verdict": crop.get("verdict"),
                  "mask_sha256": _canonical_sha256(crop["mask"]) if crop.get("mask") is not None else None,
                  "polygon_sha256": _canonical_sha256(crop["polygon"]) if crop.get("polygon") is not None else None}
                 for crop in result.get("crops", []) or []],
    }


def verify_flow_parity_cohort(
    *, package_dir: Path, pipeline: FlowchartPipeline, checkpoints: Mapping[str, Path], images,
    device: str, scope: str = "cohort", timeout_per_image: float = 300,
) -> dict[str, Any]:
    """Run the app engine and the isolated package on the same frozen images and device.

    Invalid input or an unavailable device raises before execution. Execution
    failures, changed inputs and mismatches are returned as a failed or mismatch
    report so the caller can persist them.
    """
    if scope not in ("cohort", "single_image"):
        raise ValueError("Parity scope must be cohort or single_image")
    from backend.engine.runtime_device import resolve_runtime_device
    resolved = resolve_runtime_device(device)
    inputs = _frozen_parity_inputs(images, minimum=2 if scope == "cohort" else 1)
    if scope == "single_image" and len(inputs) != 1:
        raise ValueError("Single-image parity takes exactly one image")
    package = Path(package_dir).resolve()
    identity = _parity_identity(package)
    packaged_runtime = _packaged_runtime_identity(package)
    import backend.engine.flowchart_engine as app_engine_module
    reference_runtime = {"kind": "in_process_app_engine", "device": str(resolved),
                         "engine_sha256": _file_sha256(Path(app_engine_module.__file__))}

    def resolve(job_id: str, task: str) -> Path | None:
        return Path(checkpoints[job_id]) if job_id in checkpoints else None

    rows: list[dict[str, Any]] = []
    status, error = "passed", None
    # The reference must run the packaged graph and checkpoint bytes, or nothing is compared.
    try:
        verify_flow_package(package)
        mismatch = _package_input_mismatch(package, pipeline, checkpoints)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        mismatch = f"Package integrity check failed: {exc}"
    if mismatch:
        status, error = "failed", mismatch
    engine = None
    for item in inputs:
        row = {"index": item["index"], "image_path": item["path"], "image_id": item["image_id"],
               "image_sha256": item["sha256"]}
        if status == "failed":
            rows.append({**row, "status": "not_run"})
            continue
        try:
            engine = engine or FlowchartEngine(device=str(resolved), checkpoint_resolver=resolve)
            from backend.engine.fixture_flow import fixture_scope, package_fixtures
            from backend.engine.spatial_calibration import calibration_scope, package_calibrations
            with calibration_scope(package_calibrations(package).load), fixture_scope(package_fixtures(package).load):  # the same artifacts the package measures with
                reference = engine.execute(pipeline=pipeline, image_path=item["path"], image_id=item["image_id"])
            reference = reference.model_dump() if hasattr(reference, "model_dump") else reference
            packaged = _run_packaged_image(package, item, device, timeout_per_image)
            if _file_sha256(Path(item["path"])) != item["sha256"]:
                raise ValueError("Parity input changed during verification")
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            status, error = "failed", str(exc)[-1000:]
            rows.append({**row, "status": "failed", "error": error})
            continue
        comparison = compare_flow_results(reference, packaged)
        rows.append({**row, "status": comparison["status"], "mismatched_fields": comparison["mismatched_fields"],
                     "reference": _flow_evidence(reference), "packaged": _flow_evidence(packaged)})
        if comparison["status"] != "passed" and status == "passed":
            status = "mismatch"
    if not mismatch:
        try:
            verify_flow_package(package)
            changed = ("package manifest" if _file_sha256(package / "manifest.json") != identity["manifest_sha256"]
                       else _package_input_mismatch(package, pipeline, checkpoints))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            changed = str(exc)
        if changed:
            earlier = f" (earlier: {error})" if error else ""
            status, error = "failed", f"Package, graph, runtime or checkpoint bytes changed during parity verification: {changed}{earlier}"[-1000:]
    completed = [row for row in rows if row["status"] in ("passed", "mismatch")]
    single = scope == "single_image"
    report = {
        "contract": PARITY_CONTRACT, "status": status, "scope": scope,
        "limitation": SINGLE_IMAGE_LIMITATION if single else None,
        "device": device, "resolved_device": str(resolved),
        "image_count": len(inputs), "completed_count": len(completed),
        "cohort_sha256": parity_cohort_sha256(item["sha256"] for item in inputs),
        **identity, "tolerance": dict(PARITY_TOLERANCE),
        "reference_runtime": reference_runtime, "packaged_runtime": packaged_runtime,
        "compared_fields": compare_flow_results({}, {})["compared_fields"],
        "mismatched_fields": [field if single else f"images[{row['index']}].{field}"
                              for row in rows for field in row.get("mismatched_fields", [])],
        "verdict_counts": {verdict: sum(row["packaged"]["final_verdict"] == verdict for row in completed)
                           for verdict in sorted({row["packaged"]["final_verdict"] for row in completed}, key=str)},
        "images": rows, "error": error,
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if single:
        packaged_row = completed[0]["packaged"] if completed else {}
        report.update(image_path=inputs[0]["path"], final_verdict=packaged_row.get("final_verdict"),
                      roi_count=packaged_row.get("roi_count"))
    return report


def verify_flow_parity(
    *, package_dir: Path, pipeline: FlowchartPipeline, checkpoints: Mapping[str, Path],
    image_path: Path, image_id: str | None = None,
) -> dict[str, Any]:
    """Limited compatibility check: one image on CPU. Raises when the package cannot run."""
    report = verify_flow_parity_cohort(package_dir=package_dir, pipeline=pipeline, checkpoints=checkpoints,
                                       images=[{"path": str(image_path), "image_id": image_id}], device="cpu",
                                       scope="single_image")
    if report["status"] == "failed":
        raise ValueError(report["error"] or "Packaged flow parity failed")
    return report


def write_parity_receipt(package_dir: Path, report: Mapping[str, Any]) -> Path:
    """Keep the parity evidence beside the package; it is bound to the manifest digest it names."""
    package = Path(package_dir).resolve()
    target = package / PARITY_RECEIPT
    if target.is_symlink():
        raise ValueError("Parity receipt cannot follow a link")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=package, prefix=".parity-receipt-", delete=False) as handle:
        json.dump({"schema_version": 1, **report}, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        staged = Path(handle.name)
    os.replace(staged, target)
    return target


def parity_receipt_status(package_dir: Path) -> dict[str, Any]:
    """Read the saved receipt and check it still names this package's manifest."""
    package = Path(package_dir).resolve()
    target = package / PARITY_RECEIPT
    if target.is_symlink() or not target.is_file():
        return {"present": False, "matches_manifest": False, "status": "not_run", "scope": None, "device": None}
    receipt = json.loads(target.read_text(encoding="utf-8"))
    return {"present": True, "matches_manifest": receipt.get("manifest_sha256") == _file_sha256(package / "manifest.json"),
            "status": receipt.get("status"), "scope": receipt.get("scope"), "device": receipt.get("device"),
            "contract": receipt.get("contract"), "cohort_sha256": receipt.get("cohort_sha256")}
