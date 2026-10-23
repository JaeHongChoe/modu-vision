// What the assistant may know: the footer's picture without its scope (project and source folders), counts, file names
// instead of paths, no pixels; long conversations keep whole recent turns and a one-line summary of the rest.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
const context=load(path.join(__dirname,'assistantContext.ts'));
const snapshot={scope:JSON.stringify(['/Users/kim/projects/line-3','proj-1','/Volumes/NAS/lot-7','default','classification']),stage:3,status:'blocked',
 blocker:'데이터 폴더를 찾을 수 없습니다: /Volumes/NAS/lot-7/images',nextAction:{label:'데이터 다시 열기',step:1,target:'workflow-data'},
 progress:{epoch:0,totalEpochs:30,etaSeconds:null,completed:false},errors:[{source:'공동 작업 서버',text:'https://team.example.com 연결 실패, 담당 kim@example.com'}]};
const facts={task:'classification',view:'simple',shared:true,images:120,unlabeled:0,classes:{OK:80,NG:40},split:{train:84,val:24,test:12},splitState:'saved',trainingStatus:'idle',currentImage:'/Volumes/NAS/lot-7/OK/ok_017.png'};

test('the minimal context drops the scope, reduces paths to file names and hides addresses and names',()=>{
 const value=context.minimalAssistContext(snapshot,facts),text=JSON.stringify(value);
 for(const secret of ['/Users/kim','/Volumes/NAS','line-3','proj-1','team.example.com','kim@example.com','workflow-data','scope'])assert.ok(!text.includes(secret),secret);
 assert.equal(value.step,3);assert.equal(value.step_name,'모델 학습');assert.equal(value.blocker,'데이터 폴더를 찾을 수 없습니다: images');
 assert.deepEqual(value.next_action,{label:'데이터 다시 열기',step:1});assert.equal(value.current_image,'ok_017.png');
 assert.deepEqual(value.panel_errors,[{source:'공동 작업 서버',text:'[주소] 연결 실패, 담당 [이메일]'}]);
 assert.equal(value.mode,'shared_server');assert.deepEqual(value.classes,{OK:80,NG:40});
 assert.ok(!/base64|data:image|pixel/i.test(text),'no image content');
 assert.deepEqual(context.minimalAssistContext(null,{...facts,currentImage:null}).step,null);
});

test('long conversations keep whole recent turns, summarize the dropped questions and drop old replay blocks',()=>{
 const turn=(index,size=10)=>[{role:'user',text:`질문 ${index} `+'가'.repeat(size)},{role:'assistant',text:'',toolCalls:[{id:`c${index}`,name:'read_status',arguments:{}}],replay:[{type:'thinking',thinking:'',signature:'s'}]},
  {role:'tool',toolCallId:`c${index}`,name:'read_status',content:'{}'},{role:'assistant',text:`답 ${index}`}];
 const messages=[...turn(1,4000),...turn(2,4000),...turn(3),...turn(4)];
 const trimmed=context.trimConversation(messages,{maxChars:9000,maxTurns:8});
 assert.equal(trimmed[0].role,'user');assert.match(trimmed[0].text,/^\(앞선 질문 1개 요약: 질문 1/);assert.match(trimmed[0].text,/질문 2/);
 assert.equal(trimmed.length,12,'three whole turns');
 const calls=trimmed.filter(message=>message.toolCalls).map(message=>message.toolCalls[0].id),results=trimmed.filter(message=>message.role==='tool').map(message=>message.toolCallId);
 assert.deepEqual(calls,results,'every kept tool call keeps its result');
 const replays=trimmed.map((message,index)=>message.replay?index:-1).filter(index=>index>=0);
 assert.deepEqual(replays,[9],'only the latest turn keeps its provider blocks');
 assert.equal(context.trimConversation(turn(9)).length,4);
 assert.equal(context.trimConversation([...turn(1),...turn(2),...turn(3)],{maxTurns:2}).length,8);
});

test('argument summaries carry names and numbers, never paths; hashing is canonical',async()=>{
 assert.equal(context.argumentSummary({image:'/Users/kim/lot/ok_01.png',class_name:'NG',threshold:0.5,flag:true,nested:{a:1}}),'image=ok_01.png, class_name=NG, threshold=0.5, flag=true, nested=…');
 assert.equal(context.canonicalJson({b:1,a:[{d:2,c:3}]}),'{"a":[{"c":3,"d":2}],"b":1}');
 assert.equal(await context.sha256Hex('abc'),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
 assert.match(context.randomHex(12),/^[a-f0-9]{24}$/);
});
