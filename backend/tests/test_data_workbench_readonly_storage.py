"""Real read handlers preserve bytes and even absent/empty directory namespaces.

Root executes these isolated controls; no daemon, GPU, training, model-quality
or GUI acceptance follows. The API workspace fixture is the original product
fixture, not a replacement for GET behavior or a mocked filesystem.
"""
import errno
import hashlib
import json
from pathlib import Path
import stat

import pytest

from backend.engine import data_workbench as dw
from backend.tests.test_product_data_api import client_workspace


def inventory(root):
    rows = {}
    for path in [root, *sorted(root.rglob('*'))]:
        before = path.lstat()
        identity = (before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
                    before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        if stat.S_ISLNK(before.st_mode):
            rows[path.relative_to(root).as_posix()] = ('link', str(path.readlink()), identity)
        elif stat.S_ISREG(before.st_mode):
            data = path.read_bytes()
            after = path.lstat()
            assert identity == (after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
                                after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            rows[path.relative_to(root).as_posix()] = ('file', hashlib.sha256(data).hexdigest(), identity)
        else:
            assert stat.S_ISDIR(before.st_mode)
            rows[path.relative_to(root).as_posix()] = ('directory', identity)
    return rows


def storage_path(project, source):
    key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:24]
    return project.resolve() / 'dataset' / 'data_workbench' / key


@pytest.mark.parametrize('endpoint,expected_status,expected_body', [
    ('diagnostics', 200, {'report': None}),
    ('derived', 200, {'versions': []}),
    ('derived/derived_' + 'a' * 32, 422, None),
    ('review-queues', 200, {'queues': []}),
    ('review-queues/review_' + 'a' * 32, 422, None),
])
def test_missing_read_API_preserves_complete_original_namespace(client_workspace, endpoint, expected_status, expected_body):
    api, project, source = client_workspace
    root = Path(project['project_dir'])
    expected = storage_path(root, source)
    assert not expected.exists() and not expected.parent.exists()
    before = {'project': inventory(root), 'source': inventory(source)}
    params = {'image_path': str(source / 'part.png')} if endpoint == 'derived' else None
    response = api.get('/api/data-workbench/' + endpoint, params=params)
    assert response.status_code == expected_status, response.text
    if expected_body is not None:
        assert response.json() == expected_body
    else:
        assert 'not found' in response.json()['detail'].lower()
    assert {'project': inventory(root), 'source': inventory(source)} == before
    assert not expected.exists() and not expected.parent.exists()


@pytest.mark.parametrize('layout', ['missing-project', 'empty-project', 'empty-dataset', 'empty-storage-parent', 'existing-empty-root'])
def test_read_storage_lookup_returns_same_scoped_path_without_creation(tmp_path, layout):
    source = tmp_path / 'source'; source.mkdir()
    project = tmp_path / 'project'
    root = storage_path(project, source)
    if layout != 'missing-project':
        project.mkdir()
    if layout in ['empty-dataset', 'empty-storage-parent', 'existing-empty-root']:
        (project / 'dataset').mkdir()
    if layout in ['empty-storage-parent', 'existing-empty-root']:
        root.parent.mkdir()
    if layout == 'existing-empty-root':
        root.mkdir()
    before = inventory(tmp_path)
    assert dw._storage(project, source, create=False) == root
    assert inventory(tmp_path) == before


@pytest.mark.parametrize('part', ['dataset', 'parent', 'root'])
@pytest.mark.parametrize('dangling', [False, True])
def test_read_storage_preserves_original_link_refusal_without_following(tmp_path, part, dangling):
    project = tmp_path / 'project'; project.mkdir()
    source = tmp_path / 'source'; source.mkdir()
    root = storage_path(project, source)
    target = tmp_path / 'outside'
    if not dangling:
        target.mkdir(); (target / 'sentinel').write_bytes(b'outside original')
    link = {'dataset': project / 'dataset', 'parent': root.parent, 'root': root}[part]
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    before = inventory(tmp_path)
    with pytest.raises(ValueError, match='Project data storage cannot contain symbolic links'):
        dw._storage(project, source, create=False)
    assert inventory(tmp_path) == before


@pytest.mark.parametrize('part', ['project', 'dataset', 'parent', 'root'])
def test_read_storage_matches_original_non_directory_error_class_errno(tmp_path, part):
    project = tmp_path / 'project'
    source = tmp_path / 'source'; source.mkdir()
    root = storage_path(project, source)
    bad = {'project': project, 'dataset': project / 'dataset', 'parent': root.parent, 'root': root}[part]
    bad.parent.mkdir(parents=True, exist_ok=True); bad.write_bytes(b'not a directory')
    before = inventory(tmp_path)
    expected_type = FileExistsError if part == 'root' else NotADirectoryError
    expected_errno = errno.EEXIST if part == 'root' else errno.ENOTDIR
    with pytest.raises(expected_type) as read_error:
        dw._storage(project, source, create=False)
    with pytest.raises(expected_type) as original_write_error:
        dw._storage(project, source)
    assert read_error.value.errno == original_write_error.value.errno == expected_errno
    assert read_error.value.filename == original_write_error.value.filename == str(root)
    assert inventory(tmp_path) == before


def test_saved_diagnostic_API_preserves_exact_durable_report_and_source(client_workspace):
    api, project, source = client_workspace
    response = api.post('/api/data-workbench/diagnostics', json={})
    assert response.status_code == 200, response.text
    original = response.json()
    root = Path(project['project_dir'])
    path = storage_path(root, source) / 'diagnostics_default.json'
    before = {'project': inventory(root), 'source': inventory(source)}
    saved = path.read_bytes()
    reopened = api.get('/api/data-workbench/diagnostics')
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()['source_sha256'] == original['source_sha256']
    assert reopened.json()['items'][0]['source_sha256'] == hashlib.sha256((source / 'part.png').read_bytes()).hexdigest()
    assert reopened.json()['stale'] is False
    assert path.read_bytes() == saved
    assert {'project': inventory(root), 'source': inventory(source)} == before


def test_existing_derived_reads_preserve_all_evidence_then_original_write_still_creates(client_workspace):
    api, project, source = client_workspace
    root = Path(project['project_dir']); image = source / 'part.png'
    image_raw = image.read_bytes(); digest = hashlib.sha256(image_raw).hexdigest()
    scope = {'task': 'detection', 'labelset_id': 'default'}
    made = dw.derive(root, source, image, [], {'kind': 'flip', 'axis': 'horizontal'}, 'owned-test', digest, scope=scope)
    assert storage_path(root, source).is_dir()
    before = {'project': inventory(root), 'source': inventory(source)}
    read = dw.read_derived(root, source, made['id'], scope)
    history = dw.derived_history(root, source, image, scope)
    assert read['evidence_sha256'] == made['evidence_sha256']
    assert [row['id'] for row in history] == [made['id']]
    assert {'project': inventory(root), 'source': inventory(source)} == before
    second = dw.derive(root, source, image, [], {'kind': 'brightness', 'factor': 1}, 'owned-test', digest, scope=scope)
    assert second['id'] != made['id'] and Path(second['dataset_path']).is_dir()
    assert image.read_bytes() == image_raw and hashlib.sha256(image.read_bytes()).hexdigest() == digest


def test_existing_queue_reads_preserve_bytes_and_default_advance_keeps_owned_lock(client_workspace):
    api, project, source = client_workspace
    root = Path(project['project_dir']); image = source / 'part.png'
    image_raw = image.read_bytes()
    made = dw.create_review_queue(root, source, 'detection', 'default',
        [{'file_path': str(image), 'is_correct': False, 'confidence': .9}],
        {'evaluation_id': 'evaluation_owned_control', 'job_id': 'owned-control', 'step': 4})
    path = storage_path(root, source) / 'review_queues' / (made['id'] + '.json')
    raw = path.read_bytes(); before = {'project': inventory(root), 'source': inventory(source)}
    assert dw.read_review_queue(root, source, 'detection', 'default', made['id']) == made
    assert dw.list_review_queues(root, source, 'detection', 'default') == [made]
    assert path.read_bytes() == raw
    assert {'project': inventory(root), 'source': inventory(source)} == before
    advanced = dw.advance_review_queue(root, source, 'detection', 'default', made['id'], 1, 'part.png', 'reviewed', 'owned-test')
    assert advanced['revision'] == 2 and advanced['cursor'] == 1
    assert (storage_path(root, source) / 'review_queue.lock').is_file()
    assert json.loads(path.read_bytes()) == advanced and image.read_bytes() == image_raw


def test_missing_derived_source_and_invalid_queue_id_keep_original_refusals_without_writes(client_workspace):
    api, project, source = client_workspace
    root = Path(project['project_dir']); before = {'project': inventory(root), 'source': inventory(source)}
    with pytest.raises(ValueError, match='Image must be a regular file in the active source scope'):
        dw.derived_history(root, source, source / 'absent.png')
    with pytest.raises(ValueError, match='Invalid review queue ID'):
        dw.read_review_queue(root, source, 'detection', 'default', '../foreign')
    assert {'project': inventory(root), 'source': inventory(source)} == before
