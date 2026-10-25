"""Actual original ASGI current-summary with an originally saved metadata ledger."""
import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx


def _fixture(tmp_path, monkeypatch):
    from PIL import Image
    from backend.engine import dataset_metadata
    from backend.tests.test_original_owned_flow_lifetime import _stack
    monkeypatch.chdir(tmp_path)
    app, admission, observer, actor, project, _, seed, recipe, audit = _stack(tmp_path, monkeypatch)
    root, source = Path(project['project_dir']), Path(project['source_dataset_dir'])
    from backend.api import routes_flowchart as flows
    protected = [root/'project.json', recipe, flows._active_flow_file(root), flows._version_dir(root)/(seed+'.json')]
    for index in range(6):
        folder = source/('train' if index < 2 else 'val' if index < 4 else 'test')
        folder.mkdir(exist_ok=True)
        image = folder/f'owned-{index}.png'
        Image.new('RGB', (32, 32), (30+index, 70+index, 90+index)).save(image)
        label = image.with_suffix('.json')
        label.write_text(json.dumps(dict(imagePath=image.name,imageWidth=32,imageHeight=32,
            shapes=[dict(label='defect',shape_type='polygon',points=[[1,1],[20,1],[20,20]])])))
        protected.extend((image,label))
    annotations = Path(project['annotations_dir'])
    # Original data API only, before observed requests. No ledger bytes or
    # request/lifetime witness is written by the test itself.
    rows = dataset_metadata.list_metadata(root, source, annotations)
    assert len(rows) == 6
    ledger = dataset_metadata.ledger_path(root, source, annotations)
    assert ledger.is_file() and not ledger.is_symlink()
    # The real first eligibility read initializes team defaults. Prepare that
    # original saved namespace before the immutable read baseline, using its
    # original API rather than handwritten ledger data.
    from backend.engine import team_data
    before_team = json.loads(ledger.read_text())
    assert set(before_team) == {'schema_version','images'}
    team_data.image_state(project, source, source/'train/owned-0.png')
    assert json.loads(ledger.read_text()) == {**before_team,'team_data':{
        'schema_version':1,'settings':dict(team_data.DEFAULT_SETTINGS),'books':[]}}
    protected.append(ledger)
    def basis():
        result = {}
        for path in protected:
            st = path.lstat(); raw = path.read_bytes()
            assert not path.is_symlink() and st.st_nlink == 1
            result[str(path)] = dict(sha256=hashlib.sha256(raw).hexdigest(),
                raw9=[st.st_dev,st.st_ino,st.st_mode,st.st_nlink,st.st_uid,st.st_gid,st.st_size,st.st_mtime_ns,st.st_ctime_ns])
        return result
    return app, admission, observer, project, source, ledger, basis


def test_original_current_summary_saved_metadata_reads_close_and_drain(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _login_current, _no_whole_credit
    app, admission, observer, project, source, ledger, basis = _fixture(tmp_path, monkeypatch)
    before = basis()
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/dataset/current-summary',headers=headers,
                params={'folder_path':str(source),'task':'segmentation'})
            assert response.status_code == 200
            assert response.json() == {
                'status':'success','total_images':0,'source_images':0,'unlabeled_images':0,
                'classes':{'defect_mask':0},'split':{'train':0,'val':0,'test':0},'corrupted_images':[],
                'excluded_undecodable':{'excluded':[],'recorded':True,'restored':[]},
                'labels_missing':True,'split_state':'source_folders',
                'expected_layout':'images/ 와 masks/ (이미지와 같은 이름의 PNG 마스크), 또는 이미지 옆 같은 이름의 LabelMe .json. 마스크 없이 불러와 2단계에서 그려도 됩니다',
                'validation':{'requested':True,'checked_images':6,'complete':True,
                    'scope':'all images: every image file under the folder was decoded'},
                'split_supported':False,
                'split_unavailable_reason':'일반 이미지·마스크 세그멘테이션의 재분할은 지원되지 않습니다. 원본 train/val 폴더를 사용하세요. / Re-splitting paired image/mask segmentation is not supported; use source train/val folders.'}
            return response
    response = asyncio.run(original_request())
    after = basis(); assert after == before
    owner = admission._UI_ancillary_requests
    attempts = [attempt for attempt in owner.records if attempt.name == 'current_dataset_summary']
    assert len(attempts) == 1
    attempt = attempts[0]
    assert any(path == ledger and role == 'dataset' for _,path,role in attempt.files)
    assert all(handle.closed for handle,_,_ in attempt.files)
    snapshot = observer.snapshot()
    assert len(snapshot['records']) == 3
    assert all(row['completed'] and row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in snapshot['records'])
    admission.close(); drain = admission.drain(.03)
    output = Path(os.environ.get('MV_CURRENT_SUMMARY_RESULT',tmp_path/'current-summary-result.json'))
    output.write_text(json.dumps(dict(response_status=response.status_code,response_body=response.json(),
        ordered_failures=snapshot['failures'],drain=drain,protected_exact=before==after,
        files=[dict(path=str(path),role=role,closed=handle.closed)for handle,path,role in attempt.files],
        original_metadata_contexts=len(attempt.contexts),native=False,Root06_cause_proven=False),indent=2,sort_keys=True)+'\n')
    assert drain == {'status':'managed_scopes_drained','active_scopes':0,'unsupported':[]}
    assert attempt.contexts and all(context.gen.gi_frame is None for context in attempt.contexts)
    assert not observer.snapshot()['uncertain'] and observer.snapshot()['all_settled']
    _no_whole_credit(observer)


def test_original_current_summary_foreign_selector_refuses_saved_ledger_adoption(tmp_path, monkeypatch):
    from backend.tests.test_original_owned_flow_lifetime import _login_current, _no_whole_credit
    app, admission, observer, project, source, ledger, basis = _fixture(tmp_path, monkeypatch)
    foreign = tmp_path/'foreign-source'; foreign.mkdir()
    before = basis()
    async def original_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.get('/api/dataset/current-summary',headers=headers,
                params={'folder_path':str(foreign),'task':'segmentation'})
            assert response.status_code == 403
            assert response.json() == {'detail':'Request data is outside this project or invalid'}
    asyncio.run(original_request())
    assert basis() == before
    attempt = next(row for row in admission._UI_ancillary_requests.records if row.name == 'current_dataset_summary')
    assert attempt.files == [] and attempt.dataset_operations == []
    admission.close(); drain = admission.drain(.03)
    assert drain['status'] == 'refused' and 'uncovered_request_protocol' in drain['unsupported']
    _no_whole_credit(observer)
