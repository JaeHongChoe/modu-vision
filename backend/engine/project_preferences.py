"""Revision-checked project tag palettes and user-defined model/labelset flags."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from backend.engine.dataset_metadata import _file_lock
from backend.engine.project_labelsets import _atomic_json


class PreferenceConflict(ValueError):
    pass


def read_preferences(project:Path)->dict:
    path=Path(project)/'preferences.json'
    if path.is_symlink():
        raise ValueError('Project preferences must not be a symbolic link')
    if not path.exists():
        return {'schema_version':1,'revision':0,'tag_colors':{},'model_flags':{},'labelset_flags':{},'audit':[]}
    data=json.loads(path.read_text())
    if data.get('schema_version')!=1 or type(data.get('revision')) is not int:
        raise ValueError('Invalid preference registry')
    return data


def update_preferences(project:Path,*,expected_revision:int,actor:str,changes:dict)->dict:
    project=Path(project).resolve()
    if not actor.strip() or len(actor)>100:
        raise ValueError('A reviewer name of 1–100 characters is required')
    allowed={'tag_colors','model_flags','labelset_flags'}
    if not changes or set(changes)-allowed:
        raise ValueError('Unknown preference fields')
    clean={}
    for key,values in changes.items():
        if not isinstance(values,dict) or len(values)>10000:
            raise ValueError('Preference changes must be a bounded object')
        normalized={}
        for name,value in values.items():
            if not isinstance(name,str) or not name.strip() or len(name)>100:
                raise ValueError('Invalid preference name')
            if key=='tag_colors':
                if value is not None and (not isinstance(value,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',value)):
                    raise ValueError('Tag colors require #RRGGBB')
            else:
                if not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or name in {'.','..'}:
                    raise ValueError('Invalid model or labelset identity')
                if not isinstance(value,list) or len(value)>20 or any(
                    not isinstance(flag,str) or not flag.strip() or len(flag)>40 for flag in value):
                    raise ValueError('Flags require at most twenty names of 1–40 characters')
                value=list(dict.fromkeys(flag.strip() for flag in value))
            normalized[name]=value
        clean[key]=normalized
    project.mkdir(parents=True,exist_ok=True)
    with _file_lock(project/'.preferences.lock'):
        data=read_preferences(project)
        if data['revision']!=expected_revision:
            raise PreferenceConflict('Preferences changed; refresh before saving')
        for key,values in clean.items():
            for name,value in values.items():
                if value is None:
                    data[key].pop(name,None)
                else:
                    data[key][name]=value
        data['revision']+=1
        from datetime import datetime,timezone
        data['audit'].append({'at':datetime.now(timezone.utc).isoformat(),'actor':actor.strip(),
                              'revision':data['revision'],'changes':clean})
        _atomic_json(project/'preferences.json',data)
        return copy.deepcopy(data)
