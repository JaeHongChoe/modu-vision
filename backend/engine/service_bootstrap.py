"""Trusted startup resolves the current approved deployment, never a pinned package."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from backend.engine.managed_service import ManagedService
from backend.engine.flow_package_runtime import verify_flow_package
from backend.engine.inspection_service import _verify_release_policy
from backend.engine.runtime_device import resolve_package_device as resolve_runtime_device


def runtime_command(arguments):
    return ([sys.executable,'--inspection-service'] if getattr(sys,'frozen',False)
            else [sys.executable,'-m','backend.engine.inspection_service'])+arguments


def launch_command(project_dir):
    return ([sys.executable,'--managed-service-project',str(Path(project_dir).resolve())]
            if getattr(sys,'frozen',False) else
            [sys.executable,'-m','backend.engine.service_bootstrap','--project-dir',str(Path(project_dir).resolve())])


def runtime_cwd():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]


def trusted_runtime_identity():
    """Bind diagnostics to this trusted checkout or this frozen runtime build."""
    if getattr(sys,'frozen',False):
        inventory=Path(getattr(sys,'_MEIPASS',runtime_cwd()))/'backend-build-inventory.json'
        if not inventory.is_file():return {'mode':'frozen','status':'inventory_missing'}
        value=json.loads(inventory.read_text(encoding='utf-8'))
        return {'mode':'frozen','status':'identified','build_identity_sha256':value['build_identity_sha256'],
                'inventory_sha256':hashlib.sha256(inventory.read_bytes()).hexdigest()}
    root=runtime_cwd();digest=hashlib.sha256()
    for source in sorted((root/'backend').rglob('*.py')):
        if {'tests','__pycache__','.pytest_cache'}.intersection(source.parts):continue
        digest.update(str(source.relative_to(root)).encode());digest.update(hashlib.sha256(source.read_bytes()).digest())
    return {'mode':'source','status':'identified','source_sha256':digest.hexdigest()}


def bootstrap_command(project_dir):
    service=ManagedService(project_dir)
    active=service.ledger.active()
    if not active:raise ValueError('Apply an approved release before automatic startup')
    pending=service.ledger.diagnostics()['pending']
    if pending:
        # Native startup is allowed to restore the committed package, never the
        # unaccepted candidate recorded in state/runtime.json. The manager will
        # reconcile the durable operation after an exact live acknowledgment.
        state=service.root/'state'/'runtime.json'
        state.unlink(missing_ok=True)
    release=active['release'];package=Path(release['package_path']);policy=Path(release['release_policy'])
    _,checkpoints=verify_flow_package(package)
    _verify_release_policy(package,checkpoints,policy,device=release.get('device','cpu'))
    digest=hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest()
    if digest!=release.get('manifest_sha256'):raise ValueError('Active deployment manifest differs from its release receipt')
    service.validate_accepted_device(release['package_path'],release.get('device','cpu'),expected_receipt_sha256=(release.get('parity_receipt_sha256') or release.get('runtime_acceptance_sha256')))
    resolve_runtime_device(release.get('device','cpu'))
    args=['--package',release['package_path'],'--state-dir',str(service.root/'state'),
          '--runtime-root',str(service.root/'releases'),'--release-policy',release['release_policy'],
          '--require-approved-release','--device',release.get('device','cpu'),'--port',str(service.config['port'])]
    if release.get('input_root'):args+=['--input-root',release['input_root']]
    args+=service.input_arguments(release)
    if (service.root/'adapters.json').is_file():args+=['--adapter-config',str(service.root/'adapters.json')]
    if service.config.get('native_kind')=='windows_scm' and service.config.get('scm_configuration',{}).get('warmup_image'):
        args+=['--warmup-image',service.config['scm_configuration']['warmup_image']]
    env=dict(os.environ);env['VISION_INSPECTION_TOKEN']=service.config['token']
    env['PYTHONDONTWRITEBYTECODE']='1'
    return runtime_command(args),env


def main(project_dir=None):
    if project_dir is None:
        parser=argparse.ArgumentParser(description='Start the current approved project inspection release')
        parser.add_argument('--project-dir',required=True,type=Path)
        project_dir=parser.parse_args().project_dir
    command,env=bootstrap_command(project_dir)
    log_path=Path(project_dir).resolve()/'runtime_service'/'service.log'
    if log_path.is_symlink():raise ValueError('Service log cannot be linked')
    descriptor=os.open(log_path,os.O_CREAT|os.O_WRONLY|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600)
    try:os.dup2(descriptor,1);os.dup2(descriptor,2)
    finally:os.close(descriptor)
    os.chdir(runtime_cwd());os.execve(command[0],command,env)


if __name__=='__main__':main()
