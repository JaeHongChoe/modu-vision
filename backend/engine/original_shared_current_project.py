"""One original shared-auth denial and its DB exits, never release authority."""
from contextlib import contextmanager, _GeneratorContextManager
from functools import wraps
import os
from pathlib import Path
import sqlite3
import _sqlite3
import stat
import sys
from types import MethodType

_CALLS = {}


def _file_identity(path, directory=False):
    value = path.lstat()
    if (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode) and value.st_nlink == 1):
        return value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid
    return None


def _auth():
    from backend.engine.original_current_project_request import _CURRENT
    attempt = _CURRENT.get()
    if attempt is not None: return attempt.auth
    from backend.engine.original_shared_auth_denial import shared_denial_auth
    auth = shared_denial_auth()
    if auth is not None: return auth
    from backend.engine.original_owned_account_request import owned_login_database_observation
    return owned_login_database_observation()


def observe_account_method(name):
    def decorate(function):
        @wraps(function)
        def observed(store, *args, **kwargs):
            auth = _auth()
            if auth is None: return function(store, *args, **kwargs)
            parameter = 'token' if name == 'authenticate' else 'workspace_id'
            if len(args) == 1 and not kwargs: value = args[0]
            elif not args and set(kwargs) == {parameter}: value = kwargs[parameter]
            else:
                auth.fail('method_arguments')
                return function(store, *args, **kwargs)
            return auth.method(name, store, value, function, args, kwargs)
        _CALLS[name] = observed
        return observed
    return decorate


def observe_account_db(function):
    @wraps(function)
    def observed(store):
        original = function(store)
        auth = _auth()
        if auth is None: return original
        @contextmanager
        def retained():
            connection = None
            body_error = None
            valid = auth.begin_db(store)
            try:
                with original as db:
                    connection = db
                    if type(db) is not sqlite3.Connection: auth.fail('foreign_connection')
                    try: yield db
                    except BaseException as error:
                        body_error = error
                        raise
            except BaseException as error:
                auth.end_db(connection, valid, body_error, error)
                raise
            else: auth.end_db(connection, valid, body_error, None)
        return retained()
    _CALLS['_db'] = observed
    return observed


def observe_shared_authorization(function):
    @wraps(function)
    async def observed(middleware, scope, receive, send):
        auth = _auth()
        if auth is None: return await function(middleware, scope, receive, send)
        valid = auth.begin_middleware(middleware, scope)
        try: result = await function(middleware, scope, receive, send)
        except BaseException:
            auth.fail('middleware_failed')
            raise
        auth.end_middleware(valid)
        return result
    _CALLS['authorization'] = observed
    return observed


class OriginalSharedAccountObservation:
    def __init__(self, app, admission):
        from backend.engine import shared_accounts
        from backend.api import shared_authorization
        from backend.api.shared_authorization import SharedAuthorizationMiddleware
        self.app, self.admission = app, admission
        self.store = app.state.accounts
        self.kind = shared_accounts.AccountStore
        self.module = shared_accounts
        self.path = self.store.path if type(self.store) is self.kind else None
        self.path_identity = _file_identity(self.path) if self.path is not None else None
        self.parent_identity = _file_identity(self.path.parent, True) if self.path is not None else None
        self.workspace = app.state.context_registry.workspace_id
        self.middleware = None
        self.records = []
        self.pid = os.getpid()
        self.calls = tuple((owner, name, getattr(owner, name)) for owner, names in (
            (self.kind, ('_db', 'bind_workspace', 'authenticate', '_public')),
            (SharedAuthorizationMiddleware, ('__call__',)),
            (shared_authorization, ('JSONResponse',)),
            (shared_authorization.JSONResponse, ('__init__', '__call__', 'render')),
            (shared_accounts, ('store_admission', 'sqlite3', 'hashlib', 'time')),
            (shared_accounts.hashlib, ('sha256',)),
            (shared_accounts.time, ('time',)),
            (sqlite3, ('connect', 'Connection')),
            (_GeneratorContextManager, ('__enter__', '__exit__')),
            (sys.modules[__name__], ('_file_identity', '_auth')),
            (OriginalSharedAccountObservation, ('original', 'accepts', 'new_attempt')),
            (_SharedAttempt, ('original', 'fail', 'method', 'begin_db', 'end_db',
                'begin_middleware', 'end_middleware', 'complete'))) for name in names)
        self.codes = []
        self.wrapped = []
        for _, _, function in self.calls:
            held = function
            for _ in range(4):
                if hasattr(held, '__code__'): self.codes.append((held, held.__code__))
                original = getattr(held, '__wrapped__', None)
                if original is None: break
                self.wrapped.append((held, original))
                held = original
        self.eligible = (type(self.store) is self.kind and type(self.path) is type(Path())
            and self.kind._db is _CALLS.get('_db')
            and self.kind.bind_workspace is _CALLS.get('bind_workspace')
            and self.kind.authenticate is _CALLS.get('authenticate')
            and SharedAuthorizationMiddleware.__call__ is _CALLS.get('authorization')
            and sqlite3.connect is _sqlite3.connect and sqlite3.Connection is _sqlite3.Connection)

    def __copy__(self):
        with self.admission._condition:
            self.admission._unsupported.add('uncovered_request_protocol')
            self.admission._activity_observer.poison('shared_account_owner_copy')
        raise ValueError('Original shared account observer cannot be copied')

    def __deepcopy__(self, memo): return self.__copy__()
    def __reduce_ex__(self, protocol): return self.__copy__()

    def original(self):
        if (not all(getattr(owner, name) is function for owner, name, function in self.calls)
                or not all(function.__code__ is code for function, code in self.codes)
                or not all(function.__wrapped__ is original for function, original in self.wrapped)):
            return False
        try:
            identities = (self.path_identity is not None and self.parent_identity is not None
                and _file_identity(self.path) == self.path_identity
                and _file_identity(self.path.parent, True) == self.parent_identity)
        except OSError: identities = False
        return (self.eligible and identities and self.pid == os.getpid() and self.app.state.accounts is self.store
            and type(self.store) is self.kind and self.store.path is self.path
            and self.app.state.context_registry.workspace_id is self.workspace
            and not self.path.is_symlink() and not self.path.parent.is_symlink()
            and all(type(getattr(self.store, name)) is MethodType
                and getattr(self.store, name).__self__ is self.store
                and getattr(self.store, name).__func__ is function
                for owner, name, function in self.calls if owner is self.kind
                and name in {'_db', 'bind_workspace', 'authenticate'}))

    def accepts(self, activity):
        forbidden = {b'authorization', b'cookie', b'x-vision-context', b'x-vision-project'}
        return (self.original() and not self.records
            and not any(key.lower() in forbidden for key, _ in activity.scope.get('headers', ()))
            and not activity.scope.get('subprotocols'))

    def new_attempt(self, attempt):
        auth = _SharedAttempt(self, attempt)
        self.records.append(auth)
        return auth

    def snapshot(self):
        with self.admission._condition:
            return {'records': [{'settled': row.attempt.settled,
                'middleware_calls': row.middleware_calls, 'workspace_returns': row.workspace_returns,
                'authentication_rejections': row.authentication_rejections,
                'database_context_exits': row.database_exits} for row in self.records],
                'whole_writer_coverage': False, 'process_tree_exit_verified': False,
                'can_release_launch_lease': False}


class _SharedAttempt:
    def __init__(self, owner, attempt):
        self.owner, self.attempt = owner, attempt
        self.binding = owner, attempt, attempt.activity, attempt.token
        self.middleware_calls = self.middleware_returns = 0
        self.workspace_returns = self.authentication_rejections = self.database_exits = 0
        self.method_name = None
        self.db_live = False
        self.db_results = []
        self.methods = []

    def __copy__(self):
        self.fail('copy_refused')
        raise ValueError('Original shared account attempt cannot be copied')

    def __deepcopy__(self, memo): return self.__copy__()
    def __reduce_ex__(self, protocol): return self.__copy__()

    def fail(self, reason):
        with self.owner.admission._condition: self.attempt.fail('shared_account_' + reason)

    def original(self):
        return (all(current is original for current, original in zip(
            (self.owner, self.attempt, self.attempt.activity, self.attempt.token), self.binding))
            and self.attempt.auth is self and self.attempt.original())

    def begin_middleware(self, middleware, scope):
        with self.owner.admission._condition:
            valid = (self.original() and middleware is self.owner.middleware
                and middleware.project_app is self.owner.app and scope is self.attempt.activity.scope
                and self.middleware_calls == 0)
            self.middleware_calls += 1
            if not valid: self.fail('middleware_binding')
            return valid

    def end_middleware(self, valid):
        with self.owner.admission._condition:
            if valid and self.original() and not self.db_live and self.method_name is None:
                self.middleware_returns += 1
            else: self.fail('middleware_return')

    def method(self, name, store, value, function, args, kwargs):
        with self.owner.admission._condition:
            expected = 'bind_workspace' if not self.methods else 'authenticate' if len(self.methods) == 1 else None
            valid = (self.original() and self.middleware_calls == 1 and not self.middleware_returns
                and store is self.owner.store and name == expected and self.method_name is None
                and (value is self.owner.workspace if name == 'bind_workspace'
                     else type(value) is str and value == ''))
            if not valid: self.fail('method_binding')
            self.method_name = name
            before = len(self.db_results)
        error = None
        try: result = function(store, *args, **kwargs)
        except BaseException as caught:
            error = caught
            raise
        else: return result
        finally:
            with self.owner.admission._condition:
                exits = self.db_results[before:]
                okay = (valid and self.original() and not self.db_live and len(exits) == 1
                    and (error is None and exits[0] is None if name == 'bind_workspace'
                         else type(error) is ValueError and exits[0] is error))
                self.method_name = None
                self.methods.append(name)
                if okay:
                    if name == 'bind_workspace': self.workspace_returns += 1
                    else: self.authentication_rejections += 1
                else: self.fail('method_exit')

    def begin_db(self, store):
        with self.owner.admission._condition:
            valid = (self.original() and store is self.owner.store and not self.db_live
                and self.method_name in {'bind_workspace', 'authenticate'})
            if not valid: self.fail('database_entry')
            self.db_live = True
            return valid

    def end_db(self, connection, valid, body_error, exit_error):
        with self.owner.admission._condition:
            closed = False
            if type(connection) is sqlite3.Connection:
                try: connection.in_transaction
                except sqlite3.ProgrammingError: closed = True
            okay = (valid and self.original() and self.db_live and closed
                and (exit_error is None and body_error is None
                     or type(body_error) is ValueError and exit_error is body_error))
            self.db_live = False
            if okay:
                self.database_exits += 1
                self.db_results.append(body_error)
            else: self.fail('database_exit')

    def complete(self, envelope):
        state = self.attempt.activity.scope.get('state', {})
        return (self.original() and not self.attempt.failed and self.middleware_calls == self.middleware_returns == 1
            and self.methods == ['bind_workspace', 'authenticate'] and self.workspace_returns == 1
            and self.authentication_rejections == 1 and self.database_exits == 2
            and not self.db_live and self.method_name is None
            and envelope.get('response_status') == 401 and envelope['normal_return'] and envelope['completed']
            and not any(key in state for key in ('account_user', 'account_session_token', 'scoped_project', 'permission_decision'))
            and self.attempt.handler == 0 and self.attempt.storage == 0 and not self.attempt.resolutions
            and not self.attempt.events and not self.attempt.inflight)
