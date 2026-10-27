const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function render(ready){
 const file=path.join(__dirname,'AssistantTaskPanel.tsx'),m=new Module(file,module),calls=[],effects=[];
 const state={task:null,contextMatches:true,busy:false,loading:false,error:null,restoreSettings:(...args)=>calls.push(['restoreSettings',...args]),restore:()=>calls.push('restore'),approve:(...args)=>calls.push(['approve',...args]),pause:()=>calls.push('pause'),resume:()=>calls.push('resume'),refresh:()=>calls.push('refresh')};
 const store=Object.assign(selector=>selector(state),{getState:()=>state});
 const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useEffect:effect=>effects.push(effect)};
 m.filename=file;m.paths=Module._nodeModulePaths(__dirname);m.require=name=>({react:{__esModule:true,default:react,...react},'../../stores/useAssistantTaskStore':{useAssistantTaskStore:store},'../../stores/useProjectStore':{useProjectStore:{getState:()=>({setStep:step=>calls.push(['step',step])})}},'./AssistantTaskCard':{AssistantTaskCard:'TaskCard'}}[name]);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);
 const tree=m.exports.AssistantTaskPanel({ready,blocked:false,connectionKey:'same-account'});return {tree,calls,effects,state};
}
test('opening a ready panel restores only saved task state, never automatically approves or runs it',()=>{
 const f=render(true);for(const effect of f.effects)effect();assert.deepEqual(f.calls,['restore']);
 assert.equal(f.tree.type,'TaskCard');f.tree.props.onApprove('task-1',7);assert.deepEqual(f.calls.at(-1),['approve','task-1',7]);
 f.tree.props.onNavigate(4);assert.deepEqual(f.calls.at(-1),['step',4]);
});
test('a signed-out panel does not read another session task history',()=>{
 const f=render(false);for(const effect of f.effects)effect();assert.deepEqual(f.calls,[]);assert.equal(f.tree.props.ready,false);
});

test('retained task navigation checks current project ownership before opening a screen',()=>{
 const f=render(true);f.state.contextMatches=false;f.tree.props.onNavigate(4);assert.deepEqual(f.calls,[]);
 f.state.contextMatches=true;f.state.task={id:'new-task'};f.tree.props.onNavigate(4);assert.deepEqual(f.calls,[]);
});
