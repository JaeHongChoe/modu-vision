"""The localhost launcher owns the native helper, without an application window."""
import asyncio
import os
from pathlib import Path
import shutil
import uuid

from backend.local_web_runtime import ROOT, run_https
from backend.local_web_state import _directory, _regular_fd
from .client import LocalHostClient
from .controller import LocalHostController
from .projects import LocalProjectRegistry, install_local_projects
from .routes import local_host_router
from .lifecycle import LocalHostLifecycleMiddleware
from .remote import install_remote
from .linked_sources import install_linked_sources
from .body_limit import LocalHostBodyLimitMiddleware


def helper_command(entry, state, origin, protected_paths=()):
    node = shutil.which('node')
    electron = ROOT / 'node_modules/electron/cli.js'
    if node is None or not electron.is_file(): raise RuntimeError('Install the documented native host prerequisites first')
    result = [node, str(electron), str(entry), '--state-dir', str(state), '--origin', origin]
    for path in protected_paths: result.extend(['--protected-path', str(Path(path).absolute())])
    return result


async def serve_with_local_host(api, renderer, origin, sock, cert, key, paths, helper_entry, *,
                               no_open=False, client_factory=LocalHostClient, serve=run_https):
    state = paths.root / 'native-host'
    _directory(state)
    environment = {k:v for k,v in os.environ.items() if not k.startswith('VISION_APPLICATION_') and k not in {
        'ELECTRON_RUN_AS_NODE','OPENAI_API_KEY','HF_TOKEN','HUGGING_FACE_HUB_TOKEN'}}
    descriptor = _regular_fd(state/'helper.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND)
    log = os.fdopen(descriptor,'ab',buffering=0)
    client = client_factory(helper_command(helper_entry,state,origin,[key]),cwd=ROOT,env=environment,stderr=log)
    controller = None
    try:
        await client.start()
        # No actor vault, provider or OS dialog is opened by this protocol
        # readiness probe. The one-use synthetic scope is immediately retired.
        probe = {'actor_id':uuid.uuid4().hex,'session_id':uuid.uuid4().hex*2,'tab_id':uuid.uuid4().hex}
        if await client.call(probe,'session.dispose',{},timeout=5) is not True:
            raise RuntimeError('Local helper readiness round trip failed')
        controller = LocalHostController(api,client,origin=origin,state_dir=state,protected_paths=[key])
        api.state.local_host = controller
        api.include_router(local_host_router(controller))
        api.include_router(install_remote(controller))
        api.include_router(install_linked_sources(controller))
        install_local_projects(api,LocalProjectRegistry(paths.root/'local-projects'))
        api.add_middleware(LocalHostLifecycleMiddleware,controller=controller)
        api.add_middleware(LocalHostBodyLimitMiddleware)
        controller.start_supervision()
        await serve(api,renderer,origin,sock,cert,key,paths,no_open=no_open)
    finally:
        if controller:await controller.close()
        await client.close()
        log.close()
