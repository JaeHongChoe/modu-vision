"""Source COCO documents bind nested images consistently in import and index reads."""
import hashlib
import json

import pytest
from PIL import Image

from backend.engine.annotation_formats import source_annotations_for_image
from backend.engine.dataset_annotations import SourceAnnotationScanner


def _image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (16, 12), 'white').save(path)
    return path


def _document(path, *names, label='scratch'):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'images': [{'id': i, 'file_name': name, 'width': 16, 'height': 12} for i, name in enumerate(names, 1)],
        'categories': [{'id': 3, 'name': label}],
        'annotations': [{'id': i, 'image_id': i, 'category_id': 3, 'bbox': [1, 2, 4, 5]}
                        for i in range(1, len(names) + 1)],
    }
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


@pytest.mark.parametrize('image_name,document_name,declared_name', [
    ('제품 lot/images/a.png', '제품 lot/annotations.json', 'images/a.png'),
    ('제품 lot/images/a.png', '제품 lot/annotations.json', 'a.png'),
    ('제품 lot/images/a.png', '제품 lot/annotations.json', './images/a.png'),
    ('train/a.png', 'train/annotations.json', 'a.png'),
    ('제품 lot/images/train/a.png', '제품 lot/annotations_train.json', 'a.png'),
    ('images/a.png', 'images/annotations.json', 'a.png'),
])
def test_nested_document_paths_bind_in_import_and_saved_index(tmp_path, image_name, document_name, declared_name):
    from backend.engine.dataset_index import DatasetIndex, index_path
    source = tmp_path / '원본 공간'
    image = _image(source / image_name)
    document = _document(source / document_name, declared_name)
    before = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source.rglob('*') if p.is_file()}
    imported = source_annotations_for_image(source, image)
    assert imported is not None and imported[0]['bbox'] == [1, 2, 5, 7]
    assert imported[0]['label'] == 'scratch'
    found = SourceAnnotationScanner(source).scan(image, image_name, (16, 12))
    assert (found.format, found.labels, found.error) == ('coco', ('scratch',), None)
    assert found.files == ((document_name, before[document_name]),)
    index = DatasetIndex(index_path(tmp_path / 'registry'))
    receipt = index.build_revision('local:a', tmp_path / 'project', source, 'detection', 'exclude')
    assert receipt.annotated == 1 and receipt.annotation_errors == 0
    row = index.page('local:a', receipt.revision_id)['items'][0]
    assert row['annotation_labels'] == ['scratch'] and row['valid'] == 1
    assert row['annotation_files'] == [{'path': document_name, 'sha256': before[document_name]}]
    assert {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob('*') if p.is_file()} == before


def test_same_names_in_sibling_lots_keep_their_own_documents(tmp_path):
    source = tmp_path / 'source'
    for lot, label in [('lot-a', 'scratch'), ('lot-b', 'dent')]:
        image = _image(source / lot / 'images/a.png')
        _document(source / lot / 'annotations.json', 'images/a.png', label=label)
        assert source_annotations_for_image(source, image)[0]['label'] == label
        found = SourceAnnotationScanner(source).scan(image, f'{lot}/images/a.png', (16, 12))
        assert found.labels == (label,) and found.files[0][0] == f'{lot}/annotations.json'


@pytest.mark.parametrize('two_documents', [False, True])
def test_new_relative_aliases_never_choose_between_ambiguous_bindings(tmp_path, two_documents):
    source = tmp_path / 'source'
    image = _image(source / 'lot/images/a.png')
    _document(source / 'lot/annotations.json', *(['images/a.png'] if two_documents
                                               else ['images/a.png', 'lot/images/a.png']))
    if two_documents:
        _document(source / 'annotations.json', 'lot/images/a.png')
    with pytest.raises(ValueError, match='ambiguous|Multiple COCO'):
        source_annotations_for_image(source, image)
    found = SourceAnnotationScanner(source).scan(image, 'lot/images/a.png', (16, 12))
    assert found.error.startswith('AMBIGUOUS_ANNOTATION:')


@pytest.mark.parametrize('name', ['../images/a.png', '/images/a.png', 'C:/images/a.png', 'images\\a.png'])
def test_relative_document_support_still_refuses_unsafe_paths(tmp_path, name):
    source = tmp_path / 'source'
    image = _image(source / 'lot/images/a.png')
    _document(source / 'lot/annotations.json', name)
    with pytest.raises(ValueError, match='path|filename'):
        source_annotations_for_image(source, image)
    found = SourceAnnotationScanner(source).scan(image, 'lot/images/a.png', (16, 12))
    assert found.error.startswith('INVALID_ANNOTATION:')


def test_nested_coco_dimensions_are_checked_before_import_or_index_acceptance(tmp_path):
    source = tmp_path / 'source'
    image = _image(source / 'lot/images/a.png')
    document = _document(source / 'lot/annotations.json', 'images/a.png')
    payload = json.loads(document.read_text())
    payload['images'][0]['width'] = 17
    document.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='dimensions'):
        source_annotations_for_image(source, image)
    found = SourceAnnotationScanner(source).scan(image, 'lot/images/a.png', (16, 12))
    assert found.error.startswith('INVALID_ANNOTATION:') and 'differs' in found.error


@pytest.mark.parametrize('declared_name,first_name,second_name', [
    ('lot/images/a.png', 'lot/images/a.png', 'lot/lot/images/a.png'),
    ('a.png', 'lot/a.png', 'lot/images/a.png'),
])
def test_one_row_that_names_two_existing_images_is_ambiguous(tmp_path, declared_name, first_name, second_name):
    source = tmp_path / 'source'
    first = _image(source / first_name)
    second = _image(source / second_name)
    _document(source / 'lot/annotations.json', declared_name)
    for image in (first, second):
        with pytest.raises(ValueError, match='ambiguous'):
            source_annotations_for_image(source, image)
        found = SourceAnnotationScanner(source).scan(image, image.relative_to(source).as_posix(), (16, 12))
        assert found.error.startswith('AMBIGUOUS_ANNOTATION:')


def test_a_nested_coco_symlink_outside_the_source_is_rejected_before_reading(tmp_path, monkeypatch):
    from backend.engine import annotation_formats
    source = tmp_path / 'source'
    image = _image(source / 'lot/images/a.png')
    external = _document(tmp_path / 'outside/annotations.json', 'a.png', label='outside')
    document = image.parent / 'annotations.json'
    try:
        document.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip('Creating a file symlink is unavailable for this user')
    before = external.read_bytes()
    read = annotation_formats.read_source_text
    def no_external_read(path):
        assert path.resolve().is_relative_to(source.resolve()), 'external annotation bytes must not be read'
        return read(path)
    monkeypatch.setattr(annotation_formats, 'read_source_text', no_external_read)
    with pytest.raises(ValueError, match='outside the source'):
        source_annotations_for_image(source, image)
    found = SourceAnnotationScanner(source).scan(image, 'lot/images/a.png', (16, 12))
    assert found.error.startswith('INVALID_ANNOTATION:') and 'outside the source' in found.error
    assert external.read_bytes() == before


def test_internal_symlink_aliases_of_the_same_image_are_not_ambiguous(tmp_path):
    source = tmp_path / 'source'
    image = _image(source / 'lot/images/a.png')
    alias = source / 'images/a.png'
    alias.parent.mkdir()
    try:
        alias.symlink_to(image)
    except (OSError, NotImplementedError):
        pytest.skip('Creating a file symlink is unavailable for this user')
    _document(source / 'lot/annotations.json', 'images/a.png')
    assert source_annotations_for_image(source, image)[0]['label'] == 'scratch'
    found = SourceAnnotationScanner(source).scan(image, 'lot/images/a.png', (16, 12))
    assert found.error is None and found.labels == ('scratch',)
