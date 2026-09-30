from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,Field

from backend.api.routes_project import get_current_project
from backend.engine.project_labelsets import load_labelsets
from backend.engine.project_preferences import read_preferences,update_preferences,PreferenceConflict

router=APIRouter(prefix='/api/project/preferences',tags=['project-preferences'])


class PreferenceUpdate(BaseModel):
    expected_revision:int=Field(ge=0)
    actor:str=Field(min_length=1,max_length=100)
    changes:dict[str,Any]


@router.get('')
def preferences(request:Request):
    return read_preferences(Path(get_current_project(request)['project_dir']))


@router.patch('')
def edit_preferences(body:PreferenceUpdate,request:Request):
    project=get_current_project(request)
    root=Path(project['project_dir'])
    known_sets={r['id'] for r in load_labelsets(root)['labelsets']}
    for key in ('model_flags','labelset_flags'):
        value=body.changes.get(key,{})
        if not isinstance(value,dict) or any(not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',identity) or identity in {'.','..'} for identity in value):
            raise HTTPException(422,detail='Flags require safe project identities')
    for identity in body.changes.get('labelset_flags',{}):
        if identity not in known_sets:
            raise HTTPException(404,detail='Label set does not exist in this project')
    for identity in body.changes.get('model_flags',{}):
        models=Path(project['models_dir'])
        candidates=[models/identity,*models.glob(f'*/{identity}')]
        if models.is_symlink() or not any(not model_dir.is_symlink() and model_dir.resolve().is_relative_to(models.resolve()) and (model_dir/'model_meta.json').is_file() and not (model_dir/'model_meta.json').is_symlink() for model_dir in candidates):
            raise HTTPException(404,detail='Model does not exist in this project')
    try:
        return update_preferences(root,expected_revision=body.expected_revision,actor=body.actor,changes=body.changes)
    except PreferenceConflict as exc:
        raise HTTPException(409,detail=str(exc)) from exc
    except (ValueError,OSError) as exc:
        raise HTTPException(422,detail=str(exc)) from exc
