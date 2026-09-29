"""Patch classification must preserve Stage 5 source-image coordinates."""

from pathlib import Path
import json

import numpy as np
import torch
from torch import nn

from backend.engine.flowchart_engine import FlowchartEngine, get_fixed_roi_flowchart, ordered_linear_nodes
from backend.engine.flow_package import build_flow_package
from backend.engine.flow_package_runtime import verify_flow_package


class _RedMeanClassifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.scale = nn.Parameter(torch.tensor(10.0))

    def forward(self, images):
        red = images[:, 0].mean(dim=(1, 2))
        return torch.stack(((0.5 - red) * self.scale, (red - 0.5) * self.scale), dim=1)


def test_saved_patch_model_runs_on_fixed_roi_with_original_coordinates(tmp_path: Path, monkeypatch):
    from backend.engine import patch_classification

    monkeypatch.setattr(patch_classification, "create_classification_model", lambda **_: _RedMeanClassifier())
    model_dir = tmp_path / "job_patch"
    model_dir.mkdir()
    checkpoint = model_dir / "best_model.pt"
    torch.save({"model_state_dict": _RedMeanClassifier().state_dict()}, checkpoint)
    (model_dir / "model_meta.json").write_text(json.dumps({
        "task": "patch_classification", "classes": ["OK", "NG"], "normal_class": "OK",
        "backbone": "resnet18", "patch_size": 16, "stride": 16, "image_size": [16, 16],
    }))
    pipeline = get_fixed_roi_flowchart(inspection_task="patch_classification", job_id="job_patch")
    fixed = next(node for node in pipeline.nodes if node.data.node_type == "fixed_roi")
    fixed.data.params["roi_bbox"] = [8, 8, 40, 40]
    assert ordered_linear_nodes(pipeline)

    image = np.zeros((48, 48, 3), dtype=np.uint8)
    image[8:40, 8:40, 0] = 240
    engine = FlowchartEngine(device="cpu", checkpoint_resolver=lambda job_id, task: checkpoint)
    result = engine.execute(pipeline=pipeline, image=image)

    assert result["final_verdict"] == "NG"
    assert result["roi_count"] == 4
    assert {tuple(crop["bbox"]) for crop in result["crops"]} == {
        (8, 8, 24, 24), (24, 8, 40, 24), (8, 24, 24, 40), (24, 24, 40, 40),
    }


def test_patch_model_is_included_in_verified_whole_flow_package(tmp_path: Path):
    model_dir = tmp_path / "job_patch"
    model_dir.mkdir()
    checkpoint = model_dir / "best_model.pt"
    checkpoint.write_bytes(b"checkpoint fixture")
    (model_dir / "model_meta.json").write_text('{"task":"patch_classification"}')
    pipeline = get_fixed_roi_flowchart(inspection_task="patch_classification", job_id="job_patch")
    result = build_flow_package(
        pipeline=pipeline, checkpoints={"job_patch": checkpoint},
        output_base_dir=tmp_path / "exports", package_name="patch_flow",
    )
    loaded, checkpoints = verify_flow_package(Path(result["package_path"]))
    assert loaded == pipeline
    assert checkpoints["job_patch"].read_bytes() == b"checkpoint fixture"
