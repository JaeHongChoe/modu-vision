"""Real service admission with controlled camera frames, no physical device."""
import time
import json
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from backend.tests.test_inspection_service import package, _wait
from backend.engine import inspection_service as service
from backend.engine.camera_admission import CameraAcquisition, camera_identity, validate_acquisition, group_capture_from_trigger
from backend.tests.test_e01_service_capture_groups import policy, image
from backend.tests.test_capture_intake import intake, project, sampling_policy


def acquisition(**updates):
    return {'schema_version': 1, 'camera_id': 'line-1', 'stream_session_id': 'a'*32,
            'sequence': 1, 'captured_unix_ms': 1700000000123, 'observed_monotonic_ms': 1234,
            'clock_basis': 'host_read_completion', 'observed_dropped_count': 0,
            'clock_discontinuity': False, **updates}


@pytest.mark.parametrize('changes', [
    {'schema_version': True}, {'sequence': 0}, {'sequence': True}, {'captured_unix_ms': -1},
    {'captured_unix_ms': float('nan')}, {'observed_monotonic_ms': '123'}, {'observed_dropped_count': -1},
    {'camera_id': 'rtsp://user:password@example.invalid/live'}, {'stream_session_id': 'not-session'},
    {'clock_basis': 'sensor_timestamp'}, {'clock_discontinuity': 1}, {'extra': 'unbound'},
])
def test_invalid_acquisition_is_refused_before_durable_admission(tmp_path, changes):
    store = service.InspectionStore(tmp_path/'state')
    with pytest.raises(ValueError):
        store.enqueue(image(tmp_path, 'input'), 'camera', acquisition=acquisition(**changes))
    assert store.list() == []


def test_network_camera_identity_and_correlated_provider_are_explicit(package, tmp_path):
    assert camera_identity('0') == 'usb:0'
    assert camera_identity(2) == 'usb:2'
    assert camera_identity('camera-1') == 'camera-1'
    with pytest.raises(ValueError, match='opaque'):
        service.create_service_app(package, tmp_path/'uri', token='fixture', camera_source='rtsp://private.invalid/live', auto_worker=False)
    app = service.create_service_app(package, tmp_path/'group', camera_source=0, auto_worker=False, token='fixture')
    with TestClient(app, headers={'X-Vision-Token': 'fixture'}) as client:
        response = client.put('/v1/capture-groups/policy', json={'policy': policy(), 'expected_revision': 0})
        assert response.status_code == 409 and 'provider' in response.text.lower()
    # A saved policy must be refused at restart too, before any device is opened.
    service.InspectionStore(tmp_path/'saved').configure_capture_groups(policy(), expected_revision=0)
    with pytest.raises(ValueError, match='provider'):
        service.create_service_app(package, tmp_path/'saved', token='fixture', camera_source=0, auto_worker=False)


def test_session_sequence_drops_failures_and_clock_guard_survive_restart(tmp_path):
    state = tmp_path/'state'
    store = service.InspectionStore(state)
    tracker = CameraAcquisition(store, 'camera-A')
    tracker.start_session()
    first = tracker.observe(unix_ms=2000, monotonic_ms=100)
    tracker.record_drop(); tracker.record_failure(); tracker.record_failure(connection=True)
    second = tracker.observe(unix_ms=2001, monotonic_ms=101)
    assert second['sequence'] == 2 and second['observed_dropped_count'] == 1
    reopened = CameraAcquisition(service.InspectionStore(state), 'camera-A')
    before = reopened.status()
    assert before['sequence'] == 2 and before['read_failures'] == 1 and before['connection_failures'] == 1
    reopened.start_session()
    after = reopened.observe(unix_ms=1999, monotonic_ms=1)
    assert after['stream_session_id'] != first['stream_session_id'] and after['sequence'] == 1
    assert after['observed_dropped_count'] == 1 and after['clock_discontinuity'] is True
    assert reopened.status()['reconnect_count'] == 1
    assert reopened.observe(unix_ms=3000, monotonic_ms=2)['clock_discontinuity'] is True
    with pytest.raises(ValueError, match='session'):
        tracker.observe(unix_ms=3001, monotonic_ms=103)
    bad = store.enqueue(image(tmp_path, 'clock'), 'camera', acquisition=after)
    job = store.get(bad)
    assert job['verdict'] == 'REVIEW' and job['dead_letter_reason'] == 'CAMERA_CLOCK_DISCONTINUITY'
    assert store.claim() is None and not store.retry(bad)
    with pytest.raises(ValueError):
        store.replay(bad, reason='retry', operator='QA')


def test_camera_idempotency_binds_acquisition_and_keeps_original_recipe(tmp_path):
    active = {'manifest_sha256': 'a'*64, 'recipe_id': 'accepted'}
    store = service.InspectionStore(tmp_path/'state', runtime_provider=lambda: active)
    source = image(tmp_path, 'input'); event = acquisition()
    job = store.enqueue(source, 'camera', acquisition=event, idempotency_key='camera:held:1')
    active.update(manifest_sha256='b'*64, recipe_id='new-active')
    assert store.enqueue(source, 'camera', acquisition=event, idempotency_key='camera:held:1') == job
    assert store.get(job)['runtime_binding']['manifest_sha256'] == 'a'*64
    with pytest.raises(service.InputConflict):
        store.enqueue(source, 'camera', acquisition=acquisition(sequence=2), idempotency_key='camera:held:1')
    row = store.claim(); assert row['job_id'] == job
    store.finish(job, error='controlled inference failure')
    replay = store.replay(job, reason='operator requested', operator='QA')
    assert store.get(replay)['acquisition'] == event


def test_trigger_contract_does_not_invent_identity_or_time():
    trigger = {'part_id': 'A', 'trigger_id': 'T', 'view_id': 'front', 'timestamp_basis': 'trigger_offset', 'trigger_unix_ms': 1700000000100}
    joined = group_capture_from_trigger(trigger, acquisition(), policy())
    assert joined == {'part_id': 'A', 'trigger_id': 'T', 'view_id': 'front', 'captured_at_ms': 23}
    with pytest.raises(ValueError):
        group_capture_from_trigger({**trigger, 'trigger_unix_ms': 1700000000200}, acquisition(), policy())
    with pytest.raises(ValueError):
        group_capture_from_trigger({key: value for key, value in trigger.items() if key != 'part_id'}, acquisition(), policy())
    shared = policy(); shared['policy']['timestamp_basis'] = 'shared_clock'
    trigger.pop('trigger_unix_ms'); trigger['timestamp_basis'] = 'shared_clock'
    assert group_capture_from_trigger(trigger, acquisition(), shared)['captured_at_ms'] == 1700000000123


def test_policy_revision_race_and_old_part_clock_basis_are_durable_review(tmp_path):
    store=service.InspectionStore(tmp_path/'state',runtime_provider=lambda:{'manifest_sha256':'a'*64})
    original=store.configure_capture_groups(policy(deadline=5),expected_revision=0)
    source=image(tmp_path,'input')
    first=store.enqueue(source,'camera',capture={'part_id':'A','trigger_id':'T','view_id':'front','captured_at_ms':1},
                        acquisition=acquisition(),capture_policy=original)
    store.claim(); store.finish(first,result={'final_verdict':'OK'})
    changed=policy(2); changed['policy']['timestamp_basis']='shared_clock'
    current=store.configure_capture_groups(changed,expected_revision=1)
    race=store.enqueue(source,'camera',capture={'part_id':'B','trigger_id':'T','view_id':'front','captured_at_ms':1},
                       acquisition=acquisition(sequence=2),capture_policy=original)
    old=store.enqueue(source,'camera',capture={'part_id':'A','trigger_id':'T','view_id':'back','captured_at_ms':1700000000123},
                      acquisition=acquisition(sequence=3),capture_policy=current)
    for job in (race,old):
        row=store.get(job)
        assert row['state']=='error' and row['verdict']=='REVIEW'
        assert row['dead_letter_reason']=='CAMERA_CAPTURE_POLICY_CHANGED' and row['capture'] is None
        assert not store.retry(job)
    assert store.claim() is None
    groups=store.capture_group_status()['groups']
    assert len(groups)==1 and groups[0]['part_id']=='A' and 'back' not in groups[0]['frame_refs']
    time.sleep(.02)
    expired=store.capture_group_status()['groups'][0]
    assert expired['state']=='EXPIRED' and expired['verdict']=='REVIEW'


class FramesCamera:
    def __init__(self, frames):
        self.frames = iter(frames)

    def isOpened(self): return True
    def read(self):
        try: return True, next(self.frames)
        except StopIteration: return False, None
    def release(self): pass


@pytest.mark.parametrize('failure', ['constructor', 'open', 'read'])
def test_actual_camera_exception_counters_survive_restart(package, tmp_path, monkeypatch, failure):
    class BrokenCamera:
        def isOpened(self):
            if failure=='open': raise RuntimeError('controlled open exception')
            return True
        def read(self): raise RuntimeError('controlled read exception')
        def release(self): pass
    def construct(source):
        if failure=='constructor': raise RuntimeError('controlled connection exception')
        return BrokenCamera()
    monkeypatch.setattr(service.cv2,'VideoCapture',construct)
    state=tmp_path/'state'
    app=service.create_service_app(package,state,token='fixture',camera_source=0,camera_frame_interval=.1)
    counter='read_failures' if failure=='read' else 'connection_failures'
    with TestClient(app,headers={'X-Vision-Token':'fixture'}) as client:
        until=time.monotonic()+3
        while time.monotonic()<until:
            saved=client.get('/v1/adapters').json()['camera_acquisition']
            if saved[counter]: break
            time.sleep(.01)
        assert saved[counter]>=1 and saved['sequence']==0
        assert saved['connection_failures' if failure=='read' else 'read_failures']==0
    reopened=CameraAcquisition(service.InspectionStore(state),'usb:0')
    assert reopened.status()[counter]>=1 and service.InspectionStore(state).list()==[]


def wait_jobs(client, count):
    until = time.monotonic()+5
    while time.monotonic() < until:
        rows = client.get('/v1/jobs').json()['jobs']
        if len(rows) == count: return rows
        time.sleep(.02)
    raise AssertionError('camera did not durably admit expected jobs')


def test_actual_camera_interleaved_parts_do_not_mix_and_missing_trigger_is_review(package, tmp_path, monkeypatch):
    pairs = [('A', 'back'), ('B', 'front'), ('A', 'front'), ('B', 'back')]
    frames = [np.full((16,16,3), n, dtype=np.uint8) for n in range(5)]
    monkeypatch.setattr(service.cv2, 'VideoCapture', lambda source: FramesCamera(frames))
    def provider(frame, observed):
        n = int(frame[0,0,0])
        if n == 4: raise RuntimeError('controlled trigger adapter unavailable')
        part, view = pairs[n]
        return {'part_id':part, 'trigger_id': 'T', 'view_id':view, 'timestamp_basis':'trigger_offset',
                'trigger_unix_ms':observed['captured_unix_ms']-1}
    def execute(package_path, path, *args, **kwargs):
        n = int(np.array(Image.open(path))[0,0,0])
        return {'final_verdict':'NG' if n==3 else 'OK', 'crops':[]}
    monkeypatch.setattr(service, 'run_flow_package', execute)
    state = tmp_path/'state'; service.InspectionStore(state).configure_capture_groups(policy(), expected_revision=0)
    app = service.create_service_app(package, state, token='fixture', camera_source=0,
                                     camera_frame_interval=.1, camera_capture_provider=provider)
    with TestClient(app, headers={'X-Vision-Token':'fixture'}) as client:
        rows = wait_jobs(client, 5)
        for row in rows:
            if row['capture'] and not row['capture'].get('admission_failure'):
                _wait(client, row['job_id'], 'completed')
        groups = {row['part_id']:row for row in client.get('/v1/capture-groups').json()['groups']}
        assert groups['A']['verdict']=='OK' and groups['B']['verdict']=='NG'
        assert groups['A']['frame_refs']['front'] != groups['B']['frame_refs']['front']
        missing = [client.get('/v1/jobs/'+row['job_id']).json() for row in rows if row['acquisition']['sequence']==5][0]
        assert missing['state']=='error' and missing['verdict']=='REVIEW'
        assert all(row['acquisition']['clock_basis']=='host_read_completion' for row in rows)


def test_actual_camera_consumes_trigger_before_capacity_and_cleans_refused_upload(package, tmp_path, monkeypatch):
    calls=[]; original_pending=service.InspectionStore.pending_count; original_enqueue=service.InspectionStore.enqueue
    def provider(frame, observed):
        calls.append(observed['sequence'])
        return {'part_id':str(observed['sequence']), 'trigger_id':'T', 'view_id':'front',
                'timestamp_basis':'trigger_offset', 'trigger_unix_ms':observed['captured_unix_ms']-1}
    count={'value':0}
    def enqueue(store, *args, **kwargs):
        if threading.current_thread().name=='inspection-camera':
            count['value']+=1
            if count['value']==1: raise service.InboxFull('controlled capacity race')
        return original_enqueue(store,*args,**kwargs)
    # First read is refused before writing; second loses capacity at enqueue; third is accepted.
    pending={'reads':0}
    def capacity(store):
        if threading.current_thread().name=='inspection-camera':
            pending['reads']+=1
            return 1 if pending['reads']==1 else 0
        return original_pending(store)
    monkeypatch.setattr(service.InspectionStore,'pending_count',capacity)
    monkeypatch.setattr(service.InspectionStore,'enqueue',enqueue)
    monkeypatch.setattr(service.cv2,'VideoCapture',lambda source:FramesCamera([np.full((16,16,3),n,dtype=np.uint8) for n in range(3)]))
    monkeypatch.setattr(service,'run_flow_package',lambda *a,**k:{'final_verdict':'OK','crops':[]})
    state=tmp_path/'state'; service.InspectionStore(state,max_outstanding=1).configure_capture_groups(policy(),expected_revision=0)
    app=service.create_service_app(package,state,token='fixture',camera_source=0,camera_frame_interval=.1,
                                   camera_capture_provider=provider,max_outstanding=1)
    with TestClient(app,headers={'X-Vision-Token':'fixture'}) as client:
        rows=wait_jobs(client,1)
        assert calls==[1,2,3]
        assert rows[0]['acquisition']['sequence']==3 and rows[0]['acquisition']['observed_dropped_count']==2
        status=client.get('/v1/adapters').json()['camera_acquisition']
        assert status['observed_dropped_count']==2
        assert len(list((state/'uploads').glob('camera-*.png')))==1
        assert rows[0]['capture']['part_id']=='3'


def test_deadline_evidence_keeps_camera_receipt_but_excludes_rejected_frame(tmp_path):
    store=service.InspectionStore(tmp_path/'state',runtime_provider=lambda:{'manifest_sha256':'a'*64})
    store.configure_capture_groups(policy(deadline=5),expected_revision=0)
    source=image(tmp_path,'input'); event=acquisition()
    job=store.enqueue(source,'camera',capture={'part_id':'A','trigger_id':'T','view_id':'front','captured_at_ms':1},acquisition=event)
    store.claim(); store.finish(job,result={'final_verdict':'OK'})
    bad=store.enqueue(source,'camera',capture={'part_id':'A','trigger_id':'T','view_id':'back','captured_at_ms':1},acquisition=acquisition(sequence=2,clock_discontinuity=True))
    assert store.get(bad)['verdict']=='REVIEW'
    time.sleep(.02)
    output=store.get(store.sweep_capture_deadlines(require_delivery=False)[0])
    admitted=output['result']['admitted_frames']
    assert [row['job_id'] for row in admitted]==[job] and admitted[0]['acquisition']==event


def test_sampler_uses_camera_identity_and_capture_time_with_immutable_receipt(intake, project):
    p,store,_=project; configured={**sampling_policy(), 'max_items_per_window':10}
    intake.save_sampling_policy(p,configured,expected_revision=0)
    jobs=[]
    for n,camera in enumerate(('line-A','line-B','line-A')):
        path=store.state_dir/'uploads'/f'{n}.png'; Image.new('RGB',(24,24),(n*60,30,50)).save(path)
        event=acquisition(camera_id=camera,sequence=n+1)
        job=store.enqueue(path,'camera',acquisition=event); store.claim();store.finish(job,result={'final_verdict':'NG'})
        jobs.append(job)
    first=intake.register_service_jobs(p,job_ids=jobs)
    assert len(first['candidates'])==2
    receipts=first['sampling_receipts']
    assert [row['decision'] for row in receipts]==['selected','selected','skipped']
    assert [row['run_ref']['acquisition']['camera_id'] for row in receipts]==['line-A','line-B','line-A']
    assert all(row['window']==1700000000//86400 for row in receipts)
    assert first['candidates'][0]['origin']['acquisition']['clock_basis']=='host_read_completion'
    assert intake.register_service_jobs(p,job_ids=jobs)['sampling_receipts']==receipts
    assert all(row['truth_verdict']=='UNKNOWN' for row in first['candidates'])


class OneFrameCamera:
    def __init__(self):
        self.reads = 0

    def isOpened(self):
        return True

    def read(self):
        self.reads += 1
        if self.reads == 1:
            return True, np.full((16, 16, 3), 128, dtype=np.uint8)
        return False, None

    def release(self):
        pass


def test_actual_camera_service_persists_acquisition_not_group_metadata(package, tmp_path, monkeypatch):
    monkeypatch.setattr(service.cv2, 'VideoCapture', lambda source: OneFrameCamera())
    monkeypatch.setattr(service, 'run_flow_package', lambda *a, **k: {'final_verdict': 'NG', 'crops': []})
    app = service.create_service_app(package, tmp_path / 'state', token='fixture',
                                     camera_source='0', camera_frame_interval=.1)
    with TestClient(app, headers={'X-Vision-Token': 'fixture'}) as client:
        until = time.monotonic() + 4
        rows = []
        while not rows and time.monotonic() < until:
            rows = client.get('/v1/jobs').json()['jobs']
            if not rows:
                time.sleep(.01)
        assert len(rows) == 1
        job = _wait(client, rows[0]['job_id'], 'completed')
        assert job['source'] == 'camera' and job['verdict'] == 'NG'
        assert job['capture'] is None, 'acquisition must not impersonate a correlated group'
        acquisition = job.get('acquisition')
        assert acquisition is not None, 'successful camera input lost camera/time/sequence provenance'
        assert acquisition['camera_id'] == 'usb:0'
        assert acquisition['sequence'] == 1
        assert acquisition['clock_basis'] == 'host_read_completion'
        assert acquisition['captured_unix_ms'] > 0 and acquisition['observed_monotonic_ms'] > 0
        assert acquisition['observed_dropped_count'] == 0
        assert len(acquisition['stream_session_id']) == 32
