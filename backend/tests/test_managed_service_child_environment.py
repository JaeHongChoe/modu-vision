"""Source/frozen child-env seam regression, never approval/native acceptance.

Popen and release/readback dependencies are bounded unit seams; no process,
app, API, GPU or daemon starts. Real launch methods must still invoke the
recorded validation/ownership steps. Only synthetic sentinel capabilities are
used; failures print variable names, never parent environments or token values.
"""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.engine import managed_service,service_bootstrap

APP_PRIVATE=('VISION_AI_STUDIO_USER_DATA_DIR','VISION_APPLICATION_LAUNCH_NONCE',
    'VISION_APPLICATION_GENERATION','VISION_APPLICATION_DATABASE_GENERATION',
    'VISION_APPLICATION_BACKEND_FD','VISION_APPLICATION_LAUNCH_FD',
    'VISION_AI_STUDIO_API_TOKEN')

def _seed(monkeypatch,tmp_path):
    for index,key in enumerate(APP_PRIVATE):
        monkeypatch.setenv(key,'fixture-private-'+str(index))
    policy={'CUDA_VISIBLE_DEVICES':'fixture-gpu-selection','NVIDIA_VISIBLE_DEVICES':'fixture-device-policy',
        'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_DATASETS_OFFLINE':'1',
        'OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2',
        'HOME':str(tmp_path/'runtime-home'),'TMPDIR':str(tmp_path/'runtime-temp'),
        'VISION_RESULT_WEBHOOK_TOKEN':'fixture-webhook-capability',
        'VISION_SCM_START_HANDLE':'fixture-scm-handle',
        'VISION_APPLICATION_UNRELATED_POLICY':'fixture-unrelated-policy',
        'VISION_INSPECTION_TOKEN':'parent-unrelated-service-capability'}
    for key,value in policy.items():monkeypatch.setenv(key,value)
    return policy,dict(os.environ)

def _assert_child(child,parent,policy,token):
    for key in APP_PRIVATE:
        if key in child:pytest.fail('Independent child inherited '+key,pytrace=False)
    for key,value in policy.items():
        if key=='VISION_INSPECTION_TOKEN':continue
        if child.get(key)!=value:pytest.fail('Execution policy changed: '+key,pytrace=False)
    if child.get('VISION_INSPECTION_TOKEN')!=token:pytest.fail('Child lacks its own service capability',pytrace=False)
    if dict(os.environ)!=parent:pytest.fail('Parent environment was changed',pytrace=False)

@pytest.mark.parametrize('frozen',[False,True])
def test_managed_start_strips_private_application_authority_only_for_its_child(tmp_path,monkeypatch,frozen):
    # Construct ordinary local state before adding modeled foreground context.
    service=managed_service.ManagedService(tmp_path)
    service.config['token']='fixture-own-service-capability'
    service.save(service.config)
    events=[];captured=[];process=SimpleNamespace(pid=123,poll=lambda:None)
    release={'package_path':str(tmp_path/'package'),'release_policy':str(tmp_path/'policy'),'device':'cpu'}
    def validate(*args,**kwargs):events.append('device-validation')
    def policy(*args,**kwargs):events.append('release-policy')
    def resolved(device):
        assert device=='cpu'
        events.append('device-resolution');return 'cpu'
    def popen(command,**kwargs):
        captured.append((command,kwargs));events.append('Popen');return process
    def identity(original,state):
        assert original is process and state==service.root/'state'
        events.append('original-process-identity')
        return {'pid':process.pid,'process_created_at':1.0,'process_command_sha256':'fixture-command-digest'}
    def save(value):
        events.append('save-original-identity')
        service.config=dict(value)
    def readback():
        events.append('readback');return {'status':'ready'}
    monkeypatch.setattr(service,'validate_accepted_device',validate)
    monkeypatch.setattr(managed_service,'verify_flow_package',lambda _:('fixture-pipeline',{}))
    monkeypatch.setattr(managed_service,'_verify_release_policy',policy)
    monkeypatch.setattr(managed_service,'resolve_runtime_device',resolved)
    monkeypatch.setattr(service,'owned_process',lambda:None)
    monkeypatch.setattr(service,'readback',readback)
    monkeypatch.setattr(service,'save',save)
    monkeypatch.setattr(service,'input_arguments',lambda _release:[])
    monkeypatch.setattr(managed_service.subprocess,'Popen',popen)
    monkeypatch.setattr(managed_service,'process_identity',identity)
    monkeypatch.setattr(service_bootstrap.sys,'frozen',frozen,raising=False)
    policy_values,parent=_seed(monkeypatch,tmp_path)
    assert service.start(release,recover=False)=={'status':'ready'}
    assert events==['device-validation','release-policy','device-validation','device-resolution',
                    'Popen','original-process-identity','save-original-identity','readback']
    assert len(captured)==1
    command,kwargs=captured[0]
    prefix=[service_bootstrap.sys.executable,'--inspection-service'] if frozen else [
        service_bootstrap.sys.executable,'-m','backend.engine.inspection_service']
    assert command[:len(prefix)]==prefix
    assert '--require-approved-release' in command and '--release-policy' in command
    assert kwargs['cwd']==service_bootstrap.runtime_cwd()
    _assert_child(kwargs['env'],parent,policy_values,service.config['token'])

@pytest.mark.parametrize('frozen',[False,True])
def test_automatic_bootstrap_command_uses_same_child_only_environment(tmp_path,monkeypatch,frozen):
    package=tmp_path/'package';package.mkdir()
    manifest=package/'manifest.json';manifest.write_text(json.dumps({'fixture':'unit-parity-seam'}))
    root=tmp_path/'runtime-service';root.mkdir()
    releases=root/'releases';releases.mkdir()
    release={'package_path':str(package),'release_policy':str(releases/'policy.json'),
             'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
             'device':'cpu','parity_receipt_sha256':'fixture-parity-receipt'}
    events=[]
    def verify(*args,**kwargs):events.append('release-policy')
    def validate(package,device,**kwargs):
        assert device=='cpu' and kwargs=={'expected_receipt_sha256':'fixture-parity-receipt'}
        events.append('device-validation')
    service=SimpleNamespace(root=root,releases=releases,
        config={'port':12345,'token':'fixture-own-service-capability'},
        ledger=SimpleNamespace(active=lambda:{'release':release},diagnostics=lambda:{'pending':False}),
        validate_accepted_device=validate,input_arguments=lambda _:[])
    monkeypatch.setattr(service_bootstrap,'ManagedService',lambda _:service)
    monkeypatch.setattr(service_bootstrap,'verify_flow_package',lambda _:('fixture-pipeline',{}))
    monkeypatch.setattr(service_bootstrap,'_verify_release_policy',verify)
    monkeypatch.setattr(service_bootstrap,'resolve_runtime_device',lambda device:device)
    monkeypatch.setattr(service_bootstrap.sys,'frozen',frozen,raising=False)
    policy_values,parent=_seed(monkeypatch,tmp_path)
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE','0');parent=dict(os.environ)
    command,child=service_bootstrap.bootstrap_command(tmp_path)
    assert events==['release-policy','device-validation']
    assert '--require-approved-release' in command and '--release-policy' in command
    assert child['PYTHONDONTWRITEBYTECODE']=='1'
    _assert_child(child,parent,policy_values,service.config['token'])

def test_standalone_environment_still_copies_policy_and_sets_own_capability(tmp_path,monkeypatch):
    policy_values,parent=_seed(monkeypatch,tmp_path)
    for key in APP_PRIVATE:monkeypatch.delenv(key,raising=False)
    parent=dict(os.environ)
    child=service_bootstrap.inspection_environment('fixture-own-service-capability')
    assert child is not os.environ
    _assert_child(child,parent,policy_values,'fixture-own-service-capability')
