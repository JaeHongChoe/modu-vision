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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rows',type=int,choices=[10000,100000],default=100000)
    parser.add_argument('--queue-cycles',type=int,default=100)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    result = {'scale':measure_scale(args.output/'scale',rows=args.rows),
              'queue':queue_control(args.output/'queue',cycles=args.queue_cycles)}
    target = args.output/'receipt.json'
    with target.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2); stream.flush(); os.fsync(stream.fileno())
    print(json.dumps({'receipt':str(target),'sha256':sha(target),'soak_72h_completed':False}))


if __name__ == '__main__': main()
