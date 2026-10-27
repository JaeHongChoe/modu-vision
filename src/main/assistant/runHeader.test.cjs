// The run-id header for confirmed assistant writes, and the last redaction pass before text leaves the computer.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
const {AssistantRunRegistry}=load(path.join(__dirname,'runHeader.ts'));
const {redactOutgoingText,redactOutgoingValue}=load(path.join(__dirname,'redact.ts'));

test('short credential assignments in error text are redacted without breaking a JSON tool result',()=>{
 const message='backend returned {"password":"tiny-secret","access_token":"abc"}';
 const encoded=JSON.stringify({error:{message},code:'ERR_001'}),safe=redactOutgoingText(encoded);
 assert.doesNotMatch(safe,/tiny-secret|\\"abc\\"/);assert.doesNotThrow(()=>JSON.parse(safe));
 assert.doesNotMatch(redactOutgoingText('password=short api_key=small cookie=sid123'),/short|small|sid123/);
});
test('complete Cookie and Digest header values are withheld in outgoing nested JSON and text',()=>{
 const values=['Cookie: first=synthetic-one; second=synthetic-two','Set-Cookie: first=synthetic-one; Path=/; second=synthetic-two','"Authorization": "Digest username=synthetic-user, nonce=synthetic-nonce"'];
 for(const message of values){
   const safe=redactOutgoingText(JSON.stringify({error:{message}}));assert.doesNotThrow(()=>JSON.parse(safe));
   assert.doesNotMatch(safe,/synthetic-one|synthetic-two|synthetic-user|synthetic-nonce/);
   assert.doesNotMatch(redactOutgoingText(message),/synthetic-one|synthetic-two|synthetic-user|synthetic-nonce/);
 }
 const value=redactOutgoingValue({Cookie:'first=synthetic-one; second=synthetic-two',nested:{access_token:'tiny-secret'}});
 assert.doesNotMatch(JSON.stringify(value),/synthetic-one|synthetic-two|tiny-secret/);
});
const RUN='run_'+'ab'.repeat(12);

test('only the armed tool\'s own write carries the run id, and only until it ends or expires',()=>{
 const runs=new AssistantRunRegistry();
 assert.deepEqual(runs.headerFor('http://127.0.0.1:5000/api/dataset/split','POST',0),{},'nothing is armed');
 assert.equal(runs.arm(RUN,'save_split',0),true);
 assert.deepEqual(runs.headerFor('http://127.0.0.1:5000/api/dataset/split','POST',1),{'X-Modu-Assistant-Run':RUN});
 assert.deepEqual(runs.headerFor('http://127.0.0.1:5000/api/dataset/split','GET',1),{},'another method');
 assert.deepEqual(runs.headerFor('http://127.0.0.1:5000/api/dataset/import','POST',1),{},'another path, e.g. the count refresh after the split');
 assert.deepEqual(runs.headerFor('http://127.0.0.1:5000/api/dataset/split/extra','POST',1),{});
 runs.disarm(RUN);
 assert.deepEqual(runs.headerFor('http://127.0.0.1:5000/api/dataset/split','POST',2),{});
 assert.equal(runs.arm(RUN,'start_training',0),true);
 assert.deepEqual(runs.headerFor('https://team.example.com/api/training/start','POST',119_999),{'X-Modu-Assistant-Run':RUN});
 assert.deepEqual(runs.headerFor('https://team.example.com/api/training/start','POST',120_000),{},'two minutes at most');
});

test('read tools, forbidden tools and malformed run ids are never armed',()=>{
 const runs=new AssistantRunRegistry();
 for(const [id,tool] of [[RUN,'read_status'],[RUN,'approve_model'],[RUN,'delete_label_files'],['run_short','save_split'],[RUN.toUpperCase(),'save_split'],[42,'save_split']])assert.equal(runs.arm(id,tool,0),false,`${id} ${tool}`);
 for(let index=0;index<4;index++)assert.equal(runs.arm('run_'+String(index).repeat(24),'assign_class',0),true);
 assert.equal(runs.arm('run_'+'9'.repeat(24),'assign_class',0),false,'at most four runs at once');
});

test('outgoing text: paths become file names, addresses and credentials are hidden',()=>{
 const cases=[
  ['폴더를 찾을 수 없습니다: /Users/kim/datasets/lot-7/images','폴더를 찾을 수 없습니다: images'],
  ['C:\\Users\\kim\\data\\ok_01.png 열기 실패','ok_01.png 열기 실패'],
  ['\\\\fileserver\\share\\lot\\ng_02.png','ng_02.png'],
  ['~/datasets/a/b.png','b.png'],
  ['file:///Users/kim/a%20b/c.png','c.png'],
  ['https://team.example.com:8443/api 연결 실패','[주소] 연결 실패'],
  ['서버 10.0.0.7:8000 또는 gpu-box.local:9000','서버 [주소] 또는 [주소]'],
  ['담당 kim@example.com','담당 [이메일]'],
  ['Authorization: Bearer abc.def.ghi','Authorization: Bearer [가림]'],
  ['키 sk-test0123456789abcdef 사용','키 [가림] 사용'],
  ['학습 70% / 검증 20% / 시험 10%, OK/NG 폴더, 0.95 기준','학습 70% / 검증 20% / 시험 10%, OK/NG 폴더, 0.95 기준'],
 ];
 for(const [input,expected] of cases)assert.equal(redactOutgoingText(input),expected,input);
 assert.deepEqual(redactOutgoingValue({path:'/a/b/c.png',list:['https://x.example/y'],n:3,ok:true}),{path:'c.png',list:['[주소]'],n:3,ok:true});
});
