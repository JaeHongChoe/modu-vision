import json

from PIL import Image
import pytest
import torch
from fastapi import HTTPException

from backend.api import routes_evaluation
from backend.engine.dataset_loaders import DetectionDataset, inspect_dataset
from backend.engine.detection import checkpoint_detection_num_classes, foreground_class_names


def test_sparse_coco_category_ids_are_dense_model_labels(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGB", (32, 32), "white").save(images / "sample.png")
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({
        "images": [{"id": 1, "file_name": "sample.png", "width": 32, "height": 32}],
        "categories": [{"id": 7, "name": "Bow"}, {"id": 3, "name": "Scratch"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 3, "bbox": [2, 2, 6, 6]},
            {"id": 2, "image_id": 1, "category_id": 7, "bbox": [10, 10, 6, 6]},
        ],
    }), encoding="utf-8")

    dataset = DetectionDataset(images_dir=images, annotation_file=annotations)
    assert dataset.categories == {1: "Scratch", 2: "Bow"}
    assert dataset[0][1]["labels"].tolist() == [1, 2]
    summary = inspect_dataset(tmp_path, "detection")
    assert summary.classes == {"Scratch": 1, "Bow": 1}


def test_checkpoint_head_preserves_legacy_background_metadata_and_new_multiclass():
    old_state = {"roi_heads.box_predictor.cls_score.weight": torch.zeros(2, 4)}
    assert checkpoint_detection_num_classes(old_state, ["background", "defect"]) == 2
    assert foreground_class_names(["background", "defect"]) == ["defect"]

    new_state = {"roi_heads.box_predictor.cls_score.weight": torch.zeros(3, 4)}
    assert checkpoint_detection_num_classes(new_state, ["Bow", "Scratch"]) == 3
    assert foreground_class_names(["Bow", "Scratch"]) == ["Bow", "Scratch"]


def _write_split_coco(root, split, categories, category_id):
    image_dir = root / "images" / split
    image_dir.mkdir(parents=True)
    Image.new("RGB", (32, 32), "white").save(image_dir / f"{split}.png")
    annotation_file = root / f"annotations_{split}.json"
    annotation_file.write_text(json.dumps({
        "images": [{"id": 1, "file_name": f"{split}.png", "width": 32, "height": 32}],
        "categories": categories,
        "annotations": [{"id": 1, "image_id": 1, "category_id": category_id,
                         "bbox": [2, 2, 6, 6]}],
    }), encoding="utf-8")


def test_train_and_val_detection_use_shared_labels_when_val_has_only_bow(tmp_path):
    from backend.engine.trainer import _build_detection_datasets

    _write_split_coco(tmp_path, "train", [{"id": 3, "name": "Scratch"}, {"id": 7, "name": "Bow"}], 3)
    train_annotation = tmp_path / "annotations_train.json"
    train_payload = json.loads(train_annotation.read_text(encoding="utf-8"))
    train_payload["annotations"].append({"id": 2, "image_id": 1, "category_id": 7,
                                         "bbox": [12, 12, 6, 6]})
    train_annotation.write_text(json.dumps(train_payload), encoding="utf-8")
    _write_split_coco(tmp_path, "val", [{"id": 99, "name": "Bow"}], 99)

    train_ds, val_ds = _build_detection_datasets(tmp_path, None, (32, 32))
    assert train_ds.categories == {1: "Scratch", 2: "Bow"}
    assert train_ds[0][1]["labels"].tolist() == [1, 2]
    assert val_ds.categories == train_ds.categories
    assert val_ds.original_to_dense == {99: 2}
    assert val_ds[0][1]["labels"].tolist() == [2]


@pytest.mark.parametrize("meta_classes", [
    ["Scratch", "Bow"], ["background", "Scratch", "Bow"], None,
])
def test_detection_evaluation_uses_checkpoint_class_order_for_bow_only_split(tmp_path, monkeypatch, meta_classes):
    _write_split_coco(tmp_path, "val", [{"id": 99, "name": "Bow"}], 99)

    class FakeModel:
        def to(self, device):
            return self

        def load_state_dict(self, state_dict):
            pass

        def eval(self):
            return self

        def __call__(self, image):
            return [{"boxes": torch.tensor([[2., 2., 8., 8.]]),
                     "scores": torch.tensor([0.99]), "labels": torch.tensor([2])}]

    monkeypatch.setattr(routes_evaluation.torch, "load", lambda *args, **kwargs: {
        "model_state_dict": {"roi_heads.box_predictor.cls_score.weight": torch.zeros(3, 4)},
        "classes": ["Scratch", "Bow"],
    })
    monkeypatch.setattr(routes_evaluation, "create_detection_model", lambda **kwargs: FakeModel())

    meta = {"image_size": [32, 32]}
    if meta_classes is not None:
        meta["classes"] = meta_classes
    result = routes_evaluation._evaluate_detection(tmp_path / "model.pt", meta, tmp_path, torch.device("cpu"))
    prediction = result["test_predictions"][0]
    assert prediction["ground_truth"] == "Bow"
    assert prediction["predicted_class"] == "Bow"
    assert prediction["is_correct"] is True
    assert result["confusion_matrix"]["matrix"][2][2] == 1


def test_detection_evaluation_rejects_metadata_with_different_class_order(tmp_path, monkeypatch):
    _write_split_coco(tmp_path, "val", [{"id": 99, "name": "Bow"}], 99)
    monkeypatch.setattr(routes_evaluation.torch, "load", lambda *args, **kwargs: {
        "model_state_dict": {"roi_heads.box_predictor.cls_score.weight": torch.zeros(3, 4)},
        "classes": ["Scratch", "Bow"],
    })
    monkeypatch.setattr(routes_evaluation, "create_detection_model", lambda **kwargs: None)

    with pytest.raises(HTTPException, match="class order") as error:
        routes_evaluation._evaluate_detection(
            tmp_path / "model.pt", {"classes": ["Bow", "Scratch"], "image_size": [32, 32]},
            tmp_path, torch.device("cpu"),
        )
    assert error.value.status_code == 422
