"""Portable authenticated field agent for verified full-flow releases."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import httpx
import psutil
from fastapi import Depends,FastAPI,HTTPException,Request
from backend.engine.runtime_process_control import (atomic_private_json,owned_inspection_process,
    process_identity,runtime_state_lock,serialized_lifecycle)


MAX_ARCHIVE_BYTES=1024*1024*1024
MAX_EXPANDED_BYTES=4*1024*1024*1024


def extract_package_archive(archive,destination):
    destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise ValueError('Release extraction needs a new owned directory')
    with zipfile.ZipFile(archive) as zipped:
        entries=zipped.infolist()
        if len(entries)>4096 or sum(e.file_size for e in entries)>MAX_EXPANDED_BYTES:raise ValueError('Release archive exceeds limits')
        names=[]
        for entry in entries:
            relative=PurePosixPath(entry.filename);mode=entry.external_attr>>16
            if (relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename or not relative.parts or stat.S_ISLNK(mode)
                    or (mode and stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR))):raise ValueError('Unsafe release archive entry')
            names.append(entry.filename)
        if len(set(names))!=len(names):raise ValueError('Duplicate archive entries')
        destination.mkdir(parents=True)
        try:
            for entry in entries:
                output=destination/entry.filename
                if entry.is_dir():output.mkdir(parents=True,exist_ok=True);continue
                output.parent.mkdir(parents=True,exist_ok=True)
                with zipped.open(entry) as reader,output.open('xb') as writer:shutil.copyfileobj(reader,writer)
        except Exception:shutil.rmtree(destination,ignore_errors=True);raise
    return destination


class FieldAgent:
    def __init__(self,root):
        root=Path(root).expanduser()
        if root.is_symlink():raise ValueError('Field agent root is linked')
        root.mkdir(parents=True,exist_ok=True);self.root=root.resolve();self.releases=self.root/'releases';self.releases.mkdir(exist_ok=True)
        if self.releases.is_symlink():raise ValueError('Agent release root is linked')
        for name in ('state','service.log'):
            if (self.root/name).is_symlink():raise ValueError('Agent runtime storage is linked')
        self.path=self.root/'service.json'
        if self.path.is_symlink():raise ValueError('Agent service config is linked')
        if not self.path.exists():
            with runtime_state_lock(self.root):
                if not self.path.exists():
                    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
                    self.save({'port':port,'token':secrets.token_urlsafe(32),'pid':None})
        self.config=json.loads(self.path.read_text(encoding='utf-8'))
    def save(self,value):
        atomic_private_json(self.path,value)
    def owned(self):
        if self.path.is_symlink():raise ValueError('Agent service config is linked')
        self.config=json.loads(self.path.read_text(encoding='utf-8'))
        return owned_inspection_process(self.config,self.root/'state')
    def client(self):return httpx.Client(base_url=f'http://127.0.0.1:{self.config["port"]}',headers={'X-Vision-Token':self.config['token']},timeout=30)
    def runtime(self):
        if not self.owned():return {'status':'stopped'}
        try:
            with self.client() as client:response=client.get('/v1/runtime');response.raise_for_status();return response.json()
        except (ValueError,httpx.HTTPError):return {'status':'disconnected'}
    @serialized_lifecycle
    def stage(self,archive,expected,policy):
        if len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):raise ValueError('Invalid manifest identity')
        staging=self.releases/('stage-'+secrets.token_hex(12));extract_package_archive(archive,staging)
        try:
            from backend.engine.flow_package_runtime import verify_flow_package
            from backend.engine.inspection_service import _verify_release_policy
            pipeline,checkpoints=verify_flow_package(staging)
            if hashlib.sha256((staging/'manifest.json').read_bytes()).hexdigest()!=expected:raise ValueError('Release manifest differs from sender identity')
            if not isinstance(policy,dict) or policy.get('manifest_sha256')!=expected or not isinstance(policy.get('device'),str):
                raise ValueError('Authenticated release request requires its exact trusted manifest/device/cohort approval policy')
            policy_path=self.releases/(expected+'.policy.json')
            if policy_path.is_symlink():raise ValueError('Release policy is linked')
            temporary=self.releases/('.check-'+secrets.token_hex(12)+'.policy.json')
            try:
                temporary.write_text(json.dumps(policy),encoding='utf-8');_verify_release_policy(staging,checkpoints,temporary,device=policy['device'])
            finally:temporary.unlink(missing_ok=True)
            destination=self.releases/expected
            if destination.exists():
                if destination.is_symlink():raise ValueError('Release destination is linked')
                verify_flow_package(destination)
                if hashlib.sha256((destination/'manifest.json').read_bytes()).hexdigest()!=expected:raise ValueError('Existing staged release identity differs')
                from backend.engine.runtime_release_evidence import verify_release_evidence
                verify_release_evidence(destination,policy['device'],expected_receipt_sha256=(policy.get('parity_receipt_sha256') or policy.get('runtime_acceptance_sha256')))
                shutil.rmtree(staging)
            else:staging.rename(destination)
            if policy_path.exists() and json.loads(policy_path.read_text(encoding='utf-8'))!=policy:raise ValueError('Existing release policy differs')
            if not policy_path.exists():atomic_private_json(policy_path,policy)
            return {'manifest_sha256':expected,'status':'staged','model_count':len(checkpoints),'pipeline_id':pipeline.id}
        finally:
            if staging.exists():shutil.rmtree(staging,ignore_errors=True)
    @serialized_lifecycle
    def apply(self,digest,device):
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Invalid manifest identity')
        from backend.engine.runtime_device import resolve_package_device as resolve_runtime_device
        selected=str(resolve_runtime_device(device));package=self.releases/digest;policy=self.releases/(digest+'.policy.json')
        if not package.is_dir() or not policy.is_file():raise ValueError('Stage the approved release before applying it')
        from backend.engine.flow_package_runtime import verify_flow_package
        from backend.engine.inspection_service import _verify_release_policy
        _verify_release_policy(package,verify_flow_package(package)[1],policy,device=selected)
        if not self.owned():
            from backend.engine.service_bootstrap import runtime_command,runtime_cwd
            arguments=runtime_command(['--package',str(package),'--state-dir',str(self.root/'state'),'--runtime-root',str(self.releases),
                '--release-policy',str(policy),'--require-approved-release','--device',selected,'--port',str(self.config['port'])])
            env=dict(os.environ);env['VISION_INSPECTION_TOKEN']=self.config['token']
            with (self.root/'service.log').open('ab') as log:process=subprocess.Popen(arguments,cwd=runtime_cwd(),env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:self.config.update(process_identity(process,self.root/'state'));self.save(self.config)
            except Exception:
                process.terminate();process.wait(timeout=10);raise
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('Field runtime exited; inspect service.log')
                if self.runtime().get('status')=='ready':break
                time.sleep(.1)
        with self.client() as client:
            response=client.post('/v1/runtime/apply',json={'package_path':str(package),'release_policy':str(policy),'manifest_sha256':digest,'device':selected});response.raise_for_status()
        result=self.runtime()
        if result.get('status')!='ready' or result.get('manifest_sha256')!=digest or result.get('device')!=selected:raise ValueError('Field runtime acknowledgment differs')
        return result
    @serialized_lifecycle
    def stop(self):
        process=self.owned()
        if process:
            process.terminate()
            try:process.wait(timeout=10)
            except psutil.TimeoutExpired:raise RuntimeError('Field runtime is still stopping')
        self.config.update(pid=None,process_created_at=None,process_command_sha256=None);self.save(self.config);return {'status':'stopped'}


def create_agent_app(root,token):
    if not token or len(token)<16:raise ValueError('Use a field agent access token of at least 16 characters')
    agent=FieldAgent(root);app=FastAPI(title='Modu Vision Field Agent');app.state.agent=agent
    def authorized(request:Request):
        supplied=request.headers.get('Authorization','').removeprefix('Bearer ')
        if not secrets.compare_digest(supplied,token):raise HTTPException(401,'Agent access token required')
    @app.get('/agent/v1/runtime',dependencies=[Depends(authorized)])
    def runtime():return agent.runtime()
    @app.post('/agent/v1/releases',dependencies=[Depends(authorized)])
    async def stage(request:Request):
        temporary=None
        try:
            raw_policy=request.headers.get('X-Release-Policy','')
            if not raw_policy or len(raw_policy)>65536:raise ValueError('Authenticated release request requires a bounded trusted release policy')
            policy=json.loads(raw_policy)
            with tempfile.NamedTemporaryFile(dir=agent.root,prefix='upload-',delete=False) as writer:
                temporary=Path(writer.name);size=0
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>MAX_ARCHIVE_BYTES:raise HTTPException(413,'Release archive exceeds limit')
                    writer.write(chunk)
            return agent.stage(temporary,request.headers.get('X-Manifest-SHA256',''),policy)
        except (ValueError,OSError,zipfile.BadZipFile) as exc:raise HTTPException(422,str(exc)) from exc
        finally:
            if temporary:temporary.unlink(missing_ok=True)
    @app.post('/agent/v1/apply',dependencies=[Depends(authorized)])
    def apply(payload:dict):
        try:return agent.apply(payload['manifest_sha256'],payload.get('device','cpu'))
        except (ValueError,OSError,KeyError,RuntimeError,httpx.HTTPError) as exc:raise HTTPException(409,str(exc)) from exc
    @app.post('/agent/v1/stop',dependencies=[Depends(authorized)])
    def stop():return agent.stop()
    @app.post('/agent/v1/jobs/upload',dependencies=[Depends(authorized)])
    async def inspect(request:Request):
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>64*1024*1024:raise HTTPException(413,'Image exceeds limit')
            chunks.append(chunk)
        content=b''.join(chunks)
        with agent.client() as client:response=client.post('/v1/jobs/upload',content=content);response.raise_for_status();return response.json()
    @app.get('/agent/v1/jobs/{identifier}',dependencies=[Depends(authorized)])
    def job(identifier:str):
        with agent.client() as client:response=client.get('/v1/jobs/'+identifier);response.raise_for_status();return response.json()
    return app


def main(argv=None):
    parser=argparse.ArgumentParser(description='Run an authenticated field deployment and inspection agent')
    parser.add_argument('--state-dir',required=True);parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8514);args=parser.parse_args(argv)
    import uvicorn
    uvicorn.run(create_agent_app(args.state_dir,os.environ.get('VISION_FIELD_AGENT_TOKEN','')),host=args.host,port=args.port)

if __name__=='__main__':main()
