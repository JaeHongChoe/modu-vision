import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def application():
    from backend.local_host.body_limit import LocalHostBodyLimitMiddleware
    app=FastAPI();seen=[]
    @app.post('/api/local-host/assistant/send')
    @app.post('/api/ordinary')
    def read(body:dict):seen.append(body);return {'accepted':True}
    app.add_middleware(LocalHostBodyLimitMiddleware,limit=128)
    return TestClient(app),seen


def test_native_json_is_bounded_before_typed_handler_even_with_false_or_missing_length():
    client,seen=application()
    assert client.post('/api/local-host/assistant/send',json={'x':'a'*200}).status_code==413
    body=b'{"x":"'+b'a'*200+b'"}'
    for headers in ({'Content-Type':'application/json','Content-Length':'0'}, {'Content-Type':'application/json','Transfer-Encoding':'chunked'}):
        assert client.post('/api/local-host/assistant/send',content=iter([body[:60],body[60:]]),headers=headers).status_code==413
    assert seen==[]
    assert client.post('/api/local-host/assistant/send',json={'x':'small'}).status_code==200
    assert client.post('/api/ordinary',json={'x':'a'*200}).status_code==200


@pytest.mark.parametrize('length',['invalid','-1','9'*100])
def test_bad_or_unbounded_declared_length_is_refused_without_receiving(length):
    from backend.local_host.body_limit import LocalHostBodyLimitMiddleware
    async def exercise():
        messages=[]
        async def consumer(*args):raise AssertionError('downstream must not parse this body')
        async def receive():raise AssertionError('declared refusal must not read body')
        async def send(message):messages.append(message)
        await LocalHostBodyLimitMiddleware(consumer,limit=128)({'type':'http','path':'/api/local-host/session','headers':[(b'content-length',length.encode())]},receive,send)
        assert messages[0]['status'] in {400,413}
    asyncio.run(exercise())


def test_slow_native_body_has_one_bounded_deadline():
    from backend.local_host.body_limit import LocalHostBodyLimitMiddleware
    async def exercise():
        messages=[]
        async def consumer(scope,receive,send):await receive()
        async def receive():await asyncio.sleep(1);return {'type':'http.request','body':b'{}'}
        async def send(message):messages.append(message)
        await LocalHostBodyLimitMiddleware(consumer,limit=128,read_timeout=.01)({'type':'http','path':'/api/local-host/session','headers':[]},receive,send)
        assert messages[0]['status']==408
    asyncio.run(exercise())


def test_body_deadline_does_not_cut_off_long_operations_or_disconnect_observation():
    from backend.local_host.body_limit import LocalHostBodyLimitMiddleware
    async def exercise():
        messages=[];incoming=[{'type':'http.request','body':b'{}','more_body':False},{'type':'http.disconnect'}]
        async def receive():return incoming.pop(0)
        async def send(message):messages.append(message)
        async def consumer(scope,receive,send):
            assert (await receive())['body']==b'{}'
            await asyncio.sleep(.02)
            assert (await receive())['type']=='http.disconnect'
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        await LocalHostBodyLimitMiddleware(consumer,limit=128,read_timeout=.01)({'type':'http','path':'/api/local-host/linked-sources/materialize','headers':[]},receive,send)
        assert messages[0]['status']==200
    asyncio.run(exercise())
