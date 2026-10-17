"""The owned browser checkpoint and its evaluator must share one input contract."""
import importlib.util
import json
from pathlib import Path
import sys

from PIL import Image
import torch


def test_owned_comparison_fixture_keeps_declared_checkpoint_input_in_real_evaluator(tmp_path, monkeypatch):
    # Six tiny synthetic originals; no training, pretrained download, GPU,
    # inference delay or timing assertion. The real ResNet forward still runs.
    source, models = tmp_path / "source", tmp_path / "models"
    for split in ("train", "val", "test"):
        for label, value in (("OK", 220), ("NG", 40)):
            folder = source / split / label
            folder.mkdir(parents=True)
            Image.new("RGB", (64, 64), (value, 20, 30)).save(folder / (label + ".png"))
    script = Path(__file__).resolve().parents[2] / "scripts/e2e/fixtures/comparison_models.py"
    spec = importlib.util.spec_from_file_location("owned_comparison_fixture_contract", script)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    monkeypatch.setattr(sys, "argv", [str(script), str(source), str(models)])
    previous_threads = torch.get_num_threads()
    hook_handles = []
    try:
        fixture.main()
        job = models / "job_fixture_candidate"
        checkpoint = torch.load(job / "best_model.pt", map_location="cpu", weights_only=False)
        metadata = json.loads((job / "model_meta.json").read_text())
        from backend.api import routes_evaluation
        original_factory = routes_evaluation.create_classification_model
        input_shapes = []

        def observe_original_model(*args, **kwargs):
            model = original_factory(*args, **kwargs)
            hook_handles.append(model.register_forward_pre_hook(
                lambda _model, inputs: input_shapes.append(tuple(inputs[0].shape))))
            return model

        monkeypatch.setattr(routes_evaluation, "create_classification_model", observe_original_model)
        result = routes_evaluation._evaluate_classification(
            job / "best_model.pt", metadata, source, torch.device("cpu"),
        )
        assert len(result["test_predictions"]) == 2
        assert input_shapes == [(1, 3, *checkpoint["image_size"])] * 2
        assert metadata == {key: checkpoint[key] for key in ("task", "backbone", "classes", "image_size")}
    finally:
        try:
            for handle in hook_handles:
                handle.remove()
        finally:
            torch.set_num_threads(previous_threads)
