"""Central registry of authenticated field agents and acknowledged releases."""
from __future__ import annotations
import io
import json
from pathlib import Path
import sqlite3
import time
import uuid
from urllib.parse import urlsplit,urlunsplit
import zipfile
import httpx
from backend.engine.runtime_deployment import DeploymentLedger


def validate_target_url(value):
    parts=urlsplit(value.strip())
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
        raise ValueError('Agent URL must be an HTTPS origin or loopback SSH tunnel')
    if parts.scheme=='http' and parts.hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('Remote agents require HTTPS; use a loopback SSH tunnel for HTTP')
    return urlunsplit((parts.scheme,parts.netloc,'','',''))


class EmergencyRollbackDenied(ValueError):
    pass


class EmergencyReasonRequired(ValueError):
    pass


class FleetRegistry:
    def __init__(self,project_dir):
        base=Path(project_dir)
        if base.is_symlink():raise ValueError('Fleet project storage is linked')
        self.root=base/'fleet';self.root.mkdir(parents=True,exist_ok=True)
        if self.root.is_symlink():raise ValueError('Fleet storage is linked')
        self.path=self.root/'agents.sqlite3'
        if self.path.is_symlink():raise ValueError('Fleet database is linked')
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS targets(target_id TEXT PRIMARY KEY,name TEXT NOT NULL,url TEXT NOT NULL,token TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS release_failures(attempt_id TEXT PRIMARY KEY,target_id TEXT,action TEXT,manifest_sha256 TEXT,reviewer TEXT,reason TEXT,created_at REAL)')
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS emergency_rollback_events(
                    event_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, target_id TEXT NOT NULL,
                    deployment_id TEXT NOT NULL, event TEXT NOT NULL, actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL, authentication TEXT NOT NULL, actor_role TEXT NOT NULL,
                    reason TEXT NOT NULL, detail TEXT, result_deployment_id TEXT, created_at REAL NOT NULL);
                CREATE TRIGGER IF NOT EXISTS emergency_rollback_no_update
                    BEFORE UPDATE ON emergency_rollback_events BEGIN SELECT RAISE(ABORT,'Emergency rollback audit is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS emergency_rollback_no_delete
                    BEFORE DELETE ON emergency_rollback_events BEGIN SELECT RAISE(ABORT,'Emergency rollback audit is immutable'); END;
            """)
        self.path.chmod(0o600)
    def connect(self):
        conn=sqlite3.connect(self.path,timeout=30);conn.row_factory=sqlite3.Row;return conn
    def targets(self):
        with self.connect() as conn:rows=conn.execute('SELECT * FROM targets ORDER BY name').fetchall()
        return [{key:row[key] for key in ('target_id','name','url')}|{'token_set':bool(row['token'])} for row in rows]
    def secret(self,identifier):
        with self.connect() as conn:row=conn.execute('SELECT token FROM targets WHERE target_id=?',(identifier,)).fetchone()
        if not row:raise KeyError(identifier)
        return row[0]
    def target(self,identifier):
        row=next((r for r in self.targets() if r['target_id']==identifier),None)
        if not row:raise KeyError(identifier)
        return row
    def save_target(self,*,name,url,token,target_id=None):
        if not name.strip() or len(name)>100 or not token or len(token)>4096:raise ValueError('Agent name and access token are required')
        url=validate_target_url(url);identifier=target_id or uuid.uuid4().hex
        if len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise ValueError('Invalid agent ID')
        with self.connect() as conn:conn.execute('INSERT INTO targets VALUES(?,?,?,?) ON CONFLICT(target_id) DO UPDATE SET name=excluded.name,url=excluded.url,token=excluded.token',(identifier,name.strip(),url,token))
        return self.target(identifier)
    def ledger(self,identifier):
        self.target(identifier);return DeploymentLedger(self.root/identifier)
    def client(self,identifier):
        return httpx.Client(base_url=self.target(identifier)['url'],headers={'Authorization':'Bearer '+self.secret(identifier)},timeout=30,follow_redirects=False)
    def readback(self,identifier):
        try:
            with self.client(identifier) as client:response=client.get('/agent/v1/runtime');response.raise_for_status();runtime=response.json()
            active=self.ledger(identifier).active();expected=(active or {}).get('release',{}).get('manifest_sha256')
            return {'target':self.target(identifier),'runtime':runtime,'active':active,'matches_active':bool(expected and runtime.get('status')=='ready' and runtime.get('manifest_sha256')==expected and runtime.get('device')==(active or {}).get('release',{}).get('device'))}
        except (httpx.HTTPError,ValueError) as exc:return {'target':self.target(identifier),'runtime':{'status':'disconnected'},'error':type(exc).__name__,'active':self.ledger(identifier).active(),'matches_active':False}
    def failures(self,identifier):
        self.target(identifier)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM release_failures WHERE target_id=? ORDER BY created_at DESC LIMIT 100',(identifier,))]
    def record_failure(self,identifier,*,action,reviewer,error,manifest_sha256=None):
        reason=str(error) if isinstance(error,ValueError) else type(error).__name__
        with self.connect() as conn:
            conn.execute('INSERT INTO release_failures VALUES(?,?,?,?,?,?,?)',
                         (uuid.uuid4().hex,identifier,action,manifest_sha256,reviewer,reason,time.time()))
    def apply(self,identifier,release,*,reviewer,restored_from=None,project=None):
        if project is None:
            from backend.api.routes_project import _load_project
            project=_load_project(self.root.parent)
        if Path(project['project_dir']).resolve()!=self.root.parent.resolve():
            raise ValueError('Central release project identity differs')
        from backend.engine.release_eligibility import release_authority
        try:
            with release_authority(project):
                return self._apply_authorized(identifier,release,reviewer=reviewer,restored_from=restored_from,project=project)
        except (ValueError,OSError,RuntimeError,httpx.HTTPError) as exc:
            # Preflight failures occur before the deployment journal exists.
            # Retain their concrete reason without issuing any remote command.
            self.record_failure(identifier,action='rollback' if restored_from else 'apply',
                                reviewer=reviewer,error=exc,manifest_sha256=release.get('manifest_sha256'))
            raise
    def _apply_authorized(self,identifier,release,*,reviewer,restored_from,project):
        from backend.engine.release_eligibility import authorize_release_action
        action='rollback' if restored_from else 'apply'
        # Reject before ledger recovery as well: rejected commands send no traffic.
        authorize_release_action(release['package_path'],project,action=action)
        def apply_remote(selected):
            authorize_release_action(selected['package_path'],project,action=action)
            from backend.engine.flow_package_runtime import verify_flow_package
            from backend.engine.inspection_service import _verify_release_policy
            package=Path(selected['package_path']);policy_path=Path(selected.get('release_policy') or '')
            if not selected.get('release_policy') or policy_path.is_symlink() or not policy_path.is_file() or policy_path.stat().st_size>65536:
                raise ValueError('Field release requires its separately trusted approval/device/cohort policy')
            _verify_release_policy(package,verify_flow_package(package)[1],policy_path,device=selected['device'])
            policy=json.loads(policy_path.read_text())
            if policy['manifest_sha256']!=selected['manifest_sha256']:raise ValueError('Field release policy differs from selected manifest')
            with self.client(identifier) as client:
                response=client.post('/agent/v1/releases',content=package_archive(package),headers={'Content-Type':'application/zip','X-Manifest-SHA256':selected['manifest_sha256'],
                    'X-Release-Policy':json.dumps(policy,separators=(',',':'))});response.raise_for_status()
                response=client.post('/agent/v1/apply',json={'manifest_sha256':selected['manifest_sha256'],'device':selected['device']});response.raise_for_status()
                ack=client.get('/agent/v1/runtime');ack.raise_for_status();return ack.json()
        return self.ledger(identifier).apply(release,apply_remote,reviewer=reviewer,restored_from=restored_from)
    def emergency_events(self,identifier):
        self.target(identifier)
        with self.connect() as conn:
            rows=conn.execute('SELECT * FROM emergency_rollback_events WHERE target_id=? ORDER BY rowid DESC LIMIT 100',(identifier,)).fetchall()
        return [dict(row) for row in reversed(rows)]
    def emergency_rollback(self,identifier,deployment_id,*,actor,reason,project):
        """An owner-declared incident intent; ordinary live release gates still apply.

        Actor is supplied by the authenticated route, never by the request body.
        Append an attempt before authorization/transport, then its outcome. A
        crash leaves an explicit incomplete attempt rather than a success claim.
        """
        request_id=uuid.uuid4().hex
        reason=reason.strip()
        def record(event,detail=None,result_deployment_id=None):
            event_id=uuid.uuid4().hex
            with self.connect() as conn:
                conn.execute('INSERT INTO emergency_rollback_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (event_id,request_id,identifier,deployment_id,event,actor['actor_id'],actor['actor_name'],
                              actor['authentication'],actor['actor_role'],reason,detail,result_deployment_id,time.time()))
            return event_id
        record('attempted')
        if not actor['can_emergency_rollback']:
            record('denied','owner_or_administrator_required')
            raise EmergencyRollbackDenied('Emergency rollback requires project owner or administrator permission')
        if not reason:
            record('denied','emergency_reason_required')
            raise EmergencyReasonRequired('Emergency rollback requires a nonblank reason')
        try:
            selected=self.rollback(identifier,deployment_id,reviewer=actor['actor_name'],project=project)
        except (ValueError,OSError,KeyError,RuntimeError,httpx.HTTPError) as exc:
            record('rejected',str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
            raise
        event_id=record('committed',result_deployment_id=selected['deployment_id'])
        return {**selected,'emergency':{'request_id':request_id,'event_id':event_id,'reason':reason,
                                      'actor_id':actor['actor_id'],'actor_name':actor['actor_name']}}
    def rollback(self,identifier,deployment_id,*,reviewer,project=None):
        target=next((r for r in self.ledger(identifier).history() if r['deployment_id']==deployment_id),None)
        if not target:raise KeyError(deployment_id)
        selected=self.apply(identifier,target['release'],reviewer=reviewer,restored_from=deployment_id,project=project)
        return selected


def package_archive(package):
    from backend.engine.flow_package_runtime import verify_flow_package
    verify_flow_package(package);manifest=json.loads((package/'manifest.json').read_text());stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in ['manifest.json',*(['parity_receipt.json'] if not manifest.get('runtime_acceptance_sha256') else []),*[row['path'] for row in manifest['files']]]:
            path=package/relative
            if path.is_symlink() or not path.resolve().is_relative_to(package.resolve()):raise ValueError('Release archive contains linked or escaping files')
            archive.write(path,relative)
    return stream.getvalue()
