const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>ref.startsWith('.')?load(ref+'.ts'):require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const support=()=>load('workerSupport.ts');
const decision=(state,reason=state)=>({state,reason});
const worker=(states={},extra={})=>({worker_id:'local',devices:[{kind:'cpu',name:'cpu'}],preflight:{},preflight_tasks:['classification','anomaly'],preflight_record_error:null,
 support:{classification:{train:{cpu:decision(states.train||'unverified')},evaluate:{cpu:decision(states.evaluate||'unverified')},infer:{cpu:decision('unverified')},export:{cpu:decision('unverified')},search:{cpu:decision('unsupported','no search')}}},...extra});
test('a preflight can start only for a family with a preflight and a device every preflight stage supports',()=>{const s=support();
 assert.equal(s.preflightBlocker(worker(),'classification','cpu',null),null,'search being unsupported does not block the four preflight stages');
 assert.equal(s.preflightBlocker(worker({train:'verified'}),'classification','cpu',null),null,'a verified stage can be checked again');
 assert.equal(s.preflightBlocker(worker({evaluate:'not_installed'}),'classification','cpu',null),'not_installed','the backend decision\'s own reason');
 assert.equal(s.preflightBlocker(worker({train:'unsupported'}),'classification','cpu',null),'unsupported');
 assert.match(s.preflightBlocker(worker(),'ocr','cpu',null),/아직 제공되지 않습니다/);
 assert.match(s.preflightBlocker(worker(),'classification','cuda',null),/지원 상태를 확인하지 못했습니다/,'a device the worker lacks');
 assert.match(s.preflightBlocker(worker(),'classification','cpu',{task:'anomaly',device:'cpu',stages:['train']}),/anomaly · CPU/,'one preflight at a time');});
test('the last preflight names every stage and keeps a failure\'s reason',()=>{const s=support();
 const row=(passed,reason='passed')=>({passed,reason,seconds:1,at:1});
 assert.deepEqual(s.lastPreflightText({task:'classification',device:'cpu',error:null,results:{train:row(true),evaluate:row(true),infer:row(true),export:row(true)}}),
  {text:'마지막 CPU 사전 점검: 학습 통과 · 평가 통과 · 추론 통과 · 내보내기 통과',ok:true});
 const failed=s.lastPreflightText({task:'classification',device:'cpu',error:null,results:{train:row(true),export:row(false,'ValueError: no verdict')}});
 assert.equal(failed.ok,false);assert.equal(failed.text,'마지막 CPU 사전 점검: 학습 통과 · 내보내기 실패(ValueError: no verdict)');
 assert.deepEqual(s.lastPreflightText({task:'classification',device:'cpu',error:'OSError: disk full',results:{}}),{text:'마지막 CPU 사전 점검: 기록 실패 · OSError: disk full',ok:false});
 assert.equal(s.lastPreflightText({task:'classification',device:'cpu',error:null,results:{}}).ok,false,'nothing recorded is not a pass');});
test('a reserved computer blocks a preflight with the backend reason, and the scope names the checked architecture',()=>{const s=support();
 assert.equal(s.preflightBlocker(worker({},{local_compute_busy:'학습 또는 다른 사전 점검이 이 컴퓨터의 계산 자원을 사용 중입니다.'}),'classification','cpu',null),
  '학습 또는 다른 사전 점검이 이 컴퓨터의 계산 자원을 사용 중입니다.');
 assert.match(s.preflightBlocker(worker({},{local_compute_busy:'busy'}),'classification','cpu',{task:'anomaly',device:'cpu',stages:['train']}),/anomaly · CPU/,'this app\'s own running preflight is named first');
 assert.equal(s.preflightBlocker(worker({},{local_compute_busy:null}),'classification','cpu',null),null);
 const scope=s.preflightScope(worker({},{preflight_architectures:{classification:'resnet18'}}),'classification');
 assert.match(scope,/^resnet18 구조를 작은 합성 데이터로/);assert.match(scope,/기본 구조의 가중치와 의존성, ONNX 등 다른 내보내기 형식은 이 점검에 포함되지 않습니다/);
 assert.doesNotMatch(s.preflightScope(worker(),'classification'),/undefined/,'an older backend without architectures');});
