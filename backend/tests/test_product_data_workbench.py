"""Product data workflow contracts use real images and disk reopen."""
import base64
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import numpy as np
import pytest
from PIL import Image


def engine():
    assert importlib.util.find_spec('backend.engine.data_workbench') is not None, 'Unified diagnostic/derived/review engine must exist'
    return importlib.import_module('backend.engine.data_workbench')


@pytest.fixture
def workspace(tmp_path):
    source=tmp_path/'source';source.mkdir();project=tmp_path/'project';project.mkdir()
    pixels=np.zeros((48,64,3),np.uint8);pixels[:,:32]=80;pixels[12:36,12:52]=190;pixels[18:30,24:40]=30
    Image.fromarray(pixels).save(source/'part.png')
    Image.fromarray(pixels).save(source/'near.bmp')
    Image.new('RGB',(64,48),'white').save(source/'over.png')
    Image.new('RGB',(64,48),'black').save(source/'under.png')
    return project,source


def test_readiness_reports_quality_near_duplicate_and_split_leakage(workspace):
    project,source=workspace;from backend.engine.dataset_metadata import list_metadata
    rows=list_metadata(project,source,project/'annotations')
    report=engine().diagnose(rows,{'part.png':'train','near.bmp':'test'},blur_threshold=10,exposure_fraction=.95,near_distance=2)
    assert len(report['items'])==4 and report['source_sha256']['part.png']==hashlib.sha256((source/'part.png').read_bytes()).hexdigest()
    items={r['relative_path']:r for r in report['items']}
    assert 'overexposed' in items['over.png']['issues'] and 'underexposed' in items['under.png']['issues']
    assert 'blur' in items['under.png']['issues']
    near=next(p for p in report['near_duplicates'] if set(p['images'])=={'part.png','near.bmp'})
    assert near['cross_split'] and near['distance']==0 and not near['exact_bytes']
    assert report['decision']=='needs_review' and report['issue_counts']['near_duplicate']>=2


def mask_url():
    mask=np.zeros((48,64,4),np.uint8);mask[10:20,10:30]=[255,0,0,255]
    out=io.BytesIO();Image.fromarray(mask).save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()


def test_derived_edit_transforms_every_shape_and_preserves_original_and_history(workspace):
    project,source=workspace;original=(source/'part.png').read_bytes();sha=hashlib.sha256(original).hexdigest()
    shapes=[{'id':'b','type':'bbox','label':'part','bbox':[10,10,30,20]},
            {'id':'p','type':'polygon','label':'part','polygon':[[10,10],[30,10],[30,20],[10,20]]},
            {'id':'m','type':'brush_mask','label':'part','mask_rle':mask_url()},
            {'id':'r','type':'rotated_bbox','label':'part','rotated_bbox':[20,15,20,10,0],'direction_deg':350}]
    first=engine().derive(project,source,source/'part.png',shapes,{'kind':'crop','rect':[5,5,45,35]},'Kim',sha)
    assert first['source_sha256']==sha and first['size']==[40,30] and first['annotations'][0]['bbox']==[5,5,25,15]
    second=engine().derive(project,source,source/'part.png',[],{'kind':'rotate','degrees':90},'Kim',sha,parent_id=first['id'])
    assert second['size']==[30,40] and second['annotations'][0]['bbox']==[15,5,25,25]
    assert second['annotations'][3]['direction_deg']==80 and second['annotations'][3]['rotated_bbox'][4]==-90
    with Image.open(io.BytesIO(base64.b64decode(second['annotations'][2]['mask_rle'].split(',')[1]))) as mask:
        assert mask.size==(30,40) and np.asarray(mask)[:,:,3].sum()==200*255
    history=engine().derived_history(project,source,source/'part.png')
    assert [r['id'] for r in history]==[first['id'],second['id']]
    assert (source/'part.png').read_bytes()==original and not (source/'part.json').exists()
    reopened=engine().read_derived(project,source,second['id'])
    assert reopened['parent_id']==first['id'] and Path(reopened['file_path']).is_relative_to(project)
    third=engine().derive(project,source,source/'part.png',[],{'kind':'flip','axis':'horizontal'},'Kim',sha,parent_id=second['id'])
    assert third['annotations'][3]['direction_deg']==100
    exported=json.loads((Path(third['dataset_path'])/'images'/'derived.json').read_text())
    assert exported['shapes'][-1]['flags']['studio_rotated_bbox']==third['annotations'][3]['rotated_bbox']
    assert exported['shapes'][-1]['flags']['studio_direction_deg']==100


def test_derived_rejects_stale_source_malformed_shape_and_partial_obb(workspace):
    project,source=workspace;sha=hashlib.sha256((source/'part.png').read_bytes()).hexdigest();e=engine()
    with pytest.raises(ValueError,match='changed'):
        e.derive(project,source,source/'part.png',[],{'kind':'flip','axis':'vertical'},'Kim','0'*64)
    with pytest.raises(ValueError,match='polygon'):
        e.derive(project,source,source/'part.png',[{'type':'polygon','label':'part','polygon':[[0,0],[1,1]]}],{'kind':'flip','axis':'vertical'},'Kim',sha)
    with pytest.raises(ValueError,match='rotated'):
        e.derive(project,source,source/'part.png',[{'type':'rotated_bbox','label':'part','rotated_bbox':[20,15,20,10,0]}],{'kind':'crop','rect':[15,5,45,35]},'Kim',sha)
    assert not e.derived_history(project,source,source/'part.png')


def test_ranked_review_queue_persists_cursor_origin_and_conflict(workspace):
    project,source=workspace;e=engine();rows=[{'file_path':str(source/name),'is_correct':correct,'confidence':score,'disagreement':disagree} for name,correct,score,disagree in [('part.png',True,.51,False),('near.bmp',True,.9,True),('over.png',False,.95,False)]]
    queue=e.create_review_queue(project,source,'detection','default',rows,{'evaluation_id':'evaluation_example','job_id':'job1','step':4},.5,.05)
    assert [r['relative_path'] for r in queue['items']]==['over.png','near.bmp','part.png']
    assert queue['items'][0]['reasons']==['error']
    updated=e.advance_review_queue(project,source,'detection','default',queue['id'],queue['revision'],'over.png','reviewed','Kim')
    reopened=e.read_review_queue(project,source,'detection','default',queue['id'])
    assert reopened['cursor']==1 and reopened['origin']['job_id']=='job1' and reopened['items'][0]['state']=='reviewed'
    with pytest.raises(ValueError,match='revision'):
        e.advance_review_queue(project,source,'detection','default',queue['id'],queue['revision'],'near.bmp','reviewed','Kim')
    with pytest.raises(ValueError,match='scope'):
        e.read_review_queue(project,source,'segmentation','default',queue['id'])
    Image.new('RGB',(64,48),'red').save(source/'near.bmp')
    with pytest.raises(ValueError,match='changed'):
        e.read_review_queue(project,source,'detection','default',queue['id'])


def test_real_comparison_rows_rank_errors_disagrees_and_threshold_without_inventing_truth(workspace):
    project,source=workspace;e=engine()
    rows=[{'file_path':str(source/'part.png'),'ground_truth_verdict':'OK','candidate':{'verdict':'NG','max_defect_score':.9},'incumbent':{'verdict':'OK'},'disagrees':True},
          {'file_path':str(source/'near.bmp'),'ground_truth_verdict':None,'candidate':{'verdict':'NG','max_defect_score':.9},'incumbent':{'verdict':'OK'},'disagrees':True},
          {'file_path':str(source/'over.png'),'ground_truth_verdict':'OK','candidate':{'verdict':'OK','max_defect_score':.49},'incumbent':{'verdict':'OK'},'disagrees':False}]
    queue=e.create_review_queue(project,source,'detection','default',rows,{'comparison_id':'comparison_example','step':4},.5,.05)
    assert [r['reasons'] for r in queue['items']]==[['error','disagreement'],['disagreement'],['threshold']]


def test_derived_history_ignores_unrelated_changed_source_and_rejects_invalid_boundaries(workspace):
    project,source=workspace;e=engine()
    for name in ('part.png','over.png'):
        e.derive(project,source,source/name,[],{'kind':'flip','axis':'horizontal'},'Kim',e.sha256(source/name))
    (source/'over.png').write_bytes(b'changed unrelated source')
    assert len(e.derived_history(project,source,source/'part.png'))==1


def test_derived_rejects_out_of_bounds_and_zero_area_geometry(workspace):
    project,source=workspace;e=engine()
    with pytest.raises(ValueError,match='bounds'):
        e.derive(project,source,source/'part.png',[{'type':'bbox','bbox':[-1,0,20,20]}],{'kind':'rotate','degrees':90},'Kim',e.sha256(source/'part.png'))
    with pytest.raises(ValueError,match='polygon'):
        e.derive(project,source,source/'part.png',[{'type':'polygon','polygon':[[0,0],[1,1],[2,2]]}],{'kind':'flip','axis':'horizontal'},'Kim',e.sha256(source/'part.png'))


def test_derived_polygon_supplemental_bbox_tracks_transformed_points(workspace):
    project,source=workspace;e=engine();image=source/'part.png';digest=e.sha256(image)
    original=image.read_bytes();row={'type':'polygon','label':'part','points':[[10,10],[30,10],[30,20],[10,20]],'bbox':[10,10,30,20]}
    result=e.derive(project,source,image,[row],{'kind':'rotate','degrees':90},'Kim',digest)
    polygon=result['annotations'][0]
    assert polygon['bbox']==[28,10,38,30]
    assert polygon['bbox']==[min(x for x,y in polygon['points']),min(y for x,y in polygon['points']),max(x for x,y in polygon['points']),max(y for x,y in polygon['points'])]
    assert image.read_bytes()==original
