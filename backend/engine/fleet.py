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
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS rollouts(plan_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,payload TEXT NOT NULL,updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS rollout_events(event_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,revision INTEGER NOT NULL,event TEXT NOT NULL,reviewer TEXT NOT NULL,created_at REAL NOT NULL);
                CREATE TRIGGER IF NOT EXISTS rollout_events_no_update BEFORE UPDATE ON rollout_events BEGIN SELECT RAISE(ABORT,'Rollout audit is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS rollout_events_no_delete BEFORE DELETE ON rollout_events BEGIN SELECT RAISE(ABORT,'Rollout audit is immutable'); END;
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
        if not row[0]:raise ValueError('Agent credentials are unavailable; configure this target before connecting')
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
        self.target(identifier);return DeploymentLedger(self.root/identifier,project_dir=self.root.parent)
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
            policy=json.loads(policy_path.read_text(encoding='utf-8'))
            if policy['manifest_sha256']!=selected['manifest_sha256']:raise ValueError('Field release policy differs from selected manifest')
            with self.client(identifier) as client:
                response=client.post('/agent/v1/releases',content=package_archive(package),headers={'Content-Type':'application/zip','X-Manifest-SHA256':selected['manifest_sha256'],
                    'X-Release-Policy':json.dumps(policy,separators=(',',':'))});response.raise_for_status()
                staged=response.json()
                if not isinstance(staged,dict) or staged.get('status')!='staged' or staged.get('manifest_sha256')!=selected['manifest_sha256']:
                    raise ValueError('Field stage acknowledgment identity mismatch')
                response=client.post('/agent/v1/apply',json={'manifest_sha256':selected['manifest_sha256'],'device':selected['device']});response.raise_for_status()
                DeploymentLedger._validate_ack(selected,response.json())
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

    def _rollout_lock(self,identifier):
        from backend.engine.runtime_process_control import runtime_state_lock
        if not isinstance(identifier,str) or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise ValueError('Invalid rollout plan ID')
        directory=self.root/'rollout_operations'/identifier
        if directory.is_symlink() or directory.parent.is_symlink():raise ValueError('Rollout operation storage is linked')
        directory.mkdir(parents=True,exist_ok=True)
        return runtime_state_lock(directory)

    def rollout(self,identifier):
        with self.connect() as conn:
            row=conn.execute('SELECT payload FROM rollouts WHERE plan_id=?',(identifier,)).fetchone()
        if row is None:raise KeyError(identifier)
        return json.loads(row['payload'])

    def rollouts(self):
        with self.connect() as conn:rows=conn.execute('SELECT payload FROM rollouts ORDER BY updated_at DESC').fetchall()
        return [json.loads(row['payload']) for row in rows]

    def rollout_events(self,identifier):
        self.rollout(identifier)
        with self.connect() as conn:rows=conn.execute('SELECT * FROM rollout_events WHERE plan_id=? ORDER BY revision',(identifier,)).fetchall()
        return [dict(row) for row in rows]

    def _save_rollout(self,plan,event,reviewer):
        previous=plan['revision'];updated={**plan,'revision':previous+1,'updated_at':time.time()}
        encoded=json.dumps(updated,ensure_ascii=False,allow_nan=False)
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            result=conn.execute('UPDATE rollouts SET revision=?,payload=?,updated_at=? WHERE plan_id=? AND revision=?',
                (updated['revision'],encoded,updated['updated_at'],plan['plan_id'],previous))
            if result.rowcount!=1:raise ValueError('Rollout revision changed; reload the plan before retrying')
            conn.execute('INSERT INTO rollout_events VALUES(?,?,?,?,?,?)',(uuid.uuid4().hex,plan['plan_id'],updated['revision'],event,reviewer,time.time()))
        plan.update(updated)
        return plan

    def _checked_rollout(self,identifier,expected_revision,reviewer,project=None):
        if not isinstance(reviewer,str) or not reviewer.strip():raise ValueError('Rollout reviewer is required')
        plan=self.rollout(identifier)
        if type(expected_revision) is not int or expected_revision!=plan['revision']:raise ValueError('Rollout revision changed; reload the plan before retrying')
        if project is not None and Path(project['project_dir']).resolve()!=self.root.parent.resolve():raise ValueError('Rollout project identity differs')
        return plan

    def create_rollout(self,release,*,target_ids,canary_target_ids=None,batch_size=5,reviewer,project):
        if Path(project['project_dir']).resolve()!=self.root.parent.resolve():raise ValueError('Rollout project identity differs')
        if not isinstance(reviewer,str) or not reviewer.strip():raise ValueError('Rollout reviewer is required')
        if not isinstance(target_ids,list) or not target_ids or len(target_ids)>1000 or len(set(target_ids))!=len(target_ids):raise ValueError('Rollout requires 1-1000 unique registered targets')
        canaries=canary_target_ids if canary_target_ids is not None else target_ids[:1]
        if not isinstance(canaries,list) or not canaries or len(set(canaries))!=len(canaries) or not set(canaries).issubset(target_ids):raise ValueError('Rollout canaries must be unique selected targets')
        if type(batch_size) is not int or not 1<=batch_size<=100:raise ValueError('Rollout batch size must be 1-100')
        if len(canaries)>batch_size:raise ValueError('Rollout canary cohort cannot exceed the reviewed batch size')
        if not isinstance(release,dict) or not isinstance(release.get('manifest_sha256'),str) or len(release['manifest_sha256'])!=64 or any(c not in '0123456789abcdef' for c in release['manifest_sha256']) or not release.get('package_path') or not release.get('device'):raise ValueError('Rollout requires a staged package hash and execution device')
        identifier=uuid.uuid4().hex;now=time.time()
        targets=[{'target_id':target_id,'target_url':self.target(target_id)['url'],'status':'pending','deployment_id':None,'previous_deployment_id':None,'error':None,'readback':None} for target_id in target_ids]
        plan={'schema_version':1,'plan_id':identifier,'revision':1,'status':'planned','operation':'deploy','release':{**release,'rollout_id':identifier},
            'targets':targets,'canary_target_ids':list(canaries),'batch_size':batch_size,'canary_confirmed':False,
            'reviewer':reviewer,'created_at':now,'updated_at':now,'pause_reason':None,
            'offline_policy':'existing acknowledged field runtime continues; new commands pause until live readback'}
        with self.connect() as conn:
            conn.execute('INSERT INTO rollouts VALUES(?,?,?,?)',(identifier,1,json.dumps(plan,ensure_ascii=False,allow_nan=False),now))
            conn.execute('INSERT INTO rollout_events VALUES(?,?,?,?,?,?)',(uuid.uuid4().hex,identifier,1,'created',reviewer,now))
        return plan

    def _rollout_probe(self,plan,target,*,require_desired=False):
        if self.target(target['target_id'])['url']!=target['target_url']:raise ValueError('Rollout target endpoint changed; create a new reviewed plan')
        result=self.readback(target['target_id']);runtime=result.get('runtime')
        target['readback']={'observed_at':time.time(),'runtime':runtime,'matches_active':result.get('matches_active',False)}
        if not isinstance(runtime,dict) or runtime.get('status')=='disconnected':raise ConnectionError('Target is offline; fresh runtime readback is required')
        if runtime.get('status') in ('failed','error','stopping'):raise ValueError('Target health blocks rollout')
        if require_desired:
            active=result.get('active') or {}
            if (not result.get('matches_active') or runtime.get('manifest_sha256')!=plan['release']['manifest_sha256'] or runtime.get('device')!=plan['release']['device'] or (target.get('deployment_id') and active.get('deployment_id')!=target['deployment_id'])):
                raise ValueError('Target desired release/device readback differs from the committed rollout receipt')
        return result

    def _pause_rollout_failure(self,plan,target,error,reviewer):
        target.update(status='offline' if isinstance(error,ConnectionError) else 'failed',error=str(error) if isinstance(error,(ValueError,ConnectionError)) else type(error).__name__)
        plan.update(status='paused',pause_reason=target['error'])
        return self._save_rollout(plan,'target_paused',reviewer)

    def _rollout_restored(self,plan,target,observed):
        """Adopt a committed restoration only when its exact receipt is live."""
        active=observed.get('active') or {}
        previous=target.get('previous_deployment_id')
        if not previous or active.get('restored_from')!=previous:return False
        if target.get('rollback_deployment_id') and active.get('deployment_id')!=target['rollback_deployment_id']:
            raise ValueError('Target active rollback receipt changed; review separately')
        requested=target.get('rollback_requested_at')
        if not requested or active.get('created_at',0)<requested:return False
        original=next((row for row in self.ledger(target['target_id']).history() if row['deployment_id']==previous),None)
        runtime=observed.get('runtime') or {}
        if (not original or not observed.get('matches_active') or runtime.get('status')!='ready'
            or runtime.get('manifest_sha256')!=original['release']['manifest_sha256']
            or runtime.get('device')!=original['release']['device']):
            raise ValueError('Rollback target readback differs from the previous committed release')
        target.update(status='rolled_back',rollback_deployment_id=active['deployment_id'],error=None)
        return True

    def advance_rollout(self,identifier,*,expected_revision,reviewer,project,confirm_canary=False):
        with self._rollout_lock(identifier):
            plan=self._checked_rollout(identifier,expected_revision,reviewer,project)
            if plan.get('operation')=='rollback':raise ValueError('Rollout rollback has started; continue rollback instead of deployment')
            if plan['status']=='paused':raise ValueError('Explicitly resume the paused rollout after checking target readback')
            if plan['status'] in ('completed','rolled_back'):return plan
            if plan['status']=='rolling_back' or any(row['status']=='applying' for row in plan['targets']):raise ValueError('Interrupted rollout requires explicit resume and live readback')
            if plan['status']=='waiting_canary_confirmation' and not confirm_canary:return plan
            if plan['status']=='planned' and confirm_canary:raise ValueError('Canary must deploy and produce readback before explicit confirmation')
            # Recheck every previously applied target before advancing a new batch.
            for target in plan['targets']:
                if target['status']=='applied':
                    try:self._rollout_probe(plan,target,require_desired=True)
                    except (ValueError,ConnectionError,OSError,KeyError,httpx.HTTPError,TypeError,AttributeError) as exc:return self._pause_rollout_failure(plan,target,exc,reviewer)
            canary_stage=plan['status']=='planned'
            if plan['status']=='waiting_canary_confirmation':
                plan.update(canary_confirmed=True,status='running')
                self._save_rollout(plan,'canary_confirmed',reviewer)
            selected=[row for row in plan['targets'] if row['status']=='pending' and (row['target_id'] in plan['canary_target_ids'] if canary_stage else row['target_id'] not in plan['canary_target_ids'])]
            if not canary_stage:selected=selected[:plan['batch_size']]
            for target in selected:
                try:
                    self._rollout_probe(plan,target)
                    previous=self.ledger(target['target_id']).active()
                    target.update(status='applying',previous_deployment_id=previous['deployment_id'] if previous else None,error=None)
                    plan['status']='running';self._save_rollout(plan,'target_apply_started',reviewer)
                    receipt=self.apply(target['target_id'],plan['release'],reviewer=reviewer,project=project)
                    target.update(deployment_id=receipt['deployment_id'],status='applied')
                    self._rollout_probe(plan,target,require_desired=True)
                    self._save_rollout(plan,'target_readback_verified',reviewer)
                except (ValueError,ConnectionError,OSError,KeyError,RuntimeError,httpx.HTTPError,TypeError,AttributeError) as exc:return self._pause_rollout_failure(plan,target,exc,reviewer)
            plan.update(status='waiting_canary_confirmation' if canary_stage else 'completed' if all(row['status']=='applied' for row in plan['targets']) else 'running',pause_reason=None)
            return self._save_rollout(plan,'canary_ready' if canary_stage else 'batch_finished',reviewer)

    def pause_rollout(self,identifier,*,expected_revision,reviewer,reason):
        with self._rollout_lock(identifier):
            plan=self._checked_rollout(identifier,expected_revision,reviewer)
            if not isinstance(reason,str) or not reason.strip():raise ValueError('A rollout pause reason is required')
            if plan['status'] in ('completed','rolled_back'):raise ValueError('A terminal rollout cannot be paused')
            plan.update(status='paused',pause_reason=reason.strip())
            return self._save_rollout(plan,'paused_by_reviewer',reviewer)

    def resume_rollout(self,identifier,*,expected_revision,reviewer,project):
        with self._rollout_lock(identifier):
            plan=self._checked_rollout(identifier,expected_revision,reviewer,project)
            if plan['status'] not in ('paused','running','rolling_back'):raise ValueError('This rollout is not paused or interrupted')
            for target in plan['targets']:
                if target['status']=='pending':continue
                try:
                    if plan.get('operation')=='rollback':
                        observed=self._rollout_probe(plan,target)
                        if target.get('deployment_id') and self._rollout_restored(plan,target,observed):continue
                        if target.get('rollback_deployment_id'):raise ValueError('Rollback receipt is no longer active; review separately')
                        self._rollout_probe(plan,target,require_desired=bool(target.get('deployment_id')))
                        target.update(status='applied' if target.get('deployment_id') else 'pending',error=None)
                        continue
                    observed=self._rollout_probe(plan,target,require_desired=bool(target.get('deployment_id')))
                    if not target.get('deployment_id'):
                        active=observed.get('active') or {}
                        if active.get('release',{}).get('rollout_id')==identifier:
                            target.update(deployment_id=active['deployment_id'])
                            self._rollout_probe(plan,target,require_desired=True)
                    target.update(status='applied' if target.get('deployment_id') else 'pending',error=None)
                except (ValueError,ConnectionError,OSError,KeyError,RuntimeError,httpx.HTTPError,TypeError,AttributeError) as exc:return self._pause_rollout_failure(plan,target,exc,reviewer)
            canaries=[row for row in plan['targets'] if row['target_id'] in plan['canary_target_ids']]
            if plan.get('operation')=='rollback':
                remaining=any(row.get('deployment_id') and row['status']!='rolled_back' for row in plan['targets'])
                plan.update(status='rolling_back' if remaining else 'rolled_back',pause_reason=None)
            else:plan.update(status='running' if plan['canary_confirmed'] else 'waiting_canary_confirmation' if all(row['status']=='applied' for row in canaries) else 'planned',pause_reason=None)
            return self._save_rollout(plan,'resumed_after_live_readback',reviewer)

    def rollback_rollout(self,identifier,*,expected_revision,reviewer,project):
        with self._rollout_lock(identifier):
            plan=self._checked_rollout(identifier,expected_revision,reviewer,project)
            # The ledger may commit before the plan publishes a target receipt.
            # Resolve that interrupted intent with fresh readback through resume
            # before choosing rollback targets or claiming a terminal result.
            if any(row['status']=='applying' or (not row.get('deployment_id') and row['status'] in ('failed','offline')) for row in plan['targets']):
                raise ValueError('Interrupted target apply requires explicit resume and live receipt adoption before rollout rollback')
            if plan['status']=='rolled_back':return plan
            if plan['status']=='paused' and plan.get('operation')=='rollback':raise ValueError('Explicitly resume paused rollback after live target readback')
            targets=[row for row in reversed(plan['targets']) if row.get('deployment_id') and row['status']!='rolled_back']
            if not targets:raise ValueError('Rollout has no acknowledged targets to rollback')
            plan.update(status='rolling_back',operation='rollback');self._save_rollout(plan,'rollback_started',reviewer)
            for target in targets[:plan['batch_size']]:
                try:
                    if not target.get('previous_deployment_id'):raise ValueError('Target has no previously acknowledged release for rollback')
                    active=self.ledger(target['target_id']).active()
                    if not active or active['deployment_id']!=target['deployment_id']:raise ValueError('Target active release changed after rollout; review separately before rollback')
                    self._rollout_probe(plan,target,require_desired=True)
                    target.update(status='rolling_back',rollback_requested_at=time.time())
                    self._save_rollout(plan,'target_rollback_started',reviewer)
                    receipt=self.rollback(target['target_id'],target['previous_deployment_id'],reviewer=reviewer,project=project)
                    target['rollback_deployment_id']=receipt['deployment_id']
                    observed=self.readback(target['target_id'])
                    target['readback']={'observed_at':time.time(),'runtime':observed.get('runtime'),'matches_active':observed.get('matches_active',False)}
                    if not self._rollout_restored(plan,target,observed):raise ValueError('Rollback target readback differs from committed receipt')
                    self._save_rollout(plan,'target_rollback_verified',reviewer)
                except (ValueError,ConnectionError,OSError,KeyError,RuntimeError,httpx.HTTPError,TypeError,AttributeError) as exc:return self._pause_rollout_failure(plan,target,exc,reviewer)
            remaining=any(row.get('deployment_id') and row['status']!='rolled_back' for row in plan['targets'])
            plan.update(status='rolling_back' if remaining else 'rolled_back',pause_reason=None)
            return self._save_rollout(plan,'rollback_finished',reviewer)


def package_archive(package):
    from backend.engine.flow_package_runtime import verify_flow_package
    verify_flow_package(package);manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'));stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in ['manifest.json',*(['parity_receipt.json'] if not manifest.get('runtime_acceptance_sha256') else []),*[row['path'] for row in manifest['files']]]:
            path=package/relative
            if path.is_symlink() or not path.resolve().is_relative_to(package.resolve()):raise ValueError('Release archive contains linked or escaping files')
            archive.write(path,relative)
    return stream.getvalue()
