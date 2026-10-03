"""A mask-folder segmentation dataset without a saved split trains with its own classes.

The trainer used to assume background/defect for such a dataset, so a multi-class mask folder (the app's own
synthetic generator writes one, with class_map.json) failed at its first class id above 1. The classes now come from
class_map.json and the values of the masks paired with images; value 0 is always background, binary 0/255 masks stay
background/defect, and warm start checks a parent against the same list.
"""
import codecs
import json

import numpy as np
import pytest
from PIL import Image


def _loaders():
    from backend.engine import dataset_loaders  # imported here: the training test runs without the new helpers
    return dataset_loaders


def _mask(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.zeros((8, 8), dtype=np.uint8)
    for index, value in enumerate(values):
        pixels[index, :] = value
    Image.fromarray(pixels, mode='L').save(path)


def _pair(root, split, name, values):
    _mask(root / 'masks' / split / f'{name}.png', values)
    Image.new('RGB', (8, 8), 'gray').save((root / 'images' / split).joinpath(f'{name}.png') if (root / 'images' / split).is_dir()
                                          else _folder(root / 'images' / split) / f'{name}.png')


def _folder(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def _class_map(root, value):
    data = value if isinstance(value, bytes) else json.dumps(value).encode('utf-8')
    (root / 'class_map.json').write_bytes(data)


def test_classes_come_from_the_class_map_and_every_paired_mask_value_in_train_and_val(tmp_path):
    _pair(tmp_path, 'train', 'a', [0, 1, 3])
    _pair(tmp_path, 'val', 'b', [0, 5])  # a value only the validation masks use
    _class_map(tmp_path, {'0': 'bg', '1': ' bridge ', '2': 'trace', '3': 'ball'})
    assert _loaders().segmentation_folder_classes(tmp_path) == ['background', 'bridge', 'trace', 'ball', 'class_4', 'class_5']


def test_binary_masks_stay_background_and_defect_and_stray_files_are_not_classes(tmp_path):
    _pair(tmp_path, 'train', 'a', [0, 255])
    Image.new('RGB', (8, 8), (40, 200, 90)).save(tmp_path / 'masks' / 'train' / 'overlay.jpg')  # no image of that name
    (tmp_path / 'masks' / 'train' / 'empty.png').write_bytes(b'')
    assert _loaders().segmentation_folder_classes(tmp_path) == ['background', 'defect']
    _class_map(tmp_path, {'0': 'background'})
    assert _loaders().segmentation_folder_classes(tmp_path) == ['background', 'defect'], 'naming only value 0 keeps defect'
    _pair(tmp_path, 'train', 'b', [0, 2])
    assert _loaders().segmentation_folder_classes(tmp_path) == ['background', 'class_1', 'class_2']


@pytest.mark.parametrize('class_map, classes', [
    ({'background': 0, 'scratch': 1, 'dent': 2}, ['background', 'scratch', 'dent']),
    (codecs.BOM_UTF8 + json.dumps({'0': 'background', '1': '긁힘'}, ensure_ascii=False).encode('utf-8'), ['background', '긁힘']),
])
def test_a_name_to_value_map_and_a_byte_order_mark_are_read(tmp_path, class_map, classes):
    _pair(tmp_path, 'train', 'a', [0, 1])
    _class_map(tmp_path, class_map)
    assert _loaders().segmentation_folder_classes(tmp_path)[:len(classes)] == classes


@pytest.mark.parametrize('class_map, message', [
    (['background'], 'must map'),
    ({'x': 'a'}, "key 'x'"),
    ({'0': 'background', '300': 'far'}, 'invalid entry'),
    ({'1': ' '}, 'invalid entry'),
    ({'1': 'background', '2': 'scratch'}, 'more than once'),
    ({'1': 'scratch', '2': 'scratch'}, 'more than once'),
    (b'{"0": "background"', 'could not be read'),
])
def test_an_unusable_class_map_is_refused_naming_the_file(tmp_path, class_map, message):
    _pair(tmp_path, 'train', 'a', [0, 1])
    _class_map(tmp_path, class_map)
    with pytest.raises(ValueError, match=message):
        _loaders().segmentation_folder_classes(tmp_path)


def test_the_synthetic_generators_segmentation_dataset_trains_and_warm_start_reads_the_same_classes(tmp_path):
    from backend.engine.synthetic_generator import generate_synthetic_dataset
    from backend.engine.trainer import UnifiedAutoMLTrainer
    from backend.engine.warm_start import training_classes
    generate_synthetic_dataset(output_dir=tmp_path / 'data', num_samples=12, task='segmentation', image_size=(64, 64), seed=7)
    dataset = tmp_path / 'data' / 'segmentation'
    names = json.loads((dataset / 'class_map.json').read_text(encoding='utf-8'))
    output = tmp_path / 'models' / 'job_segmentation_classes'
    output.mkdir(parents=True)
    trained = UnifiedAutoMLTrainer(task='segmentation', dataset_path=str(dataset), output_dir=str(output), preset='fast', device='cpu',
                                   config_overrides={'pretrained': False, 'model_name': 'unet', 'epochs': 1, 'image_size': 32,
                                                     'batch_size': 2}).train(job_id=output.name)
    assert trained['status'] == 'completed', trained.get('error')
    meta = json.loads((output / 'model_meta.json').read_text(encoding='utf-8'))
    assert meta['classes'] == [names[str(index)] for index in range(len(names))]
    assert list(training_classes('segmentation', dataset)) == meta['classes'], 'warm start checks a parent against the trained classes'
