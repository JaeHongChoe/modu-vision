"""Invalid annotations retain only completed, relevant reads of their original bytes."""
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from backend.engine.dataset_annotations import SourceAnnotationScanner
from backend.engine.dataset_index import DatasetIndex
from backend.engine.source_text import SourceTextError, decode_source_text


def _image(source, name='a.png'):
    path = source / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (16, 12), 'white').save(path)
    return path


def _files(source, payloads):
    result = []
    for name, data in payloads:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        result.append((name, hashlib.sha256(data).hexdigest()))
    return tuple(result)


def _coco(*names):
    return json.dumps({'images': [{'id': i, 'file_name': name} for i, name in enumerate(names)],
                       'categories': [], 'annotations': []}).encode()


def _link(path, target):
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip('Creating a file symlink is unavailable for this user')


@pytest.mark.parametrize('payloads', [
    [('a.json', b'{broken')],
    [('a.json', b'\xff')],
    [('annotations.json', b'{broken')],
    [('annotations.json', b'{"images":[{"id":0,"file_name":"../a.png"}],"categories":[],"annotations":[]}')],
    [('classes.txt', b'defect\n'), ('a.txt', b'bad 0.5 0.5 0.2 0.2')],
    [('classes.txt', b'defect\n'), ('a.txt', b'\xff')],
    [('classes.txt', b'defect\n'), ('a.txt', b'2 0.5 0.5 0.2 0.2')],
    [('data.yaml', b'names: {1: defect}'), ('a.txt', b'0 0.5 0.5 0.2 0.2')],
    [('classes.txt', b'defect\n'), ('data.yaml', b'names: ['), ('a.txt', b'0 0.5 0.5 0.2 0.2')],
])
def test_invalid_reads_keep_consumed_bytes_and_do_not_change_sources(tmp_path, monkeypatch, payloads):
    monkeypatch.setattr('backend.engine.source_text.locale.getpreferredencoding', lambda _do_setlocale: 'UTF-8')
    image = _image(tmp_path)
    expected = _files(tmp_path, payloads)
    # Class-list failure occurs before the label text is read under existing precedence.
    if payloads[0][0] == 'data.yaml' or any(name == 'data.yaml' for name, _ in payloads):
        expected = tuple(item for item in expected if item[0] != 'a.txt')
    found = SourceAnnotationScanner(tmp_path).scan(image, 'a.png', (16, 12))
    assert found.error.startswith('INVALID_ANNOTATION:')
    assert found.labels == ()
    assert found.files == expected
    assert all((tmp_path / name).read_bytes() == data for name, data in payloads)


@pytest.mark.parametrize('name,data', [
    ('a.json', b'{broken'), ('annotations.json', b'{broken'),
    ('classes.txt', b'defect\n'), ('a.txt', b'bad 0.5 0.5 0.2 0.2'),
])
def test_replacement_after_read_does_not_bind_later_bytes(tmp_path, monkeypatch, name, data):
    image = _image(tmp_path)
    _files(tmp_path, [('classes.txt', b'defect\n'), ('a.txt', b'0 0.5 0.5 0.2 0.2'), (name, data)])
    original_read = Path.read_bytes
    target = tmp_path / name
    def replace_after_read(path):
        result = original_read(path)
        if path == target:
            path.write_bytes(b'replacement')
        return result
    monkeypatch.setattr(Path, 'read_bytes', replace_after_read)
    found = SourceAnnotationScanner(tmp_path).scan(image, 'a.png', (16, 12))
    assert (name, hashlib.sha256(data).hexdigest()) in found.files


@pytest.mark.parametrize('name,data', [('annotations.json', b'{broken'), ('data.yaml', b'names: [')])
def test_cached_failures_bind_same_original_bytes_to_multiple_images(tmp_path, name, data):
    images = [_image(tmp_path, f'{name}.png') for name in ('a', 'b')]
    _files(tmp_path, [('a.txt', b'0'), ('b.txt', b'0'), (name, data)])
    scanner = SourceAnnotationScanner(tmp_path)
    first = scanner.scan(images[0], 'a.png', (16, 12))
    (tmp_path / name).write_bytes(b'replacement')
    second = scanner.scan(images[1], 'b.png', (16, 12))
    assert first.error == second.error
    assert first.files == second.files == ((name, hashlib.sha256(data).hexdigest()),)


@pytest.mark.parametrize('format', ['coco', 'yolo'])
def test_cached_valid_documents_keep_hashes_of_parsed_bytes(tmp_path, format):
    images = [_image(tmp_path, f'{name}.png') for name in ('a', 'b')]
    if format == 'coco':
        name, data = 'annotations.json', _coco('a.png', 'b.png')
    else:
        name, data = 'classes.txt', b'defect\n'
        _files(tmp_path, [('a.txt', b'0'), ('b.txt', b'0')])
    _files(tmp_path, [(name, data)])
    scanner = SourceAnnotationScanner(tmp_path)
    first = scanner.scan(images[0], 'a.png')
    (tmp_path / name).write_bytes(b'replacement')
    second = scanner.scan(images[1], 'b.png')
    assert first.error is second.error is None
    assert first.labels == second.labels
    assert dict(first.files)[name] == dict(second.files)[name] == hashlib.sha256(data).hexdigest()


def test_adjacent_read_cannot_overwrite_cached_coco_provenance(tmp_path):
    first = _image(tmp_path)
    adjacent = _image(tmp_path, 'annotations.png')
    data = _coco('a.png', 'annotations.png')
    _files(tmp_path, [('annotations.json', data)])
    scanner = SourceAnnotationScanner(tmp_path)
    assert scanner.scan(first, 'a.png').error is None
    # This document is discovered as both adjacent JSON and a cached COCO.
    _files(tmp_path, [('annotations.json', _coco('unrelated.png'))])
    found = scanner.scan(adjacent, 'annotations.png')
    assert found.format == 'coco' and found.error is None
    assert found.files == (('annotations.json', hashlib.sha256(data).hexdigest()),)


def test_unrelated_candidates_are_absent_and_agreeing_class_lists_are_bound(tmp_path):
    image = _image(tmp_path)
    _files(tmp_path, [('a.json', b'{}'), ('annotations.json', _coco('other.png'))])
    expected = _files(tmp_path, [('classes.txt', b'defect\n'), ('data.yaml', b'names: [defect]'),
                                  ('a.txt', b'bad 0.5 0.5 0.2 0.2')])
    scanner = SourceAnnotationScanner(tmp_path)
    assert scanner.scan(image, 'a.png').files == expected
    clean = _image(tmp_path, 'b.png')
    assert scanner.scan(clean, 'b.png').files == ()


@pytest.mark.parametrize('name', ['a.json', 'annotations.json', 'classes.txt'])
def test_external_links_are_refused_without_trusted_bindings(tmp_path, name):
    source = tmp_path / 'source'
    image = _image(source)
    _files(source, [('a.txt', b'0')])
    outside = tmp_path / 'outside'
    outside.write_bytes(b'{broken')
    _link(source / name, outside)
    found = SourceAnnotationScanner(source).scan(image, 'a.png')
    assert 'outside the source' in found.error
    assert found.files == ()


@pytest.mark.parametrize('name', ['a.json', 'annotations.json', 'classes.txt'])
def test_explicitly_followed_external_links_keep_read_bytes(tmp_path, monkeypatch, name):
    monkeypatch.setattr('backend.engine.source_text.locale.getpreferredencoding', lambda _: 'UTF-8')
    source = tmp_path / 'source'
    image = _image(source)
    _files(source, [('a.txt', b'0')])
    outside = tmp_path / 'outside'
    data = b'{broken' if name != 'classes.txt' else b'\xff'
    outside.write_bytes(data)
    _link(source / name, outside)
    found = SourceAnnotationScanner(source, follow_links=True).scan(image, 'a.png')
    assert found.error.startswith('INVALID_ANNOTATION:')
    assert found.files == ((name, hashlib.sha256(data).hexdigest()),)
    assert outside.read_bytes() == data


@pytest.mark.parametrize('name', ['a.json', 'annotations.json', 'classes.txt', 'a.txt'])
def test_failed_read_never_fabricates_a_digest(tmp_path, monkeypatch, name):
    image = _image(tmp_path)
    _files(tmp_path, [('classes.txt', b'defect\n'), ('a.txt', b'0'), (name, b'{broken')])
    target = tmp_path / name
    original_read = Path.read_bytes
    def failed_read(path):
        if path == target:
            with path.open('rb') as stream:
                stream.read(2)
            raise OSError('incomplete read')
        return original_read(path)
    monkeypatch.setattr(Path, 'read_bytes', failed_read)
    found = SourceAnnotationScanner(tmp_path).scan(image, 'a.png')
    assert found.error.startswith('INVALID_ANNOTATION:')
    assert name not in dict(found.files)


@pytest.mark.parametrize('policy,expected_state', [('exclude', 'prepared'), ('reject', 'rejected')])
def test_index_errors_and_manifest_keep_invalid_annotation_bindings(tmp_path, policy, expected_state):
    source = tmp_path / 'source'
    _image(source)
    first = _files(source, [('a.json', b'{broken')])
    index = DatasetIndex(tmp_path / 'registry/index.sqlite3')
    receipt = index.build_revision('p', tmp_path / 'project', source, 'detection', policy)
    row = index.page('p', receipt.revision_id)['items'][0]
    assert (receipt.state, receipt.valid_count, receipt.annotated, receipt.annotation_errors) == (expected_state, 0, 0, 1)
    assert row['valid'] == 0 and row['error_code'] == 'INVALID_ANNOTATION'
    assert row['annotation_labels'] == []
    assert row['annotation_files'] == [{'path': path, 'sha256': digest} for path, digest in first]
    _files(source, [('a.json', b'{other broken')])
    changed = index.build_revision('p', tmp_path / 'project', source, 'detection', policy)
    assert receipt.manifest_sha256 != changed.manifest_sha256
    assert index.page('p', receipt.revision_id)['items'][0] == row


def test_overlay_retains_source_failure_provenance_without_accepting_source_labels(tmp_path):
    from backend.engine.annotation_storage import dataset_annotation_dir
    source = tmp_path / 'source'
    _image(source)
    expected = _files(source, [('a.json', b'{broken')])
    overlays = tmp_path / 'project/annotations'
    studio = dataset_annotation_dir(source, overlays, use_scope=False) / 'a.json'
    studio.parent.mkdir(parents=True)
    studio.write_text(json.dumps({'annotations': [{'type': 'bbox', 'label': 'scratch', 'bbox': [1, 1, 4, 4]}]}))
    index = DatasetIndex(tmp_path / 'registry/index.sqlite3')
    receipt = index.build_revision('p', tmp_path / 'project', source, 'detection', 'reject', overlay_root=overlays)
    row = index.page('p', receipt.revision_id)['items'][0]
    assert (receipt.state, receipt.valid_count, receipt.annotated, receipt.annotation_errors) == ('prepared', 1, 0, 1)
    assert row['valid'] == 1 and row['annotation_labels'] == []
    assert row['annotation_error'].startswith('INVALID_ANNOTATION:')
    assert row['annotation_files'] == [{'path': path, 'sha256': digest} for path, digest in expected]


@pytest.mark.parametrize('locale_name', ['UTF-8', 'utf8'])
def test_utf8_locale_error_has_one_encoding_and_action(tmp_path, monkeypatch, locale_name):
    monkeypatch.setattr('backend.engine.source_text.locale.getpreferredencoding', lambda _: locale_name)
    with pytest.raises(SourceTextError) as caught:
        decode_source_text(b'\xff', 'a.txt')
    assert 'save it as UTF-8' in str(caught.value)
    assert 'nor' not in str(caught.value)


def test_non_utf8_locale_error_names_actual_fallback(monkeypatch):
    monkeypatch.setattr('backend.engine.source_text.locale.getpreferredencoding', lambda _: 'cp949')
    with pytest.raises(SourceTextError, match='nor cp949'):
        decode_source_text(b'\xff', 'a.txt')
