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
 const {ProjectWorkspaceDialog}=load('ProjectWorkspaceDialog.tsx',{'lucide-react':icons,
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
