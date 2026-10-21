"""Root-only tests-first fixed current capture cohort; no model/SSH/GPU execution.

Existing original factory/cache data seams are controlled as in
backend/tests/test_required_flow_http_framework.py. No capture source guard,
permission middleware, producer, handler or model implementation is replaced.
"""
from pathlib import Path
from backend.tests.private_qa_fixtures import private_qa_root

# External qualification is explicit; absence is a collection failure, never a skip.
J = private_qa_root()
import pytest
pytestmark = pytest.mark.private_qualification
import copy
import hashlib
import json
import os
import shutil
import stat

from starlette.requests import Request
# Original remote.worker imports the engine before any main/app factory.
from backend.engine import flowchart_engine as _original_worker_engine
from backend import main
from backend.engine import application_launch_handshake as handshake
from backend.engine import required_flow_passive_capture as capture
from backend.remote.profiles import ComputeProfile

CURRENT_PROFILE=json.loads((J/'S7-02-original-installed-manual-native-providers-Source08/retained-normalized-profile.json').read_bytes())
CURRENT_PROFILE_ID='server42-l40s-2-native43-20261016-01'
CURRENT_PROFILE_SHA='6f36f621864d3c6b327295f684118434c6c1d18ddee8b21f30071527b0e7ea49'
CURRENT_ENGINE={'size':151555,'sha256':'83bb9b25c7d66f91796cdbe6a83d6c13a3728991d76dd9cf7d3f73a27938f976'}
LEGACY_ENGINE={'size':148262,'sha256':'e62c8461a0f57d4ce8b4e49f5a0dbf21f50e25515e627f5cff778225cdcf1a70'}
LEGACY_PROFILE_ID='server42-l40s-2'
LEGACY_PROFILE_SHA='3e0ff9bf6a23791b64b4decd1d51fdbc2af8bb5d4d9ea37b9bc28c5d4c5813a1'
GRAPH=json.loads((J/'S7-02-installed-retained-rotation-segmentation-fixture-Source01/original-graph.json').read_bytes())
IMAGE=J/'S7-02-authentic-retained-family-Root-inputs-01/rotation-image.png'
QUALIFIED=json.loads((J/'Combined-R764-SOURCE6-current-source-qualification-Source01/qualification-ready-false.json').read_bytes())
CURRENT_PINS={name:{'size':row['size'],'sha256':row['sha256']} for name,row in QUALIFIED['current_SOURCE6_source_only_pins'].items()}
LEGACY_PACKAGE_RUNTIME={'size':35583,'sha256':'a55ca0b694d398bebe30af4916ccd43e0ab7ea115a9ae080af3bc313abcfeeec'}
LEGACY_PINS={**CURRENT_PINS,'backend/engine/flowchart_engine.py':LEGACY_ENGINE,
    'backend/engine/flow_package_runtime.py':LEGACY_PACKAGE_RUNTIME}
assert CURRENT_PINS['backend/engine/flowchart_engine.py']==CURRENT_ENGINE


def owned_factory(tmp_path,monkeypatch,*,shared=False):
    root=(tmp_path/'owned-bootstrap-control').resolve();root.mkdir(mode=0o700)
    nonce='2'*32
    (root/'.application-runtime-homes').mkdir(mode=0o700)
    home=root/'.application-runtime-homes'/nonce;home.mkdir(mode=0o700)
    admission=handshake._new_original_work_admission()
    # Exact existing component seam: private bootstrap/cache data only.
    # No native/controller/bootstrap acceptance is asserted by these tests.
    monkeypatch.setattr(handshake,'_CACHE',{'root':root,'proof':{'nonce':nonce},'context':handshake._context(),'admission':admission,'ready':True})
    monkeypatch.setattr(main,'backend_work_admission',lambda:admission)
    kwargs={'project_dir':str(root/'projects')}
    if shared:kwargs['shared_auth_dir']=str(root/'accounts')
    app=main.create_app(**kwargs)
    return app,getattr(app.state,'required_retained_flow_capture',None),home/'required-flow-http'


def request_for(app):
    return Request({'type':'http','method':'POST','path':'/api/flowchart/run','query_string':b'',
                    'headers':[(b'x-vision-token',app.state.api_token.encode())],'app':app})


def model_rows():
    return [{'job_id':job,'task':task,'checkpoint_sha256':digest} for job,task,digest in capture.RETAINED_MODELS]


def current_grant_and_spec():
    # Fixed data only; validate_worker_grant cannot dispatch or authorize work.
    value={'schema':'modu-vision.required-retained-flow-passive-grant/v1','nonce':'a'*32,
           'profile_id':CURRENT_PROFILE_ID,'execution_profile_sha256':CURRENT_PROFILE_SHA,
           'pipeline_sha256':capture.RETAINED_GRAPH_SHA,'image_sha256':capture.RETAINED_IMAGE_SHA,
           'models':model_rows(),'source_pins':copy.deepcopy(CURRENT_PINS),
           'gpu_uuid':capture.RETAINED_GPU_UUID,'required_call':'FlowchartEngine.execute',
           'runtime_authority':False,'parent_credit':False}
    spec={'operation':'flowchart_run','portable_models':True,'stop_node_id':None,
          'comparison_operation_contract':None,'device':'cuda','execution_profile_sha256':CURRENT_PROFILE_SHA,
          'pipeline':GRAPH,'image_sha256':capture.RETAINED_IMAGE_SHA,'models':model_rows()}
    return value,spec


def test_current_owned_shared_factory_prepared_exists_before_first_request(tmp_path,monkeypatch):
    app,selector,output=owned_factory(tmp_path,monkeypatch,shared=True)
    assert app.state.accounts is not None
    assert selector is not None, 'Current qualified source must bind the original selector at factory startup'
    assert selector.shared_owner is not None and selector.shared_owner.eligible
    assert selector.http_capture is not None
    path=output/'prepared.json'
    assert path.is_file(), 'No request is issued before checking original startup preparation'
    prepared=json.loads(path.read_bytes())
    assert prepared['profile_id']==CURRENT_PROFILE_ID
    assert prepared['capture_nonce']==selector.nonce and prepared['process_pid']==os.getpid()
    assert prepared['request_budget_seconds']==60 and prepared['request_capture_limit']==256*1024
    assert type(prepared['response_capture_limit']) is int and prepared['response_capture_limit']==32*1024*1024
    assert all(prepared[key] is False for key in ('permission_authority','lease_authority','parent_credit'))
    assert not selector.used and selector.issued is None
    assert stat.S_IMODE(output.stat().st_mode)==0o700 and stat.S_IMODE(path.stat().st_mode)==0o600
    assert not any('passive' in getattr(route,'path','') for route in app.routes)


def test_current_profile_offer_one_use_serialization_and_worker_data_use_same_exact_sources(tmp_path,monkeypatch):
    app,selector,_=owned_factory(tmp_path,monkeypatch)
    assert selector is not None
    profile=ComputeProfile(**CURRENT_PROFILE)
    # Exact original remote.operations producer codec: default ASCII unchanged.
    actual_sha=hashlib.sha256(json.dumps(profile.model_dump(),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert actual_sha==capture._graph_sha(profile.model_dump())==CURRENT_PROFILE_SHA
    carrier=capture.offer_original_required_flow_capture(selector,request_for(app),profile,
        pipeline=GRAPH,image_path=IMAGE,device='cuda',stop_node_id=None)
    assert carrier is not None
    grant=capture.serialize_required_flow_grant(carrier,profile_id=profile.id,
        execution_profile_sha256=actual_sha,pipeline=GRAPH,image_sha256=capture.RETAINED_IMAGE_SHA,models=model_rows())
    assert grant['source_pins']==CURRENT_PINS
    assert grant['profile_id']==CURRENT_PROFILE_ID and grant['execution_profile_sha256']==CURRENT_PROFILE_SHA
    assert grant['runtime_authority'] is False and grant['parent_credit'] is False
    _,spec=current_grant_and_spec()
    assert capture.validate_worker_grant(grant,spec)==grant
    assert carrier.used and selector.used
    with pytest.raises(ValueError):
        capture.serialize_required_flow_grant(carrier,profile_id=profile.id,
            execution_profile_sha256=actual_sha,pipeline=GRAPH,image_sha256=capture.RETAINED_IMAGE_SHA,models=model_rows())


def test_exact_current_and_historical_six_file_source_maps_remain_bound_and_unknown_bytes_refuse(tmp_path):
    source=Path(capture.__file__).resolve().parents[2]
    assert capture._verify_fixed_source_files(source) is not None
    clone=tmp_path/'source-copy';clone.mkdir(mode=0o700)
    for relative in CURRENT_PINS:
        path=clone/relative;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/relative,path)
    engine=clone/'backend/engine/flowchart_engine.py'
    original=engine.read_bytes()
    observed_engine=original
    for row in reversed(QUALIFIED['engine_semantic_inverse_edits']):
        start,end=row['current_offset']
        assert observed_engine[start:end]==row['current'].encode()
        observed_engine=observed_engine[:start]+row['original'].encode()+observed_engine[end:]
    assert len(observed_engine)==148410 and hashlib.sha256(observed_engine).hexdigest()=='00da5942fd90a37ea8cb5a86e89aed9ac3c65026f9e50b9337a42019c5acf001'
    legacy=observed_engine.replace(b'from backend.engine.original_owned_flow_execution_request import observe_original_execution_effect\n',b'',1).replace(b"    @observe_original_execution_effect('engine')\n",b'',1)
    assert len(legacy)==LEGACY_ENGINE['size'] and hashlib.sha256(legacy).hexdigest()==LEGACY_ENGINE['sha256']
    runtime=clone/'backend/engine/flow_package_runtime.py'
    current_runtime=runtime.read_bytes();legacy_runtime=current_runtime
    for row in reversed(QUALIFIED['runtime_observer_inverse_edits']):
        start,end=row['current_offset']
        assert legacy_runtime[start:end]==row['current'].encode()
        legacy_runtime=legacy_runtime[:start]+row['original'].encode()+legacy_runtime[end:]
    assert len(legacy_runtime)==LEGACY_PACKAGE_RUNTIME['size'] and hashlib.sha256(legacy_runtime).hexdigest()==LEGACY_PACKAGE_RUNTIME['sha256']
    engine.write_bytes(legacy);runtime.write_bytes(legacy_runtime)
    assert capture._verify_fixed_source_files(clone) is not None
    engine.write_bytes(original);runtime.write_bytes(current_runtime)
    for relative in ('backend/engine/flowchart_engine.py','backend/engine/flow_operators.py'):
        path=clone/relative;before=path.read_bytes();path.write_bytes(before+b'\n# changed source control\n')
        with pytest.raises(ValueError,match='source custody|Source|source'):
            capture._verify_fixed_source_files(clone)
        path.write_bytes(before)


@pytest.mark.parametrize('changed',['legacy-source-current-profile','legacy-profile-current-source','unknown-profile'])
def test_worker_rejects_mixed_or_unknown_declared_cohort_data(changed):
    value,spec=current_grant_and_spec()
    if changed=='legacy-source-current-profile':value['source_pins']=copy.deepcopy(LEGACY_PINS)
    elif changed=='legacy-profile-current-source':
        value['profile_id']=LEGACY_PROFILE_ID;value['execution_profile_sha256']=LEGACY_PROFILE_SHA
        spec['execution_profile_sha256']=LEGACY_PROFILE_SHA
    else:value['profile_id']='server42-unreviewed'
    with pytest.raises(ValueError):capture.validate_worker_grant(value,spec)


@pytest.mark.parametrize('changed',['legacy-image-tag','other-profile-id','changed-image-bytes'])
def test_current_selector_does_not_issue_for_nonmatching_exact_profile_or_image(tmp_path,monkeypatch,changed):
    app,selector,_=owned_factory(tmp_path,monkeypatch)
    assert selector is not None
    data=dict(CURRENT_PROFILE);image=IMAGE
    if changed=='legacy-image-tag':data['runtime_value']='modu-vision-worker:20260929'
    elif changed=='other-profile-id':data['id']='server42-unreviewed'
    else:
        image=tmp_path/'changed.png';body=IMAGE.read_bytes();image.write_bytes(body[:-1]+bytes([body[-1]^1]))
        assert image.stat().st_size==198
    offered=capture.offer_original_required_flow_capture(selector,request_for(app),ComputeProfile(**data),
        pipeline=GRAPH,image_path=image,device='cuda',stop_node_id=None)
    assert offered is None and selector.issued is None and selector.used is False


def test_current_real_engine_observer_wrapper_is_source_admitted_without_model_construction():
    from backend.engine.flowchart_engine import FlowchartEngine
    from backend.engine import original_owned_flow_execution_request as execution
    wrapper=FlowchartEngine.execute
    assert wrapper is execution._DECORATED['engine']
    assert wrapper.__wrapped__.__code__.co_filename.endswith('/backend/engine/flowchart_engine.py')
    assert wrapper.__code__.co_filename.endswith('/backend/engine/original_owned_flow_execution_request.py')
    source=Path(capture.__file__).resolve().parents[2]
    proof=capture.verify_bound_functions((capture.FunctionSpec('original_required_execute',wrapper,()),),source,CURRENT_PINS)
    assert set(proof['files'])==set(CURRENT_PINS)
    assert proof['actual_installer_runtime_or_required_call_accepted'] is False
    assert proof['roles']['original_required_execute']['path']==wrapper.__code__.co_filename


def test_generic_wrapped_engine_delegate_is_not_original_source_admission():
    from functools import wraps
    from backend.engine.flowchart_engine import FlowchartEngine
    original=FlowchartEngine.execute.__wrapped__
    @wraps(original)
    def generic(*args,**kwargs):return original(*args,**kwargs)
    source=Path(capture.__file__).resolve().parents[2]
    with pytest.raises(ValueError):
        capture.verify_bound_functions((capture.FunctionSpec('original_required_execute',generic,()),),source,CURRENT_PINS)


def test_reconstructed_same_code_engine_class_binding_is_refused(monkeypatch):
    from types import FunctionType
    from backend.engine.flowchart_engine import FlowchartEngine
    from backend.engine import original_owned_flow_execution_request as execution
    original=FlowchartEngine.execute
    rebound=FunctionType(original.__code__,original.__globals__,original.__name__,original.__defaults__,original.__closure__)
    rebound.__dict__.update(original.__dict__)
    assert rebound.__code__ is original.__code__ and rebound.__wrapped__ is original.__wrapped__
    monkeypatch.setattr(FlowchartEngine,'execute',rebound)
    assert execution._DECORATED['engine'] is original
    source=Path(capture.__file__).resolve().parents[2]
    with pytest.raises(ValueError):
        capture.verify_bound_functions((capture.FunctionSpec('original_required_execute',rebound,()),),source,CURRENT_PINS)


def test_current_unwrapped_engine_class_binding_is_refused(monkeypatch):
    from backend.engine.flowchart_engine import FlowchartEngine
    from backend.engine import original_owned_flow_execution_request as execution
    original_wrapper=FlowchartEngine.execute
    original_engine=original_wrapper.__wrapped__
    assert original_wrapper is execution._DECORATED['engine']
    monkeypatch.setattr(FlowchartEngine,'execute',original_engine)
    assert FlowchartEngine.execute is original_engine
    assert execution._DECORATED['engine'] is original_wrapper
    source=Path(capture.__file__).resolve().parents[2]
    with pytest.raises(ValueError):
        capture.verify_bound_functions((capture.FunctionSpec('original_required_execute',original_engine,()),),source,CURRENT_PINS)



def test_current_package_runtime_source_bytes_mutation_is_refused(tmp_path):
    source=Path(capture.__file__).resolve().parents[2]
    clone=tmp_path/'current-six-runtime-control';clone.mkdir(mode=0o700)
    for relative in CURRENT_PINS:
        target=clone/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source/relative,target)
    assert capture._verify_fixed_source_files(clone) is not None
    runtime=clone/'backend/engine/flow_package_runtime.py'
    original=runtime.read_bytes()
    assert len(original)==CURRENT_PINS['backend/engine/flow_package_runtime.py']['size']
    runtime.write_bytes(original+b'\n# mutated runtime source control\n')
    with pytest.raises(ValueError,match='source custody|Source|source'):
        capture._verify_fixed_source_files(clone)
