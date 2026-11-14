// The project manager never offers a switch that the click then refuses: while saving the open work would be refused
// (right after start the current project's saved source is still being read), its switch actions wait, disabled and
// with the same reason shown. Found when a hosted CI run clicked another project during that read and was refused.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
const react=require('react'),{renderToStaticMarkup}=require('react-dom/server');
function load(file,mocks){const name=path.resolve(__dirname,file);const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));const original=mod.require.bind(mod);mod.require=ref=>ref in mocks?mocks[ref]:original(ref);mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,name);return mod.exports;}
const READING='데이터 작업이 진행 중입니다. 완료 후 프로젝트를 전환하세요.';
const current={id:'a',name:'Project A',project_dir:'/projects/a',task:'segmentation',source_dataset_dir:'/data/a'};
const other={name:'Project B',project_dir:'/projects/b',task:'segmentation'};

test('the refusal the dialog shows is the one a project switch gets, and it follows every store it reads',async()=>{
 const listeners=[];
 const store=value=>({getState:()=>value,setState:update=>Object.assign(value,typeof update==='function'?update(value):update),subscribe:listener=>{listeners.push(listener);return()=>listeners.splice(listeners.indexOf(listener),1);}});
 const dataset=store({isLoading:true,isSplitting:false,isGenerating:false,setFolderPath(){}});
 const opened=[];
 const mocks={'../services/api':{api:{project:{getCurrent:async()=>current,open:async dir=>{opened.push(dir);return current;},list:async()=>({projects:[]})}},getApiPersistenceIdentity:()=> 'local'},
  './useAnnotationStore':{useAnnotationStore:store({isDirty:false})},'../services/datasetWorkflow':{},'./useDatasetStore':{useDatasetStore:dataset},
  './useFlowchartStore':{useFlowchartStore:store({isRunning:false,isLoading:false,isSaving:false})},'./useTrainingStore':{useTrainingStore:store({isTraining:false})},
  './useInspectionRunStore':{useInspectionRunStore:store({isRunning:false})},'./useModelAssistRunStore':{useModelAssistRunStore:store({activeOperations:0})},
  './projectViewState':load('../../stores/projectViewState.ts',{})};
 globalThis.localStorage={getItem:()=>null,setItem(){}};
 const m=load('../../stores/useProjectStore.ts',mocks);m.useProjectStore.setState({project:current,projectDir:current.project_dir});
 assert.equal(m.projectSwitchRefusal(),READING);
 assert.equal(await m.useProjectStore.getState().openProject(other.project_dir),false);
 assert.equal(m.useProjectStore.getState().projectError,READING,'a switch made anyway is refused with the same words');
 assert.deepEqual(opened,[],'and never reaches the backend');
 dataset.getState().isLoading=false;assert.equal(m.projectSwitchRefusal(),null);
 const before=listeners.length,stop=m.subscribeProjectSwitchRefusal(()=>{});
 assert.equal(listeners.length-before,5,'model assist, inspection, data, training and flow');stop();assert.equal(listeners.length,before);
});

function render(refusal,{busy=false}={}){
 const state={project:current,recentProjects:[{...current},other],isProjectBusy:busy,projectError:null,loadRecentProjects:async()=>{},clearProjectError(){},createProject:async()=>false,openProject:async()=>false,backupProject:async()=>null,restoreProject:async()=>false};
 const icons=new Proxy({},{get:(_,name)=>name==='__esModule'?true:()=>null});
 const {ProjectWorkspaceDialog}=load('ProjectWorkspaceDialog.tsx',{'lucide-react':icons,'../onboarding/projectPurpose':load('../onboarding/projectPurpose.ts',{}),
  '../../stores/useProjectStore':{useProjectStore:()=>state,projectSwitchRefusal:()=>refusal,subscribeProjectSwitchRefusal:()=>()=>{}},
  '../../services/hostAdapter':{host:{can:()=>false,unavailableReason:()=> '로컬 파일 선택 연결을 확인하세요.'}},'./ArtifactRetentionPanel':{ArtifactRetentionPanel:()=>null},'../../services/api':{api:{project:{}}}});
 return renderToStaticMarkup(react.createElement(ProjectWorkspaceDialog,{onClose(){}}));
}
const projectButton=(html,dir)=>{const at=html.indexOf(`title="${dir}"`);assert.ok(at>0,dir);return html.slice(html.lastIndexOf('<button',at),at);};

test('another project is offered only when switching to it would not be refused, and the reason is shown',()=>{
 const reading=render(READING);
 assert.match(projectButton(reading,other.project_dir),/ disabled=""/,'no click that the switch would refuse');
 assert.ok(reading.includes(READING),'the wait is explained before any click');
 const idle=render(null);
 assert.doesNotMatch(projectButton(idle,other.project_dir),/ disabled=""/);
 assert.match(projectButton(idle,current.project_dir),/ disabled=""/,'the open project stays not selectable');
 assert.ok(!idle.includes(READING));
 // While a switch itself runs, its own busy state speaks; the refusal line is not repeated under it.
 assert.ok(!render(READING,{busy:true}).includes(READING));
});

// Exercise submitted payloads and native controls, including direct handler calls while a guard is active.
function createFixture({viewMode='simple',refusal=null,busy=false,initialTab='create'}={}){
 let cursor=0,closed=0;const slots=[],created=[];
 const hooks={...react,useEffect(){},useRef:()=>({current:null}),useSyncExternalStore:(_subscribe,snapshot)=>snapshot(),
  useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];}};
 const state={project:current,recentProjects:[],viewMode,isProjectBusy:busy,projectError:null,loadRecentProjects:async()=>{},clearProjectError(){},
  createProject:async data=>{created.push(data);return true;},openProject:async()=>false,backupProject:async()=>null,restoreProject:async()=>false};
 const icons=new Proxy({},{get:(_,name)=>name==='__esModule'?true:()=>null});
 const mocks={react:hooks,'lucide-react':icons,'../../stores/useProjectStore':{useProjectStore:()=>state,projectSwitchRefusal:()=>refusal,subscribeProjectSwitchRefusal:()=>()=>{}},
  '../../services/hostAdapter':{host:{can:()=>false,unavailableReason:()=> '선택 불가'}},'./ArtifactRetentionPanel':{ArtifactRetentionPanel:()=>null},'../../services/api':{api:{project:{}}}};
 if(fs.existsSync(path.join(__dirname,'../onboarding/projectPurpose.ts')))mocks['../onboarding/projectPurpose']=load('../onboarding/projectPurpose.ts',{});
 const {ProjectWorkspaceDialog}=load('ProjectWorkspaceDialog.tsx',mocks);
 const nodes=tree=>{const list=[];const walk=n=>{if(!n||typeof n!=='object')return;list.push(n);react.Children.forEach(n.props?.children,walk);};walk(tree);return list;};
 const render=()=>{cursor=0;return ProjectWorkspaceDialog({initialTab,onClose:()=>closed++});};
 const all=()=>nodes(render());
 const button=label=>all().find(n=>n.type==='button'&&react.Children.toArray(n.props.children).some(child=>child===label));
 const field=placeholder=>all().find(n=>n.type==='input'&&n.props.placeholder===placeholder);
 const createTab=()=>{button('새 프로젝트').props.onClick();};
 const submit=()=>all().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});
 return{render,all,button,field,created,state,submit,createTab,closed:()=>closed};
}

test('simple creation requires an explicit purpose and unsure stays unselected with explanatory help',async()=>{
 const f=createFixture();f.createTab();f.field('예: 세라믹 표면 결함 검사').props.onChange({target:{value:' My inspection '}});
 assert.equal(f.button('프로젝트 만들기').props.disabled,true,'a project name must not silently select classification');
 await f.submit();assert.deepEqual(f.created,[]);
 const unsure=f.all().find(n=>n.type==='input'&&n.props.type==='radio'&&n.props.value==='unsure');
 assert.ok(unsure,'unsure is an explicit help choice');unsure.props.onChange({target:{value:'unsure'}});
 assert.ok(f.all().some(n=>n.props?.role==='status'),'help remains visible after choosing unsure');
 await f.submit();assert.deepEqual(f.created,[],'help must not infer a task');
});

test('each result choice creates the explicitly mapped existing task with the entered project fields',async()=>{
 for(const [purpose,task] of [['kind','classification'],['location','detection'],['shape','segmentation'],['difference','anomaly']]){
  const f=createFixture();f.createTab();f.field('예: 세라믹 표면 결함 검사').props.onChange({target:{value:' Inspection '}});
  const choices=f.all().filter(n=>n.type==='input'&&n.props.type==='radio');
  const choice=choices.find(n=>n.props.value===purpose);assert.ok(choice,'missing result choice '+purpose);
  choice.props.onChange({target:{value:purpose}});
  f.field('기본 프로젝트 폴더 사용').props.onChange({target:{value:' /projects/new '}});
  assert.equal(f.button('프로젝트 만들기').props.disabled,false);
  await f.submit();assert.deepEqual(f.created,[{name:'Inspection',task,description:'',project_dir:'/projects/new'}]);assert.equal(f.closed(),1);
 }
});

test('initial workspace destination opens create directly and full mode retains its technical task selection',async()=>{
 const f=createFixture({viewMode:'full'});
 assert.ok(f.field('예: 세라믹 표면 결함 검사'),'initial create request opens the form');
 f.field('예: 세라믹 표면 결함 검사').props.onChange({target:{value:'Advanced'}});
 const select=f.all().find(n=>n.type==='select');assert.ok(select);assert.equal(select.props.value,'classification');
 assert.equal(f.all().filter(n=>n.type==='input'&&n.props.type==='radio').length,0);
 select.props.onChange({target:{value:'segmentation'}});await f.submit();
 assert.deepEqual(f.created,[{name:'Advanced',task:'segmentation',description:''}]);
});

test('create submit preserves busy and project-switch refusals even when invoked directly',async()=>{
 for(const options of [{busy:true},{refusal:READING}]){
  const f=createFixture({viewMode:'full',...options});f.createTab();
  f.field('예: 세라믹 표면 결함 검사').props.onChange({target:{value:'Guarded'}});
  await f.submit();assert.deepEqual(f.created,[]);
 }
});
