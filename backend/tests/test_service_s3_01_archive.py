"""S3-01: uploaded ZIP datasets extracted safely into a project-owned folder (Korean names, traversal, bombs)."""
import hashlib
import io
import stat
import zipfile

import pytest
from PIL import Image


def _png():
    buffer = io.BytesIO()
    Image.new('RGB', (16, 16), 'white').save(buffer, format='PNG')
    return buffer.getvalue()


def _archive(path, entries):
    """entries: (name, data, mode, raw_cp949). A raw entry stores CP949 bytes without the UTF-8 flag, as archivers on
    Korean Windows do; the others are written as Python writes them (ASCII, or UTF-8 with the flag)."""
    original = zipfile.ZipInfo._encodeFilenameFlags
    raw_names = {entry[0] for entry in entries if len(entry) > 3 and entry[3]}

    def encode(self):
        return (self.filename.encode('cp949'), self.flag_bits) if self.filename in raw_names else original(self)

    zipfile.ZipInfo._encodeFilenameFlags = encode
    try:
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data, mode, *_raw in entries:
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                if mode is not None:
                    info.external_attr = mode << 16
                archive.writestr(info, data)
    finally:
        zipfile.ZipInfo._encodeFilenameFlags = original
    return path


def test_korean_and_spaced_names_and_same_names_in_folders_extract_with_a_receipt(tmp_path, monkeypatch):
    from backend.engine.dataset_archive_input import extract_dataset_archive
    image = _png()
    archive = _archive(tmp_path / 'set.zip', [('양품/검사 이미지 01.png', image, None, True), ('불량/same.png', image, None),
                                              ('양품/same.png', image, None), ('__MACOSX/._same.png', b'metadata', None)])
    with zipfile.ZipFile(archive) as check:
        assert not check.infolist()[0].flag_bits & 0x800, 'the first name is stored as CP949 without the UTF-8 flag'
    receipt = extract_dataset_archive(archive, tmp_path / 'project' / 'imports' / 'job-1')
    names = [row.relative_path for row in receipt]
    assert names == sorted(['양품/검사 이미지 01.png', '불량/same.png', '양품/same.png'])
    target = tmp_path / 'project' / 'imports' / 'job-1'
    assert (target / '양품' / '검사 이미지 01.png').read_bytes() == image
    assert all(row.sha256 == hashlib.sha256(image).hexdigest() and row.size == len(image) for row in receipt)
    assert not (target / '__MACOSX').exists()


@pytest.mark.parametrize('entries,reason', [
    ([('../escape.png', b'x', None)], 'Unsafe entry name'),
    ([('/abs.png', b'x', None)], 'Unsafe entry name'),
    ([('C:/windows.png', b'x', None)], 'Unsafe entry name'),
    ([('link.png', b'/etc/passwd', stat.S_IFLNK | 0o777)], 'Only regular files'),
    ([('tool.exe', b'MZ', None)], 'Unexpected file type'),
    ([('A.png', b'x', None), ('a.png', b'y', None)], 'name the same file'),
    ([('bomb.png', b'\0' * (8 * 1024 * 1024), None)], 'zip bomb'),
])
def test_unsafe_archives_are_refused_before_anything_is_written(tmp_path, entries, reason):
    from backend.engine.dataset_archive_input import ArchiveRefused, extract_dataset_archive
    archive = _archive(tmp_path / 'bad.zip', entries)
    target = tmp_path / 'project' / 'imports' / 'job-2'
    with pytest.raises(ArchiveRefused, match=reason):
        extract_dataset_archive(archive, target)
    assert not target.exists() and list((tmp_path / 'project' / 'imports').iterdir()) == [], 'no partial dataset is left'


def test_a_file_that_is_not_a_zip_and_an_existing_target_are_refused(tmp_path):
    from backend.engine.dataset_archive_input import ArchiveRefused, extract_dataset_archive
    fake = tmp_path / 'fake.zip'
    fake.write_bytes(b'not a zip')
    with pytest.raises(ArchiveRefused, match='Not a readable ZIP'):
        extract_dataset_archive(fake, tmp_path / 'out')
    existing = tmp_path / 'existing'
    existing.mkdir()
    archive = _archive(tmp_path / 'ok.zip', [('a.png', _png(), None)])
    with pytest.raises(ArchiveRefused, match='already exists'):
        extract_dataset_archive(archive, existing)


@pytest.mark.parametrize('name', ['CON.png', 'aux.json', 'lpt1.txt', 'com¹.png', 'nul .png', 'img:stream.png', 'sub/a:b.png',
                                  'dir./a.png', 'dir /a.png', 'a?.png', 'a|b.png', 'tab\tname.png'])
def test_names_windows_cannot_hold_as_ordinary_files_are_refused(tmp_path, name):
    from backend.engine.dataset_archive_input import ArchiveRefused, extract_dataset_archive
    archive = _archive(tmp_path / 'windows.zip', [('ok.png', _png(), None), (name, _png(), None)])
    with pytest.raises(ArchiveRefused, match='Windows can store|Unsafe entry name'):
        extract_dataset_archive(archive, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_a_file_that_is_also_a_folder_and_case_spelled_folders(tmp_path):
    from backend.engine.dataset_archive_input import ArchiveRefused, extract_dataset_archive
    clash = _archive(tmp_path / 'clash.zip', [('x/a.png', _png(), None), ('X/A.png/b.png', _png(), None)])
    with pytest.raises(ArchiveRefused, match='both a file and a folder'):
        extract_dataset_archive(clash, tmp_path / 'clash')
    merged = _archive(tmp_path / 'merged.zip', [('Data/a.png', _png(), None), ('data/b.png', _png(), None),
                                                ('DATA/Sub/c.png', _png(), None), ('data/sub/d.png', _png(), None)])
    receipt = extract_dataset_archive(merged, tmp_path / 'merged')
    names = [row.relative_path for row in receipt]
    assert names == ['Data/Sub/c.png', 'Data/Sub/d.png', 'Data/a.png', 'Data/b.png'], 'folders keep their first spelling'
    assert all((tmp_path / 'merged' / name).is_file() for name in names), 'the receipt names what is on disk'


def test_encrypted_unreadable_and_corrupt_entries_are_refusals_not_errors(tmp_path, monkeypatch):
    from backend.engine import dataset_archive_input
    from backend.engine.dataset_archive_input import ArchiveRefused, extract_dataset_archive
    plain = _archive(tmp_path / 'plain.zip', [('a.png', _png(), None)])
    data = bytearray(plain.read_bytes())  # zipfile clears the flag on write: set the encryption bit in both headers
    for signature, offset in ((b'PK', 6), (b'PK', 8)):
        at = data.index(signature) + offset
        data[at] |= 0x1
    encrypted = tmp_path / 'encrypted.zip'
    encrypted.write_bytes(bytes(data))
    with pytest.raises(ArchiveRefused, match='encrypted'):
        extract_dataset_archive(encrypted, tmp_path / 'encrypted-out')
    deflated = _archive(tmp_path / 'deflated.zip', [('a.png', _png() + bytes(range(256)) * 64, None)])
    monkeypatch.setattr(dataset_archive_input, '_METHODS', {zipfile.ZIP_STORED})  # stands in for deflate64 and friends
    with pytest.raises(ArchiveRefused, match='compression method'):
        extract_dataset_archive(deflated, tmp_path / 'method-out')
    monkeypatch.undo()
    data = bytearray(deflated.read_bytes())
    start = 30 + len('a.png')
    data[start + 10:start + 60] = b'\xff' * 50  # damage the deflate stream inside the first entry
    corrupt = tmp_path / 'corrupt.zip'
    corrupt.write_bytes(bytes(data))
    with pytest.raises(ArchiveRefused):
        extract_dataset_archive(corrupt, tmp_path / 'corrupt-out')
    assert not any(path.name.endswith('.partial') for path in tmp_path.iterdir()), 'no staging folder is left'


def test_the_receipt_is_kept_with_the_files_and_detects_any_change(tmp_path):
    from backend.engine.dataset_archive_input import RECEIPT, extract_dataset_archive, read_receipt, verify_extraction
    archive = _archive(tmp_path / 'set.zip', [('ok/a.png', _png(), None), ('ng/b.png', _png(), None)])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    target = tmp_path / 'imports' / digest
    receipt = extract_dataset_archive(archive, target, archive_sha256=digest)
    recorded = read_receipt(target)
    assert recorded['archive_sha256'] == digest and [row['relative_path'] for row in recorded['files']] == [r.relative_path for r in receipt]
    assert verify_extraction(target, digest) and not verify_extraction(target, '0' * 64)
    (target / 'ok' / 'extra.png').write_bytes(_png())
    assert not verify_extraction(target, digest), 'an added file'
    (target / 'ok' / 'extra.png').unlink()
    (target / 'ng' / 'b.png').write_bytes(_png()[:-1] + b'\0')
    assert not verify_extraction(target, digest), 'a changed byte'
    from backend.engine.dataset_inventory import is_inventory_path
    assert not is_inventory_path((RECEIPT,), 'classification'), 'the receipt is never a dataset image'


def test_a_concurrent_extraction_of_the_same_archive_reuses_the_verified_folder(tmp_path, monkeypatch):
    import os
    from backend.engine import dataset_archive_input
    from backend.engine.dataset_archive_input import extract_dataset_archive
    archive = _archive(tmp_path / 'set.zip', [('ok/a.png', _png(), None)])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    target = tmp_path / 'imports' / digest
    real_replace = os.replace
    raced = []

    def replace(source, destination):
        if not raced:  # another request extracts the same archive and renames it into place first
            raced.append(1)
            monkeypatch.setattr(dataset_archive_input.os, 'replace', real_replace)
            extract_dataset_archive(archive, target, archive_sha256=digest)
            monkeypatch.setattr(dataset_archive_input.os, 'replace', replace)
        return real_replace(source, destination)

    monkeypatch.setattr(dataset_archive_input.os, 'replace', replace)
    receipt = extract_dataset_archive(archive, target, archive_sha256=digest)
    assert raced and [row.relative_path for row in receipt] == ['ok/a.png'] and (target / 'ok' / 'a.png').is_file()


def test_a_volume_without_room_refuses_before_writing(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple
    from backend.engine import dataset_archive_input
    from backend.engine.dataset_archive_input import ArchiveNoSpace, extract_dataset_archive
    archive = _archive(tmp_path / 'set.zip', [('ok/a.png', _png(), None)])
    usage = namedtuple('usage', 'total used free')
    monkeypatch.setattr(dataset_archive_input.shutil, 'disk_usage', lambda path: usage(10, 10, 0))
    with pytest.raises(ArchiveNoSpace, match='bytes free'):
        extract_dataset_archive(archive, tmp_path / 'out')
    assert not (tmp_path / 'out').exists() and shutil is not None


def test_files_a_file_browser_adds_do_not_break_reuse_but_a_real_change_does(tmp_path):
    from backend.engine.dataset_archive_input import extract_dataset_archive, verify_extraction
    archive = _archive(tmp_path / 'set.zip', [('ok/a.png', _png(), None)])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    target = tmp_path / 'imports' / digest
    extract_dataset_archive(archive, target, archive_sha256=digest)
    for name in ('.DS_Store', 'ok/._a.png', 'ok/Thumbs.db', 'desktop.ini'):
        (target / name).write_bytes(b'browser metadata')
    assert verify_extraction(target, digest)
    (target / 'ok' / 'a.json').write_text('{"shapes": []}')
    assert not verify_extraction(target, digest), 'an added annotation changes what the dataset reads'


def test_a_retry_that_finds_the_verified_folder_reuses_it(tmp_path):
    from backend.engine.dataset_archive_input import ArchiveRefused, extract_dataset_archive
    archive = _archive(tmp_path / 'set.zip', [('ok/a.png', _png(), None)])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    target = tmp_path / 'imports' / digest
    first = extract_dataset_archive(archive, target, archive_sha256=digest)
    assert extract_dataset_archive(archive, target, archive_sha256=digest) == first
    with pytest.raises(ArchiveRefused, match='already exists'):
        extract_dataset_archive(archive, target)  # without the archive digest nothing can be verified


@pytest.mark.parametrize('name', ['CONIN$.png', 'conout$.json'])
def test_console_device_names_are_refused(tmp_path, name):
    from backend.engine.dataset_archive_input import ArchiveRefused, extract_dataset_archive
    with pytest.raises(ArchiveRefused, match='Windows can store'):
        extract_dataset_archive(_archive(tmp_path / 'c.zip', [(name, _png(), None)]), tmp_path / 'out')


def test_a_hidden_image_added_to_the_folder_is_a_change_but_appledouble_files_are_not(tmp_path):
    from backend.engine.dataset_archive_input import extract_dataset_archive, verify_extraction
    archive = _archive(tmp_path / 'set.zip', [('ok/a.png', _png(), None)])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    target = tmp_path / 'imports' / digest
    extract_dataset_archive(archive, target, archive_sha256=digest)
    (target / 'ok' / '._a.png').write_bytes(b'AppleDouble metadata')
    assert verify_extraction(target, digest)
    (target / 'ok' / '.injected.png').write_bytes(_png())  # a classification loader reads hidden images too
    assert not verify_extraction(target, digest)
