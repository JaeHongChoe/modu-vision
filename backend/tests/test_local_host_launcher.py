import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest


def test_helper_build_is_owned_and_returns_real_compiler_entry(tmp_path, monkeypatch):
    from backend import local_web_runtime as runtime
    root=tmp_path/'source'; root.mkdir()
    compiler=root/'node_modules/typescript/bin/tsc'; compiler.parent.mkdir(parents=True); compiler.write_text('compiler')
    electron=root/'node_modules/electron/cli.js'; electron.parent.mkdir(parents=True); electron.write_text('launcher')
    paths=SimpleNamespace(root=tmp_path/'web'); paths.root.mkdir()
    monkeypatch.setattr(runtime,'ROOT',root)
    commands=[]
    def build(argv):
        commands.append(argv)
        target=Path(argv[argv.index('--outDir')+1])/'main/localHost/entry.js'
        target.parent.mkdir(parents=True); target.write_text('compiled helper')
        return 0
    monkeypatch.setattr(runtime,'_run_renderer_builder',build)
    entry=runtime.build_local_helper(paths)
    assert entry == paths.root/'native-helper/main/localHost/entry.js'
    assert commands[0][commands[0].index('--outDir')+1] == str(paths.root/'native-helper')
    assert compiler.as_posix() in commands[0]


def test_helper_build_refuses_linked_output_before_compiler_runs(tmp_path,monkeypatch):
    from backend import local_web_runtime as runtime
    paths=SimpleNamespace(root=tmp_path/'web'); paths.root.mkdir()
    (paths.root/'native-helper').symlink_to(tmp_path,target_is_directory=True)
    def forbidden(*args): raise AssertionError('compiler must not run')
    monkeypatch.setattr(runtime,'_run_renderer_builder',forbidden)
    with pytest.raises(ValueError):runtime.build_local_helper(paths)


def test_helper_lifetime_closes_owned_child_on_server_failure(tmp_path,monkeypatch):
    from backend.local_host import launcher
    from fastapi import FastAPI
    from backend.engine.shared_accounts import AccountStore
    root=tmp_path/'web'; root.mkdir(); app=FastAPI();app.state.accounts=AccountStore(root/'auth/accounts.sqlite')
    app.state.context_registry=SimpleNamespace(workspace_id='test')
    paths=SimpleNamespace(root=root)
    class Client:
        ready={'protocol':1,'platform':'darwin'};closed=False
        def __init__(self,*args,**kwargs):self.closed=False;self.calls=[]
        async def start(self):return self.ready
        async def call(self,authority,method,args,**kwargs):self.calls.append(method);return True
        async def close(self):self.closed=True
    clients=[]
    def factory(*args,**kwargs):c=Client();clients.append(c);return c
    monkeypatch.setattr(launcher,'helper_command',lambda *a,**kw:['owned-helper'])
    monkeypatch.setattr(launcher,'install_local_projects',lambda *a:None)
    async def failing(*args,**kwargs):raise RuntimeError('server failed')
    with pytest.raises(RuntimeError,match='server failed'):
        asyncio.run(launcher.serve_with_local_host(app,root/'renderer','https://localhost:8443',None,
            root/'cert.pem',root/'key.pem',paths,root/'entry.js',no_open=True,
            client_factory=factory,serve=failing))
    assert clients[0].closed and clients[0].calls==['session.dispose']
    assert app.state.local_host is not None
