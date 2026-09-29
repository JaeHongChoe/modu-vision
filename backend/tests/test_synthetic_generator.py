"""
Tier 1 & Tier 2 Automated Test Suite for Feature F01: Procedural Synthetic Dataset Generator.
Covers procedural defect generation across all 3 modalities (PCB, Wafer, Metal Surface)
and all 4 tasks (Classification, Detection, Segmentation, Anomaly Detection),
seed determinism, resolution boundaries, throughput benchmarks, and folder hierarchies.
"""

import json
import os
import time
import numpy as np
import pytest


# ============================================================================
# Tier 1: Primary Feature Coverage Tests (>=5 tests)
# ============================================================================

@pytest.mark.parametrize("modality", ["pcb", "wafer", "metal"])
def test_modalities_normal_generation(synthetic_module, modality):
    """Tier 1: Verifies normal (zero-defect) sample generation for each modality."""
    rng = np.random.default_rng(42)
    sample = synthetic_module.render_sample(modality=modality, width=128, height=128, defect_type="none", rng=rng)

    assert isinstance(sample, synthetic_module.SyntheticSample)
    assert sample.image.shape == (128, 128, 3)
    assert sample.mask.shape == (128, 128)
    assert np.all(sample.mask == 0)
    assert len(sample.bboxes) == 0
    assert len(sample.labels) == 0
    assert sample.classification_label == "OK"
    assert sample.is_normal is True
    assert sample.modality == modality


def test_pcb_defect_injection(synthetic_module):
    """Tier 1: Verifies PCB defect injection generates non-zero mask pixels and valid bounding boxes."""
    rng = np.random.default_rng(101)
    defects = ["solder_bridge", "broken_trace", "solder_ball"]

    for d in defects:
        sample = synthetic_module.generate_pcb_sample(width=256, height=256, defect_type=d, rng=rng)
        assert sample.is_normal is False
        assert sample.classification_label == d
        assert len(sample.bboxes) > 0
        assert np.any(sample.mask > 0)

        for box in sample.bboxes:
            x1, y1, x2, y2 = box
            assert 0 <= x1 < x2 <= 256
            assert 0 <= y1 < y2 <= 256


def test_wafer_defect_injection(synthetic_module):
    """Tier 1: Verifies semiconductor wafer defect injection (scratches, stains)."""
    rng = np.random.default_rng(202)
    defects = ["micro_scratch", "ring_stain", "center_stain"]

    for d in defects:
        sample = synthetic_module.generate_wafer_sample(width=256, height=256, defect_type=d, rng=rng)
        assert sample.is_normal is False
        assert sample.classification_label == d
        assert len(sample.bboxes) > 0
        assert np.any(sample.mask > 0)

        for box in sample.bboxes:
            x1, y1, x2, y2 = box
            assert 0 <= x1 < x2 <= 256
            assert 0 <= y1 < y2 <= 256


def test_metal_defect_injection(synthetic_module):
    """Tier 1: Verifies brushed metal surface defect injection (scratches, gouges, pits)."""
    rng = np.random.default_rng(303)
    defects = ["scratch", "gouge", "pit_corrosion"]

    for d in defects:
        sample = synthetic_module.generate_metal_sample(width=256, height=256, defect_type=d, rng=rng)
        assert sample.is_normal is False
        assert sample.classification_label == d
        assert len(sample.bboxes) > 0
        assert np.any(sample.mask > 0)

        for box in sample.bboxes:
            x1, y1, x2, y2 = box
            assert 0 <= x1 < x2 <= 256
            assert 0 <= y1 < y2 <= 256


def test_multi_task_synchronization(synthetic_module):
    """Tier 1: Verifies synchronization across all 4 tasks on single generated sample."""
    rng = np.random.default_rng(404)
    # Defective sample
    d_sample = synthetic_module.render_sample("pcb", width=256, height=256, defect_type="solder_bridge", rng=rng)
    assert d_sample.is_normal is False
    assert d_sample.classification_label != "OK"
    assert len(d_sample.bboxes) >= 1
    assert np.any(d_sample.mask > 0)

    # Clean sample
    ok_sample = synthetic_module.render_sample("pcb", width=256, height=256, defect_type=None, rng=rng)
    assert ok_sample.is_normal is True
    assert ok_sample.classification_label == "OK"
    assert len(ok_sample.bboxes) == 0
    assert np.all(ok_sample.mask == 0)


# ============================================================================
# Tier 2: Boundary & Corner Cases Tests (>=5 tests)
# ============================================================================

def test_seed_determinism_identical(synthetic_module):
    """Tier 2: Validates bit-for-bit identical outputs when using identical seeds."""
    rng1 = np.random.default_rng(8888)
    sample1 = synthetic_module.generate_pcb_sample(256, 256, defect_type="solder_bridge", rng=rng1)

    rng2 = np.random.default_rng(8888)
    sample2 = synthetic_module.generate_pcb_sample(256, 256, defect_type="solder_bridge", rng=rng2)

    assert np.array_equal(sample1.image, sample2.image)
    assert np.array_equal(sample1.mask, sample2.mask)
    assert sample1.bboxes == sample2.bboxes
    assert sample1.labels == sample2.labels


def test_seed_determinism_diversity(synthetic_module):
    """Tier 2: Validates that different random seeds generate distinct patterns."""
    sample1 = synthetic_module.generate_metal_sample(128, 128, defect_type="scratch", rng=np.random.default_rng(1))
    sample2 = synthetic_module.generate_metal_sample(128, 128, defect_type="scratch", rng=np.random.default_rng(2))
    assert not np.array_equal(sample1.image, sample2.image)


def test_arbitrary_non_square_resolutions(synthetic_module):
    """Tier 2: Validates arbitrary rectangular resolutions without coordinate overflow."""
    rng = np.random.default_rng(777)
    # Wide rectangle
    wide = synthetic_module.render_sample("wafer", width=320, height=160, defect_type="micro_scratch", rng=rng)
    assert wide.image.shape == (160, 320, 3)
    assert wide.mask.shape == (160, 320)
    for b in wide.bboxes:
        assert 0 <= b[0] < b[2] <= 320
        assert 0 <= b[1] < b[3] <= 160

    # Tall rectangle
    tall = synthetic_module.render_sample("pcb", width=160, height=320, defect_type="broken_trace", rng=rng)
    assert tall.image.shape == (320, 160, 3)
    assert tall.mask.shape == (320, 160)
    for b in tall.bboxes:
        assert 0 <= b[0] < b[2] <= 160
        assert 0 <= b[1] < b[3] <= 320


def test_invalid_sample_count_raises_error(synthetic_module, temp_dir):
    """Tier 2: num_samples <= 0 must raise ValueError."""
    with pytest.raises(ValueError, match="num_samples must be greater than 0"):
        synthetic_module.generate_synthetic_dataset(output_dir=temp_dir, num_samples=0)

    with pytest.raises(ValueError, match="num_samples must be greater than 0"):
        synthetic_module.generate_synthetic_dataset(output_dir=temp_dir, num_samples=-5)


def test_invalid_modality_raises_error(synthetic_module, temp_dir):
    """Tier 2: Unknown modality name must raise ValueError."""
    with pytest.raises(ValueError, match="Invalid modality"):
        synthetic_module.generate_synthetic_dataset(
            output_dir=temp_dir, num_samples=10, modalities=["invalid_space_domain"]
        )


def test_generation_throughput_performance(synthetic_module, temp_dir):
    """Tier 2: Generates 100 industrial samples (256x256) in < 3.5 seconds."""
    start_time = time.time()
    summary = synthetic_module.generate_synthetic_dataset(
        output_dir=temp_dir,
        num_samples=100,
        image_size=(256, 256),
        seed=42,
    )
    elapsed = time.time() - start_time
    assert elapsed < 3.5, f"Generation throughput too slow: {elapsed:.2f}s >= 3.5s"
    assert summary["total_images"] == 100


def test_full_dataset_generation_folder_hierarchy(synthetic_module, temp_dir):
    """Tier 2: Validates complete 4-task directory structures and metadata files."""
    summary = synthetic_module.generate_synthetic_dataset(
        output_dir=temp_dir,
        num_samples=24,
        image_size=(128, 128),
        split_ratio=0.75,
        seed=999,
    )

    # Manifest and Summary JSON files
    manifest_path = os.path.join(temp_dir, "manifest.json")
    summary_path = os.path.join(temp_dir, "dataset_summary.json")
    assert os.path.exists(manifest_path)
    assert os.path.exists(summary_path)

    with open(manifest_path, "r") as f:
        manifest = json.load(f)
    assert len(manifest["images"]) == 24

    # 1. Classification layout
    assert os.path.isdir(os.path.join(temp_dir, "classification", "train", "OK"))
    assert os.path.isdir(os.path.join(temp_dir, "classification", "val", "OK"))

    # 2. Detection layout (COCO JSON)
    assert os.path.isfile(os.path.join(temp_dir, "detection", "annotations_train.json"))
    assert os.path.isfile(os.path.join(temp_dir, "detection", "annotations_val.json"))

    # 3. Segmentation layout (Images and single-channel PNG masks)
    assert os.path.isdir(os.path.join(temp_dir, "segmentation", "images", "train"))
    assert os.path.isdir(os.path.join(temp_dir, "segmentation", "masks", "train"))
    assert os.path.isfile(os.path.join(temp_dir, "segmentation", "class_map.json"))

    # 4. Anomaly Detection layout (MVTec AD convention)
    assert os.path.isdir(os.path.join(temp_dir, "anomaly", "train", "good"))
    assert os.path.isdir(os.path.join(temp_dir, "anomaly", "test", "good"))


def test_anomaly_train_strict_normal_invariant(synthetic_module, temp_dir):
    """Tier 2: Verifies anomaly train split contains 100% normal/good images (zero defects)."""
    synthetic_module.generate_synthetic_dataset(
        output_dir=temp_dir,
        num_samples=20,
        image_size=(128, 128),
        normal_ratio=0.3,  # Heavily defective generation overall
        seed=555,
    )

    anom_train_dir = os.path.join(temp_dir, "anomaly", "train")
    subdirs = [d for d in os.listdir(anom_train_dir) if os.path.isdir(os.path.join(anom_train_dir, d))]

    # Training split MUST only contain good/ok directory
    for s in subdirs:
        assert s.lower() in ["good", "ok", "normal"]
