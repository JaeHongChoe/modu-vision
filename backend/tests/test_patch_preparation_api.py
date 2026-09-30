import json
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient


def test_patch_api_prepares_owned_truth_and_rejects_foreign_data(tmp_path):
    from backend.main import create_app
    app=create_app(project_dir=str(tmp_path/'projects'))
    api=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=api.post('/api/project/create',json={'name':'Patch QA'}).json()
    source=tmp_path/'source';source.mkdir()
    for i in range(3):
        path=source/f'{i}.png';Image.new('RGB',(32,32),(i,0,0)).save(path)
        path.with_suffix('.json').write_text(json.dumps({'imagePath':path.name,'imageWidth':32,'imageHeight':32,'shapes':[
            {'label':'chip','shape_type':'rectangle','points':[[0,0],[8,8]]}]}))
    api.put('/api/project/update',json={'source_dataset_dir':str(source)})
    response=api.post('/api/patch-classification/prepare',json={'patch_size':16,'stride':16})
    assert response.status_code==200,response.text
    data=response.json();prepared=Path(data['dataset_path'])
    assert prepared.is_relative_to(Path(project['dataset_dir']))
    assert api.get('/api/patch-classification/manifest',params={'dataset_path':str(prepared)}).json()['patch_count']==12
    assert not (source/'patches.json').exists()
    api.post('/api/project/create',json={'name':'Other'})
    assert api.post('/api/patch-classification/train',json={'dataset_path':str(prepared),'epochs':1}).status_code==422
