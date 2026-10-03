"""Tests never write into the user's home data stores.

backend/conftest.py points every module-level store at one folder per test session. A test that saved a split for its
temporary dataset used to leave the file in the user's home split store, and a test that asked for a thumbnail left it
in the user's thumbnail cache; both stores now resolve inside the session folder, for backend/tests and for tests/e2e
(whose conftest imports backend/conftest.py).
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from backend.api import routes_dataset
from backend.engine import dataset_loaders

HOME_STORES = [Path.home() / '.modu_vision', Path.home() / '.modu-vision', Path.home() / '.vision_ai_studio_thumbnails']


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _session():
    # The folder conftest.py made for this run; the application data folder is its 'user_data' child.
    return Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']).parent


ROOT = Path(__file__).resolve().parents[2]


def test_every_store_the_dataset_routes_write_resolves_in_the_session_folder():
    session = _session()
    stores = [routes_dataset.SPLIT_MANIFEST_DIR, dataset_loaders.SPLIT_MANIFEST_DIR, routes_dataset.THUMBNAIL_CACHE_DIR,
              os.environ['VISION_AI_STUDIO_USER_DATA_DIR'], os.environ['MODU_FLOW_TEMPLATE_DIR']]
    for store in stores:
        assert _inside(store, session), store
        assert not any(_inside(store, home) for home in HOME_STORES), store


def test_a_split_saved_for_a_temporary_dataset_lands_in_the_session_folder(tmp_path):
    path = routes_dataset._split_manifest_file(tmp_path / 'source')
    assert _inside(path, _session()), path
    assert not any(_inside(path, home) for home in HOME_STORES)


def test_the_session_folder_is_exported_so_a_second_import_reuses_it():
    assert Path(os.environ['MODU_TEST_DATA_DIR']).resolve() == _session().resolve()


def test_importing_the_shared_conftest_alone_isolates_every_store(tmp_path):
    """tests/e2e gets its isolation by importing backend/conftest.py; a process whose shell names the user's real
    stores gets a fresh session folder outside the home stores for every one of them."""
    names = ['VISION_AI_STUDIO_USER_DATA_DIR', 'MODU_FLOW_TEMPLATE_DIR', 'MODU_SPLIT_MANIFEST_DIR', 'MODU_THUMBNAIL_CACHE_DIR']
    env = {key: value for key, value in os.environ.items() if key not in names + ['MODU_TEST_DATA_DIR']}
    # Shell values that name the user's real stores are replaced, never kept.
    env.update(VISION_AI_STUDIO_USER_DATA_DIR=str(Path.home() / '.modu-vision'), MODU_FLOW_TEMPLATE_DIR=str(Path.home() / '.modu_vision' / 'flow_templates'),
               MODU_SPLIT_MANIFEST_DIR=str(Path.home() / '.modu_vision' / 'splits'),
               MODU_THUMBNAIL_CACHE_DIR=str(Path.home() / '.vision_ai_studio_thumbnails'), PYTHONPATH=str(ROOT), TMPDIR=str(tmp_path))
    code = 'import json, os, backend.conftest; print(json.dumps({k: os.environ[k] for k in %r}))' % (names + ['MODU_TEST_DATA_DIR'],)
    shown = json.loads(subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True,
                                      check=True, timeout=120).stdout)
    session = Path(shown.pop('MODU_TEST_DATA_DIR'))
    assert _inside(session, tmp_path), session
    for name, value in shown.items():
        assert _inside(value, session), (name, value)
        assert not any(_inside(value, home) for home in HOME_STORES), (name, value)


def test_a_real_split_save_and_thumbnail_land_in_the_session_folder(tmp_path):
    import backend.main  # noqa: F401  (installed-httpx compatibility shim)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from PIL import Image
    folder = tmp_path / 'source'
    folder.mkdir()
    image = folder / 'ok.png'
    picture = Image.new('RGB', (40, 40), 'white')
    noise = uuid.uuid4().bytes  # 16 random pixels: a thumbnail no earlier run in a reused session folder has cached
    for index in range(16):
        picture.putpixel((index, 0), (noise[index], 255 - noise[index], index))
    picture.save(image)
    routes_dataset._write_split_manifest(folder, {'ok.png': 'test'}, seed=1)
    saved = routes_dataset._split_manifest_file(folder)
    assert saved.is_file() and _inside(saved, _session()), saved
    cache = Path(routes_dataset.THUMBNAIL_CACHE_DIR)
    before = set(cache.glob('*.jpg'))
    app = FastAPI()
    app.include_router(routes_dataset.router)
    with TestClient(app) as client:
        response = client.get('/api/dataset/thumbnail/image', params={'file_path': str(image), 'size': 32})
    assert response.status_code == 200, response.text
    created = set(cache.glob('*.jpg')) - before
    assert len(created) == 1 and _inside(next(iter(created)), _session()), created
