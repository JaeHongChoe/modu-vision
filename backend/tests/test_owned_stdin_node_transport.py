"""Real Node stdio ABI and exact original EOF custody; no app/model execution."""
import ast
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

import pytest

if os.name == 'posix':
    import fcntl
pytestmark = pytest.mark.skipif(sys.platform not in ('darwin', 'linux'),
    reason='This regression requires the POSIX Node stdin kernel ABI')


def owner_api():
    return importlib.import_module('backend.engine.owned_stdin_lifetime')

NODE_DRIVER = r"""'use strict';
// Only the exact production spawn-options function is transpiled/evaluated.
const fs=require('node:fs'),vm=require('node:vm'),cp=require('node:child_process');
const ts=require(process.argv[2]);
const path=require('node:path'),S=__dirname;
const raw=fs.readFileSync(path.join(S,'options.ts'),'utf8');
const moduleBox={exports:{}};
vm.runInNewContext(ts.transpileModule(raw,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{module:moduleBox,exports:moduleBox.exports});
const options=moduleBox.exports.backendSpawnOptions(S,{PATH:'/usr/bin:/bin',LANG:'C.UTF-8',PYTHONDONTWRITEBYTECODE:'1'},true);
const child=cp.spawn(process.argv[3],['-B',path.join(S,'child.py'),process.argv[4],process.argv[5]],options);
let stdout='',stderr='',ended=false;
function endOwnStdin(){if(!ended){ended=true;child.stdin.end('original Node supervisor data');}}
child.stdin.on('error',()=>{});
child.stdout.on('data',chunk=>{stdout+=chunk;if(stdout.includes('"kind": "ready"'))endOwnStdin();});
child.stderr.on('data',chunk=>{stderr+=chunk;});
const timer=setTimeout(endOwnStdin,5000); // Only own original stop channel; no signals.
child.on('error',error=>{clearTimeout(timer);process.stderr.write(String(error));process.exitCode=2;});
child.on('close',(code,signal)=>{clearTimeout(timer);console.log(JSON.stringify({schema:'modu-vision.real-Node-owned-stdin/v1',code,signal,stdout,stderr,stdin_ended_by_original_Node:ended,spawn_options:options,signals_sent:false,native_or_backend_run:false}));process.exitCode=code===0&&signal===null?0:1;});
"""
PYTHON_CHILD = r'''"""Source-only helper: exact real-main EOF reader, no app imports or workers."""
import ast
import importlib.util
import json
import logging
import os
from pathlib import Path
import socket
import stat
import sys
import time
from types import SimpleNamespace

path=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('source_only_owned_stdin',path)
owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)
before=os.fstat(0)
def facts(value):
    return (value.st_dev,value.st_ino,stat.S_IFMT(value.st_mode),value.st_uid,value.st_gid,value.st_nlink)
descriptor={'mode':before.st_mode,'is_fifo':stat.S_ISFIFO(before.st_mode),'is_socket':stat.S_ISSOCK(before.st_mode),'device':before.st_dev,'inode':before.st_ino}
if descriptor['is_socket']:
    duplicate=os.dup(0)
    with socket.socket(fileno=duplicate) as observed:
        descriptor.update(family=int(observed.family),type=int(observed.type),proto=observed.proto,
            local_name=observed.getsockname(),peer_name=observed.getpeername())
assert facts(os.fstat(0))==facts(before)
print(json.dumps({'kind':'descriptor','descriptor':descriptor}),flush=True)
calls=[];server=SimpleNamespace(should_exit=False)
namespace={'os':os,'logger':logging.getLogger('source-only-node-stdin'),
    'training_job_manager':SimpleNamespace(detach_all_for_shutdown=lambda:calls.append('detached'))}
exec(compile(Path(sys.argv[2]).read_text(),'original-real-main-stdin-reader','exec'),namespace)
reader=namespace['_stop_when_stdin_closes']
scope=owner.OwnedStdinLifetime(0,lambda fd,eof:reader(server,stdin_fd=fd,on_natural_eof=eof))
scope.start()
assert scope.thread.is_alive() and not scope.natural_eof_observed and facts(os.fstat(0))==facts(before)
print(json.dumps({'kind':'ready','open_original_stdin_unresolved':True}),flush=True)
assert scope.eof_event.wait(6), 'Original Node stop channel did not produce EOF'
scope.thread.join(2)
deadline=time.monotonic()+1
receipt=scope.after_eof_receipt(deadline,expected_thread=scope.thread,expected_fd=0)
assert calls==['detached'] and server.should_exit and not scope.thread.is_alive()
assert facts(os.fstat(0))==facts(before)
assert all(receipt[k] is False for k in ['whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'])
print(json.dumps({'kind':'completed','receipt':receipt,'detach_calls':calls,'original_descriptor_retained':True,
    'initial_stat_mode':before.st_mode,'final_stat_mode':os.fstat(0).st_mode}),flush=True)
'''


def test_real_production_Node_stdio_natural_EOF_and_original_child_exit(tmp_path):
    import backend
    repository=Path(next(iter(backend.__path__))).resolve().parent
    source=(repository/'src/main/supervisor.ts').read_text()
    start=source.index('export function backendSpawnOptions(');stop=source.index('\n}\n',start)+3
    (tmp_path/'options.ts').write_text(source[start:stop])
    main=(repository/'backend/main.py').read_text()
    reader=next(n for n in ast.parse(main).body if isinstance(n,ast.FunctionDef) and n.name=='_stop_when_stdin_closes')
    reader_path=tmp_path/'original-stdin-reader.py'
    reader_path.write_text(ast.get_source_segment(main,reader)+'\n')
    (tmp_path/'driver.cjs').write_text(NODE_DRIVER);(tmp_path/'child.py').write_text(PYTHON_CHILD)
    node=shutil.which('node');assert node, 'Real Node runtime is required for this transport regression'
    result=subprocess.run([node,str(tmp_path/'driver.cjs'),str(repository/'node_modules/typescript'),
        sys.executable,str(Path(owner_api().__file__).resolve()),str(reader_path)],cwd=tmp_path,
        env={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=15)
    assert result.stdout, result.stderr
    document=json.loads(result.stdout)
    print(json.dumps(document,sort_keys=True))
    assert result.returncode==document['code']==0, document
    assert document['signal'] is None and document['signals_sent'] is False
    rows=[json.loads(line) for line in document['stdout'].splitlines()]
    descriptor=next(row['descriptor'] for row in rows if row['kind']=='descriptor')
    assert descriptor['is_fifo'] or (descriptor['is_socket'] and descriptor['family']==int(socket.AF_UNIX)
        and descriptor['type']==int(socket.SOCK_STREAM) and descriptor['local_name']==descriptor['peer_name']=='')
    assert document['spawn_options']['stdio']==['pipe']*4
    assert document['spawn_options']['env']['VISION_AI_STUDIO_STOP_ON_STDIN_EOF']=='1'
    complete=next(row for row in rows if row['kind']=='completed')
    assert document['stdin_ended_by_original_Node'] and complete['detach_calls']==['detached']
    assert complete['original_descriptor_retained']

def original_reader(fd,observed):
    while os.read(fd,65536):pass
    observed()
    return {'natural_stdin_eof':True,'detach_completed':True}


def stable_facts(value):
    return (value.st_dev,value.st_ino,stat.S_IFMT(value.st_mode),value.st_uid,value.st_gid,value.st_nlink)


def test_anonymous_Unix_stream_original_EOF_receipt_retains_descriptor():
    api=owner_api()
    read,write=socket.socketpair()
    try:
        before=os.fstat(read.fileno());scope=api.OwnedStdinLifetime(read.fileno(),original_reader)
        scope.start()
        with pytest.raises(api.OriginalStdinError,match='EOF|unresolved'):
            scope.after_eof_receipt(time.monotonic()+1,expected_thread=scope.thread,expected_fd=read.fileno())
        assert scope.thread.is_alive() and not scope.natural_eof_observed
        write.sendall(b'original payload');write.close()
        assert scope.eof_event.wait(1);scope.thread.join(1)
        receipt=scope.after_eof_receipt(scope._deadline,expected_thread=scope.thread,expected_fd=read.fileno())
        assert stable_facts(os.fstat(read.fileno()))==stable_facts(before) and not scope.thread.is_alive()
        assert all(receipt[k] is False for k in ['whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'])
    finally:
        write.close();read.close()


def test_foreign_socket_replacement_is_refused_before_start():
    api=owner_api();read,write=socket.socketpair();foreign,foreign_write=socket.socketpair()
    try:
        scope=api.OwnedStdinLifetime(read.fileno(),original_reader)
        os.dup2(foreign.fileno(),read.fileno())
        with pytest.raises(api.OriginalStdinError,match='changed'):
            scope.start()
        assert not scope.thread.is_alive() and not scope.natural_eof_observed
    finally:
        read.close();write.close();foreign.close();foreign_write.close()


def test_foreign_socket_replacement_during_cleanup_is_sticky_failure():
    api=owner_api();read,write=socket.socketpair();foreign,foreign_write=socket.socketpair()
    begun=threading.Event();release=threading.Event()
    def reader(fd,observed):
        while os.read(fd,65536):pass
        observed();begun.set();assert release.wait(1)
        return {'natural_stdin_eof':True,'detach_completed':True}
    try:
        scope=api.OwnedStdinLifetime(read.fileno(),reader);scope.start();write.close();assert begun.wait(1)
        os.dup2(foreign.fileno(),read.fileno());release.set();scope.thread.join(1)
        with pytest.raises(api.OriginalStdinError,match='changed|failed'):
            scope.after_eof_receipt(time.monotonic()+1,expected_thread=scope.thread,expected_fd=read.fileno())
        assert scope._failure is not None
    finally:
        release.set();read.close();write.close();foreign.close();foreign_write.close()


def test_anonymous_socket_EOF_observer_failure_stays_failed():
    api=owner_api();read,write=socket.socketpair()
    def reader(fd,observed):
        while os.read(fd,65536):pass
        observed();raise RuntimeError('original detach failure')
    try:
        scope=api.OwnedStdinLifetime(read.fileno(),reader);scope.start();write.close();assert scope.eof_event.wait(1);scope.thread.join(1)
        with pytest.raises(api.OriginalStdinError,match='failed') as held:
            scope.after_eof_receipt(time.monotonic()+1,expected_thread=scope.thread,expected_fd=read.fileno())
        assert isinstance(held.value.__cause__,RuntimeError)
    finally:read.close();write.close()


@pytest.mark.parametrize('kind',['tcp','datagram','unconnected','named_unix','file'])
def test_non_supervisor_socket_or_regular_file_is_refused(kind,tmp_path):
    api=owner_api();resources=[]
    try:
        if kind=='file':
            f=(tmp_path/'not-stdin').open('w+b');resources.append(f);fd=f.fileno()
        elif kind=='datagram':
            read,write=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM);resources.extend([read,write]);fd=read.fileno()
        elif kind=='unconnected':
            read=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);resources.append(read);fd=read.fileno()
        elif kind=='tcp':
            listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM);resources.append(listener)
            listener.bind(('127.0.0.1',0));listener.listen(1)
            write=socket.create_connection(listener.getsockname());resources.append(write)
            read,_=listener.accept();resources.append(read);fd=read.fileno()
        else:
            short_directory=tempfile.TemporaryDirectory(prefix='mv-stdin-',dir='/tmp')
            listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);resources.append(listener)
            listener.bind(str(Path(short_directory.name)/'named.sock'));listener.listen(1)
            write=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);resources.append(write);write.connect(listener.getsockname())
            read,_=listener.accept();resources.append(read);fd=read.fileno()
        before=os.fstat(fd)
        with pytest.raises(api.OriginalStdinError,match='supervisor|pipe|transport|stream|socket'):
            api.OwnedStdinLifetime(fd,original_reader)
        assert os.fstat(fd)==before
    finally:
        for item in reversed(resources):item.close()
        if kind=='named_unix':short_directory.cleanup()



def test_global_socket_timeout_never_changes_original_stdin_OFD_flags():
    api=owner_api();read,write=socket.socketpair();previous=socket.getdefaulttimeout()
    try:
        before=os.get_blocking(read.fileno());flags=fcntl.fcntl(read.fileno(),fcntl.F_GETFL);assert before is True
        socket.setdefaulttimeout(.1)
        scope=api.OwnedStdinLifetime(read.fileno(),original_reader)
        assert os.get_blocking(read.fileno()) is before
        assert fcntl.fcntl(read.fileno(),fcntl.F_GETFL)==flags
        assert scope.thread.is_alive() is False and not scope.natural_eof_observed
    finally:
        socket.setdefaulttimeout(previous);read.close();write.close()


def test_concurrent_global_timeout_changes_never_change_original_OFD_flags():
    api=owner_api();read,write=socket.socketpair();previous=socket.getdefaulttimeout()
    stop=threading.Event();begun=threading.Event()
    def changing():
        begun.set()
        while not stop.is_set():
            for value in (.1,None,0.0):socket.setdefaulttimeout(value)
    changer=threading.Thread(target=changing)
    try:
        flags=fcntl.fcntl(read.fileno(),fcntl.F_GETFL);changer.start();assert begun.wait(1)
        scope=api.OwnedStdinLifetime(read.fileno(),original_reader)
        for _ in range(40):
            scope._fresh()
            assert fcntl.fcntl(read.fileno(),fcntl.F_GETFL)==flags
            assert os.get_blocking(read.fileno()) is True
    finally:
        stop.set();changer.join(1);socket.setdefaulttimeout(previous);read.close();write.close()
    assert not changer.is_alive()


def test_descriptor_inspection_never_constructs_Python_socket_wrapper(monkeypatch):
    api=owner_api();read,write=socket.socketpair()
    def forbidden(*args,**kwargs):raise AssertionError('Python socket wrapper would change shared OFD flags')
    try:
        monkeypatch.setattr(socket,'socket',forbidden)
        scope=api.OwnedStdinLifetime(read.fileno(),original_reader);scope._fresh()
        assert os.get_blocking(read.fileno())
    finally:read.close();write.close()


@pytest.mark.parametrize('platform,raw,length,expected',[
    ('darwin',bytes((16,int(socket.AF_UNIX)))+bytes(14),16,True),
    ('darwin',bytes((16,int(socket.AF_INET)))+bytes(14),16,False),
    ('darwin',bytes((15,int(socket.AF_UNIX)))+bytes(14),16,False),
    ('darwin',bytes((16,int(socket.AF_UNIX)))+b'x'+bytes(13),16,False),
    ('linux',int(socket.AF_UNIX).to_bytes(2,sys.byteorder),2,True),
    ('linux',int(socket.AF_INET).to_bytes(2,sys.byteorder),2,False),
    ('linux',int(socket.AF_UNIX).to_bytes(2,sys.byteorder)+b'\0',3,False),
    ('other',b'',0,False),
])
def test_exact_Darwin_and_Linux_anonymous_sockaddr_layouts(platform,raw,length,expected):
    assert owner_api()._anonymous_unix_address(raw,length,platform) is expected
