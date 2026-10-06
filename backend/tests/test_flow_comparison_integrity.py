"""Saved A/B records must survive intact and bind the exact versions/context."""
import hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from fastapi import HTTPException
from backend.api import routes_flow_workspace as workspace, routes_flowchart as flows
from backend.engine.flowchart_engine import get_fixed_roi_flowchart

@pytest.fixture
def control(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();image=source/'one.png';Image.new('RGB',(48,48),'red').save(image)
    project={'id':'a','task':'classification','project_dir':str(tmp_path/'project'),'source_dataset_dir':str(source),'active_labelset_id':'default'}
    monkeypatch.setattr(workspace,'get_current_project',lambda _:project)
    monkeypatch.setattr(flows,'get_current_project',lambda _:project)
    graph=get_fixed_roi_flowchart(inspection_task='classification',job_id='job_control')
    va=flows._save_version(graph,'classification',str(source),Path(project['project_dir']))
    graph=graph.model_copy(deep=True);graph.name='B'
    vb=flows._save_version(graph,'classification',str(source),Path(project['project_dir']))
    request=SimpleNamespace(state=SimpleNamespace());calls=[]
    def inference(body,request):
        calls.append(body.pipeline.name)
        return {'status':'success','final_verdict':'REVIEW','is_ok':False,'crops':[],'execution_steps':[]}
    monkeypatch.setattr(flows,'run_flowchart',inference)
    body=workspace.FlowCompare(project_id='a',version_a=va,version_b=vb,image_paths=[str(image)])
    return project,request,body,calls

def test_saved_comparison_hashes_result_and_reopens_unmodified(control):
    p,r,body,_=control;result=workspace.compare_versions(body,r)
    assert result['schema_version']==2 and result['integrity']=='verified'
    unsigned={k:v for k,v in result.items() if k not in ('record_sha256','integrity')}
    actual=hashlib.sha256(json.dumps(unsigned,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    assert result['record_sha256']==actual
    assert workspace.comparisons(r)['comparisons']==[result]
    assert result['quality_approved'] is False
    assert not (Path(p['project_dir'])/'flowcharts'/'active.json').exists()

@pytest.mark.parametrize('field,value',[('status','completed'),('device','cuda'),('graph_a_sha256','f'*64),('rows',[])])
def test_changed_history_is_quarantined_instead_of_shown_as_completed(control,field,value):
    p,r,body,_=control;result=workspace.compare_versions(body,r)
    f=Path(p['project_dir'])/'flowcharts'/'comparisons'/f"{result['comparison_id']}.json"
    changed=json.loads(f.read_text());changed[field]=value if changed.get(field)!=value else 'forged';f.write_text(json.dumps(changed))
    loaded=workspace.comparisons(r)
    assert loaded['comparisons']==[]
    assert loaded['invalid'][0]['comparison_id']==result['comparison_id'] and loaded['invalid'][0]['reason']=='record_hash_changed'
    assert f.exists(),'quarantine readback preserves historical bytes'

def test_legacy_history_remains_visible_but_not_verified(control):
    p,r,_,_=control;root=Path(p['project_dir'])/'flowcharts'/'comparisons';root.mkdir(parents=True)
    legacy={'comparison_id':'legacy','project_id':p['id'],'source_dataset_path':p['source_dataset_dir'],'labelset_id':'default','created_at':'2020-01-01','rows':[],'status':'completed'}
    f=root/'legacy.json';f.write_text(json.dumps(legacy));before=f.read_bytes()
    row=workspace.comparisons(r)['comparisons'][0]
    assert row['integrity']=='legacy_unverified' and row['status']=='completed'
    assert f.read_bytes()==before and 'record_sha256' not in row

@pytest.mark.parametrize('change',['version_a','version_b','source','labelset','project'])
def test_version_or_context_change_between_a_and_b_refuses_second_run(control,monkeypatch,change):
    p,r,body,calls=control
    def inference(req,request):
        calls.append(req.pipeline.name)
        if change.startswith('version'):
            identifier=body.version_a if change=='version_a' else body.version_b
            f=Path(p['project_dir'])/'flowcharts'/'versions'/f'{identifier}.json'
            j=json.loads(f.read_text());j['pipeline']['name']='Replaced while running';f.write_text(json.dumps(j))
        elif change=='source':p['source_dataset_dir']+='/other'
        elif change=='labelset':p['active_labelset_id']='other'
        else:p['id']='other'
        return {'final_verdict':'OK','crops':[],'execution_steps':[]}
    monkeypatch.setattr(flows,'run_flowchart',inference)
    with pytest.raises(HTTPException) as error:workspace.compare_versions(body,r)
    assert error.value.status_code==409 and len(calls)==1
    assert not list((Path(p['project_dir'])/'flowcharts'/'comparisons').glob('*.json'))


def test_debug_comparison_forwards_same_node_and_records_partial_scope(control,monkeypatch):
    _,r,body,calls=control;graph=flows.get_saved_pipeline_version(body.version_a,r);node=next(n for n in graph.nodes if n.data.node_type=='fixed_roi')
    selected=[]
    def inference(req,request):
        selected.append(req.stop_node_id)
        return {'status':'partial','final_verdict':'REVIEW','is_ok':False,'stop_node_id':req.stop_node_id,'crops':[],'execution_steps':[]}
    monkeypatch.setattr(flows,'run_flowchart',inference)
    body.stop_node_id=node.id;record=workspace.compare_versions(body,r)
    assert selected==[node.id,node.id] and record['comparison_scope']=='debug_partial'
    assert record['rows'][0]['result_a']['final_verdict']==record['rows'][0]['result_b']['final_verdict']=='REVIEW'
    body.stop_node_id='missing'
    with pytest.raises(HTTPException) as error:workspace.compare_versions(body,r)
    assert error.value.status_code==422 and len(selected)==2


def test_comparison_that_cannot_be_reopened_is_refused_before_publication(control,monkeypatch):
    p,r,body,_=control
    monkeypatch.setattr(workspace,'MAX_COMPARISON_RECORD_BYTES',1024,raising=False)
    monkeypatch.setattr(flows,'run_flowchart',lambda *a,**kw:{'final_verdict':'REVIEW','annotated_image':'가'*2000,'crops':[],'execution_steps':[]})
    with pytest.raises(HTTPException) as error:workspace.compare_versions(body,r)
    assert error.value.status_code==413
    assert not list((Path(p['project_dir'])/'flowcharts/comparisons').glob('*.json'))


def test_duplicate_history_fields_cannot_be_silently_overwritten(control):
    p,r,body,_=control;result=workspace.compare_versions(body,r)
    file=Path(p['project_dir'])/'flowcharts/comparisons'/f"{result['comparison_id']}.json"
    raw=json.loads(file.read_text());file.write_text('{"status":'+json.dumps(raw['status'])+','+json.dumps(raw)[1:])
    history=workspace.comparisons(r)
    assert history['comparisons']==[] and history['invalid']==[{'comparison_id':result['comparison_id'],'reason':'duplicate_record_field'}]
    assert file.exists()
