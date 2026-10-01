"""Project/source/labelset-bound product data workflow APIs."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Any,Literal
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field,model_validator
from backend.api.routes_project import get_current_project
from backend.api.routes_dataset_versions import _source_path
from backend.api.routes_dataset_metadata import _rows
from backend.api import routes_annotation,routes_dataset
from backend.engine import data_workbench as dw,dataset_metadata as dm
from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root,set_request_project_root,reset_request_project_root
from backend.engine.evaluation_history import EvaluationHistory
from backend.engine.dataset_loaders import split_root_scope

router=APIRouter(prefix='/api/data-workbench',tags=['data-workbench'])

class DiagnosticRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    blur_threshold:float=Field(50,ge=0,allow_inf_nan=False)
    exposure_fraction:float=Field(.9,gt=0,le=1)
    near_distance:int=Field(6,ge=0,le=64)

class DerivedRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    image_path:str
    expected_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    expected_revision:int=Field(ge=1)
    operation:dict[str,Any]
    actor:str=Field(min_length=1,max_length=100)
    parent_id:str|None=None

class QueueRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    evaluation_id:str|None=None
    comparison_id:str|None=None
    threshold:float=Field(.5,ge=0,le=1)
    margin:float=Field(.05,ge=0,le=1)
    @model_validator(mode='after')
    def one_origin(self):
        if bool(self.evaluation_id)==bool(self.comparison_id):raise ValueError('Choose exactly one saved evaluation or comparison')
        return self

class AdvanceRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=1)
    relative_path:str
    state:Literal['reviewed','skipped']
    actor:str=Field(min_length=1,max_length=100)


def _context(request):
    project=get_current_project(request);return project,_source_path(project)


def _error(exc):
    if isinstance(exc,dm.RevisionConflict):return HTTPException(409,detail={'message':str(exc),'current':exc.current})
    message=str(exc);conflict=any(term in message.lower() for term in ('changed','revision','scope changed','stale'))
    return HTTPException(409 if conflict else 422,detail=message)


@contextmanager
def _annotations(project):
    a=set_request_annotation_root(Path(project['annotations_dir']));p=set_request_project_root(Path(project['project_dir']))
    try:yield
    finally:reset_request_project_root(p);reset_request_annotation_root(a)


def _derived_scope(project):return {'task':project['task'],'labelset_id':project.get('active_labelset_id','default')}

def _split_assignments(project,source,rows):
    with split_root_scope(Path(project['dataset_dir'])/'splits'):
        saved=routes_dataset._read_split_manifest(source)
    return {row['relative_path']:saved.get(row['file_path'],saved.get(row['relative_path'],next((p for p in Path(row['relative_path']).parts[:-1] if p in {'train','val','test'}),'unassigned'))) for row in rows}


def _version_response(row):
    return {**row,'image_url':f"/api/data-workbench/derived/{row['id']}/image"}


@router.post('/diagnostics')
def diagnose(req:DiagnosticRequest,request:Request):
    project,source=_context(request)
    try:
        rows=_rows(project,source);assignments=_split_assignments(project,source,rows)
        report=dw.diagnose(rows,assignments,**req.model_dump())
        from backend.engine.dataset_summary import dataset_summary
        with _annotations(project):summary=dataset_summary(source,project['task'],assignments={r['file_path']:assignments[r['relative_path']] for r in rows},metadata={r['file_path']:r for r in rows})
        report.update(summary=summary,split_assignments=assignments,scope={'source':str(source),'task':project['task'],'labelset_id':project.get('active_labelset_id','default')})
        dw._write(dw._storage(project['project_dir'],source)/f"diagnostics_{project.get('active_labelset_id','default')}.json",report)
        return report
    except (ValueError,OSError,KeyError) as exc:raise _error(exc) from exc


@router.get('/diagnostics')
def saved_diagnostics(request:Request):
    project,source=_context(request);path=dw._storage(project['project_dir'],source)/f"diagnostics_{project.get('active_labelset_id','default')}.json"
    if not path.is_file():return {'report':None}
    try:
        if path.is_symlink():raise ValueError('Diagnostic report cannot be a symbolic link')
        report=json.loads(path.read_text())
        if report['scope']!={'source':str(source),'task':project['task'],'labelset_id':project.get('active_labelset_id','default')}:raise ValueError('Diagnostic scope changed')
        current={r['relative_path']:r for r in _rows(project,source)}
        assignments=_split_assignments(project,source,list(current.values()))
        report['stale']=report.get('split_assignments')!=assignments or set(current)!=set(report['source_sha256']) or any(r['content_hash']!=report['source_sha256'].get(name) or r['revision']!=next((old['revision'] for old in report['items'] if old['relative_path']==name),None) for name,r in current.items())
        for item in report['items']:item['current_split']=assignments.get(item['relative_path'],'unassigned')
        for pair in report['near_duplicates']:
            pair['current_splits']=[assignments.get(name,'unassigned') for name in pair['images']]
            pair['current_cross_split']=len(set(pair['current_splits'])-{'unassigned'})>1
        return report
    except (ValueError,OSError,KeyError) as exc:raise _error(exc) from exc


@router.post('/derived')
def derive(req:DerivedRequest,request:Request):
    project,source=_context(request)
    try:
        image=dw._source_image(source,req.image_path)
        if str(image) not in {r['file_path'] for r in _rows(project,source)}:raise ValueError('Image is absent from the active task source inventory')
        with dm.metadata_transaction(Path(project['project_dir']),source,Path(project['annotations_dir'])):
            row=dm.metadata_for_path(Path(project['project_dir']),source,image,Path(project['annotations_dir']))
            if row['revision']!=req.expected_revision:raise dm.RevisionConflict(row)
            with _annotations(project):annotations=routes_annotation.get_annotations(image.stem,file_path=str(image)).get('annotations',[])
            version=dw.derive(project['project_dir'],source,image,annotations,req.operation,req.actor,req.expected_sha256,req.parent_id,_derived_scope(project))
        return _version_response(version)
    except (ValueError,OSError,KeyError) as exc:raise _error(exc) from exc


@router.get('/derived')
def history(request:Request,image_path:str):
    project,source=_context(request)
    try:return {'versions':[_version_response(r) for r in dw.derived_history(project['project_dir'],source,image_path,_derived_scope(project))]}
    except (ValueError,OSError) as exc:raise _error(exc) from exc


@router.get('/derived/{identifier}')
def read_derived(identifier:str,request:Request):
    project,source=_context(request)
    try:return _version_response(dw.read_derived(project['project_dir'],source,identifier,_derived_scope(project)))
    except (ValueError,OSError) as exc:raise _error(exc) from exc


@router.get('/derived/{identifier}/image')
def derived_image(identifier:str,request:Request):
    project,source=_context(request)
    try:row=dw.read_derived(project['project_dir'],source,identifier,_derived_scope(project));return FileResponse(row['file_path'],media_type='image/png')
    except (ValueError,OSError) as exc:raise _error(exc) from exc


def _evaluations(project,source):
    records=EvaluationHistory(Path(project['project_dir'])/'reports'/'evaluations').list(labelset_id=project.get('active_labelset_id','default'))
    return [row for row in records if row['binding'].get('task',row['result'].get('task'))==project['task'] and row['binding'].get('source_dataset_path',row['binding'].get('source'))==str(source)]


def _comparisons(project,source):
    from backend.api.routes_model_comparisons import _report_dir
    records=[]
    for path in _report_dir(project).glob('comparison_*.json'):
        if path.is_symlink() or not re.fullmatch(r'comparison_[0-9a-f]{32}',path.stem):continue
        row=json.loads(path.read_text())
        if row.get('comparison_id')!=path.stem:raise ValueError('Saved comparison identity changed')
        if row.get('project_id')==project['id'] and row.get('source_dataset_path')==str(source) and row.get('task')==project['task'] and row.get('labelset_id','default')==project.get('active_labelset_id','default'):
            records.append({**row,'evidence_sha256':hashlib.sha256(dw.canonical(row)).hexdigest()})
    return records


def _validate_origin(project,source,queue):
    origin=queue['origin'];kind='comparison_id' if origin.get('comparison_id') else 'evaluation_id'
    records=_comparisons(project,source) if kind=='comparison_id' else _evaluations(project,source)
    record=next((r for r in records if r[kind]==origin[kind]),None)
    if record is None or record['evidence_sha256']!=origin.get('evidence_sha256'):raise ValueError('Saved review origin changed; create a new queue')
    return queue


@router.get('/review-evaluations')
def review_evaluations(request:Request):
    project,source=_context(request)
    try:
        rows=[{'kind':'evaluation','id':r['evaluation_id'],'evaluation_id':r['evaluation_id'],'created_at':r['created_at'],'job_id':r['result'].get('job_id'),'sample_count':len(r['result'].get('test_predictions',[])),'evidence_sha256':r['evidence_sha256']} for r in _evaluations(project,source)]
        rows.extend({'kind':'comparison','id':r['comparison_id'],'comparison_id':r['comparison_id'],'created_at':datetime.fromisoformat(r['created_at'].replace('Z','+00:00')).timestamp() if isinstance(r['created_at'],str) else r['created_at'],'job_id':r.get('candidate_job_id'),'sample_count':len(r.get('images',[])),'evidence_sha256':r['evidence_sha256']} for r in _comparisons(project,source))
        return {'evaluations':sorted(rows,key=lambda r:r['created_at'],reverse=True)}
    except (ValueError,OSError) as exc:raise _error(exc) from exc


@router.post('/review-queues')
def create_queue(req:QueueRequest,request:Request):
    project,source=_context(request)
    try:
        key='comparison_id' if req.comparison_id else 'evaluation_id';identifier=req.comparison_id or req.evaluation_id
        record=next((r for r in (_comparisons(project,source) if req.comparison_id else _evaluations(project,source)) if r[key]==identifier),None)
        if record is None:raise ValueError('Saved evaluation or comparison is absent from the active source/task/labelset scope')
        origin={key:record[key],'evidence_sha256':record['evidence_sha256'],'job_id':record.get('candidate_job_id') if req.comparison_id else record['result'].get('job_id'),'step':4}
        predictions=record.get('images',[]) if req.comparison_id else record['result'].get('test_predictions',[])
        return dw.create_review_queue(project['project_dir'],source,project['task'],project.get('active_labelset_id','default'),predictions,origin,req.threshold,req.margin)
    except (ValueError,OSError) as exc:raise _error(exc) from exc


@router.get('/review-queues')
def queues(request:Request):
    project,source=_context(request)
    try:
        rows=dw.list_review_queues(project['project_dir'],source,project['task'],project.get('active_labelset_id','default'))
        for row in rows:
            try:_validate_origin(project,source,row)
            except ValueError as exc:row.update(stale=True,error=str(exc))
        return {'queues':rows}
    except (ValueError,OSError) as exc:raise _error(exc) from exc


@router.get('/review-queues/{identifier}')
def queue(identifier:str,request:Request):
    project,source=_context(request)
    try:return _validate_origin(project,source,dw.read_review_queue(project['project_dir'],source,project['task'],project.get('active_labelset_id','default'),identifier))
    except (ValueError,OSError) as exc:raise _error(exc) from exc


@router.post('/review-queues/{identifier}/advance')
def advance(identifier:str,req:AdvanceRequest,request:Request):
    project,source=_context(request)
    try:
        _validate_origin(project,source,dw.read_review_queue(project['project_dir'],source,project['task'],project.get('active_labelset_id','default'),identifier))
        return dw.advance_review_queue(project['project_dir'],source,project['task'],project.get('active_labelset_id','default'),identifier,req.expected_revision,req.relative_path,req.state,req.actor)
    except (ValueError,OSError) as exc:raise _error(exc) from exc
