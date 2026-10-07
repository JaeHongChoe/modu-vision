"""Project-owned retention with durable pins and recoverable trash, never purge.

Backup, deployment publication and retention share this lock. Source datasets,
labels and bookkeeping are outside the cleanup surface. Quota includes trash:
archiving an artifact does not pretend to free physical disk space.
"""
from __future__ import annotations

from contextlib import contextmanager,closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from backend.engine.runtime_process_control import runtime_state_lock

MANAGED_ROOTS = ('models', 'reports', 'exports', 'versions', 'runtime_service/releases',
                 'runtime_service/state/uploads', 'dataset/capture_intake/candidates', 'dataset/capture_intake/drift')


def retention_project_root(directory):
    directory=Path(directory).resolve()
    for candidate in (directory,*directory.parents):
        if (candidate/'project.json').is_file():return candidate
    return directory.parent


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as reader:
        for block in iter(lambda: reader.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def _inventory(path):
    files = [path] if path.is_file() else sorted(path.rglob('*'))
    rows = []
    for item in files:
        if item.is_symlink(): raise ValueError('Retention cannot move symbolic links')
        if item.is_dir(): continue
        if not item.is_file(): raise ValueError('Retention artifact contains an unsupported file')
        rows.append({'path': '.' if item == path else item.relative_to(path).as_posix(),
                     'size': item.stat().st_size, 'sha256': _digest(item)})
    return rows


def _size(path):
    files=[path] if path.is_file() else path.rglob('*')
    total=0
    for item in files:
        if item.is_symlink():raise ValueError('Retention cannot inspect symbolic links')
        if item.is_file():total+=item.stat().st_size
    return total


def _review_identity(path):
    """Bind empty directories, replacements and ages as well as file bytes."""
    items = [path, *sorted(path.rglob('*'))] if path.is_dir() else [path]
    rows = []
    for item in items:
        if item.is_symlink(): raise ValueError('Retention cannot inspect symbolic links')
        stat = item.stat()
        if not item.is_file() and not item.is_dir():
            raise ValueError('Retention artifact contains an unsupported file')
        rows.append({'path': '.' if item == path else item.relative_to(path).as_posix(),
                     'kind': 'directory' if item.is_dir() else 'file',
                     'device': stat.st_dev, 'inode': stat.st_ino, 'mtime_ns': stat.st_mtime_ns})
    return rows


class ArtifactRetention:
    def __init__(self, project_dir):
        root = Path(project_dir).expanduser()
        if root.is_symlink() or not root.is_dir(): raise ValueError('Retention needs an owned project directory')
        self.root = root.resolve(); self.directory = self.root / '.retention'
        if self.directory.is_symlink(): raise ValueError('Retention storage is linked')
        self.directory.mkdir(exist_ok=True)
        if (self.directory/'trash').is_symlink():raise ValueError('Retention trash storage is linked')
        self.path = self.directory / 'retention.sqlite3'
        if self.path.is_symlink(): raise ValueError('Retention database is linked')
        with self.lock(),self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pins(owner TEXT,relative_path TEXT,reason TEXT,created_at REAL,PRIMARY KEY(owner,relative_path));
                CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY CHECK(id=1),retention_days INTEGER,trash_days INTEGER,quota_bytes INTEGER);
                INSERT OR IGNORE INTO policy VALUES(1,30,30,NULL);
                CREATE TABLE IF NOT EXISTS trash(trash_id TEXT PRIMARY KEY,relative_path TEXT,state TEXT,inventory TEXT,size_bytes INTEGER,created_at REAL,restored_at REAL);
                CREATE TABLE IF NOT EXISTS backups(backup_id TEXT PRIMARY KEY,status TEXT,archive_path TEXT,archive_sha256 TEXT,created_at REAL,restore_verified INTEGER,details TEXT);
                CREATE TABLE IF NOT EXISTS restores(restore_id TEXT PRIMARY KEY,status TEXT,archive_sha256 TEXT,created_at REAL,details TEXT);
                CREATE TABLE IF NOT EXISTS audit(sequence INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,details TEXT,created_at REAL);
            ''')

    @contextmanager
    def connect(self):
        if self.path.is_symlink(): raise ValueError('Retention database is linked')
        db = sqlite3.connect(self.path, timeout=10); db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:yield db
        finally:db.close()

    @contextmanager
    def lock(self):
        try:
            with runtime_state_lock(self.directory): yield
        except ValueError as exc:
            if 'lifecycle operation' in str(exc): raise ValueError('Artifact retention operation is busy; retry after backup/deployment completes') from exc
            raise

    def _relative(self, path, *, managed=False):
        path = Path(path)
        if '..' in path.parts: raise ValueError('Retention paths cannot traverse a parent directory')
        path = path if path.is_absolute() else self.root / path
        if not path.is_relative_to(self.root): raise ValueError('Retention path is outside this project; source datasets cannot be moved')
        if any(part.is_symlink() for part in [path, *path.parents] if part.is_relative_to(self.root)):
            raise ValueError('Retention cannot access linked artifact paths')
        relative = path.relative_to(self.root).as_posix()
        if managed and not any(relative.startswith(prefix + '/') for prefix in MANAGED_ROOTS):
            raise ValueError('Only managed artifacts can enter retention trash; source and labels are protected')
        return relative

    def pin(self, owner, paths, *, reason, replace=False):
        if not isinstance(owner,str) or not owner.strip() or not isinstance(reason,str) or not reason.strip():
            raise ValueError('A pin owner and retention reason are required')
        relatives = [self._relative(path) for path in paths]
        if not relatives: raise ValueError('Choose at least one pinned artifact')
        with self.lock(), self.connect() as db:
            if replace: db.execute('DELETE FROM pins WHERE owner=?',(owner,))
            for relative in relatives:
                db.execute('INSERT OR REPLACE INTO pins VALUES(?,?,?,?)',(owner,relative,reason,time.time()))
        return relatives

    def unpin(self, owner):
        with self.lock(), self.connect() as db: db.execute('DELETE FROM pins WHERE owner=?',(owner,))

    def configure(self, *, retention_days, trash_days, quota_bytes=None):
        if any(type(value) is not int or not 0<=value<=3650 for value in (retention_days,trash_days)):
            raise ValueError('Retention periods must be integer days between 0 and 3650')
        if quota_bytes is not None and (type(quota_bytes) is not int or quota_bytes<1): raise ValueError('Quota must be a positive byte count')
        with self.lock(), self.connect() as db:
            db.execute('UPDATE policy SET retention_days=?,trash_days=?,quota_bytes=? WHERE id=1',(retention_days,trash_days,quota_bytes))
        return {'retention_days':retention_days,'trash_days':trash_days,'quota_bytes':quota_bytes}

    def release_paths(self, release):
        paths=[]
        for key in ('package_path','release_policy'):
            if release and release.get(key):
                path=Path(release[key])
                if path.is_absolute() and path.is_relative_to(self.root):paths.append(self.root/self._relative(path))
        return paths

    def refresh_references(self, project):
        """Retain imported durable pins; derive current references before every move."""
        with self.lock():
            with self.connect() as db:
                recorded=db.execute("SELECT relative_path FROM pins WHERE reason='drift_reference'").fetchall()
            for (relative,) in recorded:
                if relative.startswith('dataset/capture_intake/drift/') and not (self.root/relative).is_file():
                    raise ValueError('Pinned drift reference is unavailable; retention requires review')
            ledgers=[self.root/'runtime_service'/'runtime_deployments.sqlite3',*sorted((self.root/'fleet').glob('*/runtime_deployments.sqlite3'))]
            for runtime in ledgers:
                self._relative(runtime)
                if not runtime.is_file():continue
                with closing(sqlite3.connect(runtime.as_uri()+'?mode=ro',uri=True)) as db:
                    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    active=db.execute('SELECT release FROM deployments WHERE deployment_id=(SELECT deployment_id FROM active WHERE id=1)').fetchone() if {'deployments','active'}.issubset(tables) else None
                    pending=db.execute("SELECT operation_id,release,previous FROM update_operations WHERE status IN ('applying','rolling_back','needs_review','interrupted_without_previous')").fetchall() if 'update_operations' in tables else []
                scope=runtime.parent.relative_to(self.root).as_posix()
                if active:
                    paths=self.release_paths(json.loads(active[0]))
                    if paths:self.pin('derived:active-release:'+scope,paths,reason='active_release',replace=True)
                for identifier,release,previous in pending:
                    paths=self.release_paths(json.loads(release))
                    prior=json.loads(previous) if previous else None
                    if prior:paths+=self.release_paths(prior.get('release'))
                    if paths:self.pin('derived:pending-release:'+identifier,paths,reason='pending_release',replace=True)
            capture=self.root/'dataset'/'capture_intake';reference_dir=capture/'drift'
            if reference_dir.is_symlink() or capture.is_symlink(): raise ValueError('Drift reference storage is linked')
            index_path=capture/'index.json'
            if index_path.is_symlink():raise ValueError('Capture reference index is linked')
            index=json.loads(index_path.read_text(encoding='utf-8')) if index_path.is_file() else {'candidates':{}}
            from backend.engine.image_truth import digest
            for reference in reference_dir.glob('driftref_*.json'):
                self._relative(reference)
                record=json.loads(reference.read_text(encoding='utf-8'));unsigned={key:value for key,value in record.items() if key!='record_sha256'}
                if record.get('record_sha256')!=digest(unsigned) or record.get('reference_id')!=reference.stem:
                    raise ValueError('Drift reference hash changed; retention requires review')
                paths=[reference]
                for sample in record['samples']:
                    row=index['candidates'].get(sample['candidate_id'])
                    if not row or any(row.get(key)!=sample.get(key) for key in ('source_sha256','job_receipt_sha256')):
                        raise ValueError('Drift reference capture identity changed; retention requires review')
                    relative_snapshot=Path(row['snapshot_path'])
                    if relative_snapshot.is_absolute() or '..' in relative_snapshot.parts:raise ValueError('Drift snapshot path traverses the owned capture root')
                    snapshot=capture/relative_snapshot;original=Path(row['origin']['image_path'])
                    self._relative(snapshot)
                    if not snapshot.is_relative_to(capture/'candidates'):raise ValueError('Drift snapshot is outside the capture candidates')
                    original_roots=[self.root/'runtime_service'/'state'/'uploads']
                    if project.get('source_dataset_dir'):original_roots.append(Path(project['source_dataset_dir']).resolve())
                    allowed=next((root for root in original_roots if original.is_relative_to(root)),None)
                    if allowed is None:raise ValueError('Drift original image is outside the registered source/uploads')
                    if any(parent.is_symlink() for parent in (original,*original.parents) if parent.is_relative_to(allowed)):
                        raise ValueError('Drift original image path is linked')
                    for image in (snapshot,original):
                        if image.is_symlink() or not image.is_file() or _digest(image)!=sample['source_sha256']:
                            raise ValueError('Drift reference image changed; retention requires review')
                        if image.is_relative_to(self.root):paths.append(image)
                    # Outside-project originals cannot be moved by this retention service.
                self.pin('derived:drift:'+reference.stem,paths,reason='drift_reference',replace=True)

    def _protected(self, relative):
        with self.connect() as db:pins=db.execute('SELECT relative_path FROM pins').fetchall()
        path=Path(relative)
        return any(path.is_relative_to(Path(row[0])) or Path(row[0]).is_relative_to(path) for row in pins)

    def _reconcile_trash(self):
        with self.connect() as db:
            rows=db.execute("SELECT * FROM trash WHERE state IN ('moving','restoring')").fetchall()
            for row in rows:
                payload=self.directory/'trash'/row['trash_id']/'payload';original=self.root/row['relative_path']
                if row['state']=='moving' and payload.exists() and not original.exists():db.execute("UPDATE trash SET state='trashed' WHERE trash_id=?",(row['trash_id'],))
                elif row['state']=='moving' and original.exists() and not payload.exists():db.execute("UPDATE trash SET state='move_failed' WHERE trash_id=?",(row['trash_id'],))
                elif row['state']=='restoring' and original.exists() and not payload.exists():db.execute("UPDATE trash SET state='restored',restored_at=? WHERE trash_id=?",(time.time(),row['trash_id']))
                elif row['state']=='restoring' and payload.exists() and not original.exists():db.execute("UPDATE trash SET state='trashed' WHERE trash_id=?",(row['trash_id'],))
                else:raise ValueError('Trash recovery state is ambiguous; preserve both paths for review')

    def move_to_trash(self, paths, *, project, retention_days=None, dry_run=False, expected_preview_sha256=None):
        if not paths or len(paths)>1000:raise ValueError('Choose 1–1000 managed artifacts')
        if expected_preview_sha256 is not None and (not isinstance(expected_preview_sha256,str)
                or not re.fullmatch('[0-9a-f]{64}',expected_preview_sha256)):
            raise ValueError('Invalid retention preview identity')
        with self.lock():
            self.refresh_references(project);self._reconcile_trash()
            with self.connect() as db:
                policy=dict(db.execute('SELECT * FROM policy').fetchone())
                pins=[dict(row) for row in db.execute('SELECT owner,relative_path,reason FROM pins ORDER BY owner,relative_path')]
            days=policy['retention_days'] if retention_days is None else retention_days
            if type(days)is not int or not 0<=days<=3650:raise ValueError('Retention period must be bounded integer days')
            selected=[];identities={}
            for path in paths:
                relative=self._relative(path,managed=True);owned=self.root/relative
                protected_roots=[self.root/'annotations',self.root/'labelsets']
                protected_roots += [Path(project[key]).expanduser().resolve() for key in ('source_dataset_dir','annotations_dir') if project.get(key)]
                if any(owned.is_relative_to(protected) or protected.is_relative_to(owned) for protected in protected_roots):
                    raise ValueError('Registered source and annotation/labelset paths are protected from retention')
                if not owned.exists():raise ValueError('Retention artifact is unavailable')
                if self._protected(relative):raise ValueError('Artifact is pinned by backup, release, legal hold, or drift reference')
                # Only complete model outputs may be reclaimed automatically.
                if relative.startswith('models/'):
                    model_dir=self.root/'models'/Path(relative).parts[1]
                    for name in ('remote_job.json','job_receipt.json'):
                        journal=model_dir/name
                        if journal.is_file() and json.loads(journal.read_text(encoding='utf-8')).get('status') in ('queued','preparing','running','stopping','disconnected'):
                            raise ValueError('Active or unresolved training artifact is protected')
                inventory=_inventory(owned)
                identity=_review_identity(owned)
                latest=max(row['mtime_ns'] for row in identity)/1_000_000_000
                if time.time()-latest<days*86400:raise ValueError('Artifact is still within its configured retention period')
                selected.append((relative,inventory))
                identities[relative]=identity
            if len({relative for relative,_ in selected})!=len(selected) or any(Path(a).is_relative_to(Path(b)) for a,_ in selected for b,_ in selected if a!=b):
                raise ValueError('Retention candidates cannot duplicate or overlap')
            stat=self.root.stat()
            subject={'schema_version':1,'root':{'path':str(self.root),'device':stat.st_dev,'inode':stat.st_ino},
                'project':{key:project.get(key) for key in ('id','project_dir','task','source_dataset_dir','annotations_dir')},
                'policy':policy,'effective_retention_days':days,'pins':pins,
                'artifacts':[{'relative_path':relative,'inventory':rows,'identity':identities[relative]} for relative,rows in sorted(selected)]}
            preview_sha256=hashlib.sha256(json.dumps(subject,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            if expected_preview_sha256 is not None and expected_preview_sha256!=preview_sha256:
                raise ValueError('Retention preview changed; review a new preview before moving artifacts')
            if dry_run:return {'dry_run':True,'preview_sha256':preview_sha256,'eligible':[{'relative_path':relative,'size_bytes':sum(row['size'] for row in rows)} for relative,rows in selected],'trashed':[]}
            moved=[]
            for relative,rows in selected:
                identifier=uuid.uuid4().hex;target=self.directory/'trash'/identifier;target.mkdir(parents=True)
                with self.connect() as db:db.execute('INSERT INTO trash VALUES(?,?,?,?,?,?,NULL)',(identifier,relative,'moving',json.dumps(rows),sum(row['size'] for row in rows),time.time()))
                # Recheck all bytes immediately before moving the recoverable object.
                if _inventory(self.root/relative)!=rows or _review_identity(self.root/relative)!=identities[relative]:
                    raise ValueError('Artifact changed during retention; preserved original')
                os.replace(self.root/relative,target/'payload')
                with self.connect() as db:db.execute("UPDATE trash SET state='trashed' WHERE trash_id=?",(identifier,))
                moved.append({'trash_id':identifier,'relative_path':relative,'size_bytes':sum(row['size'] for row in rows)})
            return {'dry_run':False,'preview_sha256':preview_sha256,'trashed':moved}

    def restore_trash(self, identifier):
        if not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('Invalid trash identity')
        with self.lock():
            self._reconcile_trash()
            with self.connect() as db:row=db.execute('SELECT * FROM trash WHERE trash_id=?',(identifier,)).fetchone()
            if row is None or row['state']!='trashed':raise ValueError('Recoverable trash is unavailable')
            relative=self._relative(row['relative_path'],managed=True);target=self.root/relative
            payload=self.directory/'trash'/identifier/'payload'
            if target.exists():raise ValueError('Restore target already exists; existing artifacts will not be overwritten')
            if _inventory(payload)!=json.loads(row['inventory']):raise ValueError('Recoverable trash hash changed')
            target.parent.mkdir(parents=True,exist_ok=True)
            with self.connect() as db:db.execute("UPDATE trash SET state='restoring' WHERE trash_id=?",(identifier,))
            os.replace(payload,target)
            with self.connect() as db:db.execute("UPDATE trash SET state='restored',restored_at=? WHERE trash_id=?",(time.time(),identifier))
            return {'trash_id':identifier,'state':'restored','relative_path':relative}

    def begin_backup(self):
        identifier=uuid.uuid4().hex
        with self.lock(),self.connect() as db:
            db.execute('INSERT INTO backups VALUES(?,?,NULL,NULL,?,0,?)',(identifier,'reading',time.time(),'{}'))
            db.execute('INSERT INTO pins VALUES(?,?,?,?)',('backup:'+identifier,'.','backup_read',time.time()))
            db.execute('INSERT INTO audit(kind,details,created_at) VALUES(?,?,?)',('backup_read',json.dumps({'backup_id':identifier}),time.time()))
        return identifier

    def finish_backup(self, identifier, result):
        with self.lock(),self.connect() as db:
            db.execute('UPDATE backups SET status=?,archive_path=?,archive_sha256=?,details=? WHERE backup_id=?',
                ('archive_verified',result['archive_path'],result['archive_sha256'],json.dumps(result),identifier))
            db.execute('DELETE FROM pins WHERE owner=?',('backup:'+identifier,))
            db.execute('INSERT INTO audit(kind,details,created_at) VALUES(?,?,?)',('backup_verified',json.dumps({'backup_id':identifier,'archive_sha256':result['archive_sha256']}),time.time()))

    def fail_backup(self, identifier):
        with self.lock(),self.connect() as db:
            db.execute("UPDATE backups SET status='failed' WHERE backup_id=?",(identifier,));db.execute('DELETE FROM pins WHERE owner=?',('backup:'+identifier,))

    def verified_restore(self, archive_sha256, details):
        with self.lock(),self.connect() as db:
            # Snapshot pins protect the original reader. Once this fresh copy is
            # verified they are no longer readers of the restored workspace.
            db.execute("DELETE FROM pins WHERE reason='backup_read'")
            db.execute("UPDATE backups SET status='restored_snapshot',restore_verified=1 WHERE status='reading'")
            db.execute('INSERT INTO restores VALUES(?,?,?,?,?)',(uuid.uuid4().hex,'restore_verified',archive_sha256,time.time(),json.dumps(details)))
            db.execute('INSERT INTO audit(kind,details,created_at) VALUES(?,?,?)',('restore_verified',json.dumps({'archive_sha256':archive_sha256}),time.time()))

    def audit_cursor(self):
        with self.connect() as db:return db.execute('SELECT COALESCE(MAX(sequence),0) FROM audit').fetchone()[0]

    def status(self, project):
        with self.lock():
            self.refresh_references(project);self._reconcile_trash()
            with self.connect() as db:
                policy=dict(db.execute('SELECT * FROM policy').fetchone());policy.pop('id')
                pins=[dict(row) for row in db.execute('SELECT * FROM pins ORDER BY owner,relative_path')]
                trash=[dict(row) for row in db.execute('SELECT * FROM trash ORDER BY created_at DESC')]
                backups=[dict(row) for row in db.execute('SELECT * FROM backups ORDER BY created_at DESC')]
                restores=[dict(row) for row in db.execute('SELECT * FROM restores ORDER BY created_at DESC')]
            active_bytes=0
            for prefix in MANAGED_ROOTS:
                folder=self.root/prefix
                if folder.is_symlink():raise ValueError('Managed retention root is linked')
                if folder.exists():active_bytes+=_size(folder)
            trash_bytes=sum(row['size_bytes'] for row in trash if row['state']=='trashed')
            for row in trash:
                row.pop('inventory');row['purge_eligible']=row['state']=='trashed' and time.time()-row['created_at']>=policy['trash_days']*86400
            for row in backups:row['restore_verified']=bool(row['restore_verified']);row['details']=json.loads(row['details'])
            for row in restores:row['details']=json.loads(row['details'])
            total=active_bytes+trash_bytes
            return {'schema_version':1,'policy':policy,'pins':pins,'trash':trash,'backups':backups,'restores':restores,
                'active_bytes':active_bytes,'trash_bytes':trash_bytes,'total_bytes':total,
                'over_quota':policy['quota_bytes'] is not None and total>policy['quota_bytes'],'permanent_deletion_supported':False}
