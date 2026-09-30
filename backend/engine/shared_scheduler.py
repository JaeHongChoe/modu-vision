"""SQLite leases shared by app processes; uncertain remote runs remain reserved."""
from __future__ import annotations
import os
import sqlite3
import time
import uuid
import threading
from contextlib import contextmanager
from pathlib import Path


class ResourceLeases:
    def __init__(self, path, *, owner=None, lease_seconds=30):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.owner = owner or f'{os.getpid()}:{uuid.uuid4().hex}'
        self.lease_seconds = lease_seconds
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS leases(job_id TEXT PRIMARY KEY,host TEXT,selector TEXT,owner TEXT,expires REAL,remote INTEGER,uncertain INTEGER DEFAULT 0)')
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10); conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL'); return conn
    @staticmethod
    def conflict(a, b):
        if a in (None, '', 'all') or b in (None, '', 'all'): return True
        aa, bb = a.split(','), b.split(',')
        numeric = all(x.isdecimal() for x in aa+bb)
        uuid_ids = all(x.startswith('GPU-') for x in aa+bb)
        return bool(set(aa) & set(bb)) if numeric or uuid_ids else True
    def acquire(self, job_id, host, selector='all', *, remote=False):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM leases WHERE remote=0 AND expires<?', (time.time(),))
            for row in conn.execute('SELECT * FROM leases WHERE host=?', (host,)):
                if row['job_id'] == job_id and row['owner'] == self.owner:
                    conn.execute('UPDATE leases SET expires=?,uncertain=0 WHERE job_id=?', (time.time()+self.lease_seconds,job_id)); return True
                if self.conflict(row['selector'], selector): return False
            conn.execute('INSERT INTO leases(job_id,host,selector,owner,expires,remote) VALUES(?,?,?,?,?,?)', (job_id,host,selector,self.owner,time.time()+self.lease_seconds,int(remote)))
            return True
    def adopt(self, job_id):
        # Remote ownership changes only for expired leases, after durable journal recovery.
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM leases WHERE job_id=?', (job_id,)).fetchone()
            if row is None: return True
            if row['owner'] != self.owner and row['expires'] > time.time(): return False
            conn.execute('UPDATE leases SET owner=?,expires=? WHERE job_id=?', (self.owner,time.time()+self.lease_seconds,job_id)); return True
    def heartbeat(self, job_id):
        with self.connect() as conn:
            return conn.execute('UPDATE leases SET expires=? WHERE job_id=? AND owner=?', (time.time()+self.lease_seconds,job_id,self.owner)).rowcount == 1
    def mark_uncertain(self, job_id):
        with self.connect() as conn:
            conn.execute('UPDATE leases SET uncertain=1 WHERE job_id=? AND owner=?', (job_id,self.owner))
    def release(self, job_id, *, terminal=False):
        with self.connect() as conn:
            conn.execute('DELETE FROM leases WHERE job_id=? AND owner=? AND (remote=0 OR ?=1)', (job_id,self.owner,int(terminal)))
    def list(self):
        with self.connect() as conn: return [dict(row) for row in conn.execute('SELECT * FROM leases ORDER BY host,job_id')]


def shared_leases():
    # One app-wide database, independent of the selected project.
    user_data = Path(os.environ.get('VISION_AI_STUDIO_USER_DATA_DIR', str(Path.home()/'.modu-vision')))
    path = Path(os.environ.get('VISION_RESOURCE_LEASE_DB', str(user_data/'resource_leases.sqlite3')))
    return ResourceLeases(path)


@contextmanager
def compute_lease_scope(job_id, device, *, leases=None):
    """Hold the app-wide local GPU reservation through a family job's lifetime."""
    if str(device) == 'cpu':
        yield None
        return
    leases = leases or shared_leases()
    # Generic training owns the entire local accelerator. Match that conservative
    # selector until all framework device selectors can be mapped to physical GPUs.
    if not leases.acquire(job_id, 'local-compute', 'all'):
        raise ValueError('Local compute is reserved by another training job')
    stop = threading.Event()
    interval = max(.005, min(10, leases.lease_seconds / 3))
    def heartbeat():
        while not stop.wait(interval):
            if not leases.heartbeat(job_id):
                return
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield leases
    finally:
        stop.set()
        thread.join(timeout=1)
        leases.release(job_id, terminal=True)
