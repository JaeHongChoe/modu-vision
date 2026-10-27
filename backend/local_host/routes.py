"""Explicit, typed routes. Browser strings never select arbitrary helper methods."""
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field


ASSISTANT = {
    'getState':(), 'saveProvider':('input',), 'removeKey':('provider',), 'removeProvider':('provider',),
    'setActive':('provider',), 'listModels':('provider',), 'send':('input',), 'cancel':('requestId',),
    'imageIntent':('input',), 'beginRun':('runId','tool'), 'endRun':('runId',),
    'chatgpt.begin':('input',), 'chatgpt.acknowledgePlanUsage':('recordId',),
    'chatgpt.resumeUsage':('recordId',), 'chatgpt.recover':('input',), 'chatgpt.cancel':(),
    'chatgpt.selectAccount':('recordId',), 'chatgpt.selectModel':('recordId','model'), 'chatgpt.signOut':('recordId',),
}
UPDATES = {'status':(), 'configure':('configuration',), 'check':(), 'download':(), 'verifyOffline':(),
    'selectPortableHome':(), 'inspectPortable':(), 'previewPortable':('channel','canary'),
    'applyPortable':('id',), 'recoverPortable':('action','expected'), 'launchPortable':('expected',),
    'inspectPortableLaunch':('expected',)}


class Picker(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    purpose:str=Field(min_length=1,max_length=80)
    title:str|None=Field(default=None,max_length=200)
    defaultPath:str|None=Field(default=None,max_length=4096)
    filters:list[dict[str,Any]]|None=Field(default=None,max_length=20)


class OpenPath(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    path:str=Field(min_length=1,max_length=4096)


class Arguments(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    args:list[Any]=Field(default_factory=list,max_length=3)


def local_host_router(controller):
    router=APIRouter(prefix='/api/local-host',tags=['local-host'])

    @router.get('/capabilities')
    def capabilities(request:Request): return controller.capabilities(request)

    @router.get('/status')
    def status(request:Request): return controller.process_status(request)

    @router.post('/session')
    def session(request:Request): return controller.create_tab(request)

    @router.post('/approve')
    async def approve(request:Request): return await controller.approve(request)

    @router.post('/session/dispose')
    async def dispose(request:Request): return await controller.dispose_tab(request)

    @router.post('/dialog/folder')
    async def folder(body:Picker,request:Request): return await controller.pick(request,'folder',body.model_dump(exclude_none=True))

    @router.post('/dialog/file')
    async def file(body:Picker,request:Request): return await controller.pick(request,'file',body.model_dump(exclude_none=True))

    @router.post('/open')
    async def open_path(body:OpenPath,request:Request): return await controller.open(request,body.path)

    @router.post('/assistant/{action}')
    async def assistant(action:str,body:Arguments,request:Request):
        names=ASSISTANT.get(action)
        if names is None: raise HTTPException(404,'Unknown assistant action')
        if len(names)!=len(body.args): raise HTTPException(422,'Invalid assistant arguments')
        authority,result=await controller.invoke(request,'assistant.'+action,'assistant.'+action,
            dict(zip(names,body.args)),project=action in {'send','imageIntent','beginRun','endRun'})
        if action=='beginRun' and result is True:controller.arm_run(authority,*body.args)
        if action=='endRun':controller.runs.pop(body.args[0],None)
        return result

    @router.post('/updates/{action}')
    async def updates(action:str,body:Arguments,request:Request):
        names=UPDATES.get(action)
        if names is None: raise HTTPException(404,'Unknown update action')
        if len(names)!=len(body.args): raise HTTPException(422,'Invalid update arguments')
        _,result=await controller.invoke(request,'updates.'+action,'updates.'+action,dict(zip(names,body.args)))
        return result

    return router
