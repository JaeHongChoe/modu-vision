"""Actor-scoped durable execution intent, never permission to perform app actions.

The renderer executes existing role-checked APIs. This ledger stores a person's
immutable plan/grant and bounded receipts; it cannot adopt truth or deploy a model.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import copy
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

TASK_ID = re.compile(r'task_[a-f0-9]{32}')
STEP_ID = re.compile(r'step_[a-f0-9]{32}')
ATTEMPT_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}')
CLAIM_NONCE = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}')
MAX_BODY_BYTES = 65536
MAX_RECEIPT_BYTES = 16384
MAX_RECORD_BYTES = 512 * 1024
MAX_TASKS = 256
ACTIONS = frozenset(('create_project', 'import_dataset', 'save_split', 'configure_training', 'configure_input_recipe',
    'generate_label_drafts', 'start_training', 'evaluate_model', 'save_flow',
    'evaluate_flow', 'inspect_test', 'export_report', 'open_step'))
# Only an action allowed to alter the selected project/source/settings can
# advance the opaque local execution fence. The original approval stays fixed.
BINDING_ACTIONS = frozenset(('create_project', 'import_dataset', 'save_split', 'configure_training', 'configure_input_recipe', 'start_training'))
EXECUTION_POLICY_VERSION = 'continuous-execution-v1'
EVENTS = frozenset(('approve', 'authorize', 'recheck', 'quality_result', 'begin_step', 'complete_step', 'wait_step', 'pause',
                    'resume', 'fail_step', 'reconcile_step', 'record_evidence'))
STATUSES = frozenset(('draft', 'ready', 'running', 'waiting_human', 'waiting_job',
                     'paused', 'needs_reconciliation', 'failed', 'completed'))
STEP_STATUSES = frozenset(('pending', 'running', 'waiting_human', 'waiting_job',
                          'completed', 'failed', 'uncertain'))


class TaskError(ValueError):
    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class TaskScope:
    workspace_id: str
    actor_id: str

    def document(self):
        if any(not isinstance(value, str) or not 1 <= len(value) <= 128
               for value in (self.workspace_id, self.actor_id)):
            raise TaskError('Invalid authenticated task scope')
        return {'workspace_id': self.workspace_id, 'actor_id': self.actor_id}


def is_task_preparation(path, method):
    """Exact preparation endpoints only; lookalikes retain project admission."""
    return (path == '/api/assistant-tasks' and method in {'GET', 'POST'}
        or method == 'GET' and re.fullmatch(r'/api/assistant-tasks/task_[a-f0-9]{32}', path) is not None
        or method == 'POST' and re.fullmatch(r'/api/assistant-tasks/task_[a-f0-9]{32}/events', path) is not None)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _canonical(value):
    # JSON.stringify-compatible finite numbers (Python otherwise emits 1e-06
    # where JS emits 0.000001). Plan keys are the fixed ASCII action schema.
    def encode(node):
        if node is None: return 'null'
        if type(node) is bool: return 'true' if node else 'false'
        if type(node) is str: return json.dumps(node, ensure_ascii=False)
        if type(node) is int: return str(node)
        if type(node) is float:
            if not math.isfinite(node): raise TaskError('Task numbers must be finite')
            if node == 0: return '0'
            if node.is_integer(): return str(int(node))
            spelling = repr(node)
            if 1e-6 <= abs(node) < 1e21: return format(Decimal(spelling), 'f')
            return re.sub(r'e([+-])0+(\d+)', r'e\1\2', spelling)
        if type(node) is list: return '['+','.join(encode(child) for child in node)+']'
        if type(node) is dict:
            return '{'+','.join(encode(key)+':'+encode(node[key]) for key in sorted(node))+'}'
        raise TaskError('Task data must be JSON')
    try: return encode(value).encode('utf-8')
    except (UnicodeError, RecursionError) as exc: raise TaskError('Task JSON contains invalid Unicode or nesting') from exc


def platform_supported():
    return os.name == 'posix' and os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW')


def _json(value, *, depth=0):
    if depth > 8:
        raise TaskError('Task JSON nesting exceeds the supported limit')
    if value is None or type(value) in (str, bool, int):
        if isinstance(value, str) and len(value) > 16384:
            raise TaskError('Task JSON string exceeds the supported limit')
        if type(value) is int and abs(value) > 2**53-1:
            raise TaskError('Task JSON number is outside the interoperable range')
        return value
    if type(value) is float and math.isfinite(value) and abs(value) <= 2**53-1:
        return int(value) if value.is_integer() else value
    if type(value) is dict and len(value) <= 64 and all(type(key) is str and len(key) <= 128 for key in value):
        return {key: _json(child, depth=depth+1) for key, child in value.items()}
    if type(value) is list and len(value) <= 128:
        return [_json(child, depth=depth+1) for child in value]
    raise TaskError('Task data must be bounded JSON without nonfinite numbers')


def _object(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or value.keys()-set(required)-set(optional):
        raise TaskError('Task object fields do not match the schema')


def _text(value, maximum, *, minimum=1):
    if type(value) is not str or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise TaskError('Task text must contain valid Unicode')
    length = len(value.encode('utf-16-le')) // 2
    if (not minimum <= length <= maximum or (minimum and not value.strip())
        or any(ord(char) < 32 and char not in '\t\r\n' for char in value)):
        raise TaskError('Task text is empty or exceeds the supported limit')


def _number(value, minimum, maximum, *, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise TaskError('Task action number is out of bounds')


def _enum(value, values):
    if type(value) is not str or value not in values:
        raise TaskError('Unsupported task action option')


def _args(action, args):
    if action in {'import_dataset', 'start_training', 'evaluate_flow', 'inspect_test'}:
        _object(args, ())
    elif action == 'create_project':
        _object(args, ('name', 'task'), ('description',))
        _text(args['name'], 120)
        _enum(args['task'], {'classification', 'detection', 'anomaly', 'segmentation'})
        if 'description' in args: _text(args['description'], 2000)
    elif action == 'save_split':
        _object(args, ('train_percent', 'val_percent', 'test_percent'))
        for value in args.values(): _number(value, 0, 100)
        if abs(sum(args.values())-100) > 0.0000001: raise TaskError('Split percentages must total 100')
    elif action == 'configure_training':
        _object(args, (), ('preset', 'device', 'batch_size'))
        if not args: raise TaskError('At least one supported training setting is required')
        if 'preset' in args: _enum(args['preset'], {'fast', 'precision'})
        if 'device' in args: _enum(args['device'], {'auto', 'cpu', 'cuda', 'mps'})
        if 'batch_size' in args: _number(args['batch_size'], 1, 128, integer=True)
    elif action == 'configure_input_recipe':
        _object(args, ('input_recipe',))
        from backend.engine.input_preparation import canonical_recipe
        try:
            if canonical_recipe(args['input_recipe']) is None: raise ValueError('A recipe is required')
        except (ValueError, TypeError, KeyError) as exc:
            raise TaskError(str(exc)) from exc
    elif action == 'generate_label_drafts':
        _object(args, (), ('mode', 'job_id', 'threshold', 'prompt'))
        if 'mode' in args: _enum(args['mode'], {'trained', 'phrase'})
        if 'job_id' in args: _text(args['job_id'], 256)
        if 'threshold' in args: _number(args['threshold'], 0, 1)
        if 'prompt' in args: _text(args['prompt'], 2000)
        if args.get('job_id') and args.get('prompt'): raise TaskError('Choose trained or phrase draft generation')
        if args.get('mode') == 'phrase' and ('job_id' in args or not args.get('prompt')):
            raise TaskError('Phrase drafts require a prompt and no trained job')
        if args.get('mode') == 'trained' and 'prompt' in args:
            raise TaskError('Trained drafts cannot carry a phrase prompt')
    elif action in {'evaluate_model', 'save_flow'}:
        _object(args, (), ('job_id',))
        if 'job_id' in args: _text(args['job_id'], 256)
    elif action == 'export_report':
        _object(args, ('format',))
        _enum(args['format'], {'html', 'json'})
    elif action == 'open_step':
        _object(args, ('step',), ('target',))
        _number(args['step'], 1, 6, integer=True)
        if 'target' in args:
            if type(args['target']) is not str or re.fullmatch(r'[a-z][a-z0-9-]{1,99}', args['target']) is None:
                raise TaskError('Invalid task screen target')


def validate_plan(value):
    _object(value, ('goal', 'binding', 'steps'))
    _text(value['goal'], 4096)
    _text(value['binding'], 16384)
    if type(value['steps']) is not list or not 1 <= len(value['steps']) <= 24:
        raise TaskError('Task plans require between 1 and 24 steps')
    for step in value['steps']:
        _object(step, ('action', 'args', 'label'))
        _enum(step['action'], ACTIONS)
        _text(step['label'], 160)
        _args(step['action'], step['args'])
    result = _json(value)
    if len(_canonical(result)) > MAX_BODY_BYTES: raise TaskError('Task plan exceeds 64 KiB')
    return result


def validate_event(value):
    _object(value, ('type', 'binding', 'expected_revision'),
            ('step_id', 'attempt_id', 'receipt', 'message', 'binding_after', 'wait_kind', 'confirmed', 'authorization_source'))
    _enum(value['type'], EVENTS)
    _text(value['binding'], 16384)
    _number(value['expected_revision'], 1, 2**53-1, integer=True)
    if 'confirmed' in value and type(value['confirmed']) is not bool: raise TaskError('Confirmation must be a boolean')
    if 'authorization_source' in value: _enum(value['authorization_source'], {'user_request', 'oneclick'})
    if 'message' in value: _text(value['message'], 2000, minimum=0)
    if 'binding_after' in value: _text(value['binding_after'], 16384)
    if 'wait_kind' in value: _enum(value['wait_kind'], {'human', 'job'})
    for name, pattern in (('step_id', STEP_ID), ('attempt_id', ATTEMPT_ID)):
        if name in value and (type(value[name]) is not str or not pattern.fullmatch(value[name])):
            raise TaskError('Invalid task step or attempt identifier')
    if 'receipt' in value:
        if type(value['receipt']) is not dict or not value['receipt']:
            raise TaskError('A receipt must be a nonempty JSON object')
        if 'not_started' in value['receipt'] and type(value['receipt']['not_started']) is not bool:
            raise TaskError('The not_started marker must be a boolean')
        if 'unstarted_claim' in value['receipt']:
            claim = value['receipt']['unstarted_claim']
            _object(claim, ('nonce', 'phase'))
            if type(claim['nonce']) is not str or not CLAIM_NONCE.fullmatch(claim['nonce']) or claim['phase'] != 'claimed':
                raise TaskError('Invalid unstarted execution claim')
            if value['type'] == 'record_evidence' and value['receipt'].get('not_started') is not False:
                raise TaskError('An unstarted execution claim must consume the no-effect proof')
        if len(_canonical(_json(value['receipt']))) > MAX_RECEIPT_BYTES: raise TaskError('Task receipt exceeds 16 KiB')
    kind = value['type']
    extras = {
        'approve': {'confirmed', 'message'}, 'authorize': {'authorization_source', 'message'}, 'recheck': {'message'}, 'pause': {'message'}, 'resume': {'confirmed', 'message'},
        'begin_step': {'step_id', 'attempt_id', 'message'},
        'complete_step': {'step_id', 'attempt_id', 'receipt', 'message', 'binding_after'},
        'wait_step': {'step_id', 'attempt_id', 'receipt', 'message', 'wait_kind', 'binding_after'},
        'fail_step': {'step_id', 'attempt_id', 'message'},
        'record_evidence': {'step_id', 'attempt_id', 'receipt', 'message'},
        'quality_result': {'step_id', 'attempt_id', 'receipt'},
        'reconcile_step': {'step_id', 'attempt_id', 'receipt', 'message', 'binding_after', 'wait_kind'},
    }
    if value.keys() - {'type', 'binding', 'expected_revision'} - extras[kind]:
        raise TaskError('Task event fields do not match this transition')
    if kind in {'begin_step', 'complete_step', 'wait_step', 'fail_step', 'reconcile_step', 'record_evidence', 'quality_result'}:
        if not all(key in value for key in ('step_id', 'attempt_id')): raise TaskError('Step and attempt identifiers are required')
    elif any(key in value for key in ('step_id', 'attempt_id', 'receipt', 'binding_after', 'wait_kind')):
        raise TaskError('This task event cannot modify a step')
    if kind in {'complete_step', 'wait_step', 'quality_result'} and 'receipt' not in value:
        raise TaskError('Verified action receipts are required')
    if kind == 'record_evidence' and not value.get('receipt'):
        raise TaskError('Partial execution evidence requires a nonempty receipt')
    if kind == 'wait_step' and 'wait_kind' not in value: raise TaskError('A wait kind is required')
    if 'binding_after' in value and (kind not in {'complete_step', 'wait_step', 'reconcile_step'} or 'receipt' not in value or value.get('wait_kind') == 'human'):
        raise TaskError('Only verified action completion or job intent can advance the binding')
    if 'wait_kind' in value and kind not in {'wait_step', 'reconcile_step'}:
        raise TaskError('This task event cannot introduce a wait')
    return _json(value)


def _conflict(condition, message):
    if not condition: raise TaskError(message, 409)


def _identifier_key(key):
    return key in {'id', 'ids'} or key.endswith(('_id', '_ids', '_path', '_dir', '_sha256')) or re.search(r'(?:Id|Ids)$', key) is not None


def _contains_identifier(value):
    if type(value) is dict:
        return any((_identifier_key(key) and child is not None) or _contains_identifier(child) for key, child in value.items())
    if type(value) is list: return any(_contains_identifier(child) for child in value)
    return False


def _merge_evidence(existing, incoming):
    """Partial observations may add evidence, never replace a known execution identity."""
    result = copy.deepcopy(existing or {})
    for key, value in incoming.items():
        previous = result.get(key)
        if key not in result or previous is None:
            result[key] = copy.deepcopy(value)
        elif type(previous) is dict and type(value) is dict:
            result[key] = _merge_evidence(previous, value)
        elif previous != value:
            _conflict(not _identifier_key(key) and not _contains_identifier(previous),
                      'Partial evidence conflicts with a known execution identifier')
            result[key] = copy.deepcopy(value)
    if len(_canonical(_json(result))) > MAX_RECEIPT_BYTES:
        raise TaskError('Combined task evidence exceeds 16 KiB')
    return result


def _completion_evidence(receipt):
    """Control markers alone cannot establish that a planned action happened."""
    if 'not_started' in receipt and type(receipt['not_started']) is not bool:
        raise TaskError('The not_started marker must be a boolean')
    observations = {key: value for key, value in receipt.items()
                    if key not in {'not_started', 'status', 'message', 'error', 'reason_code', 'unstarted_claim', 'unstarted_claim_history'}}
    if receipt.get('not_started') is True or not any(value is not None and value != '' and value != {} and value != [] for value in observations.values()):
        raise TaskError('Completion or job waiting requires verified action evidence')


def _has_execution_reference(value):
    if type(value) is dict:
        names = ('job_id', 'batch_id', 'run_id', 'version_id', 'evaluation_id', 'cohort_id', 'pipeline_id', 'report_path')
        return any((any(key == name or key.endswith('_'+name) for name in names) and child is not None and child != '')
                   or _has_execution_reference(child) for key, child in value.items())
    if type(value) is list: return any(_has_execution_reference(child) for child in value)
    return False


def _update_receipt(task, step, event):
    """Consume a resumed no-effect proof once before a renderer may dispatch."""
    existing, incoming = step['receipt'] or {}, event['receipt']
    if 'unstarted_claim_history' in incoming:
        _conflict(incoming['unstarted_claim_history'] == existing.get('unstarted_claim_history'),
                  'Unstarted claim history is maintained by the ledger')
    previous, candidate = existing.get('unstarted_claim'), incoming.get('unstarted_claim')
    if candidate is not None and candidate != previous:
        _conflict(event['type'] == 'record_evidence', 'Only an evidence claim can consume an unstarted proof')
        _conflict(set(incoming) == {'not_started', 'unstarted_claim'}, 'A new execution claim must be a dedicated evidence event')
        _conflict(task['status'] == 'needs_reconciliation' and step['status'] == 'waiting_human'
                  and existing.get('not_started') is True and not _has_execution_reference(existing),
                  'The human-resumed unstarted proof was consumed or execution may already exist')
        history = copy.deepcopy(existing.get('unstarted_claim_history', []))
        _conflict(not any(row.get('nonce') == candidate['nonce'] for row in history), 'Execution claim nonces cannot be reused')
        if previous is not None:
            history.append({**copy.deepcopy(previous), 'phase': 'no_effect'})
        adjusted = copy.deepcopy(incoming)
        if history: adjusted['unstarted_claim_history'] = history
        return _merge_evidence(existing, adjusted)
    if candidate is not None and event['type'] == 'record_evidence' and set(incoming) == {'not_started', 'unstarted_claim'}:
        raise TaskError('An execution claim cannot be replayed; read its saved evidence instead', 409)
    return _merge_evidence(existing, incoming)


def _file_safe(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
        raise TaskError('Task storage must use private, unlinked regular files', 409)
    if hasattr(os, 'getuid') and info.st_uid != os.getuid(): raise TaskError('Task storage owner changed', 409)


def _directory(path, *, create=False):
    """Open each directory component without following links, including parents."""
    absolute = Path(path).absolute()
    fd = os.open(absolute.anchor, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        for index, part in enumerate(absolute.parts[1:]):
            last = index == len(absolute.parts)-2
            if create and last:
                try: os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError: pass
            next_fd = os.open(part, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0), dir_fd=fd)
            os.close(fd)
            fd = next_fd
        if create and os.fstat(fd).st_mode & 0o077:
            raise TaskError('Task storage directory must be private', 409)
        return fd
    except BaseException:
        os.close(fd)
        raise


class TaskLedger:
    def __init__(self, root):
        self.root = Path(root).absolute()

    @contextmanager
    def _locked(self, scope):
        if not platform_supported():
            raise TaskError('작업 실행은 현재 macOS와 Linux에서 지원합니다. Windows 작업 실행 지원은 준비 중입니다.', 501)
        scope_doc = scope.document()
        root_fd = folder_fd = lock_fd = None
        try:
            root_fd = _directory(self.root, create=True)
            name = hashlib.sha256(_canonical(scope_doc)).hexdigest()
            try: os.mkdir(name, 0o700, dir_fd=root_fd)
            except FileExistsError: pass
            folder_fd = os.open(name, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0), dir_fd=root_fd)
            if os.fstat(folder_fd).st_mode & 0o077: raise TaskError('Task actor directory must be private', 409)
            lock_fd = os.open('.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=folder_fd)
            _file_safe(lock_fd)
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield folder_fd
        except OSError as exc:
            raise TaskError('Task storage is unavailable or contains a linked path', 409) from exc
        finally:
            if lock_fd is not None: os.close(lock_fd)
            if folder_fd is not None: os.close(folder_fd)
            if root_fd is not None: os.close(root_fd)

    def _read(self, folder_fd, scope, identifier):
        if type(identifier) is not str or not TASK_ID.fullmatch(identifier): raise TaskError('Task not found', 404)
        try: fd = os.open(identifier+'.json', os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=folder_fd)
        except FileNotFoundError as exc: raise TaskError('Task not found', 404) from exc
        with os.fdopen(fd, 'rb') as handle:
            _file_safe(handle.fileno())
            raw = handle.read(MAX_RECORD_BYTES+1)
        try:
            if len(raw) > MAX_RECORD_BYTES: raise ValueError('oversized')
            stored = json.loads(raw)
            _object(stored, ('scope', 'plan', 'task'))
            original = validate_plan(stored['plan'])
            task = stored['task']
            _object(task, ('schema_version', 'id', 'revision', 'status', 'goal', 'plan_sha256', 'binding', 'steps',
                           'approval', 'current_step_id', 'message', 'created_at', 'updated_at'), ('authorization',))
            sha = hashlib.sha256(_canonical(original)).hexdigest()
            if (stored['scope'] != scope.document() or task['id'] != identifier or task['schema_version'] != 1
                or task['plan_sha256'] != sha or task['goal'] != original['goal']
                or task['status'] not in STATUSES or type(task['revision']) is not int or task['revision'] < 1
                or len(task['steps']) != len(original['steps'])): raise ValueError('identity mismatch')
            _text(task['binding'], 16384)
            for saved, intended in zip(task['steps'], original['steps']):
                _object(saved, ('id', 'action', 'args', 'label', 'status', 'attempt_id', 'receipt', 'error'))
                if ({key: saved[key] for key in ('action', 'args', 'label')} != intended
                    or saved['status'] not in STEP_STATUSES or not STEP_ID.fullmatch(saved['id'])): raise ValueError('plan changed')
            approval = task['approval']
            if approval is not None:
                _object(approval, ('plan_sha256', 'binding', 'confirmed_at'))
                if approval['plan_sha256'] != sha or approval['binding'] != original['binding'] or not approval['confirmed_at']:
                    raise ValueError('grant changed')
            authorization = task.get('authorization')
            if authorization is not None:
                _object(authorization, ('source', 'scope', 'plan_sha256', 'binding', 'policy_version', 'authorized_at', 'allowed_steps'))
                if (authorization['source'] not in {'user_request', 'oneclick'} or authorization['scope'] != scope.document()
                    or authorization['plan_sha256'] != sha or authorization['binding'] != original['binding']
                    or authorization['policy_version'] != EXECUTION_POLICY_VERSION or approval is not None
                    or authorization['allowed_steps'] != [step['id'] for step in task['steps']]):
                    raise ValueError('execution authorization changed')
                _text(authorization['authorized_at'], 64)
            _text(task['created_at'], 64)
            _text(task['updated_at'], 64)
            if task['message'] is not None: _text(task['message'], 2000, minimum=0)
            if approval is not None: _text(approval['confirmed_at'], 64)
            ids = set()
            phase = 'completed_prefix'
            for step in task['steps']:
                if step['status'] == 'completed':
                    if phase != 'completed_prefix': raise ValueError('out-of-order completion')
                elif step['status'] == 'pending': phase = 'pending_suffix'
                else:
                    if phase != 'completed_prefix': raise ValueError('multiple or out-of-order active steps')
                    phase = 'active'
                if step['id'] in ids: raise ValueError('duplicate step')
                ids.add(step['id'])
                if step['attempt_id'] is not None and (type(step['attempt_id']) is not str or not ATTEMPT_ID.fullmatch(step['attempt_id'])):
                    raise ValueError('invalid attempt')
                if step['receipt'] is not None:
                    if type(step['receipt']) is not dict or len(_canonical(_json(step['receipt']))) > MAX_RECEIPT_BYTES:
                        raise ValueError('invalid receipt')
                if step['error'] is not None: _text(step['error'], 2000)
                if step['status'] == 'pending' and any(step[key] is not None for key in ('attempt_id', 'receipt', 'error')):
                    raise ValueError('pending step has execution data')
                if step['status'] != 'pending' and step['attempt_id'] is None: raise ValueError('missing intent')
                if step['status'] == 'completed' and step['receipt'] is None: raise ValueError('missing receipt')
                if step['status'] in {'completed', 'waiting_job'}: _completion_evidence(step['receipt'])
            active = next((step for step in task['steps'] if step['id'] == task['current_step_id']), None)
            if task['current_step_id'] is not None and active is None: raise ValueError('unknown active step')
            if task['status'] == 'draft':
                if approval is not None or authorization is not None or task['current_step_id'] is not None or any(step['status'] != 'pending' for step in task['steps']):
                    raise ValueError('unapproved execution')
            elif approval is None and authorization is None: raise ValueError('missing execution grant')
            if task['status'] == 'completed':
                if active is not None or any(step['status'] != 'completed' for step in task['steps']):
                    raise ValueError('incomplete completion')
            elif task['status'] == 'ready':
                if active is not None or not any(step['status'] == 'pending' for step in task['steps']):
                    raise ValueError('invalid ready state')
            elif task['status'] in {'running', 'waiting_human', 'waiting_job', 'failed'}:
                expected = {'running': 'running', 'waiting_human': 'waiting_human', 'waiting_job': 'waiting_job', 'failed': 'failed'}[task['status']]
                if active is None or active['status'] != expected: raise ValueError('invalid active state')
            elif task['status'] == 'needs_reconciliation':
                if active is None or active['status'] not in {'uncertain', 'waiting_human', 'waiting_job'}:
                    raise ValueError('invalid reconciliation state')
            return stored
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise TaskError('Task record failed schema or immutable plan/grant verification', 409) from exc

    def _write(self, folder_fd, stored):
        payload = _canonical(stored)
        if len(payload) > MAX_RECORD_BYTES: raise TaskError('Task record exceeds storage limit')
        filename = stored['task']['id']+'.json'
        # Refuse an existing link rather than atomically replacing it unnoticed.
        try:
            info = os.stat(filename, dir_fd=folder_fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise TaskError('Task record cannot be linked', 409)
        except FileNotFoundError: pass
        temporary = '.write-'+uuid.uuid4().hex
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=folder_fd)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, filename, src_dir_fd=folder_fd, dst_dir_fd=folder_fd)
            os.fsync(folder_fd)
        finally:
            try: os.unlink(temporary, dir_fd=folder_fd)
            except FileNotFoundError: pass

    def create(self, scope, value):
        original = validate_plan(value)
        timestamp = _now()
        task = {'schema_version': 1, 'id': 'task_'+uuid.uuid4().hex, 'revision': 1,
            'status': 'draft', 'goal': original['goal'], 'plan_sha256': hashlib.sha256(_canonical(original)).hexdigest(),
            'binding': original['binding'], 'approval': None, 'authorization': None, 'current_step_id': None, 'message': None,
            'created_at': timestamp, 'updated_at': timestamp,
            'steps': [{**copy.deepcopy(step), 'id': 'step_'+uuid.uuid4().hex, 'status': 'pending',
                       'attempt_id': None, 'receipt': None, 'error': None} for step in original['steps']]}
        with self._locked(scope) as folder:
            if sum(1 for name in os.listdir(folder) if TASK_ID.fullmatch(name.removesuffix('.json')) and name.endswith('.json')) >= MAX_TASKS:
                raise TaskError('Task history is full; no new plan was stored', 409)
            self._write(folder, {'scope': scope.document(), 'plan': original, 'task': task})
        return copy.deepcopy(task)

    def read(self, scope, identifier):
        with self._locked(scope) as folder:
            return copy.deepcopy(self._read(folder, scope, identifier)['task'])

    def list(self, scope):
        with self._locked(scope) as folder:
            names = [name[:-5] for name in os.listdir(folder) if name.endswith('.json') and TASK_ID.fullmatch(name[:-5])]
            if len(names) > MAX_TASKS: raise TaskError('Task history exceeds storage limit', 409)
            tasks = [self._read(folder, scope, name)['task'] for name in names]
            return sorted(tasks, key=lambda task: (task['updated_at'], task['id']), reverse=True)

    def event(self, scope, identifier, value):
        event = validate_event(value)
        with self._locked(scope) as folder:
            stored = self._read(folder, scope, identifier)
            task = stored['task']
            _conflict(task['revision'] == event['expected_revision'], 'Task revision changed; read the latest task')
            _conflict(task['binding'] == event['binding'], 'Task execution context changed')
            self._transition(task, event, scope)
            task['revision'] += 1
            task['updated_at'] = _now()
            self._write(folder, stored)
            return copy.deepcopy(task)

    @staticmethod
    def _transition(task, event, scope=None):
        kind = event['type']
        status = task['status']
        if kind == 'quality_result':
            step = next((row for row in task['steps'] if row['id'] == event['step_id']), None)
            assessment = event['receipt'].get('quality_assessment')
            _conflict(task.get('authorization') is not None and step is not None and step['status'] == 'completed'
                      and step['attempt_id'] == event['attempt_id'] and step['action'] in {'evaluate_model', 'evaluate_flow'}
                      and isinstance(assessment, dict) and set(event['receipt']) == {'quality_assessment'}
                      and assessment.get('evaluation_id') == (step['receipt'] or {}).get('evaluation_id')
                      and assessment.get('decision_source') == 'policy' and assessment.get('status') in {'qualified', 'not_qualified', 'input_needed'}
                      and re.fullmatch(r'quality_[a-f0-9]{32}', str(assessment.get('assessment_id'))),
                      'Quality observations require a matching completed evaluation and durable policy assessment')
            step['receipt'] = _merge_evidence(step['receipt'], event['receipt'])
            return
        _conflict(status != 'completed', 'A completed task cannot run again')
        active = next((step for step in task['steps'] if step['id'] == task['current_step_id']), None)
        if active and active['status'] == 'waiting_human' and status != 'needs_reconciliation':
            _conflict(kind in {'pause', 'resume', 'recheck'}, 'Human waiting steps require explicit confirmed resume before any further transition')
        if kind == 'approve':
            _conflict(status == 'draft' and event.get('confirmed') is True and task['approval'] is None,
                      'A person must confirm this draft plan')
            task['approval'] = {'plan_sha256': task['plan_sha256'], 'binding': task['binding'], 'confirmed_at': _now()}
            task['status'] = 'ready'
        elif kind == 'authorize':
            _conflict(status == 'draft' and task['approval'] is None and task.get('authorization') is None
                      and event.get('authorization_source') in {'user_request', 'oneclick'} and scope is not None,
                      'Only a new execution request can authorize a draft plan')
            task['authorization'] = {'source': event['authorization_source'], 'scope': scope.document(),
                'plan_sha256': task['plan_sha256'], 'binding': task['binding'],
                'allowed_steps': [step['id'] for step in task['steps']],
                'policy_version': EXECUTION_POLICY_VERSION, 'authorized_at': _now()}
            task['status'] = 'ready'
        elif kind == 'recheck':
            receipt = active['receipt'] if active else None
            _conflict(status == 'waiting_human' and task.get('authorization') is not None
                      and active is not None and active['status'] == 'waiting_human' and receipt
                      and receipt.get('not_started') is True and receipt.get('reason_code') in {'waiting_input', 'waiting_resource'},
                      'Only an authorized no-effect prerequisite wait can be rechecked automatically')
            task['status'] = 'needs_reconciliation'
        elif kind == 'pause':
            _conflict(status not in {'draft', 'failed', 'paused'}, 'This task cannot be paused')
            task['status'] = 'paused'
            for step in task['steps']:
                if step['status'] == 'running': step['status'] = 'uncertain'
        elif kind == 'resume':
            _conflict((task['approval'] is not None or task.get('authorization') is not None) and status in {'paused', 'running', 'waiting_human', 'waiting_job', 'needs_reconciliation'},
                      'This task cannot be resumed without a confirmed plan')
            active = next((step for step in task['steps'] if step['id'] == task['current_step_id']), None)
            if active:
                _conflict(active['status'] != 'waiting_human' or event.get('confirmed') is True,
                          'Human review must be confirmed before checking its result')
                if active['status'] == 'running': active['status'] = 'uncertain'
                task['status'] = 'needs_reconciliation'
            else: task['status'] = 'ready'
        else:
            _conflict(task['approval'] is not None or task.get('authorization') is not None, 'The task plan has no execution authorization')
            step = next((item for item in task['steps'] if item['id'] == event['step_id']), None)
            _conflict(step is not None, 'Unknown plan step')
            if kind == 'begin_step':
                first = next((item for item in task['steps'] if item['status'] != 'completed'), None)
                _conflict(status == 'ready' and task['current_step_id'] is None and first == step and step['status'] == 'pending',
                          'This step cannot begin or may already have executed')
                _conflict(not any(item['attempt_id'] == event['attempt_id'] for item in task['steps']), 'Attempt identifiers cannot be reused')
                step.update(status='running', attempt_id=event['attempt_id'], error=None)
                task.update(status='running', current_step_id=step['id'])
            else:
                _conflict(step['id'] == task['current_step_id'] and step['attempt_id'] == event['attempt_id'],
                          'Step execution intent does not match')
                _conflict(step['status'] in {'running', 'uncertain', 'waiting_job', 'waiting_human'}, 'Step is not active')
                _conflict(status in {'running', 'needs_reconciliation', 'waiting_job', 'waiting_human', 'paused'}, 'Task is not active')
                if kind in {'record_evidence', 'reconcile_step'}:
                    _conflict(step['status'] != 'waiting_human' or status == 'needs_reconciliation',
                              'Human waiting steps require explicit resume before reconciliation')
                if kind == 'record_evidence':
                    if event.get('message'): step['error'] = event['message']
                    step['receipt'] = _update_receipt(task, step, event)
                    step['status'] = 'uncertain'
                    task['status'] = 'paused' if status == 'paused' else 'needs_reconciliation'
                elif kind == 'fail_step':
                    step.update(status='failed', error=event.get('message') or 'Action failed')
                    task['status'] = 'failed'
                elif kind == 'reconcile_step' and 'receipt' not in event:
                    if event.get('message'): step['error'] = event['message']
                    step['status'] = 'uncertain'
                    task['status'] = 'paused' if status == 'paused' else 'needs_reconciliation'
                else:
                    _conflict(kind != 'complete_step' or step['status'] == 'running', 'Interrupted or waiting steps must reconcile before completion')
                    _conflict(step['status'] != 'waiting_human' or status == 'needs_reconciliation',
                              'Human waiting steps require explicit resume before reconciliation')
                    receipt = _update_receipt(task, step, event)
                    if event.get('wait_kind') != 'human': _completion_evidence(receipt)
                    if 'binding_after' in event:
                        _conflict(step['action'] in BINDING_ACTIONS, 'This action cannot change the execution context')
                        task['binding'] = event['binding_after']
                    step['receipt'] = receipt
                    step['error'] = None
                    if 'wait_kind' in event:
                        step['status'] = 'waiting_'+event['wait_kind']
                        task['status'] = 'paused' if status == 'paused' else step['status']
                    else:
                        step['status'] = 'completed'
                        task['current_step_id'] = None
                        task['status'] = ('completed' if all(item['status'] == 'completed' for item in task['steps'])
                                          else 'paused' if status == 'paused' else 'ready')
        task['message'] = event.get('message')
