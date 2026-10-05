"""Derived edits keep original pixels, geometry, review and heldout lineage distinct."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.engine import data_workbench as dw


def setup(tmp_path):
    app = create_app(project_dir=str(tmp_path / 'projects'))
    api = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = api.post('/api/project/create', json={'name': 'Derived workflow', 'task': 'classification'}).json()
    source = tmp_path / 'source'
    for split_index, split in enumerate(('train', 'val', 'test')):
        for index, label in enumerate(('OK', 'NG')):
            directory = source / split / label; directory.mkdir(parents=True)
            Image.new('RGB', (24, 16), (40 + index * 60, 80 + split_index * 10, 120)).save(directory / 'sample.png')
    project = api.put('/api/project/update', json={'source_dataset_dir': str(source)}).json()
    assert api.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification'}).status_code == 200
    # Explicit fixed cohort: preserve file-layout split rather than randomizing it.
    from backend.engine.flow_workspace import atomic_json
    split_file = Path(project['dataset_dir']) / 'splits' / (hashlib.sha256(str(source).encode()).hexdigest()+'.json')
    assignments = {path.relative_to(source).as_posix(): path.relative_to(source).parts[0] for path in source.rglob('*.png')}
    atomic_json(split_file, {'folder_path': str(source), 'assignments': assignments, 'seed': 31})
    image = source / 'train/NG/sample.png'
    labels = [{'id': 'box', 'type': 'bbox', 'bbox': [2, 3, 12, 10], 'label': 'NG', 'category_id': 1, 'text': 'A01'}]
    saved = api.post('/api/annotations/save', json={'image_id': 'sample', 'image_path': str(image), 'image_width':24, 'image_height':16, 'actor':'Editor', 'annotations':labels})
    assert saved.status_code == 200, saved.text
    loaded = api.get('/api/annotations/sample', params={'file_path':str(image)}).json()
    assert loaded['annotations'][0]['text']=='A01'
    row = api.get('/api/dataset/metadata/image', params={'image_path': str(image)}).json()
    made = api.post('/api/data-workbench/derived', json={'image_path':str(image), 'expected_sha256':row['content_hash'], 'expected_revision':row['revision'], 'actor':'Editor', 'operation':{'kind':'brightness','factor':.5}})
    assert made.status_code == 200, made.text
    return api, project, source, image, made.json()


def test_rigid_alignment_rotates_geometry_and_original_remains_unchanged():
    image = Image.new('RGB', (24, 16), (40, 80, 120))
    before = np.asarray(image).copy()
    labels = [{'id':'box', 'type':'bbox', 'bbox':[2,3,12,10], 'text':'A01', 'direction_deg':20}]
    result, annotations, omitted = dw.edit_image_and_annotations(image, labels, {'kind':'align', 'degrees':30})
    assert result.size == (30, 26)
    assert annotations[0]['type']=='polygon' and len(annotations[0]['polygon'])==4
    assert annotations[0]['direction_deg']==50 and annotations[0]['text']=='A01'
    assert np.array_equal(np.asarray(image),before) and labels[0]['type']=='bbox' and not omitted


def test_review_binds_derived_labels_and_unreviewed_adoption_is_refused(tmp_path):
    api, project, source, image, version = setup(tmp_path)
    before = image.read_bytes()
    route='/api/data-workbench/derived/'+version['id']+'/review'
    review = api.post(route, json={'expected_revision':0,'actor':'Reviewer','decision':'approve','note':'Inspect transformed label geometry'})
    assert review.status_code == 200, review.text
    assert review.json()['review']['derived_sha256']==version['derived_sha256']
    assert image.read_bytes()==before
    stale = api.post(route, json={'expected_revision':0,'actor':'Reviewer','decision':'reject','note':'Stale revision'})
    assert stale.status_code==409
    (Path(version['dataset_path'])/'annotations.json').write_text('{}')
    bad = api.get('/api/data-workbench/derived/'+version['id'])
    assert bad.status_code==409 and 'annotation' in bad.text.lower()


def test_train_only_owned_branch_keeps_heldout_and_requires_review(tmp_path):
    api, project, source, image, version = setup(tmp_path)
    originals = {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in source.rglob('*') if path.is_file()}
    body={'version_ids':[version['id']], 'actor':'Reviewer','name':'Reviewed edit branch'}
    refused=api.post('/api/data-workbench/derived-adoptions',json=body)
    assert refused.status_code==422 and 'review' in refused.text.lower()
    assert api.post('/api/data-workbench/derived/'+version['id']+'/review',json={'expected_revision':0,'actor':'Reviewer','decision':'approve','note':'Explicit synthetic edit review'}).status_code==200
    adopted=api.post('/api/data-workbench/derived-adoptions',json=body)
    assert adopted.status_code==200, adopted.text
    branch=adopted.json(); assert branch['kind']=='derived-edits' and branch['activated'] is False
    assert api.get('/api/project/current').json()['source_dataset_dir']==str(source)
    assert branch['adopted'][0]['relative_path'].startswith('train/NG/')
    assert branch['split_assignments'][branch['adopted'][0]['relative_path']]=='train'
    assert len(branch['fixed_test_records'])==2
    for path,digest in originals.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
        copied=Path(branch['source_dataset_path'])/Path(path).relative_to(source)
        assert hashlib.sha256(copied.read_bytes()).hexdigest()==digest
    api.put('/api/project/update',json={'source_dataset_dir':branch['source_dataset_path']})
    row=api.get('/api/dataset/metadata/image',params={'image_path':str(Path(branch['source_dataset_path'])/branch['adopted'][0]['relative_path'])}).json()
    assert row['workflow_state']=='needs_review' and row['usage_state']=='not_used'
    original_copy=api.get('/api/dataset/metadata/image',params={'image_path':str(Path(branch['source_dataset_path'])/'train/NG/sample.png')}).json()
    assert original_copy['group']==row['group'] and row['group'].startswith('derived:')
    parent=api.get('/api/data-workbench/derived-adoptions/'+branch['version_id']+'/parent')
    assert parent.status_code==200 and parent.json()['source_dataset_path']==str(source)
    image.write_bytes(b'original changed')
    assert api.get('/api/data-workbench/derived-adoptions/'+branch['version_id']+'/parent').status_code==409


@pytest.mark.parametrize('degrees',[True,float('nan'),float('inf'),181,-181,'30',None])
def test_alignment_invalid_angles_never_publish_version(tmp_path,degrees):
    source=tmp_path/'source';source.mkdir();root=tmp_path/'project';root.mkdir()
    image=source/'part.png';Image.new('RGB',(24,16),'gray').save(image)
    before=image.read_bytes()
    with pytest.raises(ValueError,match='Alignment'):
        dw.derive(root,source,image,[],{'kind':'align','degrees':degrees},'Editor',hashlib.sha256(before).hexdigest())
    assert image.read_bytes()==before and dw.derived_history(root,source,image)==[]


def test_alignment_brush_and_independent_direction_keep_one_pixel_frame():
    import base64,io
    mask=Image.new('RGBA',(24,16),(0,0,0,0));mask.putpixel((8,7),(255,0,0,255));buffer=io.BytesIO();mask.save(buffer,format='PNG')
    labels=[{'type':'rotated_bbox','rotated_bbox':[12,8,8,4,10],'direction_deg':275,'text':'Independent heading'},
            {'type':'brush_mask','mask_rle':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()},
            {'type':'tag','label':'NG'}]
    result,rows,_=dw.edit_image_and_annotations(Image.new('RGB',mask.size,'gray'),labels,{'kind':'align','degrees':30})
    with Image.open(io.BytesIO(base64.b64decode(rows[1]['mask_rle'].split(',')[1]))) as actual:
        assert actual.size==result.size and np.array_equal(np.asarray(actual),np.asarray(mask.rotate(-30,expand=True,resample=Image.Resampling.NEAREST)))
    assert rows[0]['rotated_bbox'][4]==40 and rows[0]['direction_deg']==305 and rows[0]['text']==labels[0]['text']
    assert rows[2]==labels[2]
    with pytest.raises(ValueError,match='Unsupported'):
        dw.edit_image_and_annotations(Image.new('RGB',mask.size),[{'type':'spline','points':[[1,1],[2,2],[3,4]]}],{'kind':'align','degrees':30})


def test_heldout_origin_and_revoked_edit_review_cannot_enter_training(tmp_path):
    api,_,source,image,version=setup(tmp_path)
    for part in ('val','test'):
        other=source/part/'NG/sample.png';row=api.get('/api/dataset/metadata/image',params={'image_path':str(other)}).json()
        edited=api.post('/api/data-workbench/derived',json={'image_path':str(other),'expected_sha256':row['content_hash'],'expected_revision':row['revision'],'actor':'Editor','operation':{'kind':'brightness','factor':.5}}).json()
        assert api.post('/api/data-workbench/derived/'+edited['id']+'/review',json={'expected_revision':0,'actor':'Reviewer','decision':'approve','note':'Visual edit only, no training consent'}).status_code==200
        response=api.post('/api/data-workbench/derived-adoptions',json={'version_ids':[edited['id']],'actor':'Reviewer','name':'Rejected heldout use'})
        assert response.status_code==422 and 'train-origin' in response.text
    route='/api/data-workbench/derived/'+version['id']+'/review'
    assert api.post(route,json={'expected_revision':0,'actor':'Reviewer','decision':'approve','note':'Initially valid edit'}).status_code==200
    assert api.post(route,json={'expected_revision':1,'actor':'Reviewer','decision':'reject','note':'Reconsidered geometry'}).status_code==200
    refused=api.post('/api/data-workbench/derived-adoptions',json={'version_ids':[version['id']],'actor':'Reviewer','name':'Revoked edit'})
    assert refused.status_code==422 and 'review' in refused.text.lower()
    assert api.get('/api/data-workbench/derived-adoptions').json()['versions']==[]


def test_branch_publication_failure_removes_owned_staging_and_preserves_original(tmp_path,monkeypatch):
    api,project,source,image,version=setup(tmp_path)
    before=image.read_bytes()
    assert api.post('/api/data-workbench/derived/'+version['id']+'/review',json={'expected_revision':0,'actor':'Reviewer','decision':'approve','note':'Controlled failure recovery'}).status_code==200
    from backend.engine import derived_adoption
    write=derived_adoption.atomic_json
    def fail(path,value):
        if Path(path).name=='record.json':raise OSError('Controlled publication failure')
        return write(path,value)
    monkeypatch.setattr(derived_adoption,'atomic_json',fail)
    response=api.post('/api/data-workbench/derived-adoptions',json={'version_ids':[version['id']],'actor':'Reviewer','name':'Failed branch'})
    assert response.status_code==422 and image.read_bytes()==before
    assert not list((Path(project['dataset_dir'])/'capture_intake/versions').iterdir())
    assert api.get('/api/project/current').json()['source_dataset_dir']==str(source)
    assert api.get('/api/data-workbench/derived-adoptions').json()['versions']==[]
