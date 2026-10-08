"""Runtime deployments become active only after an exact service acknowledgment."""
from __future__ import annotations
from contextlib import closing
import json
import sqlite3
from backend.engine.sqlite_wal import use_wal  # a concurrent WAL switch is retried, not failed
import time
import uuid
from pathlib import Path
from backend.engine.runtime_process_control import runtime_state_lock


class DeploymentLedger:
    def __init__(self, directory, *, project_dir=None):
        self.directory = Path(directory); self.directory.mkdir(parents=True,exist_ok=True)
        from backend.engine.artifact_retention import retention_project_root
        self.project_dir=Path(project_dir) if project_dir is not None else retention_project_root(self.directory)
        self.pin_owner='derived:active-release:'+self.directory.resolve().relative_to(self.project_dir.resolve()).as_posix()
        self.path = self.directory/'runtime_deployments.sqlite3'
        if self.path.is_symlink(): raise ValueError('Deployment database cannot be linked')
        with closing(self.connect()) as conn, conn:
            conn.executescript('CREATE TABLE IF NOT EXISTS deployments(deployment_id TEXT PRIMARY KEY,release TEXT,ack TEXT,reviewer TEXT,restored_from TEXT,created_at REAL);CREATE TABLE IF NOT EXISTS active(id INTEGER PRIMARY KEY CHECK(id=1),deployment_id TEXT);CREATE TABLE IF NOT EXISTS update_operations(operation_id TEXT PRIMARY KEY,release TEXT,previous TEXT,status TEXT,ack TEXT,error TEXT,reviewer TEXT,created_at REAL,updated_at REAL);')
    def connect(self):
        conn=sqlite3.connect(self.path,timeout=30)
        try:
            conn.row_factory=sqlite3.Row
            use_wal(conn, 30); conn.execute('PRAGMA synchronous=FULL'); return conn
        except BaseException as error:
            try:
                conn.close()
            except BaseException as close_error:
                try: error.add_note('Deployment connection close also failed: '+type(close_error).__name__)
                except BaseException: pass
            raise
    def history(self):
        with closing(self.connect()) as conn, conn: rows=conn.execute('SELECT * FROM deployments ORDER BY created_at DESC').fetchall()
        return [{**dict(row),'release':json.loads(row['release']),'ack':json.loads(row['ack'])} for row in rows]
    def active(self):
        with closing(self.connect()) as conn, conn: row=conn.execute('SELECT deployment_id FROM active WHERE id=1').fetchone()
        return next((item for item in self.history() if row and item['deployment_id']==row[0]),None)
    @staticmethod
    def _operation(row):
        if row is None: return None
        value=dict(row)
        for key in ('release','previous','ack'):
            value[key]=json.loads(value[key]) if value[key] else None
        return value
    def diagnostics(self):
        """Read-only receipts; a pending operation never counts as active acceptance."""
        with closing(self.connect()) as conn, conn:
            last=conn.execute('SELECT * FROM update_operations ORDER BY created_at DESC LIMIT 1').fetchone()
            pending=conn.execute("SELECT * FROM update_operations WHERE status IN ('applying','rolling_back','needs_review') ORDER BY created_at DESC LIMIT 1").fetchone()
        return {'schema_version':1,'active':self.active(),'pending':self._operation(pending),'last_operation':self._operation(last)}
    def _record(self, identifier, status, *, ack=None, error=None):
        with closing(self.connect()) as conn, conn:
            conn.execute('UPDATE update_operations SET status=?,ack=?,error=?,updated_at=? WHERE operation_id=?',
                         (status,json.dumps(ack) if ack else None,error,time.time(),identifier))
    @staticmethod
    def _validate_ack(release, ack):
        if not isinstance(ack,dict) or ack.get('status')!='ready':
            raise ValueError('Runtime acknowledgment is not ready')
        if any(key in release and ack.get(key)!=release[key] for key in ('manifest_sha256','device')):
            raise ValueError('Runtime acknowledgment identity mismatch')
    def recover(self, apply_runtime):
        """Restore only the last committed release after an interrupted switch."""
        from backend.engine.artifact_retention import ArtifactRetention,retention_project_root
        retention=ArtifactRetention(self.project_dir)
        with runtime_state_lock(self.directory),retention.lock():
            pending=self.diagnostics()['pending']
            if pending is None: return None
            identifier=pending['operation_id'];previous=pending['previous']
            if previous is None:
                self._record(identifier,'interrupted_without_previous',error='No previously accepted release exists; stop the unaccepted runtime and apply explicitly')
                return self.diagnostics()['last_operation']
            current=self.active()
            if not current or current['deployment_id']!=previous['deployment_id']:
                self._record(identifier,'needs_review',error='Active deployment changed during interrupted update recovery')
                raise ValueError('Active deployment differs from the recovery journal')
            self._record(identifier,'rolling_back')
            try:
                ack=apply_runtime(previous['release']);self._validate_ack(previous['release'],ack)
            except Exception as exc:
                self._record(identifier,'needs_review',error=type(exc).__name__)
                raise ValueError('Runtime rollback acknowledgment failed; runtime requires review') from exc
            self._record(identifier,'rolled_back',ack=ack)
            retention.unpin('deployment:'+identifier)
            retention.unpin('derived:pending-release:'+identifier)
            return self.diagnostics()['last_operation']
    def apply(self, release, apply_runtime, *, reviewer, restored_from=None):
        if not reviewer.strip(): raise ValueError('A reviewer is required')
        from backend.engine.artifact_retention import ArtifactRetention,retention_project_root
        retention=ArtifactRetention(self.project_dir)
        with runtime_state_lock(self.directory),retention.lock():
            self.recover(apply_runtime)
            previous=self.active()
            identifier=uuid.uuid4().hex;now=time.time()
            paths=retention.release_paths(release)+retention.release_paths((previous or {}).get('release'))
            if paths:retention.pin('deployment:'+identifier,paths,reason='pending_release')
            # Commit recovery intent BEFORE the external runtime mutates. SQLite
            # transaction rollback cannot undo a service process after power loss.
            with closing(self.connect()) as conn, conn:
                conn.execute('INSERT INTO update_operations VALUES(?,?,?,?,?,?,?,?,?)',
                             (identifier,json.dumps(release),json.dumps(previous) if previous else None,'applying',None,None,reviewer,now,now))
            try:
                ack=apply_runtime(release);self._validate_ack(release,ack)
                # Pointer, history and completion receipt commit together.
                with closing(self.connect()) as conn, conn:
                    conn.execute('BEGIN IMMEDIATE')
                    conn.execute('INSERT INTO deployments VALUES(?,?,?,?,?,?)',(identifier,json.dumps(release),json.dumps(ack),reviewer,restored_from,time.time()))
                    conn.execute('INSERT INTO active VALUES(1,?) ON CONFLICT(id) DO UPDATE SET deployment_id=excluded.deployment_id',(identifier,))
                    conn.execute("UPDATE update_operations SET status='committed',ack=?,updated_at=? WHERE operation_id=?",(json.dumps(ack),time.time(),identifier))
                paths=retention.release_paths(release)
                if paths:retention.pin(self.pin_owner,paths,reason='active_release',replace=True)
                retention.unpin('deployment:'+identifier)
                retention.unpin('derived:pending-release:'+identifier)
            except Exception as exc:
                if previous:
                    self.recover(apply_runtime)
                else:self._record(identifier,'rejected',error=type(exc).__name__)
                raise
        return next(row for row in self.history() if row['deployment_id']==identifier)
    def rollback(self, deployment_id, apply_runtime, *, reviewer):
        target=next((row for row in self.history() if row['deployment_id']==deployment_id),None)
        if target is None: raise KeyError(deployment_id)
        return self.apply(target['release'],apply_runtime,reviewer=reviewer,restored_from=deployment_id)
