"""Template mapping validates the selected checkpoint, including older thin sidecars."""
from types import SimpleNamespace

import pytest
import torch
from fastapi import HTTPException
from backend.api import routes_flow_workspace as route
from backend.engine.flow_workspace import save_template
from backend.engine.flowchart_engine import get_fixed_roi_flowchart


def setup(tmp_path,monkeypatch,payload=None,metadata=None):
    graph=get_fixed_roi_flowchart(inspection_task='classification',job_id='job_old')
    node=next(n for n in graph.nodes if n.data.node_type=='inspection')
    next(e for e in graph.edges if e.source==node.id).predicate={'kind':'class','operator':'present','class_name':'NG','min_confidence':0}
    record=save_template(tmp_path/'library',graph,name='Owned class mapping',project_id='owner')
    project={'id':'owner','project_dir':str(tmp_path),'source_dataset_dir':str(tmp_path/'source')}
    checkpoint=tmp_path/'best_model.pt';torch.save(payload if payload is not None else {'task':'classification','classes':['OK','NG']},checkpoint)
    monkeypatch.setattr(route,'get_current_project',lambda _:project)
    monkeypatch.setattr(route,'_library',lambda _:tmp_path/'library')
    monkeypatch.setattr(route.flows,'catalog_flowchart_models',lambda *a,**kw:{'models':[{'job_id':'job_target','task':'classification'}]})
    monkeypatch.setattr(route.flows,'_resolve_job_artifacts',lambda *a,**kw:(tmp_path,checkpoint,metadata if metadata is not None else {'task':'classification'},'classification',None,None))
    def apply(name):return route.import_template(record['template_id'],route.TemplateMap(project_id='owner',models={node.id:'job_target'},classes={node.id+':name:NG':name}),SimpleNamespace())
    return apply,checkpoint,record,node


@pytest.mark.parametrize('name',['not-in-model','', 'ng'])
def test_template_refuses_unrecorded_class_without_rewriting_originals(tmp_path,monkeypatch,name):
    apply,checkpoint,record,_=setup(tmp_path,monkeypatch);before=checkpoint.read_bytes();template=tmp_path/'library'/f"{record['template_id']}.json";saved=template.read_bytes()
    with pytest.raises(HTTPException) as error:apply(name)
    assert error.value.status_code==422
    assert checkpoint.read_bytes()==before and template.read_bytes()==saved


def test_checkpoint_recorded_class_maps_without_fabricating_legacy_sidecar(tmp_path,monkeypatch):
    apply,checkpoint,_,node=setup(tmp_path,monkeypatch);before=checkpoint.read_bytes()
    mapped=apply('NG')
    assert next(n for n in mapped['pipeline']['nodes'] if n['id']==node.id)['data']['model_job_id']=='job_target'
    assert checkpoint.read_bytes()==before


@pytest.mark.parametrize('payload',[{'task':'classification'}, {'task':'classification','classes':['OK','OK']}, {'task':'classification','classes':[0,'NG']}, {'task':'detection','classes':['OK','NG']}])
def test_unknown_malformed_or_foreign_checkpoint_class_record_is_refused(tmp_path,monkeypatch,payload):
    apply,checkpoint,_,_=setup(tmp_path,monkeypatch,payload=payload);before=checkpoint.read_bytes()
    with pytest.raises(HTTPException) as error:apply('NG')
    assert error.value.status_code==422 and checkpoint.read_bytes()==before


def test_conflicting_sidecar_and_checkpoint_classes_cannot_map(tmp_path,monkeypatch):
    apply,checkpoint,_,_=setup(tmp_path,monkeypatch,metadata={'task':'classification','classes':['OK','scratch']});before=checkpoint.read_bytes()
    with pytest.raises(HTTPException) as error:apply('NG')
    assert error.value.status_code==422 and checkpoint.read_bytes()==before


def test_valid_original_sidecar_supports_legacy_checkpoint_without_class_payload(tmp_path,monkeypatch):
    apply,checkpoint,_,_=setup(tmp_path,monkeypatch,payload={'task':'classification'},metadata={'task':'classification','classes':['OK','NG']});before=checkpoint.read_bytes()
    assert apply('NG')['kind']=='flow' and checkpoint.read_bytes()==before


class UnapprovedControl:
    pass


def test_restricted_checkpoint_reader_refuses_unapproved_global_without_unsafe_retry(tmp_path,monkeypatch):
    apply,checkpoint,_,_=setup(tmp_path,monkeypatch,payload={'task':'classification','classes':['OK','NG'],'unknown':UnapprovedControl()});before=checkpoint.read_bytes()
    real=torch.load;calls=[]
    def observed(*args,**kwargs):calls.append(kwargs);return real(*args,**kwargs)
    monkeypatch.setattr(torch,'load',observed)
    with pytest.raises(HTTPException) as error:apply('NG')
    assert error.value.status_code==422 and checkpoint.read_bytes()==before
    assert calls==[{'map_location':'cpu','weights_only':True}]
