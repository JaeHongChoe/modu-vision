"""Explicit same-path enrollment; old authority is retired before publication."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from .client import LocalHostRemoteError
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse

BASE = '/api/local-host/projects/location-recovery'
ACTIONS = frozenset('projects.locationRecovery.' + name for name in ('list','choose','confirm','status'))
CODE = 'local_project_location_recovery_required'


def refusal(code, message, status=409):
    return HTTPException(status, {'code': code, 'message': message})


def location_changed():
    return refusal(CODE, '프로젝트 폴더의 저장 위치를 다시 확인하세요.')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',',':')).encode()).hexdigest()


class RecoveryAdmission:
    def __init__(self):
        self.lock = threading.RLock()
        self.active = {}
        self.blocked = set()

    @contextmanager
    def admit(self, actor):
        with self.lock:
            if actor in self.blocked:
                raise refusal('location_recovery_busy', '폴더 재연결을 마친 뒤 다시 로그인하세요.')
            self.active[actor] = self.active.get(actor, 0) + 1
        try: yield
        finally:
            with self.lock:
                self.active[actor] -= 1
                if not self.active[actor]: del self.active[actor]

    @contextmanager
    def transition(self, actor):
        with self.lock:
            if actor in self.blocked or self.active.get(actor, 0):
                raise refusal('location_recovery_busy', '진행 중인 요청이 끝난 뒤 폴더를 다시 확인하세요.')
            self.blocked.add(actor)
        try: yield
        finally:
            with self.lock: self.blocked.discard(actor)


class RecoveryAdmissionMiddleware:
    """Own the full HTTP lifetime before the shared resolver can load storage."""
    def __init__(self, app, coordinator): self.app, self.coordinator = app, coordinator

    async def __call__(self, scope, receive, send):
        path = scope.get('path','')
        from .project_assets import DOWNLOAD, has_ticket, private_response
        asset_request = has_ticket(scope)
        download_request = scope['type']=='http' and path==DOWNLOAD
        if (asset_request or download_request) and scope['type']=='http': send=private_response(send)
        if (scope['type'] not in {'http','websocket'} or not path.startswith(('/api/','/ws/'))
                or path.startswith('/api/accounts/')) and not asset_request:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        token = headers.get('authorization','').removeprefix('Bearer ')
        if not token and scope['type'] == 'websocket':
            token = next((p[7:] for p in scope.get('subprotocols',[]) if p.startswith('vision.')), '')
        explicit_token = bool(token)
        if not token: token = HTTPConnection(scope).cookies.get('vision_session','')
        try: actor = self.coordinator.app.state.accounts.authenticate(token)['id']
        except ValueError: return await self.app(scope, receive, send)
        try:
            assets = getattr(self.coordinator.app.state,'project_assets',None)
            if download_request and scope.get('method')=='POST':
                if assets is None:raise refusal('local_project_asset_ticket_required','다운로드 권한을 다시 확인하세요.',403)
                with self.coordinator.admission.admit(actor):
                    replay=await assets.prepare_download(scope,receive,{'id':actor},token)
                    return await self.app(scope,replay,send)
            asset_context = assets.check(scope,{'id':actor},token) if asset_request and assets is not None else None
            if asset_request and asset_context is None: raise refusal('local_project_asset_ticket_required','이미지 읽기 권한을 다시 확인하세요.',403)
            bootstrap = path in {'/api/local-host/capabilities','/api/local-host/session','/api/local-host/status'}
            # The remote broker already compares its own cookie/tab/connection
            # binding; its existing WebSocket transport stays unchanged.
            remote_broker = path.startswith('/api/local-host/remote/')
            selected_protocol = None
            if not explicit_token and not bootstrap and not remote_broker and asset_context is None:
                required = self.coordinator.requires_browser_proof(actor)
                if scope['type'] == 'websocket':
                    offered = [value for value in scope.get('subprotocols',[]) if value.startswith('vision-local-tab.')]
                    if required or offered:
                        proof = offered[0].removeprefix('vision-local-tab.') if len(offered)==1 else ''
                        self.coordinator.check_browser_proof(actor,token,proof)
                        selected_protocol = offered[0]
                elif required:
                    self.coordinator.check_browser_proof(actor,token,headers.get('x-vision-local-tab',''))
            if path == BASE or path.startswith(BASE+'/'):
                return await self.app(scope,receive,send)
            if scope['type'] == 'websocket':
                lease = self.coordinator.admission.admit(actor)
                lease.__enter__()
                live = [True]
                def registered():
                    if live[0]:
                        live[0] = False
                        lease.__exit__(None,None,None)
                scope.setdefault('state',{})['_local_recovery_handshake_complete'] = registered
                async def protocol_send(message):
                    if message['type']=='websocket.accept' and selected_protocol is not None:
                        message = {**message,'subprotocol':selected_protocol}
                    await send(message)
                try: return await self.app(scope,receive,protocol_send)
                finally: registered()
            with self.coordinator.admission.admit(actor):
                return await self.app(scope, receive, send)
        except HTTPException as exc:
            if scope['type'] == 'websocket': await send({'type':'websocket.close','code':1008})
            else: await JSONResponse({'detail':exc.detail}, status_code=exc.status_code)(scope,receive,send)


class Candidate:
    def __init__(self, row, authority, revision, selected, snapshot, descriptor):
        self.row, self.authority, self.revision = row, authority, revision
        self.selected, self.snapshot, self.descriptor = selected, snapshot, descriptor
        self.expires = time.monotonic() + 120
        self.id = secrets.token_hex(24)
        self.used = False
    def close(self):
        if self.descriptor is not None: os.close(self.descriptor); self.descriptor = None


class LocationRecovery:
    def __init__(self, app, registry):
        self.app, self.registry = app, registry
        self.admission = RecoveryAdmission()
        self.candidates = {}
        self.candidate_lock = threading.RLock()
        with registry.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS location_recoveries(
                    receipt_id TEXT PRIMARY KEY,registration_id TEXT NOT NULL,actor_id TEXT NOT NULL,
                    phase TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS location_reselection(
                    registration_id TEXT NOT NULL,actor_id TEXT NOT NULL,receipt_id TEXT NOT NULL,
                    PRIMARY KEY(registration_id,actor_id));
            ''')

    def requires_browser_proof(self, actor):
        # Immutable approved recovery history is the durable local-only trigger;
        # clearing a project reselection marker must not reopen stale tabs.
        with self.registry.db() as db:
            return db.execute('SELECT 1 FROM location_recoveries WHERE actor_id=? LIMIT 1',(actor,)).fetchone() is not None

    def check_browser_proof(self, actor, token, proof):
        controller = getattr(self.app.state,'local_host',None)
        tab = controller.tabs.get(proof) if controller is not None else None
        session = hashlib.sha256(token.encode()).hexdigest()
        if (not tab or tab['actor_id'] != actor or tab['session_id'] != session
                or tab['expires'] <= time.monotonic()):
            raise refusal('local_browser_memory_proof_required','이 탭에서 다시 로그인하고 프로젝트를 선택하세요.',403)

    def pending(self, registration, actor):
        with self.registry.db() as db:
            return db.execute("SELECT r.phase FROM location_reselection s JOIN location_recoveries r USING(receipt_id) WHERE s.registration_id=? AND (r.phase='retiring' OR s.actor_id=?) ORDER BY CASE WHEN r.phase='retiring' THEN 0 ELSE 1 END LIMIT 1", (registration,actor)).fetchone()

    def check_access(self, row, actor, *, selecting=False):
        marker = self.pending(row['registration_id'], actor)
        if marker and marker['phase'] == 'retiring': raise location_changed()
        if marker and (not selecting or marker['phase'] != 'committed'):
            raise refusal('local_project_reselection_required', '다시 로그인한 뒤 프로젝트를 직접 선택하세요.')

    def selected(self, row, actor):
        with self.registry.db() as db:
            marker = db.execute('SELECT r.phase FROM location_reselection s JOIN location_recoveries r USING(receipt_id) WHERE s.registration_id=? AND s.actor_id=?', (row['registration_id'],actor)).fetchone()
            if marker and marker['phase'] != 'committed': raise location_changed()
            db.execute('DELETE FROM location_reselection WHERE registration_id=? AND actor_id=?', (row['registration_id'],actor))

    def authority(self, request, action):
        # Recovery authority has no project context, including stale defaults.
        forbidden = ('x-vision-context','x-vision-project','x-vision-workspace','x-vision-actor','x-vision-remote-context')
        if any(request.headers.get(name) for name in forbidden):
            raise refusal('invalid_location_recovery_context', '프로젝트 선택을 해제한 뒤 폴더를 확인하세요.', 403)
        controller = self.app.state.local_host
        authority = controller.authorize(request, 'projects.locationRecovery.'+action)
        tab = controller.tabs[authority['tab_id']]
        if not tab['approved']: raise HTTPException(403, 'Approve this web account in the local host dialog first')
        return authority

    def row(self, identifier, expected=None):
        with self.registry.db() as db:
            row = db.execute('SELECT * FROM locations WHERE registration_id=?', (identifier,)).fetchone()
        if row is None: raise HTTPException(404, 'Registration unavailable')
        if expected is not None and row['revision'] != expected: raise location_changed()
        return dict(row)

    def owner(self, row, authority, *, expected=None, db=None, require_session=True):
        accounts = self.app.state.accounts
        if db is None:
            with self.registry.db() as registration_db:
                foreign_pending = registration_db.execute("SELECT 1 FROM location_recoveries WHERE registration_id=? AND phase='retiring' AND actor_id!=?",(row['registration_id'],authority['actor_id'])).fetchone()
            if foreign_pending: raise refusal('shared_registration_requires_administrator','다른 계정의 중단된 재연결이 있습니다. 관리자 검토가 필요합니다.')
            with accounts._db() as held:
                return self.owner(row,authority,expected=expected,db=held,require_session=require_session)
        actor = authority['actor_id']; key = row['permission_key']
        try:
            if require_session: accounts._require_session(db,actor,authority['token'],'project.manage',key)
        except ValueError as exc: raise HTTPException(401, 'Account session unavailable') from exc
        if accounts._role(db,actor,key) != 'owner': raise HTTPException(403,'Current registration owner required')
        project = db.execute('SELECT path FROM projects WHERE id=?',(key,)).fetchone()
        if not project or project['path'] != row['path']: raise location_changed()
        revision_row = db.execute('SELECT revision FROM member_revisions WHERE project_id=? AND user_id=?',(key,actor)).fetchone()
        revision = revision_row['revision'] if revision_row else 0
        if expected is not None and revision != expected: raise HTTPException(403,'Registration membership changed')
        members = [r['id'] for r in db.execute('SELECT id FROM users WHERE disabled=0') if accounts._role(db,r['id'],key) is not None]
        if members != [actor]:
            raise refusal('shared_registration_requires_administrator', '여러 계정이 사용하는 프로젝트는 관리자 검토가 필요합니다.')
        return revision

    def recheck(self, request, candidate, action):
        if candidate.expires <= time.monotonic(): raise location_changed()
        now = self.authority(request,action)
        if any(now.get(k) != candidate.authority.get(k) for k in ('actor_id','session_id','tab_id')): raise HTTPException(403,'Recovery approval scope changed')
        self.owner(candidate.row,candidate.authority,expected=candidate.revision)
        row = self.row(candidate.row['registration_id'],candidate.row['revision'])
        if row['identity'] != candidate.row['identity']: raise location_changed()
        return row

    def snapshot(self, row, fd):
        from .location_identity import read, capture, open_directory
        with open_directory(row['path']) as current:
            a, b = os.fstat(fd), os.fstat(current)
            if (a.st_dev,a.st_ino) != (b.st_dev,b.st_ino): raise location_changed()
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os,'O_NONBLOCK',0)
        manifest = os.open('project.json', flags, dir_fd=fd)
        try:
            info = os.fstat(manifest)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 2*1024*1024: raise location_changed()
            raw = b''
            while len(raw) <= 2*1024*1024:
                chunk = os.read(manifest,65536)
                if not chunk: break
                raw += chunk
            after = os.fstat(manifest)
            if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns) != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns): raise location_changed()
            value = json.loads(raw)
            if not isinstance(value,dict) or value.get('id') != row['project_id']: raise location_changed()
        finally: os.close(manifest)
        identity = capture(fd)
        entry = os.stat('project.json',dir_fd=fd,follow_symlinks=False)
        fingerprint = lambda item: (item.st_dev,item.st_ino,item.st_mode,item.st_nlink,item.st_size,item.st_mtime_ns,item.st_ctime_ns)
        if fingerprint(entry) != fingerprint(after): raise location_changed()
        with open_directory(row['path']) as current:
            latest = os.fstat(current)
            if (a.st_dev,a.st_ino) != (latest.st_dev,latest.st_ino): raise location_changed()
        return {'identity':identity,'directory_identity':[str(a.st_dev),str(a.st_ino)],
                'manifest_sha256':hashlib.sha256(raw).hexdigest()}

    def check_snapshot(self, candidate):
        try:
            if self.snapshot(candidate.row,candidate.descriptor) != candidate.snapshot: raise location_changed()
        except (OSError,ValueError,TypeError): raise location_changed() from None

    def prune(self):
        for key,c in list(self.candidates.items()):
            if c.expires <= time.monotonic(): c.close(); del self.candidates[key]

    async def dialog(self, authority, method, args):
        try:
            return await self.app.state.local_host.client.call(authority, method, args)
        except TimeoutError:
            raise refusal('local_project_location_dialog_expired',
                '시간이 지나 폴더 확인이 만료되었습니다. 열린 선택·확인창을 닫고 프로젝트 폴더를 다시 확인하세요.',504) from None
        except LocalHostRemoteError as error:
            if error.code == 'native_dialog_busy':
                raise refusal('local_host_native_dialog_busy',
                    '열린 파일·폴더 선택창을 먼저 마친 뒤 다시 시도하세요.',409) from None
            raise

    async def choose(self, request, identifier, expected):
        authority = self.authority(request,'choose')
        authority = {**authority,'token':request.state.account_session_token}
        row = self.row(identifier,expected)
        revision = self.owner(row,authority)
        with self.candidate_lock:
            self.prune()
            if len(self.candidates) >= 32: raise HTTPException(429,'Too many pending folder confirmations')
        result = await self.dialog(authority,'dialog.folder',{'purpose':'location-recovery','title':'등록된 프로젝트 폴더 다시 선택','defaultPath':row['path']})
        current = self.authority(request,'choose')
        self.owner(row,authority,expected=revision)
        if not result: return {'canceled':True}
        if not isinstance(result,str) or result != row['path']: raise location_changed()
        from .location_identity import open_directory
        candidate = None
        try:
            with open_directory(result) as fd:
                snapshot = self.snapshot(row,fd)
                candidate = Candidate(row,authority,revision,result,snapshot,os.dup(fd))
        except (OSError,ValueError,TypeError):
            if candidate is not None: candidate.close()
            raise location_changed() from None
        try: self.recheck(request,candidate,'choose')
        except BaseException:
            candidate.close()
            raise
        with self.candidate_lock:
            self.prune()
            if len(self.candidates) >= 32:
                candidate.close()
                raise HTTPException(429,'Too many pending folder confirmations')
            self.candidates[candidate.id] = candidate
        return {'candidate_id':candidate.id,'registration_id':identifier,'path':row['path'],'project_id':row['project_id']}

    def workers_idle(self, row):
        from backend.engine.job_store import ledger
        from backend.api.routes_training import training_job_manager
        # Conservatively reject any active worker: some old workers lack a
        # complete namespace receipt. Never cancel them to obtain quiescence.
        if ledger().active() or training_job_manager.is_training:
            raise refusal('location_recovery_busy','진행 중인 작업이 끝난 뒤 폴더를 다시 확인하세요.')
        import importlib
        groups = (
            ('backend.api.routes_label_suggestions','_BATCH_LOCK',('_LIVE_BATCHES',)),
            ('backend.engine.labeling_tasks','_LOCK',('_LIVE',)),
            ('backend.api.routes_automated_training','_LOCK',('_EVENTS',)),
            ('backend.api.routes_model_operations','_LOCK',('_EVENTS',)),
            ('backend.engine.runtime_optimization_jobs','_LOCK',('_EVENTS',)),
            ('backend.engine.specialized_training_jobs','_LOCK',('_EVENTS',)),
            ('backend.api.routes_enhancement','_JOB_LOCK',('_CANCEL_EVENTS',)),
            ('backend.engine.comparison_jobs','_LOCK',('_LIVE','_RUNNING')),
            ('backend.engine.training_engine','_LOCK',('_EVENTS',)),
        )
        for module_name, lock_name, containers in groups:
            module = importlib.import_module(module_name)
            with getattr(module,lock_name):
                if any(getattr(module,name) for name in containers):
                    raise refusal('location_recovery_busy','진행 중인 작업이 끝난 뒤 폴더를 다시 확인하세요.')
        from backend.api import routes_rotated_detection
        with routes_rotated_detection._LOCK:
            if any(job.status in {'queued','running','stopping'} or (job.thread and job.thread.is_alive()) for job in routes_rotated_detection._JOBS.values()):
                raise refusal('location_recovery_busy','진행 중인 작업이 끝난 뒤 폴더를 다시 확인하세요.')
        from .location_identity import open_directory
        with open_directory(row['path']) as descriptor:
            for name in ('operations_worker','runtime_service'):
                try: os.stat(name,dir_fd=descriptor,follow_symlinks=False)
                except FileNotFoundError: continue
                # These independent processes can write after HTTP requests end.
                # Their existing status APIs mutate storage; no idle proof is
                # manufactured here and the repair never stops them.
                raise refusal('location_recovery_external_worker_review_required','독립 실행 서비스가 설정된 폴더입니다. 서비스를 확인한 뒤 관리자에게 재연결 검토를 요청하세요.')


    async def confirm(self, request, identifier):
        authority = self.authority(request,'confirm')
        # The immutable receipt, rather than an in-memory dialog, handles a lost
        # successful response even after restart and a fresh login.
        with self.registry.db() as db:
            replay = db.execute("SELECT * FROM location_recoveries WHERE actor_id=? AND phase='committed' AND json_extract(payload,'$.candidate_id')=?",(authority['actor_id'],identifier)).fetchone()
        if replay:
            row = self.row(replay['registration_id'])
            if self.app.state.accounts.project_role_exact(authority['actor_id'],row['permission_key']) != 'owner': raise HTTPException(403,'Current registration owner required')
            return {'receipt_id':replay['receipt_id'],'requires_relogin':True,'autoactivated':False}
        with self.candidate_lock:
            self.prune(); candidate = self.candidates.get(identifier)
            if candidate is None or candidate.used: raise location_changed()
            candidate.used = True
        try:
            self.recheck(request,candidate,'confirm'); self.check_snapshot(candidate)
            row = candidate.row; controller = self.app.state.local_host
            args = {'origin':controller.origin,'account_label':candidate.authority['account']['username'],
                    'registration_id':row['registration_id'],'project_id':row['project_id'],'workspace_id':row['workspace_id'],
                    'path':row['path'],'revision':row['revision'],**candidate.snapshot}
            args['approval_digest'] = digest(args)
            approved = await self.dialog(candidate.authority,'host.approveProjectLocation',args)
            self.recheck(request,candidate,'confirm'); self.check_snapshot(candidate)
            if not isinstance(approved,dict) or approved.get('approved') is not True: return {'canceled':True}
            if approved.get('approval_digest') != args['approval_digest']: raise location_changed()
            with self.admission.transition(candidate.authority['actor_id']):
                self.workers_idle(row)
                from backend.api.websocket_telemetry import broadcaster
                reserved = broadcaster.reserve_recovery_sockets(self.app,candidate.authority['actor_id'])
                try:
                    receipt, sessions = self.retire_checkpoint(candidate,args['approval_digest'])
                    try:
                        await controller.retire_recovery_sessions(sessions,candidate.authority['actor_id'])
                        await broadcaster.retire_account_sessions(self.app,sessions)
                    except (RuntimeError,TimeoutError,OSError) as exc:
                        raise HTTPException(503,'Session retirement incomplete; log in and confirm the folder again') from exc
                    self.check_snapshot(candidate)
                    if candidate.expires <= time.monotonic(): raise location_changed()
                    self.publish(candidate,receipt)
                finally: broadcaster.release_recovery_sockets(reserved)
            return {'receipt_id':receipt,'requires_relogin':True,'autoactivated':False}
        finally:
            candidate.close()
            with self.candidate_lock: self.candidates.pop(identifier,None)

    def retire_checkpoint(self,candidate,approval):
        row = candidate.row; actor = candidate.authority['actor_id']; accounts = self.app.state.accounts
        receipt = secrets.token_hex(24)
        with self.registry.db() as db:
            db.execute('BEGIN IMMEDIATE')
            with accounts._db() as held:
                self.owner(row,candidate.authority,expected=candidate.revision,db=held)
                current = db.execute('SELECT * FROM locations WHERE registration_id=?',(row['registration_id'],)).fetchone()
                if not current or current['revision'] != row['revision'] or current['identity'] != row['identity']: raise location_changed()
                self.check_snapshot(candidate)
                if candidate.expires <= time.monotonic(): raise location_changed()
                sessions = [r['token_hash'] for r in held.execute('SELECT token_hash FROM sessions WHERE user_id=?',(actor,))]
                payload = {'revision':row['revision'],'old_identity':row['identity'],'new_identity':candidate.snapshot['identity'],
                           'manifest_sha256':candidate.snapshot['manifest_sha256'],'membership_revision':candidate.revision,
                           'approval_digest':approval,'retired_sessions':sessions,'candidate_id':candidate.id}
                prior = db.execute('SELECT r.receipt_id,r.phase,r.payload FROM location_reselection s JOIN location_recoveries r USING(receipt_id) WHERE s.registration_id=? AND s.actor_id=?',(row['registration_id'],actor)).fetchone()
                if prior and prior['phase'] == 'retiring':
                    previous = json.loads(prior['payload'])['retired_sessions']
                    sessions = sorted(set(sessions) | set(previous))
                    payload['retired_sessions'] = sessions
                    db.execute("UPDATE location_recoveries SET phase='superseded' WHERE receipt_id=? AND phase='retiring'",(prior['receipt_id'],))
                db.execute('INSERT INTO location_recoveries VALUES(?,?,?,?,?)',(receipt,row['registration_id'],actor,'retiring',json.dumps(payload)))
                db.execute('INSERT OR REPLACE INTO location_reselection VALUES(?,?,?)',(row['registration_id'],actor,receipt))
                db.commit()  # Fail closed across the account-store commit boundary.
                held.execute('DELETE FROM sessions WHERE user_id=?',(actor,))
                held.execute('DELETE FROM selections WHERE user_id=?',(actor,))
                accounts._audit(held,actor,'session.revoke',None,'allowed',user_id=actor,reason='approved_location_recovery',receipt_id=receipt)
        return receipt,sessions

    def publish(self,candidate,receipt):
        row = candidate.row; actor = candidate.authority['actor_id']; accounts = self.app.state.accounts
        with self.registry.db() as db:
            db.execute('BEGIN IMMEDIATE')
            with accounts._db() as held:
                self.owner(row,candidate.authority,expected=candidate.revision,db=held,require_session=False)
                self.check_snapshot(candidate)
                if candidate.expires <= time.monotonic(): raise location_changed()
                pending = db.execute('SELECT phase FROM location_recoveries WHERE receipt_id=? AND actor_id=?',(receipt,actor)).fetchone()
                if not pending or pending['phase'] != 'retiring': raise location_changed()
                changed = db.execute('UPDATE locations SET identity=?,revision=revision+1 WHERE registration_id=? AND revision=? AND identity=?',
                    (json.dumps(candidate.snapshot['identity']),row['registration_id'],row['revision'],row['identity'])).rowcount
                if changed != 1: raise location_changed()
                db.execute("UPDATE location_recoveries SET phase='committed' WHERE receipt_id=?",(receipt,))
                db.commit()  # Membership reservation is held through publication.


def recovery_router(coordinator):
    router = APIRouter(prefix=BASE)
    class Choose(BaseModel):
        model_config = ConfigDict(extra='forbid',strict=True)
        registration_id: str = Field(min_length=1,max_length=128)
        expected_revision: int = Field(ge=1)
    class Confirm(BaseModel):
        model_config = ConfigDict(extra='forbid',strict=True)
        candidate_id: str = Field(pattern='^[a-f0-9]{48}$')

    @router.get('')
    def listing(request:Request):
        authority = coordinator.authority(request,'list')
        rows = []
        with coordinator.registry.db() as db:
            registered = [dict(r) for r in db.execute('SELECT * FROM locations')]
        for row in registered:
            if coordinator.app.state.accounts.project_role_exact(authority['actor_id'],row['permission_key']) != 'owner': continue
            marker = coordinator.pending(row['registration_id'],authority['actor_id'])
            state = 'confirmation_required' if marker and marker['phase']=='retiring' else 'requires_reselection' if marker else 'registered'
            rows.append({**{k:row[k] for k in ('registration_id','permission_key','project_id','workspace_id','path','revision')},'recovery_status':state})
        return {'registrations':rows}

    @router.post('/choose')
    async def choose(body:Choose,request:Request):
        return await coordinator.choose(request,body.registration_id,body.expected_revision)

    @router.post('/confirm')
    async def confirm(body:Confirm,request:Request):
        return await coordinator.confirm(request,body.candidate_id)

    @router.get('/receipts/{identifier}')
    def status(identifier:str,request:Request):
        authority = coordinator.authority(request,'status')
        with coordinator.registry.db() as db:
            row = db.execute('SELECT * FROM location_recoveries WHERE receipt_id=? AND actor_id=?',(identifier,authority['actor_id'])).fetchone()
        if row is None: raise HTTPException(404,'Receipt unavailable')
        registration = coordinator.row(row['registration_id'])
        if coordinator.app.state.accounts.project_role_exact(authority['actor_id'],registration['permission_key']) != 'owner': raise HTTPException(403,'Current registration owner required')
        return {'receipt_id':identifier,'registration_id':row['registration_id'],'phase':row['phase'],'autoactivated':False}
    return router
