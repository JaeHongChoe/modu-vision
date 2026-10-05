const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

// Execute the real event handlers. The transport/store response is controlled;
// navigation, handoff writes, and the rendered failure message are observed.
function harness(kind, outcome) {
  const states = [], writes = [], steps = [], navigations = [], taskCalls = [];
  let cursor = 0, transport = 'local';
  const job = {key:'training:job-a',id:'job-a',kind:'training',task:'segmentation',
    status:'completed',source:'/source',labelset:'default',transport:'local',raw:{}};
  const scopeFor = value => JSON.stringify([value.project?.id, value.projectDir, value.transportRevision || 0, value.apiTransportIdentity]);
  const project = {project:{id:'project-a',source_dataset_dir:'/source',active_labelset_id:'default'},
    projectDir:'/project-a',task:'classification',activeStep:3,language:'ko',projectName:'Test',
    projectError:null,isProjectBusy:false,backendStatus:{healthy:false},
    setStep:async value=>steps.push(value),setLanguage(){},
    setTask:async value=>{taskCalls.push(value);const result=await (typeof outcome==='function'?outcome():outcome);
      if(result.ok||result.applied) project.task=value;
      project.projectError=result.ok?null:result.error;return result;}};
  const compute = {transportRevision:0,selectedProfileId:null,profiles:[],probeResults:{},
    isLoaded:true,isLoading:false,loadError:null,error:null,load:async()=>{},selectTarget:async()=>{}};
  const store = value => Object.assign(selector=>selector?selector(value):value,{getState:()=>value});
  const state = initial => {
    const i=cursor++;
    if(!(i in states)) states[i]=typeof initial==='function'?initial():initial;
    return [states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;}];
  };
  const ImpactDialog=()=>null;
  const jsx=(type,props)=>({type,props:props||{}}),empty=()=>null;
  const mocks={
    react:{default:{},useState:state,useEffect(){},useRef:value=>({current:value})},
    'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':new Proxy({},{get:()=>empty}),
    '../../stores/useProjectStore':{useProjectStore:store(project)},
    '../../stores/useComputeStore':{useComputeStore:store(compute)},
    '../../stores/useTrainingStore':{useTrainingStore:store({})},
    '../../services/api':{getProjectContextGeneration:()=>0,subscribeProjectContext:()=>()=>{},getApiPersistenceIdentity:()=>transport,request:async()=>{}},
    './taskCenterModel':{taskSnapshotForScope:(saved,current,rows)=>saved===current?rows:[],
      taskLifecycle:()=>({cancellation:'none',termination:'confirmed',resource:'released'}),
      terminalTask:()=>true,taskSelection:()=>'',tasksForScope:rows=>rows,normalizeTask:(_,row)=>row,observationSummary:()=>null,releasableReservation:()=>null},
    './taskHandoff':{taskHandoffScope:scopeFor,taskHandoffContextScope:scopeFor,
      taskDestination:()=>({family:'segmentation',step:4}),modelFamilies:['segmentation'],
      saveTaskHandoff:()=>writes.push(job)},
    './TaskChangeImpactDialog':{TaskChangeImpactDialog:ImpactDialog,taskPreviewScope:()=>scopeFor({...project,...compute,apiTransportIdentity:transport})},
    './ProgramWorkbenchControls':{programInput:'',programButton:''},
    './jobProgress':(()=>{const file=path.resolve(__dirname,'../training/jobProgress.ts'),shared=new Module(file,module);shared.filename=file;
      shared._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,file);return shared.exports;})(),
  };
  const relative=kind==='center'?'../training/TaskCenter.tsx':'../wizard/WizardHeader.tsx';
  const filename=path.resolve(__dirname,relative),loaded=new Module(filename,module);
  loaded.filename=filename;loaded.paths=Module._nodeModulePaths(path.dirname(filename));
  loaded.require=reference=>mocks[reference]??new Proxy({},{get:()=>empty});
  loaded._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{
    target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);
  const render=()=>{cursor=0;return kind==='center'?loaded.exports.TaskCenter({initialOpen:true,onNavigate:step=>navigations.push(step)}):loaded.exports.WizardHeader();};
  global.window={api:{platform:'windows'}};global.localStorage={};
  render();
  if(kind==='center'){
    const rowCollections=states.filter(Array.isArray);
    assert.equal(rowCollections.length,1,'Task Center initializes one task row collection');
    rowCollections[0].push(job);
    const jobSelector=()=>nodes(render()).find(node=>node.type==='select'&&nodes(node).some(
      option=>option.type==='option'&&option.props.value===job.key));
    const select=jobSelector();assert.ok(select,'controlled task is visible in the production job dropdown');
    select.props.onChange({target:{value:job.key}});
    assert.equal(jobSelector().props.value,job.key,'production selection handler selects the controlled task');
  }
  const confirmHeader=async value=>{await headerAction({render,project}).props.onChange({target:{value}});const dialog=nodes(render()).find(n=>n.type===ImpactDialog);assert.ok(dialog,'task impact preview opens');const started=dialog.props.scope;const result=await project.setTask(value);if(started===scopeFor({...project,...compute,apiTransportIdentity:transport}))dialog.props.onResult(result);};
  return {confirmHeader,render,states,writes,steps,navigations,taskCalls,project,compute,
    changeTransport:value=>{transport=value;}};
}
function nodes(node) {
  if(!node||typeof node!=='object')return [];
  return [node,...[node.props?.children].flat(Infinity).flatMap(nodes)];
}
function centerAction(h){return nodes(h.render()).find(n=>n.type==='button'&&n.props.children==='완료 후보 평가로 이동');}
function headerAction(h){return nodes(h.render()).find(n=>n.type==='select'&&n.props.value===h.project.task);}

for(const applied of [false,true])test(`task center refuses handoff/navigation after unsuccessful task change (applied=${applied})`,async()=>{
  const h=harness('center',{ok:false,task:'classification',applied,error:'Task change refused'});
  await centerAction(h).props.onClick();
  assert.deepEqual(h.taskCalls,['segmentation']);
  assert.deepEqual(h.steps,[]);assert.deepEqual(h.navigations,[]);assert.deepEqual(h.writes,[]);
  assert.ok(nodes(h.render()).some(n=>n.props.role==='alert'&&n.props.children==='Task change refused'));
});
test('task center success writes one handoff and opens the evaluation stage',async()=>{
  const h=harness('center',{ok:true,task:'segmentation',changed:true});
  await centerAction(h).props.onClick();
  assert.deepEqual(h.steps,[4]);assert.deepEqual(h.navigations,[4]);assert.equal(h.writes.length,1);
});
test('a second click cannot bypass a partially applied task change; recovery permits navigation',async()=>{
  const h=harness('center',{ok:false,task:'segmentation',applied:true,error:'Dataset import failed'});
  await centerAction(h).props.onClick();assert.equal(h.project.task,'segmentation');
  await centerAction(h).props.onClick();assert.equal(h.writes.length,0);assert.deepEqual(h.steps,[]);
  // Production reimport/reopen clears projectError only when it succeeds.
  h.project.projectError=null;await centerAction(h).props.onClick();
  assert.equal(h.writes.length,1);assert.deepEqual(h.steps,[4]);
});
test('matching task cannot bypass another project mutation',async()=>{
  const h=harness('center',{ok:true,task:'segmentation',changed:false});
  h.project.task='segmentation';h.project.isProjectBusy=true;
  const button=centerAction(h);assert.equal(button.props.disabled,true);
  await button.props.onClick();assert.deepEqual(h.writes,[]);assert.deepEqual(h.steps,[]);
});
test('header exposes task failure inline and disables selection while project busy',async()=>{
  const h=harness('header',{ok:false,task:'classification',applied:false,error:'Task change refused'});
  await h.confirmHeader('segmentation');
  const tree=h.render(),select=nodes(tree).find(n=>n.props['aria-label']==='검사 작업 종류');
  assert.ok(select,'task selector has an accessible name');
  const failure=nodes(tree).find(n=>n.props.role==='alert'&&n.props.children==='Task change refused');
  assert.ok(failure,'refusal is visible beside the selector');
  assert.equal(select.props['aria-describedby'],failure.props.id);
  h.project.isProjectBusy=true;assert.equal(headerAction(h).props.disabled,true);
});
test('header ignores a late failure from a previous project or transport',async()=>{
  let finish;const deferred=new Promise(resolve=>{finish=resolve;});
  const h=harness('header',()=>deferred);
  const pending=h.confirmHeader('segmentation');
  await Promise.resolve();
  h.project.project={...h.project.project,id:'project-b'};h.project.projectDir='/project-b';
  h.compute.transportRevision++;h.changeTransport('server-b');
  finish({ok:false,task:'classification',applied:false,error:'Old project failure'});await pending;
  assert.ok(!nodes(h.render()).some(n=>n.props.role==='alert'&&n.props.children==='Old project failure'));
});
