#!/usr/bin/env python3
"""Execute a frozen backend twice in isolated state on its actual target.

Optional --package/--image checks an independent inspection service with a real
image. This is execution evidence, not model quality or physical approval.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as reader:
        for chunk in iter(lambda:reader.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def request(port, route, token, payload=None):
    data=json.dumps(payload).encode() if payload is not None else None
    req=urllib.request.Request(f'http://127.0.0.1:{port}{route}',data=data,
                               headers={'X-Vision-Token':token,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=3) as response:return json.load(response)


def launch(executable, arguments, directory, environment, *, port=None, timeout=60):
    process=subprocess.Popen([str(executable),*arguments],cwd=directory,env=environment,
                             stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    lines=[];port_holder=[port]
    def consume():
        for line in process.stdout:
            lines.append(line.rstrip())
            if line.startswith('VISION_AI_STUDIO_PORT='):
                try:port_holder[0]=int(line.split('=',1)[1])
                except ValueError:pass
    reader=threading.Thread(target=consume,daemon=True);reader.start()
    deadline=time.monotonic()+timeout
    try:
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('Frozen process exited during launch: '+'\n'.join(lines[-12:]))
            if port_holder[0]:
                try:
                    health=request(port_holder[0],'/health',environment['VISION_AI_STUDIO_API_TOKEN'])
                    if health.get('status') in ('ok','ready'):return process,reader,port_holder[0],health,lines
                except (OSError,ValueError):pass
            time.sleep(.1)
        raise TimeoutError('Frozen process readiness timed out: '+'\n'.join(lines[-12:]))
    except BaseException:
        shutdown(process,reader)
        raise


def shutdown(process,reader):
    if process.poll() is None:
        process.terminate()
        try:process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(timeout=10)
    reader.join(timeout=2)


def verify_known_image_execution(runtime,job,package_sha,image_sha,device,build_sha):
    identity=runtime.get('runtime_build',{})
    if (runtime.get('manifest_sha256')!=package_sha or runtime.get('device')!=device
            or identity.get('mode')!='frozen' or identity.get('status')!='identified'
            or identity.get('build_identity_sha256')!=build_sha):
        raise ValueError('Known-image package/device/runtime build differs from requested frozen release')
    result=job.get('result')
    if (job.get('state')!='completed' or job.get('image_sha256')!=image_sha or job.get('error')
            or not isinstance(result,dict) or result.get('status') not in ('success','review')
            or result.get('runtime_identity')!=runtime or result.get('final_verdict')!=job.get('verdict')
            or any(step.get('status') in ('error','warning_untrained') for step in result.get('execution_steps',[]))):
        raise ValueError('Known-image acceptance requires an exact completed image with actual bound successful inference')


def validate(directory, *, package=None, image=None, device='cpu'):
    directory=Path(directory).resolve()
    release=json.loads((directory/'backend-release.json').read_text())
    inventory=release['inventory'];executable=directory/release['executable']
    if platform.system()!=inventory['platform'] or platform.machine()!=inventory['architecture']:
        raise ValueError('requires_target: execute acceptance on the recorded operating system and architecture')
    if executable.parent!=directory or sha256(executable)!=release['executable_sha256']:
        raise ValueError('Backend executable checksum differs from build receipt')
    for row in release['files']:
        resource=directory/row['path']
        if not resource.resolve().is_relative_to(directory) or sha256(resource)!=row['sha256']:
            raise ValueError('Frozen dependency checksum differs: '+row['path'])
    report={'schema_version':1,'status':'failed','platform':platform.system(),'architecture':platform.machine(),
            'build_identity_sha256':inventory['build_identity_sha256'],
            'executable_sha256':release['executable_sha256'],'frozen':False,
            'physical_acceptance':'unverified','model_quality_acceptance':'unverified','inspection':None}
    with tempfile.TemporaryDirectory(prefix='vision-release-acceptance-') as temporary:
        state=Path(temporary)
        env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',
                 VISION_AI_STUDIO_API_TOKEN=secrets.token_hex(32),VISION_INSPECTION_TOKEN=secrets.token_hex(32),
                 VISION_AI_STUDIO_USER_DATA_DIR=str(state/'user'),YOLO_CONFIG_DIR=str(state/'yolo'))
        check=subprocess.run([str(executable),'--backend-diagnostics'],cwd=state,env=env,
                             capture_output=True,text=True,timeout=120)
        diagnostic=next((json.loads(line) for line in reversed(check.stdout.splitlines()) if line.startswith('{')),None)
        if check.returncode or not diagnostic or not diagnostic.get('frozen') or diagnostic.get('status')!='ready':
            raise RuntimeError('Frozen dependency diagnostics failed: '+(check.stderr+check.stdout)[-3000:])
        if diagnostic['build_identity_sha256']!=inventory['build_identity_sha256']:
            raise ValueError('Frozen inventory identity differs from build receipt')
        report['frozen']=True;report['diagnostic']=diagnostic
        for number in (0,1):
            args=['--host','127.0.0.1','--port','0','--project-dir',str(state/'projects'),'--log-level','warning']
            process,reader,port,health,lines=launch(executable,args,state,env)
            try:
                report['health' if number==0 else 'restart_health']={**health,'pid':process.pid,'port':port}
            finally:shutdown(process,reader)
        if package is not None:
            package=Path(package).resolve(strict=True);image=Path(image).resolve(strict=True)
            package_hash=sha256(package/'manifest.json');image_hash=sha256(image)
            with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            service_state=state/'inspection';service_state.mkdir()
            args=['--inspection-service','--package',str(package),'--state-dir',str(service_state),
                  '--port',str(port),'--device',device,'--input-root',str(image.parent)]
            executions=[]
            for number in (0,1):
                process,reader,port,health,lines=launch(executable,args,state,env,port=port)
                try:
                    runtime=request(port,'/v1/runtime',env['VISION_INSPECTION_TOKEN'])
                    job=request(port,'/v1/jobs/file',env['VISION_INSPECTION_TOKEN'],{'image_path':str(image)})
                    deadline=time.monotonic()+90
                    while time.monotonic()<deadline:
                        result=request(port,'/v1/jobs/'+job['job_id'],env['VISION_INSPECTION_TOKEN'])
                        if result['state']=='completed':break
                        if result['state'] in ('error','delivery_error'):raise RuntimeError('Known-image execution failed: '+str(result.get('error')))
                        time.sleep(.1)
                    else:raise TimeoutError('Known-image execution timed out')
                    verify_known_image_execution(runtime,result,package_hash,image_hash,device,inventory['build_identity_sha256'])
                    executions.append({'runtime':runtime,'job_id':job['job_id'],'state':result['state'],'verdict':result['verdict'],
                                       'result_sha256':hashlib.sha256(json.dumps(result['result'],sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                                       'result':result['result']})
                finally:shutdown(process,reader)
            if sha256(image)!=image_hash or sha256(package/'manifest.json')!=package_hash:
                raise ValueError('Acceptance source image or package changed during execution')
            report['inspection']={'manifest_sha256':package_hash,'image_sha256':image_hash,'device':device,'executions':executions}
        report['status']='passed'
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-dir',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--package',type=Path);parser.add_argument('--image',type=Path)
    parser.add_argument('--device',default='cpu')
    args=parser.parse_args()
    if bool(args.package)!=bool(args.image):parser.error('--package and --image are required together')
    try:report=validate(args.backend_dir,package=args.package,image=args.image,device=args.device)
    except Exception as exc:
        report={'schema_version':1,'status':'failed','platform':platform.system(),'architecture':platform.machine(),
                'error':str(exc),'error_type':type(exc).__name__}
    args.output.write_text(json.dumps(report,indent=2))
    print(json.dumps({'status':report['status'],'report':str(args.output),'error':report.get('error')}))
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
