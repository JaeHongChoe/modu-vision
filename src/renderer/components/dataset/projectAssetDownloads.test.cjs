const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');
const tick = () => new Promise(setImmediate);
function harness(name) {
  const file = path.join(__dirname, name + '.tsx');
  const source = process.env.MV_ASSET_HANDLERS_BASE
    ? require('node:child_process').execFileSync('git', ['show', 'HEAD:src/renderer/components/dataset/' + name + '.tsx'], {cwd:path.resolve(__dirname, '../../../..'), encoding:'utf8'})
    : fs.readFileSync(file, 'utf8');
  let cursor = 0, generation = 0, holdDownload;
  const slots = [], effects = [], downloads = [], exports = [], listeners = [];
  const project = {project:{id:'p',project_dir:'/owned',task:'classification',source_dataset_dir:'/source'},projectDir:'/owned',task:'classification',isProjectBusy:false};
  const dataset = {folderPath:'/source',hasSelectedFolder:true,annotationsChanged:async()=>{},loadImages:async()=>{}};
  const annotation = {reviewerName:'fixture',setReviewerName(){}};
  const react = {
    createElement:(type,props,...children)=>({type,props:props||{},children:children.flat()}),
    useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v];},
    useRef(initial){const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},
    useEffect(fn,deps){const i=cursor++;if(!slots[i]||deps.some((v,j)=>v!==slots[i].deps[j])){slots[i]?.cleanup?.();slots[i]={deps};effects.push(()=>slots[i].cleanup=fn());}}
  };
  const store = state => Object.assign(selector=>selector?selector(state):state,{getState:()=>state});
  const workflow = {
    list:async()=>({items:[],total:0}),duplicates:async()=>({duplicates:[]}),savedSplit:async()=>({availability:'unavailable'}),
    export:async(...args)=>{exports.push(args);return {download_url:'/api/dataset/formats/download/annotations_'+'a'.repeat(32),image_count:2,annotation_count:3};},
    exportMasks:async(...args)=>{exports.push(args);return {download_url:'/api/dataset/masks/download/masks_'+'b'.repeat(32),image_count:2,annotation_count:3};}
  };
  const api = {getProjectContextGeneration:()=>generation,getApiPersistenceIdentity:()=> 'local',subscribeProjectContext:fn=>{listeners.push(fn);return()=>{};},
    projectAssetDownloadNotice:(images,labels,contents='라벨')=>`${images}개 이미지 · ${labels}개 ${contents} ZIP 생성 완료 · 다운로드 요청 (브라우저 다운로드 목록을 확인하세요)`,downloadProjectAsset:async(...args)=>{downloads.push(args);if(holdDownload)await holdDownload;},resolveApiUrl:p=>p};
  const m = new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);
  m.require = ref => ref==='react'?react:ref.includes('services/api')?api:ref.includes('datasetWorkflow')?{datasetWorkflow:workflow,workflowError:e=>e.message}:
    ref.includes('useDatasetStore')?{useDatasetStore:store(dataset)}:ref.includes('useProjectStore')?{useProjectStore:store(project)}:
    ref.includes('useAnnotationStore')?{useAnnotationStore:store(annotation)}:ref.includes('useStepSheetStore')?{useStepSheetStore:Object.assign(s=>s({open:null}),{getState:()=>({show(){}})})}:
    ref.includes('projectPreferences')?{projectPreferences:{read:async()=>({tag_colors:{}})}}:ref.includes('hostAdapter')?{host:{}}:
    ref.endsWith('ProjectPreferencePanel')?{ProjectPreferencePanel:()=>null}:ref.endsWith('ExternalMaskExchangePanel')?{ExternalMaskExchangePanel:()=>null}:Module.prototype.require.call(m,ref);
  m._compile(ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.React,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);
  const render = () => {cursor=0;const tree=m.exports[name]();effects.splice(0).forEach(fn=>fn());return tree;};
  const nodes = tree => {const result=[];function walk(n){if(n&&typeof n==='object'){result.push(n);n.children?.forEach(walk);}}walk(tree);return result;};
  const button = label => nodes(render()).find(n=>n.type==='button'&&n.children.includes(label));
  return {render,nodes,button,downloads,exports,hold:promise=>holdDownload=promise,advance(){generation++;listeners.forEach(fn=>fn());}};
}
async function setup(name) {
  global.window={addEventListener(){},removeEventListener(){}};
  const h=harness(name);h.render();
  if(name==='DatasetWorkflowPanel'){h.button('이미지 검토·그룹 분할·라벨 교환').props.onClick();h.render();await tick();}
  return h;
}
test('actual label export handler dispatches the bounded ZIP helper with its format filename',async()=>{
  const h=await setup('DatasetWorkflowPanel');h.button('라벨 내보내기').props.onClick();await tick();
  assert.deepEqual(h.downloads.map(args=>args.slice(0,2)),[['/api/dataset/formats/download/annotations_'+'a'.repeat(32),'labelme_annotations.zip']]);assert.equal(h.downloads[0][2],h.exports[0][1],'export AbortSignal must reach child preparation');
  assert.equal(h.exports[0][0],'labelme');assert(h.exports[0][1] instanceof AbortSignal);
  assert(h.nodes(h.render()).some(n=>n.props.role==='status'&&n.children.join('').includes('2개 이미지 · 3개 라벨 ZIP 생성 완료 · 다운로드 요청 (브라우저 다운로드 목록을 확인하세요)')));
});
test('actual label export keeps its context fence across the asynchronous download dispatcher',async()=>{
  const h=await setup('DatasetWorkflowPanel');let finish;h.hold(new Promise(r=>finish=r));
  h.button('라벨 내보내기').props.onClick();await tick();assert.equal(h.downloads.length,1);
  h.advance();h.render();finish();await tick();
  assert(!h.nodes(h.render()).some(n=>n.props.role==='status'&&n.children.join('').includes('ZIP 생성 완료')));
});
test('actual pixel-mask export handler dispatches the bounded ZIP helper while preserving source-image selection',async()=>{
  const h=await setup('ExternalMaskExchangePanel');h.button('pixel mask 내보내기').props.onClick();await tick();
  assert.deepEqual(h.exports,[[true]]);
  assert.deepEqual(h.downloads,[['/api/dataset/masks/download/masks_'+'b'.repeat(32),'pixel_masks_with_source_mapping.zip']]);
  assert(h.nodes(h.render()).some(n=>n.props.role==='status'&&n.children.join('').includes('PNG·palette·원본 mapping ZIP 생성 완료 · 다운로드 요청 (브라우저 다운로드 목록을 확인하세요)')));
});

test('actual label export cancellation reaches the pending download dispatcher without a success notice',async()=>{
  const h=await setup('DatasetWorkflowPanel');let finish;h.hold(new Promise(resolve=>finish=resolve));h.button('라벨 내보내기').props.onClick();await tick();
  const signal=h.downloads[0][2];assert(signal instanceof AbortSignal);assert.equal(signal,h.exports[0][1]);h.button('내보내기 요청 취소').props.onClick();assert.equal(signal.aborted,true);finish();await tick();
  const statuses=h.nodes(h.render()).filter(n=>n.props.role==='status').map(n=>n.children.join('')).join(' ');assert(!statuses.includes('ZIP 생성 완료'));assert(statuses.includes('다운로드를 시작하지 않았습니다'));
});
