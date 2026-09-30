"""Atomic verified package selector used by the independent service."""
from __future__ import annotations
import hashlib
import json
import os
import threading
from pathlib import Path
from backend.engine.flow_package_runtime import verify_flow_package
from backend.engine.runtime_device import resolve_runtime_device


class ServiceRuntime:
    def __init__(self,package,state_dir,device,policy,runtime_root,verify_policy):
        self.lock=threading.RLock()
        self.state_file=Path(state_dir)/'runtime.json'
        self.runtime_root=Path(runtime_root).resolve() if runtime_root else None
        self.verify_policy=verify_policy
        self.identity=None
        if self.state_file.exists():
            prior=json.loads(self.state_file.read_text())
            if self.runtime_root is None: raise ValueError('Recovered runtime requires a managed release root')
            self.apply(prior['package_path'],prior.get('release_policy'),prior['device'],prior['manifest_sha256'],persist=False)
        else:
            self.apply(package,policy,device,None,persist=False,initial=True)
    def apply(self,package,policy,device,expected_manifest,persist=True,initial=False):
        package=Path(package)
        if package.is_symlink():raise ValueError('Runtime package cannot be a symbolic link')
        package=package.resolve(strict=True)
        if policy and Path(policy).is_symlink():raise ValueError('Runtime release policy cannot be a symbolic link')
        policy=Path(policy).resolve(strict=True) if policy else None
        if not initial:
            if self.runtime_root is None or not package.is_relative_to(self.runtime_root) or policy is None or not policy.is_relative_to(self.runtime_root):
                raise ValueError('Runtime apply requires a release and policy under the managed root')
        pipeline,checkpoints=verify_flow_package(package)
        if policy:self.verify_policy(package,checkpoints,policy)
        elif not initial:raise ValueError('Runtime apply requires approved release policy')
        digest=hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest()
        if expected_manifest is not None and digest!=expected_manifest:raise ValueError('Runtime manifest hash mismatch')
        selected=str(resolve_runtime_device(device))
        identity={'status':'ready','package_path':str(package),'release_policy':str(policy) if policy else None,'device':selected,'manifest_sha256':digest,'pipeline_id':pipeline.id,
                  'model_sha256':{job:hashlib.sha256(path.read_bytes()).hexdigest() for job,path in checkpoints.items()}}
        with self.lock:
            if persist:
                temporary=self.state_file.with_suffix('.tmp')
                with temporary.open('w') as writer:
                    json.dump(identity,writer);writer.flush();os.fsync(writer.fileno())
                os.replace(temporary,self.state_file)
            self.identity=identity
        return dict(identity)
    def read(self):
        with self.lock:return dict(self.identity)
