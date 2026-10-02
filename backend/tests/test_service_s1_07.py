"""Production permission, session and migration regressions using isolated fixtures."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import threading
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app

PASSWORD='fixture password 123'

@pytest.fixture
def team(tmp_path):
    app=create_app(project_dir=str(tmp_path/'projects'),shared_auth_dir=str(tmp_path/'accounts'))
    from backend.contracts.authentication import configure_browser_origins
    configure_browser_origins(app,['https://fixture.test'])
    bootstrap=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    admin=bootstrap.post('/api/accounts/bootstrap',json={'username':'administrator','password':PASSWORD}).json()
    def login(username):
        client=TestClient(app,base_url='https://fixture.test')
        result=client.post('/api/accounts/login',json={'username':username,'password':PASSWORD})
        assert result.status_code==200,result.text
        client.headers['Authorization']='Bearer '+result.json()['token']
        return client
    admin_client=login('administrator')
    project=admin_client.post('/api/project/create',json={'name':'Authority fixture'}).json()
    users={name:app.state.accounts.create_user(name,PASSWORD,administrator=name=='serveradmin')
           for name in ('projectowner','vieweruser','targetuser','serveradmin')}
    for name,role in [('projectowner','owner'),('vieweruser','viewer')]:
        app.state.accounts.set_membership(project['id'],users[name]['id'],role,admin['id'])
    clients={name:login(name) for name in users}
    for name in ('projectowner','vieweruser'):
        response=clients[name].post('/api/accounts/select-project',json={'project_id':project['id']})
        assert response.status_code==200,response.text
    return app,admin,admin_client,project,users,clients


def test_server_administration_does_not_manufacture_project_membership(team):
    _,_,_,project,_,clients=team
    response=clients['serveradmin'].get('/api/project/current',headers={'X-Vision-Project':project['id']})
    assert response.status_code==403,response.text


def test_owner_demotion_before_membership_commit_rejects_stale_grant(team,monkeypatch):
    app,admin,_,project,users,clients=team
    accounts=app.state.accounts
    checked=threading.Event();resume=threading.Event()
    original=accounts.project_role
    def pause_after_owner_check(user,project_id):
        result=original(user,project_id)
        if user==users['projectowner']['id'] and result=='owner':
            checked.set();assert resume.wait(5)
        return result
    monkeypatch.setattr(accounts,'project_role',pause_after_owner_check)
    path=f"/api/accounts/projects/{project['id']}/members"
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending=executor.submit(clients['projectowner'].put,path,json={'user_id':users['targetuser']['id'],'role':'owner'})
        assert checked.wait(5)
        accounts.set_membership(project['id'],users['projectowner']['id'],'viewer',admin['id'])
        resume.set();response=pending.result(timeout=10)
    assert response.status_code==403,response.text
    assert original(users['targetuser']['id'],project['id']) is None


def test_project_owner_can_revoke_membership_and_old_captured_context(team):
    _,_,_,project,users,clients=team
    context=clients['vieweruser'].get('/api/context').json()['project_context']
    response=clients['projectowner'].delete(f"/api/accounts/projects/{project['id']}/members/{users['vieweruser']['id']}")
    assert response.status_code==200,response.text
    assert clients['vieweruser'].get('/api/project/current',headers={'X-Vision-Context':json.dumps(context)}).status_code==403


def test_administrator_disables_account_and_revokes_existing_session(team):
    _,_,admin_client,_,users,clients=team
    response=admin_client.patch('/api/accounts/users/'+users['vieweruser']['id'],json={'disabled':True})
    assert response.status_code==200,response.text
    assert clients['vieweruser'].get('/api/accounts/me').status_code==401
    assert admin_client.get('/api/accounts/users').status_code==200


def test_browser_session_login_uses_protected_cookie_and_csrf(team):
    app,_,_,_,_,_=team
    browser=TestClient(app,base_url='https://fixture.test')
    # Explicit transport is additive: existing bearer clients remain unchanged.
    response=browser.post('/api/accounts/login',json={'username':'vieweruser','password':PASSWORD,'transport':'cookie'},
                          headers={'Origin':'https://fixture.test'})
    assert response.status_code==200,response.text
    cookie=response.headers.get('set-cookie','').lower()
    assert 'httponly' in cookie and 'secure' in cookie and 'samesite=strict' in cookie
    assert 'token' not in response.json()
    assert browser.get('/api/accounts/me').status_code==200
    assert browser.post('/api/accounts/logout',headers={'Origin':'https://fixture.test'}).status_code==403


def test_denied_membership_change_has_scoped_audit_receipt(team):
    _,_,_,project,users,clients=team
    denied=clients['vieweruser'].put(f"/api/accounts/projects/{project['id']}/members",json={'user_id':users['targetuser']['id'],'role':'owner'})
    assert denied.status_code==403,denied.text
    audit=clients['projectowner'].get(f"/api/accounts/projects/{project['id']}/audit")
    assert audit.status_code==200,audit.text
    assert any(row['actor_id']==users['vieweruser']['id'] and row['project_id']==project['id']
               and row['decision']=='denied' and row['action']=='membership.change' for row in audit.json()['events'])


def test_last_owner_and_last_administrator_cannot_be_disabled_or_removed(team):
    app,admin,admin_client,project,users,clients=team
    owner=clients['projectowner']
    assert owner.delete(f"/api/accounts/projects/{project['id']}/members/{admin['id']}").status_code==200
    assert owner.delete(f"/api/accounts/projects/{project['id']}/members/{users['projectowner']['id']}").status_code==403
    assert admin_client.patch('/api/accounts/users/'+users['projectowner']['id'],json={'disabled':True}).status_code==403
    assert admin_client.patch('/api/accounts/users/'+users['serveradmin']['id'],json={'disabled':True}).status_code==200
    assert admin_client.patch('/api/accounts/users/'+admin['id'],json={'disabled':True}).status_code==403
    assert app.state.accounts.project_role(users['projectowner']['id'],project['id'])=='owner'


def test_project_owner_cannot_administer_accounts_and_disable_does_not_revive_sessions(team):
    _,_,admin_client,_,users,clients=team
    viewer_id=users['vieweruser']['id']
    assert clients['projectowner'].patch('/api/accounts/users/'+viewer_id,json={'disabled':True}).status_code==403
    assert clients['projectowner'].delete('/api/accounts/users/'+viewer_id+'/sessions').status_code==403
    assert admin_client.patch('/api/accounts/users/'+viewer_id,json={'disabled':True}).status_code==200
    assert admin_client.patch('/api/accounts/users/'+viewer_id,json={'disabled':False}).status_code==200
    assert clients['vieweruser'].get('/api/accounts/me').status_code==401


def test_explicit_administrator_enrollment_and_audit_are_project_scoped(team):
    app,_,admin_client,project,_,clients=team
    admin=clients['serveradmin']
    path=f"/api/accounts/projects/{project['id']}/enroll-administrator"
    assert clients['projectowner'].post(path,json={'reason':'Emergency ownership recovery'}).status_code==403
    assert admin.post(path,json={'reason':'Explicit audited membership enrollment'}).status_code==200
    events=admin.get(f"/api/accounts/projects/{project['id']}/audit").json()['events']
    assert any(row['action']=='membership.enroll' and row['decision']=='allowed' for row in events)
    other=admin_client.post('/api/project/create',json={'name':'Other audit scope'}).json()
    assert admin.get(f"/api/accounts/projects/{other['id']}/audit").status_code==403
    assert all(row['project_id']==project['id'] for row in events)
    assert 'password' not in json.dumps(events) and 'token' not in json.dumps(events)
    assert app.state.accounts.project_role(admin.get('/api/accounts/me').json()['user']['id'],other['id']) is None


def test_permission_decisions_recheck_session_membership_and_real_resource_revision(team):
    app,admin,_,project,users,clients=team
    accounts=app.state.accounts
    actor=users['projectowner']['id']
    decision=accounts.authorize(actor,'membership.change',project['id'],7)
    assert not decision.allowed and not decision.resource_revision_verified
    actual={'revision':7}
    decision=accounts.authorize(actor,'membership.change',project['id'],7,revision_lookup=lambda:actual['revision'])
    assert decision.allowed and decision.resource_revision_verified
    actual['revision']=8
    assert not accounts.authorize(actor,'membership.change',project['id'],7,revision_lookup=lambda:actual['revision']).allowed
    assert not accounts.authorize(actor,'invented.privilege',project['id']).allowed
    token=clients['projectowner'].headers['authorization'].removeprefix('Bearer ')
    accounts.logout(token)
    assert not accounts.authorize(actor,'membership.change',project['id'],session_token=token).allowed
    accounts.set_membership(project['id'],actor,'viewer',admin['id'])
    later=accounts.authorize(actor,'membership.change',project['id'])
    assert not later.allowed and later.membership_revision>decision.membership_revision


def test_browser_csrf_origin_cookie_logout_and_no_query_token_fallback(team):
    app,_,_,project,_,_=team
    client=TestClient(app,base_url='https://fixture.test')
    response=client.post('/api/accounts/login',json={'username':'vieweruser','password':PASSWORD,'transport':'cookie'},headers={'Origin':'https://fixture.test'})
    csrf=response.json()['csrf_token']
    body={'project_id':project['id']}
    assert client.post('/api/accounts/select-project',json=body,headers={'Origin':'https://attacker.test','X-Vision-CSRF':csrf}).status_code==403
    assert client.post('/api/accounts/select-project',json=body,headers={'Origin':'https://fixture.test','X-Vision-CSRF':'wrong'}).status_code==403
    assert client.post('/api/accounts/select-project',json=body,headers={'Origin':'https://fixture.test','X-Vision-CSRF':csrf}).status_code==200
    assert client.post('/api/accounts/logout',headers={'Origin':'https://fixture.test','X-Vision-CSRF':csrf}).status_code==200
    assert client.get('/api/accounts/me').status_code==401
    bearer=TestClient(app)
    token=bearer.post('/api/accounts/login',json={'username':'vieweruser','password':PASSWORD}).json()['token']
    assert bearer.get('/api/accounts/me?token='+token).status_code==401
    assert TestClient(app,base_url='http://fixture.test').post('/api/accounts/login',json={'username':'vieweruser','password':PASSWORD,'transport':'cookie'},headers={'Origin':'https://fixture.test'}).status_code==403


def test_oidc_adapter_only_accepts_one_time_verified_enrolled_identity(team):
    import time
    from urllib.parse import urlencode
    from backend.contracts.authentication import configure_oidc,OidcRegistration,VerifiedIdentity
    app,_,admin_client,project,users,_=team
    class Provider:
        def authorization_url(self,**kwargs):
            self.start=kwargs
            return 'https://identity.test/authorize?'+urlencode(kwargs)
        def exchange(self,**kwargs):
            assert kwargs['code_verifier'] and kwargs['nonce']==self.start['nonce']
            return VerifiedIdentity('https://identity.test','subject-123','studio-client',time.time()+60,kwargs['nonce'])
    provider=Provider()
    configure_oidc(app,{'fixture':OidcRegistration('https://identity.test','studio-client','https://fixture.test/callback',provider)})
    identity={'provider':'fixture','subject':'subject-123','user_id':users['vieweruser']['id']}
    assert admin_client.post('/api/accounts/oidc/identities',json=identity).status_code==200
    browser=TestClient(app,base_url='https://fixture.test',headers={'Origin':'https://fixture.test'})
    started=browser.post('/api/accounts/oidc/start',json={'provider':'fixture'})
    assert started.status_code==200,started.text
    assert provider.start['code_challenge'] and provider.start['redirect_uri']=='https://fixture.test/callback'
    body={'state':started.json()['state'],'code':'server-verified-code'}
    response=browser.post('/api/accounts/oidc/callback',json=body)
    assert response.status_code==200,response.text
    assert 'token' not in response.json() and response.json()['user']['id']==users['vieweruser']['id']
    assert browser.get('/api/accounts/me').json()['selected_project_id']==project['id']
    assert browser.post('/api/accounts/oidc/callback',json=body).status_code==401
    assert browser.post('/api/accounts/oidc/callback',json={**body,'issuer':'https://forged.test'}).status_code==422


def test_oidc_unenrolled_or_invalid_identity_never_creates_account_or_membership(team):
    import time
    from backend.contracts.authentication import configure_oidc,OidcRegistration,VerifiedIdentity
    app,_,_,_,_,_=team
    class Provider:
        subject='unenrolled';issuer='https://identity.test'
        def authorization_url(self,**kwargs):return 'https://identity.test/authorize'
        def exchange(self,**kwargs):return VerifiedIdentity(self.issuer,self.subject,'studio-client',time.time()+60,kwargs['nonce'])
    provider=Provider()
    configure_oidc(app,{'fixture':OidcRegistration('https://identity.test','studio-client','https://fixture.test/callback',provider)})
    client=TestClient(app,base_url='https://fixture.test',headers={'Origin':'https://fixture.test'})
    before=app.state.accounts.users()
    for issuer in ('https://identity.test','https://forged.test'):
        provider.issuer=issuer
        state=client.post('/api/accounts/oidc/start',json={'provider':'fixture'}).json()['state']
        assert client.post('/api/accounts/oidc/callback',json={'state':state,'code':'fixture-code'}).status_code==401
    assert app.state.accounts.users()==before


def test_legacy_account_migration_retains_password_session_roles_and_explicit_admin_authority(tmp_path):
    import hashlib,time
    from backend.engine.shared_accounts import AccountStore
    path=tmp_path/'accounts.sqlite'
    salt='12'*16
    digest=AccountStore._password(PASSWORD,salt)
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL,administrator INTEGER NOT NULL,disabled INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE projects(id TEXT PRIMARY KEY,path TEXT NOT NULL);
            CREATE TABLE members(project_id TEXT NOT NULL,user_id TEXT NOT NULL,role TEXT NOT NULL,PRIMARY KEY(project_id,user_id));''')
        db.execute('INSERT INTO users VALUES(?,?,?,?,?,0)',('old-admin','legacy',salt,digest,1))
        db.execute('INSERT INTO users VALUES(?,?,?,?,?,0)',('old-viewer','viewer',salt,digest,0))
        db.execute('INSERT INTO projects VALUES(?,?)',('old-project',str(tmp_path/'project')))
        db.execute('INSERT INTO members VALUES(?,?,?)',('old-project','old-viewer','reviewer'))
        expires=time.time()+3600
        db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(b'legacy-token').hexdigest(),'old-viewer',expires))
    for _ in range(2):
        accounts=AccountStore(path)
        assert accounts.authenticate('legacy-token')['id']=='old-viewer'
        assert accounts.project_role('old-viewer','old-project')=='reviewer'
        assert accounts.project_role('old-admin','old-project')=='owner'
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT salt,password_hash FROM users WHERE id=?',('old-admin',)).fetchone()==(salt,digest)
            assert db.execute('SELECT expires,transport FROM sessions').fetchone()==(expires,'bearer')
            assert db.execute('SELECT count(*) FROM members WHERE user_id=?',('old-admin',)).fetchone()[0]==1
        newer=accounts.create_user('new-admin'+str(_),PASSWORD,True)
        assert accounts.project_role(newer['id'],'old-project') is None


def test_parallel_account_store_migration_is_idempotent(tmp_path):
    from backend.engine.shared_accounts import AccountStore
    path=tmp_path/'accounts.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL)')
    gate=threading.Barrier(8)
    def start(_):
        gate.wait(timeout=5)
        return AccountStore(path).metadata()
    with ThreadPoolExecutor(max_workers=8) as executor:
        rows=list(executor.map(start,range(8)))
    assert len({row['workspace_id'] for row in rows})==1
    assert len({row['organization_id'] for row in rows})==1


@pytest.mark.parametrize('requested,actual',[(True,1),(1,True),(0,0),('', ''),(1.5,1.5)])
def test_resource_revision_decision_rejects_invalid_or_boolean_revisions(team,requested,actual):
    app,_,_,project,users,_=team
    decision=app.state.accounts.authorize(users['projectowner']['id'],'membership.change',project['id'],requested,revision_lookup=lambda:actual)
    assert not decision.allowed and not decision.resource_revision_verified


def test_oidc_nonfinite_expiry_is_not_verified(team):
    from backend.contracts.authentication import configure_oidc,OidcRegistration,VerifiedIdentity
    app,_,admin_client,_,users,_=team
    class Provider:
        def authorization_url(self,**kwargs):return 'https://identity.test/authorize'
        def exchange(self,**kwargs):return VerifiedIdentity('https://identity.test','subject','client',float('nan'),kwargs['nonce'])
    configure_oidc(app,{'fixture':OidcRegistration('https://identity.test','client','https://fixture.test/callback',Provider())})
    assert admin_client.post('/api/accounts/oidc/identities',json={'provider':'fixture','subject':'subject','user_id':users['vieweruser']['id']}).status_code==200
    client=TestClient(app,base_url='https://fixture.test',headers={'Origin':'https://fixture.test'})
    state=client.post('/api/accounts/oidc/start',json={'provider':'fixture'}).json()['state']
    assert client.post('/api/accounts/oidc/callback',json={'state':state,'code':'fixture-code'}).status_code==401


@pytest.mark.parametrize('role',['viewer','labeler','trainer','reviewer','owner'])
def test_all_project_roles_cannot_read_foreign_image_artifact_job_or_fleet(team,role,monkeypatch):
    from types import SimpleNamespace
    from PIL import Image
    from backend.api import routes_training
    from backend.engine.fleet import FleetRegistry
    app,admin,admin_client,first,_,_=team
    second=admin_client.post('/api/project/create',json={'name':'Foreign data fixture'}).json()
    account=app.state.accounts.create_user('role-'+role,PASSWORD)
    app.state.accounts.set_membership(first['id'],account['id'],role,admin['id'])
    client=TestClient(app)
    token=client.post('/api/accounts/login',json={'username':'role-'+role,'password':PASSWORD}).json()['token']
    client.headers.update({'Authorization':'Bearer '+token,'X-Vision-Project':first['id']})
    foreign=Path(second['project_dir'])/'foreign.png';Image.new('RGB',(8,8),'white').save(foreign)
    assert client.get('/api/dataset/raw/foreign',params={'file_path':str(foreign)}).status_code==403
    model=Path(second['models_dir'])/'metadata.json';model.write_bytes(b'foreign model metadata')
    import hashlib
    response=admin_client.post('/api/context/artifacts',headers={'X-Vision-Project':second['id']},json={'kind':'model','relative_path':'metadata.json','sha256':hashlib.sha256(model.read_bytes()).hexdigest()})
    assert response.status_code==200,response.text
    ref=response.json()['artifact_ref']
    assert client.get('/api/context/artifacts/'+ref['id']+'/content',params={'revision':ref['revision'],'sha256':ref['sha256']}).status_code==404
    monkeypatch.setattr(routes_training.training_job_manager,'list_jobs',lambda:[SimpleNamespace(output_dir=str(Path(second['models_dir'])/'foreign-job'))])
    jobs=client.get('/api/training/jobs')
    assert jobs.status_code==200,jobs.text
    assert jobs.json()['jobs']==[]
    target=FleetRegistry(second['project_dir']).save_target(name='Foreign fixture',url='https://example.invalid',token='fixture-token-12345678')
    from backend.engine import fleet
    monkeypatch.setattr(fleet.httpx,'Client',lambda *args,**kwargs:pytest.fail('Foreign target must never create an HTTP agent client'))
    assert client.get('/api/fleet/targets/'+target['target_id']).status_code==409


def test_browser_cookie_cannot_be_replayed_over_http_or_unapproved_origin(team):
    app,_,_,_,_,_=team
    browser=TestClient(app,base_url='https://fixture.test')
    login=browser.post('/api/accounts/login',json={'username':'vieweruser','password':PASSWORD,'transport':'cookie'},headers={'Origin':'https://fixture.test'})
    cookie=browser.cookies.get('vision_session')
    assert login.status_code==200
    assert browser.get('/api/accounts/me',headers={'Origin':'https://attacker.test'}).status_code==403
    insecure=TestClient(app,base_url='http://fixture.test',headers={'Cookie':'vision_session='+cookie})
    assert insecure.get('/api/accounts/me').status_code==403


def test_legacy_administrator_lower_membership_preserves_effective_owner_until_explicit_change(tmp_path):
    from backend.engine.shared_accounts import AccountStore
    path=tmp_path/'accounts.sqlite'
    salt='12'*16;digest=AccountStore._password(PASSWORD,salt)
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL,administrator INTEGER NOT NULL,disabled INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE projects(id TEXT PRIMARY KEY,path TEXT NOT NULL);
            CREATE TABLE members(project_id TEXT NOT NULL,user_id TEXT NOT NULL,role TEXT NOT NULL,PRIMARY KEY(project_id,user_id));''')
        db.executemany('INSERT INTO users VALUES(?,?,?,?,?,0)',[('old-admin','legacy',salt,digest,1),('other-owner','owner',salt,digest,0)])
        db.execute('INSERT INTO projects VALUES(?,?)',('project',str(tmp_path/'project')))
        db.executemany('INSERT INTO members VALUES(?,?,?)',[('project','old-admin','viewer'),('project','other-owner','owner')])
    accounts=AccountStore(path)
    assert accounts.project_role('old-admin','project')=='owner'
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT role FROM members WHERE user_id='old-admin'").fetchone()[0]=='viewer'
    accounts.set_membership('project','old-admin','reviewer','other-owner')
    assert AccountStore(path).project_role('old-admin','project')=='reviewer'
    events=accounts.audit_events('project','other-owner')
    changed=next(row for row in events if row['action']=='membership.change')
    assert changed['details']['previous_effective_role']=='owner'
    assert changed['details']['current_effective_role']=='reviewer'
    accounts.remove_membership('project','old-admin','other-owner')
    assert AccountStore(path).project_role('old-admin','project') is None


def test_oidc_state_is_bound_to_initiating_browser_not_only_origin(team):
    import time
    from backend.contracts.authentication import configure_oidc,OidcRegistration,VerifiedIdentity
    app,_,admin_client,_,users,_=team
    class Provider:
        def authorization_url(self,**kwargs):return 'https://identity.test/authorize'
        def exchange(self,**kwargs):return VerifiedIdentity('https://identity.test','subject','client',time.time()+60,kwargs['nonce'])
    configure_oidc(app,{'fixture':OidcRegistration('https://identity.test','client','https://fixture.test/callback',Provider())})
    assert admin_client.post('/api/accounts/oidc/identities',json={'provider':'fixture','subject':'subject','user_id':users['vieweruser']['id']}).status_code==200
    origin={'Origin':'https://fixture.test'}
    initiator=TestClient(app,base_url='https://fixture.test',headers=origin)
    other=TestClient(app,base_url='https://fixture.test',headers=origin)
    started=initiator.post('/api/accounts/oidc/start',json={'provider':'fixture'})
    body={'state':started.json()['state'],'code':'fixture-code'}
    assert other.post('/api/accounts/oidc/callback',json=body).status_code==401
    assert initiator.post('/api/accounts/oidc/callback',json=body).status_code==200


def test_actual_middleware_decision_names_execution_and_never_echoes_verified_revision(team):
    from fastapi import Request
    app,admin,admin_client,project,users,clients=team
    @app.post('/api/training/_permission_probe')
    def probe(request:Request):
        return request.state.permission_decision.model_dump()
    client=clients['projectowner']
    context=client.get('/api/context').json()['project_context']
    headers={'X-Vision-Context':json.dumps(context)}
    result=client.post('/api/training/_permission_probe',json={'resource_revision':7,'actor':'forged'},headers=headers)
    assert result.status_code==200,result.text
    decision=result.json()
    assert decision['allowed'] and decision['actor_id']==users['projectowner']['id']
    assert decision['action']=='training.execute'
    assert decision['resource_revision'] is None and not decision['resource_revision_verified']
    app.state.accounts.set_membership(project['id'],users['projectowner']['id'],'viewer',admin['id'])
    assert client.post('/api/training/_permission_probe',json={},headers=headers).status_code==403
    events=admin_client.get(f"/api/accounts/projects/{project['id']}/audit").json()['events']
    assert any(row['action']=='training.execute' and row['decision']=='denied' for row in events)


def test_first_server_binding_gives_legacy_migration_audit_authoritative_workspace(tmp_path):
    from backend.engine.shared_accounts import AccountStore
    local=create_app(project_dir=str(tmp_path/'local-projects'))
    local_client=TestClient(local,headers={'X-Vision-Token':local.state.api_token})
    project=local_client.post('/api/project/create',json={'name':'Legacy workspace'}).json()
    auth=tmp_path/'auth';auth.mkdir();path=auth/'accounts.sqlite'
    salt='12'*16;digest=AccountStore._password(PASSWORD,salt)
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL,administrator INTEGER NOT NULL,disabled INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE projects(id TEXT PRIMARY KEY,path TEXT NOT NULL);''')
        db.execute('INSERT INTO users VALUES(?,?,?,?,?,0)',('old-admin','legacy',salt,digest,1))
        db.execute('INSERT INTO projects VALUES(?,?)',(project['id'],project['project_dir']))
    app=create_app(project_dir=str(tmp_path/'server-projects'),shared_auth_dir=str(auth))
    client=TestClient(app)
    token=client.post('/api/accounts/login',json={'username':'legacy','password':PASSWORD}).json()['token']
    client.headers['Authorization']='Bearer '+token
    context=client.get('/api/context',headers={'X-Vision-Project':project['id']}).json()['project_context']
    audit=client.get(f"/api/accounts/projects/{project['id']}/audit").json()['events']
    assert audit and all(row['workspace_id']==context['workspace_id'] for row in audit)


@pytest.mark.parametrize('role',['viewer','reviewer'])
def test_emergency_administrator_requires_explicit_live_membership_but_keeps_actual_role(team,role,monkeypatch):
    from backend.engine.fleet import FleetRegistry
    app,admin,admin_client,project,users,clients=team
    account=users['serveradmin'];client=clients['serveradmin']
    headers={'X-Vision-Project':project['id']}
    target=FleetRegistry(project['project_dir']).save_target(name='No command fixture',url='https://example.invalid',token='fixture-token-12345678')
    path='/api/fleet/targets/'+target['target_id']+'/emergency-rollback'
    payload={'deployment_id':'missing-deployment','reason':'Explicit authorized incident investigation'}
    monkeypatch.setattr(FleetRegistry,'apply',lambda *args,**kwargs:pytest.fail('No package is selected; no command may be applied'))
    assert client.post(path,json=payload,headers=headers).status_code==403
    app.state.accounts.set_membership(project['id'],account['id'],role,admin['id'])
    caps=client.get('/api/fleet/capabilities',headers=headers).json()
    assert caps['actor_role']==role and caps['can_emergency_rollback']
    assert caps['can_rollback']==(role=='reviewer')
    response=client.post(path,json=payload,headers=headers)
    assert response.status_code==409,response.text
    events=FleetRegistry(project['project_dir']).emergency_events(target['target_id'])
    assert [row['event'] for row in events]==['attempted','rejected']
    assert all(row['actor_id']==account['id'] and row['actor_role']==role for row in events)
    assert clients['projectowner'].delete(f"/api/accounts/projects/{project['id']}/members/{account['id']}").status_code==200
    assert client.post(path,json=payload,headers=headers).status_code==403
    app.state.accounts.set_membership(project['id'],account['id'],role,admin['id'])
    assert admin_client.patch('/api/accounts/users/'+account['id'],json={'disabled':True}).status_code==200
    assert client.post(path,json=payload,headers=headers).status_code==401
    assert len(FleetRegistry(project['project_dir']).emergency_events(target['target_id']))==2


def test_logout_before_membership_commit_rejects_inflight_session(team,monkeypatch):
    app,_,_,project,users,clients=team
    accounts=app.state.accounts
    checked=threading.Event();resume=threading.Event()
    original=accounts.project_role
    def pause(user,project_id):
        result=original(user,project_id)
        if user==users['projectowner']['id']:
            checked.set();assert resume.wait(5)
        return result
    monkeypatch.setattr(accounts,'project_role',pause)
    path=f"/api/accounts/projects/{project['id']}/members"
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending=executor.submit(clients['projectowner'].put,path,json={'user_id':users['targetuser']['id'],'role':'owner'})
        assert checked.wait(5)
        logout=TestClient(app,headers={'Authorization':clients['projectowner'].headers['authorization']})
        assert logout.post('/api/accounts/logout').status_code==200
        resume.set();response=pending.result(timeout=10)
    assert response.status_code==403,response.text
    assert original(users['targetuser']['id'],project['id']) is None


def test_cookie_websocket_checks_https_origin_context_and_live_revocation(team,monkeypatch):
    from urllib.parse import urlencode
    from starlette.websockets import WebSocketDisconnect
    from backend.api import websocket_telemetry
    app,_,_,project,_,_=team
    browser=TestClient(app,base_url='https://fixture.test',headers={'Origin':'https://fixture.test'})
    logged=browser.post('/api/accounts/login',json={'username':'vieweruser','password':PASSWORD,'transport':'cookie'})
    context=browser.get('/api/context',headers={'X-Vision-Project':project['id']}).json()['project_context']
    broadcaster=websocket_telemetry.TelemetryBroadcaster()
    monkeypatch.setattr(websocket_telemetry,'broadcaster',broadcaster)
    url='wss://fixture.test/ws/telemetry?'+urlencode({'project_context':json.dumps(context)})
    with pytest.raises(WebSocketDisconnect):
        with browser.websocket_connect(url,headers={'Origin':'https://attacker.test'}):pass
    with browser.websocket_connect(url) as ws:
        ws.receive_json()
        connection=next(iter(broadcaster._active_connections))
        assert connection.scope['state']['project_context'].model_dump()==context
        assert broadcaster._can_receive(connection,{'event':'hardware_stats','project_context':context})
        assert browser.post('/api/accounts/logout',headers={'X-Vision-CSRF':logged.json()['csrf_token']}).status_code==200
        assert not broadcaster._can_receive(connection,{'event':'hardware_stats','project_context':context})


def test_expired_bearer_is_rejected_before_captured_project_execution(team):
    import hashlib,time
    app,_,_,project,_,clients=team
    client=clients['vieweruser']
    context=client.get('/api/context').json()['project_context']
    token=client.headers['authorization'].removeprefix('Bearer ')
    with app.state.accounts._db() as db:
        db.execute('UPDATE sessions SET expires=? WHERE token_hash=?',(time.time()-1,hashlib.sha256(token.encode()).hexdigest()))
    assert client.get('/api/project/current',headers={'X-Vision-Context':json.dumps(context)}).status_code==401
