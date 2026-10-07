"""Read-only positive liveness proof for an original specialist execution.

A shared backend PID cannot prove that one Python closure remains active. The
private Unix endpoint answers only a fresh bounded challenge while the original
admission/thread is active. Kernel peer PID binds the response to that backend;
no launch, cancel, fence change, store write or migration command is exposed.
"""
import hashlib,json,math,os,re,socket,stat,struct,sys,tempfile,threading,uuid
from pathlib import Path

import psutil

from backend.engine.runtime_process_control import atomic_private_json

FIELDS={'protocol_version','worker_kind','installation_id','job_id','spec_sha256','output_dir',
    'lease_owner','attempt_fence','attempt_number','owner_pid','owner_created_at',
    'owner_command_sha256','owner_instance','thread_id','control_address'}
TASKS={'rotation','ocr','rotated_detection','enhancement','defect_gan'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def supported():return os.name!='nt' and sys.platform in {'linux','darwin'} and hasattr(socket,'AF_UNIX')


def peer_pid(connection):
    if sys.platform=='linux':
        return struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))[0]
    if sys.platform=='darwin':
        # Apple bsd/sys/un.h: SOL_LOCAL=0, LOCAL_PEERPID=0x002.
        return connection.getsockopt(0,2)
    raise ValueError('Kernel Unix peer-PID verification is unavailable')


def _receive(connection):
    raw=b''
    while b'\n' not in raw:
        block=connection.recv(min(1024,8193-len(raw)))
        if not block:raise ValueError('Incomplete native control challenge')
        raw+=block
        if len(raw)>8192:raise ValueError('Native control message exceeds bound')
    message,extra=raw.split(b'\n',1)
    if extra:raise ValueError('Native control accepts one bounded message')
    return json.loads(message)


def _send(connection,value):connection.sendall(json.dumps(value,sort_keys=True,separators=(',',':')).encode()+b'\n')


def _validate_ready(ready):
    if (not isinstance(ready,dict) or set(ready)!=FIELDS
            or type(ready['protocol_version']) is not int or ready['protocol_version']!=1
            or ready['worker_kind']!='local_specialist'
            or any(type(ready[key]) is not int or ready[key]<1
                   for key in ('attempt_fence','attempt_number','owner_pid','thread_id'))
            or type(ready['owner_created_at']) not in (int,float)
            or not math.isfinite(ready['owner_created_at']) or ready['owner_created_at']<=0
            or any(not isinstance(ready[key],str) or not re.fullmatch('[a-f0-9]{32}',ready[key])
                   for key in ('installation_id','job_id','owner_instance'))
            or any(not isinstance(ready[key],str) or not re.fullmatch('[a-f0-9]{64}',ready[key])
                   for key in ('spec_sha256','owner_command_sha256'))
            or not isinstance(ready['lease_owner'],str) or not 1<=len(ready['lease_owner'])<=256
            or any(not isinstance(ready[key],str) or not Path(ready[key]).is_absolute()
                   for key in ('output_dir','control_address'))):
        raise ValueError('Invalid native live descriptor')


class NativeControl:
    def __init__(self,admission,root,owner):
        self.admission=admission;self.original_thread=threading.current_thread();self.stop=threading.Event()
        self.directory=Path(tempfile.mkdtemp(prefix='mvn-',dir='/private/tmp' if sys.platform=='darwin' else '/tmp')).resolve()
        self.path=self.directory/'c.sock';self.listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.thread=None;self.identity=None
        try:
            self.listener.bind(str(self.path));self.path.chmod(0o600);self.identity=(self.path.stat().st_dev,self.path.stat().st_ino)
            self.listener.listen(4);self.listener.settimeout(.2)
            process=psutil.Process();lease=admission.lease;row=admission.store.record(admission.job_id)
            self.ready={'protocol_version':1,'worker_kind':'local_specialist','installation_id':owner['installation_id'],
                'job_id':admission.job_id,'spec_sha256':row['spec_sha256'],'output_dir':str(admission.output),
                'lease_owner':admission.scheduler.leases.owner,'attempt_fence':lease.fence,'attempt_number':lease.attempt,
                'owner_pid':process.pid,'owner_created_at':process.create_time(),'owner_command_sha256':digest(process.cmdline()),
                'owner_instance':admission.identity['owner_instance'],'thread_id':threading.get_native_id(),
                'control_address':str(self.path)}
            self.sha256=digest(self.ready)
            ready_path=admission.output/'native_control_ready.json'
            if ready_path.exists() or ready_path.is_symlink():raise ValueError('Native control acknowledgment already exists')
            admission.output.mkdir(parents=True,exist_ok=True)
            atomic_private_json(ready_path,self.ready)
            self.thread=threading.Thread(target=self._serve,daemon=True,name='native-control-'+admission.job_id[:8]);self.thread.start()
        except BaseException:
            self.close();raise

    def _serve(self):
        while not self.stop.is_set():
            try:connection,_=self.listener.accept()
            except socket.timeout:continue
            except OSError:return
            with connection:
                try:
                    connection.settimeout(.5);request=_receive(connection)
                    if (not isinstance(request,dict) or set(request)!={'challenge','operation'} or request['operation']!='prove'
                            or not isinstance(request['challenge'],str) or not re.fullmatch('[a-f0-9]{64}',request['challenge'])):
                        continue
                    # Pure in-memory proof: taking installation admission here
                    # would deadlock the exclusive migrator that asks for it.
                    a=self.admission
                    active=(not self.stop.is_set() and a._entered and self.original_thread.is_alive()
                        and a._native_control is self and a.lease is not None
                        and a.lease.fence==self.ready['attempt_fence'] and a.lease.attempt==self.ready['attempt_number'])
                    _send(connection,{'challenge':request['challenge'],'descriptor_sha256':self.sha256,'active':active})
                except (OSError,ValueError,TypeError):continue

    def close(self):
        self.stop.set();self.listener.close()
        if self.thread is not None:self.thread.join(2)
        if self.identity is not None and self.path.exists():
            current=self.path.lstat()
            if stat.S_ISSOCK(current.st_mode) and (current.st_dev,current.st_ino)==self.identity:self.path.unlink()
        if not any(self.directory.iterdir()):self.directory.rmdir()


def probe_native_control(ready):
    """Read-only fresh challenge, positive kernel/process/closure identity."""
    try:
        if not supported():raise ValueError('Unsupported native live acknowledgment')
        _validate_ready(ready)
        address=Path(ready['control_address'])
        if (not address.is_absolute() or address.name!='c.sock' or not address.parent.name.startswith('mvn-')
                or any(p.is_symlink() for p in (address,*address.parents))):raise ValueError('Invalid native live endpoint')
        parent=address.parent.stat();node=address.lstat()
        if (parent.st_uid!=os.getuid() or stat.S_IMODE(parent.st_mode)!=0o700
                or not stat.S_ISSOCK(node.st_mode) or node.st_uid!=parent.st_uid or stat.S_IMODE(node.st_mode)!=0o600):
            raise ValueError('Native live endpoint ownership differs')
        process=psutil.Process(ready['owner_pid'])
        if (process.create_time()!=ready['owner_created_at'] or digest(process.cmdline())!=ready['owner_command_sha256']
                or process.uids().real!=parent.st_uid):raise ValueError('Native live process identity differs')
        challenge=uuid.uuid4().hex+uuid.uuid4().hex
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(1);connection.connect(str(address))
            if peer_pid(connection)!=ready['owner_pid']:raise ValueError('Native socket belongs to another process')
            _send(connection,{'operation':'prove','challenge':challenge});reply=_receive(connection)
        if (not isinstance(reply,dict) or set(reply)!={'challenge','descriptor_sha256','active'}
                or reply['challenge']!=challenge or reply['descriptor_sha256']!=digest(ready) or reply['active'] is not True):
            raise ValueError('Original specialist closure did not prove live ownership')
        current=address.lstat()
        if (current.st_dev,current.st_ino)!=(node.st_dev,node.st_ino):raise ValueError('Native live endpoint changed')
        return True
    except (OSError,KeyError,TypeError,psutil.Error) as exc:
        raise ValueError('Native live control endpoint or identity is unavailable') from exc


def inspect_native_worker(root,owner,row,attempt,claims,locations):
    from backend.engine.terminal_runtime_history import _read
    from backend.engine.job_store import spec_digest
    output=Path(row['output_dir']);spec=json.loads(row['spec_json'])
    if (not re.fullmatch('[a-f0-9]{32}',row['id']) or row['state'] not in {'running','stopping','detached','disconnected'}
            or row['source']!='api' or spec.get('task') not in TASKS or spec_digest(spec)!=row['spec_sha256']
            or not output.is_absolute() or output!=output.resolve() or not output.is_relative_to(root)):
        raise ValueError('Native live job/spec/output is not original and current')
    matches=[r for r in locations if output.is_relative_to(Path(r[3]))]
    if len(matches)!=1:raise ValueError('Native live output lacks one registered project')
    key,workspace,project,directory=matches[0];metadata,_=_read(root,Path(directory)/'project.json')
    if (metadata.get('id')!=project or (row['workspace_id'],row['project_key'],row['project_id'])!=(workspace,key,project)
            or output!=Path(metadata.get('models_dir',''))/spec['task']/row['id']
            or spec.get('output_root')!=str(output.parent) or not Path(metadata.get('models_dir','')).is_relative_to(Path(directory))):
        raise ValueError('Native live registered namespace or model directory differs')
    ready,_=_read(root,output/'native_control_ready.json')
    if (not isinstance(ready,dict) or set(ready)!=FIELDS or ready['installation_id']!=owner['installation_id']
            or any(ready[key]!=expected for key,expected in {'job_id':row['id'],'spec_sha256':row['spec_sha256'],
                'output_dir':str(output),'attempt_fence':attempt['fencing_token'],'attempt_number':attempt['number']}.items())
            or len(claims)!=1 or claims[0]['owner']!=ready['lease_owner'] or claims[0]['fence']!=attempt['fencing_token']
            or claims[0]['remote'] or claims[0]['app_schema']!=2 or claims[0]['ledger_job']!=1):
        raise ValueError('Native live acknowledgment/reservation/attempt binding differs')
    checkpoint=json.loads(row['operation_json'] or '{}')
    if (checkpoint.get('native_worker') is not True or checkpoint.get('native_execution_started') is not True
            or any(checkpoint.get(k)!=ready[k] for k in ('owner_pid','owner_created_at','owner_instance'))
            or checkpoint.get('native_control_sha256')!=digest(ready)):
        raise ValueError('Native original worker checkpoint is not bound to its acknowledgment')
    probe_native_control(ready)
    return {'job_id':row['id'],'lease_owner':ready['lease_owner'],'attempt_fence':attempt['fencing_token'],
        'attempt_number':attempt['number'],'owner_pid':ready['owner_pid'],'owner_created_at':ready['owner_created_at'],
        'owner_command_sha256':ready['owner_command_sha256'],'spec_sha256':ready['spec_sha256'],'output_dir':str(output),
        'reserved':True,'uncertain':bool(claims[0]['uncertain']),'worker_kind':'local_specialist','native_control_sha256':digest(ready)}
