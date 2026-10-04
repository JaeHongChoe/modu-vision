"""Persistent image index with immutable, completely validated dataset revisions (S3-01).

A revision is the complete inventory of one registered source at one point in time. Every image of the inventory is
read once: its bytes are hashed while they are copied into a bounded private buffer, and every pixel is decoded from
that buffer, so the recorded digest and validity describe the same bytes. Each entry keeps its original relative
path, identity, digest, geometry, folder label/split and validation state. Folders that cannot be listed are recorded
as gaps, names that cannot be stored are recorded as invalid entries, and links that are not followed are counted:
nothing is silently left out.

The user picks the policy for invalid entries up front: ``reject`` keeps the revision but it can never become active;
``exclude`` keeps it activatable and keeps a receipt for every excluded entry and gap. A revision without images is
never activatable. Revisions are never modified; activation is a compare-and-swap on the project's active revision,
and re-activating the active revision is a no-op. A publication key makes a repeated build return its first receipt.

The inventory follows ``dataset_inventory`` (the same rules as the app's source listing). Folder and file links that
leave the source are followed only when the caller allows it (a local desktop source may link a NAS folder; a team
server keeps to its registered source); Windows junctions count as links.

Builds are staged: rows are written in batches to a staging table and the revision is sealed in one final
transaction, so memory stays bounded and a cancelled or failed build leaves no revision. The stat cache (device,
inode, size, mtime, ctime, validator version) is written as the build goes, so a retry reads only what changed. It
is trusted only where those fields are change evidence (POSIX with a nonzero inode); on Windows, where ctime is the
creation time, every file is read again. Each revision records whether all bytes were read and how many entries were
reused from the cache. ``verify=True`` reads every file.

Each decodable image also records what the source's own annotation files say (``dataset_annotations``: LabelMe, COCO,
YOLO; format, label names, and every binding file with its SHA-256, read fresh at every build because label files can
change without the image changing). For tasks whose training reads those files (detection, segmentation) an annotation
error (a broken document, an ambiguous binding, a class id outside the list) makes the entry invalid with that code, so
the policy above applies to it; other tasks label by folder, so the error is recorded but excludes nothing. Valid entries with the same
digest form duplicate groups; groups whose members carry different labels, or sit in different splits (train/test
leakage), are counted separately. Duplicates are reported, not removed: which copy to keep is the user's decision.

Schema 3 adds the annotation and duplicate tables; a schema-2 index is upgraded in place by adding them, and its
older revisions report ``annotations_scanned = false``. An older app then refuses the upgraded index explicitly.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
from backend.engine.sqlite_wal import use_wal  # a concurrent WAL switch is retried, not failed
import tempfile
import time
from typing import Callable, Optional
import uuid

from PIL import Image

from backend.engine.dataset_inventory import TASKS, excluded_folder, folder_label_split, is_inventory_path, scan_root

SCHEMA_VERSION = 3
_UPGRADABLE = (2,)
VALIDATOR = 'decode-v1'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS dataset_revisions(
    revision_id TEXT PRIMARY KEY, project_key TEXT NOT NULL, source_root TEXT NOT NULL, project_root TEXT NOT NULL,
    task TEXT NOT NULL, invalid_policy TEXT NOT NULL, state TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
    image_count INTEGER NOT NULL, valid_count INTEGER NOT NULL, error_count INTEGER NOT NULL, unreadable_folders INTEGER NOT NULL,
    skipped_links INTEGER NOT NULL, reused_entries INTEGER NOT NULL, verified_all INTEGER NOT NULL, follow_links INTEGER NOT NULL,
    parent_revision TEXT, publication_key TEXT, created_ns INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS dataset_revisions_project ON dataset_revisions(project_key, created_ns);
CREATE UNIQUE INDEX IF NOT EXISTS dataset_revisions_publication ON dataset_revisions(project_key, publication_key)
    WHERE publication_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS dataset_active(project_key TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES dataset_revisions(revision_id));
CREATE TABLE IF NOT EXISTS dataset_index_images(
    revision_id TEXT NOT NULL REFERENCES dataset_revisions(revision_id), relative_path TEXT NOT NULL, image_uuid TEXT NOT NULL,
    sha256 TEXT, size INTEGER NOT NULL, width INTEGER, height INTEGER, label TEXT, split TEXT, valid INTEGER NOT NULL,
    error_code TEXT, error_detail TEXT, via_link INTEGER NOT NULL, PRIMARY KEY(revision_id, relative_path));
CREATE INDEX IF NOT EXISTS dataset_index_label ON dataset_index_images(revision_id, label, relative_path);
CREATE TABLE IF NOT EXISTS dataset_index_gaps(
    revision_id TEXT NOT NULL REFERENCES dataset_revisions(revision_id), relative_path TEXT NOT NULL, reason TEXT NOT NULL,
    PRIMARY KEY(revision_id, relative_path));
CREATE TABLE IF NOT EXISTS dataset_builds(build_id TEXT PRIMARY KEY, project_key TEXT NOT NULL, started_ns INTEGER NOT NULL,
    heartbeat_ns INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS dataset_index_staging(
    build_id TEXT NOT NULL, relative_path TEXT NOT NULL, image_uuid TEXT NOT NULL, sha256 TEXT, size INTEGER NOT NULL,
    width INTEGER, height INTEGER, label TEXT, split TEXT, valid INTEGER NOT NULL, error_code TEXT, error_detail TEXT,
    via_link INTEGER NOT NULL, PRIMARY KEY(build_id, relative_path));
CREATE TABLE IF NOT EXISTS dataset_stat_cache(
    project_key TEXT NOT NULL, source_root TEXT NOT NULL, relative_path TEXT NOT NULL, validator TEXT NOT NULL,
    dev INTEGER NOT NULL, ino INTEGER NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, ctime_ns INTEGER NOT NULL,
    sha256 TEXT, width INTEGER, height INTEGER, valid INTEGER NOT NULL, error_code TEXT, error_detail TEXT,
    last_build TEXT NOT NULL, PRIMARY KEY(project_key, source_root, relative_path));
CREATE INDEX IF NOT EXISTS dataset_index_digest ON dataset_index_images(revision_id, sha256);
CREATE INDEX IF NOT EXISTS dataset_index_identity ON dataset_index_images(revision_id, image_uuid);
CREATE TABLE IF NOT EXISTS dataset_index_annotations(
    revision_id TEXT NOT NULL REFERENCES dataset_revisions(revision_id), relative_path TEXT NOT NULL, format TEXT,
    labels TEXT NOT NULL, files TEXT NOT NULL, error TEXT, PRIMARY KEY(revision_id, relative_path));
CREATE TABLE IF NOT EXISTS dataset_annotation_staging(
    build_id TEXT NOT NULL, relative_path TEXT NOT NULL, format TEXT, labels TEXT NOT NULL, files TEXT NOT NULL, error TEXT,
    PRIMARY KEY(build_id, relative_path));
CREATE TABLE IF NOT EXISTS dataset_revision_details(
    revision_id TEXT PRIMARY KEY REFERENCES dataset_revisions(revision_id), annotated INTEGER NOT NULL,
    annotation_errors INTEGER NOT NULL, duplicate_groups INTEGER NOT NULL, duplicate_images INTEGER NOT NULL,
    conflicting_duplicates INTEGER NOT NULL, cross_split_duplicates INTEGER NOT NULL, annotations_bind INTEGER NOT NULL);
'''
_DETAILS = ('annotated', 'annotation_errors', 'duplicate_groups', 'duplicate_images', 'conflicting_duplicates',
            'cross_split_duplicates', 'annotations_bind')
# Tasks whose training reads the source's annotation files; other tasks label by folder, so a broken annotation file is
# recorded but never excludes an image there.
ANNOTATED_TASKS = frozenset({'detection', 'segmentation'})
POLICIES = ('reject', 'exclude')
# Valid entries sharing a digest. A group is conflicting when its members carry different folder or annotation labels,
# and cross-split when they sit in more than one explicit split (unsplit members prove no leakage).
_GROUPS = """WITH groups AS (
    SELECT i.sha256 AS sha256, COUNT(*) AS members,
           COUNT(DISTINCT COALESCE(i.label, '') || char(31) || COALESCE(a.labels, '[]')) AS labelings,
           COUNT(DISTINCT i.split) AS splits
    FROM dataset_index_images i LEFT JOIN dataset_index_annotations a
         ON a.revision_id = i.revision_id AND a.relative_path = i.relative_path
    WHERE i.revision_id = ? AND i.valid = 1 AND i.sha256 IS NOT NULL
    GROUP BY i.sha256 HAVING COUNT(*) > 1)"""
_REVISION_ROWS = ('SELECT r.*, ' + ', '.join(f'd.{name}' for name in _DETAILS) +
                  ' FROM dataset_revisions r LEFT JOIN dataset_revision_details d ON d.revision_id = r.revision_id')
_IMAGE_ROWS = ('SELECT i.*, a.format AS annotation_format, a.labels AS annotation_labels, a.files AS annotation_files, '
               'a.error AS annotation_error FROM dataset_index_images i LEFT JOIN dataset_index_annotations a '
               'ON a.revision_id = i.revision_id AND a.relative_path = i.relative_path')
_GROUP_MEMBERS = 50
_COLUMNS = ('relative_path', 'image_uuid', 'sha256', 'size', 'width', 'height', 'label', 'split', 'valid', 'error_code',
            'error_detail', 'via_link')
_BATCH = 500
_SPOOL_IN_MEMORY = 16 * 1024 * 1024
_CHUNK = 1024 * 1024
_STALE_BUILD_NS = 24 * 3600 * 10**9
_MAX_CURSOR = 8192
# Stat fields are change evidence only where ctime is a change time (not Windows, where it is the creation time).
_STAT_CACHE_TRUSTED = os.name != 'nt'
_isjunction = getattr(os.path, 'isjunction', lambda path: False)


class StaleActiveRevision(Exception):
    """The project's active revision changed since the caller read it."""


class RevisionNotActivatable(Exception):
    """A rejected revision (invalid entries or gaps under the reject policy), or one without a valid image, can never
    become active."""


@dataclass(frozen=True)
class RevisionReceipt:
    revision_id: str
    state: str
    manifest_sha256: str
    image_count: int
    valid_count: int
    error_count: int
    invalid_policy: str
    skipped_links: int = 0
    unreadable_folders: int = 0
    reused_entries: int = 0
    verified_all: bool = True
    # None: sealed before schema 3, so annotations and duplicates were never examined (not "none found").
    annotated: Optional[int] = None
    annotation_errors: Optional[int] = None
    duplicate_groups: Optional[int] = None
    duplicate_images: Optional[int] = None
    conflicting_duplicates: Optional[int] = None
    cross_split_duplicates: Optional[int] = None
    annotations_bind: Optional[bool] = None


def index_path(registry_root: Path | str) -> Path:
    return Path(registry_root) / 'dataset_index.sqlite3'


def image_identity(project_root: Path | str, source_root: Path | str, relative: str) -> str:
    """The same identity the project metadata ledger gives an image (project root, source root, relative path)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'{Path(project_root).resolve()}\0{Path(source_root).resolve()}\0{relative}'))


def _contains(parent: str, child: str) -> bool:
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:  # different drives on Windows
        return False


def _folder_ids(path: str) -> set:
    """(device, inode) of a folder and of every folder above it: identity, not spelling (case-insensitive volumes)."""
    ids, current = set(), os.path.realpath(path)
    while True:
        try:
            stat = os.stat(current)
            if stat.st_ino:  # inode 0 (some virtual drives) identifies nothing
                ids.add((stat.st_dev, stat.st_ino))
        except OSError:
            pass
        parent = os.path.dirname(current)
        if parent == current:
            return ids
        current = parent


def _same_folder_id(path: str) -> tuple:
    """(device, inode), or (None, None) when the volume gives no usable identity (inode 0): callers then use the path."""
    try:
        stat = os.stat(path)
        return (stat.st_dev, stat.st_ino) if stat.st_ino else (None, None)
    except OSError:
        return None, None


def _storable(relative: str) -> tuple[str, bool]:
    """The name as stored, and whether the real name can be stored (undecodable bytes become escapes)."""
    try:
        relative.encode('utf-8')
        return relative, True
    except UnicodeEncodeError:
        return os.fsencode(relative).decode('utf-8', 'backslashreplace'), False


@dataclass
class _Inventory:
    entries: list  # (relative, path, via_link, storable)
    gaps: list  # (relative folder, reason)
    skipped_links: int


def _discover(source: Path, task: str, project: Path, follow_links: bool,
              cancelled: Optional[Callable[[], bool]]) -> _Inventory:
    """Every inventory image under the scan root; unreadable folders become gaps and unfollowed links are counted."""
    real_source = os.path.realpath(source)
    source_and_above = _folder_ids(real_source)  # a link to any of these contains the source itself
    project_inside = _contains(real_source, os.path.realpath(project))
    real_project = os.path.realpath(project)
    entries, gaps, seen = [], [], set()
    skipped = 0

    def relative_of(path: Path) -> str:
        return path.relative_to(source).as_posix()

    def failed(error: OSError) -> None:
        folder = Path(error.filename) if error.filename else source
        try:
            name = relative_of(folder)
        except ValueError:
            name = str(folder)
        gaps.append((_storable(name)[0] or '.', f'FOLDER_UNREADABLE: {error.strerror or error}'))

    for root, dirs, files in os.walk(scan_root(source, task), followlinks=follow_links, onerror=failed):
        if cancelled is not None and cancelled():
            raise InterruptedError('Index build cancelled; no revision was recorded')
        here = Path(root)
        real = os.path.realpath(root)
        identity = _same_folder_id(real)
        marker = identity if identity != (None, None) else real
        if marker in seen:  # a link back to a folder already walked (by identity: spelling can differ)
            dirs[:] = []
            continue
        if not follow_links and not _contains(real_source, real):  # defence in depth: never leave the source
            skipped += 1
            dirs[:] = []
            continue
        seen.add(marker)
        kept = []
        for name in dirs:
            path = here / name
            if excluded_folder(name, task):
                continue
            if project_inside and os.path.realpath(path) == real_project:
                continue  # the project's own folder inside the source
            if path.is_symlink() or _isjunction(path):
                target = os.path.realpath(path)
                if not follow_links or _contains(target, real_source) or _same_folder_id(target) in source_and_above:
                    # not allowed, or a folder containing the source (compared by identity, not by spelling)
                    skipped += 1
                    continue
            kept.append(name)
        dirs[:] = sorted(kept)
        for name in files:
            path = here / name
            relative = relative_of(path)
            if not is_inventory_path(PurePosixPath(relative).parts, task):
                continue
            via_link = path.is_symlink()
            if via_link and not follow_links and not _contains(real_source, os.path.realpath(path)):
                skipped += 1
                continue
            stored, ok = _storable(relative)
            entries.append((stored, path, int(via_link or not _contains(real_source, real)), ok))
    entries.sort(key=lambda entry: entry[0])
    gaps.sort()
    return _Inventory(entries, gaps, skipped)


def _stat_identity(value: int) -> int | str:
    # Windows file IDs can be wider than SQLite's signed 64-bit INTEGER. A nonnumeric prefix keeps the full value
    # as TEXT under INTEGER affinity; decimal text could be coerced to a lossy REAL. Existing small IDs stay integers.
    return value if -(1 << 63) <= value < (1 << 63) else f'id:{value:x}'


def _stat_key(stat: os.stat_result) -> tuple:
    return (_stat_identity(stat.st_dev), _stat_identity(stat.st_ino), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _decode_copy(copy) -> tuple:
    """(width, height, valid, error_code, error_detail) of the bytes in our private copy: deterministic, so cacheable."""
    try:
        with Image.open(copy) as image:
            image.load()  # every pixel: header checks alone pass truncated JPEG/BMP/TIFF data
            return image.size[0], image.size[1], True, None, None
    except Image.UnidentifiedImageError as exc:
        return None, None, False, 'UNIDENTIFIED_IMAGE', str(exc)
    except MemoryError:
        raise
    except Exception as exc:
        return None, None, False, 'DECODE_ERROR', f'{type(exc).__name__}: {exc}'


def _read_entry(path: Path) -> tuple:
    """(sha256, size, width, height, valid, error_code, error_detail, stat_key) from one read of the file.

    A failure to read the source (a network share hiccup, a sharing violation) or to get memory for decoding is
    reported without a stat key, so it is never cached and the next build reads the file again; a decode failure of
    bytes that were read completely is a property of those bytes and is cached with them.
    """
    from backend.engine.dicom_input import is_dicom, read_dicom
    before = path.stat()
    if before.st_size == 0:
        return None, 0, None, None, False, 'ZERO_BYTE', 'File size is 0 bytes', _stat_key(before)
    try:
        if is_dicom(path):
            try:
                image, metadata = read_dicom(path)
                digest, (width, height), valid, code, detail = metadata['source_sha256'], image.size, True, None, None
            except ValueError as exc:
                # read_dicom also reports a missing optional decoder or exhausted memory as ValueError: such a failure
                # can change without the file changing, so a DICOM decode failure is never cached.
                return None, before.st_size, None, None, False, 'DECODE_ERROR', str(exc), None
        else:
            hasher = hashlib.sha256()
            with tempfile.SpooledTemporaryFile(max_size=_SPOOL_IN_MEMORY) as copy:
                with path.open('rb') as handle:
                    while chunk := handle.read(_CHUNK):
                        hasher.update(chunk)
                        copy.write(chunk)
                digest = hasher.hexdigest()
                copy.seek(0)
                width, height, valid, code, detail = _decode_copy(copy)
    except MemoryError as exc:
        return None, before.st_size, None, None, False, 'RESOURCE_ERROR', f'Not enough memory to decode: {exc}', None
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise
        return None, before.st_size, None, None, False, 'READ_ERROR', f'{type(exc).__name__}: {exc}', None
    after = path.stat()
    if _stat_key(after) != _stat_key(before):
        return digest, after.st_size, None, None, False, 'CHANGED_DURING_SCAN', 'The file changed while it was read; index it again', None
    return digest, before.st_size, width, height, valid, code, detail, _stat_key(before)


class DatasetIndex:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            # Additive upgrade (new tables and an index only): existing revisions and rows are not rewritten. The version
            # is read under the same write lock: read before it, another opener's upgrade could commit between the two
            # reads and an index it just created would look like an unknown schema 0.
            db.execute('BEGIN IMMEDIATE')
            try:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dataset_revisions'").fetchone()
                if exists and version != SCHEMA_VERSION and version not in _UPGRADABLE:
                    raise RuntimeError(f'{self.path} uses index schema {version}; this build reads schema {SCHEMA_VERSION}')
                for statement in SCHEMA.split(';'):
                    if statement.strip():
                        db.execute(statement)
                columns = {row[1] for row in db.execute('PRAGMA table_info(dataset_revision_details)')}
                if 'annotations_bind' not in columns:  # a schema-3 index written before this column existed
                    db.execute('ALTER TABLE dataset_revision_details ADD COLUMN annotations_bind INTEGER NOT NULL DEFAULT 1')
                db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
            except BaseException:
                db.execute('ROLLBACK')
                raise
            db.execute('COMMIT')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            use_wal(db, 30)
            db.execute('PRAGMA foreign_keys=ON')
            yield db
        finally:
            db.close()

    @contextmanager
    def _tx(self, db=None):
        if db is None:
            with self._connect() as connection, self._tx(connection) as inner:
                yield inner
            return
        db.execute('BEGIN IMMEDIATE')
        try:
            yield db
        except BaseException:
            db.execute('ROLLBACK')
            raise
        db.execute('COMMIT')

    def build_revision(self, project_key: str, project_root: Path | str, source_root: Path | str, task: str,
                       invalid_policy: str = 'exclude', *, verify: bool = False, follow_links: bool = False,
                       parent_revision: Optional[str] = None, publication_key: Optional[str] = None,
                       progress: Optional[Callable[[int, int], None]] = None,
                       cancelled: Optional[Callable[[], bool]] = None,
                       before_seal: Optional[Callable[[], None]] = None,
                       overlay_root: Optional[Path | str] = None) -> RevisionReceipt:
        """Read every image of the inventory and seal an immutable revision of it. ``overlay_root`` is the project's
        active annotation folder: an image with a Studio overlay there is labelled by it in training, so a broken source
        annotation of that image is recorded without excluding it."""
        if invalid_policy not in POLICIES:
            raise ValueError(f'invalid_policy must be one of {POLICIES}')
        if task not in TASKS:
            raise ValueError(f'Unknown dataset task: {task}')
        source, project = Path(source_root).resolve(), Path(project_root).resolve()
        if not source.is_dir():
            raise ValueError(f'Source folder does not exist: {source}')
        if publication_key is not None:
            replay = self._published(project_key, publication_key)
            if replay is not None:
                return replay
        if parent_revision is not None and parent_revision not in {row['revision_id'] for row in self.revisions(project_key)}:
            raise ValueError('The parent revision does not belong to this project')
        inventory = _discover(source, task, project, follow_links, cancelled)
        identity_prefix = f'{project}\0{source}\0'
        build_id, started = uuid.uuid4().hex, time.time_ns()
        trusted = _STAT_CACHE_TRUSTED and not verify
        with self._connect() as db:
            with self._tx(db):
                # A build that crashed left staging rows; one that is alive refreshes its heartbeat at every batch.
                stale = [row[0] for row in db.execute('SELECT build_id FROM dataset_builds WHERE heartbeat_ns < ?',
                                                      (started - _STALE_BUILD_NS,))]
                for old in stale:
                    self._drop_build(db, old)
                db.execute('INSERT INTO dataset_builds VALUES(?, ?, ?, ?)', (build_id, project_key, started, started))
            try:
                root = scan_root(source, task)
                scan_prefix = '' if root == source else root.relative_to(source).as_posix() + '/'
                receipt = self._stage_and_seal(db, build_id, project_key, project, source, task, invalid_policy, follow_links,
                                               parent_revision, publication_key, inventory, identity_prefix, trusted, verify,
                                               progress, cancelled, before_seal, scan_prefix, overlay_root)
            except BaseException:
                with self._tx(db):
                    self._drop_build(db, build_id)
                raise
        return receipt

    @staticmethod
    def _drop_build(db, build_id: str) -> None:
        db.execute('DELETE FROM dataset_index_staging WHERE build_id=?', (build_id,))
        db.execute('DELETE FROM dataset_annotation_staging WHERE build_id=?', (build_id,))
        db.execute('DELETE FROM dataset_builds WHERE build_id=?', (build_id,))

    def _stage_and_seal(self, db, build_id, project_key, project, source, task, invalid_policy, follow_links, parent_revision,
                        publication_key, inventory, identity_prefix, trusted, verify, progress, cancelled, before_seal,
                        scan_prefix, overlay_root=None) -> RevisionReceipt:
        from backend.engine.annotation_storage import dataset_annotation_dir
        from backend.engine.dataset_annotations import SourceAnnotationScanner

        def overlaid(path: Path) -> bool:
            return overlay_root is not None and (
                dataset_annotation_dir(path.parent, Path(overlay_root), use_scope=False) / f'{path.stem}.json').is_file()

        scanner = SourceAnnotationScanner(source, follow_links=follow_links, task=task)
        manifest = hashlib.sha256()
        rows, cache_rows, touched, notes = [], [], [], []
        counts = {'images': 0, 'valid': 0, 'reused': 0, 'annotated': 0, 'annotation_errors': 0, 'notes': 0}
        total = len(inventory.entries)

        def write_batch() -> None:
            db.executemany(f'INSERT INTO dataset_index_staging VALUES({", ".join("?" * 13)})', [(build_id, *row) for row in rows])
            db.executemany('INSERT INTO dataset_annotation_staging VALUES(?, ?, ?, ?, ?, ?)', [(build_id, *note) for note in notes])
            db.executemany('INSERT OR REPLACE INTO dataset_stat_cache VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', cache_rows)
            db.executemany('UPDATE dataset_stat_cache SET last_build=? WHERE project_key=? AND source_root=? AND relative_path=?',
                           touched)

        def flush() -> None:
            with self._tx(db):
                db.execute('UPDATE dataset_builds SET heartbeat_ns=? WHERE build_id=?', (time.time_ns(), build_id))
                write_batch()
            rows.clear(), cache_rows.clear(), touched.clear(), notes.clear()

        for index, (relative, path, via_link, storable) in enumerate(inventory.entries, start=1):
            if cancelled is not None and cancelled():
                raise InterruptedError('Index build cancelled; no revision was recorded')
            label, split = folder_label_split(PurePosixPath(relative), task)
            image_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, identity_prefix + relative))
            if not storable:
                entry = (None, 0, None, None, False, 'UNREPRESENTABLE_NAME', 'The file name is not valid UTF-8', None)
            else:
                entry = None
                try:
                    stat = path.stat()
                    hit = db.execute('SELECT * FROM dataset_stat_cache WHERE project_key=? AND source_root=? AND relative_path=?',
                                     (project_key, str(source), relative)).fetchone() if trusted and stat.st_ino else None
                    if hit is not None and hit['validator'] == VALIDATOR and (
                            hit['dev'], hit['ino'], hit['size'], hit['mtime_ns'], hit['ctime_ns']) == _stat_key(stat):
                        entry = (hit['sha256'], hit['size'], hit['width'], hit['height'], bool(hit['valid']), hit['error_code'],
                                 hit['error_detail'], None)
                        counts['reused'] += 1
                        touched.append((build_id, project_key, str(source), relative))
                    else:
                        entry = _read_entry(path)
                except OSError as exc:
                    entry = (None, 0, None, None, False, 'READ_ERROR', f'{type(exc).__name__}: {exc}', None)
                digest, size, width, height, valid, code, detail, key = entry
                if key is not None:
                    cache_rows.append((project_key, str(source), relative, VALIDATOR, *key[:2], size, key[3], key[4], digest,
                                       width, height, int(valid), code, detail, build_id))
            digest, size, width, height, valid, code, detail, _key = entry
            note = None
            if valid:  # the stat cache above keeps the decode result; annotations are read fresh at every build
                found = scanner.scan(path, relative, (width, height))
                if found.format is not None or found.error is not None:
                    note = (relative, found.format, json.dumps(list(found.labels)), json.dumps([list(item) for item in found.files]),
                            found.error)
                    notes.append(note)
                    counts['notes'] += 1
                    counts['annotated'] += int(found.format is not None and found.error is None)
                if found.error is not None:
                    counts['annotation_errors'] += 1
                    if task in ANNOTATED_TASKS and not overlaid(path):  # training reads these files: the entry cannot be used
                        code, _, detail = found.error.partition(': ')
                        valid = False
            row = (relative, image_uuid, digest, size, width, height, label, split, int(valid), code, detail, via_link)
            rows.append(row)
            line = list(row[:9]) + ([] if note is None else [list(note[1:])])
            manifest.update(json.dumps(line, separators=(',', ':')).encode() + b'\n')
            counts['images'] += 1
            counts['valid'] += int(valid)
            if len(rows) >= _BATCH:
                flush()
            if progress is not None:
                progress(index, total)
        for gap in inventory.gaps:
            manifest.update(json.dumps(['gap', *gap], separators=(',', ':')).encode() + b'\n')
        errors = counts['images'] - counts['valid']
        holds = errors or inventory.gaps
        state = 'rejected' if holds and invalid_policy == 'reject' else 'prepared'
        revision_id = uuid.uuid4().hex
        if before_seal is not None:
            before_seal()  # e.g. the import job confirms it still owns its attempt
        try:
            with self._tx(db):
                write_batch()
                staged = db.execute('SELECT COUNT(*) FROM dataset_index_staging WHERE build_id=?', (build_id,)).fetchone()[0]
                staged_notes = db.execute('SELECT COUNT(*) FROM dataset_annotation_staging WHERE build_id=?', (build_id,)).fetchone()[0]
                if db.execute('SELECT 1 FROM dataset_builds WHERE build_id=?', (build_id,)).fetchone() is None or (
                        staged, staged_notes) != (counts['images'], counts['notes']):
                    raise RuntimeError(f'This build lost staged rows ({staged} of {counts["images"]}); no revision was recorded')
                details = self._seal(db, revision_id, build_id, project_key, source, project, task, invalid_policy, state,
                                     manifest, counts, errors, inventory, follow_links, parent_revision, publication_key,
                                     scan_prefix)
        except sqlite3.IntegrityError:
            replay = self._published(project_key, publication_key) if publication_key is not None else None
            if replay is None:
                raise
            with self._tx(db):  # this build's own staged rows are not part of any revision
                self._drop_build(db, build_id)
            return replay  # another attempt of the same publication sealed first: its revision is the published one
        return RevisionReceipt(revision_id, state, manifest.hexdigest(), counts['images'], counts['valid'], errors,
                               invalid_policy, inventory.skipped_links, len(inventory.gaps), counts['reused'],
                               counts['reused'] == 0, **details)

    def _seal(self, db, revision_id, build_id, project_key, source, project, task, invalid_policy, state, manifest, counts,
              errors, inventory, follow_links, parent_revision, publication_key, scan_prefix) -> dict:
        db.execute('INSERT INTO dataset_revisions VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (revision_id, project_key, str(source), str(project), task, invalid_policy, state, manifest.hexdigest(),
                    counts['images'], counts['valid'], errors, len(inventory.gaps), inventory.skipped_links,
                    counts['reused'], int(counts['reused'] == 0), int(follow_links), parent_revision, publication_key,
                    time.time_ns()))
        db.execute(f'INSERT INTO dataset_index_images SELECT ?, {", ".join(_COLUMNS)} FROM dataset_index_staging WHERE build_id=?',
                   (revision_id, build_id))
        db.execute('INSERT INTO dataset_index_annotations SELECT ?, relative_path, format, labels, files, error '
                   'FROM dataset_annotation_staging WHERE build_id=?', (revision_id, build_id))
        db.executemany('INSERT INTO dataset_index_gaps VALUES(?, ?, ?)', [(revision_id, *gap) for gap in inventory.gaps])
        groups = db.execute(_GROUPS + ' SELECT COUNT(*), COALESCE(SUM(members), 0), COALESCE(SUM(labelings > 1), 0), '
                            'COALESCE(SUM(splits > 1), 0) FROM groups', (revision_id,)).fetchone()
        details = dict(zip(_DETAILS, (counts['annotated'], counts['annotation_errors'], *groups, task in ANNOTATED_TASKS)))
        db.execute('INSERT INTO dataset_revision_details VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
                   (revision_id, *(int(value) for value in details.values())))
        self._drop_build(db, build_id)
        # Cache rows of files this build no longer found under its own scan root are dropped.
        db.execute("DELETE FROM dataset_stat_cache WHERE project_key=? AND source_root=? AND last_build<>? "
                   "AND (?='' OR substr(relative_path, 1, length(?))=?)",
                   (project_key, str(source), build_id, scan_prefix, scan_prefix, scan_prefix))
        return details

    def _published(self, project_key: str, publication_key: str) -> Optional[RevisionReceipt]:
        with self._connect() as db:
            row = db.execute(f'{_REVISION_ROWS} WHERE r.project_key=? AND r.publication_key=?',
                             (project_key, publication_key)).fetchone()
        return None if row is None else RevisionReceipt(
            row['revision_id'], row['state'], row['manifest_sha256'], row['image_count'], row['valid_count'], row['error_count'],
            row['invalid_policy'], row['skipped_links'], row['unreadable_folders'], row['reused_entries'], bool(row['verified_all']),
            **{name: (None if row[name] is None else bool(row[name]) if name == 'annotations_bind' else row[name])
               for name in _DETAILS})

    def revisions(self, project_key: str) -> list[dict]:
        """Revisions newest first; ``annotations_scanned`` is false for revisions sealed before schema 3."""
        with self._connect() as db:
            active = db.execute('SELECT revision_id FROM dataset_active WHERE project_key=?', (project_key,)).fetchone()
            return [{**dict(row), 'annotations_scanned': row['annotated'] is not None,
                     'active': bool(active and active[0] == row['revision_id'])} for row in db.execute(
                f'{_REVISION_ROWS} WHERE r.project_key=? ORDER BY r.created_ns DESC', (project_key,))]

    def active(self, project_key: str) -> Optional[str]:
        with self._connect() as db:
            row = db.execute('SELECT revision_id FROM dataset_active WHERE project_key=?', (project_key,)).fetchone()
            return row[0] if row else None

    def activate(self, project_key: str, revision_id: str, expected_active: Optional[str]) -> str:
        """Make a prepared revision active, only if the active revision is still the expected one (a repeat is a no-op)."""
        with self._tx() as db:
            row = db.execute('SELECT state, image_count, valid_count FROM dataset_revisions WHERE revision_id=? AND project_key=?',
                             (revision_id, project_key)).fetchone()
            if row is None:
                raise KeyError(revision_id)
            if row['state'] == 'rejected':
                raise RevisionNotActivatable('This revision has invalid entries or unreadable folders under the reject policy')
            if row['image_count'] == 0:
                raise RevisionNotActivatable('This revision has no images')
            if row['valid_count'] == 0:
                raise RevisionNotActivatable('No image of this revision is valid; every entry would be excluded')
            current = db.execute('SELECT revision_id FROM dataset_active WHERE project_key=?', (project_key,)).fetchone()
            current = current[0] if current else None
            if current == revision_id:
                return revision_id
            if current != expected_active:
                raise StaleActiveRevision('The active revision changed; reload and confirm again')
            db.execute('INSERT OR REPLACE INTO dataset_active VALUES(?, ?)', (project_key, revision_id))
            return revision_id

    def gaps(self, project_key: str, revision_id: str) -> list[dict]:
        with self._connect() as db:
            self._owned(db, project_key, revision_id)
            return [dict(row) for row in db.execute(
                'SELECT relative_path, reason FROM dataset_index_gaps WHERE revision_id=? ORDER BY relative_path', (revision_id,))]

    @staticmethod
    def _cursor(cursor: Optional[str], binding: str) -> str:
        if not cursor:
            return ''
        if len(cursor) > _MAX_CURSOR:
            raise ValueError('Invalid cursor')
        try:
            bound, after = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        except (ValueError, TypeError) as exc:
            raise ValueError('Invalid cursor') from exc
        if bound != binding:
            raise ValueError('The cursor belongs to another revision or filter')
        if not isinstance(after, str):
            raise ValueError('Invalid cursor')
        return after

    @staticmethod
    def _owned(db, project_key: str, revision_id: str) -> None:
        if db.execute('SELECT 1 FROM dataset_revisions WHERE revision_id=? AND project_key=?', (revision_id, project_key)).fetchone() is None:
            raise KeyError(revision_id)

    @staticmethod
    def _image_row(row) -> dict:
        item = dict(row)
        item['annotation_labels'] = json.loads(item['annotation_labels']) if item['annotation_labels'] is not None else []
        item['annotation_files'] = [{'path': path, 'sha256': digest} for path, digest in json.loads(item['annotation_files'])] \
            if item['annotation_files'] is not None else []
        return item

    def page(self, project_key: str, revision_id: str, *, cursor: Optional[str] = None, limit: int = 100,
             label: Optional[str] = None, split: Optional[str] = None, valid: Optional[bool] = None,
             annotation_label: Optional[str] = None, annotation_error: Optional[bool] = None) -> dict:
        """Rows of one immutable revision in (relative_path) order with their source annotations; the cursor is bound to
        the revision and filters. ``label`` is the folder label, ``annotation_label`` a label of the source annotations."""
        limit = max(1, min(int(limit), 500))
        filters = {'label': label, 'split': split, 'valid': valid}
        if annotation_label is not None:
            filters['annotation_label'] = annotation_label
        if annotation_error is not None:
            filters['annotation_error'] = annotation_error
        binding = hashlib.sha256(json.dumps([project_key, revision_id, filters], sort_keys=True).encode()).hexdigest()[:16]
        after = self._cursor(cursor, binding)
        with self._connect() as db:
            self._owned(db, project_key, revision_id)
            clauses, params = ['i.revision_id=?', 'i.relative_path>?'], [revision_id, after]
            for column, value in (('i.label', label), ('i.split', split)):
                if value is not None:
                    clauses.append(f'{column}=?')
                    params.append(value)
            if valid is not None:
                clauses.append('i.valid=?')
                params.append(int(valid))
            if annotation_label is not None:
                clauses.append('EXISTS (SELECT 1 FROM json_each(a.labels) WHERE json_each.value=?)')
                params.append(annotation_label)
            if annotation_error is not None:
                clauses.append('a.error IS NOT NULL' if annotation_error else 'a.error IS NULL')
            rows = [self._image_row(row) for row in db.execute(
                f'{_IMAGE_ROWS} WHERE {" AND ".join(clauses)} ORDER BY i.relative_path LIMIT ?', (*params, limit + 1))]
        more = len(rows) > limit
        rows = rows[:limit]
        token = base64.urlsafe_b64encode(json.dumps([binding, rows[-1]['relative_path']]).encode()).decode() if more and rows else None
        return {'revision_id': revision_id, 'items': rows, 'next_cursor': token}

    def duplicates(self, project_key: str, revision_id: str, *, cursor: Optional[str] = None, limit: int = 50,
                   kind: Optional[str] = None) -> dict:
        """Duplicate groups of one revision in digest order, each with up to 50 members (``members`` is the full count).
        ``kind`` = 'conflicting' or 'cross_split' keeps only those groups."""
        if kind not in (None, 'conflicting', 'cross_split'):
            raise ValueError("kind must be 'conflicting' or 'cross_split'")
        limit = max(1, min(int(limit), 200))
        binding = hashlib.sha256(json.dumps([project_key, revision_id, 'duplicates', kind]).encode()).hexdigest()[:16]
        after = self._cursor(cursor, binding)
        having = {'conflicting': ' AND labelings > 1', 'cross_split': ' AND splits > 1'}.get(kind, '')
        with self._connect() as db:
            self._owned(db, project_key, revision_id)
            groups = [dict(row) for row in db.execute(
                f'{_GROUPS} SELECT * FROM groups WHERE sha256 > ?{having} ORDER BY sha256 LIMIT ?', (revision_id, after, limit + 1))]
            more = len(groups) > limit
            groups = groups[:limit]
            for group in groups:
                group['conflicting'], group['cross_split'] = group.pop('labelings') > 1, group.pop('splits') > 1
                group['items'] = [self._image_row(row) for row in db.execute(
                    f'{_IMAGE_ROWS} WHERE i.revision_id=? AND i.sha256=? AND i.valid=1 ORDER BY i.relative_path LIMIT ?',
                    (revision_id, group['sha256'], _GROUP_MEMBERS))]
        token = base64.urlsafe_b64encode(json.dumps([binding, groups[-1]['sha256']]).encode()).decode() if more and groups else None
        return {'revision_id': revision_id, 'groups': groups, 'next_cursor': token}
