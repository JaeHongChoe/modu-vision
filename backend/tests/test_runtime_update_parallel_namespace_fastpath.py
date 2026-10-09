"""Root-only causal controls for the same-invocation fresh namespace candidate.

Author has not imported or run this module. Real filesystem probes are POSIX;
opaque/subclass fallbacks do not claim foreign-platform runtime qualification.
No application/service/model subprocess is permitted. Original signed fixture,
transition OFD, byte guards and worker ownership remain real when Root runs it.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PosixPath
import os
import subprocess
import threading

import pytest
from backend.engine import application_launch_lease as lease
from backend.engine import runtime_update as update
from backend.tests.test_application_backend_intent_composition import original
from backend.tests.test_runtime_update_parallel_namespace import native_files, run_real_namespace
from backend.tests.test_service_s6_04 import controlled_preactivation_guard

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Native POSIX filesystem controls only')


@pytest.fixture(autouse=True)
def no_child(monkeypatch):
    monkeypatch.setattr(subprocess.Popen, '__init__', lambda *a, **k: pytest.fail('No child permitted'))


def expected_names(manifest):
    return {row['path'] for row in manifest['files']} | {row['path'] for row in manifest['links']} | {'portable-application.json'}


def test_owning_validator_uses_two_fresh_namespace_passes_in_same_max4_executor(original, monkeypatch):
    root, record, _, _, _, _ = original
    application = root/update.GENERATIONS/record['binding']['application_generation']/'application'
    paths = list(application.rglob('*')); assert len(paths) >= 4
    original_namespace = update._intent_parallel_namespace; phases = []
    def namespace(executor, current, manifest, retained, **kwargs):
        start = len(retained)
        value = original_namespace(executor, current, manifest, retained, **kwargs)
        phases.append((executor, retained, start, list(retained[start:])))
        return value
    monkeypatch.setattr(update, '_intent_parallel_namespace', namespace)
    with lease._transition_admission(root, record['nonce']):
        update._validated_intent(root, record['binding']['update_id'])
    assert len(phases) == 2, 'Owning validator must retain both complete fresh namespace jobs'
    assert [phase[2] for phase in phases] == [1, 6]
    assert phases[0][0] is phases[1][0] and phases[0][1] is phases[1][1]
    executor, retained = phases[0][:2]
    assert executor._max_workers == 4 and executor._shutdown
    assert all(not thread.is_alive() for thread in executor._threads)
    assert len(retained) == 7
    assert [item.function.__name__ for item in retained] == ['_bundle', '_intent_namespace_range'] + ['_intent_zip_range'] * 2 + ['_intent_installed_range'] * 2 + ['_intent_namespace_range']
    assert all(item.started and item.complete.is_set() and item.future.done() for item in retained)
    for _, _, _, work in phases:
        assert len(work) == 1
        assert [path for item in work for path in item.args[-1]] == paths


def test_native_range_avoids_relative_allocation_and_retains_every_fresh_guard_in_both_passes(tmp_path, monkeypatch):
    application, manifest = native_files(tmp_path); paths = list(application.rglob('*'))
    links, directories = update._native_layout(manifest)
    original_relative = Path.relative_to; original_guard = update._unlinked
    original_symlink = Path.is_symlink; original_file = Path.is_file; original_dir = Path.is_dir
    expected_guard = [path.parent if original_symlink(path) else path for path in paths]
    ordinary = [path for path in paths if not original_symlink(path)]
    directory_paths = [path for path in ordinary if not original_file(path)]
    relative_calls = []; guarded = []; symlink_calls = []; file_calls = []; directory_calls = []
    def relative(path, *args, **kwargs):
        relative_calls.append(path); return original_relative(path, *args, **kwargs)
    def guard(path):
        guarded.append(Path(path)); return original_guard(path)
    def symlink(path):
        symlink_calls.append(path); return original_symlink(path)
    def regular(path):
        file_calls.append(path); return original_file(path)
    def directory(path):
        directory_calls.append(path); return original_dir(path)
    monkeypatch.setattr(Path, 'relative_to', relative); monkeypatch.setattr(update, '_unlinked', guard)
    monkeypatch.setattr(Path, 'is_symlink', symlink); monkeypatch.setattr(Path, 'is_file', regular); monkeypatch.setattr(Path, 'is_dir', directory)
    for _ in range(2):
        assert update._intent_namespace_range(application, manifest, links, directories, paths) == expected_names(manifest)
    assert relative_calls == [], 'Original range creates a relative Path for every member'
    assert guarded == expected_guard * 2 and symlink_calls == paths * 2
    assert file_calls == ordinary * 2 and directory_calls == directory_paths * 2


@pytest.mark.parametrize('kind', ['absolute', 'relative', 'double_slash', 'dot'])
def test_real_range_root_forms_keep_original_names_and_guard_sequence(tmp_path, monkeypatch, kind):
    application, manifest = native_files(tmp_path)
    if kind == 'relative':
        monkeypatch.chdir(tmp_path); application = Path(application.name)
    elif kind == 'double_slash': application = Path('//' + str(application).lstrip('/'))
    elif kind == 'dot':
        monkeypatch.chdir(application); application = Path('.')
    paths = list(application.rglob('*')); links, directories = update._native_layout(manifest)
    original_relative = Path.relative_to; original_guard = update._unlinked; calls = []; guarded = []
    def relative(path, *args, **kwargs):
        calls.append(path); return original_relative(path, *args, **kwargs)
    def guard(path):
        guarded.append(Path(path)); return original_guard(path)
    monkeypatch.setattr(Path, 'relative_to', relative); monkeypatch.setattr(update, '_unlinked', guard)
    assert update._intent_namespace_range(application, manifest, links, directories, paths) == expected_names(manifest)
    assert calls == (paths if kind == 'dot' else [])
    assert guarded == [path.parent if path.is_symlink() else path for path in paths]


def test_range_concrete_subclass_keeps_every_original_relative_call(tmp_path, monkeypatch):
    class SubPath(PosixPath): pass
    application, manifest = native_files(tmp_path); application = SubPath(application)
    paths = list(application.rglob('*')); links, directories = update._native_layout(manifest)
    original_relative = Path.relative_to; calls = []
    def relative(path, *args, **kwargs):
        calls.append(path); return original_relative(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'relative_to', relative)
    assert update._intent_namespace_range(application, manifest, links, directories, paths) == expected_names(manifest)
    assert paths and all(type(path) is SubPath for path in paths) and calls == paths


def test_range_opaque_application_keeps_every_original_relative_call(tmp_path, monkeypatch):
    application, manifest = native_files(tmp_path)
    class Application:
        def __fspath__(self): return str(application)
    paths = list(application.rglob('*')); links, directories = update._native_layout(manifest)
    original_relative = Path.relative_to; calls = []
    def relative(path, *args, **kwargs):
        calls.append(path); return original_relative(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'relative_to', relative)
    assert update._intent_namespace_range(Application(), manifest, links, directories, paths) == expected_names(manifest)
    assert calls == paths


@pytest.mark.parametrize('kind', ['ordinary_sibling', 'shared_string_prefix'])
def test_range_outside_member_preserves_relative_refusal_before_any_fresh_guard(tmp_path, monkeypatch, kind):
    application, manifest = native_files(tmp_path); links, directories = update._native_layout(manifest)
    outside = tmp_path/(application.name + '-foreign' if kind == 'shared_string_prefix' else 'foreign')
    outside.mkdir(); member = outside/'preserved'; member.write_bytes(b'foreign unchanged')
    original_relative = Path.relative_to; original_guard = update._unlinked; calls = []; guarded = []
    def relative(path, *args, **kwargs):
        calls.append(path); return original_relative(path, *args, **kwargs)
    def guard(path):
        guarded.append(path); return original_guard(path)
    monkeypatch.setattr(Path, 'relative_to', relative); monkeypatch.setattr(update, '_unlinked', guard)
    with pytest.raises(ValueError, match='not in the subpath'):
        update._intent_namespace_range(application, manifest, links, directories, [member])
    assert calls == [member] and guarded == [] and member.read_bytes() == b'foreign unchanged'


@pytest.mark.parametrize('kind', [ValueError, KeyboardInterrupt, SystemExit])
def test_range_subclass_relative_exception_identity_precedes_any_guard(tmp_path, monkeypatch, kind):
    application, manifest = native_files(tmp_path); links, directories = update._native_layout(manifest)
    first = kind('Exact original subclass relative operation')
    class SubPath(PosixPath):
        def relative_to(self, *args, **kwargs): raise first
    member = SubPath(application/manifest['files'][0]['path']); guarded = []
    def guard(path):
        guarded.append(path); raise AssertionError('Original relative refusal must precede guard')
    monkeypatch.setattr(update, '_unlinked', guard)
    with pytest.raises(kind) as caught:
        update._intent_namespace_range(application, manifest, links, directories, [member])
    assert caught.value is first and guarded == []


def test_range_slash_root_does_not_admit_double_anchor_member(tmp_path, monkeypatch):
    application, manifest = native_files(tmp_path); links, directories = update._native_layout(manifest)
    member = PosixPath('//' + str(application/manifest['files'][0]['path']).lstrip('/'))
    guarded = []
    def guard(path):
        guarded.append(path); raise AssertionError('Original anchor refusal must precede guard')
    monkeypatch.setattr(update, '_unlinked', guard)
    with pytest.raises(ValueError, match='not in the subpath'):
        update._intent_namespace_range(PosixPath('/'), manifest, links, directories, [member])
    assert guarded == []


def test_range_root_equal_retains_dot_name_and_guard_before_directory_refusal(tmp_path, monkeypatch):
    application, manifest = native_files(tmp_path); links, directories = update._native_layout(manifest)
    original_relative = Path.relative_to; original_guard = update._unlinked; names = []; guarded = []
    def relative(path, *args, **kwargs):
        value = original_relative(path, *args, **kwargs); names.append(value.as_posix()); return value
    def guard(path):
        guarded.append(path); return original_guard(path)
    monkeypatch.setattr(Path, 'relative_to', relative); monkeypatch.setattr(update, '_unlinked', guard)
    with pytest.raises(update.UpdateError, match='directory membership differs'):
        update._intent_namespace_range(application, manifest, links, directories, [application])
    assert names == ['.'] and guarded == [application]


@pytest.mark.parametrize('kind', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('prefix_error', [False, True])
def test_incomplete_namespace_checks_original_prefix_in_caller_without_submitting_workers(tmp_path, monkeypatch, kind, prefix_error):
    application, manifest = native_files(tmp_path); member = list(application.rglob('*'))[0]
    original_rglob = Path.rglob; original_guard = update._unlinked
    original_submit = update.ThreadPoolExecutor.submit; caller = threading.get_ident()
    first = OSError('Exact earlier original prefix guard'); later = kind('Exact later enumeration caller')
    guarded = []; submitted = []
    def enumerate_paths(path, pattern, *args, **kwargs):
        if path != application: return original_rglob(path, pattern, *args, **kwargs)
        def partial():
            yield member
            raise later
        return partial()
    def guard(path):
        value = original_guard(path); guarded.append((path, threading.get_ident()))
        if prefix_error: raise first
        return value
    def submit(executor, function, *args, **kwargs):
        submitted.append(function)
        return original_submit(executor, function, *args, **kwargs)
    monkeypatch.setattr(Path, 'rglob', enumerate_paths); monkeypatch.setattr(update, '_unlinked', guard)
    monkeypatch.setattr(update.ThreadPoolExecutor, 'submit', submit)
    expected = first if prefix_error else later; retained = []
    with pytest.raises(type(expected)) as caught: run_real_namespace(application, manifest, retained)
    assert caught.value is expected and guarded == [(member, caller)]
    assert submitted == [] and retained == []


@pytest.mark.parametrize('kind', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('phase', [1, 2])
@pytest.mark.parametrize('position', ['first', 'after_prefix'])
def test_owning_pre_and_post_enumeration_interruption_starts_no_further_stage_work(original, monkeypatch, kind, phase, position):
    root, record, _, _, _, _ = original
    application = root/update.GENERATIONS/record['binding']['application_generation']/'application'
    paths = list(application.rglob('*')); assert paths
    original_rglob = Path.rglob; original_submit = ThreadPoolExecutor.submit
    scans = []; submitted = []; interrupted = []; first = kind('Exact original enumeration caller')
    def enumerate_paths(path, pattern, *args, **kwargs):
        if path != application: return original_rglob(path, pattern, *args, **kwargs)
        scans.append(path)
        if len(scans) != phase: return original_rglob(path, pattern, *args, **kwargs)
        def partial():
            if position == 'after_prefix': yield paths[0]
            interrupted.append(True)
            raise first
        return partial()
    def submit(executor, function, *args, **kwargs):
        assert not interrupted, 'No new worker submission after caller enumeration interruption'
        submitted.append(function.__self__)
        return original_submit(executor, function, *args, **kwargs)
    monkeypatch.setattr(Path, 'rglob', enumerate_paths); monkeypatch.setattr(ThreadPoolExecutor, 'submit', submit)
    with lease._transition_admission(root, record['nonce']), pytest.raises(kind) as caught:
        update._validated_intent(root, record['binding']['update_id'])
    assert caught.value is first and len(scans) == phase and interrupted == [True]
    expected = ['_bundle'] if phase == 1 else ['_bundle', '_intent_namespace_range'] + ['_intent_zip_range'] * 2 + ['_intent_installed_range'] * 2
    assert [item.function.__name__ for item in submitted] == expected
    assert all(item.complete.is_set() and item.future.done() for item in submitted)
