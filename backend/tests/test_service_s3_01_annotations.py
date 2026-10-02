"""S3-01 slice 4: source annotations recorded per image from the source's own files (read once per document)."""
import hashlib
import json

from PIL import Image

from backend.engine.dataset_annotations import SourceAnnotationScanner


def _image(path, size=(16, 16)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', size, 'white').save(path)
    return path


def test_labelme_coco_and_yolo_labels_with_their_file_hashes(tmp_path):
    source = tmp_path / 'source'
    labelme = _image(source / 'lm' / 'a.png')
    labelme.with_suffix('.json').write_text(json.dumps({'shapes': [{'label': 'scratch'}, {'label': 'dent'}, {'label': 'scratch'}]}))
    coco = _image(source / 'images' / 'b.png')
    (source / 'annotations.json').write_text(json.dumps({'images': [{'id': 1, 'file_name': 'images/b.png', 'width': 16, 'height': 16}],
                                                         'categories': [{'id': 3, 'name': 'crack'}],
                                                         'annotations': [{'id': 1, 'image_id': 1, 'category_id': 3, 'bbox': [1, 1, 4, 4]}]}))
    yolo = _image(source / 'yolo' / 'images' / 'c.png')
    (source / 'yolo' / 'labels').mkdir(parents=True)
    (source / 'yolo' / 'labels' / 'c.txt').write_text('1 0.5 0.5 0.2 0.2\n0 0.1 0.1 0.1 0.1\n')
    (source / 'yolo' / 'classes.txt').write_text('ok\nng\n')
    scanner = SourceAnnotationScanner(source)
    first = scanner.scan(labelme, 'lm/a.png')
    assert (first.format, first.labels, first.error) == ('labelme', ('dent', 'scratch'), None)
    assert first.files == (('lm/a.json', hashlib.sha256(labelme.with_suffix('.json').read_bytes()).hexdigest()),)
    second = scanner.scan(coco, 'images/b.png')
    assert (second.format, second.labels, second.files[0][0]) == ('coco', ('crack',), 'annotations.json')
    third = scanner.scan(yolo, 'yolo/images/c.png')
    assert (third.format, third.labels) == ('yolo', ('ng', 'ok')) and [name for name, _ in third.files] == ['yolo/labels/c.txt', 'yolo/classes.txt']
    assert scanner.scan(_image(source / 'plain' / 'd.png'), 'plain/d.png').format is None


def test_a_coco_document_is_parsed_once_for_every_image(tmp_path, monkeypatch):
    from backend.engine import dataset_annotations
    source = tmp_path / 'source'
    names = [f'images/{index}.png' for index in range(50)]
    for name in names:
        _image(source / name)
    (source / 'annotations.json').write_text(json.dumps({
        'images': [{'id': index, 'file_name': name, 'width': 16, 'height': 16} for index, name in enumerate(names)],
        'categories': [{'id': 1, 'name': 'defect'}], 'annotations': [{'id': 1, 'image_id': 7, 'category_id': 1, 'bbox': [0, 0, 1, 1]}]}))
    loads = []
    real = dataset_annotations.json.loads
    monkeypatch.setattr(dataset_annotations.json, 'loads', lambda text, *args, **kwargs: loads.append(1) or real(text, *args, **kwargs))
    scanner = SourceAnnotationScanner(source)
    results = [scanner.scan(source / name, name) for name in names]
    assert len(loads) == 1 and results[7].labels == ('defect',) and results[0].labels == ()


def test_ambiguous_and_invalid_annotations_are_explicit_errors(tmp_path):
    source = tmp_path / 'source'
    image = _image(source / 'train' / 'x.png')
    document = {'images': [{'id': 1, 'file_name': 'train/x.png', 'width': 16, 'height': 16}], 'categories': [{'id': 1, 'name': 'a'}], 'annotations': []}
    (source / 'annotations.json').write_text(json.dumps(document))
    (source / 'annotations_train.json').write_text(json.dumps(document))
    assert SourceAnnotationScanner(source).scan(image, 'train/x.png').error.startswith('AMBIGUOUS_ANNOTATION')
    other = tmp_path / 'other'
    twice = _image(other / 'y.png')
    (other / 'annotations.json').write_text(json.dumps({**document, 'images': [{'id': 1, 'file_name': 'y.png'}, {'id': 2, 'file_name': 'y.png'}]}))
    assert SourceAnnotationScanner(other).scan(twice, 'y.png').error.startswith('AMBIGUOUS_ANNOTATION')
    yolo = tmp_path / 'yolo'
    target = _image(yolo / 'z.png')
    target.with_suffix('.txt').write_text('5 0.5 0.5 0.1 0.1\n')
    (yolo / 'classes.txt').write_text('ok\n')
    assert SourceAnnotationScanner(yolo).scan(target, 'z.png').error.startswith('INVALID_ANNOTATION')
    broken = tmp_path / 'broken'
    bad = _image(broken / 'w.png')
    bad.with_suffix('.json').write_text('{not json')
    assert SourceAnnotationScanner(broken).scan(bad, 'w.png').error.startswith('INVALID_ANNOTATION')


def test_scanning_only_reads(tmp_path):
    source = tmp_path / 'source'
    image = _image(source / 'lm' / 'a.png')
    image.with_suffix('.json').write_text(json.dumps({'shapes': [{'label': 'scratch'}]}))
    before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    SourceAnnotationScanner(source).scan(image, 'lm/a.png')
    assert {p: p.read_bytes() for p in source.rglob('*') if p.is_file()} == before


def test_an_annotation_file_linking_out_of_the_source_is_not_read(tmp_path):
    import pytest
    outside = tmp_path / 'other_project' / 'secret.json'
    outside.parent.mkdir(parents=True)
    outside.write_text(json.dumps({'shapes': [{'label': 'other-project-label'}]}))
    source = tmp_path / 'source'
    image = _image(source / 'lm' / 'a.png')
    try:
        image.with_suffix('.json').symlink_to(outside)
    except OSError:
        pytest.skip('links are not available here')
    found = SourceAnnotationScanner(source).scan(image, 'lm/a.png')
    assert found.labels == () and found.error.startswith('INVALID_ANNOTATION') and 'links outside the source' in found.error
    assert str(tmp_path) not in found.error and all(not path.startswith('/') for path, _ in found.files)
    followed = SourceAnnotationScanner(source, follow_links=True).scan(image, 'lm/a.png')
    assert followed.labels == ('other-project-label',) and followed.files[0][0] == 'lm/a.json', 'a local desktop may follow links'


def _yolo(source, label_line='0 0.5 0.5 0.1 0.1\n'):
    image = _image(source / 'lineA' / 'images' / 'p.png')
    (source / 'lineA' / 'labels').mkdir(parents=True, exist_ok=True)
    (source / 'lineA' / 'labels' / 'p.txt').write_text(label_line)
    return image


def test_the_recorded_class_file_is_the_one_the_names_came_from(tmp_path):
    source = tmp_path / 'source'
    image = _yolo(source)
    (source / 'lineA' / 'classes.txt').write_text('ng\nok\n')
    (source / 'data.yaml').write_text('names: [ng, ok]\n')
    agreed = SourceAnnotationScanner(source).scan(image, 'lineA/images/p.png')
    assert (agreed.labels, agreed.error) == (('ng',), None)
    assert [path for path, _ in agreed.files] == ['lineA/labels/p.txt', 'lineA/classes.txt'], 'the classes.txt the names came from'
    (source / 'data.yaml').write_text('names: [scratch, dent]\n')
    disagreeing = SourceAnnotationScanner(source).scan(image, 'lineA/images/p.png')
    assert disagreeing.error.startswith('AMBIGUOUS_ANNOTATION') and 'data.yaml' in [path for path, _ in disagreeing.files]


def test_class_lists_follow_the_importer_and_bad_yaml_is_an_annotation_error(tmp_path):
    source = tmp_path / 'source'
    image = _yolo(source, '2 0.5 0.5 0.1 0.1\n')
    (source / 'lineA' / 'classes.txt').write_text('ok\n\nng\n')  # a blank line keeps its id, as in the importer
    assert SourceAnnotationScanner(source).scan(image, 'lineA/images/p.png').labels == ('ng',)
    (source / 'lineA' / 'labels' / 'p.txt').write_text('1 0.5 0.5 0.1 0.1\n')
    assert 'blank class' in SourceAnnotationScanner(source).scan(image, 'lineA/images/p.png').error
    (source / 'lineA' / 'classes.txt').unlink()
    (source / 'lineA' / 'labels' / 'p.txt').write_text('2 0.5 0.5 0.1 0.1\n')
    (source / 'data.yaml').write_text("names: {'0': a, '1': b, '2': c, '10': z}\n")
    assert 'contiguous' in SourceAnnotationScanner(source).scan(image, 'lineA/images/p.png').error
    (source / 'data.yaml').write_text("names: {'0': a, '1': b, '2': c}\n")
    assert SourceAnnotationScanner(source).scan(image, 'lineA/images/p.png').labels == ('c',)
    for broken in ('names: [a, b\n', '- a\n- b\n', 'names: 3\n'):
        (source / 'data.yaml').write_text(broken)
        found = SourceAnnotationScanner(source).scan(image, 'lineA/images/p.png')
        assert found.labels == () and found.error.startswith('INVALID_ANNOTATION'), broken


def test_coco_names_are_not_guessed(tmp_path):
    source = tmp_path / 'source'
    image = _image(source / 'other' / 'img.png')
    document = {'images': [{'id': 1, 'file_name': '../other/img.png', 'width': 16, 'height': 16}],
                'categories': [{'id': 1, 'name': 'crack'}], 'annotations': []}
    (source / 'annotations.json').write_text(json.dumps(document))
    assert SourceAnnotationScanner(source).scan(image, 'other/img.png').error.startswith('INVALID_ANNOTATION')
    document['images'][0]['file_name'] = './other/img.png'
    document['annotations'] = [{'id': 1, 'image_id': 1, 'category_id': 9, 'bbox': [1, 1, 2, 2]}]
    (source / 'annotations.json').write_text(json.dumps(document))
    found = SourceAnnotationScanner(source).scan(image, 'other/img.png')
    assert found.error.startswith('INVALID_ANNOTATION') and 'unknown category' in found.error
    document['annotations'][0]['category_id'] = 1
    (source / 'annotations.json').write_text(json.dumps(document))
    assert SourceAnnotationScanner(source).scan(image, 'other/img.png').labels == ('crack',)
    mismatch = SourceAnnotationScanner(source).scan(image, 'other/img.png', (32, 16))
    assert 'differs from the image' in mismatch.error


def test_a_labelme_file_binds_only_the_image_it_names(tmp_path):
    source = tmp_path / 'source'
    png = _image(source / 'lm' / 'x.png')
    jpg = source / 'lm' / 'x.jpg'
    Image.new('RGB', (16, 16), 'white').save(jpg)
    (source / 'lm' / 'x.json').write_text(json.dumps({'shapes': [{'label': 'dent'}]}))
    for image in (png, jpg):
        assert SourceAnnotationScanner(source).scan(image, f'lm/{image.name}').error.startswith('AMBIGUOUS_ANNOTATION')
    (source / 'lm' / 'x.json').write_text(json.dumps({'imagePath': '..\\lm\\x.png', 'shapes': [{'label': 'dent'}]}))
    assert SourceAnnotationScanner(source).scan(png, 'lm/x.png').labels == ('dent',)
    assert SourceAnnotationScanner(source).scan(jpg, 'lm/x.jpg').format is None, 'the document names the other image'


def test_labelme_documents_are_not_kept_in_memory(tmp_path):
    source = tmp_path / 'source'
    image = _image(source / 'lm' / 'a.png')
    image.with_suffix('.json').write_text(json.dumps({'shapes': [{'label': 'dent'}]}))
    scanner = SourceAnnotationScanner(source)
    assert scanner.scan(image, 'lm/a.png').labels == ('dent',)
    image.with_suffix('.json').write_text(json.dumps({'shapes': [{'label': 'scratch'}]}))
    scanner._hash.clear()  # the digest memo is per path; the document itself is read again
    assert scanner.scan(image, 'lm/a.png').labels == ('scratch',)
    assert not hasattr(scanner, '_json') and scanner._coco == {}


def test_label_files_saved_in_this_machines_code_page_read_as_training_reads_them(tmp_path, monkeypatch):
    import locale
    source = tmp_path / 'source'
    image = _yolo(source)
    (source / 'lineA' / 'classes.txt').write_bytes('불량\n양품\n'.encode('cp949'))  # saved by a tool on Korean Windows
    monkeypatch.setattr(locale, 'getpreferredencoding', lambda do_setlocale=True: 'cp949')
    assert SourceAnnotationScanner(source).scan(image, 'lineA/images/p.png').labels == ('불량',)
    monkeypatch.setattr(locale, 'getpreferredencoding', lambda do_setlocale=True: 'UTF-8')
    found = SourceAnnotationScanner(source).scan(image, 'lineA/images/p.png')
    assert found.labels == () and found.error.startswith('INVALID_ANNOTATION') and 'UTF-8' in found.error
