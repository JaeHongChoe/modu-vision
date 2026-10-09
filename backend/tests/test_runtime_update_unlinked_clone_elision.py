"""Root-only real-file controls for lexical clone elision, not filesystem proof caching.

The 44b3 guard oracle is copied verbatim except its function name. Every native
stat remains real except explicitly declared injected syscall-error controls.
The new exact-absolute branch deliberately returns its input object; equality,
type and ordered syscall names remain original. No foreign-platform, service,
signature, model or performance qualification is claimed by this module.
Author has not imported/executed this module. Root owns all actual tests.
"""
from pathlib import Path, PosixPath, PurePosixPath, PureWindowsPath
import errno
import os
import stat
import subprocess

import pytest
from backend.engine import runtime_update as update

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Native POSIX clone controls only')
UpdateError = update.UpdateError

def original_unlinked(path):
    native_parent=type(path)is PosixPath
    path=Path(path).absolute()
    current=os.fspath(path)
    # Lexical native names are not filesystem authority: every existing stat
    # below remains fresh. All other input kinds retain the original dirname.
    native_parent=(native_parent and type(path)is PosixPath and type(current)is str
        and current.startswith('/')and not current.startswith('///')
        and '//'not in current[2:]and(current in('/','//')or not current.endswith('/')))
    while True:
        try:linked=stat.S_ISLNK(os.stat(current,follow_symlinks=False).st_mode)
        except OSError as error:
            # Preserve Path.is_symlink's selective missing/unusable-path
            # errors; os.path.islink would also hide permission and I/O errors.
            if not (getattr(error,'errno',None)in(errno.ENOENT,errno.ENOTDIR,errno.EBADF,errno.ELOOP)
                    or getattr(error,'winerror',None)in(21,123,1921)):raise
            linked=False
        except ValueError:linked=False
        if linked:raise UpdateError('Update storage cannot follow links')
        if native_parent:
            slash=current.rfind('/')
            parent=current[:slash]if slash>1 else current[:slash+1]
        else:parent=os.path.dirname(current)
        if parent==current:return path
        current=parent


@pytest.fixture(autouse=True)
def no_child(monkeypatch):
    monkeypatch.setattr(subprocess.Popen, '__init__', lambda *a, **k: pytest.fail('No child permitted'))


def native_names(path):
    current = os.fspath(Path(path).absolute()); names = []
    while True:
        names.append(current); parent = os.path.dirname(current)
        if parent == current: return names
        current = parent


def observe(monkeypatch, function, path, failure=None, ordinal=None):
    original_stat = os.stat; original_init = Path.__init__; original_absolute = Path.absolute
    probes = []; constructors = []; absolutes = []; value = None; error = None
    def probe(name, *args, **kwargs):
        probes.append((name, args, dict(kwargs)))
        if failure is not None and len(probes) == ordinal: raise failure
        return original_stat(name, *args, **kwargs)
    def construct(instance, *args, **kwargs):
        constructors.append((args, dict(kwargs)))
        return original_init(instance, *args, **kwargs)
    def absolute(instance):
        absolutes.append(instance)
        return original_absolute(instance)
    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'stat', probe)
        scoped.setattr(Path, '__init__', construct)
        scoped.setattr(Path, 'absolute', absolute)
        try: value = function(path)
        except BaseException as caught: error = caught
    return value, error, probes, constructors, absolutes


def assert_fresh_order(probes, names):
    assert probes == [(name, (), {'follow_symlinks': False}) for name in names]


def test_exact_absolute_native_skips_initial_clone_and_absolute_but_retains_every_fresh_probe(tmp_path, monkeypatch):
    parent = tmp_path/'owned'; parent.mkdir(); path = parent/'leaf'; path.write_bytes(b'owned unchanged')
    names = native_names(path)
    old, old_error, old_probes, old_construct, old_absolute = observe(monkeypatch, original_unlinked, path)
    value, error, probes, constructors, absolutes = observe(monkeypatch, update._unlinked, path)
    assert old_error is None and error is None and old == value == path
    assert type(old) is type(value) is PosixPath
    assert_fresh_order(old_probes, names); assert probes == old_probes
    assert old is not path and old_construct and len(old_absolute) == 1
    assert path.read_bytes() == b'owned unchanged'
    # Genuine baseline RED occurs after the original fresh guard assertions.
    assert constructors == [] and absolutes == [], 'Original exact absolute guard still clones and calls Path.absolute'
    assert value is path, 'Exact-Posix return identity change is explicit'


@pytest.mark.parametrize('form', ['slash_root', 'double_root', 'absolute', 'double_slash', 'dotdot'])
def test_exact_native_root_forms_keep_ordered_real_syscalls_and_equal_path_value(tmp_path, monkeypatch, form):
    parent = tmp_path/'owned'; parent.mkdir(); path = parent/'leaf'; path.write_bytes(b'owned root forms')
    if form == 'slash_root': path = PosixPath('/')
    elif form == 'double_root': path = PosixPath('//')
    elif form == 'double_slash': path = PosixPath('//' + str(path).lstrip('/'))
    elif form == 'dotdot': path = parent/'..'/'owned'/'leaf'
    names = native_names(path)
    old, old_error, old_probes, _, _ = observe(monkeypatch, original_unlinked, path)
    value, error, probes, constructors, absolutes = observe(monkeypatch, update._unlinked, path)
    assert old_error is None and error is None and value == old and type(value) is type(old)
    assert_fresh_order(old_probes, names); assert probes == old_probes
    assert constructors == [] and absolutes == [] and value is path


@pytest.mark.parametrize('kind', ['absolute_string', 'relative_string', 'relative_posix', 'subclass', 'opaque_fspath', 'pure_posix', 'pure_windows'])
def test_nonexact_or_relative_inputs_keep_original_constructor_absolute_fallback(tmp_path, monkeypatch, kind):
    parent = tmp_path/'owned'; parent.mkdir(); leaf = parent/'leaf'; leaf.write_bytes(b'owned fallback')
    monkeypatch.chdir(tmp_path)
    class SubPath(PosixPath): pass
    class Opaque:
        def __fspath__(self): return str(leaf)
    path = {'absolute_string': str(leaf), 'relative_string': 'owned/leaf',
        'relative_posix': PosixPath('owned/leaf'), 'subclass': SubPath(leaf),
        'opaque_fspath': Opaque(), 'pure_posix': PurePosixPath(leaf),
        'pure_windows': PureWindowsPath('C:/owned/leaf')}[kind]
    old, old_error, old_probes, old_constructors, old_absolutes = observe(monkeypatch, original_unlinked, path)
    value, error, probes, constructors, absolutes = observe(monkeypatch, update._unlinked, path)
    assert old_error is None and error is None and value == old and type(value) is type(old)
    assert probes == old_probes and probes
    assert len(constructors) == len(old_constructors) and constructors
    assert len(absolutes) == len(old_absolutes) == 1
    if isinstance(path, PosixPath): assert value is not path


@pytest.mark.parametrize('kind', ['permission', 'io', 'keyboard', 'exit'])
@pytest.mark.parametrize('ordinal', [1, 3])
def test_native_error_object_and_exact_probe_cutoff_keep_original_priority(tmp_path, monkeypatch, kind, ordinal):
    parent = tmp_path/'owned'; parent.mkdir(); leaf = parent/'leaf'; leaf.write_bytes(b'owned error boundary')
    failure = {'permission': PermissionError(errno.EACCES, 'Exact syscall permission'),
        'io': OSError(errno.EIO, 'Exact syscall I/O'), 'keyboard': KeyboardInterrupt('Exact syscall caller'),
        'exit': SystemExit('Exact syscall caller')}[kind]
    old, old_error, old_probes, _, _ = observe(monkeypatch, original_unlinked, leaf, failure, ordinal)
    value, error, probes, constructors, absolutes = observe(monkeypatch, update._unlinked, leaf, failure, ordinal)
    assert old is None and value is None and old_error is error is failure
    assert probes == old_probes and len(probes) == ordinal
    assert probes == [(name, (), {'follow_symlinks': False}) for name in native_names(leaf)[:ordinal]]
    assert constructors == [] and absolutes == []


@pytest.mark.parametrize('kind', ['missing', 'not_directory', 'bad_fd', 'loop', 'value'])
def test_original_selective_missing_errors_still_probe_all_native_ancestors(tmp_path, monkeypatch, kind):
    parent = tmp_path/'owned'; parent.mkdir(); leaf = parent/'leaf'; leaf.write_bytes(b'owned suppression')
    failure = {'missing': FileNotFoundError(errno.ENOENT, 'Exact missing syscall'),
        'not_directory': NotADirectoryError(errno.ENOTDIR, 'Exact non-directory syscall'),
        'bad_fd': OSError(errno.EBADF, 'Exact bad descriptor syscall'),
        'loop': OSError(errno.ELOOP, 'Exact link-loop syscall'), 'value': ValueError('Exact unusable path')}[kind]
    old, old_error, old_probes, _, _ = observe(monkeypatch, original_unlinked, leaf, failure, 1)
    value, error, probes, constructors, absolutes = observe(monkeypatch, update._unlinked, leaf, failure, 1)
    assert old_error is None and error is None and old == value == leaf
    assert_fresh_order(old_probes, native_names(leaf)); assert probes == old_probes
    assert constructors == [] and absolutes == []


def test_native_ancestor_alias_swap_is_detected_fresh_after_success_and_restoration(tmp_path, monkeypatch):
    parent = tmp_path/'owned'; parent.mkdir(); leaf = parent/'leaf'; leaf.write_bytes(b'owned alias boundary')
    preserved = tmp_path/'preserved'; names = native_names(leaf)
    for function in (original_unlinked, update._unlinked):
        value, error, probes, _, _ = observe(monkeypatch, function, leaf)
        assert error is None and value == leaf; assert_fresh_order(probes, names)
    parent.rename(preserved); parent.symlink_to(preserved, target_is_directory=True)
    try:
        for function in (original_unlinked, update._unlinked):
            value, error, probes, _, _ = observe(monkeypatch, function, leaf)
            assert value is None and isinstance(error, update.UpdateError)
            assert str(error) == 'Update storage cannot follow links'
            assert_fresh_order(probes, names[:2])
        assert (preserved/'leaf').read_bytes() == b'owned alias boundary'
    finally:
        parent.unlink(); preserved.rename(parent)
    for function in (original_unlinked, update._unlinked):
        value, error, probes, _, _ = observe(monkeypatch, function, leaf)
        assert error is None and value == leaf; assert_fresh_order(probes, names)


def test_relative_fallback_recomputes_original_cwd_value_on_each_call(tmp_path, monkeypatch):
    first = tmp_path/'first'; second = tmp_path/'second'; first.mkdir(); second.mkdir()
    (first/'leaf').write_bytes(b'first owned'); (second/'leaf').write_bytes(b'second owned')
    relative = PosixPath('leaf'); values = []
    for cwd in (first, second):
        monkeypatch.chdir(cwd)
        old, old_error, old_probes, old_construct, old_absolute = observe(monkeypatch, original_unlinked, relative)
        value, error, probes, constructors, absolutes = observe(monkeypatch, update._unlinked, relative)
        assert old_error is None and error is None and value == old == cwd/'leaf'
        assert probes == old_probes and len(constructors) == len(old_construct)
        assert len(absolutes) == len(old_absolute) == 1 and value is not relative
        values.append(value)
    assert values[0] != values[1]
