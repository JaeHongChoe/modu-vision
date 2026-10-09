"""Root-only exact lexical grammar controls; no product/test execution by author.

The oracle is the entire original a11 lexer except its name. Real stdlib regex
wrappers delegate once; no invented matches or filesystem authority are used.
"""
import re
import pytest
from backend.engine import runtime_update as update

UpdateError=update.UpdateError

def original_safe_path(value):
    if not isinstance(value,str) or not 0<len(value)<=240:raise UpdateError('Invalid portable application path')
    parts=value.split('/')
    if any(p in ('.','..') or not re.fullmatch(r'[A-Za-z0-9_.@][A-Za-z0-9_. +@^\-()]{0,159}',p) or p.endswith(('.', ' '))
            or re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',p,re.I) for p in parts):
        raise UpdateError('Unsafe portable application path')
    return value


def observed(monkeypatch,function,value):
    original=re._compile;calls=[];result=None;error=None
    def delegate(pattern,flags):
        calls.append((pattern,flags));return original(pattern,flags)
    with monkeypatch.context() as scoped:
        scoped.setattr(re,'_compile',delegate)
        try:result=function(value)
        except BaseException as caught:error=caught
    return result,error,calls


@pytest.mark.parametrize('name',[
    'one/two/three/member.py',
    'Owned Native.app/Contents/Frameworks/Electron Helper (GPU).app/Contents/Info.plist',
    '_internal/python3.13/lib-dynload/_hashlib.cpython-313-darwin.so'])
def test_real_fixed_grammar_result_precedes_zero_repeated_regex_cache_bookkeeping(monkeypatch,name):
    old,old_error,old_calls=observed(monkeypatch,original_safe_path,name)
    new,new_error,new_calls=observed(monkeypatch,update._safe_path,name)
    assert old_error is None and new_error is None
    assert old is name and new is name
    assert len(old_calls)==2*len(name.split('/'))
    assert new_calls==[], 'Exact native strings must not recreate regex cache keys per component'


@pytest.mark.parametrize('name',[
    '.hidden/name', '@native/name', '_private/file', 'a/a+b', 'a/name with space',
    'a/name^(GPU)', 'a/'+('x'*160), 'CONSOLE/member', 'a/com0', 'a/lpt10'])
def test_exact_ordinary_names_keep_original_grammar_and_return_identity(monkeypatch,name):
    old,old_error,_=observed(monkeypatch,original_safe_path,name)
    new,new_error,_=observed(monkeypatch,update._safe_path,name)
    assert old_error is None and new_error is None and old is name and new is name


@pytest.mark.parametrize('name',[
    '.', '..', '', '/absolute', 'a//b', 'a/./b', 'a/../b', 'a\\b', 'a:b',
    'a/b.', 'a/b ', 'a/CON', 'a/nul.txt', 'a/COM1', 'a/LPT9.data',
    'a/'+('x'*161), 'x'*241, '(leading)/file', 'a/(leading)',
    '/root(escape)', 'a/../Electron Helper (GPU)', 'a/./Electron Helper (GPU)',
    'a/Electron Helper (GPU)\\escape', 'a/Electron Helper (GPU):escape',
    'a/Electron Helper (GPU).', 'a/Electron Helper (GPU) ', 'a/한글',
    'a/cöm1', 'a/name\n', 'a/CON.TXT'])
def test_exact_unsafe_names_keep_original_first_refusal(monkeypatch,name):
    old,old_error,_=observed(monkeypatch,original_safe_path,name)
    new,new_error,_=observed(monkeypatch,update._safe_path,name)
    assert old is None and new is None
    assert type(new_error)is type(old_error)is UpdateError
    assert str(new_error)==str(old_error)


@pytest.mark.parametrize('value',[None,False,1,b'bytes'])
def test_nonstring_input_refuses_before_any_regex_bookkeeping(monkeypatch,value):
    old,old_error,old_calls=observed(monkeypatch,original_safe_path,value)
    new,new_error,new_calls=observed(monkeypatch,update._safe_path,value)
    assert old is None and new is None
    assert type(new_error)is type(old_error)is UpdateError
    assert str(new_error)==str(old_error)=='Invalid portable application path'
    assert old_calls==new_calls==[]


@pytest.mark.parametrize('name',['a/native','a/CON','a/name.','a/../b'])
def test_str_subclass_keeps_original_wrappers_and_return_or_first_refusal(monkeypatch,name):
    class Foreign(str):pass
    value=Foreign(name)
    old,old_error,old_calls=observed(monkeypatch,original_safe_path,value)
    new,new_error,new_calls=observed(monkeypatch,update._safe_path,value)
    assert new_calls==old_calls
    if old_error is None:assert new_error is None and old is value and new is value
    else:assert type(new_error)is type(old_error)and str(new_error)==str(old_error)


@pytest.mark.parametrize('error',[OSError('original split IO'),KeyboardInterrupt('original split KI'),SystemExit('original split SE')])
def test_foreign_split_preserves_exact_original_exception_before_regex(monkeypatch,error):
    class Foreign(str):
        def split(self,*args):raise error
    value=Foreign('a/native')
    old,old_error,old_calls=observed(monkeypatch,original_safe_path,value)
    new,new_error,new_calls=observed(monkeypatch,update._safe_path,value)
    assert old is None and new is None and old_error is error and new_error is error
    assert old_calls==new_calls==[]


@pytest.mark.parametrize('error',[OSError('original endswith IO'),KeyboardInterrupt('original endswith KI'),SystemExit('original endswith SE')])
def test_foreign_component_preserves_short_circuit_and_exact_exception(monkeypatch,error):
    class Component(str):
        def endswith(self,*args):raise error
    class Foreign(str):
        def split(self,*args):return [Component('native'),Component('CON')]
    value=Foreign('native/CON')
    old,old_error,old_calls=observed(monkeypatch,original_safe_path,value)
    new,new_error,new_calls=observed(monkeypatch,update._safe_path,value)
    assert old is None and new is None and old_error is error and new_error is error
    assert old_calls==new_calls and len(old_calls)==1
