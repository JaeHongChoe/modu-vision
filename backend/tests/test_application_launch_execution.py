"""Actual controlled CPU arithmetic through one owned source backend epoch.

These scripts and generated weights are not a frozen/native product or quality
approval. Every process and database here belongs to the temporary fixture.
"""
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import time
import zipfile

import psutil
import pytest

from backend.tests.test_application_launch_controller import controller_arguments
from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture, plan, canonical, sha

REPOSITORY = Path(__file__).resolve().parents[2]
PROJECT_ID = 'c' * 32
EXPECTED_OUTPUT = {'final_verdict': 'OK', 'roi_count': 1, 'defective_roi_count': 0,
                   'routed_output_node_id': 'output', 'recognized_texts': ['A']}


def cpu_stack(tmp_path, *, delay=0, deadline_ms=30000, backend_suffix=''):
    import numpy as np
    import torch
    from PIL import Image
    from backend.engine.ocr import SmallCTCOCR
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import FlowNode, FlowNodeData, FlowEdge, FlowchartPipeline
    from backend.engine.runtime_update import install_update
    from backend.contracts.context import ContextRegistry

    root, *_ = owned(tmp_path); value = fixture(tmp_path)
    compiler = "const fs=require('fs'),ts=require('typescript');process.stdout.write(ts.transpileModule(fs.readFileSync(process.argv[1],'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText)"
    bridge = subprocess.check_output(['node', '-e', compiler, str(REPOSITORY/'src/main/applicationLaunch.ts')])
    backend = ('#!'+sys.executable+'\nimport sys,time,os\nsys.path.insert(0,'+repr(str(REPOSITORY))+')\n'
        'os.environ.update(TMPDIR='+repr(str(tmp_path))+',PYTHONDONTWRITEBYTECODE="1",HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1")\n'
        'from backend.engine.application_launch_handshake import early_backend_bootstrap,backend_bootstrap_ready,backend_execution_service\n'
        'from backend.engine.migration_guard import maintenance_guard\n'
        'early_backend_bootstrap()\n'
        "with maintenance_guard(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']):\n"
        ' backend_bootstrap_ready()\n time.sleep('+repr(delay)+')\n'+backend_suffix+
        ' backend_execution_service()\n').encode()
    main = f"""const fs=require('fs'),path=require('path'),{{spawn}}=require('child_process');
const {{authenticateMainLaunch}}=require('../bridge.cjs');
(async()=>{{const launch=await authenticateMainLaunch();
const executable=path.join(path.dirname(process.argv[1]),'backend_fixture.py');
const errors=fs.openSync(path.join(launch.projects,'execution-backend-error.txt'),'wx');
const backend=spawn({json.dumps(sys.executable)},[executable,'--project-dir',launch.projects,'--shared-auth-dir',launch.auth],{{cwd:launch.root,env:launch.backendEnvironment({{PATH:'/usr/bin:/bin'}}),stdio:['pipe','ignore',errors,'pipe']}});fs.closeSync(errors);
await launch.bindBackend(backend,executable,null);
fs.writeFileSync(path.join(launch.projects,'execution-stack-ready.json'),JSON.stringify({{main:process.pid,backend:backend.pid}}));setInterval(()=>{{}},100);
}})().catch(error=>{{fs.writeFileSync(path.join(process.env.VISION_AI_STUDIO_USER_DATA_DIR,'projects','execution-stack-error.txt'),String(error.stack));process.exit(3);}});"""
    files = {'bin/app': ('#!'+shutil.which('node')+'\n'+main).encode(),
             'bin/backend_fixture.py': backend, 'bridge.cjs': bridge}
    manifest = {'schema_version': 1, 'version': '1.0.0', 'platform': value['target']['platform'],
        'arch': value['target']['arch'], 'entrypoint': 'bin/app',
        'files': [{'path': name, 'size': len(raw), 'sha256': sha(raw), 'executable': name.startswith('bin/')}
                  for name, raw in files.items()]}
    archive = value['directory']/'application.zip'
    with zipfile.ZipFile(archive, 'w') as writer:
        writer.writestr('portable-application.json', canonical(manifest))
        for name, raw in files.items(): writer.writestr(name, raw)
    value['payload'].update(sha256=sha(archive.read_bytes()), size=archive.stat().st_size)
    value['payload']['artifacts'][0].update(sha256=value['payload']['sha256'], size=value['payload']['size'])
    value['sign'](value['payload']); current = install_update(root, plan(root, value))
    project = root/'projects'/'cpu-known-image'; project.mkdir()
    project_manifest = {'id': PROJECT_ID, 'project_dir': str(project)}
    (project/'project.json').write_bytes(canonical(project_manifest))
    registry = ContextRegistry(root/'projects'); registry.register_project(project_manifest)
    modeldir = project/'model'; modeldir.mkdir(); checkpoint = modeldir/'best_model.pt'
    model = SmallCTCOCR(1)
    for parameter in model.parameters(): parameter.data.zero_()
    model.head.bias.data[1] = 20  # Real CTC forward emits the one literal A.
    torch.save({'task': 'ocr', 'version': 1, 'alphabet': 'A', 'image_size': [32, 64],
        'model_state_dict': model.state_dict(), 'dataset_provenance': {'dataset_sha256': 'controlled'},
        'best_epoch': 1}, checkpoint)
    nodes = [FlowNode(id=n, position={}, data=FlowNodeData(label=n, node_type=t,
        task='ocr' if n=='ocr' else None, model_job_id='a'*32 if n=='ocr' else None,
        params={'regex': '^A$'} if n=='ocr' else {}))
        for n,t in [('input','input'),('ocr','inspection'),('decision','decision'),('output','output')]]
    graph = FlowchartPipeline(nodes=nodes, edges=[FlowEdge(id=f'e{i}', source=nodes[i].id,
        target=nodes[i+1].id) for i in range(3)])
    delivery = project/'delivery'/'launch-known-image'; delivery.mkdir(parents=True)
    package = Path(build_flow_package(pipeline=graph, checkpoints={'a'*32: checkpoint},
        output_base_dir=delivery, package_name='package')['package_path'])
    image = delivery/'input.png'; Image.fromarray(np.full((32,64,3),127,np.uint8)).save(image)
    reviewed = {'schema_version': 1, 'kind': 'owned_cpu_ocr_known_image_plan',
        'workspace_id': registry.workspace_id, 'project_id': PROJECT_ID,
        'project_manifest_sha256': sha((project/'project.json').read_bytes()),
        'package_path': 'delivery/launch-known-image/package',
        'package_manifest_sha256': sha((package/'manifest.json').read_bytes()),
        'graph_sha256': sha((package/'pipeline.json').read_bytes()),
        'checkpoints': [{'job_id': 'a'*32, 'sha256': sha((package/'models'/('a'*32)/'best_model.pt').read_bytes())}],
        'input_path': 'delivery/launch-known-image/input.png', 'input_sha256': sha(image.read_bytes()),
        'semantic_output_sha256': sha(canonical(EXPECTED_OUTPUT)),
        'device': 'cpu', 'cpu_threads': 1, 'deadline_ms': deadline_ms,
        'release_policy': None,
        'runtime_source_sha256': sha(canonical([{'path': p.relative_to(REPOSITORY).as_posix(),
            'size':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted((REPOSITORY/'backend').rglob('*.py'))
            if 'tests' not in p.relative_to(REPOSITORY/'backend').parts]))}
    target = delivery/'plan.json'; target.write_bytes(canonical(reviewed))
    return root, value, current, project, reviewed, sha(target.read_bytes())


def start_cpu_stack(values, *, prefix=None, extra=None):
    root,value,current,project,reviewed,pin = values
    args = [sys.executable, *(prefix or ['-m','backend.engine.application_launch_controller']),
        *controller_arguments(root,value,current), '--cpu-known-image-workspace-id',reviewed['workspace_id'],
        '--cpu-known-image-project-id',PROJECT_ID,'--cpu-known-image-plan-sha256',pin, *(extra or [])]
    child = subprocess.Popen(args, cwd=REPOSITORY, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
    assert select.select([child.stdout],[],[],10)[0], 'No original controller acknowledgement'
    raw = child.stdout.readline(65537)
    if not raw:
        child.wait(timeout=5)
        pytest.fail('CPU request was not admitted: '+child.stderr.read(4096).decode())
    ack = json.loads(raw); child.stdout.close(); child.stderr.close()
    assert ack['status']=='starting', ack
    # Test-only retained witness for this exact original controller handle.
    from backend.engine.application_launch_lease import _identity
    child._original_cpu_fixture_nonce = ack['nonce']
    child._original_cpu_fixture_supervisor = _identity(child.pid)
    return child, ack


def stop_stack(child, root):
    # Read raw journal for teardown only: an intentional corrupt publication is
    # never repaired by the production observer or used as release authority.
    pointer = json.loads((root/'application-launch-lease.json').read_bytes())
    row = json.loads((root/'.application-launches'/pointer['nonce']/'journal.json').read_bytes())
    process = row['process']
    if process:
        try:
            from backend.engine.application_launch_lease import _identity
            assert _identity(process['pid']) == process
            assert row['supervisor']['pid']==child.pid and os.getpgid(process['pid'])==process['pid']
            if psutil.Process(process['pid']).ppid()!=child.pid:
                assert child.poll() is not None and row['binding']['executable'] in psutil.Process(process['pid']).cmdline()
            os.killpg(process['pid'], signal.SIGTERM)
        except psutil.NoSuchProcess: pass
    if child.poll() is None: child.terminate()
    child.wait(timeout=10)


def wait_receipt(root, nonce, child, timeout=50):
    target = root/'.application-launches'/nonce/'cpu-execution-receipt.json'
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        if target.exists():
            raw=target.read_bytes(); row=json.loads((target.parent/'journal.json').read_bytes())
            pointer=json.loads((root/'application-launch-lease.json').read_bytes())
            if row.get('cpu_execution',{}).get('receipt_sha256')==sha(raw) and pointer['revision']==row['revision']:
                return target,json.loads(raw)
        assert child.poll() is None, 'Original controller died'
        row = json.loads((target.parent/'journal.json').read_bytes())
        if row['state']=='recovery_required': pytest.fail('CPU execution refused: '+str(row['reason']))
        time.sleep(.05)
    pytest.fail('No actual CPU execution receipt')


def _fixture_cpu_controls(root, nonce):
    """One coherent read-only original publication, never a transition writer."""
    from backend.engine.application_launch_controller import _inspection_snapshot
    from backend.engine.application_launch_handshake import HandshakeError
    from backend.engine import application_launch_lease as lease
    try:
        with _inspection_snapshot(root):
            row = lease._load(root)
            assert row is not None and row['nonce'] == nonce, 'Original CPU fixture launch changed'
            if row.get('cpu_execution') is None:
                return None
            directory = root/'.application-launches'/nonce
            controls = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
            pointer = root/'application-launch-lease.json'
            controls[pointer] = pointer.read_bytes()
        return row, controls
    except (HandshakeError, lease.LaunchLeaseError) as exc:
        if str(exc) in {
            'ownership publication interrupted or changed',
            'Launch publication changed during read-only inspection',
            'Launch sidecar publication changed during read-only inspection',
            'Launch journal or receipt changed during read-only inspection',
        }:
            return None
        raise


def test_real_cpu_ocr_runs_in_original_authenticated_backend_epoch_and_preserves_pins(tmp_path):
    values = cpu_stack(tmp_path,delay=8); root,value,current,project,reviewed,pin = values
    before = {p: sha(p.read_bytes()) for p in [project/'project.json', project/reviewed['input_path'],
        project/reviewed['package_path']/'manifest.json', project/reviewed['package_path']/'models'/('a'*32)/'best_model.pt']}
    child,ack = start_cpu_stack(values)
    try:
        directory=root/'.application-launches'/ack['nonce'];until=time.monotonic()+15
        coherent = None
        while time.monotonic()<until:
            coherent = _fixture_cpu_controls(root, ack['nonce'])
            if coherent is not None:break
            time.sleep(.05)
        assert coherent is not None, 'No coherent original CPU intent publication'
        row, controls = coherent
        assert row.get('cpu_execution') is not None
        for _ in range(4):
            observer=subprocess.run([sys.executable,'-m','backend.engine.application_launch_controller','--inspect',
                *controller_arguments(root,value,current)],cwd=REPOSITORY,capture_output=True,timeout=15)
            assert observer.returncode==0,observer.stdout
            assert json.loads(observer.stdout)['status']=='ready'
        assert {p:p.read_bytes() for p in controls}==controls
        assert not (directory/'cpu-execution-receipt.json').exists()
        target,receipt = wait_receipt(root,ack['nonce'],child)
        bootstrap = json.loads((target.parent/'bootstrap-receipt.json').read_bytes())
        assert receipt['status']=='succeeded'
        assert receipt['actual_cpu_execution_verified'] is True and receipt['owned_backend_execution_origin_verified'] is True
        assert receipt['semantic_output']==EXPECTED_OUTPUT
        assert receipt['plan_sha256']==pin and receipt['epoch']==bootstrap['epoch']
        assert receipt['backend_process']==bootstrap['backend_process']
        assert receipt['main_process']==bootstrap['main_process']
        assert receipt['binding']==bootstrap['binding']
        assert receipt['backend_frozen'] is False and receipt['execution_scope']=='controlled_source_backend'
        assert receipt['actual_application_inference_verified'] is False and receipt['release_ready'] is False
        result = json.loads((project/receipt['output_path']).read_bytes())
        assert result['crops'][0]['recognized_text']=='A'
        assert all(step['status'] not in ('error','warning_untrained') for step in result['execution_steps'])
        assert result['runtime_execution']['device']=='cpu' and result['runtime_execution']['isolated_process'] is True
        assert sha((project/receipt['output_path']).read_bytes())==receipt['output_sha256']
        assert {p:sha(p.read_bytes()) for p in before}==before
        inspected = subprocess.run([sys.executable,'-m','backend.engine.application_launch_controller','--inspect',
            *controller_arguments(root,value,current)],cwd=REPOSITORY,capture_output=True,timeout=15)
        observed = json.loads(inspected.stdout)
        assert observed['status']=='ready' and observed['bootstrap_binding_verified'] is True
        assert observed['actual_application_inference_verified'] is False and observed['release_ready'] is False
        from backend.engine.application_launch_execution import inspect_execution
        from types import SimpleNamespace
        observer_args=SimpleNamespace(root=str(root),authority=str(value['authority']),
            pinned_authority_sha256=sha(value['authority'].read_bytes()),
            expected_installation_id=json.loads((root/'.global-migration-owner.json').read_bytes())['installation_id'],expected_update_id=current['update_id'],
            expected_database_fence=str(current['database_pointer']['fence']),
            expected_launch_nonce=ack['nonce'],cpu_known_image_workspace_id=reviewed['workspace_id'],
            cpu_known_image_project_id=PROJECT_ID,cpu_known_image_plan_sha256=pin)
        stable={p:p.read_bytes() for p in target.parent.iterdir() if p.is_file()}
        stable[root/'application-launch-lease.json']=(root/'application-launch-lease.json').read_bytes()
        from backend.engine import application_launch_lease as lease
        old=lease._transition_admission
        try:
            lease._transition_admission=lambda *a,**k:pytest.fail('Read-only CPU receipt inspection took the transition mutex')
            observed_cpu=inspect_execution(observer_args)
        finally:lease._transition_admission=old
        assert observed_cpu['status']=='verified' and observed_cpu['actual_cpu_execution_verified'] is True
        assert observed_cpu['worker_process_tree_exit_verified'] is False
        assert observed_cpu['actual_application_inference_verified'] is False
        assert {p:p.read_bytes() for p in stable}==stable
        cli=subprocess.run([sys.executable,'-m','backend.engine.application_launch_controller','--inspect-cpu-execution',
            '--expected-launch-nonce',ack['nonce'],*controller_arguments(root,value,current),
            '--cpu-known-image-workspace-id',reviewed['workspace_id'],'--cpu-known-image-project-id',PROJECT_ID,
            '--cpu-known-image-plan-sha256',pin],cwd=REPOSITORY,capture_output=True,timeout=15)
        assert cli.returncode==0 and json.loads(cli.stdout)==observed_cpu
        (project/receipt['output_path']).write_bytes((project/receipt['output_path']).read_bytes()+b'changed output')
        with pytest.raises(ValueError):inspect_execution(observer_args)
        assert {p:p.read_bytes() for p in stable}==stable
    finally: stop_stack(child,root)


def test_stable_copy_checks_the_original_source_descriptor_and_writes_exact_bytes(tmp_path):
    from backend.engine.application_launch_execution import _read
    source=tmp_path/'source';source.write_bytes(b'exact original reviewed artifact')
    destination=tmp_path/'private'/'copy'
    assert _read(source,64,expected=sha(source.read_bytes()),destination=destination)==sha(source.read_bytes())
    assert destination.read_bytes()==b'exact original reviewed artifact'


def test_registered_scope_sqlite_never_reopens_the_original_name_after_regular_file_admission(tmp_path,monkeypatch):
    from backend.engine import application_launch_execution as execution
    from backend.engine.global_store_paths import resolve_store_path
    root,_,_,project,reviewed,_=cpu_stack(tmp_path)
    registry=resolve_store_path(root/'projects'/'.context.sqlite3')
    connect=execution.sqlite3.connect;opened=[]
    def inspect_connection(name,*args,**kwargs):
        opened.append(str(name))
        assert not str(name).startswith(registry.as_uri()), 'Original pathname was reopened by SQLite after descriptor admission'
        return connect(name,*args,**kwargs)
    monkeypatch.setattr(execution.sqlite3,'connect',inspect_connection)
    assert execution._project(root,reviewed['workspace_id'],PROJECT_ID)[0]==project
    assert opened


@pytest.mark.parametrize('manifest',[[],None,7])
def test_malformed_pinned_package_document_refuses_as_a_bounded_execution_error(tmp_path,manifest):
    from backend.engine import application_launch_execution as execution
    values=cpu_stack(tmp_path);root,_,_,project,reviewed,_=values
    raw=canonical(manifest);(project/PACKAGE_FOR_TEST/'manifest.json').write_bytes(raw)
    reviewed['package_manifest_sha256']=sha(raw);(project/'delivery/launch-known-image/plan.json').write_bytes(canonical(reviewed))
    with pytest.raises(ValueError):
        execution.admit_plan(root,reviewed['workspace_id'],PROJECT_ID,sha(canonical(reviewed)))


PACKAGE_FOR_TEST='delivery/launch-known-image/package'


def wait_recovery(root,nonce,child,timeout=40):
    directory=root/'.application-launches'/nonce;deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        assert child.poll() is None,'Ambiguous original controller exited'
        row=json.loads((directory/'journal.json').read_bytes())
        if row['state']=='recovery_required':return row
        time.sleep(.05)
    pytest.fail('Expected durable recovery_required')


def _source_callback_failure_suffix():
    """Test-only facts after original failure; never mint/retry or mask it."""
    return ''' from backend.engine import application_launch_execution as ex,application_owned_cpu_child_relay as relay,application_launch_handshake as h
 from pathlib import Path
 import json
 original_failure_root=Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR'])
 original_failure_execute=ex.execute_backend
 def failed_source(*args,**kwargs):
  try:return original_failure_execute(*args,**kwargs)
  except BaseException as error:
   try:
    cap=relay._CACHE_TICKETS.get(id(h._CACHE));s=relay._PRODUCERS.get(cap) if cap is not None else None
    results=original_failure_root/'projects/cpu-known-image/delivery/launch-known-image/results'
    snapshots=sorted(p.name for p in results.glob('.owned-cpu-*'))
    fact={'error_type':type(error).__name__,'error':str(error),'request_id':args[0]['request_id'],
     'admission':h._CACHE['admission'].snapshot(),'producer':None if s is None else {'phase':s['phase'],'counted':s['counted'],'private_acquired':s['private'] is not None},
     'snapshot_names':snapshots,'result_exists':any((results/name/'result.json').exists() for name in snapshots)}
    with (original_failure_root/'projects/source-callback-failure.json').open('x') as writer:json.dump(fact,writer,sort_keys=True)
   except BaseException:pass
   raise
 ex.execute_backend=failed_source
'''


def _source_callback_spurious_wake_suffix():
    """Artifact-drift fixture only: tick the already admitted original queue.

    This preserves the original wait, authentication loop and absolute budget.
    It does not prove the default production callback wakes within five seconds.
    """
    return ''' from backend.engine import application_owned_cpu_child_relay as relay,application_launch_handshake as h
 import math
 original_source_cache=h._CACHE
 assert type(original_source_cache) is dict and original_source_cache.get('ready') is True
 original_source_queue=original_source_cache.get('source_cpu_queue')
 assert type(original_source_queue) is relay.CpuRelayQueue and original_source_queue._cache is original_source_cache
 assert relay._CPU_QUEUE_CACHES.get(id(original_source_cache)) is original_source_queue
 original_source_condition=original_source_queue._condition
 assert type(original_source_condition) is relay.threading.Condition
 original_source_wait=original_source_condition.wait
 assert original_source_wait.__self__ is original_source_condition and original_source_wait.__func__ is relay.threading.Condition.wait
 def source_observed_wait(timeout=None):
  observed=timeout
  if type(timeout) in (int,float) and math.isfinite(timeout) and timeout>0:
   observed=min(timeout,.01)
  return original_source_wait(observed)
 original_source_condition.wait=source_observed_wait
'''


def _source_input_first_refusal_script():
    return '''from backend.engine import application_launch_controller as c,application_launch_lease as l
from pathlib import Path
import json,sys
original=l.LaunchSupervisor.recovery
def first(self,reason):
 result=original(self,reason)
 try:
  with (self.root/'projects/source-input-first-refusal.json').open('x') as writer:json.dump(result,writer,sort_keys=True)
 except BaseException:pass
 return result
l.LaunchSupervisor.recovery=first
raise SystemExit(c.main(sys.argv[1:]))
'''


def _wait_source_callback_failure(root, *, name='source-callback-failure.json'):
    deadline=time.monotonic()+5;path=root/'projects'/name
    while time.monotonic()<deadline:
        if path.is_file():return json.loads(path.read_bytes())
        time.sleep(.01)
    pytest.fail('Missing exact original source failure observation: '+str(path))


def _assert_source_callback_refusal(failure, kind, snapshots, request_id):
    assert kind=='deadline' and failure['request_id']==request_id
    assert set(failure)=={'error_type','error','request_id','admission','producer','snapshot_names','result_exists'}
    assert failure['error_type']=='HandshakeError' and failure['error'] in {
        'Original SOURCE CPU admission deadline expired','Original SOURCE CPU producer deadline expired'}
    assert failure['result_exists'] is False
    assert failure['snapshot_names']==sorted(p.name for p in snapshots)
    if failure['producer'] is None:
        # Before a successful original mint, no private snapshot/count exists.
        assert snapshots==[] and failure['admission']=={'active_scopes':0,'unsupported':[]}
    else:
        assert failure['producer']=={'phase':'unresolved','counted':True,'private_acquired':True}
        assert failure['admission']=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
        for snapshot in snapshots:assert snapshot.is_dir() and not snapshot.is_symlink()


def test_cpu_timeout_retains_exact_private_snapshot_and_never_qualifies_or_unlocks(tmp_path):
    from backend.engine.application_launch_lease import assert_quiescent
    values=cpu_stack(tmp_path,deadline_ms=1,backend_suffix=_source_callback_failure_suffix());root=values[0];project=values[3]
    child,ack=start_cpu_stack(values)
    try:
        row=wait_recovery(root,ack['nonce'],child)
        assert row['cpu_execution']['receipt_sha256'] is None
        assert not (root/'.application-launches'/ack['nonce']/'cpu-execution-receipt.json').exists()
        failure=_wait_source_callback_failure(root)
        request_id=row['cpu_execution']['request_id']
        snapshots=list((project/'delivery/launch-known-image/results').glob('.owned-cpu-*'))
        _assert_source_callback_refusal(failure,'deadline',snapshots,request_id)
        with pytest.raises(ValueError):assert_quiescent(root)
        assert child.poll() is None
    finally:stop_stack(child,root)


def test_exact_cpu_receipt_retry_rechecks_artifacts_instead_of_accepting_stale_output(tmp_path):
    values=cpu_stack(tmp_path);root=values[0]
    script='''from backend.engine import application_launch_controller as c
from pathlib import Path
import sys
original=c.execute_cpu
def repeat(owner,capability):
 receipt=original(owner,capability)
 target=Path(capability['project_path'])/'delivery/launch-known-image/input.png'
 target.write_bytes(target.read_bytes()+b'controlled artifact drift')
 try:owner._publish_cpu_execution(receipt)
 except ValueError:return receipt
 raise ValueError('BUG: stale exact receipt retry was accepted')
c.execute_cpu=repeat
raise SystemExit(c.main(sys.argv[1:]))
'''
    child,ack=start_cpu_stack(values,prefix=['-c',script])
    try:
        target,receipt=wait_receipt(root,ack['nonce'],child)
        # A rejected deliberate publication retry leaves the original receipt
        # and ready record intact; a falsely accepted retry marks our diagnostic.
        time.sleep(.3)
        row=json.loads((target.parent/'journal.json').read_bytes())
        assert row['state']=='ready',row['reason']
        assert sha(target.read_bytes())==row['cpu_execution']['receipt_sha256']
    finally:stop_stack(child,root)


def test_deep_cpu_proof_refuses_as_a_bounded_error_instead_of_crashing_original_controller():
    import base64
    from backend.engine.application_launch_execution import encoded_proof
    raw=b'{"deep":'+b'['*1800+b'0'+b']'*1800+b'}'
    with pytest.raises(ValueError):encoded_proof(base64.b64encode(raw).decode())


@pytest.mark.parametrize('damage',['image_id','confidence','untrained','incomplete','verdict_shape','step_shape'])
def test_actual_flow_result_identity_and_finite_trained_evidence_are_required(tmp_path,damage):
    import torch
    from backend.engine.flow_package_runtime import run_flow_package
    from backend.engine.application_launch_execution import semantic_output
    values=cpu_stack(tmp_path);project=values[3];torch.set_num_threads(1)
    result=run_flow_package(project/PACKAGE_FOR_TEST,project/'delivery/launch-known-image/input.png',
        'owned-cpu-known-image',device='cpu',cpu_threads=1,_owned_worker=True)
    if damage=='image_id':result['image_id']='another reviewed image'
    if damage=='confidence':result['crops'][0]['confidence']=float('inf')
    if damage=='untrained':result['execution_steps'][1]['status']='warning_untrained'
    if damage=='incomplete':result['execution_steps']=result['execution_steps'][-1:]
    if damage=='verdict_shape':result['final_verdict']=[]
    if damage=='step_shape':result['execution_steps'][1]['status']=[]
    with pytest.raises(ValueError):semantic_output(result)


def test_post_spawn_unexpected_execution_error_retains_original_controller_and_ownership(tmp_path):
    values=cpu_stack(tmp_path);root=values[0]
    script='''from backend.engine import application_launch_controller as c
import sys
def failure(owner,capability):raise RuntimeError('controlled unexpected execution failure')
c.execute_cpu=failure
raise SystemExit(c.main(sys.argv[1:]))
'''
    child,ack=start_cpu_stack(values,prefix=['-c',script])
    try:
        row=wait_recovery(root,ack['nonce'],child,timeout=15)
        assert row['reason'] and row.get('cpu_execution') is None
        from backend.engine.application_launch_lease import assert_quiescent
        with pytest.raises(ValueError):assert_quiescent(root)
        assert child.poll() is None
    finally:stop_stack(child,root)


def test_controller_independently_rejects_changed_original_backend_claim_challenge(tmp_path):
    values=cpu_stack(tmp_path);root=values[0]
    script='''from backend.engine import application_launch_controller as c,application_launch_execution as e
import sys
original=e.encoded_proof
def changed(raw):
 result=original(raw)
 result['backend_proof']['challenge']='f'*64
 return result
e.encoded_proof=changed
raise SystemExit(c.main(sys.argv[1:]))
'''
    child,ack=start_cpu_stack(values,prefix=['-c',script])
    try:
        row=wait_recovery(root,ack['nonce'],child,timeout=25)
        assert row['cpu_execution']['receipt_sha256'] is None
        assert not (root/'.application-launches'/ack['nonce']/'cpu-execution-receipt.json').exists()
    finally:stop_stack(child,root)


def test_release_semantic_validators_only_reopen_private_bounded_snapshots(tmp_path,monkeypatch):
    from backend.engine import application_launch_execution as execution,flow_package_runtime,inspection_service
    values=cpu_stack(tmp_path);root,_,_,project,reviewed,_=values
    package=project/PACKAGE_FOR_TEST;path=package/'manifest.json'
    manifest=json.loads(path.read_bytes());manifest['release']={'controlled':'path-boundary-only'}
    path.write_bytes(canonical(manifest));reviewed['package_manifest_sha256']=sha(path.read_bytes())
    policy=project/'delivery/launch-known-image/release-policy.json';policy.write_bytes(canonical({'controlled':'private copy'}))
    reviewed['release_policy']={'sha256':sha(policy.read_bytes())}
    (project/'delivery/launch-known-image/plan.json').write_bytes(canonical(reviewed));seen=[]
    def verify(copied):
        assert copied!=package,'Semantic validator reopened the original package after bounded admission'
        assert (copied/'manifest.json').read_bytes()==path.read_bytes();seen.append(copied)
        return None,{}
    def approve(copied,resolved,policy_copy,**kwargs):
        assert copied!=package and policy_copy!=policy
        assert policy_copy.read_bytes()==policy.read_bytes();seen.append(policy_copy)
    monkeypatch.setattr(flow_package_runtime,'verify_flow_package',verify)
    monkeypatch.setattr(inspection_service,'_verify_release_policy',approve)
    execution.admit_plan(root,reviewed['workspace_id'],PROJECT_ID,sha(canonical(reviewed)))
    assert len(seen)==2


@pytest.mark.parametrize('damage',['input','checkpoint','plan','unlisted','link','fifo'])
def test_changed_or_unsafe_source_refuses_before_any_application_reservation(tmp_path,damage):
    values=cpu_stack(tmp_path);root,value,current,project,reviewed,pin=values
    image=project/'delivery/launch-known-image/input.png'
    if damage=='input':image.write_bytes(image.read_bytes()+b'changed reviewed input')
    if damage=='checkpoint':
        target=project/PACKAGE_FOR_TEST/'models'/('a'*32)/'best_model.pt';target.write_bytes(target.read_bytes()+b'changed checkpoint')
    if damage=='plan':(project/'delivery/launch-known-image/plan.json').write_bytes(canonical({**reviewed,'deadline_ms':1}))
    if damage=='unlisted':(project/PACKAGE_FOR_TEST/'sitecustomize.py').write_text('raise RuntimeError("unreviewed startup")')
    if damage=='link':
        target=tmp_path/'outside.png';target.write_bytes(image.read_bytes());image.unlink();image.symlink_to(target)
    if damage=='fifo':image.unlink();os.mkfifo(image)
    result=subprocess.run([sys.executable,'-m','backend.engine.application_launch_controller',
        *controller_arguments(root,value,current),'--cpu-known-image-workspace-id',reviewed['workspace_id'],
        '--cpu-known-image-project-id',PROJECT_ID,'--cpu-known-image-plan-sha256',pin],
        cwd=REPOSITORY,capture_output=True,timeout=15)
    assert result.returncode==2 and json.loads(result.stdout)['status']=='refused'
    assert not (root/'application-launch-lease.json').exists() and not (root/'.application-launches').exists()


@pytest.mark.parametrize('damage',['growth','named-replacement','hardlink','fifo'])
def test_reader_refuses_changed_or_special_files_without_blocking(tmp_path,monkeypatch,damage):
    from backend.engine import application_launch_execution as execution
    target=tmp_path/'input';target.write_bytes(b'reviewed original bytes')
    if damage=='hardlink':os.link(target,tmp_path/'linked')
    if damage=='fifo':target.unlink();os.mkfifo(target)
    once=False
    def change(point):
        nonlocal once
        if point!='after_artifact_chunk' or once:return
        once=True
        if damage=='growth':
            with target.open('ab') as writer:writer.write(b'growth')
        if damage=='named-replacement':
            raw=target.read_bytes();target.rename(tmp_path/'old');target.write_bytes(raw)
    monkeypatch.setattr(execution,'_checkpoint',change)
    began=time.monotonic()
    with pytest.raises(ValueError):execution._read(target,64)
    assert time.monotonic()-began<2


@pytest.mark.parametrize('damage',['nonce','epoch','binding_sha256','backend_claim_sha256'])
def test_stale_request_is_rejected_before_admission_or_model_work(monkeypatch,damage):
    from backend.engine import application_launch_execution as execution,application_launch_lease as lease
    proof={'nonce':'a'*32,'epoch':'b'*32,'binding_sha256':'c'*64}
    request={'schema_version':1,'kind':'cpu_execution_request','challenge':'d'*64,'request_id':'e'*32,
        **proof,'workspace_id':'f'*32,'project_id':PROJECT_ID,'plan_sha256':'1'*64,
        'backend_claim_sha256':sha(canonical(proof))}
    request[damage]='0'*len(request[damage])
    monkeypatch.setattr(lease,'_transition_admission',lambda *a,**k:pytest.fail('A stale request entered the durable admission'))
    with pytest.raises(ValueError):execution.validate_request(request,proof,Path('/unused'))


def test_frozen_cpu_worker_refuses_before_model_or_snapshot_work(tmp_path,monkeypatch):
    from backend.engine import application_launch_execution as execution
    monkeypatch.setattr(execution,'validate_request',lambda *a:pytest.fail('Frozen execution entered source-runtime admission'))
    with pytest.raises(ValueError,match='requires_target'):execution.execute_backend({}, {'frozen':True},tmp_path)
    assert not list(tmp_path.iterdir())


def test_exact_receipt_repeat_preserves_original_bytes_and_foreign_claims_refuse(tmp_path):
    values=cpu_stack(tmp_path);root=values[0]
    script='''from backend.engine import application_launch_controller as c,runtime_update as u
from pathlib import Path
import copy,sys
original=c.execute_cpu
def repeat(owner,capability):
 receipt=original(owner,capability)
 directory=owner.root/'.application-launches'/owner.nonce
 names=[directory/'journal.json',directory/'cpu-execution-receipt.json',owner.root/'application-launch-lease.json']
 before={str(p):p.read_bytes() for p in names}
 owner._publish_cpu_execution(copy.deepcopy(receipt))
 for name,value in [('workspace_id','0'*32),('checkpoints',[]),('worker_process_tree_exit_verified',True),('foreign_field',True)]:
  wrong=copy.deepcopy(receipt);wrong[name]=value
  try:owner._publish_cpu_execution(wrong)
  except ValueError:pass
  else:raise RuntimeError('Foreign receipt claim was accepted')
 assert all(Path(p).read_bytes()==raw for p,raw in before.items())
 (Path(capability['project_path'])/'receipt-repeat-verified.json').write_bytes(u._canonical({'exact_repeat':True,'foreign_refused':True}))
 return receipt
c.execute_cpu=repeat
raise SystemExit(c.main(sys.argv[1:]))
'''
    child,ack=start_cpu_stack(values,prefix=['-c',script])
    try:
        target,_=wait_receipt(root,ack['nonce'],child)
        marker=values[3]/'receipt-repeat-verified.json';until=time.monotonic()+10
        while not marker.exists() and time.monotonic()<until:time.sleep(.05)
        assert json.loads(marker.read_bytes())=={'exact_repeat':True,'foreign_refused':True}
        assert json.loads((target.parent/'journal.json').read_bytes())['state']=='ready'
    finally:stop_stack(child,root)


@pytest.mark.parametrize('stage',['intent','receipt','output'])
def test_interrupted_execution_publication_never_repairs_or_unlocks(tmp_path,stage):
    suffix=''
    if stage=='output':
        suffix=" from backend.engine import application_launch_execution as e\n def fault(point):\n  if point=='after_cpu_output':raise ValueError('controlled output publication interruption')\n e._checkpoint=fault\n"
    values=cpu_stack(tmp_path,backend_suffix=suffix);root,value,current,project,reviewed,pin=values
    script=None
    if stage!='output':
        point='after_cpu_execution_'+stage
        script=f'''from backend.engine import application_launch_controller as c,application_launch_lease as l
import sys
def fault(point):
 if point=={point!r}:raise ValueError('controlled durable publication interruption')
l._checkpoint=fault
raise SystemExit(c.main(sys.argv[1:]))
'''
    child,ack=start_cpu_stack(values,prefix=['-c',script] if script else None)
    try:
        directory=root/'.application-launches'/ack['nonce'];until=time.monotonic()+40
        while time.monotonic()<until:
            row=json.loads((directory/'journal.json').read_bytes())
            sidecar=directory/('cpu-execution-'+stage+'.json')
            if stage=='output' and row['state']=='recovery_required':break
            if stage!='output' and sidecar.exists():break
            assert child.poll() is None;time.sleep(.05)
        assert child.poll() is None
        assert row.get('cpu_execution') is None if stage=='intent' else row['cpu_execution']['receipt_sha256'] is None
        if stage=='output':assert list((project/'delivery/launch-known-image/results').glob('*.json'))
        else:assert sidecar.exists()
        time.sleep(.3)
        controls={p:p.read_bytes() for p in directory.iterdir() if p.is_file()}
        controls[root/'application-launch-lease.json']=(root/'application-launch-lease.json').read_bytes()
        for _ in range(3):
            result=subprocess.run([sys.executable,'-m','backend.engine.application_launch_controller','--inspect',
                *controller_arguments(root,value,current)],cwd=REPOSITORY,capture_output=True,timeout=15)
            observed=json.loads(result.stdout)
            assert observed['status']=='refused' if stage!='output' else observed['status']=='recovery_required'
        assert {p:p.read_bytes() for p in controls}==controls
        from backend.engine.application_launch_lease import assert_quiescent
        with pytest.raises(ValueError):assert_quiescent(root)
    finally:stop_stack(child,root)


def test_reviewed_package_python_cannot_shadow_snapshotted_current_runtime(tmp_path):
    suffix=""" import os,json
 os.environ.update(OPENAI_API_KEY='controlled-parent-secret',CUDA_VISIBLE_DEVICES='controlled-inherited-gpu',NVIDIA_VISIBLE_DEVICES='all')
 from backend.engine import application_owned_cpu_child_relay as relay
 from pathlib import Path
 original=relay.subprocess.Popen
 def closed(command,**options):
  if not options.get('pass_fds'):return original(command,**options)
  assert len(options['pass_fds'])==3 and len(command)==12
  env=options['env'];private=Path(options['cwd'])
  assert 'OPENAI_API_KEY' not in env and 'PYTHONPATH' not in env
  assert Path(env['HOME']).is_relative_to(private) and env['HF_HUB_OFFLINE']=='1'
  assert all(env[key]=='1' for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'))
  assert command[1:3]==['-I','-B']
  assert env['CUDA_VISIBLE_DEVICES']=='' and env['NVIDIA_VISIBLE_DEVICES']=='none'
  child=original(command,**options)  # Exactly once; fixed command/kwargs unchanged.
  with (private/'worker-env-proof.json').open('x') as writer:
   json.dump({'CUDA_VISIBLE_DEVICES':env['CUDA_VISIBLE_DEVICES'],'NVIDIA_VISIBLE_DEVICES':env['NVIDIA_VISIBLE_DEVICES'],
    'pid':child.pid,'command':command,'pass_fds':list(options['pass_fds'])},writer,sort_keys=True)
  return child
 relay.subprocess.Popen=closed
"""
    values=cpu_stack(tmp_path,backend_suffix=suffix);root,value,current,project,reviewed,pin=values
    package=project/PACKAGE_FOR_TEST;target=package/'backend/engine/flow_package_runtime.py';marker=tmp_path/'package-code-executed'
    poison=('from pathlib import Path\nPath('+repr(str(marker))+').write_text("package code executed")\nraise RuntimeError("package shadow")\n').encode()
    target.write_bytes(poison);manifest=json.loads((package/'manifest.json').read_bytes())
    row=next(row for row in manifest['files'] if row['path']=='backend/engine/flow_package_runtime.py')
    row.update(size=len(poison),sha256=sha(poison));(package/'manifest.json').write_bytes(canonical(manifest))
    reviewed['package_manifest_sha256']=sha((package/'manifest.json').read_bytes())
    (project/'delivery/launch-known-image/plan.json').write_bytes(canonical(reviewed))
    values=(*values[:-1],sha(canonical(reviewed)))
    child,ack=start_cpu_stack(values)
    try:
        _,receipt=wait_receipt(root,ack['nonce'],child)
        assert receipt['semantic_output']==EXPECTED_OUTPUT and not marker.exists()
        snapshot=project/'delivery/launch-known-image/results'/('.owned-cpu-'+receipt['request_id'])
        environment=json.loads((snapshot/'worker-env-proof.json').read_bytes())
        assert environment['CUDA_VISIBLE_DEVICES']=='' and environment['NVIDIA_VISIBLE_DEVICES']=='none'
        assert environment['pid']==receipt['worker_pid'] and len(environment['pass_fds'])==3
        assert environment['command'][1:4]==['-I','-B','-X'] and len(environment['command'])==12
        assert (snapshot/'package/backend/engine/flow_package_runtime.py').read_bytes()==poison
        assert (snapshot/'runtime/backend/engine/flow_package_runtime.py').read_bytes()==(REPOSITORY/'backend/engine/flow_package_runtime.py').read_bytes()
    finally:stop_stack(child,root)


@pytest.mark.parametrize('failure',['semantic-mismatch','artifact-drift'])
def test_actual_cpu_math_cannot_qualify_mismatched_or_changed_reviewed_inputs(tmp_path,failure):
    suffix=''
    if failure=='artifact-drift':
        suffix=" from backend.engine import application_launch_execution as e\n from pathlib import Path\n def change(point):\n  if point=='before_cpu_worker':\n   target=Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR'])/'projects/cpu-known-image/delivery/launch-known-image/input.png'\n   target.write_bytes(target.read_bytes()+b'controlled post-snapshot drift')\n e._checkpoint=change\n"
    if failure=='artifact-drift':
        suffix+=_source_callback_spurious_wake_suffix()+_source_callback_failure_suffix()
    values=cpu_stack(tmp_path,backend_suffix=suffix);root,_,_,project,reviewed,_=values
    if failure=='semantic-mismatch':
        reviewed['semantic_output_sha256']=sha(canonical({**EXPECTED_OUTPUT,'recognized_texts':['B']}))
        (project/'delivery/launch-known-image/plan.json').write_bytes(canonical(reviewed))
        values=(*values[:-1],sha(canonical(reviewed)))
    prefix=None
    if failure=='artifact-drift':
        prefix=['-c',_source_input_first_refusal_script()]
    child,ack=start_cpu_stack(values,prefix=prefix)
    try:
        row=wait_recovery(root,ack['nonce'],child)
        assert row['cpu_execution']['receipt_sha256'] is None
        snapshots=list((project/'delivery/launch-known-image/results').glob('.owned-cpu-*'))
        assert len(snapshots)==1
        if failure=='semantic-mismatch':
            actual=json.loads((snapshots[0]/'result.json').read_bytes())
            assert actual['crops'][0]['recognized_text']=='A' and actual['image_id']=='owned-cpu-known-image'
        else:
            first=_wait_source_callback_failure(root,name='source-input-first-refusal.json')
            assert first['state']=='recovery_required' and first['nonce']==ack['nonce']
            assert first['reason']=='Private bootstrap or execution refused: Reviewed execution artifact checksum differs'
            assert first['cpu_execution']['receipt_sha256'] is None
            assert not (snapshots[0]/'result.json').exists()
            assert sha((snapshots[0]/'input.png').read_bytes())==reviewed['input_sha256']
            failure_receipt=_wait_source_callback_failure(root)
            assert failure_receipt['request_id']==row['cpu_execution']['request_id']
            assert failure_receipt['admission']=={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']}
            assert failure_receipt['producer']['counted'] is True and failure_receipt['producer']['phase']=='unresolved'
            assert failure_receipt['result_exists'] is False
        assert not (root/'.application-launches'/ack['nonce']/'cpu-execution-receipt.json').exists()
        from backend.engine.application_launch_lease import assert_quiescent
        with pytest.raises(ValueError):assert_quiescent(root)
    finally:stop_stack(child,root)


def test_original_backend_crash_during_worker_keeps_durable_ownership_unqualified(tmp_path):
    from backend.engine.application_launch_lease import _identity,assert_quiescent
    values=cpu_stack(tmp_path);root=values[0];project=values[3]
    child,ack=start_cpu_stack(values);worker=None
    try:
        directory=root/'.application-launches'/ack['nonce'];until=time.monotonic()+25
        while time.monotonic()<until:
            bootstrap=directory/'bootstrap-receipt.json'
            if bootstrap.exists():
                backend=json.loads(bootstrap.read_bytes())['backend_process']
                for candidate in psutil.Process(backend['pid']).children():
                    command=candidate.cmdline()
                    if '-I' in command and '-c' in command and any(str(project/'delivery/launch-known-image/results') in arg for arg in command):
                        worker=_identity(candidate.pid);break
            if worker:break
            time.sleep(.02)
        assert worker,'No real original-backend CPU child was observed'
        assert _identity(backend['pid'])==backend and psutil.Process(worker['pid']).ppid()==backend['pid']
        assert os.getpgid(worker['pid'])==worker['pid']
        os.kill(backend['pid'],signal.SIGTERM)
        row=wait_recovery(root,ack['nonce'],child)
        assert row['cpu_execution']['receipt_sha256'] is None and child.poll() is None
        assert not (directory/'cpu-execution-receipt.json').exists()
        with pytest.raises(ValueError):assert_quiescent(root)
        assert list((project/'delivery/launch-known-image/results').glob('.owned-cpu-*'))
    finally:
        if worker is not None:
            try:
                if psutil.Process(worker['pid']).status()!=psutil.STATUS_ZOMBIE:
                    assert _identity(worker['pid'])==worker and os.getpgid(worker['pid'])==worker['pid']
                    os.killpg(worker['pid'],signal.SIGTERM)
            except psutil.NoSuchProcess:pass
        stop_stack(child,root)


@pytest.fixture(autouse=True)
def controlled_canary_publication(monkeypatch):
    """Scope this legacy lifecycle fixture to controlled canary proof only.

    Actual staged source CPU math is tested independently; these tests retain
    their original signed-layout, admission and recovery assertions.
    """
    from backend.tests.test_staged_update_canary import controlled_proof
    controlled_proof(monkeypatch)
