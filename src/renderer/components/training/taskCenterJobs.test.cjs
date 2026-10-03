const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// S2-09: the task center reads each job the way its workbench does, and gives a model family's next action only to a
// model family's job.
function load(file,mocks={}){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
  m.require=ref=>ref in mocks?mocks[ref]:['./jobProgress','./taskCenterModel','./taskHandoff'].includes(ref)?load(ref+'.ts'):require(ref);
  m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports;}
const memory=()=>{const values=new Map();return{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};};
const rows=[
  {kind:'labeling-batch',job_id:'batch',task:'labeling',status:'interrupted',error:'proposals stopped; partial proposals remain reviewable',source_dataset_path:'/source',training_provenance:{labelset_id:'default'}},
  {kind:'ocr',job_id:'ocr-lost',task:'ocr',status:'interrupted',epoch:1,epochs:4,error:'Application stopped before training completed',source_dataset_path:'/source',training_provenance:{labelset_id:'default'}},
  {kind:'ocr',job_id:'ocr-stuck',task:'ocr',status:'stopping',cancel_supported:false,source_dataset_path:'/source',training_provenance:{labelset_id:'default'}},
];
async function renderCenter(){
  let cursor=0;const slots=[],effects=[];const state={projectDir:'/project',project:{id:'p',source_dataset_dir:'/source',active_labelset_id:'default'},task:'detection',isProjectBusy:false,setStep:async()=>{},setTask:async()=>({ok:true})};
  const useProjectStore=Object.assign(sel=>sel?sel(state):state,{getState:()=>state});
  const react={useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v];},
    useRef:initial=>{const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},
    useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||deps.some((v,j)=>v!==slots[i][j])){slots[i]=deps;effects.push(fn);}}};
  const jsx=(type,props)=>({type,props:props||{},children:[props?.children].flat().filter(child=>child!==undefined&&child!==null&&child!==false)});
  const m=load('TaskCenter.tsx',{react,'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':{},
    '../../services/api':{getApiPersistenceIdentity:()=>'local',request:async()=>({tasks:rows,reservations:[],errors:[],source_dataset_path:'/source',labelset_id:'default'})},
    '../../stores/useProjectStore':{useProjectStore},'../../stores/useComputeStore':{useComputeStore:Object.assign(sel=>sel({transportRevision:0,profiles:[],selectedProfileId:null}),{getState:()=>({transportRevision:0,selectedProfileId:null})})},
    './ProgramWorkbenchControls':{programInput:'',programButton:''}});
  const render=()=>{cursor=0;const tree=m.TaskCenter({initialOpen:true});effects.splice(0).forEach(fn=>fn());return tree;};
  const nodesOf=tree=>{const nodes=[];const walk=n=>{if(n&&typeof n==='object'){nodes.push(n);n.children?.flat().forEach(walk);}};walk(tree);return nodes;};
  const text=tree=>{const parts=[];const walk=n=>{if(typeof n==='string'||typeof n==='number')parts.push(String(n));else if(n&&typeof n==='object')n.children?.flat().forEach(walk);};walk(tree);return parts.join('');};
  render();await new Promise(setImmediate);
  const open=key=>{nodesOf(render()).find(n=>n.props['aria-label']==='저장 작업 다시 열기').props.onChange({target:{value:key}});const tree=render();return {text:text(tree),nodes:nodesOf(tree)};};
  return open;
}

test('S2-09: the task center gives the family next action only to a model family job, never to a labeling batch',async()=>{
  const oldStorage=global.localStorage,oldInterval=global.setInterval;global.localStorage=memory();global.setInterval=()=>0;
  try{
    const open=await renderCenter();
    const batch=open('labeling-batch:local:batch');
    assert.match(batch.text,/proposals stopped; partial proposals remain reviewable/,'its own recorded cause');
    assert.doesNotMatch(batch.text,/다시 학습하세요/,'a labeling batch is not told to train again');
    assert.doesNotMatch(batch.text,/다음 행동:/);
    const family=open('ocr:local:ocr-lost');
    assert.match(family.text,/실행 주체 없음 · 재개 확인 필요/);assert.match(family.text,/문자 인식/);assert.match(family.text,/Epoch 1\/4/);
    assert.match(family.text,/다음 행동: .*다시 학습하세요/);
    assert.equal(family.nodes.filter(n=>n.type==='button'&&n.children.flat().some(c=>typeof c==='string'&&c.includes('취소 요청'))).length,0,'an interrupted job has nothing to cancel');
  }finally{global.localStorage=oldStorage;global.setInterval=oldInterval;}
});

test('S2-09: a stopping job that says it cannot be cancelled shows no pending-stop button in the task center',async()=>{
  const oldStorage=global.localStorage,oldInterval=global.setInterval;global.localStorage=memory();global.setInterval=()=>0;
  try{
    const open=await renderCenter();
    const stuck=open('ocr:local:ocr-stuck');
    assert.match(stuck.text,/취소 요청 · 종료 확인 중/);
    assert.equal(stuck.nodes.filter(n=>n.type==='button'&&n.children.flat().some(c=>typeof c==='string'&&c.includes('취소 요청'))).length,0);
  }finally{global.localStorage=oldStorage;global.setInterval=oldInterval;}
});
