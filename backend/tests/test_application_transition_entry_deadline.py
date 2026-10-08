"""Original admission entry retries only; no application, child or service.

Canonical lease flock, full original backend validator and retained local OFDs
run. Installation, OS identity, CPU math and Node channel authority are explicit
models from existing no-child publication/drain controls, never actual proof.
"""
import copy
import fcntl
import hashlib
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend.engine import application_launch_handshake as h
from backend.engine import application_launch_lease as lease
from backend.engine import application_launch_controller as controller
from backend.engine import application_owned_cpu_child_relay as cpu
from backend.engine import application_launch_execution as execution
from backend.engine import application_node_writer_authority as node
from backend.engine import runtime_update as update
from backend.tests.test_application_cpu_publication_admission import original
from backend.tests.test_application_drain_transition_busy import drain


def held_original_mutex(o):
    fd=os.open(o['lock'],os.O_RDWR);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    held=[True]
    def release():
        if held[0]:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd);held[0]=False
    return release


def release_on_original_sleep(o,monkeypatch,release,*,mutate=None):
    def sleep(seconds):
        assert 0<seconds<=.005
        assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
        o['sleeps'].append(seconds);o['clock'][0]+=seconds
        release()
        if mutate is not None:mutate()
    monkeypatch.setattr(h.time,'sleep',sleep)


def test_original_http_producer_waits_only_before_entry_and_retains_count(original,monkeypatch):
    o=original;release=held_original_mutex(o);o['checks'].clear()
    release_on_original_sleep(o,monkeypatch,release)
    bodies=[]
    try:
        with h.owned_cpu_writer_scope() as descriptors:
            bodies.append(True)
            assert len(descriptors)==1
            assert os.fstat(descriptors[0]).st_ino==os.fstat(o['cache']['writer_private_fd']).st_ino
            assert o['admission'].snapshot()=={'active_scopes':2,'unsupported':[]}
        assert bodies==[True] and o['sleeps']==[.005]
        assert o['checks'].count('executable')==2
        assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
    finally:release()


def receipt_state(o,monkeypatch):
    receipt={'controlled_original_result':True};raw=update._canonical(receipt);digest=hashlib.sha256(raw).hexdigest()
    path=o['root']/lease.LEASES/o['frame']['nonce']/'cpu-execution-receipt.json';path.write_bytes(raw)
    o['row']['cpu_execution']={'request_id':o['frame']['request_id'],'request_sha256':cpu._sha(o['frame']),'receipt_sha256':digest}
    seen=[];authority=object();node_state={'deadline':None}
    class Owner:
        root=o['root'];nonce=o['frame']['nonce']
        def _owned(self):seen.append('owned');return o['row']
    state={'owner':Owner(),'node':authority,'plan':{'cpu_request':o['frame'],'deadline_monotonic':104.0},
           'publication':{'receipt':receipt,'receipt_sha256':digest}}
    def fresh(value):
        assert value is authority;seen.append('original-node-fresh');return node_state,o['row']
    monkeypatch.setattr(node,'_state',lambda value:node_state if value is authority else pytest.fail('Foreign Node authority'))
    monkeypatch.setattr(node,'_fresh',fresh)
    monkeypatch.setattr(execution,'recheck_receipt_artifacts',lambda root,row,value:seen.append('full-artifact-read'))
    return state,seen,node_state


def test_original_receipt_read_waits_at_real_entry_under_original_deadline(original,monkeypatch):
    o=original;state,seen,_=receipt_state(o,monkeypatch);release=held_original_mutex(o)
    release_on_original_sleep(o,monkeypatch,release)
    try:
        assert cpu._receipt_current(state)==state['publication']
        assert o['sleeps']==[.005] and seen.count('full-artifact-read')==1
        assert seen.count('owned')==2 and o['admission'].snapshot()['active_scopes']==1
    finally:release()


def test_original_retained_drain_frame_entry_busy_does_not_reread_or_repeat_body(drain,monkeypatch):
    original_entry=lease._transition_admission;attempts=[]
    @contextmanager
    def entry(*args):
        attempts.append(args)
        if len(attempts)<=2:raise lease.LeaseTransitionBusy('Original modeled entry contention')
        with original_entry(*args):yield
    monkeypatch.setattr(lease,'_transition_admission',entry)
    original_frame=copy.deepcopy(drain.frame)
    result=controller.prepare_drain(drain.owner,drain.frame)
    assert len(attempts)==3 and drain.frame==original_frame
    assert result==10.05 and drain.clock.now<result
    assert sum(e[0]=='epoch_closed' for e in drain.events)==1
    assert [s['kind'] for s in drain.sent]==['backend_drain_request','managed_drain_admitted']
    assert sum(e[0]=='owned_checked' for e in drain.events)==1


def test_new_http_entry_cap_is_fixed_one_second_and_never_enters_body(original,monkeypatch):
    o=original;release=held_original_mutex(o);bodies=[]
    try:
        with pytest.raises(h.HandshakeError,match='entry deadline expired'):
            with h.owned_cpu_writer_scope():bodies.append(True)
        assert bodies==[] and 101.0<=o['clock'][0]<101.0051
        assert len(o['sleeps'])>1 and o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
    finally:release()


def test_generic_full_validator_keeps_original_nonblocking_entry(original):
    o=original;release=held_original_mutex(o)
    try:
        with pytest.raises(lease.LeaseTransitionBusy):
            h._validate(o['root'],o['values'],o['challenge'])
        assert o['sleeps']==[] and o['clock'][0]==100.0
        assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
    finally:release()


@pytest.mark.parametrize('kind',['cache','context','root','socket','socket-closed','proof','challenge','admission','anchor','writer-handle','writer-guard'])
def test_http_wait_rechecks_original_cache_channel_and_writer_custody(original,monkeypatch,kind):
    o=original;release=held_original_mutex(o);extra=[]
    def change():
        if kind=='cache':monkeypatch.setattr(h,'_CACHE',dict(o['cache']))
        elif kind=='context':monkeypatch.setattr(h,'_context',lambda:('foreign',))
        elif kind=='root':monkeypatch.setattr(h,'_root_context',lambda:(o['root']/'foreign',o['values']))
        elif kind=='socket':o['cache']['socket']=o['right']
        elif kind=='socket-closed':o['left'].close()
        elif kind=='proof':o['cache']['proof']=dict(o['proof'],nonce='f'*32)
        elif kind=='challenge':o['challenge']['nonce']='f'*32
        elif kind=='admission':o['cache']['admission']=h.BackendWorkAdmission()
        elif kind=='anchor':
            fd=os.open(o['root']/'original-writer.lock',os.O_RDWR);extra.append(fd)
            # Fresh OFD for same inode is not the original bootstrap anchor.
            assert os.fstat(fd).st_ino==os.fstat(o['cache']['writer_private_fd']).st_ino
            o['cache']['writer_private_fd']=fd
        elif kind=='writer-handle':o['cache']['writer_handle']=object()
        elif kind=='writer-guard':o['cache']['writer_guard']=object()
        else:raise AssertionError(kind)
    release_on_original_sleep(o,monkeypatch,release,mutate=change)
    bodies=[]
    try:
        with pytest.raises((h.HandshakeError,ValueError)):
            with h.owned_cpu_writer_scope():bodies.append(True)
        assert bodies==[] and o['sleeps']==[.005]
        assert o['admission'].snapshot()=={'active_scopes':1,'unsupported':[]}
    finally:
        release()
        for fd in extra:os.close(fd)


@pytest.mark.parametrize('kind',['wrong-birth','wrong-binding','wrong-registration','recovery-owner'])
def test_full_original_http_authentication_is_not_skipped_after_busy(original,monkeypatch,kind):
    o=original;release=held_original_mutex(o)
    def change():
        if kind=='wrong-birth':o['row']['process']=dict(o['row']['process'],created_at=999.0)
        elif kind=='wrong-binding':o['values']['VISION_APPLICATION_GENERATION']='foreign'
        elif kind=='wrong-registration':o['row']['writer_drain']['registration_sha256']='f'*64
        elif kind=='recovery-owner':o['row']['state']='recovery_required'
    release_on_original_sleep(o,monkeypatch,release,mutate=change)
    try:
        with pytest.raises(h.HandshakeError):
            with h.owned_cpu_writer_scope():pytest.fail('Changed original auth admitted')
        assert o['sleeps']==[.005] and o['admission'].snapshot()['active_scopes']==1
    finally:release()


@pytest.mark.parametrize('kind',['deadline','node-drain','node-channel','receipt-bytes','receipt-binding'])
def test_receipt_retry_preserves_original_bound_and_full_published_receipt(original,monkeypatch,kind):
    o=original;state,seen,nstate=receipt_state(o,monkeypatch);release=held_original_mutex(o)
    def change():
        if kind=='deadline':o['clock'][0]=104.0
        elif kind=='node-drain':nstate['deadline']=o['clock'][0]
        elif kind=='node-channel':monkeypatch.setattr(node,'_fresh',lambda authority:(_ for _ in ()).throw(h.HandshakeError('Original endpoint lost')))
        elif kind=='receipt-bytes':(o['root']/lease.LEASES/o['frame']['nonce']/'cpu-execution-receipt.json').write_text('{}')
        elif kind=='receipt-binding':o['row']['cpu_execution']['receipt_sha256']='f'*64
    release_on_original_sleep(o,monkeypatch,release,mutate=change)
    try:
        with pytest.raises(h.HandshakeError):cpu._receipt_current(state)
        assert o['sleeps']==[.005] and seen.count('full-artifact-read')==0
        assert o['admission'].snapshot()['active_scopes']==1
    finally:release()


@pytest.mark.parametrize('where',['entry','body','exit'])
@pytest.mark.parametrize('typed',[False,True])
def test_only_typed_enter_retries_never_body_or_exit(monkeypatch,where,typed):
    clock=[10.0];calls=[];bodies=[];sleeps=[]
    error=(lease.LeaseTransitionBusy('same original error') if typed else lease.LaunchLeaseError('same original error'))
    @contextmanager
    def entry(*args):
        calls.append('enter')
        if where=='entry' and len(calls)==1:raise error
        try:yield
        finally:
            calls.append('exit')
            if where=='exit':raise error
    monkeypatch.setattr(lease,'_transition_admission',entry)
    monkeypatch.setattr(h.time,'monotonic',lambda:clock[0])
    def sleep(seconds):sleeps.append(seconds);clock[0]+=seconds
    monkeypatch.setattr(h.time,'sleep',sleep)
    def action():
        with h._transition_admission_before_deadline('root','n'*32,10.05):
            bodies.append(True)
            if where=='body':raise error
    if where=='entry' and typed:
        action();assert calls==['enter','enter','exit'] and bodies==[True] and sleeps==[.005]
    else:
        with pytest.raises(type(error)) as captured:action()
        assert captured.value is error and sleeps==[]
        assert calls==(['enter'] if where=='entry' else ['enter','exit'])
        assert bodies==([] if where=='entry' else [True])


@pytest.mark.parametrize('when',['enter','body','exit'])
def test_entry_bound_cannot_be_renewed_by_slow_enter_body_or_exit(monkeypatch,when):
    clock=[10.0];bodies=[];calls=[]
    @contextmanager
    def entry(*args):
        calls.append('enter')
        if when=='enter':clock[0]=10.05
        try:yield
        finally:
            calls.append('exit')
            if when=='exit':clock[0]=10.05
    monkeypatch.setattr(lease,'_transition_admission',entry)
    monkeypatch.setattr(h.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(h.time,'sleep',lambda *a:pytest.fail('No failed entry to retry'))
    with pytest.raises(h.HandshakeError,match='entry deadline expired'):
        with h._transition_admission_before_deadline('root','n'*32,10.05):
            bodies.append(True)
            if when=='body':clock[0]=10.05
    assert bodies==([] if when=='enter' else [True]) and calls==['enter','exit']


def test_original_generator_exit_is_forwarded_once_without_normal_exit_validation(monkeypatch):
    calls=[];original=GeneratorExit('Original generator closing')
    @contextmanager
    def entry(*args):
        calls.append('enter')
        try:yield
        except BaseException as error:
            assert error is original;calls.append('original-generator-exit');raise
        else:pytest.fail('GeneratorExit was changed into a normal original exit')
        finally:calls.append('exit')
    monkeypatch.setattr(lease,'_transition_admission',entry)
    monkeypatch.setattr(h.time,'monotonic',lambda:10.0)
    monkeypatch.setattr(h.time,'sleep',lambda *a:pytest.fail('Cannot retry closing original body'))
    with pytest.raises(GeneratorExit) as captured:
        with h._transition_admission_before_deadline('root','n'*32,10.05):raise original
    assert captured.value is original and calls==['enter','original-generator-exit','exit']


def test_retained_drain_frame_wait_cannot_extend_original_budget_or_dispatch(drain,monkeypatch):
    calls=[];frame=copy.deepcopy(drain.frame)
    @contextmanager
    def entry(*args):
        calls.append(args);raise lease.LeaseTransitionBusy('Original retained entry busy')
        yield  # Original context-manager shape; no caller body can be entered.
    monkeypatch.setattr(lease,'_transition_admission',entry)
    with pytest.raises(h.HandshakeError,match='entry deadline expired'):
        controller.prepare_drain(drain.owner,frame)
    assert drain.clock.now==10.05 and frame==drain.frame
    assert calls and drain.events==[] and drain.sent==[]


@pytest.mark.parametrize('budget',[None,True,0,-1,4001,1.0,'4'])
def test_malformed_drain_budget_cannot_schedule_entry_retry(drain,monkeypatch,budget):
    def entry(*args):pytest.fail('Malformed budget attempted admission')
    monkeypatch.setattr(lease,'_transition_admission',entry)
    frame=dict(drain.frame,budget_ms=budget)
    with pytest.raises(h.HandshakeError,match='request differs or replayed'):controller.prepare_drain(drain.owner,frame)
    assert drain.events==[] and drain.sent==[] and drain.clock.now==10.0


@pytest.mark.parametrize('damage',['nonce','request_id','writer-phase'])
def test_retained_damaged_drain_frame_preserves_original_exact_refusal_after_entry(drain,monkeypatch,damage):
    original_entry=lease._transition_admission;calls=[]
    @contextmanager
    def entry(*args):
        calls.append(True)
        if len(calls)==1:raise lease.LeaseTransitionBusy('Original modeled entry contention')
        with original_entry(*args):yield
    monkeypatch.setattr(lease,'_transition_admission',entry)
    frame=dict(drain.frame)
    if damage=='nonce':frame['nonce']='f'*32
    elif damage=='request_id':frame['request_id']='malformed'
    else:drain.owner._owned()['writer_drain']['phase']='closing';drain.events.clear()
    with pytest.raises(h.HandshakeError,match='Original main managed drain request differs or replayed'):
        controller.prepare_drain(drain.owner,frame)
    assert len(calls)==2 and drain.sent==[] and not any(r[0]=='epoch_closed' for r in drain.events)
