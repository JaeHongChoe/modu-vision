"""Exercise stop/status using a small, read-only sample of real LabelMe pairs."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from pathlib import Path

from backend.api import routes_training


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    pairs = [(label, label.with_suffix(".jpg")) for label in sorted(source.glob("*.json"))
             if label.with_suffix(".jpg").is_file()][:8]
    if len(pairs) != 8:
        raise SystemExit("At least 8 paired LabelMe JSON/JPG files are required")
    files = [path for pair in pairs for path in pair]
    before = {str(path): sha256(path) for path in files}

    with tempfile.TemporaryDirectory(prefix="modu-cancel-realdata-") as root_dir:
        root = Path(root_dir)
        input_dir = root / "input"
        input_dir.mkdir()
        for label, image in pairs:
            (input_dir / label.name).symlink_to(label)
            (input_dir / image.name).symlink_to(image)

        started = routes_training.start_training(routes_training.TrainingStartRequest(
            task="segmentation", dataset_path=str(input_dir), output_dir=str(root / "models"),
            device="cpu", config_overrides={"epochs": 1, "batch_size": 2, "image_size": 64},
        ))
        job_id = started["job_id"]
        request_start = time.monotonic()
        stopped = routes_training.stop_training(routes_training.TrainingStopRequest(job_id=job_id))
        stop_seconds = time.monotonic() - request_start
        pending = routes_training.get_training_status(job_id=job_id)
        record = routes_training.training_job_manager.get_job(job_id)
        record.thread.join(timeout=30)
        finished = routes_training.get_training_status(job_id=job_id)
        unchanged = before == {str(path): sha256(path) for path in files}

        result = {
            "pairs": len(pairs),
            "stop_response": stopped["status"],
            "stop_seconds": round(stop_seconds, 3),
            "pending_status": pending["status"],
            "finished_status": finished["status"],
            "worker_alive": record.thread.is_alive(),
            "source_hash_unchanged": unchanged,
        }
        print(json.dumps(result, ensure_ascii=False))
        if not (stopped["status"] == "stopping" and finished["status"] == "aborted"
                and not record.thread.is_alive() and unchanged):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
