"""Unknown lock providers cannot grant a real thumbnail lifetime.

These explicit negative controls change only the provider being challenged.
The HTTP handler, thumbnail decoder and ownership checker stay original.
"""
import asyncio
from pathlib import Path
import threading

import httpx
from PIL import Image
import pytest


@pytest.mark.parametrize('replacement_time', ('before-bind', 'after-bind'))
def test_unknown_thumbnail_memo_lock_refuses_ownership(replacement_time, tmp_path, monkeypatch):
    from backend.api import routes_dataset
    from backend.tests.test_original_owned_flow_lifetime import _stack, _login_current, _no_whole_credit
    from backend.tests.test_original_owned_account_request_lifetime import _refused

    class DelegatedLock:
        """An unknown provider, even with a real lock delegated underneath."""
        def __init__(self):
            self.lock = threading.Lock()
        def __enter__(self):
            self.lock.acquire()
            return self
        def __exit__(self, *error):
            self.lock.release()
        def locked(self):
            return self.lock.locked()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'runtime'))
    monkeypatch.setenv('MODU_FLOW_TEMPLATE_DIR', str(tmp_path/'templates'))
    cache = tmp_path/'thumbnails'
    cache.mkdir()
    monkeypatch.setattr(routes_dataset, 'THUMBNAIL_CACHE_DIR', cache)
    if replacement_time == 'before-bind':
        monkeypatch.setattr(routes_dataset, '_DIGEST_LOCK', DelegatedLock())
    app, admission, observer, _, project, _, _, _, _ = _stack(tmp_path, monkeypatch)
    image = Path(project['source_dataset_dir'])/'original.png'
    with Image.new('RGB', (16, 16), (30, 70, 90)) as source:
        source.save(image)
    image_before = image.read_bytes()

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            if replacement_time == 'after-bind':
                # Even another builtin lock cannot replace the captured one.
                monkeypatch.setattr(routes_dataset, '_DIGEST_LOCK', threading.Lock())
            response = await client.get('/api/dataset/thumbnail/'+image.name,
                headers=headers, params={'file_path':str(image), 'size':128})
            assert response.status_code == 200
            assert response.headers['content-type'] == 'image/jpeg'
            assert int(response.headers['content-length']) == len(response.content) > 0

    asyncio.run(request())
    assert image.read_bytes() == image_before
    _refused(admission)
    assert observer.snapshot()['uncertain']
    _no_whole_credit(observer)
