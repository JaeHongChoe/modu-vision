const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function render(ready,extra={},onNewRequestPrepared=()=>{}){
 const file=path.join(__dirname,'AssistantTaskPanel.tsx'),m=new Module(file,module),calls=[],effects=[];
 const state={task:null,contextMatches:true,busy:false,loading:false,error:null,planningNew:false,history:[],readPending:false,prepareNewRequest:async()=>false,stopRead:()=>calls.push('stopRead'),loadHistory:()=>calls.push('loadHistory'),restoreSettings:(...args)=>calls.push(['restoreSettings',...args]),restore:(id)=>calls.push(id?['restore',id]:'restore'),approve:(...args)=>calls.push(['approve',...args]),pause:()=>calls.push('pause'),resume:()=>calls.push('resume'),refresh:()=>calls.push('refresh'),...extra};
 const store=Object.assign(selector=>selector(state),{getState:()=>state});
 const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useEffect:effect=>effects.push(effect)};
 m.filename=file;m.paths=Module._nodeModulePaths(__dirname);m.require=name=>({react:{__esModule:true,default:react,...react},'../../stores/useAssistantTaskStore':{useAssistantTaskStore:store},'../../stores/useProjectStore':{useProjectStore:{getState:()=>({setStep:step=>calls.push(['step',step])})}},'./AssistantTaskCard':{AssistantTaskCard:'TaskCard'}}[name]);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);
 const tree=m.exports.AssistantTaskPanel({ready,blocked:false,connectionKey:'same-account',onNewRequestPrepared});return {tree,calls,effects,state};
}
test('opening a ready panel restores only saved task state, never automatically approves or runs it',()=>{
 const f=render(true);for(const effect of f.effects)effect();assert.deepEqual(f.calls,['restore']);
 assert.equal(f.tree.type,'TaskCard');f.tree.props.onApprove('task-1',7);assert.deepEqual(f.calls.at(-1),['approve','task-1',7]);
 f.tree.props.onNavigate(4);assert.deepEqual(f.calls.at(-1),['step',4]);
});

test('only successful current preparation clears conversation after the awaited controller result',async()=>{
 for(const accepted of [false,true]){
  let finish;const waiting=new Promise(resolve=>{finish=resolve;}),calls=[];
  const f=render(true,{task:{id:'shown',revision:7},prepareNewRequest:(...args)=>{calls.push(['prepare',...args]);return waiting;}},()=>calls.push('prepared'));
  const pending=f.tree.props.onPrepareNewRequest('shown',7);
  assert.deepEqual(calls,[['prepare','shown',7]]);
  if(accepted)f.state.planningNew=true;
  finish(accepted);await pending;
  assert.deepEqual(calls,accepted?[['prepare','shown',7],'prepared']:[['prepare','shown',7]]);
 }
});

test('a stale prepared completion cannot clear a newer selection and history controls remain reads',async()=>{
 let finish;const calls=[],waiting=new Promise(resolve=>{finish=resolve;});
 const f=render(true,{task:{id:'shown',revision:7},prepareNewRequest:()=>waiting},()=>calls.push('prepared'));
 const pending=f.tree.props.onPrepareNewRequest('shown',7);f.state.task={id:'new',revision:1};f.state.planningNew=true;finish(true);await pending;
 assert.deepEqual(calls,[]);
 f.tree.props.onSelectHistory('record-2');f.tree.props.onLoadHistory();f.tree.props.onStopRead();
 assert.deepEqual(f.calls,[['restore','record-2'],'loadHistory','stopRead']);
});
test('a signed-out panel does not read another session task history',()=>{
 const f=render(false);for(const effect of f.effects)effect();assert.deepEqual(f.calls,[]);assert.equal(f.tree.props.ready,false);
});

test('retained task navigation checks current project ownership before opening a screen',()=>{
 const f=render(true);f.state.contextMatches=false;f.tree.props.onNavigate(4);assert.deepEqual(f.calls,[]);
 f.state.contextMatches=true;f.state.task={id:'new-task'};f.tree.props.onNavigate(4);assert.deepEqual(f.calls,[]);
});

test('old execution handlers are inert after the person selects new-request mode',()=>{
 const f=render(true,{task:{id:'shown',revision:7}});
 f.state.planningNew=true;
 f.tree.props.onApprove('shown',7);f.tree.props.onResume();f.tree.props.onRefresh();f.tree.props.onPause();f.tree.props.onRestoreSettings('shown',7);f.tree.props.onNavigate(2);
 assert.deepEqual(f.calls,[]);
});

test('reopening in prepared mode does not automatically restore an older plan',()=>{
 const f=render(true,{planningNew:true});for(const effect of f.effects)effect();assert.deepEqual(f.calls,[]);
});
