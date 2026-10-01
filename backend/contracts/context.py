"""Authenticated request identity and references to existing project artifacts.

Paths remain server-side registry data. This registry links existing files; it
does not replace their histories or implement the S1-06 upload/object store.
"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
import hashlib
import json
import re
from pathlib import Path
import sqlite3
from typing import Literal
import uuid
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_ID = r'^[A-Za-z0-9_.:-]{1,128}$'


class ProjectContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', strict=True)
    workspace_id: str = Field(pattern=_ID)
    project_id: str = Field(pattern=_ID)
    actor_id: str = Field(pattern=_ID)
    mode: Literal['local', 'team']


class ArtifactRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', strict=True)
    id: str = Field(pattern=_ID)
    revision: int = Field(gt=0)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class ArtifactRegistration(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['source', 'label', 'split', 'model', 'evaluation', 'flow', 'package', 'result']
    relative_path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_revision: int | None = Field(default=None, gt=0)


current_project_context: ContextVar[ProjectContext | None] = ContextVar('project_context', default=None)


def declared_context(headers, *, query_string: bytes | None = None) -> ProjectContext | None:
    """HTTP header, or bounded explicit WebSocket query for browser clients."""
    values = [headers['x-vision-context']] if headers.get('x-vision-context') is not None else []
    if query_string is not None:
        if len(query_string) > 16384:
            raise HTTPException(400, 'WebSocket query exceeds 16384 bytes')
        try:
            fields = parse_qs(query_string.decode('utf-8'), max_num_fields=16, keep_blank_values=True)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(400, 'Invalid WebSocket project context query') from exc
        supplied = fields.get('project_context', [])
        if len(supplied) > 1:
            raise HTTPException(400, 'Duplicate explicit WebSocket project context')
        values.extend(supplied)
    if not values:
        return None
    contexts = []
    for value in values:
        if len(value.encode('utf-8')) > 4096:
            raise HTTPException(400, 'Project context exceeds 4096 bytes')
        try:
            contexts.append(ProjectContext.model_validate(json.loads(value)))
        except (ValueError, TypeError, ValidationError, RecursionError) as exc:
            raise HTTPException(400, 'Invalid explicit project context') from exc
    if any(context != contexts[0] for context in contexts[1:]):
        raise HTTPException(400, 'Conflicting explicit project contexts')
    return contexts[0]


def get_project_context(request: Request) -> ProjectContext:
    context = getattr(request.state, 'project_context', None)
    if context is None:
        raise HTTPException(409, 'A project context is required')
    return context


def originating_context() -> dict | None:
    context = current_project_context.get()
    return context.model_dump() if context is not None else None


class ContextRegistry:
    """Workspace-scoped atomic identities and project-scoped immutable references."""
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.path = self.root / '.context.sqlite3'
        self.root.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS identities(name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS artifacts(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
                    relative_path TEXT NOT NULL, revision INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    UNIQUE(project_id, kind, relative_path));
                CREATE TABLE IF NOT EXISTS managed_artifacts(
                    id TEXT PRIMARY KEY REFERENCES artifacts(id),
                    storage_key TEXT NOT NULL, size_bytes INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS project_locations(
                    scope_key TEXT UNIQUE NOT NULL REFERENCES projects(id),
                    workspace_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    path TEXT UNIQUE NOT NULL, PRIMARY KEY(workspace_id,project_id));
            ''')
            for name in ('workspace_id', 'local_actor_id'):
                db.execute('INSERT OR IGNORE INTO identities VALUES(?, ?)', (name, uuid.uuid4().hex))
            identities = dict(db.execute('SELECT name, value FROM identities'))
            # Additive v1 -> v2 migration: retain old scope keys and all artifact
            # rows. Restored copies can now preserve their logical project IDs.
            for identifier, path in db.execute('SELECT id,path FROM projects').fetchall():
                if not db.execute('SELECT 1 FROM project_locations WHERE scope_key=?', (identifier,)).fetchone():
                    db.execute('INSERT INTO project_locations VALUES(?,?,?,?)',
                               (identifier, identities['workspace_id'], identifier, path))
            db.execute('INSERT OR REPLACE INTO identities VALUES(?,?)', ('schema_version','2'))
        self.workspace_id = identities['workspace_id']
        self.local_actor_id = identities['local_actor_id']

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        """Share atomic ref publication with S1-06 storage lifecycle tables."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    def register_project(self, project: dict):
        identifier = project['id']
        path = str(Path(project['project_dir']).resolve())
        with self.transaction() as db:
            row = db.execute('SELECT scope_key,project_id FROM project_locations WHERE path=?', (path,)).fetchone()
            if row:
                if row[1] != identifier:
                    raise HTTPException(409, 'Registered project manifest identity changed')
                return row[0]
            copies = db.execute('SELECT scope_key,workspace_id,path FROM project_locations WHERE project_id=?', (identifier,)).fetchall()
            # An unavailable old location does not prove that this is its move:
            # it can be a restored copy. Never redirect a captured old context.
            workspace = self.workspace_id if not copies else uuid.uuid4().hex
            key = uuid.uuid4().hex
            db.execute('INSERT INTO projects VALUES(?,?)', (key,path))
            db.execute('INSERT INTO project_locations VALUES(?,?,?,?)', (key,workspace,identifier,path))
            return key

    def local_project(self, identifier: str, request, *, workspace_id: str | None = None):
        from backend.api.routes_project import _load_history, _load_project
        if not isinstance(identifier, str) or not re.fullmatch(_ID, identifier):
            raise HTTPException(400, 'Invalid explicit project identity')
        with self._db() as db:
            rows = db.execute('SELECT path FROM project_locations WHERE project_id=?' +
                              (' AND workspace_id=?' if workspace_id else ''),
                              (identifier,workspace_id) if workspace_id else (identifier,)).fetchall()
        if not rows:
            if workspace_id is not None:
                raise HTTPException(404, 'Explicit workspace/project context is not registered')
            # Import existing identities without changing manifests, model IDs,
            # flow versions, or the legacy active-project pointer.
            candidates = [p for p in _load_history(request) if p.get('id') == identifier]
            for directory in self.root.iterdir():
                manifest = directory / 'project.json'
                if not directory.is_symlink() and manifest.is_file():
                    try:
                        if json.loads(manifest.read_text(encoding='utf-8')).get('id') == identifier:
                            candidates.append({'project_dir': str(directory)})
                    except (OSError, ValueError, AttributeError):
                        continue
            roots = {str(Path(p['project_dir']).resolve()) for p in candidates}
            if not roots:
                raise HTTPException(404, 'Explicit project identity is not registered')
            for root in sorted(roots):
                self.register_project(_load_project(Path(root)))
            return self.local_project(identifier, request)
        if len(rows) > 1:
            raise HTTPException(409, 'Project ID exists in multiple workspaces. Open the intended workspace, then send its explicit X-Vision-Context.')
        project = _load_project(Path(rows[0][0]))
        if project['id'] != identifier:
            raise HTTPException(409, 'Registered project manifest identity changed')
        return project

    def context(self, project: dict, account: dict | None) -> ProjectContext:
        key = self.register_project(project)
        with self._db() as db:
            workspace = db.execute('SELECT workspace_id FROM project_locations WHERE scope_key=?', (key,)).fetchone()[0]
        if account and workspace != self.workspace_id:
            raise HTTPException(409, 'Shared project authority requires its registered server workspace')
        return ProjectContext(workspace_id=workspace, project_id=project['id'],
                              actor_id=account['id'] if account else self.local_actor_id,
                              mode='team' if account else 'local')

    def project_key(self, context: ProjectContext, *, db=None) -> str:
        """Opaque internal authority key; never substitute a logical ID alone."""
        if db is None:
            with self._db() as connection:
                return self.project_key(context,db=connection)
        row = db.execute('SELECT scope_key FROM project_locations WHERE workspace_id=? AND project_id=?',
                         (context.workspace_id,context.project_id)).fetchone()
        if row is None:
            raise HTTPException(404, 'Workspace/project context is not registered')
        return row[0]

    @staticmethod
    def _artifact_path(project, kind, relative):
        roots = {'source': project.get('source_dataset_dir'), 'label': project['annotations_dir'],
                 'split': str(Path(project['dataset_dir']) / 'splits'), 'model': project['models_dir'],
                 'evaluation': project['reports_dir'], 'flow': str(Path(project['project_dir']) / 'flowcharts'),
                 'package': str(Path(project['project_dir']) / 'exports'), 'result': project['reports_dir']}
        root = roots[kind]
        # Reject Windows absolute/drive paths as well as POSIX traversal, on
        # every host. Resolution also fences symlinks outside the declared root.
        if not root or '\\' in relative or ':' in relative:
            raise HTTPException(422, 'Artifact path must be relative to its registered project root')
        suffix = Path(relative)
        root = Path(root).resolve()
        path = (root / suffix).resolve()
        if suffix.is_absolute() or '..' in suffix.parts or not path.is_relative_to(root):
            raise HTTPException(422, 'Artifact path is outside its registered project root')
        if not path.is_file():
            raise HTTPException(404, 'Project artifact is unavailable')
        return path, suffix.as_posix()

    @staticmethod
    def _sha(path):
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    def register_artifact(self, project, body: ArtifactRegistration) -> ArtifactRef:
        project_key = self.register_project(project)
        path, relative = self._artifact_path(project, body.kind, body.relative_path)
        if self._sha(path) != body.sha256:
            raise HTTPException(409, 'Artifact content hash does not match')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id, revision, sha256 FROM artifacts WHERE project_id=? AND kind=? AND relative_path=?',
                             (project_key, body.kind, relative)).fetchone()
            if row:
                identifier, revision, digest = row
                if body.expected_revision is not None and body.expected_revision != revision:
                    raise HTTPException(409, 'Artifact reference revision changed')
                if digest != body.sha256:
                    if body.expected_revision is None:
                        raise HTTPException(409, 'An expected revision is required to update an artifact reference')
                    revision += 1
                    db.execute('UPDATE artifacts SET revision=?, sha256=? WHERE id=?', (revision, body.sha256, identifier))
            else:
                if body.expected_revision is not None:
                    raise HTTPException(409, 'Artifact reference has no existing revision')
                identifier, revision = uuid.uuid4().hex, 1
                db.execute('INSERT INTO artifacts VALUES(?, ?, ?, ?, ?, ?)',
                           (identifier, project_key, body.kind, relative, revision, body.sha256))
        return ArtifactRef(id=identifier, revision=revision, sha256=body.sha256)

    def resolve_artifact(self, project, ref: ArtifactRef):
        project_key = self.register_project(project)
        if self.managed_reference(project_key, ref) is not None:
            raise HTTPException(409, 'Managed artifact content requires the artifact storage API')
        with self._db() as db:
            row = db.execute('SELECT kind, relative_path, revision, sha256 FROM artifacts WHERE id=? AND project_id=?',
                             (ref.id, project_key)).fetchone()
        if row is None:
            raise HTTPException(404, 'Artifact reference is not registered in this project')
        kind, relative, revision, digest = row
        if (revision, digest) != (ref.revision, ref.sha256):
            raise HTTPException(409, 'Artifact reference revision or hash changed')
        path, _ = self._artifact_path(project, kind, relative)
        if self._sha(path) != digest:
            raise HTTPException(409, 'Artifact content changed since registration')
        return path

    def register_managed_artifact(self, project_id, kind, sha256, storage_key, size_bytes, *, db=None) -> ArtifactRef:
        """Register a verified CAS object in the caller's storage transaction.

        The storage service verifies object state/hash, quota, and actor rights
        before calling this hook. This hook owns the one project-ref identity.
        """
        if db is None:
            with self.transaction() as connection:
                return self.register_managed_artifact(project_id, kind, sha256, storage_key, size_bytes, db=connection)
        if not isinstance(sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', sha256) or storage_key != sha256:
            raise HTTPException(422, 'Managed storage key must be its verified SHA256')
        if type(size_bytes) is not int or size_bytes < 0 or kind not in {'source', 'label', 'split', 'model', 'evaluation', 'flow', 'package', 'result'}:
            raise HTTPException(422, 'Invalid managed artifact kind or size')
        if not db.execute('SELECT id FROM projects WHERE id=?', (project_id,)).fetchone():
            raise HTTPException(404, 'Managed artifact project is not registered')
        relative = 'sha256:' + sha256
        row = db.execute('SELECT id, revision, sha256 FROM artifacts WHERE project_id=? AND kind=? AND relative_path=?',
                         (project_id, kind, relative)).fetchone()
        if row:
            ref = ArtifactRef(id=row[0], revision=row[1], sha256=row[2])
            managed = self.managed_reference(project_id, ref, db=db)
            if managed is None or managed['size_bytes'] != size_bytes or managed['storage_key'] != storage_key:
                raise HTTPException(409, 'Managed artifact identity has different storage metadata')
            return ref
        ref = ArtifactRef(id=uuid.uuid4().hex, revision=1, sha256=sha256)
        db.execute('INSERT INTO artifacts VALUES(?, ?, ?, ?, ?, ?)', (ref.id, project_id, kind, relative, ref.revision, ref.sha256))
        db.execute('INSERT INTO managed_artifacts VALUES(?, ?, ?)', (ref.id, storage_key, size_bytes))
        return ref

    def managed_reference(self, project_id, ref: ArtifactRef, *, db=None):
        if db is None:
            with self._db() as connection:
                return self.managed_reference(project_id, ref, db=connection)
        row = db.execute('SELECT kind, revision, sha256 FROM artifacts WHERE id=? AND project_id=?', (ref.id, project_id)).fetchone()
        if row is None:
            raise HTTPException(404, 'Artifact reference is not registered in this project')
        if (row[1], row[2]) != (ref.revision, ref.sha256):
            raise HTTPException(409, 'Artifact reference revision or hash changed')
        managed = db.execute('SELECT storage_key, size_bytes FROM managed_artifacts WHERE id=?', (ref.id,)).fetchone()
        return {'id': ref.id, 'project_id': project_id, 'kind': row[0], 'revision': ref.revision,
                'sha256': ref.sha256, 'storage_key': managed[0], 'size_bytes': managed[1]} if managed else None

    def release_managed_artifact(self, project_id, ref: ArtifactRef, *, db=None):
        """Storage service checks pins/leases before deleting this scoped ref."""
        if db is None:
            with self.transaction() as connection:
                return self.release_managed_artifact(project_id, ref, db=connection)
        if self.managed_reference(project_id, ref, db=db) is None:
            raise HTTPException(409, 'Reference is not a managed artifact')
        db.execute('DELETE FROM managed_artifacts WHERE id=?', (ref.id,))
        db.execute('DELETE FROM artifacts WHERE id=? AND project_id=?', (ref.id, project_id))
