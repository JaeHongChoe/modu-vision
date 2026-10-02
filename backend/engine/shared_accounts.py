"""Persistent accounts, hashed sessions and explicit shared-project memberships."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

from backend.contracts.authentication import PermissionDecision

ROLES={'viewer','labeler','trainer','reviewer','owner'}
ACTIONS={
    'project.read':ROLES, 'membership.change':{'owner'}, 'audit.read':{'owner'},
    'label.write':{'labeler','trainer','reviewer','owner'},
    'training.execute':{'trainer','reviewer','owner'},
    'review.approve':{'reviewer','owner'}, 'artifact.upload':{'labeler','trainer','reviewer','owner'},
    'artifact.manage':{'owner'}, 'fleet.emergency':{'owner'},
    'project.manage':{'owner'}, 'flow.execute':{'trainer','reviewer','owner'},
    'compute.execute':{'labeler','trainer','reviewer','owner'},
    'capture.register':{'labeler','trainer','reviewer','owner'},
    'fleet.emergency.request':ROLES, 'delivery.select':ROLES, 'delivery.preflight':ROLES,
}

class AccountStore:
    def __init__(self,path):
        self.path=Path(path)
        if self.path.is_symlink() or self.path.parent.is_symlink():raise ValueError('Account storage cannot be linked')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL,administrator INTEGER NOT NULL,disabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,path TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS members(project_id TEXT NOT NULL,user_id TEXT NOT NULL,role TEXT NOT NULL,PRIMARY KEY(project_id,user_id));
                CREATE TABLE IF NOT EXISTS selections(user_id TEXT PRIMARY KEY,project_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS failures(username TEXT PRIMARY KEY,count INTEGER NOT NULL,blocked_until REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,at REAL NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL);
            ''')
        self._migrate()
        os.chmod(self.path,0o600)

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except Exception:
            db.rollback();raise
        finally:db.close()

    @staticmethod
    def _password(password,salt):
        if not isinstance(password,str) or not 12<=len(password)<=1024:raise ValueError('Password must contain 12 to 1024 characters')
        return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32).hex()

    @staticmethod
    def _public(row):return {k:row[k] for k in ('id','username','administrator','disabled')}

    def _create(self,db,username,password,administrator):
        if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{3,80}',username):raise ValueError('Username must contain 3 to 80 safe characters')
        salt=secrets.token_hex(16);digest=self._password(password,salt);identifier=uuid.uuid4().hex
        try:db.execute('INSERT INTO users VALUES(?,?,?,?,?,0)',(identifier,username,salt,digest,int(administrator)))
        except sqlite3.IntegrityError as exc:raise ValueError('Username is unavailable') from exc
        return self._public(db.execute('SELECT * FROM users WHERE id=?',(identifier,)).fetchone())

    def bootstrap(self,username,password):
        with self._db() as db:
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:raise ValueError('Administrator is already configured')
            return self._create(db,username,password,True)

    def create_user(self,username,password,administrator=False,*,actor=None,session_token=None):
        with self._db() as db:
            if actor is not None:self._require_admin(db,actor,'account.create',session_token)
            created=self._create(db,username,password,administrator)
            if actor is not None:self._audit(db,actor,'account.create',None,'allowed',user_id=created['id'])
            return created

    def users(self):
        with self._db() as db:return [self._public(r) for r in db.execute('SELECT * FROM users ORDER BY username')]

    def login(self,username,password,ttl_seconds=28800,transport='bearer'):
        if transport not in {'bearer','cookie'}:raise ValueError('Unknown session transport')
        now=time.time()
        with self._db() as db:
            failure=db.execute('SELECT * FROM failures WHERE username=?',(username,)).fetchone()
            row=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
            blocked=failure is not None and failure['blocked_until']>now
            try:digest=self._password(password,row['salt'] if row else '00'*16)
            except ValueError:digest=''
            valid=bool(row and not row['disabled'] and not blocked and hmac.compare_digest(digest,row['password_hash']))
            if not valid:
                count=(failure['count'] if failure else 0)+1
                db.execute('INSERT OR REPLACE INTO failures VALUES(?,?,?)',(username,count,now+900 if count>=10 else 0))
                # Commit the rate limit before reporting a rejected request.
                db.commit()
                raise ValueError('Invalid credentials or account unavailable')
            db.execute('DELETE FROM failures WHERE username=?',(username,))
            return self._session(db,row,ttl_seconds,transport)

    def authenticate(self,token):
        if not isinstance(token,str) or len(token)>256:raise ValueError('Session unavailable')
        with self._db() as db:
            row=db.execute('SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires>? AND disabled=0',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
            if row is None:raise ValueError('Session expired or unavailable')
            return self._public(row)

    def logout(self,token):
        with self._db() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))

    def register_project(self,identifier,path,owner):
        path=str(Path(path).resolve())
        with self._db() as db:
            existing=db.execute('SELECT path FROM projects WHERE id=?',(identifier,)).fetchone()
            if existing and existing['path']!=path:raise ValueError('Project identity already belongs to another location')
            db.execute('INSERT OR IGNORE INTO projects VALUES(?,?)',(identifier,path))
            db.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(identifier,owner,'owner'))
            if not db.execute('SELECT 1 FROM member_revisions WHERE project_id=? AND user_id=?',(identifier,owner)).fetchone():self._bump_membership(db,identifier,owner)
            db.execute('INSERT OR REPLACE INTO selections VALUES(?,?)',(owner,identifier))

    @staticmethod
    def _role(db,user_id,project_id):
        user=db.execute('SELECT disabled FROM users WHERE id=?',(user_id,)).fetchone()
        if not user or user['disabled']:return None
        grant=db.execute('SELECT 1 FROM legacy_admin_grants JOIN projects ON projects.id=legacy_admin_grants.project_id WHERE user_id=? AND project_id=?',(user_id,project_id)).fetchone()
        if grant:return 'owner'
        row=db.execute('SELECT role FROM members JOIN projects ON projects.id=members.project_id WHERE user_id=? AND project_id=?',(user_id,project_id)).fetchone()
        return row['role'] if row else None

    def project_role(self,user_id,project_id):
        with self._db() as db:return self._role(db,user_id,project_id)

    def set_membership(self,project_id,user_id,role,actor,*,session_token=None):
        if role not in ROLES:raise ValueError('Unknown project role')
        # Preserve the existing public preflight; authority is checked again in
        # the mutation transaction, so an in-flight demotion cannot grant access.
        self.project_role(actor,project_id)
        with self._db() as db:
            self._require_owner(db,actor,project_id,'membership.change',session_token)
            if not db.execute('SELECT id FROM users WHERE id=? AND disabled=0',(user_id,)).fetchone():raise ValueError('User unavailable')
            previous=self._role(db,user_id,project_id)
            self._protect_owner(db,project_id,user_id,role)
            db.execute('DELETE FROM legacy_admin_grants WHERE project_id=? AND user_id=?',(project_id,user_id))
            db.execute('INSERT OR REPLACE INTO members VALUES(?,?,?)',(project_id,user_id,role))
            revision=self._bump_membership(db,project_id,user_id)
            self._audit(db,actor,'membership.change',project_id,'allowed',user_id=user_id,role=role,revision=revision,previous_effective_role=previous,current_effective_role=role)

    def select_project(self,user_id,project_id):
        if self.project_role(user_id,project_id) is None:raise ValueError('Project permission required')
        with self._db() as db:db.execute('INSERT OR REPLACE INTO selections VALUES(?,?)',(user_id,project_id))

    def project_for(self,user_id,project_id=None):
        if project_id is None:
            with self._db() as db:
                selected=db.execute('SELECT project_id FROM selections WHERE user_id=?',(user_id,)).fetchone()
            project_id=selected['project_id'] if selected else None
        if project_id is None:return None
        if self.project_role(user_id,project_id) is None:raise ValueError('Project permission required')
        with self._db() as db:return dict(db.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone())

    def projects_for(self,user_id):
        with self._db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM projects ORDER BY id')]
        return [{**r,'role':self.project_role(user_id,r['id'])} for r in rows if self.project_role(user_id,r['id'])]

    def project_members(self,project_id,actor):
        """Only project members may read its assignable identities and roles."""
        if self.project_role(actor,project_id) is None:raise ValueError('Project permission required')
        with self._db() as db:
            rows=db.execute('SELECT users.id,users.username,members.role FROM members JOIN users ON users.id=members.user_id WHERE members.project_id=? AND users.disabled=0 ORDER BY users.username',(project_id,)).fetchall()
            return [{'id':row['id'],'name':row['username'],'role':row['role']} for row in rows]

    def _migrate(self):
        """Additive migration preserves password hashes, session expiry and roles."""
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS legacy_admin_grants(project_id TEXT NOT NULL,user_id TEXT NOT NULL,PRIMARY KEY(project_id,user_id));
                CREATE TABLE IF NOT EXISTS account_meta(name TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS member_revisions(project_id TEXT NOT NULL,user_id TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(project_id,user_id));
                CREATE TABLE IF NOT EXISTS permission_audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,at REAL NOT NULL,workspace_id TEXT NOT NULL,project_id TEXT,actor_id TEXT NOT NULL,action TEXT NOT NULL,decision TEXT NOT NULL,details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS external_identities(provider TEXT NOT NULL,issuer TEXT NOT NULL,subject TEXT NOT NULL,user_id TEXT NOT NULL,PRIMARY KEY(provider,issuer,subject));
                CREATE TABLE IF NOT EXISTS oidc_pending(state_hash TEXT PRIMARY KEY,provider TEXT NOT NULL,nonce TEXT NOT NULL,verifier TEXT NOT NULL,origin TEXT NOT NULL,expires REAL NOT NULL);
            ''')
            # executescript ends the prior transaction; serialize the additive
            # schema checks and migration journal before inspecting columns.
            db.execute('BEGIN IMMEDIATE')
            columns={row['name'] for row in db.execute('PRAGMA table_info(sessions)')}
            pending_columns={row['name'] for row in db.execute('PRAGMA table_info(oidc_pending)')}
            if 'browser_hash' not in pending_columns:
                db.execute("ALTER TABLE oidc_pending ADD COLUMN browser_hash TEXT NOT NULL DEFAULT ''")
            for name,declaration in [('transport',"TEXT NOT NULL DEFAULT 'bearer'"),('csrf_hash',"TEXT NOT NULL DEFAULT ''")]:
                if name not in columns:db.execute(f'ALTER TABLE sessions ADD COLUMN {name} {declaration}')
            db.execute("INSERT OR IGNORE INTO account_meta VALUES('workspace_id',?)",(uuid.uuid4().hex,))
            db.execute("INSERT OR IGNORE INTO account_meta VALUES('organization_id',?)",(uuid.uuid4().hex,))
            if not db.execute("SELECT 1 FROM account_meta WHERE name='schema_version'").fetchone():
                # Preserve literal memberships AND prior effective owner access.
                # Only identities/locations proven to predate this migration get
                # this explicit grant. Future admins/projects never inherit it.
                legacy=db.execute('SELECT projects.id AS project_id,users.id AS user_id FROM projects CROSS JOIN users WHERE users.administrator=1 AND users.disabled=0').fetchall()
                for row in legacy:
                    db.execute('INSERT INTO legacy_admin_grants VALUES(?,?)',(row['project_id'],row['user_id']))
                    db.execute("INSERT OR IGNORE INTO members VALUES(?,?,'owner')",(row['project_id'],row['user_id']))
                    self._audit(db,row['user_id'],'membership.migrate',row['project_id'],'allowed',reason='preserved_legacy_administrator_authority',previous_effective_role='owner',current_effective_role='owner')
                db.execute("INSERT INTO account_meta VALUES('schema_version','2')")
            db.execute('INSERT OR IGNORE INTO member_revisions SELECT project_id,user_id,1 FROM members')

    def bind_workspace(self,workspace_id):
        """Bind account metadata to the server's persistent context registry."""
        with self._db() as db:
            old=db.execute("SELECT value FROM account_meta WHERE name='context_workspace_id'").fetchone()
            if old and old['value']!=workspace_id:raise ValueError('Account workspace differs from the configured server')
            if not old:
                previous=db.execute("SELECT value FROM account_meta WHERE name='workspace_id'").fetchone()['value']
                # Before the first server binding these are account-local
                # migration records, not previously emitted team events. Keep
                # that original identity as an alias and bind the journal once.
                db.execute("INSERT OR IGNORE INTO account_meta VALUES('account_store_id',?)",(previous,))
                db.execute('UPDATE permission_audit SET workspace_id=? WHERE workspace_id=?',(workspace_id,previous))
            db.execute("INSERT OR IGNORE INTO account_meta VALUES('context_workspace_id',?)",(workspace_id,))
            db.execute("UPDATE account_meta SET value=? WHERE name='workspace_id'",(workspace_id,))

    def metadata(self):
        with self._db() as db:return dict((r['name'],r['value']) for r in db.execute('SELECT * FROM account_meta'))

    @staticmethod
    def _bump_membership(db,project_id,user_id):
        db.execute('INSERT INTO member_revisions VALUES(?,?,1) ON CONFLICT(project_id,user_id) DO UPDATE SET revision=revision+1',(project_id,user_id))
        return db.execute('SELECT revision FROM member_revisions WHERE project_id=? AND user_id=?',(project_id,user_id)).fetchone()['revision']

    @staticmethod
    def _audit(db,actor,action,project,decision,**details):
        workspace=db.execute("SELECT value FROM account_meta WHERE name='workspace_id'").fetchone()['value']
        identifier=uuid.uuid4().hex
        db.execute('INSERT INTO permission_audit(id,at,workspace_id,project_id,actor_id,action,decision,details) VALUES(?,?,?,?,?,?,?,?)',
                   (identifier,time.time(),workspace,project,actor,action,decision,json.dumps(details)))
        # Keep legacy audit history and its table contract available.
        db.execute('INSERT INTO audit VALUES(?,?,?,?,?)',(identifier,time.time(),actor,action,json.dumps({'project':project,'decision':decision,**details})))

    def _require_session(self,db,actor,token,action,project):
        # Internal administration calls retain their explicit trusted boundary;
        # every HTTP mutation supplies the authenticated session token.
        if token is None:return
        found=db.execute('SELECT 1 FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND user_id=? AND expires>? AND disabled=0',
            (hashlib.sha256(token.encode()).hexdigest(),actor,time.time())).fetchone()
        if not found:
            self._audit(db,actor,action,project,'denied',reason='session_unavailable')
            db.commit()
            raise ValueError('Session expired or unavailable')

    def _require_owner(self,db,actor,project,action,session_token=None):
        self._require_session(db,actor,session_token,action,project)
        if self._role(db,actor,project)!='owner':
            self._audit(db,actor,action,project,'denied',reason='owner_required')
            db.commit()  # A rejected mutation must retain its denial receipt.
            raise ValueError('Project owner permission required')

    def _require_admin(self,db,actor,action,session_token=None):
        self._require_session(db,actor,session_token,action,None)
        row=db.execute('SELECT administrator,disabled FROM users WHERE id=?',(actor,)).fetchone()
        if not row or row['disabled'] or not row['administrator']:
            self._audit(db,actor,action,None,'denied',reason='administrator_required')
            db.commit()
            raise ValueError('Server administrator permission required')

    def _protect_owner(self,db,project,user,replacement):
        if self._role(db,user,project)=='owner' and replacement!='owner':
            count=db.execute("SELECT count(*) FROM users WHERE disabled=0 AND (EXISTS(SELECT 1 FROM members WHERE members.user_id=users.id AND project_id=? AND role='owner') OR EXISTS(SELECT 1 FROM legacy_admin_grants WHERE legacy_admin_grants.user_id=users.id AND project_id=?))",(project,project)).fetchone()[0]
            if count<=1:raise ValueError('The last enabled project owner cannot be removed')

    def remove_membership(self,project_id,user_id,actor,*,session_token=None):
        with self._db() as db:
            self._require_owner(db,actor,project_id,'membership.remove',session_token)
            previous=self._role(db,user_id,project_id)
            self._protect_owner(db,project_id,user_id,None)
            db.execute('DELETE FROM legacy_admin_grants WHERE project_id=? AND user_id=?',(project_id,user_id))
            db.execute('DELETE FROM members WHERE project_id=? AND user_id=?',(project_id,user_id))
            db.execute('DELETE FROM selections WHERE project_id=? AND user_id=?',(project_id,user_id))
            revision=self._bump_membership(db,project_id,user_id)
            self._audit(db,actor,'membership.remove',project_id,'allowed',user_id=user_id,revision=revision,previous_effective_role=previous,current_effective_role=None)

    def set_disabled(self,user_id,disabled,actor,*,session_token=None):
        with self._db() as db:
            self._require_admin(db,actor,'account.disable',session_token)
            row=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
            if not row:raise ValueError('User unavailable')
            if disabled and not row['disabled']:
                if row['administrator'] and db.execute('SELECT count(*) FROM users WHERE administrator=1 AND disabled=0').fetchone()[0]<=1:
                    raise ValueError('The last enabled server administrator cannot be disabled')
                for project in db.execute("SELECT project_id FROM members WHERE user_id=? AND role='owner' UNION SELECT project_id FROM legacy_admin_grants WHERE user_id=?",(user_id,user_id)).fetchall():
                    self._protect_owner(db,project['project_id'],user_id,None)
            db.execute('UPDATE users SET disabled=? WHERE id=?',(int(disabled),user_id))
            if disabled:db.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
            self._audit(db,actor,'account.disable',None,'allowed',user_id=user_id,disabled=disabled)
            return self._public(db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone())

    def revoke_sessions(self,user_id,actor,*,session_token=None):
        with self._db() as db:
            self._require_session(db,actor,session_token,'session.revoke',None)
            if user_id!=actor:self._require_admin(db,actor,'session.revoke',session_token)
            if not db.execute('SELECT id FROM users WHERE id=? AND disabled=0',(actor,)).fetchone():raise ValueError('Account unavailable')
            db.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
            self._audit(db,actor,'session.revoke',None,'allowed',user_id=user_id)

    def enroll_administrator(self,project_id,actor,reason,*,session_token=None):
        """Explicit recovery/enrollment, visible in the project's scoped audit."""
        if not isinstance(reason,str) or not 8<=len(reason.strip())<=1000:raise ValueError('Enrollment requires a reason')
        with self._db() as db:
            self._require_admin(db,actor,'membership.enroll',session_token)
            if not db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():raise ValueError('Project unavailable')
            previous=self._role(db,actor,project_id)
            db.execute('DELETE FROM legacy_admin_grants WHERE project_id=? AND user_id=?',(project_id,actor))
            db.execute("INSERT OR REPLACE INTO members VALUES(?,?,'owner')",(project_id,actor))
            revision=self._bump_membership(db,project_id,actor)
            self._audit(db,actor,'membership.enroll',project_id,'allowed',reason=reason.strip(),revision=revision,previous_effective_role=previous,current_effective_role='owner')

    def audit_events(self,project,actor,after=0,limit=100,*,session_token=None):
        with self._db() as db:
            self._require_owner(db,actor,project,'audit.read',session_token)
            rows=db.execute('SELECT * FROM permission_audit WHERE project_id=? AND seq>? ORDER BY seq LIMIT ?',(project,after,min(max(limit,1),200))).fetchall()
            return [{**dict(row),'details':json.loads(row['details'])} for row in rows]

    def authorize(self,actor,action,project,resource_revision=None,*,session_token=None,revision_lookup=None):
        """Fresh decision; resource verification requires a trusted route lookup.

        Middleware never labels an echoed HTTP revision as verified. Resource
        owners supply their real lookup, or retain their existing peer checks.
        """
        with self._db() as db:
            role=self._role(db,actor,project)
            revision=db.execute('SELECT revision FROM member_revisions WHERE project_id=? AND user_id=?',(project,actor)).fetchone()
            allowed=role in ACTIONS.get(action,set())
            if action=='delivery.preflight' and role!='owner':
                admin=db.execute('SELECT administrator FROM users WHERE id=? AND disabled=0',(actor,)).fetchone()
                allowed=allowed and bool(admin and admin['administrator'])
            reason='allowed' if allowed else 'role_or_membership_required'
            if session_token is not None:
                found=db.execute('SELECT 1 FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND user_id=? AND expires>? AND disabled=0',
                    (hashlib.sha256(session_token.encode()).hexdigest(),actor,time.time())).fetchone()
                if not found:allowed=False;reason='session_unavailable'
            def valid_revision(value):
                return (type(value) is int and value>0) or (type(value) is str and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value)))
            verified=False
            if resource_revision is not None:
                if revision_lookup is None:allowed=False;reason='resource_revision_unverified'
                else:
                    try:actual=revision_lookup()
                    except (ValueError,OSError,KeyError):actual=None
                    verified=valid_revision(resource_revision) and valid_revision(actual) and type(actual) is type(resource_revision) and actual==resource_revision
                    if not verified:allowed=False;reason='resource_revision_changed'
            workspace=db.execute("SELECT value FROM account_meta WHERE name='workspace_id'").fetchone()['value']
            return PermissionDecision(allowed,reason,actor,action,workspace,project,role,
                                      revision['revision'] if revision else None,resource_revision,verified)

    def _session(self,db,row,ttl_seconds,transport):
        token=secrets.token_urlsafe(48);expires=time.time()+ttl_seconds
        csrf=secrets.token_urlsafe(32) if transport=='cookie' else ''
        db.execute('INSERT INTO sessions(token_hash,user_id,expires,transport,csrf_hash) VALUES(?,?,?,?,?)',
            (hashlib.sha256(token.encode()).hexdigest(),row['id'],expires,transport,hashlib.sha256(csrf.encode()).hexdigest() if csrf else ''))
        result={'token':token,'expires_at':expires,'user':self._public(row)}
        if csrf:result['csrf_token']=csrf
        return result

    def cookie_session(self,token,csrf=None):
        account=self.authenticate(token)
        with self._db() as db:
            row=db.execute('SELECT transport,csrf_hash FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if not row or row['transport']!='cookie':raise ValueError('Browser session unavailable')
            if csrf is not None and (not csrf or not hmac.compare_digest(row['csrf_hash'],hashlib.sha256(csrf.encode()).hexdigest())):
                raise ValueError('Browser CSRF verification required')
        return account

    def bind_external_identity(self,provider,issuer,subject,user_id,actor,*,session_token=None):
        if not subject or len(subject)>256:raise ValueError('Invalid external identity')
        with self._db() as db:
            self._require_admin(db,actor,'identity.enroll',session_token)
            if not db.execute('SELECT 1 FROM users WHERE id=? AND disabled=0',(user_id,)).fetchone():raise ValueError('User unavailable')
            try:db.execute('INSERT INTO external_identities VALUES(?,?,?,?)',(provider,issuer,subject,user_id))
            except sqlite3.IntegrityError as exc:raise ValueError('External identity already enrolled') from exc
            self._audit(db,actor,'identity.enroll',None,'allowed',provider=provider,user_id=user_id)

    def begin_oidc(self,provider,origin):
        state=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
        browser=secrets.token_urlsafe(32)
        with self._db() as db:
            db.execute('DELETE FROM oidc_pending WHERE expires<=?',(time.time(),))
            db.execute('INSERT INTO oidc_pending(state_hash,provider,nonce,verifier,origin,expires,browser_hash) VALUES(?,?,?,?,?,?,?)',(hashlib.sha256(state.encode()).hexdigest(),provider,nonce,verifier,origin,time.time()+300,hashlib.sha256(browser.encode()).hexdigest()))
        return state,nonce,verifier,browser

    def consume_oidc(self,state,origin,browser):
        with self._db() as db:
            digest=hashlib.sha256(state.encode()).hexdigest()
            row=db.execute('SELECT * FROM oidc_pending WHERE state_hash=? AND expires>? AND origin=? AND browser_hash=?',(digest,time.time(),origin,hashlib.sha256(browser.encode()).hexdigest())).fetchone()
            if not row:raise ValueError('OIDC state expired or unavailable')
            db.execute('DELETE FROM oidc_pending WHERE state_hash=?',(digest,))
            return dict(row)

    def external_session(self,provider,identity):
        with self._db() as db:
            row=db.execute('SELECT users.* FROM external_identities JOIN users ON users.id=external_identities.user_id WHERE provider=? AND issuer=? AND subject=? AND disabled=0',
                (provider,identity.issuer,identity.subject)).fetchone()
            if not row:raise ValueError('External identity requires explicit account enrollment')
            return self._session(db,row,28800,'cookie')

    def record_decision(self,decision):
        """Record authorization, distinct from the operation's final outcome."""
        with self._db() as db:
            self._audit(db,decision.actor_id,decision.action,decision.project_id,
                        'allowed' if decision.allowed else 'denied',reason=decision.reason,
                        membership_revision=decision.membership_revision,
                        resource_revision=decision.resource_revision,
                        resource_revision_verified=decision.resource_revision_verified)
