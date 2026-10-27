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
