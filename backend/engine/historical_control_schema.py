"""Exact original predecessor DDL; no installed-directory or live authority claim.

These signatures were read from the recorded original source commits, never
learned from a user's database. Only a private staged copy is reconstructed.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile

KNOWN_PREDECESSORS = [{'scope': 'ledger',
  'source_commit': '86a6faf70c0b1fb74fb53ca5820f341a1334521d',
  'schema': [['index',
              'jobs_idempotency',
              'jobs',
              'CREATE UNIQUE INDEX jobs_idempotency\n'
              '    ON jobs(workspace_id, project_key, actor_id, kind, idempotency_key) WHERE '
              'idempotency_key IS NOT NULL'],
             ['table',
              'artifacts',
              'artifacts',
              'CREATE TABLE artifacts(\n'
              '    job_id TEXT NOT NULL REFERENCES jobs(id), role TEXT NOT NULL, artifact_id TEXT '
              'NOT NULL,\n'
              '    revision INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(job_id, role))'],
             ['table',
              'attempts',
              'attempts',
              'CREATE TABLE attempts(\n'
              '    job_id TEXT NOT NULL REFERENCES jobs(id), number INTEGER NOT NULL, '
              'fencing_token INTEGER NOT NULL,\n'
              '    executor TEXT NOT NULL, owner_boot_id TEXT, owner_pid INTEGER, started_ns '
              'INTEGER NOT NULL, ended_ns INTEGER, lease_expires_ns INTEGER, worker_id TEXT,\n'
              '    PRIMARY KEY(job_id, number))'],
             ['table',
              'cancel_intents',
              'cancel_intents',
              'CREATE TABLE cancel_intents(\n'
              '    job_id TEXT PRIMARY KEY REFERENCES jobs(id), actor_id TEXT NOT NULL, reason '
              'TEXT NOT NULL, requested_ns INTEGER NOT NULL)'],
             ['table',
              'events',
              'events',
              'CREATE TABLE events(\n'
              '    job_id TEXT NOT NULL REFERENCES jobs(id), seq INTEGER NOT NULL, revision '
              'INTEGER NOT NULL, event TEXT NOT NULL,\n'
              '    from_state TEXT, to_state TEXT NOT NULL, payload_json TEXT, at_ns INTEGER NOT '
              'NULL, PRIMARY KEY(job_id, seq))'],
             ['table',
              'jobs',
              'jobs',
              'CREATE TABLE jobs(\n'
              '    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, project_key TEXT NOT NULL, '
              'project_id TEXT NOT NULL,\n'
              '    actor_id TEXT NOT NULL, mode TEXT NOT NULL, kind TEXT NOT NULL, idempotency_key '
              'TEXT,\n'
              '    spec_sha256 TEXT NOT NULL, spec_json TEXT NOT NULL, state TEXT NOT NULL, '
              'revision INTEGER NOT NULL,\n'
              '    parent_id TEXT REFERENCES jobs(id), output_dir TEXT, response_json TEXT, source '
              'TEXT NOT NULL,\n'
              '    created_ns INTEGER NOT NULL, updated_ns INTEGER NOT NULL, registry_root TEXT, '
              'project_dir TEXT, priority INTEGER NOT NULL DEFAULT 0, resources_json TEXT, '
              'budget_json TEXT, wait_reason TEXT, queued_ns INTEGER)'],
             ['table',
              'migrations',
              'migrations',
              'CREATE TABLE migrations(\n'
              '    source_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, job_id TEXT, outcome TEXT '
              'NOT NULL, migrated_ns INTEGER NOT NULL)'],
             ['table',
              'quotas',
              'quotas',
              'CREATE TABLE quotas(\n'
              '    project_key TEXT PRIMARY KEY, max_running INTEGER NOT NULL)']]},
 {'scope': 'leases',
  'source_commit': '053999cec646de1281920c249c27c361b3aa5dc0',
  'schema': [['table',
              'devices',
              'devices',
              'CREATE TABLE devices(host TEXT NOT NULL,selector TEXT NOT NULL,uuid TEXT NOT '
              'NULL,parent_uuid TEXT,memory_mb INTEGER NOT NULL,PRIMARY KEY(host,selector))'],
             ['table',
              'leases',
              'leases',
              'CREATE TABLE leases(job_id TEXT PRIMARY KEY,host TEXT,selector TEXT,owner '
              'TEXT,expires REAL,remote INTEGER,uncertain INTEGER DEFAULT 0, memory_budget_mb '
              'INTEGER NOT NULL DEFAULT 0, allow_sharing INTEGER NOT NULL DEFAULT 0, task TEXT, '
              'project_id TEXT, account_id TEXT)']]}]


def predecessor(scope, schema):
    """Return only a statically recorded exact original predecessor identity."""
    for version in KNOWN_PREDECESSORS:
        if version['scope']==scope and version['schema']==schema:
            return version['source_commit']
    return None


def _rebuild(path, expected):
    # The caller holds installation-wide exclusive admission. This is a staged
    # copy, never an original control file or an active generation.
    fd,name=tempfile.mkstemp(prefix='.historical-control-',suffix='.sqlite3',dir=path.parent)
    os.fchmod(fd,0o600);os.close(fd);target=Path(name)
    try:
        with closing(sqlite3.connect(target,uri=True)) as db:
            db.execute('ATTACH DATABASE ? AS original',(path.resolve().as_uri()+'?mode=ro',))
            for kind,_,_,sql in sorted(expected,key=lambda r:r[0]!='table'):
                if kind not in {'table','index'}:raise ValueError('Current trusted schema contains unsupported objects')
                db.execute(sql)
            tables=[r[1] for r in expected if r[0]=='table']
            old_tables={r[0] for r in db.execute("SELECT name FROM original.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if not old_tables.issubset(tables):raise ValueError('Historical table inventory changed while staged')
            for table in sorted(old_tables):
                quoted='"'+table.replace('"','""')+'"'
                columns=[r[1] for r in db.execute('PRAGMA original.table_info('+quoted+')')]
                new_columns={r[1] for r in db.execute('PRAGMA main.table_info('+quoted+')')}
                if not set(columns).issubset(new_columns):raise ValueError('Historical conversion would discard a column')
                selected=','.join('"'+c.replace('"','""')+'"' for c in columns)
                db.execute('INSERT INTO main.'+quoted+' ('+selected+') SELECT '+selected+' FROM original.'+quoted)
                if (db.execute('SELECT COUNT(*) FROM original.'+quoted).fetchone()[0]
                        !=db.execute('SELECT COUNT(*) FROM main.'+quoted).fetchone()[0]):
                    raise ValueError('Historical row count changed during reconstruction')
                old='SELECT '+selected+' FROM original.'+quoted;new='SELECT '+selected+' FROM main.'+quoted
                if db.execute(old+' EXCEPT '+new+' LIMIT 1').fetchone() or db.execute(new+' EXCEPT '+old+' LIMIT 1').fetchone():
                    raise ValueError('Historical row contents changed during reconstruction')
            if db.execute('PRAGMA main.quick_check').fetchone()[0]!='ok' or db.execute('PRAGMA main.foreign_key_check').fetchone():
                raise ValueError('Historical reconstructed database failed integrity/foreign-key checks')
            db.commit()
        with target.open('r+b') as handle:os.fsync(handle.fileno())
        os.replace(target,path)
    finally:
        target.unlink(missing_ok=True)


def normalize_staged(root,scopes,known):
    """Canonical current DDL in a verified private copy; original rows retained."""
    from backend.engine.global_migration import _schema
    from backend.engine.global_store_paths import _STAGED,owned_root,active_generation
    root=Path(root).absolute();installation,_=owned_root(root)
    if (installation is None or root.parent!=installation/'.global-generations'
            or _STAGED.get()!=(str(installation),str(root))
            or any(p.is_symlink() for p in (root,*root.parents))):
        raise ValueError('Historical conversion requires explicit private staged construction')
    active=active_generation(installation)
    if active is not None and active[0]==root:raise ValueError('Historical conversion cannot rewrite an active generation')
    for scope in ('ledger','leases'):
        path=root/scopes[scope];actual=_schema(path)
        if actual==known[scope]:continue
        if predecessor(scope,actual) is None:raise ValueError('Unsupported historical '+scope+' schema')
        _rebuild(path,known[scope])
        if _schema(path)!=known[scope]:raise ValueError('Historical '+scope+' reconstruction differs from current trusted schema')
