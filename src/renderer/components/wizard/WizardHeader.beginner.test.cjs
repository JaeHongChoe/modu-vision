// A first-time user's header: the current step is announced, plain Korean names replace console jargon,
// port/driver readouts stay behind "기술 상세" until asked for, and 간단히 보기 is one row with a 더 보기 menu.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
const react=require('react'),{renderToStaticMarkup}=require('react-dom/server');
function load(file,mocks){const name=path.resolve(__dirname,file);const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));const original=mod.require.bind(mod);mod.require=ref=>ref in mocks?mocks[ref]:original(ref);mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,name);return mod.exports;}
const Icon=()=>null;
const icons=new Proxy({},{get:(_,name)=>name==='__esModule'?true:Icon});
function header({activeStep=2,language='ko',device='mps',technical=false,selectedProfileId=null,viewMode}={},extra={}){
 const projectState={activeStep,setStep:async()=>{},task:'classification',language,setLanguage(){},backendStatus:{healthy:true,port:57944,device,deviceName:'Apple GPU'},projectName:'예제 프로젝트',projectError:null,isProjectBusy:false,technicalDetails:technical,setTechnicalDetails(){},project:{id:'p1'},projectDir:'/p',viewMode};
 const projectStore=Object.assign(select=>select?select(projectState):projectState,{getState:()=>projectState});
 const computeState={profiles:[{id:'gpu-a',name:'GPU 서버 A'}],selectedProfileId,isLoaded:true,isLoading:false,loadError:null,error:null,transportRevision:0,probeResults:{},load:async()=>{},selectTarget:async()=>{}};
 const computeStore=Object.assign(select=>select?select(computeState):computeState,{getState:()=>computeState});
 return load('WizardHeader.tsx',{'lucide-react':icons,'../../stores/useProjectStore':{useProjectStore:projectStore},'../../stores/useComputeStore':{useComputeStore:computeStore},
  '../../stores/useTrainingStore':{useTrainingStore:()=>({jobId:null,jobComputeProfileId:null,jobComputeLabel:null})},'../../services/api':{getApiPersistenceIdentity:()=>'local'},
  '../compute/ComputeServerPanel':{ComputeServerPanel:()=>null},'./ProjectWorkspaceDialog':{ProjectWorkspaceDialog:()=>null},'../common/ThumbnailDiagnostics':{ThumbnailDiagnostics:()=>null},'../common/ProvenancePanel':{ProvenancePanel:()=>null},'./TaskChangeImpactDialog':{TaskChangeImpactDialog:()=>null,taskPreviewScope:()=>''},...extra}).WizardHeader;
}
function render({props,...state}={}){
 const WizardHeader=header(state);
 global.window={api:{platform:'darwin'}};
 try{return renderToStaticMarkup(react.createElement(WizardHeader,props));}finally{delete global.window;}
}
// The header as an element tree under a hook stub (no DOM, no effects), so the 더 보기 menu's own handlers can be called.
function mount(state){
 const states=[];let cursor=0;
 const stub={Fragment:'fragment',createElement:(type,props,...children)=>({type,props:{...props,children}}),useEffect(){},
  useState(initial){const index=cursor++;if(!(index in states))states[index]=initial;return[states[index],value=>{states[index]=typeof value==='function'?value(states[index]):value;}];}};
 const WizardHeader=header(state,{react:{__esModule:true,default:stub,...stub}});
 return()=>{cursor=0;return WizardHeader({});};
}
const nodes=value=>Array.isArray(value)?value.flatMap(nodes):value&&typeof value==='object'?[value,...nodes(value.props?.children)]:[];
// The opening tag of the closest <div> before a marker: the group (or wrapper) the marked control sits in.
const divBefore=(html,marker)=>{const open=html.lastIndexOf('<div class="',html.indexOf(marker));return html.slice(open,html.indexOf('>',open)+1);};
// A <details> stand-in: whether it is open, what it contains and what took focus.
function fakeMenu(){const focused=[],inside=new Set();const summary={focus(){focused.push('summary');}};inside.add(summary);
 return{open:true,focused,inside,querySelector:selector=>selector==='summary'?summary:null,contains:node=>inside.has(node)};}

test('the active step is announced to assistive technology and the step list has a Korean name',()=>{
 const html=render({activeStep:2});
 assert.match(html,/aria-label="작업 단계[^"]*"/);
 assert.match(html,/aria-current="step"[^>]*>(?:(?!<\/button>).)*라벨링 &amp; AI/s);
 assert.equal((html.match(/aria-current="step"/g)||[]).length,1);
});

test('console readouts (port, driver badges, RECIPE) are hidden until 기술 상세 is turned on',()=>{
 const plain=render({technical:false});
 for(const jargon of ['PORT:','METAL GPU MPS','HW ACCEL','RECIPE:','CPU FALLBACK','This computer','Compute:'])assert.ok(!plain.includes(jargon),`beginner header shows ${jargon}`);
 assert.match(plain,/이 컴퓨터/);
 assert.match(plain,/GPU 가속/);
 const technical=render({technical:true});
 assert.match(technical,/57944/);
 assert.match(technical,/mps/i);
});

test('the compute location is a labelled Korean control and a missing server is named in Korean',()=>{
 const html=render({selectedProfileId:'gone'});
 assert.match(html,/aria-label="실행 위치"/);
 assert.match(html,/<option[^>]*value=""[^>]*>이 컴퓨터<\/option>/);
 assert.match(html,/연결되지 않은 서버/);
 assert.ok(!html.includes('Missing server'));
});

test('간단히 보기 is one row: the project, the six steps, 모두 보기 and a 더 보기 menu that keeps every control by name',()=>{
 const html=render({props:{simpleControls:react.createElement('button',{type:'button'},'처음 시작 안내'),simpleMenu:react.createElement('button',{type:'button'},'운영자 검사 화면')}});
 assert.equal((html.match(/<nav /g)||[]).length,1);
 assert.ok(!html.includes('bg-[#131822] px-4 py-2'),'the second row renders only in 모두 보기');
 assert.equal((html.match(/<details/g)||[]).length,1);
 const steps=html.indexOf('<nav '),menu=html.indexOf('<details'),toggle=html.indexOf('>모두 보기<');
 assert.ok(html.indexOf('title="프로젝트 관리"')<steps&&steps<toggle&&toggle<menu,'project, steps, 모두 보기, then 더 보기');
 const row=html.slice(0,menu),panel=html.slice(menu);
 for(const name of ['aria-label="실행 위치"','aria-label="검사 작업 종류"','aria-label="언어 전환 (KR / EN)"','>서버 관리<','>데이터·판정 이력<','>기술 상세<','>밝은 화면<','>처음 시작 안내<','>운영자 검사 화면<','role="status"'])assert.ok(panel.includes(name)&&!row.includes(name),`${name} is not (only) under 더 보기`);
 assert.match(panel,/<summary[^>]*>더 보기<\/summary>/);
 assert.ok(!html.includes('workspace-button--primary'),'the header carries no primary action');
 assert.match(html,/<summary class="[^"]*workspace-button[^"]*"/);assert.match(html,/aria-pressed="false"[^>]*class="workspace-button whitespace-nowrap"/,'simple-view controls use the shared secondary button');
});

test('모두 보기 keeps its two rows and the row controls outside any menu',()=>{
 const html=render({viewMode:'full'});
 assert.ok(!html.includes('<details'),'no 더 보기 menu in 모두 보기');
 const row2=html.indexOf('bg-[#131822] px-4 py-2');
 assert.ok(row2>0,'the second row is there');assert.equal((html.match(/<nav /g)||[]).length,1);assert.ok(row2<html.indexOf('<nav '),'the steps sit in the second row');
 for(const name of ['>서버 관리<','>데이터·판정 이력<','>기술 상세<','>간단히 보기<','aria-label="실행 위치"','aria-label="언어 전환 (KR / EN)"','role="status"'])assert.ok(html.indexOf(name)>0&&html.indexOf(name)<row2,`${name} is not in the first row`);
 assert.ok(html.indexOf('aria-label="검사 작업 종류"')>row2,'the inspection type stays in the second row');
});

// At the minimum window (1280, Windows padding) the row needs more than one line and 모두 보기 / 더 보기 wrap alone onto
// the next one. justify-between puts a lone item at the line start, and the menu panel, anchored at the group's right
// edge, then opened at x=-89 with every item cut off.
test('간단히 보기 keeps 모두 보기 and 더 보기 at the right edge when they wrap alone, so the menu opens inside the window',()=>{
 const simple=render(),full=render({viewMode:'full'});
 assert.match(divBefore(simple,'>모두 보기<'),/class="[^"]*\bml-auto\b/,'the simple-view group is pushed to the right edge on any line');
 assert.match(divBefore(simple,'<nav '),/class="[^"]*\bml-auto\b/,'the step bar shares the free space, so one line keeps its spacing');
 assert.match(simple,/<details class="relative"><summary/,'the panel is still anchored to the 더 보기 group');assert.match(simple,/<div class="absolute right-0 /);
 assert.equal(divBefore(full,'>간단히 보기<'),'<div class="flex flex-wrap items-center gap-2" style="-webkit-app-region:no-drag">','모두 보기 keeps its group as it was');
});

// The step wrapper was shrink-0, so the bar kept its one-line width (951px with five finished steps) and its own
// flex-wrap never applied: at 1024 px with macOS padding '06 검사 실행' ran past the overflow-hidden app frame.
test('the simple-view step bar may shrink to the window width, so its steps wrap inside it instead of running past the edge',()=>{
 const wrapper=divBefore(render({props:{completedSteps:[1,2,3,4,5]},activeStep:6}),'<nav ');
 assert.match(wrapper,/class="[^"]*\bmin-w-0\b/,'the wrapper can shrink below its one-line width');
 assert.doesNotMatch(wrapper,/\bshrink-0\b/,'a shrink-0 wrapper keeps the bar at its one-line width');
 assert.match(render(),/<nav class="flex flex-wrap /,'the bar wraps its steps once it is narrower than one line');
});

// Choosing an item used to close the menu with focus still on that (now hidden) item: a dialog opened from it returned
// focus to nothing (<body>), and so did 기술 상세, the theme and the language. Tabbing out left the panel open over the
// next control.
test('choosing a 더 보기 item closes the menu and puts focus on 더 보기, and focus leaving the menu closes it',()=>{
 global.window={api:{platform:'darwin'}};
 try{
  const draw=mount();
  const details=tree=>nodes(tree).find(node=>node.type==='details');
  const menu=fakeMenu();details(draw()).props.ref(menu);
  const open=details(draw()),panel=nodes(open.props.children).find(node=>node.type==='div'&&typeof node.props.onClick==='function');
  panel.props.onClick({target:{closest:selector=>selector==='button'?{}:null}});
  assert.equal(menu.open,false,'choosing an item closes the menu');
  assert.deepEqual(menu.focused,['summary'],'focus moves to 더 보기 before the item opens its dialog');
  const select=fakeMenu();details(draw()).props.ref(select);
  nodes(details(draw()).props.children).find(node=>node.type==='div'&&typeof node.props.onClick==='function').props.onClick({target:{closest:()=>null}});
  assert.equal(select.open,true,'a click on a select or a label inside the panel keeps it open');assert.deepEqual(select.focused,[]);
  // A new inspection type opens the change dialog, which hands focus back to whatever had it: 더 보기, not the hidden select.
  nodes(details(draw()).props.children).find(node=>node.type==='select'&&node.props['aria-label']==='검사 작업 종류').props.onChange({target:{value:'detection'}});
  assert.equal(select.open,false,'choosing an inspection type closes the menu');assert.deepEqual(select.focused,['summary']);
  const blur=open.props.onBlur;assert.equal(typeof blur,'function','the menu closes when focus leaves it');
  const leaving=fakeMenu(),item={};leaving.inside.add(item);
  blur({currentTarget:leaving,relatedTarget:item});assert.equal(leaving.open,true,'moving between items keeps it open');
  blur({currentTarget:leaving,relatedTarget:null});assert.equal(leaving.open,true,'no next target (a click elsewhere) is the outside-click handler\'s case');
  blur({currentTarget:leaving,relatedTarget:{}});assert.equal(leaving.open,false,'Tab past the last item closes it');
 }finally{delete global.window;}
});

test('the 더 보기 panel ends above the window bottom and scrolls, without squeezing its items',()=>{
 global.window={api:{platform:'darwin'},innerHeight:640};
 try{
  const open=nodes(mount()()).find(node=>node.type==='details'),panel={style:{},getBoundingClientRect:()=>({top:163})};
  open.props.onToggle({currentTarget:{open:true,lastElementChild:panel}});
  assert.equal(panel.style.maxHeight,'469px','window height minus the panel top and an 8px margin');
  const closed={style:{},getBoundingClientRect:()=>({top:163})};open.props.onToggle({currentTarget:{open:false,lastElementChild:closed}});assert.equal(closed.style.maxHeight,undefined);
 }finally{delete global.window;}
 assert.match(render(),/<div class="absolute right-0 [^"]*\boverflow-y-auto\b[^"]*\*:shrink-0"/);
});

test('no header text is set below 12px',()=>{
 const source=fs.readFileSync(path.resolve(__dirname,'WizardHeader.tsx'),'utf8');
 assert.ok(!/text-\[(9|10|11)px\]/.test(source),'header still uses sub-12px text classes');
});
