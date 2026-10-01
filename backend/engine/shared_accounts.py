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

ROLES={'viewer','labeler','trainer','reviewer','owner'}

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

    def create_user(self,username,password,administrator=False):
        with self._db() as db:return self._create(db,username,password,administrator)

    def users(self):
        with self._db() as db:return [self._public(r) for r in db.execute('SELECT * FROM users ORDER BY username')]

    def login(self,username,password,ttl_seconds=28800):
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
            token=secrets.token_urlsafe(48);expires=now+ttl_seconds
            db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],expires))
            return {'token':token,'expires_at':expires,'user':self._public(row)}

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
            db.execute('INSERT OR REPLACE INTO selections VALUES(?,?)',(owner,identifier))

    def project_role(self,user_id,project_id):
        with self._db() as db:
            user=db.execute('SELECT administrator,disabled FROM users WHERE id=?',(user_id,)).fetchone()
            if not user or user['disabled']:return None
            if not db.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone():return None
            if user['administrator']:return 'owner'
            row=db.execute('SELECT role FROM members WHERE project_id=? AND user_id=?',(project_id,user_id)).fetchone()
            return row['role'] if row else None

    def set_membership(self,project_id,user_id,role,actor):
        if role not in ROLES:raise ValueError('Unknown project role')
        if self.project_role(actor,project_id)!='owner':raise ValueError('Project owner permission required')
        with self._db() as db:
            if not db.execute('SELECT id FROM users WHERE id=?',(user_id,)).fetchone():raise ValueError('User unavailable')
            db.execute('INSERT OR REPLACE INTO members VALUES(?,?,?)',(project_id,user_id,role))
            db.execute('INSERT INTO audit VALUES(?,?,?,?,?)',(uuid.uuid4().hex,time.time(),actor,'membership',json.dumps({'project':project_id,'user':user_id,'role':role})))

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
