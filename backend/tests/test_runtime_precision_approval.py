"""Precision acceptance binds measured heldout evidence and creates a new release."""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from backend.tests.test_runtime_deadline_sdk import real_package


def test_precision_request_requires_explicit_boolean_and_numeric_bound():
    from backend.api.routes_export import PrecisionApprovalRequest
    fields=dict(reviewer='engineer',reason='Reviewed native heldout output',holdout_reviewed=True,
                maximum_absolute_drift=.001,approval_revision_ids={'job':'revision'})
    for key,value in [('holdout_reviewed',1),('maximum_absolute_drift',True),('maximum_absolute_drift',float('nan'))]:
        with pytest.raises(ValidationError):PrecisionApprovalRequest(**{**fields,key:value})


def test_runtime_refuses_unconverted_openvino_package_before_ready(real_package,tmp_path,monkeypatch):
    from backend.engine import service_runtime
    package,_=real_package
    monkeypatch.setattr(service_runtime,'resolve_runtime_device',lambda value:value)
    with pytest.raises(ValueError,match='converted'):
        service_runtime.ServiceRuntime(package,tmp_path/'state','openvino:CPU',None,None,lambda *_:None)


@pytest.fixture
def measured_candidate(real_package,tmp_path):
    pytest.importorskip('openvino')
    from backend.api.routes_export import _optimization_input_receipt
    from backend.engine.openvino_runtime import optimize_flow_package
    from backend.engine.flow_package_runtime import _sha256
    package,image=real_package
    source=tmp_path/'source';(source/'val').mkdir(parents=True)
    heldout=source/'val/heldout.png';heldout.write_bytes(image.read_bytes())
    project={'project_dir':str(tmp_path),'source_dataset_dir':str(source),'dataset_dir':str(tmp_path/'dataset')}
    receipt=_optimization_input_receipt(project,source,[],[str(heldout)])
    folder=tmp_path/'exports/flows';folder.mkdir()
    result=optimize_flow_package(package,output_dir=folder/'candidate',validation_images=[heldout],input_receipt=receipt)
    candidate=Path(result['package_path'])
    revision={'revision_id':'revision_fixture','job_id':'job_real_runtime','task':'segmentation',
              'checkpoint_sha256':_sha256(candidate/'models/job_real_runtime/best_model.pt')}
    return candidate,heldout,project,result,revision


def test_fresh_precision_release_keeps_candidate_and_exact_acceptance(measured_candidate,tmp_path):
    from backend.engine.runtime_precision_approval import approve_precision_package
    from backend.engine.flow_package_runtime import verify_flow_package,Predictor
    candidate,image,project,result,revision=measured_candidate
    before=hashlib.sha256((candidate/'manifest.json').read_bytes()).hexdigest()
    args=dict(revisions={'job_real_runtime':revision},reviewer='engineer',reason='Native heldout output inspected',
              maximum_absolute_drift=.001,holdout_reviewed=True)
    with pytest.raises(ValueError,match='checkpoint'):approve_precision_package(candidate,tmp_path/'wrong',**{**args,'revisions':{'job_real_runtime':{**revision,'checkpoint_sha256':'0'*64}}})
    with pytest.raises(ValueError,match='reviewed'):approve_precision_package(candidate,tmp_path/'unreviewed',**{**args,'holdout_reviewed':False})
    approved=approve_precision_package(candidate,tmp_path/'approved',**args)
    released=Path(approved['package_path']);verify_flow_package(released)
    assert hashlib.sha256((candidate/'manifest.json').read_bytes()).hexdigest()==before
    assert 'release' not in json.loads((candidate/'manifest.json').read_text())
    assert approved['runtime_acceptance']['input_receipt']['validation_images'][0]['split']=='val'
    assert approved['release_policy']['runtime_acceptance_sha256']==hashlib.sha256((released/'runtime_acceptance.json').read_bytes()).hexdigest()
    assert Predictor(released).predict(image)['final_verdict']=='NG'
    with pytest.raises(ValueError,match='accepted device'):Predictor(released,device='cpu').predict(image)
    with pytest.raises(ValueError,match='accepted device'):
        from backend.engine.service_runtime import ServiceRuntime
        ServiceRuntime(released,tmp_path/'state','cpu',None,None,lambda *_:None)


def test_approval_api_refuses_changed_holdout_and_active_revision(measured_candidate,monkeypatch):
    from backend.api import routes_export,routes_model_deployments
    from backend.engine import runtime_optimization_jobs
    from contextlib import nullcontext
    candidate,image,project,result,revision=measured_candidate
    record={'status':'completed','result':result}
    monkeypatch.setattr(runtime_optimization_jobs,'read_job',lambda *_:record)
    monkeypatch.setattr(routes_export,'get_current_project',lambda _:project)
    monkeypatch.setattr(routes_model_deployments,'_store',lambda _:nullcontext(None))
    monkeypatch.setattr(routes_model_deployments,'_active',lambda *_:{'revision_id':'revision_fixture'})
    monkeypatch.setattr(routes_model_deployments,'verified_release_revision',lambda *_,**__:revision)
    app=FastAPI();app.include_router(routes_export.router)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            prefix='/api/export/flow/optimization-jobs/'+('a'*32)
            body={'reviewer':'engineer','reason':'Native heldout output inspected','holdout_reviewed':True,
                  'maximum_absolute_drift':.001,'approval_revision_ids':{'job_real_runtime':'stale_revision'}}
            assert (await client.get(prefix+'/approval-prerequisites')).status_code==200
            proof=await client.get(prefix+'/heldout-results/0')
            assert proof.status_code==200,proof.text
            assert proof.json()['reference']['final_verdict']==proof.json()['candidate']['final_verdict']=='NG'
            assert (await client.get(prefix+'/heldout-results/1')).status_code==409
            stale=await client.post(prefix+'/approve',json=body)
            assert stale.status_code==409 and 'changed' in stale.text
            image.write_bytes(b'changed original')
            changed=await client.get(prefix+'/approval-prerequisites')
            assert changed.status_code==409 and 'changed' in changed.text
    asyncio.run(exercise())
