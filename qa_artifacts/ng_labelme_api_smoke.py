"""One-epoch API smoke test on a read-only subset of paired NG_labelme files.

This checks integration, not model quality or an OK/NG acceptance criterion.
All generated data and receipts stay under --work-dir.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.engine import exporter
from backend.main import create_app


def digest(path: Path) -> str:
    hash_value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hash_value.update(block)
    return hash_value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=8)
    args = parser.parse_args()

    source = args.source.resolve()
    work = args.work_dir.resolve()
    subset = work / "source_subset"
    subset.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    pairs = []
    for annotation in sorted(source.glob("*.json")):
        image = annotation.with_suffix(".jpg")
        if image.is_file():
            pairs.append((image, annotation))
        if len(pairs) >= args.pairs:
            break
    if len(pairs) < 3:
        raise RuntimeError("At least three paired LabelMe examples are required")

    original_hashes = {str(path): digest(path) for pair in pairs for path in pair}
    for pair in pairs:
        for path in pair:
            destination = subset / path.name
            if destination.exists() or destination.is_symlink():
                destination.unlink()
            destination.symlink_to(path)

    # Checkpoint resolution intentionally trusts only ./models under the active
    # project root. Run the isolated QA project from its own work directory.
    os.chdir(work)
    exporter.EXPORTS_DIR = work / "exports"
    app = create_app(project_dir=str(work / "project"))
    with TestClient(app, headers={"X-Vision-Token": app.state.api_token}) as client:
        imported = client.post("/api/dataset/import", json={
            "folder_path": str(subset), "task": "segmentation", "validate_images": False,
        })
        imported.raise_for_status()
        split = client.post("/api/dataset/split", json={
            "folder_path": str(subset), "train_ratio": 0.75, "val_ratio": 0.25, "test_ratio": 0,
        })
        split.raise_for_status()

        started = client.post("/api/training/start", json={
            "task": "segmentation", "preset": "fast", "dataset_path": str(subset),
            "output_dir": str(work / "models"), "device": "mps",
            "config_overrides": {"epochs": 1, "batch_size": 2, "image_size": 256},
        })
        if not started.is_success:
            raise RuntimeError(f"Training start failed ({started.status_code}): {started.text}")
        job_id = started.json()["job_id"]
        deadline = time.monotonic() + 240
        while True:
            status = client.get("/api/training/status", params={"job_id": job_id})
            status.raise_for_status()
            state = status.json()
            if state["status"] in ("completed", "failed", "aborted"):
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"Training did not finish: {state}")
            time.sleep(1)
        if state["status"] != "completed":
            raise RuntimeError(f"Training failed: {state}")

        evaluated = client.get("/api/evaluation/results", params={"job_id": job_id})
        evaluated.raise_for_status()
        exported = client.post("/api/export/runtime", json={
            "job_id": job_id, "export_format": "torchscript", "resolution": 256,
            "package_name": f"{job_id}_qa",
        })
        exported.raise_for_status()

    export_data = exported.json()
    package = Path(export_data["package_path"])
    prepared_val = sorted((Path(started.json()["output_dir"]) / "dataset" / "images" / "val").glob("*.png"))
    if not prepared_val:
        raise RuntimeError("Prepared validation set is empty")
    standalone = subprocess.run(
        [sys.executable, str(package / "infer.py"), "--image", str(prepared_val[0])],
        text=True, capture_output=True, timeout=120,
    )
    if standalone.returncode:
        raise RuntimeError(f"Standalone inference failed: {standalone.stderr}")
    standalone_result = json.loads(standalone.stdout)
    evaluation_prediction = next(
        prediction for prediction in evaluated.json()["test_predictions"]
        if Path(prediction["file_path"]).resolve() == prepared_val[0].resolve()
    )
    score_gap = abs(float(evaluation_prediction["defect_score"]) - standalone_result["defect_score"])
    if score_gap > 0.002:
        raise RuntimeError(f"Evaluation/export defect scores differ by {score_gap:.4f}")

    final_hashes = {str(path): digest(path) for pair in pairs for path in pair}
    if final_hashes != original_hashes:
        raise RuntimeError("Source data changed during smoke test")
    receipt = {
        "source": str(source), "subset_pairs": len(pairs), "source_unchanged": True,
        "source_sha256": original_hashes, "import": imported.json(), "split": split.json(),
        "training": state, "evaluation": evaluated.json(), "export": export_data,
        "standalone": standalone_result, "score_gap": score_gap,
    }
    receipt_path = work / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False, default=str))
    print(json.dumps({"receipt": str(receipt_path), "job_id": job_id,
                      "train_status": state["status"], "prediction_count": len(evaluated.json()["test_predictions"]),
                      "package_path": str(package)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
