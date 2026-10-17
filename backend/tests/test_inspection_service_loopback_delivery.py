"""S7-05: original durable outbox and real owned loopback transport.

The result producer is an explicit precomputed synthetic fixture. No model,
GPU, camera, GUI, external network, operational approval or 72-hour run.
Recipient job-id/body-hash dedup is a controlled receiver contract, not a
sender exactly-once guarantee or qualification of any real MES destination.
"""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import threading
import uuid

from PIL import Image
import backend.engine.inspection_service as service

EXPECTED_ROOT=os.environ.get('MV_S705_EXPECTED_SOURCE')
if EXPECTED_ROOT:
    assert Path(service.__file__).resolve()==Path(EXPECTED_ROOT).resolve()/'backend/engine/inspection_service.py'


def sha(value):
    return hashlib.sha256(value).hexdigest()


class Receiver:
    """An owned real TCP receiver; every acceptance and attempt is durable."""
    def __init__(self,database,path,mode,port=0):
        self.database=Path(database);self.path=path;self.mode=mode;self.errors=[]
        with sqlite3.connect(self.database)as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS attempts(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT,body_sha256 TEXT,
                body BLOB,action TEXT,response_code INTEGER,response BLOB);
                CREATE TABLE IF NOT EXISTS accepted(
                job_id TEXT PRIMARY KEY,body_sha256 TEXT NOT NULL,body BLOB NOT NULL,
                first_sequence INTEGER NOT NULL);''')
        owner=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.0'
            def log_message(self,*_args):pass
            def do_POST(self):
                try:
                    assert self.path==owner.path
                    length=int(self.headers['Content-Length']);assert 0<length<65536
                    body=self.rfile.read(length);assert len(body)==length
                    value=json.loads(body);job=value['job_id'];digest=sha(body)
                    assert type(job)is str and len(job)==32
                    with sqlite3.connect(owner.database)as db:
                        db.execute('BEGIN IMMEDIATE')
                        prior=db.execute('SELECT body_sha256 FROM accepted WHERE job_id=?',(job,)).fetchone()
                        conflict=prior is not None and prior[0]!=digest
                        status=503 if owner.mode=='503' else 409 if conflict else 200
                        response=json.dumps({'accepted':status==200,'job_id':job,
                                             'fixture_recipient_dedup':prior is not None}).encode()
                        action='HTTP503'if owner.mode=='503'else'accepted_then_lost_ACK'if owner.mode=='drop-ack'else'HTTP200'
                        cursor=db.execute('INSERT INTO attempts(job_id,body_sha256,body,action,response_code,response)VALUES(?,?,?,?,?,?)',
                                          (job,digest,body,action,status,response if owner.mode!='drop-ack'else None))
                        if status==200 and prior is None:
                            db.execute('INSERT INTO accepted VALUES(?,?,?,?)',(job,digest,body,cursor.lastrowid))
                    if owner.mode=='drop-ack':
                        # Interrupt only this handler's actual owned connection,
                        # after the recipient transaction is durably committed.
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    self.send_response(status);self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(response)));self.end_headers()
                    self.wfile.write(response);self.wfile.flush()
                except Exception as exc:
                    owner.errors.append(type(exc).__name__+': '+str(exc))
                    raise
        self.server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
        assert self.server.server_address[0]=='127.0.0.1'
        self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},
                                     name='S705-owned-loopback-receiver',daemon=False)
        self.thread.start()
        assert self.thread.is_alive()
        self.closed=False

    @property
    def url(self):return 'http://127.0.0.1:'+str(self.port)+self.path

    def close(self):
        if not self.closed:
            self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
            self.closed=True
            assert not self.thread.is_alive() and self.server.socket.fileno()==-1
            assert self.errors==[]

    def rows(self):
        with sqlite3.connect(self.database)as db:
            db.row_factory=sqlite3.Row
            return {name:[dict(r)for r in db.execute('SELECT * FROM '+name+' ORDER BY '+order)]
                    for name,order in [('attempts','sequence'),('accepted','first_sequence')]}


@contextmanager
def unavailable_owned_port(port):
    """Own this exact same TCP port without listening, preventing adoption.

    The kernel's actual connection refusal is exercised by original httpx.post;
    no mock, synthetic exception or unowned target is contacted.
    """
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        sock.bind(('127.0.0.1',port))
        assert sock.getsockname()==('127.0.0.1',port)
        yield
    finally:sock.close()


def prepare(tmp_path):
    image=tmp_path/'synthetic-source.png'
    with Image.new('RGB',(32,32),(64,96,128))as pixels:pixels.save(image)
    original=sha(image.read_bytes());state=tmp_path/'original-sender-state'
    store=service.InspectionStore(state,max_outstanding=2,max_attempts=3)
    key='S705-'+uuid.uuid4().hex
    job=store.enqueue(image,'file',image_id='synthetic-image-7',idempotency_key=key)
    assert store.enqueue(image,'file',image_id='synthetic-image-7',idempotency_key=key)==job
    row=store.claim();assert row['job_id']==job
    result={'final_verdict':'OK','fixture_scope':'precomputed_synthetic_delivery_only_no_model',
            'source_sequence':7,'roi_count':0,'crops':[],'execution_steps':[]}
    store.finish(job,result=result,require_delivery=True)
    current=store.get(job)
    assert current['state']=='delivery_pending'and current['verdict']=='REVIEW'and current['model_verdict']=='OK'
    return store,state,image,original,job,result,current['deadline_at']


def snapshot(store,job):
    with sqlite3.connect(store.database)as db:
        db.row_factory=sqlite3.Row
        deliveries=[dict(r)for r in db.execute('SELECT * FROM deliveries ORDER BY job_id')]
        events=[dict(r)for r in db.execute('SELECT * FROM events WHERE job_id=? ORDER BY event_id',(job,))]
    return {'job':store.get(job),'deliveries':deliveries,'events':events}


def review_failure(store,job,attempts,deadline,needle):
    current=snapshot(store,job)
    assert current['job']['state']=='delivery_error'and current['job']['verdict']=='REVIEW'
    assert current['job']['model_verdict']=='OK'and current['job']['deadline_at']==deadline
    if isinstance(needle,tuple):
        assert current['job']['error'].split(':',1)[0] in needle
    else:
        assert needle in current['job']['error']
    assert len(current['deliveries'])==1
    assert current['deliveries'][0]['state']=='failed'and current['deliveries'][0]['attempts']==attempts
    assert current['deliveries'][0]['last_error']==current['job']['error']
    return current


def send_original(store,job,url):
    row=store.claim_delivery();assert row is not None and row['job_id']==job
    assert store.claim_delivery()is None  # no second claim of the actual sending row
    service._deliver_job(store,row,url,None)  # unchanged real HTTP timeout=5


def final_reopen(state,image,original,job,result,deadline,attempts,expected_states):
    final=service.InspectionStore(state,max_outstanding=2,max_attempts=3)
    final.recover();current=snapshot(final,job)
    assert current['job']['state']=='completed'and current['job']['verdict']=='OK'
    assert current['job']['model_verdict']=='OK'and current['job']['result']==result
    assert current['job']['error']is None and current['job']['deadline_at']==deadline
    assert current['job']['attempts']==1 and current['job']['image_sha256']==original
    assert current['deliveries'][0]['state']=='sent'and current['deliveries'][0]['attempts']==attempts
    assert current['deliveries'][0]['last_error']is None
    assert [r['state']for r in current['events']]==expected_states
    assert [r['event_id']for r in current['events']]==list(range(1,len(expected_states)+1))
    assert len(final.list())==1 and final.pending_count()==0
    assert final.claim_delivery()is None and final.retry_delivery(job)is False
    assert sha(image.read_bytes())==original
    return current


def save_receipt(tmp_path,name,value):
    def safe(x):
        if isinstance(x,bytes):return {'utf8':x.decode(),'sha256':sha(x),'bytes':len(x)}
        if isinstance(x,dict):return {k:safe(v)for k,v in x.items()}
        if isinstance(x,list):return [safe(v)for v in x]
        return x
    root=Path(os.environ.get('MV_S705_RECEIPT_DIR',str(tmp_path)))
    root.mkdir(parents=True,exist_ok=True)
    with (root/(name+'.json')).open('x')as stream:
        json.dump(safe({**value,'original_httpx_timeout_seconds':5,'actual_model_inference':False,
                        'source_sequence':7,'external_target_qualified':False,'soak_72h_rerun':False,
                        'recipient_dedup_fixture_not_sender_exactly_once':True,'parent_credit':0}),stream,indent=2)
        stream.write('\n')


def test_real_503_unavailable_same_port_reconnect_and_durable_reopen(tmp_path):
    store,state,image,original,job,result,deadline=prepare(tmp_path)
    path='/owned-result/'+uuid.uuid4().hex;database=tmp_path/'receiver.sqlite3'
    first=Receiver(database,path,'503');port=first.port
    try:
        send_original(store,job,first.url)
        failed503=review_failure(store,job,1,deadline,'HTTP 503')
        assert len(first.rows()['accepted'])==0
    finally:first.close()
    with unavailable_owned_port(port):
        assert store.retry_delivery(job)is True
        assert store.get(job)['verdict']=='REVIEW'
        send_original(store,job,first.url)
        failed_offline=review_failure(store,job,2,deadline,('ConnectError','ConnectTimeout'))
    second=Receiver(database,path,'200',port=port)
    try:
        assert second.port==port
        reopened=service.InspectionStore(state,max_outstanding=2,max_attempts=3)
        reopened.recover()
        assert reopened.get(job)['state']=='delivery_pending'and reopened.get(job)['verdict']=='REVIEW'
        assert reopened.get(job)['deadline_at']==deadline
        send_original(reopened,job,second.url)
        receiver=second.rows()
        assert [r['sequence']for r in receiver['attempts']]==[1,2]
        assert [r['response_code']for r in receiver['attempts']]==[503,200]
        assert len(receiver['accepted'])==1
        assert len({r['body_sha256']for r in receiver['attempts']})==1
        accepted=json.loads(receiver['accepted'][0]['body'])
        assert accepted['job_id']==job and accepted['model_verdict']=='OK'
        assert accepted['image_sha256']==original and accepted['result']==result
        assert accepted['runtime_identity']is None
        final=final_reopen(state,image,original,job,result,deadline,3,
                          ['queued','running','delivery_pending','delivery_error','delivery_pending',
                           'delivery_error','delivery_pending','completed'])
    finally:second.close()
    save_receipt(tmp_path,'503-offline-reconnect',{'owned_host':'127.0.0.1','same_port':port,'path':path,
                 'failed503':failed503,'failed_offline':failed_offline,'receiver':receiver,'reopened_final':final,
                 'all_owned_receiver_threads_closed':True,'all_owned_receiver_sockets_closed':True})


def test_real_ack_loss_receiver_dedup_reconnect_and_sender_reopen(tmp_path):
    store,state,image,original,job,result,deadline=prepare(tmp_path)
    path='/owned-result/'+uuid.uuid4().hex;database=tmp_path/'receiver.sqlite3'
    first=Receiver(database,path,'drop-ack');port=first.port
    try:
        send_original(store,job,first.url)
        lost_ack=review_failure(store,job,1,deadline,'RemoteProtocolError')
        committed_before_ack=first.rows()
        assert len(committed_before_ack['accepted'])==1
        assert committed_before_ack['attempts'][0]['response']is None
    finally:first.close()
    second=Receiver(database,path,'200',port=port)
    try:
        reopened=service.InspectionStore(state,max_outstanding=2,max_attempts=3);reopened.recover()
        assert reopened.get(job)['state']=='delivery_pending'and reopened.get(job)['verdict']=='REVIEW'
        assert reopened.get(job)['deadline_at']==deadline
        send_original(reopened,job,second.url)
        receiver=second.rows()
        assert [r['sequence']for r in receiver['attempts']]==[1,2]
        assert len(receiver['accepted'])==1 and receiver['accepted']==committed_before_ack['accepted']
        assert len({r['body_sha256']for r in receiver['attempts']})==1
        assert json.loads(receiver['attempts'][1]['response'])['fixture_recipient_dedup']is True
        accepted=json.loads(receiver['accepted'][0]['body'])
        assert accepted['job_id']==job and accepted['model_verdict']=='OK'
        assert accepted['result']==result and accepted['image_sha256']==original
        final=final_reopen(state,image,original,job,result,deadline,2,
                          ['queued','running','delivery_pending','delivery_error','delivery_pending','completed'])
    finally:second.close()
    save_receipt(tmp_path,'ACK-loss-dedup-reconnect',{'owned_host':'127.0.0.1','same_port':port,'path':path,
                 'sender_REVIEW_after_recipient_commit':lost_ack,'recipient_committed_before_lost_ack':committed_before_ack,
                 'receiver_reopened_same_database':receiver,'reopened_final':final,
                 'all_owned_receiver_threads_closed':True,'all_owned_receiver_sockets_closed':True})
