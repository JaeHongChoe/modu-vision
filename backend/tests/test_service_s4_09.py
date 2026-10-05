import hashlib
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from backend.engine import enhancement as e
from fastapi.testclient import TestClient
from backend.main import create_app

def fixtures(tmp_path):
    source=tmp_path/'source';targets=tmp_path/'targets';source.mkdir();targets.mkdir();rows=[]
    for i,split in enumerate(('train','val','test')):
        pixels=np.full((24,32,3),40+i*60,np.uint8);pixels[4:10,5:11]=10+i
        Image.fromarray(pixels).save(targets/f'{i}.png')
        noise=np.random.default_rng(i).normal(0,10,pixels.shape)
        Image.fromarray(np.clip(pixels.astype(float)+noise,0,255).astype(np.uint8)).save(source/f'{i}.png')
        rows.append({'input':f'{i}.png','target':f'{i}.png','split':split})
    return source,targets,rows

def inventory(root):return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}

def test_import_actual_explicit_pairs_preserves_pixels_trains_and_measures_ssim(tmp_path):
    source,targets,rows=fixtures(tmp_path);before=(inventory(source),inventory(targets))
    manifest=e.import_enhancement_pairs(source,targets,rows,tmp_path/'owned')
    assert manifest['mode']=='explicit_pairs' and manifest['provenance']['sample_count']==3
    for original,row in zip(rows,manifest['records']):
        assert (tmp_path/'owned'/row['input']).read_bytes()==(source/original['input']).read_bytes()
        assert (tmp_path/'owned'/row['target']).read_bytes()==(targets/original['target']).read_bytes()
        assert row['source_relative_path']==original['input'] and row['source_sha256']==before[0][original['input']]
    e.train_enhancement(tmp_path/'owned',tmp_path/'candidate',epochs=1)
    result=e.evaluate_enhancement(tmp_path/'candidate'/'best_model.pt',tmp_path/'owned')
    assert result['sample_count']==1 and result['evaluation_geometry']['resize'] is False
    assert all(np.isfinite(result[k]) for k in ('input_ssim','output_ssim','input_psnr','output_psnr'))
    assert result['ssim_definition']['window_size']==7 and result['defect_preservation_status']=='unreviewed'
    assert (inventory(source),inventory(targets))==before

@pytest.mark.parametrize('failure', ['missing','dimensions','duplicate_input','duplicate_target','split_leak','unsafe'])
def test_invalid_pair_mapping_publishes_no_dataset(tmp_path,failure):
    source,targets,rows=fixtures(tmp_path)
    if failure=='missing':rows[1]['target']='missing.png'
    elif failure=='dimensions':Image.new('RGB',(5,9)).save(targets/'1.png')
    elif failure=='duplicate_input':rows[1]['input']=rows[0]['input']
    elif failure=='duplicate_target':rows[1]['target']=rows[0]['target']
    elif failure=='split_leak':(source/'1.png').write_bytes((source/'0.png').read_bytes())
    elif failure=='unsafe':rows[1]['target']='../targets/1.png'
    with pytest.raises(ValueError):e.import_enhancement_pairs(source,targets,rows,tmp_path/'owned')
    assert not (tmp_path/'owned').exists()

def test_ssim_uniform_window_matches_independent_population_formula():
    a=np.random.default_rng(14).integers(0,256,(8,9,3),dtype=np.uint8)
    b=np.clip(a.astype(int)+np.indices((8,9))[0][...,None]*8,0,255).astype(np.uint8)
    values=[];c1=(.01*255)**2;c2=(.03*255)**2
    for y in range(2):
        for x in range(3):
            for channel in range(3):
                u=a[y:y+7,x:x+7,channel].astype(float);v=b[y:y+7,x:x+7,channel].astype(float)
                mu,mv=u.mean(),v.mean();cov=((u-mu)*(v-mv)).mean()
                values.append(((2*mu*mv+c1)*(2*cov+c2))/((mu**2+mv**2+c1)*(u.var()+v.var()+c2)))
    assert e._pixel_ssim(a,b)==pytest.approx(np.mean(values),abs=1e-12)
    assert e._pixel_ssim(a,a)==pytest.approx(1)

def test_pair_copy_race_leaves_no_published_partial(tmp_path,monkeypatch):
    source,targets,rows=fixtures(tmp_path);atomic=e._atomic
    def changed(path,data):
        atomic(path,data)
        if path.name=='000000.png' and path.parent.name=='inputs':(source/'0.png').write_bytes(b'changed owned source')
    monkeypatch.setattr(e,'_atomic',changed)
    with pytest.raises(ValueError,match='changed'):e.import_enhancement_pairs(source,targets,rows,tmp_path/'owned')
    assert not (tmp_path/'owned').exists()

def test_pair_api_import_preview_and_manifest_are_owned_by_active_project(tmp_path):
    source,targets,rows=fixtures(tmp_path);app=create_app(str(tmp_path/'registry'))
    client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=client.post('/api/project/create',json={'name':'Paired input owner','task':'segmentation'}).json()
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    response=client.post('/api/enhancement/pairs/import',json={'target_folder':str(targets),'records':rows})
    assert response.status_code==200,response.text
    folder=response.json()['dataset_path'];assert Path(folder).is_relative_to(Path(project['dataset_dir']))
    preview=client.get('/api/enhancement/pairs/preview',params={'dataset_path':folder,'sample_index':0})
    assert preview.status_code==200,preview.text
    assert preview.json()['preview_only'] and preview.json()['previews']['input']['source_width']==32
    assert client.get('/api/enhancement/pairs/preview',params={'dataset_path':folder,'sample_index':3}).status_code==422
    client.post('/api/project/create',json={'name':'Foreign pair source','task':'segmentation'})
    assert client.get('/api/enhancement/manifest',params={'dataset_path':folder}).status_code==422
    assert client.get('/api/enhancement/pairs/preview',params={'dataset_path':folder,'sample_index':0}).status_code==422
