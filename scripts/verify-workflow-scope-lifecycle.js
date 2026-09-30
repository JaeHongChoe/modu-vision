const assert=require('node:assert/strict'),fs=require('node:fs'),Module=require('node:module'),path=require('node:path');
global.document={body:{}};
global.window={setTimeout:()=>0,clearTimeout(){},addEventListener(){},removeEventListener(){}};
// Exercise real component event/effect code with controlled async responses.
const root=path.resolve(__dirname,'..');const ts=require('typescript');
function harness(file,exportName,props,api,project,images=[]){
 const slots=[],effects=[];let cursor=0;
 const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;}];},useRef(initial){const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect(fn,deps){const i=cursor++;const old=slots[i];if(!old||deps.some((d,n)=>d!==old.deps[n])){old?.cleanup?.();const entry={deps};slots[i]=entry;effects.push(()=>entry.cleanup=fn());}}};
 const ds={folderPath:'/sourceA',images};const store=s=>Object.assign(selector=>selector(s),{getState:()=>s});const stubs={react,'lucide-react':new Proxy({},{get:(_,k)=>String(k)}),'react-dom':{createPortal:x=>x},'../../services/api':api,'../../stores/useProjectStore':{useProjectStore:store(project)},'../../stores/useDatasetStore':{useDatasetStore:store(ds)},'../../stores/useAnnotationStore':{useAnnotationStore:store({currentImage:images[0]})}};
 stubs['../evaluation/SpecializedApprovalPanel']={SpecializedApprovalPanel:()=>null};
 stubs['./WarmStartSelector']={WarmStartSelector:()=>null};
 const filename=root+'/'+file;const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));m.require=spec=>stubs[spec]||require(spec);m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,filename);
 const render=()=>{global.React=react;cursor=0;const tree=m.exports[exportName](props);effects.splice(0).forEach(fn=>fn());return tree;};const flatten=tree=>!tree||typeof tree!=='object'?[]:[tree,...(tree.props?.children||[]).flat(Infinity).flatMap(flatten)];const text=tree=>(tree?.props?.children||[]).flat(Infinity).map(x=>typeof x==='string'?x:x&&typeof x==='object'?text(x):'').join('');const find=(tree,kind,label)=>flatten(tree).find(x=>x.type===kind&&(x.props['aria-label']===label||text(x).includes(label)));return{render,flatten,text,find,ds,slots};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const project={project:{id:'A',active_labelset_id:'default'}};
 let count=0;const u=harness('src/renderer/components/common/ProvenancePanel.tsx','ProvenancePanel',{onClose(){}},{request:async()=>{count++;return{project:{id:'A',name:'A'},image:{},label:{workflow_state:'approved',history:[]},split:{},dataset_versions:[],models:[],flows:[],inspections:[]};}},project,[{file_path:'/sourceA/a.png',file_name:'a.png'}]);
 u.render();await tick();u.render();project.project={...project.project,active_labelset_id:'second'};u.render();await tick();console.log('PROVENANCE_LABELSET_SWITCH_REQUESTS',count,'expected2');assert.equal(count,2);
 const p={project:{id:'A'}};let finishB;const e=harness('src/renderer/components/training/EnhancementWorkbench.tsx','EnhancementWorkbench',{}, {request:async url=>{if(url.endsWith('/models'))return p.project.id==='A'?{models:[{job_id:'Amodel',metadata:{source_dataset_path:'/sourceA',best_epoch:1}}]}:new Promise(resolve=>finishB=resolve);return{jobs:[]};}},p);
 e.render();await tick();e.render();p.project={id:'B'};e.ds.folderPath='/sourceB';e.render();const old=e.flatten(e.render()).find(x=>x.type==='option'&&x.props.value==='Amodel');console.log('ENHANCEMENT_OLD_PROJECT_OPTION_WHILE_LOADING',!!old);assert.equal(old,undefined);finishB({models:[]});await tick();
 let resolveHistory;const props={sourceFolder:'/sourceA',task:'detection',jobId:'model'};const h=harness('src/renderer/components/evaluation/EvaluationHistoryPanel.tsx','EvaluationHistoryPanel',props,{request:()=>new Promise(resolve=>resolveHistory=resolve)}, {project:{id:'A'}});
 h.render();h.render();props.sourceFolder='';h.render();resolveHistory({items:[]});await tick();console.log('HISTORY_BUSY_WITHOUT_SOURCE',h.find(h.render(),'button','평가 이력 새로고침').props.disabled);assert.equal(h.find(h.render(),'button','평가 이력 새로고침').props.disabled,false);
 global.window={setTimeout:()=>0,clearTimeout(){},addEventListener(){},removeEventListener(){}};let resolveCancel;
 const cprops={projectDir:'/projectA',sourceFolder:'/sourceA',task:'detection',language:'ko'};const comparisonApi={comparisonModels:async()=>({models:[]}),listComparisons:async()=>({comparisons:[]}),getComparison:async()=>({})};
 const c=harness('src/renderer/components/evaluation/ModelComparisonPanel.tsx','ModelComparisonPanel',cprops,{api:{evaluation:comparisonApi},request:async url=>{if(url.includes('/jobs/Ajob?')&&url.includes('sourceB'))throw new Error('Job not in this project');return url.includes('/cancel')?new Promise(resolve=>resolveCancel=resolve):url.includes('/jobs?')?{jobs:cprops.projectDir==='/projectA'?[{job_id:'Ajob',status:'running',completed_images:0,total_images:2,cancel_requested:0}]:[]}:{job_id:'Ajob',status:'running',completed_images:0,total_images:2,cancel_requested:0};}},{});
 c.render();await tick();c.render();await tick();c.find(c.render(),'button','비교 중단').props.onClick();cprops.projectDir='/projectB';cprops.sourceFolder='/sourceB';c.render();await tick();resolveCancel({job_id:'Ajob',status:'running',completed_images:0,total_images:2,cancel_requested:1});await tick();c.render();const reopened=c.slots.find(x=>x&&x.job_id==='Ajob');console.log('COMPARISON_CROSSPROJECT_CANCEL_REINSERTED_JOB',!!reopened);assert.equal(reopened,undefined);
 const serviceProps={projectDir:'/projectA'};let finishServiceB;let serviceBReady=false;const servicePuts=[];const serviceB={runtime:{status:'stopped'},active:null,history:[],port:10000,adapter_config:{enabled:true,modbus:null,mes:{url:'http://project-B-mes',token:null},clear_mes_token:false}};
 const r=harness('src/renderer/components/runtime/RuntimeServicePanel.tsx','RuntimeServicePanel',serviceProps,{request:async(url,options)=>{if(options?.method==='PUT'){servicePuts.push(JSON.parse(options.body));return{};}return serviceProps.projectDir==='/projectA'?{runtime:{status:'stopped'},active:null,history:[],port:9999,adapter_config:{enabled:true,modbus:null,mes:{url:'http://project-A-mes',token:null}}}:serviceBReady?serviceB:new Promise(resolve=>finishServiceB=resolve);}},{});
 r.render();await tick();r.render();serviceProps.projectDir='/projectB';r.render();const fresh=r.render();
 console.log('RUNTIME_PREVIOUS_PROJECT_CONFIG_WHILE_LOADING',r.find(fresh,'textarea','PLC MES 설정').props.value.includes('project-A-mes'));
 console.log('RUNTIME_CAN_SAVE_ADAPTERS_BEFORE_LOADING',!r.find(fresh,'button','설정 검증 후 저장').props.disabled);
 assert.equal(r.find(fresh,'textarea','PLC MES 설정').props.value.includes('project-A-mes'),false);
 assert.equal(r.find(fresh,'button','설정 검증 후 저장').props.disabled,true);
 serviceBReady=true;finishServiceB(serviceB);await tick();
 assert.equal(r.find(r.render(),'button','설정 검증 후 저장').props.disabled,false);
 r.find(r.render(),'button','설정 검증 후 저장').props.onClick();await tick();
 assert.equal(servicePuts.at(-1).mes.token,null);
 assert.equal(servicePuts.at(-1).clear_mes_token,false);
 r.find(r.render(),'input','저장된 MES 인증값 삭제').props.onChange({target:{checked:true}});
 r.find(r.render(),'button','설정 검증 후 저장').props.onClick();await tick();
 assert.equal(servicePuts.at(-1).clear_mes_token,true);
 assert.equal(r.find(r.render(),'input','저장된 MES 인증값 삭제').props.checked,false);
 console.log('RUNTIME_EXPLICIT_SECRET_CLEAR_PAYLOAD',servicePuts.at(-1).clear_mes_token);
})().catch(e=>{console.error(e);process.exit(1);});
