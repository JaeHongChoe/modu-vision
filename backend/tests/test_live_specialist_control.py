"""A shared backend PID is not proof of an active specialist closure."""
import hashlib,json,os,sqlite3,threading,time,uuid
from pathlib import Path

import pytest

from backend.tests.test_global_migration import owned
from backend.engine.live_specialist_control import supported

pytestmark=pytest.mark.skipif(not supported(),reason='Native live closure proof requires POSIX kernel peer PID')


@pytest.fixture
def native_live(tmp_path,monkeypatch):
    from backend.engine.specialist_training_queue import NativeAdmission
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine.shared_scheduler import ResourceLeases
    root,scopes,store,registry,*_=owned(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root))
    directory=root/'projects'/'native';models=directory/'models';models.mkdir(parents=True)
    project={'id':'native','workspace_id':registry.workspace_id,'project_dir':str(directory),'models_dir':str(models)}
    (directory/'project.json').write_text(json.dumps(project))
    context=registry.context(project,None);identifier=uuid.uuid4().hex;output=models/'ocr'/identifier;output.mkdir(parents=True)
    spec={'task':'ocr','output_root':str(output.parent),'device':'cpu','epochs':1}
    ref=store.submit(context,registry.project_key(context),'specialist_training',spec,job_id=identifier,
        output_dir=str(output),registry_root=str(root/'projects'),project_dir=str(directory))
    leases=ResourceLeases(root/scopes['leases'],owner='original-native-owner',cooperative=True)
    scheduler=JobScheduler(store,leases,lease_seconds=300)
    scheduler.enqueue(identifier,ref.revision,resources={'host':'local-compute'})
    admission=NativeAdmission(store,scheduler,identifier,output,context.actor_id,threading.Event())
    entered=threading.Event();finish=threading.Event();errors=[];publications=[]
    def execute():
        try:
            with admission.scope():
                entered.set();assert finish.wait(15)
                admission.complete(lambda:publications.append(identifier))
        except BaseException as exc:errors.append(exc)
    thread=threading.Thread(target=execute,name='owned-native-lifecycle');thread.start()
    assert entered.wait(5),errors
    try:yield root,scopes,store,leases,admission,thread,finish,errors,publications
    finally:
        finish.set();thread.join(5)
        assert not thread.is_alive(),errors


def test_actual_native_thread_continues_once_without_new_execution_grant(native_live):
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.engine.job_store import JobStore
    from backend.engine.global_store_paths import resolve_store_path
    root,scopes,store,leases,a,thread,finish,errors,published=native_live
    plan=preview_live(root);assert plan['can_apply'],plan['blockers']
    worker=plan['live_adoptions']['workers'][0]
    assert worker['worker_kind']=='local_specialist' and worker['owner_pid']==os.getpid()
    assert plan['live_adoptions']['protocol_version']==3
    lease=a.lease
    leases.mark_uncertain_fenced(a.job_id,lease.fence)
    plan=preview_live(root);assert plan['can_apply'],plan['blockers']
    result=apply_live(root,expected_source_sha256=plan['source_sha256'])
    assert result['worker_count']==1 and result['workers_relaunched']==0 and result['uncertain_reservations_cleared']==0
    old=(root/scopes['ledger']).read_bytes()
    a.check();a.lease=a.scheduler.heartbeat(a.lease)
    assert leases.list()[0]['uncertain'] and leases.list()[0]['fence']==lease.fence
    with pytest.raises(ValueError,match='continuation|restart'):leases.acquire_for_job('new','unused','all')
    with pytest.raises(ValueError,match='continuation|restart'):leases.restamp_fence(a.job_id,999)
    assert thread.is_alive() and a.lease.fence==lease.fence
    finish.set();thread.join(5);assert not errors and published==[a.job_id]
    fresh=JobStore(root/scopes['ledger']);assert fresh.get(a.job_id).state=='completed'
    assert len(fresh.attempts(a.job_id))==1 and len([e for e in fresh.events(a.job_id) if e['event']=='complete'])==1
    assert not leases.list() and (root/scopes['ledger']).read_bytes()==old
    assert a.output.parent.parent==root/'projects/native/models'
    assert resolve_store_path(root/scopes['ledger'])!=root/scopes['ledger']


@pytest.mark.parametrize('damage',['missing_ack','ack_spec','ack_instance','ack_pid','ack_endpoint','wrong_fence','foreign_owner','foreign_actor','foreign_project','spec_changed','output_changed','no_attempt','linked_ack'])
def test_native_identity_damage_refuses_activation(native_live,damage,tmp_path):
    from backend.engine.live_control_migration import preview_live,apply_live
    root,scopes,store,leases,a,*_=native_live
    path=a.output/'native_control_ready.json'
    assert path.is_file(),'Current native workers need a positive original closure acknowledgment'
    ready=json.loads(path.read_bytes())
    if damage=='missing_ack':path.unlink()
    elif damage.startswith('ack_'):
        key={'ack_spec':'spec_sha256','ack_instance':'owner_instance','ack_pid':'owner_pid','ack_endpoint':'control_address'}[damage]
        ready[key]=999999999 if key=='owner_pid' else 'foreign';path.write_text(json.dumps(ready))
    elif damage=='linked_ack':
        outside=tmp_path/'linked-native-ack';outside.write_bytes(path.read_bytes());path.unlink();path.symlink_to(outside)
    elif damage=='wrong_fence':
        with sqlite3.connect(root/scopes['leases']) as db:db.execute('UPDATE leases SET fence=999')
    elif damage=='foreign_owner':
        with sqlite3.connect(root/scopes['leases']) as db:db.execute("UPDATE leases SET owner='other'")
    elif damage in {'foreign_actor','foreign_project','spec_changed','output_changed'}:
        key={'foreign_actor':'actor_id','foreign_project':'project_id','spec_changed':'spec_json','output_changed':'output_dir'}[damage]
        with sqlite3.connect(root/scopes['ledger']) as db:db.execute(f'UPDATE jobs SET {key}=?',('{}' if key=='spec_json' else 'other',))
    elif damage=='no_attempt':
        with sqlite3.connect(root/scopes['ledger']) as db:db.execute('DELETE FROM attempts')
    if damage=='linked_ack':
        with pytest.raises(ValueError,match='linked|unlinked'):preview_live(root)
        with pytest.raises(ValueError):apply_live(root,expected_source_sha256='0'*64)
        assert not (root/'global-active.json').exists()
        return
    plan=preview_live(root);assert not plan['can_apply'] and plan['blockers']
    with pytest.raises(ValueError):apply_live(root,expected_source_sha256=plan['source_sha256'])
    assert not (root/'global-active.json').exists()


def test_ended_closure_is_not_live_just_because_shared_backend_pid_remains(native_live):
    from backend.engine.live_specialist_control import probe_native_control
    root,scopes,store,leases,a,thread,finish,errors,published=native_live
    ready=json.loads((a.output/'native_control_ready.json').read_bytes())
    finish.set();thread.join(5);assert not errors and published==[a.job_id]
    assert __import__('psutil').pid_exists(ready['owner_pid'])
    with pytest.raises(ValueError,match='live|available|socket|endpoint'):probe_native_control(ready)


def test_later_attempt_fences_original_native_publisher(native_live):
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.engine.job_store import JobStore,StaleFencingToken
    root,scopes,store,leases,a,thread,finish,errors,published=native_live
    plan=preview_live(root);assert plan['can_apply'],plan['blockers']
    apply_live(root,expected_source_sha256=plan['source_sha256'])
    fresh=JobStore(root/scopes['ledger']);fresh.reattach(a.job_id,worker_id='new-fenced-observer',lease_seconds=300)
    with pytest.raises(StaleFencingToken):a.check()
    finish.set();thread.join(5);assert published==[] and errors
    assert fresh.get(a.job_id).state=='running' and len(fresh.attempts(a.job_id))==1
    assert leases.list()[0]['fence']==a.lease.fence


def test_separate_cli_process_proves_the_original_backend_closure(native_live):
    import subprocess,sys
    root,scopes,store,leases,a,*_=native_live
    completed=subprocess.run([sys.executable,'-m','backend.engine.global_migration','preview-live','--root',str(root)],
        cwd=Path(__file__).parents[2],capture_output=True,text=True,timeout=8)
    assert completed.returncode==0,completed.stderr
    plan=json.loads(completed.stdout);assert plan['can_apply'],plan['blockers']
    worker=plan['live_adoptions']['workers'][0]
    assert worker['owner_pid']==os.getpid() and worker['job_id']==a.job_id and worker['attempt_fence']==a.lease.fence


def test_a_response_from_a_different_process_cannot_impersonate_backend(native_live):
    import socket,subprocess,sys,tempfile
    from backend.engine.live_specialist_control import probe_native_control
    root,scopes,store,leases,a,*_=native_live
    ready=json.loads((a.output/'native_control_ready.json').read_bytes())
    with tempfile.TemporaryDirectory(prefix='mvn-',dir='/private/tmp' if sys.platform=='darwin' else '/tmp') as directory:
        address=Path(directory)/'c.sock'
        code="import socket,sys,os;s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.bind(sys.argv[1]);os.chmod(sys.argv[1],0o600);s.listen(1);c,_=s.accept();c.recv(8192);c.close();s.close()"
        child=subprocess.Popen([sys.executable,'-c',code,str(address)])
        try:
            deadline=time.monotonic()+3
            while not address.exists() and time.monotonic()<deadline:time.sleep(.01)
            assert address.exists();ready['control_address']=str(address)
            with pytest.raises(ValueError,match='another process'):probe_native_control(ready)
            assert child.wait(3)==0
        finally:
            if child.poll() is None:child.terminate();child.wait(3)


def test_exact_native_cancel_follows_current_ledger_and_original_event(native_live):
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.engine.specialist_training_queue import cancel_owned
    from backend.engine.job_store import JobStore
    root,scopes,store,leases,a,thread,finish,errors,published=native_live
    plan=preview_live(root);apply_live(root,expected_source_sha256=plan['source_sha256'])
    cancel_owned(a.output)
    assert a.event.is_set() and JobStore(root/scopes['ledger']).cancel_intent(a.job_id)['actor_id']==a.actor
    finish.set();thread.join(5)
    assert errors and published==[] and JobStore(root/scopes['ledger']).get(a.job_id).state=='aborted'
    assert not leases.list()


@pytest.mark.parametrize('review_race',['natural','heartbeat_after_preview'])
def test_actual_cpu_ocr_training_completes_once_across_cli_cutover(tmp_path,monkeypatch,review_race):
    import subprocess,sys
    from fastapi.testclient import TestClient
    from PIL import Image
    from backend.main import create_app
    from backend.api import routes_ocr
    from backend.engine.job_store import JobStore
    from backend.engine.shared_scheduler import shared_leases
    from backend.engine import specialist_training_queue as native_queue
    admissions=[];reserve=native_queue.reserve
    def capture_admission(*args,**kwargs):
        admission,replay=reserve(*args,**kwargs)
        if admission is not None:admissions.append(admission)
        return admission,replay
    monkeypatch.setattr(native_queue,'reserve',capture_admission)
    root,scopes,*_=owned(tmp_path);monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root))
    app=create_app(project_dir=str(root/'projects'),shared_auth_dir=str(root/'auth'))
    client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    login=client.post('/api/accounts/login',json={'username':'fixture-admin','password':'fixture-password-123'})
    assert login.status_code==200,login.text
    client.headers['Authorization']='Bearer '+login.json()['token']
    response=client.post('/api/project/create',json={'name':'Actual specialist cutover'});assert response.status_code==200,response.text
    project=response.json();source=Path(project['project_dir'])/'source';source.mkdir();rows=[]
    for i,split in enumerate(['train','train','val','test']):
        file=source/f'{i}.png';Image.new('RGB',(32,32),(20+i*50,20,100)).save(file);rows.append({'image':file.name,'text':'A','split':split})
    original={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.png')}
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    prepared=client.post('/api/ocr/prepare',json={'source_dataset_path':str(source),'samples':rows});assert prepared.status_code==200,prepared.text
    entered=threading.Event();resume=threading.Event();calls=[];workers=[];checkpoint_deadline=[];trainer=routes_ocr.train_ocr
    def train(*args,**kwargs):
        calls.append(True);workers.append(threading.current_thread());callback=kwargs['on_progress']
        def progress(values):
            callback(values)
            if not entered.is_set():
                checkpoint_deadline.append(time.monotonic()+12)
                entered.set();assert resume.wait(12),'Owned checkpoint gate timed out'
        return trainer(*args,**{**kwargs,'on_progress':progress})
    monkeypatch.setattr(routes_ocr,'train_ocr',train)
    response=client.post('/api/ocr/train',json={'dataset_path':prepared.json()['dataset_path'],'epochs':2,
        'batch_size':2,'image_height':32,'image_width':64,'device':'cpu','background':True,'max_runtime_s':30})
    assert response.status_code==202,response.text
    identifier=response.json()['job_id'];output=Path(project['models_dir'])/'ocr'/identifier
    try:
        assert entered.wait(10),'Actual trainer did not reach its first completed optimizer batch'
        descriptor=(output/'native_control_ready.json').read_bytes();ready=json.loads(descriptor)
        assert JobStore(root/scopes['ledger']).get(identifier).state=='running'
        assert len(admissions)==1 and admissions[0].job_id==identifier
        admission=admissions[0]
        def control_identity():
            attempts=admission.store.attempts(identifier)
            claims=[row for row in admission.scheduler.leases.list() if row['job_id']==identifier]
            assert len(attempts)==len(claims)==1
            assert admission.store.get(identifier).state=='running'
            return {'attempt':{key:value for key,value in attempts[0].items() if key!='lease_expires_ns'},
                'reservation':{key:value for key,value in claims[0].items() if key!='expires'}}
        original_control=control_identity()
        original_pointers={name:(root/name).read_bytes() if (root/name).exists() else None
            for name in ('global-active.json','application-active.json')}
        assert original_pointers=={'global-active.json':None,'application-active.json':None}
        cmd=[sys.executable,'-m','backend.engine.global_migration'];reviews=[];stale_refusals=[]
        def invoke(*args):
            remaining=checkpoint_deadline[0]-time.monotonic()
            assert remaining>0,'Owned checkpoint review/apply budget exhausted'
            return subprocess.run([*cmd,*args],cwd=Path(__file__).parents[2],capture_output=True,
                text=True,timeout=min(5,remaining))
        for review_number in range(3):
            plan=invoke('preview-live','--root',str(root))
            assert plan.returncode==0,plan.stdout+plan.stderr
            value=json.loads(plan.stdout);assert value['can_apply'],value['blockers']
            assert control_identity()==original_control
            if review_race=='heartbeat_after_preview' and review_number==0:
                # The real heartbeat keeps writing expiry while optimizer work
                # waits. Reproduce that exact CAS race without muting the writer.
                with admission._control_scope(),admission._ownership_lock:
                    before_expiry=admission.store.attempts(identifier)[0]['lease_expires_ns']
                    admission.lease=admission.scheduler.heartbeat(admission.lease)
                    assert admission.store.attempts(identifier)[0]['lease_expires_ns']>before_expiry
            result=invoke('apply-live','--root',str(root),'--expected-source-sha256',value['source_sha256'])
            reviews.append({'source_sha256':value['source_sha256'],'returncode':result.returncode})
            assert (output/'native_control_ready.json').read_bytes()==descriptor
            assert control_identity()==original_control
            if result.returncode==0:
                migrated=json.loads(result.stdout)
                break
            assert result.returncode==1 and result.stderr=='',result.stdout+result.stderr
            assert json.loads(result.stdout)=={'error':'Live source changed since preview','status':'refused'},result.stdout
            stale_refusals.append(value['source_sha256'])
            assert {name:(root/name).read_bytes() if (root/name).exists() else None
                for name in original_pointers}==original_pointers
            assert not (root/'.global-generations').exists() and not (root/'.global-migrations').exists()
            assert original=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.png')}
        else:pytest.fail('Three exact reviews were refused within the original checkpoint budget')
        if review_race=='heartbeat_after_preview':assert stale_refusals
        assert migrated['workers_relaunched']==0 and migrated['worker_count']==1
        assert migrated['uncertain_reservations_cleared']==0
        frozen=(root/scopes['ledger']).read_bytes();resume.set()
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            state=JobStore(root/scopes['ledger']).get(identifier).state
            if state in {'completed','failed','aborted'}:break
            time.sleep(.01)
        assert state=='completed',json.loads((output/'job.json').read_text())
        journal=json.loads((output/'job.json').read_text());receipt=json.loads((output/'job_receipt.json').read_text())
        assert journal['status']=='completed' and journal['epoch']==2 and calls==[True]
        assert hashlib.sha256((output/'best_model.pt').read_bytes()).hexdigest()==receipt['checkpoint_sha256']
        assert (output/'native_control_ready.json').read_bytes()==descriptor
        assert not any(row['job_id']==identifier for row in shared_leases().list())
        attempts=JobStore(root/scopes['ledger']).attempts(identifier)
        assert len(attempts)==1 and attempts[0]['fencing_token']==ready['attempt_fence'] and attempts[0]['ended_ns'] is not None
        assert (root/scopes['ledger']).read_bytes()==frozen
        assert original=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.png')}
        deadline=time.monotonic()+3
        while Path(ready['control_address']).exists() and time.monotonic()<deadline:time.sleep(.01)
        assert not Path(ready['control_address']).exists(),'Original closure did not release its private proof endpoint'
        artifacts=os.environ.get('MV_NATIVE_RECEIPT_DIR')
        if artifacts:
            import shutil,tempfile
            directory=Path(tempfile.mkdtemp(prefix='native-ocr-',dir=artifacts))
            for name in ('native_control_ready.json','job.json','job_receipt.json'):
                shutil.copyfile(output/name,directory/name)
            records={name:{'sha256':hashlib.sha256((directory/name).read_bytes()).hexdigest(),
                          'size':(directory/name).stat().st_size} for name in ('native_control_ready.json','job.json','job_receipt.json')}
            (directory/'execution.json').write_text(json.dumps({'actual_cpu_training':True,'epochs_completed':2,
                'optimizer_progress_observed_before_cutover':True,'original_backend_pid':ready['owner_pid'],
                'attempt_count':len(attempts),'fencing_token':ready['attempt_fence'],'worker_relaunches':migrated['workers_relaunched'],
                'model_sha256':receipt['checkpoint_sha256'],'original_inputs':original,'original_inputs_preserved':True,
                'original_ledger_bytes_preserved':True,'private_endpoint_removed':True,'reservation_released':True,
                'migration':migrated,'attempts':attempts,'files':records,'review_race':review_race,
                'review_attempts':reviews,'stale_review_refusals':stale_refusals,
                'checkpoint_budget_s':12,'max_review_attempts':3,'quality_approved':False},indent=2)+'\n')
    finally:
        resume.set()
        for worker in workers:
            worker.join(12)
            assert not worker.is_alive(),'Original owned OCR worker did not unwind'
        client.close()


@pytest.mark.parametrize('field,value',[
    ('protocol_version',True),('attempt_fence',True),('attempt_number',0),('owner_pid',True),
    ('owner_created_at',float('nan')),('thread_id',True),('thread_id',0),
    ('installation_id','foreign'),('owner_instance','foreign'),('job_id','job_foreign'),
    ('spec_sha256','foreign'),('owner_command_sha256','foreign'),('lease_owner',''),('output_dir','relative')])
def test_malformed_descriptor_is_refused_before_endpoint_probe(native_live,field,value):
    from backend.engine.live_specialist_control import probe_native_control
    admission=native_live[4]
    ready=json.loads((admission.output/'native_control_ready.json').read_bytes());ready[field]=value
    with pytest.raises(ValueError,match='Invalid native live descriptor'):probe_native_control(ready)


def test_numeric_active_reply_cannot_stand_in_for_boolean_ownership(native_live,monkeypatch):
    from backend.engine import live_specialist_control as native
    ready=json.loads((native_live[4].output/'native_control_ready.json').read_bytes())
    receive=native._receive
    def numeric(connection):
        value=receive(connection)
        if 'active' in value:value['active']=1
        return value
    monkeypatch.setattr(native,'_receive',numeric)
    with pytest.raises(ValueError,match='prove live ownership'):native.probe_native_control(ready)


@pytest.mark.parametrize('message',[{'operation':'cancel','challenge':'a'*64},
    {'operation':'prove','challenge':'short'},{'operation':'prove','challenge':'a'*64,'grant':True},'x'*8193])
def test_read_only_endpoint_refuses_other_operations_and_unbounded_frames(native_live,message):
    import socket
    from backend.engine.live_specialist_control import probe_native_control
    admission=native_live[4];ready=json.loads((admission.output/'native_control_ready.json').read_bytes())
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(2);connection.connect(ready['control_address'])
        raw=message.encode()+b'\n' if isinstance(message,str) else json.dumps(message).encode()+b'\n'
        connection.sendall(raw)
        try:assert connection.recv(8192)==b''
        except ConnectionResetError:pass
    assert probe_native_control(ready) and not admission.event.is_set()
    assert len(admission.store.attempts(admission.job_id))==1


def test_interrupted_prepared_native_cutover_finishes_without_relaunch(native_live):
    from backend.engine.live_control_migration import preview_live,apply_live,recover_live
    from backend.engine.job_store import JobStore
    root,scopes,store,leases,a,thread,finish,errors,published=native_live
    plan=preview_live(root);saved=[]
    def interrupted(journal):
        saved.append(journal);raise RuntimeError('Owned interruption before pointer publication')
    with pytest.raises(RuntimeError,match='Owned interruption'):
        apply_live(root,expected_source_sha256=plan['source_sha256'],on_prepared=interrupted)
    assert not (root/'global-active.json').exists() and thread.is_alive()
    identifier=saved[0]['migration_id'];result=recover_live(root,identifier,action='finish')
    assert result['current_writes_preserved'] and a.lease.fence==plan['live_adoptions']['workers'][0]['attempt_fence']
    with pytest.raises(ValueError,match='cannot be restored'):recover_live(root,identifier,action='rollback')
    finish.set();thread.join(5);assert not errors and published==[a.job_id]
    assert JobStore(root/scopes['ledger']).get(a.job_id).state=='completed' and not leases.list()
