"""Real SQLite handles stay closed without depending on garbage collection."""
from pathlib import Path
import sqlite3

import pytest

from backend.engine import runtime_deployment as deployment


@pytest.fixture
def retained_connections(monkeypatch):
    original = sqlite3.connect
    retained = []  # Strong references prevent GC from hiding an unclosed handle.

    class ObservedConnection(sqlite3.Connection):
        closes = 0

        def close(self):
            self.closes += 1
            return super().close()

    def connect(database, *args, **kwargs):
        if Path(str(database)).name != 'runtime_deployments.sqlite3':
            return original(database, *args, **kwargs)
        assert 'factory' not in kwargs
        connection = original(database, *args, factory=ObservedConnection, **kwargs)
        retained.append(connection)
        return connection

    monkeypatch.setattr(deployment.sqlite3, 'connect', connect)
    try:
        yield retained
    finally:
        # Also release an old implementation's leaked handles after its failure.
        for connection in retained:
            if connection.closes == 0:
                connection.close()


def closed_between_operations(ledger, retained):
    assert retained
    for connection in retained:
        assert connection.closes == 1
        with pytest.raises(sqlite3.ProgrammingError, match='closed database'):
            connection.execute('SELECT 1')
    assert not Path(str(ledger.path) + '-wal').exists()
    assert not Path(str(ledger.path) + '-shm').exists()


def test_transactions_close_real_handles_and_keep_recovery_receipts(tmp_path, retained_connections):
    project = tmp_path / 'project'
    project.mkdir()
    ledger = deployment.DeploymentLedger(project / 'runtime_service', project_dir=project)
    closed_between_operations(ledger, retained_connections)
    assert ledger.history() == []
    assert ledger.active() is None
    assert ledger.diagnostics()['pending'] is None
    closed_between_operations(ledger, retained_connections)
    first = {'manifest_sha256': 'a' * 64, 'device': 'cpu'}
    second = {'manifest_sha256': 'b' * 64, 'device': 'cpu'}

    def ready(value):
        # The durable intent is committed and its handle closed before handoff.
        closed_between_operations(ledger, retained_connections)
        return {'status': 'ready', **value}

    accepted = ledger.apply(first, ready, reviewer='test operator')
    assert ledger.active()['deployment_id'] == accepted['deployment_id']
    assert ledger.diagnostics()['last_operation']['status'] == 'committed'
    closed_between_operations(ledger, retained_connections)

    def interrupted(value):
        closed_between_operations(ledger, retained_connections)
        raise KeyboardInterrupt('controlled interruption after committed intent')

    with pytest.raises(KeyboardInterrupt):
        ledger.apply(second, interrupted, reviewer='test operator')
    assert ledger.active()['deployment_id'] == accepted['deployment_id']
    assert ledger.diagnostics()['pending']['release'] == second
    closed_between_operations(ledger, retained_connections)
    recovered = ledger.recover(ready)
    assert recovered['status'] == 'rolled_back'
    assert recovered['ack'] == {'status': 'ready', **first}
    assert ledger.diagnostics()['pending'] is None
    assert len(ledger.history()) == 1
    closed_between_operations(ledger, retained_connections)
    restored = ledger.rollback(accepted['deployment_id'], ready, reviewer='test operator')
    assert restored['restored_from'] == accepted['deployment_id']
    assert ledger.active()['deployment_id'] == restored['deployment_id']
    assert len(ledger.history()) == 2
    closed_between_operations(ledger, retained_connections)


def test_setup_refusal_closes_real_handle_and_preserves_exception(tmp_path, retained_connections, monkeypatch):
    refusal = RuntimeError('controlled WAL setup refusal')

    def refused(connection, timeout):
        assert isinstance(connection, sqlite3.Connection)
        assert timeout == 30
        raise refusal

    monkeypatch.setattr(deployment, 'use_wal', refused)
    with pytest.raises(RuntimeError) as caught:
        deployment.DeploymentLedger(tmp_path / 'runtime_service', project_dir=tmp_path)
    assert caught.value is refusal
    assert len(retained_connections) == 1
    connection = retained_connections[0]
    assert connection.closes == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed database'):
        connection.execute('SELECT 1')
