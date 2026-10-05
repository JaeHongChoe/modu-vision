"""S2-07: searching a validated revision and resolving saved selections by identity, never by path alone."""
import pytest
from PIL import Image


@pytest.fixture
def built(tmp_path):
    from backend.engine.dataset_index import DatasetIndex, index_path
    index = DatasetIndex(index_path(tmp_path / 'registry'))
    source = tmp_path / '원본'
    for split in ('train', 'test'):
        for label, color in (('ok', 'white'), ('ng', 'black')):
            for number in range(5):
                path = source / split / label / f'검사_{split}_{label}_{number}.png'
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGB', (8, 8), (number * 40, 10 if label == 'ok' else 200, 30 if split == 'train' else 90)).save(path)
    (source / 'train' / 'ng' / '100%_odd_name.png').write_bytes(b'not an image')
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'exclude')
    return index, source, receipt, tmp_path


def _query(built, **options):
    from backend.engine.image_query import query_images
    index, _source, receipt, _tmp = built
    return query_images(index.path, 'ns:a', receipt.revision_id, **options)


def test_search_and_index_filters_page_by_relative_path(built):
    page = _query(built, query='검사_test', filters={'label': 'ng'}, limit=50)
    assert [item['relative_path'] for item in page['items']] == [f'test/ng/검사_test_ng_{n}.png' for n in range(5)]
    assert page['next_cursor'] is None and page['items'][0]['file_name'] == '검사_test_ng_0.png'
    invalid = _query(built, filters={'state': 'invalid'})
    assert [item['relative_path'] for item in invalid['items']] == ['train/ng/100%_odd_name.png']
    assert invalid['items'][0]['error_code'] == 'UNIDENTIFIED_IMAGE' and invalid['items'][0]['valid'] is False
    assert [item['relative_path'] for item in _query(built, query='100%')['items']] == ['train/ng/100%_odd_name.png']
    assert _query(built, query='_odd')['items'][0]['relative_path'].endswith('100%_odd_name.png')
    assert _query(built, query='0%x')['items'] == [], '% and _ are literal characters, not wildcards'
    assert len(_query(built, filters={'split': 'test'}, limit=200)['items']) == 10


def test_ledger_filters_with_bounded_scans_continue_through_cursors(built):
    ledger = {f'train/ok/검사_train_ok_{n}.png': {'tags': ['scratch'], 'lot': 'L1', 'workflow_state': 'approved'} for n in (1, 3)}
    ledger['test/ng/검사_test_ng_4.png'] = {'tags': ['scratch'], 'lot': 'L2', 'workflow_state': 'needs_review'}
    found, cursor, pages = [], None, 0
    while True:
        page = _query(built, ledger=ledger, filters={'tag': 'scratch'}, cursor=cursor, limit=10, scan_limit=4)
        pages += 1
        assert page['scanned'] <= 4, 'a page never reads more than its scan budget'
        found += [item['relative_path'] for item in page['items']]
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert found == ['test/ng/검사_test_ng_4.png', 'train/ok/검사_train_ok_1.png', 'train/ok/검사_train_ok_3.png'] and pages > 3
    approved = _query(built, ledger=ledger, filters={'workflow_state': 'approved', 'lot': 'L1'}, limit=50)
    assert [item['relative_path'] for item in approved['items']] == ['train/ok/검사_train_ok_1.png', 'train/ok/검사_train_ok_3.png']
    assert approved['items'][0]['tags'] == ['scratch'] and approved['items'][0]['lot'] == 'L1'
    unworked = _query(built, ledger=ledger, filters={'workflow_state': 'unworked'}, limit=200)
    assert len(unworked['items']) == 21 - 3, 'images without a ledger row are unworked'


def test_cursors_are_bound_and_foreign_revisions_are_not_found(built):
    from backend.engine.image_query import query_images
    index, _source, receipt, _tmp = built
    first = _query(built, limit=3)
    assert len(first['items']) == 3 and first['next_cursor']
    second = _query(built, limit=3, cursor=first['next_cursor'])
    assert second['items'][0]['relative_path'] > first['items'][-1]['relative_path']
    with pytest.raises(ValueError, match='another revision'):
        _query(built, limit=3, cursor=first['next_cursor'], query='검사')
    with pytest.raises(ValueError, match='Unknown filters'):
        _query(built, filters={'path': '/etc'})
    with pytest.raises(KeyError):
        query_images(index.path, 'ns:other', receipt.revision_id)


def test_saved_selections_resolve_by_identity_and_never_by_path_alone(built):
    from backend.engine.image_query import resolve_image_ids
    index, source, receipt, tmp_path = built
    rows = {item['relative_path']: item for item in _query(built, limit=200)['items']}
    kept = rows['train/ok/검사_train_ok_0.png']
    replaced = rows['train/ok/검사_train_ok_1.png']
    renamed = rows['train/ok/검사_train_ok_2.png']
    removed = rows['train/ok/검사_train_ok_3.png']
    Image.new('RGB', (8, 8), (250, 250, 250)).save(source / replaced['relative_path'])  # other bytes at the same path
    (source / renamed['relative_path']).rename(source / 'train' / 'ok' / 'moved.png')
    (source / removed['relative_path']).unlink()
    newer = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'exclude')
    selections = [{key: row[key] for key in ('image_uuid', 'sha256', 'relative_path')} for row in (kept, replaced, renamed, removed)]
    resolved = resolve_image_ids(index.path, 'ns:a', newer.revision_id, selections)
    assert [row['status'] for row in resolved] == ['found', 'changed', 'moved', 'missing']
    assert resolved[1]['current']['relative_path'] == replaced['relative_path'], 'the same path with other bytes is not silently re-selected'
    assert [candidate['relative_path'] for candidate in resolved[2]['candidates']] == ['train/ok/moved.png']
    assert resolved[3]['candidates'] == [] and resolved[3]['current'] is None
    path_only = resolve_image_ids(index.path, 'ns:a', newer.revision_id, [{'relative_path': kept['relative_path']}])
    assert path_only[0]['status'] == 'missing', 'a selection without an identity is never matched by its path'


def _finished(client, job_id):
    import time
    for _ in range(200):
        view = client.get(f'/api/dataset/imports/{job_id}').json()
        if view['state'] in ('completed', 'failed', 'aborted', 'interrupted'):
            return view
        time.sleep(0.05)
    raise AssertionError('import did not finish')


def test_the_library_serves_the_active_revision_and_resolves_selections(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    source = tmp_path / '원본'
    for label, color in (('ok', 'white'), ('ng', 'black')):
        for number in range(3):
            path = source / label / f'{label}_{number}.png'
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (8, 8), (number * 60, 0, 0) if label == 'ok' else (0, number * 60, 255)).save(path)
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        client.post('/api/project/create', json={'name': 'Library', 'task': 'classification'})
        assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
        assert client.get('/api/dataset/library/images').status_code == 409, 'nothing is served before a revision is accepted'
        view = _finished(client, client.post('/api/dataset/imports', json={'task': 'classification'}).json()['job_id'])
        revision = view['result']['revision']['revision_id']
        assert client.post(f"/api/dataset/imports/{view['job_id']}/accept", json={'revision_id': revision, 'expected_active': None}).status_code == 200
        page = client.get('/api/dataset/library/images', params={'q': 'ng_', 'limit': 2}).json()
        assert [item['relative_path'] for item in page['items']] == ['ng/ng_0.png', 'ng/ng_1.png'] and page['next_cursor']
        assert page['active'] is True and page['items'][0]['file_path'] == str(source.resolve() / 'ng' / 'ng_0.png')
        assert page['items'][0]['workflow_state'] == 'unworked' and page['items'][0]['tags'] == []
        rest = client.get('/api/dataset/library/images', params={'q': 'ng_', 'limit': 2, 'cursor': page['next_cursor']}).json()
        assert [item['relative_path'] for item in rest['items']] == ['ng/ng_2.png'] and rest['next_cursor'] is None
        assert client.get('/api/dataset/library/images', params={'q': 'ok', 'cursor': page['next_cursor']}).status_code == 422
        chosen = page['items'][0]
        (source / 'ng' / 'ng_0.png').rename(source / 'ng' / 'renamed.png')
        newer = _finished(client, client.post('/api/dataset/imports', json={'task': 'classification', 'verify': True}).json()['job_id'])
        newer_id = newer['result']['revision']['revision_id']
        resolved = client.post('/api/dataset/library/resolve', json={'revision_id': newer_id, 'selections': [
            {'image_uuid': chosen['image_uuid'], 'sha256': chosen['sha256']}]}).json()
        assert resolved['active'] is False and resolved['results'][0]['status'] == 'moved'
        assert resolved['results'][0]['candidates'][0]['file_path'] == str(source.resolve() / 'ng' / 'renamed.png')
        path_only = client.post('/api/dataset/library/resolve', json={'selections': [{'relative_path': 'ng/ng_1.png'}]})
        assert path_only.status_code == 422, 'a path is never accepted as a selection'
        assert client.post('/api/dataset/library/resolve', json={'selections': [{}]}).json()['results'][0]['status'] == 'missing'
        assert client.get('/api/dataset/library/images', params={'revision_id': '0' * 32}).status_code == 404


def test_resolving_selections_is_a_read_for_project_roles():
    from backend.contracts.authentication import permission_action
    assert permission_action('/api/dataset/library/resolve', 'POST') == 'project.read'
    assert permission_action('/api/dataset/library/images', 'GET') == 'project.read'


def test_a_team_viewer_can_search_and_resolve_but_not_another_projects_revision(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    from backend.main import create_app
    app = create_app(str(tmp_path / 'shared-registry'), shared_auth_dir=str(tmp_path / 'accounts'))
    store = app.state.accounts
    admin = store.bootstrap('owner', 'long password 123')
    viewer_account = store.create_user('viewer', 'long password 456')
    client = lambda name, password: TestClient(app, headers={'Authorization': 'Bearer ' + store.login(name, password)['token']})
    owner = client('owner', 'long password 123')
    project = owner.post('/api/project/create', json={'name': 'Shared library', 'task': 'classification'}).json()
    source = tmp_path / 'shared-source'
    for label in ('ok', 'ng'):
        (source / label).mkdir(parents=True)
        Image.new('RGB', (8, 8), 'white' if label == 'ok' else 'black').save(source / label / f'{label}.png')
    assert owner.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    view = _finished(owner, owner.post('/api/dataset/imports', json={'task': 'classification'}).json()['job_id'])
    revision = view['result']['revision']['revision_id']
    assert owner.post(f"/api/dataset/imports/{view['job_id']}/accept", json={'revision_id': revision, 'expected_active': None}).status_code == 200
    store.set_membership(project['id'], viewer_account['id'], 'viewer', admin['id'])
    store.select_project(viewer_account['id'], project['id'])
    viewer = client('viewer', 'long password 456')
    page = viewer.get('/api/dataset/library/images', params={'q': 'ng/'})
    assert page.status_code == 200 and [item['relative_path'] for item in page.json()['items']] == ['ng/ng.png'], page.text
    item = page.json()['items'][0]
    resolved = viewer.post('/api/dataset/library/resolve', json={'selections': [{'image_uuid': item['image_uuid'], 'sha256': item['sha256']}]})
    assert resolved.status_code == 200 and resolved.json()['results'][0]['status'] == 'found', resolved.text
    other = owner.post('/api/project/create', json={'name': 'Other', 'task': 'classification'}).json()
    assert other['id'] != project['id']
    foreign = owner.get('/api/dataset/library/images', params={'revision_id': revision})
    assert foreign.status_code == 404, 'the other project cannot read this project revision by its id'


def test_decomposed_korean_names_are_found_by_a_composed_search(tmp_path):
    import unicodedata
    from backend.engine.dataset_index import DatasetIndex, index_path
    from backend.engine.image_query import query_images
    index = DatasetIndex(index_path(tmp_path / 'registry'))
    source = tmp_path / 'source'
    name = unicodedata.normalize('NFD', '스크래치_01.png')
    (source / 'ng').mkdir(parents=True)
    Image.new('RGB', (8, 8)).save(source / 'ng' / name)
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'exclude')
    for typed in (unicodedata.normalize('NFC', '스크래치'), unicodedata.normalize('NFD', '스크래치')):
        assert len(query_images(index.path, 'ns:a', receipt.revision_id, query=typed)['items']) == 1, typed


def test_an_image_unread_in_this_build_is_kept_as_unreadable_and_lookups_are_batched(built):
    import os
    import sqlite3
    from backend.engine.image_query import resolve_image_ids
    index, source, receipt, tmp_path = built
    rows = {item['relative_path']: item for item in _query(built, limit=200)['items']}
    target = rows['train/ok/검사_train_ok_0.png']
    path = source / target['relative_path']
    os.chmod(path, 0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip('this account can read a file without read permission')
        newer = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'exclude', verify=True)
    finally:
        os.chmod(path, 0o644)
    result = resolve_image_ids(index.path, 'ns:a', newer.revision_id, [{'image_uuid': target['image_uuid'], 'sha256': target['sha256']}])
    assert result[0]['status'] == 'unreadable', 'an image this build could not read is not reported as replaced'
    many = [{'image_uuid': row['image_uuid'], 'sha256': row['sha256']} for row in rows.values()] * 40
    resolved = resolve_image_ids(index.path, 'ns:a', receipt.revision_id, many)
    assert len(resolved) == len(many) > 500 and [row['image_uuid'] for row in resolved] == [row['image_uuid'] for row in many]
    with sqlite3.connect(index.path) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='dataset_index_identity'").fetchone()


def test_error_filters_distinguish_annotation_errors_from_image_errors_and_bind_cursor(built):
    import sqlite3
    index, _source, receipt, _tmp = built
    relative = 'test/ng/검사_test_ng_0.png'
    with sqlite3.connect(index.path) as db:
        db.execute('INSERT OR REPLACE INTO dataset_index_annotations(revision_id,relative_path,format,labels,files,error) VALUES(?,?,?,?,?,?)',
                   (receipt.revision_id, relative, 'labelme', '[]', '[]', 'Malformed source annotation'))
    images = _query(built, filters={'error': 'image'})['items']
    annotations = _query(built, filters={'error': 'annotation'})['items']
    assert [row['relative_path'] for row in images] == ['train/ng/100%_odd_name.png']
    assert [row['relative_path'] for row in annotations] == [relative]
    assert annotations[0]['valid'], 'bad labels must not be presented as damaged image bytes'
    assert len(_query(built, filters={'error': 'any'})['items']) == 2
    assert len(_query(built, filters={'state': 'valid', 'error': 'any'})['items']) == 1
    page = _query(built, filters={'error': 'any'}, limit=1)
    with pytest.raises(ValueError, match='another revision'):
        _query(built, filters={'error': 'annotation'}, cursor=page['next_cursor'])
    with pytest.raises(ValueError, match='error must'):
        _query(built, filters={'error': 'unsupported'})
