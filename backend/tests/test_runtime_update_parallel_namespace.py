"""Root-only real namespace probes; author has not executed this module.

The causal probe retains the signed fixture, original file validators, retained
transition OFD and exact stdlib worker guard. Direct namespace controls use
fresh owned real paths and a real same4 pool, but do not claim signature, lease,
compiled app, service, process, model or full update authority. Faults explicitly
identify injected control boundaries; no Future/work completion is fabricated.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import stat
import subprocess
import threading

import pytest
from backend.engine import application_launch_lease as lease
from backend.engine import runtime_update as update
from backend.tests.test_application_backend_intent_composition import original
from backend.tests.test_service_s6_04 import controlled_preactivation_guard


@pytest.fixture(autouse=True)
def no_child(monkeypatch):
    monkeypatch.setattr(subprocess.Popen, '__init__', lambda *a, **k: pytest.fail('No child permitted'))


def test_real_validated_intent_namespace_runs_in_original_caller_order(original, monkeypatch):
    root, record, _, _, _, _ = original
    application = root/update.GENERATIONS/record['binding']['application_generation']/'application'
    paths = list(application.rglob('*'))
    assert len(paths) >= 4
    ordinary = [path for path in paths if path.name != 'portable-application.json']
    first, second = ordinary[0], ordinary[-1]
    assert paths.index(first) < paths.index(second)
    original_is_symlink = Path.is_symlink
    caller = threading.get_ident(); observations = []; phases = []
    original_namespace = update._intent_parallel_namespace
    def namespace(executor, application, manifest, retained, **kwargs):
        start = len(retained)
        value = original_namespace(executor, application, manifest, retained, **kwargs)
        phases.append((executor, retained, start, list(retained[start:])))
        return value
    def check(path):
        if path == first:
            observations.append(('first', threading.get_ident()))
        elif path == second:
            observations.append(('second', threading.get_ident()))
        return original_is_symlink(path)
    monkeypatch.setattr(Path, 'is_symlink', check)
    monkeypatch.setattr(update, '_intent_parallel_namespace', namespace)
    with lease._transition_admission(root, record['nonce']):
        update._validated_intent(root, record['binding']['update_id'])
    # Each fresh pass preserves the original ordered contiguous path ranges.
    # Worker completion order across ranges is not a caller-order assertion.
    assert len(phases) == 2 and [phase[2] for phase in phases] == [1, 6]
    assert phases[0][0] is phases[1][0] and phases[0][1] is phases[1][1]
    assert sorted(label for label, _ in observations) == ['first', 'first', 'second', 'second']
    assert all(thread != caller for _, thread in observations)
    for executor, retained, _, work in phases:
        assert len(work) == 1 and all(item.function is update._intent_namespace_range for item in work)
        assert [path for item in work for path in item.args[-1]] == paths
        assert executor._max_workers == 4 and executor._shutdown
        assert all(not thread.is_alive() for thread in executor._threads)
        assert len(retained) == 7 and all(item.complete.is_set() and item.future.done() for item in retained)


def native_files(tmp_path):
    application = tmp_path/'owned-application'; application.mkdir()
    entry = 'Fixture.app/Contents/MacOS/main'
    readme = 'Fixture.app/Contents/Resources/readme.bin'
    payloads = {entry: b'owned inert entry', readme: b'owned namespace bytes'}
    manifest = {'schema_version': 2, 'version': '1.0.0', 'platform': 'darwin', 'arch': 'arm64',
        'entrypoint': entry, 'files': [{'path': name, 'sha256': update._sha(raw),
            'size': len(raw), 'executable': name == entry} for name, raw in payloads.items()],
        'links': [{'path': 'Fixture.app/Contents/Current', 'target': 'Resources'}]}
    for name, raw in payloads.items():
        path = application/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    (application/manifest['links'][0]['path']).symlink_to('Resources')
    (application/'portable-application.json').write_bytes(update._canonical(manifest))
    return application, manifest


def run_real_namespace(application, manifest, retained):
    executor = ThreadPoolExecutor(max_workers=4); first = None
    try:
        update._intent_parallel_namespace(executor, application, manifest, retained)
    except BaseException as error:
        first = error
    finally:
        first = update._intent_executor_close(executor, retained, first)
    assert executor._shutdown and all(not thread.is_alive() for thread in executor._threads)
    assert all(work.complete.is_set() and (work.future is None or work.future.done())
               and (work.started or work.rejected) for work in retained)
    if first is not None: raise first


def test_every_real_namespace_path_keeps_original_unlinked_guard(tmp_path, monkeypatch):
    application, manifest = native_files(tmp_path)
    paths = list(application.rglob('*')); original_guard = update._unlinked
    observed = []; retained = []
    def guard(path):
        value = original_guard(path)
        observed.append(Path(path))
        return value
    monkeypatch.setattr(update, '_unlinked', guard)
    run_real_namespace(application, manifest, retained)
    expected = [path.parent if path.is_symlink() else path for path in paths]
    assert sorted(map(str, observed)) == sorted(map(str, expected))
    assert len(retained) == 4
    assert all(work.started and work.future.done() and work.complete.is_set() for work in retained)


@pytest.mark.parametrize('kind,message', [
    ('changed_link', 'link target changed'), ('regular_alias', 'replaced with a regular member'),
    ('unexpected_link', 'link target changed'), ('special', 'special file'),
    ('extra_directory', 'directory membership differs'), ('missing_regular', 'membership differs'),
    ('unlisted_regular', 'membership differs'), ('linked_application_parent', 'cannot follow links')])
def test_fresh_real_native_namespace_negatives(tmp_path, kind, message):
    application, manifest = native_files(tmp_path)
    outside = tmp_path/'owned-outside'; outside.mkdir(); marker = outside/'marker'; marker.write_bytes(b'foreign-preserved')
    alias = application/manifest['links'][0]['path']
    if kind == 'changed_link': alias.unlink(); alias.symlink_to(outside)
    elif kind == 'regular_alias': alias.unlink(); alias.write_bytes(b'regular alias replacement')
    elif kind == 'unexpected_link': (application/'unexpected-link').symlink_to(outside)
    elif kind == 'special': os.mkfifo(application/'unexpected-fifo')
    elif kind == 'extra_directory': (application/'Fixture.app/Contents/Unlisted').mkdir()
    elif kind == 'missing_regular': (application/manifest['files'][1]['path']).unlink()
    elif kind == 'unlisted_regular': (application/'unexpected-regular').write_bytes(b'foreign current member')
    elif kind == 'linked_application_parent':
        preserved = application.parent/'owned-preserved'; application.rename(preserved); application.symlink_to(preserved)
    retained = []
    with pytest.raises(update.UpdateError, match=message): run_real_namespace(application, manifest, retained)
    assert marker.read_bytes() == b'foreign-preserved'
    assert all(work.complete.is_set() and work.future.done() for work in retained)


def test_schema_one_fresh_unlisted_directory_semantics_retained(tmp_path):
    application, manifest = native_files(tmp_path)
    (application/manifest['links'][0]['path']).unlink(); manifest['schema_version'] = 1; manifest.pop('links')
    (application/'ordinary-original-directory').mkdir()
    retained = []; run_real_namespace(application, manifest, retained)
    assert all(work.future.done() for work in retained)


def test_first_real_enumeration_path_error_precedes_faster_later_path_error(tmp_path, monkeypatch):
    application, manifest = native_files(tmp_path)
    paths = list(application.rglob('*')); first_path, later_path = paths[0], paths[-1]
    earlier = OSError('Exact earlier original namespace path'); later = OSError('Exact later original namespace path')
    original_guard = update._unlinked; ready = threading.Event(); observed = []
    def guard(path):
        value = original_guard(path)
        if Path(path) == first_path:
            assert ready.wait(2), 'Later original namespace range must actually execute'
            observed.append('first'); raise earlier
        if Path(path) == later_path:
            observed.append('later'); ready.set(); raise later
        return value
    monkeypatch.setattr(update, '_unlinked', guard)
    retained = []
    with pytest.raises(OSError) as caught: run_real_namespace(application, manifest, retained)
    assert caught.value is earlier and observed == ['later', 'first']
    assert all(work.future.done() and work.complete.is_set() for work in retained)


@pytest.mark.parametrize('earlier_error', [False, True])
def test_enumeration_error_keeps_original_prefix_error_priority(tmp_path, monkeypatch, earlier_error):
    application, manifest = native_files(tmp_path)
    paths = list(application.rglob('*'))
    original_rglob = Path.rglob; original_guard = update._unlinked
    prefix_error = OSError('Exact earlier original path'); enumeration_error = OSError('Exact later injected enumeration boundary')
    def enumerate_paths(path, pattern, *args, **kwargs):
        if path != application: return original_rglob(path, pattern, *args, **kwargs)
        def partial():
            yield paths[0]
            raise enumeration_error
        return partial()
    def guard(path):
        value = original_guard(path)
        if earlier_error and Path(path) == paths[0]: raise prefix_error
        return value
    monkeypatch.setattr(Path, 'rglob', enumerate_paths); monkeypatch.setattr(update, '_unlinked', guard)
    retained = []
    with pytest.raises(OSError) as caught: run_real_namespace(application, manifest, retained)
    assert caught.value is (prefix_error if earlier_error else enumeration_error)
    assert all(work.future.done() and work.complete.is_set() for work in retained)


@pytest.mark.parametrize('kind', [KeyboardInterrupt, SystemExit])
def test_caller_wait_interruption_preserves_exact_object_and_real_future_cleanup(tmp_path, monkeypatch, kind):
    application, manifest = native_files(tmp_path); original_wait = update.wait
    first = kind('Exact real namespace wait caller'); interruptions = [first, kind('Later caller')]
    def wait(*args, **kwargs):
        if interruptions: raise interruptions.pop(0)
        return original_wait(*args, **kwargs)
    monkeypatch.setattr(update, 'wait', wait)
    retained = []
    with pytest.raises(kind) as caught: run_real_namespace(application, manifest, retained)
    assert caught.value is first
    assert len(retained) == 4 and all(work.future.done() and work.complete.is_set() for work in retained)


@pytest.mark.parametrize('kind', [RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('hidden_started', [False, True])
def test_real_namespace_submission_priority_and_hidden_work_cleanup(tmp_path, monkeypatch, kind, hidden_started):
    application, manifest = native_files(tmp_path)
    paths = list(application.rglob('*')); first_path = paths[0]
    earlier = OSError('Exact earlier original namespace path')
    later = kind('Exact later original submit caller')
    original_guard = update._unlinked; original_submit = ThreadPoolExecutor.submit
    original_range = update._intent_namespace_range
    started = threading.Event(); release = threading.Event(); captured = []; calls = []
    # Four fixed ranges: the injected third submission points to this exact
    # real range. A real worker records entry before submit raises without
    # returning its Future; no start/complete/Future state is assigned here.
    hidden_paths = paths[len(paths)*2//4:len(paths)*3//4]
    def guarded(path):
        value = original_guard(path)
        if Path(path) == first_path: raise earlier
        return value
    def namespace_range(app, current, links, directories, selected):
        if selected == hidden_paths:
            started.set()
            assert release.wait(2), 'Hidden real namespace work must stay owned until caller release'
        return original_range(app, current, links, directories, selected)
    def submit(executor, function, *args, **kwargs):
        ordinal = len(calls); calls.append(function)
        if ordinal == 2 and not hidden_started: raise later
        future = original_submit(executor, function, *args, **kwargs); captured.append(future)
        if ordinal == 2:
            try:
                assert started.wait(2), 'Hidden real namespace worker must actually start'
            finally:
                release.set()
            raise later
        return future
    monkeypatch.setattr(update, '_unlinked', guarded)
    monkeypatch.setattr(update, '_intent_namespace_range', namespace_range)
    monkeypatch.setattr(ThreadPoolExecutor, 'submit', submit)
    retained = []
    expected = earlier if kind is RuntimeError else later
    with pytest.raises(type(expected)) as caught: run_real_namespace(application, manifest, retained)
    assert caught.value is expected
    assert captured and all(future.done() for future in captured)
    assert all(work.complete.is_set() for work in retained)
    if hidden_started:
        hidden = retained[-1]
        assert started.is_set() and hidden.started and hidden.future is None and hidden.complete.is_set()


@pytest.mark.parametrize('kind', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('earlier_error', [False, True])
def test_enumeration_baseexception_keeps_original_prefix_error_priority(tmp_path, monkeypatch, kind, earlier_error):
    application, manifest = native_files(tmp_path)
    paths = list(application.rglob('*'))
    assert paths and not paths[0].is_symlink()
    original_rglob = Path.rglob; original_guard = update._unlinked
    prefix_error = update.UpdateError('Exact earlier original prefix path')
    enumeration_error = kind('Exact later injected enumeration boundary')
    caller = threading.get_ident(); observed = []; submitted = []
    original_submit = ThreadPoolExecutor.submit
    def enumerate_paths(path, pattern, *args, **kwargs):
        if path != application: return original_rglob(path, pattern, *args, **kwargs)
        def partial():
            yield paths[0]
            raise enumeration_error
        return partial()
    def guard(path):
        value = original_guard(path)
        observed.append((Path(path), threading.get_ident()))
        if earlier_error and Path(path) == paths[0]: raise prefix_error
        return value
    def submit(executor, function, *args, **kwargs):
        submitted.append(function)
        return original_submit(executor, function, *args, **kwargs)
    monkeypatch.setattr(Path, 'rglob', enumerate_paths); monkeypatch.setattr(update, '_unlinked', guard)
    monkeypatch.setattr(ThreadPoolExecutor, 'submit', submit)
    retained = []; expected = prefix_error if earlier_error else enumeration_error
    with pytest.raises(type(expected)) as caught: run_real_namespace(application, manifest, retained)
    assert caught.value is expected
    assert observed == [(paths[0], caller)]
    assert submitted == [] and retained == [], 'Incomplete enumeration must guard its original prefix in the caller without new work'
