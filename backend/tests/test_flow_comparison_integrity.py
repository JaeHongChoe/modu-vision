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
    checkpoint=tmp_path/'fixture-checkpoint.bin';checkpoint.write_bytes(b'controlled model identity, inference mocked')
    monkeypatch.setattr(flows,'trusted_checkpoint',lambda *a,**kw:checkpoint)
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


def test_model_bytes_changed_between_versions_refuse_old_cohort_receipt(control,monkeypatch,tmp_path):
    p,r,body,calls=control;p['models_dir']=str(tmp_path/'models')
    checkpoint=tmp_path/'checkpoint.bin';checkpoint.write_bytes(b'checkpoint-original')
    monkeypatch.setattr(flows,'trusted_checkpoint',lambda *a,**kw:checkpoint)
    def changing(req,request):
        calls.append(req.pipeline.name);checkpoint.write_bytes(b'checkpoint-replaced')
        return {'final_verdict':'OK','crops':[],'execution_steps':[]}
    monkeypatch.setattr(flows,'run_flowchart',changing)
    with pytest.raises(HTTPException) as error:workspace.compare_versions(body,r)
    assert error.value.status_code==409 and 'model' in error.value.detail.lower() and len(calls)==1
    assert not list((Path(p['project_dir'])/'flowcharts/comparisons').glob('*.json'))


def test_new_model_metadata_during_comparison_cannot_be_omitted_from_identity(control,monkeypatch,tmp_path):
    p,r,body,calls=control;checkpoint=tmp_path/'checkpoint.bin';checkpoint.write_bytes(b'fixed checkpoint')
    monkeypatch.setattr(flows,'trusted_checkpoint',lambda *a,**kw:checkpoint)
    def changing(req,request):
        calls.append(req.pipeline.name);(tmp_path/'model_meta.json').write_text('{"changed":true}')
        return {'final_verdict':'OK','crops':[],'execution_steps':[]}
    monkeypatch.setattr(flows,'run_flowchart',changing)
    with pytest.raises(HTTPException) as error:workspace.compare_versions(body,r)
    assert error.value.status_code==409 and len(calls)==1


def test_saved_comparison_binds_actual_model_bytes_and_frozen_input_list(control):
    p,r,body,_=control;record=workspace.compare_versions(body,r)
    binding=record['model_bindings']['a'][0]
    assert binding['job_id']=='job_control' and binding['task']=='classification'
    assert binding['checkpoint_sha256']==hashlib.sha256((Path(p['project_dir']).parent/'fixture-checkpoint.bin').read_bytes()).hexdigest()
    assert record['model_bindings']['a']==record['model_bindings']['b']
    assert len(record['cohort_sha256'])==64 and binding['identity_files_sha256']['job_receipt.json'] is None


def test_removed_input_cannot_publish_even_if_both_model_runs_complete(control,monkeypatch):
    p,r,body,calls=control
    def removing(req,request):
        calls.append(1);Path(body.image_paths[0]).unlink(missing_ok=True)
        return {'final_verdict':'OK','crops':[],'execution_steps':[]}
    monkeypatch.setattr(flows,'run_flowchart',removing)
    with pytest.raises(HTTPException) as error:workspace.compare_versions(body,r)
    assert error.value.status_code==409 and len(calls)==2
    assert not list((Path(p['project_dir'])/'flowcharts/comparisons').glob('*.json'))


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
