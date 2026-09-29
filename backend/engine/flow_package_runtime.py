"""Run an exported inspection flow with the exact bundled graph engine."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from backend.engine.flowchart_engine import FlowchartEngine, FlowchartPipeline, ordered_linear_nodes
from backend.engine.industrial_adapters import read_image_safely_rgb


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _package_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("Invalid package file path")
    part = PurePosixPath(relative)
    if part.is_absolute() or any(segment in ("", ".", "..") for segment in relative.split("/")):
        raise ValueError("Invalid package file path")
    path = root.joinpath(*part.parts)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents):
        raise ValueError(f"Package file is a symbolic link: {relative}")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Package file is missing or outside the package: {relative}")
    return path


def verify_flow_package(package_dir: Path) -> tuple[FlowchartPipeline, dict[str, Path]]:
    """Reject changed files and model links before loading a PyTorch checkpoint."""
    root = Path(package_dir).resolve()
    manifest_path = _package_file(root, "manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Unsupported flow package manifest")
    files = manifest.get("files")
    models = manifest.get("models")
    if not isinstance(files, list) or not isinstance(models, list) or not files or not models:
        raise ValueError("Incomplete flow package manifest")
    seen: set[str] = set()
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or row["path"] in seen:
            raise ValueError("Duplicate or invalid package file entry")
        seen.add(row["path"])
        path = _package_file(root, row["path"])
        if path.stat().st_size != row.get("size") or _sha256(path) != row.get("sha256"):
            raise ValueError(f"Package checksum mismatch: {row['path']}")
    if "pipeline.json" not in seen or "run_flow.py" not in seen:
        raise ValueError("Flow package has no graph or runner")
    pipeline = FlowchartPipeline.model_validate(json.loads((root / "pipeline.json").read_text(encoding="utf-8")))
    ordered_linear_nodes(pipeline)
    expected = {
        (node.data.model_job_id, "detection" if node.data.node_type == "detection_crop" else node.data.task)
        for node in pipeline.nodes if node.data.node_type in ("detection_crop", "inspection")
    }
    checkpoints: dict[str, Path] = {}
    found: set[tuple[str, str]] = set()
    for row in models:
        if not isinstance(row, dict):
            raise ValueError("Invalid model entry")
        job_id, task, relative = row.get("job_id"), row.get("task"), row.get("checkpoint")
        if not isinstance(job_id, str) or not isinstance(task, str) or not isinstance(relative, str):
            raise ValueError("Invalid model entry")
        if relative != f"models/{job_id}/best_model.pt" or relative not in seen:
            raise ValueError("Model checkpoint is missing from the package manifest")
        if job_id in checkpoints or (job_id, task) in found:
            raise ValueError("Duplicate model job in the package manifest")
        checkpoints[job_id] = _package_file(root, relative)
        found.add((job_id, task))
    if found != expected:
        raise ValueError("Packaged model jobs do not match the saved graph")
    return pipeline, checkpoints


def run_flow_package(package_dir: Path, image_path: Path, image_id: str | None = None) -> dict[str, Any]:
    pipeline, checkpoints = verify_flow_package(package_dir)
    image = Path(image_path).expanduser().resolve()
    if not image.is_file():
        raise FileNotFoundError(f"Inspection image not found: {image}")
    try:
        probe = read_image_safely_rgb(image, max_dim=32)
        if probe.size == 0:
            raise ValueError("Empty image")
    except (OSError, ValueError) as exc:
        raise ValueError(f"Not a readable image: {image}") from exc

    def resolve(job_id: str, task: str) -> Path | None:
        return checkpoints.get(job_id)

    engine = FlowchartEngine(device="cpu", checkpoint_resolver=resolve)
    result = engine.execute(pipeline=pipeline, image_path=image, image_id=image_id)
    return result.model_dump() if hasattr(result, "model_dump") else result


def compare_flow_results(reference: dict[str, Any], packaged: dict[str, Any]) -> dict[str, Any]:
    """Compare decisions and spatial evidence, excluding machine-dependent timing."""
    fields = (
        "final_verdict", "roi_count", "defective_roi_count", "routed_output_node_id",
        "rejection_reason", "inspected_image_size",
    )
    mismatches = [field for field in fields if reference.get(field) != packaged.get(field)]
    ref_steps = [(step.get("node_id"), step.get("status")) for step in reference.get("execution_steps", [])]
    pkg_steps = [(step.get("node_id"), step.get("status")) for step in packaged.get("execution_steps", [])]
    if ref_steps != pkg_steps:
        mismatches.append("execution_steps")

    ref_crops, pkg_crops = reference.get("crops", []), packaged.get("crops", [])
    if len(ref_crops) != len(pkg_crops):
        mismatches.append("crops.length")
    else:
        for index, (left, right) in enumerate(zip(ref_crops, pkg_crops)):
            for field in ("roi_id", "label", "bbox", "verdict", "defect_area_px"):
                if left.get(field) != right.get(field):
                    mismatches.append(f"crops[{index}].{field}")
            try:
                score_gap = abs(float(left.get("defect_score")) - float(right.get("defect_score")))
            except (TypeError, ValueError):
                score_gap = float("inf")
            if score_gap > 1e-4:
                mismatches.append(f"crops[{index}].defect_score")
    return {
        "status": "passed" if not mismatches else "mismatch",
        "mismatched_fields": mismatches,
        "compared_fields": [*fields, "execution_steps", "crops"],
        "final_verdict": packaged.get("final_verdict"),
        "roi_count": packaged.get("roi_count"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a saved Modu Vision inspection flow offline")
    parser.add_argument("--verify-only", action="store_true", help="Verify graph, code, and model checksums")
    parser.add_argument("--image", type=Path, help="Image to inspect")
    parser.add_argument("--image-id", help="Optional source image ID")
    parser.add_argument("--output", type=Path, help="Write the complete JSON result here")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        if args.verify_only:
            pipeline, checkpoints = verify_flow_package(root)
            result = {"status": "verified", "pipeline_id": pipeline.id, "model_job_ids": sorted(checkpoints)}
        else:
            if args.image is None:
                parser.error("--image is required unless --verify-only is set")
            result = run_flow_package(root, args.image, args.image_id)
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(payload + "\n", encoding="utf-8")
        else:
            print(payload)
        return 0
    except (ValueError, FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Flow package error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
