"""Root-only finite path-guard controls; author has not executed this module.

Native filesystem checks cover the executing host only. Windows checks below
are lexical only; injected errors identify their controlled stat boundary.
No update, archive, lease, process, service, model, or performance acceptance.
"""
import errno
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import posixpath
import ntpath

import pytest
from backend.engine import runtime_update as update


def original_unlinked(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise update.UpdateError('Update storage cannot follow links')
    return path


def observed_call(monkeypatch, function, path, failure=None, failure_path=None):
    original_stat = os.stat
    calls = []
    def observe(probe, *args, **kwargs):
        calls.append((os.fspath(probe), args, kwargs.copy()))
        if failure is not None and os.fspath(probe) == failure_path:
            raise failure
        return original_stat(probe, *args, **kwargs)
    with monkeypatch.context() as scope:
        scope.setattr(os, 'stat', observe)
        try:
            value = function(path)
        except BaseException as error:
            return None, error, calls
    return value, None, calls


def test_deep_native_path_preserves_every_probe_without_ancestor_path_allocations(tmp_path, monkeypatch):
    file = tmp_path/'owned'/'deep'/'original.bin'
    file.parent.mkdir(parents=True)
    file.write_bytes(b'original owned bytes')
    expected = [(os.fspath(p), (), {'follow_symlinks':False}) for p in (file,*file.parents)]
    value, error, calls = observed_call(monkeypatch, original_unlinked, file)
    assert error is None and type(value) is type(file) and value == file
    assert calls == expected
    allocations = []
    original = Path.parents
    def count(self):
        allocations.append(self)
        return original.fget(self)
    with monkeypatch.context() as scope:
        scope.setattr(Path, 'parents', property(count))
        value, error, calls = observed_call(monkeypatch, update._unlinked, file)
    assert error is None and type(value) is type(file) and value == file
    assert calls == expected
    assert allocations == [], 'Fresh stat guards must not construct a Path for every ancestor'


@pytest.mark.parametrize('where', ['leaf', 'ancestor', 'broken_leaf', 'two_links'])
def test_native_links_refuse_the_same_first_path_and_short_circuit(tmp_path, monkeypatch, where):
    target = tmp_path/'target'; target.mkdir()
    (target/'original.bin').write_bytes(b'owned')
    leaf = tmp_path/'original.bin'
    if where == 'leaf':
        leaf.symlink_to(target/'original.bin')
    elif where == 'broken_leaf':
        leaf.symlink_to(target/'missing.bin')
    else:
        parent = tmp_path/'linked'; parent.symlink_to(target, target_is_directory=True)
        if where == 'two_links':
            (target/'alias.bin').symlink_to(target/'original.bin')
            leaf = parent/'alias.bin'
        else:
            leaf = parent/'original.bin'
    first = leaf.parent if where == 'ancestor' else leaf
    expected = [(os.fspath(p), (), {'follow_symlinks':False}) for p in (leaf,*leaf.parents)]
    expected = expected[:1+next(i for i,r in enumerate(expected) if r[0] == os.fspath(first))]
    previous = None
    for function in (original_unlinked, update._unlinked):
        value, error, calls = observed_call(monkeypatch, function, leaf)
        assert value is None and type(error) is update.UpdateError
        assert str(error) == 'Update storage cannot follow links' and calls == expected
        if previous is not None: assert calls == previous
        previous = calls


def test_native_ancestor_alias_swap_is_observed_fresh_on_each_call(tmp_path, monkeypatch):
    for ordinal, function in enumerate((original_unlinked, update._unlinked)):
        root = tmp_path/str(ordinal); root.mkdir()
        owned = root/'owned'; owned.mkdir()
        file = owned/'original.bin'; file.write_bytes(b'unchanged owned bytes')
        first_value, first_error, first_calls = observed_call(monkeypatch,function,file)
        assert first_error is None and first_value == file
        parked = root/'parked'; owned.rename(parked)
        owned.symlink_to(parked, target_is_directory=True)
        value, error, linked_calls = observed_call(monkeypatch,function,file)
        assert value is None and type(error) is update.UpdateError
        assert [r[0] for r in linked_calls] == [str(file), str(owned)]
        owned.unlink(); parked.rename(owned)
        value, error, restored_calls = observed_call(monkeypatch,function,file)
        assert error is None and value == file and restored_calls == first_calls
        assert file.read_bytes() == b'unchanged owned bytes'


@pytest.mark.parametrize('shape', ['missing_leaf', 'missing_parent', 'file_parent'])
def test_missing_native_paths_preserve_original_ignored_errors_and_full_probes(tmp_path, monkeypatch, shape):
    if shape == 'missing_leaf':
        file = tmp_path/'missing.bin'
    elif shape == 'missing_parent':
        file = tmp_path/'absent'/'missing.bin'
    else:
        parent = tmp_path/'regular'; parent.write_bytes(b'not a directory')
        file = parent/'missing.bin'
    value, error, calls = observed_call(monkeypatch,original_unlinked,file)
    new_value, new_error, new_calls = observed_call(monkeypatch,update._unlinked,file)
    assert error is None and new_error is None and value == new_value == file
    assert calls == new_calls
    assert [r[0] for r in calls] == [str(p) for p in (file,*file.parents)]


@pytest.mark.parametrize('number', [errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP])
def test_exact_original_ignored_errno_keeps_next_ancestor_probe(tmp_path, monkeypatch, number):
    file = tmp_path/'original.bin'; file.write_bytes(b'owned')
    failure = OSError(number,'controlled original stat refusal')
    previous = None
    for function in (original_unlinked, update._unlinked):
        value,error,calls = observed_call(monkeypatch,function,file,failure,str(file))
        assert error is None and value == file
        if previous is not None: assert calls == previous
        previous = calls


@pytest.mark.parametrize('number', [21, 123, 1921])
def test_exact_original_winerror_filter_is_preserved_without_native_windows_claim(tmp_path, monkeypatch, number):
    file = tmp_path/'original.bin'; file.write_bytes(b'owned')
    failure = OSError(errno.EIO,'controlled Windows stat error field')
    failure.winerror = number
    previous = None
    for function in (original_unlinked, update._unlinked):
        value,error,calls = observed_call(monkeypatch,function,file,failure,str(file))
        assert error is None and value == file
        if previous is not None: assert calls == previous
        previous = calls


@pytest.mark.parametrize('failure', [OSError(errno.EACCES,'permission'), OSError(errno.EPERM,'permission'),
    OSError(errno.EIO,'io'), OSError(9999,'unknown'), AttributeError('unknown'), TypeError('unknown'),
    KeyboardInterrupt('interrupted'), SystemExit('interrupted')])
@pytest.mark.parametrize('depth', [0, 1])
def test_unknown_error_object_and_probe_cutoff_are_unchanged(tmp_path, monkeypatch, failure, depth):
    file = tmp_path/'owned'/'original.bin'; file.parent.mkdir(); file.write_bytes(b'owned')
    paths = [file,*file.parents]; previous = None
    for function in (original_unlinked, update._unlinked):
        value,error,calls = observed_call(monkeypatch,function,file,failure,str(paths[depth]))
        assert value is None and error is failure
        assert [r[0] for r in calls] == [str(p) for p in paths[:depth+1]]
        if previous is not None: assert calls == previous
        previous = calls


def test_original_valueerror_only_is_ignored(tmp_path, monkeypatch):
    file = tmp_path/'original.bin'; file.write_bytes(b'owned')
    failure = ValueError('controlled non-encodable original path')
    value,error,calls = observed_call(monkeypatch,original_unlinked,file,failure,str(file))
    new_value,new_error,new_calls = observed_call(monkeypatch,update._unlinked,file,failure,str(file))
    assert error is None and new_error is None and value == new_value == file
    assert calls == new_calls


@pytest.mark.parametrize('path', [None, 17])
def test_constructor_type_refusal_precedes_every_stat_probe(monkeypatch, path):
    value,error,calls = observed_call(monkeypatch,original_unlinked,path)
    new_value,new_error,new_calls = observed_call(monkeypatch,update._unlinked,path)
    assert value is None and new_value is None and type(error) is type(new_error) is TypeError
    assert str(error) == str(new_error) and calls == new_calls == []


@pytest.mark.parametrize('family, raw', [
    ('posix','/'), ('posix','//'), ('posix','///'), ('posix','/a//b/./c/'),
    ('posix','/a/../b'), ('posix','/../x'), ('posix','//server/share/a'), ('posix','//server/share/..'),
    ('windows','C:\\'), ('windows','C:\\a\\..\\b'), ('windows','C:/a//./b/'),
    ('windows','\\\\server\\share'), ('windows','\\\\server\\share\\a\\..\\b'),
    ('windows','\\\\?\\C:\\a\\b'), ('windows','\\\\?\\UNC\\server\\share\\a'), ('windows','C:\\..\\x')])
def test_string_ancestor_order_matches_original_pure_path_lexically(family, raw):
    cls, parser = (PurePosixPath,posixpath) if family == 'posix' else (PureWindowsPath,ntpath)
    path = cls(raw); current = os.fspath(path); sequence = []
    while True:
        sequence.append(current)
        parent = parser.dirname(current)
        if parent == current: break
        current = parent
    assert sequence == [str(path),*map(str,path.parents)]
