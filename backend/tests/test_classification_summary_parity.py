"""Import/readiness counts must describe the default classification loader's eligible cohort."""
import pytest
from PIL import Image

from backend.engine.dataset_loaders import ClassificationDataset, inspect_dataset


def image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (8, 8), (20, 30, 40)).save(path)


def test_folder_summary_excludes_hidden_thumbnail_and_unused_paths(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    eligible = {'train': ['OK/a.png', 'OK/b.png', 'NG/c.png'],
                'val': ['OK/d.png', 'NG/e.png'], 'test': ['NG/f.png']}
    for split, names in eligible.items():
        for name in names:
            image(source / split / name)
        image(source / split / 'OK' / '.hidden.png')
        image(source / split / '@eaDir' / 'thumbnail.png')
    unused = source / 'train' / 'NG' / 'unused.png'
    image(unused)
    monkeypatch.setattr('backend.engine.dataset_usage.unused_image_paths', lambda folder: {str(unused.resolve())})
    summary = inspect_dataset(source, 'classification')
    assert summary.total_images == 6
    assert summary.classes == {'OK': 3, 'NG': 3}
    assert summary.split_counts == {'train': 3, 'val': 2, 'test': 1}
    for split, names in eligible.items():
        loader = ClassificationDataset(source, split=split)
        assert {path.relative_to(source / split).as_posix() for path, _ in loader.samples} == set(names)


@pytest.mark.parametrize('count,expected', [(1, {'train': 2, 'val': 0}), (2, {'train': 2, 'val': 2})])
def test_automatic_summary_matches_per_class_small_cohort_split(tmp_path, count, expected):
    for name in ('OK', 'NG'):
        for index in range(count):
            image(tmp_path / name / f'{index}.png')
    summary = inspect_dataset(tmp_path, 'classification')
    assert summary.total_images == count * 2
    assert summary.split_counts == expected
    train = ClassificationDataset(tmp_path, split='train')
    val = ClassificationDataset(tmp_path, split='val')
    assert len(train) == expected['train'] and len(val) == expected['val']
    assert {p for p, _ in train.samples}.isdisjoint({p for p, _ in val.samples})


def test_validation_test_only_folders_do_not_invent_training_images(tmp_path):
    image(tmp_path / 'val' / 'OK' / 'a.png')
    image(tmp_path / 'test' / 'NG' / 'b.png')
    summary = inspect_dataset(tmp_path, 'classification')
    assert summary.total_images == 2
    assert summary.classes == {'OK': 1, 'NG': 1}
    assert summary.split_counts == {'train': 0, 'val': 1, 'test': 1}
    assert len(ClassificationDataset(tmp_path, split='train')) == 0


def test_explicit_saved_assignments_keep_their_existing_hidden_image_contract(tmp_path, monkeypatch):
    a, b = tmp_path / 'OK' / 'visible.png', tmp_path / 'OK' / '.hidden.png'
    image(a)
    image(b)
    monkeypatch.setattr('backend.engine.dataset_loaders._classification_split_assignments',
                        lambda folder: {str(a): 'train', str(b): 'test'})
    summary = inspect_dataset(tmp_path, 'classification')
    assert summary.total_images == 2
    assert summary.split_counts == {'train': 1, 'val': 0, 'test': 1}
