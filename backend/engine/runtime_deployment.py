"""Runtime deployments become active only after an exact service acknowledgment."""
from __future__ import annotations
import json
import sqlite3
import time
import uuid
from pathlib import Path


class DeploymentLedger:
    def __init__(self, directory):
        self.directory = Path(directory); self.directory.mkdir(parents=True,exist_ok=True)
        self.path = self.directory/'runtime_deployments.sqlite3'
        with self.connect() as conn:
            conn.executescript('CREATE TABLE IF NOT EXISTS deployments(deployment_id TEXT PRIMARY KEY,release TEXT,ack TEXT,reviewer TEXT,restored_from TEXT,created_at REAL);CREATE TABLE IF NOT EXISTS active(id INTEGER PRIMARY KEY CHECK(id=1),deployment_id TEXT);')
    def connect(self):
        conn=sqlite3.connect(self.path,timeout=30); conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL'); return conn
    def history(self):
        with self.connect() as conn: rows=conn.execute('SELECT * FROM deployments ORDER BY created_at DESC').fetchall()
        return [{**dict(row),'release':json.loads(row['release']),'ack':json.loads(row['ack'])} for row in rows]
    def active(self):
        with self.connect() as conn: row=conn.execute('SELECT deployment_id FROM active WHERE id=1').fetchone()
        return next((item for item in self.history() if row and item['deployment_id']==row[0]),None)
    def apply(self, release, apply_runtime, *, reviewer, restored_from=None):
        if not reviewer.strip(): raise ValueError('A reviewer is required')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            previous=self.active()
            try:
                ack=apply_runtime(release)
                for key in ('manifest_sha256','device'):
                    if key in release and ack.get(key)!=release[key]: raise ValueError('Runtime acknowledgment identity mismatch')
                if ack.get('status')!='ready': raise ValueError('Runtime acknowledgment is not ready')
                identifier=uuid.uuid4().hex
                conn.execute('INSERT INTO deployments VALUES(?,?,?,?,?,?)',(identifier,json.dumps(release),json.dumps(ack),reviewer,restored_from,time.time()))
                conn.execute('INSERT INTO active VALUES(1,?) ON CONFLICT(id) DO UPDATE SET deployment_id=excluded.deployment_id',(identifier,))
            except Exception:
                if previous:
                    # Restore actual runtime as well as preserving the database pointer.
                    recovery=apply_runtime(previous['release'])
                    if (recovery.get('status')!='ready' or any(
                        key in previous['release'] and recovery.get(key)!=previous['release'][key]
                        for key in ('manifest_sha256','device'))):
                        raise ValueError('Runtime rollback acknowledgment failed; runtime requires review')
                raise
        return next(row for row in self.history() if row['deployment_id']==identifier)
    def rollback(self, deployment_id, apply_runtime, *, reviewer):
        target=next((row for row in self.history() if row['deployment_id']==deployment_id),None)
        if target is None: raise KeyError(deployment_id)
        return self.apply(target['release'],apply_runtime,reviewer=reviewer,restored_from=deployment_id)
