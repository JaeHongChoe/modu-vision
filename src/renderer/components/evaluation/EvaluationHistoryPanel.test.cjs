const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>Array.isArray(t)?t.flatMap(nodes):t&&typeof t==='object'?[t,...nodes(t.props?.children)]:[];
const text=n=>[n?.props?.children].flat(Infinity).map(x=>typeof x==='object'?text(x):x??'').join('');
function storage(){const values=new Map();return{values,getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};}
function record(id,labelset='default',task='classification'){return{evaluation_id:id,binding:{labelset_id:labelset},result:{task,job_id:'model-'+id,test_predictions:[]},grouped_errors:{product:{},lot:{},ground_truth:{}}};}
function fixture(config={}){
 let index=0,tree,dirty=true;const slots=[],effects=[],requests=[],catalogs=[];
 const compute={selectedProfileId:null,transportRevision:1,...config.compute};
 const project={project:{id:'project-one',active_labelset_id:'default'}};
 const props={sourceFolder:'/source-one',task:'classification',jobId:null};
 const local=config.storage||storage();let identity='local';
 const react={useState(value){const i=index++;if(!(i in slots))slots[i]=typeof value==='function'?value():value;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;dirty=true;}];},useRef(value){const i=index++;return slots[i]??(slots[i]={current:value});},useEffect(fn,deps){const i=index++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 const hook=state=>selector=>selector?selector(state):state;
 function load(file){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const req=m.require.bind(m);m.require=n=>n==='react'?react:n.endsWith('/services/api')?{getApiPersistenceIdentity:()=>identity,api:{project:{listLabelsets:async()=>{catalogs.push({projectId:project.project.id,transportRevision:compute.transportRevision,identity});return config.listLabelsets?config.listLabelsets():{labelsets:config.labelsets||[{id:'default',name:'Default'},{id:'second',name:'Second'}]};}}},request:async(url,options)=>{requests.push({url,options});if(options)throw Error('Opening history must not dispatch');const q=new URL(url,'http://local').searchParams;const response={items:(config.records||[record('eval-first'),record('eval-second'),record('eval-other','second'),record('eval-seg','second','segmentation')]).filter(r=>r.result.task===q.get('task')&&(!q.get('labelset_id')||r.binding.labelset_id===q.get('labelset_id')))};return config.reply?config.reply(q,response):response;}}:n.includes('useComputeStore')?{useComputeStore:hook(compute)}:n.includes('useProjectStore')?{useProjectStore:hook(project)}:n==='./EvaluationEvidencePanel'?{EvaluationEvidencePanel:()=>null}:n.startsWith('.')&&fs.existsSync(path.resolve(path.dirname(file),n+'.ts'))?load(path.resolve(path.dirname(file),n+'.ts')):req(n);
 m._compile(ts.transpileModule(fs.readFileSync(path.basename(file)==='EvaluationHistoryPanel.tsx'&&process.env.MV_HISTORY_PANEL_SOURCE?process.env.MV_HISTORY_PANEL_SOURCE:file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);return m.exports;}
 const component=load(path.join(__dirname,'EvaluationHistoryPanel.tsx'));
 function render(){global.localStorage=local;index=0;dirty=false;tree=component.EvaluationHistoryPanel(props);effects.splice(0).forEach(f=>f());}
 async function settle(){for(let i=0;i<20;i++){if(dirty)render();await new Promise(setImmediate);if(!dirty)return;}throw Error('Component did not settle');}
 const all=()=>nodes(tree),selector=()=>all().find(n=>n.type==='select'&&!n.props['aria-label']);
 return{local,compute,project,props,requests,catalogs,all,selector,render,settle,identity(value){identity=value;render();},async change(label,value){const n=label==='record'?selector():all().find(n=>n.props?.['aria-label']===label);assert(n,label);n.props.onChange({target:{value}});await settle();},async refresh(){const n=all().find(n=>n.props?.['aria-label']==='평가 이력 새로고침');assert(n);n.props.onClick();await settle();},async open(){all().find(n=>n.type==='details').props.onToggle({currentTarget:{open:true}});await settle();}};
}
test('refresh retains the exact labelset and saved model evaluation',async()=>{
 const f=fixture();await f.settle();await f.change('평가 라벨 세트','second');await f.change('record','eval-other');await f.refresh();
 assert.equal(f.all().find(n=>n.props?.['aria-label']==='평가 라벨 세트').props.value,'second');assert.equal(f.selector().props.value,'eval-other');assert(f.requests.every(r=>!r.options));
});
test('remount restores model family, labelset, exact evaluation, grouping and expanded view',async()=>{
 const saved=storage(),first=fixture({storage:saved});await first.settle();await first.change('평가 이력 모델 종류','segmentation');await first.change('평가 라벨 세트','second');await first.change('record','eval-seg');await first.change('평가 오류 집계 기준','lot');await first.open();
 const next=fixture({storage:saved});await next.settle();assert.equal(next.all().find(n=>n.props?.['aria-label']==='평가 이력 모델 종류').props.value,'segmentation');assert.equal(next.all().find(n=>n.props?.['aria-label']==='평가 라벨 세트').props.value,'second');assert.equal(next.selector().props.value,'eval-seg');assert.equal(next.all().find(n=>n.props?.['aria-label']==='평가 오류 집계 기준').props.value,'lot');assert.equal(next.all().find(n=>n.type==='details').props.open,true);assert(next.requests.every(r=>!r.options));
});
test('a missing remembered evaluation refuses instead of opening a different model',async()=>{
 const saved=storage(),first=fixture({storage:saved});await first.settle();await first.change('record','eval-second');const next=fixture({storage:saved,records:[record('eval-first')]});await next.settle();assert(next.all().some(n=>n.props?.role==='alert'&&text(n).includes('eval-second')));assert.equal(next.selector().props.value,'');assert(!next.all().some(n=>n.type?.name==='EvaluationEvidencePanel'));await next.change('record','eval-first');assert.equal(next.selector().props.value,'eval-first');
});
test('project, source, API identity and compute profile do not inherit another exact choice',async()=>{
 const saved=storage(),f=fixture({storage:saved});await f.settle();await f.change('record','eval-second');
 for(const mutate of [()=>f.project.project.id='project-two',()=>f.props.sourceFolder='/source-two',()=>f.compute.selectedProfileId='server-two']){mutate();f.render();await f.settle();assert.equal(f.selector().props.value,'eval-first');}
 f.identity('shared:http://another-server');await f.settle();assert.equal(f.selector().props.value,'eval-first');
 f.project.project.id='project-one';f.props.sourceFolder='/source-one';f.compute.selectedProfileId=null;f.identity('local');await f.settle();assert.equal(f.selector().props.value,'eval-second');
});
test('an explicit review return supersedes the old manual selection and stays selected on remount',async()=>{
 const saved=storage(),first=fixture({storage:saved});await first.settle();await first.change('record','eval-first');
 const scope=JSON.stringify(['project-one','/source-one','classification','default','local','local']);saved.setItem('modu-evaluation-return:'+scope,JSON.stringify({evaluation_id:'eval-second',image_id:'part',file_path:'/source-one/part.png'}));
 const returned=fixture({storage:saved});await returned.settle();assert.equal(returned.selector().props.value,'eval-second');assert.equal(returned.all().find(n=>n.type?.name==='EvaluationEvidencePanel').props.initialPath,'/source-one/part.png');const reopened=fixture({storage:saved});await reopened.settle();assert.equal(reopened.selector().props.value,'eval-second');
});
test('a late real history response is discarded after changing the labelset',async()=>{
 let resolve;const f=fixture({reply:(q,response)=>q.get('labelset_id')==='second'?new Promise(r=>resolve=()=>r(response)):response});await f.settle();await f.change('평가 라벨 세트','second');await f.change('평가 라벨 세트','default');resolve();await f.settle();assert.equal(f.selector().props.value,'eval-first');assert(!f.all().some(n=>n.type==='option'&&n.props.value==='eval-other'));
});
test('unavailable or malformed preference storage cannot block the readonly history',async()=>{
 const blocked={getItem(){throw Error('Storage unavailable');},setItem(){throw Error('Storage unavailable');},removeItem(){throw Error('Storage unavailable');}};
 const f=fixture({storage:blocked});await f.settle();await f.change('record','eval-second');await f.refresh();assert(f.selector());assert(f.requests.every(r=>!r.options));
 const malformed=storage();malformed.setItem('vision-evaluation-view:'+JSON.stringify(['project-one','/source-one','evaluation_history','','local','local']),'{bad JSON');const next=fixture({storage:malformed});await next.settle();assert.equal(next.selector().props.value,'eval-first');
});
test('a disappeared labelset keeps its filter and refuses another set or evidence',async()=>{
 const saved=storage(),first=fixture({storage:saved});await first.settle();await first.change('평가 라벨 세트','second');await first.change('record','eval-other');
 const next=fixture({storage:saved,labelsets:[{id:'default',name:'Default'}]});await next.settle();assert.equal(next.all().find(n=>n.props?.['aria-label']==='평가 라벨 세트').props.value,'second');assert(next.all().some(n=>n.props?.role==='alert'&&text(n).includes('라벨 세트 second')));assert(!next.all().some(n=>n.type?.name==='EvaluationEvidencePanel'));
});
test('the initially displayed evaluation stays exact when a newer report appears during refresh',async()=>{
 const config={records:[record('eval-first')]},f=fixture(config);await f.settle();config.records.unshift(record('eval-new'));await f.refresh();assert.equal(f.selector().props.value,'eval-first');
});
test('late default model hydration preserves the opened panel and labelset filter',async()=>{
 const f=fixture();await f.settle();await f.open();await f.change('평가 라벨 세트','second');f.props.task='segmentation';f.render();await f.settle();assert.equal(f.all().find(n=>n.type==='details').props.open,true);assert.equal(f.all().find(n=>n.props?.['aria-label']==='평가 라벨 세트').props.value,'second');assert.equal(f.selector().props.value,'eval-seg');
});
test('an explicit history family survives a change of the parent recipe default',async()=>{
 const f=fixture();await f.settle();await f.change('평가 이력 모델 종류','segmentation');await f.change('평가 라벨 세트','second');f.props.task='ocr';f.render();await f.settle();assert.equal(f.all().find(n=>n.props?.['aria-label']==='평가 이력 모델 종류').props.value,'segmentation');assert.equal(f.selector().props.value,'eval-seg');
});
test('a queued details toggle cannot overwrite a newer manual labelset and family',async()=>{
 const f=fixture();await f.settle();const queued=f.all().find(n=>n.type==='details').props.onToggle;await f.change('평가 이력 모델 종류','segmentation');await f.change('평가 라벨 세트','second');queued({currentTarget:{open:true}});await f.settle();assert.equal(f.all().find(n=>n.props?.['aria-label']==='평가 이력 모델 종류').props.value,'segmentation');assert.equal(f.all().find(n=>n.props?.['aria-label']==='평가 라벨 세트').props.value,'second');assert.equal(f.selector().props.value,'eval-seg');
});
test('opening before initial source-folder hydration stays open after the folder becomes available',async()=>{
 const f=fixture();f.props.sourceFolder='';await f.settle();await f.open();f.props.sourceFolder='/source-one';f.render();await f.settle();assert.equal(f.all().find(n=>n.type==='details').props.open,true);assert.equal(f.selector().props.value,'eval-first');
});

function rememberedSet(labelset){const local=storage(),key='vision-evaluation-view:'+JSON.stringify(['project-one','/source-one','evaluation_history','','local','local']);local.setItem(key,JSON.stringify({version:1,task:'classification',taskExplicit:true,labelsetId:labelset,group:'product'}));return local;}
const catalog=()=>({labelsets:[{id:'default',name:'Default'},{id:'second',name:'Second'}]});
const reevaluate=f=>f.all().find(n=>n.type==='button'&&text(n)==='선택 모델 재평가 · 새 이력 저장');

test('actual pending labelset catalog emits no history GET until its exact saved set is confirmed',async()=>{
 let release;const local=rememberedSet('default'),before=[...local.values],f=fixture({storage:local,listLabelsets:()=>new Promise(resolve=>release=resolve)});
 await f.settle();const beforeRelease=[...f.requests];assert.equal(f.catalogs.length,1);assert.equal(f.all().find(n=>n.props?.['aria-label']==='평가 라벨 세트').props.value,'default');
 assert.equal(reevaluate(f).props.disabled,true);assert.deepEqual([...local.values],before);release(catalog());await f.settle();
 assert.deepEqual(beforeRelease,[],'catalog pending must not read an unconfirmed saved labelset');assert.equal(f.requests.length,1);
 assert.equal(f.requests[0].url,'/api/evaluation/history?source_dataset_path=%2Fsource-one&task=classification&labelset_id=default');assert.equal(f.selector().props.value,'eval-first');
});

test('missing persisted set keeps its exact hint and preference without history GET or reevaluation',async()=>{
 const local=rememberedSet('owned_missing_labelset'),before=[...local.values],f=fixture({storage:local});await f.settle();
 assert.deepEqual(f.requests,[]);assert.deepEqual([...local.values],before);assert.equal(f.all().find(n=>n.props?.['aria-label']==='평가 라벨 세트').props.value,'owned_missing_labelset');
 const alerts=f.all().filter(n=>n.props?.role==='alert');assert.equal(alerts.length,1);assert.equal(text(alerts[0]),'선택했던 라벨 세트 owned_missing_labelset를 찾지 못했습니다. 다른 세트를 직접 선택하세요.');
 assert(!f.selector());assert(!f.all().some(n=>n.type?.name==='EvaluationEvidencePanel'));assert.equal(reevaluate(f).props.disabled,true);await reevaluate(f).props.onClick();await f.settle();assert.deepEqual(f.requests,[]);
 await f.change('평가 라벨 세트','default');assert.equal(f.requests.length,1);assert.equal(f.selector().props.value,'eval-first');assert(f.requests.every(r=>!r.options));
});

test('failed catalog preserves its real error and refresh retries only after a real valid catalog',async()=>{
 let calls=0;const f=fixture({storage:rememberedSet('default'),listLabelsets:async()=>{if(++calls===1)throw Error('Controlled exact catalog outage');return catalog();}});await f.settle();
 assert.deepEqual(f.requests,[]);assert(f.all().some(n=>n.props?.role==='alert'&&text(n)==='Controlled exact catalog outage'));assert.equal(reevaluate(f).props.disabled,true);
 await f.refresh();assert.equal(f.catalogs.length,2);assert.equal(f.requests.length,1);assert.equal(f.selector().props.value,'eval-first');assert(f.requests.every(r=>!r.options));
});

test('a late catalog from a prior project cannot release history in the current still-pending project',async()=>{
 const releases=[];const f=fixture({listLabelsets:()=>new Promise(resolve=>releases.push(resolve))});await f.settle();f.project.project.id='project-two';f.render();await f.settle();assert.equal(releases.length,2);
 releases[0]({labelsets:[{id:'old-only',name:'Old-only'}]});await f.settle();assert.deepEqual(f.requests,[]);assert(!f.all().some(n=>n.type==='option'&&n.props.value==='old-only'));
 releases[1](catalog());await f.settle();assert.equal(f.requests.length,1);assert.equal(f.requests[0].url,'/api/evaluation/history?source_dataset_path=%2Fsource-one&task=classification');assert.equal(f.selector().props.value,'eval-first');
});

test('refresh waits for its new catalog revision and preserves exact remembered evaluation',async()=>{
 let calls=0,release;const f=fixture({storage:rememberedSet('default'),listLabelsets:()=>++calls===1?catalog():new Promise(resolve=>release=resolve)});await f.settle();await f.change('record','eval-second');const before=[...f.local.values],beforeRequests=f.requests.length;
 await f.refresh();assert.equal(f.requests.length,beforeRequests,'refresh cannot reuse the prior loaded catalog for a new history GET');assert.deepEqual([...f.local.values],before);assert(!f.selector());assert.equal(reevaluate(f).props.disabled,true);
 release(catalog());await f.settle();assert.equal(f.requests.length,beforeRequests+1);assert.equal(f.selector().props.value,'eval-second');assert.deepEqual([...f.local.values],before);
});

test('a late history cannot populate or persist while its refreshed catalog is pending',async()=>{
 let catalogs=0,histories=0,releaseCatalog,releaseHistory;const f=fixture({storage:rememberedSet('default'),listLabelsets:()=>++catalogs===1?catalog():new Promise(resolve=>releaseCatalog=resolve),reply:(_q,response)=>++histories===1?new Promise(resolve=>releaseHistory=()=>resolve(response)):response});
 await f.settle();assert.equal(f.requests.length,1);const before=[...f.local.values];await f.refresh();releaseHistory();await f.settle();assert.equal(f.requests.length,1);assert(!f.selector());assert(!f.all().some(n=>n.type?.name==='EvaluationEvidencePanel'));assert.deepEqual([...f.local.values],before);
 releaseCatalog(catalog());await f.settle();assert.equal(f.requests.length,2);assert.equal(f.selector().props.value,'eval-first');
});

test('a confirmed catalog still sends exact history and preserves its actual server error',async()=>{
 const f=fixture({storage:rememberedSet('default'),reply:async()=>{throw Error('Controlled exact history server refusal');}});await f.settle();assert.equal(f.requests.length,1);
 assert.equal(f.requests[0].url,'/api/evaluation/history?source_dataset_path=%2Fsource-one&task=classification&labelset_id=default');assert(f.all().some(n=>n.props?.role==='alert'&&text(n)==='Controlled exact history server refusal'));assert(!f.selector());assert.equal(reevaluate(f).props.disabled,true);assert(f.requests.every(r=>!r.options));
});

test('a previously rendered reevaluation handler cannot dispatch while its refreshed catalog is pending',async()=>{
 let calls=0,release;const f=fixture({storage:rememberedSet('default'),listLabelsets:()=>++calls===1?catalog():new Promise(resolve=>release=resolve)});f.props.jobId='owned-completed-job';await f.settle();
 const captured=reevaluate(f).props.onClick;assert.equal(reevaluate(f).props.disabled,false);await f.refresh();const before=f.requests.length,preferences=[...f.local.values];captured();await f.settle();
 assert.equal(f.requests.length,before,'old ready handler must not post after catalog readiness is revoked');assert.deepEqual([...f.local.values],preferences);assert.equal(reevaluate(f).props.disabled,true);
 release(catalog());await f.settle();assert.equal(f.selector().props.value,'eval-first');assert(f.requests.every(r=>!r.options));
});

for(const [labelset,chosen] of [['default','eval-second'],['second','eval-other']])test(`a failed refresh preserves real history reading for the previously confirmed exact ${labelset} membership`,async()=>{
 let unavailable=false;const f=fixture({storage:rememberedSet(labelset),listLabelsets:async()=>{if(unavailable)throw Error('Controlled exact catalog refresh503');return catalog();}});
 await f.settle();await f.change('record',chosen);const preferences=[...f.local.values],before=f.requests.length;unavailable=true;await f.refresh();
 assert.equal(f.requests.length,before+1,'confirmed same-scope catalog outage must still read the real scoped history');
 assert.equal(f.requests.at(-1).url,`/api/evaluation/history?source_dataset_path=%2Fsource-one&task=classification&labelset_id=${labelset}`);
 assert.equal(f.selector().props.value,chosen);assert.deepEqual([...f.local.values],preferences);
 assert(f.all().some(n=>n.props?.role==='alert'&&text(n)==='Controlled exact catalog refresh503'));
 assert.equal(reevaluate(f).props.disabled,true);assert(f.requests.every(r=>!r.options));
 unavailable=false;await f.refresh();assert.equal(f.selector().props.value,chosen);assert(!f.all().some(n=>n.props?.role==='alert'));assert.deepEqual([...f.local.values],preferences);
});

test('confirmed membership authority is not inherited by another project, source, transport, profile, task or selected set after catalog failure',async()=>{
 const boundaries=[
  ['project',async f=>{f.project.project.id='project-two';f.render();await f.settle();}],
  ['source',async f=>{f.props.sourceFolder='/source-two';f.render();await f.settle();}],
  ['transport revision',async f=>{f.compute.transportRevision++;f.render();await f.settle();}],
  ['API transport identity',async f=>{f.identity('shared:http://other-origin');await f.settle();}],
  ['compute profile',async f=>{f.compute.selectedProfileId='other-profile';f.render();await f.settle();}],
  ['history task',f=>f.change('평가 이력 모델 종류','segmentation')],
  ['selected labelset',f=>f.change('평가 라벨 세트','second')]
 ];
 for(const [name,mutate] of boundaries){let unavailable=false;const f=fixture({storage:rememberedSet('default'),listLabelsets:async()=>{if(unavailable)throw Error('Controlled current catalog unavailable');return catalog();}});
  await f.settle();await f.change('record','eval-second');unavailable=true;await f.refresh();const count=f.requests.length;await mutate(f);
  assert.equal(f.requests.length,count,`${name} must not inherit the prior confirmed membership`);assert(!f.selector());assert(!f.all().some(n=>n.type?.name==='EvaluationEvidencePanel'));
  assert.equal(reevaluate(f).props.disabled,true);await reevaluate(f).props.onClick();await f.settle();assert.equal(f.requests.length,count);assert(f.requests.every(r=>!r.options));
 }
});

test('a successful catalog that removes the selected set revokes older membership before any later refresh outage',async()=>{
 let mode='valid';const f=fixture({storage:rememberedSet('default'),listLabelsets:async()=>{if(mode==='error')throw Error('Controlled later catalog unavailable');return mode==='missing'?{labelsets:[{id:'second',name:'Second'}]}:catalog();}});
 await f.settle();await f.change('record','eval-second');const before=f.requests.length;mode='missing';await f.refresh();assert.equal(f.requests.length,before);assert(!f.selector());
 assert(f.all().some(n=>n.props?.role==='alert'&&text(n).includes('라벨 세트 default')));mode='error';await f.refresh();
 assert.equal(f.requests.length,before);assert(!f.selector());assert.equal(reevaluate(f).props.disabled,true);assert(f.requests.every(r=>!r.options));
});

test('a cached reevaluation handler cannot dispatch during read-only continuity after a confirmed catalog outage',async()=>{
 let unavailable=false;const f=fixture({storage:rememberedSet('default'),listLabelsets:async()=>{if(unavailable)throw Error('Controlled read-only catalog outage');return catalog();}});
 f.props.jobId='owned-completed-job';await f.settle();await f.change('record','eval-second');const captured=reevaluate(f).props.onClick;assert.equal(reevaluate(f).props.disabled,false);
 unavailable=true;await f.refresh();const count=f.requests.length,preferences=[...f.local.values];assert.equal(reevaluate(f).props.disabled,true);captured();await f.settle();
 assert.equal(f.requests.length,count);assert(f.requests.every(r=>!r.options));assert.deepEqual([...f.local.values],preferences);assert(f.selector(),'read-only continuity must expose the exact confirmed record');assert.equal(f.selector().props.value,'eval-second');
});

test('the prior revision late history cannot replace the real current history recovered after catalog refresh failure',async()=>{
 let unavailable=false,reads=0,release;const local=rememberedSet('default');local.setItem('vision-evaluation-record:'+JSON.stringify(['project-one','/source-one','classification','default','local','local']),JSON.stringify({version:1,evaluation_id:'eval-second'}));
 const f=fixture({storage:local,listLabelsets:async()=>{if(unavailable)throw Error('Controlled refreshed catalog outage');return catalog();},reply:(_q,response)=>++reads===1?new Promise(resolve=>release=()=>resolve({items:[record('eval-first')]})):response});
 await f.settle();assert.equal(f.requests.length,1);const before=[...local.values];unavailable=true;await f.refresh();assert.equal(f.requests.length,2);assert.equal(f.selector().props.value,'eval-second');
 release();await f.settle();assert.equal(f.selector().props.value,'eval-second');assert.deepEqual([...local.values],before);assert(f.requests.every(r=>!r.options));
});
