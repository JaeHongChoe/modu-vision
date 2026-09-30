/** Reopened image-grain counts and delayed statistics must share the current UI scope. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const Module=require('node:module');
const path=require('node:path');
const test=require('node:test');
const ts=require('typescript');
const {createStore}=require('zustand/vanilla');
// Keep Zustand's real state transitions while supplying a hook-free VM view.
const create=initializer=>{const store=createStore(initializer);return Object.assign(selector=>selector?selector(store.getState()):store.getState(),store);};
const root=path.resolve(__dirname,'..');
const settle=()=>new Promise(resolve=>setImmediate(resolve));

function mount() {
  const project={id:'own',project_dir:'/project',source_dataset_dir:'/source',task:'segmentation',active_labelset_id:'default'};
  const projects=create(()=>({project,task:project.task,projectDir:project.project_dir,language:'ko',openImageForLabeling(){}}));
  const requests=[];
  const api={dataset:{getImages:async()=>({items:[],total:88})}};
  const noOpStore={getState:()=>({invalidateForDataChange(){}})};
  const slots=[],effects=[];let cursor=0;
  const react={
    createElement:(type,props,...children)=>({type,props:{...props,children}}),
    useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];},
    useEffect(fn,deps){const i=cursor++;const previous=slots[i];if(!previous||deps.some((value,n)=>value!==previous.deps[n])){previous?.cleanup?.();const entry={deps};slots[i]=entry;effects.push(()=>{entry.cleanup=fn();});}},
  };
  const mocks={
    react,zustand:{create},'lucide-react':new Proxy({},{get:(_,key)=>String(key)}),
    '../services/api':{api},
    './useTrainingStore':{useTrainingStore:noOpStore},
    './useEvaluationStore':{useEvaluationStore:noOpStore},
    './useFlowchartStore':{useFlowchartStore:noOpStore},
    '../../stores/useProjectStore':{useProjectStore:projects},
    '../../services/projectPreferences':{projectPreferences:{statistics:()=>new Promise(resolve=>requests.push(resolve))}},
    '../../stores/useAnnotationStore':{useAnnotationStore:create(()=>({setImages:async()=>true}))},
    '../../services/api':{resolveApiUrl:async url=>url},
  };
  for(const name of ['ProceduralGeneratorModal','DatasetVersionPanel','DatasetWorkflowPanel','DatasetStatisticsPanel'])mocks[`./${name}`]={[name]:name};
  for(const name of ['OperatorGuidanceBanner','JargonTooltip','GuardrailBanner'])mocks[`../common/${name}`]={[name]:name};
  function load(relative) {
    const filename=path.join(root,relative),loaded=new Module(filename,module);
    loaded.filename=filename;loaded.paths=Module._nodeModulePaths(path.dirname(filename));
    loaded.require=specifier=>{
      if(Object.hasOwn(mocks,specifier))return mocks[specifier];
      if(specifier==='./classDistribution')return load('src/renderer/components/dataset/classDistribution.ts');
      if(specifier==='../../utils/datasetSplitCapability')return load('src/renderer/utils/datasetSplitCapability.ts');
      return require(specifier);
    };
    loaded._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,filename);
    return loaded.exports;
  }
  const dataset=load('src/renderer/stores/useDatasetStore.ts').useDatasetStore;
  dataset.setState({folderPath:'/source',hasSelectedFolder:true,datasetKey:'/source\0segmentation',lastImportedKey:'/source\0segmentation',classes:{Bow:111},totalImages:80,sourceImages:88,unlabeledImages:8,splitSupported:true});
  mocks['../../stores/useDatasetStore']={useDatasetStore:dataset};
  const panel=load('src/renderer/components/dataset/DatasetStatisticsPanel.tsx').DatasetStatisticsPanel;
  const studio=load('src/renderer/components/dataset/DatasetStudio.tsx').DatasetStudio;
  const events=new Map();
  global.window={addEventListener:(name,listener)=>events.set(name,listener),removeEventListener:name=>events.delete(name)};
  const render=component=>{cursor=0;const tree=component();effects.splice(0).forEach(fn=>fn());return tree;};
  const text=tree=>tree==null||typeof tree==='boolean'?'':typeof tree!=='object'?String(tree):(tree.props?.children||[]).flat(Infinity).map(text).join(' ');
  return{projects,dataset,requests,render:()=>render(panel),studio:()=>{const saved=slots.splice(0);const tree=render(studio);slots.splice(0,slots.length,...saved);return text(tree);},refresh:()=>events.get('dataset-statistics-changed')(),text};
}

const statistics=(labelset='default',classes={Bow:{count:80,ratio:80/88}})=>({total:88,labelset_id:labelset,labeling:{labeled:{count:80,ratio:80/88},unlabeled:{count:8,ratio:8/88}},assignments:{train:{count:0,ratio:0},val:{count:0,ratio:0},test:{count:0,ratio:0},not_used:{count:0,ratio:0},not_split:{count:88,ratio:1}},classes,items:[]});

test('reopened stored polygon counts are replaced by current image counts in both dataset panels',async()=>{
  const ui=mount();ui.render();
  ui.requests[0](statistics());await settle();
  assert.deepEqual(ui.dataset.getState().classes,{Bow:80});
  assert.equal(ui.dataset.getState().totalImages,80,'training inventory stays separate from the 88-source-image ratio');
  const lower=ui.studio();assert.match(lower,/80장/);assert.match(lower,/91\s*%/);assert.doesNotMatch(lower,/111장|139\s*%/);
  assert.match(ui.text(ui.render()),/88/);
});

for(const [name,change] of [
  ['project',ui=>ui.projects.setState({project:{...ui.projects.getState().project,id:'other'}})],
  ['workspace',ui=>ui.projects.setState({project:{...ui.projects.getState().project,project_dir:'/other-project'}})],
  ['source',ui=>ui.projects.setState({project:{...ui.projects.getState().project,source_dataset_dir:'/other-source'}})],
  ['project task',ui=>ui.projects.setState({project:{...ui.projects.getState().project,task:'detection'}})],
  ['renderer task',ui=>ui.projects.setState({task:'detection'})],
  ['labelset',ui=>ui.projects.setState({project:{...ui.projects.getState().project,active_labelset_id:'second'}})],
  ['selected folder',ui=>ui.dataset.setState({folderPath:'/other-source'})],
])test(`late statistics cannot overwrite counts after a ${name} switch before effect cleanup`,async()=>{
  const ui=mount();ui.render();change(ui);ui.dataset.setState({classes:{Current:3}});
  ui.requests[0](statistics());await settle();
  assert.deepEqual(ui.dataset.getState().classes,{Current:3});
  assert.doesNotMatch(ui.text(ui.render()),/Bow/,'old panel data also stays invisible');
});

test('refresh replaces stale counts and rejects a response for a different active labelset',async()=>{
  const ui=mount();ui.render();ui.requests[0](statistics('other'));await settle();
  assert.deepEqual(ui.dataset.getState().classes,{Bow:111});
  ui.refresh();ui.render();ui.requests[1](statistics());await settle();
  assert.deepEqual(ui.dataset.getState().classes,{Bow:80});
  ui.refresh();ui.render();ui.requests[2](statistics('default',{Crack:{count:2,ratio:2/88}}));await settle();
  assert.deepEqual(ui.dataset.getState().classes,{Crack:2});
});

test('statistics wait for an active import and retry when it settles',async()=>{
  const ui=mount();ui.dataset.setState({isLoading:true});ui.render();
  assert.equal(ui.requests.length,0);
  ui.dataset.setState({isLoading:false,classes:{Bow:111}});ui.render();ui.requests[0](statistics());await settle();
  assert.deepEqual(ui.dataset.getState().classes,{Bow:80});
});

test('detection image-summary counts render image units and source ratios',async()=>{
  const ui=mount();ui.projects.setState({task:'detection',project:{...ui.projects.getState().project,task:'detection'}});
  ui.dataset.setState({datasetKey:'/source\0detection',lastImportedKey:'/source\0detection'});
  ui.render();ui.requests[0](statistics());await settle();
  const lower=ui.studio();assert.match(lower,/80장/);assert.match(lower,/91\s*%/);assert.doesNotMatch(lower,/80개 객체|객체 주석 80개/);
});

test('legacy detection imports retain object units until the image summary arrives',()=>{
  const ui=mount();ui.projects.setState({task:'detection',project:{...ui.projects.getState().project,task:'detection'}});
  ui.dataset.setState({datasetKey:'/source\0detection',lastImportedKey:'/source\0detection',classCountUnit:'objects'});
  const lower=ui.studio();assert.match(lower,/111개 객체/);assert.match(lower,/100\s*%/);assert.match(lower,/라벨된 이미지 80장/);
});
