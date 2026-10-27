"""TEST ONLY composition: original app/controller/client/service, fake provider/native I/O."""
import argparse
import os
from pathlib import Path
import sys
from contextlib import asynccontextmanager

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--fixture-origin', required=True)
parser.add_argument('--fixture-node', required=True)
parser.add_argument('--fixture-root', required=True)
options, original_args = parser.parse_known_args()
sys.argv = [sys.argv[0], *original_args]
from backend import main
from backend.local_host.client import LocalHostClient
from backend.local_host.controller import LocalHostController
from backend.local_host.routes import local_host_router
from backend.local_host.remote import install_remote
from backend.local_host.projects import LocalProjectRegistry, install_local_projects
from backend.local_host.lifecycle import LocalHostLifecycleMiddleware
from backend.local_host.body_limit import LocalHostBodyLimitMiddleware

original_factory = main.create_app

def fixture_factory(*args, **kwargs):
    app = original_factory(*args, **kwargs)
    root = Path(options.fixture_root)
    state = root / 'controlled-native-host'
    state.mkdir(mode=0o700)
    helper = Path(__file__).with_name('localAssistantHelper.cjs')
    environment = {key: value for key, value in os.environ.items() if not any(word in key.upper() for word in ('TOKEN', 'PASSWORD', 'SECRET', 'API_KEY')) and key not in {'NODE_OPTIONS', 'ELECTRON_RUN_AS_NODE'}}
    client = LocalHostClient([options.fixture_node, str(helper), str(state), options.fixture_origin, str(root/'controlled-helper.jsonl')], cwd=Path(__file__).resolve().parents[3], env=environment)
    controller = LocalHostController(app, client, origin=options.fixture_origin, state_dir=state)
    app.state.local_host = controller
    app.include_router(local_host_router(controller))
    app.include_router(install_remote(controller))
    install_local_projects(app, LocalProjectRegistry(root/'controlled-local-projects'))
    app.add_middleware(LocalHostLifecycleMiddleware, controller=controller)
    app.add_middleware(LocalHostBodyLimitMiddleware)
    original_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifetime(application):
        try:
            await client.start()
            controller.start_supervision()
            async with original_lifespan(application):
                yield
        finally:
            await controller.close()
            await client.close()
    app.router.lifespan_context = lifetime
    return app

main.create_app = fixture_factory
main.run_server()
