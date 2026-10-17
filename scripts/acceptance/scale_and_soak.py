"""Owned scale measurements and durable queue controls (S7-05).

Metadata rows are fixtures, not validated photographs. Queue controls record
REVIEW and never run a model or contact a camera/PLC. Short runs cannot satisfy
the separate 72-hour target-operation gate. No production project is modified.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import stat
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PIL import Image
from backend.engine.dataset_index import DatasetIndex
from backend.engine.dataset_loaders import decode_image_file
from backend.engine.image_query import query_images
from backend.engine.inspection_service import InspectionStore, InboxFull


def sha(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def host() -> dict:
    value = {'platform': platform.system(), 'architecture': platform.machine(), 'logical_cpus': os.cpu_count()}
    try:
        import psutil
        value.update(ram_total_bytes=psutil.virtual_memory().total,
                     process_rss_bytes=psutil.Process().memory_info().rss)
    except ImportError:
        value['ram_measurement'] = 'unavailable without psutil'
    return value


def peak_rss() -> int | None:
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == 'darwin' else value * 1024)
    except ImportError: return None


def summary(samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {'samples': len(samples), 'p95_ms': ordered[math.ceil(.95 * len(ordered)) - 1],
            'max_ms': ordered[-1], 'min_ms': ordered[0]}


def measure_scale(root: Path, *, rows: int = 100000, width: int = 10000,
                  height: int = 8000, repeats: int = 30) -> dict:
    if not 1 <= rows <= 100000 or not 1 <= repeats <= 100:
        raise ValueError('Use 1–100000 metadata rows and 1–100 measurement repeats')
    if not 8 <= width <= 12000 or not 8 <= height <= 12000 or width * height > 80000000:
        raise ValueError('Image dimensions must contain 8–12000 pixels per side and at most 80M pixels')
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=False)
    before = host(); disk_before = shutil.disk_usage(root).free
    index = DatasetIndex(root / 'metadata-fixture.sqlite3')
    revision, project = 'scale-fixture', 'isolated-scale-fixture'
    # Deliberately not an accepted/active production revision. The query path
    # is exercised on synthetic metadata; no source images are implied.
    with sqlite3.connect(index.path) as db:
        db.execute('''INSERT INTO dataset_revisions
          (revision_id,project_key,source_root,project_root,task,invalid_policy,state,manifest_sha256,
           image_count,valid_count,error_count,unreadable_folders,skipped_links,reused_entries,
           verified_all,follow_links,created_ns)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (revision,project,str(root/'metadata-only-not-images'),str(root),'classification','reject',
           'fixture_only','0'*64,rows,rows,0,0,0,0,0,0,time.time_ns()))
        db.executemany('''INSERT INTO dataset_index_images
          (revision_id,relative_path,image_uuid,sha256,size,width,height,label,split,valid,via_link)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
          ((revision,f'{"NG" if i % 2 else "OK"}/검사 {i:06}.png',hashlib.sha256(str(i).encode()).hexdigest(),
            '0'*64,0,32,32,'NG' if i % 2 else 'OK',
            'test' if i % 10 == 0 else 'val' if i % 10 == 1 else 'train',1,0) for i in range(rows)))
    paged, cursor, previous, digest, page_times = 0, None, '', hashlib.sha256(), []
    while True:
        start = time.perf_counter()
        page = query_images(index.path, project, revision, cursor=cursor, limit=200)
        page_times.append((time.perf_counter() - start) * 1000)
        for item in page['items']:
            current = item['relative_path']
            if current <= previous: raise RuntimeError('Duplicate or unordered metadata page')
            digest.update(current.encode()); previous = current; paged += 1
        cursor = page['next_cursor']
        if cursor is None: break
    if paged != rows: raise RuntimeError('Metadata page count differs from fixture')
    searches = {}
    for name, query, filters in [('ng', '검사', {'label':'NG'}), ('missing', 'missing_%', {}),
                                  ('test', None, {'split':'test','state':'valid'})]:
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            page = query_images(index.path, project, revision, query=query, filters=filters, limit=100)
            times.append((time.perf_counter() - start) * 1000)
        searches[name] = {**summary(times), 'returned': len(page['items'])}
    large = root / 'large-original.png'
    image = Image.new('RGB', (width, height), (32, 96, 160))
    try: image.save(large)
    finally: image.close()
    original = sha(large); start = time.perf_counter(); decoded = decode_image_file(large)
    elapsed = (time.perf_counter() - start) * 1000
    if not decoded.valid: raise RuntimeError(f'Large-image decode failed: {decoded.error_code}')
    unchanged = sha(large) == original
    if not unchanged: raise RuntimeError('Large source changed during measurement')
    with sqlite3.connect(index.path) as db: db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return {'schema_version':1, 'host_before':before, 'host_after':host(),
            'process_peak_rss_bytes':peak_rss(), 'disk_bytes_used':disk_before-shutil.disk_usage(root).free,
            'metadata':{'fixture_kind':'metadata_only_not_image_inventory','rows':rows,'paged_rows':paged,
                        'unique_ordered_rows':True,'paged_paths_sha256':digest.hexdigest(),
                        'paging':summary(page_times),'searches':searches,'sqlite_bytes':index.path.stat().st_size},
            'large_image':{'dimensions':[width,height],'decoded':True,'decode_ms':elapsed,
                           'file_bytes':large.stat().st_size,'sha256':original,'source_unchanged':unchanged},
            'target_support_approved':False,'soak_72h_completed':False}


def queue_control(root: Path, *, cycles: int = 100, capacity: int = 50) -> dict:
    if not 1 <= cycles <= 10000 or not 1 <= capacity <= 100:
        raise ValueError('Use 1–10000 cycles and 1–100 outstanding control jobs')
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=False)
    image = root / 'queue-control.png'; Image.new('RGB',(32,32),'gray').save(image)
    original = sha(image); started = time.monotonic()
    state = root / 'owned-queue'; completed = duplicate = refused = 0
    store = InspectionStore(state,max_outstanding=capacity)
    for cycle in range(cycles):
        for i in range(capacity):
            key = f'control-{cycle}-{i}'
            job = store.enqueue(image,'file',idempotency_key=key)
            if store.enqueue(image,'file',idempotency_key=key) != job:
                raise RuntimeError('Duplicate input created another job')
            duplicate += 1
        try: store.enqueue(image,'file',idempotency_key=f'overflow-{cycle}')
        except InboxFull: refused += 1
        else: raise RuntimeError('Queue capacity did not refuse another input')
        # Durable restart at the same store, not a reset/new database.
        store = InspectionStore(state,max_outstanding=capacity); store.recover()
        for _ in range(capacity):
            job = store.claim()
            if job is None: raise RuntimeError('A queued control job disappeared on reopen')
            store.finish(job['job_id'],result={'final_verdict':'REVIEW','queue_control':True})
            completed += 1
        if store.pending_count(): raise RuntimeError('Control backlog was not drained')
    reopened = InspectionStore(state,max_outstanding=capacity)
    with sqlite3.connect(reopened.database) as db:
        count = db.execute("SELECT COUNT(*) FROM jobs WHERE state='completed' AND verdict='REVIEW'").fetchone()[0]
    if count != completed or sha(image) != original: raise RuntimeError('Reopened jobs or original source changed')
    return {'fixture_kind':'queue_control_without_model_or_target','capacity':capacity,'cycles':cycles,
            'completed':completed,'duplicate_requests':duplicate,'backpressure_refusals':refused,
            'reopened_completed':count,'source_unchanged':True,'elapsed_seconds':time.monotonic()-started,
            'actual_model_inference':False,'soak_72h_completed':False,'target_support_approved':False}


def _photo_raw9(value: os.stat_result) -> list[int]:
    return [value.st_dev, value.st_ino, value.st_mode, value.st_nlink, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_uid, value.st_gid]


def _photo_plain_path(path: Path, *, directory: bool = False) -> os.stat_result:
    """Reject links in every component rather than resolving an input alias."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Photo inputs must use canonical absolute paths')
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current = current / part
            value = current.lstat()
            if stat.S_ISLNK(value.st_mode):
                raise ValueError('Photo input paths cannot contain symbolic links')
            if current != path and not stat.S_ISDIR(value.st_mode):
                raise ValueError('Photo input parents must be directories')
        value = path.lstat()
    except OSError as error:
        raise ValueError('Photo input path is unavailable') from error
    if directory:
        if not stat.S_ISDIR(value.st_mode):
            raise ValueError('Photo source root must be a directory')
    elif not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise ValueError('Photo inputs must be regular files with one link')
    return value


def _photo_pin(path: Path, *, limit: int, retain_body: bool = False) -> dict:
    before = _photo_raw9(_photo_plain_path(path))
    if not 0 < before[4] <= limit:
        raise ValueError('Photo input exceeds its positive byte bound')
    digest = hashlib.sha256(); size = 0; blocks = []
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            if _photo_raw9(os.fstat(stream.fileno())) != before:
                raise ValueError('Photo input identity changed before reading')
            while block := stream.read(min(1024 * 1024, limit + 1 - size)):
                size += len(block)
                if size > limit:
                    raise ValueError('Photo input changed beyond its byte bound')
                digest.update(block)
                if retain_body: blocks.append(block)
            if _photo_raw9(os.fstat(stream.fileno())) != before:
                raise ValueError('Photo input changed while reading')
    except OSError as error:
        raise ValueError('Photo input could not be pinned') from error
    if size != before[4] or _photo_raw9(_photo_plain_path(path)) != before:
        raise ValueError('Photo input changed while reading')
    result = {'path': str(path), 'size': size, 'sha256': digest.hexdigest(), 'raw9': before}
    if retain_body: result['body'] = b''.join(blocks)
    return result


def _photo_expect(pin: dict) -> None:
    actual = _photo_pin(Path(pin['path']), limit=pin['size'])
    if any(actual[key] != pin[key] for key in ('size', 'sha256', 'raw9')):
        raise ValueError('Photo input or manifest changed during measurement')


def _photo_json_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value: raise ValueError('Photo manifest contains a duplicate JSON key')
        value[key] = item
    return value


def _photo_output(root: Path, source: Path, manifest: Path) -> Path:
    if '..' in root.parts: raise ValueError('Photo output must use a canonical path')
    root = Path(os.path.abspath(root))
    for original in (source, manifest):
        if root.is_relative_to(original) or original.is_relative_to(root):
            raise ValueError('Photo output and original inputs must be separate')
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current = current / part
        try: value = current.lstat()
        except FileNotFoundError: break
        if stat.S_ISLNK(value.st_mode):
            raise ValueError('Photo output cannot contain symbolic links')
        if current == root:
            raise FileExistsError(root)
        if not stat.S_ISDIR(value.st_mode):
            raise ValueError('Photo output parents must be directories')
    return root


def _admit_photo_manifest(root: Path, manifest: Path, *, repeats: int = 1,
                          max_files: int = 100000, max_total_bytes: int = 2147483648,
                          max_file_bytes: int = 536870912) -> dict:
    for name, value, ceiling in [('repeats', repeats, 3), ('max_files', max_files, 100000),
                                  ('max_total_bytes', max_total_bytes, 2147483648),
                                  ('max_file_bytes', max_file_bytes, 536870912)]:
        if type(value) is not int or not 1 <= value <= ceiling:
            raise ValueError(f'{name} must be a bounded positive integer')
    manifest = Path(os.path.abspath(manifest))
    manifest_pin = _photo_pin(manifest, limit=16 * 1024 * 1024, retain_body=True)
    try:
        value = json.loads(manifest_pin.pop('body').decode('utf-8'),
                           object_pairs_hook=_photo_json_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON value')))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Photo manifest must be UTF-8 JSON') from error
    if (type(value) is not dict or set(value) != {'schema_version', 'kind', 'source_root', 'files'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['kind'] != 'photo_decode_measurement_manifest'):
        raise ValueError('Photo manifest schema differs from the measurement contract')
    raw_root = value['source_root']
    if type(raw_root) is not str or not raw_root or any(ord(c) < 32 or ord(c) == 127 for c in raw_root):
        raise ValueError('Photo source root must be a canonical absolute path')
    source = Path(raw_root)
    if str(source) != raw_root:
        raise ValueError('Photo source root contains a path alias')
    _photo_plain_path(source, directory=True)
    output = _photo_output(Path(root), source, manifest)
    rows = value['files']
    if type(rows) is not list or not 1 <= len(rows) <= max_files:
        raise ValueError('Photo manifest must contain a bounded nonempty file list')
    previous = ''; total = 0; identities = set(); admitted = []
    for row in rows:
        if type(row) is not dict or set(row) != {'relative_path', 'size', 'sha256'}:
            raise ValueError('Photo manifest file record has unexpected fields')
        name, size, digest = row['relative_path'], row['size'], row['sha256']
        if (type(name) is not str or not name or name <= previous or name.startswith('/')
                or '\\' in name or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or any(part in ('', '.', '..') for part in name.split('/'))):
            raise ValueError('Photo relative paths must be unique ordered canonical POSIX paths')
        if type(size) is not int or not 1 <= size <= max_file_bytes:
            raise ValueError('Photo size exceeds the per-file read budget')
        if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Photo SHA256 must be lowercase hexadecimal')
        total += size
        if total > max_total_bytes: raise ValueError('Photo manifest exceeds the total read budget')
        path = source / name
        observed = _photo_plain_path(path)
        identity = (observed.st_dev, observed.st_ino)
        if identity in identities or observed.st_size != size:
            raise ValueError('Photo input alias or declared size differs')
        identities.add(identity)
        admitted.append({**row, 'path': str(path), 'raw9': _photo_raw9(observed)})
        previous = name
    # Validate every declared row and budget before reading photo bodies or decoding.
    for pin in admitted: _photo_expect(pin)
    _photo_expect(manifest_pin)
    return {'output': output, 'manifest': manifest_pin, 'source': source,
            'files': admitted, 'source_bytes': total, 'repeats': repeats}


def measure_photo_manifest(root: Path, manifest: Path, *, repeats: int = 1,
                           max_files: int = 100000, max_total_bytes: int = 2147483648,
                           max_file_bytes: int = 536870912) -> dict:
    """Measure pinned existing photo decoding; this does not approve a target."""
    admitted = _admit_photo_manifest(root, manifest, repeats=repeats, max_files=max_files,
                                     max_total_bytes=max_total_bytes, max_file_bytes=max_file_bytes)
    output = admitted['output']; output.mkdir(mode=0o700, parents=True, exist_ok=False)
    before = host(); disk_before = shutil.disk_usage(output).free
    files = []; timings = []; extensions = {}; succeeded = failed = 0
    for pin in admitted['files']:
        _photo_expect(pin); _photo_expect(admitted['manifest'])
        errors = []; dimensions = None; file_timings = []
        for _ in range(repeats):
            started = time.perf_counter()
            decoded = decode_image_file(Path(pin['path']))
            elapsed = (time.perf_counter() - started) * 1000
            timings.append(elapsed); file_timings.append(elapsed)
            if decoded.dimensions is not None: dimensions = list(decoded.dimensions)
            if decoded.valid: succeeded += 1
            else:
                failed += 1; errors.append(decoded.error_code)
            _photo_expect(pin); _photo_expect(admitted['manifest'])
        extension = Path(pin['relative_path']).suffix.lower()
        extensions[extension] = extensions.get(extension, 0) + 1
        files.append({key: pin[key] for key in ('relative_path', 'size', 'sha256')})
        files[-1].update(dimensions=dimensions, decoded=not errors, decode_failures=len(errors),
                         error_codes=errors, timing=summary(file_timings))
    for pin in admitted['files']: _photo_expect(pin)
    _photo_expect(admitted['manifest'])
    disk_after = shutil.disk_usage(output).free
    manifest_result = {key: admitted['manifest'][key] for key in ('path', 'size', 'sha256')}
    manifest_result.update(source_root=str(admitted['source']), file_count=len(files),
                           source_bytes=admitted['source_bytes'],
                           ordered_paths_sha256=hashlib.sha256(json.dumps(
                               [pin['relative_path'] for pin in admitted['files']],
                               ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest())
    return {'schema_version': 1, 'kind': 'manifest_photo_decode_measurement',
            'manifest': manifest_result, 'files': files, 'extension_counts': extensions,
            'decode': {'attempted': len(timings), 'succeeded': succeeded, 'failed': failed,
                       'all_files_decoded': failed == 0, 'timing': summary(timings),
                       'repeats_per_file': repeats, 'cache_state': 'uncontrolled_os_cache'},
            'host_before': before, 'host_after': host(), 'process_peak_rss_bytes': peak_rss(),
            'disk_free_before_bytes': disk_before, 'disk_free_after_bytes': disk_after,
            'disk_free_change_bytes': disk_before - disk_after,
            'manifest_unchanged': True, 'source_unchanged': True,
            'actual_model_inference': False, 'model_quality_approved': False,
            'performance_approved': False, 'scale_10k_100k_qualified': False,
            'target_support_approved': False, 'soak_72h_completed': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rows',type=int,choices=[10000,100000],default=100000)
    parser.add_argument('--queue-cycles',type=int,default=100)
    parser.add_argument('--photo-manifest',type=Path)
    args = parser.parse_args()
    if args.photo_manifest is not None:
        # Admit every original before claiming even the outer CLI output. The
        # public helper rechecks custody; no photo is decoded by this preflight.
        _admit_photo_manifest(args.output, args.photo_manifest)
        args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
        result = {'real_photos':measure_photo_manifest(args.output/'real-photos',args.photo_manifest)}
    else:
        args.output.mkdir(parents=True,exist_ok=False)
        result = {'scale':measure_scale(args.output/'scale',rows=args.rows),
                  'queue':queue_control(args.output/'queue',cycles=args.queue_cycles)}
    target = args.output/'receipt.json'
    if args.photo_manifest is not None:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2); stream.flush(); os.fsync(stream.fileno())
    else:
        with target.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2); stream.flush(); os.fsync(stream.fileno())
    print(json.dumps({'receipt':str(target),'sha256':sha(target),'soak_72h_completed':False}))


if __name__ == '__main__': main()
