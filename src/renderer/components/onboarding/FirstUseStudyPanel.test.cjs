const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(setImmediate);
function fixture(){
 const files=['firstUseStudy.ts','firstUseStudyStore.ts','FirstUseStudyPanel.tsx'];for(const name of files)assert.ok(fs.existsSync(path.join(__dirname,name)),'missing first-use feature: '+name);
 let now=1000,scope='scope-A',cursor=0;const slots=[],effects=[],cache=new Map(),downloads=[],listeners=new Map(),storeListeners=new Set();
 const react={createElement:(type,props,...children)=>({type,props:props||{},children:children.flat()}),useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;}];},useEffect(fn,deps){const i=cursor++;if(!slots[i]||deps.some((v,n)=>v!==slots[i].deps[n])){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 function load(name,mocks={}){
  const file=path.join(__dirname,name);if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);cache.set(file,m);const original=m.require.bind(m);
  const jsx=(type,props)=>({type,props:props||{},children:[props?.children].flat().filter(value=>value!==undefined&&value!==false&&value!==null)});
  m.require=ref=>Object.hasOwn(mocks,ref)?mocks[ref]:ref==='react'?react:ref==='react/jsx-runtime'?{jsx,jsxs:jsx}:ref==='./firstUseStudy'?load('firstUseStudy.ts'):ref==='./firstUseStudyStore'?{useFirstUseStudyStore:hook,currentFirstUseStudyScope:()=>scope,subscribeFirstUseStudyScope:fn=>{storeListeners.add(fn);return()=>storeListeners.delete(fn);}}:ref==='../../services/hostAdapter'?{host:{kind:()=> 'desktop',updates:{status:async()=>({app_version:'0.1.0',platform:'darwin',architecture:'arm64',token:'sk-private',path:'/secret/file'})}}}:original(ref);
  m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,file);return m.exports;
 }
 const moduleStore=load('firstUseStudyStore.ts',{'../../services/api':{},'../../stores/useProjectStore':{},'../../stores/useDatasetStore':{}});
 const store=moduleStore.createFirstUseStudyStore(()=>now);const hook=Object.assign(selector=>selector?selector(store.getState()):store.getState(),{getState:store.getState,subscribe:store.subscribe});
 const panel=load('FirstUseStudyPanel.tsx');
 const saved={window:global.window,document:global.document,URL:global.URL,setInterval:global.setInterval,clearInterval:global.clearInterval};let interval;
 global.window={addEventListener:(name,fn)=>{if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn);},removeEventListener:(name,fn)=>listeners.get(name)?.delete(fn)};
 global.document={createElement:()=>({click(){downloads.push(this.href);}})};
 global.URL={createObjectURL:blob=>{downloads.push(blob);return 'blob:study';},revokeObjectURL:()=>{}};
 global.setInterval=fn=>{interval=fn;return 7;};global.clearInterval=()=>{interval=null;};
 const nodes=tree=>{const found=[];const walk=n=>{if(n&&typeof n==='object'){found.push(n);n.children?.forEach(walk);}};walk(tree);return found;};
 const render=()=>{cursor=0;const result=panel.FirstUseStudyPanel();effects.splice(0).forEach(fn=>fn());return result;};
 const button=name=>nodes(render()).find(n=>n.type==='button'&&n.children.includes(name));
 const cleanup=()=>{for(const value of slots)value?.cleanup?.();Object.assign(global,saved);};
 return{store,render,nodes,button,downloads,advance:ms=>{now+=ms;interval?.();},load,panel,listeners,storeListeners,
  setScope:value=>{scope=value;for(const listener of storeListeners)listener();},
  fire:(type,control=false)=>{const target={closest:selector=>{assert.equal(selector,'[data-first-use-study-controls]');return control?{}:null;}};const event={target};for(const key of ['key','code','value','textContent','clientX','clientY'])Object.defineProperty(event,key,{get(){throw Error('private event field accessed: '+key);}});for(const fn of listeners.get(type)||[])fn(event);},
  mountObserver(){cursor=100;panel.FirstUseStudyObserver();effects.splice(0).forEach(fn=>fn());},cleanup};
}
test('mounted panel explicitly starts a chosen mode/task, binds once, marks images and downloads only projected JSON',async()=>{
 const f=fixture();try{f.render();await tick();const select=label=>f.nodes(f.render()).find(n=>n.type==='select'&&n.props['aria-label']===label);
  select('측정 방식').props.onChange({target:{value:'automated'}});select('측정 과제').props.onChange({target:{value:'manual_label'}});
  f.button('로컬 측정 시작').props.onClick();assert.equal(f.store.getState().snapshot.mode,'automated');
  f.button('현재 프로젝트·폴더로 고정').props.onClick();assert.equal(f.store.getState().snapshot.binding,'bound');
  f.advance(60000);f.button('이미지 1장 완료로 기록').props.onClick();f.advance(60000);f.button('기록 끝내기').props.onClick();
  const value=f.store.getState().snapshot;assert.equal(value.images_completed_self_reported,1);assert.equal(value.elapsed_ms_per_image,120000);assert.equal(value.acceptance,'unverified');
  f.button('측정 JSON 저장').props.onClick();const exported=JSON.parse(await f.downloads[0].text());assert.equal(exported.sessions[0].elapsed_ms,120000);
  assert.equal(exported.sessions[0].build.build_sha,null);assert.doesNotMatch(JSON.stringify(exported),/sk-private|secret\/file|scope-A/);
 }finally{f.cleanup();}
});
test('global observer survives guide closure, counts no private fields, separates its controls and aborts on bound scope changes',()=>{
 const f=fixture();try{f.mountObserver();f.fire('pointerdown');assert.equal(f.store.getState().snapshot,null);
  f.store.getState().start({mode:'human_self_reported',task:'first_flow'});f.fire('pointerdown');f.fire('keydown');f.fire('pointerdown',true);f.advance(1000);
  assert.deepEqual(f.store.getState().snapshot.workflow_actions,{pointer:1,keyboard:1});assert.deepEqual(f.store.getState().snapshot.recorder_actions,{pointer:1,keyboard:0});
  f.setScope('scope-B');assert.equal(f.store.getState().snapshot.status,'recording','setup does not abort before binding');
  f.store.getState().bind('scope-B');f.advance(1000);f.setScope('scope-C');assert.equal(f.store.getState().snapshot.status,'aborted');
  assert.equal(f.store.getState().snapshot.elapsed_ms,2000);assert.equal(f.store.getState().history.length,1);
 }finally{f.cleanup();assert.ok([...f.listeners.values()].every(set=>set.size===0));assert.equal(f.storeListeners.size,0);}
});
test('observer removal seals an exportable partial and clears its timer/listeners',()=>{
 const f=fixture();f.mountObserver();f.store.getState().start({mode:'automated',task:'first_flow'});f.advance(75);f.cleanup();
 assert.equal(f.store.getState().snapshot.status,'aborted');assert.equal(f.store.getState().snapshot.reason,'observer_removed');assert.equal(f.store.getState().snapshot.elapsed_ms,75);
 f.advance(1000);assert.equal(f.store.getState().snapshot.elapsed_ms,75);
});
test('completed manual and assisted records remain available together with honest all-time comparison',()=>{
 const f=fixture();try{for(const task of ['manual_label','assisted_draft']){f.store.getState().start({mode:'automated',task});f.advance(1000);f.store.getState().mark('image_complete_self_reported');f.advance(500);f.store.getState().stop();}
  const report=f.store.getState().exportReport();assert.equal(report.sessions.length,2);assert.deepEqual(report.sessions.map(row=>row.elapsed_ms_per_image),[1500,1500]);assert.equal(report.acceptance,'unverified');
  assert.ok(f.nodes(f.render()).some(node=>node.children.some(value=>typeof value==='string'&&value.includes('같은 이미지·난이도인지 자동 확인하지 않습니다'))));
 }finally{f.cleanup();}
});
