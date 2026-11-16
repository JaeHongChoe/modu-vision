"""Child-side durable reservation precedes actual upstream queue publication.

A reserved/last nonterminal record after restart is intentionally ambiguous. It
is returned for inspection and is NEVER republished automatically.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid


class CorrelationStore:
    def __init__(self, path):
        self.path = Path(path)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS submissions (id TEXT PRIMARY KEY, digest TEXT NOT NULL, data TEXT NOT NULL)')

    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA synchronous=FULL')
        return db

    def reserve(self, operation_id, spec):
        digest = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT digest,data FROM submissions WHERE id=?', (operation_id,)).fetchone()
            if old:
                if old[0] != digest: raise ValueError('Operation ID has a different training specification')
                return {**json.loads(old[1]), 'is_new': False}
            record = {'job_id': str(uuid.uuid4()), 'operation_id': operation_id, 'status': 'submission_unconfirmed', 'progress': 0, 'worker_exited': False}
            db.execute('INSERT INTO submissions VALUES(?,?,?)', (operation_id, digest, json.dumps(record)))
            return {**record, 'is_new': True}

    def get(self, operation_id):
        with self._db() as db:
            row = db.execute('SELECT data FROM submissions WHERE id=?', (operation_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, operation_id, value):
        with self._db() as db:
            old = self.get(operation_id)
            record = {**old, **value, 'operation_id': operation_id}
            record.pop('is_new', None)
            db.execute('UPDATE submissions SET data=? WHERE id=?', (json.dumps(record), operation_id))
        return record

    def all(self):
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM submissions')]
