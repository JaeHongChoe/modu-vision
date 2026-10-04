"""Host-observed camera provenance; no claim of sensor time or unseen loss."""
from __future__ import annotations

import json
import re
import time
import uuid


def camera_identity(source, configured=None):
    if configured is None and (type(source) is int or isinstance(source, str) and source.isdecimal()):
        configured = f'usb:{int(source)}'
    elif configured is None and isinstance(source, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', source):
        configured = source
    if not isinstance(configured, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', configured):
        raise ValueError('Camera requires an opaque camera_id; do not use a URI or credentials')
    return configured


def validate_acquisition(value):
    fields = {'schema_version', 'camera_id', 'stream_session_id', 'sequence', 'captured_unix_ms',
              'observed_monotonic_ms', 'clock_basis', 'observed_dropped_count', 'clock_discontinuity'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Invalid camera acquisition fields')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('Unknown camera acquisition schema')
    camera_identity(None, value['camera_id'])
    if not isinstance(value['stream_session_id'], str) or not re.fullmatch('[0-9a-f]{32}', value['stream_session_id']):
        raise ValueError('Invalid camera stream session')
    for name in ('sequence', 'captured_unix_ms', 'observed_monotonic_ms', 'observed_dropped_count'):
        if type(value[name]) is not int or not 0 <= value[name] <= 2**63 - 1 or (name == 'sequence' and value[name] == 0):
            raise ValueError('Camera acquisition times and counts must be finite nonnegative integers')
    if value['clock_basis'] != 'host_read_completion' or type(value['clock_discontinuity']) is not bool:
        raise ValueError('Camera acquisition requires an explicit host clock basis')
    return dict(value)


def group_capture_from_trigger(trigger, acquisition, policy):
    """A configured trigger adapter supplies identity, never invented correlation."""
    acquisition = validate_acquisition(acquisition)
    basis = policy['policy']['timestamp_basis']
    keys = {'part_id', 'trigger_id', 'view_id', 'timestamp_basis'}
    if basis == 'trigger_offset':
        keys.add('trigger_unix_ms')
    if not isinstance(trigger, dict) or set(trigger) != keys or trigger['timestamp_basis'] != basis:
        raise ValueError('Camera trigger provider must bind the configured timestamp basis')
    for name in ('part_id', 'trigger_id', 'view_id'):
        if not isinstance(trigger[name], str) or not trigger[name].strip() or len(trigger[name]) > 160:
            raise ValueError('Camera trigger requires part, trigger and view identities')
    at = acquisition['captured_unix_ms']
    if basis == 'trigger_offset':
        start = trigger['trigger_unix_ms']
        if type(start) is not int or not 0 <= start <= at:
            raise ValueError('Camera trigger offset requires a prior explicit epoch in milliseconds')
        at -= start
    elif basis != 'shared_clock':
        raise ValueError('Unsupported camera clock basis')
    return {**{name: trigger[name] for name in ('part_id', 'trigger_id', 'view_id')}, 'captured_at_ms': at}


class CameraAcquisition:
    """Session sequence and observed drops persist in the existing owned service DB."""
    def __init__(self, store, camera_id):
        self.store = store
        self.camera_id = camera_identity(None, camera_id)
        self.session = None
        with store._connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS camera_adapter_state(camera_id TEXT PRIMARY KEY, state_json TEXT NOT NULL)')
            initial = {'stream_session_id': None, 'sequence': 0, 'last_unix_ms': None, 'last_monotonic_ms': None,
                       'observed_dropped_count': 0, 'read_failures': 0, 'connection_failures': 0,
                       'reconnect_count': 0, 'clock_discontinuity': False}
            db.execute('INSERT OR IGNORE INTO camera_adapter_state VALUES(?,?)', (self.camera_id, json.dumps(initial)))

    def _change(self, update):
        with self.store._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            state = json.loads(db.execute('SELECT state_json FROM camera_adapter_state WHERE camera_id=?', (self.camera_id,)).fetchone()[0])
            update(state)
            db.execute('UPDATE camera_adapter_state SET state_json=? WHERE camera_id=?',
                       (json.dumps(state, allow_nan=False), self.camera_id))
        return state

    def start_session(self):
        self.session = uuid.uuid4().hex
        def begin(state):
            if state['stream_session_id']:
                state['reconnect_count'] += 1
            state.update(stream_session_id=self.session, sequence=0, last_monotonic_ms=None, clock_discontinuity=False)
        return self._change(begin)

    def observe(self, *, unix_ms=None, monotonic_ms=None):
        unix_ms = time.time_ns() // 1_000_000 if unix_ms is None else unix_ms
        monotonic_ms = time.monotonic_ns() // 1_000_000 if monotonic_ms is None else monotonic_ms
        if any(type(at) is not int or not 0 <= at <= 2**63 - 1 for at in (unix_ms, monotonic_ms)):
            raise ValueError('Camera clock values must be epoch/monotonic integer milliseconds')
        def observed(state):
            if not self.session or state['stream_session_id'] != self.session:
                raise ValueError('Camera session changed; reopen the owned adapter')
            if ((state['last_unix_ms'] is not None and unix_ms < state['last_unix_ms']) or
                    (state['last_monotonic_ms'] is not None and monotonic_ms < state['last_monotonic_ms'])):
                state['clock_discontinuity'] = True
            state.update(sequence=state['sequence'] + 1, last_unix_ms=unix_ms, last_monotonic_ms=monotonic_ms)
        state = self._change(observed)
        return validate_acquisition({'schema_version': 1, 'camera_id': self.camera_id, 'stream_session_id': self.session,
                                    'sequence': state['sequence'], 'captured_unix_ms': unix_ms, 'observed_monotonic_ms': monotonic_ms,
                                    'clock_basis': 'host_read_completion', 'observed_dropped_count': state['observed_dropped_count'],
                                    'clock_discontinuity': state['clock_discontinuity']})

    def record_drop(self):
        return self._change(lambda state: state.update(observed_dropped_count=state['observed_dropped_count'] + 1))

    def record_failure(self, *, connection=False):
        name = 'connection_failures' if connection else 'read_failures'
        return self._change(lambda state: state.update(**{name: state[name] + 1}))

    def status(self):
        with self.store._connection() as db:
            state = json.loads(db.execute('SELECT state_json FROM camera_adapter_state WHERE camera_id=?', (self.camera_id,)).fetchone()[0])
        return {'schema_version': 1, 'camera_id': self.camera_id, 'clock_basis': 'host_read_completion',
                'count_scope': 'observed_service_reads; unseen sensor losses are not measured', **state}
