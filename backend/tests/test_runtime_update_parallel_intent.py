"""Exact product AST with inert executor and regular-file models.

No real executor, thread, signature/OS/lease authority, archive or file I/O is
exercised. The original validators are independently byte-pinned in the source
receipt; these controls test orchestration and new namespace barriers only.
"""
import ast
import copy
from hashlib import sha256
import io
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'engine/runtime_update.py'


class OriginalFailure(BaseException):
    pass


class ModelLock:
    def __init__(self): self.held = False
    def __enter__(self):
        assert not self.held
        self.held = True
        return self
    def __exit__(self, *args): self.held = False


class ModelEvent:
    def __init__(self, world): self.world = world; self.ready = False
    def set(self): self.ready = True
    def is_set(self):
        if self.world.event_check_interrupts: raise self.world.event_check_interrupts.pop(0)
        return self.ready
    def wait(self, timeout=None):
        assert timeout is None, 'no new deadline or polling cap'
        if self.world.event_interrupts: raise self.world.event_interrupts.pop(0)
        if not self.ready: self.world.progress_one()
        return self.ready


class ModelPath:
    def __init__(self, world, value): self.world = world; self.value = value
    def __truediv__(self, value): return ModelPath(self.world, self.value + '/' + value)
    @property
    def parent(self): return ModelPath(self.world, self.value.rsplit('/', 1)[0])
    def relative_to(self, value): return ModelPath(self.world, self.value[len(value.value) + 1:])
    def as_posix(self): return self.value
    def rglob(self, pattern):
        assert pattern == '*'; self.world.scans += 1
        self.world.log.append(('namespace', self.world.scans))
        return [self / p for p in self.world.members]
    def is_symlink(self): return self.value.rsplit('/application/', 1)[-1] in self.world.aliases
    def is_file(self): return self.world.kinds.get(self.value.rsplit('/application/', 1)[-1], 'file') == 'file'
    def is_dir(self): return self.world.kinds.get(self.value.rsplit('/application/', 1)[-1], 'file') == 'dir'
    def stat(self):
        name = self.value.rsplit('/', 1)[-1]
        self.world.log.append(('mode', name))
        return SimpleNamespace(st_mode=self.world.modes.get(name, 0o400))


class ModelFuture:
    def __init__(self, executor, function, args, kwargs, ordinal):
        self.executor = executor; self.function = function
        self.args = args; self.kwargs = kwargs; self.ordinal = ordinal
        self.finished = False; self.body_finished = False; self.value = None; self.error = None; self.partial = None
    def finish(self):
        if self.finished: return
        assert self.executor.world.fence_held
        if self.body_finished:
            self.finished = True
            self.executor.world.log.append(('future_done', self.ordinal))
            return
        try:
            if self.partial is None:
                self.value = self.function(*self.args, **self.kwargs)
            else:
                # Explicit inert continuation after the original wrapper's
                # start lock, before its validator; ordinary paths run full AST.
                work = self.partial
                try: self.value = work.value = work.function(*work.args, **work.kwargs)
                except BaseException as error:
                    work.error = error
                    raise
                finally: work.complete.set()
        except BaseException as error: self.error = error
        self.body_finished = True
        if self.ordinal in self.executor.world.delayed_future_marks:
            self.executor.world.log.append(('worker_record_only', self.ordinal))
            return
        self.finished = True
        self.executor.world.log.append(('future_done', self.ordinal))
        if self.executor.world.after_completion:
            self.executor.world.after_completion(self)
    def done(self): return self.finished
    def result(self, timeout=None):
        assert timeout is None
        if self.executor.world.result_interrupts:
            raise self.executor.world.result_interrupts.pop(0)
        if not self.finished: self.finish()
        if self.error is not None: raise self.error
        return self.value
    def exception(self, timeout=None):
        assert self.finished and timeout is None
        return self.error
    def cancel(self): raise AssertionError('started or queued Future cannot be cancelled')


class ModelExecutor:
    def __init__(self, world, *, max_workers):
        assert max_workers == 4
        self.world = world; self.futures = []; self.shut = False
        world.executors.append(self); world.log.append(('executor', max_workers))
    def submit(self, function, *args, **kwargs):
        ordinal = len(self.futures)
        if ordinal == self.world.reject_submit:
            raise self.world.submit_error
        future = ModelFuture(self, function, args, kwargs, ordinal)
        self.futures.append(future)
        self.world.max_pending = max(self.world.max_pending,
            sum(not f.done() for f in self.futures))
        self.world.log.append(('submit', ordinal))
        if ordinal == self.world.hidden_submit:
            self.world.hidden = future
            if self.world.hidden_started:
                work = function.__self__
                with work.lock:
                    assert not work.started and not work.rejected
                    work.started = True
                future.partial = work
                self.world.log.append(('modeled_worker_started', ordinal))
            raise self.world.submit_error
        return future
    def shutdown(self, wait=True, *, cancel_futures=False):
        assert wait is True and cancel_futures is False
        self.world.log.append(('shutdown', tuple(f.done() for f in self.futures)))
        if self.world.shutdown_interrupts:
            raise self.world.shutdown_interrupts.pop(0)
        for future in self.futures: future.finish()
        self.shut = True
    def __enter__(self): return self
    def __exit__(self, *args): self.shutdown()


class World:
    def __init__(self):
        self.log = []; self.executors = []; self.scans = 0
        self.fence_held = True; self.max_pending = 0
        self.wait_interrupts = []; self.result_interrupts = []
        self.event_interrupts = []; self.event_check_interrupts = []; self.shutdown_interrupts = []
        self.reject_submit = self.hidden_submit = -1; self.hidden = None; self.hidden_started = False
        self.submit_error = RuntimeError('original ordinary submit')
        self.after_completion = None; self.errors = {}; self.order = []; self.delayed_future_marks = set()
        self.rows = [{'path': 'folder/r' + str(i), 'sha256':
            'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
            'size': 3, 'executable': i == 0} for i in range(7)]
        self.manifest = {'schema_version': 2, 'version': '1.0.0', 'platform':
            'macos', 'arch': 'arm64', 'entrypoint': 'folder/r0', 'files':
            self.rows, 'links': [{'path': 'alias', 'target': 'folder/r0'}]}
        self.members = ['folder', 'alias', 'portable-application.json',
                        *(row['path'] for row in self.rows)]
        self.modes = {'r0': 0o500}; self.aliases = {'alias': 'folder/r0'}
        self.kinds = {'folder': 'dir'}; self.signed_error = None; self.manifest_bytes = b'manifest'; self.database = {}
        self.release = {'version': '1.0.0', 'platform': 'macos', 'arch': 'arm64',
            'artifacts': [{'path': 'application.zip', 'kind': 'installer'}]}
        self.record = {'authority_path': 'authority', 'authority_sha256':
            'a' * 64, 'target': {'platform': 'macos', 'arch': 'arm64'},
            'envelope_sha256': sha256(b'envelope').hexdigest(),
            'release': self.release, 'migration_id': None}
        self.root = ModelPath(self, 'root')

    def guard_call(self, kind):
        self.log.append(('validator', kind))
        if kind in self.errors: raise self.errors[kind]

    def bundle(self, directory, release, *, destination=None):
        assert directory.value == 'root/update/bundle'
        assert release is self.release and destination is None
        self.guard_call('bundle')

    def portable(self, archive, release, *, destination=None):
        assert archive.value == 'root/update/bundle/application.zip'
        assert release is self.release and destination is None
        self.guard_call('portable')
        return self.manifest, sha256(b'manifest').hexdigest()

    def check_file(self, path, row):
        self.guard_call(row['path'])
        self.log.append(('original_file_before_after', row['path']))

    def progress_one(self, futures=None):
        pending = [f for e in self.executors for f in e.futures if not f.done()]
        if futures is not None: pending = [f for f in pending if f in futures]
        if not pending: return
        wanted = next((n for n in self.order if any(f.ordinal == n for f in pending)), None)
        future = next((f for f in pending if f.ordinal == wanted), pending[0])
        future.finish()

    def wait(self, futures, *, return_when):
        assert return_when == 'FIRST_COMPLETED'
        if self.wait_interrupts: raise self.wait_interrupts.pop(0)
        if not any(f.done() for f in futures): self.progress_one(futures)
        return {f for f in futures if f.done()}, {f for f in futures if not f.done()}

    def load(self):
        tree = ast.parse(SOURCE.read_bytes())
        wanted = {'UpdateError', '_validated_intent', '_IntentCheckWork',
            '_intent_parallel_checks', '_intent_executor_close', '_intent_checks_join',
            '_installed_intent_namespace', '_installed_intent_row'}
        definitions = [n for n in tree.body if isinstance(n, (ast.FunctionDef,
            ast.ClassDef)) and n.name in wanted]
        ns = {'_intent': lambda root, identifier: (self.record, self.root / 'update'),
            'verify_release': self.verify_release,
            '_sha': lambda b: sha256(b).hexdigest(), '_bundle': self.bundle,
            '_portable': self.portable, '_unlinked': lambda path: path,
            'GENERATIONS': 'generation', 'MAX_APPLICATION_MANIFEST': 8 * 1024**2,
            '_read': lambda path, limit: self.manifest_bytes, '_native_layout': lambda m:
                ({'alias': 'folder/r0'}, {'folder'}), '_check_file': self.check_file,
            'stat': stat, 'os': SimpleNamespace(readlink=lambda path: self.aliases[path.value.rsplit('/application/', 1)[-1]]),
            'ThreadPoolExecutor': lambda **kwargs: ModelExecutor(self, **kwargs),
            'wait': self.wait, 'FIRST_COMPLETED': 'FIRST_COMPLETED',
            'threading': SimpleNamespace(Lock=ModelLock,
                Event=lambda: ModelEvent(self)), 'migration': SimpleNamespace(_journal=self.journal)}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), ns)
        return ns

    def verify_release(self, *args):
        if self.signed_error is not None: raise self.signed_error
        return self.release, b'envelope'

    def journal(self, *args):
        self.log.append(('migration',))
        return None, self.database

    def invoke(self):
        return self.load()['_validated_intent'](self.root, 'b' * 32)


class ParallelIntentControls(unittest.TestCase):
    def assert_finished(self, world):
        self.assertTrue(world.fence_held)
        self.assertTrue(all(f.done() for e in world.executors for f in e.futures))
        self.assertTrue(all(e.shut for e in world.executors))

    def test_pair_submitted_before_first_original_validation_and_four_window(self):
        w = World(); result = w.invoke()
        self.assertIs(result[0], w.record); self.assertIs(result[2], w.manifest)
        self.assertEqual(len(w.executors), 1)
        first = next(i for i, x in enumerate(w.log) if x[0] == 'validator')
        self.assertEqual([x for x in w.log[:first] if x[0] == 'submit'],
                         [('submit', 0), ('submit', 1)])
        self.assertEqual(w.max_pending, 4); self.assert_finished(w)

    def test_complete_pair_barrier_precedes_rows(self):
        w = World(); w.invoke()
        self.assertIn(('submit', 2), w.log, 'original serial path has no parallel row admission')
        firstrow = w.log.index(('submit', 2))
        self.assertIn(('future_done', 0), w.log[:firstrow])
        self.assertIn(('future_done', 1), w.log[:firstrow])
        self.assert_finished(w)

    def test_bundle_original_error_precedes_faster_portable(self):
        w = World(); first = OriginalFailure('bundle'); later = OriginalFailure('portable')
        w.errors = {'bundle': first, 'portable': later}; w.order = [1, 0]
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, first); self.assert_finished(w)
        self.assertFalse(any(x[0] == 'namespace' for x in w.log))

    def test_row_original_index_error_precedes_completion_order(self):
        w = World(); first = OriginalFailure('r0'); later = OriginalFailure('r2')
        w.errors = {'folder/r0': first, 'folder/r2': later}; w.order = [1, 0, 4, 3, 2]
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, first); self.assert_finished(w)

    def test_hash_body_failure_cannot_be_replaced_by_same_row_mode(self):
        w = World(); first = OriginalFailure('hash'); w.errors['folder/r0'] = first
        w.modes['r0'] = 0
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, first)
        self.assertNotIn(('mode', 'r0'), w.log); self.assert_finished(w)

    def test_original_mode_refusal_after_original_byte_guard(self):
        w = World(); w.modes['r0'] = 0
        with self.assertRaises(ValueError) as got: w.invoke()
        self.assertEqual(str(got.exception), 'Installed application executable mode changed')
        self.assertLess(w.log.index(('original_file_before_after', 'folder/r0')),
                        w.log.index(('mode', 'r0')))
        self.assert_finished(w)

    def test_fresh_post_namespace_rejects_late_extra_file(self):
        w = World()
        def drift(future):
            if sum(x[0] == 'original_file_before_after' for x in w.log) == 7:
                w.members.append('foreign')
        w.after_completion = drift
        with self.assertRaisesRegex(ValueError, 'Installed application membership differs'): w.invoke()
        self.assertEqual(w.scans, 2); self.assert_finished(w)

    def test_fresh_post_mode_rejects_after_all_original_rows(self):
        w = World()
        def drift(future):
            if sum(x[0] == 'original_file_before_after' for x in w.log) == 7:
                w.modes['r0'] = 0
        w.after_completion = drift
        with self.assertRaisesRegex(ValueError, 'Installed application executable mode changed'): w.invoke()
        self.assertEqual(w.scans, 2); self.assert_finished(w)

    def test_submission_failure_joins_started_and_refuses_unverified_rows(self):
        w = World(); w.reject_submit = 3
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, w.submit_error); self.assert_finished(w)
        self.assertFalse(any(x == ('submit', 4) for x in w.log))

    def test_hidden_submission_rejected_before_original_IO_but_known_joins(self):
        w = World(); w.hidden_submit = 1
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, w.submit_error); self.assert_finished(w)
        self.assertNotIn(('validator', 'portable'), w.log)
        self.assertIn(('validator', 'bundle'), w.log)

    def test_repeated_wait_interrupt_preserves_first_and_joins(self):
        w = World(); first = KeyboardInterrupt('first'); second = KeyboardInterrupt('second')
        w.wait_interrupts = [first, second]
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, first); self.assert_finished(w)

    def test_repeated_shutdown_interrupt_preserves_first_after_join(self):
        w = World(); first = KeyboardInterrupt('first'); w.shutdown_interrupts = [first, SystemExit(2)]
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, first); self.assert_finished(w)

    def test_invocations_do_not_reuse_executor_or_manifest_authority(self):
        w = World(); w.invoke(); w.invoke()
        self.assertEqual(len(w.executors), 2)
        self.assertEqual(sum(x == ('validator', 'bundle') for x in w.log), 2)
        self.assertEqual(sum(x == ('validator', 'portable') for x in w.log), 2)
        self.assert_finished(w)

    def test_hidden_started_submit_waits_original_completion_event(self):
        w = World(); w.hidden_submit = 1; w.hidden_started = True
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, w.submit_error)
        self.assertIn(('modeled_worker_started', 1), w.log)
        self.assertEqual(w.log.count(('validator', 'portable')), 1)
        self.assertTrue(w.hidden.function.__self__.complete.is_set())
        self.assert_finished(w)

    def test_hidden_started_completion_wait_interrupts_keep_first(self):
        w = World(); w.hidden_submit = 1; w.hidden_started = True
        first = KeyboardInterrupt('event first'); w.event_interrupts = [first, SystemExit(4)]
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, first); self.assert_finished(w)
        self.assertTrue(w.hidden.function.__self__.complete.is_set())

    def test_repeated_result_interrupts_keep_first_without_abandon(self):
        w = World(); first = KeyboardInterrupt('result first')
        w.result_interrupts = [first, SystemExit(4)]
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, first); self.assert_finished(w)
        self.assertEqual(w.log.count(('validator', 'bundle')), 1)
        self.assertEqual(w.log.count(('validator', 'portable')), 1)

    def test_earlier_original_error_precedes_later_submission_error(self):
        w = World(); original = OriginalFailure('r0 earlier')
        w.errors['folder/r0'] = original; w.reject_submit = 3
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, original); self.assert_finished(w)

    def test_bundle_original_error_precedes_hidden_portable_submit_failure(self):
        w = World(); original = OriginalFailure('bundle earlier')
        w.errors['bundle'] = original; w.hidden_submit = 1
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, original); self.assert_finished(w)
        self.assertNotIn(('validator', 'portable'), w.log)

    def test_first_submit_rejection_starts_no_original_validation(self):
        w = World(); w.reject_submit = 0
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, w.submit_error); self.assert_finished(w)
        self.assertFalse(any(x[0] == 'validator' for x in w.log))

    def test_signed_release_failure_cannot_construct_executor(self):
        w = World(); original = OriginalFailure('signed release'); w.signed_error = original
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, original); self.assertEqual(w.executors, [])
        self.assertEqual(w.log, [])

    def test_installed_manifest_failure_waits_pair_and_refuses_rows(self):
        w = World(); w.manifest_bytes = b'changed manifest'
        with self.assertRaisesRegex(ValueError, 'Installed application manifest integrity differs'): w.invoke()
        self.assert_finished(w)
        self.assertFalse(any(x == ('validator', 'folder/r0') for x in w.log))

    def test_pre_namespace_refuses_unlisted_file_before_original_rows(self):
        w = World(); w.members.append('foreign')
        with self.assertRaisesRegex(ValueError, 'Installed application membership differs'): w.invoke()
        self.assert_finished(w); self.assertNotIn(('validator', 'folder/r0'), w.log)

    def test_post_namespace_refuses_changed_original_link(self):
        w = World()
        def drift(future):
            if sum(x[0] == 'original_file_before_after' for x in w.log) == 7:
                w.aliases['alias'] = 'foreign'
        w.after_completion = drift
        with self.assertRaisesRegex(ValueError, 'Installed native link target changed'): w.invoke()
        self.assertEqual(w.scans, 2); self.assert_finished(w)

    def test_post_namespace_refuses_original_link_replaced_regular(self):
        w = World()
        def drift(future):
            if sum(x[0] == 'original_file_before_after' for x in w.log) == 7:
                w.aliases = {}
        w.after_completion = drift
        with self.assertRaisesRegex(ValueError, 'Installed native link replaced with a regular member'): w.invoke()
        self.assertEqual(w.scans, 2); self.assert_finished(w)

    def test_post_namespace_refuses_special_file_and_unlisted_directory(self):
        for kind, message in [('special', 'special file'), ('dir', 'directory membership differs')]:
            with self.subTest(kind=kind):
                w = World()
                def drift(future):
                    if sum(x[0] == 'original_file_before_after' for x in w.log) == 7:
                        w.members.append('foreign'); w.kinds['foreign'] = kind
                w.after_completion = drift
                with self.assertRaisesRegex(ValueError, message): w.invoke()
                self.assertEqual(w.scans, 2); self.assert_finished(w)

    def test_schema_one_keeps_original_directory_semantics(self):
        w = World(); w.manifest['schema_version'] = 1
        w.members.remove('alias'); w.aliases = {}; w.members.append('original-dir')
        w.kinds['original-dir'] = 'dir'
        self.assertIs(w.invoke()[2], w.manifest); self.assert_finished(w)

    def test_migration_binding_guard_after_all_full_row_and_namespace_checks(self):
        w = World(); w.record.update(migration_id='m', installation_id='install',
            source_sha256='c' * 64, previous_database={'p': 1})
        w.database = {'installation_id': 'foreign', 'source_sha256': 'c' * 64,
                      'previous_pointer': {'p': 1}}
        with self.assertRaisesRegex(ValueError, 'database intent binding changed'): w.invoke()
        self.assert_finished(w); self.assertEqual(w.scans, 2)
        self.assertLess(w.log.index(('namespace', 2)), w.log.index(('migration',)))
        self.assertEqual(sum(x[0] == 'original_file_before_after' for x in w.log), 7)

    def test_repeated_completion_check_interrupt_preserves_first(self):
        w = World(); first = KeyboardInterrupt('completion check first')
        w.event_check_interrupts = [first, SystemExit(4)]
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, first); self.assert_finished(w)

    def test_shutdown_is_not_the_original_completion_proof(self):
        w = World(); w.invoke()
        self.assertTrue(any(x[0] == 'shutdown' for x in w.log), 'original serial path has no executor cleanup barrier')
        shutdown = next(x for x in w.log if x[0] == 'shutdown')
        self.assertEqual(shutdown[1], (True,) * 9)
        self.assert_finished(w)

    def test_worker_completion_event_does_not_substitute_future_done(self):
        w = World(); w.delayed_future_marks = {0, 1}; w.invoke()
        self.assertIn(('worker_record_only', 0), w.log)
        self.assertIn(('worker_record_only', 1), w.log)
        row = w.log.index(('submit', 2))
        for ordinal in (0, 1):
            self.assertLess(w.log.index(('worker_record_only', ordinal)), w.log.index(('future_done', ordinal)))
            self.assertLess(w.log.index(('future_done', ordinal)), row)
        self.assert_finished(w)

    def test_wait_caller_failure_preserved_even_if_original_worker_fails(self):
        w = World(); first = KeyboardInterrupt('original caller')
        w.wait_interrupts = [first]; w.errors['bundle'] = OriginalFailure('original worker')
        with self.assertRaises(BaseException) as got: w.invoke()
        self.assertIs(got.exception, first); self.assert_finished(w)

    def test_submit_keyboard_interrupt_preserves_caller_over_earlier_worker(self):
        for shape in ('rejected', 'hidden-notstarted', 'hidden-started'):
            with self.subTest(shape=shape):
                w = World(); first = KeyboardInterrupt('original submit caller')
                w.submit_error = first; w.errors['bundle'] = OriginalFailure('earlier bundle worker')
                if shape == 'rejected': w.reject_submit = 1
                else:
                    w.hidden_submit = 1; w.hidden_started = shape == 'hidden-started'
                with self.assertRaises(BaseException) as got: w.invoke()
                self.assertIs(got.exception, first); self.assert_finished(w)
                self.assertEqual(w.log.count(('validator', 'bundle')), 1)
                self.assertEqual(w.log.count(('validator', 'portable')), int(shape == 'hidden-started'))

    def test_submit_system_exit_preserves_caller_over_earlier_worker(self):
        for shape in ('rejected', 'hidden-notstarted', 'hidden-started'):
            with self.subTest(shape=shape):
                w = World(); first = SystemExit('original submit caller')
                w.submit_error = first; w.errors['bundle'] = OriginalFailure('earlier bundle worker')
                if shape == 'rejected': w.reject_submit = 1
                else:
                    w.hidden_submit = 1; w.hidden_started = shape == 'hidden-started'
                with self.assertRaises(BaseException) as got: w.invoke()
                self.assertIs(got.exception, first); self.assert_finished(w)
                self.assertEqual(w.log.count(('validator', 'bundle')), 1)
                self.assertEqual(w.log.count(('validator', 'portable')), int(shape == 'hidden-started'))

    def test_ordinary_submit_error_keeps_original_worker_index_in_all_shapes(self):
        for shape in ('rejected', 'hidden-notstarted', 'hidden-started'):
            with self.subTest(shape=shape):
                w = World(); first = OriginalFailure('earlier bundle worker')
                w.errors['bundle'] = first
                if shape == 'rejected': w.reject_submit = 1
                else:
                    w.hidden_submit = 1; w.hidden_started = shape == 'hidden-started'
                with self.assertRaises(BaseException) as got: w.invoke()
                self.assertIs(got.exception, first); self.assert_finished(w)

    def test_fixed_original_API_refuses_supplied_authority_inputs(self):
        w = World(); fn = w.load()['_validated_intent']
        for field in ('deadline', 'manifest', 'executor', 'callback', 'cached_proof'):
            with self.subTest(field=field):
                with self.assertRaises(TypeError): fn(w.root, 'b' * 32, **{field: object()})
        self.assertEqual(w.log, [])


if __name__ == '__main__':
    if '--source' in sys.argv:
        i = sys.argv.index('--source'); SOURCE = Path(sys.argv[i + 1]); del sys.argv[i:i + 2]
    unittest.main(verbosity=2)
