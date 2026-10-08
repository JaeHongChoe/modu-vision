"""Preflight ticket/transport controls; authentication modeling is explicit.

Original registry/OFD and temporary children are real. These controls do not
qualify complete writers, process-tree exit, accelerator or release acceptance.
"""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from backend.engine import application_launch_handshake as h, worker_preflight as p
from backend.tests.test_application_launch_quiescence import epoch
from backend.tests.test_application_cpu_writer_lifetime import controlled_cache, exclusive_available, FIND_LOCK, wait_file

pytestmark=pytest.mark.skipif(os.name!='posix',reason='Original POSIX writer transport')


def ticket(device='cpu'):
    factory=getattr(h,'create_preflight_writer_ticket',None)
    assert factory is not None,'A create-only preflight writer ticket is required'
    return factory(device=device)


@pytest.mark.parametrize('damage',['closed_epoch','closed_admission','partial','foreign'])
def test_preflight_refuses_before_mutable_run_preparation(epoch,monkeypatch,tmp_path,damage):
    with controlled_cache(epoch,monkeypatch) as (root,authority,state,cache):
        if damage=='closed_epoch':authority.close_epoch(expected_registry_sha256=authority.snapshot()['registry_sha256'])
        if damage=='closed_admission':state.close()
        if damage=='partial':cache['writer_private_fd']=None
        if damage=='foreign':monkeypatch.setattr(h,'_context',lambda:('foreign',))
        scratch=tmp_path/'premature-runs'
        monkeypatch.setattr(p,'sweep_stale_runs',lambda *_:pytest.fail('Closed writer reached sweep'))
        with pytest.raises(ValueError,match='closed|partial|unavailable'):
            p.run_preflight('classification','cpu',('train',),store=p.PreflightStore(tmp_path/'record.json'),workdir_root=scratch)
        assert not scratch.exists() and state.snapshot()['active_scopes']==0


def test_route_refuses_before_plan_reservation_and_state(epoch,monkeypatch):
    from backend.api import routes_workers as routes
    with controlled_cache(epoch,monkeypatch) as (_,_,state,_):
        state.close();monkeypatch.setattr(routes,'_STATE',{'running':None,'last':None})
        monkeypatch.setattr(p,'plan',lambda *_:pytest.fail('Closed request reached capability planning'))
        with pytest.raises(ValueError,match='closed'):
            routes.start_preflight(routes.PreflightRequest(task='classification'),SimpleNamespace(state=SimpleNamespace(account_user=None)))
        assert routes._STATE=={'running':None,'last':None}


def test_one_ticket_crosses_close_and_exact_thread_handoff_without_new_open(epoch,monkeypatch):
    with controlled_cache(epoch,monkeypatch) as (_,authority,state,cache):
        admitted=ticket();observed=[]
        def work():
            admitted.claim()
            try:
                with exposed_transport(admitted) as passed:observed.append((len(passed),state.snapshot()['active_scopes']))
            finally:admitted.finish()
        child=threading.Thread(target=work);admitted.dispatch_to(child)
        authority.close_epoch(expected_registry_sha256=authority.snapshot()['registry_sha256']);state.close()
        monkeypatch.setattr(h,'_validate',lambda *_ ,**__:pytest.fail('An admitted ticket reopened validation'))
        child.start();child.join(3)
        assert not child.is_alive() and observed==[(1,1)]
        assert state.snapshot()=={'active_scopes':0,'unsupported':['accepted_background_work']}
        with pytest.raises(ValueError,match='finished|used|inactive'):admitted.claim()


def test_ticket_reconstruction_reuse_and_foreign_thread_have_no_authority(epoch,monkeypatch):
    with controlled_cache(epoch,monkeypatch) as (_,_,state,_):
        admitted=ticket();admitted.claim()
        with pytest.raises(ValueError,match='used|active'):admitted.claim()
        fake=copy.copy(admitted)
        with pytest.raises(ValueError,match='original|ticket'):
            with exposed_transport(fake):pytest.fail('Copied ticket became authority')
        failures=[]
        def foreign():
            try:
                with exposed_transport(admitted):pytest.fail('Foreign thread became authority')
            except ValueError as e:failures.append(str(e))
        child=threading.Thread(target=foreign);child.start();child.join(3)
        assert len(failures)==1 and state.snapshot()['active_scopes']==1
        admitted.finish();assert state.snapshot()['active_scopes']==0


@pytest.mark.parametrize('device',['cpu','cuda','mps'])
def test_declared_dispatch_is_preserved_without_accelerator_execution(epoch,monkeypatch,device):
    with controlled_cache(epoch,monkeypatch) as (_,_,state,_):
        admitted=ticket(device);admitted.claim()
        try:
            with admitted.transport(device=device) as passed:assert len(passed)==1
        finally:admitted.finish()
        assert state.snapshot()=={'active_scopes':0,'unsupported':[]}


def test_cancel_before_claim_denies_late_work_and_releases_only_original_ticket(epoch,monkeypatch):
    with controlled_cache(epoch,monkeypatch) as (_,_,state,_):
        admitted=ticket();failures=[]
        def work():
            try:admitted.claim()
            except ValueError as e:failures.append(str(e))
        child=threading.Thread(target=work);admitted.dispatch_to(child)
        assert admitted.cancel_dispatch() is True
        assert state.snapshot()['active_scopes']==1
        child.start();child.join(3)
        admitted.finish()
        assert len(failures)==1 and state.snapshot()=={'active_scopes':0,'unsupported':['accepted_background_work','cpu_producer_unconfirmed']}


def test_start_after_claim_ambiguity_retains_worker_custody(epoch,monkeypatch):
    with controlled_cache(epoch,monkeypatch) as (_,_,state,_):
        admitted=ticket();claimed=threading.Event();release=threading.Event()
        def work():
            admitted.claim();claimed.set();release.wait(5);admitted.finish()
        child=threading.Thread(target=work);admitted.dispatch_to(child);child.start()
        try:
            assert claimed.wait(3) and admitted.cancel_dispatch() is False
            assert state.snapshot()['active_scopes']==1
        finally:release.set();child.join(3)
        assert not child.is_alive() and state.snapshot()=={'active_scopes':0,'unsupported':['accepted_background_work','cpu_producer_unconfirmed']}


@pytest.mark.parametrize('protocol',[2,3])
def test_exact_legacy_ticket_compatibility_is_counted_but_has_no_enrolled_fence(epoch,monkeypatch,protocol):
    with controlled_cache(epoch,monkeypatch) as (_,_,state,cache):
        cache['challenge']={}
        for name in ('writer_guard','writer_handle','writer_private_fd','writer_fd_identity'):cache[name]=None
        def valid(*_,validated):validated['protocol_version']=protocol;return cache['proof']
        monkeypatch.setattr(h,'_validate',valid)
        if protocol==2:
            with pytest.raises(ValueError,match='legacy|unsupported|partial'):ticket()
            assert state.snapshot()['active_scopes']==0
        else:
            admitted=ticket();admitted.claim()
            try:
                with exposed_transport(admitted) as passed:assert passed==() and state.snapshot()['active_scopes']==1
            finally:admitted.finish()
            assert state.snapshot()=={'active_scopes':0,'unsupported':[]}


@pytest.mark.parametrize('endpoint', ['missing', 'foreign'])
def test_unmodeled_owned_relay_refuses_incomplete_original_endpoint_before_spawn(epoch,monkeypatch,tmp_path,endpoint):
    """An incomplete owned cache must not silently receive legacy authority."""
    from backend.engine import application_preflight_child_relay as relay
    with controlled_cache(epoch,monkeypatch) as (_,authority,state,cache):
        if endpoint == 'foreign':cache['socket'] = object()
        admitted=ticket();admitted.claim();before=authority.snapshot()
        original_active=set(relay._ACTIVE);scratch=tmp_path/'unadmitted-child'
        monkeypatch.setattr(subprocess,'Popen',lambda *_,**__:pytest.fail('Incomplete original endpoint reached Popen'))
        try:
            with pytest.raises(KeyError if endpoint == 'missing' else h.HandshakeError,
                               match='socket' if endpoint == 'missing' else 'channel'):
                relay.reserve_backend_child(admitted,task='classification',device='cpu',stages=('train',),
                    workdir=scratch,limit=900,deadline=time.monotonic()+900)
            assert not scratch.exists() and authority.snapshot()==before
            assert not hasattr(admitted,'_relay_child') and set(relay._ACTIVE)==original_active
            assert state.snapshot()=={'active_scopes':1,'unsupported':[]}
        finally:admitted.finish()
        assert state.snapshot()=={'active_scopes':0,'unsupported':[]}


def test_nested_preflight_uses_same_ticket_and_inherits_actual_private_writer_dup(epoch,monkeypatch,tmp_path):
    with controlled_cache(epoch,monkeypatch) as (_,_,state,cache):
        from backend.engine import application_preflight_child_relay as relay
        # This existing controlled child intentionally substitutes its command.
        # Model only the new reservation boundary; no authenticated relay,
        # child enrollment or production stage math is claimed by this control.
        def transport_only(admitted, **plan):
            assert admitted._cache is cache and admitted._phase == 'active'
            assert plan['device'] == 'cpu' and plan['stages'] == ('train',)
            assert state.snapshot()['active_scopes'] == 1
            return None
        monkeypatch.setattr(relay, 'reserve_backend_child', transport_only)
        admitted=ticket();admitted.claim();seen=[];original=subprocess.Popen
        def spawn(command,**kwargs):
            assert state.snapshot()['active_scopes']==1
            passed=kwargs.get('pass_fds',());assert len(passed)==1
            assert passed[0]!=cache['writer_private_fd']
            info=os.fstat(passed[0]);assert (info.st_dev,info.st_ino)==cache['writer_fd_identity']
            # Actual temporary child exercises inheritance; stage math is separate.
            script=FIND_LOCK+"from pathlib import Path;Path(sys.argv[3]).write_text(json.dumps({'runtime_digest':'controlled','results':{'train':{'passed':True,'reason':'controlled transport','seconds':0}}}));print(json.dumps({'inherited_writer_refs':1}),flush=True)"
            seen.append(passed[0]);return original([sys.executable,'-I','-B','-c',script,str(info.st_dev),str(info.st_ino),str(Path(kwargs['cwd'])/p.RESULT_FILE)],**kwargs)
        monkeypatch.setattr(subprocess,'Popen',spawn)
        try:
            answer=p.run_preflight('classification','cpu',('train',),store=p.PreflightStore(tmp_path/'record.json'),workdir_root=tmp_path/'runs',_writer_ticket=admitted)
            assert answer['results']['train']['passed'] is True and len(seen)==1
            assert state.snapshot()['active_scopes']==1
        finally:admitted.finish()
        assert state.snapshot()=={'active_scopes':0,'unsupported':[]}


def test_route_retains_ticket_until_inflight_heartbeat_release_and_final_state(epoch,monkeypatch,tmp_path):
    from backend.api import routes_workers as routes,routes_training
    from backend.engine import shared_scheduler
    with controlled_cache(epoch,monkeypatch) as (root,authority,state,cache):
        q,_,owner,_=epoch
        writer=cache['challenge']['writer']['writer_id']
        lock=root/q.EPOCHS/owner.nonce/'writers'/writer/'ownership.lock'
        heartbeat_entered=threading.Event();heartbeat_release=threading.Event();body_release=threading.Event();done=threading.Event();trace=[]
        class Leases:
            lease_seconds=.03
            def acquire(self,*_,**__):trace.append(('acquire',state.snapshot()['active_scopes']));return True
            def heartbeat(self,*_):heartbeat_entered.set();heartbeat_release.wait(5);trace.append(('heartbeat_done',state.snapshot()['active_scopes']));return True
            def release(self,*_):
                info=os.fstat(cache['writer_private_fd'])
                assert (info.st_dev,info.st_ino)==cache['writer_fd_identity']
                assert exclusive_available(lock) is False
                trace.append(('release',state.snapshot()['active_scopes']));return True
        monkeypatch.setattr(shared_scheduler,'shared_leases',Leases)
        monkeypatch.setattr(routes_training,'training_job_manager',SimpleNamespace(local_queue_waiting=lambda:False))
        monkeypatch.setattr(routes,'_STATE',{'running':None,'last':None});monkeypatch.setattr(routes,'_WORKER',{'thread':None})
        monkeypatch.setattr(p,'plan',lambda *args:('train',))
        def body(*_,**kwargs):
            with exposed_transport(kwargs['_writer_ticket']) as passed:assert passed
            assert state.snapshot()['active_scopes']==1
            body_release.wait(5);done.set();return {'results':{},'runtime_digest':'controlled'}
        monkeypatch.setattr(p,'run_preflight',body)
        request=SimpleNamespace(state=SimpleNamespace(account_user=None))
        result=routes.start_preflight(routes.PreflightRequest(task='classification'),request)
        try:
            assert result['status']=='started' and heartbeat_entered.wait(3)
            authority.close_epoch(expected_registry_sha256=authority.snapshot()['registry_sha256']);state.close()
            body_release.set();assert done.wait(3)
            assert state.snapshot()['active_scopes']==1 and routes._STATE['last'] is None
        finally:
            body_release.set();heartbeat_release.set();routes._WORKER['thread'].join(5)
        assert not routes._WORKER['thread'].is_alive()
        assert trace==[(name,1) for name in ('acquire','heartbeat_done','release')]
        assert routes._STATE['running'] is None and routes._STATE['last']['error'] is None
        assert state.snapshot()=={'active_scopes':0,'unsupported':['accepted_background_work']}


@contextmanager
def exposed_transport(admitted):
    value=admitted.transport(device='cpu')
    if hasattr(value,'__enter__'):
        with value as result:yield result
    else:yield value  # Baseline exposed-number reproduction, not authority.


def test_exposed_transport_rebind_cannot_change_private_ticket_original_ofd(epoch,monkeypatch,tmp_path):
    with controlled_cache(epoch,monkeypatch) as (_,_,state,cache):
        admitted=ticket();admitted.claim();foreign=tmp_path/'foreign-empty';foreign.touch(mode=0o600)
        try:
            with exposed_transport(admitted) as passed:
                fd=passed[0];info=os.fstat(fd)
                replacement=os.open(foreign,os.O_RDWR)
                try:os.dup2(replacement,fd)
                finally:os.close(replacement)
                try:
                    with exposed_transport(admitted) as fresh:
                        now=os.fstat(fresh[0]);assert (now.st_dev,now.st_ino)==cache['writer_fd_identity']
                finally:os.dup2(cache['writer_private_fd'],fd)  # Controlled exposed test transport only.
        finally:admitted.finish()
        assert foreign.read_bytes()==b'' and state.snapshot()['active_scopes']==0


@pytest.mark.parametrize('when',['before_claim','after_claim'])
def test_route_failed_start_keeps_custody_until_original_cleanup(epoch,monkeypatch,when):
    from backend.api import routes_workers as routes,routes_training
    from backend.engine import shared_scheduler
    with controlled_cache(epoch,monkeypatch) as (_,_,state,_):
        held=[False];claimed=threading.Event();release=threading.Event();observed=[]
        class Leases:
            lease_seconds=30
            def acquire(self,*_,**__):held[0]=True;return True
            def heartbeat(self,*_):return True
            def release(self,*_):observed.append(state.snapshot()['active_scopes']);held[0]=False;return True
        monkeypatch.setattr(shared_scheduler,'shared_leases',Leases)
        monkeypatch.setattr(routes_training,'training_job_manager',SimpleNamespace(local_queue_waiting=lambda:False))
        monkeypatch.setattr(routes,'_STATE',{'running':None,'last':None});monkeypatch.setattr(routes,'_WORKER',{'thread':None})
        monkeypatch.setattr(p,'plan',lambda *args:('train',))
        def body(*_,**kwargs):claimed.set();release.wait(5);return {'results':{},'runtime_digest':'controlled'}
        monkeypatch.setattr(p,'run_preflight',body)
        original=threading.Thread.start
        def start(thread):
            if thread.name=='worker-preflight-classification':
                if when=='after_claim':original(thread);assert claimed.wait(3)
                raise RuntimeError('controlled start boundary')
            return original(thread)
        monkeypatch.setattr(threading.Thread,'start',start)
        try:
            with pytest.raises(RuntimeError,match='controlled start'):
                routes.start_preflight(routes.PreflightRequest(task='classification'),SimpleNamespace(state=SimpleNamespace(account_user=None)))
            if when=='after_claim':assert held[0] and state.snapshot()['active_scopes']==1
        finally:
            release.set();thread=routes._WORKER.get('thread')
            if thread is not None and thread.ident is not None:thread.join(5)
        assert not held[0] and routes._STATE['running'] is None
        assert observed==[1] and state.snapshot()=={'active_scopes':0,'unsupported':['accepted_background_work','cpu_producer_unconfirmed']}


def test_failed_mint_guard_exit_cannot_publish_or_lose_a_counted_ticket(epoch,monkeypatch):
    from backend.engine import application_launch_quiescence as q
    with controlled_cache(epoch,monkeypatch) as (_,_,state,_):
        original=q.writer_guard;before=set(h._PREFLIGHT_TICKETS)
        @contextmanager
        def changed(*args,**kwargs):
            with original(*args,**kwargs) as guard:yield guard
            raise ValueError('controlled final guard identity refusal')
        monkeypatch.setattr(q,'writer_guard',changed)
        try:
            with pytest.raises(ValueError,match='final guard'):ticket()
            assert state.snapshot()=={'active_scopes':0,'unsupported':['cpu_producer_unconfirmed']}
            assert set(h._PREFLIGHT_TICKETS)==before
        finally:
            # Only a never-returned controlled fixture capability; no process was started.
            for leaked in set(h._PREFLIGHT_TICKETS)-before:leaked.unconfirmed();leaked.finish()


def test_failed_thread_construction_cleans_reservation_under_original_ticket(epoch,monkeypatch):
    from backend.api import routes_workers as routes,routes_training
    from backend.engine import shared_scheduler
    with controlled_cache(epoch,monkeypatch) as (_,_,state,_):
        held=[False];observed=[]
        class Leases:
            lease_seconds=30
            def acquire(self,*_,**__):held[0]=True;return True
            def release(self,*_):observed.append(state.snapshot()['active_scopes']);held[0]=False;return True
        monkeypatch.setattr(shared_scheduler,'shared_leases',Leases)
        monkeypatch.setattr(routes_training,'training_job_manager',SimpleNamespace(local_queue_waiting=lambda:False))
        monkeypatch.setattr(routes,'_STATE',{'running':None,'last':None});monkeypatch.setattr(p,'plan',lambda *args:('train',))
        def construct(*_,**__):raise RuntimeError('controlled thread construction failure')
        monkeypatch.setattr(threading,'Thread',construct)
        with pytest.raises(RuntimeError,match='construction'):
            routes.start_preflight(routes.PreflightRequest(task='classification'),SimpleNamespace(state=SimpleNamespace(account_user=None)))
        assert not held[0] and observed==[1] and routes._STATE['running'] is None
        assert state.snapshot()=={'active_scopes':0,'unsupported':['cpu_producer_unconfirmed']}


def test_ticket_transport_dup_failure_stays_sticky_before_admission_leaves(epoch,monkeypatch):
    with controlled_cache(epoch,monkeypatch) as (_,_,state,_):
        admitted=ticket();admitted.claim();original=os.dup
        def duplicate(fd):
            if fd==admitted._transport[0]:raise OSError('controlled transport duplicate failure')
            return original(fd)
        monkeypatch.setattr(os,'dup',duplicate)
        try:
            with pytest.raises(OSError,match='duplicate'):
                with exposed_transport(admitted):pytest.fail('Failed duplicate yielded a capability')
        finally:admitted.finish()
        assert state.snapshot()=={'active_scopes':0,'unsupported':['cpu_producer_unconfirmed']}


def test_ambiguous_heartbeat_join_retains_original_ticket_and_blocks_drain(epoch,monkeypatch):
    from backend.api import routes_workers as routes,routes_training
    from backend.engine import shared_scheduler
    with controlled_cache(epoch,monkeypatch) as (_,_,state,_):
        entered=threading.Event();release=threading.Event();body_done=threading.Event()
        class Leases:
            lease_seconds=.03
            def acquire(self,*_,**__):return True
            def heartbeat(self,*_):entered.set();release.wait(5);return True
            def release(self,*_):return True
        monkeypatch.setattr(shared_scheduler,'shared_leases',Leases)
        monkeypatch.setattr(routes_training,'training_job_manager',SimpleNamespace(local_queue_waiting=lambda:False))
        monkeypatch.setattr(routes,'_STATE',{'running':None,'last':None});monkeypatch.setattr(routes,'_WORKER',{'thread':None})
        monkeypatch.setattr(p,'plan',lambda *args:('train',))
        def body(*_,**kwargs):assert entered.wait(3);body_done.set();return {'results':{},'runtime_digest':'controlled'}
        monkeypatch.setattr(p,'run_preflight',body)
        original=threading.Thread.join;threads=[]
        def join(thread,*args,**kwargs):
            if thread.name.endswith('-heartbeat'):
                threads.append(thread);raise RuntimeError('controlled original join refusal')
            return original(thread,*args,**kwargs)
        monkeypatch.setattr(threading.Thread,'join',join)
        routes.start_preflight(routes.PreflightRequest(task='classification'),SimpleNamespace(state=SimpleNamespace(account_user=None)))
        try:
            assert body_done.wait(3);routes._WORKER['thread'].join(3)
            assert state.snapshot()['active_scopes']==1
            assert state.snapshot()['unsupported']==['accepted_background_work','cpu_producer_unconfirmed']
            assert routes._STATE['running'] is not None and routes._STATE['last'] is None
            state.close();assert state.drain(.01)['status']=='refused'
        finally:
            release.set()
            for child in threads:original(child,3)
        # The private original capability remains unresolved, never repaired by
        # observing this test thread's eventual exit. Pytest process exit closes
        # its own retained FD; no launch-lease release is authorized here.


def test_release_failure_retains_original_ticket_without_publishing_completion(epoch,monkeypatch):
    from backend.api import routes_workers as routes,routes_training
    from backend.engine import shared_scheduler
    with controlled_cache(epoch,monkeypatch) as (_,_,state,_):
        class Leases:
            lease_seconds=30
            def acquire(self,*_,**__):return True
            def heartbeat(self,*_):return True
            def release(self,*_):raise OSError('controlled original release uncertainty')
        monkeypatch.setattr(shared_scheduler,'shared_leases',Leases)
        monkeypatch.setattr(routes_training,'training_job_manager',SimpleNamespace(local_queue_waiting=lambda:False))
        monkeypatch.setattr(routes,'_STATE',{'running':None,'last':None});monkeypatch.setattr(routes,'_WORKER',{'thread':None})
        monkeypatch.setattr(p,'plan',lambda *args:('train',));monkeypatch.setattr(p,'run_preflight',lambda *_,**__:{'results':{}})
        before=set(h._PREFLIGHT_UNRESOLVED)
        routes.start_preflight(routes.PreflightRequest(task='classification'),SimpleNamespace(state=SimpleNamespace(account_user=None)))
        routes._WORKER['thread'].join(3)
        unresolved=set(h._PREFLIGHT_UNRESOLVED)-before;assert len(unresolved)==1
        retained=unresolved.pop();assert retained._phase=='unresolved'
        assert state.snapshot()=={'active_scopes':1,'unsupported':['accepted_background_work','cpu_producer_unconfirmed']}
        assert routes._STATE['running'] is not None and routes._STATE['last'] is None
        with pytest.raises(ValueError,match='inactive|thread'):retained.finish()
        state.close();assert state.drain(.01)['status']=='refused'


def test_cooperative_escaped_preflight_child_retains_original_kernel_fence(epoch,monkeypatch,tmp_path):
    """Transport only: controlled stage result, real original child/OFD/leaf."""
    ready,release,exited=(tmp_path/name for name in ('leaf-ready.json','release.trigger','leaf-exited.json'))
    leaf=FIND_LOCK+'''
from pathlib import Path
import time
ready,release,exited=map(Path,sys.argv[3:6]);ready.write_text(json.dumps({'pid':os.getpid(),'session':os.getsid(0),'device':device,'inode':inode}))
until=time.monotonic()+15
while not release.exists() and time.monotonic()<until:time.sleep(.01)
os.close(writer_fd);exited.write_text(json.dumps({'pid':os.getpid(),'cooperative_release':release.exists()}))
'''
    worker=FIND_LOCK+'''
import subprocess,time
from pathlib import Path
leaf,ready,release,exited,result=sys.argv[3:8]
child=subprocess.Popen([sys.executable,'-I','-B','-c',leaf,str(device),str(inode),ready,release,exited],pass_fds=(writer_fd,),start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
until=time.monotonic()+5
while not Path(ready).exists() and time.monotonic()<until:time.sleep(.01)
assert Path(ready).exists()
Path(result).write_text(json.dumps({'runtime_digest':'controlled','results':{'train':{'passed':True,'reason':'transport-only controlled result','seconds':0}}}))
'''
    q,root,owner,authority=epoch;original=subprocess.Popen;handles=[]
    try:
        with controlled_cache(epoch,monkeypatch) as (_,_,state,cache):
            from backend.engine import application_preflight_child_relay as relay
            # Only this transport-only escaped-leaf fixture models reservation.
            # Exact ticket, original Popen and real inherited OFD remain tested;
            # authenticated relay enrollment is separate actual-source proof.
            def transport_only(admitted, **plan):
                assert admitted._cache is cache and admitted._phase == 'active'
                assert plan['device'] == 'cpu' and plan['stages'] == ('train',)
                assert state.snapshot()['active_scopes'] == 1
                return None
            monkeypatch.setattr(relay, 'reserve_backend_child', transport_only)
            registration=cache['challenge']['writer'];lock=root/q.EPOCHS/owner.nonce/'writers'/registration['writer_id']/'ownership.lock'
            def spawn(command,**kwargs):
                info=os.fstat(kwargs['pass_fds'][0]);assert state.snapshot()['active_scopes']==1
                child=original([sys.executable,'-I','-B','-c',worker,str(info.st_dev),str(info.st_ino),leaf,str(ready),str(release),str(exited),str(Path(kwargs['cwd'])/p.RESULT_FILE)],**kwargs)
                handles.append(child);return child
            monkeypatch.setattr(subprocess,'Popen',spawn)
            answer=p.run_preflight('classification','cpu',('train',),store=p.PreflightStore(tmp_path/'record.json'),workdir_root=tmp_path/'runs')
            assert answer['results']['train']['passed'] and handles[0].returncode==0
            assert state.snapshot()['active_scopes']==0
        published=wait_file(ready);assert published['inode']==lock.stat().st_ino
        # All original simulated backend/parent guards and ticket transports
        # have closed. Only the cooperative escaped leaf's original OFD remains.
        assert exclusive_available(lock) is False
        assert authority.snapshot()['registry']['writers'][0]['status']=='reserved'
    finally:
        release.touch()  # Private cooperative protocol; no PID/session signal.
        for child in handles:
            if child.poll() is None:child.wait(timeout=10)
    final=wait_file(exited);assert final['cooperative_release'] is True
    assert exclusive_available(lock)
    (tmp_path/'escaped-preflight-proof.json').write_text(json.dumps({'original_returncode':handles[0].returncode,
        'descendant':published,'cooperative_exit':final,'exclusive_blocked_after_original_fixture_references_closed':True,
        'whole_writer_coverage':False,'process_tree_exit_verified':False,'lease_release':False,'actual_stage_math_verified':False}))


def test_actual_authenticated_source_preflight_cpu_preserves_guard_through_real_stages(tmp_path,monkeypatch):
    """Original source controller/backend descriptors plus real preflight CPU.

    Signed source-script trust is controlled. This is not packaged/native,
    publisher, full-writer/tree, accelerator, or human-quality acceptance.
    """
    import select
    import urllib.request
    import zipfile
    from backend.tests import test_application_launch_execution as fixtures
    from backend.tests.test_application_launch_controller import controller_arguments
    from backend.tests.test_application_launch_quiescence_bridge import managed_stack
    from backend.tests.test_staged_update_canary import controlled_proof
    from backend.tests.test_service_s6_04 import canonical,sha
    from backend.engine import application_launch_lease as lease,application_launch_quiescence as q
    controlled_proof(monkeypatch)
    original_fixture=fixtures.fixture
    instrumentation='''
 os.environ.update(CUDA_VISIBLE_DEVICES='',NVIDIA_VISIBLE_DEVICES='none',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
 from backend.api import routes_workers as rw
 from backend.engine import worker_preflight as wp
 import subprocess,psutil
 app.include_router(rw.router)
 @app.middleware('http')
 async def preserve_preflight_first_error(request,call_next):
  try:return await call_next(request)
  except BaseException:
   if request.url.path=='/api/workers/local/preflight':
    try:
     import traceback
     (projects/'preflight-first-backend-error.txt').write_text(traceback.format_exc())
    except BaseException:pass  # Diagnostic failure cannot replace original cause.
   raise
 original_spawn=subprocess.Popen
 def observed_spawn(command,*args,**kwargs):
  child=original_spawn(command,*args,**kwargs)
  if len(command)>2 and command[1:3]==['-m','backend.engine.worker_preflight']:
   passed=kwargs.get('pass_fds',());info=os.fstat(passed[0]);writer=h._CACHE['challenge']['writer']
   lock=root/'.application-writer-epochs'/os.environ['VISION_APPLICATION_LAUNCH_NONCE']/'writers'/writer['writer_id']/'ownership.lock'
   refs=[{'fd':entry.fd,'path':entry.path} for entry in psutil.Process(child.pid).open_files() if entry.path==str(lock)]
   (projects/'preflight-original-child.json').write_text(json.dumps({'pid':child.pid,'birth':psutil.Process(child.pid).create_time(),
    'command':command,'device':info.st_dev,'inode':info.st_ino,'inherited_refs':refs,'passed_count':len(passed),
    'all_passed_refs':[{'fd':fd,'device':os.fstat(fd).st_dev,'inode':os.fstat(fd).st_ino} for fd in passed],
    'active_scopes':state.snapshot()['active_scopes'],'CUDA_VISIBLE_DEVICES':kwargs['env'].get('CUDA_VISIBLE_DEVICES'),
    'NVIDIA_VISIBLE_DEVICES':kwargs['env'].get('NVIDIA_VISIBLE_DEVICES'),'close_fds':kwargs.get('close_fds')}))
  return child
 subprocess.Popen=observed_spawn
 original_record=wp.PreflightStore.record
 def observed_record(self,*args,**kwargs):
  value=original_record(self,*args,**kwargs)
  with (projects/'preflight-record-counts.jsonl').open('a') as out:out.write(json.dumps({'stage':args[3],'active_scopes':state.snapshot()['active_scopes']})+'\\n')
  return value
 wp.PreflightStore.record=observed_record
'''
    def fixture(folder):
        value=original_fixture(folder);sign=value['sign']
        def instrumented(payload):
            archive=value['directory']/'application.zip'
            with zipfile.ZipFile(archive) as reader:files={name:reader.read(name) for name in reader.namelist()}
            manifest=json.loads(files.pop('portable-application.json'));needle=b' app=FastAPI()\n'
            assert files['bin/backend_fixture.py'].count(needle)==1
            files['bin/backend_fixture.py']=files['bin/backend_fixture.py'].replace(needle,needle+instrumentation.encode())
            manifest['files']=[{'path':name,'size':len(raw),'sha256':sha(raw),'executable':name.startswith('bin/')} for name,raw in files.items()]
            with zipfile.ZipFile(archive,'w') as writer:
                writer.writestr('portable-application.json',canonical(manifest))
                for name,raw in files.items():writer.writestr(name,raw)
            payload.update(sha256=sha(archive.read_bytes()),size=archive.stat().st_size)
            payload['artifacts'][0].update(sha256=payload['sha256'],size=payload['size'])
            return sign(payload)
        value['sign']=instrumented;return value
    monkeypatch.setattr(fixtures,'fixture',fixture)
    values=managed_stack(tmp_path,monkeypatch);root,value,current,project,reviewed,pin=values
    protected={str(path):sha(path.read_bytes()) for path in (root/'application-active.json',root/'global-active.json',project/'project.json',project/reviewed['input_path'],project/reviewed['package_path']/'manifest.json')}
    # No known-image flags: this exact admitted epoch executes only preflight.
    child=subprocess.Popen([sys.executable,'-m','backend.engine.application_launch_controller',*controller_arguments(root,value,current)],
        cwd=Path(__file__).resolve().parents[2],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    assert select.select([child.stdout],[],[],10)[0],'Original controller acknowledgement missing'
    raw=child.stdout.readline(65537);assert raw,'Original controller refused source preflight fixture'
    ack=json.loads(raw);child.stdout.close();child.stderr.close();assert ack['status']=='starting'
    original_binding=None
    try:
        # 'starting' acknowledges the original main spawn, not ASGI readiness.
        # Observe cold source startup within the unchanged controller bootstrap
        # bound, rather than borrowing the post-ready five-second leaf helper.
        server=wait_file(root/'projects/managed-server.json',seconds=h.MAX_DEADLINE);base=f"http://127.0.0.1:{server['port']}"
        bootstrap=wait_file(root/'.application-launches'/ack['nonce']/'bootstrap-receipt.json')
        assert bootstrap['nonce']==ack['nonce'] and bootstrap['backend_process']['pid']==server['pid']
        original_binding=bootstrap['binding']
        request=urllib.request.Request(base+'/api/workers/local/preflight',data=json.dumps({'task':'classification','device':'cpu'}).encode(),headers={'Content-Type':'application/json'},method='POST')
        try:
            with urllib.request.urlopen(request,timeout=10) as response:assert response.status==202;started=json.load(response)
        except urllib.error.HTTPError as exc:
            failure=root/'projects/preflight-first-backend-error.txt'
            try: detail=failure.read_text() if failure.is_file() else 'Original backend first-error artifact is unavailable'
            except OSError as read_error: detail='Original backend first-error artifact could not be read: '+type(read_error).__name__
            try: body=exc.read().decode(errors='replace')
            except OSError as read_error: body='Original HTTP error body could not be read: '+type(read_error).__name__
            pytest.fail('Original preflight HTTP '+str(exc.code)+' '+body+'\n'+detail)
        until=time.monotonic()+90
        while time.monotonic()<until:
            with urllib.request.urlopen(base+'/api/workers',timeout=10) as response:status=json.load(response)
            if status['running_preflight'] is None:break
            time.sleep(.05)
        assert status['running_preflight'] is None,'Original preflight did not finish its existing production budget'
        last=status['last_preflight'];assert last['error'] is None,last
        assert set(last['results'])=={'train','evaluate','infer','export'}
        assert all(row['passed'] and row['evidence']['device']=='cpu' for row in last['results'].values()),last
        observation=wait_file(root/'projects/preflight-original-child.json')
        records=[json.loads(raw) for raw in (root/'projects/preflight-record-counts.jsonl').read_text().splitlines()]
        assert {row['stage'] for row in records}=={'train','evaluate','infer','export'} and all(row['active_scopes']>=1 for row in records)
        registry=q.inspect_epoch(root,ack['nonce']);writers=registry['registry']['writers']
        assert len(writers)==2 and [row['role'] for row in writers]==['backend','preflight']
        backend,preflight=writers
        assert backend['status']=='active' and backend['exit_code'] is None
        assert backend['process']==bootstrap['backend_process']
        assert backend['process']['pid']==server['pid']!=bootstrap['main_process']['pid']
        assert backend['lock_identity']=={'device':observation['device'],'inode':observation['inode']}
        assert preflight['status']=='direct_exited' and preflight['exit_code']==0
        assert preflight['process']=={'pid':observation['pid'],'created_at':observation['birth'],
            'command_sha256':sha(canonical(observation['command']))}
        assert preflight['process']['pid'] not in {server['pid'],bootstrap['main_process']['pid']}
        assert preflight['writer_id']!=backend['writer_id'] and preflight['lock_identity']!=backend['lock_identity']
        refs=observation['all_passed_refs'];assert observation['passed_count']==len(refs)==3
        assert [(ref['device'],ref['inode']) for ref in refs[:2]]==[
            (backend['lock_identity']['device'],backend['lock_identity']['inode']),
            (preflight['lock_identity']['device'],preflight['lock_identity']['inode'])]
        assert len({ref['fd'] for ref in refs})==3
        assert len(observation['inherited_refs'])==1 and observation['close_fds'] is True
        assert observation['active_scopes']>=1 and observation['CUDA_VISIBLE_DEVICES']=='' and observation['NVIDIA_VISIBLE_DEVICES']=='none'
        assert started['device']=='cpu' and status['workers'][0]['local_compute_busy'] is None
        assert protected=={name:sha(Path(name).read_bytes()) for name in protected}
        (root/'projects/actual-preflight-writer-proof.json').write_bytes(canonical({'scope':'controlled_signed_source_application_epoch',
            'controller':ack,'backend':server,'preflight':last,'original_child':observation,'record_lifetimes':records,
            'registry':registry,'protected':protected,'actual_preflight_cpu_stages_verified':True,
            'actual_preflight_enrollment_verified':True,'actual_preflight_original_popen_finalize_verified':True,
            'actual_native_application_verified':False,'whole_writer_coverage':False,'process_tree_exit_verified':False,
            'lease_release':False,'model_quality_approved':False,'real_publisher_verified':False,'gpu_execution_verified':False}))
    finally:
        # Only this original test-owned stack's private cooperative controls.
        # Keep the existing 5 / 7 / 5 second cleanup observation budgets. A
        # two-file publication interval refuses strict readers transiently;
        # retry that exact observation, never edit either record or reopen PID
        # authority. Every other read failure retains the original controller.
        if child.returncode is None:
            projects=root/'projects';(projects/'drain.trigger').touch()
            wait_file(projects/'ordinary-stop-requested.json',seconds=5)
            (projects/'exit.trigger').touch()
            wait_file(projects/'managed-stop-result.json',seconds=7)
            until=time.monotonic()+5
            while time.monotonic()<until:
                try: final=lease.inspect_launch(root)
                except lease.LeaseTransitionBusy:
                    time.sleep(.01);continue
                assert final['nonce']==ack['nonce']
                if original_binding is not None:assert final['binding']==original_binding
                assert final['binding']['installation_id']==ack['installation_id']
                assert final['binding']['update_id']==ack['update_id']
                assert final['binding']['database_pointer']['fence']==ack['database_fence']
                if final.get('exit_observation',{}):
                    assert final['exit_observation']['direct_child_pid']==final['process']['pid']
                    assert final['exit_observation']['process_tree_exit_verified'] is False
                    child.terminate();child.wait(timeout=5);break
                time.sleep(.05)
            else:pytest.fail('Original controller did not record retained main exit; retain this fixture')
    final=lease._load(root);assert final['state']=='recovery_required' and final['writer_drain']['phase']=='refused'
    assert final['writer_drain']['receipt']['whole_writer_coverage'] is False
    assert final['writer_drain']['receipt']['can_release_launch_lease'] is False
    with pytest.raises(ValueError):lease.assert_quiescent(root)


@contextmanager
def no_child_counted_ticket(tmp_path,monkeypatch):
    """Modeled admission with real private original OFD; no process or thread."""
    import fcntl
    monkeypatch.setattr(h,'_CACHE',None)
    monkeypatch.setattr(h,'_root_context',lambda:None)
    monkeypatch.setattr(h,'_context',lambda:())
    lock=tmp_path/'original-no-child.lock'
    anchor=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
    fcntl.flock(anchor,fcntl.LOCK_SH|fcntl.LOCK_NB)
    private=os.dup(anchor);state=h.BackendWorkAdmission();state._begin_producer()
    admitted=h._mint_preflight_ticket('cpu',(private,),state)
    original_close=os.close;created=[anchor,private]
    try:yield admitted,state,lock,created,original_close
    finally:
        # Exact original handles created solely by this no-child control. No
        # production ticket is finished, repaired, retried or removed from its
        # unresolved custody by this explicit fixture teardown.
        monkeypatch.setattr(os,'close',original_close)
        for fd in reversed(created):
            try:original_close(fd)
            except OSError:pass


def assert_permanent_no_child_refusal(admitted,state,lock,original_close):
    assert state.snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
    assert admitted._phase=='unresolved' and admitted in h._PREFLIGHT_UNRESOLVED
    import fcntl
    probe=os.open(lock,os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:
        # This newly created test-only probe may reuse the consumed target's
        # number. Its own saved close is not a retry of ticket cleanup.
        original_close(probe)
    for operation in (admitted.claim,admitted.finish,admitted.cancel_dispatch):
        with pytest.raises(h.HandshakeError):operation()
    with pytest.raises(h.HandshakeError):
        with admitted.transport(device='cpu'):pytest.fail('Unresolved ticket became reusable authority')
    state.close();assert state.drain(.01)=={'status':'refused','active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}


@pytest.mark.parametrize('closed_before_error',[False,True])
def test_original_ticket_close_ambiguity_permanently_retains_original_count_and_ofd(tmp_path,monkeypatch,closed_before_error):
    import errno
    with no_child_counted_ticket(tmp_path,monkeypatch) as (admitted,state,lock,created,original_close):
        admitted.claim();target=admitted._transport[0];attempts=[]
        def close(fd):
            if fd==target:
                attempts.append(fd)
                if closed_before_error:original_close(fd);created.remove(fd)
                raise OSError(errno.EINTR,'controlled original private close ambiguity')
            return original_close(fd)
        monkeypatch.setattr(os,'close',close)
        with pytest.raises(OSError,match='original private close'):admitted.finish()
        assert attempts==[target]
        assert_permanent_no_child_refusal(admitted,state,lock,original_close)
        assert attempts==[target],'Unresolved cleanup retried an unknown descriptor number'


@pytest.mark.parametrize('closed_before_error',[False,True])
def test_transport_close_ambiguity_preserves_inflight_count_and_original_anchor(tmp_path,monkeypatch,closed_before_error):
    import errno
    with no_child_counted_ticket(tmp_path,monkeypatch) as (admitted,state,lock,created,original_close):
        admitted.claim();attempts=[]
        with pytest.raises(OSError,match='duplicate private close'):
            with admitted.transport(device='cpu') as passed:
                target=passed[0];created.append(target)
                def close(fd):
                    if fd==target:
                        attempts.append(fd)
                        if closed_before_error:original_close(fd);created.remove(fd)
                        raise OSError(errno.EINTR,'controlled duplicate private close ambiguity')
                    return original_close(fd)
                monkeypatch.setattr(os,'close',close)
        assert admitted._using_transport==1
        assert_permanent_no_child_refusal(admitted,state,lock,original_close)
        assert attempts==[target]


def test_standalone_preflight_finally_cannot_finish_unknown_duplicate_cleanup(tmp_path,monkeypatch):
    import errno
    with no_child_counted_ticket(tmp_path,monkeypatch) as (admitted,state,lock,created,original_close):
        original_dup=os.dup;duplicates=[];attempts=[]
        monkeypatch.setattr(h,'create_preflight_writer_ticket',lambda **_:admitted)
        monkeypatch.setattr(p,'_run_preflight_admitted',lambda *_,**__:{'results':{},'controlled_no_math':True})
        def duplicate(fd):
            result=original_dup(fd)
            if fd==admitted._transport[0]:duplicates.append(result);created.append(result)
            return result
        def close(fd):
            if fd in duplicates:
                attempts.append(fd);raise OSError(errno.EINTR,'controlled standalone duplicate ambiguity')
            return original_close(fd)
        monkeypatch.setattr(os,'dup',duplicate);monkeypatch.setattr(os,'close',close)
        with pytest.raises(OSError,match='standalone duplicate'):
            p.run_preflight('classification','cpu',('train',))
        assert attempts==duplicates and len(duplicates)==1
        assert_permanent_no_child_refusal(admitted,state,lock,original_close)


def test_failed_mint_original_close_ambiguity_retains_unreturned_custody(epoch,monkeypatch):
    import errno
    from backend.engine import application_launch_quiescence as q
    with controlled_cache(epoch,monkeypatch) as (_,_,state,cache):
        original_guard=q.writer_guard;original_dup=os.dup;original_close=os.close
        captured=[];attempts=[];before=set(h._PREFLIGHT_UNRESOLVED)
        @contextmanager
        def guard(*args,**kwargs):
            with original_guard(*args,**kwargs) as value:yield value
            raise ValueError('controlled mint final validation failure')
        def duplicate(fd):
            result=original_dup(fd)
            if fd==cache['writer_private_fd']:captured.append(result)
            return result
        def close(fd):
            if fd in captured:attempts.append(fd);raise OSError(errno.EINTR,'controlled failed mint cleanup ambiguity')
            return original_close(fd)
        monkeypatch.setattr(q,'writer_guard',guard);monkeypatch.setattr(os,'dup',duplicate);monkeypatch.setattr(os,'close',close)
        try:
            with pytest.raises(OSError,match='failed mint cleanup'):ticket()
            assert len(captured)==1 and attempts==captured
            assert state.snapshot()=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
            unresolved=set(h._PREFLIGHT_UNRESOLVED)-before;assert len(unresolved)==1
            held=unresolved.pop();assert held._phase=='unresolved'
            with pytest.raises(h.HandshakeError):held.claim()
            with pytest.raises(h.HandshakeError):held.finish()
            assert attempts==captured
        finally:
            monkeypatch.setattr(os,'close',original_close)
            for fd in captured:original_close(fd)  # Exact no-child control handles only.


@pytest.mark.parametrize('closed_before_error',[False,True])
def test_inner_transport_close_ambiguity_retains_standalone_original_count(tmp_path,monkeypatch,closed_before_error):
    import errno
    with no_child_counted_ticket(tmp_path,monkeypatch) as (admitted,state,lock,created,original_close):
        original_dup=os.dup;outer=[];inner=[];attempts=[]
        monkeypatch.setattr(h,'create_preflight_writer_ticket',lambda **_:admitted)
        monkeypatch.setattr(p,'_run_preflight_admitted',lambda *_,**__:{'results':{},'controlled_no_math':True})
        def duplicate(fd):
            result=original_dup(fd)
            if fd==admitted._transport[0]:outer.append(result)
            elif fd in outer:inner.append(result);created.append(result)
            return result
        def close(fd):
            if fd in inner:
                attempts.append(fd)
                if closed_before_error:original_close(fd);created.remove(fd)
                raise OSError(errno.EINTR,'controlled inner helper close ambiguity')
            return original_close(fd)
        monkeypatch.setattr(os,'dup',duplicate);monkeypatch.setattr(os,'close',close)
        with pytest.raises(OSError,match='inner helper close'):
            p.run_preflight('classification','cpu',('train',))
        assert len(inner)==1 and attempts==inner
        assert_permanent_no_child_refusal(admitted,state,lock,original_close)


@pytest.mark.parametrize('failure',['release_false','original_close','ticket_transport','runtime_transport'])
def test_route_unknown_cleanup_never_releases_or_publishes_a_finished_state(tmp_path,monkeypatch,failure):
    import errno
    from backend.api import routes_workers as routes,routes_training
    from backend.engine import shared_scheduler
    with no_child_counted_ticket(tmp_path,monkeypatch) as (admitted,state,lock,created,original_close):
        releases=[];original_dup=os.dup;outer=[];inner=[];attempts=[]
        class Leases:
            lease_seconds=30
            def acquire(self,*_,**__):return True
            def heartbeat(self,*_):return True
            def release(self,*_):releases.append(state.snapshot()['active_scopes']);return failure!='release_false'
        monkeypatch.setattr(h,'create_preflight_writer_ticket',lambda **_:admitted)
        monkeypatch.setattr(shared_scheduler,'shared_leases',Leases)
        monkeypatch.setattr(routes_training,'training_job_manager',SimpleNamespace(local_queue_waiting=lambda:False))
        monkeypatch.setattr(routes,'_STATE',{'running':None,'last':None});monkeypatch.setattr(routes,'_WORKER',{'thread':None})
        monkeypatch.setattr(p,'plan',lambda *args:('train',));monkeypatch.setattr(p,'_run_preflight_admitted',lambda *_,**__:{'results':{}})
        def duplicate(fd):
            result=original_dup(fd)
            if fd==admitted._transport[0]:
                outer.append(result)
                if failure=='ticket_transport':created.append(result)
            elif fd in outer:
                inner.append(result)
                if failure=='runtime_transport':created.append(result)
            return result
        def close(fd):
            target=(failure=='original_close' and fd==admitted._transport[0]
                or failure=='ticket_transport' and fd in outer
                or failure=='runtime_transport' and fd in inner)
            if target:attempts.append(fd);raise OSError(errno.EINTR,'controlled route cleanup ambiguity')
            return original_close(fd)
        monkeypatch.setattr(os,'dup',duplicate);monkeypatch.setattr(os,'close',close)
        routes.start_preflight(routes.PreflightRequest(task='classification'),SimpleNamespace(state=SimpleNamespace(account_user=None)))
        routes._WORKER['thread'].join(3);assert not routes._WORKER['thread'].is_alive()
        assert releases==([1] if failure=='release_false' else [])
        assert routes._STATE['running'] is not None and routes._STATE['last'] is None
        assert admitted._phase=='unresolved' and admitted in h._PREFLIGHT_UNRESOLVED
        assert state.snapshot()=={'active_scopes':1,'unsupported':['accepted_background_work','cpu_producer_unconfirmed']}
        assert exclusive_available(lock) is False
        with pytest.raises(h.HandshakeError):admitted.finish()
        assert len(attempts)==(0 if failure=='release_false' else 1)



def test_inner_transport_enter_close_ambiguity_retains_original_count(tmp_path,monkeypatch):
    import errno
    with no_child_counted_ticket(tmp_path,monkeypatch) as (admitted,state,lock,created,original_close):
        original_dup=os.dup;original_fstat=os.fstat;outer=[];inner=[];attempts=[]
        monkeypatch.setattr(h,'create_preflight_writer_ticket',lambda **_:admitted)
        monkeypatch.setattr(p,'_run_preflight_admitted',lambda *_,**__:pytest.fail('Invalid transport entered preflight body'))
        def duplicate(fd):
            result=original_dup(fd)
            if fd==admitted._transport[0]:outer.append(result)
            elif fd in outer:inner.append(result);created.append(result)
            return result
        def fstat(fd):
            if fd in inner:raise OSError('controlled pre-yield validation failure')
            return original_fstat(fd)
        def close(fd):
            if fd in inner:attempts.append(fd);raise OSError(errno.EINTR,'controlled pre-yield duplicate close ambiguity')
            return original_close(fd)
        monkeypatch.setattr(os,'dup',duplicate);monkeypatch.setattr(os,'fstat',fstat);monkeypatch.setattr(os,'close',close)
        with pytest.raises(OSError,match='pre-yield duplicate close'):
            p.run_preflight('classification','cpu',('train',))
        assert attempts==inner and len(inner)==1
        assert_permanent_no_child_refusal(admitted,state,lock,original_close)



def test_uncounted_mint_close_ambiguity_retains_opaque_custody_without_leaving_admission(epoch,monkeypatch):
    import errno
    with controlled_cache(epoch,monkeypatch) as (root,_,state,cache):
        original_dup=os.dup;original_close=os.close;captured=[];attempts=[]
        before=set(h._PREFLIGHT_UNRESOLVED);state.close()
        monkeypatch.setattr(state,'leave',lambda:pytest.fail('Failed uncounted mint left admission'))
        def duplicate(fd):
            result=original_dup(fd)
            if fd==cache['writer_private_fd']:captured.append(result)
            return result
        def close(fd):
            if fd in captured:attempts.append(fd);raise OSError(errno.EINTR,'controlled uncounted mint close ambiguity')
            return original_close(fd)
        monkeypatch.setattr(os,'dup',duplicate);monkeypatch.setattr(os,'close',close)
        try:
            with pytest.raises(OSError,match='uncounted mint close'):ticket()
            assert attempts==captured and len(captured)==1
            assert state.snapshot()=={'active_scopes':0,'unsupported':['cpu_producer_unconfirmed']}
            unresolved=set(h._PREFLIGHT_UNRESOLVED)-before;assert len(unresolved)==1
            held=unresolved.pop();assert held._phase=='unresolved' and held._transport==tuple(captured)
            for operation in (held.claim,held.finish,held.cancel_dispatch):
                with pytest.raises(h.HandshakeError):operation()
            assert attempts==captured
            assert state.drain(.01)=={'status':'refused','active_scopes':0,'unsupported':['cpu_producer_unconfirmed']}
            q,_,owner,_=epoch
            lock=root/q.EPOCHS/owner.nonce/'writers'/cache['challenge']['writer']['writer_id']/'ownership.lock'
            assert exclusive_available(lock) is False
        finally:
            monkeypatch.setattr(os,'close',original_close)
            for fd in captured:original_close(fd)  # Exact test-created no-child descriptor only.
