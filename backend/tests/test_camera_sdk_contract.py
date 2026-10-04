"""Trusted SDK adapter and simulator use the production camera admission path."""
import time
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from backend.tests.test_inspection_service import package, _wait
from backend.engine import inspection_service as service


@pytest.mark.parametrize('kind',['sdk','simulator'])
def test_explicit_sdk_factory_and_simulator_preserve_pixels_and_durable_provenance(package, tmp_path, monkeypatch,kind):
    from backend.engine.camera_adapters import CameraAdapterFactory, SimulatedCamera
    source=np.full((16,16,3),[12,34,56],dtype=np.uint8)
    original=source.copy();capture=SimulatedCamera([source])
    # A vendor SDK may reuse its input buffers; this capture owns the frame.
    source[:]=255
    opened=[]
    factory=CameraAdapterFactory(kind,lambda identifier:(opened.append(identifier),capture)[1])
    monkeypatch.setattr(service.cv2,'VideoCapture',lambda *_:pytest.fail('SDK path fell back to OpenCV'))
    monkeypatch.setattr(service,'run_flow_package',lambda *a,**kw:{'final_verdict':'OK','crops':[]})
    state=tmp_path/'state'
    app=service.create_service_app(package,state,token='fixture',camera_source='simulated-line',camera_id='line-A',
                                   camera_adapter=factory,camera_frame_interval=.1)
    with TestClient(app,headers={'X-Vision-Token':'fixture'}) as client:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            rows=client.get('/v1/jobs').json()['jobs']
            if rows:break
            time.sleep(.01)
        assert len(rows)==1
        row=_wait(client,rows[0]['job_id'],'completed')
        acquisition=row['acquisition']
        assert acquisition['camera_id']=='line-A' and acquisition['sequence']==1
        assert acquisition['clock_basis']=='host_read_completion'
        status=client.get('/v1/adapters').json()
        assert status['camera_adapter_kind']==kind and status['camera_hardware_verified'] is False
        uploads=list((state/'uploads').glob('camera-*.png'));assert len(uploads)==1
        assert np.array_equal(np.array(Image.open(uploads[0]))[:,:,::-1],original)
    assert capture.released and opened==['simulated-line']
    reopened=service.InspectionStore(state).list()
    assert len(reopened)==1 and reopened[0]['acquisition']==acquisition


@pytest.mark.parametrize('frame',[np.zeros((2,2),dtype=np.uint8),np.zeros((2,2,3),dtype=np.float32),None])
def test_simulator_refuses_unconverted_sdk_frames(frame):
    from backend.engine.camera_adapters import SimulatedCamera
    with pytest.raises(ValueError,match='uint8 BGR'):SimulatedCamera([frame])


def test_bad_factory_refused_before_mutable_state(package,tmp_path):
    from backend.engine.camera_adapters import CameraAdapterFactory
    with pytest.raises(ValueError):CameraAdapterFactory('auto-download',lambda source:None)
    with pytest.raises(ValueError):CameraAdapterFactory('sdk',None)
    root=tmp_path/'bad-state'
    with pytest.raises(ValueError):service.create_service_app(package,root,token='fixture',camera_source=0,camera_adapter=object())
    assert not root.exists()


def test_simulator_buffer_ownership_end_and_release_contract():
    from backend.engine.camera_adapters import CameraAdapterFactory, SimulatedCamera
    frame=np.full((4,4,3),17,dtype=np.uint8);capture=SimulatedCamera([frame,frame])
    first=capture.read()[1];first[:]=0
    assert (capture.read()[1]==17).all()
    assert capture.read()==(False,None) and not capture.isOpened()
    capture.release();capture.release();assert capture.released and capture.read()==(False,None)
    with pytest.raises(ValueError,match='read/release'):CameraAdapterFactory('sdk',lambda _:object()).create('opaque')


@pytest.mark.parametrize('bad', ['open-state','read-state','frame'])
def test_sdk_contract_failures_are_recorded_without_admitting_bad_pixels(package,tmp_path,monkeypatch,bad):
    from backend.engine.camera_adapters import CameraAdapterFactory
    class BadSDK:
        released=False
        def isOpened(self):return 'yes' if bad=='open-state' else True
        def read(self):return (1,np.zeros((4,4,3),dtype=np.uint8)) if bad=='read-state' else (True,np.zeros((4,4,3),dtype=np.float32))
        def release(self):
            self.released=True
            raise RuntimeError('controlled SDK close failure')
    capture=BadSDK();factory=CameraAdapterFactory('sdk',lambda source:capture)
    monkeypatch.setattr(service.cv2,'VideoCapture',lambda *_:pytest.fail('failed SDK fell back to OpenCV'))
    state=tmp_path/'state'
    app=service.create_service_app(package,state,token='fixture',camera_source='opaque',camera_id='line-A',camera_adapter=factory)
    with TestClient(app,headers={'X-Vision-Token':'fixture'}) as client:
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            status=client.get('/v1/adapters').json()
            if status['camera']=='close_failed':break
            time.sleep(.01)
        assert status['camera']=='close_failed' and capture.released
        counters=status['camera_acquisition']
        assert counters['connection_failures']==(2 if bad=='open-state' else 1)
        assert counters['read_failures']==(0 if bad=='open-state' else 1)
        assert counters['sequence']==0 and client.get('/v1/jobs').json()['jobs']==[]
    assert not list((state/'uploads').glob('camera-*.png'))
