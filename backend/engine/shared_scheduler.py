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
            columns={row[1] for row in conn.execute('PRAGMA table_info(leases)')}
            for name,definition in {'memory_budget_mb':'INTEGER NOT NULL DEFAULT 0','allow_sharing':'INTEGER NOT NULL DEFAULT 0',
                                    'task':'TEXT','project_id':'TEXT','account_id':'TEXT'}.items():
                if name not in columns:conn.execute(f'ALTER TABLE leases ADD COLUMN {name} {definition}')
            conn.execute('CREATE TABLE IF NOT EXISTS devices(host TEXT NOT NULL,selector TEXT NOT NULL,uuid TEXT NOT NULL,parent_uuid TEXT,memory_mb INTEGER NOT NULL,PRIMARY KEY(host,selector))')
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10); conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL'); return conn
    @staticmethod
    def conflict(a, b):
        if a in (None, '', 'all') or b in (None, '', 'all'): return True
        aa, bb = a.split(','), b.split(',')
        numeric = all(x.isdecimal() for x in aa+bb)
        uuid_ids = all(x.startswith(('GPU-','MIG-')) for x in aa+bb)
        return bool(set(aa) & set(bb)) if numeric or uuid_ids else True
    def configure_devices(self,host,devices):
        """Publish probe-observed identities; do not change active capacity claims."""
        rows=[]
        for row in devices:
            selector=str(row['selector']);identifier=str(row['uuid']);memory=row['memory_mb']
            if not selector or not identifier or type(memory) is not int or memory<=0:raise ValueError('Invalid observed device capacity')
            rows.append((host,selector,identifier,row.get('parent_uuid'),memory))
        if len({r[1] for r in rows})!=len(rows):raise ValueError('Duplicate device selector')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing=[tuple(row) for row in conn.execute('SELECT host,selector,uuid,parent_uuid,memory_mb FROM devices WHERE host=? ORDER BY selector',(host,))]
            if conn.execute('SELECT 1 FROM leases WHERE host=?',(host,)).fetchone() and sorted(rows,key=lambda r:r[1])!=existing:
                raise ValueError('Observed capacity changed while a reservation is active; reconcile jobs first')
            conn.execute('DELETE FROM devices WHERE host=?',(host,))
            conn.executemany('INSERT INTO devices VALUES(?,?,?,?,?)',rows)

    def devices(self,host=None):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM devices'+(' WHERE host=?' if host else '')+' ORDER BY host,selector',(host,) if host else ())]

    def _identities(self,conn,host,selector):
        result=[]
        if selector in (None,'','all'):
            observed=[dict(row) for row in conn.execute('SELECT * FROM devices WHERE host=?',(host,))]
            return observed or [{'uuid':'all','parent_uuid':None,'memory_mb':None}]
        for part in selector.split(','):
            row=conn.execute('SELECT * FROM devices WHERE host=? AND (selector=? OR uuid=?)',(host,part,part)).fetchone()
            result.append(dict(row) if row else {'uuid':part,'parent_uuid':None,'memory_mb':None})
        return result

    def _overlap(self,conn,host,a,b):
        if a in (None,'','all') or b in (None,'','all'):return True
        first=self._identities(conn,host,a);second=self._identities(conn,host,b)
        # Registered indexes and UUIDs refer to the same device. A physical GPU
        # overlaps all children; separate MIG UUIDs have their own capacities.
        for left in first:
            for right in second:
                if left['uuid']==right['uuid'] or left['uuid']==right['parent_uuid'] or right['uuid']==left['parent_uuid']:return True
        if all(row['memory_mb'] for row in first+second):return False
        if any(row['uuid'].startswith('MIG-') and not row['memory_mb'] for row in first+second):return True
        return self.conflict(a,b)

    def _available(self,conn,host,selector,memory_budget_mb,allow_sharing,job_id=None):
        identities=self._identities(conn,host,selector)
        if allow_sharing:
            if type(memory_budget_mb) is not int or memory_budget_mb<=0:raise ValueError('Shared jobs require a positive memory budget')
            if selector in (None,'','all') or len(identities)!=1 or not identities[0]['memory_mb']:raise ValueError('Sharing requires one device with probe-observed capacity')
            capacity=min(row[0] for row in conn.execute('SELECT memory_mb FROM devices WHERE uuid=?',(identities[0]['uuid'],)))
            if memory_budget_mb>capacity:raise ValueError('Requested memory exceeds observed capacity')
        overlapping=[]
        for row in conn.execute('SELECT * FROM leases'):
            if row['job_id']==job_id:continue
            if row['host']==host:
                overlap=self._overlap(conn,host,row['selector'],selector)
            else:
                previous=self._identities(conn,row['host'],row['selector'])
                overlap=any(a['memory_mb'] and b['memory_mb'] and (a['uuid']==b['uuid'] or a['uuid']==b['parent_uuid'] or b['uuid']==a['parent_uuid']) for a in identities for b in previous)
            if overlap:overlapping.append(row)
        if not overlapping:return True
        if not allow_sharing or any(not row['allow_sharing'] for row in overlapping):return False
        target=identities[0]['uuid']
        if any(self._identities(conn,row['host'],row['selector'])[0]['uuid']!=target for row in overlapping):return False
        return sum(row['memory_budget_mb'] for row in overlapping)+memory_budget_mb<=capacity

    def available(self,host,selector='all',*,memory_budget_mb=0,allow_sharing=False):
        with self.connect() as conn:
            return self._available(conn,host,selector,memory_budget_mb,allow_sharing)

    def acquire(self, job_id, host, selector='all', *, remote=False,memory_budget_mb=0,allow_sharing=False,task=None,project_id=None,account_id=None):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM leases WHERE remote=0 AND uncertain=0 AND expires<?', (time.time(),))
            row=conn.execute('SELECT * FROM leases WHERE job_id=?',(job_id,)).fetchone()
            if row:
                if row['owner']!=self.owner:return False
                if (row['host'],row['selector'],row['memory_budget_mb'],bool(row['allow_sharing']))!=(host,selector,memory_budget_mb,bool(allow_sharing)):
                    raise ValueError('An existing reservation cannot change its device or memory claim')
                conn.execute('UPDATE leases SET expires=? WHERE job_id=?',(time.time()+self.lease_seconds,job_id));return True
            if not self._available(conn,host,selector,memory_budget_mb,allow_sharing):return False
            conn.execute('INSERT INTO leases(job_id,host,selector,owner,expires,remote,memory_budget_mb,allow_sharing,task,project_id,account_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (job_id,host,selector,self.owner,time.time()+self.lease_seconds,int(remote),memory_budget_mb,int(allow_sharing),task,project_id,account_id))
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
def compute_lease_scope(job_id, device, *, leases=None,memory_budget_mb=0,allow_sharing=False,task=None,project_id=None,account_id=None):
    """Hold the app-wide local GPU reservation through a family job's lifetime."""
    if str(device) == 'cpu':
        yield None
        return
    leases = leases or shared_leases()
    selector=str(device).removeprefix('cuda:') if str(device).startswith('cuda:') else 'all'
    if not leases.acquire(job_id, 'local-compute', selector,memory_budget_mb=memory_budget_mb,allow_sharing=allow_sharing,task=task,project_id=project_id,account_id=account_id):
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
