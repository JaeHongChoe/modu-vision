"""
Tier 1 & Tier 2 Automated Test Suite for Feature F03: Common Industrial Dataset Formats & Loaders.
Covers PyTorch Dataset classes across all 4 tasks (Classification, Detection, Segmentation, Anomaly),
image health validation (corrupt headers, 0-byte files), inverted box sanitization,
mask dimension alignment, single-sample stratify fallback, and Unicode filename resilience.
"""

import json
import os
import shutil
import tempfile
import cv2
import numpy as np
import pytest
import torch
from PIL import Image


@pytest.fixture
def synthetic_data_dir(synthetic_module, temp_dir):
    """Generates a real synthetic dataset to test loaders on."""
    summary = synthetic_module.generate_synthetic_dataset(
        output_dir=temp_dir,
        num_samples=20,
        image_size=(128, 128),
        seed=123,
    )
    return temp_dir


# ============================================================================
# Tier 1: Primary Feature Coverage Tests (>=5 tests)
# ============================================================================

def test_classification_loader(loader_module, synthetic_data_dir):
    """Tier 1: Verifies ClassificationDataset loads folder structure and normalizes pixels."""
    train_dir = os.path.join(synthetic_data_dir, "classification", "train")
    ds = loader_module.ClassificationDataset(root_dir=train_dir)

    assert len(ds) > 0
    img_t, label_idx = ds[0]

    assert isinstance(img_t, torch.Tensor)
    assert img_t.shape == (3, 128, 128)
    assert img_t.dtype == torch.float32
    assert 0.0 <= img_t.min() <= img_t.max() <= 1.0
    assert isinstance(label_idx, int)
    assert 0 <= label_idx < len(ds.classes)


def test_detection_loader_coco(loader_module, synthetic_data_dir):
    """Tier 1: Verifies DetectionDataset parses COCO JSON annotations and bounding boxes."""
    img_dir = os.path.join(synthetic_data_dir, "detection", "images", "train")
    anno_file = os.path.join(synthetic_data_dir, "detection", "annotations_train.json")
    ds = loader_module.DetectionDataset(images_dir=img_dir, annotation_file=anno_file)

    assert len(ds) > 0
    img_t, target = ds[0]

    assert img_t.shape == (3, 128, 128)
    assert "boxes" in target
    assert "labels" in target
    assert "image_id" in target
    assert "area" in target
    assert "iscrowd" in target

    assert target["boxes"].ndim == 2
    assert target["boxes"].shape[-1] == 4
    assert target["labels"].dtype == torch.int64


def test_segmentation_loader(loader_module, synthetic_data_dir):
    """Tier 1: Verifies SegmentationDataset pairs images with single-channel integer masks."""
    img_dir = os.path.join(synthetic_data_dir, "segmentation", "images", "train")
    mask_dir = os.path.join(synthetic_data_dir, "segmentation", "masks", "train")
    ds = loader_module.SegmentationDataset(images_dir=img_dir, masks_dir=mask_dir)

    assert len(ds) > 0
    img_t, mask_t = ds[0]

    assert img_t.shape == (3, 128, 128)
    assert mask_t.shape == (128, 128)
    assert mask_t.dtype == torch.int64
    assert mask_t.min() >= 0


def test_anomaly_loader_strict_normal_train(loader_module, synthetic_data_dir):
    """Tier 1: Verifies AnomalyDataset train split contains exclusively normal samples."""
    anom_root = os.path.join(synthetic_data_dir, "anomaly")
    train_ds = loader_module.AnomalyDataset(root_dir=anom_root, split="train")

    assert len(train_ds) > 0
    for i in range(len(train_ds)):
        img_t, label, mask_t = train_ds[i]
        assert label == 0
        assert torch.all(mask_t == 0)


def test_anomaly_loader_flaw_detection_in_test(loader_module, synthetic_data_dir):
    """Tier 1: Small validation/test sets remain disjoint and retain all labels together."""
    anom_root = os.path.join(synthetic_data_dir, "anomaly")
    val_ds = loader_module.AnomalyDataset(root_dir=anom_root, split="val")
    test_ds = loader_module.AnomalyDataset(root_dir=anom_root, split="test")

    labels = [sample[1] for sample in val_ds.samples + test_ds.samples]
    assert 0 in labels
    assert 1 in labels
    assert {p.resolve() for p, _, _ in val_ds.samples}.isdisjoint(
        {p.resolve() for p, _, _ in test_ds.samples}
    )


def test_anomaly_explicit_normal_folder_has_disjoint_train_val_test(loader_module, tmp_path):
    normal_dir = tmp_path / "OK"
    anomaly_dir = tmp_path / "NG"
    normal_dir.mkdir()
    anomaly_dir.mkdir()
    for index in range(10):
        Image.new("RGB", (8, 8), "white").save(normal_dir / f"ok_{index:02d}.png")
    for index in range(4):
        Image.new("RGB", (8, 8), "red").save(anomaly_dir / f"ng_{index:02d}.png")

    datasets = {
        split: loader_module.AnomalyDataset(
            normal_dir=normal_dir, anomaly_dir=anomaly_dir, split=split
        ) for split in ("train", "val", "test")
    }
    paths = {split: {p.resolve() for p, _, _ in ds.samples} for split, ds in datasets.items()}

    assert [len(datasets[split]) for split in ("train", "val", "test")] == [8, 3, 3]
    assert paths["train"].isdisjoint(paths["val"])
    assert paths["train"].isdisjoint(paths["test"])
    assert paths["val"].isdisjoint(paths["test"])
    assert {label for _, label, _ in datasets["val"].samples} == {0, 1}
    assert {label for _, label, _ in datasets["test"].samples} == {0, 1}
    assert paths["train"] | paths["val"] | paths["test"] == {
        p.resolve() for p in list(normal_dir.glob("*.png")) + list(anomaly_dir.glob("*.png"))
    }


@pytest.mark.parametrize("count,expected", [(1, (1, 0, 0)), (2, (1, 1, 0)), (3, (1, 1, 1))])
def test_anomaly_small_normal_only_folder_never_reuses_images(loader_module, tmp_path, count, expected):
    normal_dir = tmp_path / "OK"
    normal_dir.mkdir()
    for index in range(count):
        Image.new("RGB", (8, 8), "white").save(normal_dir / f"ok_{index}.png")

    datasets = [loader_module.AnomalyDataset(root_dir=tmp_path, split=split) for split in ("train", "val", "test")]
    paths = [{p.resolve() for p, _, _ in ds.samples} for ds in datasets]

    assert tuple(map(len, datasets)) == expected
    assert len(set.union(*paths)) == count
    assert sum(map(len, paths)) == count


def test_anomaly_reference_test_source_is_partitioned_between_val_and_test(loader_module, tmp_path):
    train_dir = tmp_path / "train" / "good"
    test_ok = tmp_path / "test" / "good"
    test_ng = tmp_path / "test" / "scratch"
    for directory in (train_dir, test_ok, test_ng):
        directory.mkdir(parents=True)
    for index in range(4):
        Image.new("RGB", (8, 8), "white").save(train_dir / f"train_{index}.png")
        Image.new("RGB", (8, 8), "white").save(test_ok / f"ok_{index}.png")
        Image.new("RGB", (8, 8), "red").save(test_ng / f"ng_{index}.png")

    datasets = {split: loader_module.AnomalyDataset(root_dir=tmp_path, split=split)
                for split in ("train", "val", "test")}
    paths = {split: {p.resolve() for p, _, _ in ds.samples} for split, ds in datasets.items()}

    assert [len(datasets[split]) for split in ("train", "val", "test")] == [4, 4, 4]
    assert paths["train"].isdisjoint(paths["val"])
    assert paths["train"].isdisjoint(paths["test"])
    assert paths["val"].isdisjoint(paths["test"])
    assert {label for _, label, _ in datasets["val"].samples} == {0, 1}
    assert {label for _, label, _ in datasets["test"].samples} == {0, 1}

    summary = loader_module.inspect_dataset(tmp_path, task="anomaly")
    assert summary.total_images == 12
    assert summary.split_counts == {"train": 4, "val": 4, "test": 4}
    assert summary.classes == {"good": 8, "defect": 4}

    for split in ("train", "val", "test"):
        flexible = loader_module.FlexibleAnomalyDataset(root_dir=tmp_path, split=split)
        assert {p.resolve() for p, _, _ in flexible.samples} == paths[split]

    industrial = loader_module.inspect_industrial_dataset(tmp_path, task="anomaly")
    assert industrial["total_images"] == 12
    assert industrial["split"] == {"train": 4, "val": 4, "test": 4}


def test_anomaly_train_good_only_still_creates_disjoint_validation_and_test(loader_module, tmp_path):
    good_dir = tmp_path / "train" / "good"
    good_dir.mkdir(parents=True)
    for index in range(10):
        Image.new("RGB", (8, 8), "white").save(good_dir / f"ok_{index:02d}.png")

    datasets = {split: loader_module.AnomalyDataset(root_dir=tmp_path, split=split)
                for split in ("train", "val", "test")}
    paths = [{p.resolve() for p, _, _ in datasets[split].samples}
             for split in ("train", "val", "test")]

    assert tuple(len(datasets[split]) for split in ("train", "val", "test")) == (8, 1, 1)
    assert len(set.union(*paths)) == 10
    assert sum(map(len, paths)) == 10


def test_stratified_dataset_splitter(loader_module):
    """Tier 1: Verifies split_dataset partitions data according to split ratios."""
    items = [
        {"id": f"ok_{i}", "label": "OK"} for i in range(20)
    ] + [
        {"id": f"bridge_{i}", "label": "solder_bridge"} for i in range(20)
    ]

    splits = loader_module.split_dataset(items, train_ratio=0.8, val_ratio=0.2, seed=42)
    assert len(splits["train"]) == 32
    assert len(splits["val"]) == 8


# ============================================================================
# Tier 2: Boundary & Corner Cases Tests (>=5 tests)
# ============================================================================

def test_image_health_validator_corrupt_files(loader_module, temp_dir):
    """Tier 2: Validates zero-byte, corrupt headers, and valid image detection."""
    # 1. Non-existent file
    res_none = loader_module.validate_image_file(os.path.join(temp_dir, "ghost.png"))
    assert res_none.valid is False
    assert res_none.error_code == "FILE_NOT_FOUND"

    # 2. 0-byte file
    zero_p = os.path.join(temp_dir, "zero.png")
    open(zero_p, "wb").close()
    res_zero = loader_module.validate_image_file(zero_p)
    assert res_zero.valid is False
    assert res_zero.error_code == "ZERO_BYTE"

    # 3. Corrupt header garbage file
    garb_p = os.path.join(temp_dir, "garb.png")
    with open(garb_p, "wb") as f:
        f.write(b"NOT_A_VALID_IMAGE_DATA_STREAM")
    res_garb = loader_module.validate_image_file(garb_p)
    assert res_garb.valid is False
    assert res_garb.error_code == "CORRUPT_HEADER"

    # 4. Valid image
    val_p = os.path.join(temp_dir, "valid.png")
    Image.new("RGB", (64, 64), color="green").save(val_p)
    res_val = loader_module.validate_image_file(val_p)
    assert res_val.valid is True
    assert res_val.dimensions == (64, 64)


def test_detection_inverted_bbox_sanitization(loader_module, temp_dir):
    """
    Tier 2: CRITICAL CORNER CASE.
    Bounding boxes with negative width/height (inverted coordinates) must be sanitized.
    """
    img_dir = os.path.join(temp_dir, "det_imgs")
    os.makedirs(img_dir)
    img_p = os.path.join(img_dir, "sample.png")
    Image.new("RGB", (100, 100), color="white").save(img_p)

    anno_data = {
        "images": [{"id": 1, "file_name": "sample.png", "width": 100, "height": 100}],
        "annotations": [
            # Inverted box: width is negative (-30)
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [70, 20, -30, 40], "area": 0, "iscrowd": 0},
            # Valid box: [10, 10, 20, 20]
            {"id": 2, "image_id": 1, "category_id": 1, "bbox": [10, 10, 20, 20], "area": 400, "iscrowd": 0},
        ],
        "categories": [{"id": 1, "name": "scratch", "supercategory": "defect"}],
    }
    anno_file = os.path.join(temp_dir, "anno.json")
    with open(anno_file, "w") as f:
        json.dump(anno_data, f)

    ds = loader_module.DetectionDataset(images_dir=img_dir, annotation_file=anno_file)
    _, target = ds[0]

    # Inverted box was dropped; exactly 1 box retained
    assert target["boxes"].shape[0] == 1
    assert torch.equal(target["boxes"][0], torch.tensor([10.0, 10.0, 30.0, 30.0]))


def test_segmentation_mask_dimension_mismatch_resizing(loader_module, temp_dir):
    """
    Tier 2: CRITICAL CORNER CASE.
    If mask dimensions differ from image dimensions, loader automatically resizes mask.
    """
    img_dir = os.path.join(temp_dir, "seg_img")
    mask_dir = os.path.join(temp_dir, "seg_mask")
    os.makedirs(img_dir)
    os.makedirs(mask_dir)

    # Image is 128x128
    Image.new("RGB", (128, 128), color="red").save(os.path.join(img_dir, "001.png"))
    # Mask is 256x256
    mask_arr = np.zeros((256, 256), dtype=np.uint8)
    mask_arr[50:100, 50:100] = 2
    cv2.imwrite(os.path.join(mask_dir, "001.png"), mask_arr)

    ds = loader_module.SegmentationDataset(images_dir=img_dir, masks_dir=mask_dir)
    img_t, mask_t = ds[0]

    assert img_t.shape == (3, 128, 128)
    assert mask_t.shape == (128, 128)
    assert mask_t.max() == 2


def test_stratified_splitter_single_sample_fallback(loader_module):
    """
    Tier 2: CRITICAL CORNER CASE.
    Classes with only 1 sample fail standard stratify. Must gracefully fallback.
    """
    items = [
        {"id": f"ok_{i}", "label": "OK"} for i in range(12)
    ] + [
        {"id": "defect_lonely", "label": "NG_RareAnomaly"}  # Only 1 sample
    ]

    splits = loader_module.split_dataset(items, train_ratio=0.8, val_ratio=0.2, seed=99)
    assert len(splits["train"]) > 0
    assert len(splits["val"]) > 0
    assert len(splits["train"]) + len(splits["val"]) == 13


def test_anomaly_loader_strict_normal_enforcement(loader_module, temp_dir):
    """
    Tier 2: CRITICAL SECURITY INVARIANT.
    If a defect folder is placed inside anomaly/train, loader must raise ValueError.
    """
    anom_dir = os.path.join(temp_dir, "bad_anomaly")
    train_good = os.path.join(anom_dir, "train", "good")
    train_defect = os.path.join(anom_dir, "train", "scratch")
    os.makedirs(train_good)
    os.makedirs(train_defect)

    Image.new("RGB", (32, 32), "green").save(os.path.join(train_good, "01.png"))
    Image.new("RGB", (32, 32), "red").save(os.path.join(train_defect, "01.png"))

    with pytest.raises(ValueError, match="Anomaly training split must contain exclusively normal"):
        loader_module.AnomalyDataset(root_dir=anom_dir, split="train")


def test_unicode_and_spaces_in_filepaths(loader_module, temp_dir):
    """Tier 2 (Adversarial): Filepaths containing Korean characters and spaces load cleanly."""
    cls_dir = os.path.join(temp_dir, "cls_unicode")
    sub_dir = os.path.join(cls_dir, "정상_OK")
    os.makedirs(sub_dir)

    special_name = "PCB_기판 01 #샘플.png"
    Image.new("RGB", (64, 64), color="blue").save(os.path.join(sub_dir, special_name))

    ds = loader_module.ClassificationDataset(root_dir=cls_dir)
    assert len(ds) == 1
    img_t, lbl = ds[0]
    assert img_t.shape == (3, 64, 64)
    assert lbl == 0


def test_bounding_box_coordinates(loader_module):
    """Tier 1: Tests BoundingBox coordinate conversions (VOC, COCO, normalized)."""
    bbox = loader_module.BoundingBox(xmin=100.0, ymin=50.0, xmax=200.0, ymax=150.0, category_id=1, category_name="scratch")
    assert bbox.to_voc() == [100.0, 50.0, 200.0, 150.0]
    assert bbox.to_coco() == [100.0, 50.0, 100.0, 100.0]
    norm = bbox.to_normalized(640, 480)
    assert pytest.approx(norm[0], 1e-4) == 100.0 / 640.0
    assert pytest.approx(norm[1], 1e-4) == 50.0 / 480.0
    assert pytest.approx(norm[2], 1e-4) == 200.0 / 640.0
    assert pytest.approx(norm[3], 1e-4) == 150.0 / 480.0


def test_bounding_box_degenerate_sanitization(loader_module):
    """Tier 2: Zero width/height box rejected; inverted coordinates auto-swapped."""
    bad_box = loader_module.BoundingBox(xmin=50.0, ymin=50.0, xmax=50.0, ymax=100.0, category_id=1, category_name="scratch")
    assert bad_box.sanitize(640, 480) is None

    inverted = loader_module.BoundingBox(xmin=100.0, ymin=100.0, xmax=50.0, ymax=50.0, category_id=1, category_name="scratch")
    san = inverted.sanitize(640, 480)
    assert san is not None
    assert san.xmin == 50.0 and san.xmax == 100.0


def test_detection_voc_xml_parser(loader_module, temp_dir):
    """Tier 1: Verifies PascalVocParser parses XML format."""
    xml_content = """<annotation>
        <filename>sample.png</filename>
        <size><width>640</width><height>480</height></size>
        <object>
            <name>bridge</name>
            <bndbox><xmin>10</xmin><ymin>20</ymin><xmax>100</xmax><ymax>120</ymax></bndbox>
        </object>
    </annotation>"""
    xml_file = os.path.join(temp_dir, "sample.xml")
    with open(xml_file, "w") as f:
        f.write(xml_content)

    parsed = loader_module.PascalVocParser.parse_file(xml_file)
    assert parsed["width"] == 640
    assert parsed["height"] == 480
    assert len(parsed["objects"]) == 1
    assert parsed["objects"][0]["name"] == "bridge"
    assert parsed["objects"][0]["bbox"] == [10.0, 20.0, 100.0, 120.0]


def test_detection_collate_fn(loader_module):
    """Tier 1: Tests detection_collate_fn variable box collation."""
    img1 = torch.rand(3, 100, 100)
    target1 = {"boxes": torch.tensor([[10., 10., 50., 50.]]), "labels": torch.tensor([1])}
    meta1 = {"image_path": "img1.png"}

    img2 = torch.rand(3, 100, 100)
    target2 = {"boxes": torch.tensor([[0., 0., 10., 10.], [20., 20., 30., 30.]]), "labels": torch.tensor([1, 2])}
    meta2 = {"image_path": "img2.png"}

    batch = [(img1, target1, meta1), (img2, target2, meta2)]
    imgs, targets, metas = loader_module.detection_collate_fn(batch)

    assert len(imgs) == 2
    assert len(targets) == 2
    assert len(metas) == 2
    assert targets[0]["boxes"].shape == (1, 4)
    assert targets[1]["boxes"].shape == (2, 4)


def test_inspect_dataset_contract(loader_module, temp_dir):
    """Tier 1: Verifies inspect_dataset summary matches schema."""
    ok_dir = os.path.join(temp_dir, "OK")
    ng_dir = os.path.join(temp_dir, "NG_scratch")
    os.makedirs(ok_dir)
    os.makedirs(ng_dir)

    for i in range(5):
        Image.new("RGB", (32, 32)).save(os.path.join(ok_dir, f"{i}.png"))
        Image.new("RGB", (32, 32)).save(os.path.join(ng_dir, f"{i}.png"))

    summary = loader_module.inspect_dataset(temp_dir, task="classification")
    assert summary.task == "classification"
    assert summary.total_images == 10
    assert summary.classes["OK"] == 5
    assert summary.classes["NG_scratch"] == 5
    assert "train" in summary.split_counts
    assert "val" in summary.split_counts
