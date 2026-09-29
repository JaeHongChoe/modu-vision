"""
backend/tests/test_m1_pipeline.py

End-to-End Integration Test Suite for Milestone M1 Pipeline:
Procedural generator -> All 4 Dataset Loaders -> PyTorch DataLoader batching -> Hardware Device placement.
"""

import os
import tempfile
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from backend.engine.device import (
    DynamicAMPContext,
    get_device,
    get_device_info,
    get_memory_stats,
    to_device,
)
from backend.engine.synthetic_generator import generate_synthetic_dataset
from backend.engine.dataset_loaders import (
    AnomalyDataset,
    ClassificationDataset,
    DetectionDataset,
    SegmentationDataset,
)


def test_full_m1_pipeline():
    """Generates synthetic dataset, creates dataloaders for all 4 tasks, batches to device."""
    device = get_device()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Step 1: Generate synthetic dataset (30 samples)
        summary = generate_synthetic_dataset(
            output_dir=tmpdir,
            num_samples=30,
            image_size=(128, 128),
            seed=777,
        )
        assert summary["total_images"] == 30

        # Step 2: Task 1 (Classification) Pipeline
        cls_ds = ClassificationDataset(root_dir=os.path.join(tmpdir, "classification", "train"))
        cls_loader = DataLoader(cls_ds, batch_size=4, shuffle=True)
        images, labels = next(iter(cls_loader))
        images = to_device(images, device)
        labels = to_device(labels, device)
        assert images.shape == (4, 3, 128, 128)
        assert images.device.type == device.type
        assert labels.device.type == device.type

        # Step 3: Task 2 (Detection) Pipeline
        det_img_dir = os.path.join(tmpdir, "detection", "images", "train")
        det_anno = os.path.join(tmpdir, "detection", "annotations_train.json")
        det_ds = DetectionDataset(images_dir=det_img_dir, annotation_file=det_anno)

        def collate_fn(batch):
            return tuple(zip(*batch))

        det_loader = DataLoader(det_ds, batch_size=2, collate_fn=collate_fn)
        det_images, det_targets = next(iter(det_loader))
        det_images = [to_device(img, device) for img in det_images]
        det_targets = [{k: to_device(v, device) for k, v in t.items()} for t in det_targets]
        assert len(det_images) == 2
        assert det_images[0].device.type == device.type
        assert det_targets[0]["boxes"].device.type == device.type

        # Step 4: Task 3 (Segmentation) Pipeline
        seg_img_dir = os.path.join(tmpdir, "segmentation", "images", "train")
        seg_mask_dir = os.path.join(tmpdir, "segmentation", "masks", "train")
        seg_ds = SegmentationDataset(images_dir=seg_img_dir, masks_dir=seg_mask_dir)
        seg_loader = DataLoader(seg_ds, batch_size=4, shuffle=True)
        s_images, s_masks = next(iter(seg_loader))
        s_images = to_device(s_images, device)
        s_masks = to_device(s_masks, device)
        assert s_images.shape == (4, 3, 128, 128)
        assert s_masks.shape == (4, 128, 128)
        assert s_images.device.type == device.type
        assert s_masks.device.type == device.type

        # Step 5: Task 4 (Anomaly Detection) Pipeline
        anom_root = os.path.join(tmpdir, "anomaly")
        anom_train_ds = AnomalyDataset(root_dir=anom_root, split="train")
        anom_loader = DataLoader(anom_train_ds, batch_size=4, shuffle=True)
        a_images, a_labels, a_masks = next(iter(anom_loader))
        a_images = to_device(a_images, device)
        assert a_images.shape == (4, 3, 128, 128)
        assert torch.all(a_labels == 0)  # Strict: All normal in train split!


def test_pipeline_forward_pass_with_amp():
    """Verifies that batched data from loaders passes through a model with DynamicAMPContext."""
    device = get_device()
    amp = DynamicAMPContext(device, enabled=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        generate_synthetic_dataset(
            output_dir=tmpdir,
            num_samples=12,
            image_size=(64, 64),
            seed=42,
        )

        cls_ds = ClassificationDataset(root_dir=os.path.join(tmpdir, "classification", "train"))
        loader = DataLoader(cls_ds, batch_size=2)
        images, _ = next(iter(loader))
        images = to_device(images, device)

        # Simple feature extractor
        model = nn.Sequential(
            nn.Conv2d(3, 16, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((1, 1)),
            nn.Flatten(),
            nn.Linear(16, 4),
        )
        model = to_device(model, device)

        with amp.autocast():
            logits = model(images)
            assert logits.shape == (2, 4)
            assert not torch.isnan(logits).any()


def test_val_splits_pipeline():
    """Verifies that validation splits across all 4 tasks load and yield proper tensors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        generate_synthetic_dataset(
            output_dir=tmpdir,
            num_samples=20,
            image_size=(64, 64),
            split_ratio=0.8,
            seed=100,
        )

        # 1. Classification val
        cls_val = ClassificationDataset(root_dir=os.path.join(tmpdir, "classification", "val"))
        assert len(cls_val) > 0
        img, lbl = cls_val[0]
        assert img.shape == (3, 64, 64)

        # 2. Detection val
        det_val = DetectionDataset(
            images_dir=os.path.join(tmpdir, "detection", "images", "val"),
            annotation_file=os.path.join(tmpdir, "detection", "annotations_val.json"),
        )
        assert len(det_val) > 0

        # 3. Segmentation val
        seg_val = SegmentationDataset(
            images_dir=os.path.join(tmpdir, "segmentation", "images", "val"),
            masks_dir=os.path.join(tmpdir, "segmentation", "masks", "val"),
        )
        assert len(seg_val) > 0

        # 4. Anomaly test
        anom_test = AnomalyDataset(root_dir=os.path.join(tmpdir, "anomaly"), split="test")
        assert len(anom_test) > 0
        t_img, t_lbl, t_mask = anom_test[0]
        assert t_img.shape == (3, 64, 64)
        assert t_lbl in (0, 1)
        assert t_mask.shape == (64, 64)
