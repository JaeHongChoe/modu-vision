"""Real runtime_probe, production closure AST and CaptureSession; stdlib only.

No app/backend/Torch/NumPy/GPU/remote/model forward is imported or executed.
The five producer roles below are explicit non-model source controls.
"""
import ast,copy,importlib.util,json,os,sys,tempfile,types,unittest
from pathlib import Path
SOURCE=Path(os.environ.get('MV_CAPTURE_SOURCE',str(Path(__file__).resolve().parents[1]/'engine/required_flow_passive_capture.py')))
MODULE_LOAD_BEFORE=frozenset(sys.modules)
spec=importlib.util.spec_from_file_location('capture_runtime_snapshot_control',SOURCE);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
MODULE_LOAD_IMPORTS=frozenset(sys.modules)-MODULE_LOAD_BEFORE
def owned_runtime_imports(before,after):
 return frozenset(k for k in set(after)-set(before) if k=='torch'or k.startswith('torch.')or k=='numpy'or k=='backend'or k.startswith('backend.'))
class FakeNode:
 def __init__(self,values):self.values=values
 def __getattr__(self,key):
  if key not in self.values:raise AttributeError(key)
  return self.values[key]
def configured_runtime():
 calls=[]
 def getter(name,value):
  def read(*args):calls.append((name,args));return value
  return read
 cuda=FakeNode({'matmul':FakeNode({'allow_tf32':False,'fp32_precision':'highest'}),'flash_sdp_enabled':getter('flash',True),'mem_efficient_sdp_enabled':getter('efficient',True),'math_sdp_enabled':getter('math',True),'cudnn_sdp_enabled':getter('cudnn_sdp',True)})
 cudnn=FakeNode({'allow_tf32':True,'enabled':True,'benchmark':False,'deterministic':False,'conv':FakeNode({'fp32_precision':'tf32'}),'version':getter('cudnn_version',9900)})
 torch=FakeNode({'__version__':'FAKE-source-control','version':FakeNode({'cuda':'FAKE-CUDA-NO-RUNTIME'}),'backends':FakeNode({'cuda':cuda,'cudnn':cudnn,'mkldnn':FakeNode({'enabled':True,'deterministic':False,'fp32_precision':'ieee'}),'cpu':FakeNode({'fp32_precision':'ieee'}),'mkl':FakeNode({'is_available':getter('mkl',True)})}),'get_float32_matmul_precision':getter('matmul_precision','highest'),'get_default_dtype':getter('dtype','float32'),'get_num_threads':getter('threads',1),'get_num_interop_threads':getter('interop',1),'are_deterministic_algorithms_enabled':getter('deterministic',False),'is_grad_enabled':getter('grad',False),'is_autocast_enabled':getter('autocast',False),'__config__':FakeNode({'show':getter('build','FAKE BUILD NO TORCH')})})
 return torch,FakeNode({'__version__':'FAKE OpenCV'}),calls

def actual_prepared_probe(*,runtime_value_override='original'):
 torch,cv2,reads=configured_runtime();text=SOURCE.read_text();fn=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)and n.name=='prepare_worker_observation')
 assignment=next(n for n in fn.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='configured_runtime'for t in n.targets))
 probe=next(n for n in fn.body if isinstance(n,ast.FunctionDef)and n.name=='probe')
 namespaces={'runtime_probe':m.runtime_probe,'torch':torch,'cv2':cv2,'actual_device':'cuda:0','observed_uuid':m.RETAINED_GPU_UUID}
 exec(compile(ast.Module(body=[assignment,probe],type_ignores=[]),str(SOURCE),'exec'),namespaces)
 if runtime_value_override!='original':namespaces['configured_runtime']=runtime_value_override
 return namespaces['probe'],copy.deepcopy(namespaces['configured_runtime']),reads

def control_forward(images):return {'vector':[0.0,1.0],'input':images}
def control_logits(x):return {'logits':[0.25,0.75],'input':x}
def control_predict(rgb,image_size):return {'RGB':rgb,'image_size':image_size,'angle':0.0}
def control_local(probabilities,pixels,bbox,names,threshold,params):return {'local_probabilities':probabilities,'pixels':pixels,'bbox':bbox}
def control_remap(entries,transform,source_shape,bbox,source_pixels,params):return {'remapped':entries,'transform':transform}
FUNCTIONS=lambda:(m.FunctionSpec('rotation_forward',control_forward,('images',)),m.FunctionSpec('segmentation_forward',control_logits,('x',)),m.FunctionSpec('rotation_predict',control_predict,('rgb','image_size')),m.FunctionSpec('class_evidence',control_local,('probabilities','pixels','bbox','names','threshold','params')),m.FunctionSpec('remap_class_evidence',control_remap,('entries','transform','source_shape','bbox','source_pixels','params')))
def original_control_call(returned,calls,error=None):
 def call():
  calls.append('one original non-model control')
  control_forward([1.0]);control_logits([1.0]);control_predict('RGB control',32);local=control_local([0.25,0.75],[1],[0,0,1,1],['background','defect'],0.5,{})
  control_remap(local,[[1,0,0],[0,1,0]],[1,1],[0,0,1,1],[1],{})
  if error is not None:raise error
  return returned
 return call
class RuntimeSnapshotContract(unittest.TestCase):
 def test_real_probe_mapping_completes_all5_role_capture_without_reprobe_or_extra_call(self):
  probe,configured,reads=actual_prepared_probe();self.assertIs(type(configured),dict);before=list(reads);returned=object();calls=[]
  with tempfile.TemporaryDirectory()as tmp:
   output=Path(tmp).resolve()/'capture';session=m.CaptureSession(output,FUNCTIONS(),lambda v:None,probe)
   self.assertIs(session.observe(original_control_call(returned,calls)),returned)
   saved=json.loads((output/'capture.json').read_bytes());self.assertEqual(saved['capture_errors'],[]);self.assertTrue(saved['capture_complete']);self.assertEqual(saved['missing_roles'],[]);self.assertEqual(len(saved['events']),5);self.assertEqual(saved['unclosed_observed_frames'],0);self.assertTrue(saved['observer_removed']);self.assertIsNone(session.terminal_error)
   expected={**configured,'original_selected_device':'cuda:0','observed_GPU_UUID':m.RETAINED_GPU_UUID}
   self.assertEqual(saved['runtime_before'],expected);self.assertEqual(saved['runtime_after'],expected)
   for event in saved['events']:
    self.assertTrue(event['returned']);self.assertEqual(event['runtime_before'],expected);self.assertEqual(event['runtime_after'],expected)
   self.assertEqual(calls,['one original non-model control']);self.assertEqual(reads,before);self.assertFalse(saved['actual_installed_runtime_accepted']);self.assertFalse(saved['historical_CPU_cause_proved']);self.assertEqual(saved['parent_credit'],0)
 def test_real_runtime_configuration_is_retained_without_precision_setters_or_repeated_getters(self):
  probe,configured,reads=actual_prepared_probe();before=list(reads);self.assertIs(type(configured),dict);self.assertFalse(configured['flags']['backends.cuda.matmul.allow_tf32']);self.assertTrue(configured['flags']['backends.cudnn.allow_tf32']);self.assertEqual(configured['flags']['get_float32_matmul_precision'],'highest')
  first,second=probe(),probe();self.assertEqual(first,second);self.assertEqual({k:v for k,v in first.items()if k not in('original_selected_device','observed_GPU_UUID')},configured);self.assertEqual(reads,before)
 def test_nonmapping_runtime_is_explicit_incomplete_and_original_return_not_replaced(self):
  for invalid in(None,[],17,'invalid'):
   with self.subTest(invalid=invalid),tempfile.TemporaryDirectory()as tmp:
    probe,_,_=actual_prepared_probe(runtime_value_override=invalid);returned=object();calls=[];output=Path(tmp).resolve()/'capture';session=m.CaptureSession(output,FUNCTIONS(),lambda v:None,probe)
    self.assertIs(session.observe(original_control_call(returned,calls)),returned);saved=json.loads((output/'capture.json').read_bytes());self.assertFalse(saved['capture_complete']);self.assertTrue(saved['capture_errors']);self.assertTrue(all(e['type']=='TypeError'for e in saved['capture_errors']));self.assertEqual(len(calls),1);self.assertIsNone(sys.gettrace())
 def test_original_producer_exception_identity_is_retained_without_retry(self):
  probe,_,_=actual_prepared_probe();returned=object();calls=[];original=RuntimeError('source control original exception')
  with tempfile.TemporaryDirectory()as tmp:
   output=Path(tmp).resolve()/'capture';session=m.CaptureSession(output,FUNCTIONS(),lambda v:None,probe)
   try:session.observe(original_control_call(returned,calls,original))
   except BaseException as observed:self.assertIs(observed,original)
   else:self.fail('Original exception was replaced')
   saved=json.loads((output/'capture.json').read_bytes());self.assertFalse(saved['capture_complete']);self.assertEqual(saved['capture_errors'],[]);self.assertEqual(saved['producer_exception']['type'],'RuntimeError');self.assertEqual(len(calls),1);self.assertTrue(saved['observer_removed']);self.assertIsNone(sys.gettrace())
 def test_no_application_framework_or_model_module_was_imported(self):
  self.assertFalse(owned_runtime_imports((),MODULE_LOAD_IMPORTS))
  before=frozenset(sys.modules)
  probe,_,_=actual_prepared_probe();returned=object();calls=[]
  with tempfile.TemporaryDirectory()as tmp:
   output=Path(tmp).resolve()/'capture';session=m.CaptureSession(output,FUNCTIONS(),lambda v:None,probe)
   self.assertIs(session.observe(original_control_call(returned,calls)),returned)
   saved=json.loads((output/'capture.json').read_bytes());self.assertTrue(saved['capture_complete']);self.assertEqual(saved['capture_errors'],[]);self.assertEqual(len(saved['events']),5);self.assertEqual(len(calls),1)
  self.assertFalse(owned_runtime_imports(before,frozenset(sys.modules)))
 def test_import_boundary_excludes_prior_test_but_rejects_owned_module_additions(self):
  # Explicit module-name sets model ordering only; no application module loads.
  prior=frozenset(('backend.unrelated_previous_pytest_source_control','json'))
  self.assertFalse(owned_runtime_imports(prior,prior|{'pathlib'}))
  for owned in('torch','torch.control','numpy','backend','backend.owned_probe_source_control'):
   with self.subTest(owned=owned):self.assertEqual(owned_runtime_imports(prior,prior|{owned}),frozenset((owned,)))
if __name__=='__main__':unittest.main(verbosity=2)
