"""Original middleware/observer/admission/carrier ASGI source controls.

Controlled source loading uses stdlib; pytest marks this external replay.
The app state/bootstrap cache, graph request and
downstream handler are explicit controls; no FastAPI, model, network or GPU.
"""
import ast, asyncio, builtins, copy, importlib.util, json, os, sys, tempfile, types, unittest
from pathlib import Path
from backend.tests.private_qa_fixtures import private_qa_root

# External qualification is explicit; absence is a collection failure, never a skip.
J = private_qa_root()
import pytest
pytestmark = pytest.mark.private_qualification

HERE=Path(__file__).resolve(); CANDIDATE=HERE.parents[1]
ORIGINAL=Path(os.environ.get('MV_HTTP_ORIGINAL_ROOT',str(J/'Required-flow-runtime-snapshot-current-root733-01')))
GRAPH=json.loads((J/'S7-02-installed-retained-rotation-segmentation-fixture-Source01/original-graph.json').read_bytes())

def load(path,name,mapping,*,only=None):
 module=types.ModuleType(name);module.__file__=str(path);sys.modules[name]=module
 base_import=builtins.__import__
 def original_import(name,*args,**kwargs):
  if name in mapping:return mapping[name]
  return base_import(name,*args,**kwargs)
 module.__dict__['__builtins__']={**vars(builtins),'__import__':original_import}
 tree=ast.parse(path.read_bytes())
 if only is not None:tree.body=[n for n in tree.body if isinstance(n,ast.ClassDef)and n.name in only]
 exec(compile(tree,str(path),'exec'),module.__dict__)
 return module

class OriginalASGIControls(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.root.chmod(0o700)
  self.nonce='1'*32;home=self.root/'.application-runtime-homes'/self.nonce;home.mkdir(mode=0o700,parents=True);home.parent.chmod(0o700)
  self.mapping={};self.h=load(ORIGINAL/'backend/engine/application_launch_handshake.py','http_control_handshake',self.mapping)
  self.o=load(ORIGINAL/'backend/engine/original_request_activity.py','http_control_observer',self.mapping)
  self.current=load(ORIGINAL/'backend/engine/original_current_project_request.py','http_control_current',self.mapping)
  self.p=load(CANDIDATE/'engine/required_flow_passive_capture.py','http_control_passive',self.mapping)
  self.http=load(CANDIDATE/'engine/required_flow_http_capture.py','http_control_receipt',self.mapping)
  self.main=load(Path(os.environ.get('MV_HTTP_MAIN_SOURCE',str(CANDIDATE/'main.py'))),'http_control_main',self.mapping,only={'OwnedWorkAdmissionMiddleware'})
  self.mapping.update({'backend.engine.application_launch_handshake':self.h,'backend.engine.original_request_activity':self.o,'backend.engine.original_current_project_request':self.current,'backend.engine.required_flow_passive_capture':self.p,'backend.engine.required_flow_http_capture':self.http,'backend.main':self.main})
  self.mapping['backend.engine']=types.SimpleNamespace(application_launch_handshake=self.h)
  self.admission=self.h.BackendWorkAdmission(activity_observer=self.o.OriginalRequestActivityObserver())
  self.app=types.SimpleNamespace(state=types.SimpleNamespace(owned_work_admission=self.admission,api_token='a'*64,accounts=None))
  class Auth:
   async def __call__(self,*args):return None
  self.selector=self.p._OriginalRequiredFlowSelector(self.app,lambda:self.admission,Auth,ORIGINAL)
  self.app.state.required_retained_flow_capture=self.selector
  self.h._CACHE={'root':self.root,'proof':{'nonce':self.nonce},'admission':self.admission,'context':self.h._context(),'ready':True}
  self.owner=self.http.bind_original_flow_http_capture(self.selector)
  self.assertIsNotNone(self.owner);self.output=home/'required-flow-http'
  self.scope={'type':'http','method':'POST','path':'/api/flowchart/run','query_string':b'','headers':[(b'x-vision-token',b'a'*64)],'app':self.app}
  self.request={'image_path':'/explicit-source-control/image.png','image_id':'control-image','pipeline':GRAPH,'project_id':'control-project','execution_target':'selected_compute','device':'cuda','compute_profile_id':self.p.RETAINED_PROFILE_ID}
  self.body=json.dumps(self.request,sort_keys=True,separators=(',',':')).encode()
  self.response=json.dumps({'status':'success','source_control_not_GPU':True}).encode()
  self.models=[{'job_id':j,'task':t,'checkpoint_sha256':s}for j,t,s in self.p.RETAINED_MODELS]
  self.received=[];self.forwarded=[]
 def tearDown(self):self.tmp.cleanup()

 async def run_original(self,*,chunks=None,select=True,scope=None,send_error=False,handler_error=False,late=False,drift=False,copy_carrier=False):
  scope=self.scope if scope is None else scope
  pieces=[self.body]if chunks is None else chunks
  messages=[{'type':'http.request','body':body,'more_body':n<len(pieces)-1}for n,body in enumerate(pieces)]
  original_messages=list(messages)
  async def receive():
   value=messages.pop(0);self.received.append(value);return value
  async def send(value):
   if send_error:raise BrokenPipeError('explicit source control')
   self.forwarded.append(value)
   if drift:value['body']=b'changed by modeled transport'
  async def downstream(scope,receive,send):
   for value in original_messages:self.assertIs(await receive(),value)
   if select:
    request=types.SimpleNamespace(scope=scope,app=self.app)
    carrier=self.p._OriginalRequiredFlowRequest(self.selector,request);self.selector.issued=carrier
    if copy_carrier:carrier=copy.copy(carrier)
    carrier.consume(self.p.RETAINED_PROFILE_ID,self.p.RETAINED_EXECUTION_PROFILE_SHA,GRAPH,self.p.RETAINED_IMAGE_SHA,self.models)
   if late:self.owner.attempt.started_monotonic-=61
   if handler_error:raise RuntimeError('original modeled handler failure')
   await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]})
   await send({'type':'http.response.body','body':self.response[:8],'more_body':True})
   await send({'type':'http.response.body','body':self.response[8:],'more_body':False})
  middleware=self.main.OwnedWorkAdmissionMiddleware(downstream,self.admission)
  await middleware(scope,receive,send)
  return original_messages

 def terminal(self):return json.loads((self.output/'terminal.json').read_bytes())
 def assert_private(self):
  self.assertEqual(self.output.stat().st_mode&0o777,0o700)
  for name in('prepared.json','terminal.json'):
   p=self.output/name;self.assertEqual(p.stat().st_mode&0o777,0o600);self.assertEqual(p.stat().st_nlink,1);self.assertFalse(p.is_symlink())

 async def test_original_chunked_receive_send_once_with_real_observer_and_carrier(self):
  original=await self.run_original(chunks=[self.body[:17],self.body[17:]])
  self.assertEqual(self.received,original);self.assertEqual(len(self.forwarded),3)
  row=self.terminal();self.assertTrue(row['complete']);self.assertEqual(row['refusals'],[])
  import base64
  self.assertEqual(base64.b64decode(row['request_base64'],validate=True),self.body)
  self.assertEqual(base64.b64decode(row['response_base64'],validate=True),self.response)
  self.assertEqual(row['request_receives'],2);self.assertEqual(row['successful_sends'],3)
  self.assertEqual(row['status'],200);self.assertEqual(row['capture_nonce'],self.selector.nonce)
  self.assertEqual(self.admission._active,0);self.assertIn('uncovered_request_protocol',self.admission._unsupported)
  self.assertFalse(row['lease_authority']);self.assertFalse(row['parent_credit']);self.assert_private()
  self.assertNotIn(b'a'*64,(self.output/'terminal.json').read_bytes())

 async def test_missing_consumed_carrier_records_refusal_without_body(self):
  await self.run_original(select=False);row=self.terminal();self.assertFalse(row['complete']);self.assertNotIn('request_base64',row);self.assertIn('original_carrier_not_consumed',row['refusals']);self.assertEqual(len(self.forwarded),3)
 async def test_original_send_failure_and_handler_exception_propagate_unchanged(self):
  with self.assertRaises(BrokenPipeError):await self.run_original(send_error=True)
  row=self.terminal();self.assertFalse(row['complete']);self.assertNotIn('response_base64',row);self.assertEqual(self.admission._active,0)
 async def test_original_handler_failure_keeps_its_exception_and_no_body(self):
  with self.assertRaisesRegex(RuntimeError,'original modeled handler failure'):await self.run_original(handler_error=True)
  self.assertFalse(self.terminal()['complete']);self.assertEqual(self.forwarded,[])
 async def test_private_request_limit_refuses_capture_without_changing_business_response(self):
  await self.run_original(chunks=[b'x'*(self.http.REQUEST_LIMIT+1)])
  row=self.terminal();self.assertIn('request_limit',row['refusals']);self.assertFalse(row['complete']);self.assertNotIn('request_base64',row);self.assertEqual(len(self.forwarded),3)
 async def test_private_response_limit_is_only_a_capture_refusal(self):
  # The private data ceiling changes no transport or product size limit.
  self.response=b'x'*(self.http.RESPONSE_LIMIT+1)
  await self.run_original();row=self.terminal();self.assertIn('response_limit',row['refusals']);self.assertFalse(row['complete']);self.assertNotIn('response_base64',row);self.assertEqual(sum(len(v.get('body',b''))for v in self.forwarded),len(self.response))
 async def test_original_request_deadline_is_not_renewed(self):
  await self.run_original(late=True);row=self.terminal();self.assertIn('request_deadline',row['refusals']);self.assertFalse(row['complete'])
 async def test_transport_frame_substitution_is_not_accepted(self):
  await self.run_original(drift=True);row=self.terminal();self.assertFalse(row['complete']);self.assertIn('sent_frame_changed',row['refusals'])
 async def test_unrelated_route_is_pass_through_and_never_a_capture(self):
  scope={**self.scope,'path':'/unrelated-control'}
  await self.run_original(select=False,scope=scope);self.assertFalse((self.output/'terminal.json').exists());self.assertEqual(len(self.forwarded),3)
 async def test_wrong_token_is_not_captured_and_original_refusal_survives(self):
  self.scope['headers']=[(b'x-vision-token',b'b'*64)]
  with self.assertRaisesRegex(ValueError,'Original authenticated process token differs'):await self.run_original()
  row=self.terminal();self.assertFalse(row['complete']);self.assertNotIn('request_base64',row)
 async def test_private_token_in_original_body_is_never_serialized(self):
  self.request['image_id']='a'*64;self.body=json.dumps(self.request).encode()
  await self.run_original();row=self.terminal();self.assertIn('private_token_in_body',row['refusals']);self.assertNotIn('request_base64',row);self.assertNotIn(b'a'*64,(self.output/'terminal.json').read_bytes())
 async def test_nonfinite_response_does_not_qualify_JSON(self):
  self.response=b'{"invalid":NaN}'
  await self.run_original();row=self.terminal();self.assertIn('original_JSON_data_differs',row['refusals']);self.assertNotIn('response_base64',row)
 async def test_query_is_not_serialized_or_allowed_as_fixed_capture(self):
  self.scope['query_string']=b'private-query-control'
  with self.assertRaisesRegex(ValueError,'Original authenticated full-flow request scope differs'):await self.run_original()
  row=self.terminal();self.assertFalse(row['complete']);self.assertNotIn(b'private-query-control',(self.output/'terminal.json').read_bytes())
 async def test_additional_matching_request_leaves_exclusive_sticky_refusal_data(self):
  await self.run_original();self.assertTrue(self.terminal()['complete'])
  # The original business request still runs; this second scope cannot gain a
  # capture or silently preserve a one-request fixture claim.
  await self.run_original(select=False)
  refusal=json.loads((self.output/'matching-request-replayed.json').read_bytes())
  self.assertFalse(refusal['complete']);self.assertFalse(refusal['lease_authority']);self.assertIn('matching_request_replayed',self.owner.refusals)
 async def test_copied_issued_carrier_refuses_before_any_new_grant(self):
  with self.assertRaisesRegex(ValueError,'Only the original issued request carrier'):await self.run_original(copy_carrier=True)
  self.assertFalse(self.selector.used);self.assertFalse(self.terminal()['complete'])
 def test_default_none_does_not_create_output_or_import_activation(self):
  self.assertIsNone(self.http.bind_original_flow_http_capture(None))
  self.assertIsNone(self.http.begin_original_flow_http_capture(None,None))
 def test_existing_terminal_is_never_overwritten(self):
  target=self.output/'terminal.json';target.write_bytes(b'original retained control');target.chmod(0o600)
  self.owner._write('terminal.json',{'control':True})
  self.assertEqual(target.read_bytes(),b'original retained control');self.assertIn('receipt_write_failed',self.owner.refusals)
 def test_symlink_terminal_is_never_followed(self):
  protected=self.root/'protected-control';protected.write_bytes(b'keep');(self.output/'terminal.json').symlink_to(protected)
  self.owner._write('terminal.json',{'control':True});self.assertEqual(protected.read_bytes(),b'keep');self.assertIn('receipt_write_failed',self.owner.refusals)
 def test_replaced_output_directory_is_not_adopted(self):
  old=self.output.with_name('retained-control');self.output.rename(old);self.output.mkdir(mode=0o700)
  self.owner._write('terminal.json',{'control':True});self.assertFalse((self.output/'terminal.json').exists());self.assertTrue((old/'prepared.json').exists());self.assertIn('receipt_write_failed',self.owner.refusals)
 def test_foreign_cache_is_not_adopted(self):
  self.h._CACHE=dict(self.h._CACHE)
  self.owner._write('terminal.json',{'control':True});self.assertFalse((self.output/'terminal.json').exists());self.assertIn('receipt_write_failed',self.owner.refusals)

if __name__=='__main__':unittest.main(verbosity=2)
