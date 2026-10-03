"""SQLite leases shared by app processes; uncertain remote runs remain reserved."""
from __future__ import annotations
import os
import sqlite3
from backend.engine.sqlite_wal import use_wal  # a concurrent WAL switch is retried, not failed
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
            # Additive only: an older app shares this file and keeps inserting with its own column list.
            # fence: the ledger attempt holding the row; ledger_job: written by the job scheduler;
            # app_schema: NULL for rows an older app wrote (its version predates the column).
            for name,definition in {'memory_budget_mb':'INTEGER NOT NULL DEFAULT 0','allow_sharing':'INTEGER NOT NULL DEFAULT 0',
                                    'task':'TEXT','project_id':'TEXT','account_id':'TEXT',
                                    'fence':'INTEGER','ledger_job':'INTEGER NOT NULL DEFAULT 0','app_schema':'INTEGER'}.items():
                if name not in columns:
                    try:conn.execute(f'ALTER TABLE leases ADD COLUMN {name} {definition}')
                    except sqlite3.OperationalError as exc:
                        if 'duplicate column' not in str(exc):raise
            conn.execute('CREATE TABLE IF NOT EXISTS devices(host TEXT NOT NULL,selector TEXT NOT NULL,uuid TEXT NOT NULL,parent_uuid TEXT,memory_mb INTEGER NOT NULL,PRIMARY KEY(host,selector))')
    def connect(self, timeout=10):
        conn = sqlite3.connect(self.path, timeout=timeout); conn.row_factory = sqlite3.Row
        use_wal(conn, timeout); return conn
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
        return self._assess(conn,host,selector,memory_budget_mb,allow_sharing,job_id)[0]

    def _assess(self,conn,host,selector,memory_budget_mb,allow_sharing,job_id=None):
        """(available, overlapping rows): the rows decide why a claim waits."""
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
        if not overlapping:return True,[]
        if not allow_sharing or any(not row['allow_sharing'] for row in overlapping):return False,overlapping
        target=identities[0]['uuid']
        if any(self._identities(conn,row['host'],row['selector'])[0]['uuid']!=target for row in overlapping):return False,overlapping
        fits=sum(row['memory_budget_mb'] for row in overlapping)+memory_budget_mb<=capacity
        return fits,([] if fits else overlapping)

    def available(self,host,selector='all',*,memory_budget_mb=0,allow_sharing=False):
        with self.connect() as conn:
            return self._available(conn,host,selector,memory_budget_mb,allow_sharing)

    def acquire(self, job_id, host, selector='all', *, remote=False,memory_budget_mb=0,allow_sharing=False,task=None,project_id=None,account_id=None):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # A fenced row belongs to a ledger attempt: it never expires into free capacity (expiry makes it uncertain).
            conn.execute('DELETE FROM leases WHERE remote=0 AND uncertain=0 AND expires<? AND fence IS NULL', (time.time(),))
            row=conn.execute('SELECT * FROM leases WHERE job_id=?',(job_id,)).fetchone()
            if row:
                if row['owner']!=self.owner:return False
                if (row['host'],row['selector'],row['memory_budget_mb'],bool(row['allow_sharing']))!=(host,selector,memory_budget_mb,bool(allow_sharing)):
                    raise ValueError('An existing reservation cannot change its device or memory claim')
                conn.execute('UPDATE leases SET expires=? WHERE job_id=?',(time.time()+self.lease_seconds,job_id));return True
            if not self._available(conn,host,selector,memory_budget_mb,allow_sharing):return False
            conn.execute('INSERT INTO leases(job_id,host,selector,owner,expires,remote,memory_budget_mb,allow_sharing,task,project_id,account_id,app_schema) VALUES(?,?,?,?,?,?,?,?,?,?,?,2)',
                         (job_id,host,selector,self.owner,time.time()+self.lease_seconds,int(remote),memory_budget_mb,int(allow_sharing),task,project_id,account_id))
            return True

    # --- Ledger-scheduled reservations (S1-03) ---------------------------------------------------------------
    def older_app_active(self,host,timeout=10):
        """An unexpired local reservation written by an older app version on this host (drain: claim nothing there)."""
        with self.connect(timeout) as conn:
            return conn.execute('SELECT 1 FROM leases WHERE host=? AND app_schema IS NULL AND remote=0 AND expires>=?',
                                (host,time.time())).fetchone() is not None

    def acquire_for_job(self,job_id,host,selector='all',*,memory_budget_mb=0,allow_sharing=False,task=None,project_id=None,account_id=None,timeout=10):
        """Reserve devices for a scheduler claim: (acquired, blocking rows). The row is fenced after the ledger claim."""
        with self.connect(timeout) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM leases WHERE remote=0 AND uncertain=0 AND expires<? AND fence IS NULL', (time.time(),))
            row=conn.execute('SELECT * FROM leases WHERE job_id=?',(job_id,)).fetchone()
            if row:
                if row['owner']==self.owner and row['ledger_job']:
                    conn.execute('UPDATE leases SET expires=? WHERE job_id=?',(time.time()+self.lease_seconds,job_id));return True,[]
                return False,[dict(row)]
            available,blocking=self._assess(conn,host,selector,memory_budget_mb,allow_sharing)
            if not available:return False,[dict(row) for row in blocking]
            conn.execute('INSERT INTO leases(job_id,host,selector,owner,expires,remote,memory_budget_mb,allow_sharing,task,project_id,account_id,ledger_job,app_schema) VALUES(?,?,?,?,?,0,?,?,?,?,?,1,2)',
                         (job_id,host,selector,self.owner,time.time()+self.lease_seconds,memory_budget_mb,int(allow_sharing),task,project_id,account_id))
            return True,[]

    def stamp_fence(self,job_id,fence):
        with self.connect() as conn:
            return conn.execute('UPDATE leases SET fence=? WHERE job_id=? AND owner=?',(fence,job_id,self.owner)).rowcount==1

    def adopt_fenced(self,job_id,fence):
        """A restarted owner takes the reservation over under the attempt's new fence."""
        with self.connect() as conn:
            return conn.execute('UPDATE leases SET owner=?,fence=?,expires=?,uncertain=0 WHERE job_id=?',
                                (self.owner,fence,time.time()+self.lease_seconds,job_id)).rowcount==1

    def heartbeat_fenced(self,job_id,fence):
        with self.connect() as conn:
            return conn.execute('UPDATE leases SET expires=? WHERE job_id=? AND fence=?',(time.time()+self.lease_seconds,job_id,fence)).rowcount==1

    def mark_uncertain_fenced(self,job_id,fence):
        with self.connect() as conn:
            conn.execute('UPDATE leases SET uncertain=1 WHERE job_id=? AND fence=?',(job_id,fence))

    def release_fenced(self,job_id,fence):
        with self.connect() as conn:
            conn.execute('DELETE FROM leases WHERE job_id=? AND fence=?',(job_id,fence))

    def restamp_fence(self,job_id,fence):
        """A reattached attempt carries its new fence onto the reservation; the owner (the worker's heartbeat) is kept."""
        with self.connect() as conn:
            return conn.execute('UPDATE leases SET fence=?,uncertain=0,app_schema=2 WHERE job_id=? AND remote=0',(fence,job_id)).rowcount==1

    def mark_uncertain_local(self,job_id):
        """A launched local worker outlives this backend: its reservation stays held until exit evidence."""
        with self.connect() as conn:
            conn.execute('UPDATE leases SET uncertain=1 WHERE job_id=? AND remote=0',(job_id,))

    def release_unfenced(self,job_id):
        """Undo a reservation whose ledger claim did not happen (owned by this process, never fenced)."""
        with self.connect() as conn:
            conn.execute('DELETE FROM leases WHERE job_id=? AND owner=? AND fence IS NULL',(job_id,self.owner))
    def adopt(self, job_id):
        # Remote ownership changes only for expired leases, after durable journal recovery.
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM leases WHERE job_id=?', (job_id,)).fetchone()
            if row is None: return True
            if row['owner'] != self.owner and row['expires'] > time.time(): return False
            conn.execute('UPDATE leases SET owner=?,expires=?,app_schema=2 WHERE job_id=?', (self.owner,time.time()+self.lease_seconds,job_id)); return True
    def heartbeat(self, job_id):
        with self.connect() as conn:
            # A row this version keeps alive is no longer an older app's reservation (drain does not apply to it).
            return conn.execute('UPDATE leases SET expires=?,app_schema=2 WHERE job_id=? AND owner=?', (time.time()+self.lease_seconds,job_id,self.owner)).rowcount == 1
    def mark_uncertain(self, job_id):
        with self.connect() as conn:
            conn.execute('UPDATE leases SET uncertain=1 WHERE job_id=? AND owner=?', (job_id,self.owner))
    def release(self, job_id, *, terminal=False):
        with self.connect() as conn:
            conn.execute('DELETE FROM leases WHERE job_id=? AND owner=? AND (remote=0 OR ?=1)', (job_id,self.owner,int(terminal)))
    def release_uncertain(self, job_id, fence=None):
        """An operator settles a reservation whose worker exit could not be proven (S1-04). Only a row that is still
        uncertain and still under the fence the operator saw is removed, so a reservation re-taken meanwhile stays."""
        with self.connect() as conn:
            return conn.execute('DELETE FROM leases WHERE job_id=? AND uncertain=1 AND fence IS ?', (job_id, fence)).rowcount == 1
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
