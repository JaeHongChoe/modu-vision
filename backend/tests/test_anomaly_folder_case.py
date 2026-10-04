"""An anomaly layout's fixed folder names (OK, NG, fail, train, test, val, ground_truth, test_crop_output, ...) are read
whatever their letter case, so a case-sensitive file system (Linux, a case-sensitive Windows folder) reads a layout as
macOS and Windows do by default. This computer's disk may be case-insensitive, so ``case_sensitive`` makes Path.is_dir
and Path.exists answer as a case-sensitive one does: a path exists only when every part is spelled as its folder lists
it (on a case-sensitive disk it changes nothing). Several layouts here come from the independent review's probes."""
import os
from pathlib import Path

import pytest
from PIL import Image

from backend.engine import dataset_loaders
from backend.engine.dataset_loaders import AnomalyDataset
from backend.engine.industrial_adapters import FlexibleAnomalyDataset


@pytest.fixture
def case_sensitive(monkeypatch):
    real_is_dir, real_exists = Path.is_dir, Path.exists

    def spelled(path):
        path = Path(os.path.abspath(path))
        return all(part.name in os.listdir(part.parent) for part in (path, *path.parents) if part.parent != part)

    monkeypatch.setattr(Path, 'is_dir', lambda self, *args, **kwargs: real_is_dir(self, *args, **kwargs) and spelled(self))
    monkeypatch.setattr(Path, 'exists', lambda self, *args, **kwargs: real_exists(self, *args, **kwargs) and spelled(self))


@pytest.fixture
def looked(monkeypatch):
    """The fixed names looked up, in order (the loaders call dataset_loaders.named_child_dir at run time)."""
    names = []
    real = dataset_loaders.named_child_dir
    monkeypatch.setattr(dataset_loaders, 'named_child_dir', lambda root, name: names.append(name) or real(root, name))
    return names


def images(folder, count, color, prefix=None):
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        Image.new('RGB', (16, 16), color).save(folder / f'{prefix or folder.name}_{index}.png')


def labels(dataset_type, root, splits=('val', 'test')):
    return [label for split in splits for _, label, _ in dataset_type(root_dir=root, split=split).samples]


# ------------------------------------------------------------------------------------------------ layouts

def test_a_root_with_lowercase_ok_and_ng_folders_is_read_on_a_case_sensitive_file_system(tmp_path, case_sensitive):
    images(tmp_path / 'ok', 10, 'white')
    images(tmp_path / 'ng', 6, 'black')
    train = AnomalyDataset(root_dir=tmp_path, split='train')
    assert train.samples and all(label == 0 and path.parent.name == 'ok' for path, label, _ in train.samples)
    assert set(labels(AnomalyDataset, tmp_path)) == {0, 1}, 'the evaluation splits read both the normal and the defect folder'
    flexible = FlexibleAnomalyDataset(root_dir=tmp_path, split='train')
    assert flexible.samples and all(path.parent.name == 'ok' for path, *_ in flexible.samples)


def test_named_splits_with_capitalised_names_are_read(tmp_path, case_sensitive):
    images(tmp_path / 'Train' / 'good', 6, 'white')
    images(tmp_path / 'Test' / 'good', 2, 'white')
    images(tmp_path / 'Test' / 'crack', 2, 'black')
    images(tmp_path / 'VAL' / 'good', 1, 'white')
    images(tmp_path / 'VAL' / 'crack', 1, 'black')
    assert len(AnomalyDataset(root_dir=tmp_path, split='train').samples) == 6
    test = AnomalyDataset(root_dir=tmp_path, split='test')
    assert sorted(label for _, label, _ in test.samples) == [0, 0, 1, 1], 'named test and val splits: the test split as it is'
    assert sorted(label for _, label, _ in AnomalyDataset(root_dir=tmp_path, split='val').samples) == [0, 1]


@pytest.mark.parametrize('held_out', ['Test', 'VAL'])
def test_a_capitalised_held_out_split_alone_keeps_every_train_normal(tmp_path, case_sensitive, held_out):
    images(tmp_path / 'train' / 'good', 6, 'white')
    images(tmp_path / held_out / 'good', 2, 'white')
    images(tmp_path / held_out / 'crack', 2, 'black')
    assert len(AnomalyDataset(root_dir=tmp_path, split='train').samples) == 6, 'a held-out split exists: train/ is not partitioned'


def test_capitalised_ground_truth_masks_are_found(tmp_path, case_sensitive):
    images(tmp_path / 'train' / 'good', 4, 'white')
    images(tmp_path / 'test' / 'good', 2, 'white')
    images(tmp_path / 'test' / 'crack', 2, 'black')
    (tmp_path / 'Ground_Truth' / 'crack').mkdir(parents=True)
    for index in range(2):
        Image.new('L', (16, 16), 255).save(tmp_path / 'Ground_Truth' / 'crack' / f'crack_{index}_mask.png')
    masks = [mask for split in ('val', 'test') for _, label, mask in AnomalyDataset(root_dir=tmp_path, split=split).samples if label == 1]
    assert len(masks) == 2 and all(mask is not None and mask.parent.parent.name == 'Ground_Truth' for mask in masks)


def test_capitalised_fail_folder_is_the_defect_folder(tmp_path, case_sensitive):
    images(tmp_path / 'ok', 10, 'white')
    images(tmp_path / 'Fail', 4, 'black')
    assert labels(AnomalyDataset, tmp_path).count(1) == 4
    assert labels(FlexibleAnomalyDataset, tmp_path).count(1) == 4


def test_capitalised_crop_folder_is_read(tmp_path, case_sensitive):
    images(tmp_path / 'Test_Crop_Output', 10, 'white')
    assert len(AnomalyDataset(root_dir=tmp_path, split='train').samples) == 8
    assert len(AnomalyDataset(root_dir=tmp_path, split='val').samples) == 1
    assert len(FlexibleAnomalyDataset(root_dir=tmp_path, split='train').samples) == 8


def test_crop_folder_masks_stay_out_whatever_the_folder_spelling(tmp_path):
    """This computer's own disk, no emulation: a crop folder's mask_* files are never normal images."""
    images(tmp_path / 'Test_Crop_Output', 10, 'white', prefix='crop')
    for index in range(3):
        Image.new('RGB', (16, 16), 'black').save(tmp_path / 'Test_Crop_Output' / f'mask_crop_{index}.png')
    train = AnomalyDataset(root_dir=tmp_path, split='train').samples
    assert not [path for path, _, _ in train if path.name.startswith('mask_')], 'mask files are not normal images'
    assert len(train) == 8


def test_evaluation_fallback_on_a_capitalised_train_folder_reads_it_as_a_train_folder(tmp_path, case_sensitive):
    images(tmp_path / 'Train' / 'good', 10, 'white')
    Image.new('RGB', (16, 16), 'white').save(tmp_path / 'Train' / 'stray.png')  # a train folder's loose files are not read
    held_out = [path for split in ('val', 'test') for path, _, _ in AnomalyDataset(root_dir=tmp_path, split=split).samples]
    assert len(held_out) == 2 and all(path.parent.name == 'good' for path in held_out)


def test_flexible_capitalised_train_good_with_test_hands_off(tmp_path, case_sensitive):
    images(tmp_path / 'Train' / 'Good', 6, 'white')
    images(tmp_path / 'Test' / 'good', 2, 'white')
    images(tmp_path / 'Test' / 'crack', 2, 'black')
    assert len(FlexibleAnomalyDataset(root_dir=tmp_path, split='train').samples) == 6
    assert sorted(labels(FlexibleAnomalyDataset, tmp_path)) == [0, 0, 1, 1]


def test_flexible_capitalised_train_good_without_test_is_the_normal_folder(tmp_path, case_sensitive):
    images(tmp_path / 'TRAIN' / 'GOOD', 10, 'white')
    images(tmp_path / 'ng', 4, 'black')
    assert len(FlexibleAnomalyDataset(root_dir=tmp_path, split='train').samples) == 8
    assert sorted(set(labels(FlexibleAnomalyDataset, tmp_path))) == [0, 1]


def test_flexible_lowercase_scan_anomalies(tmp_path, case_sensitive):
    images(tmp_path / 'ok', 10, 'white')
    images(tmp_path / 'Scan_Anomalies', 4, 'black')
    assert sum(labels(FlexibleAnomalyDataset, tmp_path)) == 4


def test_a_file_named_like_a_folder_is_not_a_match(tmp_path, case_sensitive):
    images(tmp_path / 'OK', 10, 'white')
    if not os.path.exists(tmp_path / 'ok'):  # a case-folding disk cannot hold both
        (tmp_path / 'ok').write_bytes(b'not a folder')
    assert dataset_loaders.named_child_dir(tmp_path, 'OK') == tmp_path / 'OK'
    assert len(AnomalyDataset(root_dir=tmp_path, split='train').samples) == 8


def test_a_train_file_is_not_a_train_folder(tmp_path, case_sensitive):
    images(tmp_path / 'ok', 10, 'white')
    (tmp_path / 'train').write_bytes(b'a file, not a folder')
    assert len(AnomalyDataset(root_dir=tmp_path, split='train').samples) == 8


# ------------------------------------------------------------------------------------------------ the lookup

def test_a_folder_is_returned_as_spelled_on_disk_and_missing_ones_as_asked(tmp_path, case_sensitive):
    (tmp_path / 'ok').mkdir()
    assert dataset_loaders.named_child_dir(tmp_path, 'OK') == tmp_path / 'ok'
    assert dataset_loaders.named_child_dir(tmp_path, 'NG') == tmp_path / 'NG', 'no such folder: the name as given'
    assert dataset_loaders.named_child_dir(tmp_path / 'missing', 'OK') == tmp_path / 'missing' / 'OK'


def test_two_spellings_side_by_side_are_refused_on_any_file_system(tmp_path, monkeypatch):
    real_iterdir = Path.iterdir
    monkeypatch.setattr(Path, 'iterdir', lambda self: iter([self / 'OK', self / 'ok', self / 'ng']) if self == tmp_path else real_iterdir(self))
    monkeypatch.setattr(Path, 'is_dir', lambda self, *args, **kwargs: True)
    with pytest.raises(ValueError, match='letter case: OK, ok'):
        dataset_loaders.named_child_dir(tmp_path, 'ok')
    assert dataset_loaders.named_child_dir(tmp_path, 'NG') == tmp_path / 'ng'


def test_one_construction_lists_a_root_once_and_the_next_sees_a_folder_added_since(tmp_path, monkeypatch):
    """Review anc2 N-P2: a cache across constructions kept a listing when the folder's timestamp did not move (a coarse
    file system clock), so a dataset rebuilt after ng/ was added saw no defects."""
    images(tmp_path / 'ok', 10, 'white')
    listings = []
    real_iterdir = Path.iterdir
    monkeypatch.setattr(Path, 'iterdir', lambda self: listings.append(self) or real_iterdir(self))
    assert labels(AnomalyDataset, tmp_path, ('val',)) == [0]
    assert listings.count(tmp_path) == 1, 'every fixed name of one construction from one listing of the root'
    stat = os.stat(tmp_path)
    images(tmp_path / 'NG', 4, 'black')
    os.utime(tmp_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # as if the clock had not moved
    assert labels(AnomalyDataset, tmp_path).count(1) == 4, 'the next construction sees the folder added since'
    with dataset_loaders.folder_listings():
        first = dataset_loaders.named_child_dir(tmp_path, 'fail')
        (tmp_path / 'fail').mkdir()
        assert dataset_loaders.named_child_dir(tmp_path, 'fail') == first, 'inside one construction the listing holds'
    assert dataset_loaders.named_child_dir(tmp_path, 'FAIL') == tmp_path / 'fail', 'outside it, every lookup lists afresh'


# ------------------------------------------------------------------------------------------------ laziness

def test_a_folder_the_layout_does_not_read_is_never_looked_up(tmp_path, looked):
    images(tmp_path / 'train' / 'good', 6, 'white')
    assert len(AnomalyDataset(root_dir=tmp_path, split='train').samples) == 4
    assert 'OK' not in looked and 'test_crop_output' not in looked, 'train/ is present: OK and the crop folder are not read'


def test_evaluation_lookups_are_lazy(tmp_path, looked):
    images(tmp_path / 'OK', 10, 'white')
    images(tmp_path / 'fail', 4, 'black')
    AnomalyDataset(root_dir=tmp_path, split='val')
    assert 'train' not in looked and 'NG' not in looked, looked


def test_the_ok_ng_layout_never_looks_up_ground_truth(tmp_path, looked):
    images(tmp_path / 'OK', 10, 'white')
    images(tmp_path / 'NG', 4, 'black')
    AnomalyDataset(root_dir=tmp_path, split='val')
    assert 'ground_truth' not in looked, looked


def test_the_flexible_train_split_never_looks_up_defect_folders(tmp_path, looked):
    images(tmp_path / 'OK', 10, 'white')
    FlexibleAnomalyDataset(root_dir=tmp_path, split='train')
    assert not {'fail', 'NG', 'scan_anomalies'} & set(looked), looked


def test_the_flexible_hand_off_looks_up_no_defect_folder(tmp_path, looked):
    """Review anc2 P3: before the hand-off to AnomalyDataset the flexible loader looked up fail, NG and scan_anomalies."""
    images(tmp_path / 'train' / 'good', 6, 'white')
    images(tmp_path / 'test' / 'good', 2, 'white')
    images(tmp_path / 'test' / 'crack', 2, 'black')
    assert sorted(labels(FlexibleAnomalyDataset, tmp_path, ('val',)) + labels(FlexibleAnomalyDataset, tmp_path, ('test',))) == [0, 0, 1, 1]
    assert not {'fail', 'NG', 'scan_anomalies'} & set(looked), looked
