import json
from pathlib import Path
from PIL import Image


def test_unused_images_remain_visible_but_never_enter_training(tmp_path):
    from backend.engine.annotation_storage import set_request_project_root,reset_request_project_root,set_request_annotation_root,reset_request_annotation_root
    from backend.engine.dataset_loaders import ClassificationDataset
    from backend.engine.grouped_dataset_views import source_image_paths
    from backend.engine import dataset_metadata as dm
    project=tmp_path/'project';project.mkdir();source=tmp_path/'source';(source/'OK').mkdir(parents=True)
    for i in range(3):Image.new('RGB',(32,32),(i,0,0)).save(source/'OK'/f'{i}.png')
    annotations=project/'annotations';annotations.mkdir()
    (project/'project.json').write_text(json.dumps({'source_dataset_dir':str(source)}))
    ptoken=set_request_project_root(project);atoken=set_request_annotation_root(annotations)
    try:
        row=dm.metadata_for_path(project,source,source/'OK/1.png',annotations)
        dm.update_metadata(project,source,row['image_uuid'],row['revision'],'operator',{'usage_state':'not_used'},annotations)
        assert len(source_image_paths(source,'classification',include_unused=True))==3
        assert len(source_image_paths(source,'classification'))==2
        dataset=ClassificationDataset(source,split=None)
        assert {p.name for p,_ in dataset.samples}=={'0.png','2.png'}
        changed=dm.metadata_for_path(project,source,source/'OK/1.png',annotations)
        dm.update_metadata(project,source,changed['image_uuid'],changed['revision'],'operator',{'usage_state':'active'},annotations)
        assert len(ClassificationDataset(source,split=None))==3
    finally:
        reset_request_annotation_root(atoken);reset_request_project_root(ptoken)


def test_statistics_filters_and_gallery_use_the_same_image_grain(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app=create_app(project_dir=str(tmp_path/'projects'));api=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    api.post('/api/project/create',json={'name':'Usage'})
    source=tmp_path/'source';(source/'OK').mkdir(parents=True)
    for i in range(2):Image.new('RGB',(32,32),(i,0,0)).save(source/'OK'/f'{i}.png')
    api.put('/api/project/update',json={'source_dataset_dir':str(source)})
    rows=api.get('/api/dataset/metadata').json()['items'];row=rows[0]
    edited=api.patch('/api/dataset/metadata/'+row['image_uuid'],json={'expected_revision':row['revision'],'actor':'Operator','changes':{'usage_state':'not_used'}})
    assert edited.status_code==200,edited.text
    stats=api.get('/api/dataset/metadata/statistics').json()
    assert stats['assignments']['not_used']['count']==1
    listing=api.get('/api/dataset/images',params={'folder_path':str(source),'task':'classification','split':'not_used'})
    assert listing.status_code==200,listing.text
    assert listing.json()['total']==1
    assert listing.json()['items'][0]['file_path']==row['file_path']
