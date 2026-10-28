"""Ignored diagnostic prototype. No production imports, runtime patches or OS probes.

Preparation reads caller-supplied source bytes. Only failure emission accesses files.
Tracing is a timing perturbation and cannot establish the historical failure cause.
"""
import ast
import dis
import hashlib
import json
import marshal
import os
import sys
from types import CodeType, FunctionType


RUNTIME_SHA256 = 'd98502523703705b5fef1ac06eb367a1e4e02d73d19949953e5b051edd627a44'
TEST_AST_SHA256 = '18902a9bb8dd796d46f01dad4b317bed500833258e161e59351cb07cc0b07f95'
TEST_NAME = 'test_actual_canary_partial_publication_never_runs_a_second_worker'
NODEID = 'backend/tests/test_staged_update_canary.py::' + TEST_NAME + '[after_canary_result]'
FILENAME = 'canary-unknown-after-result.json'
_SITES = {
    ('observe', 215): 'leader_identity_unproven',
    ('observe', 223): 'unregistered_without_live_leader',
    ('observe', 224): 'foreign_session_row',
    ('observe', 229): 'registered_identity_unproven',
    ('_terminate_owned', 309): 'termination_leader_unproven',
}
_NEXT_LINES = {215: 216, 223: 224, 224: 210, 229: 226, 309: 310}
_PHASES = {382: 'wait_observation', 401: 'interrupted_cleanup_observation', 404: 'final_observation'}


def _normalized_code(code):
    """Normalize filename only; retain bytecode, constants, flags and line mapping."""
    constants = tuple(_normalized_code(value) if type(value) is CodeType else value for value in code.co_consts)
    return code.replace(co_filename='<pinned-source>', co_consts=constants)


def _code_digest(code):
    return hashlib.sha256(marshal.dumps(_normalized_code(code))).hexdigest()


def _find_code(code, name):
    for value in code.co_consts:
        if type(value) is CodeType:
            if value.co_name == name:
                return value
            found = _find_code(value, name)
            if found is not None:
                return found
    return None


def _function_code(value):
    """Only ordinary function metadata; never invoke descriptors or wrappers."""
    for _ in range(4):
        if type(value) is not FunctionType:
            return None
        wrapped = value.__dict__.get('__wrapped__')
        if wrapped is None:
            return value.__code__
        value = wrapped
    return None


def _command_shape(command):
    if type(command) is not tuple or len(command) > 128:
        return None
    if any(type(value) is not str or len(value) > 4096 for value in command):
        return None
    return 'nonempty' if command else 'empty'


class Observer:
    """One exact selected item, one original owner, bounded primitive-only state."""
    def __init__(self, *, module, runtime_source, test_source, selected_callable, canonical_test_callable,
                 selected_code_anchor, nodeid, point,
                 max_events=100000, max_owners=1):
        self._status = 'ready'
        self._codes = ()
        self._normal_returns = {}
        self._owner_type = None
        self._installed_trace = self._dispatch  # Stable bound-method identity for restoration.
        self._installed = False
        self._entered = False
        self._depth = 0
        self._disabled = False
        self._max_events = max_events if type(max_events) is int and 1 <= max_events <= 100000 else 0
        self._max_owners = max_owners if type(max_owners) is int and 0 <= max_owners <= 1 else 0
        self._events = 0
        self._owners = 0
        self._frames = {}
        self._execution = None
        self._owner = None
        self._unknown = None
        self._first = None
        self._initial_command = 'not_captured'
        self._unknown_count = None
        self._remaining_count = None
        self._refused = False
        self._outcome = 'not_captured'
        self._unconfirmed = False
        if sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 13):
            self._disable('interpreter_unsupported'); return
        if type(nodeid) is not str or nodeid != NODEID or type(point) is not str or point != 'after_canary_result':
            self._disable('selection_mismatch'); return
        if type(runtime_source) is not bytes or hashlib.sha256(runtime_source).hexdigest() != RUNTIME_SHA256:
            self._disable('source_pin_mismatch'); return
        if type(test_source) is not bytes:
            self._disable('test_ast_pin_mismatch'); return
        try:
            test_tree = ast.parse(test_source)
            test_node = next(n for n in test_tree.body if isinstance(n, ast.FunctionDef) and n.name == TEST_NAME)
            digest = hashlib.sha256(ast.dump(test_node, include_attributes=False).encode()).hexdigest()
            if digest != TEST_AST_SHA256:
                self._disable('test_ast_pin_mismatch'); return
            expected_test = _find_code(compile(test_tree, '<pinned-test>', 'exec'), TEST_NAME)
            actual_test = selected_callable.__code__ if type(selected_callable) is FunctionType else None
            # Pytest rewrites assertion bytecode. Bind the actual selected function
            # to its canonical module callable, and pin its original AST instead.
            metadata = ('co_name', 'co_qualname', 'co_firstlineno', 'co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount')
            if selected_callable is not canonical_test_callable or actual_test is None or type(selected_code_anchor) is not CodeType or actual_test is not selected_code_anchor or any(getattr(actual_test, name) != getattr(expected_test, name) for name in metadata):
                self._disable('test_callable_pin_mismatch'); return
            tree = ast.parse(runtime_source)
            add_lines = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'add' and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == 'unknown':
                    add_lines.append(node.lineno)
            if sorted(add_lines) != [215, 223, 224, 229, 309]:
                self._disable('ast_map_mismatch'); return
            expected = compile(tree, '<pinned-runtime>', 'exec')
            owned = module._OwnedGroup
            pairs = [(name, _function_code(getattr(owned, name))) for name in ('__init__', 'leader_matches', 'member_state', 'observe')]
            pairs += [(name, _function_code(getattr(module, name))) for name in ('_terminate_owned', '_observe_owned', 'execute_owned_process')]
            expected_owned = _find_code(expected, '_OwnedGroup')
            if any(code is None or _code_digest(code) != _code_digest(_find_code(expected_owned if name in ('__init__', 'leader_matches', 'member_state', 'observe') else expected, name)) for name, code in pairs):
                self._disable('code_pin_mismatch'); return
            self._codes = tuple(pairs)
            self._normal_returns = {name: tuple(instruction.offset for instruction in dis.get_instructions(code) if instruction.opname in ('RETURN_VALUE', 'RETURN_CONST')) for name, code in pairs}
            self._owner_type = id(owned)
        except Exception:
            self._disable('preparation_failed')

    def _disable(self, status):
        self._status = status
        self._disabled = True
        self._frames.clear()

    def __enter__(self):
        if self._depth:
            self._depth += 1
            self._status = 'observer_reentry_conflict'
            return self
        self._depth = 1
        if self._entered:
            self._disable('observer_reused')
            self._first = None
            self._refused = False
            self._outcome = self._initial_command = 'not_captured'
            self._unknown_count = self._remaining_count = None
            self._owners = self._events = 0
            return self
        self._entered = True
        if self._disabled:
            return self
        if sys.gettrace() is not None:
            self._disable('existing_trace_conflict'); return self
        try:
            sys.settrace(self._installed_trace)
            self._installed = True
            self._status = 'captured'
        except Exception:
            self._disable('trace_install_failed')
        return self

    def __exit__(self, kind, value, traceback):
        if self._depth > 1:
            self._depth -= 1
            return False
        self._depth = 0
        if self._installed:
            try:
                current = sys.gettrace()
                if current is self._installed_trace:
                    sys.settrace(None)
                elif current is not None:
                    self._disable('trace_changed_by_other')
                elif not self._disabled:
                    self._disable('trace_changed_by_other')
            except BaseException:
                self._disable('trace_restore_failed')
        self._installed = False
        self._frames.clear()
        self._execution = self._owner = self._unknown = None
        return False

    def _role(self, code):
        for name, original in self._codes:
            if code is original:
                return name
        return None

    def _dispatch(self, frame, event, arg):
        try:
            if self._disabled:
                return None
            role = self._role(frame.f_code)
            if role is None:
                return None
            if sys.gettrace() is not self._installed_trace:
                self._disable('trace_changed_by_other'); return None
            self._events += 1
            if self._events > self._max_events:
                self._disable('event_budget_exceeded'); return None
            self._event(role, frame, event, arg)
        except BaseException:
            # A diagnostic bug must not become an exception in the original runtime.
            self._disable('callback_failed')
        return None if self._disabled else self._installed_trace

    def _owner_fields(self, frame, role):
        locals_ = frame.f_locals
        owner = locals_.get('ownership') if role == '_terminate_owned' else locals_.get('self')
        if owner is None or id(type(owner)) != self._owner_type or id(owner) != self._owner:
            return None
        fields = object.__getattribute__(owner, '__dict__')
        return fields if type(fields) is dict else None

    def _phase(self, frame, role):
        if role == '_terminate_owned':
            return 'termination_guard'
        parent = frame.f_back
        for _ in range(4):
            if parent is None:
                break
            if self._role(parent.f_code) == 'execute_owned_process' and id(parent) == self._execution:
                return _PHASES.get(parent.f_lineno, 'unmapped')
            parent = parent.f_back
        return 'unmapped'

    def _facts(self, frame, fields):
        values = frame.f_locals
        captured = fields.get('leader')
        birth = values.get('birth')
        command = values.get('command')
        result = {'birth_equal': None, 'command_equal': None, 'command_empty': None}
        if type(captured) is not tuple or len(captured) != 2:
            return result
        if type(birth) in (float, int) and type(captured[0]) in (float, int):
            result['birth_equal'] = birth == captured[0]
        shape = _command_shape(command)
        old_shape = _command_shape(captured[1])
        if shape is not None:
            result['command_empty'] = shape == 'empty'
        if shape is not None and old_shape is not None:
            result['command_equal'] = command == captured[1]
        return result

    def _candidate(self, frame, role, fields):
        if self._first is not None:
            return None
        unknown = fields.get('unknown')
        members = fields.get('members')
        if type(unknown) is not set or type(members) is not dict or id(unknown) != self._unknown:
            self._disable('owner_shape_unavailable'); return None
        if len(members) > 1000 or any(type(value) is not int for value in members):
            self._disable('owner_shape_unavailable'); return None
        if len(unknown) != 0:
            self._unconfirmed = True; return None
        values = frame.f_locals
        process = fields.get('process')
        process_fields = object.__getattribute__(process, '__dict__')
        leader = process_fields.get('pid') if type(process_fields) is dict else None
        candidate = leader if role == '_terminate_owned' else values.get('pid')
        if type(candidate) is not int or type(leader) is not int:
            self._disable('owner_shape_unavailable'); return None
        relation = 'original_leader' if candidate == leader else 'registered_member' if candidate in members else 'other'
        row_equal = None
        if frame.f_lineno in (215, 223, 224):
            row = values.get('row')
            observed = row.get('session') if type(row) is dict else None
            expected = fields.get('session')
            if type(observed) is int and type(expected) is int:
                row_equal = observed == expected
        parent_state = self._frames.get(id(frame), {})
        fact_key = 'termination_leader' if role == '_terminate_owned' else 'scan_leader'
        facts = parent_state.get(fact_key, {}) if frame.f_lineno != 229 else {}
        return {'line': frame.f_lineno, 'count': 0, 'facts': {
            'reason': _SITES[(role, frame.f_lineno)], 'relation': relation,
            'phase': self._phase(frame, role), 'row_session_equal': row_equal,
            'birth_equal': facts.get('birth_equal'), 'command_equal': facts.get('command_equal'),
            'command_empty': facts.get('command_empty'),
            'member_identity': parent_state.get('member_identity', 'not_captured') if frame.f_lineno == 229 else 'not_captured',
        }}

    def _event(self, role, frame, event, arg):
        key = id(frame)
        if event == 'call':
            if len(self._frames) >= 16:
                self._disable('frame_budget_exceeded'); return
            if role == 'execute_owned_process':
                if self._execution is not None:
                    self._disable('owner_budget_exceeded'); return
                self._execution = key
            if role == '__init__':
                parent = frame.f_back
                if parent is None or self._role(parent.f_code) != 'execute_owned_process' or id(parent) != self._execution:
                    return
                self._owners += 1
                if self._owners > self._max_owners:
                    self._disable('owner_budget_exceeded'); return
                self._owner = id(frame.f_locals['self'])
            self._frames[key] = {'exception_pending': False}
        state = self._frames.get(key)
        if state is None:
            return
        fields = self._owner_fields(frame, role) if role not in ('execute_owned_process', '_observe_owned') else None
        pending = state.get('pending')
        if pending is not None and event in ('line', 'return'):
            state.pop('pending')
            unknown = fields.get('unknown') if fields is not None else None
            valid = type(unknown) is set and id(unknown) == self._unknown
            next_valid = event == 'return' or frame.f_lineno == _NEXT_LINES[pending['line']]
            if not valid or not next_valid:
                self._unconfirmed = True
            elif len(unknown) == 1:
                self._first = pending['facts']
            elif len(unknown) != 0:
                self._unconfirmed = True
        if event == 'exception':
            state['exception_pending'] = True
        elif event == 'line':
            state['exception_pending'] = False
            if fields is not None and role == 'leader_matches' and frame.f_lineno == 176:
                state['facts'] = self._facts(frame, fields)
            if fields is not None and (role, frame.f_lineno) in _SITES:
                candidate = self._candidate(frame, role, fields)
                if candidate is not None:
                    state['pending'] = candidate
        elif event == 'return':
            if fields is not None and role == '__init__' and frame.f_lasti in self._normal_returns['__init__']:
                unknown = fields.get('unknown')
                if type(unknown) is set:
                    self._unknown = id(unknown)
                    captured = fields.get('leader')
                    self._initial_command = 'absent' if captured is None else _command_shape(captured[1]) if type(captured) is tuple and len(captured) == 2 else 'not_captured'
            elif fields is not None and role == 'leader_matches' and type(arg) is bool:
                parent = frame.f_back
                parent_state = self._frames.get(id(parent), {}) if parent is not None else {}
                if parent is not None and self._role(parent.f_code) == 'observe' and parent.f_lineno == 199:
                    parent_state['scan_leader'] = state.get('facts', {})
                elif parent is not None and self._role(parent.f_code) == '_terminate_owned':
                    parent_state['termination_leader'] = state.get('facts', {})
            elif role == 'member_state' and type(arg) is str and arg == 'unknown':
                parent = frame.f_back
                parent_state = self._frames.get(id(parent), {}) if parent is not None else {}
                parent_state['member_identity'] = {189: 'reader_unavailable', 193: 'birth_or_command_mismatch', 196: 'reader_error'}.get(frame.f_lineno, 'not_captured')
            if fields is not None and type(fields.get('unknown')) is set:
                self._unknown_count = min(len(fields['unknown']), 1001)
                remaining = fields.get('remaining')
                self._remaining_count = min(len(remaining), 1001) if type(remaining) is set else None
            if role == 'execute_owned_process':
                if type(arg) is dict:
                    status = arg.get('status')
                    reason = arg.get('rejection_reason')
                    if type(status) is str and status in ('uncertain', 'completed', 'timeout', 'cancelled'):
                        self._outcome = status
                    self._refused = type(status) is str and status == 'uncertain' and type(reason) is str and reason == 'OWNED_PROCESS_GROUP_UNRECONCILED'
                self._execution = self._owner = self._unknown = None
            self._frames.pop(key, None)

    def public_record(self):
        return {'schema_version': 1, 'diagnostic_only': True, 'release_qualified': False,
                'process_tree_exit_verified': False, 'selection': 'actual_canary_after_result',
                'runtime_source_sha256': RUNTIME_SHA256, 'original_test_ast_sha256': TEST_AST_SHA256,
                'instrumentation_status': self._status, 'owner_count': min(self._owners, 2),
                'initial_command': self._initial_command, 'ownership_refusal_observed': self._refused,
                'outcome': self._outcome, 'unknown_count': self._unknown_count,
                'remaining_count': self._remaining_count, 'transition_unconfirmed': self._unconfirmed,
                'first_unknown': None if self._first is None else dict(self._first)}

    def emit_failure(self, directory, *, call_failed):
        if call_failed is not True or type(directory) is not str:
            return False
        if not self._refused and self._status == 'captured':
            return False
        directory_fd = file_fd = None
        written = False
        try:
            data = (json.dumps(self.public_record(), sort_keys=True, separators=(',', ':')) + '\n').encode()
            if len(data) > 4096:
                return False
            directory_fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
            file_fd = os.open(FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=directory_fd)
            with os.fdopen(file_fd, 'wb') as output:
                file_fd = None
                output.write(data)
            written = True
        except Exception:
            written = False
        finally:
            for descriptor in (file_fd, directory_fd):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except Exception:
                        written = False
        return written
