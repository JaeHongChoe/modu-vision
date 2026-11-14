const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),test=require('node:test'),ts=require('typescript'),react=require('react');
function load(name,mocks){const file=path.join(__dirname,name),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);m.require=ref=>Object.hasOwn(mocks,ref)?mocks[ref]:original(ref);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,file);return m.exports;}
function fixture({running=false,examples=[],openResult=true}={}){
 const actions=[],starts=[];let closed=0,dismissed=0,loaded=0;
 const demo={state:running?'running':'idle',steps:{},error:null,result:null};
 const first={demo,onboarding:{example_projects:examples},setDemo(next){first.demo=next;},load:async()=>loaded++};
 const useFirstRunStore=Object.assign(selector=>selector(first),{getState:()=>first});
 const project={openProject:async dir=>{actions.push(['open',dir]);return openResult;},setStep:step=>actions.push(['step',step])};
 const mocks={react:{...react,useRef:()=>({current:null})},'../../services/api':{},'../../stores/useProjectStore':{useProjectStore:{getState:()=>project}},
  '../../stores/useDatasetStore':{},'../../stores/useTrainingStore':{},'../../stores/useComputeStore':{},'../../stores/useEvaluationStore':{},'../../stores/useFlowchartStore':{},
  '../../services/flowEvaluation':{},'../inference/batchInspection':{},'./firstRun':load('firstRun.ts',{}),'./firstRunStore':{useFirstRunStore},
  '../wizard/workflowReadiness':{focusWorkflowTarget:target=>actions.push(['focus',target])},'./FirstUseStudyPanel':{FirstUseStudyPanel:()=>null},
  './runDemo':{DEMO_PROJECT_NAME:'예제 · 표면 긁힘 분류',DEMO_TRAINING:{},runDemo:async(deps,update,options)=>{starts.push(options);update({state:'running',steps:{},error:null,result:null});}}};
 const {FirstRunStudio}=load('FirstRunStudio.tsx',mocks);
 const render=()=>FirstRunStudio({onClose:()=>closed++,onDismiss:()=>dismissed++,onOpenWorkspace:tab=>actions.push(['workspace',tab])});
 const nodes=tree=>{const list=[];const walk=n=>{if(!n||typeof n!=='object')return;list.push(n);react.Children.forEach(n.props?.children,walk);};walk(tree);return list;};
 const text=node=>react.Children.toArray(node.props?.children).map(n=>typeof n==='object'?text(n):String(n)).join('');
 const button=label=>nodes(render()).find(n=>n.type==='button'&&text(n)===label);
 return{render,nodes,text,button,first,actions,starts,closed:()=>closed,dismissed:()=>dismissed,loaded:()=>loaded};
}

test('first run opens distinct new-photo and existing-work destinations without creating a project or dismissing the guide permanently',()=>{
 const f=fixture();const own=f.button('내 사진으로 만들기'),existing=f.button('만들어 둔 작업 이어가기');
 assert.ok(own&&existing,'both project paths are available from the start');
 own.props.onClick();existing.props.onClick();
 assert.deepEqual(f.actions,[['workspace','create'],['workspace','recent']]);assert.equal(f.closed(),2);assert.equal(f.dismissed(),0);
});

test('example start uses the existing demo runner and preserves unique example naming',async()=>{
 const f=fixture({examples:[{id:'demo',name:'예제 · 표면 긁힘 분류',project_dir:'/demo'}]});
 const button=f.button('예제로 해보기');assert.ok(button,'the example path is visible alongside the project paths');
 const previous=global.requestAnimationFrame;global.requestAnimationFrame=fn=>fn();
 try{button.props.onClick();await new Promise(setImmediate);}finally{global.requestAnimationFrame=previous;}
 assert.deepEqual(f.starts,[{projectName:'예제 · 표면 긁힘 분류 2',takenNames:['예제 · 표면 긁힘 분류']}]);
 assert.equal(f.loaded(),1);assert.equal(f.closed(),0);assert.equal(f.dismissed(),0);
});

test('running demo blocks alternate entry paths and dismissal including direct callback invocation',()=>{
 const f=fixture({running:true});
 for(const label of ['내 사진으로 만들기','만들어 둔 작업 이어가기','다음부터 보지 않기','닫기']){
  const button=f.button(label);assert.ok(button,label);assert.equal(button.props.disabled,true);button.props.onClick();
 }
 assert.deepEqual(f.actions,[]);assert.equal(f.closed(),0);assert.equal(f.dismissed(),0);
});

test('an existing example opens its inspection results only after project open succeeds',async()=>{
 for(const openResult of [false,true]){
  const f=fixture({examples:[{id:'example',name:'Saved example',project_dir:'/saved-example'}],openResult});
  await f.button('열기').props.onClick();await new Promise(setImmediate);
  assert.deepEqual(f.actions,openResult?[['open','/saved-example'],['step',6]]:[['open','/saved-example']]);
  assert.equal(f.closed(),openResult?1:0);assert.equal(f.dismissed(),0);
 }
});

test('initial guide shows one example notice and reveals example details only for real progress or saved history',()=>{
 const notice=load('firstRun.ts',{}).EXAMPLE_NOTICE;
 const empty=fixture();assert.equal(empty.text(empty.render()).split(notice).length-1,1);
 assert.equal(empty.nodes(empty.render()).some(n=>n.type==='section'&&n.props['aria-labelledby']==='first-run-example'),false);
 assert.deepEqual(empty.starts,[]);
 for(const state of ['running','failed','ready']){
  const f=fixture();f.first.demo={state,steps:{train:{status:state==='failed'?'failed':'done'}},error:state==='failed'?'예제 오류':null,result:state==='ready'?{counts:{OK:2},images:2}:null};
  const tree=f.render();assert.ok(f.nodes(tree).some(n=>n.type==='section'&&n.props['aria-labelledby']==='first-run-example'),state);
  assert.equal(f.text(tree).split(notice).length-1,1);assert.ok(f.nodes(tree).some(n=>n.props['aria-label']==='예제 진행'));
  if(state==='failed')assert.match(f.text(tree),/예제 오류/);
  if(state==='ready')assert.ok(f.button('검사 결과 보기 (6단계)'));
  assert.deepEqual(f.starts,[]);
 }
 const saved=fixture({examples:[{id:'saved',name:'Saved example',project_dir:'/saved'}]});
 assert.ok(saved.button('열기'));assert.equal(saved.text(saved.render()).split(notice).length-1,1);
});
