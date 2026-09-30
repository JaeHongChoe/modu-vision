"""Build a portable, checksum-bound snapshot of one saved inspection flow."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
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
    """Add only the optional adapters that the packaged checkpoints require."""
    import torch
    extra: set[str] = set()
    for checkpoint in checkpoints.values():
        metadata = Path(checkpoint).with_name("model_meta.json")
        records = []
        if metadata.is_file() and not metadata.is_symlink():
            records.append(json.loads(metadata.read_text(encoding="utf-8")))
        try:
            records.append(torch.load(checkpoint, map_location="cpu", weights_only=True))
        except (OSError, ValueError, RuntimeError, EOFError, IndexError, KeyError, pickle.UnpicklingError):
            # Checksum-only legacy exports remain supported by the builder. The
            # API's scoped checkpoint resolver validates genuine weights.
            pass
        for record in records:
            if not isinstance(record, dict):
                continue
            identifiers = [str(record.get(key, "")).lower() for key in ("backbone", "model_name", "architecture")]
            if any("dinov3" in name for name in identifiers):
                extra.add("timm>=1.0.24")
            if any(name.startswith("yolo") or "detection:yolo" in name for name in identifiers):
                extra.add("ultralytics>=8.4.41")
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
) -> dict[str, Any]:
    """Create a new package, never overwriting an existing release."""
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


def verify_flow_parity(
    *, package_dir: Path, pipeline: FlowchartPipeline, checkpoints: Mapping[str, Path],
    image_path: Path, image_id: str | None = None,
) -> dict[str, Any]:
    """Compare the app CPU engine with the isolated exported runner on one image."""
    image = Path(image_path).expanduser().resolve()
    if not image.is_file():
        raise ValueError(f"Parity image is missing: {image}")
    try:
        probe = read_image_safely_rgb(image, max_dim=32)
        if probe.size == 0:
            raise ValueError("Empty image")
    except (OSError, ValueError) as exc:
        raise ValueError(f"Parity image is not readable: {image}") from exc

    def resolve(job_id: str, task: str) -> Path | None:
        return Path(checkpoints[job_id]) if job_id in checkpoints else None

    reference = FlowchartEngine(device="cpu", checkpoint_resolver=resolve).execute(
        pipeline=pipeline, image_path=str(image), image_id=image_id,
    )
    reference = reference.model_dump() if hasattr(reference, "model_dump") else reference
    package = Path(package_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="modu-flow-parity-") as temporary:
        output = Path(temporary) / "result.json"
        command = [sys.executable, str(package / "run_flow.py"), "--image", str(image), "--output", str(output)]
        if image_id:
            command.extend(("--image-id", image_id))
        completed = subprocess.run(
            command, cwd=temporary, env={**os.environ, "PYTHONPATH": ""},
            capture_output=True, text=True, timeout=300, check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            raise ValueError(f"Packaged flow execution failed: {completed.stderr.strip()[-1000:]}")
        packaged = json.loads(output.read_text(encoding="utf-8"))
    report = compare_flow_results(reference, packaged)
    report["image_path"] = str(image)
    return report
