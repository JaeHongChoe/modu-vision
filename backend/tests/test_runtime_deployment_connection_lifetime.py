"""Exact ledger source with inert connections: no SQLite import or database use.

Run directly with Python for SOURCE controls. Pytest may also collect these
unittest cases later. Auth, retention and SQLite effects are explicitly modeled;
only the original ledger control flow and transaction/close ordering execute.
"""
from __future__ import annotations

import ast
from contextlib import closing, nullcontext
import json
from pathlib import Path
import sys
import types
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'engine/runtime_deployment.py'


class _Path:
    def __init__(self, value='/owned/runtime_service'):
        self.value = value.value if isinstance(value, _Path) else str(value)
    def __truediv__(self, part): return _Path(self.value + '/' + str(part))
    def mkdir(self, **kwargs): pass
    def resolve(self): return self
    def relative_to(self, parent): return _Path(self.value.removeprefix(parent.value + '/'))
    def as_posix(self): return self.value
    def is_symlink(self): return False


class _Cursor:
    def __init__(self, row=None, rows=()): self.row, self.rows = row, rows
    def fetchone(self): return self.row
    def fetchall(self): return self.rows


class _Connection:
    """SQLite-like transaction context deliberately does NOT close itself."""
    def __init__(self, events, name='connection', *, row=None, rows=(), fail=None,
                 enter_error=None, exit_error=None, close_error=None,
                 row_factory_error=None):
        self.events, self.name = events, name
        self.row, self.rows, self.fail = row, rows, fail
        self.enter_error, self.exit_error = enter_error, exit_error
        self.close_error, self.row_factory_error = close_error, row_factory_error
        self.closed = False
        self.close_calls = 0
        self.statements = []
        self._row_factory = None
    @property
    def row_factory(self): return self._row_factory
    @row_factory.setter
    def row_factory(self, value):
        self.events.append((self.name, 'row_factory'))
        if self.row_factory_error is not None: raise self.row_factory_error
        self._row_factory = value
    def __enter__(self):
        self.events.append((self.name, 'enter'))
        if self.enter_error is not None: raise self.enter_error
        return self
    def __exit__(self, kind, error, trace):
        self.events.append((self.name, 'rollback' if kind is not None else 'commit'))
        if self.exit_error is not None: raise self.exit_error
        return False
    def execute(self, sql, parameters=()):
        self.events.append((self.name, 'execute', sql))
        self.statements.append((sql, parameters))
        if self.fail is not None:
            prefix, error = self.fail
            if sql.startswith(prefix): raise error
        return _Cursor(self.row, self.rows)
    def executescript(self, sql):
        self.events.append((self.name, 'executescript'))
        self.statements.append((sql, ()))
        if self.fail is not None: raise self.fail[1]
    def close(self):
        self.events.append((self.name, 'close'))
        self.close_calls += 1
        if self.close_error is not None: raise self.close_error
        self.closed = True


class _Retention:
    def __init__(self, events): self.events = events
    def lock(self): return nullcontext()
    def release_paths(self, release):
        self.events.append(('retention', 'release_paths', release))
        return ['owned-package'] if release else []
    def pin(self, *args, **kwargs): self.events.append(('retention', 'pin', args, kwargs))
    def unpin(self, *args): self.events.append(('retention', 'unpin', args))


class LedgerConnectionLifetime(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.retention = _Retention(self.events)
        self.fake_sqlite = types.SimpleNamespace(Row=object(), connect=self.forbidden_connect)
        self.wal = lambda conn, timeout: self.events.append((conn.name, 'wal', timeout))
        self.ns = {'Path': _Path, 'sqlite3': self.fake_sqlite, 'use_wal': self.call_wal,
                   'closing': closing, 'json': json, 'time': types.SimpleNamespace(time=lambda: 17.0),
                   'uuid': types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(hex='deployment-one')),
                   'runtime_state_lock': lambda directory: nullcontext()}
        source = ast.parse(SOURCE.read_text())
        ledger = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'DeploymentLedger')
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ledger], type_ignores=[])),
                     str(SOURCE) + ':exact-class-only', 'exec'), self.ns)
        self.Ledger = self.ns['DeploymentLedger']
        retention_module = types.ModuleType('backend.engine.artifact_retention')
        retention_module.ArtifactRetention = lambda root: self.retention
        retention_module.retention_project_root = lambda directory: _Path('/owned')
        self.old_modules = {key: sys.modules.get(key) for key in
                            ('backend', 'backend.engine', 'backend.engine.artifact_retention')}
        for key in ('backend', 'backend.engine'):
            module = types.ModuleType(key); module.__path__ = []; sys.modules[key] = module
        sys.modules['backend.engine.artifact_retention'] = retention_module
    def tearDown(self):
        for key, original in self.old_modules.items():
            if original is None: sys.modules.pop(key, None)
            else: sys.modules[key] = original
    @staticmethod
    def forbidden_connect(*args, **kwargs): raise AssertionError('real connection is forbidden')
    def call_wal(self, conn, timeout): return self.wal(conn, timeout)
    def ledger(self, connections):
        ledger = object.__new__(self.Ledger)
        ledger.directory = _Path(); ledger.project_dir = _Path('/owned')
        ledger.path = _Path('/owned/runtime_service/runtime_deployments.sqlite3')
        ledger.pin_owner = 'derived:active-release:runtime_service'
        queue = iter(connections)
        def connect():
            for prior in connections:
                if prior.name in self.opened:
                    self.assertTrue(prior.closed, 'previous owned read must close before the next open')
            conn = next(queue); self.opened.append(conn.name)
            self.events.append((conn.name, 'open')); return conn
        self.opened = []
        ledger.connect = connect
        return ledger
    def assert_closed(self, conn, *, rollback=False):
        self.assertTrue(conn.closed); self.assertEqual(conn.close_calls, 1)
        terminal = (conn.name, 'rollback' if rollback else 'commit')
        self.assertLess(self.events.index(terminal), self.events.index((conn.name, 'close')))
    def history_row(self):
        return {'deployment_id': 'deployment-one', 'release': json.dumps({'device': 'cpu'}),
                'ack': json.dumps({'status': 'ready'}), 'reviewer': 'operator',
                'restored_from': None, 'created_at': 17.0}
    def operation(self):
        return {'operation_id': 'operation', 'release': json.dumps({'device': 'cpu'}),
                'previous': None, 'ack': None, 'status': 'applying'}

    def test_constructor_transaction_closes(self):
        conn = _Connection(self.events)
        self.fake_sqlite.connect = lambda path, timeout: conn
        ledger = self.Ledger(_Path(), project_dir=_Path('/owned'))
        self.assertIs(ledger.path.__class__, _Path); self.assert_closed(conn)
        self.assertEqual(len(conn.statements), 2)  # original synchronous PRAGMA and exact schema

    def test_constructor_failure_rolls_back_before_close(self):
        error = RuntimeError('original schema failure')
        conn = _Connection(self.events, fail=('schema', error))
        self.fake_sqlite.connect = lambda path, timeout: conn
        with self.assertRaises(RuntimeError) as raised: self.Ledger(_Path(), project_dir=_Path('/owned'))
        self.assertIs(raised.exception, error); self.assert_closed(conn, rollback=True)

    def test_history_returns_complete_rows_after_close(self):
        conn = _Connection(self.events, rows=[self.history_row()]); ledger = self.ledger([conn])
        result = ledger.history(); self.assert_closed(conn)
        self.assertEqual(result[0], {**self.history_row(), 'release': {'device': 'cpu'}, 'ack': {'status': 'ready'}})

    def test_active_read_closes_before_its_history_read(self):
        one = _Connection(self.events, 'pointer', row=('deployment-one',))
        two = _Connection(self.events, 'history', rows=[self.history_row()])
        ledger = self.ledger([one, two]); result = ledger.active()
        self.assertEqual(result['deployment_id'], 'deployment-one')
        self.assert_closed(one); self.assert_closed(two)

    def test_diagnostics_closes_all_three_original_reads(self):
        one = _Connection(self.events, 'operations', row=self.operation())
        two = _Connection(self.events, 'pointer', row=('deployment-one',))
        three = _Connection(self.events, 'history', rows=[self.history_row()])
        result = self.ledger([one, two, three]).diagnostics()
        self.assertEqual(result['schema_version'], 1); self.assertEqual(result['pending']['release'], {'device': 'cpu'})
        for conn in (one, two, three): self.assert_closed(conn)

    def test_record_preserves_full_original_parameters_and_closes(self):
        conn = _Connection(self.events); ledger = self.ledger([conn])
        ledger._record('operation', 'rejected', ack={'status': 'ready'}, error='original')
        self.assertEqual(conn.statements[0][1], ('rejected', '{"status": "ready"}', 'original', 17.0, 'operation'))
        self.assert_closed(conn)

    def apply_ledger(self, connections):
        ledger = self.ledger(connections)
        ledger.recover = lambda callback: self.events.append(('ledger', 'recover'))
        ledger.active = lambda: None
        ledger.history = lambda: [{'deployment_id': 'deployment-one'}]
        return ledger
    def test_apply_commits_and_closes_intent_before_runtime_then_completion_before_pin(self):
        one = _Connection(self.events, 'intent'); two = _Connection(self.events, 'completion')
        ledger = self.apply_ledger([one, two]); release = {'device': 'cpu', 'manifest_sha256': 'a' * 64}
        def runtime(value):
            self.assert_closed(one); self.assertEqual(two.close_calls, 0)
            self.events.append(('runtime', 'apply')); return {'status': 'ready', **value}
        result = ledger.apply(release, runtime, reviewer='operator')
        self.assertEqual(result, {'deployment_id': 'deployment-one'})
        self.assert_closed(one); self.assert_closed(two)
        self.assertEqual(two.statements[0], ('BEGIN IMMEDIATE', ()))
        self.assertEqual(len(two.statements), 4)
        pins = [i for i, event in enumerate(self.events) if event[:2] == ('retention', 'pin')]
        self.assertLess(self.events.index(('completion', 'close')), pins[-1])

    def test_intent_failure_prevents_runtime_and_closes(self):
        error = RuntimeError('intent body failed'); conn = _Connection(self.events, fail=('INSERT INTO update_operations', error))
        ledger = self.apply_ledger([conn]); calls = []
        with self.assertRaises(RuntimeError) as raised: ledger.apply({'device': 'cpu'}, lambda value: calls.append(value), reviewer='operator')
        self.assertIs(raised.exception, error); self.assertEqual(calls, []); self.assert_closed(conn, rollback=True)

    def test_completion_failure_rolls_back_closes_then_records_rejection(self):
        error = RuntimeError('completion failed')
        one = _Connection(self.events, 'intent')
        two = _Connection(self.events, 'completion', fail=('INSERT INTO active', error))
        three = _Connection(self.events, 'rejection')
        ledger = self.apply_ledger([one, two, three])
        with self.assertRaises(RuntimeError) as raised:
            ledger.apply({'device': 'cpu'}, lambda value: {'status': 'ready', **value}, reviewer='operator')
        self.assertIs(raised.exception, error)
        self.assert_closed(one); self.assert_closed(two, rollback=True); self.assert_closed(three)
        self.assertEqual(three.statements[0][1][0], 'rejected')

    def test_wrong_ack_never_starts_completion_and_records_rejection(self):
        one = _Connection(self.events, 'intent'); two = _Connection(self.events, 'rejection')
        ledger = self.apply_ledger([one, two])
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            ledger.apply({'device': 'cpu'}, lambda value: {'status': 'ready', 'device': 'foreign'}, reviewer='operator')
        self.assert_closed(one); self.assert_closed(two)
        self.assertFalse(any(sql == 'BEGIN IMMEDIATE' for conn in (one, two) for sql, _ in conn.statements))

    def test_read_failure_or_cancellation_rolls_back_before_close(self):
        for error in (RuntimeError('read failed'), KeyboardInterrupt('cancelled')):
            with self.subTest(error=type(error).__name__):
                self.events.clear(); conn = _Connection(self.events, fail=('SELECT', error)); ledger = self.ledger([conn])
                with self.assertRaises(type(error)) as raised: ledger.history()
                self.assertIs(raised.exception, error); self.assert_closed(conn, rollback=True)

    def test_enter_failure_still_closes_once(self):
        error = RuntimeError('transaction entry failed'); conn = _Connection(self.events, enter_error=error)
        with self.assertRaises(RuntimeError) as raised: self.ledger([conn]).history()
        self.assertIs(raised.exception, error); self.assertTrue(conn.closed); self.assertEqual(conn.close_calls, 1)
        self.assertNotIn((conn.name, 'commit'), self.events)

    def test_transaction_exit_failure_still_closes_once(self):
        error = RuntimeError('commit failed'); conn = _Connection(self.events, exit_error=error)
        with self.assertRaises(RuntimeError) as raised: self.ledger([conn]).history()
        self.assertIs(raised.exception, error); self.assert_closed(conn)

    def test_close_failure_is_propagated_and_never_retried(self):
        error = RuntimeError('close failed'); conn = _Connection(self.events, close_error=error)
        with self.assertRaises(RuntimeError) as raised: self.ledger([conn]).history()
        self.assertIs(raised.exception, error); self.assertEqual(conn.close_calls, 1)
        self.assertLess(self.events.index((conn.name, 'commit')), self.events.index((conn.name, 'close')))

    def test_connect_success_returns_same_open_connection_for_caller_ownership(self):
        conn = _Connection(self.events); calls = []
        self.fake_sqlite.connect = lambda path, timeout: (calls.append((path, timeout)), conn)[1]
        ledger = object.__new__(self.Ledger); ledger.path = _Path('/owned/db')
        result = ledger.connect()
        self.assertIs(result, conn); self.assertFalse(conn.closed); self.assertEqual(conn.close_calls, 0)
        self.assertEqual(calls, [(ledger.path, 30)]); self.assertIs(conn.row_factory, self.fake_sqlite.Row)
        self.assertEqual(conn.statements, [('PRAGMA synchronous=FULL', ())])
        self.assertIn((conn.name, 'wal', 30), self.events)

    def test_setup_errors_close_and_preserve_original_exception(self):
        for where in ('row_factory', 'wal', 'synchronous'):
            for close_fails in (False, True):
                with self.subTest(where=where, close_fails=close_fails):
                    self.events.clear(); error = KeyboardInterrupt('original ' + where)
                    conn = _Connection(self.events,
                        row_factory_error=error if where == 'row_factory' else None,
                        fail=('PRAGMA synchronous', error) if where == 'synchronous' else None,
                        close_error=RuntimeError('close failed') if close_fails else None)
                    self.fake_sqlite.connect = lambda path, timeout: conn
                    self.wal = (lambda connection, timeout: (_ for _ in ()).throw(error)) if where == 'wal' else lambda connection, timeout: None
                    ledger = object.__new__(self.Ledger); ledger.path = _Path('/owned/db')
                    with self.assertRaises(KeyboardInterrupt) as raised: ledger.connect()
                    self.assertIs(raised.exception, error); self.assertEqual(conn.close_calls, 1)
                    if not close_fails: self.assertTrue(conn.closed)
                    self.assertFalse(any(event[1] in ('enter', 'commit', 'rollback') for event in self.events))

    def test_connect_constructor_error_has_no_connection_to_close(self):
        error = RuntimeError('no connection created'); calls = []
        def rejected(path, timeout): calls.append((path, timeout)); raise error
        self.fake_sqlite.connect = rejected
        ledger = object.__new__(self.Ledger); ledger.path = _Path('/owned/db')
        with self.assertRaises(RuntimeError) as raised: ledger.connect()
        self.assertIs(raised.exception, error); self.assertEqual(len(calls), 1); self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
