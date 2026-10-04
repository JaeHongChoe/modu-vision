"""Backups retain data, while held process coordination files stay local."""
import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile

from PIL import Image

from backend.engine.project_archive import create_archive
from backend.tests.test_project_archive import _client


def _project(tmp_path):
    api = _client(tmp_path / 'projects')
    project = api.post('/api/project/create', json={'name': 'Runtime lock archive'}).json()
    source = tmp_path / 'source'
    source.mkdir()
    Image.new('RGB', (16, 16), 'white').save(source / 'part.png')
    assert api.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    project = api.get('/api/project/current').json()
    root = Path(project['project_dir'])
    (root / 'runtime_service').mkdir(exist_ok=True)
    return project, root, source


def test_backup_omits_only_owned_locks_and_retains_same_named_user_files(tmp_path):
    project, root, source = _project(tmp_path)
    retained = {
        'project/runtime_lifecycle.lock': b'project user data',
        'project/dataset/runtime_lifecycle.lock': b'dataset user data',
        'project/runtime_service/operator.lock': b'operator user data',
        'source/runtime_lifecycle.lock': b'source user data',
        'source/.retention/runtime_lifecycle.lock': b'source retention named data',
        'source/runtime_service/runtime_lifecycle.lock': b'source runtime named data',
    }
    for member, content in retained.items():
        prefix, relative = member.split('/', 1)
        path = (root if prefix == 'project' else source) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    result = create_archive(project, tmp_path / 'backups')

    assert result['backup_status'] == 'archive_verified'
    for relative in ('.retention/runtime_lifecycle.lock', 'runtime_service/runtime_lifecycle.lock'):
        assert (root / relative).is_file(), 'backup must keep the live coordination file'
    with ZipFile(result['archive_path']) as archive:
        names = archive.namelist()
        assert 'project/.retention/runtime_lifecycle.lock' not in names
        assert 'project/runtime_service/runtime_lifecycle.lock' not in names
        for member, content in retained.items():
            assert archive.read(member) == content


def test_backup_snapshots_committed_wal_data_and_omits_only_sqlite_journals(tmp_path):
    project, root, _ = _project(tmp_path)
    database = root / 'runtime_service' / 'records.sqlite3'
    connection = sqlite3.connect(database)
    try:
        assert connection.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('CREATE TABLE records(value TEXT)')
        connection.execute("INSERT INTO records VALUES ('committed in WAL')")
        connection.commit()
        assert database.with_name(database.name + '-wal').stat().st_size > 0
        result = create_archive(project, tmp_path / 'backups')
        with ZipFile(result['archive_path']) as archive:
            member = 'project/runtime_service/records.sqlite3'
            assert member + '-wal' not in archive.namelist()
            assert member + '-shm' not in archive.namelist()
            snapshot = tmp_path / 'snapshot.sqlite3'
            snapshot.write_bytes(archive.read(member))
            manifest = json.loads(archive.read('backup-manifest.json'))
            assert member in manifest['db_revision']
        with sqlite3.connect(snapshot) as readback:
            assert readback.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert readback.execute('SELECT value FROM records').fetchall() == [('committed in WAL',)]
    finally:
        connection.close()
