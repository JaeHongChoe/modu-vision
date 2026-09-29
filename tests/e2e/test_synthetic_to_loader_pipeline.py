"""
End-to-End Pipeline Integration Test Suite (Tier 4 Real-World Application Scenarios).
Validates full data ingestion lifecycle across all 3 industrial modalities (PCB, Wafer, Metal)
and all 4 vision tasks (Classification, Detection, Segmentation, Anomaly Detection)
from procedural generation -> PyTorch Datasets -> DataLoader batching -> Hardware acceleration (MPS/CPU).
"""

import os
import pytest
import torch
from torch.utils.data import DataLoader


def test_full_classification_pipeline_e2e(synthetic_module, loader_module, device_module, temp_dir):
    """
    E2E Scenario 1: PCB / Wafer / Metal Multi-Class Defect Pipeline.
    Procedural generator -> ClassificationDataset -> DataLoader -> Device batching.
    """
    dev = device_module.get_device()

    summary = synthetic_module.generate_synthetic_dataset(
        output_dir=temp_dir,
        num_samples=24,
        image_size=(128, 128),
        seed=101,
    )
    assert summary["total_images"] == 24

    train_dir = os.path.join(temp_dir, "classification", "train")
    ds = loader_module.ClassificationDataset(root_dir=train_dir)
    loader = DataLoader(ds, batch_size=4, shuffle=True)

    batch_count = 0
    for images, labels in loader:
        images = device_module.to_device(images, dev)
        labels = device_module.to_device(labels, dev)

        assert images.device.type == dev.type
        assert labels.device.type == dev.type
        assert images.shape[1:] == (3, 128, 128)
        assert not torch.isnan(images).any()
        batch_count += 1

    assert batch_count > 0


def test_full_detection_pipeline_e2e(synthetic_module, loader_module, device_module, temp_dir):
    """
    E2E Scenario 2: Discrete Flaw Detection Pipeline.
    Procedural generator -> COCO Annotations -> DetectionDataset -> Variable-box Collation -> Device.
    """
    dev = device_module.get_device()

    synthetic_module.generate_synthetic_dataset(
        output_dir=temp_dir,
        num_samples=16,
        image_size=(128, 128),
        seed=202,
    )

    img_dir = os.path.join(temp_dir, "detection", "images", "train")
    anno_file = os.path.join(temp_dir, "detection", "annotations_train.json")
    ds = loader_module.DetectionDataset(images_dir=img_dir, annotation_file=anno_file)

    def collate_fn(batch):
        return tuple(zip(*batch))

    loader = DataLoader(ds, batch_size=2, shuffle=False, collate_fn=collate_fn)

    for images, targets in loader:
        dev_images = [device_module.to_device(img, dev) for img in images]
        dev_targets = [{k: device_module.to_device(v, dev) for k, v in t.items()} for t in targets]

        assert 1 <= len(dev_images) <= 2
        assert dev_images[0].device.type == dev.type
        for t in dev_targets:
            assert t["boxes"].device.type == dev.type
            assert t["labels"].device.type == dev.type


def test_full_segmentation_pipeline_e2e(synthetic_module, loader_module, device_module, temp_dir):
    """
    E2E Scenario 3: Fine Crack & Scratch Segmentation Pipeline.
    Procedural generator -> Paired Mask PNGs -> SegmentationDataset -> DataLoader -> Device.
    """
    dev = device_module.get_device()

    synthetic_module.generate_synthetic_dataset(
        output_dir=temp_dir,
        num_samples=16,
        image_size=(128, 128),
        seed=303,
    )

    img_dir = os.path.join(temp_dir, "segmentation", "images", "train")
    mask_dir = os.path.join(temp_dir, "segmentation", "masks", "train")
    ds = loader_module.SegmentationDataset(images_dir=img_dir, masks_dir=mask_dir)
    loader = DataLoader(ds, batch_size=4, shuffle=True)

    for images, masks in loader:
        images = device_module.to_device(images, dev)
        masks = device_module.to_device(masks, dev)

        assert images.device.type == dev.type
        assert masks.device.type == dev.type
        assert 1 <= images.shape[0] <= 4
        assert images.shape[1:] == (3, 128, 128)
        assert masks.shape[1:] == (128, 128)
        assert masks.dtype == torch.int64


def test_full_anomaly_detection_pipeline_e2e(synthetic_module, loader_module, device_module, temp_dir):
    """
    E2E Scenario 4: Unsupervised Normal-Only Anomaly Ingestion Pipeline.
    Procedural generator -> Reference AD Hierarchy -> AnomalyDataset -> Device.
    Strict Invariant: Train loader batches must be 100% defect-free (label=0, mask=zeros).
    """
    dev = device_module.get_device()

    synthetic_module.generate_synthetic_dataset(
        output_dir=temp_dir,
        num_samples=20,
        image_size=(128, 128),
        seed=404,
    )

    anom_root = os.path.join(temp_dir, "anomaly")
    train_ds = loader_module.AnomalyDataset(root_dir=anom_root, split="train")
    loader = DataLoader(train_ds, batch_size=4, shuffle=True)

    for images, labels, masks in loader:
        images = device_module.to_device(images, dev)
        labels = device_module.to_device(labels, dev)
        masks = device_module.to_device(masks, dev)

        assert images.device.type == dev.type
        assert torch.all(labels == 0)
        assert torch.all(masks == 0)
