"""Patch decision recipes and owned original-coordinate previews."""
import json
import math
from pathlib import Path
import pytest
from PIL import Image
from fastapi.testclient import TestClient


@pytest.mark.parametrize('mode,options,decision',[
    ('max',{},'FAIL'),('vote',{'vote_fraction':.75},'PASS'),
    ('vote',{'vote_fraction':.5},'FAIL'),('ng_count',{'minimum_ng_count':3},'PASS'),
    ('ng_count',{'minimum_ng_count':2},'FAIL'),
])
def test_shared_patch_recipe_uses_inclusive_threshold_and_declared_vote(mode,options,decision):
    from backend.engine.patch_classification import aggregate_patch_scores
    result=aggregate_patch_scores([.1,.5,.6,.2],{'mode':mode,'threshold':.5,**options})
    assert result['decision']==decision
    assert result['ng_count']==2 and result['patch_count']==4
    assert result['ng_fraction']==.5 and result['max_defect_score']==.6
    assert result['recipe']['threshold_comparison']=='greater_than_or_equal'


@pytest.mark.parametrize('scores,recipe',[
    ([],{}),([math.nan],{}),([1.1],{}),([.2],{'mode':'unknown'}),
    ([.2],{'threshold':math.nan}),([.2],{'minimum_ng_count':True}),
    ([.2],{'vote_fraction':0}),([.2],{'extra':'not supported'}),
])
def test_patch_recipe_refuses_unmeasured_invalid_and_unknown_fields(scores,recipe):
    from backend.engine.patch_classification import aggregate_patch_scores
    with pytest.raises(ValueError):aggregate_patch_scores(scores,recipe)


def test_patch_sample_preview_binds_source_pixels_and_active_project(tmp_path):
    from backend.main import create_app
    app=create_app(project_dir=str(tmp_path/'projects'))
    with TestClient(app,headers={'X-Vision-Token':app.state.api_token}) as api:
        project=api.post('/api/project/create',json={'name':'Owned patch sample'}).json()
        source=tmp_path/'source';source.mkdir()
        for i in range(3):
            file=source/f'{i}.png';Image.new('RGB',(35,29),(i,20,30)).save(file)
            file.with_suffix('.json').write_text(json.dumps({'imagePath':file.name,'imageWidth':35,'imageHeight':29,'shapes':[{'label':'chip','shape_type':'rectangle','points':[[0,0],[8,8]]}]}))
        api.put('/api/project/update',json={'source_dataset_dir':str(source)})
        data=api.post('/api/patch-classification/prepare',json={'patch_size':16,'stride':12}).json()
        response=api.get('/api/patch-classification/sample',params={'dataset_path':data['dataset_path'],'sample_index':0})
        assert response.status_code==200,response.text
        sample=response.json();assert sample['box']==[0,0,16,16]
        assert sample['source_size']==[35,29] and sample['preview_only'] is True
        assert sample['original_base64'] and sample['patch_base64']
        assert sample['source_relative_path'] in {'0.png','1.png','2.png'}
        api.post('/api/project/create',json={'name':'Foreign'})
        assert api.get('/api/patch-classification/sample',params={'dataset_path':data['dataset_path'],'sample_index':0}).status_code==422
        assert api.post('/api/project/open',json={'project_dir':project['project_dir']}).status_code==200
        Image.new('RGB',(35,29),(200,20,30)).save(source/sample['source_relative_path'])
        assert api.get('/api/patch-classification/sample',params={'dataset_path':data['dataset_path'],'sample_index':0}).status_code==422


def test_patch_recipe_flow_requires_one_bound_patch_input_and_exact_threshold():
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart,ordered_linear_nodes
    from backend.engine.patch_classification import apply_patch_recipe
    flow=get_single_segmentation_flowchart(job_id='job_patch')
    inspection=next(n for n in flow.nodes if n.data.node_type=='inspection');inspection.data.task='patch_classification'
    apply_patch_recipe(flow,{'mode':'vote','threshold':.37,'vote_fraction':.75})
    assert ordered_linear_nodes(flow)
    decision=next(n for n in flow.nodes if n.data.node_type=='decision')
    assert decision.data.rule=='patch_recipe' and inspection.data.threshold==.37
    inspection.data.threshold=.5
    with pytest.raises(ValueError,match='Patch recipe'):ordered_linear_nodes(flow)
