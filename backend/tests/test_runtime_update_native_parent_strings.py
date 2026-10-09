"""Real Root-only fresh native parent controls; Source author never executes this module."""
from pathlib import Path, PosixPath, PureWindowsPath
import errno
import os
import stat
import pytest
from backend.engine import runtime_update as update
UpdateError = update.UpdateError


def original_unlinked(path):
    path=Path(path).absolute()
    current=os.fspath(path)
    while True:
        try:linked=stat.S_ISLNK(os.stat(current,follow_symlinks=False).st_mode)
        except OSError as error:
            if not (getattr(error,'errno',None)in(errno.ENOENT,errno.ENOTDIR,errno.EBADF,errno.ELOOP)
                    or getattr(error,'winerror',None)in(21,123,1921)):raise
            linked=False
        except ValueError:linked=False
        if linked:raise UpdateError('Update storage cannot follow links')
        parent=os.path.dirname(current)
        if parent==current:return path
        current=parent


def observe(monkeypatch, function, path, failure=None, ordinal=None):
    native_stat=os.stat;calls=[]
    def seen(probe,*args,**kwargs):
        calls.append((os.fspath(probe),args,kwargs.copy()))
        if failure is not None and len(calls)==ordinal:raise failure
        return native_stat(probe,*args,**kwargs)
    with monkeypatch.context()as scope:
        scope.setattr(os,'stat',seen)
        try:return function(path),None,calls
        except BaseException as error:return None,error,calls


def compare(monkeypatch,path,failure=None,ordinal=None):
    old,old_error,old_calls=observe(monkeypatch,original_unlinked,path,failure,ordinal)
    new,new_error,new_calls=observe(monkeypatch,update._unlinked,path,failure,ordinal)
    assert new_calls==old_calls
    if old_error is None:
        assert new_error is None and type(new)is type(old)and new==old
    elif failure is not None and old_error is failure:
        assert new_error is failure
    else:
        assert type(new_error)is type(old_error)and str(new_error)==str(old_error)
    return new,new_error,new_calls


def test_exact_native_parent_chain_avoids_general_dirname_allocations_and_keeps_all_fresh_stat_guards(tmp_path,monkeypatch):
    leaf=tmp_path/'a'/'b'/'c'/'member';leaf.parent.mkdir(parents=True);leaf.write_bytes(b'fresh')
    expected=[(os.fspath(p),(),{'follow_symlinks':False})for p in(leaf,*leaf.parents)]
    dirname=os.path.dirname;old_parent_calls=[];new_parent_calls=[]
    def old_seen(value):old_parent_calls.append(value);return dirname(value)
    def new_seen(value):new_parent_calls.append(value);return dirname(value)
    with monkeypatch.context()as scope:
        scope.setattr(os.path,'dirname',old_seen)
        old,old_error,old_calls=observe(monkeypatch,original_unlinked,leaf)
    with monkeypatch.context()as scope:
        scope.setattr(os.path,'dirname',new_seen)
        new,new_error,new_calls=observe(monkeypatch,update._unlinked,leaf)
    assert old_error is new_error is None
    assert type(new)is type(old)is PosixPath and new==old
    assert old_calls==new_calls==expected
    assert old_parent_calls==[p[0]for p in expected]
    assert new_parent_calls==[], 'Exact native parents must avoid repeated general dirname while retaining every fresh stat guard'


@pytest.mark.parametrize('raw',['/','//','/missing/member','//missing/member'])
def test_literal_root_anchors_and_root_children_preserve_exact_fresh_probe_sequence(monkeypatch,raw):
    # No filesystem is changed or enumerated; ordinary fresh stat includes the named root.
    path=PosixPath(raw);_,error,calls=compare(monkeypatch,path)
    assert error is None
    expected=[(os.fspath(p),(),{'follow_symlinks':False})for p in(path,*path.parents)]
    assert calls==expected


@pytest.mark.parametrize('suffix',['member','a//member','a/./member','a/../member','./member','../member'])
def test_absolute_relative_repeated_slash_dot_and_dotdot_remain_original_lexical_paths(tmp_path,monkeypatch,suffix):
    monkeypatch.chdir(tmp_path);(tmp_path/'a').mkdir();(tmp_path/'member').write_bytes(b'x')
    for value in(PosixPath(suffix),PosixPath(str(tmp_path)+'/'+suffix)):
        _,error,calls=compare(monkeypatch,value)
        assert error is None
        expected=value.absolute()
        assert calls==[(os.fspath(p),(),{'follow_symlinks':False})for p in(expected,*expected.parents)]


@pytest.mark.parametrize('kind',['subclass','custom_fspath','str','str_subclass','pure_windows'])
def test_nonexact_native_inputs_use_whole_original_dirname_fallback(tmp_path,monkeypatch,kind):
    class SubPath(PosixPath):pass
    class Text(str):pass
    class Custom:
        def __fspath__(self):return str(tmp_path/'member')
    (tmp_path/'member').write_bytes(b'x')
    path={'subclass':SubPath(tmp_path/'member'),'custom_fspath':Custom(),'str':str(tmp_path/'member'),'str_subclass':Text(str(tmp_path/'member')),'pure_windows':PureWindowsPath(r'C:\owned\member')}[kind]
    dirname=os.path.dirname;parents=[]
    def seen(value):parents.append(value);return dirname(value)
    with monkeypatch.context()as scope:
        scope.setattr(os.path,'dirname',seen)
        value,error,calls=compare(monkeypatch,path)
    assert error is None and parents==[c[0]for c in calls]*2
    assert type(value)is PosixPath


@pytest.mark.parametrize('number',[errno.ENOENT,errno.ENOTDIR,errno.EBADF,errno.ELOOP])
def test_original_selective_missing_errno_still_checks_every_later_ancestor(tmp_path,monkeypatch,number):
    leaf=tmp_path/'member';leaf.write_bytes(b'x');failure=OSError(number,'controlled missing')
    _,error,calls=compare(monkeypatch,leaf,failure,1)
    assert error is None and len(calls)==1+len(leaf.parents)


@pytest.mark.parametrize('number',[21,123,1921])
def test_original_selective_winerror_keeps_native_posix_probes_without_windows_runtime_claim(tmp_path,monkeypatch,number):
    leaf=tmp_path/'member';leaf.write_bytes(b'x');failure=OSError();failure.winerror=number
    _,error,calls=compare(monkeypatch,leaf,failure,1)
    assert error is None and len(calls)==1+len(leaf.parents)


@pytest.mark.parametrize('factory',[lambda:OSError(errno.EACCES,'controlled permission'),lambda:OSError(errno.EIO,'controlled IO'),lambda:RuntimeError('controlled ordinary'),lambda:KeyboardInterrupt('controlled KI'),lambda:SystemExit('controlled SE')])
@pytest.mark.parametrize('ordinal',[1,2])
def test_original_error_object_and_probe_cutoff_precede_all_later_native_parents(tmp_path,monkeypatch,factory,ordinal):
    leaf=tmp_path/'member';leaf.write_bytes(b'x');failure=factory()
    _,error,calls=compare(monkeypatch,leaf,failure,ordinal)
    assert error is failure and len(calls)==ordinal


def test_original_valueerror_filter_keeps_all_fresh_guards(tmp_path,monkeypatch):
    leaf=tmp_path/'member';leaf.write_bytes(b'x')
    _,error,calls=compare(monkeypatch,leaf,ValueError('controlled unusable'),1)
    assert error is None and len(calls)==1+len(leaf.parents)


@pytest.mark.parametrize('where',['leaf','ancestor','broken_leaf','two_links'])
def test_first_link_refusal_and_exact_probe_cutoff_are_not_changed(tmp_path,monkeypatch,where):
    real=tmp_path/'real';real.mkdir();(real/'member').write_bytes(b'x')
    if where=='leaf':path=tmp_path/'member';path.symlink_to(real/'member')
    elif where=='ancestor':alias=tmp_path/'alias';alias.symlink_to(real,target_is_directory=True);path=alias/'member'
    elif where=='broken_leaf':path=tmp_path/'member';path.symlink_to(tmp_path/'missing')
    else:
        alias=tmp_path/'alias';alias.symlink_to(real,target_is_directory=True);(real/'alias-member').symlink_to(real/'member');path=alias/'alias-member'
    _,error,calls=compare(monkeypatch,path)
    assert isinstance(error,UpdateError)and str(error)=='Update storage cannot follow links'
    assert calls and calls[0][0]==os.fspath(path)


def test_native_parent_swap_is_read_again_on_each_invocation(tmp_path,monkeypatch):
    real=tmp_path/'real';real.mkdir();(real/'member').write_bytes(b'x');named=tmp_path/'named';named.mkdir();(named/'member').write_bytes(b'x');path=named/'member'
    for function in(original_unlinked,update._unlinked):
        value,error,first=observe(monkeypatch,function,path);assert error is None and value==path
        (named/'member').unlink();named.rmdir();named.symlink_to(real,target_is_directory=True)
        value,error,linked=observe(monkeypatch,function,path);assert isinstance(error,UpdateError)
        assert linked==first[:2]
        named.unlink();named.mkdir();(named/'member').write_bytes(b'x')
        value,error,restored=observe(monkeypatch,function,path);assert error is None and restored==first


@pytest.mark.parametrize('raw',['/unusual//member','///unusual/member','/unusual/member/','relative/member'])
def test_noncanonical_custom_fspath_observation_keeps_general_parent_fallback(tmp_path,monkeypatch,raw):
    path=tmp_path/'member';path.write_bytes(b'x');original_fspath=os.fspath;dirname=os.path.dirname;parents=[]
    def changed(value):return raw if type(value)is PosixPath else original_fspath(value)
    def seen(value):parents.append(value);return dirname(value)
    with monkeypatch.context()as scope:
        scope.setattr(os,'fspath',changed);scope.setattr(os.path,'dirname',seen)
        _,error,calls=compare(monkeypatch,path)
    assert error is None and parents==[c[0]for c in calls]*2


@pytest.mark.parametrize('path',[None,17])
def test_original_constructor_refusal_happens_before_every_stat_probe(monkeypatch,path):
    _,error,calls=compare(monkeypatch,path)
    assert isinstance(error,TypeError)and calls==[]
