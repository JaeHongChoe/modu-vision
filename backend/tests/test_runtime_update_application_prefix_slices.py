"""Root-only real namespace controls; Source author never executes this module."""
import sys
import pytest
from backend.engine import runtime_update as update
UpdateError=update.UpdateError

def original_namespaces(names):
    namespaces={}
    for name in names:
        parts=name.split('/')
        for count in range(1,len(parts)+1):
            prefix='/'.join(parts[:count]);fold=prefix.casefold();kind='file' if count==len(parts) else 'directory'
            prior=namespaces.get(fold)
            if prior and prior!=(prefix,kind):raise UpdateError('Portable file/directory/link namespace conflicts')
            namespaces[fold]=(prefix,kind)
    return namespaces


def capture(function,names):
    try:return function(names),None
    except BaseException as error:return None,error


def compare(names_factory):
    old,old_error=capture(original_namespaces,names_factory())
    new,new_error=capture(update._application_namespaces,names_factory())
    assert new==old
    if old_error is None:assert new_error is None
    else:assert type(new_error)is type(old_error)and str(new_error)==str(old_error)
    return new,new_error


PATHSETS=[
    [],[''],['/'],['//'],['a'],['a/b/c'],['a/','a/b'],['a','a/b'],
    ['a/b','a'],['A/b','a/c'],['SS/file','ß/next'],['한글/부품','한글/다음'],
    ['/a//b'],['a/\x00/b'],['./../member'],['root/'+('deep/'*12)+'member'],
]


@pytest.mark.parametrize('names',PATHSETS)
def test_exact_string_prefixes_preserve_original_mapping_and_first_conflict(names):
    compare(lambda:list(names))


def observe_join(function,names):
    previous=sys.getprofile();joins=[]
    def observer(frame,event,value):
        if event=='c_call' and getattr(value,'__name__',None)=='join' and getattr(value,'__self__',None)=='/':
            joins.append(frame.f_code.co_name)
        if previous is not None:previous(frame,event,value)
    sys.setprofile(observer)
    try:return function(names),joins
    finally:sys.setprofile(previous)


def test_exact_string_prefix_construction_avoids_repeated_join_and_preserves_every_namespace_entry():
    names=[f'owned/branch/member-{index:03}.bin'for index in range(64)]
    old,old_joins=observe_join(original_namespaces,names)
    new,new_joins=observe_join(update._application_namespaces,names)
    assert new==old
    assert len(old_joins)==192
    assert new_joins==[], 'Exact string prefixes must avoid repeated slice/join reconstruction; namespace checks still run'


@pytest.mark.parametrize('kind',['ordinary','tuple','custom_parts'])
def test_string_subclass_keeps_original_split_and_whole_join_fallback(kind):
    calls=[]
    class Text(str):
        def split(self,separator):
            calls.append((str(self),separator))
            if kind=='tuple':return tuple(super().split(separator))
            if kind=='custom_parts':return ['custom','parts','member']
            return super().split(separator)
    old,old_joins=observe_join(original_namespaces,[Text('owned/member')])
    new,new_joins=observe_join(update._application_namespaces,[Text('owned/member')])
    assert new==old and new_joins==['_application_namespaces']*len(old_joins)
    assert len(old_joins)==(3 if kind=='custom_parts'else 2)
    assert calls==[('owned/member','/'),('owned/member','/')]


def test_custom_name_split_object_retains_original_fallback_operations():
    calls=[]
    class Name:
        def split(self,separator):calls.append(separator);return ['owned','custom','member']
    old,old_joins=observe_join(original_namespaces,[Name()])
    new,new_joins=observe_join(update._application_namespaces,[Name()])
    assert new==old and len(old_joins)==len(new_joins)==3
    assert calls==['/','/']


@pytest.mark.parametrize('factory',[lambda:RuntimeError('iterator'),lambda:KeyboardInterrupt('iterator'),lambda:SystemExit('iterator')])
def test_later_iterator_exception_object_and_yield_cutoff_remain_original(factory):
    failure=factory();events=[]
    def names():
        events.append('first');yield 'owned/member'
        events.append('error');raise failure
    _,old_error=capture(original_namespaces,names())
    _,new_error=capture(update._application_namespaces,names())
    assert old_error is new_error is failure
    assert events==['first','error','first','error']


@pytest.mark.parametrize('factory',[lambda:RuntimeError('later'),lambda:KeyboardInterrupt('later'),lambda:SystemExit('later')])
def test_earlier_prefix_conflict_precedes_unrequested_later_iterator_exception(factory):
    failure=factory();events=[]
    def names():
        events.append('file');yield 'owned'
        events.append('conflict');yield 'owned/member'
        events.append('unrequested');raise failure
    old,old_error=capture(original_namespaces,names())
    new,new_error=capture(update._application_namespaces,names())
    assert old is new is None
    assert type(old_error)is type(new_error)is UpdateError
    assert str(old_error)==str(new_error)=='Portable file/directory/link namespace conflicts'
    assert events==['file','conflict','file','conflict']


@pytest.mark.parametrize('factory',[lambda:RuntimeError('split'),lambda:KeyboardInterrupt('split'),lambda:SystemExit('split')])
def test_custom_split_exception_identity_precedes_every_later_name(factory):
    failure=factory();events=[]
    class Text(str):
        def split(self,separator):events.append(separator);raise failure
    def names():yield Text('bad');events.append('unrequested');yield 'later'
    _,old_error=capture(original_namespaces,names())
    _,new_error=capture(update._application_namespaces,names())
    assert old_error is new_error is failure and events==['/','/']


def test_every_invocation_rebuilds_namespace_and_conflicts_from_current_inputs():
    names=['owned/member'];first=update._application_namespaces(names)
    names[:]=['owned'];second=update._application_namespaces(names)
    assert first==original_namespaces(['owned/member'])
    assert second==original_namespaces(['owned'])and first!=second
    names.append('owned/member')
    _,error=capture(update._application_namespaces,names)
    assert type(error)is UpdateError and str(error)=='Portable file/directory/link namespace conflicts'
