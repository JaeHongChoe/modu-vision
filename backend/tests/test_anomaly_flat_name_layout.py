"""A field export with nomaly*/anomaly* files lying flat in train/ and validation/: the anomaly loader trains on the
normal-named files only and reads the validation folder's defects from their names, instead of treating every train
file as normal."""
from pathlib import Path

from PIL import Image

from backend.engine.dataset_loaders import AnomalyDataset, name_says_normal


def _layout(root: Path):
    for folder, names in {'train': ['nomaly_001.jpg', 'nomaly_002.jpg', 'anomaly001.jpg'],
                          'validation': ['nomaly_101.jpg', 'anomaly101.jpg', 'anomaly102.jpg']}.items():
        (root / folder).mkdir(parents=True)
        for name in names:
            Image.new('RGB', (16, 16), (90, 90, 90)).save(root / folder / name)


def test_file_names_split_a_flat_export_into_normal_and_defect(tmp_path):
    _layout(tmp_path)
    train = AnomalyDataset(root_dir=tmp_path, split='train')
    assert sorted(p.name for p, label, _ in train.samples) == ['nomaly_001.jpg', 'nomaly_002.jpg']
    assert {label for _, label, _ in train.samples} == {0}
    # One evaluation folder is shared between val and test; together they hold every file with the label its name says.
    held_out = AnomalyDataset(root_dir=tmp_path, split='val').samples + AnomalyDataset(root_dir=tmp_path, split='test').samples
    assert sorted((p.name, label) for p, label, _ in held_out) == [('anomaly101.jpg', 1), ('anomaly102.jpg', 1), ('nomaly_101.jpg', 0)]


def test_names_that_say_nothing_are_left_to_the_folder_rules():
    assert name_says_normal('IMG_0001') is None
    assert name_says_normal('OK_0001') is True
    assert name_says_normal('NG-0001') is False
