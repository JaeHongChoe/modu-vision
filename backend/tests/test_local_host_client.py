"""Private child framing and ownership without launching Electron in unit tests."""
import asyncio
import json
import sys
import pytest


def test_private_client_correlates_concurrent_frames_and_reaps_its_child(tmp_path):
    from backend.local_host.client import LocalHostClient
    child = tmp_path / 'child.py'
    child.write_text('''import sys,json
print(json.dumps({'event':'ready','protocol':1,'platform':'darwin'}),flush=True)
for line in sys.stdin:
 x=json.loads(line)
 print(json.dumps({'id':x['id'],'ok':True,'result':x['args']}),flush=True)
''')
    async def exercise():
        client = LocalHostClient([sys.executable, '-u', str(child)], cwd=tmp_path)
        await client.start()
        assert client.ready['protocol'] == 1
        scope = {'actor_id':'a','session_id':'b','tab_id':'c'}
        assert await asyncio.gather(client.call(scope, 'dialog.file', {'value':1}),
                                    client.call(scope, 'dialog.file', {'value':2})) == [{'value':1},{'value':2}]
        await client.close()
        assert client.process.returncode is not None
        with pytest.raises(RuntimeError): await client.call(scope, 'dialog.file', {})
    asyncio.run(exercise())


def test_eof_and_timeout_fail_pending_calls_without_repeating_a_write(tmp_path):
    from backend.local_host.client import LocalHostClient
    child = tmp_path / 'child.py'
    child.write_text('''import sys,json,time
print(json.dumps({'event':'ready','protocol':1,'platform':'darwin'}),flush=True)
for line in sys.stdin: time.sleep(10)
''')
    async def exercise():
        client = LocalHostClient([sys.executable, '-u', str(child)], cwd=tmp_path)
        await client.start()
        with pytest.raises(TimeoutError): await client.call({'actor_id':'a','session_id':'b','tab_id':'c'}, 'updates.applyPortable', {}, timeout=.02)
        await client.close()
    asyncio.run(exercise())


def test_non_protocol_stdout_is_refused_before_ready(tmp_path):
    from backend.local_host.client import LocalHostClient
    child = tmp_path / 'child.py'; child.write_text("print('not an RPC frame',flush=True)")
    async def exercise():
        client = LocalHostClient([sys.executable, '-u', str(child)], cwd=tmp_path)
        with pytest.raises(RuntimeError): await client.start(timeout=2)
        await client.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('after_ready', ["sys.exit(0)", "print('not a frame',flush=True);time.sleep(10)"])
def test_lost_or_malformed_helper_revokes_readiness_and_fails_closed(tmp_path, after_ready):
    from backend.local_host.client import LocalHostClient
    child=tmp_path/'child.py'
    child.write_text("import sys,json,time\nprint(json.dumps({'event':'ready','protocol':1,'platform':'darwin'}),flush=True)\ntime.sleep(.05)\n"+after_ready)
    async def exercise():
        client=LocalHostClient([sys.executable,'-u',str(child)],cwd=tmp_path)
        try:
            await client.start()
            for _ in range(100):
                if client.reader.done():break
                await asyncio.sleep(.01)
            assert client.reader.done()
            assert client.ready is None, 'dead or malformed helper cannot advertise native capabilities'
            with pytest.raises(RuntimeError,match='unavailable'):
                await client.call({'actor_id':'a','session_id':'b','tab_id':'c'},'dialog.file',{})
        finally:await client.close()
        assert client.process.returncode is not None
    asyncio.run(exercise())


class _DialogDeadlineWriter:
    def __init__(self): self.frames = []
    def write(self, data): self.frames.append(json.loads(data))
    async def drain(self): pass


def test_dialog_deadline_private_timeout_discards_old_uuid_during_a_new_wait(tmp_path):
    from types import SimpleNamespace
    from backend.local_host.client import LocalHostClient
    import inspect
    assert inspect.signature(LocalHostClient.call).parameters['timeout'].default == 180
    async def exercise():
        client = LocalHostClient([], cwd=tmp_path)
        stream, writer = asyncio.StreamReader(), _DialogDeadlineWriter()
        client.process = SimpleNamespace(returncode=None, stdin=writer, stdout=stream)
        client.ready = {'protocol':1}
        client.ready_future = asyncio.get_running_loop().create_future(); client.ready_future.set_result(client.ready)
        client.reader = asyncio.create_task(client._read())
        scope = {'actor_id':'fixture-actor','session_id':'fixture-session','tab_id':'fixture-tab'}
        try:
            with pytest.raises(TimeoutError): await client.call(scope, 'dialog.folder', {}, timeout=.01)
            assert client.pending == {} and len(writer.frames) == 1
            old = writer.frames[0]['id']
            fresh = asyncio.create_task(client.call(scope, 'dialog.folder', {}, timeout=1))
            await asyncio.sleep(0)
            assert len(writer.frames) == 2
            current = writer.frames[1]['id']; assert old != current
            for identifier, value in ((old,'expired-folder'), (current,'fresh-folder')):
                stream.feed_data((json.dumps({'id':identifier,'ok':True,'result':value})+'\n').encode())
            assert await fresh == 'fresh-folder'
            assert client.pending == {} and len(writer.frames) == 2 and client.ready is not None
        finally:
            client.reader.cancel(); await asyncio.gather(client.reader, return_exceptions=True)
    asyncio.run(exercise())


def test_dialog_deadline_private_busy_code_is_typed_and_independent_of_message(tmp_path):
    from types import SimpleNamespace
    from backend.local_host.client import LocalHostClient
    async def exercise():
        client = LocalHostClient([], cwd=tmp_path)
        stream, writer = asyncio.StreamReader(), _DialogDeadlineWriter()
        client.process = SimpleNamespace(returncode=None, stdin=writer, stdout=stream)
        client.ready = {'protocol':1}
        client.ready_future = asyncio.get_running_loop().create_future(); client.ready_future.set_result(client.ready)
        client.reader = asyncio.create_task(client._read())
        try:
            pending = asyncio.create_task(client.call({'actor_id':'a','session_id':'s','tab_id':'t'}, 'dialog.folder', {}, timeout=1))
            await asyncio.sleep(0)
            stream.feed_data((json.dumps({'id':writer.frames[0]['id'],'ok':False,'error':{'code':'native_dialog_busy','message':'opaque generated message'}})+'\n').encode())
            with pytest.raises(RuntimeError) as caught: await pending
            assert getattr(caught.value,'code',None) == 'native_dialog_busy'
            assert str(caught.value) == 'opaque generated message' and client.pending == {}
        finally:
            client.reader.cancel(); await asyncio.gather(client.reader, return_exceptions=True)
    asyncio.run(exercise())
