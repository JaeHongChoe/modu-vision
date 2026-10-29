"""Real isolated CPU daemon/outbox recovery; no training or target approval.

The receiver commits one business result, withholds its first HTTP ACK, and
observes an identical replay after the *owned daemon* is killed/restarted.
This is controlled process interruption, never physical power-loss evidence.
"""
import asyncio
import errno
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from functools import wraps
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import threading
import time
from types import SimpleNamespace
import uuid

import httpx
import pytest
from PIL import Image
import psutil

from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.engine import runtime_deadline
from backend.engine.runtime_process_control import process_identity, owned_inspection_process, _runtime_spawn_diagnostic
from backend.engine.service_bootstrap import runtime_command, runtime_cwd
from backend.tests.runtime_release_fixture import real_classification_checkpoints


_identity_now = time.monotonic
_IDENTITY_NOTE = 'Original daemon identity failure observations: '



class _OwnedDaemonTrace:
    """Bounded stored facts for exactly the first two original daemon handles."""
    _PHASES = ('init', 'leader_matches', 'observe')
    _FLAGS = ('group_present', 'session_present', 'root_unknown', 'command_unavailable',
              'pending_present', 'pending_empty', 'admission_refused')
    _COUNTS = ('sequence', 'leader_argc', 'registered_count', 'unknown_count', 'remaining_count')

    def __init__(self, handles):
        self.handles, self._records = handles, {}

    def _index(self, process):
        return next((index for index, original in enumerate(self.handles[:2]) if process is original), None)

    @staticmethod
    def _integer(value, *, signed=False):
        lower = -(1 << 31) if signed else 0
        return value if type(value) is int and lower <= value < (1 << 31) else None

    @classmethod
    def _safe_row(cls, row):
        if type(row) is not dict:
            return None
        leader = row.get('leader_state')
        value = {'phase': row.get('phase') if row.get('phase') in cls._PHASES else 'unavailable',
                 'leader_state': leader if leader in ('none', 'empty', 'nonempty', 'unavailable') else 'unavailable',
                 'returncode': cls._integer(row.get('returncode'), signed=True)}
        value.update({key: cls._integer(row.get(key)) for key in cls._COUNTS})
        value.update({key: row.get(key) if type(row.get(key)) is bool else None for key in cls._FLAGS})
        return value

    @classmethod
    def sanitize(cls, stored):
        source = stored if type(stored) is dict else {}
        initial = cls._safe_row(source.get('initial'))
        classification = 'unclassified'
        if initial and initial['leader_state'] == 'empty':
            classification = 'initial_empty_command_observed'
        elif initial and initial['leader_state'] == 'none' and initial['pending_present'] is True:
            classification = 'initial_pending_anchor_observed'
        calls = source.get('calls');calls = calls if type(calls) is dict else {}
        last = source.get('last');last = last if type(last) is list else []
        return {'schema': 'modu-vision.daemon-passive-stored-trace/v1',
            'non_atomic': True, 'ownership_verified': False, 'classification': classification,
            'initial': initial, 'first_new_unknown': cls._safe_row(source.get('first_new_unknown')),
            'last': [cls._safe_row(row) for row in last[-8:]],
            'calls': {phase: cls._integer(calls.get(phase)) for phase in cls._PHASES}}

    def capture(self, owner, phase):
        data = vars(owner)
        process = data.get('process')
        index = self._index(process)
        if index is None:
            return
        process_data = vars(process)
        ledger = self._records.setdefault(index, {'initial': None, 'first_new_unknown': None,
            'last': [], 'calls': {name: 0 for name in self._PHASES}, 'previous_unknown': None, 'sequence': 0})
        ledger['sequence'] += 1
        ledger['calls'][phase] += 1
        leader = data.get('leader');argc = None
        state = 'none' if leader is None else 'unavailable'
        if type(leader) in (tuple, list) and len(leader) == 2 and type(leader[1]) in (tuple, list):
            argc = len(leader[1]);state = 'nonempty' if argc else 'empty'
        unknown = data.get('unknown');pid = process_data.get('pid')
        count = lambda value, types: len(value) if type(value) in types else None
        row = self._safe_row({'phase': phase, 'sequence': ledger['sequence'],
            'leader_state': state, 'leader_argc': argc, 'returncode': process_data.get('returncode'),
            'group_present': data.get('group') is not None, 'session_present': data.get('session') is not None,
            'registered_count': count(data.get('members'), (dict,)),
            'unknown_count': count(unknown, (set, frozenset)),
            'remaining_count': count(data.get('remaining'), (set, frozenset)),
            'root_unknown': pid in unknown if type(pid) is int and type(unknown) in (set, frozenset) else None,
            'command_unavailable': data.get('_leader_command_unavailable'),
            'pending_present': data['_leader_pending'] is not None if '_leader_pending' in data else None,
            'pending_empty': data.get('_leader_pending_empty'),
            'admission_refused': data.get('_leader_admission_refused')})
        if phase == 'init' and ledger['initial'] is None:
            ledger['initial'] = row
        previous = ledger['previous_unknown']
        if (ledger['first_new_unknown'] is None and type(previous) is int
                and type(row['unknown_count']) is int and row['unknown_count'] > previous):
            ledger['first_new_unknown'] = row
        ledger['previous_unknown'] = row['unknown_count']
        ledger['last'] = (ledger['last'] + [row])[-8:]

    def wrap(self, original, phase):
        @wraps(original)
        def observed(owner, *args, **kwargs):
            try:
                return original(owner, *args, **kwargs)
            finally:
                try:self.capture(owner, phase)
                except BaseException:pass  # A diagnostic must not replace result/exception.
        return observed

    def for_handle(self, process):
        index = self._index(process)
        return self.sanitize(self._records[index]) if index in self._records else None


def _owned_daemon_outcome_note(outcome, passive_trace=None):
    """Failure-only stored observations, never new OS reads or raw private bytes."""
    try:
        source = outcome if type(outcome) is dict else {}
        ownership = source.get('ownership')
        ownership = ownership if type(ownership) is dict else {}
        def integer(value):
            return value if type(value) is int and -(1 << 31) <= value < (1 << 31) else None
        def pids(value):
            return [pid for pid in value[:8] if type(pid) is int and 0 < pid < (1 << 31)] if type(value) is list else []
        registered = ownership.get('registered_members')
        registered = registered if type(registered) is list else []
        note = {'schema': 'modu-vision.daemon-outcome-failure-summary/v1',
            'non_atomic': True, 'ownership_verified': False,
            'status': source.get('status') if source.get('status') in ('completed', 'uncertain', 'timeout', 'cancelled') else 'unavailable',
            'returncode': integer(source.get('returncode')),
            'leader_returncode': integer(source.get('leader_returncode')),
            'pid': integer(source.get('pid')),
            'registered_member_count': len(registered),
            'registered_member_pids': pids([row.get('pid') for row in registered[:8] if type(row) is dict]),
            'unknown_members': pids(ownership.get('unknown_members')),
            'remaining_members': pids(ownership.get('remaining_members')),
            'ownership_flags': {key: ownership.get(key) if type(ownership.get(key)) is bool else None
                for key in ('leader_exit_confirmed', 'observation_failed', 'group_exit_verified',
                            'process_tree_exit_verified', 'recovery_required', 'termination_attempted',
                            'termination_failed')}}
        if passive_trace is not None:
            note['passive_trace'] = _OwnedDaemonTrace.sanitize(passive_trace)
        return 'Original owned daemon outcome (no new authority): ' + json.dumps(note, allow_nan=False, separators=(',', ':'))
    except BaseException:
        return 'Original owned daemon outcome summary unavailable; original assertion retained'


def test_owned_daemon_outcome_failure_summary_is_bounded_and_redacts_private_bytes():
    outcome = {'status': 'uncertain', 'returncode': 1, 'leader_returncode': 0, 'pid': 733,
        'stdout': 'PRIVATE-STDOUT', 'stderr': 'PRIVATE-STDERR',
        'private_diagnostics': '/PRIVATE-DIAGNOSTICS',
        'ownership': {'registered_members': [{'pid': 800 + index, 'birth': 42.5,
            'command': ['PRIVATE-TOKEN', '/PRIVATE-PATH']} for index in range(100)],
            'unknown_members': list(range(900, 1000)), 'remaining_members': list(range(1000, 1100)),
            'observation_failed': False, 'leader_exit_confirmed': True}}
    note = _owned_daemon_outcome_note(outcome)
    value = json.loads(note.split(': ', 1)[1])
    assert len(note.encode()) < 4096 and 'PRIVATE-' not in note
    assert value['non_atomic'] is True and value['ownership_verified'] is False
    assert value['status'] == 'uncertain' and value['returncode'] == 1 and value['leader_returncode'] == 0
    assert value['registered_member_count'] == 100
    assert value['registered_member_pids'] == list(range(800, 808))
    assert value['unknown_members'] == list(range(900, 908))
    assert value['remaining_members'] == list(range(1000, 1008))



class _StoredDaemonProcess:
    def __init__(self, pid):
        self.pid, self.returncode = pid, None
        self.args = ['PRIVATE-TOKEN', '/PRIVATE-PATH']

    def poll(self):
        raise AssertionError('Passive diagnostics may not poll')

    def status(self):
        raise AssertionError('Passive diagnostics may not query status')

    def cmdline(self):
        raise AssertionError('Passive diagnostics may not query command')

    def create_time(self):
        raise AssertionError('Passive diagnostics may not query birth')


def _stored_daemon_owner(process, leader):
    return SimpleNamespace(process=process, leader=leader, group=process.pid, session=process.pid,
        members={}, unknown=set(), remaining=set(), _leader_command_unavailable=False,
        _leader_pending=None, _leader_pending_empty=False, _leader_admission_refused=False)


def test_passive_daemon_trace_calls_original_once_returns_exact_values_and_ignores_other_handles(monkeypatch):
    factory = globals().get('_OwnedDaemonTrace')
    assert factory is not None, 'Bounded stored-state passive trace is missing'
    first, second, impostor = (_StoredDaemonProcess(733), _StoredDaemonProcess(734), _StoredDaemonProcess(733))
    handles = [first, second, impostor]
    trace, calls, traps = factory(handles), [], []
    leader_result, observe_result = object(), object()

    def forbidden(*_args, **_kwargs):
        traps.append(True)
        raise AssertionError('No new OS observation is permitted')

    monkeypatch.setattr(psutil, 'Process', forbidden)
    monkeypatch.setattr(runtime_deadline, '_group_members', forbidden)

    class Group:
        def __init__(self, process, module):
            calls.append(('init', process))
            self.__dict__.update(vars(_stored_daemon_owner(process, (42.5, ['PRIVATE-TOKEN']))))

        def leader_matches(self):
            calls.append(('leader_matches', self.process))
            return leader_result

        def observe(self):
            calls.append(('observe', self.process))
            self.unknown.add(self.process.pid)
            return observe_result

    for method, phase in (('__init__', 'init'), ('leader_matches', 'leader_matches'), ('observe', 'observe')):
        monkeypatch.setattr(Group, method, trace.wrap(getattr(Group, method), phase))
    owner, other = Group(first, None), Group(impostor, None)
    assert owner.leader_matches() is leader_result and owner.observe() is observe_result
    assert other.leader_matches() is leader_result and other.observe() is observe_result
    assert calls == [('init', first), ('init', impostor), ('leader_matches', first), ('observe', first),
                     ('leader_matches', impostor), ('observe', impostor)]
    assert owner.unknown == {733} and other.unknown == {733} and traps == []
    stored = trace.for_handle(first)
    assert stored['initial']['leader_state'] == 'nonempty' and stored['initial']['leader_argc'] == 1
    assert stored['first_new_unknown']['root_unknown'] is True
    assert stored['calls'] == {'init': 1, 'leader_matches': 1, 'observe': 1}
    assert trace.for_handle(second) is None and trace.for_handle(impostor) is None


def test_passive_daemon_trace_retains_initial_first_unknown_last_eight_and_no_private_values():
    factory = globals().get('_OwnedDaemonTrace')
    assert factory is not None, 'Bounded stored-state passive trace is missing'
    process = _StoredDaemonProcess(733)
    trace = factory([process])
    owner = _stored_daemon_owner(process, (42.5, []))
    original = lambda _owner: None
    trace.wrap(original, 'init')(owner)
    owner.leader = (42.5, ['PRIVATE-TOKEN', '/PRIVATE-PATH'])
    owner.members = {str(i): {'command': ['PRIVATE-MEMBER']} for i in range(1000)}
    for index in range(100):
        owner.unknown.add(733 + index)
        owner.remaining = set(range(index))
        trace.wrap(original, 'observe')(owner)
    process.returncode = 0
    trace.wrap(original, 'leader_matches')(owner)
    stored = trace.for_handle(process)
    assert stored['initial']['leader_state'] == 'empty' and stored['initial']['leader_argc'] == 0
    assert stored['first_new_unknown']['unknown_count'] == 1
    assert stored['first_new_unknown']['registered_count'] == 1000
    assert len(stored['last']) == 8 and stored['last'][-1]['returncode'] == 0
    assert stored['last'][-1]['unknown_count'] == 100 and stored['calls']['observe'] == 100
    assert stored['classification'] == 'initial_empty_command_observed'
    outcome = {'status': 'uncertain', 'returncode': 1, 'leader_returncode': 0, 'pid': 733,
               'ownership': {'registered_members': [], 'unknown_members': [733]}}
    stored['PRIVATE-FIELD'] = '/PRIVATE-PATH'
    stored['last'].append({'phase': 'PRIVATE-TOKEN', 'leader_state': '/PRIVATE-PATH'})
    note = _owned_daemon_outcome_note(outcome, stored)
    assert len(note.encode()) < 8192 and 'PRIVATE-' not in note
    value = json.loads(note.split(': ', 1)[1])
    assert value['passive_trace']['non_atomic'] is True and value['passive_trace']['ownership_verified'] is False
    assert len(value['passive_trace']['last']) <= 8
    assert trace.for_handle(process)['initial']['leader_state'] == 'empty'


@pytest.mark.parametrize('initial,pending,expected', [
    (None, False, 'unclassified'),
    ((42.5, ['PRIVATE-TOKEN']), False, 'unclassified'),
    (None, True, 'initial_pending_anchor_observed'),
])
def test_passive_daemon_trace_classifies_only_initial_stored_facts(initial, pending, expected):
    factory = globals().get('_OwnedDaemonTrace')
    assert factory is not None, 'Bounded stored-state passive trace is missing'
    process = _StoredDaemonProcess(733)
    trace = factory([process])
    owner = _stored_daemon_owner(process, initial)
    if pending:
        owner._leader_pending = (42.5, ('PRIVATE-TOKEN',), 123, process.pid)
    trace.wrap(lambda _owner: None, 'init')(owner)
    owner.leader = (42.5, [])  # Later empty metadata cannot rewrite the initial fact.
    owner.unknown.add(process.pid)
    owner._leader_pending_empty = True
    owner._leader_admission_refused = True
    trace.wrap(lambda _owner: False, 'observe')(owner)
    stored = trace.for_handle(process)
    assert stored['classification'] == expected
    assert stored['last'][-1]['pending_empty'] is True and stored['last'][-1]['admission_refused'] is True
    assert stored['initial']['pending_present'] is pending


def test_passive_daemon_trace_diagnostic_failure_does_not_replace_original_result_or_exception(monkeypatch):
    factory = globals().get('_OwnedDaemonTrace')
    assert factory is not None, 'Bounded stored-state passive trace is missing'
    process = _StoredDaemonProcess(733)
    owner = _stored_daemon_owner(process, None)
    trace = factory([process])
    result, original_error, calls = object(), ValueError('synthetic original error'), []

    def failed_capture(*_args):
        raise AssertionError('Synthetic diagnostic failure')

    def original(_owner):
        calls.append(True)
        return result

    def raising(_owner):
        calls.append(False)
        raise original_error

    monkeypatch.setattr(trace, 'capture', failed_capture)
    assert trace.wrap(original, 'observe')(owner) is result
    with pytest.raises(ValueError) as caught:
        trace.wrap(raising, 'observe')(owner)
    assert caught.value is original_error and calls == [True, False]


def _identity_for_daemon_test(process, state, case_deadline):
    try:
        return process_identity(process, state)
    except ValueError as original:
        try:
            _add_identity_failure_note(process, state, case_deadline, original)
        except BaseException:
            pass  # Even a failed observer must preserve the original refusal.
        raise


def _add_identity_failure_note(process, state, case_deadline, original):
    """At most four failure-only observations; no sleep, retry or authority."""
    started = _identity_now()
    stop = min(case_deadline, started + .2)
    note = {'schema': 'modu-vision.daemon-identity-failure-observations/v1',
            'non_atomic': True, 'ownership_verified': False,
            'sample_budget_ms': 200, 'samples': []}
    baseline = getattr(original, 'runtime_spawn_diagnostic', {}).get('process', {}).get('birth')
    if type(baseline) not in (int, float) or not 0 < baseline < float('inf'):
        note['unavailable'] = 'original_birth_unavailable'
    else:
        def within(read):
            if _identity_now() >= stop:
                raise psutil.TimeoutExpired(.2)
            return read()
        for _ in range(4):
            if _identity_now() >= stop:
                break
            sample = {'at_monotonic': _identity_now()}
            note['samples'].append(sample)
            try:
                polled = within(process.poll)
                if polled is not None:
                    sample['original_poll_exit_code'] = polled if type(polled) is int else 'unavailable'
                    break
                owner = within(lambda: psutil.Process(process.pid))
                birth = within(owner.create_time)
                if birth != baseline:
                    sample['unavailable'] = 'original_birth_changed'
                    break
                arguments = within(owner.cmdline)
                # The production projection hashes command vectors and maps
                # observation exceptions to fixed reason codes. Proxy methods
                # check this frame before each additional OS read.
                bounded_owner = SimpleNamespace(
                    create_time=lambda: within(owner.create_time),
                    status=lambda: within(owner.status))
                bounded_process = SimpleNamespace(
                    pid=process.pid, args=process.args, poll=lambda: within(process.poll))
                observed = _runtime_spawn_diagnostic(bounded_process, bounded_owner, arguments, state)
                for key in ('expected', 'observed'):
                    sample[key] = {name: item for name, item in observed[key].items()
                                   if name in ('argc', 'sha256', 'entry', 'state_flag_count',
                                               'state_matches', 'unavailable')}
                sample['process'] = observed['process']
            except (psutil.Error, OSError) as error:
                sample['unavailable'] = ('deadline' if isinstance(error, psutil.TimeoutExpired)
                                         else 'process_observation_unavailable')
                break
    note['sampling_budget_exhausted'] = _identity_now() >= stop
    encoded = _IDENTITY_NOTE + json.dumps(note, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    if len(encoded.encode('utf-8')) <= 4096:
        original.add_note(encoded)


@pytest.fixture
def identity_observation_case(monkeypatch, tmp_path):
    counts = {'process': 0, 'birth': 0, 'cmdline': 0, 'status': 0, 'poll': 0}
    state = tmp_path / 'PRIVATE-STATE'
    command = ['python', '-m', 'backend.engine.inspection_service', '--state-dir', str(state),
               '--token-file', 'PRIVATE-TOKEN-PATH', '--package', 'PRIVATE-MODEL-PATH']
    value = {'command': command, 'birth': 42.5, 'status': 'running', 'poll': None, 'now': 0.0}
    error = ValueError('Spawned runtime command differs from its owned state')
    error.runtime_spawn_diagnostic = {'process': {'birth': 42.5}}
    error.add_note('Original diagnostic note retained')
    def read(key):
        counts[key] += 1
        return value[{'cmdline': 'command'}.get(key, key)]
    owner = SimpleNamespace(pid=733, create_time=lambda: read('birth'),
                            cmdline=lambda: read('cmdline'), status=lambda: read('status'))
    process = SimpleNamespace(pid=733, args=command, poll=lambda: read('poll'))
    def lookup(pid):
        counts['process'] += 1
        assert pid == process.pid
        return owner
    def refuse(*args):
        raise error
    monkeypatch.setitem(globals(), 'process_identity', refuse)
    # Keep doubles in this test module; other CI workers can be observing their
    # own real processes at the same time.
    monkeypatch.setitem(globals(), 'psutil', SimpleNamespace(
        Process=lookup, Error=psutil.Error, TimeoutExpired=psutil.TimeoutExpired))
    monkeypatch.setitem(globals(), '_identity_now', lambda: value['now'])
    return SimpleNamespace(process=process, owner=owner, state=state, error=error,
                           value=value, counts=counts)


def _added_identity_note(error):
    notes = [n for n in error.__notes__ if n.startswith(_IDENTITY_NOTE)]
    assert len(notes) == 1
    assert len(notes[0].encode('utf-8')) <= 4096
    return json.loads(notes[0][len(_IDENTITY_NOTE):])


def test_identity_failure_observation_preserves_refusal_and_redacts_argv(identity_observation_case):
    o = identity_observation_case
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 10.0)
    assert caught.value is o.error and o.error.__notes__[0] == 'Original diagnostic note retained'
    note = _added_identity_note(o.error)
    assert note['non_atomic'] is True and note['ownership_verified'] is False
    assert 1 <= len(note['samples']) <= 4
    retained = json.dumps(note)
    for private in [str(o.state), 'PRIVATE-TOKEN-PATH', 'PRIVATE-MODEL-PATH']:
        assert private not in retained
    assert o.counts['cmdline'] <= 4


def test_identity_failure_observation_expired_case_budget_reads_no_process(identity_observation_case):
    o = identity_observation_case
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 0.0)
    assert caught.value is o.error
    assert _added_identity_note(o.error)['samples'] == []
    assert not any(o.counts.values())


@pytest.mark.parametrize('case_deadline,crossed', [(10.0, .201), (.05, .051)])
def test_identity_failure_observation_stops_new_reads_at_its_frame(identity_observation_case, case_deadline, crossed):
    o = identity_observation_case
    def command_crosses_budget():
        o.counts['cmdline'] += 1
        o.value['now'] = crossed
        return o.value['command']
    o.owner.cmdline = command_crosses_budget
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, case_deadline)
    assert caught.value is o.error
    note = _added_identity_note(o.error)
    assert note['sampling_budget_exhausted'] is True and len(note['samples']) == 1
    assert o.counts == {'process': 1, 'birth': 1, 'cmdline': 1, 'status': 0, 'poll': 1}


def test_identity_failure_observation_unknown_original_birth_reads_no_process(identity_observation_case):
    o = identity_observation_case
    o.error.runtime_spawn_diagnostic = {'process': {'birth': {'unavailable': 'AccessDenied'}}}
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 10.0)
    assert caught.value is o.error
    note = _added_identity_note(o.error)
    assert note['samples'] == [] and note['unavailable'] == 'original_birth_unavailable'
    assert not any(o.counts.values())


def test_identity_failure_observation_large_vectors_remain_bounded(identity_observation_case):
    o = identity_observation_case
    o.process.args = ['PRIVATE-ARGV'] * 65
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 10.0)
    assert caught.value is o.error
    note = _added_identity_note(o.error)
    assert note['samples'][0]['expected'] == {'unavailable': 'command_bounds', 'argc': 65}
    assert 'PRIVATE' not in json.dumps(note)


@pytest.mark.parametrize('boundary', ['exit', 'birth'])
def test_identity_failure_observation_stops_before_reading_another_command(identity_observation_case, boundary):
    o = identity_observation_case
    o.value['poll' if boundary == 'exit' else 'birth'] = 0 if boundary == 'exit' else 43.0
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 10.0)
    assert caught.value is o.error
    assert len(_added_identity_note(o.error)['samples']) == 1
    assert o.counts['cmdline'] == 0


def test_identity_failure_observation_failure_cannot_replace_original_error(identity_observation_case, monkeypatch):
    o = identity_observation_case
    def unavailable(*args):
        raise SystemExit('PRIVATE-OBSERVER-ERROR')
    monkeypatch.setitem(globals(), '_runtime_spawn_diagnostic', unavailable)
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 10.0)
    assert caught.value is o.error
    assert o.error.__notes__ == ['Original diagnostic note retained']


def test_identity_failure_observation_never_reads_on_success(identity_observation_case, monkeypatch):
    o = identity_observation_case
    identity = {'pid': 733, 'process_created_at': 42.5, 'process_command_sha256': '0' * 64}
    monkeypatch.setitem(globals(), 'process_identity', lambda *_: identity)
    assert _identity_for_daemon_test(o.process, o.state, 10.0) is identity
    assert not any(o.counts.values())


def _sha(path):
    p = Path(path)
    assert p.is_file() and not p.is_symlink()
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _raw_state(database):
    p = Path(database)
    assert p.is_file() and not p.is_symlink()
    with sqlite3.connect(p.as_uri() + '?mode=ro', uri=True, timeout=1) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        return {name: [dict(row) for row in conn.execute('SELECT * FROM ' + name)]
                for name in ('jobs', 'deliveries', 'inbox_items', 'events')}


def _poll(call, predicate, deadline, message):
    last = None
    while time.monotonic() < deadline:
        last = call()
        if predicate(last):
            return last
        time.sleep(min(.025, max(0, deadline - time.monotonic())))
    raise AssertionError(message)


def _prepare_daemon_command(process, command, deadline):
    """Only empty metadata may wait, inside the original readiness frame.

    This grants no ownership: the original strict identity call follows once.
    OS reads are synchronous; a read crossing the frame must fail when it returns.
    """
    message = 'Original10s command readiness cap expired'

    def read(call):
        assert time.monotonic() < deadline, message
        value = call()
        assert time.monotonic() < deadline, message
        return value

    pid = read(lambda: process.pid)
    assert type(pid) is int and pid > 0
    expected = list(command)
    assert process.args == expected
    parent = read(os.getpid)
    birth = None
    while True:
        assert read(lambda: process.pid) == pid
        assert read(process.poll) is None, 'Original daemon exited before command readiness'
        owner = read(lambda: psutil.Process(pid))
        assert read(lambda: owner.pid) == pid
        observed_birth = read(owner.create_time)
        assert type(observed_birth) in (int, float) and 0 < observed_birth < float('inf')
        if birth is None:
            birth = observed_birth
        assert observed_birth == birth, 'Original daemon birth changed before command readiness'
        assert read(owner.ppid) == parent
        assert read(lambda: os.getpgid(pid)) == pid
        assert read(lambda: os.getsid(pid)) == pid
        assert read(owner.status) not in ('zombie', 'dead')
        arguments = read(owner.cmdline)
        if arguments != []:
            assert arguments == expected, 'Original daemon command differs before identity admission'
            return
        time.sleep(min(.025, max(0, deadline - time.monotonic())))


def _assert_registered_cpu_fixture_boundary(registered, observed, initial_status, *, zombie):
    """Test-only policy; pure records here never establish real OS ownership."""
    assert observed['pid'] == registered['pid'] == observed['handle_pid']
    for key in ('birth', 'parent_pid', 'nonce'):
        assert observed[key] == registered[key]
    assert observed['handle_command'] == registered['command']
    if zombie:
        assert initial_status == observed['status'] == psutil.STATUS_ZOMBIE
        for key in ('pgid', 'sid'):
            if observed[key] is not None:
                assert observed[key] == registered[key]
    else:
        for key in ('pgid', 'sid'):
            assert observed[key] == registered[key]
        for status in (initial_status, observed['status']):
            assert isinstance(status, str) and status and status not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
        assert observed['command'] == registered['command']


def _read_registered_cpu_fixture_scope(read, owner, process, registered, *, zombie, status):
    """A test-only scope read; only actual same-generation zombies may lack it."""
    try:
        return read(owner.pid)
    except ProcessLookupError as error:
        if error.errno != errno.ESRCH or not zombie or status != psutil.STATUS_ZOMBIE:
            raise
        assert process.returncode is None  # No poll/wait: retain the original unreaped handle.
        assert owner.pid == process.pid == registered['pid']
        fresh = psutil.Process(process.pid)
        assert fresh.status() == psutil.STATUS_ZOMBIE
        assert fresh.create_time() == registered['birth']
        return None  # Current terminal scope is unavailable, not the stored live value.


@pytest.fixture
def registered_cpu_boundary_records():
    """Synthetic records solely for the fixture guard's deterministic policy tests."""
    registered = {'pid': 40123, 'birth': 1234.5, 'parent_pid': 201,
                  'pgid': 40123, 'sid': 40123, 'nonce': 'controlled-fixture-nonce',
                  'command': ('python', 'controlled-fixture.py')}
    observed = {**registered, 'handle_pid': 40123,
                'handle_command': registered['command'], 'status': psutil.STATUS_SLEEPING}
    return registered, observed


@pytest.mark.parametrize('zombie,initial_status,status', [
    (False, psutil.STATUS_SLEEPING, psutil.STATUS_RUNNING),
    (False, psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING),
    (False, psutil.STATUS_SLEEPING, psutil.STATUS_SLEEPING),
    (True, psutil.STATUS_ZOMBIE, psutil.STATUS_ZOMBIE),
])
def test_registered_cpu_fixture_boundary_preserves_phase_across_scheduler_states(
        registered_cpu_boundary_records, zombie, initial_status, status):
    registered, observed = registered_cpu_boundary_records
    observed = {**observed, 'status': status,
                'command': None if zombie else observed['command']}
    _assert_registered_cpu_fixture_boundary(registered, observed, initial_status, zombie=zombie)


@pytest.mark.parametrize('field,foreign', [
    ('pid', 40124), ('birth', 1235.5), ('parent_pid', 202),
    ('pgid', 40124), ('sid', 40124), ('nonce', 'foreign-fixture'),
    ('handle_pid', 40124), ('handle_command', ('foreign',)), ('command', ('foreign',)),
    ('status', psutil.STATUS_ZOMBIE), ('status', psutil.STATUS_DEAD),
])
def test_registered_cpu_fixture_boundary_refuses_changed_generation_or_live_phase(
        registered_cpu_boundary_records, field, foreign):
    registered, observed = registered_cpu_boundary_records
    with pytest.raises(AssertionError):
        _assert_registered_cpu_fixture_boundary(
            registered, {**observed, field: foreign}, psutil.STATUS_SLEEPING, zombie=False)


def test_registered_cpu_fixture_boundary_refuses_zombie_to_live_transition(registered_cpu_boundary_records):
    registered, observed = registered_cpu_boundary_records
    with pytest.raises(AssertionError):
        _assert_registered_cpu_fixture_boundary(
            registered, {**observed, 'command': None}, psutil.STATUS_ZOMBIE, zombie=True)


@pytest.mark.parametrize('unavailable', [('pgid',), ('sid',), ('pgid', 'sid')])
def test_registered_cpu_fixture_boundary_allows_unavailable_zombie_group_scope(
        registered_cpu_boundary_records, unavailable):
    registered, observed = registered_cpu_boundary_records
    observed = {**observed, 'status': psutil.STATUS_ZOMBIE, 'command': None,
                **{key: None for key in unavailable}}
    _assert_registered_cpu_fixture_boundary(
        registered, observed, psutil.STATUS_ZOMBIE, zombie=True)


@pytest.mark.parametrize('field', ['pgid', 'sid'])
def test_registered_cpu_fixture_boundary_refuses_unavailable_live_group_scope(
        registered_cpu_boundary_records, field):
    registered, observed = registered_cpu_boundary_records
    with pytest.raises(AssertionError):
        _assert_registered_cpu_fixture_boundary(
            registered, {**observed, field: None}, psutil.STATUS_SLEEPING, zombie=False)


@pytest.mark.parametrize('fault,want', [
    ('same_zombie_esrch', None),
    ('live_esrch', ProcessLookupError),
    ('other_lookup_errno', ProcessLookupError),
    ('permission', PermissionError),
    ('zombie_became_live', AssertionError),
    ('changed_birth', AssertionError),
    ('changed_handle', AssertionError),
    ('reaped_handle', AssertionError),
])
def test_registered_cpu_fixture_scope_reader_keeps_synthetic_faults_fail_closed(
        registered_cpu_boundary_records, monkeypatch, fault, want):
    registered, _ = registered_cpu_boundary_records
    owner = SimpleNamespace(pid=registered['pid'])
    process = SimpleNamespace(pid=registered['pid'] + (fault == 'changed_handle'),
                              returncode=0 if fault == 'reaped_handle' else None)
    fresh = SimpleNamespace(status=lambda: psutil.STATUS_RUNNING if fault == 'zombie_became_live' else psutil.STATUS_ZOMBIE,
                            create_time=lambda: registered['birth'] + (fault == 'changed_birth'))
    monkeypatch.setattr(psutil, 'Process', lambda pid: fresh)

    def unavailable(pid):
        assert pid == registered['pid']
        if fault == 'permission':
            raise PermissionError(errno.EACCES, 'controlled scope reader refusal')
        raise ProcessLookupError(errno.ENOENT if fault == 'other_lookup_errno' else errno.ESRCH,
                                 'controlled scope reader boundary')

    def read():
        return _read_registered_cpu_fixture_scope(
            unavailable, owner, process, registered, zombie=fault != 'live_esrch',
            status=psutil.STATUS_SLEEPING if fault == 'live_esrch' else psutil.STATUS_ZOMBIE)

    if want is None:
        assert read() is None
    else:
        with pytest.raises(want):
            read()


@pytest.mark.parametrize('boundary,want', [
    ('registered_zombie_empty_command', 'exited'),
    ('live_original_command', 'live'),
    ('zombie_original_command', 'exited'),
    ('live_empty_command', 'unknown'),
    ('live_changed_command', 'unknown'),
    ('zombie_changed_command', 'unknown'),
    ('zombie_changed_birth', 'unknown'),
])
def test_registered_cpu_member_exit_keeps_live_and_foreign_identity_refusals(tmp_path, monkeypatch, boundary, want):
    """Linux may return [] for a real zombie; empty live/foreign work is unproven."""
    if os.name != 'posix':
        pytest.skip('Original unreaped POSIX child exit boundary')
    from backend.tests.test_runtime_deadline_identity import cooperative_leader
    from backend.tests.test_runtime_deadline_tree import publish_owned_stop

    original_command = psutil.Process.cmdline
    with cooperative_leader(tmp_path / boundary) as fixture:
        process = runtime_deadline.subprocess.Popen(fixture['command'], cwd=fixture['directory'],
            stdin=runtime_deadline.subprocess.DEVNULL, stdout=runtime_deadline.subprocess.DEVNULL,
            stderr=runtime_deadline.subprocess.DEVNULL, start_new_session=True)
        fixture.update(owner=SimpleNamespace(process=process), original_cmdline=original_command)
        end = time.monotonic() + 3
        registration = fixture['directory'] / 'registered.json'
        _poll(registration.exists, bool, end, 'Original CPU child did not register')
        identity = json.loads(registration.read_text())
        original = psutil.Process(process.pid)
        assert identity['nonce'] == fixture['nonce'] and identity['pid'] == process.pid
        assert identity['birth'] == original.create_time()
        assert identity['command'] == original_command(original) == fixture['command']
        assert original.ppid() == os.getpid() and original.status() != psutil.STATUS_ZOMBIE
        assert os.getpgid(process.pid) == os.getsid(process.pid) == process.pid
        registered = {**identity, 'command': tuple(identity['command'])}
        zombie = boundary.startswith(('registered_zombie', 'zombie'))
        if zombie:
            publish_owned_stop(fixture['directory'], fixture['nonce'])
            _poll(original.status, lambda state: state == psutil.STATUS_ZOMBIE, end,
                  'Original cooperative CPU child did not reach unreaped zombie boundary')
            assert process.returncode is None  # Do not reap or substitute its result.
        observed_status = original.status()

        def observe_boundary(owner):
            status = owner.status()
            def scope(read):
                return _read_registered_cpu_fixture_scope(
                    read, owner, process, registered, zombie=zombie, status=status)
            return {'pid': owner.pid, 'birth': owner.create_time(), 'parent_pid': owner.ppid(),
                    'pgid': scope(os.getpgid), 'sid': scope(os.getsid),
                    'nonce': fixture['nonce'], 'handle_pid': process.pid,
                    'handle_command': tuple(process.args), 'status': status,
                    'command': None if zombie else tuple(original_command(owner))}

        _assert_registered_cpu_fixture_boundary(
            registered, observe_boundary(original), observed_status, zombie=zombie)
        if boundary == 'zombie_changed_birth':
            identity = {**identity, 'birth': identity['birth'] - 1}

        def read_command(owner):
            if owner.pid != process.pid:
                return original_command(owner)
            # Preserve the actual OS status/birth/handle. Only model the Linux
            # [] cmdline read; macOS instead raises ZombieProcess here.
            _assert_registered_cpu_fixture_boundary(
                registered, observe_boundary(owner), observed_status, zombie=zombie)
            if 'original_command' in boundary:
                return fixture['command']
            return ['controlled-foreign-command'] if 'changed_command' in boundary else []

        monkeypatch.setattr(psutil.Process, 'cmdline', read_command)
        owner = SimpleNamespace(module=psutil)
        assert runtime_deadline._OwnedGroup.member_state(owner, identity) == want


def test_owned_real_cpu_daemon_restarts_committed_inbox_result_and_replays_outbox_once(tmp_path, monkeypatch):
    if os.name != 'posix':
        pytest.skip('POSIX owned-process interruption; Windows native scope is separately waived')
    # Same absolute 240s case cap as the existing Studio-exit daemon scenario;
    # readiness/join/HTTP stay <=10s, model initialization+inference stays30s.
    case_deadline = time.monotonic() + 240
    root = tmp_path / 'owned-daemon-recovery'
    root.mkdir(mode=0o700)
    epoch = uuid.uuid4().hex
    inbox = root / 'input' / 'inbox'
    inbox.mkdir(parents=True)
    image = inbox / ('part-' + epoch + '.png')
    Image.new('RGB', (8, 8), 'white').save(image)
    image_sha = _sha(image)
    model_root = root / 'models' / 'owned_cpu'
    model_root.mkdir(parents=True)
    checkpoint = model_root / 'best_model.pt'
    import torch
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        real_classification_checkpoints({'job_owned_cpu': checkpoint})
    finally:
        torch.set_num_threads(previous_threads)
    graph = get_single_segmentation_flowchart('job_owned_cpu')
    for node in graph.nodes:
        if node.data.node_type == 'inspection':
            node.data.task = 'classification'
    inspection_ids = {node.id for node in graph.nodes if node.data.node_type == 'inspection'}
    package = build_flow_package(pipeline=graph, checkpoints={'job_owned_cpu': checkpoint},
        output_base_dir=root / 'packages', package_name='owned_daemon_cpu',
        runtime_config={'device': 'cpu', 'cpu_threads': 1, 'deadline_ms': 30_000})
    package_root = Path(package['package_path'])
    package_files = {str(p.relative_to(package_root)): _sha(p)
                     for p in package_root.rglob('*') if p.is_file()}
    checkpoint_sha = _sha(checkpoint)
    source_root = Path(runtime_cwd())
    source_paths = ['backend/engine/inspection_service.py', 'backend/engine/input_adapters.py',
        'backend/engine/flow_package.py', 'backend/engine/flow_package_runtime.py',
        'backend/engine/flowchart_engine.py', 'backend/engine/runtime_deadline.py',
        'backend/engine/runtime_process_control.py', 'backend/engine/runtime_configuration.py',
        'backend/engine/service_runtime.py', 'backend/engine/service_listener_security.py',
        'backend/engine/process_isolation.py', 'backend/tests/runtime_release_fixture.py']
    source_pins = {rel: _sha(source_root / rel) for rel in source_paths}
    token_file = root / 'service-token.txt'
    token = uuid.uuid4().hex + uuid.uuid4().hex
    with token_file.open('x', encoding='ascii') as out:
        out.write(token)
    token_file.chmod(0o600)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        service_port = probe.getsockname()[1]
    state = root / 'state'
    receiver_db = root / 'receiver.sqlite3'
    with sqlite3.connect(receiver_db) as conn:
        conn.executescript('CREATE TABLE accepted(job_id TEXT PRIMARY KEY,payload_json TEXT,payload_sha256 TEXT);'
                           'CREATE TABLE requests(number INTEGER PRIMARY KEY,job_id TEXT,payload_sha256 TEXT);')
    first_committed = threading.Event()
    first_release = threading.Event()
    first_ack_attempted = threading.Event()
    receiver_errors = []
    receiver_lock = threading.Lock()
    route = '/owned-result/' + epoch

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                assert self.path == route
                size = int(self.headers['Content-Length'])
                assert 0 < size <= 1024 * 1024
                self.connection.settimeout(5)
                raw = self.rfile.read(size)
                assert len(raw) == size
                payload = json.loads(raw)
                assert set(payload) == {'job_id', 'model_verdict', 'image_sha256', 'result', 'runtime_identity'}
                assert payload['image_sha256'] == image_sha
                canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
                digest = hashlib.sha256(canonical.encode()).hexdigest()
                with receiver_lock, sqlite3.connect(receiver_db) as conn:
                    prior = conn.execute('SELECT payload_json,payload_sha256 FROM accepted WHERE job_id=?',
                                         (payload['job_id'],)).fetchone()
                    if prior is None:
                        conn.execute('INSERT INTO accepted VALUES(?,?,?)', (payload['job_id'], canonical, digest))
                    else:
                        assert prior == (canonical, digest), 'Receiver refuses a different result for the same job'
                    number = conn.execute('SELECT COUNT(*) FROM requests').fetchone()[0] + 1
                    conn.execute('INSERT INTO requests VALUES(?,?,?)', (number, payload['job_id'], digest))
                    conn.commit()
                if number == 1:
                    first_committed.set()
                    assert first_release.wait(5), 'First ACK barrier expired before owned daemon exit'
                    first_ack_attempted.set()
                assert number in (1, 2), 'Unexpected third replay'
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                # The first connection was owned by the killed daemon. Its
                # post-exit ACK delivery is unknown, never an original ACK claim.
                if not first_release.is_set():
                    receiver_errors.append('connection_closed_before_owned_exit')
            except Exception as exc:
                receiver_errors.append(type(exc).__name__)
                first_committed.set()

    receiver = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    receiver.daemon_threads = False
    server_thread = threading.Thread(target=receiver.serve_forever, name='owned-result-receiver')
    server_thread.start()
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL') if key in os.environ}
    env.update({'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
        'CUDA_VISIBLE_DEVICES': '', 'NVIDIA_VISIBLE_DEVICES': 'none', 'HF_HUB_OFFLINE': '1',
        'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
        'VISION_PACKAGE_PARITY_IDENTITY': '1'})
    for key, rel in {'HOME': 'home', 'USERPROFILE': 'home', 'TMPDIR': 'tmp', 'TMP': 'tmp',
        'TEMP': 'tmp', 'XDG_CACHE_HOME': 'cache', 'HF_HOME': 'cache/hf',
        'TORCH_HOME': 'cache/torch', 'MPLCONFIGDIR': 'cache/matplotlib',
        'NUMBA_CACHE_DIR': 'cache/numba'}.items():
        directory = root / rel
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        env[key] = '1'
    command = runtime_command(['--package', str(package_root), '--state-dir', str(state),
        '--token-file', str(token_file), '--host', '127.0.0.1', '--port', str(service_port),
        '--input-root', str(inbox.parent), '--inbox', str(inbox), '--device', 'cpu',
        '--deadline-ms', '30000', '--max-outstanding', '1', '--max-attempts', '3',
        '--max-queue-age-seconds', '3600', '--result-webhook-url',
        'http://127.0.0.1:' + str(receiver.server_port) + route])
    handles, attempts, outcomes, failures = [], [], [], []
    launch_event = threading.Event()
    original_popen = runtime_deadline.subprocess.Popen

    def observe_real_launch(args, **kwargs):
        process = original_popen(args, **kwargs)
        if args == command:
            handles.append(process)
            launch_event.set()
        # Production ownership census may also invoke its original fixed ps
        # command. Leave every unrelated invocation byte-for-byte unchanged.
        return process

    # Observer only: the original production Popen handle and arguments are
    # returned unchanged. No inference/HTTP/store function is substituted.
    monkeypatch.setattr(runtime_deadline.subprocess, 'Popen', observe_real_launch)
    passive_trace = _OwnedDaemonTrace(handles)
    for method, phase in (('__init__', 'init'), ('leader_matches', 'leader_matches'), ('observe', 'observe')):
        monkeypatch.setattr(runtime_deadline._OwnedGroup, method,
                            passive_trace.wrap(getattr(runtime_deadline._OwnedGroup, method), phase))
    def api(path, post=False, deadline=None, require200=True):
        stop = min(case_deadline - 10, time.monotonic() + 10, deadline if deadline is not None else case_deadline)
        async def request():
            remaining = stop - time.monotonic()
            assert remaining > 0, 'Original absolute HTTP frame expired'
            async with asyncio.timeout(remaining):
                async with httpx.AsyncClient(base_url='http://127.0.0.1:' + str(service_port),
                        headers={'X-Vision-Token': token}, timeout=remaining, trust_env=False) as client:
                    response = await (client.post(path) if post else client.get(path))
                    value = response.json()
                    assert time.monotonic() <= stop, 'Full response body exceeded its original frame'
                    if require200:
                        assert response.status_code == 200
                    return value if require200 else (response.status_code, value)
        return asyncio.run(request())

    def launch():
        launch_event.clear()
        execution = runtime_deadline.CancellableExecution()
        outcomes.append(None)
        index = len(outcomes) - 1
        remaining_ms = min(90_000, int((case_deadline - time.monotonic() - 10) * 1000))
        assert remaining_ms > 0
        def run():
            try:
                with execution.running() as cancel:
                    outcomes[index] = runtime_deadline.execute_owned_process(command, deadline_ms=remaining_ms,
                        cwd=source_root, env=env, cancel_event=cancel)
            except BaseException as exc:
                failures.append(type(exc).__name__)
        thread = threading.Thread(target=run, name='owned-daemon-executor-' + str(index))
        attempts.append((thread, execution))
        thread.start()
        assert launch_event.wait(min(10, max(0, case_deadline - time.monotonic())))
        process = handles[index]
        deadline = min(case_deadline - 10, time.monotonic() + 10)
        _prepare_daemon_command(process, command, deadline)
        identity = _identity_for_daemon_test(process, state, case_deadline)
        owner = owned_inspection_process(identity, state)
        assert owner is not None and owner.pid == process.pid
        assert owner.ppid() == os.getpid() and owner.cmdline() == command
        assert os.getpgid(process.pid) == os.getsid(process.pid) == process.pid
        def ready():
            assert process.poll() is None, 'Original daemon exited before readiness'
            try:
                status, value = api('/v1/readiness', deadline=deadline, require200=False)
                return value if status == 200 else None
            except httpx.TransportError:
                return None
        _poll(ready, lambda x: isinstance(x, dict) and x['status'] == 'ready' and x['worker_started'],
              deadline, 'Original10s readiness cap expired')
        return process, identity

    proof = None
    try:
        first, identity = launch()
        assert first_committed.wait(min(30, max(0, case_deadline - time.monotonic() - 10)))
        assert not receiver_errors and not first_ack_attempted.is_set()
        raw = _raw_state(state / 'inspection_service.sqlite3')
        assert len(raw['jobs']) == len(raw['deliveries']) == len(raw['inbox_items']) == 1
        job, delivery = raw['jobs'][0], raw['deliveries'][0]
        assert job['state'] == 'delivery_pending' and delivery['state'] == 'sending' and delivery['attempts'] == 1
        assert job['source'] == 'inbox' and job['image_path'] == str(image.resolve()) and job['image_sha256'] == image_sha
        assert job['result_json'] and job['attempts'] == 1 and job['deadline_at'] > time.time()
        binding = json.loads(job['runtime_binding_json'])
        assert _sha(package_root / 'manifest.json') == binding['manifest_sha256']
        assert hashlib.sha256(job['runtime_binding_json'].encode()).hexdigest() == job['runtime_binding_sha256']
        assert binding['device'] == 'cpu' and binding['package_path'] == str(package_root)
        model_result = json.loads(job['result_json'])
        assert model_result['runtime_identity'] == binding and model_result['final_verdict'] == 'OK'
        assert model_result['status'] not in ('failed', 'error', 'timeout') and not model_result.get('error_message')
        assert model_result['runtime_device_identity']['device'] == 'cpu'
        assert model_result['runtime_device_identity']['gpu_uuid'] is None
        assert model_result['runtime_device_identity']['process_id'] != first.pid
        assert any(step['node_id'] in inspection_ids and step['status'] == 'passed'
                   for step in model_result['execution_steps'])
        assert any(crop['predicted_class'] == 'OK' for crop in model_result['crops'])
        http_original = api('/v1/jobs/' + job['job_id'])
        assert http_original['result'] == model_result and http_original['runtime_binding'] == binding
        owner = owned_inspection_process(identity, state)
        assert owner is not None and owner.pid == first.pid and owner.cmdline() == command
        assert not owner.children(recursive=True), 'Refuse crash injection while child work is still observed'
        assert not first_ack_attempted.is_set()
        # Exactly the newly-created verified leader handle; no numeric PID,
        # group/name scan, foreign process or production exit hook is used.
        owner.kill()
        first_thread = attempts[0][0]
        first_thread.join(min(10, max(0, case_deadline - time.monotonic())))
        assert not first_thread.is_alive() and not failures
        assert outcomes[0]['status'] == 'completed' and outcomes[0]['returncode'] == -signal.SIGKILL, _owned_daemon_outcome_note(outcomes[0], passive_trace.for_handle(first))
        assert outcomes[0]['pid'] == first.pid and not outcomes[0].get('private_diagnostics')
        assert owned_inspection_process(identity, state) is None
        assert _raw_state(state / 'inspection_service.sqlite3') == raw
        first_release.set()
        second, identity2 = launch()
        assert second.pid != first.pid and identity2['process_created_at'] != identity['process_created_at']
        delivery_deadline = min(case_deadline - 10, time.monotonic() + 10)
        terminal = _poll(lambda: api('/v1/jobs/' + job['job_id'], deadline=delivery_deadline),
                         lambda x: x['state'] == 'completed', delivery_deadline, 'Original result did not resume delivery')
        inbox_deadline = min(case_deadline - 10, time.monotonic() + 10)
        _poll(lambda: api('/v1/adapters', deadline=inbox_deadline), lambda x: x['file_inbox'] == 'connected',
              inbox_deadline, 'Restarted original inbox did not finish its settled scan')
        after = _raw_state(state / 'inspection_service.sqlite3')
        assert len(after['jobs']) == len(after['deliveries']) == len(after['inbox_items']) == 1
        stable = [k for k in job if k not in {'state', 'verdict', 'error', 'updated_at'}]
        assert {k: after['jobs'][0][k] for k in stable} == {k: job[k] for k in stable}
        assert terminal['result'] == model_result and terminal['runtime_binding'] == binding
        assert terminal['error'] is None and terminal['verdict'] == terminal['model_verdict'] == 'OK'
        assert after['deliveries'][0]['state'] == 'sent' and after['deliveries'][0]['attempts'] == 2
        assert after['inbox_items'] == raw['inbox_items']
        assert [e['state'] for e in after['events']] == ['queued', 'running', 'delivery_pending', 'delivery_pending', 'completed']
        with sqlite3.connect(receiver_db) as conn:
            accepted = conn.execute('SELECT * FROM accepted').fetchall()
            requests = conn.execute('SELECT * FROM requests ORDER BY number').fetchall()
        assert len(accepted) == 1 and len(requests) == 2
        assert requests[0][1:] == requests[1][1:] == (job['job_id'], accepted[0][2])
        assert json.loads(accepted[0][1]) == {'job_id': job['job_id'], 'model_verdict': 'OK',
            'image_sha256': image_sha, 'result': model_result, 'runtime_identity': binding}
        assert not receiver_errors
        assert api('/v1/runtime/shutdown', post=True)['status'] == 'stopping'
        attempts[1][0].join(min(10, max(0, case_deadline - time.monotonic())))
        assert not attempts[1][0].is_alive() and not failures
        assert outcomes[1]['status'] == 'completed' and outcomes[1]['returncode'] == 0, _owned_daemon_outcome_note(outcomes[1], passive_trace.for_handle(second))
        assert outcomes[1]['pid'] == second.pid and not outcomes[1].get('private_diagnostics')
        assert owned_inspection_process(identity2, state) is None
        assert _sha(image) == image_sha and _sha(checkpoint) == checkpoint_sha
        assert {str(p.relative_to(package_root)): _sha(p) for p in package_root.rglob('*') if p.is_file()} == package_files
        assert {rel: _sha(source_root / rel) for rel in source_paths} == source_pins
        proof = {'scope': 'actual_owned_cpu_daemon_interruption_and_outbox_replay_only',
            'fixture_input_epoch': epoch, 'original_owned_identity': identity, 'restarted_owned_identity': identity2,
            'outcomes': outcomes, 'original_raw_state': raw, 'recovered_raw_state': after,
            'receiver_business_rows': accepted, 'receiver_request_rows': requests,
            'first_business_commit_before_crash': True, 'first_ack_sent_before_crash': False,
            'post_exit_first_ack_wire_delivery': 'unknown', 'actual_inference_count': 1,
            'receiver_request_count': 2, 'receiver_unique_business_result_count': 1,
            'source_pins': source_pins, 'original_image_sha256': image_sha,
            'original_checkpoint_sha256': checkpoint_sha, 'package_files': package_files,
            'physical_power_loss_verified': False, 'boot_or_os_service_registration_verified': False,
            'gpu_or_hardware_verified': False, 'quality_approved': False, 'parent_accepted': False,
            'complete_process_tree_verified': False}
    finally:
        first_release.set()
        unresolved = []
        for thread, execution in attempts:
            if thread.is_alive():
                execution.cancel()  # Original production owner only, preserving uncertainty retention.
                thread.join(10)
                if thread.is_alive():
                    unresolved.append(thread.name)
        receiver.shutdown()
        receiver.server_close()
        server_thread.join(10)
        assert not unresolved, 'Owned executor remains unresolved; retain all workspace bytes'
        assert not server_thread.is_alive(), 'Owned receiver remains unresolved'
    assert proof is not None
    (root / 'owned-real-daemon-recovery-proof.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
