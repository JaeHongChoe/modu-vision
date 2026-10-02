"""Uploaded ZIP datasets extracted into a project-owned folder (S3-01).

An archive is never extracted into a registered source. Every entry is checked before anything is written:

- names: no absolute paths, drive letters, ``..`` parts or NUL; no part that Windows cannot hold as an ordinary file
  (a colon, which would write an NTFS data stream, a reserved device name such as ``CON`` or ``LPT1`` with or without an
  extension, a trailing dot or space, ``<>"|?*`` or control characters); names that differ only by case or Unicode form
  collide on Windows/macOS and are refused, a file name that is also another entry's folder is refused, and folders
  spelled with different case are written under their first spelling so the receipt names what is on disk. A name is
  decoded as UTF-8 when the archive says so, otherwise the stored bytes are tried as UTF-8 and then as CP949 (archives
  made on Korean Windows), falling back to the archive's CP437;
- kinds: regular files only (links and device entries are refused), with an image or annotation extension, neither
  encrypted nor compressed with a method this build cannot read;
- size: the total uncompressed size, the entry count and each entry's compression ratio are bounded (zip bombs), and
  the destination volume must have room for the whole archive before anything is written.

Files are written to a staging folder, flushed to disk, and renamed into place only when every entry was extracted, so a
failure leaves no partial dataset. Any failure while reading the archive or writing the files is a refusal
(``ArchiveRefused``; ``ArchiveNoSpace`` when the volume is full), never a raw error. The receipt lists each extracted
file with its size and SHA-256 and is kept inside the folder (``RECEIPT``, hidden from the dataset inventory), so a
later request can verify the folder before reusing it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat as _stat
import unicodedata
import uuid
import zipfile
import zlib

from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS

ANNOTATION_EXTENSIONS = {'.json', '.txt', '.yaml', '.yml', '.xml', '.csv'}
MAX_TOTAL_BYTES = 32 * 1024**3
MAX_ENTRIES = 500_000
MAX_RATIO = 200  # a larger expansion of one entry is treated as a zip bomb
RECEIPT = '.modu-extraction.json'
_CHUNK = 1024 * 1024
_FREE_MARGIN = 256 * 1024**2
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$',
             *(f'{port}{n}' for port in ('COM', 'LPT') for n in (*'123456789', '¹', '²', '³'))}
_FORBIDDEN = set('<>:"|?*') | {chr(code) for code in range(32)}


def _methods() -> set:
    methods = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
    for name, module in (('ZIP_BZIP2', 'bz2'), ('ZIP_LZMA', 'lzma')):
        try:
            __import__(module)
            methods.add(getattr(zipfile, name))
        except ImportError:
            pass
    return methods


_METHODS = _methods()


class ArchiveRefused(ValueError):
    """The archive (or one of its entries) cannot be extracted safely; nothing was written."""


class ArchiveNoSpace(ArchiveRefused):
    """The destination volume has no room for the extracted archive; nothing was kept."""


@dataclass(frozen=True)
class ExtractedFile:
    relative_path: str
    size: int
    sha256: str


def entry_name(info: zipfile.ZipInfo) -> str:
    """The entry's real name: UTF-8 when flagged, else UTF-8 or CP949 bytes stored without the flag, else CP437."""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode('cp437')
    for encoding in ('utf-8', 'cp949'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.filename


def _safe_parts(name: str) -> tuple:
    normalized = name.replace('\\', '/')
    if '\x00' in normalized or normalized.startswith('/') or (len(normalized) > 1 and normalized[1] == ':'):
        raise ArchiveRefused(f'Unsafe entry name: {name!r}')
    parts = tuple(part for part in PurePosixPath(normalized).parts if part not in ('', '.'))
    if not parts or any(part == '..' for part in parts):
        raise ArchiveRefused(f'Unsafe entry name: {name!r}')
    for part in parts:
        if _FORBIDDEN & set(part) or part[-1] in '. ' or part.split('.')[0].rstrip(' ').upper() in _RESERVED:
            raise ArchiveRefused(f'{name!r} is not a name Windows can store as a file')
    return parts


def plan_entries(archive: zipfile.ZipFile) -> list:
    """[(info, relative path)] for the files to extract; raises ArchiveRefused before anything is written."""
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ArchiveRefused(f'The archive has more than {MAX_ENTRIES} entries')
    planned, seen, folders, total = [], {}, {}, 0
    for info in infos:
        name = entry_name(info)
        if info.is_dir():
            _safe_parts(name)
            continue
        kind = _stat.S_IFMT(info.external_attr >> 16)  # many archivers store permission bits only (no type)
        if kind and kind != _stat.S_IFREG:
            raise ArchiveRefused(f'Only regular files can be extracted: {name!r}')
        parts = _safe_parts(name)
        if parts[0] == '__MACOSX' or parts[-1].startswith('._') or parts[-1] in ('.DS_Store', 'Thumbs.db'):
            continue  # metadata written by archivers, not dataset files
        suffix = PurePosixPath(parts[-1]).suffix.lower()
        if suffix not in SUPPORTED_IMAGE_EXTENSIONS and suffix not in ANNOTATION_EXTENSIONS:
            raise ArchiveRefused(f'Unexpected file type in a dataset archive: {name!r}')
        if info.flag_bits & 0x1:
            raise ArchiveRefused(f'{name!r} is encrypted; extract it yourself and upload the files')
        if info.compress_type not in _METHODS:
            raise ArchiveRefused(f'{name!r} uses a compression method this build cannot read ({info.compress_type})')
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise ArchiveRefused(f'{name!r} expands more than {MAX_RATIO} times; refused as a possible zip bomb')
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise ArchiveRefused(f'The archive expands beyond {MAX_TOTAL_BYTES} bytes')
        spelled = []  # each folder under its first spelling: a case-insensitive volume merges them anyway
        for depth in range(1, len(parts)):
            folded = unicodedata.normalize('NFC', '/'.join(parts[:depth])).casefold()
            folders.setdefault(folded, parts[depth - 1])
            spelled.append(folders[folded])
        relative = '/'.join((*spelled, parts[-1]))
        folded = unicodedata.normalize('NFC', relative).casefold()
        if folded in seen:
            raise ArchiveRefused(f'{relative!r} and {seen[folded]!r} name the same file on Windows/macOS')
        seen[folded] = relative
        planned.append((info, relative))
    clash = next((seen[path] for path in seen if path in folders), None)
    if clash is not None:
        raise ArchiveRefused(f'{clash!r} is both a file and a folder in this archive')
    if not planned:
        raise ArchiveRefused('The archive contains no dataset files')
    return planned


def read_receipt(folder: Path | str) -> dict:
    return json.loads((Path(folder) / RECEIPT).read_text(encoding='utf-8'))


_IGNORED = ('.DS_Store', 'Thumbs.db', 'desktop.ini')  # written by file browsers on their own


def _ignored(relative: str) -> bool:
    """Metadata files operating systems add on their own (.DS_Store, AppleDouble ._* files, Thumbs.db, desktop.ini).
    Any other added file, hidden or not, counts as a change: some loaders read hidden image files too."""
    name = relative.rsplit('/', 1)[-1]
    return name in _IGNORED or (name.startswith('._') and relative != RECEIPT)


def verify_extraction(folder: Path | str, archive_sha256: str) -> bool:
    """Whether ``folder`` still holds exactly the files its receipt lists for this archive (every byte is read).
    Metadata files a file browser adds (.DS_Store, ._*, Thumbs.db, desktop.ini) are ignored; anything else is a change."""
    folder = Path(folder)
    try:
        receipt = read_receipt(folder)
        if receipt.get('archive_sha256') != archive_sha256:
            return False
        expected = {row['relative_path']: (row['size'], row['sha256']) for row in receipt['files']}
        present = {path.relative_to(folder).as_posix() for path in folder.rglob('*') if path.is_file() or path.is_symlink()}
        if {path for path in present if not _ignored(path)} != {*expected, RECEIPT}:
            return False
        for relative, (size, digest) in expected.items():
            path = folder.joinpath(*relative.split('/'))
            if path.is_symlink() or path.stat().st_size != size:
                return False
            hasher = hashlib.sha256()
            with path.open('rb') as handle:
                while chunk := handle.read(_CHUNK):
                    hasher.update(chunk)
            if hasher.hexdigest() != digest:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _fsync_folder(path: Path) -> None:
    if os.name == 'nt':
        return  # folders cannot be opened for flushing on Windows; NTFS journals the rename
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def extract_dataset_archive(archive_path, destination: Path | str, *, archive_sha256: str | None = None) -> list:
    """Extract a ZIP (a path or a readable file object) into ``destination``, which must not exist yet, and return
    the receipt rows. When another extraction of the same archive put the folder in place first, its verified content
    is reused (a retried request)."""
    destination = Path(destination)
    if destination.exists():
        if archive_sha256 is not None and verify_extraction(destination, archive_sha256):
            return [ExtractedFile(**row) for row in read_receipt(destination)['files']]  # a retry raced the first request
        raise ArchiveRefused(f'{destination} already exists; an archive is extracted into a new folder')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.partial')
    staging.mkdir()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            planned = plan_entries(archive)
            needed = sum(info.file_size for info, _ in planned) + _FREE_MARGIN
            free = shutil.disk_usage(staging).free
            if free < needed:
                raise ArchiveNoSpace(f'The project volume has {free} bytes free; extracting needs about {needed}')
            receipt = []
            for info, relative in planned:
                target = staging.joinpath(*relative.split('/'))
                target.parent.mkdir(parents=True, exist_ok=True)
                digest, written = hashlib.sha256(), 0
                with archive.open(info) as source, open(target, 'xb') as handle:
                    while chunk := source.read(_CHUNK):
                        written += len(chunk)
                        if written > info.file_size:  # the header understated the size
                            raise ArchiveRefused(f'{relative!r} is larger than its archive header says')
                        digest.update(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                receipt.append(ExtractedFile(relative, written, digest.hexdigest()))
        receipt.sort(key=lambda row: row.relative_path)
        record = {'archive_sha256': archive_sha256, 'files': [asdict(row) for row in receipt]}
        with open(staging / RECEIPT, 'x', encoding='utf-8') as handle:
            json.dump(record, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_folder(staging)
        try:
            os.replace(staging, destination)
        except OSError:
            if destination.is_dir() and archive_sha256 is not None and verify_extraction(destination, archive_sha256):
                return receipt  # a concurrent extraction of the same archive finished first
            raise
        _fsync_folder(destination.parent)
        return receipt
    except ArchiveRefused:
        raise
    except OSError as exc:
        if exc.errno in (errno.ENOSPC, getattr(errno, 'EDQUOT', errno.ENOSPC)):
            raise ArchiveNoSpace(f'The project volume ran out of space while extracting: {exc}') from exc
        raise ArchiveRefused(f'The archive could not be extracted: {type(exc).__name__}: {exc}') from exc
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError) as exc:
        raise ArchiveRefused(f'Not a readable ZIP archive: {type(exc).__name__}: {exc}') from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)  # only our own partial staging folder
