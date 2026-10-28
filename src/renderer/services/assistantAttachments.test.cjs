const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(){const file=path.join(__dirname,'assistantAttachments.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=name=>name==='./assistantImagePreparation'?{normalizeSelectedImage:async file=>file}:original(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
test('explicit work requests allow continuous execution while explanation questions remain read-only',()=>{
 const {isAssistantExecutionRequest}=load();
 for(const text of ['이 자료로 모델 학습하고 평가까지 해줘','이미지 라벨을 저장해 주세요','검사 흐름 만들어줘','Please train this model and evaluate it','Can you import the dataset?'])assert.equal(isAssistantExecutionRequest(text),true,text);
 for(const text of ['학습이 뭐야?','이 오류는 왜 뜨는 거야?','이미지가 무슨 모양인지 설명해줘','선택 코호트 평가를 꼭 해야 하나?'])assert.equal(isAssistantExecutionRequest(text),false,text);
});
test('attachments validate media/size and hash exact bytes without exposing a local path',async()=>{
 const {prepareAssistantAttachment}=load();
 const file=new Blob([Uint8Array.from([137,80,78,71,13,10,26,10])],{type:'image/png'});Object.defineProperty(file,'name',{value:'example.png'});
 const value=await prepareAssistantAttachment(file);
 assert.equal(value.fileName,'example.png');assert.equal(value.base64,'iVBORw0KGgo=');assert.match(value.imageIdentity,/^[a-f0-9]{64}$/);
 assert.equal(value.previewUrl,'data:image/png;base64,iVBORw0KGgo=');
 await assert.rejects(prepareAssistantAttachment(new Blob(['x'],{type:'text/plain'})),/PNG/);
 await assert.rejects(prepareAssistantAttachment(new Blob([],{type:'image/png'})),/비어/);
 await assert.rejects(prepareAssistantAttachment(new Blob([new Uint8Array(5*1024*1024+1)],{type:'image/png'})),/5MB/);
});

test('quoted commands, negated execution and explanation-only wording never authorize tasks',()=>{
 const {isAssistantExecutionRequest:work}=load();
 for(const value of ['"학습 시작해줘"는 무슨 뜻이야?','“저장해줘”라고 말하면 무슨 일이야?','학습 시작해줘라는 문구는 무슨 뜻이야?','학습은 실행하지 말고 설명만 해줘','Please train? Just explain, do not run anything.','학습해줘라는 요청을 받으면 어떻게 실행해?','Please run nothing; just describe training.','학습 시작해줘. 아니 실행하지 마.'])assert.equal(work(value),false,value);
 for(const value of ['"예제" 프로젝트로 학습해줘','왜 실패했는지 확인하고 고쳐줘','이 자료로 학습을 시작해줘'])assert.equal(work(value),true,value);
});
