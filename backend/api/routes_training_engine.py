"""External UI contract, independent of the desktop's active project state."""
from pathlib import Path
import json
import re
from typing import Any,Literal
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field
from backend.engine import training_engine as engine
from backend.engine.job_store import JobConflict

_IDEMPOTENCY_KEY=re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')

router=APIRouter(prefix='/api/engine',tags=['training-engine'])

class PrepareRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task:str
    source_dataset_path:str
    output_dir:str
    labels:Any=None
    labels_path:str|None=None
    prepare_options:dict=Field(default_factory=dict)

class TrainRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    output_dir:str
    prepared_id:str|None=None
    mode:Literal['quick','search','fast_retrain']='quick'
    preset:Literal['fast','precision']='fast'
    device:str='cpu'
    config:dict=Field(default_factory=dict)
    search_space:dict|None=None
    budget:dict|None=None
    epochs_per_trial:int=Field(default=1,ge=1,le=500)
    parent_job_id:str|None=None
    background:bool=True
    config_path:str|None=None

class RunRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    output_dir:str
    run_id:str

class EvaluateRequest(RunRequest):
    device:str='cpu'
    split:Literal['val','test']='test'

class PredictRequest(RunRequest):
    images:list[str]|None=None
    device:str='cpu'
    threshold:float=Field(default=.5,ge=0,le=1)

def _scope(request,output,source=None):
    if getattr(request.state,'account_user',None):
        project=getattr(request.state,'scoped_project',None)
        if not project:raise HTTPException(409,'Select an authorized project')
        if not Path(output).expanduser().resolve().is_relative_to(Path(project['project_dir']).resolve()):raise HTTPException(403,'Engine output must be inside the authorized project')
        canonical=source or _call(engine._prepared,output)['source_dataset_path']
        if not project.get('source_dataset_dir') or Path(canonical).resolve()!=Path(project['source_dataset_dir']).resolve():raise HTTPException(403,'Engine source must be the authorized project source')

def _call(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except JobConflict as exc:raise HTTPException(409,str(exc)) from exc
    except FileNotFoundError as exc:raise HTTPException(404,str(exc)) from exc
    except (ValueError,OSError,KeyError,TypeError,RuntimeError) as exc:raise HTTPException(422,str(exc)) from exc

def _scope_recipe(request,options):
    if not getattr(request.state,'account_user',None):return
    project=request.state.scoped_project;roots=[Path(project['project_dir']).resolve()]
    if project.get('source_dataset_dir'):roots.append(Path(project['source_dataset_dir']).resolve())
    def check(value,key=''):
        if isinstance(value,dict):
            for name,item in value.items():check(item,name)
        elif isinstance(value,list):
            for item in value:check(item,key.removesuffix('s'))
        elif isinstance(value,str) and value and (key.endswith(('_path','_dir')) or key=='pretrained_checkpoint'):
            if not any(Path(value).expanduser().resolve().is_relative_to(root) for root in roots):raise HTTPException(403,'Reusable configuration file selector is outside the authorized project')
    check(options)

@router.get('/capabilities')
def capabilities():return engine.capabilities()

@router.post('/prepare')
def prepare(req:PrepareRequest,request:Request):
    _scope(request,req.output_dir,req.source_dataset_path)
    return _call(engine.prepare,**req.model_dump())

@router.post('/train')
def train(req:TrainRequest,request:Request):
    _scope(request,req.output_dir)
    options=req.model_dump(exclude={'background','config_path'})
    if req.config_path:
        recipe=_call(lambda:engine.configuration_recipe(json.loads(Path(req.config_path).read_text(encoding='utf-8'))))
        options={**recipe,**req.model_dump(include=req.model_fields_set-{'background','config_path'})}
        if options.get('mode')!='search' and 'search_space' not in req.model_fields_set:options['search_space']=None
    _scope_recipe(request,options)
    # The job ledger reserves the key before the run folder is written; a repeat returns the reserved run.
    key=request.headers.get('Idempotency-Key')
    if key is not None and not _IDEMPOTENCY_KEY.fullmatch(key):raise HTTPException(422,'Idempotency-Key must be 1-128 letters, digits or the characters . _ : -')
    options['idempotency_key']=key
    if req.background:return _call(engine.start_run,**options)
    record=_call(engine.create_run,**options)
    if record.get('idempotent_replay'):return record
    return _call(engine.execute_run,req.output_dir,record['run_id'])

@router.get('/jobs')
def jobs(output_dir:str,request:Request):
    _scope(request,output_dir);return {'jobs':_call(engine.list_runs,output_dir)}

@router.get('/status')
def status(output_dir:str,run_id:str,request:Request):
    _scope(request,output_dir);return _call(engine.read_run,output_dir,run_id)

@router.post('/cancel')
def cancel(req:RunRequest,request:Request):
    _scope(request,req.output_dir);return _call(engine.cancel_run,req.output_dir,req.run_id)

@router.post('/evaluate')
def evaluate(req:EvaluateRequest,request:Request):
    _scope(request,req.output_dir);return _call(engine.evaluate,req.output_dir,req.run_id,device=req.device,split=req.split)

@router.post('/predict')
def predict(req:PredictRequest,request:Request):
    _scope(request,req.output_dir);return _call(engine.predict,req.output_dir,req.run_id,images=req.images,device=req.device,threshold=req.threshold)

@router.get('/artifacts')
def artifacts(output_dir:str,run_id:str,request:Request):
    _scope(request,output_dir);record=_call(engine.read_run,output_dir,run_id)
    return {'run_id':run_id,'status':record['status'],'artifacts':record['artifacts']}

@router.get('/artifacts/{name}')
def artifact_file(name:Literal['model','metadata','configuration','evaluation','predictions'],output_dir:str,run_id:str,request:Request):
    _scope(request,output_dir);record=_call(engine.read_run,output_dir,run_id)
    if name not in record['artifacts']:raise HTTPException(404,'Engine artifact has not been produced')
    artifact=record['artifacts'][name];path=Path(artifact['path'])
    return FileResponse(path,filename=path.name,media_type='application/json' if path.suffix=='.json' else 'application/octet-stream',headers={'X-Content-SHA256':artifact['sha256']})

@router.get('/image-artifacts/{index}')
def image_artifact(index:int,output_dir:str,run_id:str,request:Request):
    _scope(request,output_dir);record=_call(engine.read_run,output_dir,run_id)
    if 'predictions' not in record['artifacts']:raise HTTPException(404,'Predict before requesting an image artifact')
    outputs=json.loads(Path(record['artifacts']['predictions']['path']).read_text(encoding='utf-8')).get('output_files',[])
    if index<0 or index>=len(outputs):raise HTTPException(404,'Image artifact does not exist')
    artifact=outputs[index];path=Path(artifact['path'])
    return FileResponse(path,filename=path.name,headers={'X-Content-SHA256':artifact['sha256']})
