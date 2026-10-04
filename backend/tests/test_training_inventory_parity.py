"""Unsaved classification discovery follows the accepted inventory's path rules."""
from pathlib import Path

import pytest
from PIL import Image

from backend.engine.dataset_index import DatasetIndex
from backend.engine.dataset_loaders import ClassificationDataset


def image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (8, 8), (20, 30, 40)).save(path)


@pytest.mark.parametrize('split', [None, 'train'])
def test_unsaved_classification_matches_index_eligible_paths(tmp_path, split):
    source = tmp_path / 'source'
    prefix = source if split is None else source / 'train'
    image(prefix / 'OK' / 'visible.png')
    image(prefix / 'OK' / '.hidden.png')
    image(prefix / '.hidden_class' / 'hidden_parent.png')
    image(prefix / '@eaDir' / 'thumbnail.png')
    (prefix / 'OK' / 'unsupported.txt').write_text('not an image', encoding='utf-8')
    index = DatasetIndex(tmp_path / 'index.db')
    receipt = index.build_revision('project', tmp_path / 'project', source, 'classification')
    assert receipt.image_count == 1
    dataset = ClassificationDataset(source, split=split, ignore_saved_split=True)
    assert [path.relative_to(source).as_posix() for path, _ in dataset.samples] == [
        ('train/' if split else '') + 'OK/visible.png'
    ]
    assert dataset.classes == ['OK']


def test_saved_split_discovery_contract_is_preserved(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    visible = source / 'OK' / 'visible.png'
    hidden = source / 'OK' / '.hidden.png'
    image(visible)
    image(hidden)
    # Reader supplies an explicit saved assignment. Discovery must still validate
    # exactly that assignment rather than silently turning it into unsaved policy.
    monkeypatch.setattr('backend.engine.dataset_loaders._classification_split_assignments',
                        lambda root: {str(visible): 'train', str(hidden): 'val'})
    dataset = ClassificationDataset(source, split='val')
    assert dataset.split_basis == 'saved_manifest'
    assert [path for path, _ in dataset.samples] == [hidden]


def test_invalid_image_still_has_explicit_inventory_receipt(tmp_path):
    source = tmp_path / 'source'
    image(source / 'OK' / 'visible.png')
    (source / 'OK' / 'corrupt.png').write_bytes(b'corrupt')
    receipt = DatasetIndex(tmp_path / 'index.db').build_revision(
        'project', tmp_path / 'project', source, 'classification')
    assert receipt.image_count == 2
    assert receipt.valid_count == 1
    assert receipt.error_count == 1
    # Path eligibility is separate from full decode validation. This fix does
    # not pretend a folder loader is an immutable validated-revision reader.
    assert {path.name for path, _ in ClassificationDataset(source).samples} == {'visible.png', 'corrupt.png'}


def test_explicit_anomaly_roots_match_inventory_eligible_paths(tmp_path):
    from backend.engine.dataset_loaders import AnomalyDataset
    normal = tmp_path / 'source' / 'good'
    for n in range(10):
        image(normal / f'{n}.png')
    image(normal / '.hidden.png')
    image(normal / '.hidden_parent' / 'nested.png')
    image(normal / '@eaDir' / 'thumbnail.png')
    receipt = DatasetIndex(tmp_path / 'index.db').build_revision(
        'project', tmp_path / 'project', normal.parent, 'anomaly')
    assert receipt.image_count == 10
    collected = {path for split in ('train', 'val', 'test')
                 for path, _, _ in AnomalyDataset(normal_dir=normal, split=split).samples}
    assert collected == {normal / f'{n}.png' for n in range(10)}


def test_unreadable_eligible_image_does_not_silently_disappear(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    path = source / 'OK' / 'visible.png'
    image(path)
    dataset = ClassificationDataset(source)
    assert [p for p, _ in dataset.samples] == [path]
    def denied_read(candidate):
        raise PermissionError('fixture unreadable image')
    monkeypatch.setattr('backend.engine.dataset_loaders._read_image_rgb', denied_read)
    with pytest.raises(PermissionError, match='fixture unreadable image'):
        dataset[0]
