"""Real original ASGI reads of imported and internal project PNG inputs.

No auth, route, image decoder, worker, owner or response implementation is mocked.
The distinct imported source and internal dataset are ordinary fixture data.
Evidence is saved before the desired ownership assertion, including causal RED.
"""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path

import httpx
from PIL import Image
import pytest


def _pin(path):
    before = path.lstat()
    data = path.read_bytes()
    after = path.lstat()
    raw = lambda value: [value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
                         value.st_size, value.st_mtime_ns, value.st_ctime_ns,
                         value.st_uid, value.st_gid]
    assert raw(before) == raw(after)
    return {'raw9': raw(after), 'sha256': hashlib.sha256(data).hexdigest()}


def _fixture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / 'thumbnail-cache'
    cache.mkdir()
    monkeypatch.setenv('MODU_THUMBNAIL_CACHE_DIR', str(cache))
    from backend.api import routes_dataset
    from backend.tests.test_original_owned_flow_lifetime import _stack
    monkeypatch.setattr(routes_dataset, 'THUMBNAIL_CACHE_DIR', cache)
    app, admission, observer, actor, project, graph, seed, recipe, audit = _stack(tmp_path, monkeypatch)
    imported = tmp_path / 'imported-source'
    imported.mkdir()
    internal = Path(project['dataset_dir'])
    assert imported.resolve() != internal.resolve()
    project['source_dataset_dir'] = str(imported.resolve())
    root = Path(project['project_dir'])
    from backend.api import routes_flowchart as flows
    flows._write_json(root / 'project.json', project)
    images = {}
    for name, directory in [('source', imported), ('internal', internal)]:
        image = directory / 'retained-required-input.png'
        with Image.new('RGB', (8, 6), (30, 70, 90)) as pixels:
            pixels.save(image)
        images[name] = image
    protected = [*images.values(), root / 'project.json', recipe,
                 flows._active_flow_file(root), flows._version_dir(root) / (seed + '.json')]
    return app, admission, observer, project, images, protected, audit


@pytest.mark.parametrize(('location', 'kind'), [
    ('source', 'image-info'), ('internal', 'image-info'),
    ('source', 'thumbnail-name'), ('internal', 'thumbnail-name'),
    ('source', 'thumbnail-preview'), ('internal', 'thumbnail-preview'),
])
def test_original_current_project_image_reads_close(location, kind, tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _login_current, _no_whole_credit
    app, admission, observer, project, images, protected, audit = _fixture(tmp_path, monkeypatch)
    image = images[location]
    basis = {str(path): _pin(path) for path in protected}
    expected_sha = basis[str(image)]['sha256']

    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if kind == 'image-info':
                route = '/api/flow-workspace/image-info'
                response = await client.get(route, headers=headers, params={'image_path': str(image)})
                assert response.status_code == 200
                assert response.json() == {'width': 8, 'height': 6, 'sha256': expected_sha}
            else:
                route = '/api/dataset/thumbnail/' + (image.name if kind == 'thumbnail-name' else 'preview')
                response = await client.get(route, headers=headers, params={'file_path': str(image), 'size': 128})
                assert response.status_code == 200
                assert response.headers['content-type'] == 'image/jpeg'
                assert int(response.headers['content-length']) == len(response.content) > 0
                with Image.open(io.BytesIO(response.content)) as decoded:
                    assert decoded.size == (8, 6)
                    decoded.load()
            assert response.headers['x-vision-context'] == headers['x-vision-context']
            return route, len(response.content), hashlib.sha256(response.content).hexdigest()

    route, size, body_sha = asyncio.run(original_request())
    assert {str(path): _pin(path) for path in protected} == basis
    assert audit.verify() == {'intact': True, 'rows': 0}
    snapshot = observer.snapshot()
    assert len(snapshot['records']) == 3
    assert snapshot['records'][-1]['ingress']['path'] == route
    assert all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in snapshot['records'])
    admission.close()
    drain = admission.drain(.03)
    proof = {'case': location + '-' + kind, 'HTTP_status': 200, 'body_bytes': size,
             'body_sha256': body_sha, 'protected_unchanged': True,
             'original_reasons': list(observer._failures), 'drain': drain,
             'records': [{key: row.get(key) for key in ('identity', 'ingress', 'response_status',
                         'released', 'normal_return', 'completed', 'terminal_forwarded')}
                         for row in snapshot['records']],
             'native_causal_equivalence_or_acceptance': False}
    output = os.environ.get('MODU_IMAGE_REPRO_EVIDENCE_DIR')
    if output:
        file = Path(output) / (proof['case'] + '.json')
        with file.open('x') as stream:
            json.dump(proof, stream, indent=2)
            stream.write('\n')
    # Genuine HTTP, body, context, closed original records and immutable inputs
    # are established before the causal ownership assertion.
    assert drain == {'status': 'managed_scopes_drained', 'active_scopes': 0, 'unsupported': []}
    assert observer.snapshot()['all_settled']
    assert observer._failures == []
    _no_whole_credit(observer)


def test_transient_original_open_must_join_actual_descriptor(tmp_path, monkeypatch):
    """Negative IO supplier opens real foreign bytes then restores the pathname.

    Only this explicit negative provider is substituted before original factory
    binding. PIL, handler, original ownership code and actual ASGI stay intact.
    The returned handle really owns the temporarily selected foreign inode;
    the subsequent passive pathname check sees the restored owned inode.
    """
    import builtins
    from backend.tests.test_original_owned_flow_lifetime import _login_current, _no_whole_credit
    original_open = builtins.open
    selected = {}
    observed = []

    def transient_open(file, *args, **kwargs):
        if (selected and not observed and isinstance(file, (str, bytes, os.PathLike))
                and os.fsdecode(file) == str(selected['owned'])):
            owned, foreign, aside = (selected[key] for key in ('owned', 'foreign', 'aside'))
            owned.rename(aside)
            foreign.rename(owned)
            try:
                handle = original_open(file, *args, **kwargs)
            finally:
                owned.rename(foreign)
                aside.rename(owned)
            value = os.fstat(handle.fileno())
            observed.append({'actual_inode': value.st_ino, 'restored_inode': owned.lstat().st_ino})
            return handle
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', transient_open)
    app, admission, observer, project, images, protected, audit = _fixture(tmp_path, monkeypatch)
    image = images['internal']
    foreign = tmp_path / 'actual-foreign-open.png'
    with Image.new('RGB', (12, 7), (110, 140, 170)) as pixels:
        pixels.save(foreign)
    owned_bytes, foreign_bytes = image.read_bytes(), foreign.read_bytes()
    basis = {str(path): _pin(path) for path in protected if path != image}
    selected.update(owned=image, foreign=foreign, aside=image.with_name('temporarily-owned.png'))

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/flow-workspace/image-info', headers=headers,
                                        params={'image_path': str(image)})
            assert response.status_code == 200
            assert response.json() == {'width': 12, 'height': 7,
                                       'sha256': hashlib.sha256(owned_bytes).hexdigest()}
            assert response.headers['x-vision-context'] == headers['x-vision-context']
    asyncio.run(request())
    assert len(observed) == 1 and observed[0]['actual_inode'] != observed[0]['restored_inode']
    assert image.read_bytes() == owned_bytes and foreign.read_bytes() == foreign_bytes
    assert {str(path): _pin(path) for path in protected if path != image} == basis
    assert audit.verify() == {'intact': True, 'rows': 0}
    snapshot = observer.snapshot()
    assert len(snapshot['records']) == 3
    assert all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in snapshot['records'])
    admission.close()
    drain = admission.drain(.03)
    output = os.environ.get('MODU_IMAGE_REPRO_EVIDENCE_DIR')
    if output:
        with (Path(output) / 'transient-original-open.json').open('x') as stream:
            json.dump({'HTTP_status': 200, 'actual_foreign_dimensions': [12, 7],
                       'actual_descriptor_differs_from_restored_path': True,
                       'file_bytes_restored': True, 'rename_changes_inode_ctime': True,
                       'original_reasons': list(observer._failures), 'drain': drain,
                       'native_causal_equivalence_or_acceptance': False}, stream, indent=2)
            stream.write('\n')
    assert drain['status'] == 'refused'
    assert 'uncovered_request_protocol' in drain['unsupported']
    assert observer.snapshot()['uncertain']
    _no_whole_credit(observer)


@pytest.mark.parametrize('case', [
    'foreign-path', 'linked-foreign', 'linked-owned', 'preview-linked-owned',
    'thumbnail-arbitrary-name', 'input-body-mutation', 'foreign-context', 'late-stat-provider',
])
def test_unknown_internal_image_claim_does_not_gain_ownership(case, tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _login_current, _no_whole_credit
    app, admission, observer, project, images, protected, audit = _fixture(tmp_path, monkeypatch)
    image = images['internal']
    foreign = tmp_path / 'foreign-project.png'
    with Image.new('RGB', (8, 6), (60, 80, 100)) as pixels:
        pixels.save(foreign)
    target = foreign if case == 'foreign-path' else image
    if case in {'linked-foreign', 'linked-owned', 'preview-linked-owned'}:
        target = image.with_name('alias.png')
        target.symlink_to(foreign if case == 'linked-foreign' else image)
    basis = {str(path): _pin(path) for path in [*protected, foreign]}
    body_sha = basis[str(image)]['sha256']
    mutations = []

    async def transport(scope, receive, send):
        async def original_send(message):
            if (case == 'input-body-mutation' and scope.get('path') == '/api/flow-workspace/image-info'
                    and message['type'] == 'http.response.start'):
                # Change actual input bytes only after the original handler's
                # closed reads/returned hash, before its original HTTP finish.
                with Image.new('RGB', (8, 6), (90, 120, 150)) as pixels:
                    pixels.save(image)
                mutations.append(_pin(image))
            await send(message)
        await app(scope, receive, original_send)

    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=transport), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if case == 'foreign-context':
                context = json.loads(headers['x-vision-context'])
                context['actor_id'] = 'foreign-actor'
                headers['x-vision-context'] = json.dumps(context)
            if case == 'late-stat-provider':
                original_stat = Path.stat
                def delegated_stat(self, *args, **kwargs):
                    return original_stat(self, *args, **kwargs)
                monkeypatch.setattr(Path, 'stat', delegated_stat)
            if case in {'thumbnail-arbitrary-name', 'preview-linked-owned'}:
                name = 'arbitrary-not-the-image' if case == 'thumbnail-arbitrary-name' else 'preview'
                response = await client.get('/api/dataset/thumbnail/' + name, headers=headers,
                                            params={'file_path': str(target), 'size': 128})
            else:
                response = await client.get('/api/flow-workspace/image-info', headers=headers,
                                            params={'image_path': str(target)})
            if case in {'foreign-path', 'linked-foreign'}:
                assert response.status_code == 403
                assert response.json() == {'detail': 'Request data is outside this project or invalid'}
            elif case == 'foreign-context':
                assert response.status_code == 403
                assert response.json() == {'detail': 'Explicit context does not match the authenticated account workspace'}
            elif case in {'thumbnail-arbitrary-name', 'preview-linked-owned'}:
                assert response.status_code == 200
                assert response.headers['content-type'] == 'image/jpeg'
                assert int(response.headers['content-length']) == len(response.content) > 0
            else:
                assert response.status_code == 200
                assert response.json() == {'width': 8, 'height': 6, 'sha256': body_sha}
            return response.status_code

    status = asyncio.run(original_request())
    if case == 'input-body-mutation':
        assert len(mutations) == 1 and mutations[0] != basis[str(image)]
    for file in [*protected, foreign]:
        if case != 'input-body-mutation' or file != image:
            assert _pin(file) == basis[str(file)]
    assert audit.verify() == {'intact': True, 'rows': 0}
    snapshot = observer.snapshot()
    assert len(snapshot['records']) == 3
    assert all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in snapshot['records'])
    admission.close()
    drain = admission.drain(.03)
    output = os.environ.get('MODU_IMAGE_REPRO_EVIDENCE_DIR')
    if output:
        with (Path(output) / ('negative-' + case + '.json')).open('x') as stream:
            json.dump({'case': case, 'HTTP_status': status, 'drain': drain,
                       'original_reasons': list(observer._failures),
                       'mutation_count': len(mutations),
                       'native_causal_equivalence_or_acceptance': False}, stream, indent=2)
            stream.write('\n')
    assert drain['status'] == 'refused'
    assert 'uncovered_request_protocol' in drain['unsupported']
    assert observer.snapshot()['uncertain']
    _no_whole_credit(observer)
