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
 const {ProjectWorkspaceDialog}=load('ProjectWorkspaceDialog.tsx',{'../common/useWorkspaceDialogFocus':{useWorkspaceDialogFocus:()=>()=>{}},'lucide-react':icons,'../onboarding/projectPurpose':load('../onboarding/projectPurpose.ts',{}),
  '../../stores/useProjectStore':{useProjectStore:()=>state,projectSwitchRefusal:()=>refusal,subscribeProjectSwitchRefusal:()=>()=>{}},
  '../../services/hostAdapter':{host:{can:()=>false,unavailableReason:()=> '로컬 파일 선택 연결을 확인하세요.'}},'./ArtifactRetentionPanel':{ArtifactRetentionPanel:()=>null},'../../services/api':{api:{project:{}},getProjectContextGeneration:()=>0,subscribeProjectContext:()=>()=>{}}});
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
 const mocks={react:hooks,'../common/useWorkspaceDialogFocus':{useWorkspaceDialogFocus:()=>()=>{}},'lucide-react':icons,'../../stores/useProjectStore':{useProjectStore:()=>state,projectSwitchRefusal:()=>refusal,subscribeProjectSwitchRefusal:()=>()=>{}},
  '../../services/hostAdapter':{host:{can:()=>false,unavailableReason:()=> '선택 불가'}},'./ArtifactRetentionPanel':{ArtifactRetentionPanel:()=>null},'../../services/api':{api:{project:{}},getProjectContextGeneration:()=>0,subscribeProjectContext:()=>()=>{}}};
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

const descendants=tree=>{const rows=[];const walk=node=>{if(!node||typeof node!=='object')return;rows.push(node);react.Children.forEach(node.props?.children,walk);};walk(tree);return rows;};
const nodeText=node=>react.Children.toArray(node?.props?.children).map(child=>typeof child==='object'?nodeText(child):String(child)).join('');

test('simple purpose guidance shows only the selected preparation and changes with the explicit choice',()=>{
 const f=createFixture();
 const help=()=>f.all().find(node=>node.props?.id==='project-purpose-help');
 assert.ok(help());assert.doesNotMatch(nodeText(help()),/종류별 사진|대상마다 사각형|영역의 모양/);
 const select=value=>f.all().find(node=>node.type==='input'&&node.props.value===value).props.onChange();
 select('shape');assert.match(nodeText(help()),/해당 영역의 모양을 표시/);assert.doesNotMatch(nodeText(help()),/대상마다 사각형/);
 assert.equal(f.all().filter(node=>node.type==='span'&&nodeText(node).includes('준비할 것:')).length,0,'requirements do not repeat inside every choice');
 select('location');assert.match(nodeText(help()),/대상마다 사각형과 종류/);assert.doesNotMatch(nodeText(help()),/해당 영역의 모양/);
 select('unsure');assert.equal(f.button('프로젝트 만들기').props.disabled,true);assert.equal(help().props.role,'status');
});

test('simple optional inputs stay mounted in a closed disclosure and preserve entered values through purpose changes',async()=>{
 const f=createFixture();const optional=()=>f.all().find(node=>node.type==='details'&&descendants(node).some(child=>child.type==='textarea'));
 assert.ok(optional(),'optional inputs are a native disclosure');assert.ok(!optional().props.open);
 assert.ok(descendants(optional()).some(node=>node.type==='input'&&node.props.placeholder==='기본 프로젝트 폴더 사용'));
 f.field('예: 세라믹 표면 결함 검사').props.onChange({target:{value:'Compact'}});
 f.all().find(node=>node.type==='textarea').props.onChange({target:{value:' Kept description '}});
 f.field('기본 프로젝트 폴더 사용').props.onChange({target:{value:' /projects/compact '}});
 for(const purpose of ['shape','location'])f.all().find(node=>node.type==='input'&&node.props.value===purpose).props.onChange();
 await f.submit();assert.deepEqual(f.created,[{name:'Compact',task:'detection',description:'Kept description',project_dir:'/projects/compact'}]);
 assert.ok(!optional().props.open,'rerenders do not force optional fields open');
 const full=createFixture({viewMode:'full'});assert.equal(full.all().filter(node=>node.type==='details'&&descendants(node).some(child=>child.type==='textarea')).length,0,'full mode retains visible options');
});

test('simple sidebar keeps the complete current path inside a closed keyboard-operable disclosure',()=>{
 const f=createFixture();const aside=f.all().find(node=>node.type==='aside');
 const pathDetails=descendants(aside).find(node=>node.type==='details'&&nodeText(node).includes('/projects/a'));
 assert.ok(pathDetails);assert.ok(!pathDetails.props.open);assert.ok(descendants(pathDetails).some(node=>node.type==='summary'));
 const full=createFixture({viewMode:'full'});const fullAside=full.all().find(node=>node.type==='aside');
 assert.ok(nodeText(fullAside).includes('/projects/a'));assert.equal(descendants(fullAside).filter(node=>node.type==='details').length,0);
});


test('project dialog owns Escape and keeps busy Escape and backdrop guarded',()=>{
 for(const busy of [false,true]){
  const f=createFixture({busy});const tree=f.render();
  const dialog=f.all().find(node=>node.props?.role==='dialog');
  let prevented=0,stopped=0;
  dialog.props.onKeyDown({key:'Escape',preventDefault(){prevented++;},stopPropagation(){stopped++;}});
  assert.equal(f.closed(),busy?0:1);assert.equal(prevented,1);assert.equal(stopped,1);
  const target={};tree.props.onMouseDown({target,currentTarget:target});
  assert.equal(f.closed(),busy?0:2);
 }
});

test('shared modal focus skips closed disclosure content with retained rectangles and restores a disconnected opener fallback',t=>{
 const previousGlobals=Object.fromEntries(['document','HTMLElement','HTMLDetailsElement','getComputedStyle'].map(key=>[key,globalThis[key]]));
 t.after(()=>{for(const [key,value] of Object.entries(previousGlobals)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}});
 class Element {
  constructor(tagName='BUTTON'){this.tagName=tagName;this.children=[];this.isConnected=true;this.hidden=false;}
  closest(){return this.tagName==='DIV'?this:this.parentElement?.closest();}
  getClientRects(){return[{}];}
  contains(node){return node===this||this.children.some(child=>child.contains(node));}
  focus(){document.activeElement=this;}
  append(child){this.children.push(child);child.parentElement=this;}
 }
 class Details extends Element {constructor(){super('DETAILS');this.open=false;}}
 globalThis.HTMLElement=Element;globalThis.HTMLDetailsElement=Details;globalThis.getComputedStyle=()=>({visibility:'visible'});
 const opener=new Element(),fallback=new Element(),dialog=new Element('DIV'),close=new Element(),details=new Details(),summary=new Element('SUMMARY'),hiddenPath=new Element('INPUT');
 dialog.append(close);dialog.append(details);details.append(summary);details.append(hiddenPath);
 dialog.querySelector=()=>close;dialog.querySelectorAll=()=>[close,summary,hiddenPath];
 globalThis.document={body:new Element('BODY'),activeElement:opener};
 let cleanup;const {useWorkspaceDialogFocus}=load('../common/useWorkspaceDialogFocus.ts',{react:{useLayoutEffect:effect=>{cleanup=effect();}}});
 const keydown=useWorkspaceDialogFocus({current:dialog},{restoreTarget:()=>fallback});
 assert.equal(document.activeElement,close);
 let prevented=0;keydown({key:'Tab',target:close,shiftKey:true,preventDefault(){prevented++;}});
 assert.equal(document.activeElement,summary,'retained rectangles never make the hidden input the last control');assert.equal(prevented,1);
 details.open=true;close.focus();keydown({key:'Tab',target:close,shiftKey:true,preventDefault(){}});
 assert.equal(document.activeElement,hiddenPath,'opening the disclosure adds its input to the boundary');
 opener.isConnected=false;cleanup();assert.equal(document.activeElement,fallback);
});

test('a portalled child modal keeps a middle-control Tab when React bubbles it to its parent modal',t=>{
 const keys=['document','HTMLElement','HTMLDetailsElement','getComputedStyle'];const previous=Object.fromEntries(keys.map(key=>[key,globalThis[key]]));
 t.after(()=>{for(const key of keys){if(previous[key]===undefined)delete globalThis[key];else globalThis[key]=previous[key];}});
 class Element {
  constructor(owner){this.owner=owner;this.children=[];this.isConnected=true;}
  getClientRects(){return[{}];}
  closest(){return this.owner;}
  contains(node){return node===this||this.children.includes(node);}
  focus(){document.activeElement=this;}
 }
 globalThis.HTMLElement=Element;globalThis.HTMLDetailsElement=class extends Element{};globalThis.getComputedStyle=()=>({visibility:'visible'});
 const outer=new Element(),inner=new Element();outer.owner=outer;inner.owner=inner;
 const outerFirst=new Element(outer),outerLast=new Element(outer),innerFirst=new Element(inner),innerMiddle=new Element(inner),innerLast=new Element(inner);
 outer.children=[outerFirst,outerLast];inner.children=[innerFirst,innerMiddle,innerLast];
 outer.querySelectorAll=()=>outer.children;inner.querySelectorAll=()=>inner.children;
 globalThis.document={activeElement:innerMiddle};
 const {useWorkspaceDialogFocus}=load('../common/useWorkspaceDialogFocus.ts',{react:{useLayoutEffect(){}}});
 const parentKeydown=useWorkspaceDialogFocus({current:outer}),childKeydown=useWorkspaceDialogFocus({current:inner});
 let prevented=0;const event={key:'Tab',target:innerMiddle,shiftKey:false,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;prevented++;}};
 childKeydown(event);parentKeydown(event);
 assert.equal(document.activeElement,innerMiddle,'parent must leave the child nonboundary Tab to the browser');assert.equal(prevented,0);
 innerLast.focus();event.target=innerLast;childKeydown(event);parentKeydown(event);
 assert.equal(document.activeElement,innerFirst,'child still wraps its own Tab boundary');assert.equal(prevented,1);
 outerFirst.focus();event.target=outerFirst;event.defaultPrevented=false;event.shiftKey=true;parentKeydown(event);
 assert.equal(document.activeElement,outerLast,'parent still wraps after child ownership ends');
});
