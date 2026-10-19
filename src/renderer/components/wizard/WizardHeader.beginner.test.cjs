// A first-time user's header: the current step is announced, plain Korean names replace console jargon, and
// port/driver readouts stay behind "기술 상세" until asked for.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
const react=require('react'),{renderToStaticMarkup}=require('react-dom/server');
function load(file,mocks){const name=path.resolve(__dirname,file);const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));const original=mod.require.bind(mod);mod.require=ref=>ref in mocks?mocks[ref]:original(ref);mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,name);return mod.exports;}
const Icon=()=>null;
const icons=new Proxy({},{get:(_,name)=>name==='__esModule'?true:Icon});
function render({activeStep=2,language='ko',device='mps',technical=false,selectedProfileId=null}={}){
 const projectState={activeStep,setStep:async()=>{},task:'classification',language,setLanguage(){},backendStatus:{healthy:true,port:57944,device,deviceName:'Apple GPU'},projectName:'예제 프로젝트',projectError:null,isProjectBusy:false,technicalDetails:technical,setTechnicalDetails(){},project:{id:'p1'},projectDir:'/p'};
 const projectStore=Object.assign(select=>select?select(projectState):projectState,{getState:()=>projectState});
 const computeState={profiles:[{id:'gpu-a',name:'GPU 서버 A'}],selectedProfileId,isLoaded:true,isLoading:false,loadError:null,error:null,transportRevision:0,probeResults:{},load:async()=>{},selectTarget:async()=>{}};
 const computeStore=Object.assign(select=>select?select(computeState):computeState,{getState:()=>computeState});
 const {WizardHeader}=load('WizardHeader.tsx',{'lucide-react':icons,'../../stores/useProjectStore':{useProjectStore:projectStore},'../../stores/useComputeStore':{useComputeStore:computeStore},
  '../../stores/useTrainingStore':{useTrainingStore:()=>({jobId:null,jobComputeProfileId:null,jobComputeLabel:null})},'../../services/api':{getApiPersistenceIdentity:()=>'local'},
  '../compute/ComputeServerPanel':{ComputeServerPanel:()=>null},'./ProjectWorkspaceDialog':{ProjectWorkspaceDialog:()=>null},'../common/ProvenancePanel':{ProvenancePanel:()=>null},'./TaskChangeImpactDialog':{TaskChangeImpactDialog:()=>null,taskPreviewScope:()=>''}});
 global.window={api:{platform:'darwin'}};
 try{return renderToStaticMarkup(react.createElement(WizardHeader));}finally{delete global.window;}
}

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

test('no header text is set below 12px',()=>{
 const source=fs.readFileSync(path.resolve(__dirname,'WizardHeader.tsx'),'utf8');
 assert.ok(!/text-\[(9|10|11)px\]/.test(source),'header still uses sub-12px text classes');
});
