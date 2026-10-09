"""Fixed frozen worker boundaries; source probes never imply compiled acceptance."""
import runpy
import sys
import hashlib
import json
import os
import subprocess
import zipfile
import shutil
import time
import select
from pathlib import Path

import pytest

# Test-only legacy controlled publication fixture; no staged canary acceptance.
from backend.tests.test_application_launch_execution import controlled_canary_publication

ROOT = Path(__file__).resolve().parents[2]


def test_fixed_frozen_worker_flag_refuses_source_before_backend_or_home(monkeypatch, tmp_path):
    from backend.engine import application_launch_handshake as handshake
    events = []
    monkeypatch.setattr(handshake, 'early_backend_bootstrap', lambda: events.append('bootstrap'))
    monkeypatch.setattr(runpy, 'run_module', lambda *a, **k: events.append('mutable-server'))
    monkeypatch.setattr(sys, 'argv', [str(ROOT/'scripts/frozen_backend_entry.py'),
                                     '--owned-application-cpu-worker'])
    monkeypatch.setenv('HOME', str(tmp_path/'home'))
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(ROOT/'scripts/frozen_backend_entry.py'), run_name='__main__')
    assert stopped.value.code == 2
    assert events == []
    assert not (tmp_path/'home').exists()


def test_current_inventory_advertises_separate_fixed_cpu_protocol(monkeypatch):
    from scripts import build_backend_binary as build
    monkeypatch.setattr(build, 'DEPENDENCIES', ())
    inventory = build.dependency_inventory(ROOT)
    assert inventory['owned_application_cpu_execution_protocol'] == 1
    assert type(inventory['owned_application_cpu_execution_protocol']) is int
    build.validate_inventory(inventory)


@pytest.fixture
def resource_boundary(tmp_path, monkeypatch):
    """Synthetic resources exercise validators, never compiled execution."""
    from backend.engine import application_launch_execution as execution
    from backend.engine import runtime_update as update
    root=tmp_path/'bundled-resources';rows=[]
    for name in execution.CPU_RESOURCES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'# source boundary fixture\n')
        rows.append({'path':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    inventory={'resources':rows,execution.CPU_PROTOCOL:1,'owned_application_launch_controller_protocol':1}
    inventory['build_identity_sha256']=update._sha(update._canonical(inventory))
    (root/'backend-build-inventory.json').write_bytes(update._canonical(inventory))
    monkeypatch.setattr(sys,'frozen',True,raising=False);monkeypatch.setattr(sys,'_MEIPASS',str(root),raising=False)
    return execution,root,inventory


@pytest.mark.parametrize('change',['unknown','coerced','missing','duplicate','drift','unlisted','link','fifo'])
def test_frozen_resource_boundary_refuses_changed_or_unreviewed_inputs_before_home(resource_boundary,tmp_path,change):
    execution,root,inventory=resource_boundary
    from backend.engine import runtime_update as update
    if change=='unknown':inventory[execution.CPU_PROTOCOL]=2
    elif change=='coerced':inventory[execution.CPU_PROTOCOL]=True
    elif change=='missing':inventory['resources'].pop()
    elif change=='duplicate':inventory['resources'].append(inventory['resources'][-1])
    else:
        target=root/'backend/engine/ocr.py'
        if change=='drift':target.write_bytes(b'# drift\n')
        elif change=='unlisted':(root/'backend/unlisted.py').write_bytes(b'# unlisted\n')
        elif change=='link':target.unlink();target.symlink_to(root/'backend/engine/process_isolation.py')
        elif change=='fifo':target.unlink();os.mkfifo(target)
    inventory['build_identity_sha256']=update._sha(update._canonical({k:v for k,v in inventory.items() if k!='build_identity_sha256'}))
    (root/'backend-build-inventory.json').write_bytes(update._canonical(inventory))
    with pytest.raises(ValueError):execution._frozen_inventory()
    assert not (tmp_path/'home').exists()


def test_frozen_worker_refuses_foreign_parent_before_private_home(resource_boundary,tmp_path):
    execution,root,inventory=resource_boundary
    from backend.engine import runtime_update as update
    capsule={'schema_version':1,'kind':'owned_frozen_cpu_worker','root':str(tmp_path),
        'request':{},'capability':{},'backend_proof':{'schema_version':1,'kind':'backend_claim',
        'challenge':'a'*64,'epoch':'b'*32,'nonce':'c'*32,'binding_sha256':'d'*64,
        'process':{'pid':os.getppid()+100000},'executable':sys.executable,'executable_sha256':'e'*64,
        'build_identity_sha256':inventory['build_identity_sha256'],'frozen':True}}
    request=tmp_path/'request.json';raw=update._canonical(capsule);request.write_bytes(raw)
    # This isolates the parent check from owned-root setup. It does not grant
    # admission, reserve a lease, or execute arithmetic.
    from backend.tests.test_global_migration import owned
    owned_root,*_=owned(tmp_path);capsule['root']=str(owned_root)
    raw=update._canonical(capsule);request.write_bytes(raw)
    with pytest.raises(ValueError,match='foreign.*parent'):
        execution._frozen_worker_admission(request,update._sha(raw))
    assert not (tmp_path/'home').exists()


def test_fixed_worker_replay_and_partial_marker_refuse_before_math(resource_boundary,tmp_path,monkeypatch):
    execution,root,inventory=resource_boundary
    directory=tmp_path/'retained';directory.mkdir();(directory/'worker-admission.json').write_bytes(b'{')
    proof={'process':{'pid':os.getppid()}}
    monkeypatch.setattr(execution,'_frozen_worker_admission',lambda *a:(tmp_path,{},proof,{},directory))
    assert execution.frozen_worker_main(['--request-file',str(directory/'worker-request.json'),'--request-sha256','a'*64])==2
    assert (directory/'worker-admission.json').read_bytes()==b'{'
    assert not (directory/'home').exists() and not (directory/'result.json').exists()


def test_fixed_worker_parser_rejects_unknown_flags_before_inventory(resource_boundary,monkeypatch):
    execution,*_=resource_boundary
    monkeypatch.setattr(execution,'_frozen_inventory',lambda:pytest.fail('Unknown flag reached inventory'))
    with pytest.raises(SystemExit) as stopped:
        execution.frozen_worker_main(['--request-file','/foreign','--request-sha256','a'*64,'--module','backend.main'])
    assert stopped.value.code==2


@pytest.mark.parametrize('change',['growth','fifo','link'])
def test_worker_request_changes_refuse_before_home_or_marker(resource_boundary,tmp_path,monkeypatch,change):
    execution,root,inventory=resource_boundary
    from backend.engine import runtime_update as update
    request=tmp_path/'request.json';request.write_bytes(b'{}')
    monkeypatch.setattr(execution,'_frozen_inventory',lambda:(inventory,'a'*64))
    if change=='growth':
        mutated=False
        def grow(point):
            nonlocal mutated
            if not mutated:
                mutated=True
                with request.open('ab') as writer:writer.write(b'!')
        monkeypatch.setattr(execution,'_checkpoint',grow)
    elif change=='fifo':request.unlink();os.mkfifo(request)
    else:
        other=tmp_path/'other';other.write_bytes(b'{}');request.unlink();request.symlink_to(other)
    with pytest.raises(ValueError):execution._frozen_worker_admission(request,update._sha(b'{}'))
    assert not (tmp_path/'home').exists() and not (tmp_path/'worker-admission.json').exists()


@pytest.mark.parametrize('state',['reserved','starting','recovery_required','exited'])
def test_worker_origin_refuses_lifecycle_race_even_when_original_processes_stay_live(tmp_path,monkeypatch,state):
    from backend.engine import application_launch_execution as execution,application_launch_controller as controller
    import psutil
    row={'state':state,'claimed':True,'ready_receipt_sha256':'a'*64,'binding':{},
        'supervisor':{'pid':10},'process':{'pid':20}}
    proof={'backend_process':{'pid':30},'backend_executable':'/controlled/backend',
        'backend_executable_sha256':'b'*64,'backend_build_identity_sha256':None,'backend_frozen':False}
    monkeypatch.setattr(controller,'_same_identity',lambda *a:True)
    monkeypatch.setattr(controller,'_backend_artifact',lambda *a:None)
    class Live:
        def __init__(self,pid):self.pid=pid
        def ppid(self):return {20:10,30:20}[self.pid]
    monkeypatch.setattr(psutil,'Process',Live)
    monkeypatch.setattr(os,'getsid',lambda pid:pid);monkeypatch.setattr(os,'getpgid',lambda pid:pid)
    # Isolate the second lifecycle check after first request admission. A
    # process still living does not permit math after ownership went ambiguous.
    with pytest.raises(ValueError,match='not ready'):
        execution._live_origin(tmp_path,row,proof)


@pytest.fixture
def compiled_backend():
    value=os.environ.get('MODU_PRIVATE_FROZEN_BACKEND')
    if not value:pytest.skip('Actual private compiled backend was not provisioned for this gate')
    binary=Path(value)
    assert binary.is_file() and not binary.is_symlink()
    return binary


@pytest.mark.parametrize('case',['missing','unknown','foreign-parent'])
def test_actual_compiled_dispatch_refuses_before_owned_home(compiled_backend,tmp_path,case):
    """Real compiled boundary only; no successful arithmetic/installer claim."""
    from backend.tests.test_global_migration import owned
    from backend.engine import runtime_update as update
    root,*_=owned(tmp_path)
    receipt=json.loads((compiled_backend.parent/'backend-release.json').read_bytes())
    proof={'schema_version':1,'kind':'backend_claim','challenge':'a'*64,'epoch':'b'*32,'nonce':'c'*32,
        'binding_sha256':'d'*64,'process':{'pid':os.getpid()},'executable':str(compiled_backend),
        'executable_sha256':receipt['executable_sha256'],
        'build_identity_sha256':receipt['inventory']['build_identity_sha256'],'frozen':True}
    request=tmp_path/'request.json'
    request.write_bytes(update._canonical({'schema_version':1,'kind':'owned_frozen_cpu_worker',
        'root':str(root),'request':{},'backend_proof':proof,'capability':{}}))
    args=[str(compiled_backend),'--owned-application-cpu-worker']
    if case!='missing':args+=['--request-file',str(request),'--request-sha256',update._sha(request.read_bytes())]
    if case=='unknown':args+=['--module','backend.main']
    home=tmp_path/'worker-home'
    result=subprocess.run(args,cwd=tmp_path,env={'PATH':os.defpath,'HOME':str(home),'USERPROFILE':str(home),
        'TMPDIR':str(tmp_path),'CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none'},
        capture_output=True,text=True,timeout=30)
    assert result.returncode==2,result.stderr
    assert not home.exists() and not (root/'application-launch-lease.json').exists()
    if case=='foreign-parent':assert 'foreign original backend parent/build' in result.stderr


def _compiled_stack(tmp_path,binary):
    """An exact private compiled artifact in a legacy controlled committed pair.

    The native-shaped entry is a Node fixture, not the packaged Electron app.
    This does not exercise the new staged canary or qualify an OS installer.
    """
    from backend.tests.test_application_launch_execution import cpu_stack,canonical,sha,REPOSITORY
    from backend.tests.test_service_s6_04 import fixture,plan
    from backend.engine.runtime_update import install_update
    values=cpu_stack(tmp_path,deadline_ms=60000)
    root,old,current,project,reviewed,pin=values
    new=fixture(tmp_path,version='1.1.0',key=old['key'],authority=old['authority'])
    new['target']['current_version']='1.0.0'
    prefix='Owned CPU.app/Contents/Resources/backend/'
    compiler="const fs=require('fs'),ts=require('typescript');process.stdout.write(ts.transpileModule(fs.readFileSync(process.argv[1],'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText)"
    bridge=subprocess.check_output(['node','-e',compiler,str(REPOSITORY/'src/main/applicationLaunch.ts')])
    main="""const fs=require('fs'),path=require('path'),{spawn}=require('child_process');
const {authenticateMainLaunch}=require('../Resources/bridge.cjs');
(async()=>{const launch=await authenticateMainLaunch();
fs.writeFileSync(path.join(launch.projects,'compiled-main-admitted.json'),JSON.stringify({main:process.pid}));
await new Promise((resolve,reject)=>{const until=Date.now()+180000;const timer=setInterval(()=>{
if(fs.existsSync(path.join(launch.projects,'compiled-release-backend.flag'))){clearInterval(timer);resolve();}
else if(Date.now()>until){clearInterval(timer);reject(Error('Owned fixture startup barrier expired'));}},25);});
const executable=path.join(path.dirname(process.argv[1]),'../Resources/backend/vision_ai_backend');
const inventory=JSON.parse(fs.readFileSync(path.join(path.dirname(executable),'backend-release.json'))).inventory;
const home=path.join(launch.root,'compiled-runtime-home'),cache=path.join(home,'cache'),scratch=path.join(home,'tmp');
for(const folder of [home,cache,scratch])fs.mkdirSync(folder,{recursive:true,mode:0o700});
const errors=fs.openSync(path.join(launch.projects,'compiled-backend-error.txt'),'wx');
const backend=spawn(executable,['--port','0','--device','cpu','--project-dir',launch.projects,'--shared-auth-dir',launch.auth],
{cwd:launch.root,env:launch.backendEnvironment({PATH:'/usr/bin:/bin',HOME:home,USERPROFILE:home,TMPDIR:scratch,TMP:scratch,TEMP:scratch,
XDG_CACHE_HOME:cache,XDG_CONFIG_HOME:path.join(home,'config'),TORCH_HOME:path.join(cache,'torch'),HF_HOME:path.join(cache,'hf'),
MPLCONFIGDIR:path.join(cache,'matplotlib'),YOLO_CONFIG_DIR:path.join(home,'yolo'),HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1',HF_DATASETS_OFFLINE:'1',
CUDA_VISIBLE_DEVICES:'',NVIDIA_VISIBLE_DEVICES:'none',OMP_NUM_THREADS:'1',MKL_NUM_THREADS:'1',OPENBLAS_NUM_THREADS:'1'}),stdio:['pipe','ignore',errors,'pipe']});fs.closeSync(errors);
await launch.bindBackend(backend,executable,inventory.build_identity_sha256);
fs.writeFileSync(path.join(launch.projects,'compiled-stack-ready.json'),JSON.stringify({main:process.pid,backend:backend.pid}));setInterval(()=>{},100);
})().catch(error=>{fs.writeFileSync(path.join(process.env.VISION_AI_STUDIO_USER_DATA_DIR,'projects','compiled-stack-error.txt'),String(error.stack));process.exit(3);});"""
    extras={'Owned CPU.app/Contents/MacOS/owned-main':('#!'+shutil.which('node')+'\n'+main).encode(),
            'Owned CPU.app/Contents/Resources/bridge.cjs':bridge}
    rows=[];links=[];files=[]
    for path in sorted(binary.parent.rglob('*')):
        name=prefix+path.relative_to(binary.parent).as_posix()
        if path.is_symlink():links.append({'path':name,'target':os.readlink(path)})
        elif path.is_file():
            digest=hashlib.sha256()
            with path.open('rb') as source:
                for chunk in iter(lambda:source.read(1024**2),b''):digest.update(chunk)
            rows.append({'path':name,'size':path.stat().st_size,'sha256':digest.hexdigest(),
                         'executable':bool(path.stat().st_mode&0o111)});files.append((name,path))
    rows += [{'path':name,'size':len(raw),'sha256':sha(raw),'executable':'/MacOS/' in name} for name,raw in extras.items()]
    manifest={'schema_version':2,'version':'1.1.0','platform':new['target']['platform'],
        'arch':new['target']['arch'],'entrypoint':'Owned CPU.app/Contents/MacOS/owned-main','files':rows,'links':links}
    archive=new['directory']/'application.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as writer:
        writer.writestr('portable-application.json',canonical(manifest))
        for name,path in files:writer.write(path,name,compresslevel=0 if name in (
            prefix+'_internal/torch/lib/libtorch_cpu.dylib',
            prefix+'_internal/_polars_runtime_32/_polars_runtime.abi3.so',
            prefix+'_internal/llvmlite/binding/libllvmlite.dylib') else 3)
        for name,raw in extras.items():writer.writestr(name,raw)
        for row in links:
            info=zipfile.ZipInfo(row['path']);info.external_attr=(0o120777<<16);writer.writestr(info,row['target'].encode())
    digest=hashlib.sha256()
    with archive.open('rb') as source:
        for chunk in iter(lambda:source.read(1024**2),b''):digest.update(chunk)
    new['payload'].update(sha256=digest.hexdigest(),size=archive.stat().st_size)
    new['payload']['artifacts'][0].update(sha256=digest.hexdigest(),size=archive.stat().st_size)
    new['sign'](new['payload']);current=install_update(root,plan(root,new))
    return root,new,current,project,reviewed,pin


@pytest.mark.skipif(sys.platform!='darwin',reason='This controlled native-shaped compiled fixture is macOS only')
def test_actual_compiled_cpu_ocr_in_original_epoch_keeps_all_native_quality_tree_claims_false(compiled_backend,tmp_path):
    from backend.tests.test_application_launch_execution import stop_stack,wait_receipt,sha,PROJECT_ID
    from backend.tests.test_application_launch_controller import controller_arguments
    # Large controlled installation fixtures belong on a provisioned external
    # temp volume. No current app/user-home/root or real publisher is touched.
    values=_compiled_stack(tmp_path,compiled_backend)
    root,value,current,project,reviewed,pin=values
    # Listed, independently hash-pinned exported Python still must never run.
    package=project/reviewed['package_path'];marker=tmp_path/'exported-runtime-executed'
    poison=('from pathlib import Path\nPath('+repr(str(marker))+').write_text("exported code executed")\nraise RuntimeError("exported runtime")\n').encode()
    target=package/'backend/engine/flow_package_runtime.py';target.write_bytes(poison)
    manifest=json.loads((package/'manifest.json').read_bytes())
    row=next(row for row in manifest['files'] if row['path']=='backend/engine/flow_package_runtime.py')
    row.update(size=len(poison),sha256=sha(poison))
    from backend.tests.test_application_launch_execution import canonical
    (package/'manifest.json').write_bytes(canonical(manifest));reviewed['package_manifest_sha256']=sha(canonical(manifest))
    (project/'delivery/launch-known-image/plan.json').write_bytes(canonical(reviewed));pin=sha(canonical(reviewed))
    command=[str(compiled_backend),'--owned-application-launch-controller',*controller_arguments(root,value,current),
        '--cpu-known-image-workspace-id',reviewed['workspace_id'],'--cpu-known-image-project-id',PROJECT_ID,
        '--cpu-known-image-plan-sha256',pin]
    child=subprocess.Popen(command,cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env={'PATH':os.defpath,'TMPDIR':str(tmp_path),'HOME':str(tmp_path/'controller-home'),
             'CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none'},start_new_session=True)
    try:
        assert select.select([child.stdout],[],[],210)[0],'No compiled controller acknowledgement'
        raw=child.stdout.readline(65537)
        if not raw:
            child.wait(timeout=10);pytest.fail('Compiled controller refused: '+child.stderr.read(4096).decode())
        ack=json.loads(raw);assert ack['status']=='starting',ack
        child.stdout.close();child.stderr.close()
        until=time.monotonic()+120
        while not (root/'projects/compiled-main-admitted.json').exists() and time.monotonic()<until:
            assert child.poll() is None;time.sleep(.05)
        assert (root/'projects/compiled-main-admitted.json').exists()
        directory=root/'.application-launches'/ack['nonce']
        before={str(path):path.read_bytes() for path in directory.iterdir() if path.is_file()}
        pointer=root/'application-launch-lease.json';before[str(pointer)]=pointer.read_bytes()
        for _ in range(3):
            observed=subprocess.run([str(compiled_backend),'--owned-application-launch-controller','--inspect',
                *controller_arguments(root,value,current)],cwd=tmp_path,
                env={'PATH':os.defpath,'HOME':str(tmp_path/'observer-home'),'TMPDIR':str(tmp_path),
                    'CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none'},capture_output=True,timeout=60)
            assert observed.returncode==0,observed.stderr
            assert json.loads(observed.stdout)['status']=='starting'
            assert {name:Path(name).read_bytes() for name in before}==before
            assert {str(path) for path in directory.iterdir() if path.is_file()}==set(before)-{str(pointer)}
        (root/'projects/compiled-release-backend.flag').write_bytes(b'owned fixture release')
        # The fixture starts observing before compiled backend initialization
        # and repeated full signed-artifact verification. Its observation
        # budget includes those phases; production frame/CPU deadlines stay
        # at 210 seconds/60,000ms respectively.
        path,receipt=wait_receipt(root,ack['nonce'],child,timeout=360)
        assert receipt['backend_frozen'] is True
        assert receipt['execution_scope']=='controlled_frozen_backend'
        assert receipt['actual_cpu_execution_verified'] is True and receipt['owned_backend_execution_origin_verified'] is True
        assert receipt['semantic_output']['recognized_texts']==['A']
        assert receipt['semantic_output']['final_verdict']=='OK'
        assert sha((project/receipt['output_path']).read_bytes())==receipt['output_sha256']
        assert not marker.exists()
        for name in ('actual_application_inference_verified','native_app_handshake_verified','model_quality_approved',
                     'release_ready','worker_process_tree_exit_verified'):assert receipt[name] is False
        # Actual compiled observers validate the retained proof without
        # repeating arithmetic, repairing partial publication, or changing
        # any original durable ownership bytes.
        observation_command=[str(compiled_backend),'--owned-application-launch-controller','--inspect-cpu-execution',
            *controller_arguments(root,value,current),'--expected-launch-nonce',ack['nonce'],
            '--cpu-known-image-workspace-id',reviewed['workspace_id'],'--cpu-known-image-project-id',PROJECT_ID,
            '--cpu-known-image-plan-sha256',pin]
        def observe_cpu():
            return subprocess.run(observation_command,cwd=tmp_path,
                env={'PATH':os.defpath,'HOME':str(tmp_path/'observer-home'),'TMPDIR':str(tmp_path),
                     'CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none'},capture_output=True,timeout=90)
        sealed={str(item):item.read_bytes() for item in directory.iterdir() if item.is_file()}
        sealed[str(pointer)]=pointer.read_bytes()
        for _ in range(2):
            observed=observe_cpu();assert observed.returncode==0,observed.stderr
            readback=json.loads(observed.stdout)
            assert readback['status']=='verified' and readback['execution_scope']=='controlled_frozen_backend'
            assert readback['actual_cpu_execution_verified'] is True
            assert readback['receipt_sha256']==sha(path.read_bytes())
            assert {name:Path(name).read_bytes() for name in sealed}==sealed
            assert {str(item) for item in directory.iterdir() if item.is_file()}==set(sealed)-{str(pointer)}
        output=project/receipt['output_path'];output_raw=output.read_bytes();receipt_raw=path.read_bytes()
        for changed,raw in ((output,output_raw),(path,receipt_raw)):
            changed.write_bytes(raw+b'!')
            damaged={name:Path(name).read_bytes() for name in sealed}
            observed=observe_cpu();assert observed.returncode==2,observed.stdout
            assert json.loads(observed.stdout)['status']=='refused'
            assert {name:Path(name).read_bytes() for name in damaged}==damaged
            assert changed.read_bytes()==raw+b'!'
            changed.write_bytes(raw)
        assert {name:Path(name).read_bytes() for name in sealed}==sealed
        assert output.read_bytes()==output_raw and not (tmp_path/'observer-home').exists()
        (tmp_path/'compiled-execution-proof.json').write_bytes(canonical({'schema_version':1,
            'receipt':receipt,'receipt_sha256':sha(receipt_raw),'original_lease_files':
                {str(Path(name).relative_to(root)):sha(raw) for name,raw in sealed.items()},
            'startup_inspection_repeats':3,'sealed_receipt_inspection_repeats':2,
            'changed_output_refused':True,'partial_receipt_refused':True,'exported_python_executed':False,
            'native_application_verified':False,'full_installer_verified':False,'publisher_verified':False,
            'model_quality_approved':False,'process_tree_exit_verified':False}))
    finally:
        if (root/'application-launch-lease.json').exists():stop_stack(child,root)
        else:
            if child.poll() is None:child.terminate()
            child.wait(timeout=10)
