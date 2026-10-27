import asyncio
import hashlib
import pytest
from backend.tests.test_local_host_routes import setup
from fastapi import HTTPException


def test_logout_revokes_all_tabs_of_exact_session_and_native_pending_work(tmp_path):
    app, client, controller, helper, session, headers = setup(tmp_path)
    tabs=[client.post('/api/local-host/session',headers=headers).json()['tab_id'] for _ in range(2)]
    for tab in tabs:client.post('/api/local-host/approve',headers={**headers,'X-Vision-Local-Tab':tab},json={})
    app.state.accounts.logout(session['token'])
    asyncio.run(controller.prune_sessions())
    assert not controller.tabs
    assert helper.calls.count('session.disposeAll') == 1
    assert not controller.sessions


def test_disposing_tab_does_not_retire_other_tab_or_session(tmp_path):
    app, client, controller, helper, session, headers = setup(tmp_path)
    tabs=[client.post('/api/local-host/session',headers=headers).json()['tab_id'] for _ in range(2)]
    for tab in tabs:client.post('/api/local-host/approve',headers={**headers,'X-Vision-Local-Tab':tab},json={})
    response=client.post('/api/local-host/session/dispose',headers={**headers,'X-Vision-Local-Tab':tabs[0]},json={})
    assert response.status_code==200 and response.json()['disposed'] is True
    assert tabs[0] not in controller.tabs and tabs[1] in controller.tabs
    assert helper.calls.count('session.dispose')==1 and 'session.disposeAll' not in helper.calls


def test_attribution_is_single_use_and_never_a_write_authority(tmp_path):
    from starlette.requests import Request
    app, client, controller, helper, session, headers=setup(tmp_path)
    tab=client.post('/api/local-host/session',headers=headers).json()['tab_id']
    h={**headers,'X-Vision-Local-Tab':tab};client.post('/api/local-host/approve',headers=h,json={})
    scope={'type':'http','app':app,'method':'POST','scheme':'https','path':'/api/dataset/split',
           'headers':[(b'host',b'localhost:8443'),(b'origin',b'https://localhost:8443'),
           (b'cookie',('vision_session='+session['token']).encode()),(b'x-vision-csrf',session['csrf_token'].encode()),
           (b'x-vision-local-tab',tab.encode()),(b'x-modu-assistant-run',('run_'+'a'*24).encode())]}
    authority=controller.authorize(Request(scope),'assistant.beginRun')
    controller.arm_run(authority,'run_'+'a'*24,'save_split')
    controller.commit_grants(scope)
    with pytest.raises(HTTPException):controller.commit_grants(scope)
