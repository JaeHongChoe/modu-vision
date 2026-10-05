"""Create deterministic, untrained checkpoints for owned comparison UI fixtures.

The app executes real CPU inference on these weights. These receipts describe a
controlled fixture, and are never evidence of model training or quality.
"""
import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.api import routes_dataset
from backend.engine.classification.model import create_classification_model
from backend.engine.dataset_fingerprint import fingerprint_dataset


def main():
    source, models = (Path(arg).resolve() for arg in sys.argv[1:])
    fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    torch.set_num_threads(1)
    for name, preferred in (("job_fixture_incumbent", 0), ("job_fixture_candidate", 1)):
        job = models / name
        (job / "dataset").mkdir(parents=True, exist_ok=False)
        model = create_classification_model("resnet18", 2, pretrained=False)
        with torch.no_grad():
            for parameter in model.parameters():
                parameter.zero_()
            model.fc.bias[preferred] = 10
        torch.save({"task": "classification", "backbone": "resnet18",
                    "classes": ["OK", "NG"], "image_size": [64, 64],
                    "model_state_dict": model.state_dict()}, job / "best_model.pt")
        (job / "model_meta.json").write_text(json.dumps({"task": "classification"}), encoding="utf-8")
        (job / "job_receipt.json").write_text(json.dumps({
            "status": "completed", "task": "classification",
            "source_dataset_path": str(source), "dataset_fingerprint": fingerprint,
            "dataset_path": str(job / "dataset"), "test_fixture": "untrained_deterministic",
        }), encoding="utf-8")
    print(json.dumps({"checkpoint_kind": "untrained_deterministic", "dataset_fingerprint": fingerprint}))


if __name__ == "__main__":
    main()
