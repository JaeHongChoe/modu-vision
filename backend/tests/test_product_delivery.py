"""Delivery workflows use real persisted packages, source bytes and protocol adapters."""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import asyncio
import httpx
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
import pytest
from PIL import Image
from backend.tests.test_runtime_deadline_sdk import real_package


def delivery():
    assert importlib.util.find_spec('backend.engine.product_delivery'), 'Persistent delivery workflow is missing'
    from backend.engine import product_delivery
    return product_delivery


def project(tmp_path):
    root=tmp_path/'project';root.mkdir(exist_ok=True)
    source=tmp_path/'source';source.mkdir(exist_ok=True)
    return {'id':'project-a','name':'Inspection','task':'segmentation','project_dir':str(root),'source_dataset_dir':str(source),'dataset_dir':str(root/'dataset'),'models_dir':str(root/'models')}


class ApiClient:
    def __init__(self,app):self.app=app
    def request(self,method,path,**kwargs):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:return await client.request(method,path,**kwargs)
        return asyncio.run(send())
    def get(self,path,**kwargs):return self.request('GET',path,**kwargs)
    def post(self,path,**kwargs):return self.request('POST',path,**kwargs)


class LocalPreflightTransport:
    """Map a validated Linux target to this test's owned directory on every host OS.

    Execute the actual decoder locally; no SSH or remote execution is claimed.
    """
    remote_root = '/controlled-preflight'

    def __init__(self, root, *, fail_upload=False):
        self.root = root.resolve()
        self.fail_upload = fail_upload

    def local_path(self, remote):
        relative = PurePosixPath(remote).relative_to(self.remote_root)
        destination = self.root.joinpath(*relative.parts).resolve()
        assert destination.is_relative_to(self.root)
        return destination

    def probe(self, profile):
        return {'ready': True, 'runtime_ready': True, 'checks': {'ssh': True, 'runtime': True}}

    def runtime_argv(self, profile, args, **kwargs):
        assert kwargs['gpu'] is False
        return [sys.executable, *args[:-1], str(self.local_path(args[-1]))]

    def exec(self, profile, args, **kwargs):
        if args[:3] == ['rm', '-rf', '--']:
            assert len(args) == 4
            scratch = self.local_path(args[3])
            assert scratch.parent == self.root / 'runs'
            assert re.fullmatch(r'preflight_[0-9a-f]{32}', scratch.name)
            if scratch.exists():
                shutil.rmtree(scratch)
            return SimpleNamespace(returncode=0, stderr='')
        assert args[0] == sys.executable
        return subprocess.run(args, capture_output=True, text=True, encoding='utf-8', timeout=30)

    def upload(self, profile, local, relative):
        destination = self.local_path(profile.remote_root + '/' + relative)
        destination.parent.mkdir(parents=True)
        if self.fail_upload:
            destination.write_bytes(b'partial')
            return SimpleNamespace(returncode=1, stderr='simulated disconnect')
        shutil.copyfile(local, destination)
        return SimpleNamespace(returncode=0, stderr='')


def test_package_library_reopens_receipt_and_rejects_other_project(real_package,tmp_path):
    module=delivery();p=project(tmp_path);package,image=real_package
    owned=Path(p['project_dir'])/'exports'/'flows'/'saved';owned.parent.mkdir(parents=True);shutil.copytree(package,owned)
    module.record_package(p,owned,version_id='a'*32,recipe_task='segmentation',parity={'status':'passed'})
    rows=module.package_library(p)['packages'];assert len(rows)==1
    row=rows[0];assert row['version_id']=='a'*32 and row['integrity']=='verified'
    reopened=module.select_package(p,row['package_id']);assert reopened['parity']['status']=='passed'
    assert module.package_library(p)['selected_package_id']==row['package_id']
    other={**p,'id':'other','source_dataset_dir':str(tmp_path/'other-source')}
    with pytest.raises(ValueError,match='scope|source|project'):module.select_package(other,row['package_id'])
    (owned/'pipeline.json').write_text('{}')
    assert module.package_library(p)['packages'][0]['integrity']=='failed'
    with pytest.raises(ValueError,match='checksum'):module.select_package(p,row['package_id'])


def test_package_library_does_not_follow_external_symlinks(real_package,tmp_path):
    module=delivery();p=project(tmp_path);package,_=real_package
    root=Path(p['project_dir'])/'exports'/'flows';root.mkdir(parents=True);(root/'external').symlink_to(package,target_is_directory=True)
    assert module.package_library(p)['packages']==[]


def test_real_input_preflight_runs_remote_decoder_without_a_training_job(tmp_path):
    module=delivery();p=project(tmp_path);image=Path(p['source_dataset_dir'])/'sample.png';Image.new('RGB',(17,19),'white').save(image)
    from backend.remote.profiles import ComputeProfile
    profile=ComputeProfile(id='server',name='Server',ssh_target='worker',ssh_port=22,remote_root='/workspace',runtime_kind='python',runtime_value='python3')
    class Transport:
        def probe(self,profile):return {'ready':True,'runtime_ready':True,'checks':{'ssh':True,'runtime':True}}
        def runtime_argv(self,profile,args,**kwargs):return [sys.executable,*args]
        def exec(self,profile,args,**kwargs):return subprocess.run(args,capture_output=True,text=True,timeout=15)
    report=module.server_preflight(p,profile,str(image),transport=Transport())
    assert report['ready'] and report['input']['width']==17 and report['input']['height']==19
    assert report['input']['local_sha256']==report['input']['remote_sha256']
    assert report['input']['execution']=='decode_and_tensor_cpu' and report['training_started'] is False
    assert module.server_preflight(p,profile,str(image),transport=Transport())['input']['local_sha256']==report['input']['local_sha256']
    outside=tmp_path/'outside.png';Image.new('RGB',(2,2)).save(outside)
    with pytest.raises(ValueError,match='source'):module.server_preflight(p,profile,str(outside),transport=Transport())


@pytest.mark.parametrize('protocol',['http','modbus'])
@pytest.mark.parametrize('mode',['success','reject','timeout'])
def test_local_receiver_exercises_real_adapter_ack_and_failures(protocol,mode):
    result=delivery().exercise_protocol(protocol,mode)
    assert result['protocol']==protocol and result['scope']=='loopback_contract_only'
    assert result['acknowledged'] is (mode=='success')
    assert result['received_job_id'] or protocol=='modbus'
    assert result['physical_equipment_verified'] is False


def test_diagnostics_redacts_secrets_paths_and_url_credentials(tmp_path):
    module=delivery();value={'token':'SECRET','Authorization':'Bearer SECRET','nested':{'password':'SECRET','image_path':'/private/customer/sample.png','endpoint':'https://user:SECRET@device.example/api?token=SECRET'},'message':'Authorization: Bearer SECRET /private/customer/sample.png'}
    redacted=module.redact_diagnostics(value)
    encoded=json.dumps(redacted);assert 'SECRET' not in encoded and '/private/customer' not in encoded
    assert redacted['nested']['endpoint']=='[URL]'
    p=project(tmp_path);report=module.installation_readiness(p)
    assert report['sdk']['C++']['requires_embedded_python'] is True
    assert report['sdk']['C#']['requires_embedded_python'] is True
    assert report['project']['compatible'] and report['project']['migration_performed'] is False
    (Path(p['project_dir'])/'project.json').write_text(json.dumps({'schema_version':999}))
    assert module.installation_readiness(p)['project']['compatible'] is False


def test_operator_review_persists_without_replacing_model_verdict(tmp_path):
    module=delivery();p=project(tmp_path);root=Path(p['project_dir'])/'runtime_service'/'state'
    from backend.engine.inspection_service import InspectionStore
    store=InspectionStore(root);image=Path(p['source_dataset_dir'])/'input.png';Image.new('RGB',(3,3)).save(image)
    identifier=store.enqueue(image,'file');store.claim();store.finish(identifier,result={'final_verdict':'REVIEW','roi_count':0})
    module.review_operator_result(p,identifier,'NG','Operator','Verified defect')
    rows=module.operator_records(p);assert rows[0]['model_verdict']=='REVIEW'
    assert rows[0]['operator_review']['verdict']=='NG'
    assert InspectionStore(root).get(identifier)['model_verdict']=='REVIEW'
    with pytest.raises(ValueError,match='job'):module.review_operator_result(p,'foreign','OK','Operator','Wrong scope')


def test_hardware_matrix_requires_execution_for_live_verified_state(tmp_path):
    module=delivery();p=project(tmp_path)
    matrix=module.hardware_matrix(p,capabilities={'torch_devices':['cpu','cuda:0'],'openvino':{'available':False,'devices':[]}})
    gpu=next(row for row in matrix['devices'] if row['device']=='cuda:0')
    assert gpu['configured'] and gpu['live_verified'] is False and gpu['approved'] is False
    assert any(row['kind']=='mig' and not row['live_verified'] for row in matrix['devices'])


def test_delivery_router_returns_scoped_library_and_redacted_download(tmp_path):
    assert importlib.util.find_spec('backend.api.routes_product_delivery'), 'Delivery workspace API is missing'
    from fastapi import FastAPI
    from backend.api.routes_product_delivery import router
    p=project(tmp_path);app=FastAPI();app.state.current_project=p;app.include_router(router);client=ApiClient(app)
    response=client.get('/api/product-delivery/packages');assert response.status_code==200,response.text
    assert response.json()['scope']['project_id']==p['id']
    response=client.post('/api/product-delivery/diagnostics');assert response.status_code==200,response.text
    assert '/private/' not in json.dumps(response.json()['bundle'])
    assert response.json()['bundle']['installation']['sdk']['C++']['requires_embedded_python'] is True
    assert client.post('/api/product-delivery/protocol-test',json={'protocol':'http','mode':'reject'}).json()['acknowledged'] is False


def test_operator_api_missing_service_keeps_explicit_input_health(tmp_path):
    assert importlib.util.find_spec('backend.api.routes_product_delivery'), 'Operator workspace API is missing'
    from fastapi import FastAPI
    from backend.api.routes_product_delivery import router
    p=project(tmp_path);app=FastAPI();app.state.current_project=p;app.include_router(router);client=ApiClient(app)
    state=client.get('/api/product-delivery/operator').json()
    assert state['input_health']['source_exists'] and state['service']['runtime']['status']=='stopped'
    assert state['runtime_matches_active'] is False and state['results']==[]
    image=Path(p['source_dataset_dir'])/'image.png';Image.new('RGB',(3,3)).save(image)
    assert client.post('/api/product-delivery/operator/inspect',json={'image_path':str(image)}).status_code==409


def test_hardware_execution_receipt_is_invalidated_by_changed_manifest(real_package,tmp_path):
    module=delivery();p=project(tmp_path);package,image=real_package
    owned=Path(p['project_dir'])/'exports'/'flows'/'saved';owned.parent.mkdir(parents=True);shutil.copytree(package,owned)
    module.record_package(p,owned,version_id='a'*32,parity={'status':'not_run'})
    module.record_execution(p,owned,image,{'final_verdict':'NG'},'cpu')
    capabilities={'torch_devices':['cpu'],'openvino':{'available':False,'devices':[]}}
    assert next(r for r in module.hardware_matrix(p,capabilities=capabilities)['devices'] if r['device']=='cpu')['live_verified']
    (owned/'manifest.json').write_text('{}')
    assert next(r for r in module.hardware_matrix(p,capabilities=capabilities)['devices'] if r['device']=='cpu')['live_verified'] is False


def test_operator_input_configuration_reopens_and_rejects_external_folder(tmp_path):
    module=delivery();p=project(tmp_path)
    assert hasattr(module,'configure_operator_inputs'), 'Persisted operator input setup is missing'
    saved=module.configure_operator_inputs(p,'folder',p['source_dataset_dir'],None)
    assert saved['restart_required'] is False and saved['config']['mode']=='folder'
    assert module.read_operator_inputs(p)['folder']==p['source_dataset_dir']
    with pytest.raises(ValueError,match='source'):module.configure_operator_inputs(p,'folder',str(tmp_path),None)
    assert module.read_operator_inputs(p)['folder']==p['source_dataset_dir']


def test_shared_accounts_keep_independent_package_selection(real_package,tmp_path):
    module=delivery();p=project(tmp_path);package,_=real_package
    directory=Path(p['project_dir'])/'exports'/'flows';directory.mkdir(parents=True)
    identifiers=[]
    for name in ('first','second'):
        owned=directory/name;shutil.copytree(package,owned);identifiers.append(module.record_package(p,owned,version_id='a'*32))
    first={**p,'_delivery_account_id':'first-account'};second={**p,'_delivery_account_id':'second-account'}
    module.select_package(first,identifiers[0]);module.select_package(second,identifiers[1])
    assert module.package_library(first)['selected_package_id']==identifiers[0]
    assert module.package_library(second)['selected_package_id']==identifiers[1]


def test_large_actual_source_preflight_transfers_bytes_and_cleans_owned_scratch(tmp_path):
    module=delivery();p=project(tmp_path);image=Path(p['source_dataset_dir'])/'large.png'
    import numpy as np
    Image.fromarray(np.random.default_rng(2).integers(0,255,(256,256,3),dtype=np.uint8)).save(image)
    assert image.stat().st_size>48*1024
    from backend.remote.profiles import ComputeProfile
    root=tmp_path/'remote';root.mkdir()
    profile=ComputeProfile(id='server',name='Server',ssh_target='worker',ssh_port=22,
                           remote_root=LocalPreflightTransport.remote_root,runtime_kind='python',runtime_value='python3')
    report=module.server_preflight(p,profile,str(image),transport=LocalPreflightTransport(root))
    assert report['ready'] and report['input']['width']==256 and report['input']['local_sha256']==report['input']['remote_sha256']
    assert list((root/'runs').iterdir())==[]


def test_diagnostics_download_contains_only_explicitly_selected_sections(tmp_path):
    from fastapi import FastAPI
    from backend.api.routes_product_delivery import router
    p=project(tmp_path);app=FastAPI();app.state.current_project=p;app.include_router(router);client=ApiClient(app)
    response=client.post('/api/product-delivery/diagnostics',json={'sections':['installation']})
    assert response.status_code==200,response.text
    bundle=response.json()['bundle'];assert 'installation' in bundle
    assert 'packages' not in bundle and 'hardware' not in bundle and 'operator_results' not in bundle
    assert client.post('/api/product-delivery/diagnostics',json={'sections':['source_images']}).status_code==422


def test_failed_preflight_transfer_removes_only_its_owned_scratch(tmp_path):
    module=delivery();p=project(tmp_path);image=Path(p['source_dataset_dir'])/'large.png'
    import numpy as np
    Image.fromarray(np.random.default_rng(7).integers(0,255,(256,256,3),dtype=np.uint8)).save(image)
    from backend.remote.profiles import ComputeProfile
    root=tmp_path/'remote';foreign=root/'runs'/'existing';foreign.mkdir(parents=True);(foreign/'keep').write_text('unrelated')
    profile=ComputeProfile(id='server',name='Server',ssh_target='worker',ssh_port=22,
                           remote_root=LocalPreflightTransport.remote_root,runtime_kind='python',runtime_value='python3')
    with pytest.raises(ValueError,match='transfer failed'):
        module.server_preflight(p,profile,str(image),transport=LocalPreflightTransport(root,fail_upload=True))
    assert list((root/'runs').iterdir())==[foreign]
    assert (foreign/'keep').read_text()=='unrelated'


def test_operator_response_permissions_prevent_viewer_controls(tmp_path):
    from fastapi import FastAPI
    from backend.api.routes_product_delivery import router
    p=project(tmp_path);app=FastAPI();app.include_router(router);app.state.accounts=SimpleNamespace(project_role=lambda *args:'viewer')
    @app.middleware('http')
    async def scope(request,call_next):
        request.state.scoped_project=p;request.state.account_user={'id':'viewer','username':'Viewer'}
        return await call_next(request)
    state=ApiClient(app).get('/api/product-delivery/operator').json()
    assert state['permissions']=={'can_control':False,'can_inspect':False,'can_review':False,'can_configure':False}


def test_saved_package_api_executes_actual_image_and_reopens_device_receipt(real_package,tmp_path):
    from fastapi import FastAPI
    from backend.api.routes_product_delivery import router
    module=delivery();p=project(tmp_path);package,original=real_package
    owned=Path(p['project_dir'])/'exports'/'flows'/'saved';owned.parent.mkdir(parents=True);shutil.copytree(package,owned)
    image=Path(p['source_dataset_dir'])/'input.png';shutil.copyfile(original,image)
    identifier=module.record_package(p,owned,version_id='a'*32)
    app=FastAPI();app.state.current_project=p;app.include_router(router);client=ApiClient(app)
    response=client.post('/api/product-delivery/packages/'+identifier+'/verify',json={'image_path':str(image),'device':'cpu'})
    assert response.status_code==200,response.text
    assert response.json()['result']['final_verdict']=='NG'
    assert response.json()['evidence']['image_sha256']==module._sha256(image)
    reopened=client.get('/api/product-delivery/hardware').json()
    cpu=next(row for row in reopened['devices'] if row['device']=='cpu')
    assert cpu['live_verified'] and not cpu['approved']


def test_support_bundle_redacts_url_paths_api_keys_and_private_endpoints():
    from backend.engine.product_delivery import redact_diagnostics
    token = 'ghp_' + 'a' * 36
    report = {'api_key': token,
              'url': 'https://private.example.invalid/' + token + '?password=hidden#fragment',
              'message': 'Connection to ' + '10.55.8.9' + ' failed; token ' + token,
              'malformed': 'https://endpoint.invalid:bad-port/path',
              'status': 'failed', 'build': 'sdk-1'}
    sanitized = redact_diagnostics(report)
    encoded = json.dumps(sanitized)
    assert token not in encoded and '10.55.8.9' not in encoded
    assert 'private.example.invalid' not in encoded and 'bad-port' not in encoded
    assert sanitized['status'] == 'failed' and sanitized['build'] == 'sdk-1'


def test_diagnostics_api_persists_only_the_sanitized_bundle(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from backend.api.routes_product_delivery import router
    module=delivery();p=project(tmp_path);app=FastAPI();app.state.current_project=p;app.include_router(router)
    token='ghp_'+'z'*36
    monkeypatch.setattr(module,'installation_readiness',lambda _: {'api_key':token,'endpoint':'https://private.example.invalid:bad-port/'+token,'status':'failed'})
    response=ApiClient(app).post('/api/product-delivery/diagnostics',json={'sections':['installation']})
    assert response.status_code==200,response.text
    value=response.json();bundle=value['bundle']
    persisted=json.loads((Path(p['project_dir'])/'delivery'/'diagnostics.json').read_bytes())
    assert persisted==bundle and value['includes_source_images'] is False and value['redacted'] is True
    assert bundle['installation']=={'api_key':'[REDACTED]','endpoint':'[URL]','status':'failed'}
    assert token not in json.dumps(persisted)


def _checksum_only_converted_library_package(tmp_path, monkeypatch, *, receipt_source='current'):
    """Scope regression with real graph/inventory checks; never execute/load models."""
    import hashlib
    import torch
    from backend.engine import flowchart_engine
    def forbidden_model(*args, **kwargs):
        raise AssertionError('Scope readback must not load, construct or execute a model')
    monkeypatch.setattr(torch, 'load', forbidden_model)
    for name in ('create_classification_model', 'create_detection_model', 'build_segmentation_model'):
        monkeypatch.setattr(flowchart_engine, name, forbidden_model)
    monkeypatch.setattr(flowchart_engine, 'FlowchartEngine', forbidden_model)
    p=project(tmp_path);p['task']='classification'
    package=Path(p['project_dir'])/'exports'/'flows'/'converted';package.mkdir(parents=True)
    graph=flowchart_engine.get_single_segmentation_flowchart('job_scope_only')
    graph.nodes[1].data.task='classification';graph.nodes[1].data.params={}
    job='job_scope_only';checkpoint=f'models/{job}/best_model.pt';directory=f'models/{job}/openvino'
    content={'pipeline.json':json.dumps(graph.model_dump(mode='json')).encode(),
             'run_flow.py':b'# Inert runner: must never execute in a scope readback test.\n',
             checkpoint:b'opaque checkpoint bytes; no torch serialization or model',
             directory+'/model.xml':b'inert IR bytes; no compilation',directory+'/model.bin':b'inert weights',
             directory+'/conversion.json':b'{}'}
    receipt={} if receipt_source is None else {'source_dataset_path':p['source_dataset_dir'] if receipt_source=='current' else str(tmp_path/'foreign-source'),
                                            'source_fingerprint':'v1:'+'0'*64,'split_manifest_path':None,'split_manifest_sha256':None,
                                            'calibration_images':[],'validation_images':[]}
    content['openvino_models.json']=json.dumps({'models':[{'job_id':job,'task':'classification','checkpoint':checkpoint,'directory':directory}],
                                             'input_receipt':receipt}).encode()
    for name,data in content.items():
        target=package/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    manifest={'schema_version':1,'models':[{'job_id':job,'task':'classification','checkpoint':checkpoint}],
              'files':[{'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in content.items()]}
    (package/'manifest.json').write_text(json.dumps(manifest))
    return p,package,graph


@pytest.mark.parametrize('versions_state',['missing','empty'])
def test_converted_library_receipt_without_saved_flow_versions_has_current_scope(tmp_path,monkeypatch,versions_state):
    module=delivery();p,package,_=_checksum_only_converted_library_package(tmp_path,monkeypatch)
    versions=Path(p['project_dir'])/'flowcharts'/'versions'
    if versions_state=='empty':versions.mkdir(parents=True)
    else:assert not versions.exists()
    before={file.relative_to(package).as_posix():file.read_bytes() for file in package.rglob('*') if file.is_file()}
    row=module.package_library(p)['packages'][0]
    assert row['integrity']=='verified' and row['scope_matches'] is True
    assert row['recipe_task']=='classification' and row['approval_present'] is False and row['version_id'] is None
    assert module.select_package(p,row['package_id'])==row
    assert module.package_library(p)['selected_package_id']==row['package_id']
    assert before=={file.relative_to(package).as_posix():file.read_bytes() for file in package.rglob('*') if file.is_file()}
    assert versions_state=='empty' or not versions.exists(),'Scope fallback must not create a saved flow/version'


@pytest.mark.parametrize('receipt_source',['foreign',None])
def test_converted_library_receipt_without_saved_flow_versions_rejects_foreign_or_missing_source(tmp_path,monkeypatch,receipt_source):
    module=delivery();p,_,_=_checksum_only_converted_library_package(tmp_path,monkeypatch,receipt_source=receipt_source)
    row=module.package_library(p)['packages'][0]
    assert row['integrity']=='verified' and row['scope_matches'] is False
    with pytest.raises(ValueError,match='scope'):module.select_package(p,row['package_id'])


@pytest.mark.parametrize('obstruction',['file','link'])
def test_converted_library_receipt_keeps_saved_flow_version_path_obstruction_refusal(tmp_path,monkeypatch,obstruction):
    module=delivery();p,_,_=_checksum_only_converted_library_package(tmp_path,monkeypatch)
    versions=Path(p['project_dir'])/'flowcharts'/'versions';versions.parent.mkdir()
    if obstruction=='file':versions.write_text('original obstruction')
    else:
        foreign=tmp_path/'foreign-versions';foreign.mkdir();versions.symlink_to(foreign,target_is_directory=True)
    row=module.package_library(p)['packages'][0]
    assert row['integrity']=='verified' and row['scope_matches'] is False
    with pytest.raises(ValueError,match='scope'):module.select_package(p,row['package_id'])
    assert versions.is_symlink() if obstruction=='link' else versions.read_text()=='original obstruction'


@pytest.mark.parametrize('obstruction',['file','link','dangling-link'])
def test_converted_library_receipt_keeps_absent_versions_parent_obstruction_refusal(tmp_path,monkeypatch,obstruction):
    module=delivery();p,_,_=_checksum_only_converted_library_package(tmp_path,monkeypatch)
    parent=Path(p['project_dir'])/'flowcharts'
    if obstruction=='file':parent.write_text('original parent obstruction')
    else:
        foreign=tmp_path/'foreign-flowcharts'
        if obstruction=='link':foreign.mkdir()
        parent.symlink_to(foreign,target_is_directory=True)
    row=module.package_library(p)['packages'][0]
    assert row['integrity']=='verified' and row['scope_matches'] is False
    with pytest.raises(ValueError,match='scope'):module.select_package(p,row['package_id'])
    assert parent.is_symlink() if obstruction!='file' else parent.read_text()=='original parent obstruction'


def test_converted_library_retains_existing_matching_saved_flow_scope(tmp_path,monkeypatch):
    module=delivery();p,_,graph=_checksum_only_converted_library_package(tmp_path,monkeypatch,receipt_source='foreign')
    versions=Path(p['project_dir'])/'flowcharts'/'versions';versions.mkdir(parents=True)
    version='a'*32;(versions/(version+'.json')).write_text(json.dumps({'version_id':version,'recipe_task':'classification',
        'source_dataset_path':p['source_dataset_dir'],'pipeline':graph.model_dump(mode='json')}))
    row=module.package_library(p)['packages'][0]
    assert row['integrity']=='verified' and row['scope_matches'] is True and row['version_id']==version
    assert module.select_package(p,row['package_id'])==row


def test_converted_library_fallback_never_accepts_changed_sealed_receipt(tmp_path,monkeypatch):
    module=delivery();p,package,_=_checksum_only_converted_library_package(tmp_path,monkeypatch)
    (package/'openvino_models.json').write_text(json.dumps({'models':[],'input_receipt':{'source_dataset_path':p['source_dataset_dir']}}))
    row=module.package_library(p)['packages'][0]
    assert row['integrity']=='failed' and row['scope_matches'] is False
    with pytest.raises(ValueError,match='checksum'):module.select_package(p,row['package_id'])
