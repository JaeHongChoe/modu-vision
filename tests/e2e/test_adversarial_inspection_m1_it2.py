"""
tests/e2e/test_adversarial_inspection_m1_it2.py

Adversarial Verification Suite for Milestone M1 Iteration 2 (Challenger 2).
Focus:
  1. Synthetic dataset generation and inspect_dataset() contract across all 4 tasks:
     - Classification (pre-split and flat)
     - Detection (pre-split and flat)
     - Segmentation (pre-split and flat)
     - Anomaly Detection (pre-split and val fallback)
     Verifying total_images > 0 and accurate class breakdowns.
  2. AnomalyDataset with custom folder naming (train/normal AND train/pass).
  3. Adversarial boundary conditions, invalid tasks, corrupted files, and security invariants.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict

import cv2
import numpy as np
import pytest
import torch
from PIL import Image

from backend.engine.dataset_loaders import (
    AnomalyDataset,
    ClassificationDataset,
    DetectionDataset,
    SegmentationDataset,
    create_dataloader,
    inspect_dataset,
    validate_image_file,
)
from backend.engine.synthetic_generator import (
    generate_synthetic_dataset,
    MODALITY_DEFECTS,
)


# ============================================================================
# 1. Empirical Verification: All 4 Tasks with inspect_dataset()
# ============================================================================

@pytest.mark.parametrize("modality", ["pcb", "wafer", "metal", None])
def test_inspect_dataset_all_four_tasks(modality):
    """
    Generate synthetic dataset with task='all' (or specific modality)
    and verify inspect_dataset() across all 4 vision tasks:
      - classification
      - detection
      - segmentation
      - anomaly
    Verifies total_images > 0, accurate class breakdowns, and correct split counts.
    """
    num_samples = 30
    split_ratio = 0.8
    expected_train = int(round(num_samples * split_ratio))
    expected_val = num_samples - expected_train

    with tempfile.TemporaryDirectory() as tmp_dir:
        gen_result = generate_synthetic_dataset(
            output_dir=tmp_dir,
            num_samples=num_samples,
            modality=modality,
            task="all",
            split_ratio=split_ratio,
            seed=101,
        )
        assert gen_result["status"] == "success"
        assert gen_result["total_images"] == num_samples

        base = Path(tmp_dir)

        # -------------------------------------------------------------
        # 1.1 Classification
        # -------------------------------------------------------------
        cls_path = base / "classification"
        assert cls_path.is_dir(), f"Classification dir missing at {cls_path}"
        cls_summary = inspect_dataset(cls_path, task="classification")

        assert cls_summary.task == "classification"
        assert cls_summary.total_images == num_samples
        assert cls_summary.total_images > 0
        assert cls_summary.split_counts.get("train") == expected_train
        assert cls_summary.split_counts.get("val") == expected_val

        # Verify class breakdown matches files on disk
        actual_cls_counts: Dict[str, int] = {}
        for split in ["train", "val"]:
            s_dir = cls_path / split
            if s_dir.exists():
                for c_dir in s_dir.iterdir():
                    if c_dir.is_dir() and not c_dir.name.startswith("."):
                        count = len(list(c_dir.glob("*.png")))
                        actual_cls_counts[c_dir.name] = actual_cls_counts.get(c_dir.name, 0) + count

        assert cls_summary.classes == actual_cls_counts, (
            f"Classification classes mismatch: {cls_summary.classes} != {actual_cls_counts}"
        )
        assert sum(cls_summary.classes.values()) == num_samples
        assert "OK" in cls_summary.classes

        # -------------------------------------------------------------
        # 1.2 Detection
        # -------------------------------------------------------------
        det_path = base / "detection"
        assert det_path.is_dir(), f"Detection dir missing at {det_path}"
        det_summary = inspect_dataset(det_path, task="detection")

        assert det_summary.task == "detection"
        assert det_summary.total_images == num_samples
        assert det_summary.total_images > 0
        assert det_summary.split_counts.get("train") == expected_train
        assert det_summary.split_counts.get("val") == expected_val

        # Verify Detection annotation counts match JSON annotations
        with open(det_path / "annotations_train.json") as f:
            tr_json = json.load(f)
        with open(det_path / "annotations_val.json") as f:
            val_json = json.load(f)

        assert len(tr_json["images"]) == expected_train
        assert len(val_json["images"]) == expected_val

        cat_id_to_name = {c["id"]: c["name"] for c in tr_json["categories"]}
        expected_cat_counts = {name: 0 for name in cat_id_to_name.values() if name != "__background__"}
        for ann in tr_json["annotations"] + val_json["annotations"]:
            cname = cat_id_to_name[ann["category_id"]]
            if cname != "__background__":
                expected_cat_counts[cname] = expected_cat_counts.get(cname, 0) + 1

        assert det_summary.classes == expected_cat_counts, (
            f"Detection classes mismatch: {det_summary.classes} != {expected_cat_counts}"
        )

        # -------------------------------------------------------------
        # 1.3 Segmentation
        # -------------------------------------------------------------
        seg_path = base / "segmentation"
        assert seg_path.is_dir(), f"Segmentation dir missing at {seg_path}"
        seg_summary = inspect_dataset(seg_path, task="segmentation")

        assert seg_summary.task == "segmentation"
        assert seg_summary.total_images == num_samples
        assert seg_summary.total_images > 0
        assert seg_summary.split_counts.get("train") == expected_train
        assert seg_summary.split_counts.get("val") == expected_val
        assert seg_summary.classes == {"defect_mask": num_samples}

        # Verify on-disk files match
        train_imgs = list((seg_path / "images" / "train").glob("*.png"))
        val_imgs = list((seg_path / "images" / "val").glob("*.png"))
        assert len(train_imgs) == expected_train
        assert len(val_imgs) == expected_val

        # -------------------------------------------------------------
        # 1.4 Anomaly Detection
        # -------------------------------------------------------------
        anom_path = base / "anomaly"
        assert anom_path.is_dir(), f"Anomaly dir missing at {anom_path}"
        anom_summary = inspect_dataset(anom_path, task="anomaly")

        assert anom_summary.task == "anomaly"
        assert anom_summary.total_images == num_samples
        assert anom_summary.total_images > 0
        assert anom_summary.split_counts.get("train") == expected_train
        assert anom_summary.split_counts["val"] + anom_summary.split_counts["test"] == expected_val
        anomaly_datasets = {
            split: AnomalyDataset(root_dir=anom_path, split=split)
            for split in ("train", "val", "test")
        }
        anomaly_paths = {
            split: {path.resolve() for path, _, _ in dataset.samples}
            for split, dataset in anomaly_datasets.items()
        }
        assert anomaly_paths["train"].isdisjoint(anomaly_paths["val"] | anomaly_paths["test"])
        assert anomaly_paths["val"].isdisjoint(anomaly_paths["test"])
        assert set.union(*anomaly_paths.values()) == {
            path.resolve() for folder in (anom_path / "train", anom_path / "test")
            for path in folder.rglob("*.png")
        }
        assert sum(anom_summary.classes.values()) == num_samples
        assert "good" in anom_summary.classes
        assert anom_summary.classes["good"] >= expected_train  # Train is 100% good


def test_inspect_dataset_individual_task_generation():
    """
    Verify selective generation per task works cleanly with inspect_dataset().
    """
    tasks = ["classification", "detection", "segmentation", "anomaly"]
    for t in tasks:
        with tempfile.TemporaryDirectory() as tmp_dir:
            generate_synthetic_dataset(
                output_dir=tmp_dir,
                num_samples=16,
                task=t,
                split_ratio=0.75,
                seed=77,
            )
            target_path = Path(tmp_dir) / t
            summary = inspect_dataset(target_path, task=t)
            assert summary.total_images == 16
            assert summary.split_counts["train"] == 12
            if t == "anomaly":
                assert summary.split_counts["val"] + summary.split_counts["test"] == 4
            else:
                assert summary.split_counts["val"] == 4
            assert summary.total_images > 0


# ============================================================================
# 2. AnomalyDataset with Custom Folder Naming
# ============================================================================

def test_anomaly_dataset_custom_folder_naming_normal_and_pass():
    """
    MISSION CRITICAL REQUIREMENT 2:
    Create a dataset with `train/normal` and `train/pass`,
    and verify `AnomalyDataset` loads all normal samples cleanly.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir)
        dir_normal = p / "train" / "normal"
        dir_pass = p / "train" / "pass"
        dir_normal.mkdir(parents=True)
        dir_pass.mkdir(parents=True)

        # Create 4 samples in train/normal
        for i in range(4):
            img = Image.new("RGB", (64, 64), color=(10 + i * 10, 50, 80))
            img.save(dir_normal / f"sample_normal_{i:02d}.png")

        # Create 3 samples in train/pass
        for i in range(3):
            img = Image.new("RGB", (64, 64), color=(80, 20 + i * 15, 40))
            img.save(dir_pass / f"sample_pass_{i:02d}.png")

        datasets = {
            split: AnomalyDataset(root_dir=p, split=split, image_size=(32, 32))
            for split in ("train", "val", "test")
        }
        paths = {
            split: {path.resolve() for path, _, _ in dataset.samples}
            for split, dataset in datasets.items()
        }
        assert tuple(len(datasets[split]) for split in ("train", "val", "test")) == (5, 1, 1)
        assert paths["train"].isdisjoint(paths["val"] | paths["test"])
        assert paths["val"].isdisjoint(paths["test"])
        assert set.union(*paths.values()) == {
            path.resolve() for folder in (dir_normal, dir_pass) for path in folder.glob("*.png")
        }

        # Verify every normal sample remains readable with a zero mask.
        for dataset in datasets.values():
            for idx in range(len(dataset)):
                img_tensor, label, mask_tensor = dataset[idx]
                assert isinstance(img_tensor, torch.Tensor)
                assert img_tensor.shape == (3, 32, 32)
                assert img_tensor.dtype == torch.float32
                assert label == 0, f"Sample {idx} must have normal label 0, got {label}"
                assert isinstance(mask_tensor, torch.Tensor)
                assert mask_tensor.shape == (32, 32)
                assert (mask_tensor == 0).all(), f"Sample {idx} mask must be strictly zero"

        # Verify integration with DataLoader
        loader = create_dataloader(datasets["train"], batch_size=3, shuffle=False)
        total_loaded = 0
        for batch_imgs, batch_labels, batch_masks in loader:
            total_loaded += batch_imgs.shape[0]
            assert (batch_labels == 0).all()
            assert (batch_masks == 0).all()
        assert total_loaded == 5


def test_anomaly_dataset_all_four_normal_aliases():
    """
    Verify all supported normal aliases ('good', 'ok', 'normal', 'pass')
    can coexist in the source folder without split leakage or omission.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir)
        aliases = ["good", "ok", "normal", "pass"]
        samples_per_alias = 2

        for alias in aliases:
            a_dir = p / "train" / alias
            a_dir.mkdir(parents=True)
            for i in range(samples_per_alias):
                Image.new("RGB", (48, 48), color=(30, 60, 90)).save(a_dir / f"{alias}_{i}.png")

        datasets = {split: AnomalyDataset(root_dir=p, split=split)
                    for split in ("train", "val", "test")}
        paths = {split: {path.resolve() for path, _, _ in dataset.samples}
                 for split, dataset in datasets.items()}
        assert tuple(len(datasets[split]) for split in ("train", "val", "test")) == (6, 1, 1)
        assert paths["train"].isdisjoint(paths["val"] | paths["test"])
        assert paths["val"].isdisjoint(paths["test"])
        assert set.union(*paths.values()) == {
            path.resolve() for alias in aliases for path in (p / "train" / alias).glob("*.png")
        }

        # Also verify inspect_dataset() accurately counts all 8 normal images
        summary = inspect_dataset(p, task="anomaly")
        assert summary.total_images == 8
        assert summary.classes["good"] == 8
        assert summary.split_counts == {"train": 6, "val": 1, "test": 1}


def test_anomaly_dataset_strict_normal_invariant_enforcement():
    """
    Adversarial security test:
    If any non-normal directory exists in train/ (e.g., 'defect', 'NG', 'scratch'),
    AnomalyDataset MUST raise ValueError to prevent training contamination.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir)
        (p / "train" / "normal").mkdir(parents=True)
        (p / "train" / "scratch").mkdir(parents=True)

        Image.new("RGB", (32, 32)).save(p / "train" / "normal" / "ok.png")
        Image.new("RGB", (32, 32)).save(p / "train" / "scratch" / "ng.png")

        with pytest.raises(ValueError, match="exclusively normal"):
            AnomalyDataset(root_dir=p, split="train")


def test_anomaly_dataset_val_split_fallback_and_test_defects():
    """
    Verify test/val split loads both normal and defective images,
    and connects ground_truth masks accurately.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir)
        # Train normal
        (p / "train" / "pass").mkdir(parents=True)
        Image.new("RGB", (32, 32)).save(p / "train" / "pass" / "ok.png")

        # Val with normal and defect
        (p / "val" / "normal").mkdir(parents=True)
        (p / "val" / "bent_pin").mkdir(parents=True)
        (p / "ground_truth" / "bent_pin").mkdir(parents=True)

        Image.new("RGB", (32, 32)).save(p / "val" / "normal" / "v_ok.png")
        Image.new("RGB", (32, 32)).save(p / "val" / "bent_pin" / "v_ng.png")

        # Create defect ground truth mask (binary 255)
        mask_im = Image.new("L", (32, 32), color=0)
        mask_im.paste(255, (8, 8, 24, 24))
        mask_im.save(p / "ground_truth" / "bent_pin" / "v_ng_mask.png")

        ds_val = AnomalyDataset(root_dir=p, split="val")
        assert len(ds_val) == 2

        # Verify inspect_dataset with val fallback
        summary = inspect_dataset(p, task="anomaly")
        assert summary.total_images == 3
        assert summary.split_counts == {"train": 1, "val": 2, "test": 0}
        assert summary.classes == {"good": 2, "defect": 1}

        # Check sample 0 (normal) vs sample 1 (defect with mask)
        labels = [ds_val[i][1] for i in range(len(ds_val))]
        assert 0 in labels
        assert 1 in labels

        # Defect sample should have positive mask pixels
        defect_idx = labels.index(1)
        _, d_label, d_mask = ds_val[defect_idx]
        assert d_label == 1
        assert d_mask.sum() > 0


# ============================================================================
# 3. Adversarial Edge Cases & Boundary Conditions
# ============================================================================

def test_inspect_dataset_invalid_task_raises():
    """Invalid task type raises descriptive ValueError."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        with pytest.raises(ValueError, match="Unknown task type"):
            inspect_dataset(tmp_dir, task="non_existent_task_foo")


@pytest.mark.parametrize("task_str", [
    "CLASSIFICATION",
    "  detection  ",
    "SEGMENTATION",
    "anomaly_detection",
    "ANOMALY",
])
def test_inspect_dataset_task_casing_and_whitespace(task_str):
    """Task name string should be whitespace and case tolerant."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        generate_synthetic_dataset(output_dir=tmp_dir, num_samples=10, task="all", seed=42)
        base = Path(tmp_dir)
        folder = "anomaly" if "anomaly" in task_str.lower() else task_str.strip().lower()
        summary = inspect_dataset(base / folder, task=task_str)
        assert summary.total_images == 10


def test_flat_classification_inspection():
    """Flat classification layout without train/val splits."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir)
        (p / "OK").mkdir()
        (p / "NG_defect").mkdir()
        for i in range(5):
            Image.new("RGB", (32, 32)).save(p / "OK" / f"{i}.png")
        for i in range(3):
            Image.new("RGB", (32, 32)).save(p / "NG_defect" / f"{i}.png")

        summary = inspect_dataset(p, task="classification")
        assert summary.total_images == 8
        assert summary.classes == {"OK": 5, "NG_defect": 3}
        assert summary.split_counts["train"] == int(8 * 0.8)
        assert summary.split_counts["val"] == 8 - int(8 * 0.8)


def test_corrupt_files_health_validation():
    """Ensure image health validator catches 0-byte, corrupt headers, and invalid formats."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir)
        # 0-byte file
        zero_p = p / "zero.png"
        zero_p.touch()
        v_zero = validate_image_file(zero_p)
        assert not v_zero.valid
        assert v_zero.error_code == "ZERO_BYTE"

        # Corrupt header
        corrupt_p = p / "corrupt.png"
        with open(corrupt_p, "wb") as f:
            f.write(b"NOT_A_PNG_HEADER_CORRUPT")
        v_corrupt = validate_image_file(corrupt_p)
        assert not v_corrupt.valid
        assert v_corrupt.error_code == "CORRUPT_HEADER"

        # Non-existent file
        v_missing = validate_image_file(p / "does_not_exist.png")
        assert not v_missing.valid
        assert v_missing.error_code == "FILE_NOT_FOUND"

        # Valid image
        valid_p = p / "valid.png"
        Image.new("RGB", (32, 32)).save(valid_p)
        v_valid = validate_image_file(valid_p)
        assert v_valid.valid
        assert v_valid.error_code == "OK"
        assert v_valid.dimensions == (32, 32)
