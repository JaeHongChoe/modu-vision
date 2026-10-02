"""Persistent job ledger (S1-02): jobs, attempts, events, artifacts and cancel intents.

One SQLite file in the configured application data folder holds every project's
jobs; each row carries the explicit workspace, project namespace and actor of the
request that created it. An idempotency key is reserved atomically, before any
folder, project or process side effect, so a retried submission names the
original job and a different spec under the same key is refused. Attempts are
recorded before a worker is launched and carry a fencing token, so an earlier
owner can never report over a later one.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional
import uuid

from backend.engine.job_state import ACTIVE, TERMINAL, IllegalTransition, from_legacy, transition as next_state

SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs(
    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, project_key TEXT NOT NULL, project_id TEXT NOT NULL,
    actor_id TEXT NOT NULL, mode TEXT NOT NULL, kind TEXT NOT NULL, idempotency_key TEXT,
    spec_sha256 TEXT NOT NULL, spec_json TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL,
    parent_id TEXT REFERENCES jobs(id), output_dir TEXT, response_json TEXT, source TEXT NOT NULL,
    created_ns INTEGER NOT NULL, updated_ns INTEGER NOT NULL, registry_root TEXT, project_dir TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency
    ON jobs(workspace_id, project_key, actor_id, kind, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS attempts(
    job_id TEXT NOT NULL REFERENCES jobs(id), number INTEGER NOT NULL, fencing_token INTEGER NOT NULL,
    executor TEXT NOT NULL, owner_boot_id TEXT, owner_pid INTEGER, started_ns INTEGER NOT NULL, ended_ns INTEGER,
    PRIMARY KEY(job_id, number));
CREATE TABLE IF NOT EXISTS events(
    job_id TEXT NOT NULL REFERENCES jobs(id), seq INTEGER NOT NULL, revision INTEGER NOT NULL, event TEXT NOT NULL,
    from_state TEXT, to_state TEXT NOT NULL, payload_json TEXT, at_ns INTEGER NOT NULL, PRIMARY KEY(job_id, seq));
CREATE TABLE IF NOT EXISTS artifacts(
    job_id TEXT NOT NULL REFERENCES jobs(id), role TEXT NOT NULL, artifact_id TEXT NOT NULL,
    revision INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(job_id, role));
CREATE TABLE IF NOT EXISTS cancel_intents(
    job_id TEXT PRIMARY KEY REFERENCES jobs(id), actor_id TEXT NOT NULL, reason TEXT NOT NULL, requested_ns INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS quotas(
    project_key TEXT PRIMARY KEY, max_running INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS migrations(
    source_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, job_id TEXT, outcome TEXT NOT NULL, migrated_ns INTEGER NOT NULL);
'''


_ADDED_COLUMNS = (
    ('jobs', 'registry_root', 'TEXT'), ('jobs', 'project_dir', 'TEXT'),
    ('jobs', 'priority', 'INTEGER NOT NULL DEFAULT 0'), ('jobs', 'resources_json', 'TEXT'), ('jobs', 'budget_json', 'TEXT'),
    ('jobs', 'wait_reason', 'TEXT'), ('jobs', 'queued_ns', 'INTEGER'),
    ('attempts', 'lease_expires_ns', 'INTEGER'), ('attempts', 'worker_id', 'TEXT'),
)
# States in which a job holds an attempt (and counts against its project's quota).
HOLDING = ('running', 'stopping', 'detached', 'disconnected')


class JobConflict(ValueError):
    """The idempotency key was already reserved for a different spec."""


class QuotaExceeded(Exception):
    """The job's project already runs its allowed number of jobs."""


class StaleRevision(Exception):
    """The job changed since the caller read it."""


class StaleFencingToken(Exception):
    """A newer attempt owns the job; the caller's attempt may not report."""


class UnknownJob(KeyError):
    """No job with this id is recorded."""


class PublicationFailed(Exception):
    """A job's result could not be published; the failure is recorded and the job still ends."""


@dataclass(frozen=True)
class JobRef:
    id: str
    revision: int
    state: str
    created: bool = False


@dataclass(frozen=True)
class Attempt:
    number: int
    fencing_token: int


def spec_digest(spec: Any) -> str:
    """Canonical digest: key order and whitespace never change the identity of a spec."""
    canonical = json.dumps(spec, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def user_data_root() -> Path:
    """The configured application data folder (set by the desktop supervisor), else the standalone default."""
    return Path(os.environ.get('VISION_AI_STUDIO_USER_DATA_DIR') or Path.home() / '.modu-vision')


def default_ledger_path() -> Path:
    return user_data_root() / 'jobs' / 'ledger.sqlite3'


_shared_lock = threading.Lock()
_shared: Optional['JobStore'] = None


def ledger() -> 'JobStore':
    """The ledger in the configured application data folder, resolved again when that folder changes."""
    global _shared
    path = default_ledger_path()
    with _shared_lock:
        if _shared is None or _shared.path != path:
            _shared = JobStore(path)
        return _shared


def _context_fields(context: Any) -> tuple[str, str, str, str]:
    value = context.model_dump() if hasattr(context, 'model_dump') else dict(context)
    return value['workspace_id'], value['project_id'], value['actor_id'], value['mode']


class JobStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)
            # Additive migration for older ledgers (submission scope, then queue and attempt-lease columns).
            for table, column, definition in _ADDED_COLUMNS:
                columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
                if column not in columns:
                    try:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
                    except sqlite3.OperationalError as exc:
                        if 'duplicate column' not in str(exc):  # another process added it first
                            raise

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA foreign_keys=ON')
            yield db
        finally:
            db.close()

    @contextmanager
    def _tx(self):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
            except BaseException:
                db.execute('ROLLBACK')
                raise
            db.execute('COMMIT')

    @staticmethod
    def _job(db, job_id: str) -> sqlite3.Row:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise UnknownJob(job_id)
        return row

    @staticmethod
    def _event(db, job_id, revision, event, from_state, to_state, payload=None, now=None):
        seq = db.execute('SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE job_id=?', (job_id,)).fetchone()[0]
        db.execute('INSERT INTO events VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
                   (job_id, seq, revision, event, from_state, to_state,
                    json.dumps(payload, sort_keys=True, default=str) if payload is not None else None, now or time.time_ns()))

    def submit(self, context: Any, project_key: str, kind: str, spec: Any, idempotency_key: Optional[str] = None, *,
               job_id: Optional[str] = None, parent_id: Optional[str] = None, output_dir: Optional[str] = None,
               registry_root: Optional[str] = None, project_dir: Optional[str] = None) -> JobRef:
        """Reserve a job before any side effect; a repeated key returns the reserved job."""
        workspace_id, project_id, actor_id, mode = _context_fields(context)
        digest, now = spec_digest(spec), time.time_ns()
        with self._tx() as db:
            if idempotency_key is not None:
                row = db.execute('SELECT id, revision, state, spec_sha256 FROM jobs WHERE workspace_id=? AND project_key=?'
                                 ' AND actor_id=? AND kind=? AND idempotency_key=?',
                                 (workspace_id, project_key, actor_id, kind, idempotency_key)).fetchone()
                if row is not None:
                    if row['spec_sha256'] != digest:
                        raise JobConflict('This idempotency key was already used for a different request.')
                    return JobRef(row['id'], row['revision'], row['state'], False)
            identifier = job_id or uuid.uuid4().hex
            # Lineage is recorded only for a completed parent in the same workspace and namespace; any other
            # claimed parent (another project, unfinished, or a model the ledger never ran) stays unverified.
            verified_parent = None
            if parent_id is not None and db.execute(
                    "SELECT 1 FROM jobs WHERE id=? AND workspace_id=? AND project_key=? AND state='completed'",
                    (parent_id, workspace_id, project_key)).fetchone():
                verified_parent = parent_id
            db.execute('INSERT INTO jobs(id, workspace_id, project_key, project_id, actor_id, mode, kind, idempotency_key,'
                       ' spec_sha256, spec_json, state, revision, parent_id, output_dir, response_json, source, created_ns,'
                       ' updated_ns, registry_root, project_dir) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (identifier, workspace_id, project_key, project_id, actor_id, mode, kind, idempotency_key, digest,
                        json.dumps(spec, sort_keys=True, default=str), 'accepted', 1, verified_parent, output_dir, None, 'api', now, now,
                        registry_root, project_dir))
            self._event(db, identifier, 1, 'submit', None, 'accepted',
                        {'idempotency_key': idempotency_key, 'claimed_parent': parent_id,
                         'parent_verified': verified_parent is not None}, now)
            return JobRef(identifier, 1, 'accepted', True)

    def get(self, job_id: str) -> JobRef:
        with self._connect() as db:
            row = self._job(db, job_id)
            return JobRef(row['id'], row['revision'], row['state'])

    def record(self, job_id: str) -> dict:
        with self._connect() as db:
            return dict(self._job(db, job_id))

    def transition(self, job_id: str, expected_revision: int, event: str, payload: Any = None,
                   fencing_token: Optional[int] = None) -> JobRef:
        now = time.time_ns()
        with self._tx() as db:
            row = self._job(db, job_id)
            if fencing_token is not None:
                latest = db.execute('SELECT MAX(fencing_token) FROM attempts WHERE job_id=?', (job_id,)).fetchone()[0]
                if latest is None or fencing_token != latest:
                    raise StaleFencingToken(f'Attempt token {fencing_token} no longer owns {job_id}')
            if row['revision'] != expected_revision:
                raise StaleRevision(f'{job_id} is at revision {row["revision"]}, not {expected_revision}')
            state = next_state(row['state'], event)
            revision = row['revision'] + 1
            db.execute('UPDATE jobs SET state=?, revision=?, updated_ns=? WHERE id=?', (state, revision, now, job_id))
            if state in TERMINAL or event == 'requeue':  # a requeued job's next claim opens a new attempt
                db.execute('UPDATE attempts SET ended_ns=? WHERE job_id=? AND ended_ns IS NULL', (now, job_id))
            self._event(db, job_id, revision, event, row['state'], state, payload, now)
            return JobRef(job_id, revision, state)

    def finish(self, job_id: str, event: str, payload: Any = None, fencing_token: Optional[int] = None,
               publish: Optional[Any] = None) -> JobRef:
        """End a job and publish its result under the attempt's fence, in one transaction.

        The write lock is held from the fence check to the end event, so no newer attempt can begin in
        between: a stale owner neither ends the job nor publishes anything. ``publish`` returns an
        artifact reference (or None) or raises PublicationFailed, which is recorded as a retained failure.
        """
        now = time.time_ns()
        with self._tx() as db:
            row = self._job(db, job_id)
            latest = db.execute('SELECT MAX(fencing_token) FROM attempts WHERE job_id=?', (job_id,)).fetchone()[0]
            if (fencing_token is not None and fencing_token != latest) or (fencing_token is None and latest is not None):
                raise StaleFencingToken(f'Attempt token {fencing_token} does not own {job_id}')
            state = next_state(row['state'], event)
            revision = row['revision']
            if publish is not None:
                try:
                    reference = publish()
                except PublicationFailed as exc:
                    revision += 1
                    self._event(db, job_id, revision, 'receipt_link_failed', row['state'], row['state'], {'reason': str(exc)}, now)
                else:
                    if reference is not None:
                        value = reference.model_dump() if hasattr(reference, 'model_dump') else dict(reference)
                        db.execute('INSERT OR REPLACE INTO artifacts VALUES(?, ?, ?, ?, ?)',
                                   (job_id, 'receipt', value['id'], value['revision'], value['sha256']))
            revision += 1
            db.execute('UPDATE jobs SET state=?, revision=?, updated_ns=? WHERE id=?', (state, revision, now, job_id))
            if state in TERMINAL:
                db.execute('UPDATE attempts SET ended_ns=? WHERE job_id=? AND ended_ns IS NULL', (now, job_id))
            self._event(db, job_id, revision, event, row['state'], state, payload, now)
            return JobRef(job_id, revision, state)

    def begin_attempt(self, job_id: str, expected_revision: int, executor: str, owner_boot_id: Optional[str],
                      owner_pid: Optional[int]) -> Attempt:
        """Record an attempt before its worker starts; the newest attempt alone may report."""
        now = time.time_ns()
        with self._tx() as db:
            row = self._job(db, job_id)
            if row['revision'] != expected_revision:
                raise StaleRevision(f'{job_id} is at revision {row["revision"]}, not {expected_revision}')
            if row['state'] in TERMINAL:
                raise IllegalTransition(f'{job_id} already ended as {row["state"]}')
            number, token = db.execute('SELECT COALESCE(MAX(number), 0) + 1, COALESCE(MAX(fencing_token), 0) + 1'
                                       ' FROM attempts WHERE job_id=?', (job_id,)).fetchone()
            db.execute('INSERT INTO attempts(job_id, number, fencing_token, executor, owner_boot_id, owner_pid, started_ns)'
                       ' VALUES(?, ?, ?, ?, ?, ?, ?)', (job_id, number, token, executor, owner_boot_id, owner_pid, now))
            revision = row['revision'] + 1
            db.execute('UPDATE jobs SET revision=?, updated_ns=? WHERE id=?', (revision, now, job_id))
            self._event(db, job_id, revision, 'attempt', row['state'], row['state'],
                        {'number': number, 'executor': executor, 'owner_pid': owner_pid}, now)
            return Attempt(number, token)

    def attempts(self, job_id: str) -> list[dict]:
        with self._connect() as db:
            self._job(db, job_id)
            return [dict(row) for row in db.execute('SELECT * FROM attempts WHERE job_id=? ORDER BY number', (job_id,))]

    def events(self, job_id: str) -> list[dict]:
        with self._connect() as db:
            self._job(db, job_id)
            rows = db.execute('SELECT * FROM events WHERE job_id=? ORDER BY seq', (job_id,))
            return [{**dict(row), 'payload': json.loads(row['payload_json']) if row['payload_json'] else None} for row in rows]

    def record_event(self, job_id: str, event: str, payload: Any = None) -> None:
        """An event that does not change the job's state, e.g. a retained failure to link an artifact."""
        now = time.time_ns()
        with self._tx() as db:
            row = self._job(db, job_id)
            revision = row['revision'] + 1
            db.execute('UPDATE jobs SET revision=?, updated_ns=? WHERE id=?', (revision, now, job_id))
            self._event(db, job_id, revision, event, row['state'], row['state'], payload, now)

    def request_cancel(self, job_id: str, actor_id: str, reason: str) -> None:
        """Persist the intent even when the worker cannot be reached; recovery honours it."""
        now = time.time_ns()
        with self._tx() as db:
            row = self._job(db, job_id)
            if db.execute('INSERT OR IGNORE INTO cancel_intents VALUES(?, ?, ?, ?)', (job_id, actor_id, reason, now)).rowcount:
                revision = row['revision'] + 1
                db.execute('UPDATE jobs SET revision=?, updated_ns=? WHERE id=?', (revision, now, job_id))
                self._event(db, job_id, revision, 'cancel_requested', row['state'], row['state'],
                            {'actor_id': actor_id, 'reason': reason}, now)

    def cancel_intent(self, job_id: str) -> Optional[dict]:
        with self._connect() as db:
            row = db.execute('SELECT * FROM cancel_intents WHERE job_id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    def set_response(self, job_id: str, response: dict) -> None:
        """The first successful response, replayed for a repeated idempotency key."""
        with self._tx() as db:
            self._job(db, job_id)
            db.execute('UPDATE jobs SET response_json=? WHERE id=? AND response_json IS NULL',
                       (json.dumps(response, sort_keys=True, default=str), job_id))

    def response(self, job_id: str) -> Optional[dict]:
        with self._connect() as db:
            row = self._job(db, job_id)
            return json.loads(row['response_json']) if row['response_json'] else None

    def artifacts(self, job_id: str) -> list[dict]:
        with self._connect() as db:
            self._job(db, job_id)
            return [dict(row) for row in db.execute('SELECT * FROM artifacts WHERE job_id=? ORDER BY role', (job_id,))]

    def active(self, kind: Optional[str] = None) -> list[dict]:
        with self._connect() as db:
            marks = ','.join('?' * len(ACTIVE))
            query = f'SELECT * FROM jobs WHERE state IN ({marks})' + (' AND kind=?' if kind else '') + ' ORDER BY created_ns'
            return [dict(row) for row in db.execute(query, (*sorted(ACTIVE), *([kind] if kind else [])))]

    def ended(self, kind: str, project_key: str, states: Iterable[str]) -> list[dict]:
        """Ended jobs of one project namespace, newest first, for readback once no in-memory record is left."""
        wanted = sorted(set(states) & TERMINAL)
        if not wanted:
            return []
        with self._connect() as db:
            marks = ','.join('?' * len(wanted))
            query = f'SELECT * FROM jobs WHERE kind=? AND project_key=? AND state IN ({marks}) ORDER BY updated_ns DESC'
            return [dict(row) for row in db.execute(query, (kind, project_key, *wanted))]

    # --- Scheduler support (S1-03): queue, claim under a fence, attempt leases, quotas -----------------------------

    def enqueue(self, job_id: str, expected_revision: int, priority: int = 0, resources: Optional[dict] = None,
                budget: Optional[dict] = None) -> JobRef:
        now = time.time_ns()
        with self._tx() as db:
            row = self._job(db, job_id)
            if row['revision'] != expected_revision:
                raise StaleRevision(f'{job_id} is at revision {row["revision"]}, not {expected_revision}')
            state = next_state(row['state'], 'queue')
            revision = row['revision'] + 1
            db.execute('UPDATE jobs SET state=?, revision=?, updated_ns=?, priority=?, resources_json=?, budget_json=?,'
                       ' queued_ns=?, wait_reason=NULL WHERE id=?',
                       (state, revision, now, int(priority), json.dumps(resources) if resources else None,
                        json.dumps(budget) if budget else None, now, job_id))
            self._event(db, job_id, revision, 'queue', row['state'], state,
                        {'priority': int(priority), 'resources': resources, 'budget': budget}, now)
            return JobRef(job_id, revision, state)

    def set_budget(self, job_id: str, budget: dict) -> None:
        with self._tx() as db:
            self._job(db, job_id)
            db.execute('UPDATE jobs SET budget_json=? WHERE id=?', (json.dumps(budget), job_id))

    def set_quota(self, project_key: str, max_running: int) -> None:
        with self._tx() as db:
            db.execute('INSERT OR REPLACE INTO quotas VALUES(?, ?)', (project_key, int(max_running)))

    def quota(self, project_key: str) -> Optional[int]:
        with self._connect() as db:
            row = db.execute('SELECT max_running FROM quotas WHERE project_key=?', (project_key,)).fetchone()
            return row[0] if row else None

    def queued(self, project_key: Optional[str] = None) -> list[dict]:
        with self._connect() as db:
            query = "SELECT * FROM jobs WHERE state='queued'" + (' AND project_key=?' if project_key else '') + ' ORDER BY queued_ns, id'
            return [dict(row) for row in db.execute(query, (project_key,) if project_key else ())]

    def holding_counts(self) -> tuple[dict, dict]:
        """Jobs holding an attempt, by project namespace and by actor (fairness and quota input)."""
        marks = ','.join('?' * len(HOLDING))
        with self._connect() as db:
            by_project = dict(db.execute(f'SELECT project_key, COUNT(*) FROM jobs WHERE state IN ({marks}) GROUP BY project_key', HOLDING).fetchall())
            by_actor = dict(db.execute(f'SELECT actor_id, COUNT(*) FROM jobs WHERE state IN ({marks}) GROUP BY actor_id', HOLDING).fetchall())
        return by_project, by_actor

    def set_wait_reasons(self, reasons: dict) -> None:
        if not reasons:
            return
        with self._tx() as db:
            db.executemany("UPDATE jobs SET wait_reason=? WHERE id=? AND state='queued'",
                           [(reason, job_id) for job_id, reason in reasons.items()])

    def claim(self, job_id: str, worker_id: str, lease_seconds: float) -> Attempt:
        """Claim a queued job: quota re-checked, attempt and fence recorded, queued -> running, in one transaction."""
        now = time.time_ns()
        with self._tx() as db:
            row = self._job(db, job_id)
            if row['state'] != 'queued':
                raise IllegalTransition(f'{job_id} is {row["state"]}, not queued')
            limit = db.execute('SELECT max_running FROM quotas WHERE project_key=?', (row['project_key'],)).fetchone()
            if limit is not None:
                marks = ','.join('?' * len(HOLDING))
                held = db.execute(f'SELECT COUNT(*) FROM jobs WHERE project_key=? AND state IN ({marks})',
                                  (row['project_key'], *HOLDING)).fetchone()[0]
                if held >= limit[0]:
                    raise QuotaExceeded(f'{row["project_key"]} already runs {held} of {limit[0]} allowed jobs')
            number, token = db.execute('SELECT COALESCE(MAX(number), 0) + 1, COALESCE(MAX(fencing_token), 0) + 1'
                                       ' FROM attempts WHERE job_id=?', (job_id,)).fetchone()
            expires = now + int(lease_seconds * 1e9)
            db.execute('INSERT INTO attempts(job_id, number, fencing_token, executor, owner_boot_id, owner_pid, started_ns,'
                       ' lease_expires_ns, worker_id) VALUES(?, ?, ?, ?, NULL, NULL, ?, ?, ?)',
                       (job_id, number, token, 'scheduler', now, expires, worker_id))
            state = next_state(row['state'], 'claim')
            db.execute('UPDATE jobs SET state=?, revision=?, updated_ns=?, wait_reason=NULL WHERE id=?',
                       (state, row['revision'] + 2, now, job_id))
            self._event(db, job_id, row['revision'] + 1, 'attempt', row['state'], row['state'],
                        {'number': number, 'executor': 'scheduler', 'worker_id': worker_id}, now)
            self._event(db, job_id, row['revision'] + 2, 'claim', row['state'], state, {'attempt': number, 'worker_id': worker_id}, now)
            return Attempt(number, token)

    def heartbeat(self, job_id: str, fencing_token: int, lease_seconds: float) -> int:
        """Extend the attempt lease; only the newest attempt of a job still holding it may."""
        now = time.time_ns()
        with self._tx() as db:
            row = self._job(db, job_id)
            latest = db.execute('SELECT MAX(fencing_token) FROM attempts WHERE job_id=?', (job_id,)).fetchone()[0]
            if fencing_token != latest or row['state'] not in HOLDING:
                raise StaleFencingToken(f'Attempt token {fencing_token} no longer holds {job_id}')
            expires = now + int(lease_seconds * 1e9)
            db.execute('UPDATE attempts SET lease_expires_ns=? WHERE job_id=? AND fencing_token=? AND ended_ns IS NULL',
                       (expires, job_id, fencing_token))
            return expires

    def reattach(self, job_id: str, worker_id: Optional[str] = None, lease_seconds: Optional[float] = None) -> int:
        """A restarted owner takes over the open attempt under a new fence; the previous owner can no longer report."""
        now = time.time_ns()
        with self._tx() as db:
            row = self._job(db, job_id)
            if row['state'] not in HOLDING:
                raise IllegalTransition(f'{job_id} is {row["state"]}; nothing to reattach')
            attempt = db.execute('SELECT number, fencing_token FROM attempts WHERE job_id=? ORDER BY fencing_token DESC LIMIT 1',
                                 (job_id,)).fetchone()
            if attempt is None:
                raise IllegalTransition(f'{job_id} has no attempt to reattach')
            token = db.execute('SELECT MAX(fencing_token) + 1 FROM attempts WHERE job_id=?', (job_id,)).fetchone()[0]
            expires = now + int(lease_seconds * 1e9) if lease_seconds else None
            db.execute('UPDATE attempts SET fencing_token=?, worker_id=COALESCE(?, worker_id),'
                       ' lease_expires_ns=COALESCE(?, lease_expires_ns) WHERE job_id=? AND number=?',
                       (token, worker_id, expires, job_id, attempt['number']))
            state = next_state(row['state'], 'reattach') if row['state'] in ('detached', 'disconnected') else row['state']
            revision = row['revision'] + 1
            db.execute('UPDATE jobs SET state=?, revision=?, updated_ns=? WHERE id=?', (state, revision, now, job_id))
            self._event(db, job_id, revision, 'reattach', row['state'], state,
                        {'attempt': attempt['number'], 'previous_token': attempt['fencing_token'], 'fencing_token': token}, now)
            return token

    def expired_attempts(self, now_ns: Optional[int] = None) -> list[dict]:
        """Newest open attempts whose lease ran out while their job still holds it."""
        marks = ','.join('?' * len(HOLDING))
        with self._connect() as db:
            rows = db.execute(
                f'SELECT a.job_id, a.number, a.fencing_token, a.worker_id, a.lease_expires_ns, a.started_ns, j.state,'
                f' j.budget_json, j.resources_json FROM attempts a JOIN jobs j ON j.id=a.job_id'
                f' WHERE a.ended_ns IS NULL AND a.lease_expires_ns IS NOT NULL AND a.lease_expires_ns<? AND j.state IN ({marks})'
                f' AND a.fencing_token=(SELECT MAX(fencing_token) FROM attempts WHERE job_id=a.job_id)',
                (now_ns or time.time_ns(), *HOLDING))
            return [dict(row) for row in rows]

    def open_attempts(self) -> list[dict]:
        marks = ','.join('?' * len(HOLDING))
        with self._connect() as db:
            rows = db.execute(f'SELECT a.job_id, a.fencing_token, a.started_ns, j.budget_json FROM attempts a JOIN jobs j'
                              f' ON j.id=a.job_id WHERE a.ended_ns IS NULL AND j.state IN ({marks})', HOLDING)
            return [dict(row) for row in rows]

    def recover_unlaunched(self, reason: str) -> list[JobRef]:
        """Jobs accepted but never launched become interrupted after a restart; they are never relaunched."""
        recovered = []
        for row in self.active():
            if self.attempts(row['id']):
                continue
            try:
                recovered.append(self.transition(row['id'], row['revision'], 'interrupt', {'reason': reason}))
            except (StaleRevision, IllegalTransition):
                continue
        return recovered

    def migrate_legacy(self, roots: Iterable[Path | str], receipt_path: Path | str) -> dict:
        """Copy legacy job journals into the ledger without deleting or rewriting them; repeat runs are no-ops."""
        migrated, skipped, sources = [], [], []
        for root in roots:
            root = Path(root)
            if not root.is_dir():
                continue
            for path in sorted(root.glob('*.json')):
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                sources.append({'path': str(path), 'sha256': digest})
                outcome, job_id = self._migrate_one(path, raw, digest)
                (migrated if outcome == 'migrated' else skipped).append(job_id if outcome == 'migrated'
                                                                         else {'path': str(path), 'reason': outcome})
        receipt = {'schema': 'modu-vision.job-ledger-migration/v1', 'ledger': str(self.path),
                   'migrated': migrated, 'skipped': skipped, 'sources': sources,
                   'written_at': datetime.now(timezone.utc).isoformat()}
        target = Path(receipt_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
        temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding='utf-8')
        os.replace(temporary, target)
        return receipt

    def _migrate_one(self, path: Path, raw: bytes, digest: str) -> tuple[str, Optional[str]]:
        now = time.time_ns()
        with self._tx() as db:
            if db.execute('SELECT 1 FROM migrations WHERE source_path=? AND sha256=?', (str(path), digest)).fetchone():
                return 'already migrated', None
            try:
                data = json.loads(raw)
                job_id = str(data['job_id'])
            except (ValueError, KeyError, TypeError):
                db.execute('INSERT OR REPLACE INTO migrations VALUES(?, ?, NULL, ?, ?)', (str(path), digest, 'unreadable', now))
                return 'unreadable', None
            if db.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone():
                db.execute('INSERT OR REPLACE INTO migrations VALUES(?, ?, ?, ?, ?)', (str(path), digest, job_id, 'exists', now))
                return 'job already in ledger', None
            status = data.get('status') or data.get('state')
            try:
                state = from_legacy(status) if status else 'interrupted'
            except ValueError:
                state = 'interrupted'
            if state in ACTIVE:
                # A live or unknown worker is not imported as ended; a later run migrates it once it has ended.
                return 'job still active; migrate after it ends', None
            output_dir = data.get('output_dir')
            spec = {'legacy_source': str(path), 'legacy_sha256': digest}
            db.execute('INSERT INTO jobs(id, workspace_id, project_key, project_id, actor_id, mode, kind, idempotency_key,'
                       ' spec_sha256, spec_json, state, revision, parent_id, output_dir, response_json, source, created_ns,'
                       ' updated_ns) VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 1, NULL, ?, NULL, ?, ?, ?)',
                       (job_id, 'legacy', f'legacy:{Path(output_dir).parent}' if output_dir else 'legacy:unknown', 'legacy',
                        'legacy', 'local', data.get('kind') or 'training', spec_digest(spec), json.dumps(spec, sort_keys=True),
                        state, output_dir, 'legacy_migration', now, now))
            self._event(db, job_id, 1, 'migrate', None, state, {'source': str(path), 'legacy_status': status}, now)
            db.execute('INSERT OR REPLACE INTO migrations VALUES(?, ?, ?, ?, ?)', (str(path), digest, job_id, 'migrated', now))
            return 'migrated', job_id


def legacy_roots() -> list[Path]:
    """Job journal folders written before the ledger: the canonical folder's copies and both legacy home folders."""
    home = Path.home()
    candidates = [user_data_root() / 'local_jobs', user_data_root() / 'remote_jobs',
                  home / '.modu_vision' / 'local_jobs', home / '.modu_vision' / 'remote_jobs',
                  home / '.modu-vision' / 'local_jobs', home / '.modu-vision' / 'remote_jobs']
    unique = []
    for path in candidates:
        if path.resolve() not in {item.resolve() for item in unique}:
            unique.append(path)
    return unique


def main(argv: Optional[list[str]] = None) -> int:
    """Explicit, non-deleting migration of legacy job journals into the ledger."""
    import argparse
    parser = argparse.ArgumentParser(prog='python -m backend.engine.job_store')
    commands = parser.add_subparsers(dest='command', required=True)
    migrate = commands.add_parser('migrate-legacy', help='copy legacy job journals into the ledger (originals are kept)')
    migrate.add_argument('--receipt', type=Path, required=True, help='where to write the migration receipt')
    migrate.add_argument('--root', type=Path, action='append', help='journal folder to read (default: all known roots)')
    args = parser.parse_args(argv)
    receipt = ledger().migrate_legacy(args.root or legacy_roots(), args.receipt)
    print(json.dumps({'migrated': len(receipt['migrated']), 'skipped': len(receipt['skipped']), 'receipt': str(args.receipt)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
