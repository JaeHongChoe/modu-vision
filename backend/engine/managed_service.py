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
from backend.engine.runtime_device import resolve_package_device as resolve_runtime_device
from backend.engine.runtime_process_control import (atomic_private_json,
    owned_inspection_process,process_identity,runtime_state_lock,serialized_lifecycle,session_isolation)


class ManagedService:
    def __init__(self,project_dir):
        project_dir=Path(project_dir)
        if project_dir.is_symlink() or not project_dir.is_dir():raise ValueError('Project service root is invalid')
        self.root=project_dir/'runtime_service'
        if self.root.is_symlink():raise ValueError('Managed service storage is linked')
        self.root.mkdir(parents=True,exist_ok=True)
        for name in ('service.json','state','adapters.json','install','service.log','native-install.json','runtime_deployments.sqlite3','runtime_deployments.sqlite3-wal','runtime_deployments.sqlite3-shm'):
            if (self.root/name).is_symlink():raise ValueError('Managed service project state is linked')
        for name in ('runtime.json','inspection_service.sqlite3','uploads'):
            if (self.root/'state'/name).is_symlink():raise ValueError('Managed service execution state is linked')
        self.releases=self.root/'releases'
        if self.releases.is_symlink():raise ValueError('Managed releases root is linked')
        self.releases.mkdir(exist_ok=True)
        self.config_path=self.root/'service.json'
        if not self.config_path.exists():
            with runtime_state_lock(self.root):
                if not self.config_path.exists():
                    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
                    self.save({'port':port,'token':secrets.token_urlsafe(32),'pid':None})
        self.config=json.loads(self.config_path.read_text(encoding='utf-8'))
        self.ledger=DeploymentLedger(self.root)
    def save(self,config):
        atomic_private_json(self.config_path,config)
    def client(self):
        return httpx.Client(base_url=f'http://127.0.0.1:{self.config["port"]}',headers={'X-Vision-Token':self.config['token']},timeout=30)
    def native_identity(self):
        expected='local.moduvision.inspection.'+hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        label=self.config.get('native_label')
        if label and label!=expected:raise ValueError('Native service identity does not match this project')
        return expected
    def owned_process(self):
        if self.config_path.is_symlink():raise ValueError('Managed service state is linked')
        self.config=json.loads(self.config_path.read_text(encoding='utf-8'))
        if self.config.get('native_label'):
            from backend.engine.native_autostart import NativeAutostart
            native=NativeAutostart(self);state=native.query();pid=state.get('pid')
            try:
                if pid:
                    process=psutil.Process(pid);identity=process_identity(process,self.root/'state')
                    return owned_inspection_process(identity,self.root/'state')
                if native.system=='Windows' and state.get('registered'):
                    # Task Scheduler does not expose its child PID; exact state-dir
                    # and inspection command checks still establish ownership.
                    for process in psutil.process_iter(['pid']):
                        try:
                            identity=process_identity(process,self.root/'state')
                            owned=owned_inspection_process(identity,self.root/'state')
                            if owned:return owned
                        except (psutil.Error,ValueError):continue
            except (psutil.Error,ValueError):pass
            return None
        return owned_inspection_process(self.config,self.root/'state')
    def readback(self):
        if self.owned_process() is None:return {'status':'stopped','port':self.config['port']}
        try:
            with self.client() as client:
                response=client.get('/v1/runtime');response.raise_for_status();return response.json()
        except (httpx.HTTPError,ValueError):return {'status':'disconnected','port':self.config['port']}
    def state(self):
        from backend.engine.native_autostart import NativeAutostart
        from backend.engine.service_bootstrap import trusted_runtime_identity
        runtime=self.readback()
        return {'runtime':runtime,'active':self.ledger.active(),'history':self.ledger.history(),'port':self.config['port'], 'adapter_config':self.read_adapter_config(),'native_install':NativeAutostart(self).query(),
                'recovery':self.ledger.diagnostics(),'runtime_build':runtime.get('runtime_build') if runtime.get('status')=='ready' else None,
                'manager_build':trusted_runtime_identity()}
    def input_arguments(self,release):
        path=self.root/'inputs.json'
        if path.is_symlink():raise ValueError('Input configuration cannot follow links')
        if not path.exists():return []
        value=json.loads(path.read_text(encoding='utf-8'));scope=value.get('scope',{})
        source=release.get('input_root')
        if not source or scope.get('source_dataset_path')!=str(Path(source).resolve()):raise ValueError('Input configuration source changed; configure operator inputs again')
        if value.get('mode')=='folder':
            folder=Path(value.get('folder') or '')
            if folder.is_symlink() or not folder.is_dir() or not folder.resolve().is_relative_to(Path(source).resolve()):raise ValueError('Input folder is no longer inside the active release source')
            return ['--inbox',str(folder.resolve())]
        if value.get('mode')=='camera':
            camera=value.get('camera')
            if not isinstance(camera,str) or not (camera.isdecimal() or camera.startswith(('rtsp://','rtsps://'))):raise ValueError('Invalid configured camera source')
            return ['--camera-source',camera]
        return []
    @staticmethod
    def validate_accepted_device(package,device,*,expected_receipt_sha256=None):
        from backend.engine.runtime_release_evidence import verify_release_evidence
        verify_release_evidence(package,device,expected_receipt_sha256=expected_receipt_sha256)
        manifest=json.loads((Path(package)/'manifest.json').read_text(encoding='utf-8'))
        if device.startswith('openvino:') and not any(row['path']=='openvino_models.json' for row in manifest['files']):
            raise ValueError('OpenVINO execution requires a verified package with openvino_models.json')
        if manifest.get('runtime_acceptance_sha256') and device!=manifest['runtime']['device']:
            raise ValueError('Reviewed precision runtime requires its explicitly accepted device')
    @serialized_lifecycle
    def stage(self,package_path,project,device=None):
        package=Path(package_path).expanduser()
        if package.is_symlink():raise ValueError('Linked release package is unsupported')
        package=package.resolve(strict=True)
        _,checkpoints=verify_flow_package(package)
        manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
        from backend.engine.runtime_release_evidence import verify_release_evidence
        device=device or manifest.get('runtime',{}).get('device','cpu')
        evidence=verify_release_evidence(package,device)
        from backend.engine.release_eligibility import authorize_release_action, release_authority
        approvals=authorize_release_action(package,project,action='stage')
        digest=hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest()
        destination=self.releases/digest
        if destination.is_symlink():raise ValueError('Staged release directory is linked')
        if not destination.exists():
            temporary=self.releases/('.stage-'+uuid.uuid4().hex)
            try:
                temporary.mkdir()
                # Copy only checksum-bound package files; unlisted material is never staged.
                for relative in ['manifest.json', *(['parity_receipt.json'] if evidence['receipt_kind']=='flow_parity' else []), *[row['path'] for row in manifest['files']]]:
                    origin=package/relative;target=temporary/relative
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(origin,target,follow_symlinks=False)
                verify_release_evidence(temporary,device,expected_receipt_sha256=evidence['receipt_sha256'])
                if hashlib.sha256((temporary/'manifest.json').read_bytes()).hexdigest()!=digest:raise ValueError('Package changed while staging')
                os.rename(temporary,destination)
            finally:
                if temporary.exists():shutil.rmtree(temporary)
        policy=self.releases/(digest+'.policy.json')
        if policy.is_symlink():raise ValueError('Staged release policy is linked')
        verify_release_evidence(destination,device,expected_receipt_sha256=evidence['receipt_sha256'])
        with release_authority(project):
            # Copy/parity work can outlive a truth edit or approval revocation.
            authorize_release_action(destination,project,action='stage')
            policy_payload={'schema_version':1,'manifest_sha256':digest,'approval_revisions':approvals,
                            'device':device}
            if evidence['receipt_kind']=='flow_parity':policy_payload['parity_receipt_sha256']=evidence['receipt_sha256']
            if manifest.get('runtime_acceptance_sha256'):policy_payload['runtime_acceptance_sha256']=manifest['runtime_acceptance_sha256']
            if policy.exists() and json.loads(policy.read_text(encoding='utf-8'))!=policy_payload:raise ValueError('Existing release policy differs')
            if not policy.exists():
                with policy.open('x',encoding='utf-8') as writer:json.dump(policy_payload,writer)
                policy.chmod(0o600)
            _verify_release_policy(destination,verify_flow_package(destination)[1],policy,device=device)
            return {'package_path':str(destination),'release_policy':str(policy),'manifest_sha256':digest,'device':device,
                    'approval_revisions':approvals,
                    ('parity_receipt_sha256' if evidence['receipt_kind']=='flow_parity' else 'runtime_acceptance_sha256'):evidence['receipt_sha256'],
                    'acceptance_contract':evidence['receipt_kind'],
                    'parity_cohort_sha256':evidence['cohort_sha256'],'input_root':project.get('source_dataset_dir')}
    @serialized_lifecycle
    def start(self,release=None,*,recover=True):
        if recover and not self.config.get('native_label'):
            from backend.engine.native_autostart import NativeAutostart
            native=NativeAutostart(self);journal=native._journal()
            if journal and journal['status']=='registering' and native.query()['registered']:
                # Explicit start reconciles an owned registration that survived
                # a crash before service.json publication, avoiding a second daemon.
                native.install()
        if recover:
            recovery=self.ledger.recover(self.apply_runtime)
            if recovery and recovery['status']=='interrupted_without_previous':
                self.stop()
                raise ValueError('Interrupted initial deployment has no accepted release; apply an approved package explicitly')
        release=release or (self.ledger.active() or {}).get('release')
        if release:
            self.validate_accepted_device(release['package_path'],release.get('device','cpu'),expected_receipt_sha256=(release.get('parity_receipt_sha256') or release.get('runtime_acceptance_sha256')))
            _verify_release_policy(Path(release['package_path']),verify_flow_package(Path(release['package_path']))[1],Path(release['release_policy']),device=release.get('device','cpu'))
        if self.owned_process():
            readback=self.readback()
            if recover and release:
                self.ledger._validate_ack(release,readback)
            return readback
        if self.config.get('native_label'):
            from backend.engine.native_autostart import NativeAutostart
            NativeAutostart(self).install()
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                result=self.readback()
                if result.get('status')=='ready':
                    if recover and release:self.ledger._validate_ack(release,result)
                    return result
                time.sleep(.1)
            raise TimeoutError('Native service registered but runtime readiness timed out; inspect service.log')
        if release is None:raise ValueError('Apply an approved package before starting the service')
        self.validate_accepted_device(release['package_path'],release.get('device','cpu'),expected_receipt_sha256=(release.get('parity_receipt_sha256') or release.get('runtime_acceptance_sha256')))
        resolve_runtime_device(release.get('device','cpu'))
        from backend.engine.service_bootstrap import runtime_command, runtime_cwd
        arguments=runtime_command(['--package',release['package_path'],'--state-dir',str(self.root/'state'),'--runtime-root',str(self.releases),'--release-policy',release['release_policy'],'--require-approved-release','--device',release.get('device','cpu'),'--port',str(self.config['port'])])
        if release.get('input_root'):arguments+=['--input-root',release['input_root']]
        arguments+=self.input_arguments(release)
        adapter_path=self.root/'adapters.json'
        if adapter_path.exists():arguments+=['--adapter-config',str(adapter_path)]
        env=dict(os.environ);env['VISION_INSPECTION_TOKEN']=self.config['token']
        # Runtime modules are loaded from this checkout, never from untrusted release code.
        checkout=runtime_cwd()
        log=(self.root/'service.log').open('ab')
        try:process=subprocess.Popen(arguments,cwd=checkout,env=env,stdout=log,stderr=subprocess.STDOUT,**session_isolation())
        finally:log.close()
        try:self.config.update(process_identity(process,self.root/'state'));self.save(self.config)
        except Exception:
            process.terminate();process.wait(timeout=10);raise
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('Managed service exited; inspect its local service.log')
            result=self.readback()
            if result.get('status')=='ready':
                if recover:self.ledger._validate_ack(release,result)
                return result
            time.sleep(.1)
        self.stop();raise TimeoutError('Managed service readiness timed out')
    @serialized_lifecycle
    def stop(self):
        process=self.owned_process()
        if self.config.get('native_label'):
            from backend.engine.native_autostart import NativeAutostart
            return {**NativeAutostart(self).stop(),'status':'stopped'}
        if process:
            process.terminate()
            try:process.wait(timeout=12)
            except psutil.TimeoutExpired:raise RuntimeError('Service is stopping; jobs remain recoverable')
        self.config.update(pid=None,process_created_at=None,process_command_sha256=None);self.save(self.config)
        return {'status':'stopped'}
    @serialized_lifecycle
    def apply_runtime(self,release):
        _,checkpoints=verify_flow_package(Path(release['package_path']))
        _verify_release_policy(Path(release['package_path']),checkpoints,Path(release['release_policy']),device=release.get('device','cpu'))
        self.start(release,recover=False)
        with self.client() as client:
            response=client.post('/v1/runtime/apply',json=release);response.raise_for_status()
            ack=client.get('/v1/runtime');ack.raise_for_status();return ack.json()
    @serialized_lifecycle
    def apply(self,package,device,reviewer,project):
        self.validate_accepted_device(package,device)
        release={**self.stage(package,project,str(resolve_runtime_device(device))),'device':str(resolve_runtime_device(device))}
        previous = self.ledger.active()
        try: return self.ledger.apply(release,self.apply_runtime,reviewer=reviewer)
        except Exception:
            if previous is None: self.stop()
            raise
    @serialized_lifecycle
    def rollback(self,deployment_id,reviewer):
        return self.ledger.rollback(deployment_id,self.apply_runtime,reviewer=reviewer)
    def read_adapter_config(self):
        from backend.engine.field_adapters import load_adapter_config
        config=load_adapter_config(self.root/'adapters.json' if (self.root/'adapters.json').exists() else None).model_dump()
        if config.get('mes'):config['mes']['token']=None
        config['clear_mes_token']=False
        return config
    @serialized_lifecycle
    def configure_adapters(self,config):
        from backend.engine.field_adapters import FieldAdapterConfig,ModbusTCPAdapter,HTTPMESAdapter
        checked=FieldAdapterConfig.model_validate(config)
        previous=self.root/'adapters.json'
        if checked.mes:
            saved=json.loads(previous.read_text(encoding='utf-8')).get('mes') if previous.is_file() else None
            if checked.clear_mes_token:checked.mes.token=None
            elif checked.mes.token is None and saved and saved.get('token'):
                if saved.get('url')!=checked.mes.url:
                    raise ValueError('Changing the MES URL requires a new token or clear_mes_token=true')
                checked.mes.token=saved['token']
        checked.clear_mes_token=False
        if checked.modbus:ModbusTCPAdapter(checked.modbus)
        if checked.mes:HTTPMESAdapter(checked.mes)
        # Configuration is saved without network calls. Apply by explicit service restart.
        atomic_private_json(self.root/'adapters.json',checked.model_dump())
        return {'saved':True,'restart_required':self.owned_process() is not None}
    @serialized_lifecycle
    def install_files(self):
        if self.ledger.active() is None:raise ValueError('An approved applied release is required')
        from backend.engine.native_autostart import NativeAutostart
        return NativeAutostart(self).prepare()

    @serialized_lifecycle
    def activate_install(self):
        if self.ledger.active() is None:raise ValueError('An approved applied release is required')
        from backend.engine.native_autostart import NativeAutostart
        previous_native=bool(self.config.get('native_label'))
        previous_running=self.owned_process() is not None
        if not previous_native:self.stop()
        try:return NativeAutostart(self).install()
        except Exception:
            if previous_running and not previous_native:
                # Registration failure must not leave the accepted independent
                # service stopped merely because its startup handoff failed.
                self.start()
            raise

    @serialized_lifecycle
    def uninstall_native(self):
        from backend.engine.native_autostart import NativeAutostart
        return NativeAutostart(self).remove()
