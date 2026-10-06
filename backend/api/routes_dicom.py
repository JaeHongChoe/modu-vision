"""Project-scoped DICOM display preparation; original identity is retained."""
from pathlib import Path
import re
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from backend.api.routes_project import get_current_project
from backend.api.routes_label_suggestions import _image_path
from backend.engine.dicom_input import normalized_view,is_dicom

router=APIRouter(prefix='/api/dataset/dicom',tags=['dicom'])

class ViewRequest(BaseModel):
    image_path:str
    window_center:float|None=None
    window_width:float|None=Field(None,gt=0)
    frame_index:int|None=Field(None,ge=0,strict=True)

@router.post('/view')
def prepare_view(req:ViewRequest,request:Request):
    project=get_current_project(request);path=_image_path(project,req.image_path)
    if not is_dicom(path):raise HTTPException(422,detail='Select a DICOM source image')
    try:receipt=normalized_view(path,Path(project['project_dir'])/'dicom_views',**req.model_dump(exclude={'image_path'}))
    except (ValueError,OSError) as exc:raise HTTPException(422,detail=str(exc)) from exc
    return {**receipt,'display_url':f"/api/dataset/dicom/views/{receipt['view_id']}"}

@router.get('/views/{view_id}')
def display_view(view_id:str,request:Request):
    if not re.fullmatch('[0-9a-f]{64}',view_id):raise HTTPException(422,detail='Invalid DICOM view ID')
    project=get_current_project(request);path=Path(project['project_dir'])/'dicom_views'/f'{view_id}.png'
    if not path.is_file() or path.is_symlink():raise HTTPException(404,detail='DICOM display view not found')
    return FileResponse(path,media_type='image/png')
