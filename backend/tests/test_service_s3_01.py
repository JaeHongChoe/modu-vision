"""S3-01 (data index and import), first slice: thumbnail identity and honest import validation coverage.

Real dataset routes on temporary synthetic images. Originals are hashed before and after every call: reading a
thumbnail or inspecting a folder never changes a source byte.
"""
import hashlib
import io
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def dataset_client(tmp_path, monkeypatch):
    import backend.main  # noqa: F401  (installed-httpx compatibility shim)
    from backend.api import routes_dataset as routes
    monkeypatch.setattr(routes, 'THUMBNAIL_CACHE_DIR', tmp_path / 'thumbs')
    routes.THUMBNAIL_CACHE_DIR.mkdir()
    monkeypatch.setattr(routes, 'STUDIO_ANNOTATIONS_DIR', tmp_path / 'annotations')
    monkeypatch.setattr(routes, 'SPLIT_MANIFEST_DIR', tmp_path / 'splits')
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        yield client


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def thumbnail(client, source, **headers):
    return client.get('/api/dataset/thumbnail/image', params={'file_path': str(source), 'size': 32}, headers=headers)


def test_a_changed_image_with_the_same_path_size_and_mtime_gets_a_new_thumbnail(dataset_client, tmp_path):
    source = tmp_path / '원본 공간.bmp'
    Image.new('RGB', (16, 12), 'red').save(source)
    stat = source.stat()
    assert thumbnail(dataset_client, source).status_code == 200
    Image.new('RGB', (16, 12), 'blue').save(source)  # same size (uncompressed BMP)
    assert source.stat().st_size == stat.st_size
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # the old modification time is restored
    before = digest(source)
    second = thumbnail(dataset_client, source)
    assert second.status_code == 200, second.text
    assert digest(source) == before, 'reading a thumbnail never changes the original'
    red, _green, blue = Image.open(io.BytesIO(second.content)).convert('RGB').getpixel((0, 0))
    assert blue > red, 'the new content is shown, not the cached red thumbnail'


def test_the_browser_revalidates_by_content_etag(dataset_client, tmp_path):
    source = tmp_path / 'part.png'
    Image.new('RGB', (20, 20), 'green').save(source)
    first = thumbnail(dataset_client, source)
    etag = first.headers['etag']
    assert first.headers['cache-control'] == 'private, no-cache', 'no day-long cache keyed by path'
    assert thumbnail(dataset_client, source, **{'If-None-Match': etag}).status_code == 304
    Image.new('RGB', (20, 20), 'black').save(source)
    changed = thumbnail(dataset_client, source, **{'If-None-Match': etag})
    assert changed.status_code == 200 and changed.headers['etag'] != etag


def test_a_failed_render_leaves_neither_a_staging_file_nor_a_partial_thumbnail(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'part.png'
    Image.new('RGB', (40, 40), 'white').save(source)
    real_save = Image.Image.save

    def disk_full(self, target, *args, **kwargs):
        real_save(self, target, *args, **kwargs)  # part of the file is written, then the disk fills up
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(Image.Image, 'save', disk_full)
    assert thumbnail(dataset_client, source).status_code == 400
    assert list(routes.THUMBNAIL_CACHE_DIR.iterdir()) == [], 'no staging file and no thumbnail under the final name'


def test_the_key_and_the_pixels_come_from_the_same_read(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'race.png'
    Image.new('RGB', (20, 20), 'red').save(source)
    real_open = routes.Image.open

    def rewrite_then_open(handle, *args, **kwargs):
        if not isinstance(handle, (str, os.PathLike)):  # decoding the copy that was hashed
            Image.new('RGB', (20, 20), 'blue').save(source)  # another program rewrites the file mid-request
        return real_open(handle, *args, **kwargs)

    monkeypatch.setattr(routes.Image, 'open', rewrite_then_open)
    first = thumbnail(dataset_client, source)
    monkeypatch.setattr(routes.Image, 'open', real_open)
    red = Image.open(io.BytesIO(first.content)).convert('RGB').getpixel((0, 0))
    assert red[0] > red[2], 'the pixels belong to the bytes that were hashed'
    second = thumbnail(dataset_client, source)
    blue = Image.open(io.BytesIO(second.content)).convert('RGB').getpixel((0, 0))
    assert blue[2] > blue[0] and second.headers['etag'] != first.headers['etag']


def test_a_refused_replace_serves_the_thumbnail_another_request_already_wrote(dataset_client, tmp_path, monkeypatch):
    import shutil
    from backend.api import routes_dataset as routes
    source = tmp_path / 'part.png'
    Image.new('RGB', (30, 30), 'green').save(source)

    def windows_replace(staging, target):
        shutil.copyfile(staging, target)  # another request finished first and is sending the file
        raise PermissionError(13, 'The process cannot access the file because it is being used by another process')

    monkeypatch.setattr(routes.os, 'replace', windows_replace)
    response = thumbnail(dataset_client, source)
    assert response.status_code == 200, response.text
    assert [path.name for path in routes.THUMBNAIL_CACHE_DIR.iterdir() if path.name.startswith('.')] == []


def test_weak_and_listed_etags_revalidate(dataset_client, tmp_path):
    source = tmp_path / 'part.png'
    Image.new('RGB', (20, 20), 'green').save(source)
    etag = thumbnail(dataset_client, source).headers['etag']
    for header in (f'W/{etag}', f'"other", {etag}', '*'):
        assert thumbnail(dataset_client, source, **{'If-None-Match': header}).status_code == 304, header


def test_a_warm_hit_reads_no_source_bytes(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'part.png'
    Image.new('RGB', (20, 20), 'green').save(source)
    assert thumbnail(dataset_client, source).status_code == 200
    reads = []
    monkeypatch.setattr(routes, '_hash_source', lambda *args: reads.append(args))
    monkeypatch.setattr(routes, '_probe_header', lambda *args: reads.append(args))
    assert thumbnail(dataset_client, source).status_code == 200
    assert reads == []


def _folder(root, count, corrupt_name='zz_corrupt.png'):
    category = root / 'good'
    category.mkdir(parents=True)
    for index in range(count):
        Image.new('RGB', (16, 16), 'white').save(category / f'{index:04}.png')
    corrupt = category / corrupt_name
    corrupt.write_bytes(b'not an image')
    return corrupt


def test_a_sampled_inspection_says_it_is_sampled(dataset_client, tmp_path, monkeypatch):
    source = tmp_path / '검사 data'
    _folder(source, 201)
    before = {p.relative_to(source).as_posix(): digest(p) for p in source.rglob('*') if p.is_file()}
    result = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True})
    assert result.status_code == 200, result.text
    assert {p.relative_to(source).as_posix(): digest(p) for p in source.rglob('*') if p.is_file()} == before
    validation = result.json()['validation']
    assert validation == {'requested': True, 'checked_images': 201, 'complete': False,
                          'scope': 'sampled: first 201 images in folder order'}, 'an empty corrupted list here covers 201 images only'


def test_a_small_folder_is_validated_completely_and_reports_its_corrupt_image(dataset_client, tmp_path):
    source = tmp_path / 'small'
    corrupt = _folder(source, 5)
    result = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['validation']['complete'] is True and body['validation']['checked_images'] == 6
    assert str(corrupt) in {row['file_path'] for row in body['corrupted_images']}


def test_nested_images_with_the_same_name_stay_distinct(dataset_client, tmp_path):
    source = tmp_path / '분류 공간'
    for label, color in [('good', 'white'), ('bad', 'black')]:
        path = source / label / 'same.png'
        path.parent.mkdir(parents=True)
        Image.new('RGB', (8, 6), color).save(path)
    response = dataset_client.get('/api/dataset/images', params={'folder_path': str(source), 'task': 'classification', 'limit': 1, 'offset': 1})
    assert response.status_code == 200, response.text
    assert response.json()['total'] == 2 and len(response.json()['items']) == 1


def test_exactly_201_images_are_validated_completely(dataset_client, tmp_path):
    source = tmp_path / 'boundary'
    _folder(source, 200)  # 200 good + 1 corrupt = 201
    body = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True}).json()
    assert body['validation']['complete'] is True and body['validation']['checked_images'] == 201


def test_images_behind_a_folder_link_are_decoded(dataset_client, tmp_path):
    source = tmp_path / 'linked'
    _folder(source, 3)
    elsewhere = tmp_path / 'elsewhere' / 'ng'
    elsewhere.mkdir(parents=True)
    for index in range(2):
        Image.new('RGB', (16, 16), 'black').save(elsewhere / f'{index}.png')
    try:
        (source / 'ng').symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip('folder links are not available here')
    body = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True}).json()
    assert body['total_images'] == 6, 'the inventory counts the linked images'
    assert body['validation'] == {'requested': True, 'checked_images': 6, 'complete': True,
                                  'scope': 'all images: every image file under the folder was decoded'}


def _link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('folder links are not available here')


def test_masks_never_stand_in_for_linked_segmentation_images(dataset_client, tmp_path):
    source, shared = tmp_path / 'seg', tmp_path / 'nas' / 'images'
    (source / 'masks').mkdir(parents=True)
    shared.mkdir(parents=True)
    for index in range(4):
        Image.new('RGB', (16, 16), 'white').save(shared / f'{index}.png')
        Image.new('L', (16, 16), 0).save(source / 'masks' / f'{index}.png')
    truncated = shared / '3.png'
    truncated.write_bytes(truncated.read_bytes()[:40])
    _link(source / 'images', shared)
    body = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'segmentation', 'validate_images': True}).json()
    assert str(source / 'images' / '3.png') in {row['file_path'] for row in body['corrupted_images']}, body
    assert body['validation']['checked_images'] == 8, 'the linked images are decoded, not only the masks'


def test_hidden_previews_never_stand_in_for_a_linked_class(dataset_client, tmp_path):
    source, shared = tmp_path / 'cls', tmp_path / 'nas' / 'bad'
    (source / 'good').mkdir(parents=True)
    (source / '.previews').mkdir()
    shared.mkdir(parents=True)
    for index in range(3):
        Image.new('RGB', (16, 16), 'white').save(source / 'good' / f'{index}.png')
    for index in range(2):
        Image.new('RGB', (16, 16), 'gray').save(source / '.previews' / f'{index}.png')
    Image.new('RGB', (16, 16), 'black').save(shared / 'ok.png')
    (shared / 'broken.png').write_bytes(b'not an image')
    _link(source / 'bad', shared)
    body = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True}).json()
    assert str(source / 'bad' / 'broken.png') in {row['file_path'] for row in body['corrupted_images']}, body


def test_an_inventory_listed_by_annotation_files_is_never_claimed_complete(dataset_client, tmp_path):
    import json
    source = tmp_path / 'coco'
    for partition, names in (('train', ['a.png', 'b.png', 'missing.png']), ('val', ['c.png'])):
        folder = source / 'images' / partition
        folder.mkdir(parents=True)
        for name in names:
            if name != 'missing.png':
                Image.new('RGB', (16, 16), 'white').save(folder / name)
        (source / f'annotations_{partition}.json').write_text(json.dumps({
            'images': [{'id': i, 'file_name': name, 'width': 16, 'height': 16} for i, name in enumerate(names)],
            'annotations': [], 'categories': [{'id': 1, 'name': 'defect'}]}))
    result = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'detection', 'validate_images': True})
    assert result.status_code == 200, result.text
    validation = result.json()['validation']
    assert validation['complete'] is False and validation['scope'].startswith('partial: 3 image files'), validation


def test_a_folder_link_back_to_an_ancestor_is_walked_once(dataset_client, tmp_path):
    source = tmp_path / 'loop'
    _folder(source, 2)
    _link(source / 'good' / 'again', source)
    body = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True}).json()
    assert body['validation']['checked_images'] == 3


def test_a_decompression_bomb_is_refused_from_its_header_without_reading_the_file(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'huge.png'
    Image.new('RGB', (40, 40), 'white').save(source)
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 100)  # 1600 pixels is over twice the limit
    reads = []
    monkeypatch.setattr(routes, '_hash_source', lambda *args: reads.append(args))
    for _ in range(3):
        assert thumbnail(dataset_client, source).status_code == 400
    assert reads == [], 'refused before any read of the file'


def test_a_source_over_the_copy_limit_is_drawn_from_the_file_and_checked_again(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'stack.tif'
    Image.new('RGB', (64, 64), 'white').save(source)
    monkeypatch.setattr(routes, '_COPY_LIMIT', 1024)
    monkeypatch.setattr(routes.tempfile, 'SpooledTemporaryFile', lambda *args, **kwargs: pytest.fail('no copy above the limit'))
    assert thumbnail(dataset_client, source).status_code == 200, 'a large source still gets its thumbnail'
    real_render = routes._render_jpeg

    def rewritten_while_drawn(image, size):
        rendered = real_render(image, size)
        Image.new('RGB', (64, 64), 'black').save(source)  # another program rewrites it during the render
        return rendered

    monkeypatch.setattr(routes, '_render_jpeg', rewritten_while_drawn)
    routes._DIGESTS.clear()
    Image.new('RGB', (64, 64), 'gray').save(source)
    changed = dataset_client.get('/api/dataset/thumbnail/image', params={'file_path': str(source), 'size': 48})
    assert changed.status_code == 409, 'pixels from bytes that changed while read are never attached to a key'


def test_memory_stays_bounded_while_a_large_source_is_hashed_and_rendered(dataset_client, tmp_path, monkeypatch):
    import tracemalloc
    from backend.api import routes_dataset as routes
    source = tmp_path / 'line-scan.bmp'
    Image.new('RGB', (2000, 1500), 'white').save(source)  # 9 MB uncompressed
    size = source.stat().st_size
    monkeypatch.setattr(routes, '_SPOOL_IN_MEMORY', 512 * 1024)
    for label in ('cold miss: hash, copy and render', 'cold hit: hash only'):
        tracemalloc.start()
        assert thumbnail(dataset_client, source).status_code == 200
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert peak < size / 3, (label, peak, size)
        routes._DIGESTS.clear()  # as after a restart


def test_after_a_restart_a_cached_thumbnail_is_served_without_decoding(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'part.png'
    Image.new('RGB', (20, 20), 'green').save(source)
    first = thumbnail(dataset_client, source)
    routes._DIGESTS.clear()  # the in-memory memo is gone after a restart
    renders, hashes = [], []
    real_hash = routes._hash_source
    monkeypatch.setattr(routes, '_render_jpeg', lambda *args: renders.append(args))
    monkeypatch.setattr(routes, '_hash_source', lambda *args: hashes.append(args) or real_hash(*args))
    cold = thumbnail(dataset_client, source, **{'If-None-Match': first.headers['etag']})
    assert cold.status_code == 304 and renders == [] and len(hashes) == 1
    assert thumbnail(dataset_client, source).status_code == 200 and len(hashes) == 1, 'then warm again'


def test_identical_bytes_decoded_as_dicom_and_as_tiff_are_different_thumbnails(dataset_client, tmp_path, monkeypatch):
    import hashlib as _hashlib
    from backend.engine import dicom_input
    tiff = tmp_path / 'slide.tif'
    Image.new('RGB', (20, 20), 'red').save(tiff)
    dicom = tmp_path / 'slide.dcm'
    dicom.write_bytes(tiff.read_bytes())
    monkeypatch.setattr(dicom_input, 'is_dicom', lambda path: Path(path).suffix == '.dcm')
    monkeypatch.setattr(dicom_input, 'read_dicom', lambda path: (Image.new('RGB', (20, 20), 'blue'),
                                                                {'source_sha256': _hashlib.sha256(Path(path).read_bytes()).hexdigest()}))
    from_dicom, from_tiff = thumbnail(dataset_client, dicom), thumbnail(dataset_client, tiff)
    assert from_dicom.headers['etag'] != from_tiff.headers['etag']
    red, _green, blue = Image.open(io.BytesIO(from_tiff.content)).convert('RGB').getpixel((0, 0))
    assert red > blue, 'the TIFF is drawn by its own decoder'
    dicom.unlink()
    other = tmp_path / 'gone.dcm'
    other.write_bytes(b'DICM')
    monkeypatch.setattr(dicom_input, 'read_dicom', lambda path: Path(path).read_bytes())
    real_stat = Path.stat
    other_unlinked = []

    def vanish_after_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == other and not other_unlinked:
            other_unlinked.append(True)
            other.unlink()
        return result

    monkeypatch.setattr(Path, 'stat', vanish_after_stat)
    assert dataset_client.get('/api/dataset/thumbnail/x', params={'file_path': str(other), 'size': 32}).status_code == 404


def test_a_thumbnail_that_cannot_be_cached_is_still_served(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'part.png'
    Image.new('RGB', (30, 30), 'green').save(source)
    real_write = Path.write_bytes

    def disk_full(self, data):
        if self.parent == routes.THUMBNAIL_CACHE_DIR:
            real_write(self, data[:10])
            raise OSError(28, 'No space left on device')
        return real_write(self, data)

    monkeypatch.setattr(Path, 'write_bytes', disk_full)
    response = thumbnail(dataset_client, source)
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert Image.open(io.BytesIO(response.content)).size == (30, 30)
    assert list(routes.THUMBNAIL_CACHE_DIR.iterdir()) == [], 'nothing partial is left'
    monkeypatch.setattr(routes.os, 'replace', lambda staging, target: None)  # the cache file vanishes before serving
    monkeypatch.setattr(Path, 'write_bytes', real_write)
    routes._DIGESTS.clear()
    vanished = thumbnail(dataset_client, source, **{'If-None-Match': '"other"'})
    assert vanished.status_code == 200 and vanished.headers['content-type'] == 'image/jpeg' and vanished.content


# --- Freeze-3 review regressions ---------------------------------------------------------------------------
@pytest.mark.parametrize('annotation', ['annotations.json', 'annotations/instances_default.json'])
def test_any_coco_file_makes_a_detection_inventory_listed(dataset_client, tmp_path, annotation):
    import json
    source = tmp_path / 'coco'
    (source / 'images').mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    Image.new('RGB', (16, 16), 'white').save(source / 'images' / 'a.png')
    Image.new('RGB', (16, 16), 'white').save(source / 'images' / 'unlisted.png')
    (outside / 'broken.png').write_bytes(b'not an image')
    path = source / annotation
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'images': [{'id': 1, 'file_name': 'a.png'}, {'id': 2, 'file_name': 'missing.png'},
                                           {'id': 3, 'file_name': '../../outside/broken.png'}],
                                'annotations': [], 'categories': [{'id': 1, 'name': 'defect'}]}))
    result = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'detection', 'validate_images': True})
    assert result.status_code == 200, result.text
    assert result.json()['validation']['complete'] is False, result.json()['validation']


def test_jpeg_sources_decode_at_a_reduced_scale(dataset_client, tmp_path, monkeypatch):
    source = tmp_path / 'camera.jpg'
    Image.new('RGB', (4000, 3000), 'white').save(source, quality=80)
    sizes = []
    real_convert = Image.Image.convert
    monkeypatch.setattr(Image.Image, 'convert', lambda self, *args, **kwargs: sizes.append(self.size) or real_convert(self, *args, **kwargs))
    assert thumbnail(dataset_client, source).status_code == 200
    assert sizes and max(sizes[0]) <= 500, f'decoded at {sizes[0]}, not the full 4000x3000'


def test_a_dicom_that_vanishes_while_it_is_decoded_is_not_found(dataset_client, tmp_path, monkeypatch):
    from backend.engine import dicom_input
    source = tmp_path / 'scan.dcm'
    source.write_bytes(b'DICM' * 16)
    monkeypatch.setattr(dicom_input, 'is_dicom', lambda path: True)

    def vanished(path):
        Path(path).unlink()  # gone after the cache lookup read it
        raise FileNotFoundError(2, 'No such file or directory', str(path))

    monkeypatch.setattr(dicom_input, 'read_dicom', vanished)
    assert thumbnail(dataset_client, source).status_code == 404


def test_a_file_that_failed_to_decode_is_not_read_again_until_it_changes(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'truncated.png'
    Image.new('RGB', (64, 64), 'white').save(source)
    source.write_bytes(source.read_bytes()[:120])
    reads = []
    real_hash = routes._hash_source
    monkeypatch.setattr(routes, '_hash_source', lambda *args: reads.append(args) or real_hash(*args))
    statuses = [thumbnail(dataset_client, source).status_code for _ in range(3)]
    assert statuses == [400, 400, 400] and len(reads) <= 2, f'read {len(reads)} times for 3 views'
    reads.clear()
    Image.new('RGB', (64, 64), 'white').save(source)  # repaired
    assert thumbnail(dataset_client, source).status_code == 200


def test_a_refused_replace_and_a_refused_cleanup_still_serve_the_thumbnail(dataset_client, tmp_path, monkeypatch):
    import shutil
    from backend.api import routes_dataset as routes
    source = tmp_path / 'part.png'
    Image.new('RGB', (30, 30), 'green').save(source)

    def windows_replace(staging, target):
        shutil.copyfile(staging, target)
        raise PermissionError(13, 'in use')

    real_unlink = Path.unlink
    monkeypatch.setattr(routes.os, 'replace', windows_replace)
    monkeypatch.setattr(Path, 'unlink', lambda self, missing_ok=False: (_ for _ in ()).throw(PermissionError(13, 'in use'))
                        if self.name.startswith('.') else real_unlink(self, missing_ok=missing_ok))
    assert thumbnail(dataset_client, source).status_code == 200


def test_a_full_temporary_folder_is_reported_as_server_storage_not_a_corrupt_image(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'part.png'
    Image.new('RGB', (30, 30), 'green').save(source)

    class Full:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def write(self, data):
            raise OSError(28, 'No space left on device')

    monkeypatch.setattr(routes.tempfile, 'SpooledTemporaryFile', lambda *args, **kwargs: Full())
    response = thumbnail(dataset_client, source)
    assert response.status_code == 507 and 'temporary storage' in response.text


def test_a_link_to_a_folder_containing_the_selection_is_not_walked(dataset_client, tmp_path):
    parent = tmp_path / 'datasets'
    source = parent / 'line-a'
    _folder(source, 2)
    sibling = parent / 'line-b' / 'good'
    sibling.mkdir(parents=True)
    (sibling / 'broken.png').write_bytes(b'not an image')
    _link(source / 'good' / 'up', parent)
    body = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True}).json()
    assert all('line-b' not in row['file_path'] for row in body['corrupted_images']), 'a sibling dataset is never read'
    assert body['validation']['complete'] is False and 'contains the selection' in body['validation']['scope']


def test_the_walk_stops_after_its_folder_budget(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    monkeypatch.setattr(routes, '_QUICK_VALIDATION_FOLDERS', 3)
    source = tmp_path / 'deep'
    _folder(source, 1)
    for index in range(5):
        (source / f'empty{index}').mkdir()
    body = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True}).json()
    assert body['validation']['complete'] is False and body['validation']['scope'].startswith('sampled: stopped after 3 folders')


@pytest.mark.parametrize('suffix,save', [('jpg', {'format': 'JPEG'}), ('bmp', {'format': 'BMP'}), ('tif', {'format': 'TIFF'})])
def test_a_truncated_bitstream_that_passes_the_header_checks_is_reported(dataset_client, tmp_path, suffix, save):
    import random
    source = tmp_path / 'cut'
    _folder(source, 2)
    noisy = Image.effect_noise((64, 64), 60).convert('RGB')
    cut = source / 'good' / f'cut.{suffix}'
    noisy.save(cut, **save)
    cut.write_bytes(cut.read_bytes()[:int(cut.stat().st_size * 0.5)])
    body = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True}).json()
    reported = {row['file_path']: row['error_code'] for row in body['corrupted_images']}
    assert reported.get(str(cut)) == 'DECODE_ERROR', reported


def test_a_transient_read_error_is_not_remembered_as_a_broken_image(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'share.png'
    Image.new('RGB', (30, 30), 'green').save(source)
    real_open = Path.open
    hiccups = []

    def share(self, *args, **kwargs):
        if self == source and not hiccups:
            hiccups.append(1)
            raise OSError(5, 'Input/output error')
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', share)
    assert thumbnail(dataset_client, source).status_code == 400
    assert thumbnail(dataset_client, source).status_code == 200, 'the next view reads the file again'


def test_a_huge_webp_is_refused_before_pillow_reads_it_whole(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'huge.webp'
    Image.new('RGB', (64, 64), 'white').save(source, format='WEBP')
    monkeypatch.setattr(routes, '_WEBP_LIMIT', 16)
    monkeypatch.setattr(routes, '_probe_header', lambda path: pytest.fail('read'))
    assert thumbnail(dataset_client, source).status_code == 400


def test_a_parent_link_is_caught_by_identity_not_spelling(dataset_client, tmp_path, monkeypatch):
    """Case-insensitive volumes let a link spell the parent differently; emulated by making spelling never match."""
    from backend.api import routes_dataset as routes
    parent = tmp_path / 'datasets'
    source = parent / 'line-a'
    _folder(source, 2)
    sibling = parent / 'line-b' / 'good'
    sibling.mkdir(parents=True)
    (sibling / 'broken.png').write_bytes(b'not an image')
    _link(source / 'good' / 'up', parent)
    monkeypatch.setattr(routes, '_contains', lambda parent_path, child: False)
    body = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True}).json()
    assert all('line-b' not in row['file_path'] for row in body['corrupted_images'])
    assert body['validation']['complete'] is False


def test_a_read_error_at_the_header_probe_is_not_remembered(dataset_client, tmp_path, monkeypatch):
    import builtins
    source = tmp_path / 'probe.png'
    Image.new('RGB', (30, 30), 'green').save(source)
    real_open = builtins.open
    hiccups = []

    def share(file, *args, **kwargs):
        if str(file) == str(source) and not hiccups:
            hiccups.append(1)
            raise OSError(5, 'Input/output error')
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', share)
    assert thumbnail(dataset_client, source).status_code == 400
    assert thumbnail(dataset_client, source).status_code == 200


def test_a_webp_named_jpg_is_held_to_the_webp_limit(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'saved-from-browser.jpg'
    Image.new('RGB', (64, 64), 'white').save(source, format='WEBP')
    monkeypatch.setattr(routes, '_WEBP_LIMIT', 16)
    monkeypatch.setattr(routes, '_hash_source', lambda *args: pytest.fail('read whole'))
    assert thumbnail(dataset_client, source).status_code == 400


def test_a_link_back_to_the_selection_spelled_differently_is_walked_once(dataset_client, tmp_path):
    """On a case-insensitive volume a link spelled /ONCE reaches /once; only folder identity shows it was walked."""
    source = tmp_path / 'once'
    _folder(source, 2)
    _link(source / 'good' / 'AGAIN', Path(str(source).replace('/once', '/ONCE')))
    body = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True}).json()
    assert body['validation']['checked_images'] == 3 and len(body['corrupted_images']) == 1


def test_a_volume_without_inode_numbers_is_still_walked_completely(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'virtual'
    _folder(source, 3)
    real_stat = os.stat

    class NoInode:
        def __init__(self, stat):
            self._stat = stat

        def __getattr__(self, name):
            return 0 if name == 'st_ino' else getattr(self._stat, name)

    monkeypatch.setattr(routes.os, 'stat', lambda path, *args, **kwargs: NoInode(real_stat(path, *args, **kwargs)))
    body = dataset_client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification', 'validate_images': True}).json()
    assert body['validation']['checked_images'] == 4 and body['validation']['complete'] is True


def test_a_corrupt_header_reported_without_an_errno_is_remembered(dataset_client, tmp_path, monkeypatch):
    from backend.api import routes_dataset as routes
    source = tmp_path / 'broken.bmp'
    source.write_bytes(b'BM' + b'\xff' * 60)
    probes = []
    real_probe = routes._probe_header
    monkeypatch.setattr(routes, '_probe_header', lambda path: probes.append(path) or real_probe(path))
    assert [thumbnail(dataset_client, source).status_code for _ in range(2)] == [400, 400]
    assert len(probes) == 1, 'the second view is answered from the failure memo'
