"""Real signed files, update guards and held lease flock; no child/application.

OS parent/backend cmdline identities are explicitly modeled. No portable
publisher, compiled app, model math or runtime acceptance is asserted.
"""
import copy
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest
import psutil
from backend.engine import application_launch_handshake as h
from backend.engine import application_launch_lease as lease
from backend.engine import runtime_update as update
from backend.tests.test_application_launch_handshake import installed_backend
from backend.tests.test_service_s6_04 import controlled_preactivation_guard


def _allow_original_intent_validator_workers(monkeypatch):
    """Test-only allowance for the exact current invocation's file validators.

    Names never authorize a thread. The original stdlib worker, executor weakref,
    work queue, constructor caller and submitter code must all be the originals.
    Original Popen denial remains in each fixture. No application thread is
    admitted by this source fixture compatibility boundary.
    """
    import concurrent.futures.thread as pool_module
    import types
    import weakref
    executor_type=pool_module.ThreadPoolExecutor
    worker=pool_module._worker
    adjustment=executor_type._adjust_thread_count.__code__
    thread_type=threading.Thread
    original_start=thread_type.start
    original_validation=update._validated_intent
    validation_code=original_validation.__code__
    submission_code=update._intent_parallel_checks.__code__
    original_run=update._IntentCheckWork.run
    contexts=[];observations=[]

    def validation(*args,**kwargs):
        context={'pools':[],'works':[],'futures':[],'threads':{}}
        contexts.append(context)
        try:
            return original_validation(*args,**kwargs)
        finally:
            assert contexts[-1] is context
            contexts.pop()
            for executor in context['pools']:
                assert type(executor) is executor_type and executor._shutdown
                assert all(not thread.is_alive() for thread in executor._threads)
                assert set(executor._threads)<=set(context['threads'])
            for work in context['works']:
                assert work.complete.is_set()
                assert work.started or (work.rejected and work.submission_error is not None)
            assert all(future.done() for future in context['futures'])
            observations.append(context)

    def make_executor(*args,**kwargs):
        caller=sys._getframe(1)
        assert contexts and caller.f_code is validation_code
        assert args==() and kwargs=={'max_workers':4}
        context=contexts[-1]
        executor=executor_type(*args,**kwargs)
        assert type(executor) is executor_type and executor._initializer is None
        context['pools'].append(executor)
        original_submit=executor.submit
        def submit(function,*arguments,**keywords):
            caller=sys._getframe(1)
            assert contexts and contexts[-1] is context and caller.f_code is submission_code
            assert type(function) is types.MethodType and function.__func__ is original_run
            assert type(function.__self__) is update._IntentCheckWork
            assert caller.f_locals['work'] is function.__self__
            assert arguments==() and keywords=={}
            context['works'].append(function.__self__)
            future=original_submit(function,*arguments,**keywords)
            context['futures'].append(future)
            return future
        executor.submit=submit
        return executor

    def start(thread):
        caller=sys._getframe(1)
        assert contexts and caller.f_code is adjustment
        context=contexts[-1];executor=caller.f_locals['self']
        assert any(executor is owned for owned in context['pools'])
        assert type(thread) is thread_type and caller.f_locals['t'] is thread and thread._target is worker
        arguments=thread._args
        assert len(arguments)==4 and type(arguments[0]) is weakref.ReferenceType
        assert arguments[0]() is executor and arguments[1] is executor._work_queue
        assert arguments[2] is executor._initializer is None and arguments[3]==executor._initargs==()
        assert thread not in context['threads']
        context['threads'][thread]=executor
        return original_start(thread)

    monkeypatch.setattr(update,'ThreadPoolExecutor',make_executor)
    monkeypatch.setattr(update,'_validated_intent',validation)
    monkeypatch.setattr(threading.Thread,'start',start)
    return observations


@pytest.fixture
def original(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess.Popen, '__init__', lambda *a, **k: pytest.fail('No child permitted'))
    _allow_original_intent_validator_workers(monkeypatch)
    root, record, backend = installed_backend(tmp_path)
    backend_identity = lease._identity(os.getpid())
    main_identity = copy.deepcopy(record['process'])
    monkeypatch.setattr(lease, '_identity', lambda pid: copy.deepcopy(backend_identity if pid == os.getpid() else main_identity))
    class OriginalProcess:
        def __init__(self, pid):
            assert pid == os.getpid()
        def cmdline(self):
            return [sys.executable, str(backend)]
    monkeypatch.setattr(psutil, 'Process', OriginalProcess)
    monkeypatch.setattr(sys, 'argv', [str(backend), '--project-dir', str(root/'projects'), '--shared-auth-dir', str(root/'auth')])
    frame={'schema_version':1,'kind':'backend_challenge','challenge':'a'*64,'epoch':'b'*32,'nonce':record['nonce'],
           'binding':copy.deepcopy(record['binding']),'main_process':main_identity,'backend_pid':os.getpid(),
           'backend_executable':str(backend),'backend_build_identity_sha256':None}
    values={'VISION_APPLICATION_LAUNCH_NONCE':record['nonce'],
            'VISION_APPLICATION_GENERATION':record['binding']['application_generation'],
            'VISION_APPLICATION_DATABASE_GENERATION':record['binding']['database_generation_path']}
    full = update._validated_intent
    validations=[]
    def fresh(*args, **kwargs):
        value=full(*args, **kwargs)
        validations.append(value)
        return value
    monkeypatch.setattr(update, '_validated_intent', fresh)
    return root, record, backend, frame, values, validations


def test_original_authenticates_before_and_after_one_full_intent_pass_each(original):
    root, record, backend, frame, values, validations = original
    before = h._validate(root, values, frame)
    assert before['executable'] == str(backend)
    assert before['executable_sha256'] == update._sha(backend.read_bytes())
    assert len(validations) == 2, 'Each original owner pass must freshly validate exactly once; pre and post both execute'
    assert validations[0][2] is not validations[1][2], 'The after pass must obtain a new full validated manifest'


def test_original_launch_binding_callers_keep_independent_full_validation(original):
    root, record, backend, frame, values, validations = original
    binding = record['binding']
    first=update._launch_binding(root, binding['authority_path'], pinned_authority_sha256=binding['authority_sha256'])
    second=update._launch_binding(root, binding['authority_path'], pinned_authority_sha256=binding['authority_sha256'])
    assert first == second == binding
    assert len(validations) == 2


def test_one_original_transition_OFD_is_held_across_both_fresh_passes_and_body(original,monkeypatch):
    root,record,backend,frame,values,validations=original
    lock=root/lease.LEASES/frame['nonce']/lease.TRANSITION_LOCK
    original_validation=update._validated_intent
    attempts=[]
    def cannot_acquire(stage):
        descriptor=os.open(lock,os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            attempts.append(stage)
        finally:
            os.close(descriptor)
    def fresh(*args,**kwargs):
        cannot_acquire('fresh')
        return original_validation(*args,**kwargs)
    monkeypatch.setattr(update,'_validated_intent',fresh)
    with h._validated_backend_admission(root,values,frame):
        cannot_acquire('body')
    assert attempts==['fresh','body','fresh']
    assert len(validations)==2


def _tamper(original, kind):
    root, record, backend, frame, values, validations = original
    application=backend.parent.parent
    if kind in {'backend', 'app'}:
        file=backend if kind=='backend' else application/'bin/app'
        file.chmod(0o600); file.write_bytes(file.read_bytes()+b'changed')
    elif kind=='mode':backend.chmod(0o400)
    elif kind=='link':
        raw=backend.read_bytes(); foreign=root.parent/'foreign-backend'; foreign.write_bytes(raw)
        backend.unlink(); backend.symlink_to(foreign)
    elif kind=='extra':(application/'unreviewed').write_bytes(b'unreviewed')
    elif kind=='manifest':
        file=application/'portable-application.json';file.chmod(0o600)
        file.write_bytes(file.read_bytes()+b' ')
    elif kind=='authority':
        file=Path(record['binding']['authority_path']);file.write_bytes(file.read_bytes()+b' ')
    else:raise AssertionError(kind)


@pytest.mark.parametrize('kind', ['backend','app','mode','link','extra','manifest','authority'])
def test_original_full_file_guards_refuse_before_yield(original, kind):
    root, record, backend, frame, values, validations = original
    _tamper(original,kind)
    entered=[]
    with pytest.raises(ValueError):
        with h._validated_backend_admission(root,values,frame):entered.append(True)
    assert entered==[]


@pytest.mark.parametrize('kind', ['backend','app','mode','link','extra','manifest','authority'])
def test_after_owner_rehash_refuses_body_byte_membership_mode_or_link_drift(original,kind):
    root, record, backend, frame, values, validations = original
    proof=None
    with pytest.raises(ValueError):
        with h._validated_backend_admission(root, values, frame) as before:
            proof=before;assert len(validations)==1
            _tamper(original,kind)
    assert proof is not None
    # The next owner re-enters the complete fresh intent validation. No cached
    # manifest or previously successful binding authorizes the body mutation.
    assert len(validations)==1


@pytest.mark.parametrize('kind', ['nonce','main_birth','backend_pid','generation','protocol'])
def test_after_owner_rechecks_original_context_and_proof(original,kind):
    root, record, backend, frame, values, validations = original
    with pytest.raises(ValueError):
        with h._validated_backend_admission(root, values, frame):
            assert len(validations)==1
            if kind=='nonce':frame['nonce']='f'*32
            elif kind=='main_birth':frame['main_process']['created_at']+=1
            elif kind=='backend_pid':frame['backend_pid']+=1
            elif kind=='generation':values['VISION_APPLICATION_GENERATION']='foreign'
            elif kind=='protocol':
                raw=lease._load(root);raw['protocol_version']=4
                # Explicit foreign enrolled-protocol receipt without writer.
                update._write(root/lease.LEASES/frame['nonce']/'journal.json',raw)
                update._write(root/lease.ACTIVE_LEASE,lease._publication(raw))


def test_first_body_error_identity_survives_after_full_guard_refusal(original):
    root, record, backend, frame, values, validations=original
    class OriginalError(BaseException):pass
    error=OriginalError('first original body error')
    with pytest.raises(OriginalError) as caught:
        with h._validated_backend_admission(root,values,frame):
            _tamper(original,'backend');raise error
    assert caught.value is error
    assert len(validations)==1


def test_original_absolute_deadline_refuses_without_new_validation_or_body(original,monkeypatch):
    root, record, backend, frame, values, validations=original
    monkeypatch.setattr(h.time,'monotonic',lambda:100.0)
    entered=[]
    with pytest.raises(h.HandshakeError,match='deadline expired'):
        with h._validated_backend_admission(root,values,frame,absolute_deadline=100.0):entered.append(True)
    assert entered==[] and validations==[]


def test_expired_body_does_not_renew_original_admission_deadline(original,monkeypatch):
    root, record, backend, frame, values, validations=original
    now=[100.0];monkeypatch.setattr(h.time,'monotonic',lambda:now[0])
    with pytest.raises(h.HandshakeError,match='deadline expired'):
        with h._validated_backend_admission(root,values,frame,absolute_deadline=101.0):
            assert len(validations)==1;now[0]=101.0
    assert len(validations)==2


@pytest.mark.parametrize('key',['manifest','validated','inspector','cached_proof'])
def test_fixed_composition_does_not_accept_transferred_validation_inputs(original,key):
    root,record,backend,frame,values,validations=original;binding=record['binding']
    with pytest.raises(TypeError):
        update._backend_launch_binding(root,binding['authority_path'],frame,
            pinned_authority_sha256=binding['authority_sha256'],**{key:{'trusted':True}})
    assert validations==[]


def test_standalone_executable_still_validates_a_fresh_full_intent(original):
    root,record,backend,frame,values,validations=original
    result=h._executable(root,record['binding'],frame)
    assert result['executable']==str(backend) and len(validations)==1
    _tamper(original,'app')
    with pytest.raises(ValueError):h._executable(root,record['binding'],frame)
    assert len(validations)==1


def test_original_main_parent_refusal_precedes_invalid_executable(original):
    root,record,backend,frame,values,validations=original
    frame['main_process']['created_at']+=1
    frame['backend_executable']='/foreign/backend'
    with pytest.raises(h.HandshakeError,match='main process birth or parent identity differs'):
        h._validate(root,values,frame)
    assert len(validations)==1


def test_fresh_binding_refusal_precedes_invalid_executable(original,monkeypatch):
    root,record,backend,frame,values,validations=original
    original_validation=update._validated_intent
    def changed_fresh(*a,**k):
        # Model a contradictory fresh inspection result at the exact binding
        # comparison boundary; underlying full signed file guards still run.
        stored,directory,manifest=original_validation(*a,**k)
        stored=copy.deepcopy(stored);stored['release']['version']='foreign'
        return stored,directory,manifest
    monkeypatch.setattr(update,'_validated_intent',changed_fresh)
    frame['backend_executable']='/foreign/backend'
    with pytest.raises(h.HandshakeError,match='Committed backend launch pair changed'):
        h._validate(root,values,frame)
    assert len(validations)==1


def test_fixed_executable_body_is_ast_equivalent_to_original_standalone():
    import ast,inspect,textwrap
    old=ast.parse(textwrap.dedent(inspect.getsource(h._executable))).body[0].body[2:]
    pair=ast.parse(textwrap.dedent(inspect.getsource(update._launch_binding_pair))).body[0].body
    starts=[i for i,n in enumerate(pair) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='application' for t in n.targets)]
    new=pair[starts[-1]:]
    class Normalize(ast.NodeTransformer):
        def visit_Attribute(self,node):
            node=self.generic_visit(node)
            if isinstance(node.value,ast.Name) and node.value.id=='update':return ast.Name(id=node.attr,ctx=node.ctx)
            return node
        def visit_Name(self,node):
            if node.id=='backend_frame':node.id='frame'
            return node
        def visit_Import(self,node):
            node.names=[name for name in node.names if name.name!='sys']
            return node
        def visit_Return(self,node):
            node=self.generic_visit(node)
            if isinstance(node.value,ast.Tuple):node.value=node.value.elts[1]
            return node
    norm=lambda body:ast.dump(Normalize().visit(ast.Module(body=copy.deepcopy(body),type_ignores=[])),include_attributes=False)
    assert norm(old)==norm(new), 'Fixed compiled/source executable guards must not drift from original standalone checks'


def test_original_build_identity_hex_predicate_is_exactly_preserved():
    import ast,inspect,textwrap
    old=ast.parse(textwrap.dedent(inspect.getsource(h._hex))).body[0]
    current=ast.parse(textwrap.dedent(inspect.getsource(update._hex))).body[0]
    assert ast.dump(old,include_attributes=False)==ast.dump(current,include_attributes=False)
    for value in (None,True,False,0,[],{},'', '0'*63,'0'*64,'0'*65,'F'*64,'g'*64,'a'*64):
        assert h._hex(value)==update._hex(value)


@pytest.fixture
def frozen_original(tmp_path,monkeypatch):
    import zipfile
    from backend.tests.test_global_migration import owned
    from backend.tests.test_service_s6_04 import fixture,plan,canonical,sha
    monkeypatch.setattr(subprocess.Popen,'__init__',lambda *a,**k:pytest.fail('No child permitted'))
    _allow_original_intent_validator_workers(monkeypatch)
    root,*_=owned(tmp_path);value=fixture(tmp_path)
    payload=b'inert controlled binary bytes; never executable in this gate'
    inventory={'schema_version':1,'build_identity_sha256':'d'*64,'scope':'explicit inert compiled inventory model'}
    receipt={'schema_version':1,'executable':'vision_backend','executable_sha256':sha(payload),'inventory':inventory}
    files={'bin/app':b'inert app','bin/vision_backend':payload,'bin/backend-release.json':canonical(receipt),
           'bin/embedded/backend-build-inventory.json':canonical(inventory)}
    manifest={'schema_version':1,'version':'1.0.0','platform':value['target']['platform'],'arch':value['target']['arch'],
              'entrypoint':'bin/app','files':[{'path':n,'size':len(raw),'sha256':sha(raw),
                'executable':n in ('bin/app','bin/vision_backend')}for n,raw in files.items()]}
    archive=value['directory']/'application.zip'
    with zipfile.ZipFile(archive,'w') as out:
        out.writestr('portable-application.json',canonical(manifest))
        for n,raw in files.items():out.writestr(n,raw)
    value['payload'].update(sha256=sha(archive.read_bytes()),size=archive.stat().st_size)
    value['payload']['artifacts'][0].update(sha256=value['payload']['sha256'],size=value['payload']['size'])
    value['sign'](value['payload']);update.install_update(root,plan(root,value))
    supervisor=lease.LaunchSupervisor.reserve(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256'])
    file=root/lease.LEASES/supervisor.nonce/'journal.json';record=update._json(update._read(file))
    parent=lease._identity(os.getpid());identity=copy.deepcopy(parent)
    record.update(state='starting',spawn_attempted=True,process=parent,revision=2)
    update._write(file.parent/'spawn-intent.json',{'schema_version':1,'nonce':supervisor.nonce,'binding_sha256':sha(canonical(record['binding']))})
    update._write(file,record);update._write(root/lease.ACTIVE_LEASE,lease._publication(record))
    backend=Path(record['binding']['executable']).parent/'vision_backend'
    monkeypatch.setattr(lease,'_identity',lambda pid:copy.deepcopy(identity))
    command=[str(backend)]
    class Process:
        def __init__(self,pid):assert pid==os.getpid()
        def cmdline(self):return list(command)
    monkeypatch.setattr(psutil,'Process',Process)
    monkeypatch.setattr(sys,'argv',[str(backend),'--project-dir',str(root/'projects'),'--shared-auth-dir',str(root/'auth')])
    monkeypatch.setattr(sys,'frozen',True,raising=False);monkeypatch.setattr(sys,'executable',str(backend))
    monkeypatch.setattr(sys,'_MEIPASS',str(backend.parent/'embedded'),raising=False)
    frame={'schema_version':1,'kind':'backend_challenge','challenge':'a'*64,'epoch':'b'*32,'nonce':record['nonce'],
           'binding':copy.deepcopy(record['binding']),'main_process':parent,'backend_pid':os.getpid(),
           'backend_executable':str(backend),'backend_build_identity_sha256':inventory['build_identity_sha256']}
    values={'VISION_APPLICATION_LAUNCH_NONCE':record['nonce'],'VISION_APPLICATION_GENERATION':record['binding']['application_generation'],
            'VISION_APPLICATION_DATABASE_GENERATION':record['binding']['database_generation_path']}
    full=update._validated_intent;calls=[]
    def fresh(*a,**k):
        v=full(*a,**k);calls.append(v);return v
    monkeypatch.setattr(update,'_validated_intent',fresh)
    return root,record,backend,frame,values,calls,command


def test_fixed_frozen_body_preserves_exact_receipt_inventory_and_current_process(frozen_original):
    root,record,backend,frame,values,calls,command=frozen_original
    proof=h._validate(root,values,frame)
    assert proof['frozen'] is True and proof['build_identity_sha256']==frame['backend_build_identity_sha256']
    assert proof['executable_sha256']==update._sha(backend.read_bytes()) and len(calls)==2
    assert calls[0][2] is not calls[1][2]


@pytest.mark.parametrize('kind',['build','command','sys-executable','embedded','receipt'])
@pytest.mark.parametrize('when',['before','after'])
def test_frozen_guard_refuses_namespace_receipt_or_current_process_drift(frozen_original,monkeypatch,kind,when):
    root,record,backend,frame,values,calls,command=frozen_original
    def change():
        if kind=='build':frame['backend_build_identity_sha256']='e'*64
        elif kind=='command':command[0]='/foreign/backend'
        elif kind=='sys-executable':monkeypatch.setattr(sys,'executable','/foreign/backend')
        elif kind=='embedded':
            # Original embedded inventory namespace is a fresh runtime read,
            # never a reusable metadata proof; no installed file is altered.
            foreign=root.parent/'foreign-embedded';foreign.mkdir()
            update._write(foreign/'backend-build-inventory.json',{'build_identity_sha256':'e'*64})
            monkeypatch.setattr(sys,'_MEIPASS',str(foreign))
        elif kind=='receipt':
            file=backend.parent/'backend-release.json';file.chmod(0o600)
            raw=update._json(file.read_bytes());raw['executable_sha256']='f'*64;file.write_bytes(update._canonical(raw))
    if when=='before':change()
    entered=[]
    with pytest.raises(ValueError):
        with h._validated_backend_admission(root,values,frame):
            entered.append(True)
            if when=='after':change()
    assert entered==([True] if when=='after' else [])
