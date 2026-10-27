"""Retire native work when authenticated account mutations revoke browser authority."""
import hashlib
from starlette.requests import Request


class LocalHostLifecycleMiddleware:
    def __init__(self,app,controller):self.app,self.controller=app,controller

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not scope.get('path','').startswith('/api/accounts/'):
            return await self.app(scope,receive,send)
        token=Request(scope).cookies.get('vision_session')
        async def checked_send(message):
            if message['type']=='http.response.start' and 200<=message['status']<300:
                if token and scope['path'] in {'/api/accounts/login','/api/accounts/logout'}:
                    await self.controller.revoke_session(hashlib.sha256(token.encode()).hexdigest())
                await self.controller.prune_sessions()
            await send(message)
        await self.app(scope,receive,checked_send)
