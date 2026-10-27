"""Bound native HTTP payloads before FastAPI deserializes nested RPC arguments."""
import asyncio
from fastapi import HTTPException
from starlette.responses import JSONResponse

from .client import MAX_FRAME


class _BodyRefused(HTTPException):
    pass


class LocalHostBodyLimitMiddleware:
    def __init__(self,app,*,limit=MAX_FRAME,read_timeout=30):
        self.app,self.limit,self.read_timeout=app,limit,read_timeout

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not scope.get('path','').startswith('/api/local-host/'):
            return await self.app(scope,receive,send)
        lengths=[value.decode('ascii','replace').strip() for name,value in scope.get('headers',[]) if name.lower()==b'content-length']
        started=False;size=0;complete=False;deadline=asyncio.get_running_loop().time()+self.read_timeout
        async def bounded_receive():
            nonlocal size,complete
            if complete:message=await receive()
            else:
                remaining=deadline-asyncio.get_running_loop().time()
                if remaining<=0:raise _BodyRefused(408,'Local operation request body timed out')
                try:message=await asyncio.wait_for(receive(),remaining)
                except TimeoutError:raise _BodyRefused(408,'Local operation request body timed out') from None
            if message['type']=='http.request':
                size+=len(message.get('body',b''))
                if size>self.limit:raise _BodyRefused(413,'Local operation payload exceeds the native transport limit')
                if not message.get('more_body',False):complete=True
            return message
        async def checked_send(message):
            nonlocal started
            if message['type']=='http.response.start':started=True
            await send(message)
        try:
            if any(not value.isascii() or not value.isdecimal() for value in lengths) or len(set(lengths))>1:
                raise _BodyRefused(400,'Invalid local operation Content-Length')
            if any(len(value)>20 or int(value)>self.limit for value in lengths):
                raise _BodyRefused(413,'Local operation payload exceeds the native transport limit')
            await self.app(scope,bounded_receive,checked_send)
        except _BodyRefused as error:
            if started:raise
            await JSONResponse({'detail':error.detail},status_code=error.status_code)(scope,receive,send)
