"""Layouts that real field exports arrive in, replayed on synthetic images.

* A LabelMe folder (image + .json side by side) chosen for a classification project is refused with the sentence
  the screen turns into "영역 분할로 바꾸고 가져오기 / 객체 검출로 바꾸고 가져오기"; the same folder imports as segmentation.
* ``._name.jpg`` resource forks copied from a macOS external drive are not images: the inventory and the training
  loaders both skip them instead of failing on the first unreadable file.
"""
import json

from fastapi.testclient import TestClient
from PIL import Image

from backend.engine.dataset_inventory import is_inventory_path
from backend.engine.dataset_loaders import ClassificationDataset, SegmentationDataset

TASK_SWITCH_SENTENCE = '영역 분할과 객체 검출을 학습할 수 있습니다'


def _labelme_folder(root):
    root.mkdir()
    for index in range(2):
        Image.new('RGB', (32, 32), (index, 0, 0)).save(root / f'part-{index}.jpg')
        (root / f'part-{index}.json').write_text(json.dumps({
            'imagePath': f'part-{index}.jpg', 'imageWidth': 32, 'imageHeight': 32,
            'shapes': [{'label': 'Scratch', 'shape_type': 'polygon', 'points': [[1, 1], [10, 1], [10, 10]]}]}))
    return root


def test_a_labelme_folder_is_refused_for_classification_with_the_task_switch_sentence(tmp_path):
    from backend.main import create_app
    source = _labelme_folder(tmp_path / 'NG')
    app = create_app(project_dir=str(tmp_path / 'projects'))
    api = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    created = api.post('/api/project/create', json={'name': 'Field NG', 'task': 'classification'})
    assert created.status_code == 200, created.text
    assert api.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    refused = api.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification'})
    assert refused.status_code == 422, refused.text
    assert TASK_SWITCH_SENTENCE in json.dumps(refused.json(), ensure_ascii=False)
    # The screen switches the project task and imports the same folder; nothing in the folder was renamed.
    assert api.put('/api/project/update', json={'task': 'segmentation'}).status_code == 200, 'task switch'
    imported = api.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'segmentation'})
    assert imported.status_code == 200, imported.text
    assert imported.json()['total_images'] == 2 and imported.json()['classes'] == {'Scratch': 2}
    assert sorted(p.name for p in source.iterdir()) == ['part-0.jpg', 'part-0.json', 'part-1.jpg', 'part-1.json']


def _resource_forks(folder):
    for image in sorted(folder.glob('*.jpg')) + sorted(folder.glob('*.png')):
        (folder / f'._{image.name}').write_bytes(b'\x00\x05\x16\x07resource fork, not an image')


def test_resource_fork_files_are_skipped_by_the_inventory_and_the_loaders(tmp_path):
    for label in ('OK', 'NG'):
        (tmp_path / 'cls' / label).mkdir(parents=True)
        for index in range(2):
            Image.new('RGB', (16, 16), (0, index, 0)).save(tmp_path / 'cls' / label / f'{label.lower()}-{index}.jpg')
        _resource_forks(tmp_path / 'cls' / label)
    assert not is_inventory_path(('OK', '._ok-0.jpg'), 'classification')
    assert is_inventory_path(('OK', 'ok-0.jpg'), 'classification')
    classification = ClassificationDataset(root_dir=tmp_path / 'cls')
    assert sorted(p.name for p, _ in classification.samples) == ['ng-0.jpg', 'ng-1.jpg', 'ok-0.jpg', 'ok-1.jpg']

    images, masks = tmp_path / 'seg' / 'images', tmp_path / 'seg' / 'masks'
    images.mkdir(parents=True), masks.mkdir()
    for index in range(2):
        Image.new('RGB', (16, 16), (0, 0, index)).save(images / f'shot-{index}.png')
        Image.new('L', (16, 16), 0).save(masks / f'shot-{index}.png')
    _resource_forks(images)
    segmentation = SegmentationDataset(images_dir=images, masks_dir=masks)
    assert sorted(p.name for p, _ in segmentation.samples) == ['shot-0.png', 'shot-1.png']
