"""One private compiled preactivation canary under the original installer.

This role never borrows an application launch lease, backend epoch or shared
store admission. Its evidence covers fixed CPU math, not native acceptance or
complete process-tree exit. Interrupted attempts remain owned and cannot replay.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import math
import secrets
import socket
import stat
import subprocess
import time

PROTOCOL='owned_staged_canary_cpu_execution_protocol'
POLICY='same_reviewed_frozen_runtime_worker_v1'
SCOPE='staged_frozen_runtime_worker'
RESOURCES=('scripts/frozen_backend_entry.py',
    'backend/engine/staged_canary_frozen_execution.py',
    'backend/engine/staged_update_canary.py','backend/engine/runtime_update.py',
    'backend/engine/global_migration.py','backend/engine/migration_guard.py',
    'backend/engine/application_launch_execution.py',
    'backend/engine/application_launch_handshake.py',
    'backend/engine/flow_package_runtime.py','backend/engine/ocr.py',
    'backend/engine/process_isolation.py')
BINDING_FIELDS={'protocol','executable_path','executable_sha256','build_receipt_path',
    'build_receipt_sha256','build_identity_sha256','runtime_source_sha256','resource_inventory_sha256'}
REQUEST_FIELDS={'schema_version','kind','root','update_id','migration_id','requirement_sha256',
    'nonce','epoch','challenge','installer','worker_binding','capability','lock_identity'}
CHALLENGE_FIELDS={'schema_version','kind','challenge','nonce','epoch','request_path','request_sha256','transport'}
PROOF_FIELDS={'schema_version','kind','challenge','nonce','epoch','request_sha256','requirement_sha256',
    'process','executable','executable_sha256','build_identity_sha256','runtime_source_sha256',
    'resource_inventory_sha256','environment','flow_result'}


def _helpers():
    from backend.engine import runtime_update,staged_update_canary,application_launch_execution
    return runtime_update,staged_update_canary,application_launch_execution


def _inventory_shape(inventory):
    u,s,e=_helpers()
    if (not isinstance(inventory,dict) or type(inventory.get(PROTOCOL)) is not int or inventory[PROTOCOL]!=1
            or type(inventory.get('owned_application_cpu_execution_protocol')) is not int
            or inventory['owned_application_cpu_execution_protocol']!=1
            or type(inventory.get('owned_application_launch_controller_protocol')) is not int
            or inventory['owned_application_launch_controller_protocol']!=1
            or not u._hex(inventory.get('build_identity_sha256'))
            or u._sha(u._canonical({k:v for k,v in inventory.items() if k!='build_identity_sha256'}))!=inventory['build_identity_sha256']
            or not isinstance(inventory.get('resources'),list) or not 1<=len(inventory['resources'])<=2048):
        raise s.CanaryError('requires_target: staged compiled worker inventory capability is missing or changed')
    pins={}
    for row in inventory['resources']:
        if not isinstance(row,dict) or set(row)!={'path','sha256'} or not u._hex(row['sha256']):
            raise s.CanaryError('Invalid staged compiled resource row')
        name=u._safe_path(row['path'])
        if name in pins:raise s.CanaryError('Duplicate staged compiled resource row')
        pins[name]=row['sha256']
    if not set(RESOURCES)<=pins.keys():raise s.CanaryError('Staged compiled worker prerequisites are not checksum-bound')
    return pins


def candidate_binding(manifest,*,application=None,archive=None):
    """Derive all worker paths from checksum-bound signed application rows."""
    import zipfile
    u,s,e=_helpers()
    if (application is None)==(archive is None):raise s.CanaryError('One exact staged candidate source is required')
    rows=manifest.get('files') if isinstance(manifest,dict) else None
    if not isinstance(rows,list) or not 1<=len(rows)<=u.MAX_APPLICATION_MEMBERS:
        raise s.CanaryError('Invalid staged compiled application rows')
    pins={row['path']:row for row in rows}
    receipts=[row for row in rows if row['path'].split('/')[-1]=='backend-release.json']
    if len(receipts)!=1:raise s.CanaryError('requires_target: candidate needs one exact compiled backend release receipt')
    def read(name,limit):
        row=pins.get(name)
        if row is None or row['size']>limit:raise s.CanaryError('Staged compiled resource is not an approved bounded application row')
        if application is not None:return e._read(Path(application)/name,limit,expected=row['sha256'])
        with u._file(archive,1024**3) as (reader,_),zipfile.ZipFile(reader) as bundle:
            member=bundle.getinfo(name)
            if member.file_size!=row['size']:raise s.CanaryError('Staged compiled archive member size differs')
            raw=bundle.read(name)
        if len(raw)!=row['size'] or u._sha(raw)!=row['sha256']:raise s.CanaryError('Staged compiled archive resource differs')
        return raw
    receipt_path=receipts[0]['path'];receipt=e._json(read(receipt_path,e.MAX_RESULT))
    if (not isinstance(receipt,dict) or type(receipt.get('schema_version')) is not int or receipt['schema_version']!=1
            or not isinstance(receipt.get('executable'),str) or '/' in receipt['executable'] or '\\' in receipt['executable']
            or receipt['executable'] not in ('vision_ai_backend','vision_ai_backend.exe')
            or not u._hex(receipt.get('executable_sha256'))):
        raise s.CanaryError('Invalid staged compiled backend receipt')
    prefix=receipt_path[:-len('backend-release.json')]
    executable=prefix+receipt['executable'];row=pins.get(executable)
    if row is None or row['executable'] is not True or row['size']<=0 or row['sha256']!=receipt['executable_sha256']:
        raise s.CanaryError('Staged compiled executable differs from signed application row')
    read(executable,1024**3)
    inventory=receipt.get('inventory');sources=_inventory_shape(inventory)
    resource_root=prefix+'_internal/'
    if e._json(read(resource_root+'backend-build-inventory.json',e.MAX_RESULT))!=inventory:
        raise s.CanaryError('Staged compiled receipt and bundled inventory differ')
    names=sorted(name for name in sources if name.startswith('backend/') and name.endswith('.py'))
    actual=sorted(name[len(resource_root):] for name in pins if name.startswith(resource_root+'backend/') and name.endswith('.py'))
    if names!=actual or not 1<=len(names)<=e.MAX_FILES:raise s.CanaryError('Staged compiled source resource membership differs')
    runtime_rows=[];total=0
    for name in sorted(set(names)|set(RESOURCES)):
        row=pins.get(resource_root+name)
        if row is None or row['sha256']!=sources[name] or row['executable'] or row['size']>e.MAX_FILE:
            raise s.CanaryError('Staged compiled source resource differs from signed rows')
        total+=row['size']
        if total>e.MAX_TOTAL:raise s.CanaryError('Staged compiled source resources exceed their total bound')
        read(resource_root+name,e.MAX_FILE)
        if name in names:runtime_rows.append({key:row[key] for key in ('path','size','sha256')})
    runtime_rows=[{**row,'path':row['path'][len(resource_root):]} for row in runtime_rows]
    return {'protocol':1,'executable_path':executable,'executable_sha256':row_hash(pins[executable]),
        'build_receipt_path':receipt_path,'build_receipt_sha256':row_hash(receipts[0]),
        'build_identity_sha256':inventory['build_identity_sha256'],
        'runtime_source_sha256':u._sha(u._canonical(sorted(runtime_rows,key=lambda row:row['path']))),
        'resource_inventory_sha256':u._sha(u._canonical(inventory['resources']))}


def row_hash(row):return row['sha256']


def review_candidate(root,manifest,spec,capability_sha256,*,archive=None):
    u,s,e=_helpers()
    value={'schema_version':1,'protocol':2,'required':True,'policy':POLICY,'status':'missing_pins',
        'supported':False,'pins':spec,'capability_sha256':capability_sha256,
        'candidate_runtime_source_sha256':None,'worker_binding':None,
        'reason':'Supply explicit trusted known-image workspace, project and plan SHA-256 pins',
        **{name:False for name in s.FLAGS}}
    if spec is None:return value
    try:
        if os.name!='posix':raise s.CanaryError('requires_target: staged compiled transport requires POSIX')
        capability=e.admit_plan(root,**s.validate_spec(spec))
        if u._sha(u._canonical(capability))!=capability_sha256:raise s.CanaryError('Canary reviewed capability differs')
        binding=candidate_binding(manifest,archive=archive)
        if binding['runtime_source_sha256']!=capability['plan']['runtime_source_sha256']:
            raise s.CanaryError('requires_target: candidate changes the independently reviewed frozen runtime')
        graph=e._json(e._read(Path(capability['project_path'])/e.PACKAGE/'pipeline.json',e.MAX_RESULT,
            expected=capability['plan']['graph_sha256']))
        s._linear_graph(graph,capability['plan'])
        value.update(status='frozen_ready',supported=True,worker_binding=binding,
            candidate_runtime_source_sha256=binding['runtime_source_sha256'],reason=None)
    except (ValueError,OSError,KeyError) as exc:value.update(status='requires_target',reason=str(exc)[:500])
    return value


def _application(root,record):
    u,_,_=_helpers()
    return u._unlinked(Path(root)/u.GENERATIONS/record['application_generation']/'application')


def requirement(root,record,manifest,base):
    return {**base,'schema_version':2,'execution_policy':SCOPE,
        'worker_binding':candidate_binding(manifest,application=_application(root,record))}


def _process_identity(pid):
    import psutil
    u,s,_= _helpers()
    try:
        process=psutil.Process(pid);birth=process.create_time();command=process.cmdline()
    except psutil.Error as exc:raise s.CanaryError('Original staged process is absent or ambiguous') from exc
    return {'pid':pid,'created_at':birth,'command_sha256':u._sha(u._canonical(command))}


def _installer_identity():
    import psutil
    u,s,e=_helpers();executable=psutil.Process().exe();frozen=bool(getattr(sys,'frozen',False))
    return {'process':_process_identity(os.getpid()),'executable':executable,
        'executable_sha256':u._sha(e._read(executable,1024**3)),
        'build_identity_sha256':_inventory()[0]['build_identity_sha256'] if frozen else None,'frozen':frozen}


def _identity_shape(value):
    u,_,_=_helpers()
    return (isinstance(value,dict) and set(value)=={'pid','created_at','command_sha256'}
        and type(value.get('pid')) is int and value['pid']>0 and type(value.get('created_at')) in (int,float)
        and math.isfinite(value['created_at']) and value['created_at']>0 and u._hex(value.get('command_sha256')))


def _validate_installer(value,*,expected_pid):
    import psutil
    u,s,e=_helpers()
    if (not isinstance(value,dict) or set(value)!={'process','executable','executable_sha256','build_identity_sha256','frozen'}
            or not _identity_shape(value.get('process')) or value['process']['pid']!=expected_pid
            or type(value.get('frozen')) is not bool or not u._hex(value.get('executable_sha256'))
            or not isinstance(value.get('executable'),str) or not Path(value['executable']).is_absolute()
            or str(Path(value['executable']))!=value['executable']
            or (not u._hex(value.get('build_identity_sha256')) if value['frozen'] else value.get('build_identity_sha256') is not None)):
        raise s.CanaryError('Staged worker has a foreign original installer identity')
    try:
        if _process_identity(expected_pid)!=value['process'] or psutil.Process(expected_pid).exe()!=value['executable']:
            raise s.CanaryError('Staged worker original installer birth, command or executable differs')
        e._read(value['executable'],1024**3,expected=value['executable_sha256'])
        if value['frozen']:
            receipt=e._json(e._read(Path(value['executable']).parent/'backend-release.json',e.MAX_RESULT))
            if (not isinstance(receipt,dict) or receipt.get('executable_sha256')!=value['executable_sha256']
                    or not isinstance(receipt.get('inventory'),dict)
                    or receipt['inventory'].get('build_identity_sha256')!=value['build_identity_sha256']):
                raise s.CanaryError('Original compiled installer receipt differs')
    except psutil.Error as exc:raise s.CanaryError('Original installer is absent or ambiguous') from exc


def _lock_identity(root):
    u,s,e=_helpers();path=u._unlinked(Path(root)/'migration_admission.lock');value=path.stat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink!=1:raise s.CanaryError('Installer admission lock is linked or special')
    return {'device':value.st_dev,'inode':value.st_ino}


def _probe_exclusive(root,expected):
    """A blocked probe supplements parent authority; it cannot identify a holder."""
    import fcntl
    u,s,e=_helpers()
    if (not isinstance(expected,dict) or set(expected)!={'device','inode'}
            or any(type(expected[name]) is not int or expected[name]<0 for name in expected)):
        raise s.CanaryError('Invalid original installer admission lock identity')
    path=u._unlinked(Path(root)/'migration_admission.lock')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1
                or {'device':before.st_dev,'inode':before.st_ino}!=expected):
            raise s.CanaryError('Original installer admission lock changed')
        try:fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BlockingIOError:pass
        else:
            fcntl.flock(fd,fcntl.LOCK_UN)
            raise s.CanaryError('Original installer exclusive admission is absent')
        u._unlinked(path);after=path.stat()
        if (after.st_dev,after.st_ino,after.st_nlink,after.st_mode)!=(before.st_dev,before.st_ino,before.st_nlink,before.st_mode):
            raise s.CanaryError('Original installer admission lock changed during probe')
    finally:os.close(fd)


def _previous_pointers(root,requirement):
    u,s,e=_helpers();root=Path(root)
    app=root/u.ACTIVE;database=root/u.migration.POINTER_FILE
    current_app=e._json(e._read(app)) if app.exists() else None
    current_db=e._json(e._read(database)) if database.exists() else None
    if current_app!=requirement['previous_application'] or current_db!=requirement['previous_database']:
        raise s.CanaryError('Staged canary must run before both previous pointers change')


def _inventory():
    u,s,e=_helpers();inventory,digest=e._frozen_inventory();pins=_inventory_shape(inventory)
    root=u._unlinked(sys._MEIPASS)
    for name in RESOURCES:e._read(root/name,e.MAX_FILE,expected=pins[name])
    return inventory,digest


def _transport(value):
    from backend.engine.application_launch_handshake import _transport
    return _transport(value)


def _request_shape(value):
    u,s,e=_helpers()
    if (not isinstance(value,dict) or set(value)!=REQUEST_FIELDS or type(value.get('schema_version')) is not int
            or value['schema_version']!=1 or value.get('kind')!='staged_frozen_canary_request'
            or not isinstance(value.get('root'),str) or not Path(value['root']).is_absolute()
            or str(Path(value['root']))!=value['root']
            or any(not u._hex(value.get(name),32) for name in ('update_id','migration_id','nonce','epoch'))
            or any(not u._hex(value.get(name)) for name in ('requirement_sha256','challenge'))
            or not isinstance(value.get('capability'),dict) or set(value['capability'])!={'plan','plan_sha256','project_path','scope_key'}
            or not isinstance(value['capability'].get('plan'),dict) or set(value['capability']['plan'])!=e.PLAN_FIELDS
            or not isinstance(value.get('worker_binding'),dict) or set(value['worker_binding'])!=BINDING_FIELDS
            or type(value['worker_binding'].get('protocol')) is not int or value['worker_binding']['protocol']!=1):
        raise s.CanaryError('Invalid closed staged compiled request')
    for name in ('executable_path','build_receipt_path'):u._safe_path(value['worker_binding'][name])
    if any(not u._hex(value['worker_binding'].get(name)) for name in BINDING_FIELDS-{'protocol','executable_path','build_receipt_path'}):
        raise s.CanaryError('Invalid staged compiled request binding')
    return value


def _private_members(private):
    u,s,e=_helpers();private=u._unlinked(private)
    allowed={'project','home','cache','tmp','worker-request.json','worker-spawn.json',
        'worker-admission.json','worker-result.json','worker-diagnostics.json'}
    if not private.is_dir() or any(path.name not in allowed for path in private.iterdir()):
        raise s.CanaryError('Staged private capsule has unknown activity members')
    for path in private.iterdir():
        u._unlinked(path)
        if path.name in {'project','home','cache','tmp'}:
            if not path.is_dir():raise s.CanaryError('Staged private capsule directory is special')
        else:e._read(path,e.MAX_RESULT)


def _proof_shape(value):
    u,s,e=_helpers()
    if (not isinstance(value,dict) or set(value)!=PROOF_FIELDS or type(value.get('schema_version')) is not int
            or value['schema_version']!=1 or value.get('kind')!='staged_frozen_canary_result'
            or any(not u._hex(value.get(name),32) for name in ('nonce','epoch'))
            or any(not u._hex(value.get(name)) for name in ('challenge','request_sha256','requirement_sha256',
                'executable_sha256','build_identity_sha256','runtime_source_sha256','resource_inventory_sha256'))
            or not _identity_shape(value.get('process')) or not isinstance(value.get('executable'),str)
            or not isinstance(value.get('environment'),dict) or not isinstance(value.get('flow_result'),dict)):
        raise s.CanaryError('Invalid closed staged compiled result proof')
    return value


def _private_inputs(private,capability,spec):
    """Validate captured data only; never open a source project manager or DB."""
    u,s,e=_helpers();private=u._unlinked(private);_private_members(private);plan=capability.get('plan')
    if (not isinstance(plan,dict) or set(plan)!=e.PLAN_FIELDS or type(plan.get('schema_version')) is not int
            or plan['schema_version']!=1 or plan.get('kind')!='owned_cpu_ocr_known_image_plan'
            or plan.get('workspace_id')!=spec['workspace_id'] or plan.get('project_id')!=spec['project_id']
            or plan.get('device')!='cpu' or type(plan.get('cpu_threads')) is not int or plan['cpu_threads']!=1
            or type(plan.get('deadline_ms')) is not int or not 1<=plan['deadline_ms']<=60000
            or plan.get('package_path')!=e.PACKAGE or plan.get('input_path')!=e.INPUT
            or capability.get('plan_sha256')!=spec['plan_sha256']
            or any(not u._hex(plan.get(name)) for name in ('project_manifest_sha256','package_manifest_sha256',
                'graph_sha256','input_sha256','semantic_output_sha256','runtime_source_sha256'))):
        raise s.CanaryError('Invalid independently pinned staged CPU plan')
    delivery=private/'project/delivery/launch-known-image';package=delivery/'package'
    if e._json(e._read(delivery/'plan.json',expected=spec['plan_sha256']))!=plan:
        raise s.CanaryError('Staged captured plan differs')
    project=e._json(e._read(private/'project/project.json',1024**2,expected=plan['project_manifest_sha256']))
    if not isinstance(project,dict) or project.get('id')!=spec['project_id'] or project.get('project_dir')!=capability['project_path']:
        raise s.CanaryError('Staged captured project authority differs')
    manifest=e._json(e._read(package/'manifest.json',e.MAX_RESULT,expected=plan['package_manifest_sha256']))
    files=manifest.get('files') if isinstance(manifest,dict) else None;models=manifest.get('models') if isinstance(manifest,dict) else None
    if not isinstance(files,list) or not 1<=len(files)<=e.MAX_FILES or not isinstance(models,list) or len(models)!=1:
        raise s.CanaryError('Invalid staged captured package inventory')
    pins={};total=0
    for row in files:
        if (not isinstance(row,dict) or set(row)!={'path','size','sha256'} or type(row.get('size')) is not int
                or not 0<=row['size']<=e.MAX_FILE or not u._hex(row.get('sha256'))):
            raise s.CanaryError('Invalid staged captured package row')
        name=u._safe_path(row['path']);total+=row['size']
        if name in pins or name=='manifest.json' or total>e.MAX_TOTAL:raise s.CanaryError('Staged package duplicate or bound exceeded')
        pins[name]=row;e._read(package/name,row['size'],expected=row['sha256'])
    if e._members(package)!={'manifest.json',*pins}:raise s.CanaryError('Staged captured package membership differs')
    model=models[0]
    if (not isinstance(model,dict) or set(model)!={'job_id','task','checkpoint'} or not u._hex(model.get('job_id'),32)
            or model.get('task')!='ocr' or model.get('checkpoint')!='models/'+model['job_id']+'/best_model.pt'
            or model['checkpoint'] not in pins or plan.get('checkpoints')!=[{'job_id':model['job_id'],'sha256':pins[model['checkpoint']]['sha256']}]):
        raise s.CanaryError('Staged checkpoint authority differs')
    e._read(delivery/'input.png',e.MAX_FILE,expected=plan['input_sha256'])
    graph=e._json(e._read(package/'pipeline.json',e.MAX_RESULT,expected=plan['graph_sha256']));s._linear_graph(graph,plan)
    policy=plan.get('release_policy')
    if 'release' in manifest:
        if not isinstance(policy,dict) or set(policy)!={'sha256'} or not u._hex(policy.get('sha256')):
            raise s.CanaryError('Staged released package requires independent policy')
        e._read(delivery/'release-policy.json',e.MAX_RESULT,expected=policy['sha256'])
        from backend.engine.flow_package_runtime import verify_flow_package
        from backend.engine.inspection_service import _verify_release_policy
        _,resolved=verify_flow_package(package);_verify_release_policy(package,resolved,delivery/'release-policy.json',device='cpu')
    elif policy is not None:raise s.CanaryError('Staged unreleased package cannot invent approval')
    return plan


def _capture_inputs(root,record,private):
    u,s,e=_helpers();spec=s.validate_spec(record['canary']);capability=e.admit_plan(root,**spec)
    if u._sha(u._canonical(capability))!=record['canary_capability_sha256']:
        raise s.CanaryError('Reviewed staged capability changed')
    delivery=private/'project/delivery/launch-known-image'
    copied=e.admit_plan(root,**spec,copy_to=delivery)
    if copied!=capability:raise s.CanaryError('Staged copied capability changed')
    project=Path(capability['project_path']);plan=capability['plan']
    u._write_raw(delivery/'plan.json',e._read(project/e.PLAN,expected=spec['plan_sha256']))
    u._write_raw(private/'project/project.json',e._read(project/'project.json',1024**2,expected=plan['project_manifest_sha256']))
    if plan['release_policy'] is not None:
        u._write_raw(delivery/'release-policy.json',e._read(project/'delivery/launch-known-image/release-policy.json',e.MAX_RESULT,
            expected=plan['release_policy']['sha256']))
    _private_inputs(private,capability,spec)
    return capability


def _channel_live(control):
    """Reject observable EOF or a second request without waiting for a peer."""
    import select
    u,s,e=_helpers()
    if select.select([control],[],[],0)[0]:
        value=control.recv(1,getattr(socket,'MSG_PEEK',0)|getattr(socket,'MSG_DONTWAIT',0))
        if not value:raise s.CanaryError('Original staged installer private descriptor closed')
        raise s.CanaryError('Staged private descriptor contains an unexpected or replayed frame')


def _validate_worker_environment(private):
    """Confine the pinned frozen Matplotlib hook before restoring fixed config.

    PyInstaller creates an empty private temporary directory and replaces
    MPLCONFIGDIR before the fixed entry dispatch. Only that confined temporary
    directory may differ, after the caller has authenticated the original
    installer and candidate. It never supplies a destination or code authority.
    """
    u,s,e=_helpers()
    for name in ('home','cache','tmp'):
        path=u._unlinked(private/name)
        if not path.is_dir() or path.stat().st_mode&0o077:
            raise s.CanaryError('Staged CPU writable directories are not private')
    expected=s._worker_environment(private/'home',private/'cache',private/'tmp')
    changed={name for name,value in expected.items() if os.environ.get(name)!=value}
    if not changed:return
    if changed!={'MPLCONFIGDIR'} or not getattr(sys,'frozen',False):
        raise s.CanaryError('Staged fixed private CPU environment differs')
    observed=os.environ.get('MPLCONFIGDIR');path=Path(observed or '')
    if not path.is_absolute() or path.parent!=private/'tmp':
        raise s.CanaryError('Frozen Matplotlib hook escaped the private temporary directory')
    path=u._unlinked(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_DIRECTORY)
    def identity(st):
        return (st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_nlink,st.st_mtime_ns,st.st_ctime_ns)
    try:
        initial=os.fstat(fd)
        if (not stat.S_ISDIR(initial.st_mode) or initial.st_mode&0o777!=0o700
                or initial.st_uid!=os.getuid() or identity(initial)!=identity(path.lstat())
                or os.listdir(fd) or identity(initial)!=identity(os.fstat(fd))
                or identity(initial)!=identity(path.lstat()) or os.environ.get('MPLCONFIGDIR')!=observed):
            raise s.CanaryError('Frozen Matplotlib hook temporary directory is not stable, empty and private')
        os.environ['MPLCONFIGDIR']=expected['MPLCONFIGDIR']
    finally:os.close(fd)


def _worker_context(control,request,root,requirement,private):
    """Observe the same original authority around captured semantic validation."""
    u,s,e=_helpers()
    _validate_installer(request['installer'],expected_pid=os.getppid());_channel_live(control)
    _probe_exclusive(root,request['lock_identity']);_previous_pointers(root,requirement)
    _validate_worker_environment(private)


def _admit(control,frame):
    """No writable state or heavy model imports precede this entire check."""
    u,s,e=_helpers()
    if (not isinstance(frame,dict) or set(frame)!=CHALLENGE_FIELDS or type(frame.get('schema_version')) is not int
            or frame['schema_version']!=1 or frame.get('kind')!='staged_frozen_canary_challenge'
            or any(not u._hex(frame.get(name),32) for name in ('nonce','epoch'))
            or any(not u._hex(frame.get(name)) for name in ('challenge','request_sha256'))):
        raise s.CanaryError('Invalid private staged canary challenge')
    st=os.fstat(control.fileno())
    if frame.get('transport')!={'device':st.st_dev,'inode':st.st_ino,'family':'AF_UNIX','type':'SOCK_STREAM','anonymous':True}:
        raise s.CanaryError('Staged challenge is not on the original anonymous descriptor')
    raw=e._read(frame['request_path'],65536,expected=frame['request_sha256']);request=_request_shape(e._json(raw))
    if any(frame[name]!=request[name] for name in ('challenge','nonce','epoch')):
        raise s.CanaryError('Stale staged challenge or epoch')
    _validate_installer(request['installer'],expected_pid=os.getppid())
    _channel_live(control)
    inventory,runtime_sha=_inventory()
    root,owner=u._root(request['root']);record,_,manifest=u._validated_intent(root,request['update_id'])
    if (record.get('status')!='database_prepared' or record.get('migration_id')!=request['migration_id']
            or record.get('canary_execution_protocol')!=2 or record.get('canary_requirement_sha256')!=request['requirement_sha256']
            or record.get('canary_receipt_sha256') is not None):
        raise s.CanaryError('Staged installer durable journal is absent, stale or already published')
    pending=e._json(e._read(root/u.PENDING))
    if pending!={'schema_version':1,'installation_id':owner['installation_id'],'update_id':request['update_id']}:
        raise s.CanaryError('Original staged installer recovery ownership differs')
    attempt=s._directory(root,record);private=attempt/'private'
    if u._unlinked(frame['request_path'])!=private/'worker-request.json':raise s.CanaryError('Staged request is outside its fixed private capsule')
    requirement,requirement_sha=s._document(attempt/s.REQUIREMENT)
    database=e._json(e._read(root/'.global-migrations'/request['migration_id']/'journal.json',1024**2))
    if requirement_sha!=request['requirement_sha256'] or requirement!=s.expected_requirement(root,record,database,manifest):
        raise s.CanaryError('Staged original requirement/seal binding differs')
    intent,intent_sha=s._document(attempt/s.INTENT)
    if intent!=_intent(request,frame['request_sha256']):raise s.CanaryError('Staged original one-use intent differs')
    spawn,_=s._document(private/'worker-spawn.json')
    if spawn!=_spawn(request,frame['request_sha256'],_process_identity(os.getpid())):
        raise s.CanaryError('Staged original child spawn publication differs')
    s._capsule_members(attempt,{s.REQUIREMENT,s.INTENT,'private'})
    _previous_pointers(root,requirement);_probe_exclusive(root,request['lock_identity'])
    binding=candidate_binding(manifest,application=_application(root,record))
    if (binding!=request['worker_binding'] or binding!=record['canary_worker_binding']
            or sys.executable!=str(_application(root,record)/binding['executable_path'])
            or binding['build_identity_sha256']!=inventory['build_identity_sha256']
            or binding['resource_inventory_sha256']!=u._sha(u._canonical(inventory['resources']))
            or binding['runtime_source_sha256']!=runtime_sha
            or u._sha(u._canonical(request['capability']))!=record['canary_capability_sha256']
            or request['capability']['plan']['runtime_source_sha256']!=runtime_sha):
        raise s.CanaryError('Staged compiled artifact/build/runtime/capability differs')
    _worker_context(control,request,root,requirement,private)
    # Released-package semantic validators may import the ML stack. The fixed
    # CPU environment and private writable locations must be admitted first.
    plan=_private_inputs(private,request['capability'],record['canary'])
    _worker_context(control,request,root,requirement,private)
    if e._read(private/'worker-request.json',65536,expected=frame['request_sha256'])!=raw:
        raise s.CanaryError('Staged capsule changed during admission')
    return root,record,manifest,requirement,request,private,plan


def _intent(request,request_sha):
    return {'schema_version':2,'kind':'staged_update_canary_intent','status':'spawn_started',
        **{name:request[name] for name in ('requirement_sha256','nonce','epoch','installer','worker_binding')},
        'request_sha256':request_sha}


def _spawn(request,request_sha,process):
    return {'schema_version':1,'kind':'staged_frozen_worker_spawn','request_sha256':request_sha,
        'installer':request['installer'],'process':process,'worker_binding':request['worker_binding']}


def frozen_worker_main(argv=None):
    """Fixed early entry; no user paths, ordinary backend bootstrap or cache."""
    if not getattr(sys,'frozen',False):
        print('requires_target: staged worker requires a compiled frozen backend',file=sys.stderr)
        return 2
    import argparse
    parser=argparse.ArgumentParser(add_help=False,allow_abbrev=False)
    parser.add_argument('--control-fd',required=True)
    args=parser.parse_args(argv)
    try:
        with _transport(args.control_fd) as control:
            return _run_worker(control)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('Staged compiled canary refused: '+str(exc),file=sys.stderr)
        return 2


def _run_worker(control):
    from backend.engine.application_launch_handshake import read_frame,send_frame
    u,s,e=_helpers();frame=read_frame(control,60)
    root,record,manifest,requirement,request,private,plan=_admit(control,frame)
    identity=_process_identity(os.getpid());binding=request['worker_binding']
    marker={'schema_version':1,'kind':'staged_frozen_worker_admission','request_sha256':frame['request_sha256'],
        'installer':request['installer'],'process':identity,'challenge':request['challenge']}
    s._write_sealed(private/'worker-admission.json',marker)
    send_frame(control,{'schema_version':1,'kind':'staged_canary_admitted','request_sha256':frame['request_sha256'],
        'process':identity,'challenge':request['challenge'],'nonce':request['nonce'],'epoch':request['epoch']})
    import threading
    timer=threading.Timer(plan['deadline_ms']/1000,lambda:os._exit(124));timer.daemon=True;timer.start()
    try:
        # Compiled fixed code only. No source copy or exported Python reaches sys.path.
        sys.modules['pyarrow']=None
        from backend.engine.flow_package_runtime import run_flow_package
        import torch
        torch.set_num_threads(1)
        delivery=private/'project/delivery/launch-known-image'
        flow=run_flow_package(delivery/'package',delivery/'input.png','owned-cpu-known-image',device='cpu',cpu_threads=1,_owned_worker=True)
        e.validate_result(flow,{**request['capability'],'project_path':str(private/'project')})
        if _admit(control,frame)[4]!=request:raise s.CanaryError('Staged original request changed during math')
        if candidate_binding(manifest,application=_application(root,record))!=binding:
            raise s.CanaryError('Staged candidate artifact changed during math')
        proof={'schema_version':1,'kind':'staged_frozen_canary_result',
            **{name:request[name] for name in ('challenge','nonce','epoch','requirement_sha256')},
            'request_sha256':frame['request_sha256'],'process':identity,'executable':sys.executable,
            **{name:binding[name] for name in ('executable_sha256','build_identity_sha256','runtime_source_sha256','resource_inventory_sha256')},
            'environment':{name:os.environ.get(name) for name in s.ENVIRONMENT_PROOF_FIELDS},'flow_result':flow}
        _proof_shape(proof)
        if len(u._canonical(proof))>e.MAX_RESULT:raise s.CanaryError('Staged result exceeds its bound')
        result_sha=s._write_sealed(private/'worker-result.json',proof)
        send_frame(control,{'schema_version':1,'kind':'staged_canary_complete','request_sha256':frame['request_sha256'],
            'result_sha256':result_sha,'challenge':request['challenge'],'nonce':request['nonce'],'epoch':request['epoch']})
        return 0
    finally:timer.cancel()


def _cleanup_child(process):
    # Only the original Popen child handle is signalled. No group/PID scan grants
    # authority over descendants; pending intent survives every cleanup outcome.
    if process.poll() is None:
        try:process.kill()
        except OSError:pass
    try:process.wait(timeout=5)
    except (OSError,subprocess.TimeoutExpired):pass


def execute_candidate(root,record,database,manifest,requirement_sha256):
    """Original exclusive installer owns one compiled attempt and its proof."""
    from backend.engine.application_launch_handshake import send_frame,read_frame
    from backend.engine.process_isolation import session_isolation
    import tempfile
    u,s,e=_helpers();s._require_exclusive(root);attempt=s._directory(root,record)
    requirement,sha=s._document(attempt/s.REQUIREMENT)
    if sha!=requirement_sha256 or requirement!=s.expected_requirement(root,record,database,manifest):
        raise s.CanaryError('Staged compiled requirement changed before attempt')
    _previous_pointers(root,requirement);s._source_and_target(root,record,database)
    private=u._unlinked(attempt/'private');private.mkdir(mode=0o700,exist_ok=False)
    capability=_capture_inputs(root,record,private);binding=candidate_binding(manifest,application=_application(root,record))
    if binding!=requirement['worker_binding'] or binding['runtime_source_sha256']!=capability['plan']['runtime_source_sha256']:
        raise s.CanaryError('requires_target: staged candidate changes reviewed runtime')
    request={'schema_version':1,'kind':'staged_frozen_canary_request','root':str(root),
        'update_id':record['update_id'],'migration_id':record['migration_id'],'requirement_sha256':sha,
        'nonce':secrets.token_hex(16),'epoch':secrets.token_hex(16),'challenge':secrets.token_hex(32),
        'installer':_installer_identity(),'worker_binding':binding,'capability':capability,
        'lock_identity':_lock_identity(root)}
    _request_shape(request);request_sha=s._write_sealed(private/'worker-request.json',request)
    for name in ('home','cache','tmp'):(private/name).mkdir(mode=0o700)
    u.migration._sync_directories(private,recursive=True)
    intent_sha=s._write_sealed(attempt/s.INTENT,_intent(request,request_sha))
    s._checkpoint('before_canary_spawn');_previous_pointers(root,requirement);s._require_exclusive(root)
    started=time.monotonic();deadline=capability['plan']['deadline_ms']/1000
    parent,child=socket.socketpair();child_stat=os.fstat(child.fileno());process=None
    def budget():
        remaining=deadline-(time.monotonic()-started)
        if remaining<=0:raise s.CanaryError('Staged compiled CPU deadline exceeded; retain recovery ownership')
        return remaining
    with tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
        try:
            process=subprocess.Popen([str(_application(root,record)/binding['executable_path']),
                '--owned-staged-canary-cpu-worker','--control-fd',str(child.fileno())],
                stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,close_fds=True,pass_fds=(child.fileno(),),
                env=s._worker_environment(private/'home',private/'cache',private/'tmp'),cwd=private,**session_isolation())
            child.close()
            identity=_process_identity(process.pid)
            s._write_sealed(private/'worker-spawn.json',_spawn(request,request_sha,identity))
            send_frame(parent,{'schema_version':1,'kind':'staged_frozen_canary_challenge',
                **{name:request[name] for name in ('challenge','nonce','epoch')},
                'request_path':str(private/'worker-request.json'),'request_sha256':request_sha,
                'transport':{'device':child_stat.st_dev,'inode':child_stat.st_ino,'family':'AF_UNIX','type':'SOCK_STREAM','anonymous':True}})
            admitted=read_frame(parent,budget())
            if _process_identity(process.pid)!=identity or admitted!={'schema_version':1,'kind':'staged_canary_admitted','request_sha256':request_sha,
                    'process':identity,**{name:request[name] for name in ('challenge','nonce','epoch')}}:
                raise s.CanaryError('Staged worker original process/challenge proof differs')
            import psutil
            if psutil.Process(process.pid).exe()!=str(_application(root,record)/binding['executable_path']):
                raise s.CanaryError('Staged worker actual executable differs')
            complete=read_frame(parent,budget())
            result_raw=e._read(private/'worker-result.json',e.MAX_RESULT);proof=_proof_shape(e._json(result_raw))
            if complete!={'schema_version':1,'kind':'staged_canary_complete','request_sha256':request_sha,
                    'result_sha256':u._sha(result_raw),**{name:request[name] for name in ('challenge','nonce','epoch')}}:
                raise s.CanaryError('Staged completion descriptor differs from durable worker result')
            process.wait(timeout=budget())
            if process.returncode!=0:raise s.CanaryError('Staged compiled CPU did not complete; retain recovery ownership')
            elapsed=round((time.monotonic()-started)*1000,3)
        except BaseException:
            if process is not None:_cleanup_child(process)
            stdout.seek(0);stderr.seek(0)
            diagnostics={'worker_pid':process.pid if process is not None else None,
                'stdout':stdout.read(65536).decode('utf-8',errors='replace'),
                'stderr':stderr.read(65536).decode('utf-8',errors='replace'),
                'process_tree_exit_verified':False,'recovery_required':True}
            try:s._write_sealed(private/'worker-diagnostics.json',diagnostics)
            except (ValueError,OSError):pass
            raise
        finally:parent.close();child.close()
    if (proof['process']!=identity or any(proof[name]!=request[name] for name in ('nonce','epoch','challenge','requirement_sha256'))
            or proof['request_sha256']!=request_sha or proof['executable']!=str(_application(root,record)/binding['executable_path'])
            or any(proof[name]!=binding[name] for name in ('executable_sha256','build_identity_sha256','runtime_source_sha256','resource_inventory_sha256'))
            or proof['environment']!={name:s._worker_environment(private/'home',private/'cache',private/'tmp')[name] for name in s.ENVIRONMENT_PROOF_FIELDS}):
        raise s.CanaryError('Staged compiled worker retained proof differs')
    flow=proof['flow_result'];flow['runtime_execution']={'device':'cpu','cpu_threads':1,
        'deadline_ms':capability['plan']['deadline_ms'],'isolated_process':True,'pid':identity['pid'],'elapsed_ms':elapsed}
    semantic=e.validate_result(flow,{**capability,'project_path':str(private/'project')})
    semantic_sha=u._sha(u._canonical(semantic));s._checkpoint('after_canary_math')
    if semantic_sha!=capability['plan']['semantic_output_sha256']:raise s.CanaryError('Staged actual math differs from independent semantic pin')
    _private_inputs(private,capability,record['canary']);s._require_exclusive(root);_previous_pointers(root,requirement)
    if e.admit_plan(root,**record['canary'])!=capability or candidate_binding(manifest,application=_application(root,record))!=binding:
        raise s.CanaryError('Staged original capability or candidate changed during math')
    s._source_and_target(root,record,database)
    result={'schema_version':2,'requirement_sha256':sha,'nonce':request['nonce'],'epoch':request['epoch'],
        'semantic_output':semantic,'semantic_output_sha256':semantic_sha,'runtime_source_sha256':binding['runtime_source_sha256'],
        'capability':capability,'worker_binding':binding,'installer':request['installer'],'worker':proof,'flow_result':flow}
    result_sha=s._write_sealed(attempt/'canary-result.json',result);s._checkpoint('after_canary_result')
    receipt={'schema_version':2,'kind':'staged_update_canary_receipt','status':'verified','execution_scope':SCOPE,
        'requirement_sha256':sha,'nonce':request['nonce'],'epoch':request['epoch'],'intent_sha256':intent_sha,
        'result_sha256':result_sha,'semantic_output_sha256':semantic_sha,'runtime_source_sha256':binding['runtime_source_sha256'],
        'worker_binding_sha256':u._sha(u._canonical(binding)),'installer_sha256':u._sha(u._canonical(request['installer'])),
        **{name:False for name in s.FLAGS}}
    s._write_sealed(attempt/s.RECEIPT,receipt);s._checkpoint('after_canary_receipt')


def validate_receipt(root,record,database,manifest):
    """Readback never spawns or replays and does not require old PIDs to live."""
    u,s,e=_helpers();attempt=s._directory(root,record);private=attempt/'private'
    members={s.REQUIREMENT,s.INTENT,s.RECEIPT,'canary-result.json','private'};s._capsule_members(attempt,members)
    requirement,requirement_sha=s._document(attempt/s.REQUIREMENT)
    receipt,receipt_sha=s._document(attempt/s.RECEIPT)
    fields={'schema_version','kind','status','execution_scope','requirement_sha256','nonce','epoch','intent_sha256',
        'result_sha256','semantic_output_sha256','runtime_source_sha256','worker_binding_sha256','installer_sha256',*s.FLAGS}
    if (requirement!=s.expected_requirement(root,record,database,manifest) or record.get('canary_requirement_sha256')!=requirement_sha
            or not isinstance(receipt,dict) or set(receipt)!=fields or type(receipt.get('schema_version')) is not int
            or receipt['schema_version']!=2 or receipt.get('kind')!='staged_update_canary_receipt'
            or receipt.get('status')!='verified' or receipt.get('execution_scope')!=SCOPE
            or receipt.get('requirement_sha256')!=requirement_sha or record.get('canary_receipt_sha256')!=receipt_sha
            or any(receipt.get(name) is not False for name in s.FLAGS)
            or any(not u._hex(receipt.get(name),32) for name in ('nonce','epoch'))
            or any(not u._hex(receipt.get(name)) for name in ('intent_sha256','result_sha256','semantic_output_sha256',
                'runtime_source_sha256','worker_binding_sha256','installer_sha256'))):
        raise s.CanaryError('Staged compiled canary receipt is missing, foreign or grants unsupported acceptance')
    request_raw=e._read(private/'worker-request.json',65536);request=_request_shape(e._json(request_raw));request_sha=u._sha(request_raw)
    intent,intent_sha=s._document(attempt/s.INTENT);result,result_sha=s._document(attempt/'canary-result.json',e.MAX_RESULT)
    fields={'schema_version','requirement_sha256','nonce','epoch','semantic_output','semantic_output_sha256',
        'runtime_source_sha256','capability','worker_binding','installer','worker','flow_result'}
    if (intent!=_intent(request,request_sha) or intent_sha!=receipt['intent_sha256'] or not isinstance(result,dict)
            or set(result)!=fields or type(result.get('schema_version')) is not int or result['schema_version']!=2
            or result_sha!=receipt['result_sha256'] or result.get('installer')!=request['installer']
            or result.get('worker_binding')!=requirement['worker_binding'] or result.get('worker_binding')!=request['worker_binding']
            or u._sha(u._canonical(result['worker_binding']))!=receipt['worker_binding_sha256']
            or u._sha(u._canonical(result['installer']))!=receipt['installer_sha256']
            or any(result.get(name)!=receipt[name] or request.get(name)!=receipt[name] for name in ('nonce','epoch','requirement_sha256'))
            or any(request.get(name)!=record[name] for name in ('update_id','migration_id')) or request.get('root')!=str(root)):
        raise s.CanaryError('Staged compiled retained intent/result/capsule differs')
    capability=e.admit_plan(root,**record['canary'])
    if capability!=result.get('capability') or capability!=request['capability'] or u._sha(u._canonical(capability))!=record['canary_capability_sha256']:
        raise s.CanaryError('Staged compiled retained capability differs')
    _private_inputs(private,capability,record['canary']);proof=_proof_shape(result['worker'])
    marker,_=s._document(private/'worker-admission.json')
    spawn,_=s._document(private/'worker-spawn.json')
    if (marker!={'schema_version':1,'kind':'staged_frozen_worker_admission','request_sha256':request_sha,
                'installer':request['installer'],'process':proof['process'],'challenge':request['challenge']}
            or spawn!=_spawn(request,request_sha,proof['process'])
            or proof['request_sha256']!=request_sha or any(proof[name]!=request[name] for name in ('challenge','nonce','epoch','requirement_sha256'))
            or proof['executable']!=str(_application(root,record)/result['worker_binding']['executable_path'])
            or any(proof[name]!=result['worker_binding'][name] for name in ('executable_sha256','build_identity_sha256','runtime_source_sha256','resource_inventory_sha256'))
            or proof['environment']!={name:s._worker_environment(private/'home',private/'cache',private/'tmp')[name] for name in s.ENVIRONMENT_PROOF_FIELDS}):
        raise s.CanaryError('Staged compiled original admission/process proof differs')
    raw_proof=_proof_shape(e._json(e._read(private/'worker-result.json',e.MAX_RESULT)))
    if {k:v for k,v in proof.items() if k!='flow_result'}!={k:v for k,v in raw_proof.items() if k!='flow_result'}:
        raise s.CanaryError('Staged compiled original worker result differs')
    flow=result['flow_result'];e._runtime_execution(flow,capability['plan'],proof['process']['pid'])
    if flow!=proof['flow_result'] or {k:v for k,v in flow.items() if k!='runtime_execution'}!={k:v for k,v in raw_proof['flow_result'].items() if k!='runtime_execution'}:
        raise s.CanaryError('Staged compiled actual math proof differs')
    semantic=e.validate_result(flow,{**capability,'project_path':str(private/'project')});semantic_sha=u._sha(u._canonical(semantic))
    if (semantic!=result['semantic_output'] or semantic_sha!=receipt['semantic_output_sha256']
            or semantic_sha!=capability['plan']['semantic_output_sha256'] or result['semantic_output_sha256']!=semantic_sha
            or result['runtime_source_sha256']!=capability['plan']['runtime_source_sha256']
            or receipt['runtime_source_sha256']!=capability['plan']['runtime_source_sha256']):
        raise s.CanaryError('Staged compiled actual known-image math differs from independent pin')
    s._source_and_target(root,record,database);s._capsule_members(attempt,members)
    return receipt
