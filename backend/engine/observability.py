"""Project-owned durable, redacted observations; local notifications default off."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time

from backend.engine.product_delivery import _root,redact_diagnostics

STATES={'accepted','queued','running','completed','failed','aborted','interrupted','error','delivery_pending','delivery_error','ready','stopped','unavailable','version_mismatch','changed'}
FAILURES={'failed','interrupted','error','delivery_error'}

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()

def local_metrics(root):
    disk=shutil.disk_usage(root)
    gpu={'available':False,'devices':[],'reason':'no_local_nvidia'}
    executable=shutil.which('nvidia-smi')
    if executable:
        try:
            reply=subprocess.run([executable,'--query-gpu=uuid,utilization.gpu,memory.used,memory.total','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=2,check=True)
            devices=[]
            for line in reply.stdout.strip().splitlines():
                identity,utilization,used,total=[v.strip() for v in line.split(',')]
                devices.append({'id':identity,'utilization_percent':int(utilization),'memory_used_mib':int(used),'memory_total_mib':int(total)})
            gpu={'available':bool(devices),'devices':devices,'reason':None if devices else 'no_devices'}
        except (OSError,ValueError,subprocess.SubprocessError):gpu['reason']='probe_unavailable'
    return {'disk':{'available':True,'free_bytes':disk.free,'total_bytes':disk.total},'gpu':gpu,'measured_at':time.time(),'measurement_host':'current_backend'}

class ObservabilityStore:
    def __init__(self,project):
        self.root=_root(project);self.project_id=project['id'];directory=self.root/'delivery'
        if directory.is_symlink():raise ValueError('Observability storage cannot follow links')
        directory.mkdir(exist_ok=True);self.path=directory/'observability.sqlite3'
        if self.path.is_symlink():raise ValueError('Observability database cannot follow links')
        with self._db() as db:
            db.executescript('''
             CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY,source_key TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,
              job_id TEXT,trace_id TEXT,event TEXT,state TEXT,at REAL,message TEXT,payload_json TEXT NOT NULL);
             CREATE INDEX IF NOT EXISTS logs_job ON logs(job_id,id);
             CREATE TABLE IF NOT EXISTS cursors(stream TEXT PRIMARY KEY,position INTEGER NOT NULL,anchor TEXT);
             CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
             CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,log_id INTEGER NOT NULL UNIQUE,
              job_id TEXT,reason TEXT NOT NULL,at REAL NOT NULL);
             CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,enabled INTEGER NOT NULL);
             INSERT OR IGNORE INTO policy VALUES(1,1,0);
             CREATE TABLE IF NOT EXISTS policy_history(revision INTEGER PRIMARY KEY,enabled INTEGER NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,at REAL NOT NULL);
            ''')

    @contextmanager
    def _db(self):
        if self.path.is_symlink():raise ValueError('Observability database cannot follow links')
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            with db:yield db
        finally:db.close()

    def policy(self):
        with self._db() as db:row=dict(db.execute('SELECT revision,enabled FROM policy WHERE id=1').fetchone())
        return {**row,'enabled':bool(row['enabled']),'delivery':'local_history','external_telemetry':False}

    def configure(self,enabled,*,expected_revision,actor,reason):
        if type(enabled) is not bool or type(expected_revision) is not int or expected_revision<1:raise ValueError('Invalid notification policy or revision')
        if not isinstance(actor,str) or not 1<=len(actor.strip())<=100 or not isinstance(reason,str) or not 1<=len(reason.strip())<=1000:raise ValueError('Record policy actor and reason')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM policy WHERE id=1').fetchone()
            if row['revision']!=expected_revision:raise ValueError('Notification policy revision changed; reload it')
            db.execute('UPDATE policy SET revision=revision+1,enabled=? WHERE id=1',(enabled,))
            db.execute('INSERT INTO policy_history VALUES(?,?,?,?,?)',(expected_revision+1,enabled,redact_diagnostics(actor.strip()),redact_diagnostics(reason.strip()),time.time()))
        return self.policy()

    def policy_history(self):
        with self._db() as db:return [dict(r) for r in db.execute('SELECT * FROM policy_history ORDER BY revision')]

    def _append(self,db,key,kind,job,event,state,at,message,payload,*,actionable=False):
        payload=redact_diagnostics(payload);message=redact_diagnostics(message or '')
        inserted=db.execute('INSERT OR IGNORE INTO logs(source_key,kind,job_id,trace_id,event,state,at,message,payload_json) VALUES(?,?,?,?,?,?,?,?,?)',
                            (key,kind,job,job or 'project:'+self.project_id,event,state,at,message,canonical(payload)))
        if inserted.rowcount and actionable and db.execute('SELECT enabled FROM policy WHERE id=1').fetchone()[0]:
            db.execute('INSERT INTO notifications(log_id,job_id,reason,at) VALUES(?,?,?,?)',(inserted.lastrowid,job,message or state,time.time()))

    def _inspection(self,db):
        source=self.root/'runtime_service'/'state'/'inspection_service.sqlite3'
        for candidate in (source,*source.parents):
            if candidate==self.root:break
            if candidate.is_symlink():raise ValueError('Inspection observations cannot follow links')
        if not source.is_file():return {'available':False,'outstanding':0,'states':{},'backlog':False}
        previous=db.execute('SELECT position,anchor FROM cursors WHERE stream=?',('inspection',)).fetchone()
        position=previous['position'] if previous else 0
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as inspection:
            inspection.row_factory=sqlite3.Row;inspection.execute('BEGIN')
            anchor=inspection.execute('SELECT * FROM events WHERE event_id=?',(position,)).fetchone() if position else None
            if position and (anchor is None or digest(dict(anchor))!=previous['anchor']):position=0
            rows=[dict(r) for r in inspection.execute('SELECT * FROM events WHERE event_id>? ORDER BY event_id LIMIT 501',(position,))]
            counts=dict(inspection.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state').fetchall())
            trace={}
            for row in rows[:500]:
                if row['state'] in {'completed','error','delivery_error'}:
                    job=inspection.execute('SELECT image_sha256,runtime_binding_json,result_json FROM jobs WHERE job_id=?',(row['job_id'],)).fetchone()
                    if job:
                        result=json.loads(job['result_json']) if job['result_json'] else {}
                        trace[row['event_id']]={'input_sha256':job['image_sha256'],'runtime_binding':json.loads(job['runtime_binding_json'] or '{}'),
                                                'execution_steps':result.get('execution_steps',[]),'final_verdict':result.get('final_verdict')}
        backlog=len(rows)>500
        for row in rows[:500]:
            self._append(db,'inspection:'+digest(row),'inspection',row['job_id'],row['state'],row['state'],row['created_at'],row['message'],trace.get(row['event_id'],{}),actionable=row['state'] in FAILURES)
        if rows:
            last=rows[min(499,len(rows)-1)]
            db.execute('INSERT INTO cursors VALUES(?,?,?) ON CONFLICT(stream) DO UPDATE SET position=excluded.position,anchor=excluded.anchor',('inspection',last['event_id'],digest(last)))
        return {'available':True,'states':counts,'outstanding':sum(counts.get(k,0) for k in ('queued','running','delivery_pending')),'backlog':backlog}

    def _training(self,db,training):
        if training is None:return {'available':False,'backlog':False}
        jobs,context,project_key=training
        stream='training:'+digest([jobs.ledger_id(),context.workspace_id,project_key])
        prior=db.execute('SELECT position FROM cursors WHERE stream=?',(stream,)).fetchone()
        rows,head,backlog=jobs.events_after(context.workspace_id,project_key,prior[0] if prior else 0,500)
        for row in rows:
            payload=json.loads(row['payload_json']) if row['payload_json'] else {}
            message=payload.get('reason') if isinstance(payload,dict) else None
            self._append(db,stream+':'+str(row['position']),row['kind'],row['job_id'],row['event'],row['to_state'],row['at_ns']/1e9,
                         message or row['event'],payload,actionable=row['to_state'] in FAILURES)
        db.execute('INSERT INTO cursors VALUES(?,?,NULL) ON CONFLICT(stream) DO UPDATE SET position=excluded.position',(stream,head))
        return {'available':True,'backlog':backlog}

    def collect(self,service,*,metrics=None,training=None):
        metrics=metrics if metrics is not None else local_metrics(self.root)
        runtime=service.get('runtime',{});active=(service.get('active') or {}).get('release',{})
        status=runtime.get('status','unavailable')
        if status=='ready' and (not active.get('manifest_sha256') or runtime.get('manifest_sha256')!=active.get('manifest_sha256')):status='version_mismatch'
        readiness=service.get('readiness')
        if status=='ready' and readiness is not None and readiness.get('status')!='ready':status='unavailable'
        if status not in {'ready','stopped','unavailable','version_mismatch'}:status='unavailable'
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE');queue=self._inspection(db);jobs=self._training(db,training)
            queue['capacity']=readiness.get('capacity') if readiness else None
            health={'service':status,'disk':'low' if metrics['disk']['available'] and metrics['disk']['free_bytes']<1024**3 else 'ok',
                    'queue':'full' if queue['capacity'] is not None and queue['outstanding']>=queue['capacity'] else 'available'}
            previous=db.execute("SELECT value FROM meta WHERE key='health'").fetchone()
            old=json.loads(previous[0]) if previous else None
            if old is None or old['value']!=health:
                revision=old['revision']+1 if old else 1
                self._append(db,'health:'+str(revision),'health',None,'health_changed',status,time.time(),
                             'Service readiness, disk or queue state changed',health,actionable=status in {'unavailable','version_mismatch'} or health['disk']=='low' or health['queue']=='full')
                db.execute("INSERT INTO meta VALUES('health',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical({'revision':revision,'value':health}),))
            value={'service':status,'readiness':{'available':readiness is not None,'status':readiness.get('status') if readiness else None},'metrics':redact_diagnostics(metrics),'queue':queue,'training_events':jobs,'external_telemetry':False,
                   'error_catalog':{'error':'Inspect the stored input/error and choose bounded retry or explicit replay.','delivery_error':'Check receiver ACK before explicit redelivery.','version_mismatch':'Confirm approved apply and its runtime ACK.','unavailable':'Check the owned service process and readiness response.'}}
            db.execute("INSERT INTO meta VALUES('snapshot',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical(value),))
        return {**value,'policy':self.policy()}

    def snapshot(self):
        with self._db() as db:row=db.execute("SELECT value FROM meta WHERE key='snapshot'").fetchone()
        return json.loads(row[0]) if row else {'external_telemetry':False,'observed':False}

    @staticmethod
    def _filters(limit,offset,job_id=None,state=None):
        if type(limit) is not int or not 1<=limit<=500 or type(offset) is not int or not 0<=offset<=10**9:raise ValueError('Invalid log page')
        if job_id is not None and (not isinstance(job_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',job_id)):raise ValueError('Invalid job/inspection identifier')
        if state is not None and state not in STATES:raise ValueError('Invalid log state')

    def logs(self,*,limit=50,offset=0,job_id=None,state=None):
        self._filters(limit,offset,job_id,state);clauses=[];values=[]
        if job_id is not None:clauses.append('job_id=?');values.append(job_id)
        if state is not None:clauses.append('state=?');values.append(state)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        with self._db() as db:
            db.execute('BEGIN');total=db.execute('SELECT COUNT(*) FROM logs'+where,values).fetchone()[0]
            rows=[dict(r) for r in db.execute('SELECT * FROM logs'+where+' ORDER BY id DESC LIMIT ? OFFSET ?',[*values,limit,offset])]
        for row in rows:row['payload']=json.loads(row.pop('payload_json'));row.pop('source_key')
        return {'items':rows,'total':total,'limit':limit,'offset':offset,'has_more':offset+len(rows)<total}

    def notifications(self,*,limit=50,offset=0):
        self._filters(limit,offset)
        with self._db() as db:
            db.execute('BEGIN');total=db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]
            rows=[dict(r) for r in db.execute('SELECT * FROM notifications ORDER BY id DESC LIMIT ? OFFSET ?',(limit,offset))]
        return {'items':rows,'total':total,'limit':limit,'offset':offset,'has_more':offset+len(rows)<total}
