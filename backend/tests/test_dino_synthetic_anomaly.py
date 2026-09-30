"""Normal-only native patch training, procedural boundaries and offline state contracts."""
import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from PIL import Image
from torch import nn
from torch.nn import functional as F


def _module(name):
    qualified = f'backend.engine.anomaly.{name}'
    assert importlib.util.find_spec(qualified) is not None, f'{name} contract is not implemented'
    return importlib.import_module(qualified)


class TinyEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.project = nn.Conv2d(3, 4, 1)
        self.num_features = 4
        self.num_prefix_tokens = 5
        self.patch_embed = SimpleNamespace(patch_size=(16, 16))
        self.seen_shapes = []

    def forward_features(self, x):
        self.seen_shapes.append(tuple(x.shape))
        spatial = F.avg_pool2d(self.project(x), 16).flatten(2).transpose(1, 2)
        return torch.cat((spatial.mean(1, keepdim=True), spatial[:, :1].expand(-1, 4, -1), spatial), 1)


class TinyTaskModel(nn.Module):
    def __init__(self, task, backbone, num_classes, pretrained=True, **options):
        super().__init__()
        self.encoder = TinyEncoder().requires_grad_(False)
        self.encoder.eval()
        self.num_prefix_tokens = 5
        self.head = nn.Linear(4, num_classes)
        self.register_buffer('input_mean', torch.tensor([0.485, 0.456, 0.406]).reshape(1, 3, 1, 1))
        self.register_buffer('input_std', torch.tensor([0.229, 0.224, 0.225]).reshape(1, 3, 1, 1))
        self.model_metadata = {'pretrained': pretrained, 'pretrained_sha256': 'a' * 64 if pretrained else None,
                               'pretrained_source': 'verified_fixture' if pretrained else None}


def _detector(monkeypatch, **options):
    module = _module('dino_synthetic')
    monkeypatch.setattr(module, 'DinoTaskModel', TinyTaskModel)
    torch.set_num_threads(1)
    return module.DinoSyntheticDetector(patch_size=32, stride=16, epochs=2, batch_size=4,
                                       patches_per_image=3, inference_batch_size=5, **options)


def _samples(tmp_path, count=2, label=0):
    samples = []
    for index in range(count):
        path = tmp_path / f'normal_{index}.png'
        y, x = np.indices((73, 91))
        rgb = np.stack(((x + index * 23) % 256, (y * 2 + index * 7) % 256, (x + y + 17) % 256), -1).astype('uint8')
        Image.fromarray(rgb).save(path)
        samples.append((path, label, None))
    return SimpleNamespace(samples=samples)


@pytest.mark.parametrize('family', ['spot', 'scratch', 'pollution', 'chipping'])
def test_procedural_masks_are_deterministic_and_preserve_outside_pixels(family):
    module = _module('synthetic_defects')
    source = np.full((48, 64, 3), 110, dtype=np.uint8)
    placement = np.zeros(source.shape[:2], dtype=bool)
    placement[12:36, 15:49] = True
    actual, mask, name = module.synthesize_defect(source, np.random.default_rng(17), family, placement)
    repeat, repeat_mask, _ = module.synthesize_defect(source, np.random.default_rng(17), family, placement)
    assert name == family
    assert mask.dtype == np.uint8 and mask.shape == source.shape[:2] and mask.any()
    assert np.array_equal(actual, repeat) and np.array_equal(mask, repeat_mask)
    assert np.array_equal(actual[mask == 0], source[mask == 0])
    assert not np.any(mask[~placement])
    assert np.any(actual[mask > 0] != source[mask > 0])
    assert np.all(source == 110), 'generation must not mutate the caller image'


def test_procedural_generation_rejects_invalid_or_empty_placement():
    module = _module('synthetic_defects')
    with pytest.raises(ValueError, match='placement'):
        module.synthesize_defect(np.zeros((32, 32, 3), dtype='uint8'), np.random.default_rng(1),
                                 placement_mask=np.zeros((32, 32), dtype=bool))


def test_fit_trains_head_only_native_balanced_patches_and_changes_each_epoch(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=True)
    source = _samples(tmp_path)
    before_encoder = {name: value.clone() for name, value in detector.feature_extractor.state_dict().items()}
    before_head = {name: value.clone() for name, value in detector.head.state_dict().items()}
    progress = []
    summary = detector.fit(SimpleNamespace(dataset=source), progress_callback=lambda *args: progress.append(args))
    assert summary['source_image_count'] == 2
    assert summary['normal_patch_count'] == summary['synthetic_patch_count'] == 12
    assert len(summary['epoch_history']) == 2
    assert summary['epoch_history'][0]['sample_plan_sha256'] != summary['epoch_history'][1]['sample_plan_sha256']
    assert all(row['source_coverage'] == 2 for row in summary['epoch_history'])
    assert sum(summary['synthetic_family_counts'].values()) == 12
    assert all(shape[-2:] == (32, 32) for shape in detector.feature_extractor.seen_shapes)
    assert all(not parameter.requires_grad for parameter in detector.feature_extractor.parameters())
    assert all(torch.equal(before_encoder[name], value) for name, value in detector.feature_extractor.state_dict().items())
    assert any(not torch.equal(before_head[name], value) for name, value in detector.head.state_dict().items())
    assert [(row[0], row[1]) for row in progress] == [(1, 2), (2, 2)]
    assert all(np.isfinite(row[2]) for row in progress)
    assert detector.threshold == 0.5
    assert detector.model_metadata['map_semantics'] == 'patch_score'
    assert detector.model_metadata['architecture'] == 'anomaly:dino_synthetic:dinov3_vits16:p32:s16:head_v1'


def test_fit_repeatability_for_same_seed_and_source(tmp_path, monkeypatch):
    source = _samples(tmp_path)
    first = _detector(monkeypatch, pretrained=False)
    second = _detector(monkeypatch, pretrained=False)
    a = first.fit(SimpleNamespace(dataset=source))
    b = second.fit(SimpleNamespace(dataset=source))
    assert [row['sample_plan_sha256'] for row in a['epoch_history']] == [row['sample_plan_sha256'] for row in b['epoch_history']]
    assert all(torch.equal(value, second.head.state_dict()[name]) for name, value in first.head.state_dict().items())


def test_normal_only_gate_and_cooperative_cancel_leave_head_unchanged(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=False)
    before = {name: value.clone() for name, value in detector.head.state_dict().items()}
    with pytest.raises(ValueError, match='normal|label'):
        detector.fit(SimpleNamespace(dataset=_samples(tmp_path, label=1)))
    assert all(torch.equal(value, detector.head.state_dict()[name]) for name, value in before.items())
    from backend.engine.anomaly.cancellation import AnomalyFitCancelled
    with pytest.raises(AnomalyFitCancelled):
        detector.fit(SimpleNamespace(dataset=_samples(tmp_path)), cancellation_requested=lambda: True)
    assert all(torch.equal(value, detector.head.state_dict()[name]) for name, value in before.items())


def test_native_inference_covers_more_than_256_patches_in_bounded_batches(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=False)
    detector.fit(SimpleNamespace(dataset=_samples(tmp_path)))
    detector.feature_extractor.seen_shapes.clear()
    x = torch.rand(1, 3, 288, 288)
    maps, scores = detector(x)
    assert maps.shape == (1, 288, 288) and scores.shape == (1,)
    assert torch.isfinite(maps).all() and torch.all((maps >= 0) & (maps <= 1))
    calls = detector.feature_extractor.seen_shapes
    assert sum(shape[0] for shape in calls) == 17 * 17
    assert max(shape[0] for shape in calls) <= 5
    assert all(shape[-2:] == (32, 32) for shape in calls)
    assert scores.item() == pytest.approx(maps.max().item())
    array, score = detector.predict_anomaly_map(x[0], out_size=(37, 41))
    assert array.shape == (37, 41) and score >= 0


def test_full_offline_state_roundtrip_keeps_geometry_normalization_provenance_and_scores(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=True)
    detector.fit(SimpleNamespace(dataset=_samples(tmp_path)))
    checkpoint = tmp_path / 'detector.pt'
    torch.save(detector.state_dict(), checkpoint)
    state = torch.load(checkpoint, weights_only=True)
    restored = _detector(monkeypatch, pretrained=False)
    restored.patch_size = 64
    restored.stride = 32
    restored.load_state_dict(state)
    assert (restored.patch_size, restored.stride, restored.inference_batch_size) == (32, 16, 5)
    assert restored.model_metadata['pretrained_sha256'] == 'a' * 64
    assert torch.equal(restored.model.input_mean, detector.model.input_mean)
    x = torch.rand(1, 3, 41, 45)
    a, b = detector(x), restored(x)
    assert torch.equal(a[0], b[0]) and torch.equal(a[1], b[1])
    bad = dict(state)
    bad['backbone_name'] = 'dinov3_vitb16'
    with pytest.raises(ValueError, match='backbone'):
        restored.load_state_dict(bad)


def test_heldout_normal_calibration_rejects_ng_and_training_overlap(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=False)
    source = _samples(tmp_path)
    with pytest.raises(ValueError, match='held.?out|overlap'):
        detector.fit(SimpleNamespace(dataset=source), calibration_dataset=source)
    other = tmp_path / 'holdout'
    other.mkdir()
    with pytest.raises(ValueError, match='normal|label'):
        detector.fit(SimpleNamespace(dataset=source), calibration_dataset=_samples(other, label=1))


def test_missing_pretrained_checkpoint_fails_before_random_fallback(tmp_path):
    module = _module('dino_synthetic')
    with pytest.raises(FileNotFoundError, match='Pretrained checkpoint'):
        module.DinoSyntheticDetector(pretrained_checkpoint=str(tmp_path / 'missing.safetensors'))


def test_unfitted_detector_refuses_random_head_inference(monkeypatch):
    detector = _detector(monkeypatch, pretrained=False)
    with pytest.raises(RuntimeError, match='fit|trained'):
        detector(torch.rand(1, 3, 32, 32))


def test_subset_keeps_only_selected_normal_source_and_empty_calibration_is_optional(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=False)
    source = _samples(tmp_path)
    source.samples[1] = (source.samples[1][0], 1, None)
    selected = torch.utils.data.Subset(source, [0])
    summary = detector.fit(selected, calibration_dataset=SimpleNamespace(samples=[]))
    assert summary['source_image_count'] == 1
    assert detector.fitted and detector.threshold == 0.5
    assert detector.calibration['method'] == 'uncalibrated'


def test_calibration_uses_heldout_image_maxima_and_strict_comparison_on_ties(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=False)
    source = _samples(tmp_path)
    heldout_path = tmp_path / 'heldout.png'
    Image.fromarray(np.full((49, 65, 3), 143, dtype=np.uint8)).save(heldout_path)
    heldout = SimpleNamespace(samples=[(heldout_path, 0, None)])
    monkeypatch.setattr(detector, '_native_maps', lambda x, cancellation_requested=None:
                        (torch.full((1, x.shape[-2], x.shape[-1]), 0.375), torch.tensor([0.375])))
    summary = detector.fit(SimpleNamespace(dataset=source), calibration_dataset=heldout)
    assert detector.threshold == 0.375
    assert detector.calibration['comparison'] == '>'
    assert detector.calibration['observed_image_fpr'] == 0
    assert detector.calibration['normal_image_count'] == 1
    assert summary['calibrated_threshold'] == 0.375


def test_calibration_rejects_a_training_image_copied_to_another_path(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=False)
    source = _samples(tmp_path)
    copied = tmp_path / 'copied.png'
    copied.write_bytes(source.samples[0][0].read_bytes())
    with pytest.raises(ValueError, match='content overlaps'):
        detector.fit(SimpleNamespace(dataset=source), calibration_dataset=SimpleNamespace(samples=[(copied, 0, None)]))


def test_native_small_and_ragged_images_replicate_pad_and_cover_all_pixels(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=False)
    detector.fit(SimpleNamespace(dataset=_samples(tmp_path)))
    seen = []
    monkeypatch.setattr(detector, '_logits', lambda batch:
                        (seen.append(batch.detach().cpu().clone()) or torch.zeros(batch.shape[0], device=batch.device)))
    small = torch.rand(1, 3, 19, 23)
    maps, scores = detector(small)
    assert maps.shape == (1, 19, 23) and maps.device.type == 'cpu'
    assert torch.all(maps == 0.5) and scores.item() == 0.5
    assert torch.equal(seen[0][0, :, :19, :23], small[0])
    assert torch.equal(seen[0][0, :, -1, -1], small[0, :, -1, -1])
    seen.clear()
    maps, _ = detector(torch.rand(2, 3, 53, 71))
    assert maps.shape == (2, 53, 71) and torch.all(maps == 0.5)
    assert sum(batch.shape[0] for batch in seen) == 2 * 3 * 4


@pytest.mark.parametrize('height,width,stride', [(10001, 10001, 16), (1100, 1100, 1)])
def test_native_inference_checks_work_budget_before_pixel_scan_or_grid_allocation(tmp_path, monkeypatch, height, width, stride):
    detector = _detector(monkeypatch, pretrained=False)
    detector.fit(SimpleNamespace(dataset=_samples(tmp_path)))
    detector.stride = stride
    # Expand is a zero-allocation view, so this test does not allocate a huge source.
    source = torch.zeros(1, 3, 1, 1).expand(1, 3, height, width)
    monkeypatch.setattr(detector, '_axis_positions', lambda *args: pytest.fail('guard must run before creating the grid'))
    with pytest.raises(ValueError, match='budget|limit'):
        detector(source)


def test_mid_fit_cancellation_stops_before_an_epoch_callback_and_prevents_inference(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=False)
    source = _samples(tmp_path)
    updates = []
    before = {name: value.clone() for name, value in detector.head.state_dict().items()}
    from backend.engine.anomaly.cancellation import AnomalyFitCancelled
    with pytest.raises(AnomalyFitCancelled):
        detector.fit(SimpleNamespace(dataset=source), progress_callback=lambda *args: updates.append(args),
                     cancellation_requested=lambda: any(not torch.equal(value, detector.head.state_dict()[name])
                                                        for name, value in before.items()))
    assert not updates and not detector.fitted
    with pytest.raises(RuntimeError, match='fit|trained'):
        detector(torch.rand(1, 3, 32, 32))


def test_heldout_empirical_threshold_has_at_most_one_percent_false_positives(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=False)
    source = _samples(tmp_path)
    heldout = []
    for index in range(200):
        path = tmp_path / f'heldout_{index:03}.png'
        Image.fromarray(np.full((33, 33, 3), index, dtype=np.uint8)).save(path)
        heldout.append((path, 0, None))
    scores = iter(index / 200 for index in range(200))
    monkeypatch.setattr(detector, '_native_maps', lambda x, cancellation_requested=None:
                        (torch.zeros(1, x.shape[-2], x.shape[-1]), torch.tensor([next(scores)])))
    detector.fit(SimpleNamespace(dataset=source), calibration_dataset=SimpleNamespace(samples=heldout))
    assert detector.threshold == pytest.approx(197 / 200)
    assert detector.calibration['observed_image_fpr'] == 0.01
    assert detector.calibration['normal_image_count'] == 200


def test_checkpoint_validation_is_atomic_for_bad_normalization_dtype(tmp_path, monkeypatch):
    detector = _detector(monkeypatch, pretrained=False)
    detector.fit(SimpleNamespace(dataset=_samples(tmp_path)))
    state = detector.state_dict()
    state['input_std'] = torch.ones_like(state['input_std'], dtype=torch.int64)
    # A valid tensor change must not be applied if another checkpoint field is invalid.
    key = next(iter(state['head_state_dict']))
    state['head_state_dict'][key].add_(1)
    original = {name: value.clone() for name, value in detector.head.state_dict().items()}
    with pytest.raises(ValueError, match='normalization'):
        detector.load_state_dict(state)
    assert all(torch.equal(value, detector.head.state_dict()[name]) for name, value in original.items())


@pytest.mark.parametrize('changed_source', ['training', 'calibration'])
def test_fit_rejects_changed_source_bytes_after_pinning_data_digest(tmp_path, monkeypatch, changed_source):
    detector = _detector(monkeypatch, pretrained=False)
    source = _samples(tmp_path)
    heldout_path = tmp_path / 'heldout.png'
    Image.fromarray(np.full((49, 65, 3), 143, dtype=np.uint8)).save(heldout_path)
    heldout = SimpleNamespace(samples=[(heldout_path, 0, None)])
    changed_path = source.samples[0][0] if changed_source == 'training' else heldout_path
    target_epoch = 1 if changed_source == 'training' else 2

    def change_after_epoch(epoch, *args):
        if epoch == target_epoch:
            Image.fromarray(np.full((73, 91, 3), 229, dtype=np.uint8)).save(changed_path)

    with pytest.raises(ValueError, match='changed|digest'):
        detector.fit(SimpleNamespace(dataset=source), calibration_dataset=heldout,
                     progress_callback=change_after_epoch)
    assert not detector.fitted
