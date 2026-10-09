"""Staged compiled worker controls; these never qualify a native application."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import subprocess
import time
import zipfile

import pytest

from backend.engine import runtime_update as update
from scripts import build_backend_binary as build

ROOT=Path(__file__).resolve().parents[2]
PROTOCOL='owned_staged_canary_cpu_execution_protocol'


def adapter():return importlib.import_module('backend.engine.staged_canary_frozen_execution')


def candidate_metadata(tmp_path,monkeypatch):
    """Actual reviewed source rows with inert bytes: metadata only, never math."""
    monkeypatch.setattr(build,'DEPENDENCIES',())
    monkeypatch.setattr(build,'check_pyinstaller',lambda:False)
    inventory=build.dependency_inventory(ROOT)
    prefix='Owned Canary.app/Contents/Resources/backend/'
    files={prefix+'vision_ai_backend':b'inert compiled metadata fixture',
        'Owned Canary.app/Contents/MacOS/studio':b'#!/bin/sh\nexit 0\n'}
    for row in inventory['resources']:
        if row['path'].startswith('backend/') or row['path']=='scripts/frozen_backend_entry.py':
            files[prefix+'_internal/'+row['path']]=(ROOT/row['path']).read_bytes()
    files[prefix+'_internal/backend-build-inventory.json']=update._canonical(inventory)
    receipt={'schema_version':1,'executable':'vision_ai_backend',
        'executable_sha256':update._sha(files[prefix+'vision_ai_backend']),
        'inventory':inventory,'signature_status':'unverified','acceptance':None,
        'license_texts':{},'files':[]}
    files[prefix+'backend-release.json']=update._canonical(receipt)
    application=tmp_path/'application';application.mkdir()
    rows=[]
    for name,raw in files.items():
        path=application/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        rows.append({'path':name,'sha256':update._sha(raw),'size':len(raw),
            'executable':name.endswith('/vision_ai_backend') or '/MacOS/' in name})
    manifest={'schema_version':2,'version':'1.0.0','platform':'darwin','arch':'arm64',
        'entrypoint':'Owned Canary.app/Contents/MacOS/studio','files':rows,'links':[]}
    return application,manifest,receipt,prefix


def test_current_exact_build_advertises_separate_staged_worker_protocol(monkeypatch):
    monkeypatch.setattr(build,'DEPENDENCIES',())
    monkeypatch.setattr(build,'check_pyinstaller',lambda:False)
    inventory=build.dependency_inventory(ROOT)
    assert inventory.get(PROTOCOL)==1
    assert type(inventory[PROTOCOL]) is int
    build.validate_inventory(inventory)


@pytest.mark.parametrize('change',['old','dormant','wrong-target','late','missing-resource','duplicate-resource','boolean','float'])
def test_staged_capability_requires_early_fixed_dispatch_and_exact_resources(tmp_path,monkeypatch,change):
    from backend.tests.test_owned_launch_build_inventory import checkout,DISPATCH,PATHS
    root=checkout(tmp_path,monkeypatch)
    cpu="""    if len(sys.argv)>1 and sys.argv[1]=='--owned-application-cpu-worker':
        from backend.engine.application_launch_execution import frozen_worker_main
        raise SystemExit(frozen_worker_main(sys.argv[2:]))
"""
    staged="""    if len(sys.argv)>1 and sys.argv[1]=='--owned-staged-canary-cpu-worker':
        from backend.engine.staged_canary_frozen_execution import frozen_worker_main
        raise SystemExit(frozen_worker_main(sys.argv[2:]))
"""
    base=DISPATCH.replace('    from backend.engine.application_launch_handshake',cpu+'    from backend.engine.application_launch_handshake')
    entry=base.replace('    from backend.engine.application_launch_handshake',staged+'    from backend.engine.application_launch_handshake')
    for name in build.OWNED_STAGED_CANARY_RESOURCES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():path.write_text('# Exact staged resource fixture\n')
    if change=='old':entry=base
    elif change=='dormant':entry=base+'\ndef unused():\n'+staged
    elif change=='wrong-target':entry=entry.replace('staged_canary_frozen_execution import frozen_worker_main','application_launch_execution import frozen_worker_main')
    elif change=='late':entry=base+staged
    (root/PATHS[0]).write_text(entry)
    if change=='missing-resource':(root/'backend/engine/staged_canary_frozen_execution.py').unlink()
    inventory=build.dependency_inventory(root)
    if change in {'old','dormant','wrong-target','late','missing-resource'}:
        assert PROTOCOL not in inventory
    else:
        assert inventory[PROTOCOL]==1
        if change=='duplicate-resource':
            inventory['resources'].append(copy.deepcopy(next(row for row in inventory['resources'] if row['path']=='backend/engine/staged_canary_frozen_execution.py')))
        else:inventory[PROTOCOL]=True if change=='boolean' else 1.0
        with pytest.raises(ValueError):build.validate_inventory(inventory)


def test_compiled_metadata_binding_is_derived_and_closed_without_acceptance(tmp_path,monkeypatch):
    application,manifest,receipt,prefix=candidate_metadata(tmp_path,monkeypatch)
    value=adapter().candidate_binding(manifest,application=application)
    assert set(value)=={'protocol','executable_path','executable_sha256','build_receipt_path',
        'build_receipt_sha256','build_identity_sha256','runtime_source_sha256','resource_inventory_sha256'}
    assert value['protocol']==1 and value['executable_path']==prefix+'vision_ai_backend'
    assert value['build_receipt_path']==prefix+'backend-release.json'
    assert value['build_identity_sha256']==receipt['inventory']['build_identity_sha256']
    from backend.engine.application_launch_execution import runtime_source_identity
    assert value['runtime_source_sha256']==runtime_source_identity()


@pytest.mark.parametrize('damage',['absent','boolean','unknown','missing-row','unlisted','drift','duplicate'])
def test_candidate_capability_and_resource_drift_refuse(tmp_path,monkeypatch,damage):
    application,manifest,receipt,prefix=candidate_metadata(tmp_path,monkeypatch)
    inventory=receipt['inventory']
    if damage=='absent':inventory.pop(PROTOCOL,None)
    elif damage=='boolean':inventory[PROTOCOL]=True
    elif damage=='unknown':inventory[PROTOCOL]=2
    elif damage=='missing-row':inventory['resources']=[row for row in inventory['resources'] if row['path']!='backend/engine/staged_canary_frozen_execution.py']
    elif damage=='duplicate':inventory['resources'].append(copy.deepcopy(inventory['resources'][-1]))
    else:
        name=prefix+'_internal/backend/engine/ocr.py'
        if damage=='drift':(application/name).write_bytes(b'changed')
        else:
            name=prefix+'_internal/backend/unlisted.py';(application/name).write_bytes(b'unlisted')
            manifest['files'].append({'path':name,'size':8,'sha256':update._sha(b'unlisted'),'executable':False})
    inventory['build_identity_sha256']=update._sha(update._canonical({k:v for k,v in inventory.items() if k!='build_identity_sha256'}))
    for name,raw in ((prefix+'backend-release.json',update._canonical(receipt)),
                     (prefix+'_internal/backend-build-inventory.json',update._canonical(inventory))):
        (application/name).write_bytes(raw)
        row=next(row for row in manifest['files'] if row['path']==name);row.update(size=len(raw),sha256=update._sha(raw))
    with pytest.raises(ValueError):adapter().candidate_binding(manifest,application=application)


@pytest.mark.parametrize('value',['0','2','03','8193','-1','foreign'])
def test_fixed_staged_descriptor_refuses_unknown_numbers(value,tmp_path):
    with pytest.raises(ValueError):adapter()._transport(value)
    assert list(tmp_path.iterdir())==[]


def test_source_process_cannot_claim_compiled_staged_worker(tmp_path,monkeypatch):
    monkeypatch.delattr(sys,'frozen',raising=False)
    assert adapter().frozen_worker_main(['--control-fd','3'])==2
    assert list(tmp_path.iterdir())==[]


def test_staged_parser_rejects_arbitrary_paths_before_inventory(monkeypatch):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    module=adapter();monkeypatch.setattr(module,'_inventory',lambda:pytest.fail('Unrecognized argv reached inventory'))
    with pytest.raises(SystemExit) as stopped:
        module.frozen_worker_main(['--control-fd','3','--module','backend.main'])
    assert stopped.value.code==2


def test_anonymous_transport_is_not_inherited_by_further_children():
    parent,child=socket.socketpair()
    fd=child.detach();os.set_inheritable(fd,True)
    try:
        bound=adapter()._transport(str(fd))
        assert bound.family==socket.AF_UNIX and not os.get_inheritable(fd)
        bound.close()
    finally:parent.close()


def test_worker_cannot_treat_unlocked_installation_as_parent_exclusive(tmp_path):
    lock=tmp_path/'migration_admission.lock';lock.write_bytes(b'');lock.chmod(0o600)
    st=lock.stat();identity={'device':st.st_dev,'inode':st.st_ino}
    with pytest.raises(ValueError,match='exclusive'):
        adapter()._probe_exclusive(tmp_path,identity)
    # The acquired probe was released, so a later owner can acquire its lock.
    import fcntl
    with lock.open('rb') as reader:
        fcntl.flock(reader,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(reader,fcntl.LOCK_UN)


def test_exclusive_probe_is_supplemental_and_exact(tmp_path):
    from backend.engine.migration_guard import maintenance_guard
    lock=tmp_path/'migration_admission.lock'
    with maintenance_guard(tmp_path,exclusive=True):
        st=lock.stat();identity={'device':st.st_dev,'inode':st.st_ino}
        adapter()._probe_exclusive(tmp_path,identity)
        with pytest.raises(ValueError):adapter()._probe_exclusive(tmp_path,{**identity,'inode':st.st_ino+1})


def test_original_installer_identity_rejects_changed_birth_command_and_parent():
    module=adapter();installer=module._installer_identity()
    module._validate_installer(installer,expected_pid=os.getpid())
    for key,value in [('created_at',installer['process']['created_at']+1),('command_sha256','0'*64),('pid',os.getpid()+100000)]:
        changed=copy.deepcopy(installer);changed['process'][key]=value
        with pytest.raises(ValueError):module._validate_installer(changed,expected_pid=os.getpid())


def test_staged_math_never_borrows_a_post_cutover_pointer(tmp_path,monkeypatch):
    from backend.tests.test_global_migration import owned
    root,*_=owned(tmp_path)
    requirement={'previous_application':None,'previous_database':None}
    module=adapter();module._previous_pointers(root,requirement)
    (root/update.ACTIVE).write_bytes(update._canonical({'foreign':'pointer'}))
    with pytest.raises(ValueError,match='before.*pointer|previous.*pointer'):
        module._previous_pointers(root,requirement)


def test_an_unsealed_or_extra_staged_request_refuses_before_mutation(tmp_path):
    module=adapter()
    for value in ({},{'schema_version':True,'kind':'staged_frozen_canary_request'},
                  {'schema_version':1,'kind':'owned_frozen_cpu_worker','module':'backend.main'}):
        with pytest.raises(ValueError):module._request_shape(value)
    assert list(tmp_path.iterdir())==[]


def test_staged_proof_cannot_claim_origin_or_quality(tmp_path):
    module=adapter()
    with pytest.raises(ValueError):module._proof_shape({'schema_version':1,'native_application_verified':True})


@pytest.mark.parametrize('damage',['source','fifo','named','internet'])
def test_staged_transport_has_no_named_ipc_or_network_authority(tmp_path,damage):
    module=adapter()
    short=None
    if damage=='source':
        path=tmp_path/'file';path.write_bytes(b'{}');fd=os.open(path,os.O_RDONLY)
    elif damage=='fifo':
        path=tmp_path/'fifo';os.mkfifo(path);fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK)
    elif damage=='named':
        import tempfile
        short=tempfile.TemporaryDirectory(prefix='scfd-',dir='/tmp');name=str(Path(short.name)/'socket')
        listener=socket.socket(socket.AF_UNIX);listener.bind(name);listener.listen()
        client=socket.socket(socket.AF_UNIX);client.connect(name);server,_=listener.accept();fd=server.detach()
    else:
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
        client=socket.socket();client.connect(listener.getsockname());server,_=listener.accept();fd=server.detach()
    try:
        with pytest.raises(ValueError):module._transport(str(fd))
    finally:
        try:os.close(fd)
        except OSError:pass
        if damage in ('named','internet'):client.close();listener.close()
        if short is not None:short.cleanup()


def test_unrecognized_private_capsule_activity_cannot_be_ignored(tmp_path):
    module=adapter();(tmp_path/'unknown-writer.json').write_bytes(b'{}')
    with pytest.raises(ValueError,match='private.*member|unknown.*activity'):
        module._private_members(tmp_path)


def test_staged_worker_derives_the_same_private_foundation_home_without_caller_authority(tmp_path,monkeypatch):
    from backend.engine.staged_update_canary import _worker_environment
    home,cache,scratch=(tmp_path/name for name in ('home','cache','tmp'))
    monkeypatch.setenv('CFFIXED_USER_HOME',str(tmp_path/'foreign-foundation-home'))
    environment=_worker_environment(home,cache,scratch)
    assert environment['HOME']==str(home) and environment['CFFIXED_USER_HOME']==str(home)
    assert environment['NVIDIA_VISIBLE_DEVICES']=='none' and environment['CUDA_VISIBLE_DEVICES']==''
    assert not any(name in environment for name in ('VISION_APPLICATION_LAUNCH_FD','VISION_APPLICATION_BACKEND_FD','PYTHONPATH','DYLD_INSERT_LIBRARIES'))


@pytest.mark.parametrize('damage',['none','source','foreign','public','linked','nonempty','changing','other-key'])
def test_pinned_frozen_mpl_hook_is_confined_before_fixed_environment_restore(tmp_path,monkeypatch,damage):
    """The bundled hook's empty private temp directory grants no path authority."""
    from types import SimpleNamespace
    from backend.engine import staged_update_canary as canary
    module=adapter();private=tmp_path/'private';private.mkdir(mode=0o700)
    for name in ('home','cache','tmp'):(private/name).mkdir(mode=0o700)
    expected=canary._worker_environment(private/'home',private/'cache',private/'tmp')
    for name,value in expected.items():monkeypatch.setenv(name,value)
    hook=private/'tmp/hook-created';hook.mkdir(mode=0o700)
    monkeypatch.setattr(sys,'frozen',damage!='source',raising=False)
    if damage=='foreign':hook=tmp_path/'foreign';hook.mkdir(mode=0o700)
    elif damage=='public':hook.chmod(0o755)
    elif damage=='linked':
        foreign=tmp_path/'foreign';foreign.mkdir(mode=0o700);hook.rmdir();hook.symlink_to(foreign)
    elif damage=='nonempty':(hook/'unrecognized-writer').write_bytes(b'x')
    elif damage=='other-key':monkeypatch.setenv('CUDA_VISIBLE_DEVICES','0')
    elif damage=='changing':
        original=os.listdir
        def changing(fd):
            names=original(fd);(hook/'late-writer').write_bytes(b'x');return names
        monkeypatch.setattr(module,'os',SimpleNamespace(**{**vars(os),'listdir':changing}))
    monkeypatch.setenv('MPLCONFIGDIR',str(hook))
    if damage=='none':
        module._validate_worker_environment(private)
        assert os.environ['MPLCONFIGDIR']==expected['MPLCONFIGDIR']
        assert hook.is_dir() and not list(hook.iterdir())
        assert not (private/'cache/matplotlib').exists()
    else:
        with pytest.raises(ValueError):module._validate_worker_environment(private)
        assert os.environ['MPLCONFIGDIR']==str(hook)


@pytest.mark.parametrize('damage',['environment','public-home','linked-home','closed-channel','replayed-frame',
    'closed-during-validation','parent-lost-during-validation','foreign-mpl','public-mpl','linked-mpl'])
def test_released_semantic_import_boundary_is_after_private_worker_admission(tmp_path,monkeypatch,damage):
    """Controlled prior authority gates isolate the real final admission order.

    This does not emulate compiled math or prove a release. The package policy
    boundary deliberately fails if any ML/semantic validator is reached.
    """
    from types import SimpleNamespace
    from backend.engine import staged_update_canary as canary,application_launch_execution as execution
    from backend.engine.migration_guard import maintenance_guard
    module=adapter();application,manifest,receipt,_=candidate_metadata(tmp_path,monkeypatch)
    binding=module.candidate_binding(manifest,application=application)
    root=tmp_path/'owned';root.mkdir();identifier='a'*32;migration='b'*32
    attempt=root/'attempt';attempt.mkdir();private=attempt/'private';private.mkdir(mode=0o700)
    for name in ('home','cache','tmp'):(private/name).mkdir(mode=0o700)
    cap={'plan':{name:None for name in execution.PLAN_FIELDS},'plan_sha256':'c'*64,
        'project_path':str(root/'projects/original'),'scope_key':'d'*32}
    cap['plan'].update(runtime_source_sha256=binding['runtime_source_sha256'],release_policy={'sha256':'e'*64})
    requirement={'previous_application':None,'previous_database':None}
    requirement_sha=canary._write_sealed(attempt/canary.REQUIREMENT,requirement)
    record={'status':'database_prepared','update_id':identifier,'migration_id':migration,
        'canary_execution_protocol':2,'canary_requirement_sha256':requirement_sha,'canary_receipt_sha256':None,
        'canary_worker_binding':binding,'canary_capability_sha256':update._sha(update._canonical(cap)),
        'canary':{'workspace_id':'1'*32,'project_id':'2'*32,'plan_sha256':'c'*64}}
    request={'schema_version':1,'kind':'staged_frozen_canary_request','root':str(root),'update_id':identifier,
        'migration_id':migration,'requirement_sha256':requirement_sha,'nonce':'3'*32,'epoch':'4'*32,'challenge':'5'*64,
        'installer':{},'worker_binding':binding,'capability':cap,'lock_identity':{}}
    update._write(root/update.PENDING,{'schema_version':1,'installation_id':'6'*32,'update_id':identifier})
    journal=root/'.global-migrations'/migration/'journal.json';journal.parent.mkdir(parents=True);update._write(journal,{})
    local_update=SimpleNamespace(**vars(update));local_canary=SimpleNamespace(**vars(canary))
    local_update._root=lambda path:(root,{'installation_id':'6'*32})
    local_update._validated_intent=lambda path,identity:(record,None,manifest)
    local_canary._directory=lambda path,row:attempt
    local_canary.expected_requirement=lambda *args:requirement
    monkeypatch.setattr(module,'_helpers',lambda:(local_update,local_canary,execution))
    monkeypatch.setattr(module,'_application',lambda *args:application)
    monkeypatch.setattr(module,'_inventory',lambda:(receipt['inventory'],binding['runtime_source_sha256']))
    state={'lost':False}
    def validate_parent(*args,**kwargs):
        if state['lost']:raise ValueError('Original installer disappeared during captured semantic validation')
    monkeypatch.setattr(module,'_validate_installer',validate_parent)
    monkeypatch.setattr(sys,'executable',str(application/binding['executable_path']))
    monkeypatch.setattr(module,'_private_inputs',lambda *args:pytest.fail('Released package semantic/ML import boundary reached before private worker admission'))
    for name,value in canary._worker_environment(private/'home',private/'cache',private/'tmp').items():monkeypatch.setenv(name,value)
    if damage=='environment':monkeypatch.setenv('HOME',str(tmp_path/'foreign-home'))
    elif damage=='public-home':(private/'home').chmod(0o755)
    elif damage=='linked-home':
        foreign=tmp_path/'foreign-home';foreign.mkdir(mode=0o700);(private/'home').rmdir();(private/'home').symlink_to(foreign)
    elif damage in {'foreign-mpl','public-mpl','linked-mpl'}:
        monkeypatch.setattr(sys,'frozen',True,raising=False)
        hook=private/'tmp/hook-created';hook.mkdir(mode=0o700)
        if damage=='foreign-mpl':hook=tmp_path/'foreign-mpl';hook.mkdir(mode=0o700)
        elif damage=='public-mpl':hook.chmod(0o755)
        else:
            foreign=tmp_path/'foreign-mpl';foreign.mkdir(mode=0o700);hook.rmdir();hook.symlink_to(foreign)
        monkeypatch.setenv('MPLCONFIGDIR',str(hook))
    parent,child=socket.socketpair()
    try:
        if damage=='closed-channel':parent.close()
        elif damage=='replayed-frame':parent.sendall(b'{}\n')
        elif damage in {'closed-during-validation','parent-lost-during-validation'}:
            def private_validation(*args):
                if damage=='closed-during-validation':parent.close()
                else:state['lost']=True
                return cap['plan']
            monkeypatch.setattr(module,'_private_inputs',private_validation)
        with maintenance_guard(root,exclusive=True):
            request['lock_identity']=module._lock_identity(root)
            request_sha=canary._write_sealed(private/'worker-request.json',request)
            canary._write_sealed(attempt/canary.INTENT,module._intent(request,request_sha))
            canary._write_sealed(private/'worker-spawn.json',module._spawn(request,request_sha,module._process_identity(os.getpid())))
            st=os.fstat(child.fileno())
            frame={'schema_version':1,'kind':'staged_frozen_canary_challenge','nonce':request['nonce'],'epoch':request['epoch'],
                'challenge':request['challenge'],'request_path':str(private/'worker-request.json'),'request_sha256':request_sha,
                'transport':{'device':st.st_dev,'inode':st.st_ino,'family':'AF_UNIX','type':'SOCK_STREAM','anonymous':True}}
            with pytest.raises(ValueError):module._admit(child,frame)
            assert not (private/'worker-admission.json').exists()
    finally:parent.close();child.close()



def compiled_candidate(tmp_path,binary,*,binary_runtime=False,deadline_ms=60000,layout_schema=2):
    """Real signed onedir, inert main; no Electron or native installer claim."""
    from backend.tests.test_staged_update_canary import source_candidate
    from backend.engine import application_launch_execution as execution
    root,value,_,project,reviewed=source_candidate(tmp_path,deadline_ms=deadline_ms)
    receipt=json.loads((binary.parent/'backend-release.json').read_bytes())
    if binary_runtime:
        rows=[]
        for item in receipt['inventory']['resources']:
            if item['path'].startswith('backend/') and item['path'].endswith('.py'):
                path=binary.parent/'_internal'/item['path']
                rows.append({'path':item['path'],'size':path.stat().st_size,'sha256':item['sha256']})
        reviewed['runtime_source_sha256']=update._sha(update._canonical(sorted(rows,key=lambda row:row['path'])))
    # The exported Python is pinned data, and deliberately cannot be imported.
    package=project/execution.PACKAGE;manifest=json.loads((package/'manifest.json').read_bytes())
    for row in [row for row in manifest['files'] if row['path'].endswith('.py')]:
        path=package/row['path'];path.write_bytes(b"raise RuntimeError('Exported Python must never execute')\n")
        row.update(size=path.stat().st_size,sha256=update._sha(path.read_bytes()))
    (package/'manifest.json').write_bytes(update._canonical(manifest))
    reviewed['package_manifest_sha256']=update._sha((package/'manifest.json').read_bytes())
    (project/execution.PLAN).write_bytes(update._canonical(reviewed))
    spec={'workspace_id':reviewed['workspace_id'],'project_id':reviewed['project_id'],
        'plan_sha256':update._sha((project/execution.PLAN).read_bytes())}
    prefix='Owned Canary.app/Contents/Resources/backend/';rows=[];links=[];files=[]
    digest=lambda path:build.sha256(path)
    for path in sorted(binary.parent.rglob('*')):
        name=prefix+path.relative_to(binary.parent).as_posix()
        if path.is_symlink():links.append({'path':name,'target':os.readlink(path)})
        elif path.is_file():
            rows.append({'path':name,'size':path.stat().st_size,'sha256':digest(path),
                'executable':bool(path.stat().st_mode&0o111)});files.append((name,path))
    entry='Owned Canary.app/Contents/MacOS/studio';inert=b'#!/bin/sh\nexit 0\n'
    rows.append({'path':entry,'size':len(inert),'sha256':update._sha(inert),'executable':True})
    assert type(layout_schema) is int and layout_schema in (1,2)
    application={'schema_version':layout_schema,'version':'1.0.0','platform':value['target']['platform'],
        'arch':value['target']['arch'],'entrypoint':entry,'files':rows}
    if layout_schema==2:application['links']=links
    else:assert not links,'Schema1 portable metadata cannot claim native aliases'
    archive=value['directory']/'application.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as writer:
        writer.writestr('portable-application.json',update._canonical(application));writer.writestr(entry,inert)
        for name,path in files:writer.write(path,name,compresslevel=0 if name in (
            prefix+'_internal/torch/lib/libtorch_cpu.dylib',
            prefix+'_internal/_polars_runtime_32/_polars_runtime.abi3.so',
            prefix+'_internal/llvmlite/binding/libllvmlite.dylib') else 3)
        for row in links:
            info=zipfile.ZipInfo(row['path']);info.external_attr=0o120777<<16;writer.writestr(info,row['target'].encode())
    value['payload'].update(size=archive.stat().st_size,sha256=digest(archive))
    value['payload']['artifacts'][0].update(size=archive.stat().st_size,sha256=value['payload']['sha256'])
    value['sign'](value['payload'])
    return root,value,project,reviewed,spec


@pytest.mark.parametrize('source_target',[{'platform':'darwin','arch':'arm64'}, {'platform':'linux','arch':'x64'}],ids=['darwin-source','linux-source'])
def test_staged_compiled_attempt_intent_blocks_crash_retry_without_spawning(tmp_path,monkeypatch,source_target):
    """Signed inert metadata exercises protocol2 durable dispatch only."""
    from backend.engine import staged_update_canary as canary
    from backend.tests.test_staged_update_canary import pointers
    from backend.tests import test_staged_update_canary as source_fixtures
    from types import SimpleNamespace
    application,_,metadata_receipt,prefix=candidate_metadata(tmp_path,monkeypatch)
    original_source=source_fixtures.source_candidate
    def controlled_source_target(*args,**kwargs):
        values=original_source(*args,**kwargs);value=values[1]
        value['target'].update(source_target);value['payload'].update(source_target)
        # Model only the target classifier for inert metadata. No binary or
        # installer executes, and this is not qualification of the modeled OS.
        monkeypatch.setattr(update,'platform',SimpleNamespace(
            system=lambda:'Darwin' if source_target['platform']=='darwin' else 'Linux',
            machine=lambda:'arm64' if source_target['arch']=='arm64' else 'x86_64'))
        return values
    monkeypatch.setattr(source_fixtures,'source_candidate',controlled_source_target)
    binary=application/(prefix+'vision_ai_backend');binary.chmod(0o755)
    case=tmp_path/'case';case.mkdir()
    layout_schema=2 if source_target['platform']=='darwin' else 1
    root,value,_,_,spec=compiled_candidate(case,binary,layout_schema=layout_schema)
    proposal=update.plan_update(root,value['directory'],value['envelope'],value['authority'],
        pinned_authority_sha256=value['pinned_authority_sha256'],target=value['target'],canary=spec)
    assert proposal.canary_preflight['protocol']==2 and proposal.canary_preflight['status']=='frozen_ready'
    assert {name:value['target'][name] for name in ('platform','arch')}==source_target
    assert {name:value['payload'][name] for name in ('platform','arch')}==source_target
    assert {name:proposal.app[name] for name in ('platform','arch')}==source_target
    with zipfile.ZipFile(value['directory']/'application.zip') as archive:
        signed_application=json.loads(archive.read('portable-application.json'))
    assert {name:signed_application[name] for name in ('platform','arch')}==source_target
    assert signed_application['schema_version']==layout_schema
    if layout_schema==2:assert signed_application['links']==[]
    else:assert 'links' not in signed_application
    assert metadata_receipt['signature_status']=='unverified' and metadata_receipt['acceptance'] is None
    assert all(proposal.canary_preflight[name] is False for name in canary.FLAGS)
    before=pointers(root);module=adapter()
    monkeypatch.setattr(module,'subprocess',SimpleNamespace(Popen=lambda *args,**kwargs:pytest.fail('Interrupted attempt spawned a process')))
    def crash(point):
        if point=='before_canary_spawn':raise KeyboardInterrupt('Owned compiled-attempt intent publication interruption')
    monkeypatch.setattr(canary,'_checkpoint',crash)
    with pytest.raises(KeyboardInterrupt):update.install_update(root,proposal)
    assert pointers(root)==before
    identifier=json.loads((root/update.PENDING).read_bytes())['update_id'];record=update._intent(root,identifier)[0]
    attempt=canary._directory(root,record)
    intent=json.loads((attempt/canary.INTENT).read_bytes())
    assert intent['schema_version']==2 and intent['status']=='spawn_started'
    assert not (attempt/'private/worker-spawn.json').exists() and not (attempt/canary.RECEIPT).exists()
    original={path.relative_to(attempt).as_posix():build.sha256(path) for path in attempt.rglob('*') if path.is_file()}
    monkeypatch.setattr(canary,'_checkpoint',lambda point:None)
    with pytest.raises(ValueError,match='already attempted'):update.recover_update(root,identifier,action='finish')
    with pytest.raises(ValueError,match='attempt|spawn|recovery'):update.recover_update(root,identifier,action='abort')
    assert pointers(root)==before
    assert original=={path.relative_to(attempt).as_posix():build.sha256(path) for path in attempt.rglob('*') if path.is_file()}


def cli_args(binary,root,value,spec,command):
    return [str(binary),'--offline-application-update',command,'--root',str(root),
        '--bundle',str(value['directory']),'--envelope',str(value['envelope']),
        '--authority',str(value['authority']),'--pinned-authority-sha256',value['pinned_authority_sha256'],
        '--target-json',json.dumps(value['target']),'--canary-workspace-id',spec['workspace_id'],
        '--canary-project-id',spec['project_id'],'--canary-plan-sha256',spec['plan_sha256']]


@pytest.fixture
def compiled_backend():
    value=os.environ.get('MODU_STAGED_FROZEN_BACKEND')
    if not value:pytest.skip('Actual private staged compiled backend is not provisioned in this source gate')
    binary=Path(value)
    assert binary.is_file() and not binary.is_symlink()
    return binary


@pytest.mark.parametrize('case',['missing','unknown','invalid-fd'])
def test_actual_compiled_stage_dispatch_refuses_before_home(compiled_backend,tmp_path,case):
    home=tmp_path/'private-home';args=[str(compiled_backend),'--owned-staged-canary-cpu-worker']
    if case=='unknown':args+=['--control-fd','3','--module','backend.main']
    if case=='invalid-fd':args+=['--control-fd','0']
    outcome=subprocess.run(args,cwd=tmp_path,env={'PATH':os.defpath,'HOME':str(home),'USERPROFILE':str(home),
        'TMPDIR':str(tmp_path),'CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none'},
        capture_output=True,text=True,timeout=30)
    assert outcome.returncode==2,outcome.stderr
    assert not home.exists()


def test_actual_compiled_installer_math_is_sealed_before_both_pointer_changes(compiled_backend,tmp_path):
    from backend.tests.test_staged_update_canary import EXPECTED_OUTPUT,pointers
    from backend.engine import staged_update_canary as canary
    root,value,project,reviewed,spec=compiled_candidate(tmp_path,compiled_backend,
        binary_runtime=os.environ.get('MODU_STAGED_OLD_FEATURE_RED')=='1')
    env={'PATH':os.defpath,'HOME':str(tmp_path/'installer-home'),'TMPDIR':str(tmp_path),
        'CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none','OMP_NUM_THREADS':'1'}
    before=pointers(root);preview=subprocess.run(cli_args(compiled_backend,root,value,spec,'preview'),
        cwd=tmp_path,env=env,capture_output=True,text=True,timeout=180)
    assert preview.returncode==0,preview.stdout+preview.stderr
    proposal=json.loads(preview.stdout.strip().splitlines()[-1]);proof=proposal['preactivation_canary']
    assert proof['protocol']==2 and proof['status']=='frozen_ready' and proof['supported'] is True,proof
    assert set(proof)=={'schema_version','protocol','required','policy','status','supported','pins','capability_sha256',
        'candidate_runtime_source_sha256','worker_binding','reason',*canary.FLAGS}
    assert pointers(root)==before and not (root/update.PENDING).exists()
    command=cli_args(compiled_backend,root,value,spec,'install')+['--expected-plan-sha256',proposal['plan_sha256']]
    observed=[]
    with (tmp_path/'install-stdout.txt').open('wb') as stdout,(tmp_path/'install-stderr.txt').open('wb') as stderr:
        child=subprocess.Popen(command,cwd=tmp_path,env=env,stdout=stdout,stderr=stderr)
        try:
            deadline=time.monotonic()+360
            while child.poll() is None:
                assert time.monotonic()<deadline,'Actual compiled installer fixture observation exceeded its bound'
                pending=root/update.PENDING
                if pending.exists():
                    record=update._intent(root,json.loads(pending.read_bytes())['update_id'])[0]
                    if record.get('migration_id'):
                        attempt=canary._directory(root,record)
                        if (attempt/'private/worker-admission.json').exists() and not (attempt/canary.RECEIPT).exists():
                            observed.append({'phase':'compiled_worker_admitted','original_pointers_unchanged':pointers(root)==before})
                        if (attempt/canary.RECEIPT).exists() and pointers(root)==before:
                            observed.append({'phase':'sealed_compiled_math_before_activation','original_pointers_unchanged':True})
                time.sleep(.1)
            child.wait()
        finally:
            # Only this fixture's original new child handle is eligible. All
            # pending journals and private artifacts survive an observer error.
            if child.poll() is None:child.kill()
            child.wait(timeout=10)
    output=(tmp_path/'install-stdout.txt').read_text();errors=(tmp_path/'install-stderr.txt').read_text()
    assert child.returncode==0,output+errors
    installed=json.loads(output.strip().splitlines()[-1]);assert installed['status']=='committed'
    record=update._intent(root,installed['update_id'])[0];attempt=canary._directory(root,record)
    receipt=json.loads((attempt/canary.RECEIPT).read_bytes());result=json.loads((attempt/'canary-result.json').read_bytes())
    assert receipt['schema_version']==2 and receipt['execution_scope']=='staged_frozen_runtime_worker'
    assert result['semantic_output']==EXPECTED_OUTPUT and result['installer']['frozen'] is True
    assert result['installer']['process']['pid']==child.pid
    assert result['worker']['environment']['CUDA_VISIBLE_DEVICES']==''
    assert result['worker']['environment']['NVIDIA_VISIBLE_DEVICES']=='none'
    assert all(receipt[name] is False for name in canary.FLAGS)
    assert observed and all(row['original_pointers_unchanged'] for row in observed)
    assert any(row['phase']=='sealed_compiled_math_before_activation' for row in observed)
    assert not (project/'delivery/launch-known-image/results').exists()
    # The retained receipt is data evidence after the original installer exits;
    # it authorizes neither a new parent nor another use of the private capsule.
    _,_,manifest=update._validated_intent(root,installed['update_id'])
    database=update.migration._journal(root,record['migration_id'])[1]
    assert adapter().validate_receipt(root,record,database,manifest)==receipt
    request_path=attempt/'private/worker-request.json';request=json.loads(request_path.read_bytes())
    private=attempt/'private'
    retained={path.relative_to(private).as_posix():build.sha256(path)
        for path in private.rglob('*') if path.is_file()}
    parent,sock=socket.socketpair();worker=None
    try:
        from backend.engine.application_launch_handshake import send_frame
        worker=subprocess.Popen([str(root/update.GENERATIONS/installed['update_id']/'application'/proof['worker_binding']['executable_path']),
            '--owned-staged-canary-cpu-worker','--control-fd',str(sock.fileno())],
            pass_fds=(sock.fileno(),),close_fds=True,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            cwd=private,env=canary._worker_environment(private/'home',private/'cache',private/'tmp'))
        st=os.fstat(sock.fileno());sock.close()
        send_frame(parent,{'schema_version':1,'kind':'staged_frozen_canary_challenge',
            **{name:request[name] for name in ('challenge','nonce','epoch')},'request_path':str(request_path),
            'request_sha256':update._sha(request_path.read_bytes()),
            'transport':{'device':st.st_dev,'inode':st.st_ino,'family':'AF_UNIX','type':'SOCK_STREAM','anonymous':True}})
        _,refusal=worker.communicate(timeout=30)
        assert worker.returncode==2 and b'original installer identity' in refusal,refusal
    finally:
        parent.close();sock.close()
        if worker is not None:
            if worker.poll() is None:worker.kill()
            worker.wait(timeout=10)
    assert retained=={path.relative_to(private).as_posix():build.sha256(path)
        for path in private.rglob('*') if path.is_file()}
    assert all(value is not None for value in pointers(root).values())
    artifact={'scope':'controlled_compiled_installer_and_staged_cpu_worker','observations':observed,
        'review':proof,'receipt':receipt,'semantic_output':result['semantic_output'],
        'canary_requirement_sha256':record['canary_requirement_sha256'],'canary_receipt_sha256':record['canary_receipt_sha256'],
        'all_external_acceptance_false':True,'source_runtime_sha256':reviewed['runtime_source_sha256'],
        'post_cutover_receipt_readback_verified':True,'foreign_parent_capsule_replay_refused':True,
        'private_capsule_bytes_unchanged_after_refusal':True}
    (tmp_path/'compiled-staged-canary-proof.json').write_bytes(update._canonical(artifact))


def test_actual_compiled_deadline_keeps_original_pair_and_never_replays(compiled_backend,tmp_path):
    from backend.tests.test_staged_update_canary import pointers
    from backend.engine import staged_update_canary as canary
    root,value,_,_,spec=compiled_candidate(tmp_path,compiled_backend,deadline_ms=1)
    env={'PATH':os.defpath,'HOME':str(tmp_path/'installer-home'),'TMPDIR':str(tmp_path),
        'CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none','OMP_NUM_THREADS':'1'}
    proposal=update.plan_update(root,value['directory'],value['envelope'],value['authority'],
        pinned_authority_sha256=value['pinned_authority_sha256'],target=value['target'],canary=spec)
    assert proposal.canary_preflight['protocol']==2 and proposal.canary_preflight['status']=='frozen_ready'
    before=pointers(root)
    command=cli_args(compiled_backend,root,value,spec,'install')+['--expected-plan-sha256',update.review_update(proposal)['plan_sha256']]
    outcome=subprocess.run(command,cwd=tmp_path,env=env,capture_output=True,text=True,timeout=360)
    (tmp_path/'deadline-stdout.txt').write_text(outcome.stdout);(tmp_path/'deadline-stderr.txt').write_text(outcome.stderr)
    assert outcome.returncode==2 and 'deadline' in (outcome.stdout+outcome.stderr).lower(),outcome.stdout+outcome.stderr
    assert pointers(root)==before and (root/update.PENDING).exists()
    identifier=json.loads((root/update.PENDING).read_bytes())['update_id'];record=update._intent(root,identifier)[0]
    attempt=canary._directory(root,record);assert (attempt/canary.INTENT).exists() and not (attempt/canary.RECEIPT).exists()
    diagnostics=json.loads((attempt/'private/worker-diagnostics.json').read_bytes())
    assert type(diagnostics['worker_pid']) is int and diagnostics['worker_pid']>0
    assert diagnostics['process_tree_exit_verified'] is False and diagnostics['recovery_required'] is True
    retained={path.relative_to(attempt).as_posix():build.sha256(path) for path in attempt.rglob('*') if path.is_file()}
    with pytest.raises(ValueError,match='already attempted'):update.recover_update(root,identifier,action='finish')
    with pytest.raises(ValueError,match='attempt|spawn|recovery'):update.recover_update(root,identifier,action='abort')
    assert pointers(root)==before
    assert retained=={path.relative_to(attempt).as_posix():build.sha256(path) for path in attempt.rglob('*') if path.is_file()}
    (tmp_path/'compiled-staged-deadline-proof.json').write_bytes(update._canonical({'scope':'controlled_compiled_installer_deadline_refusal',
        'update_id':identifier,'deadline_ms':1,'original_pointers_unchanged':True,'attempt_retained':True,
        'replay_and_abort_refused':True,'owned_worker_pid':diagnostics['worker_pid'],
        'worker_process_tree_exit_verified':False,'all_external_acceptance_false':True}))



@pytest.mark.parametrize('field,value',[('protocol',True),('protocol',1.0),('schema_version',True),('schema_version',1.0)])
def test_install_coerced_review_schema_refuses_before_staging(tmp_path,monkeypatch,field,value):
    from dataclasses import replace
    from backend.tests.test_global_migration import owned
    from backend.tests.test_service_s6_04 import fixture
    from backend.tests.test_staged_update_canary import controlled_proof,CONTROL_SPEC
    root,*_=owned(tmp_path);artifact=fixture(tmp_path);controlled_proof(monkeypatch)
    proposal=update.plan_update(root,artifact['directory'],artifact['envelope'],artifact['authority'],
        pinned_authority_sha256=artifact['pinned_authority_sha256'],target=artifact['target'],canary=CONTROL_SPEC)
    changed=replace(proposal,canary_preflight={**proposal.canary_preflight,field:value})
    with pytest.raises(ValueError,match='canary|Canary'):update.install_update(root,changed)
    assert not (root/update.PENDING).exists() and not (root/update.UPDATES).exists()
