"""Durable project, immutable transfer and operation/job bindings."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

_KEY = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')


def opaque_key(value: str) -> str:
    if not isinstance(value, str) or not _KEY.fullmatch(value) or value in {'.', '..'}:
        raise ValueError('Invalid opaque platform identity')
    return value


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class PlatformStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'bindings.sqlite3'
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS bindings (project_key TEXT NOT NULL, kind TEXT NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(project_key,kind));
                CREATE TABLE IF NOT EXISTS operations (project_key TEXT NOT NULL, operation_id TEXT NOT NULL,
                    actor TEXT NOT NULL, kind TEXT NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(project_key,operation_id));
                CREATE TABLE IF NOT EXISTS jobs (project_key TEXT NOT NULL, id TEXT NOT NULL,
                    actor TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(project_key,id));
            ''')

    @contextmanager
    def _db(self):
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute('PRAGMA synchronous=FULL')
            yield db

    def _get(self, key, kind):
        with self._db() as db:
            row = db.execute('SELECT data FROM bindings WHERE project_key=? AND kind=?', (opaque_key(key), kind)).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, key, kind, value):
        with self._db() as db:
            db.execute('INSERT INTO bindings VALUES(?,?,?) ON CONFLICT(project_key,kind) DO UPDATE SET data=excluded.data',
                       (opaque_key(key), kind, canonical(value)))
        return value

    def get_project(self, key): return self._get(key, 'project')
    def save_project(self, key, values): return self._save(key, 'project', values)
    def get_dataset(self, key): return self._get(key, 'dataset')
    def save_dataset(self, key, receipt): return self._save(key, 'dataset', receipt)

    def reserve_operation(self, key, actor, operation_id, kind, spec):
        key, actor, operation_id = opaque_key(key), opaque_key(actor), opaque_key(operation_id)
        digest = hashlib.sha256(canonical({'kind': kind, 'spec': spec}).encode()).hexdigest()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT actor,digest,data FROM operations WHERE project_key=? AND operation_id=?', (key, operation_id)).fetchone()
            if row:
                if row[0] != actor: raise ValueError('Operation belongs to another actor')
                if row[1] != digest: raise ValueError('Operation ID was used for a different request')
                return {**json.loads(row[2]), 'is_new': False}
            value = {'operation_id': operation_id, 'kind': kind, 'status': 'reserved', 'created_at': time.time(), 'spec_sha256': digest}
            db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?)', (key, operation_id, actor, kind, digest, canonical(value)))
            return {**value, 'is_new': True}

    def complete_operation(self, key, actor, operation_id, result, *, status='completed'):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT actor,data FROM operations WHERE project_key=? AND operation_id=?', (opaque_key(key), opaque_key(operation_id))).fetchone()
            if not row or row[0] != actor: raise ValueError('Operation actor does not match reservation')
            value = {**json.loads(row[1]), 'status': status, 'result': result}
            db.execute('UPDATE operations SET data=? WHERE project_key=? AND operation_id=?', (canonical(value), key, operation_id))
            return value

    def save_job(self, key, actor, record):
        identifier = opaque_key(record['id'])
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT data FROM jobs WHERE project_key=? AND id=?', (opaque_key(key), identifier)).fetchone()
            if previous:
                old = json.loads(previous[0])
                if old.get('worker_exited') and old.get('status') in {'done','failed','cancelled','interrupted','rejected'}:
                    record = {**record, **old}
                elif old.get('worker_exited'):
                    record = {**record, 'worker_exited': True}
                if old.get('cancel_requested'): record = {**record, 'cancel_requested': True}
            db.execute('INSERT INTO jobs VALUES(?,?,?,?) ON CONFLICT(project_key,id) DO UPDATE SET data=excluded.data',
                       (opaque_key(key), identifier, opaque_key(actor), canonical(record)))
        return record

    def list_jobs(self, key):
        with self._db() as db:
            rows = db.execute('SELECT data FROM jobs WHERE project_key=? ORDER BY rowid DESC', (opaque_key(key),)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_job(self, key, id):
        with self._db() as db:
            row = db.execute('SELECT data FROM jobs WHERE project_key=? AND id=?', (opaque_key(key), opaque_key(id))).fetchone()
        return json.loads(row[0]) if row else None

    def activity_jobs(self):
        """Internal evidence, including terminal artifacts whose worker is alive."""
        with self._db() as db:
            rows = db.execute('SELECT project_key,actor,data FROM jobs ORDER BY rowid').fetchall()
        result = []
        for key, actor, encoded in rows:
            job = json.loads(encoded)
            if job.get('worker_exited') is not True:
                result.append({'project_key': key, 'actor': actor, 'job': job})
        return result
