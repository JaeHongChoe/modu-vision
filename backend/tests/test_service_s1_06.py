"""Storage publication through authenticated APIs; all bytes are isolated fixtures."""
import hashlib
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.contracts.context import ContextRegistry, ProjectContext


def sha(data):
    return hashlib.sha256(data).hexdigest()


def local(tmp_path):
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    projects = [client.post('/api/project/create', json={'name': name, 'task': 'classification'}).json()
                for name in ('First', 'Second')]
    return app, client, projects


def header(project):
    return {'X-Vision-Project': project['id']}


def begin(client, project, data, **fields):
    return client.post('/api/artifacts/uploads', headers=header(project), json={
        'kind': 'source', 'size_bytes': len(data), 'sha256': sha(data), **fields})


def upload(client, project, data):
    started = begin(client, project, data)
    assert started.status_code == 200, started.text
    identifier = started.json()['upload']['id']
    written = client.put(f'/api/artifacts/uploads/{identifier}', params={'offset': 0},
                         headers={**header(project), 'Content-Type': 'application/octet-stream'}, content=data)
    assert written.status_code == 200, written.text
    completed = client.post(f'/api/artifacts/uploads/{identifier}/complete', headers=header(project))
    assert completed.status_code == 200, completed.text
    return completed.json()['artifact_ref']


def content(client, project, ref, **headers):
    return client.get(f'/api/artifacts/{ref["id"]}/content', params={'revision': ref['revision'], 'sha256': ref['sha256']},
                      headers={**header(project), **headers})


def test_real_api_resume_restart_verified_ref_range_and_project_isolation(tmp_path):
    app, client, (first, second) = local(tmp_path)
    data = b'verified upload fixture'
    started = begin(client, first, data)
    assert started.status_code == 200, started.text
    identifier = started.json()['upload']['id']
    endpoint = f'/api/artifacts/uploads/{identifier}'
    headers = {**header(first), 'Content-Type': 'application/octet-stream'}
    assert client.put(endpoint, params={'offset': 0}, headers=headers, content=data[:8]).status_code == 200
    assert client.put(endpoint, params={'offset': 0}, headers=headers, content=data[:8]).status_code == 200
    assert client.put(endpoint, params={'offset': 0}, headers=headers, content=b'conflict').status_code == 409
    assert client.get(endpoint, headers=header(second)).status_code == 404
    restarted = create_app(project_dir=str(tmp_path / 'projects'))
    other = TestClient(restarted, headers={'X-Vision-Token': restarted.state.api_token})
    assert other.get(endpoint, headers=header(first)).json()['upload']['offset'] == 8
    assert other.put(endpoint, params={'offset': 8}, headers=headers, content=data[8:]).status_code == 200
    complete = other.post(endpoint + '/complete', headers=header(first))
    assert complete.status_code == 200, complete.text
    ref = complete.json()['artifact_ref']
    assert complete.json()['project_context']['project_id'] == first['id']
    assert str(tmp_path) not in complete.text
    assert other.post(endpoint + '/complete', headers=header(first)).json()['artifact_ref'] == ref
    assert content(other, first, ref).content == data
    ranged = content(other, first, ref, Range='bytes=3-8')
    assert ranged.status_code == 206 and ranged.content == data[3:9]
    assert content(other, second, ref).status_code == 404
    assert content(other, first, {**ref, 'revision': 2}).status_code == 409
    assert content(other, first, ref, Range='bytes=1000-').status_code == 416


def store_fixture(tmp_path, **kwargs):
    from backend.engine.artifact_store import ArtifactStore
    registry = ContextRegistry(tmp_path / 'metadata')
    context = ProjectContext(workspace_id=registry.workspace_id, project_id='fixture-project',
                             actor_id=registry.local_actor_id, mode='local')
    registry.register_project({'id': context.project_id, 'project_dir': str(tmp_path / 'project')})
    return ArtifactStore(registry, **kwargs), context


def test_store_hash_failure_quota_cancel_and_no_published_partial(tmp_path):
    store, ctx = store_fixture(tmp_path, quota_bytes=8)
    session = store.begin(ctx, 'source', sha(b'abcdefgh'), 8)
    with pytest.raises(Exception, match='quota'):
        store.begin(ctx, 'source', sha(b'other'), 5)
    store.append(ctx, session['id'], 0, b'badbytes')
    with pytest.raises(Exception, match='hash'):
        store.complete(ctx, session['id'])
    assert store.status(ctx, session['id'])['state'] == 'staging'
    store.cancel(ctx, session['id'])
    assert store.begin(ctx, 'source', sha(b'new'), 3)['offset'] == 0
    with store.registry.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM managed_artifacts').fetchone()[0] == 0


def test_store_shared_hash_references_backup_pin_and_gc_grace(tmp_path):
    now = [1000.0]
    store, first = store_fixture(tmp_path, grace_seconds=60, clock=lambda: now[0])
    second = first.model_copy(update={'project_id': 'second-project'})
    store.registry.register_project({'id': second.project_id, 'project_dir': str(tmp_path / 'second')})
    data = b'identical content'
    a = store.put_verified(first, io.BytesIO(data), sha(data), len(data), kind='source')
    b = store.put_verified(second, io.BytesIO(data), sha(data), len(data), kind='source')
    assert a.id != b.id
    pin = store.pin(first, a, 'backup-one')
    store.release(first, a)
    now[0] += 120
    assert store.collect_garbage()['objects_deleted'] == 0
    with store.open(second, b) as handle:
        assert handle.read() == data
    store.release(second, b)
    assert store.collect_garbage()['objects_deleted'] == 0
    store.unpin(first, pin['id'])
    assert store.collect_garbage()['objects_deleted'] == 0
    now[0] += 61
    assert store.collect_garbage()['objects_deleted'] == 1


def test_ingest_preserves_read_only_source_and_legacy_fingerprint(tmp_path):
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    _, client, (project, _) = local(tmp_path)
    source = tmp_path / 'read-only-source'; source.mkdir()
    path = source / 'source.png'; path.write_bytes(b'original fixture bytes')
    assert client.put('/api/project/update', headers=header(project), json={'source_dataset_dir': str(source)}).status_code == 200
    path.chmod(0o444); source.chmod(0o555)
    before = fingerprint_dataset(source, use_scope=False)
    try:
        response = client.post('/api/dataset/artifacts/ingest', headers=header(project), json={
            'source_name': 'source.png', 'sha256': sha(path.read_bytes())})
        assert response.status_code == 200, response.text
        assert content(client, project, response.json()['artifact_ref']).content == path.read_bytes()
        assert fingerprint_dataset(source, use_scope=False) == before
        assert set(p.name for p in source.iterdir()) == {'source.png'}
        assert client.post('/api/dataset/artifacts/ingest', headers=header(project), json={
            'source_name': '../outside', 'sha256': 'a' * 64}).status_code == 422
    finally:
        source.chmod(0o755); path.chmod(0o644)


def test_duplicate_staging_reservations_cannot_bypass_quota(tmp_path):
    store, ctx = store_fixture(tmp_path, quota_bytes=8)
    store.begin(ctx, 'source', sha(b'abcdefgh'), 8)
    with pytest.raises(Exception, match='quota'):
        store.begin(ctx, 'source', sha(b'abcdefgh'), 8)


def test_commit_failure_retains_verified_recovery_and_never_deletes_shared_bytes(tmp_path, monkeypatch):
    import sqlite3
    store, first = store_fixture(tmp_path, grace_seconds=0)
    second = first.model_copy(update={'project_id': 'second-project'})
    store.registry.register_project({'id': second.project_id, 'project_dir': str(tmp_path / 'second')})
    data = b'shared committed content'
    ref = store.put_verified(first, io.BytesIO(data), sha(data), len(data))
    upload = store.begin(second, 'source', sha(data), len(data))
    store.append(second, upload['id'], 0, data)
    original = store.registry.register_managed_artifact
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise sqlite3.OperationalError('fixture commit failure')
    monkeypatch.setattr(store.registry, 'register_managed_artifact', fail)
    with pytest.raises(sqlite3.OperationalError):
        store.complete(second, upload['id'])
    assert store.status(second, upload['id'])['state'] == 'verified'
    with store.registry.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM artifacts WHERE project_id=?', (store.registry.project_key(second),)).fetchone()[0] == 0
    store.cancel(second, upload['id'])
    assert store.collect_garbage()['objects_deleted'] == 0
    with store.open(first, ref) as handle:
        assert handle.read() == data
    monkeypatch.setattr(store.registry, 'register_managed_artifact', original)
    recovered = store.put_verified(second, io.BytesIO(data), sha(data), len(data))
    assert recovered.id != ref.id


def test_chunk_recovery_discards_only_uncommitted_tail(tmp_path):
    store, ctx = store_fixture(tmp_path)
    data = b'abcdef'
    upload = store.begin(ctx, 'source', sha(data), len(data))
    store.append(ctx, upload['id'], 0, data[:3])
    with store._stage(upload['id']).open('ab') as handle:
        handle.write(b'crash tail')
    store.append(ctx, upload['id'], 3, data[3:])
    ref = store.complete(ctx, upload['id'])
    with store.open(ctx, ref) as handle:
        assert handle.read() == data


class FakeS3:
    """In-memory object transport only; no credentials or real network calls."""
    def __init__(self):
        self.objects = {}; self.corrupt = False; self.calls = []

    def put_object(self, **kwargs):
        self.calls.append('put'); assert kwargs['IfNoneMatch'] == '*'
        key = kwargs['Key']
        if key in self.objects:
            error = RuntimeError('already exists'); error.response = {'Error': {'Code': 'PreconditionFailed'}}
            raise error
        self.objects[key] = kwargs['Body'].read()

    def get_object(self, **kwargs):
        self.calls.append('get')
        return {'Body': io.BytesIO(b'corrupt' if self.corrupt else self.objects[kwargs['Key']])}

    def list_objects_v2(self, **kwargs):
        from datetime import datetime, timezone
        return {'Contents': [{'Key': key, 'LastModified': datetime.fromtimestamp(0, timezone.utc)}
                             for key in self.objects if key.startswith(kwargs['Prefix'])]}

    def delete_object(self, **kwargs):
        self.calls.append('delete'); self.objects.pop(kwargs['Key'], None)


def test_s3_transport_readback_failure_cannot_publish_and_scoped_bytes_work(tmp_path):
    from backend.engine.artifact_store import S3ObjectAdapter
    transport = FakeS3(); adapter = S3ObjectAdapter(transport, 'fixture-bucket', 'owned/workspace')
    store, ctx = store_fixture(tmp_path, adapter=adapter, grace_seconds=0)
    data = b's3 fixture data'; transport.corrupt = True
    with pytest.raises(Exception, match='hash'):
        store.put_verified(ctx, io.BytesIO(data), sha(data), len(data))
    with store.registry.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM managed_artifacts').fetchone()[0] == 0
    transport.corrupt = False
    ref = store.put_verified(ctx, io.BytesIO(data), sha(data), len(data))
    with store.open(ctx, ref) as handle:
        assert handle.read() == data
    assert transport.calls.count('get') >= 3
    transport.objects['unowned/key'] = b'preserve'
    store.release(ctx, ref); store.collect_garbage()
    assert transport.objects == {'unowned/key': b'preserve'}


def test_transfer_client_resumes_actual_api_and_downloads_verified_atomic_copy(tmp_path):
    from backend.remote.transfer import ArtifactTransfer
    _, client, (project, _) = local(tmp_path)
    data = b'transfer production protocol fixture'
    started = begin(client, project, data).json()['upload']
    assert client.put(f'/api/artifacts/uploads/{started["id"]}', params={'offset': 0},
                      headers={**header(project), 'Content-Type': 'application/octet-stream'}, content=data[:5]).status_code == 200
    transfer = ArtifactTransfer(client, headers=header(project), chunk_bytes=7)
    ref = transfer.upload(io.BytesIO(data), sha(data), len(data), upload_id=started['id'])
    destination = tmp_path / 'download.bin'
    transfer.download(ref, destination)
    assert destination.read_bytes() == data
    partial = tmp_path / 'resume.part'; partial.write_bytes(data[:4])
    transfer.download(ref, destination, partial=partial)
    assert destination.read_bytes() == data and not partial.exists()
    assert not list(tmp_path.glob('.artifact-download-*'))


def test_team_live_permission_revocation_and_upload_actor_binding(tmp_path, monkeypatch):
    _, _, (project, second) = local(tmp_path)
    app = create_app(project_dir=str(tmp_path / 'team'), shared_auth_dir=str(tmp_path / 'auth'))
    accounts = app.state.accounts
    owner = accounts.bootstrap('owner', 'fixture password 123')
    labeler = accounts.create_user('labeler', 'fixture password 456')
    viewer = accounts.create_user('viewer', 'fixture password 789')
    for row in (project, second):
        accounts.register_project(row['id'], row['project_dir'], owner['id'])
        accounts.set_membership(row['id'], labeler['id'], 'labeler', owner['id'])
        accounts.set_membership(row['id'], viewer['id'], 'viewer', owner['id'])
    tokens = {name: accounts.login(name, 'fixture password ' + password)['token']
              for name, password in [('owner', '123'), ('labeler', '456'), ('viewer', '789')]}
    clients = {name: TestClient(app, headers={'Authorization': 'Bearer ' + token}) for name, token in tokens.items()}
    assert begin(clients['viewer'], project, b'a').status_code == 403
    upload = begin(clients['labeler'], project, b'a').json()['upload']
    endpoint = '/api/artifacts/uploads/' + upload['id']
    assert clients['owner'].get(endpoint, headers=header(project)).status_code == 404
    assert clients['labeler'].get(endpoint, headers=header(second)).status_code == 404
    assert clients['labeler'].put(endpoint, params={'offset': 0}, content=b'a',
                                  headers={**header(project), 'Content-Type': 'application/octet-stream'}).status_code == 200
    accounts.set_membership(project['id'], labeler['id'], 'viewer', owner['id'])
    assert clients['labeler'].post(endpoint + '/complete', headers=header(project)).status_code == 403
    accounts.set_membership(project['id'], labeler['id'], 'labeler', owner['id'])
    adapter = app.state.artifact_store.adapter
    publish = adapter.publish
    def revoked_during_publication(*args):
        publish(*args)
        accounts.set_membership(project['id'], labeler['id'], 'viewer', owner['id'])
    monkeypatch.setattr(adapter, 'publish', revoked_during_publication)
    assert clients['labeler'].post(endpoint + '/complete', headers=header(project)).status_code == 403
    assert clients['labeler'].get(endpoint, headers=header(project)).json()['upload']['state'] == 'verified'
    monkeypatch.setattr(adapter, 'publish', publish)
    accounts.set_membership(project['id'], labeler['id'], 'labeler', owner['id'])
    completed = clients['labeler'].post(endpoint + '/complete', headers=header(project))
    assert completed.status_code == 200, completed.text
    ref = completed.json()['artifact_ref']
    assert content(clients['viewer'], project, ref).content == b'a'
    pinpath = '/api/artifacts/' + ref['id'] + '/pins'
    assert clients['viewer'].post(pinpath, headers=header(project), json={'artifact_ref': ref, 'name': 'backup'}).status_code == 403
    assert clients['owner'].post(pinpath, headers=header(project), json={'artifact_ref': ref, 'name': 'backup'}).status_code == 200
    with accounts._db() as db:
        db.execute('DELETE FROM members WHERE user_id=? AND project_id=?', (viewer['id'], project['id']))
    assert content(clients['viewer'], project, ref).status_code == 403
    accounts.logout(tokens['labeler'])
    assert clients['labeler'].get(endpoint, headers=header(project)).status_code == 401
    assert content(clients['labeler'], project, ref).status_code == 401


def test_filesystem_adapter_keeps_metadata_local_and_never_follows_symlinks(tmp_path):
    from backend.engine.artifact_store import FileObjectAdapter
    object_root = tmp_path / 'server-file-root' / 'owned-objects'
    store, ctx = store_fixture(tmp_path, adapter=FileObjectAdapter(object_root))
    data = b'NAS adapter fixture, local filesystem only'
    ref = store.put_verified(ctx, io.BytesIO(data), sha(data), len(data))
    assert store.registry.path.parent == tmp_path / 'metadata'
    assert list(object_root.rglob(ref.sha256))
    assert not list(object_root.rglob('*.sqlite*'))
    outside = tmp_path / 'outside'; outside.mkdir()
    link = tmp_path / 'linked'; link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(Exception, match='symbolic'):
        FileObjectAdapter(link / 'objects')
    assert list(outside.iterdir()) == []


def test_actual_api_corrupt_content_never_returns_unverified_bytes(tmp_path):
    app, client, (project, _) = local(tmp_path)
    ref = upload(client, project, b'valid bytes')
    store = app.state.artifact_store
    store.adapter._path(ref['sha256']).write_bytes(b'changed bytes')
    response = content(client, project, ref)
    assert response.status_code == 409, response.text
    assert b'changed bytes' not in response.content


def test_actual_api_pin_release_and_gc_preserve_other_project_reference(tmp_path):
    app, client, (first, second) = local(tmp_path)
    data = b'same API bytes'
    a = upload(client, first, data); b = upload(client, second, data)
    assert a['id'] != b['id']
    store = app.state.artifact_store; store.grace_seconds = 0
    pin = client.post('/api/artifacts/' + a['id'] + '/pins', headers=header(first),
                      json={'artifact_ref': a, 'name': 'backup'}).json()['pin']
    assert client.request('DELETE', '/api/artifacts/' + a['id'], headers=header(first), json=a).status_code == 200
    assert client.request('DELETE', '/api/artifacts/' + b['id'], headers=header(first), json=b).status_code == 404
    assert client.post('/api/artifacts/gc', headers=header(first)).json()['collection']['objects_deleted'] == 0
    assert content(client, second, b).content == data
    assert client.request('DELETE', '/api/artifacts/' + b['id'], headers=header(second), json=b).status_code == 200
    assert client.post('/api/artifacts/gc', headers=header(first)).json()['collection']['objects_deleted'] == 0
    assert client.delete('/api/artifacts/pins/' + pin['id'], headers=header(second)).status_code == 404
    assert client.delete('/api/artifacts/pins/' + pin['id'], headers=header(first)).status_code == 200
    assert client.post('/api/artifacts/gc', headers=header(first)).json()['collection']['objects_deleted'] == 1


def test_commit_failure_after_object_publication_leaves_safe_orphan_for_grace(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import sqlite3
    store, ctx = store_fixture(tmp_path, grace_seconds=60)
    data = b'orphan candidate'
    upload = store.begin(ctx, 'source', sha(data), len(data)); store.append(ctx, upload['id'], 0, data)
    transaction = store.registry.transaction
    @contextmanager
    def failure():
        with transaction() as db:
            yield db
            raise sqlite3.OperationalError('fixture DB commit unavailable')
    monkeypatch.setattr(store.registry, 'transaction', failure)
    with pytest.raises(sqlite3.OperationalError):
        store.complete(ctx, upload['id'])
    monkeypatch.setattr(store.registry, 'transaction', transaction)
    assert store.status(ctx, upload['id'])['state'] == 'staging'
    with transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM managed_artifacts').fetchone()[0] == 0
    store.cancel(ctx, upload['id'])
    assert store.collect_garbage()['objects_deleted'] == 0
    store.clock = lambda: __import__('time').time() + 61
    assert store.collect_garbage()['objects_deleted'] == 1


def test_gc_cleans_only_expired_owned_staging_and_preserves_active_uploads(tmp_path):
    import os
    import time
    store, ctx = store_fixture(tmp_path, grace_seconds=60)
    active = store.begin(ctx, 'source', sha(b'a'), 1)
    store.append(ctx, active['id'], 0, b'a')
    orphan = store.staging / ('f' * 32); orphan.write_bytes(b'uncommitted orphan')
    unrelated = store.staging / 'keep.txt'; unrelated.write_bytes(b'unrelated')
    old = time.time() - 120
    os.utime(orphan, (old, old)); os.utime(store._stage(active['id']), (old, old))
    result = store.collect_garbage()
    assert result['staging_deleted'] == 1
    assert not orphan.exists() and unrelated.exists() and store._stage(active['id']).exists()


def test_ingest_descriptor_survives_posix_swap_or_windows_delete_guard(tmp_path, monkeypatch):
    import os
    from backend.api import routes_artifacts
    _, client, (project, _) = local(tmp_path)
    source = tmp_path / 'source'; source.mkdir()
    original = b'authorized original'; outside = tmp_path / 'outside-secret'; outside.write_bytes(b'outside')
    path = source / 'fixture.png'; path.write_bytes(original)
    assert client.put('/api/project/update', headers=header(project), json={'source_dataset_dir': str(source)}).status_code == 200
    get_store = routes_artifacts.get_artifact_store
    replacement = []
    def swapped(request):
        try:
            path.unlink()
        except PermissionError as exc:
            # The ordinary Windows source handle denies delete sharing. This is
            # a blocked replacement, not evidence that a path swap succeeded.
            assert os.name == 'nt' and exc.winerror in (5, 32)
            replacement.append('blocked')
            assert path.read_bytes() == original
        else:
            path.symlink_to(outside)
            replacement.append('swapped')
        return get_store(request)
    monkeypatch.setattr(routes_artifacts, 'get_artifact_store', swapped)
    response = client.post('/api/dataset/artifacts/ingest', headers=header(project),
                           json={'source_name': path.name, 'sha256': sha(original)})
    assert response.status_code == 200, response.text
    monkeypatch.setattr(routes_artifacts, 'get_artifact_store', get_store)
    assert content(client, project, response.json()['artifact_ref']).content == original
    assert outside.read_bytes() == b'outside'
    assert replacement == ['blocked' if os.name == 'nt' else 'swapped']
    # The route must release its source handle, including on Windows.
    path.unlink()


def test_ingest_denied_replacement_keeps_descriptor_without_reopening_source(tmp_path, monkeypatch):
    import os
    from backend.api import routes_artifacts
    _, client, (project, _) = local(tmp_path)
    source = tmp_path / 'source'; source.mkdir()
    original = b'authorized original'
    path = source / 'fixture.png'; path.write_bytes(original)
    outside = tmp_path / 'outside-secret'; outside.write_bytes(b'outside')
    assert client.put('/api/project/update', headers=header(project), json={'source_dataset_dir': str(source)}).status_code == 200
    get_store = routes_artifacts.get_artifact_store
    path_open = Path.open
    os_open = os.open
    path_unlink = Path.unlink
    attempts = []
    def denied(request):
        # Model an external actor whose replacement is denied. Keep production
        # descriptor acquisition, verified copying and publication real.
        def reject_replacement(self, *args, **kwargs):
            if self == path:
                attempts.append('denied')
                raise PermissionError('Source descriptor prevents replacement')
            return path_unlink(self, *args, **kwargs)
        with monkeypatch.context() as replacement:
            replacement.setattr(Path, 'unlink', reject_replacement)
            with pytest.raises(PermissionError):
                path.unlink()
        def no_source_reopen(self, *args, **kwargs):
            assert self not in (path, outside), 'Copy must consume the held descriptor'
            return path_open(self, *args, **kwargs)
        def no_source_descriptor_reopen(name, *args, **kwargs):
            assert Path(name) not in (path, outside), 'Copy must consume the held descriptor'
            return os_open(name, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', no_source_reopen)
        monkeypatch.setattr(os, 'open', no_source_descriptor_reopen)
        return get_store(request)
    monkeypatch.setattr(routes_artifacts, 'get_artifact_store', denied)
    response = client.post('/api/dataset/artifacts/ingest', headers=header(project),
                           json={'source_name': path.name, 'sha256': sha(original)})
    monkeypatch.setattr(Path, 'open', path_open)
    monkeypatch.setattr(os, 'open', os_open)
    monkeypatch.setattr(routes_artifacts, 'get_artifact_store', get_store)
    assert response.status_code == 200, response.text
    assert attempts == ['denied']
    assert content(client, project, response.json()['artifact_ref']).content == original
    assert path.read_bytes() == original and outside.read_bytes() == b'outside'


def test_same_logical_project_restored_copy_cannot_resolve_original_managed_ref(tmp_path):
    import json
    import shutil
    _, client, (project, _) = local(tmp_path)
    first_context = client.get('/api/context', headers=header(project)).json()['project_context']
    ref = upload(client, project, b'original project bytes')
    restored = tmp_path / 'restored'
    shutil.copytree(project['project_dir'], restored)
    opened = client.post('/api/project/open', json={'project_dir': str(restored)})
    assert opened.status_code == 200, opened.text
    second_context = client.get('/api/context').json()['project_context']
    assert first_context['project_id'] == second_context['project_id']
    assert first_context['workspace_id'] != second_context['workspace_id']
    path = f'/api/artifacts/{ref["id"]}/content'
    params = {'revision': ref['revision'], 'sha256': ref['sha256']}
    assert client.get(path, params=params, headers={'X-Vision-Context': json.dumps(first_context)}).content == b'original project bytes'
    assert client.get(path, params=params, headers={'X-Vision-Context': json.dumps(second_context)}).status_code == 404


def test_s3_config_reports_missing_optional_sdk_without_network(tmp_path, monkeypatch):
    import builtins
    from backend.engine.artifact_store import ArtifactStore
    registry = ContextRegistry(tmp_path / 'registry')
    original = builtins.__import__
    def no_sdk(name, *args, **kwargs):
        if name == 'boto3':
            raise ImportError('fixture optional SDK absent')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_sdk)
    monkeypatch.setenv('VISION_ARTIFACT_S3_BUCKET', 'fixture-bucket')
    with pytest.raises(Exception, match='optional boto3 SDK'):
        ArtifactStore.configured(registry)


def test_expired_interrupted_upload_releases_reservation_after_retention(tmp_path):
    now = [1000.0]
    store, ctx = store_fixture(tmp_path, quota_bytes=8, grace_seconds=60, upload_ttl=120, clock=lambda: now[0])
    upload = store.begin(ctx, 'source', sha(b'abcdefgh'), 8)
    store.append(ctx, upload['id'], 0, b'abc')
    now[0] += 121
    assert store.collect_garbage()['uploads_expired'] == 1
    assert store.status(ctx, upload['id'])['state'] == 'expired'
    assert store.begin(ctx, 'source', sha(b'new'), 3)['offset'] == 0


def test_fs_crash_publication_temporary_is_collected_after_grace(tmp_path):
    import os
    import time
    store, _ = store_fixture(tmp_path, grace_seconds=60)
    directory = store.adapter.root / 'ab'; directory.mkdir()
    temporary = directory / '.publish-fixture'; temporary.write_bytes(b'crash during publish')
    untouched = directory / 'readme'; untouched.write_bytes(b'keep')
    old = time.time() - 120; os.utime(temporary, (old, old))
    assert store.collect_garbage()['publication_temps_deleted'] == 1
    assert not temporary.exists() and untouched.exists()


def test_fake_s3_actual_api_reports_storage_failure_without_transport_details(tmp_path):
    from backend.engine.artifact_store import ArtifactStore, S3ObjectAdapter
    app, client, (project, _) = local(tmp_path)
    transport = FakeS3()
    app.state.artifact_store = ArtifactStore(app.state.context_registry,
        adapter=S3ObjectAdapter(transport, 'fixture-bucket', 'owned/fixture'))
    ref = upload(client, project, b'API through fake S3')
    assert content(client, project, ref).content == b'API through fake S3'
    def unavailable(**kwargs):
        raise RuntimeError('fixture transport internal credential details')
    transport.get_object = unavailable
    response = content(client, project, ref)
    assert response.status_code == 503, response.text
    assert 'credential' not in response.text


def test_invalid_download_reference_and_range_fail_as_client_errors(tmp_path):
    _, client, (project, _) = local(tmp_path)
    response = client.get('/api/artifacts/invalid!/content', headers=header(project), params={'revision': 1, 'sha256': 'a' * 64})
    assert response.status_code == 422
    ref = upload(client, project, b'range fixture')
    assert content(client, project, ref, Range='bytes=' + '9' * 5000 + '-').status_code == 416


def test_complete_partial_after_fsync_resumes_with_live_server_verification(tmp_path, monkeypatch):
    import os
    from backend.remote.transfer import ArtifactTransfer
    _, client, (project, _) = local(tmp_path)
    data = b'complete retained partial bytes'
    ref = upload(client, project, data)
    partial = tmp_path / 'download.part'; partial.write_bytes(data[:3])
    destination = tmp_path / 'download.bin'; destination.write_bytes(b'existing destination')
    transfer = ArtifactTransfer(client, headers=header(project))
    replace = os.replace
    def crash(source, target):
        if Path(source) == partial:
            raise OSError('fixture crash after fsync before replace')
        return replace(source, target)
    monkeypatch.setattr(os, 'replace', crash)
    with pytest.raises(OSError, match='fixture crash'):
        transfer.download(ref, destination, partial=partial)
    assert partial.read_bytes() == data and destination.read_bytes() == b'existing destination'
    monkeypatch.setattr(os, 'replace', replace)
    transfer.download(ref, destination, partial=partial)
    assert destination.read_bytes() == data and not partial.exists()


@pytest.mark.parametrize('denial', ['session', 'membership', 'revision', 'hash'])
def test_complete_partial_never_bypasses_current_authorization_or_reference(tmp_path, denial):
    import httpx
    from backend.remote.transfer import ArtifactTransfer
    _, _, (project, _) = local(tmp_path)
    app = create_app(project_dir=str(tmp_path / 'team'), shared_auth_dir=str(tmp_path / 'auth'))
    accounts = app.state.accounts
    owner = accounts.bootstrap('owner', 'fixture password 123')
    user = accounts.create_user('writer', 'fixture password 456')
    accounts.register_project(project['id'], project['project_dir'], owner['id'])
    accounts.set_membership(project['id'], user['id'], 'trainer', owner['id'])
    token = accounts.login('writer', 'fixture password 456')['token']
    client = TestClient(app, headers={'Authorization': 'Bearer ' + token})
    data = b'already complete local partial'
    ref = upload(client, project, data)
    partial = tmp_path / 'retained.part'; partial.write_bytes(data)
    destination = tmp_path / 'destination.bin'; destination.write_bytes(b'preserve destination')
    if denial == 'session':
        accounts.logout(token)
    elif denial == 'membership':
        with accounts._db() as db:
            db.execute('DELETE FROM members WHERE project_id=? AND user_id=?', (project['id'], user['id']))
    elif denial == 'revision':
        ref['revision'] += 1
    else:
        ref['sha256'] = 'f' * 64
    with pytest.raises(httpx.HTTPStatusError) as error:
        ArtifactTransfer(client, headers=header(project)).download(ref, destination, partial=partial)
    assert error.value.response.status_code == {'session': 401, 'membership': 403, 'revision': 409, 'hash': 409}[denial]
    assert partial.read_bytes() == data and destination.read_bytes() == b'preserve destination'
