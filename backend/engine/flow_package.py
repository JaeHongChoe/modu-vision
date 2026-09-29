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
from backend.engine.flowchart_engine import FlowchartEngine, FlowchartPipeline, ordered_linear_nodes
from backend.engine.flow_package_runtime import compare_flow_results, verify_flow_package
from backend.engine.industrial_adapters import read_image_safely_rgb


_RUNNER = """#!/usr/bin/env python3
from backend.engine.flow_package_runtime import main

if __name__ == '__main__':
    raise SystemExit(main())
"""

_REQUIREMENTS = """numpy>=1.26
Pillow>=10.4
opencv-python-headless>=4.10
pydantic>=2.8
psutil>=6.0
scikit-learn>=1.5
torch>=2.4
torchvision>=0.19
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

The runner uses CPU for portability. Recheck image-by-image verdicts against the
source app before using this package for an operational decision.
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
        if node.data.node_type not in ("detection_crop", "inspection"):
            continue
        job_id = node.data.model_job_id
        task = "detection" if node.data.node_type == "detection_crop" else node.data.task
        if not is_job_id(job_id) or task not in ("detection", "classification", "segmentation", "anomaly"):
            raise ValueError(f"Invalid model job or task for node {node.id}")
        if job_id in jobs and jobs[job_id] != task:
            raise ValueError(f"Model job {job_id} has conflicting tasks")
        jobs[job_id] = task
    return jobs


def build_flow_package(
    *,
    pipeline: FlowchartPipeline,
    checkpoints: Mapping[str, Path],
    output_base_dir: Path,
    package_name: str,
) -> dict[str, Any]:
    """Create a new package, never overwriting an existing release."""
    if not isinstance(package_name, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}", package_name):
        raise ValueError("Invalid package name")
    ordered_linear_nodes(pipeline)
    jobs = _model_jobs(pipeline)
    if set(checkpoints) != set(jobs):
        raise ValueError("Checkpoint jobs do not match the saved flow")
    for job_id, checkpoint in checkpoints.items():
        path = Path(checkpoint)
        if path.is_symlink() or not path.is_file() or path.name != "best_model.pt":
            raise ValueError(f"Unsafe or missing checkpoint for {job_id}")

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
        (staging / "requirements.txt").write_text(_REQUIREMENTS, encoding="utf-8")
        (staging / "README_DEPLOY.md").write_text(_README, encoding="utf-8")
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
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        verify_flow_package(staging)
        target = base / package_name
        if target.exists() or target.is_symlink():
            target = base / f"{package_name}_{time.time_ns()}"
        os.rename(staging, target)
        return {
            "status": "success", "package_path": str(target), "package_name": target.name,
            "pipeline_id": pipeline.id, "model_job_ids": sorted(jobs), "total_files": len(files) + 1,
        }
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
