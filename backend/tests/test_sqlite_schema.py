"""Two openers of a new or older store upgrade its schema at once without failing (S1-02 follow-up).

Comparison jobs and the inspection store added a missing column after reading the column list outside any lock, so a
second opener also added it and failed with 'duplicate column name' (a fresh project could answer HTTP 500). The
dataset index read its schema version outside its upgrade transaction, so an index another opener had just created
read as an unknown schema 0. Each check and change now runs under one write lock.

The deterministic tests let another opener finish its upgrade exactly between a store's unlocked schema read and its
change (the unfixed code fails every time); the thread races are a smaller check of real concurrency.
"""
import sqlite3
import threading
from pathlib import Path

import pytest


def add_missing_columns(*args):
    from backend.engine.sqlite_schema import add_missing_columns as add  # imported here: the race tests run without it
    return add(*args)


def _race(tmp_path, opener, runs, openers=6, older=None, older_schema='CREATE TABLE older_marker(x)'):
    """Open a store with ``openers`` threads released together, ``runs`` times; the errors they raised."""
    errors = []
    for run in range(runs):
        folder = tmp_path / f'run{run}'
        folder.mkdir()
        if older:
            connection = sqlite3.connect(folder / older)  # a file written before the store's schema (rollback journal)
            connection.executescript(older_schema)
            connection.commit()
            connection.close()
        barrier = threading.Barrier(openers)

        def go():
            barrier.wait()
            try:
                opener(folder)
            except Exception as exc:  # each failing opener is one finding
                errors.append(f'{type(exc).__name__}: {exc}')
        threads = [threading.Thread(target=go) for _ in range(openers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
    return errors


def test_comparison_jobs_opened_at_once_on_a_new_file_never_add_a_column_twice(tmp_path):
    from backend.engine.evaluation_history import ComparisonJobs
    assert _race(tmp_path, lambda folder: ComparisonJobs(folder / 'compare.sqlite3'), 40) == []
    columns = {row[1] for row in sqlite3.connect(tmp_path / 'run0' / 'compare.sqlite3').execute('PRAGMA table_info(jobs)')}
    assert {'owner_pid', 'owner_created_at', 'owner_command_sha256'} <= columns


def test_the_inspection_store_opened_at_once_on_a_new_file_never_adds_a_column_twice(tmp_path):
    from backend.engine.inspection_service import InspectionStore
    assert _race(tmp_path, InspectionStore, 20) == []
    columns = {row[1] for row in sqlite3.connect(tmp_path / 'run0' / 'inspection_service.sqlite3').execute('PRAGMA table_info(jobs)')}
    assert {'model_verdict', 'interrupted'} <= columns


def test_the_dataset_index_opened_at_once_on_an_older_file_reads_its_own_schema(tmp_path):
    from backend.engine.dataset_index import SCHEMA_VERSION, DatasetIndex
    assert _race(tmp_path, lambda folder: DatasetIndex(folder / 'index.sqlite3'), 60, older='index.sqlite3') == []
    assert sqlite3.connect(tmp_path / 'run0' / 'index.sqlite3').execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION


# The inspection history as an older build wrote it, before its added run and row columns.
OLDER_HISTORY = """
CREATE TABLE runs (run_id TEXT PRIMARY KEY, source_folder TEXT NOT NULL, task TEXT NOT NULL, scope TEXT NOT NULL,
    pipeline_id TEXT NOT NULL, pipeline_name TEXT NOT NULL, pipeline_hash TEXT NOT NULL, pipeline_json TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE rows (run_id TEXT NOT NULL REFERENCES runs(run_id), image_path TEXT NOT NULL, image_json TEXT NOT NULL,
    state TEXT NOT NULL, result_json TEXT, error TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (run_id, image_path));
"""


def test_inspection_history_opened_at_once_on_an_older_file_never_adds_a_column_twice(tmp_path, monkeypatch):
    from backend.api import routes_inspections
    current = {}
    monkeypatch.setattr(routes_inspections, 'get_current_project', lambda request: {'project_dir': str(current['folder'])})

    def opener(folder):
        current['folder'] = folder  # every opener of one run opens the same project folder
        with routes_inspections._store(object()):
            pass
    assert _race(tmp_path, opener, 30, older='inspection_history.sqlite3', older_schema=OLDER_HISTORY) == []
    columns = {row[1] for row in sqlite3.connect(tmp_path / 'run0' / 'inspection_history.sqlite3').execute('PRAGMA table_info(runs)')}
    assert {'owner_instance', 'saved_version_id', 'execution_config_sha256'} <= columns


def test_a_store_of_an_unknown_schema_is_still_refused(tmp_path):
    from backend.engine.dataset_index import DatasetIndex
    path = tmp_path / 'index.sqlite3'
    DatasetIndex(path)
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA user_version = 99')
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match='uses index schema 99'):
        DatasetIndex(path)


def test_missing_columns_are_added_once_and_a_failed_upgrade_adds_none(tmp_path):
    connection = sqlite3.connect(tmp_path / 'store.sqlite3')
    connection.execute('CREATE TABLE jobs(job_id TEXT)')
    add_missing_columns(connection, 'jobs', {'owner_pid': 'INTEGER', 'note': "TEXT NOT NULL DEFAULT ''"})
    add_missing_columns(connection, 'jobs', {'owner_pid': 'INTEGER'})  # present: nothing to add
    assert [row[1] for row in connection.execute('PRAGMA table_info(jobs)')] == ['job_id', 'owner_pid', 'note']
    with pytest.raises(sqlite3.OperationalError):
        add_missing_columns(connection, 'jobs', {'first': 'INTEGER', 'second': 'NOT A TYPE ('})
    assert [row[1] for row in connection.execute('PRAGMA table_info(jobs)')] == ['job_id', 'owner_pid', 'note'], 'rolled back'
    assert not connection.in_transaction


def test_unsafe_names_or_an_open_transaction_are_refused(tmp_path):
    connection = sqlite3.connect(tmp_path / 'store.sqlite3')
    connection.execute('CREATE TABLE jobs(job_id TEXT)')
    with pytest.raises(ValueError):
        add_missing_columns(connection, 'jobs; DROP TABLE jobs', {'x': 'TEXT'})
    with pytest.raises(ValueError):
        add_missing_columns(connection, 'jobs', {'x y': 'TEXT'})
    connection.execute('INSERT INTO jobs VALUES (?)', ('a',))  # the module opens a transaction before DML
    with pytest.raises(RuntimeError, match='outside a transaction'):
        add_missing_columns(connection, 'jobs', {'x': 'TEXT'})


def test_a_store_with_every_column_takes_no_write_lock(tmp_path):
    """An opener of an up-to-date file only reads: it never waits behind another writer for nothing."""
    path = tmp_path / 'store.sqlite3'
    setup = sqlite3.connect(path)
    setup.execute('CREATE TABLE jobs(job_id TEXT, owner_pid INTEGER)')
    setup.commit()
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute('BEGIN IMMEDIATE')  # another process is writing
    try:
        reader = sqlite3.connect(path, timeout=0)
        add_missing_columns(reader, 'jobs', {'owner_pid': 'INTEGER'})  # would fail at once ('locked') if it locked
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            add_missing_columns(reader, 'jobs', {'note': 'TEXT'})  # a missing column needs the write lock
    finally:
        writer.execute('ROLLBACK')


REAL_CONNECT = sqlite3.connect


class _Rows(list):
    def fetchone(self):
        return self[0] if self else None

    def fetchall(self):
        return list(self)


class _Interleave(sqlite3.Connection):
    """After the first matching read made outside a transaction, another opener changes the file and commits."""
    trigger, hook, fired = None, None, 0

    def execute(self, sql, *args):
        if _Interleave.hook and sql.replace(' ', '') == _Interleave.trigger and not self.in_transaction:
            rows = _Rows(super().execute(sql, *args).fetchall())
            hook, _Interleave.hook = _Interleave.hook, None
            _Interleave.fired += 1
            hook()
            return rows
        return super().execute(sql, *args)


@pytest.fixture
def interleave(monkeypatch):
    _Interleave.trigger, _Interleave.hook, _Interleave.fired = None, None, 0
    monkeypatch.setattr(sqlite3, 'connect', lambda *args, **kwargs: REAL_CONNECT(*args, **{**kwargs, 'factory': _Interleave}))

    def arm(trigger, hook):
        _Interleave.trigger, _Interleave.hook = trigger.replace(' ', ''), hook
    return arm


def _other_opener_adds(path, table, names):
    def run():
        other = REAL_CONNECT(path, isolation_level=None, timeout=10)
        other.execute('BEGIN IMMEDIATE')
        for name in names:
            other.execute(f'ALTER TABLE {table} ADD COLUMN {name} TEXT')
        other.execute('COMMIT')
        other.close()
    return run


def test_comparison_jobs_survive_another_opener_adding_the_columns_right_after_their_read(tmp_path, interleave):
    from backend.engine.evaluation_history import ComparisonJobs
    path = tmp_path / 'compare.sqlite3'
    interleave('PRAGMA table_info(jobs)', _other_opener_adds(path, 'jobs', ['owner_pid', 'owner_created_at', 'owner_command_sha256']))
    ComparisonJobs(path)
    assert _Interleave.fired == 1


def test_the_inspection_store_survives_another_opener_adding_its_column_right_after_its_read(tmp_path, interleave):
    from backend.engine.inspection_service import InspectionStore
    interleave('PRAGMA table_info(jobs)', _other_opener_adds(tmp_path / 'inspection_service.sqlite3', 'jobs', ['interrupted']))
    InspectionStore(tmp_path)
    assert _Interleave.fired == 1


def test_inspection_history_survives_another_opener_upgrading_an_older_file_right_after_its_read(tmp_path, interleave, monkeypatch):
    from backend.api import routes_inspections
    path = tmp_path / 'inspection_history.sqlite3'
    older = REAL_CONNECT(path)
    older.executescript(OLDER_HISTORY)
    older.close()
    monkeypatch.setattr(routes_inspections, 'get_current_project', lambda request: {'project_dir': str(tmp_path)})
    interleave('PRAGMA table_info(runs)', _other_opener_adds(path, 'runs', ['owner_instance', 'saved_version_id', 'model_sha256_json',
                                                                            'model_paths_json', 'execution_config_json', 'execution_config_sha256']))
    with routes_inspections._store(object()):
        pass
    assert _Interleave.fired == 1


def test_the_dataset_index_reads_its_version_only_under_its_upgrade_lock(tmp_path, interleave, monkeypatch):
    from backend.engine.dataset_index import DatasetIndex
    path = tmp_path / 'index.sqlite3'
    older = REAL_CONNECT(path)
    older.execute('CREATE TABLE older_marker(x)')
    older.commit()
    older.close()

    def another_process_upgrades():
        monkeypatch.setattr(sqlite3, 'connect', REAL_CONNECT)  # the other opener is not intercepted
        DatasetIndex(path)
    interleave('PRAGMA user_version', another_process_upgrades)
    DatasetIndex(path)  # unfixed: the version was read unlocked, the other opener upgraded, and this one refused schema 0
