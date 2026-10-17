"""Stdlib controls of actual passive framing/storage methods; auth is modeled.

The separate real-framework file proves original auth/SQLite/route coupling.
No backend/framework/model import, request generation or network here.
"""
import ast,asyncio,hashlib,json,os,tempfile,time,types,unittest
from pathlib import Path

PATH=Path(__file__).resolve().parents[1]/'engine/required_flow_http_capture.py'
MODULE=types.ModuleType('original_readback_source_controls');MODULE.__file__=str(PATH)
exec(compile(ast.parse(PATH.read_bytes()),str(PATH),'exec'),MODULE.__dict__)

class ReadbackSourceControls(unittest.IsolatedAsyncioTestCase):
    def attempt(self,headers=()):
        owner=types.SimpleNamespace(_current=lambda:None,attempt=None)
        owner.refusals=[]
        readbacks=MODULE._OriginalGETReadbacks(owner)
        scope={'type':'http','method':'GET','path':'/api/project/current','headers':headers}
        attempt=MODULE._OriginalGETAttempt(readbacks,types.SimpleNamespace(scope=scope),{})
        readbacks.attempts.append(attempt)
        return owner,readbacks,attempt

    def test_exact_two_path_families_only(self):
        self.assertTrue(MODULE._get_path('/api/project/current'))
        self.assertTrue(MODULE._get_path('/api/flowchart/pipelines/'+'1'*32))
        for path in('/api/project/current/','/api/project/current?x=1','/api/flowchart/pipelines/'+'A'*32,'/api/flowchart/pipelines/../x',None):
            self.assertFalse(MODULE._get_path(path))

    def test_declared_empty_is_not_claimed_receive(self):
        for headers in((),((b'content-length',b'0'),)):
            _,_,attempt=self.attempt(headers)
            self.assertTrue(attempt.declared_empty);self.assertEqual(attempt.request_receives,0)
        for headers in(((b'content-length',b'1'),),((b'content-length',b'0'),(b'content-length',b'0')),((b'transfer-encoding',b'chunked'),)):
            _,_,attempt=self.attempt(headers);self.assertFalse(attempt.declared_empty)

    async def test_exact_send_frames_and_no_receive_invocation(self):
        owner,_,attempt=self.attempt()
        for message in({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]},
                        {'type':'http.response.body','body':b'{"x":1}','more_body':False}):
            frame=attempt.send_attempt(message);attempt.sent(message,frame)
        self.assertEqual(bytes(attempt.response),b'{"x":1}');self.assertTrue(attempt.response_ended)
        self.assertEqual(attempt.request_receives,0);self.assertEqual(attempt.refusals,[]);self.assertEqual(owner.refusals,[])

    async def test_substituted_frame_refuses_only_this_GET(self):
        owner,_,attempt=self.attempt();message={'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]}
        frame=attempt.send_attempt(message);message['status']=201;attempt.sent(message,frame)
        self.assertIn('sent_frame_changed',attempt.refusals);self.assertEqual(owner.refusals,[])

    async def test_limit_retains_no_oversized_response_and_does_not_change_message(self):
        owner,_,attempt=self.attempt();message={'type':'http.response.body','body':b'x'*(MODULE.GET_LIMIT+1),'more_body':False}
        frame=attempt.send_attempt(message);attempt.sent(message,frame)
        self.assertIn('GET_response_limit',attempt.refusals);self.assertEqual(attempt.response,b'')
        self.assertEqual(len(message['body']),MODULE.GET_LIMIT+1);self.assertEqual(owner.refusals,[])

    async def test_original_ten_seconds_and_nonempty_observed_body_refuse(self):
        owner,_,attempt=self.attempt();attempt.started_monotonic-=11
        message={'type':'http.request','body':b'x','more_body':False};attempt.received(message)
        self.assertIn('GET_deadline',attempt.refusals);self.assertIn('GET_observed_body_not_empty',attempt.refusals)
        self.assertEqual(message['body'],b'x');self.assertEqual(owner.refusals,[])

    def test_original_method_path_and_query_data_cannot_drift(self):
        for key,value in(('method','POST'),('path','/other'),('query_string',b'changed=1')):
            _,_,attempt=self.attempt();attempt.activity.scope[key]=value;attempt._current()
            self.assertIn('GET_scope_changed',attempt.refusals)

    def test_provisional_requires_terminal_state_wrong_caller_refuses_and_lineage_is_exact(self):
        writes=[];owner=types.SimpleNamespace(attempt=object(),refusals=[],_write=lambda name,value:writes.append((name,dict(value))))
        readbacks=MODULE._OriginalGETReadbacks(owner);readbacks.lineage=('own','session-memory');readbacks.active_version=('4'*32,'f'*64)
        readbacks.provisional=[(('foreign','session-memory'),{'path':'/api/project/current','phase':'before_post'}),
            (readbacks.lineage,{'path':'/api/project/current','phase':'before_post'})]
        readbacks.promote();self.assertEqual(writes,[]);self.assertEqual(len(readbacks.provisional),2)
        readbacks.post_completed(owner.attempt,True,'a'*64)
        self.assertIsNone(readbacks.post_terminal_sha);self.assertEqual(writes,[])
        # Explicit modeled terminal state for this pure data-partition control.
        # Only the separate real-framework test proves original POST completion.
        readbacks.post_terminal_sha='a'*64;readbacks.promote()
        self.assertEqual(len(writes),1);self.assertEqual(writes[0][0],'readback-000.json');self.assertEqual(readbacks.provisional,[])
        readbacks.promote();self.assertEqual(len(writes),1)

    def test_actual_exclusive_private_writer_never_replaces_or_accepts_broad_names(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory).resolve();root.chmod(0o700)
            owner=MODULE._OriginalFlowHTTPCapture.__new__(MODULE._OriginalFlowHTTPCapture)
            owner.output=root;owner.output_identity=MODULE._private_directory(root);owner.refusals=[];owner._current=lambda:None
            first=owner._write('readback-000.json',{'one':1});self.assertEqual(len(first),64)
            path=root/'readback-000.json';before=path.read_bytes()
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertIsNone(owner._write('readback-000.json',{'two':2}));self.assertEqual(path.read_bytes(),before)
            self.assertIsNone(owner._write('../outside.json',{}));self.assertIsNone(owner._write('readback-008.json',{}))
            self.assertEqual(sorted(p.name for p in root.iterdir()),['readback-000.json'])

    def test_actual_saved_data_reader_is_bounded_regular_and_no_alias(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory).resolve();path=root/'record.json';path.write_bytes(b'{"one":1}')
            value,sha=MODULE._owned_small_json(path);self.assertEqual(value,{'one':1});self.assertEqual(sha,hashlib.sha256(path.read_bytes()).hexdigest())
            alias=root/'alias.json';alias.symlink_to(path)
            with self.assertRaises(ValueError):MODULE._owned_small_json(alias)
            path.write_bytes(b'x'*(MODULE.GET_LIMIT+1))
            with self.assertRaises(ValueError):MODULE._owned_small_json(path)
            fifo=root/'fifo.json';os.mkfifo(fifo,0o600)
            with self.assertRaises(ValueError):MODULE._owned_small_json(fifo)

    def test_after_POST_saved_record_requires_same_id_and_exact_original_SHA(self):
        writes=[];owner=types.SimpleNamespace(_write=lambda name,value:writes.append((name,dict(value))))
        readbacks=MODULE._OriginalGETReadbacks(owner);readbacks.lineage=('same',);readbacks.active_version=('4'*32,'a'*64);readbacks.post_terminal_sha='f'*64
        record={'path':'/api/flowchart/pipelines/'+'4'*32,'phase':'after_post','saved_version':'4'*32,'saved_record_sha256':'b'*64}
        readbacks.provisional=[(readbacks.lineage,record)];readbacks.promote();self.assertEqual(writes,[])
        readbacks.provisional=[(readbacks.lineage,{**record,'saved_record_sha256':'a'*64})];readbacks.promote();self.assertEqual(len(writes),1)

    def classified_owner(self):
        writes=[];admission=object();app=object();records={}
        owner=types.SimpleNamespace(_current=lambda:None,_write=lambda name,value:writes.append((name,dict(value))),admission=admission,
            selector=types.SimpleNamespace(app=app,nonce='1'*32),pid=os.getpid(),observer=types.SimpleNamespace(_records=records,_valid=lambda activity:True))
        return owner,MODULE.OriginalFlowExchanges(owner),writes

    def classified(self,owner,exchanges,path,number,delegate=None):
        activity=types.SimpleNamespace(scope={'type':'http','method':'POST','path':path,'app':owner.selector.app},token=number)
        row={'carrier':activity,'accepted':True,'identity':number,'entered_sequence':2*number,'released_sequence':2*number+1,
            'released':True,'normal_return':True,'completed':True}
        owner.observer._records[number]=row
        return exchanges.classify_original_POST(owner.admission,activity,delegate)

    def test_original_POST_class_counts_are_memory_only_until_terminal_and_never_store_other_paths(self):
        owner,exchanges,writes=self.classified_owner()
        wrapper=self.classified(owner,exchanges,'/api/training/start',1);wrapper.finish(True)
        other=self.classified(owner,exchanges,'/private/not-retained',2);other.finish(True);self.assertEqual(writes,[])
        exchanges.post_terminal_sha='a'*64  # MODELED terminal state, not an auth/POST proof.
        exchanges.publish_counts();self.assertEqual(len(writes),2)
        value=writes[-1][1];self.assertEqual(value['counts']['FORBIDDEN_TRAIN'],1);self.assertEqual(value['counts']['OTHER'],1)
        self.assertNotIn('/private/not-retained',json.dumps(value));self.assertFalse(value['authorization_inferred_from_counts'])

    def test_classified_wrapper_passes_original_messages_and_frame_by_identity(self):
        owner,exchanges,_=self.classified_owner();calls=[];frame=object()
        class Delegate:
            def received(self,message):calls.append(('receive',message))
            def send_attempt(self,message):calls.append(('attempt',message));return frame
            def sent(self,message,original):calls.append(('sent',message,original))
            def finish(self,normal):calls.append(('finish',normal))
        wrapper=self.classified(owner,exchanges,'/api/flowchart/run',1,Delegate());message={'one':'same'}
        wrapper.received(message);self.assertIs(wrapper.send_attempt(message),frame);wrapper.sent(message,frame);wrapper.finish(True)
        self.assertIs(calls[0][1],message);self.assertIs(calls[1][1],message);self.assertIs(calls[2][1],message);self.assertIs(calls[2][2],frame)

    def test_classification_bound_and_replay_do_not_create_more_than_128_rows(self):
        owner,exchanges,_=self.classified_owner()
        for number in range(1,130):self.classified(owner,exchanges,'/api/export/flow',number)
        self.assertEqual(len(exchanges.classified),128);self.assertTrue(exchanges.classification_overflow)

    async def test_exact_save10_export115_deadlines_have_no_reset(self):
        for path,budget in MODULE.EXCHANGE_SECONDS.items():
            owner=types.SimpleNamespace(_current=lambda:None,attempt=None);exchanges=MODULE.OriginalFlowExchanges(owner)
            attempt=MODULE._OriginalExchangeAttempt(exchanges,types.SimpleNamespace(scope={'path':path}),{})
            self.assertEqual(attempt.budget,budget);attempt.started_monotonic-=budget+1;attempt._current()
            self.assertIn('exchange_deadline',attempt.refusals)

if __name__=='__main__':unittest.main()
