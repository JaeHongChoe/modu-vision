"""Saved user flow modules and reproducible A/B inspection experiments."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from backend.api.routes_project import get_current_project
from backend.api import routes_flowchart as flows
from backend.engine.flowchart_engine import FlowchartPipeline, FlowchartRunRequest
from backend.engine.flow_provenance import pipeline_sha256
from backend.engine.flow_workspace import save_template, load_templates, map_template, compare_results, atomic_json
from backend.engine.dicom_input import open_source_image

router=APIRouter(prefix='/api/flow-workspace',tags=['flow-workspace'])
MAX_COMPARISON_RECORD_BYTES=128*1024*1024


def _library(request):
    base=Path(os.environ.get('MODU_FLOW_TEMPLATE_DIR',str(Path.home()/'.modu_vision'/'flow_templates')))
    user=getattr(getattr(request,'state',None),'account_user',None)
    return base/'accounts'/hashlib.sha256(str(user['id']).encode()).hexdigest() if user else base


def _context(request,project_id):
    project=get_current_project(request)
    if project['id']!=project_id: raise HTTPException(409,'Project changed during flow workspace operation')
    return project


def _image(project,raw):
    path=Path(raw).expanduser().resolve()
    roots=[Path(project[k]).resolve() for k in ('source_dataset_dir','dataset_dir') if project.get(k)]
    if not path.is_file() or not any(path.is_relative_to(root) for root in roots):
        raise HTTPException(422,'Select an existing image from the current project dataset')
    return path


class TemplateSave(BaseModel):
    model_config=ConfigDict(extra='forbid')
    project_id:str
    name:str=Field(min_length=1,max_length=120)
    pipeline:FlowchartPipeline
    node_ids:list[str]|None=None


class TemplateMap(BaseModel):
    model_config=ConfigDict(extra='forbid')
    project_id:str
    models:dict[str,str]=Field(default_factory=dict)
    classes:dict[str,str|int]=Field(default_factory=dict)


@router.get('/templates')
def templates(request:Request): return {'templates':load_templates(_library(request))}


@router.post('/templates')
def store_template(req:TemplateSave,request:Request):
    _context(request,req.project_id)
    try: return save_template(_library(request),req.pipeline,name=req.name,project_id=req.project_id,node_ids=req.node_ids)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


@router.post('/templates/{template_id}/map')
def import_template(template_id:str,req:TemplateMap,request:Request):
    project=_context(request,req.project_id)
    record=next((r for r in load_templates(_library(request)) if r['template_id']==template_id),None)
    if record is None: raise HTTPException(404,'Template is unavailable or its checksum changed')
    catalog=flows.catalog_flowchart_models(project.get('source_dataset_dir') or project['dataset_dir'],request=request)
    try:
        graph=map_template(record,req.models,req.classes,catalog['models'])
        return {'kind':record['kind'],'pipeline':graph.model_dump(),'ports':record['ports']}
    except (ValueError,TypeError) as exc: raise HTTPException(422,str(exc)) from exc


@router.get('/image-info')
def image_info(image_path:str,request:Request):
    project=get_current_project(request)
    path=_image(project,image_path)
    try:
        with open_source_image(path) as im: return {'width':im.width,'height':im.height,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    except (OSError,ValueError) as exc: raise HTTPException(422,'Image cannot be decoded') from exc


class FlowCompare(BaseModel):
    model_config=ConfigDict(extra='forbid')
    project_id:str
    version_a:str
    version_b:str
    image_paths:list[str]=Field(min_length=1,max_length=20)
    name:str=Field(default='Version comparison',min_length=1,max_length=120)
    device:str=Field(default='cpu',pattern=r'^(cpu|mps|cuda(?::[0-9]+)?)$')
    execution_target:Literal['local','selected_compute']='local'
    compute_profile_id:str|None=None
    stop_node_id:str|None=Field(default=None,min_length=1,max_length=200)


def _comparisons(project): return Path(project['project_dir'])/'flowcharts'/'comparisons'


def _comparison_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _unique_record_fields(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate_record_field')
        result[key]=value
    return result


def _ensure_comparison_size(record):
    # Match atomic_json's UTF-8 formatting without allocating a second full preview envelope.
    size=0
    for part in json.JSONEncoder(ensure_ascii=False,indent=2,allow_nan=False).iterencode(record):
        size+=len(part.encode('utf-8'))
        if size>MAX_COMPARISON_RECORD_BYTES:
            raise HTTPException(413,'Comparison previews exceed the saved history limit; compare fewer images')


def _comparison_context(project):
    return {key:project.get(key,'default' if key=='active_labelset_id' else None)
            for key in ('id','project_dir','source_dataset_dir','active_labelset_id','task')}


def _same_comparison_context(request,context,versions):
    if _comparison_context(get_current_project(request))!=context:
        raise HTTPException(409,'Comparison project, source or labelset changed; results were not saved')
    for file,expected in versions.items():
        if file.is_symlink() or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=expected:
            raise HTTPException(409,'Saved comparison flow version changed; results were not saved')


@router.get('/comparisons')
def comparisons(request:Request):
    project=get_current_project(request)
    rows=[];invalid=[]
    for file in _comparisons(project).glob('*.json'):
        try:
            if file.is_symlink() or file.stat().st_size>MAX_COMPARISON_RECORD_BYTES:
                raise ValueError('linked_or_oversize_record')
            record=json.loads(file.read_text(encoding='utf-8'),object_pairs_hook=_unique_record_fields)
            if record['project_id']==project['id'] and record.get('source_dataset_path')==project.get('source_dataset_dir') and record.get('labelset_id','default')==project.get('active_labelset_id','default'):
                if record.get('comparison_id')!=file.stem or not isinstance(record.get('rows'),list) or not isinstance(record.get('created_at'),str):
                    raise ValueError('invalid_record_identity')
                if 'record_sha256' in record or 'schema_version' in record:
                    unsigned={k:v for k,v in record.items() if k not in ('record_sha256','integrity')}
                    if record.get('schema_version')!=2 or record.get('record_sha256')!=_comparison_digest(unsigned):
                        raise ValueError('record_hash_changed')
                    integrity='verified'
                else:integrity='legacy_unverified'
                rows.append({**record,'integrity':integrity})
        except (OSError,ValueError,KeyError,TypeError) as exc:
            invalid.append({'comparison_id':file.stem,'reason':str(exc) if isinstance(exc,ValueError) else 'unreadable_record'})
    return {'comparisons':sorted(rows,key=lambda r:r['created_at'],reverse=True),'invalid':invalid}


@router.post('/comparisons')
def compare_versions(req:FlowCompare,request:Request):
    project=_context(request,req.project_id)
    context=_comparison_context(project)
    if req.version_a==req.version_b: raise HTTPException(422,'Select two different saved flow versions')
    if req.execution_target=='selected_compute' and not req.compute_profile_id:
        raise HTTPException(422,'Select a compute profile before target comparison')
    versions=flows.list_saved_pipelines(project.get('source_dataset_dir'),request=request)['pipelines']
    if not all(any(v['version_id']==x for v in versions) for x in (req.version_a,req.version_b)):
        raise HTTPException(409,'Saved flow versions must belong to the selected source dataset')
    version_files={flows._version_dir(Path(project['project_dir']))/f'{identifier}.json':None for identifier in (req.version_a,req.version_b)}
    for file in version_files:
        if file.is_symlink() or not file.is_file():raise HTTPException(409,'Saved comparison flow version is unavailable')
        version_files[file]=hashlib.sha256(file.read_bytes()).hexdigest()
    ga=flows.get_saved_pipeline_version(req.version_a,request=request)
    gb=flows.get_saved_pipeline_version(req.version_b,request=request)
    if req.stop_node_id and any(req.stop_node_id not in {node.id for node in graph.nodes} for graph in (ga,gb)):
        raise HTTPException(422,'Debug comparison node must exist in both saved versions')
    _same_comparison_context(request,context,version_files)
    paths=list(dict.fromkeys(_image(project,p) for p in req.image_paths))
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    record={'schema_version':2,'quality_approved':False,'comparison_id':uuid.uuid4().hex,'project_id':project['id'],'source_dataset_path':project.get('source_dataset_dir'),'labelset_id':project.get('active_labelset_id','default'),
        'name':req.name,'created_at':datetime.now(timezone.utc).isoformat(),'version_a':req.version_a,'version_b':req.version_b,
        'graph_a_sha256':pipeline_sha256(ga),'graph_b_sha256':pipeline_sha256(gb),'device':req.device,
        'execution_target':req.execution_target,'compute_profile_id':req.compute_profile_id,
        'stop_node_id':req.stop_node_id,'comparison_scope':'debug_partial' if req.stop_node_id else 'whole_flow',
        'version_file_sha256':{file.stem:value for file,value in version_files.items()},'rows':[]}
    for path in paths:
        _same_comparison_context(request,context,version_files)
        row={'image_path':str(path),'file_name':path.name,'image_sha256':hashes[str(path)]}
        snapshot_root=Path(project.get('dataset_dir') or Path(project['project_dir'])/'dataset')/'flow_compare_inputs'
        snapshot_root.mkdir(parents=True,exist_ok=True)
        # Both versions open the same isolated bytes. A changing source cannot
        # cause B to inspect a different image while retaining A's receipt hash.
        with tempfile.TemporaryDirectory(prefix='comparison-',dir=snapshot_root) as temporary:
            snapshot=Path(temporary)/path.name
            contents=path.read_bytes()
            if hashlib.sha256(contents).hexdigest()!=hashes[str(path)]:
                raise HTTPException(409,'Test input changed before comparison')
            snapshot.write_bytes(contents)
            snapshot.chmod(0o400)
            try:
                a=flows.run_flowchart(FlowchartRunRequest(project_id=project['id'],pipeline=ga,image_path=str(snapshot),execution_target=req.execution_target,device=req.device,compute_profile_id=req.compute_profile_id,stop_node_id=req.stop_node_id),request=request)
                _same_comparison_context(request,context,version_files)
                b=flows.run_flowchart(FlowchartRunRequest(project_id=project['id'],pipeline=gb,image_path=str(snapshot),execution_target=req.execution_target,device=req.device,compute_profile_id=req.compute_profile_id,stop_node_id=req.stop_node_id),request=request)
                _same_comparison_context(request,context,version_files)
                if hashlib.sha256(snapshot.read_bytes()).hexdigest()!=hashes[str(path)]:
                    raise HTTPException(409,'Frozen comparison input was changed')
                for result in (a,b): result['image_path']=str(path)
                row.update(result_a=a,result_b=b,difference=compare_results(a,b))
            except HTTPException as exc:
                if exc.status_code==409: raise
                row['error']=str(exc.detail)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=hashes[str(path)]: raise HTTPException(409,'Test input changed during comparison; results were not saved')
        record['rows'].append(row)
        _ensure_comparison_size(record)
    record['status']='error' if any('error' in r for r in record['rows']) else 'completed'
    _same_comparison_context(request,context,version_files)
    record['record_sha256']=_comparison_digest(record)
    _ensure_comparison_size(record)
    atomic_json(_comparisons(project)/f"{record['comparison_id']}.json",record)
    return {**record,'integrity':'verified'}
