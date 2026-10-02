"""Verified, project-scoped objects. Metadata stays in the local context registry.

Only owned CAS bytes are collected. File/NAS and optional S3 share the same
publication lifecycle; external originals and legacy path references are untouched.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

from backend.contracts.context import ArtifactRef, ContextRegistry, ProjectContext

CHUNK_BYTES = 4 * 1024 * 1024
KINDS = {'source', 'label', 'split', 'model', 'evaluation', 'flow', 'package', 'result'}


class ArtifactError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r'[a-f0-9]{64}', value) is None:
        raise ArtifactError('Invalid content hash', 422)
    return value


def _hash(handle):
    digest, size = hashlib.sha256(), 0
    for block in iter(lambda: handle.read(CHUNK_BYTES), b''):
        digest.update(block); size += len(block)
    return digest.hexdigest(), size


def _unlinked(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ArtifactError('Artifact storage cannot use symbolic links', 422)
    return path


class FileObjectAdapter:
    """Dedicated CAS directory on a local/server/NAS filesystem, never its DB."""
    def __init__(self, root):
        self.root = _unlinked(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        _digest(key)
        return _unlinked(self.root / key[:2] / key)

    def publish(self, key, source, size):
        target = self._path(key)
        target.parent.mkdir(exist_ok=True)
        if target.exists():
            with target.open('rb') as reader:
                if _hash(reader) != (key, size):
                    raise ArtifactError('Existing object hash verification failed')
            return
        # Copy and atomically link on the destination filesystem, including NAS.
        fd, name = tempfile.mkstemp(prefix='.publish-', dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as writer, Path(source).open('rb') as reader:
                for block in iter(lambda: reader.read(CHUNK_BYTES), b''):
                    writer.write(block)
                writer.flush(); os.fsync(writer.fileno())
            with temporary.open('rb') as reader:
                if _hash(reader) != (key, size):
                    raise ArtifactError('Object hash changed during publication')
            try:
                os.link(temporary, target)
            except FileExistsError:
                with target.open('rb') as reader:
                    if _hash(reader) != (key, size):
                        raise ArtifactError('Existing object hash verification failed')
        finally:
            temporary.unlink(missing_ok=True)

    def read_into(self, key, target):
        with self._path(key).open('rb') as reader:
            for block in iter(lambda: reader.read(CHUNK_BYTES), b''):
                target.write(block)

    def keys(self):
        for directory in self.root.iterdir():
            if directory.is_symlink() or not directory.is_dir() or re.fullmatch(r'[a-f0-9]{2}', directory.name) is None:
                continue
            for path in directory.iterdir():
                if not path.is_symlink() and path.is_file() and re.fullmatch(r'[a-f0-9]{64}', path.name) and path.name[:2] == directory.name:
                    yield path.name, path.stat().st_mtime

    def cleanup_publication_temps(self, cutoff):
        removed = 0
        for directory in self.root.iterdir():
            if directory.is_symlink() or not directory.is_dir() or re.fullmatch(r'[a-f0-9]{2}', directory.name) is None:
                continue
            for path in directory.glob('.publish-*'):
                if not path.is_symlink() and path.is_file() and path.stat().st_mtime <= cutoff:
                    path.unlink(); removed += 1
        return removed

    def delete(self, key):
        self._path(key).unlink(missing_ok=True)


class S3ObjectAdapter:
    """Optional boto3-compatible transport; no SDK or credentials are installed here."""
    def __init__(self, client, bucket, prefix):
        if not bucket or not re.fullmatch(r'[A-Za-z0-9/_-]+', prefix) or '..' in prefix:
            raise ArtifactError('Invalid configured S3 storage namespace', 422)
        self.client, self.bucket, self.prefix = client, bucket, prefix.rstrip('/') + '/'

    def _key(self, digest):
        return self.prefix + _digest(digest)

    def publish(self, key, source, size):
        try:
            with Path(source).open('rb') as body:
                self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=body,
                                       ContentLength=size, IfNoneMatch='*')
        except Exception as exc:
            code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
            if str(code) not in {'PreconditionFailed', '412'}:
                raise
        # ETag is not a SHA256 receipt (multipart/encryption differ): read back bytes.
        with tempfile.TemporaryFile() as reader:
            self.read_into(key, reader); reader.seek(0)
            if _hash(reader) != (key, size):
                raise ArtifactError('S3 object hash verification failed')

    def read_into(self, key, target):
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        reader = response['Body']
        try:
            for block in iter(lambda: reader.read(CHUNK_BYTES), b''):
                target.write(block)
        finally:
            reader.close()

    def keys(self):
        token = None
        while True:
            args = {'Bucket': self.bucket, 'Prefix': self.prefix}
            if token:
                args['ContinuationToken'] = token
            response = self.client.list_objects_v2(**args)
            for entry in response.get('Contents', []):
                key = entry['Key'][len(self.prefix):]
                if re.fullmatch(r'[a-f0-9]{64}', key):
                    yield key, entry['LastModified'].timestamp()
            if not response.get('IsTruncated'):
                break
            token = response['NextContinuationToken']

    def cleanup_publication_temps(self, cutoff):
        # Conditional single-object PUT has no client-created multipart upload.
        return 0

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))


class ArtifactStore:
    def __init__(self, registry: ContextRegistry, *, adapter=None, quota_bytes=10 * 1024**3,
                 grace_seconds=86400, upload_ttl=7 * 86400, clock=time.time):
        if quota_bytes < 1 or grace_seconds < 0 or upload_ttl < 1:
            raise ArtifactError('Invalid configured storage quota or grace period', 422)
        self.registry, self.quota_bytes, self.grace_seconds, self.clock = registry, quota_bytes, grace_seconds, clock
        self.upload_ttl = upload_ttl
        self.staging = _unlinked(registry.root / '.artifact-staging')
        self.staging.mkdir(exist_ok=True)
        self.adapter = adapter or FileObjectAdapter(registry.root / '.artifact-objects')
        with registry.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS stored_objects(sha256 TEXT PRIMARY KEY,size_bytes INTEGER NOT NULL,orphaned_at REAL)')
            db.execute('''CREATE TABLE IF NOT EXISTS artifact_uploads(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,kind TEXT NOT NULL,sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL,
                offset INTEGER NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,artifact_id TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS artifact_pins(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
                name TEXT NOT NULL,sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL,created REAL NOT NULL,
                UNIQUE(project_id,name))''')

    @classmethod
    def configured(cls, registry):
        bucket = os.environ.get('VISION_ARTIFACT_S3_BUCKET')
        directory = os.environ.get('VISION_ARTIFACT_OBJECT_ROOT')
        if bucket and directory:
            raise ArtifactError('Configure only one artifact storage adapter', 503)
        adapter = None
        if bucket:
            try:
                import boto3
            except ImportError as exc:
                raise ArtifactError('S3 storage requires the optional boto3 SDK in the server environment', 503) from exc
            endpoint = os.environ.get('VISION_ARTIFACT_S3_ENDPOINT')
            if endpoint and not endpoint.startswith('https://'):
                raise ArtifactError('S3 endpoint must use HTTPS', 503)
            adapter = S3ObjectAdapter(boto3.client('s3', endpoint_url=endpoint), bucket,
                                      'modu-artifacts/' + registry.workspace_id)
        elif directory:
            adapter = FileObjectAdapter(Path(directory) / registry.workspace_id)
        return cls(registry, adapter=adapter)

    def _context(self, context):
        return self.registry.project_key(context)

    def _backend(self, method, *args):
        try:
            result = getattr(self.adapter, method)(*args)
            return list(result) if method == 'keys' else result
        except ArtifactError:
            raise
        except Exception as exc:
            # SDK exceptions may contain endpoint/credential diagnostics. Keep
            # those out of the public API while preserving retryable state.
            raise ArtifactError('Object storage is unavailable; retry after recovery', 503) from exc

    def _stage(self, identifier):
        if re.fullmatch(r'[a-f0-9]{32}', identifier) is None:
            raise ArtifactError('Upload is unavailable', 404)
        return _unlinked(self.staging / identifier)

    def _upload(self, db, context, identifier):
        self._context(context)
        row = db.execute('SELECT id,kind,sha256,size_bytes,offset,state,artifact_id,updated FROM artifact_uploads WHERE id=? AND project_id=? AND actor_id=?',
                         (identifier, self._context(context), context.actor_id)).fetchone()
        if row is None:
            raise ArtifactError('Upload is unavailable in this project for this actor', 404)
        result = dict(zip(('id','kind','sha256','size_bytes','offset','state','artifact_id','updated'), row))
        result['expires_at'] = result['updated'] + self.upload_ttl if result['state'] in {'staging', 'verified'} else None
        return result

    def _usage(self, db, project_id):
        records = db.execute('SELECT a.sha256,m.size_bytes FROM artifacts a JOIN managed_artifacts m ON m.id=a.id WHERE a.project_id=?', (project_id,)).fetchall()
        records += db.execute('SELECT sha256,size_bytes FROM artifact_pins WHERE project_id=?', (project_id,)).fetchall()
        retained = sum({key: size for key, size in records}.values())
        reserved = db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM artifact_uploads WHERE project_id=? AND state IN ('staging','verified')", (project_id,)).fetchone()[0]
        return retained + reserved

    def begin(self, context, kind, expected_hash, size_bytes):
        self._context(context); _digest(expected_hash)
        if kind not in KINDS or type(size_bytes) is not int or size_bytes < 0:
            raise ArtifactError('Invalid artifact kind or size', 422)
        identifier = uuid.uuid4().hex
        with self.registry.transaction() as db:
            if self._usage(db, self._context(context)) + size_bytes > self.quota_bytes:
                raise ArtifactError('Project storage quota exceeded', 413)
            now = self.clock()
            db.execute('INSERT INTO artifact_uploads VALUES(?,?,?,?,?,?,0,?,?,?,NULL)',
                       (identifier, self._context(context), context.actor_id, kind, expected_hash, size_bytes, 'staging', now, now))
        return self.status(context, identifier)

    def status(self, context, identifier):
        with self.registry.transaction() as db:
            return self._upload(db, context, identifier)

    def append(self, context, identifier, offset, data):
        if type(offset) is not int or offset < 0 or not isinstance(data, bytes) or len(data) > CHUNK_BYTES:
            raise ArtifactError('Invalid upload offset or chunk size', 422)
        with self.registry.transaction() as db:
            upload = self._upload(db, context, identifier)
            if upload['state'] != 'staging':
                raise ArtifactError('Upload is not writable')
            path = self._stage(identifier)
            if offset + len(data) > upload['size_bytes'] or offset > upload['offset']:
                raise ArtifactError('Upload offset or size does not match')
            with path.open('r+b' if path.exists() else 'w+b') as handle:
                if path.stat().st_size < upload['offset']:
                    raise ArtifactError('Committed upload bytes are missing')
                if offset < upload['offset']:
                    handle.seek(offset)
                    if offset + len(data) > upload['offset'] or handle.read(len(data)) != data:
                        raise ArtifactError('Upload retry conflicts with committed bytes')
                    return upload
                handle.truncate(upload['offset']); handle.seek(offset); handle.write(data)
                handle.flush(); os.fsync(handle.fileno())
            db.execute('UPDATE artifact_uploads SET offset=?,updated=? WHERE id=?',
                       (offset + len(data), self.clock(), identifier))
        return self.status(context, identifier)

    def complete(self, context, identifier, *, authorize=lambda: None):
        with self.registry.transaction() as db:
            upload = self._upload(db, context, identifier)
            if upload['state'] == 'referenced':
                ref = ArtifactRef(id=upload['artifact_id'], revision=1, sha256=upload['sha256'])
                self.registry.managed_reference(self._context(context), ref, db=db)
                authorize()
                return ref
            if upload['state'] not in {'staging', 'verified'} or upload['offset'] != upload['size_bytes']:
                raise ArtifactError('Upload is incomplete or cancelled')
            path = self._stage(identifier)
            if not path.exists() and upload['size_bytes'] == 0:
                path.touch(exist_ok=False)
            with path.open('rb') as reader:
                if _hash(reader) != (upload['sha256'], upload['size_bytes']):
                    raise ArtifactError('Upload content hash does not match')
            self._backend('publish', upload['sha256'], path, upload['size_bytes'])
            db.execute('INSERT OR IGNORE INTO stored_objects VALUES(?,?,?)', (upload['sha256'], upload['size_bytes'], self.clock()))
            db.execute("UPDATE artifact_uploads SET state='verified',updated=? WHERE id=?", (self.clock(), identifier))
        # A verified object alone grants no read permission. Ref+upload publication
        # shares one registry commit, and failure retains resumable verified state.
        with self.registry.transaction() as db:
            upload = self._upload(db, context, identifier)
            if upload['state'] not in {'verified', 'referenced'}:
                raise ArtifactError('Upload was cancelled before publication')
            authorize()
            ref = self.registry.register_managed_artifact(self._context(context), upload['kind'], upload['sha256'],
                                                         upload['sha256'], upload['size_bytes'], db=db)
            db.execute("UPDATE artifact_uploads SET state='referenced',artifact_id=?,updated=? WHERE id=?", (ref.id, self.clock(), identifier))
            db.execute('UPDATE stored_objects SET orphaned_at=NULL WHERE sha256=?', (ref.sha256,))
        self._stage(identifier).unlink(missing_ok=True)
        return ref

    def put_verified(self, context, stream, expected_hash, size_bytes, *, kind='source', authorize=lambda: None):
        upload = self.begin(context, kind, expected_hash, size_bytes)
        try:
            offset = 0
            for data in iter(lambda: stream.read(CHUNK_BYTES), b''):
                self.append(context, upload['id'], offset, data); offset += len(data)
            return self.complete(context, upload['id'], authorize=authorize)
        except Exception:
            self.cancel(context, upload['id'])
            raise

    def cancel(self, context, identifier):
        with self.registry.transaction() as db:
            upload = self._upload(db, context, identifier)
            if upload['state'] == 'referenced':
                raise ArtifactError('Published uploads must be released by reference')
            db.execute("UPDATE artifact_uploads SET state='cancelled',updated=? WHERE id=?", (self.clock(), identifier))
        self._stage(identifier).unlink(missing_ok=True)

    def reference(self, context, ref):
        """The managed artifact's metadata once the reference is checked against this project (nothing is copied)."""
        meta = self.registry.managed_reference(self._context(context), ref)
        if meta is None:
            raise ArtifactError('Use the context API for legacy file references', 409)
        return meta

    @contextmanager
    def open(self, context, ref):
        self._context(context)
        # Keep a metadata transaction during verified copy so GC cannot remove it.
        with tempfile.TemporaryFile() as handle:
            with self.registry.transaction() as db:
                meta = self.registry.managed_reference(self._context(context), ref, db=db)
                if meta is None:
                    raise ArtifactError('Use the context API for legacy file references', 409)
                self._backend('read_into', meta['storage_key'], handle); handle.seek(0)
                if _hash(handle) != (ref.sha256, meta['size_bytes']):
                    raise ArtifactError('Stored artifact hash verification failed')
                handle.seek(0)
            yield handle

    def pin(self, context, ref, name):
        self._context(context)
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ArtifactError('Backup pin name is required', 422)
        with self.registry.transaction() as db:
            meta = self.registry.managed_reference(self._context(context), ref, db=db)
            if meta is None:
                raise ArtifactError('Only managed artifacts can be pinned', 422)
            row = db.execute('SELECT id,sha256 FROM artifact_pins WHERE project_id=? AND name=?', (self._context(context), name)).fetchone()
            if row and row[1] != ref.sha256:
                raise ArtifactError('Backup pin name already belongs to different content')
            identifier = row[0] if row else uuid.uuid4().hex
            db.execute('INSERT OR IGNORE INTO artifact_pins VALUES(?,?,?,?,?,?)', (identifier, self._context(context), name, ref.sha256, meta['size_bytes'], self.clock()))
        return {'id': identifier, 'name': name, 'artifact_ref': ref.model_dump()}

    def _orphan(self, db, digest):
        references = db.execute('SELECT 1 FROM artifacts a JOIN managed_artifacts m ON m.id=a.id WHERE a.sha256=? LIMIT 1', (digest,)).fetchone()
        pins = db.execute('SELECT 1 FROM artifact_pins WHERE sha256=? LIMIT 1', (digest,)).fetchone()
        if not references and not pins:
            db.execute('UPDATE stored_objects SET orphaned_at=COALESCE(orphaned_at,?) WHERE sha256=?', (self.clock(), digest))

    def release(self, context, ref):
        self._context(context)
        with self.registry.transaction() as db:
            self.registry.release_managed_artifact(self._context(context), ref, db=db)
            self._orphan(db, ref.sha256)

    def unpin(self, context, identifier):
        self._context(context)
        with self.registry.transaction() as db:
            row = db.execute('SELECT sha256 FROM artifact_pins WHERE id=? AND project_id=?', (identifier, self._context(context))).fetchone()
            if row is None:
                raise ArtifactError('Backup pin is unavailable in this project', 404)
            db.execute('DELETE FROM artifact_pins WHERE id=?', (identifier,)); self._orphan(db, row[0])

    def collect_garbage(self):
        removed, staging_removed = 0, 0
        with self.registry.transaction() as db:
            expired = db.execute("UPDATE artifact_uploads SET state='expired' WHERE state IN ('staging','verified') AND updated<=?",
                                 (self.clock() - self.upload_ttl,)).rowcount
            active = {r[0] for r in db.execute('SELECT a.sha256 FROM artifacts a JOIN managed_artifacts m ON m.id=a.id')}
            active.update(r[0] for r in db.execute('SELECT sha256 FROM artifact_pins'))
            active.update(r[0] for r in db.execute("SELECT sha256 FROM artifact_uploads WHERE state IN ('staging','verified')"))
            recorded = dict(db.execute('SELECT sha256,orphaned_at FROM stored_objects'))
            for digest, modified in self._backend('keys'):
                if digest in active:
                    continue
                since = recorded.get(digest, modified)
                if since is None:
                    db.execute('UPDATE stored_objects SET orphaned_at=? WHERE sha256=?', (self.clock(), digest))
                elif self.clock() - since >= self.grace_seconds:
                    self._backend('delete', digest)
                    db.execute('DELETE FROM stored_objects WHERE sha256=?', (digest,)); removed += 1
            active_uploads = {r[0] for r in db.execute("SELECT id FROM artifact_uploads WHERE state IN ('staging','verified')")}
            for path in self.staging.iterdir():
                if (re.fullmatch(r'[a-f0-9]{32}', path.name) and not path.is_symlink() and path.is_file()
                        and path.name not in active_uploads and self.clock() - path.stat().st_mtime >= self.grace_seconds):
                    path.unlink(); staging_removed += 1
            temporary_removed = self._backend('cleanup_publication_temps', self.clock() - self.grace_seconds)
        return {'objects_deleted': removed, 'staging_deleted': staging_removed, 'publication_temps_deleted': temporary_removed,
                'uploads_expired': expired, 'grace_seconds': self.grace_seconds}
