"""S0-01 regressions exercise training datasets, using only synthetic files."""
import json

import numpy as np
import pytest
import torch
from PIL import Image

from backend.engine.augmentations import IndustrialAugmentationPipeline, create_industrial_transforms, PhotometricTransform
from backend.engine.dataset_loaders import (
    ClassificationDataset, DetectionDataset, SegmentationDataset,
    set_request_split_root, reset_request_split_root,
)
from backend.engine.grouped_dataset_views import load_manifest_dataset


def _pipeline(task, **kwargs):
    return IndustrialAugmentationPipeline(
        task=task, brightness_range=(0, 0), contrast_range=(0, 0),
        max_rotation_deg=0, flip_horizontal=True, flip_vertical=False,
        cutout_prob=0, **kwargs,
    )


@pytest.fixture
def source(tmp_path):
    root = tmp_path / '합성 데이터'
    (root / 'images').mkdir(parents=True)
    (root / 'masks').mkdir()
    pixels = np.zeros((32, 32, 3), dtype=np.uint8)
    pixels[3:7, 2:6] = 255
    Image.fromarray(pixels).save(root / 'images' / 'sample.png')
    mask = np.zeros((32, 32), dtype=np.uint8)
    mask[3:7, 2:6] = 3
    Image.fromarray(mask).save(root / 'masks' / 'sample.png')
    coco = {
        'images': [{'id': 1, 'file_name': 'sample.png', 'width': 32, 'height': 32}],
        'categories': [{'id': 7, 'name': 'scratch'}],
        'annotations': [{'id': 1, 'image_id': 1, 'category_id': 7, 'bbox': [2, 3, 4, 4]}],
    }
    (root / 'annotations.json').write_text(json.dumps(coco))
    return root


def _dataset(source, task, grouped, transform, tmp_path):
    if grouped:
        from backend.api.routes_dataset import _write_split_manifest
        token = set_request_split_root(tmp_path / 'splits')
        try:
            _write_split_manifest(source, {'images/sample.png': 'train'}, 42)
            return load_manifest_dataset(task, source, 'train', transform=transform)
        finally:
            reset_request_split_root(token)
    if task == 'detection':
        return DetectionDataset(root_dir=source, transform=transform)
    return SegmentationDataset(root_dir=source, transform=transform)


@pytest.mark.parametrize('grouped', [False, True])
def test_detection_loader_flips_pixels_and_all_box_coordinates(source, tmp_path, monkeypatch, grouped):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    dataset = _dataset(source, 'detection', grouped, _pipeline('detection'), tmp_path)
    image, target = dataset[0]
    assert image[0, 3:7, 26:30].eq(1).all()
    assert target['boxes'].tolist() == [[26, 3, 30, 7]]
    assert target['boxes_normalized'].tolist() == [[26 / 32, 3 / 32, 30 / 32, 7 / 32]]
    assert target['area'].tolist() == [16]
    assert target['labels'].tolist() == [1]
    # Original parsed annotations must remain intact across repeated samples.
    assert dataset.img_to_annos[1][0]['bbox'] == [2, 3, 4, 4]


@pytest.mark.parametrize('grouped', [False, True])
def test_segmentation_loader_flip_keeps_multiclass_mask_aligned(source, tmp_path, monkeypatch, grouped):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    dataset = _dataset(source, 'segmentation', grouped, _pipeline('segmentation'), tmp_path)
    image, mask = dataset[0]
    pixels, region = image[0] > .5, mask > 0
    assert int((pixels & region).sum()) / int((pixels | region).sum()) == 1.0
    assert mask[3:7, 26:30].eq(3).all()
    assert mask.dtype == torch.int64
    assert mask.unique().tolist() == [0, 3]


@pytest.mark.parametrize('task', ['detection', 'segmentation'])
@pytest.mark.parametrize('grouped', [False, True])
def test_loader_rotation_uses_same_angle_for_pixels_and_targets(source, tmp_path, monkeypatch, task, grouped):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .9)
    monkeypatch.setattr('backend.engine.augmentations.random.uniform', lambda *args: 90.)
    transform = IndustrialAugmentationPipeline(
        task=task, brightness_range=(0, 0), contrast_range=(0, 0),
        max_rotation_deg=90, flip_horizontal=False, flip_vertical=False, cutout_prob=0,
    )
    image, target = _dataset(source, task, grouped, transform, tmp_path)[0]
    assert image[0, 26:30, 3:7].eq(1).all()
    if task == 'detection':
        assert target['boxes'].tolist() == [[3, 26, 7, 30]]
        assert target['boxes_normalized'].tolist() == [[3 / 32, 26 / 32, 7 / 32, 30 / 32]]
    else:
        assert target[26:30, 3:7].eq(3).all()
        assert torch.equal(image[0] > .5, target > 0)


@pytest.mark.parametrize('task', ['detection', 'segmentation'])
@pytest.mark.parametrize('profile', ['none', 'photometric'])
def test_loader_none_and_photometric_preserve_target_geometry(source, tmp_path, task, profile):
    image, target = _dataset(source, task, False, create_industrial_transforms(task=task, profile=profile), tmp_path)[0]
    if task == 'detection':
        assert target['boxes'].tolist() == [[2, 3, 6, 7]]
    else:
        assert target[3:7, 2:6].eq(3).all()
        assert target.unique().tolist() == [0, 3]
    if profile == 'none':
        assert image[0, 3:7, 2:6].eq(1).all()


def test_classification_image_only_transform_preserves_class(source, tmp_path, monkeypatch):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    dataset = ClassificationDataset(root_dir=source, transform=_pipeline('classification'))
    image, label = dataset[0]
    assert image[0, 3:7, 26:30].eq(1).all()
    assert isinstance(label, int)


def test_detection_empty_labels_keep_empty_tensor_shapes(source, tmp_path, monkeypatch):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    coco = json.loads((source / 'annotations.json').read_text())
    coco['annotations'] = []
    dataset = DetectionDataset(images_dir=source / 'images', annotation_data=coco, transform=_pipeline('detection'))
    image, target = dataset[0]
    assert image[0, 3:7, 26:30].eq(1).all()
    assert target['boxes'].shape == target['boxes_normalized'].shape == (0, 4)
    assert target['area'].shape == target['labels'].shape == target['iscrowd'].shape == (0,)


def test_loader_rotation_clips_boxes_and_recomputes_area(source, tmp_path, monkeypatch):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .9)
    monkeypatch.setattr('backend.engine.augmentations.random.uniform', lambda *args: 45.)
    coco = json.loads((source / 'annotations.json').read_text())
    coco['annotations'][0]['bbox'] = [0, 0, 8, 8]
    transform = IndustrialAugmentationPipeline(
        task='detection', brightness_range=(0, 0), contrast_range=(0, 0),
        max_rotation_deg=45, flip_horizontal=False, flip_vertical=False, cutout_prob=0,
    )
    _, target = DetectionDataset(images_dir=source / 'images', annotation_data=coco, transform=transform)[0]
    # Hand projection around (16,16): x=[-6.6274,4.6863], y=[10.3431,21.6569].
    assert target['boxes'][0].tolist() == pytest.approx([0, 10.34314575, 4.68629150, 21.65685425])
    assert target['area'].item() == pytest.approx(53.01933598)
    assert target['boxes_normalized'][0].tolist() == pytest.approx([0, .323223305, .14644661, .676776695])


def test_seeded_sample_receipt_replays_without_changing_global_rng(source):
    import random
    transform = create_industrial_transforms(task='segmentation')
    image = torch.zeros((3, 32, 32)); image[:, 3:7, 2:6] = 1
    mask = torch.zeros((32, 32), dtype=torch.long); mask[3:7, 2:6] = 3
    python_state, torch_state = random.getstate(), torch.random.get_rng_state().clone()
    first = transform.augment_sample(image, mask, task='segmentation', seed=42)
    second = transform.augment_sample(image, mask, task='segmentation', seed=42)
    assert torch.equal(first.image, second.image)
    assert torch.equal(first.targets, second.targets)
    assert first.transform_receipt == second.transform_receipt
    assert first.transform_receipt['version'] == 1
    assert first.transform_receipt['seed'] == 42
    assert first.targets.unique().tolist() == [0, 3]
    assert random.getstate() == python_state
    assert torch.equal(torch.random.get_rng_state(), torch_state)


def test_legacy_image_only_callable_still_works_for_labelled_loaders(source, tmp_path):
    for task in ('detection', 'segmentation'):
        image, target = _dataset(source, task, False, PhotometricTransform(lambda image: image * .5), tmp_path)[0]
        assert image[0, 3:7, 2:6].eq(.5).all()
        if task == 'detection':
            assert target['boxes'].tolist() == [[2, 3, 6, 7]]
        else:
            assert target[3:7, 2:6].eq(3).all()


@pytest.mark.parametrize('grouped', [False, True])
def test_loader_vertical_flip_moves_pixels_and_mask(source, tmp_path, monkeypatch, grouped):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    transform = IndustrialAugmentationPipeline(
        task='segmentation', brightness_range=(0, 0), contrast_range=(0, 0),
        max_rotation_deg=0, flip_horizontal=False, flip_vertical=True, cutout_prob=0,
    )
    image, mask = _dataset(source, 'segmentation', grouped, transform, tmp_path)[0]
    assert image[0, 25:29, 2:6].eq(1).all()
    assert mask[25:29, 2:6].eq(3).all()
    assert torch.equal(image[0] > .5, mask > 0)


@pytest.mark.parametrize('grouped', [False, True])
def test_loader_oblique_rotation_never_creates_fractional_mask_classes(source, tmp_path, monkeypatch, grouped):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .9)
    monkeypatch.setattr('backend.engine.augmentations.random.uniform', lambda *args: 7.)
    transform = IndustrialAugmentationPipeline(
        task='segmentation', brightness_range=(0, 0), contrast_range=(0, 0),
        max_rotation_deg=7, flip_horizontal=False, flip_vertical=False, cutout_prob=0,
    )
    image, mask = _dataset(source, 'segmentation', grouped, transform, tmp_path)[0]
    assert mask.dtype == torch.int64
    assert mask.unique().tolist() == [0, 3]
    # Bilinear edge coverage differs from nearest labels; labelled pixels retain signal.
    assert image[0][mask > 0].gt(0).all()


def test_trainer_detection_builder_supplies_joint_transform(source, monkeypatch):
    from backend.engine.trainer import _build_detection_datasets
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    train, val = _build_detection_datasets(source, _pipeline('detection'), (32, 32))
    assert train[0][1]['boxes'].tolist() == [[26, 3, 30, 7]]
    assert val[0][1]['boxes'].tolist() == [[2, 3, 6, 7]]


def test_anomaly_loader_flips_known_ground_truth_together(tmp_path, monkeypatch):
    from backend.engine.dataset_loaders import AnomalyDataset
    (tmp_path / 'test' / 'crack').mkdir(parents=True)
    (tmp_path / 'ground_truth' / 'crack').mkdir(parents=True)
    pixels = np.zeros((32, 32, 3), dtype=np.uint8); pixels[3:7, 2:6] = 255
    mask = np.zeros((32, 32), dtype=np.uint8); mask[3:7, 2:6] = 255
    Image.fromarray(pixels).save(tmp_path / 'test' / 'crack' / 'ng.png')
    Image.fromarray(mask).save(tmp_path / 'ground_truth' / 'crack' / 'ng_mask.png')
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    dataset = AnomalyDataset(root_dir=tmp_path, split='val', transform=_pipeline('anomaly'))
    image, label, target = dataset[0]
    assert label == 1
    assert target[3:7, 26:30].eq(1).all()
    assert torch.equal(image[0] > .5, target > 0)


def test_loader_discards_fully_rotated_out_box_with_matching_labels(source, monkeypatch):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .9)
    monkeypatch.setattr('backend.engine.augmentations.random.uniform', lambda *args: 45.)
    coco = json.loads((source / 'annotations.json').read_text())
    coco['annotations'][0]['bbox'] = [0, 0, 1, 1]
    transform = IndustrialAugmentationPipeline(
        task='detection', brightness_range=(0, 0), contrast_range=(0, 0),
        max_rotation_deg=45, flip_horizontal=False, flip_vertical=False, cutout_prob=0,
    )
    _, target = DetectionDataset(images_dir=source / 'images', annotation_data=coco, transform=transform)[0]
    assert target['boxes'].shape == target['boxes_normalized'].shape == (0, 4)
    assert target['labels'].shape == target['area'].shape == target['iscrowd'].shape == (0,)
    assert target['image_id'].tolist() == [1]


@pytest.mark.parametrize('task', ['detection', 'segmentation'])
@pytest.mark.parametrize('grouped', [False, True])
def test_explicit_joint_crop_clips_target_and_keeps_pixels_aligned(source, tmp_path, monkeypatch, task, grouped):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .9)
    transform = IndustrialAugmentationPipeline(
        task=task, crop=(4, 4, 16, 16), brightness_range=(0, 0), contrast_range=(0, 0),
        max_rotation_deg=0, flip_horizontal=False, flip_vertical=False, cutout_prob=0,
    )
    image, target = _dataset(source, task, grouped, transform, tmp_path)[0]
    assert image.shape == (3, 16, 16)
    assert image[0, :3, :2].eq(1).all()
    if task == 'detection':
        assert target['boxes'].tolist() == [[0, 0, 2, 3]]
        assert target['area'].tolist() == [6]
        assert target['boxes_normalized'].tolist() == [[0, 0, 2 / 16, 3 / 16]]
    else:
        assert target.shape == (16, 16)
        assert target[:3, :2].eq(3).all()
        assert torch.equal(image[0] > .5, target > 0)


def test_explicit_crop_can_remove_all_objects_and_retains_sample_id(source, tmp_path, monkeypatch):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .9)
    transform = IndustrialAugmentationPipeline(
        task='detection', crop=(16, 16, 16, 16), brightness_range=(0, 0), contrast_range=(0, 0),
        max_rotation_deg=0, flip_horizontal=False, flip_vertical=False, cutout_prob=0,
    )
    image, target = _dataset(source, 'detection', False, transform, tmp_path)[0]
    assert image.sum().item() == 0
    assert target['boxes'].shape == target['boxes_normalized'].shape == (0, 4)
    assert target['labels'].shape == target['area'].shape == target['iscrowd'].shape == (0,)
    assert target['image_id'].tolist() == [1]


@pytest.mark.parametrize('crop', [(-1, 0, 16, 16), (0, 0, 0, 16), (0, 0, 64, 16), (0, 0, 3.5, 16)])
def test_explicit_crop_rejects_invalid_geometry_before_transform(source, tmp_path, crop):
    transform = IndustrialAugmentationPipeline(task='detection', crop=crop)
    dataset = _dataset(source, 'detection', False, transform, tmp_path)
    with pytest.raises(ValueError, match='crop'):
        dataset[0]


@pytest.mark.parametrize('flip_horizontal,flip_vertical,rotation,expected_box,expected_direction', [
    (True, False, 0, [27, 5, 4, 2, -30], 145),
    (False, True, 0, [5, 27, 4, 2, -30], 325),
    (False, False, 90, [5, 27, 4, 2, -60], 305),
])
def test_detection_loader_rotates_oriented_boxes_and_independent_direction(
    source, monkeypatch, flip_horizontal, flip_vertical, rotation, expected_box, expected_direction,
):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    monkeypatch.setattr('backend.engine.augmentations.random.uniform', lambda *args: float(rotation))
    coco = json.loads((source / 'annotations.json').read_text())
    coco['annotations'][0]['rotated_bbox'] = [5, 5, 4, 2, 30]
    coco['annotations'][0]['direction_deg'] = 35
    transform = IndustrialAugmentationPipeline(
        task='detection', brightness_range=(0, 0), contrast_range=(0, 0),
        max_rotation_deg=rotation, flip_horizontal=flip_horizontal,
        flip_vertical=flip_vertical, cutout_prob=0,
    )
    dataset = DetectionDataset(images_dir=source / 'images', annotation_data=coco, transform=transform)
    _, target = dataset[0]
    assert target['rotated_boxes'][0].tolist() == pytest.approx(expected_box)
    assert target['direction_deg'].tolist() == [expected_direction]
    assert coco['annotations'][0]['rotated_bbox'] == [5, 5, 4, 2, 30]
    assert coco['annotations'][0]['direction_deg'] == 35


def test_oriented_joint_crop_translates_center_without_losing_orientation(source, monkeypatch):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .9)
    coco = json.loads((source / 'annotations.json').read_text())
    coco['annotations'][0]['rotated_bbox'] = [5, 5, 4, 2, 30]
    coco['annotations'][0]['direction_deg'] = 35
    transform = IndustrialAugmentationPipeline(
        task='detection', crop=(1, 1, 16, 16), brightness_range=(0, 0), contrast_range=(0, 0),
        max_rotation_deg=0, flip_horizontal=False, flip_vertical=False, cutout_prob=0,
    )
    _, target = DetectionDataset(images_dir=source / 'images', annotation_data=coco, transform=transform)[0]
    assert target['rotated_boxes'][0].tolist() == [4, 4, 4, 2, 30]
    assert target['direction_deg'].tolist() == [35]
    assert target['boxes'][0].tolist() == pytest.approx([1.76794919, 2.1339746, 6.23205081, 5.8660254])


def test_oriented_crop_rejects_partial_box_instead_of_silently_changing_geometry(source):
    coco = json.loads((source / 'annotations.json').read_text())
    coco['annotations'][0]['rotated_bbox'] = [5, 5, 4, 2, 30]
    transform = IndustrialAugmentationPipeline(task='detection', crop=(4, 4, 16, 16))
    dataset = DetectionDataset(images_dir=source / 'images', annotation_data=coco, transform=transform)
    with pytest.raises(ValueError, match='rotated.*crop|crop.*rotated'):
        dataset[0]


def test_grouped_rotated_targets_retain_orientation_and_direction(source, tmp_path, monkeypatch):
    # LabelMe/native is the existing supported input for independent direction.
    document = {'imagePath': 'sample.png', 'imageWidth': 32, 'imageHeight': 32, 'shapes': [
        {'label': 'scratch', 'shape_type': 'polygon', 'points': [[3, 3], [7, 3], [7, 7]],
         'flags': {'studio_rotated_bbox': [5, 5, 4, 2, 30], 'studio_direction_deg': 35}},
        {'label': 'scratch', 'shape_type': 'rectangle', 'points': [[10, 10], [14, 14]], 'flags': {}},
    ]}
    (source / 'images' / 'sample.json').write_text(json.dumps(document))
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    _, target = _dataset(source, 'detection', True, _pipeline('detection'), tmp_path)[0]
    assert target['rotated_boxes'][0].tolist() == [27, 5, 4, 2, -30]
    assert target['rotated_boxes_valid'].tolist() == [True, False]
    assert target['direction_deg'][0].item() == 145
    assert target['direction_valid'].tolist() == [True, False]
    assert target['boxes'][1].tolist() == [18, 10, 22, 14]


def test_new_detection_checkpoint_records_joint_augmentation_contract(source, tmp_path):
    from backend.engine.trainer import UnifiedAutoMLTrainer
    trainer = UnifiedAutoMLTrainer(task='detection', dataset_path=source, output_dir=tmp_path / 'checkpoint', device='cpu')
    trainer._save_checkpoint(epoch=1, model=torch.nn.Linear(2, 2), metric=.5,
                             classes=['scratch'], img_size=(32, 32), elapsed=0)
    metadata = json.loads((tmp_path / 'checkpoint' / 'model_meta.json').read_text())
    assert metadata['augmentation_contract']['version'] == 1
    assert metadata['augmentation_contract']['target_sync'] is True
    assert metadata['augmentation_contract']['crop'] is None


def test_rotated_detection_sample_task_dispatch_preserves_joint_geometry(monkeypatch):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    transform = _pipeline('rotated_detection')
    image = torch.zeros((3, 32, 32)); image[:, 3:7, 2:6] = 1
    targets = {'boxes': torch.tensor([[2., 3., 6., 7.]]), 'labels': torch.tensor([1]),
               'rotated_boxes': torch.tensor([[4., 5., 4., 4., 0.]]), 'direction_deg': torch.tensor([35.])}
    sample = transform.augment_sample(image, targets, task='rotated_detection')
    assert sample.targets['boxes'].tolist() == [[26, 3, 30, 7]]
    assert sample.targets['rotated_boxes'].tolist() == [[28, 5, 4, 4, 0]]
    assert sample.targets['direction_deg'].tolist() == [145]


def test_crop_removed_box_cannot_reappear_after_rotation(source, monkeypatch):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .9)
    monkeypatch.setattr('backend.engine.augmentations.random.uniform', lambda *args: 45.)
    transform = IndustrialAugmentationPipeline(
        task='detection', crop=(3, 8, 16, 16), brightness_range=(0, 0), contrast_range=(0, 0),
        max_rotation_deg=45, flip_horizontal=False, flip_vertical=False, cutout_prob=0,
    )
    _, target = DetectionDataset(root_dir=source, transform=transform)[0]
    assert target['boxes'].shape == (0, 4)
    assert target['labels'].shape == (0,)


def test_oriented_loader_anisotropic_resize_retains_exact_polygon(source):
    coco = json.loads((source / 'annotations.json').read_text())
    coco['annotations'][0]['rotated_bbox'] = [5, 5, 4, 2, 30]
    coco['annotations'][0]['direction_deg'] = 45
    dataset = DetectionDataset(images_dir=source / 'images', annotation_data=coco, image_size=(64, 32))
    image, target = dataset[0]
    assert image.shape == (3, 32, 64)
    assert target['rotated_boxes_valid'].tolist() == [False]
    assert target['rotated_boxes'].tolist() == [[0, 0, 0, 0, 0]]
    assert target['rotated_polygons_valid'].tolist() == [True]
    expected = [[7.53589838, 3.13397460], [14.46410162, 5.13397460],
                [12.46410162, 6.86602540], [5.53589838, 4.86602540]]
    torch.testing.assert_close(target['rotated_polygons'][0], torch.tensor(expected), atol=1e-5, rtol=0)
    assert target['boxes'][0].tolist() == pytest.approx([5.53589838, 3.13397460, 14.46410162, 6.86602540])
    assert target['direction_deg'].tolist() == pytest.approx([26.565051177])


@pytest.mark.parametrize('task', ['detection', 'segmentation'])
def test_legacy_spatial_callback_cannot_silently_leave_labels_untransformed(source, task):
    from torchvision.transforms import Compose, RandomHorizontalFlip
    transform = Compose([RandomHorizontalFlip(p=1)])
    dataset = (DetectionDataset if task == 'detection' else SegmentationDataset)(
        root_dir=source, transform=transform)
    with pytest.raises(ValueError, match='joint'):
        dataset[0]


@pytest.mark.parametrize('field', ['masks', 'keypoints'])
def test_unsupported_detection_geometry_requires_a_spatial_adapter(field):
    image = torch.zeros((3, 32, 32))
    target = {'boxes': torch.tensor([[2., 3., 6., 7.]]), 'labels': torch.tensor([1]),
              field: torch.ones((1, 3, 3))}
    with pytest.raises(ValueError, match=field):
        _pipeline('detection').augment_sample(image, target, seed=1)


def test_oriented_axis_aligned_box_survives_anisotropic_resize(source):
    coco = json.loads((source / 'annotations.json').read_text())
    coco['annotations'][0]['rotated_bbox'] = [5, 5, 4, 2, 0]
    _, target = DetectionDataset(images_dir=source / 'images', annotation_data=coco, image_size=(64, 32))[0]
    assert target['rotated_boxes_valid'].tolist() == [True]
    assert target['rotated_boxes'].tolist() == [[10, 5, 8, 2, 0]]


def test_oriented_resize_rounding_keeps_standard_detection_loading(tmp_path):
    images = tmp_path / 'images'; images.mkdir()
    Image.new('RGB', (2000, 1001)).save(images / 'wide.png')
    coco = {'images': [{'id': 1, 'file_name': 'wide.png', 'width': 2000, 'height': 1001}],
            'categories': [{'id': 1, 'name': 'scratch'}],
            'annotations': [{'image_id': 1, 'category_id': 1, 'bbox': [200, 100, 60, 40],
                             'rotated_bbox': [230, 120, 60, 40, 30]}]}
    image, target = DetectionDataset(images_dir=images, annotation_data=coco)[0]
    assert image.shape == (3, 801, 1600)
    assert target['labels'].tolist() == [1]
    # Rounding is still an affine deformation, not a falsely exact rectangle.
    assert target['rotated_boxes_valid'].tolist() == [False]
    assert target['rotated_polygons_valid'].tolist() == [True]


@pytest.mark.parametrize('grouped', [False, True])
def test_anisotropic_native_polygon_flips_with_actual_loader(source, tmp_path, monkeypatch, grouped):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    if grouped:
        document = {'imagePath': 'sample.png', 'imageWidth': 32, 'imageHeight': 32, 'shapes': [
            {'label': 'scratch', 'shape_type': 'polygon', 'points': [[3, 3], [7, 3], [7, 7]],
             'flags': {'studio_rotated_bbox': [5, 5, 4, 2, 30]}}]}
        (source / 'images' / 'sample.json').write_text(json.dumps(document))
        from backend.api.routes_dataset import _write_split_manifest
        token = set_request_split_root(tmp_path / 'splits')
        try:
            _write_split_manifest(source, {'images/sample.png': 'train'}, 42)
            dataset = load_manifest_dataset('detection', source, 'train', transform=_pipeline('detection'), image_size=(64, 32))
        finally:
            reset_request_split_root(token)
    else:
        coco = json.loads((source / 'annotations.json').read_text())
        coco['annotations'][0]['rotated_bbox'] = [5, 5, 4, 2, 30]
        dataset = DetectionDataset(images_dir=source / 'images', annotation_data=coco, transform=_pipeline('detection'), image_size=(64, 32))
    _, target = dataset[0]
    assert target['rotated_boxes_valid'].tolist() == [False]
    assert target['rotated_polygons'][0, 0].tolist() == pytest.approx([56.46410162, 3.13397460])
    assert target['boxes'][0].tolist() == pytest.approx([49.53589838, 3.13397460, 58.46410162, 6.86602540])


@pytest.mark.parametrize('task', ['detection', 'segmentation'])
@pytest.mark.parametrize('kind', ['v2_flip', 'lambda_flip', 'sequential_flip', 'v2_compose'])
def test_unknown_spatial_transforms_require_explicit_joint_protocol(source, task, kind):
    from torchvision import transforms as T
    from torchvision.transforms import v2
    import torchvision.transforms.functional as functional
    transforms = {'v2_flip': v2.RandomHorizontalFlip(p=1),
                  'lambda_flip': T.Lambda(functional.hflip),
                  'sequential_flip': torch.nn.Sequential(T.RandomHorizontalFlip(p=1)),
                  'v2_compose': v2.Compose([v2.RandomHorizontalFlip(p=1)])}
    dataset = (DetectionDataset if task == 'detection' else SegmentationDataset)(root_dir=source, transform=transforms[kind])
    with pytest.raises(ValueError, match='joint|PhotometricTransform'):
        dataset[0]


def test_arbitrary_callback_requires_explicit_photometric_declaration(source):
    dataset = DetectionDataset(root_dir=source, transform=lambda image: image.flip(-1))
    with pytest.raises(ValueError, match='PhotometricTransform|joint'):
        dataset[0]


@pytest.mark.parametrize('task', ['detection', 'segmentation'])
def test_explicit_photometric_callback_preserves_legacy_effect(source, task):
    from backend.engine.augmentations import PhotometricTransform
    callback = PhotometricTransform(lambda image: image * .5)
    dataset = (DetectionDataset if task == 'detection' else SegmentationDataset)(root_dir=source, transform=callback)
    image, target = dataset[0]
    assert image[0, 3:7, 2:6].eq(.5).all()
    if task == 'detection':
        assert target['boxes'].tolist() == [[2, 3, 6, 7]]
    else:
        assert target[3:7, 2:6].eq(3).all()


@pytest.mark.parametrize('kind', ['v1_compose', 'v2_compose', 'sequential'])
def test_supported_photometric_containers_preserve_target_geometry(source, kind):
    from torchvision import transforms as T
    from torchvision.transforms import v2
    transforms = {'v1_compose': T.Compose([T.ColorJitter(brightness=(.5, .5))]),
                  'v2_compose': v2.Compose([v2.ColorJitter(brightness=(.5, .5))]),
                  'sequential': torch.nn.Sequential(T.ColorJitter(brightness=(.5, .5)))}
    image, target = DetectionDataset(root_dir=source, transform=transforms[kind])[0]
    assert image[0, 3:7, 2:6].eq(.5).all()
    assert target['boxes'].tolist() == [[2, 3, 6, 7]]


@pytest.mark.parametrize('task', ['detection', 'segmentation'])
def test_labelme_adapter_joint_flip_matches_actual_targets(source, monkeypatch, task):
    from backend.engine.industrial_adapters import LabelMeDetectionDataset, LabelMeSegmentationDataset
    folder = source / 'images'
    document = {'imagePath': 'sample.png', 'imageWidth': 32, 'imageHeight': 32, 'shapes': [
        {'label': 'scratch', 'shape_type': 'rectangle', 'points': [[2, 3], [6, 7]], 'flags': {}}]}
    if task == 'segmentation':
        document['shapes'][0].update(shape_type='polygon', points=[[2, 3], [6, 3], [6, 7], [2, 7]])
    (folder / 'sample.json').write_text(json.dumps(document))
    if task == 'segmentation':
        # Existing LabelMe rasterizer includes both rectangle boundary pixels.
        pixels = np.zeros((32, 32, 3), dtype=np.uint8); pixels[3:8, 2:7] = 255
        Image.fromarray(pixels).save(folder / 'sample.png')
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .25)
    factory = LabelMeDetectionDataset if task == 'detection' else LabelMeSegmentationDataset
    image, target = factory(folder, transform=_pipeline(task))[0]
    if task == 'detection':
        assert image[0, 3:7, 26:30].eq(1).all()
        assert target['boxes'].tolist() == [[26, 3, 30, 7]]
        assert target['boxes_normalized'].tolist() == [[26 / 32, 3 / 32, 30 / 32, 7 / 32]]
    else:
        assert target[3:8, 25:30].eq(1).all()
        assert torch.equal(image[0] > .5, target > 0)


@pytest.mark.parametrize('task', ['detection', 'segmentation'])
def test_labelme_adapter_accepts_explicit_photometric_callback(source, task):
    from backend.engine.industrial_adapters import LabelMeDetectionDataset, LabelMeSegmentationDataset
    folder = source / 'images'
    document = {'imagePath': 'sample.png', 'imageWidth': 32, 'imageHeight': 32, 'shapes': [
        {'label': 'scratch', 'shape_type': 'rectangle', 'points': [[2, 3], [6, 7]], 'flags': {}}]}
    if task == 'segmentation':
        document['shapes'][0].update(shape_type='polygon', points=[[2, 3], [6, 3], [6, 7], [2, 7]])
    (folder / 'sample.json').write_text(json.dumps(document))
    factory = LabelMeDetectionDataset if task == 'detection' else LabelMeSegmentationDataset
    image, target = factory(folder, transform=PhotometricTransform(lambda image: image * .5))[0]
    assert image[0, 3:7, 2:6].eq(.5).all()
    if task == 'detection':
        assert target['boxes'].tolist() == [[2, 3, 6, 7]]
    else:
        assert target[3:7, 2:6].eq(1).all()


@pytest.mark.parametrize('task', ['detection', 'segmentation', 'rotated_detection'])
def test_spatial_task_requires_joint_targets_before_modifying_image(task):
    image = torch.zeros((3, 32, 32))
    with pytest.raises(ValueError, match='target|mask'):
        _pipeline(task)(image)


def test_segmentation_numpy_target_is_rejected_before_spatial_transform():
    image = torch.zeros((3, 32, 32))
    mask = np.zeros((32, 32), dtype=np.int64)
    with pytest.raises(ValueError, match='Tensor|tensor'):
        _pipeline('segmentation').augment_sample(image, mask, seed=1)


@pytest.mark.parametrize('bbox', [[32, 3, 4, 4], [40, 3, 4, 4], [-6, 3, 4, 4], [2, 32, 4, 4]])
def test_detection_loader_drops_boxes_outside_image_without_augmentation(source, bbox):
    coco = json.loads((source / 'annotations.json').read_text())
    coco['annotations'][0]['bbox'] = bbox
    _, target = DetectionDataset(images_dir=source / 'images', annotation_data=coco)[0]
    assert target['boxes'].shape == (0, 4)
    assert target['labels'].shape == target['area'].shape == (0,)


def test_zero_width_joint_target_cannot_gain_area_when_rotated(monkeypatch):
    monkeypatch.setattr('backend.engine.augmentations.random.random', lambda: .9)
    monkeypatch.setattr('backend.engine.augmentations.random.uniform', lambda *args: 45.)
    transform = IndustrialAugmentationPipeline(task='detection', flip_horizontal=False, flip_vertical=False,
        max_rotation_deg=45, brightness_range=(0, 0), contrast_range=(0, 0), cutout_prob=0)
    sample = transform.augment_sample(torch.zeros((3, 32, 32)),
        {'boxes': torch.tensor([[5., 3., 5., 7.]]), 'labels': torch.tensor([1])})
    assert sample.targets['boxes'].shape == (0, 4)
    assert sample.targets['labels'].shape == (0,)
