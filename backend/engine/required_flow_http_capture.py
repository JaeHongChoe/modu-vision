"""Private data receipt for the one original retained-flow HTTP invocation.

The existing exact owned selector opts in. This module cannot issue a request,
grant work, change a response, qualify drain, release a lease or stop a process.
Only a consumed original carrier promotes the original received/sent bytes.
"""
from __future__ import annotations
import base64
import asyncio
import hashlib
import json
import os
from pathlib import Path
import stat
import re
import sys
import threading
import time

REQUEST_LIMIT = 256 * 1024
RESPONSE_LIMIT = 32 * 1024 * 1024
REQUEST_SECONDS = 60
# Root's fixed original JavaScript JSON.stringify graph, before typed parsing.
# The passive model_dump graph retains its separate original Python identity.
RETAINED_WIRE_GRAPH_SHA = 'dd8d079af861bcc350e4a3a4625957e96647abd2d891093cd4793660db2d69b4'


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _private_directory(path):
    if path.resolve(strict=True) != path:
        raise ValueError('Private receipt directory changed')
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('Private receipt directory differs')
    return _identity(info)


class _OriginalFlowHTTPCapture:
    def __init__(self, selector, cache, cache_current, context_current):
        self.selector = selector
        self.cohort = selector.cohort
        self.cache, self.cache_current = cache, cache_current
        self.context_current = context_current
        self.context_code = context_current.__code__
        self.pid = os.getpid()
        self.admission, self.observer = selector.admission, selector.admission._activity_observer
        self.lock = threading.Lock()
        self.attempt = None
        self.refusals = []
        self.finished = False
        self.readbacks = _OriginalGETReadbacks(self)
        self.exchanges = OriginalFlowExchanges(self)
        self.output = None
        nonce = cache.get('proof', {}).get('nonce')
        if (type(nonce) is not str or len(nonce) != 32
                or any(c not in '0123456789abcdef' for c in nonce)):
            raise ValueError('Original launch data nonce differs')
        self.launch_nonce = nonce
        root = cache['root']
        if type(root) is not Path:
            root = Path(root)
        self.ancestors = []
        for path in (root, root/'.application-runtime-homes', root/'.application-runtime-homes'/nonce):
            self.ancestors.append((path, _private_directory(path)))
        self._current()
        output = self.ancestors[-1][0]/'required-flow-http'
        os.mkdir(output, 0o700)  # An existing output is never reused or adopted.
        self.output = output
        self.output_identity = _private_directory(output)
        self._write('prepared.json', {
            'schema': 'modu-vision.original-required-flow-http-prepared/v1',
            'process_pid': self.pid, 'launch_nonce': self.launch_nonce,
            'capture_nonce': selector.nonce,
            'profile_id': self.cohort['profile_id'], 'maximum_matching_requests': 1,
            'request_budget_seconds': REQUEST_SECONDS,
            'request_capture_limit': REQUEST_LIMIT, 'response_capture_limit': RESPONSE_LIMIT,
            'permission_authority': False, 'lease_authority': False, 'parent_credit': False,
        })

    def _current(self):
        selector = self.selector
        if (os.getpid() != self.pid or self.cache_current() is not self.cache
                or self.context_current.__code__ is not self.context_code
                or self.context_current() != self.cache['context']
                or self.cache.get('admission') is not self.admission
                or self.cache.get('proof', {}).get('nonce') != self.launch_nonce
                or selector.pid != self.pid
                or selector.cohort is not self.cohort or selector.original_cohort is not self.cohort
                or selector.current_admission() is not self.admission
                or selector.current_admission.__code__ is not selector.admission_code
                or selector.app.state is not selector.state
                or selector.state.owned_work_admission is not self.admission
                or selector.state.api_token is not selector.token or selector.state.accounts is not selector.accounts
                or selector.auth_type.__call__ is not selector.auth_call or selector.auth_call.__code__ is not selector.auth_code
                or selector.state.required_retained_flow_capture is not selector
                or selector.admission._activity_observer is not self.observer):
            raise ValueError('Original private HTTP data binding changed')
        for path, expected in self.ancestors:
            if _private_directory(path) != expected:
                raise ValueError('Original private receipt ancestor changed')
        if self.output is not None and _private_directory(self.output) != self.output_identity:
            raise ValueError('Original private receipt directory replaced')

    def refuse(self, code):
        if code not in self.refusals and len(self.refusals) < 16:
            self.refusals.append(code)

    def _write(self, name, value):
        """Data-only, exact fixed names, no replacement and no retained handles."""
        try:
            self._current()
            if (name not in {'prepared.json', 'terminal.json', 'matching-request-replayed.json'}
                    and re.fullmatch(r'readback-00[0-7]\.json', name) is None
                    and re.fullmatch(r'exchange-00[01]\.json', name) is None
                    and re.fullmatch(r'post-counts-(?:0[0-9]{2}|1[01][0-9]|12[0-7])\.json', name) is None):
                raise ValueError('Private receipt name differs')
            raw = (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()
            directory = os.open(self.output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if _identity(os.fstat(directory)) != self.output_identity:
                    raise ValueError('Private receipt original directory handle differs')
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size != len(raw)):
                        raise ValueError('Private receipt file identity differs')
                published = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if _identity(published) != _identity(info) or published.st_size != len(raw) or published.st_nlink != 1:
                    raise ValueError('Private receipt published file changed')
                self._current()
            finally:
                os.close(directory)
            return hashlib.sha256(raw).hexdigest()
        except Exception:
            # Diagnostics cannot replace an original request result/exception.
            self.refuse('receipt_write_failed')
            return None

    def begin(self, admission, activity):
        try:
            self._current()
            if self.cache.get('ready') is not True:
                return None
            scope = activity.scope
            if (scope.get('type') != 'http' or scope.get('method') != 'POST'
                    or scope.get('path') != '/api/flowchart/run'):
                return None
            if not self.lock.acquire(blocking=False):
                self.refuse('concurrent_matching_request')
                return None
            try:
                if self.attempt is not None or self.finished:
                    self.refuse('matching_request_replayed')
                    self._write('matching-request-replayed.json', {
                        'schema': 'modu-vision.original-required-flow-http-replay-refusal/v1',
                        'process_pid': self.pid, 'launch_nonce': self.launch_nonce,
                        'capture_nonce': self.selector.nonce, 'complete': False,
                        'permission_authority': False, 'lease_authority': False, 'parent_credit': False,
                    })
                    return None
                if (admission is not self.admission or not self.observer._valid(activity)
                        or scope.get('app') is not self.selector.app):
                    self.refuse('original_activity_changed')
                    return None
                row = self.observer._records.get(activity.token)
                if row is None or row.get('carrier') is not activity or row.get('accepted') is not True:
                    self.refuse('original_activity_not_admitted')
                    return None
                self.attempt = _OriginalHTTPAttempt(self, activity, row)
                if scope.get('query_string', b'') != b'':
                    self.refuse('query_not_empty')
                return self.attempt
            finally:
                self.lock.release()
        except Exception:
            self.refuse('original_activity_unavailable')
            return None

    def selected(self, carrier):
        """Called only after all original one-use consume predicates succeed."""
        try:
            self._current()
            from backend.engine.required_flow_passive_capture import _OriginalRequiredFlowRequest
            attempt = self.attempt
            if (type(carrier) is not _OriginalRequiredFlowRequest or self.selector.issued is not carrier
                    or carrier.selector is not self.selector or carrier.scope is not attempt.activity.scope
                    or carrier.request.scope is not carrier.scope or carrier.request.app is not self.selector.app
                    or carrier.used is not True or self.selector.used is not True
                    or threading.get_ident() != carrier.thread or attempt.selected_carrier is not None):
                raise ValueError('Only the original consumed carrier may promote data')
            attempt.selected_carrier = carrier
            self.readbacks.selected(carrier)
            self.exchanges.selected(carrier)
        except Exception:
            self.refuse('original_carrier_changed')


class _OriginalHTTPAttempt:
    def __init__(self, owner, activity, row):
        self.owner, self.activity, self.row = owner, activity, row
        self.started_monotonic, self.started_epoch = time.monotonic(), time.time()
        self.request, self.response = bytearray(), bytearray()
        self.request_receives = self.successful_sends = 0
        self.request_ended = self.response_ended = False
        self.status = None
        self.selected_carrier = None
        self.pending_send = None

    def _current(self):
        self.owner._current()
        if time.monotonic() - self.started_monotonic >= REQUEST_SECONDS:
            self.owner.refuse('request_deadline')

    def received(self, message):
        try:
            self._current()
            if (message.get('type') != 'http.request' or self.request_ended
                    or type(message.get('body', b'')) is not bytes
                    or type(message.get('more_body', False)) is not bool):
                self.owner.refuse('request_framing')
                return
            self.request_receives += 1
            body = message.get('body', b'')
            if len(self.request)+len(body) > REQUEST_LIMIT:
                self.owner.refuse('request_limit')
                self.request.clear()
            elif 'request_limit' not in self.owner.refusals:
                self.request.extend(body)
            self.request_ended = not message.get('more_body', False)
        except Exception:
            self.owner.refuse('request_observation_failed')

    def send_attempt(self, message):
        try:
            self._current()
            kind = message.get('type')
            if kind == 'http.response.start':
                frame = (kind, message.get('status'), tuple(message.get('headers', ())))
            elif kind == 'http.response.body':
                frame = (kind, message.get('body', b''), message.get('more_body', False))
            else:
                self.owner.refuse('response_framing')
                return None
            if self.pending_send is not None:
                self.owner.refuse('overlapping_send')
            self.pending_send = frame
            return frame
        except Exception:
            self.owner.refuse('send_observation_failed')
            return None

    def sent(self, message, original_frame):
        try:
            self._current()
            pending = self.pending_send
            self.pending_send = None
            if pending is None or original_frame is not pending:
                self.owner.refuse('original_send_unmatched')
                return
            kind = message.get('type')
            now = (kind, message.get('status'), tuple(message.get('headers', ()))) if kind == 'http.response.start' else (kind, message.get('body', b''), message.get('more_body', False))
            if now != pending:
                self.owner.refuse('sent_frame_changed')
                return
            self.successful_sends += 1
            if kind == 'http.response.start':
                if self.status is not None or type(pending[1]) is not int:
                    self.owner.refuse('response_framing')
                self.status = pending[1]
                content_types = [v for k, v in pending[2] if type(k) is bytes and k.lower() == b'content-type']
                if len(content_types) != 1 or not content_types[0].lower().startswith(b'application/json'):
                    self.owner.refuse('response_not_JSON')
            else:
                body, more = pending[1:]
                if self.status is None or self.response_ended or type(body) is not bytes or type(more) is not bool:
                    self.owner.refuse('response_framing')
                    return
                if len(self.response)+len(body) > RESPONSE_LIMIT:
                    self.owner.refuse('response_limit')
                    self.response.clear()
                elif 'response_limit' not in self.owner.refusals:
                    self.response.extend(body)
                self.response_ended = more is False
        except Exception:
            self.owner.refuse('send_observation_failed')

    def finish(self, normal):
        try:
            self._current()
            owner = self.owner
            owner.finished = True
            if self.selected_carrier is None:
                owner.refuse('original_carrier_not_consumed')
            if (not normal or not self.request_ended or not self.response_ended or self.pending_send is not None
                    or self.status != 200 or self.row.get('released') is not True
                    or self.row.get('normal_return') is not True or self.row.get('completed') is not True):
                owner.refuse('original_HTTP_not_completed')
            if owner.selector.token.encode() in self.request or owner.selector.token.encode() in self.response:
                owner.refuse('private_token_in_body')
            if not owner.refusals:
                try:
                    def pairs(items):
                        result = {}
                        for key, value in items:
                            if key in result: raise ValueError('Duplicate data key')
                            result[key] = value
                        return result
                    def finite(_): raise ValueError('Nonfinite original JSON data')
                    body = json.loads(self.request, object_pairs_hook=pairs, parse_constant=finite)
                    from backend.engine.required_flow_passive_capture import _graph_sha, RETAINED_GRAPH_SHA
                    if (type(body) is not dict or set(body) != {'image_path','image_id','pipeline','project_id','execution_target','device','compute_profile_id'}
                            or body['execution_target'] != 'selected_compute' or body['device'] not in ('cuda','cuda:0')
                            or body['compute_profile_id'] != owner.cohort['profile_id'] or _graph_sha(body['pipeline']) not in (RETAINED_GRAPH_SHA, RETAINED_WIRE_GRAPH_SHA)):
                        raise ValueError('Original fixed request data differs')
                    json.loads(self.response, object_pairs_hook=pairs, parse_constant=finite)
                except (ValueError, TypeError):
                    owner.refuse('original_JSON_data_differs')
            complete = not owner.refusals
            receipt = {
                'schema': 'modu-vision.original-required-flow-http-terminal/v1',
                'process_pid': owner.pid, 'launch_nonce': owner.launch_nonce,
                'capture_nonce': owner.selector.nonce, 'complete': complete,
                'method': 'POST', 'route': 'FLOWCHART_RUN', 'status': self.status,
                'started_epoch': self.started_epoch, 'finished_epoch': time.time(),
                'elapsed_monotonic_seconds': time.monotonic()-self.started_monotonic,
                'request_receives': self.request_receives, 'successful_sends': self.successful_sends,
                'original_request_identity': self.row['identity'],
                'original_entered_sequence': self.row['entered_sequence'],
                'original_released_sequence': self.row['released_sequence'],
                'refusals': list(owner.refusals),
                'headers_query_or_tokens_serialized': False,
                'server_ASGI_send_receipt': True, 'client_receive_proven': False,
                'permission_authority': False, 'lease_authority': False, 'parent_credit': False,
            }
            if complete:
                for name, raw in (('request', self.request), ('response', self.response)):
                    receipt[name+'_bytes'] = len(raw)
                    receipt[name+'_sha256'] = hashlib.sha256(raw).hexdigest()
                    receipt[name+'_base64'] = base64.b64encode(raw).decode('ascii')
            published = owner._write('terminal.json', receipt)
            self.owner.readbacks.post_completed(self, complete, published)
            self.owner.exchanges.post_completed(self, complete, published)
        except Exception:
            self.owner.refuse('terminal_observation_failed')
        finally:
            self.request.clear()
            self.response.clear()


def bind_original_flow_http_capture(selector):
    if selector is None:
        return None
    try:
        from backend.engine import application_launch_handshake as handshake
        from backend.engine.required_flow_passive_capture import _OriginalRequiredFlowSelector
        if type(selector) is not _OriginalRequiredFlowSelector or hasattr(selector, 'http_capture'):
            return None
        capture = _OriginalFlowHTTPCapture(selector, handshake._CACHE, lambda: handshake._CACHE, handshake._context)
        selector.http_capture = capture
        return capture
    except Exception:
        # Missing/refused prepared receipt cannot grant an actual fixture pass.
        return None


def begin_original_flow_http_capture(admission, activity):
    if admission is None or activity is None:
        return None
    try:
        selector = getattr(activity.scope.get('app').state, 'required_retained_flow_capture', None)
        capture = getattr(selector, 'http_capture', None)
        if type(capture) is not _OriginalFlowHTTPCapture or capture.selector is not selector:
            return None
        if activity.scope.get('method') == 'GET':
            return capture.readbacks.begin_activity(admission, activity)
        delegate = capture.begin(admission, activity) if activity.scope.get('path') == '/api/flowchart/run' else capture.exchanges.begin_activity(admission, activity)
        return capture.exchanges.classify_original_POST(admission, activity, delegate)
    except Exception:
        return None


def observe_original_flow_http_selection(carrier):
    try:
        capture = getattr(carrier.selector, 'http_capture', None)
        if type(capture) is _OriginalFlowHTTPCapture and capture.selector is carrier.selector:
            capture.selected(carrier)
    except Exception:
        pass


GET_LIMIT = 1024 * 1024
GET_SECONDS = 10
GET_COUNT = 8


def _get_path(path):
    return type(path) is str and (path == '/api/project/current' or re.fullmatch(r'/api/flowchart/pipelines/[0-9a-f]{32}', path) is not None)


def _json_data(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate original JSON key')
            result[key] = value
        return result
    def finite(_): raise ValueError('Nonfinite original JSON')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=finite)


def _shared_lineage(witness):
    # Session and storage selectors are comparison data in memory only.
    return (witness.account['id'], witness.project['id'], witness.owner.workspace,
            witness.decision.role, witness.session,
            *(witness.project.get(key) for key in witness.project_keys))


def _owned_small_json(path):
    if path.resolve(strict=True) != path: raise ValueError('Original saved data alias changed')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1
                or before.st_size > GET_LIMIT): raise ValueError('Original saved data shape differs')
        raw = stream.read(GET_LIMIT + 1); after = os.fstat(stream.fileno())
    fields = lambda value: (_identity(value), value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if len(raw) > GET_LIMIT or fields(before) != fields(after) or fields(after) != fields(path.lstat()):
        raise ValueError('Original saved data changed')
    return _json_data(raw), hashlib.sha256(raw).hexdigest()


def _version_data(project, version):
    """One exact already-requested saved record, never process/auth authority."""
    from backend.engine.required_flow_passive_capture import _graph_sha, RETAINED_GRAPH_SHA
    root = Path(project['project_dir'])
    path = root/'flowcharts'/'versions'/(version+'.json')
    if path.resolve(strict=True) != path or not path.is_relative_to(root):
        raise ValueError('Original version path changed')
    value, source_sha = _owned_small_json(path)
    if (value.get('version_id') != version or value.get('source_dataset_path') != project.get('source_dataset_dir')
            or value.get('recipe_task') != project.get('task') or _graph_sha(value.get('pipeline')) != RETAINED_GRAPH_SHA):
        raise ValueError('Original saved source/graph differs')
    return source_sha, value['pipeline']


class _OriginalGETReadbacks:
    def __init__(self, owner):
        self.owner = owner
        self.attempts, self.provisional, self.refusals = [], [], []
        self.lineage = self.active_version = self.post_terminal_sha = None
        self.published = 0

    def refuse(self, code):
        if code not in self.refusals and len(self.refusals) < 16: self.refusals.append(code)

    def for_scope(self, scope):
        return next((attempt for attempt in self.attempts if attempt.activity.scope is scope and not attempt.finished), None)

    def begin_activity(self, admission, activity):
        try:
            owner = self.owner; owner._current(); scope = activity.scope
            if (owner.cache.get('ready') is not True or not _get_path(scope.get('path'))
                    or scope.get('query_string', b'') != b'' or scope.get('method') != 'GET'
                    or scope.get('type') != 'http' or scope.get('app') is not owner.selector.app): return None
            if len(self.attempts) >= GET_COUNT:
                self.refuse('GET_count_limit'); return None
            row = owner.observer._records.get(activity.token)
            if (admission is not owner.admission or not owner.observer._valid(activity) or row is None
                    or row.get('carrier') is not activity or row.get('accepted') is not True): return None
            attempt = _OriginalGETAttempt(self, activity, row)
            self.attempts.append(attempt)
            return attempt
        except Exception:
            self.refuse('GET_activity_unavailable'); return None

    def selected(self, carrier):
        try:
            witness = self.owner.selector.shared_owner.current(carrier.request)
            self.lineage = _shared_lineage(witness)
            project = witness.project
            root = Path(project['project_dir'])
            active = root/'flowcharts'/'active.json'
            value, _ = _owned_small_json(active); version = value.get('version_id')
            if (type(version) is not str or re.fullmatch('[0-9a-f]{32}', version) is None
                    or value.get('project_id') != project['id'] or value.get('source_dataset_path') != project.get('source_dataset_dir')
                    or value.get('recipe_task') != project.get('task') or value.get('labelset_id') != project.get('active_labelset_id')):
                raise ValueError('Original active source differs')
            self.active_version = (version, _version_data(project, version)[0])
        except Exception:
            self.refuse('GET_POST_lineage_unavailable')

    def post_completed(self, attempt, complete, published):
        if sys._getframe(1).f_code is not _OriginalHTTPAttempt.finish.__code__: return
        if (attempt is self.owner.attempt and self.owner.finished is True and complete is True and type(published) is str
                and re.fullmatch('[0-9a-f]{64}', published) is not None and not self.owner.refusals
                and attempt.row.get('released') is True and attempt.row.get('normal_return') is True and attempt.row.get('completed') is True):
            self.post_terminal_sha = published
            self.promote()
        else:
            self.provisional.clear()

    def promote(self):
        if self.post_terminal_sha is None:
            return
        if self.lineage is None or self.active_version is None or self.refusals:
            self.provisional.clear(); return
        while self.provisional:
            witness_lineage, receipt = self.provisional.pop(0)
            if witness_lineage != self.lineage: continue
            if receipt['phase'] == 'after_post' and receipt['path'] != '/api/project/current' and (receipt.get('saved_version'), receipt.get('saved_record_sha256')) != self.active_version:
                continue
            if self.published >= GET_COUNT:
                self.refuse('GET_count_limit'); return
            receipt['post_terminal_sha256'] = self.post_terminal_sha
            self.owner._write('readback-%03d.json' % self.published, receipt)
            self.published += 1


class _OriginalGETWitness:
    def __init__(self, original, attempt):
        self.original, self.attempt = original, attempt
        self.active, self.normal_closed, self.valid_terminal_send = True, False, False

    def __copy__(self): raise ValueError('Original GET witness cannot be copied')
    def __deepcopy__(self, memo): return self.__copy__()
    def __reduce_ex__(self, protocol): return self.__copy__()

    def current(self):
        from starlette.requests import Request
        original = self.original
        return (self.active and self.attempt.witness is self
                and self.attempt.readbacks.for_scope(original.scope) is self.attempt
                and asyncio.current_task() is original.task and asyncio.get_running_loop() is original.loop
                and original.original(Request(original.scope)))

    def closing_current(self):
        # The inner project ContextVar has naturally reset before auth exits.
        # Its live value was proved during the successful terminal send above.
        original = self.original; owner = original.owner; scope = original.scope; state = scope.get('state', {})
        return (self.active and self.valid_terminal_send and self.attempt.witness is self and owner.original()
                and asyncio.current_task() is original.task and asyncio.get_running_loop() is original.loop
                and scope.get('app') is owner.app and state is original.state
                and owner.middleware is original.middleware and owner.app.middleware_stack is owner.stack and original.middleware.app is owner.downstream
                and tuple(scope.get('headers', ())) == original.headers and state.get('account_session_token') is original.session
                and state.get('account_user') is original.account and state.get('scoped_project') is original.project and state.get('permission_decision') is original.decision
                and tuple(original.account.items()) == original.account_values and tuple(vars(original.decision).items()) == original.decision_values
                and tuple(original.project.get(key) for key in original.project_keys) == original.project_values)


def begin_original_get_readback(owner, middleware, scope):
    """Only the actual final original shared-auth generator can attach data."""
    try:
        from backend.engine.original_shared_required_flow import OriginalSharedRequiredFlowOwner, _OriginalSharedContinuation
        if type(owner) is not OriginalSharedRequiredFlowOwner or not owner.original(): return None
        hook = sys._getframe(1); enter = hook.f_back; auth = enter.f_back if enter is not None else None
        if (hook.f_code is not owner.hook_code or hook.f_globals is not owner.hook_body.__globals__
                or enter is None or enter.f_code is not owner.enter_code
                or auth is None or auth.f_code is not owner.auth_body_code or auth.f_globals is not owner.auth_globals
                or auth.f_lasti not in owner.entry_offsets): return None
        if getattr(getattr(enter.f_locals.get('self'), 'gen', None), 'gi_frame', None) is not hook: return None
        if (type(scope) is not dict or scope.get('type') != 'http' or scope.get('method') != 'GET'
                or not _get_path(scope.get('path')) or scope.get('query_string', b'') != b''
                or scope.get('app') is not owner.app or type(middleware) is not owner.auth_type or middleware.project_app is not owner.app): return None
        state = scope.get('state', {}); account = state.get('account_user'); project = state.get('scoped_project'); decision = state.get('permission_decision')
        values = auth.f_locals
        if (values.get('self') is not middleware or values.get('scope') is not scope or values.get('state') is not state
                or values.get('store') is not owner.store or values.get('account') is not account or values.get('project') is not project
                or values.get('decision') is not decision or values.get('token') is not state.get('account_session_token')
                or values.get('path') != scope['path'] or values.get('method') != 'GET'
                or type(account) is not dict or type(project) is not dict or type(decision) is not owner.decision_type
                or decision.allowed is not True or decision.reason != 'allowed' or decision.actor_id != account.get('id')
                or decision.project_id != project.get('id') or decision.workspace_id != owner.workspace
                or decision.action != owner.permission_action(scope['path'], 'GET') or values.get('role') != decision.role
                or type(state.get('account_session_token')) is not str or not state['account_session_token']): return None
        node = owner.app.middleware_stack
        for _ in range(16):
            if node is middleware: break
            node = getattr(node, 'app', None)
            if node is None: return None
        else: return None
        # Same original member/stack binding as POST; this issues no POST carrier.
        if owner.middleware is None: owner.middleware, owner.stack, owner.downstream = middleware, owner.app.middleware_stack, middleware.app
        if middleware is not owner.middleware or owner.app.middleware_stack is not owner.stack or middleware.app is not owner.downstream: return None
        selector = getattr(owner.state, 'required_retained_flow_capture', None)
        capture = getattr(selector, 'http_capture', None)
        if type(capture) is not _OriginalFlowHTTPCapture or capture.selector.shared_owner is not owner: return None
        attempt = capture.readbacks.for_scope(scope)
        if attempt is None or attempt.witness is not None: return None
        witness = _OriginalGETWitness(_OriginalSharedContinuation(owner, middleware, scope, state, account, project, decision, asyncio.current_task(), asyncio.get_running_loop()), attempt)
        attempt.witness = witness
        return witness
    except Exception:
        return None


def end_original_get_readback(witness, normal):
    try:
        if type(witness) is not _OriginalGETWitness or sys._getframe(1).f_code is not witness.original.owner.hook_code: return
        witness.normal_closed = normal is True and witness.closing_current()
        witness.active = witness.original.active = False
    except Exception:
        witness.active = False


class _OriginalGETAttempt(_OriginalHTTPAttempt):
    def __init__(self, readbacks, activity, row):
        super().__init__(readbacks.owner, activity, row)
        self.readbacks, self.witness, self.finished = readbacks, None, False
        self.refusals = []
        self.scope_facts = tuple(activity.scope.get(key, b'' if key == 'query_string' else None) for key in ('method','path','query_string','app'))
        self.phase = 'after_post' if readbacks.post_terminal_sha is not None else 'before_post' if readbacks.owner.attempt is None else 'during_post'
        self.declared_empty = True
        lengths = []
        for key, value in activity.scope.get('headers', ()):
            if type(key) is not bytes or type(value) is not bytes: self.declared_empty = False; continue
            if key.lower() == b'transfer-encoding': self.declared_empty = False
            if key.lower() == b'content-length': lengths.append(value)
        if lengths not in ([], [b'0']): self.declared_empty = False
        if not self.declared_empty: self.refuse('GET_body_not_declared_empty')

    def refuse(self, code):
        if code not in self.refusals and len(self.refusals) < 16: self.refusals.append(code)

    def _current(self):
        self.owner._current()
        if not self.scope_current(): self.refuse('GET_scope_changed')
        if time.monotonic() - self.started_monotonic >= GET_SECONDS: self.refuse('GET_deadline')
        if self.witness is not None and not self.witness.current(): self.refuse('GET_authorization_changed')

    def scope_current(self):
        return self.scope_facts == tuple(self.activity.scope.get(key, b'' if key == 'query_string' else None) for key in ('method','path','query_string','app'))

    def received(self, message):
        try:
            self._current(); self.request_receives += 1
            if message.get('type') != 'http.request' or message.get('body', b'') != b'' or type(message.get('body', b'')) is not bytes or message.get('more_body', False) is not False:
                self.refuse('GET_observed_body_not_empty')
        except Exception: self.refuse('GET_receive_observation_failed')

    def send_attempt(self, message):
        # Reuse original matching frame algorithm with a local refusal owner.
        return self._frame_call(super().send_attempt, message)

    def _frame_call(self, function, *args):
        # The original methods report only to this exact per-GET refusal sink.
        owner = self.owner
        self.owner = _GETFrameOwner(owner, self)
        try: return function(*args)
        finally: self.owner = owner

    def sent(self, message, original_frame):
        try:
            body = message.get('body', b'')
            if message.get('type') == 'http.response.body' and (type(body) is not bytes or len(self.response) + len(body) > GET_LIMIT):
                self.refuse('GET_response_limit'); self.response.clear(); self.pending_send = None; return
            self._frame_call(super().sent, message, original_frame)
            if self.response_ended and self.witness is not None:
                self.witness.valid_terminal_send = self.witness.current()
        except Exception: self.refuse('GET_send_observation_failed')

    def finish(self, normal):
        try:
            self.owner._current()
            witness = self.witness
            if (self.finished or not self.scope_current() or time.monotonic()-self.started_monotonic >= GET_SECONDS or not normal or not self.response_ended
                    or self.pending_send is not None or self.status != 200 or witness is None or not witness.normal_closed or not witness.valid_terminal_send
                    or self.row.get('released') is not True or self.row.get('normal_return') is not True or self.row.get('completed') is not True):
                self.refuse('GET_original_not_completed')
            if self.refusals: return
            original = witness.original
            if self.owner.selector.token.encode() in self.response or original.session.encode() in self.response:
                self.refuse('GET_private_token_in_body'); return
            data = _json_data(self.response); project = original.project; path = self.activity.scope['path']
            version, source_sha = None, None
            if path == '/api/project/current':
                if type(data) is not dict or any(data.get(key) != project.get(key) for key in original.project_keys):
                    self.refuse('GET_project_data_differs'); return
            else:
                version = path.rsplit('/', 1)[1]; source_sha, pipeline = _version_data(project, version)
                from backend.engine.flowchart_engine import FlowchartPipeline
                if data != FlowchartPipeline.model_validate(pipeline).model_dump(mode='json'):
                    self.refuse('GET_saved_response_differs'); return
            receipt = {'schema':'modu-vision.original-required-flow-http-readback/v1','complete':True,'method':'GET','path':path,'phase':self.phase,
                'process_pid':self.owner.pid,'launch_nonce':self.owner.launch_nonce,'capture_nonce':self.owner.selector.nonce,
                'actor_id':original.account['id'],'project_id':project['id'],'workspace_id':original.owner.workspace,
                'actual_original_shared_authorization':True,'status':self.status,'started_epoch':self.started_epoch,'finished_epoch':time.time(),
                'elapsed_monotonic_seconds':time.monotonic()-self.started_monotonic,'successful_sends':self.successful_sends,
                'original_request_identity':self.row['identity'],'original_entered_sequence':self.row['entered_sequence'],'original_released_sequence':self.row['released_sequence'],
                'request_body_declared_empty':self.declared_empty,'request_body_receive_observed':self.request_receives>0,
                'headers_query_or_tokens_serialized':False,'server_ASGI_send_receipt':True,'client_receive_proven':False,
                'permission_authority':False,'lease_authority':False,'parent_credit':False,'refusals':[],
                'saved_version':version,'saved_record_sha256':source_sha,'response_bytes':len(self.response),
                'response_sha256':hashlib.sha256(self.response).hexdigest(),'response_base64':base64.b64encode(self.response).decode('ascii')}
            self.readbacks.provisional.append((_shared_lineage(original), receipt))
            self.readbacks.promote()
        except Exception:
            self.refuse('GET_terminal_observation_failed')
        finally:
            self.finished = True; self.response.clear(); self.request.clear()
            if self.witness is not None:
                self.witness.original.task = self.witness.original.loop = None


class _GETFrameOwner:
    def __init__(self, owner, attempt): self.original, self.attempt, self.refusals = owner, attempt, attempt.refusals
    def _current(self): return self.original._current()
    def refuse(self, code): return self.attempt.refuse(code)


EXCHANGE_SECONDS = {'/api/flowchart/pipeline': 10, '/api/export/flow': 115}
POST_CLASSES = ('SAVE', 'RUN', 'EXPORT', 'FORBIDDEN_TRAIN', 'OTHER')


class OriginalFlowExchanges(_OriginalGETReadbacks):
    """Two original UI exchanges and finite method classes, never a sender."""
    def __init__(self, owner):
        super().__init__(owner)
        self.classified, self.count_publications = [], 0
        self.classification_overflow = False
        self.published_paths = set()

    def begin_activity(self, admission, activity):
        try:
            owner = self.owner; owner._current(); scope = activity.scope
            if (owner.cache.get('ready') is not True or scope.get('type') != 'http' or scope.get('method') != 'POST'
                    or scope.get('path') not in EXCHANGE_SECONDS or scope.get('app') is not owner.selector.app): return None
            if len(self.attempts) >= 2 or any(attempt.activity.scope['path'] == scope['path'] for attempt in self.attempts):
                self.refuse('exchange_replayed'); return None
            row = owner.observer._records.get(activity.token)
            if (admission is not owner.admission or not owner.observer._valid(activity) or row is None
                    or row.get('carrier') is not activity or row.get('accepted') is not True): return None
            attempt = _OriginalExchangeAttempt(self, activity, row); self.attempts.append(attempt)
            return attempt
        except Exception:
            self.refuse('exchange_activity_unavailable'); return None

    def classify_original_POST(self, admission, activity, delegate):
        try:
            self.owner._current(); scope = activity.scope
            if (scope.get('type') != 'http' or scope.get('method') != 'POST' or scope.get('app') is not self.owner.selector.app
                    or admission is not self.owner.admission or not self.owner.observer._valid(activity)): return delegate
            row = self.owner.observer._records.get(activity.token)
            if row is None or row.get('carrier') is not activity or row.get('accepted') is not True: return delegate
            if len(self.classified) >= 128:
                self.classification_overflow = True; return delegate
            if any(item['row'] is row for item in self.classified):
                self.classification_overflow = True; return delegate
            path = scope.get('path', '')
            kind = {'/api/flowchart/pipeline':'SAVE','/api/flowchart/run':'RUN','/api/export/flow':'EXPORT'}.get(path)
            if kind is None: kind = 'FORBIDDEN_TRAIN' if re.search(r'/(?:train|fit|training|autodl)(?:/|$)', path) else 'OTHER'
            item = {'row':row,'class':kind,'finished':False,'normal':False,'published':False}
            self.classified.append(item)
            return _OriginalClassifiedPOST(self, item, delegate)
        except Exception:
            self.classification_overflow = True; return delegate

    def publish_counts(self):
        if self.post_terminal_sha is None: return
        for item in self.classified:
            if not item['finished'] or item['published']: continue
            if self.count_publications >= 128:
                self.classification_overflow = True; return
            counts = {kind:sum(record['class'] == kind for record in self.classified) for kind in POST_CLASSES}
            rows = [{'class':record['class'],'original_request_identity':record['row']['identity'],
                'original_entered_sequence':record['row']['entered_sequence'],
                'original_released_sequence':record['row'].get('released_sequence'),
                'finished':record['finished'],'normal_return':record['normal']} for record in self.classified]
            value = {'schema':'modu-vision.original-required-flow-POST-classes/v1','counts':counts,'rows':rows,
                'complete':not self.classification_overflow and all(record['finished'] and record['normal'] for record in self.classified),
                'pending':sum(not record['finished'] for record in self.classified),'overflow':self.classification_overflow,
                'actual_original_request_activity':True,'authorization_inferred_from_counts':False,
                'post_terminal_sha256':self.post_terminal_sha,'process_pid':self.owner.pid,'capture_nonce':self.owner.selector.nonce,
                'headers_query_or_tokens_serialized':False,'unrelated_paths_or_bodies_serialized':False,
                'permission_authority':False,'lease_authority':False,'parent_credit':False}
            self.owner._write('post-counts-%03d.json' % self.count_publications, value)
            item['published'] = True; self.count_publications += 1

    def post_completed(self, attempt, complete, published):
        if sys._getframe(1).f_code is not _OriginalHTTPAttempt.finish.__code__: return
        if (attempt is self.owner.attempt and self.owner.finished is True and complete is True
                and type(published) is str and re.fullmatch('[0-9a-f]{64}', published) is not None and not self.owner.refusals
                and attempt.row.get('released') is True and attempt.row.get('normal_return') is True and attempt.row.get('completed') is True):
            self.post_terminal_sha = published
            self.promote(); self.publish_counts()
        else: self.provisional.clear()

    def promote(self):
        if self.post_terminal_sha is None: return
        if self.lineage is None or self.active_version is None:
            self.provisional.clear(); return
        while self.provisional:
            lineage, receipt = self.provisional.pop(0)
            if lineage != self.lineage or (receipt['saved_version'], receipt['saved_record_sha256']) != self.active_version: continue
            path = receipt['path']
            if path in self.published_paths: continue
            if (path == '/api/flowchart/pipeline' and receipt['phase'] != 'before_post'
                    or path == '/api/export/flow' and receipt['phase'] != 'after_post'): continue
            if len(self.published_paths) >= 2: return
            receipt['post_terminal_sha256'] = self.post_terminal_sha
            self.owner._write('exchange-%03d.json' % len(self.published_paths), receipt)
            self.published_paths.add(path)


class _OriginalClassifiedPOST:
    def __init__(self, owner, item, delegate): self.owner, self.item, self.delegate = owner, item, delegate
    def received(self, message):
        if self.delegate is not None: self.delegate.received(message)
    def send_attempt(self, message):
        return self.delegate.send_attempt(message) if self.delegate is not None else None
    def sent(self, message, frame):
        if self.delegate is not None: self.delegate.sent(message, frame)
    def finish(self, normal):
        try:
            if self.delegate is not None: self.delegate.finish(normal)
        except Exception: self.owner.classification_overflow = True
        finally:
            try:
                self.owner.owner._current(); row = self.item['row']
                self.item['normal'] = (normal is True and row.get('released') is True and row.get('normal_return') is True and row.get('completed') is True)
                self.item['finished'] = True; self.owner.publish_counts()
            except Exception: self.owner.classification_overflow = True


class _OriginalExchangeAttempt(_OriginalGETAttempt):
    def __init__(self, exchanges, activity, row):
        _OriginalHTTPAttempt.__init__(self, exchanges.owner, activity, row)
        self.readbacks, self.witness, self.finished = exchanges, None, False
        self.refusals = []
        self.scope_facts = tuple(activity.scope.get(key, b'' if key == 'query_string' else None) for key in ('method','path','query_string','app'))
        self.phase = 'after_post' if exchanges.post_terminal_sha is not None else 'before_post' if exchanges.owner.attempt is None else 'during_post'
        self.budget = EXCHANGE_SECONDS[activity.scope['path']]

    def _current(self):
        self.owner._current()
        if not self.scope_current(): self.refuse('exchange_scope_changed')
        if time.monotonic()-self.started_monotonic >= self.budget: self.refuse('exchange_deadline')
        if self.witness is not None and not self.witness.current(): self.refuse('exchange_authorization_changed')

    def received(self, message):
        return self._frame_call(_OriginalHTTPAttempt.received.__get__(self), message)

    def finish(self, normal):
        try:
            self.owner._current(); witness = self.witness
            if (self.finished or not self.scope_current() or time.monotonic()-self.started_monotonic >= self.budget or not normal or not self.request_ended
                    or not self.response_ended or self.pending_send is not None or self.status != 200 or witness is None
                    or not witness.normal_closed or not witness.valid_terminal_send or self.row.get('released') is not True
                    or self.row.get('normal_return') is not True or self.row.get('completed') is not True): self.refuse('exchange_original_not_completed')
            if self.refusals: return
            original = witness.original; project = original.project; path = self.activity.scope['path']
            if any(token.encode() in raw for token in (self.owner.selector.token, original.session) for raw in (self.request, self.response)):
                self.refuse('exchange_private_token_in_body'); return
            request, response = _json_data(self.request), _json_data(self.response)
            if type(request) is not dict or type(response) is not dict: raise ValueError('Original exchange JSON differs')
            from backend.engine.required_flow_passive_capture import _graph_sha, RETAINED_GRAPH_SHA, RETAINED_MODELS
            if path == '/api/flowchart/pipeline':
                from urllib.parse import parse_qs
                query_raw = self.activity.scope.get('query_string', b'')
                if type(query_raw) is not bytes or len(query_raw) > 16384: raise ValueError('Original save query bound differs')
                query = parse_qs(query_raw.decode('utf-8'), keep_blank_values=True, strict_parsing=True, max_num_fields=4)
                if (not {'recipe_task','source_dataset_path'} <= set(query)
                        or not set(query) <= {'recipe_task','source_dataset_path','change_reason','expected_version_id'}
                        or any(len(values) != 1 for values in query.values())
                        or query['recipe_task'][0] != project.get('task') or query['source_dataset_path'][0] != project.get('source_dataset_dir')
                        or query.get('expected_version_id', ['none'])[0] not in ('none',) and re.fullmatch('[0-9a-f]{32}', query['expected_version_id'][0]) is None
                        or 'change_reason' in query and not 0 < len(query['change_reason'][0]) <= 2000
                        or _graph_sha(request) not in (RETAINED_GRAPH_SHA, RETAINED_WIRE_GRAPH_SHA) or response.get('status') != 'saved'
                        or response.get('pipeline_id') != request.get('id') or response.get('node_count') != len(request.get('nodes', ()))
                        or response.get('recipe_task') != project.get('task')): raise ValueError('Original saved request/response binding differs')
                version = response.get('version_id')
            else:
                if (self.activity.scope.get('query_string', b'') != b''
                        or not {'source_dataset_path','recipe_task','version_id'} <= set(request)
                        or not set(request) <= {'source_dataset_path','recipe_task','version_id','package_name','deployment_profile','target_os','target_arch','runtime_config'}
                        or request['source_dataset_path'] != project.get('source_dataset_dir') or request['recipe_task'] != project.get('task')
                        or response.get('parity') != {'status':'not_run'} or response.get('model_job_ids') != sorted(row[0] for row in RETAINED_MODELS)):
                    raise ValueError('Original export no-parity body differs')
                package = Path(response['package_path']); expected = Path(project['project_dir'])/'exports'/'flows'
                if not package.is_absolute() or '..' in package.parts or package.resolve() != package or not package.is_relative_to(expected):
                    raise ValueError('Original export package data is outside owned project')
                version = request['version_id']
            if type(version) is not str or re.fullmatch('[0-9a-f]{32}', version) is None: raise ValueError('Original saved version differs')
            record_sha, _ = _version_data(project, version)
            receipt = {'schema':'modu-vision.original-required-flow-exchange/v1','complete':True,'method':'POST','path':path,'phase':self.phase,
                'process_pid':self.owner.pid,'launch_nonce':self.owner.launch_nonce,'capture_nonce':self.owner.selector.nonce,
                'actor_id':original.account['id'],'project_id':project['id'],'workspace_id':original.owner.workspace,
                'actual_original_shared_authorization':True,'status':self.status,'started_epoch':self.started_epoch,'finished_epoch':time.time(),
                'elapsed_monotonic_seconds':time.monotonic()-self.started_monotonic,'original_budget_seconds':self.budget,
                'request_receives':self.request_receives,'successful_sends':self.successful_sends,
                'original_request_identity':self.row['identity'],'original_entered_sequence':self.row['entered_sequence'],'original_released_sequence':self.row['released_sequence'],
                'headers_query_or_tokens_serialized':False,'server_ASGI_send_receipt':True,'client_receive_proven':False,
                'permission_authority':False,'lease_authority':False,'parent_credit':False,'refusals':[],
                'saved_version':version,'saved_record_sha256':record_sha,'original_save_query_matched_source_and_recipe':path == '/api/flowchart/pipeline'}
            for name, raw in (('request', self.request), ('response', self.response)):
                receipt[name+'_bytes'] = len(raw); receipt[name+'_sha256'] = hashlib.sha256(raw).hexdigest(); receipt[name+'_base64'] = base64.b64encode(raw).decode('ascii')
            self.readbacks.provisional.append((_shared_lineage(original), receipt)); self.readbacks.promote()
        except Exception: self.refuse('exchange_terminal_observation_failed')
        finally:
            self.finished = True; self.request.clear(); self.response.clear()
            if self.witness is not None: self.witness.original.task = self.witness.original.loop = None


def begin_original_exchange_readback(owner, middleware, scope):
    """Only the actual final original shared-auth generator can attach data."""
    try:
        from backend.engine.original_shared_required_flow import OriginalSharedRequiredFlowOwner, _OriginalSharedContinuation
        if type(owner) is not OriginalSharedRequiredFlowOwner or not owner.original(): return None
        hook = sys._getframe(1); enter = hook.f_back; auth = enter.f_back if enter is not None else None
        if (hook.f_code is not owner.hook_code or hook.f_globals is not owner.hook_body.__globals__
                or enter is None or enter.f_code is not owner.enter_code
                or auth is None or auth.f_code is not owner.auth_body_code or auth.f_globals is not owner.auth_globals
                or auth.f_lasti not in owner.entry_offsets): return None
        if getattr(getattr(enter.f_locals.get('self'), 'gen', None), 'gi_frame', None) is not hook: return None
        if (type(scope) is not dict or scope.get('type') != 'http' or scope.get('method') != 'POST'
                or scope.get('path') not in EXCHANGE_SECONDS
                or scope.get('app') is not owner.app or type(middleware) is not owner.auth_type or middleware.project_app is not owner.app): return None
        state = scope.get('state', {}); account = state.get('account_user'); project = state.get('scoped_project'); decision = state.get('permission_decision')
        values = auth.f_locals
        if (values.get('self') is not middleware or values.get('scope') is not scope or values.get('state') is not state
                or values.get('store') is not owner.store or values.get('account') is not account or values.get('project') is not project
                or values.get('decision') is not decision or values.get('token') is not state.get('account_session_token')
                or values.get('path') != scope['path'] or values.get('method') != 'POST'
                or type(account) is not dict or type(project) is not dict or type(decision) is not owner.decision_type
                or decision.allowed is not True or decision.reason != 'allowed' or decision.actor_id != account.get('id')
                or decision.project_id != project.get('id') or decision.workspace_id != owner.workspace
                or decision.action != owner.permission_action(scope['path'], 'POST') or values.get('role') != decision.role
                or type(state.get('account_session_token')) is not str or not state['account_session_token']): return None
        node = owner.app.middleware_stack
        for _ in range(16):
            if node is middleware: break
            node = getattr(node, 'app', None)
            if node is None: return None
        else: return None
        # Same original member/stack binding as POST; this issues no POST carrier.
        if owner.middleware is None: owner.middleware, owner.stack, owner.downstream = middleware, owner.app.middleware_stack, middleware.app
        if middleware is not owner.middleware or owner.app.middleware_stack is not owner.stack or middleware.app is not owner.downstream: return None
        selector = getattr(owner.state, 'required_retained_flow_capture', None)
        capture = getattr(selector, 'http_capture', None)
        if type(capture) is not _OriginalFlowHTTPCapture or capture.selector.shared_owner is not owner: return None
        attempt = capture.exchanges.for_scope(scope)
        if attempt is None or attempt.witness is not None: return None
        witness = _OriginalGETWitness(_OriginalSharedContinuation(owner, middleware, scope, state, account, project, decision, asyncio.current_task(), asyncio.get_running_loop()), attempt)
        attempt.witness = witness
        return witness
    except Exception:
        return None

