"""Every SQLite store switches to WAL through one helper that survives a concurrent switch.

Two processes opening a new or older store at the same moment both switch it to WAL; SQLite answers the second with
SQLITE_BUSY ('database is locked') at once instead of waiting. The helper tries again until the store's own timeout;
any other error, 'database table is locked' (SQLITE_LOCKED) included, is raised at once. Every store ends in WAL.
"""
import re
import sqlite3
from pathlib import Path

import pytest

from backend.engine import sqlite_wal

ROOT = Path(__file__).resolve().parents[2]


class _Refusing:
    """A connection whose WAL switch is refused ``times`` times with ``message`` (and SQLite result ``code``, if any)."""
    def __init__(self, connection, times, message='database is locked', code=None):
        self.connection, self.times, self.message, self.code, self.switches = connection, times, message, code, 0

    def execute(self, sql, *args):
        if sql == 'PRAGMA journal_mode=WAL':
            self.switches += 1
            if self.switches <= self.times:
                error = sqlite3.OperationalError(self.message)
                if self.code is not None:
                    error.sqlite_errorcode = self.code
                raise error
        return self.connection.execute(sql, *args)


def test_a_refused_concurrent_switch_is_tried_again_and_the_store_ends_in_wal(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite_wal.time, 'sleep', lambda seconds: None)
    real = sqlite3.connect(tmp_path / 'store.sqlite3')
    connection = _Refusing(real, 2)
    sqlite_wal.use_wal(connection)
    assert connection.switches == 3
    assert real.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'


def test_another_error_is_raised_at_once_and_a_lasting_lock_after_the_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite_wal.time, 'sleep', lambda seconds: None)
    broken = _Refusing(sqlite3.connect(tmp_path / 'a.sqlite3'), 1, 'disk I/O error')
    with pytest.raises(sqlite3.OperationalError, match='disk I/O'):
        sqlite_wal.use_wal(broken)
    assert broken.switches == 1
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(sqlite_wal.time, 'monotonic', lambda: next(clock))
    held = _Refusing(sqlite3.connect(tmp_path / 'b.sqlite3'), 10_000)
    with pytest.raises(sqlite3.OperationalError, match='locked'):
        sqlite_wal.use_wal(held, timeout=10)
    assert 1 < held.switches < 10


def test_only_a_busy_answer_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite_wal.time, 'sleep', lambda seconds: None)
    table = _Refusing(sqlite3.connect(tmp_path / 'a.sqlite3'), 1, 'database table is locked', code=6)  # SQLITE_LOCKED
    with pytest.raises(sqlite3.OperationalError, match='table is locked'):
        sqlite_wal.use_wal(table)
    assert table.switches == 1
    recovery = _Refusing(sqlite3.connect(tmp_path / 'b.sqlite3'), 2, 'database is locked', code=261)  # SQLITE_BUSY_RECOVERY
    sqlite_wal.use_wal(recovery)
    assert recovery.switches == 3


def test_a_real_busy_database_is_waited_out_and_ends_in_wal(tmp_path, monkeypatch):
    path = tmp_path / 'older.sqlite3'
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute('CREATE TABLE older(x)')
    holder.execute('BEGIN EXCLUSIVE')  # another process holds the file
    opener = sqlite3.connect(path, timeout=0)
    waits = []

    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            holder.execute('COMMIT')
    monkeypatch.setattr(sqlite_wal.time, 'sleep', wait)
    sqlite_wal.use_wal(opener)
    assert len(waits) == 2, 'SQLite answered busy until the holder let go'
    assert opener.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'


def test_every_store_ends_in_wal(tmp_path, monkeypatch):
    from backend.api import routes_inspections, routes_model_deployments
    from backend.engine import dataset_index, evaluation_history, inspection_service, job_store, model_operations
    from backend.engine import runtime_deployment, shared_scheduler

    def entered(manager):
        with manager:
            pass
    stores = {  # name: (file the store opens in its folder, opener)
        'job_store': ('ledger.sqlite3', lambda d: job_store.JobStore(d / 'ledger.sqlite3')),
        'dataset_index': ('index.sqlite3', lambda d: dataset_index.DatasetIndex(d / 'index.sqlite3')),
        'evaluation_history': ('compare.sqlite3', lambda d: evaluation_history.ComparisonJobs(d / 'compare.sqlite3')),
        'inspection_service': ('inspection_service.sqlite3', lambda d: inspection_service.InspectionStore(d)),
        'model_operations': ('model_operations.sqlite3', lambda d: model_operations.OperationsStore(d)),
        'runtime_deployment': ('runtime_deployments.sqlite3', lambda d: runtime_deployment.DeploymentLedger(d)),
        'shared_scheduler': ('leases.sqlite3', lambda d: shared_scheduler.ResourceLeases(d / 'leases.sqlite3')),
        'inspection_history': ('inspection_history.sqlite3', lambda d: entered(routes_inspections._store(object()))),
        'inspection_run_index': ('inspection_run_index.sqlite3', lambda d: entered(routes_inspections._run_index(object()))),
        'model_deployments': ('model_deployments.sqlite3', lambda d: entered(routes_model_deployments._store({'project_dir': str(d)}))),
    }
    modes = {}
    for name, (filename, opener) in stores.items():
        folder = tmp_path / name
        folder.mkdir()
        monkeypatch.setattr(routes_inspections, 'get_current_project', lambda request, d=folder: {'project_dir': str(d)})
        monkeypatch.setattr(routes_inspections, '_project_root', lambda request, d=folder: d)
        opener(folder)
        check = sqlite3.connect(f'{(folder / filename).as_uri()}?mode=ro', uri=True)
        modes[name] = check.execute('PRAGMA journal_mode').fetchone()[0]
        check.close()
    assert modes == {name: 'wal' for name in stores}


def test_no_store_sets_a_journal_mode_except_through_the_helper():
    direct = []
    for path in (ROOT / 'backend').rglob('*.py'):
        if 'tests' in path.parts or path.name == 'sqlite_wal.py':
            continue
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if re.search(r'journal_mode', line, flags=re.I):  # any spelling: journal_mode = 'wal', journal_mode(WAL), ...
                direct.append(f'{path.relative_to(ROOT)}:{number}')
    assert direct == [], direct
