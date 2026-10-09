"""Root-only genuine fresh namespace controls; author has not executed tests.

The allocation causality probe counts real Path.relative_to calls while keeping
every original fresh guard. Direct namespace tests use only owned tmp files;
they are not signatures, whole-tree5s, compiled app, service or model proof.
"""
from pathlib import Path, PosixPath
import os
import subprocess

import pytest
from backend.engine import runtime_update as update
from backend.tests.test_runtime_update_parallel_namespace import native_files


@pytest.fixture(autouse=True)
def no_child(monkeypatch):
    monkeypatch.setattr(subprocess.Popen, '__init__', lambda *a, **k: pytest.fail('No child permitted'))


def test_exact_posix_namespace_avoids_relative_parent_allocations_but_keeps_both_fresh_guard_sequences(tmp_path, monkeypatch):
    application, manifest = native_files(tmp_path)
    paths = list(application.rglob('*'))
    expected = [path.parent if path.is_symlink() else path for path in paths]
    original_relative = Path.relative_to; original_guard = update._unlinked
    original_symlink = Path.is_symlink; original_file = Path.is_file; original_dir = Path.is_dir
    relative_calls = []; guarded = []; symlink_calls = []; file_calls = []; directory_calls = []
    def relative(path, *args, **kwargs):
        relative_calls.append(path)
        return original_relative(path, *args, **kwargs)
    def guard(path):
        guarded.append(Path(path))
        return original_guard(path)
    def symlink(path):
        symlink_calls.append(path)
        return original_symlink(path)
    def regular(path):
        file_calls.append(path)
        return original_file(path)
    def directory(path):
        directory_calls.append(path)
        return original_dir(path)
    monkeypatch.setattr(Path, 'relative_to', relative); monkeypatch.setattr(update, '_unlinked', guard)
    monkeypatch.setattr(Path, 'is_symlink', symlink); monkeypatch.setattr(Path, 'is_file', regular); monkeypatch.setattr(Path, 'is_dir', directory)
    update._installed_intent_namespace(application, manifest)
    update._installed_intent_namespace(application, manifest)
    assert relative_calls == [], 'Exact native namespace names must avoid per-member relative parent Path construction'
    assert guarded == expected * 2 and symlink_calls == paths * 2
    ordinary = [path for path in paths if not original_symlink(path)]
    directories = [path for path in ordinary if not original_file(path)]
    assert file_calls == ordinary * 2 and directory_calls == directories * 2


@pytest.mark.parametrize('root_kind', ['absolute', 'relative', 'double_slash', 'dot'])
def test_real_normalized_posix_roots_keep_exact_membership_and_all_guard_calls(tmp_path, monkeypatch, root_kind):
    application, manifest = native_files(tmp_path)
    if root_kind == 'relative':
        monkeypatch.chdir(tmp_path); application = Path(application.name)
    elif root_kind == 'double_slash':
        application = Path('//' + str(application).lstrip('/'))
    elif root_kind == 'dot':
        monkeypatch.chdir(application); application = Path('.')
    paths = list(application.rglob('*')); original_guard = update._unlinked; original_relative = Path.relative_to
    guarded = []; relatives = []
    def guard(path):
        guarded.append(Path(path))
        return original_guard(path)
    def relative(path, *args, **kwargs):
        relatives.append(path)
        return original_relative(path, *args, **kwargs)
    monkeypatch.setattr(update, '_unlinked', guard); monkeypatch.setattr(Path, 'relative_to', relative)
    update._installed_intent_namespace(application, manifest)
    assert guarded == [path.parent if path.is_symlink() else path for path in paths]
    assert relatives == (paths if root_kind == 'dot' else [])


def test_concrete_subclass_falls_back_to_every_original_relative_call(tmp_path, monkeypatch):
    class SubPath(PosixPath):
        pass
    application, manifest = native_files(tmp_path); application = SubPath(application)
    paths = list(application.rglob('*')); original_relative = Path.relative_to; calls = []
    def relative(path, *args, **kwargs):
        calls.append(path)
        return original_relative(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'relative_to', relative)
    update._installed_intent_namespace(application, manifest)
    assert paths and all(type(path) is SubPath for path in paths) and calls == paths


def test_custom_pathlike_application_keeps_original_relative_fallback(tmp_path, monkeypatch):
    application, manifest = native_files(tmp_path)
    class Application:
        def __fspath__(self): return str(application)
        def rglob(self, pattern): return application.rglob(pattern)
    original_relative = Path.relative_to; calls = []; paths = list(application.rglob('*'))
    def relative(path, *args, **kwargs):
        calls.append(path)
        return original_relative(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'relative_to', relative)
    update._installed_intent_namespace(Application(), manifest)
    assert calls == paths


@pytest.mark.parametrize('outside_kind', ['ordinary_sibling', 'shared_string_prefix'])
@pytest.mark.parametrize('later_kind', [KeyboardInterrupt, SystemExit])
def test_outside_member_original_relative_error_precedes_any_guard_and_later_enumeration_interrupt(tmp_path, monkeypatch, outside_kind, later_kind):
    application, manifest = native_files(tmp_path)
    outside = tmp_path / (application.name + '-foreign' if outside_kind == 'shared_string_prefix' else 'foreign')
    outside.mkdir(); member = outside/'preserved'; member.write_bytes(b'foreign unchanged')
    original_rglob = Path.rglob; original_relative = Path.relative_to; original_guard = update._unlinked; calls = []; guarded = []
    later = later_kind('Exact later original enumeration interruption')
    def enumerate_paths(path, pattern, *args, **kwargs):
        if path != application: return original_rglob(path, pattern, *args, **kwargs)
        def partial():
            yield member
            raise later
        return partial()
    def relative(path, *args, **kwargs):
        calls.append(path)
        return original_relative(path, *args, **kwargs)
    def guard(path):
        guarded.append(path)
        return original_guard(path)
    monkeypatch.setattr(Path, 'rglob', enumerate_paths); monkeypatch.setattr(Path, 'relative_to', relative)
    monkeypatch.setattr(update, '_unlinked', guard)
    with pytest.raises(ValueError, match='not in the subpath'):
        update._installed_intent_namespace(application, manifest)
    assert calls == [member] and guarded == [] and member.read_bytes() == b'foreign unchanged'


def test_root_equal_member_keeps_original_dot_relative_name_and_guard_before_schema_error(tmp_path, monkeypatch):
    application, manifest = native_files(tmp_path)
    original_rglob = Path.rglob; original_relative = Path.relative_to; original_guard = update._unlinked
    calls = []; guarded = []
    def enumerate_paths(path, pattern, *args, **kwargs):
        if path == application: return iter([application])
        return original_rglob(path, pattern, *args, **kwargs)
    def relative(path, *args, **kwargs):
        value = original_relative(path, *args, **kwargs); calls.append((path, value.as_posix()))
        return value
    def guard(path):
        guarded.append(path)
        return original_guard(path)
    monkeypatch.setattr(Path, 'rglob', enumerate_paths); monkeypatch.setattr(Path, 'relative_to', relative); monkeypatch.setattr(update, '_unlinked', guard)
    with pytest.raises(update.UpdateError, match='directory membership differs'):
        update._installed_intent_namespace(application, manifest)
    assert calls == [(application, '.')] and guarded == [application]


@pytest.mark.parametrize('kind', [ValueError, KeyboardInterrupt, SystemExit])
def test_yielded_subclass_original_relative_exception_identity_is_preserved(tmp_path, monkeypatch, kind):
    application, manifest = native_files(tmp_path)
    first = kind('Exact original subclass name operation')
    class SubPath(PosixPath):
        def relative_to(self, *args, **kwargs): raise first
    member = SubPath(application/manifest['files'][0]['path'])
    original_rglob = Path.rglob; original_guard = update._unlinked; guarded = []
    def enumerate_paths(path, pattern, *args, **kwargs):
        if path == application: return iter([member])
        return original_rglob(path, pattern, *args, **kwargs)
    def guard(path):
        guarded.append(path)
        return original_guard(path)
    monkeypatch.setattr(Path, 'rglob', enumerate_paths); monkeypatch.setattr(update, '_unlinked', guard)
    with pytest.raises(kind) as caught: update._installed_intent_namespace(application, manifest)
    assert caught.value is first and guarded == []


@pytest.mark.parametrize('later_kind', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('prefix_error', [False, True])
def test_serial_fast_namespace_keeps_original_prefix_guard_before_later_enumeration_baseexception(tmp_path, monkeypatch, later_kind, prefix_error):
    application, manifest = native_files(tmp_path)
    member = application/manifest['files'][0]['path']
    original_rglob = Path.rglob; original_guard = update._unlinked; guarded = []
    first = update.UpdateError('Exact original prefix guard refusal'); later = later_kind('Exact later original enumeration interruption')
    def enumerate_paths(path, pattern, *args, **kwargs):
        if path != application: return original_rglob(path, pattern, *args, **kwargs)
        def partial():
            yield member
            raise later
        return partial()
    def guard(path):
        value = original_guard(path); guarded.append(path)
        if prefix_error: raise first
        return value
    monkeypatch.setattr(Path, 'rglob', enumerate_paths); monkeypatch.setattr(update, '_unlinked', guard)
    expected = first if prefix_error else later
    with pytest.raises(type(expected)) as caught: update._installed_intent_namespace(application, manifest)
    assert caught.value is expected and guarded == [member]


@pytest.mark.parametrize('kind,message', [
    ('changed_link','link target changed'), ('regular_alias','replaced with a regular member'),
    ('unexpected_link','link target changed'), ('special','special file'),
    ('extra_directory','directory membership differs'), ('missing_regular','membership differs'),
    ('unlisted_regular','membership differs'), ('linked_application_parent','cannot follow links')])
def test_serial_fast_namespace_fresh_real_native_negatives_keep_original_refusals(tmp_path, kind, message):
    application, manifest = native_files(tmp_path)
    outside = tmp_path/'owned-outside'; outside.mkdir(); marker = outside/'marker'; marker.write_bytes(b'foreign preserved')
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
    with pytest.raises(update.UpdateError, match=message): update._installed_intent_namespace(application, manifest)
    assert marker.read_bytes() == b'foreign preserved'


def test_serial_fast_schema_one_original_unlisted_directory_semantics(tmp_path):
    application, manifest = native_files(tmp_path)
    (application/manifest['links'][0]['path']).unlink(); manifest['schema_version'] = 1; manifest.pop('links')
    (application/'ordinary-original-directory').mkdir()
    update._installed_intent_namespace(application, manifest)


@pytest.mark.parametrize('member_kind', ['double_anchor_root', 'double_anchor_owned_file'])
@pytest.mark.parametrize('later_kind', [KeyboardInterrupt, SystemExit])
def test_slash_root_rejects_double_slash_anchor_before_any_guard_or_later_enumeration_interrupt(tmp_path, monkeypatch, member_kind, later_kind):
    owned_application, manifest = native_files(tmp_path)
    application = PosixPath('/')
    member = PosixPath('//') if member_kind == 'double_anchor_root' else PosixPath('//' + str(owned_application/manifest['files'][0]['path']).lstrip('/'))
    original_rglob = Path.rglob; original_relative = Path.relative_to; original_guard = update._unlinked
    calls = []; guarded = []; later = later_kind('Exact later original root-anchor enumeration interruption')
    def enumerate_paths(path, pattern, *args, **kwargs):
        if path != application: return original_rglob(path, pattern, *args, **kwargs)
        def partial():
            yield member
            raise later
        return partial()
    def relative(path, *args, **kwargs):
        calls.append(path)
        return original_relative(path, *args, **kwargs)
    def guard(path):
        guarded.append(path)
        return original_guard(path)
    monkeypatch.setattr(Path, 'rglob', enumerate_paths); monkeypatch.setattr(Path, 'relative_to', relative); monkeypatch.setattr(update, '_unlinked', guard)
    with pytest.raises(ValueError, match='not in the subpath'): update._installed_intent_namespace(application, manifest)
    assert application.anchor == '/' and member.anchor == '//'
    assert calls == [member] and guarded == []


@pytest.mark.parametrize('anchor', ['/', '//'])
@pytest.mark.parametrize('later_kind', [KeyboardInterrupt, SystemExit])
def test_equal_literal_posix_root_keeps_dot_name_and_original_guard_before_schema_error_and_later_interrupt(tmp_path, monkeypatch, anchor, later_kind):
    _, manifest = native_files(tmp_path)
    application = PosixPath(anchor); original_rglob = Path.rglob; original_relative = Path.relative_to; original_guard = update._unlinked
    calls = []; guarded = []; later = later_kind('Exact later original equal-root enumeration interruption')
    def enumerate_paths(path, pattern, *args, **kwargs):
        if path != application: return original_rglob(path, pattern, *args, **kwargs)
        def partial():
            yield application
            raise later
        return partial()
    def relative(path, *args, **kwargs):
        value = original_relative(path, *args, **kwargs); calls.append((path, value.as_posix()))
        return value
    def guard(path):
        guarded.append(path)
        return original_guard(path)
    monkeypatch.setattr(Path, 'rglob', enumerate_paths); monkeypatch.setattr(Path, 'relative_to', relative); monkeypatch.setattr(update, '_unlinked', guard)
    with pytest.raises(update.UpdateError, match='directory membership differs'):
        update._installed_intent_namespace(application, manifest)
    assert application.anchor == anchor and calls == [(application, '.')] and guarded == [application]
