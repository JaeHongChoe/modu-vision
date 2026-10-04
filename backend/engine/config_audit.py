"""Inspection-rule change audit (E04): who changed which rule, from which revision to which, why, and what the runtime
was running at the time.

One SQLite file per project (``configuration_changes.sqlite3``) holds an append-only list: database triggers refuse any
UPDATE or DELETE, and every row carries the hash of the previous row, so a row edited or removed outside the app breaks
the chain that ``verify`` checks. The caller writes a row inside the same lock and rollback as the change it records:
a change that fails, or loses a concurrent save, leaves no row.

The actor comes from the authenticated session (a team account), or is this computer in personal mode; nothing a
client sends can name another actor.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Literal, Optional

from backend.engine.sqlite_wal import use_wal

Action = Literal['save', 'activate']
SCHEMA = (
    'CREATE TABLE IF NOT EXISTS configuration_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT, change_id TEXT NOT NULL UNIQUE,'
    ' time_ns INTEGER NOT NULL, actor TEXT NOT NULL, subject TEXT NOT NULL, action TEXT NOT NULL, parent_revision TEXT,'
    ' next_revision TEXT NOT NULL, semantic_delta TEXT NOT NULL, layout_only INTEGER NOT NULL, reason TEXT,'
    ' observed_runtime_release TEXT, previous_hash TEXT NOT NULL, row_hash TEXT NOT NULL)',
    "CREATE TRIGGER IF NOT EXISTS configuration_changes_no_update BEFORE UPDATE ON configuration_changes"
    " BEGIN SELECT RAISE(ABORT, 'configuration changes are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS configuration_changes_no_delete BEFORE DELETE ON configuration_changes"
    " BEGIN SELECT RAISE(ABORT, 'configuration changes are append-only'); END",
)
_GENESIS = '0' * 64
_FIELDS = ('change_id', 'time_ns', 'actor', 'subject', 'action', 'parent_revision', 'next_revision', 'semantic_delta',
           'layout_only', 'reason', 'observed_runtime_release')


def actor_from_request(request: Any) -> dict:
    """The authenticated actor of a request: its team account, or this computer in personal mode."""
    account = getattr(getattr(request, 'state', None), 'account_user', None) if request is not None else None
    if account:
        return {'kind': 'account', 'id': str(account.get('id')), 'name': str(account.get('username') or account.get('id'))}
    return {'kind': 'local', 'id': 'this-computer', 'name': 'this computer'}


def _row_hash(previous: str, values: dict) -> str:
    canonical = json.dumps({key: values[key] for key in _FIELDS}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256((previous + canonical).encode('utf-8')).hexdigest()


class ConfigAuditStore:
    def __init__(self, project_dir: Path | str, timeout: float = 10.0):
        self.path = Path(project_dir) / 'configuration_changes.sqlite3'
        self.timeout = timeout

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        use_wal(db, self.timeout)
        for statement in SCHEMA:
            db.execute(statement)
        return db

    def append(self, *, actor: dict, subject: dict, action: Action, parent_revision: Optional[str], next_revision: str,
               semantic_delta: dict, reason: Optional[str], observed_runtime_release: Optional[dict] = None) -> dict:
        if action not in ('save', 'activate'):
            raise ValueError('action must be save or activate')
        if reason is not None and (not isinstance(reason, str) or len(reason) > 2000):
            raise ValueError('a change reason is text of at most 2000 characters')
        values = {'change_id': uuid.uuid4().hex, 'time_ns': time.time_ns(), 'actor': json.dumps(actor, sort_keys=True),
                  'subject': json.dumps(subject, sort_keys=True, ensure_ascii=False), 'action': action,
                  'parent_revision': parent_revision, 'next_revision': next_revision,
                  'semantic_delta': json.dumps(semantic_delta, sort_keys=True, ensure_ascii=False),
                  'layout_only': int(bool(semantic_delta.get('layout_only'))), 'reason': (reason or '').strip() or None,
                  'observed_runtime_release': json.dumps(observed_runtime_release, sort_keys=True) if observed_runtime_release else None}
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                last = db.execute('SELECT row_hash FROM configuration_changes ORDER BY seq DESC LIMIT 1').fetchone()
                previous = last[0] if last else _GENESIS
                row_hash = _row_hash(previous, values)
                db.execute(f"INSERT INTO configuration_changes({', '.join(_FIELDS)}, previous_hash, row_hash) VALUES({', '.join('?' * (len(_FIELDS) + 2))})",
                           [values[key] for key in _FIELDS] + [previous, row_hash])
                db.execute('COMMIT')
            except BaseException:
                try:
                    db.execute('ROLLBACK')
                except sqlite3.Error:
                    pass
                raise
        return self._public({**values, 'previous_hash': previous, 'row_hash': row_hash})

    @staticmethod
    def _public(row: dict) -> dict:
        return {'change_id': row['change_id'], 'time_ns': row['time_ns'], 'actor': json.loads(row['actor']),
                'subject': json.loads(row['subject']), 'action': row['action'], 'parent_revision': row['parent_revision'],
                'next_revision': row['next_revision'], 'semantic_delta': json.loads(row['semantic_delta']),
                'layout_only': bool(row['layout_only']), 'reason': row['reason'],
                'observed_runtime_release': json.loads(row['observed_runtime_release']) if row['observed_runtime_release'] else None,
                'row_hash': row['row_hash']}

    def list(self, limit: int = 100) -> list[dict]:
        if not self.path.is_file():
            return []
        with closing(self._connect()) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT * FROM configuration_changes ORDER BY seq DESC LIMIT ?', (int(limit),)).fetchall()
        return [self._public(dict(row)) for row in rows]

    def verify(self) -> dict:
        """Whether every row still carries the hash of its content and of the row before it."""
        if not self.path.is_file():
            return {'intact': True, 'rows': 0}
        with closing(self._connect()) as db:
            db.row_factory = sqlite3.Row
            previous = _GENESIS
            count = 0
            for row in db.execute('SELECT * FROM configuration_changes ORDER BY seq'):
                values = dict(row)
                count += 1
                if values['previous_hash'] != previous or _row_hash(previous, values) != values['row_hash']:
                    return {'intact': False, 'rows': count, 'broken_at': values['change_id']}
                previous = values['row_hash']
        return {'intact': True, 'rows': count}


def observed_runtime_release(project_dir: Path | str) -> Optional[dict]:
    """The release the project's inspection runtime runs at this moment (a saved rule change does not reach it until a
    new release is applied and acknowledged)."""
    path = Path(project_dir) / 'runtime_service' / 'runtime_deployments.sqlite3'
    if not path.is_file():
        return None
    try:
        from backend.engine.runtime_deployment import DeploymentLedger
        active = DeploymentLedger(path.parent).active()
    except Exception:  # an unreadable ledger is recorded as such; the change itself still stands
        return {'unreadable': True}
    if not active:
        return None
    release = active.get('release') or {}
    return {'deployment_id': active.get('deployment_id'), 'manifest_sha256': release.get('manifest_sha256'),
            'acknowledged': bool(active.get('ack'))}


def runtime_release_status(project_dir: Path | str) -> dict:
    """What the project's inspection runtime runs now and what is being applied: an applying release is not shown as
    running until the runtime acknowledged it."""
    path = Path(project_dir) / 'runtime_service' / 'runtime_deployments.sqlite3'
    if not path.is_file():
        return {'active': None, 'pending': None}
    try:
        from backend.engine.runtime_deployment import DeploymentLedger
        diagnostics = DeploymentLedger(path.parent).diagnostics()
    except Exception:  # an unreadable ledger is shown as such, never as a release
        return {'active': None, 'pending': None, 'unreadable': True}
    active, pending = diagnostics.get('active'), diagnostics.get('pending')
    return {'active': {'deployment_id': active.get('deployment_id'), 'manifest_sha256': (active.get('release') or {}).get('manifest_sha256'),
                       'acknowledged': bool(active.get('ack'))} if active else None,
            'pending': {'operation_id': pending.get('operation_id'), 'status': pending.get('status'),
                        'manifest_sha256': (pending.get('release') or {}).get('manifest_sha256')} if pending else None}
