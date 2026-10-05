from pathlib import Path
import pytest
from backend.engine.product_delivery import configure_operator_inputs,read_operator_inputs
from backend.engine.managed_service import ManagedService
from backend.tests.test_product_delivery import project
from backend.tests.test_runtime_deadline_sdk import real_package

def test_operator_rtsp_identity_survives_config_and_exact_worker_arguments(tmp_path):
 p=project(tmp_path)
 configure_operator_inputs(p,'camera',camera='rtsp://camera.invalid/stream',camera_id='line-A')
 saved=read_operator_inputs(p);assert saved['camera_id']=='line-A'
 args=ManagedService(p['project_dir']).input_arguments({'input_root':p['source_dataset_dir']})
 assert args==['--camera-source','rtsp://camera.invalid/stream','--camera-id','line-A']

def test_operator_camera_uri_requires_opaque_id_before_write(tmp_path):
 p=project(tmp_path)
 with pytest.raises(ValueError,match='camera_id|Camera ID'):
  configure_operator_inputs(p,'camera',camera='rtsp://camera.invalid/stream')
 assert not (Path(p['project_dir'])/'runtime_service/inputs.json').exists()

@pytest.mark.parametrize('identifier',['rtsp://camera.invalid/stream','has spaces','x'*101])
def test_camera_identity_validation_precedes_persistence(tmp_path,identifier):
 p=project(tmp_path)
 with pytest.raises(ValueError):configure_operator_inputs(p,'camera',camera='0',camera_id=identifier)
 assert not (Path(p['project_dir'])/'runtime_service/inputs.json').exists()

def test_legacy_usb_default_and_manual_clear(tmp_path):
 p=project(tmp_path)
 configure_operator_inputs(p,'camera',camera='2')
 assert read_operator_inputs(p)['camera_id']=='usb:2'
 configure_operator_inputs(p,'manual')
 assert read_operator_inputs(p)['camera_id'] is None

def test_owned_video_decoding_and_actual_cpu_inspection_are_durable(real_package,tmp_path):
 import cv2,hashlib,time
 import numpy as np
 from fastapi.testclient import TestClient
 from backend.engine.inspection_service import create_service_app,InspectionStore
 package,_=real_package
 video=tmp_path/'owned-video.avi';writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),10,(32,32))
 assert writer.isOpened()
 for level in (30,70):writer.write(np.full((32,32,3),level,dtype=np.uint8))
 writer.release();before=hashlib.sha256(video.read_bytes()).hexdigest()
 state=tmp_path/'video-state';app=create_service_app(package,state,token='video-fixture',camera_source=str(video),camera_id='owned-clip',camera_frame_interval=.1)
 with TestClient(app,headers={'X-Vision-Token':'video-fixture'}) as client:
  until=time.monotonic()+20
  while time.monotonic()<until:
   rows=client.get('/v1/jobs').json()['jobs'];status=client.get('/v1/adapters').json()
   if len(rows)==2 and all(row['state']=='completed' for row in rows) and status['camera']=='disconnected':break
   time.sleep(.05)
  assert len(rows)==2 and all(row['state']=='completed' and row['model_verdict']=='NG' for row in rows)
  assert status['camera_adapter_kind']=='opencv' and status['camera_hardware_verified'] is False
  assert status['camera_acquisition']['read_failures']==1 and status['camera_acquisition']['observed_dropped_count']==0
  sessions={row['acquisition']['stream_session_id'] for row in rows};assert len(sessions)==1
  assert {row['acquisition']['sequence'] for row in rows}=={1,2}
  assert all(row['acquisition']['camera_id']=='owned-clip' and row['acquisition']['clock_basis']=='host_read_completion' for row in rows)
  assert all(row['result']['execution_steps'] and row['result']['final_verdict']=='NG' for row in rows)
 reopened=InspectionStore(state).list();assert len(reopened)==2
 assert {row['acquisition']['sequence'] for row in reopened}=={1,2}
 assert hashlib.sha256(video.read_bytes()).hexdigest()==before
 assert app.state.worker_thread.is_alive() is False

def test_legacy_network_configuration_requires_reconfiguration_without_activation(tmp_path):
 import json
 p=project(tmp_path);configure_operator_inputs(p,'manual');path=Path(p['project_dir'])/'runtime_service/inputs.json'
 legacy=json.loads(path.read_text());legacy.update(mode='camera',camera='rtsp://camera.invalid/stream');legacy.pop('camera_id',None);path.write_text(json.dumps(legacy))
 before=path.read_bytes();readback=read_operator_inputs(p)
 assert readback['camera_id'] is None and readback['needs_reconfiguration'] is True
 assert path.read_bytes()==before
 with pytest.raises(ValueError,match='opaque'):ManagedService(p['project_dir']).input_arguments({'input_root':p['source_dataset_dir']})

def test_scoped_camera_identity_api_roundtrip_and_invalid_request_are_non_mutating(tmp_path):
 from fastapi import FastAPI
 from backend.api.routes_product_delivery import router
 from backend.tests.test_product_delivery import ApiClient
 p=project(tmp_path);app=FastAPI();app.include_router(router)
 @app.middleware('http')
 async def scope(request,next):request.state.scoped_project=p;return await next(request)
 client=ApiClient(app)
 result=client.request('PUT','/api/product-delivery/operator/inputs',json={'mode':'camera','camera':'rtsp://camera.invalid/stream','camera_id':'owned-A'})
 assert result.status_code==200,result.text
 assert read_operator_inputs(p)['camera_id']=='owned-A'
 path=Path(p['project_dir'])/'runtime_service/inputs.json';before=path.read_bytes()
 rejected=client.request('PUT','/api/product-delivery/operator/inputs',json={'mode':'camera','camera':'rtsp://camera.invalid/stream','camera_id':'has spaces'})
 assert rejected.status_code==409 and path.read_bytes()==before
