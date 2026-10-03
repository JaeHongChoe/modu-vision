"""Source mask bytes bind immutable index revisions; originals are never rewritten."""
import hashlib
import json

import numpy as np
import pytest
from PIL import Image

from backend.engine.dataset_annotations import SourceAnnotationScanner
from backend.engine.dataset_index import DatasetIndex


def _pair(root, name='a', split='train', values=(0, 1), *, size=(16, 16), mode='L'):
    images = root / 'images' / split if split else root / 'images'
    masks = root / 'masks' / split if split else root / 'masks'
    images.mkdir(parents=True, exist_ok=True)
    masks.mkdir(parents=True, exist_ok=True)
    image, mask = images / f'{name}.png', masks / f'{name}.png'
    Image.new('RGB', size, 'white').save(image)
    pixels = np.zeros((size[1], size[0]), dtype=np.uint8)
    for number, value in enumerate(values):
        pixels[number, :] = value
    if mode == 'L':
        Image.fromarray(pixels).save(mask)
    else:
        Image.new(mode, size).save(mask)
    return image, mask


def _map(root, value):
    path = root / 'class_map.json'
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


def _snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def _build(tmp_path, source, policy='exclude', **options):
    index = DatasetIndex(tmp_path / 'registry' / 'index.sqlite3')
    receipt = index.build_revision('p', tmp_path / 'project', source, 'segmentation', policy, **options)
    return index, receipt, index.page('p', receipt.revision_id)['items']


@pytest.mark.parametrize('nested,split', [(False, ''), (False, 'train'), (True, 'val')])
def test_source_mask_and_class_map_are_recorded_in_the_production_index(tmp_path, nested, split):
    source = tmp_path / '검사 source'
    root = source / 'segmentation' if nested else source
    image, mask = _pair(root, split=split, values=(0, 3))
    mapping = _map(root, {'0': '배경', '3': '긁힘'})
    before = _snapshot(source)
    index, receipt, rows = _build(tmp_path, source)
    assert (receipt.image_count, receipt.valid_count, receipt.annotated, receipt.annotation_errors) == (1, 1, 1, 0)
    row = rows[0]
    assert row['relative_path'] == image.relative_to(source).as_posix()
    assert (row['annotation_format'], row['annotation_labels']) == ('mask', ['긁힘'])
    assert row['annotation_files'] == [
        {'path': path.relative_to(source).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in (mask, mapping)]
    assert index.page('p', receipt.revision_id, annotation_label='긁힘')['items'] == rows
    assert _snapshot(source) == before


def test_mask_and_class_map_changes_change_the_manifest_without_changing_older_revisions(tmp_path, monkeypatch):
    from backend.engine import dataset_index
    monkeypatch.setattr(dataset_index, '_STAT_CACHE_TRUSTED', True)
    source = tmp_path / 'source'
    image, mask = _pair(source)
    mapping = _map(source, {'0': 'background', '1': 'scratch', '2': 'dent'})
    index, first, rows = _build(tmp_path, source)
    Image.new('L', (16, 16), 2).save(mask)
    second = index.build_revision('p', tmp_path / 'project', source, 'segmentation', 'exclude')
    mapping.write_text(json.dumps({'0': 'background', '1': 'scratch', '2': 'crack'}))
    third = index.build_revision('p', tmp_path / 'project', source, 'segmentation', 'exclude')
    assert first.manifest_sha256 != second.manifest_sha256 != third.manifest_sha256
    assert second.reused_entries == third.reused_entries == 1
    assert index.page('p', first.revision_id)['items'][0]['annotation_labels'] == ['scratch']
    assert index.page('p', second.revision_id)['items'][0]['annotation_labels'] == ['dent']
    assert index.page('p', third.revision_id)['items'][0]['annotation_labels'] == ['crack']


@pytest.mark.parametrize('keep_flat_mask', [False, True])
def test_flat_images_bind_the_trainers_masks_train_folder_and_reject_changed_bad_masks(tmp_path, keep_flat_mask):
    from backend.engine.dataset_loaders import SegmentationDataset, mask_folder_layout
    source = tmp_path / 'source'
    image, flat_mask = _pair(source, split='')
    train_mask = source / 'masks' / 'train' / 'a.png'
    train_mask.parent.mkdir()
    Image.new('L', (16, 16), 2).save(train_mask)
    if not keep_flat_mask:
        flat_mask.unlink()
    _map(source, {'0': 'background', '1': 'scratch', '2': 'dent'})
    train_images, train_masks, _val_images, _val_masks = mask_folder_layout(source)
    assert SegmentationDataset(images_dir=train_images, masks_dir=train_masks).samples == [(image, train_mask)]
    before = _snapshot(source)
    index, first, rows = _build(tmp_path, source, 'reject')
    assert _snapshot(source) == before
    assert (first.state, first.annotated, first.annotation_errors) == ('prepared', 1, 0)
    assert rows[0]['annotation_labels'] == ['dent']
    assert rows[0]['annotation_files'][0] == {
        'path': 'masks/train/a.png', 'sha256': hashlib.sha256(train_mask.read_bytes()).hexdigest()}
    Image.new('RGB', (8, 8)).save(train_mask)
    changed = index.build_revision('p', tmp_path / 'project', source, 'segmentation', 'reject')
    assert changed.manifest_sha256 != first.manifest_sha256
    assert (changed.state, changed.valid_count, changed.annotation_errors) == ('rejected', 0, 1)
    assert index.page('p', changed.revision_id)['items'][0]['error_code'] == 'INVALID_ANNOTATION'
    assert index.page('p', first.revision_id)['items'][0]['annotation_labels'] == ['dent']


def test_binary_values_and_unnamed_classes_use_the_folder_trainers_class_names(tmp_path):
    source = tmp_path / 'source'
    first, _ = _pair(source, 'binary', values=(0, 255))
    scanner = SourceAnnotationScanner(source)
    assert scanner.scan(first, 'images/train/binary.png', (16, 16)).labels == ('defect',)
    second, _ = _pair(source, 'multi', split='val', values=(0, 2))
    scanner = SourceAnnotationScanner(source)
    assert scanner.scan(first, 'images/train/binary.png', (16, 16)).labels == ('class_1',)
    assert scanner.scan(second, 'images/val/multi.png', (16, 16)).labels == ('class_2',)


def test_a_background_only_mask_has_no_foreground_label_or_normal_truth(tmp_path):
    source = tmp_path / 'source'
    _pair(source, values=(0,))
    index, receipt, rows = _build(tmp_path, source)
    assert receipt.annotated == 1
    assert rows[0]['annotation_format'] == 'mask'
    assert rows[0]['annotation_labels'] == []
    assert rows[0]['label'] is None
    assert 'truth' not in rows[0] and 'is_normal' not in rows[0]


@pytest.mark.parametrize('problem', ['geometry', 'RGB', 'RGBA', 'P', 'I;16', 'corrupt'])
@pytest.mark.parametrize('policy', ['reject', 'exclude'])
def test_invalid_mask_geometry_or_channels_apply_the_index_policy(tmp_path, problem, policy):
    source = tmp_path / 'source'
    image, mask = _pair(source)
    if problem == 'geometry':
        Image.new('L', (8, 16), 1).save(mask)
    elif problem == 'corrupt':
        mask.write_bytes(b'not a png')
    else:
        Image.new(problem, (16, 16)).save(mask)
    before = _snapshot(source)
    index, receipt, rows = _build(tmp_path, source, policy)
    assert receipt.state == ('rejected' if policy == 'reject' else 'prepared')
    assert (receipt.valid_count, receipt.annotation_errors) == (0, 1)
    assert rows[0]['error_code'] == 'INVALID_ANNOTATION'
    assert rows[0]['annotation_format'] == 'mask'
    assert rows[0]['annotation_error'].startswith('INVALID_ANNOTATION:')
    assert _snapshot(source) == before


@pytest.mark.parametrize('mapping', [['scratch'], {'1': ' '}, {'1': 'scratch', '2': 'scratch'}, {'1': 'background'}, {'255': 'defect'}])
def test_an_unusable_class_map_is_recorded_as_a_mask_annotation_error(tmp_path, mapping):
    source = tmp_path / 'source'
    image, mask = _pair(source)
    classes = _map(source, mapping)
    found = SourceAnnotationScanner(source).scan(image, 'images/train/a.png', (16, 16))
    assert found.format == 'mask' and found.error.startswith('INVALID_ANNOTATION:')
    assert 'class_map.json' in found.error
    assert [path for path, digest in found.files] == ['masks/train/a.png', 'class_map.json']


@pytest.mark.parametrize('which', ['mask', 'mapping'])
def test_source_mask_files_linking_outside_are_refused_before_reading(tmp_path, which):
    source = tmp_path / 'source'
    image, mask = _pair(source)
    mapping = _map(source, {'0': 'background', '1': 'scratch'})
    target = mask if which == 'mask' else mapping
    outside = tmp_path / f'outside-{target.name}'
    target.rename(outside)
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip('links unavailable')
    found = SourceAnnotationScanner(source).scan(image, 'images/train/a.png', (16, 16))
    assert found.error.startswith('INVALID_ANNOTATION:') and 'links outside the source' in found.error
    assert str(tmp_path) not in found.error
    allowed = SourceAnnotationScanner(source, follow_links=True).scan(image, 'images/train/a.png', (16, 16))
    assert (allowed.format, allowed.labels, allowed.error) == ('mask', ('scratch',), None)


def test_existing_labelme_binding_keeps_precedence_over_a_mask(tmp_path):
    source = tmp_path / 'source'
    image, mask = _pair(source)
    image.with_suffix('.json').write_text(json.dumps({'shapes': [{'label': 'polygon'}]}))
    mask.write_bytes(b'broken mask')
    found = SourceAnnotationScanner(source).scan(image, 'images/train/a.png', (16, 16))
    assert (found.format, found.labels, found.error) == ('labelme', ('polygon',), None)


@pytest.mark.parametrize('task', ['classification', 'detection'])
def test_a_malformed_segmentation_mask_does_not_bind_other_tasks(tmp_path, task):
    source = tmp_path / 'source'
    image, mask = _pair(source)
    Image.new('RGB', (8, 8)).save(mask)  # decodes as an image, but is an invalid segmentation mask
    _map(source, ['invalid class map'])
    index = DatasetIndex(tmp_path / 'registry' / 'index.sqlite3')
    receipt = index.build_revision('p', tmp_path / 'project', source, task, 'reject')
    assert (receipt.state, receipt.annotation_errors, receipt.annotated) == ('prepared', 0, 0)
    # Classification retains its existing inventory: a PNG in masks/ is also a
    # folder-labelled image; detection excludes auxiliary mask folders.
    assert receipt.valid_count == (2 if task == 'classification' else 1)
    row = next(row for row in index.page('p', receipt.revision_id)['items'] if row['relative_path'] == 'images/train/a.png')
    assert row['annotation_format'] is None and row['annotation_files'] == []


@pytest.mark.parametrize('name', ['part.png', 'part_mask.png', 'part.mask.png'])
def test_folder_pairing_preserves_the_trainers_suffixes_and_double_extension_stems(tmp_path, name):
    source = tmp_path / 'source'
    image, mask = _pair(source, name='part')
    image.rename(image.with_name('part.jpg.jpg'))
    image = image.with_name('part.jpg.jpg')
    mask.rename(mask.with_name(name))
    found = SourceAnnotationScanner(source).scan(image, 'images/train/part.jpg.jpg', (16, 16))
    assert (found.format, found.labels, found.error) == ('mask', ('defect',), None)
    assert found.files[0][0] == f'masks/train/{name}'
