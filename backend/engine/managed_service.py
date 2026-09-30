"""Controls only an app-owned independent loopback inspection service."""
from __future__ import annotations
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
import httpx
import psutil
from backend.engine.flow_package_runtime import verify_flow_package
from backend.engine.inspection_service import _verify_release_policy
from backend.engine.runtime_deployment import DeploymentLedger
from backend.engine.runtime_device import resolve_runtime_device


class ManagedService:
    def __init__(self,project_dir):
        project_dir=Path(project_dir)
        if project_dir.is_symlink() or not project_dir.is_dir():raise ValueError('Project service root is invalid')
        self.root=project_dir/'runtime_service'
        if self.root.is_symlink():raise ValueError('Managed service storage is linked')
        self.root.mkdir(parents=True,exist_ok=True)
        for name in ('service.json','state','adapters.json','install','service.log','runtime_deployments.sqlite3'):
            if (self.root/name).is_symlink():raise ValueError('Managed service project state is linked')
        for name in ('runtime.json','inspection_service.sqlite3','uploads'):
            if (self.root/'state'/name).is_symlink():raise ValueError('Managed service execution state is linked')
        self.releases=self.root/'releases'
        if self.releases.is_symlink():raise ValueError('Managed releases root is linked')
        self.releases.mkdir(exist_ok=True)
        self.config_path=self.root/'service.json'
        if not self.config_path.exists():
            with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            self.save({'port':port,'token':secrets.token_urlsafe(32),'pid':None})
        self.config=json.loads(self.config_path.read_text())
        self.ledger=DeploymentLedger(self.root)
    def save(self,config):
        temporary=self.config_path.with_suffix('.tmp')
        with temporary.open('w') as writer:json.dump(config,writer);writer.flush();os.fsync(writer.fileno())
        temporary.chmod(0o600);os.replace(temporary,self.config_path)
    def client(self):
        return httpx.Client(base_url=f'http://127.0.0.1:{self.config["port"]}',headers={'X-Vision-Token':self.config['token']},timeout=30)
    def native_identity(self):
        expected='local.moduvision.inspection.'+hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        label=self.config.get('native_label')
        if label and label!=expected:raise ValueError('Native service identity does not match this project')
        return expected
    def owned_process(self):
        pid=self.config.get('pid')
        if self.config.get('native_label'):
            label=self.native_identity()
            result=subprocess.run(['launchctl','print',f'gui/{os.getuid()}/{label}'],capture_output=True,text=True,timeout=5)
            import re
            match=re.search(r'\bpid = ([0-9]+)',result.stdout)
            pid=int(match[1]) if match else None
        if not pid:return None
        try:
            process=psutil.Process(pid);arguments=process.cmdline()
            if 'backend.engine.inspection_service' in arguments and '--state-dir' in arguments and arguments[arguments.index('--state-dir')+1]==str(self.root/'state'):
                return process
        except (psutil.NoSuchProcess,psutil.AccessDenied):pass
        return None
    def readback(self):
        if self.owned_process() is None:return {'status':'stopped','port':self.config['port']}
        try:
            with self.client() as client:
                response=client.get('/v1/runtime');response.raise_for_status();return response.json()
        except (httpx.HTTPError,ValueError):return {'status':'disconnected','port':self.config['port']}
    def state(self):
        return {'runtime':self.readback(),'active':self.ledger.active(),'history':self.ledger.history(),'port':self.config['port'], 'adapter_config':self.read_adapter_config()}
    def stage(self,package_path,project):
        package=Path(package_path).expanduser()
        if package.is_symlink():raise ValueError('Linked release package is unsupported')
        package=package.resolve(strict=True)
        _,checkpoints=verify_flow_package(package)
        manifest=json.loads((package/'manifest.json').read_text())
        approvals=manifest.get('release',{}).get('approval_revisions')
        if not isinstance(approvals,list) or not approvals or len(approvals)!=len(checkpoints):raise ValueError('Every package model requires explicit project approval')
        from backend.api.routes_model_deployments import verified_approval_revision, _fingerprint, _store, _active
        for approval in approvals:
            verified=verified_approval_revision(project,approval.get('revision_id'),expected_task=approval.get('task'))
            if verified is None or any(verified.get(key)!=approval.get(key) for key in ('job_id','task','checkpoint_sha256')):
                raise ValueError('Release approval is stale, mismatched, or unverified')
            source=Path(verified['source_dataset_path'])
            if _fingerprint(source)!=verified['evaluation_dataset_fingerprint']:raise ValueError('Release evaluation dataset has changed')
            with _store(project) as conn:active=_active(conn,source,verified['task'])
            if active is None or active['revision_id']!=approval['revision_id']:raise ValueError('Release approval is not the active approved revision')
        digest=hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest()
        destination=self.releases/digest
        if destination.is_symlink():raise ValueError('Staged release directory is linked')
        if not destination.exists():
            temporary=self.releases/('.stage-'+uuid.uuid4().hex)
            try:
                temporary.mkdir()
                # Copy only checksum-bound package files; unlisted material is never staged.
                for relative in ['manifest.json', *[row['path'] for row in manifest['files']]]:
                    origin=package/relative;target=temporary/relative
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(origin,target,follow_symlinks=False)
                verify_flow_package(temporary)
                if hashlib.sha256((temporary/'manifest.json').read_bytes()).hexdigest()!=digest:raise ValueError('Package changed while staging')
                os.rename(temporary,destination)
            finally:
                if temporary.exists():shutil.rmtree(temporary)
        policy=self.releases/(digest+'.policy.json')
        if policy.is_symlink():raise ValueError('Staged release policy is linked')
        policy_payload={'schema_version':1,'manifest_sha256':digest,'approval_revisions':approvals}
        if policy.exists() and json.loads(policy.read_text())!=policy_payload:raise ValueError('Existing release policy differs')
        if not policy.exists():
            with policy.open('x') as writer:json.dump(policy_payload,writer)
            policy.chmod(0o600)
        _verify_release_policy(destination,verify_flow_package(destination)[1],policy)
        return {'package_path':str(destination),'release_policy':str(policy),'manifest_sha256':digest,
                'approval_revisions':approvals,'input_root':project.get('source_dataset_dir')}
    def start(self,release=None):
        if self.owned_process():return self.readback()
        release=release or (self.ledger.active() or {}).get('release')
        if release is None:raise ValueError('Apply an approved package before starting the service')
        resolve_runtime_device(release.get('device','cpu'))
        arguments=[sys.executable,'-m','backend.engine.inspection_service','--package',release['package_path'],'--state-dir',str(self.root/'state'),'--runtime-root',str(self.releases),'--release-policy',release['release_policy'],'--require-approved-release','--device',release.get('device','cpu'),'--port',str(self.config['port'])]
        if release.get('input_root'):arguments+=['--input-root',release['input_root']]
        adapter_path=self.root/'adapters.json'
        if adapter_path.exists():arguments+=['--adapter-config',str(adapter_path)]
        env=dict(os.environ);env['VISION_INSPECTION_TOKEN']=self.config['token']
        # Runtime modules are loaded from this checkout, never from untrusted release code.
        checkout=Path(__file__).resolve().parents[2]
        log=(self.root/'service.log').open('ab')
        try:process=subprocess.Popen(arguments,cwd=checkout,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        finally:log.close()
        self.config['pid']=process.pid;self.save(self.config)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('Managed service exited; inspect its local service.log')
            result=self.readback()
            if result.get('status')=='ready':return result
            time.sleep(.1)
        self.stop();raise TimeoutError('Managed service readiness timed out')
    def stop(self):
        process=self.owned_process()
        if self.config.get('native_label'):
            raise ValueError('Remove this app-owned automatic-start installation before manual stop/restart')
        if process:
            process.terminate()
            try:process.wait(timeout=12)
            except psutil.TimeoutExpired:raise RuntimeError('Service is stopping; jobs remain recoverable')
        self.config['pid']=None;self.save(self.config)
        return {'status':'stopped'}
    def apply_runtime(self,release):
        _,checkpoints=verify_flow_package(Path(release['package_path']))
        _verify_release_policy(Path(release['package_path']),checkpoints,Path(release['release_policy']))
        self.start(release)
        with self.client() as client:
            response=client.post('/v1/runtime/apply',json=release);response.raise_for_status()
            ack=client.get('/v1/runtime');ack.raise_for_status();return ack.json()
    def apply(self,package,device,reviewer,project):
        release={**self.stage(package,project),'device':str(resolve_runtime_device(device))}
        previous = self.ledger.active()
        try: return self.ledger.apply(release,self.apply_runtime,reviewer=reviewer)
        except Exception:
            if previous is None: self.stop()
            raise
    def rollback(self,deployment_id,reviewer):
        return self.ledger.rollback(deployment_id,self.apply_runtime,reviewer=reviewer)
    def read_adapter_config(self):
        from backend.engine.field_adapters import load_adapter_config
        config=load_adapter_config(self.root/'adapters.json' if (self.root/'adapters.json').exists() else None).model_dump()
        if config.get('mes'):config['mes']['token']=None
        config['clear_mes_token']=False
        return config
    def configure_adapters(self,config):
        from backend.engine.field_adapters import FieldAdapterConfig,ModbusTCPAdapter,HTTPMESAdapter
        checked=FieldAdapterConfig.model_validate(config)
        previous=self.root/'adapters.json'
        if checked.mes:
            saved=json.loads(previous.read_text()).get('mes') if previous.is_file() else None
            if checked.clear_mes_token:checked.mes.token=None
            elif checked.mes.token is None and saved and saved.get('token'):
                if saved.get('url')!=checked.mes.url:
                    raise ValueError('Changing the MES URL requires a new token or clear_mes_token=true')
                checked.mes.token=saved['token']
        checked.clear_mes_token=False
        if checked.modbus:ModbusTCPAdapter(checked.modbus)
        if checked.mes:HTTPMESAdapter(checked.mes)
        # Configuration is saved without network calls. Apply by explicit service restart.
        path=self.root/'adapters.json';temporary=path.with_suffix('.tmp')
        with temporary.open('w') as writer:writer.write(checked.model_dump_json());writer.flush();os.fsync(writer.fileno())
        temporary.chmod(0o600);os.replace(temporary,path)
        return {'saved':True,'restart_required':self.owned_process() is not None}
    def install_files(self):
        """Prepare native launch files; installation/activation is an explicit user control."""
        import plistlib
        directory=self.root/'install';directory.mkdir(exist_ok=True)
        active=self.ledger.active()
        if active is None:raise ValueError('An approved applied release is required')
        release=active['release']
        arguments=[sys.executable,'-m','backend.engine.inspection_service','--package',release['package_path'],'--state-dir',str(self.root/'state'),'--runtime-root',str(self.releases),'--release-policy',release['release_policy'],'--require-approved-release','--device',release['device'],'--port',str(self.config['port'])]
        if release.get('input_root'):arguments+=['--input-root',release['input_root']]
        if (self.root/'adapters.json').exists():arguments+=['--adapter-config',str(self.root/'adapters.json')]
        label=self.native_identity()
        plist=directory/(label+'.plist')
        plist.write_bytes(plistlib.dumps({'Label':label,'ProgramArguments':arguments,'WorkingDirectory':str(Path(__file__).resolve().parents[2]),'EnvironmentVariables':{'VISION_INSPECTION_TOKEN':self.config['token']},'RunAtLoad':True,'KeepAlive':True,'StandardOutPath':str(self.root/'service.log'),'StandardErrorPath':str(self.root/'service.log')}))
        plist.chmod(0o600)
        # A portable launcher and native command instructions use the same verified runtime contract.
        import shlex
        launcher=directory/'start-service.sh';launcher.write_text('#!/bin/sh\ncd '+shlex.quote(str(Path(__file__).resolve().parents[2]))+'\nexport VISION_INSPECTION_TOKEN='+shlex.quote(self.config['token'])+'\nexec '+shlex.join(arguments)+'\n');launcher.chmod(0o700)
        return {'files':[str(plist),str(launcher)],'macos_install_command':f'launchctl bootstrap gui/{os.getuid()} '+shlex.quote(str(plist)),'macos_uninstall_command':f'launchctl bootout gui/{os.getuid()}/'+label,'status':'prepared'}

    def activate_install(self):
        """Activate only this app-owned login service after explicit UI action."""
        import platform
        if platform.system()!='Darwin':raise ValueError('Native automatic-start activation currently requires macOS; use the prepared launcher on this host')
        prepared=self.install_files()
        plist=Path(prepared['files'][0])
        import plistlib
        label=plistlib.loads(plist.read_bytes())['Label']
        self.stop()
        result=subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)],capture_output=True,text=True,timeout=15)
        if result.returncode:raise RuntimeError('Native login-service installation failed: '+result.stderr.strip())
        self.config['native_label']=label;self.save(self.config)
        return {'status':'installed','label':label,'readiness':'pending native service start','files':prepared['files']}
    def uninstall_native(self):
        label=self.config.get('native_label')
        if not label:raise ValueError('No app-owned native service installation is recorded')
        label=self.native_identity()
        result=subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{label}'],capture_output=True,text=True,timeout=15)
        if result.returncode:raise RuntimeError('Native login-service removal failed: '+result.stderr.strip())
        self.config.pop('native_label',None);self.save(self.config)
        return {'status':'uninstalled'}
