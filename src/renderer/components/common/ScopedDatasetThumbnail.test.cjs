// Resource lifetime + real HTTP serializer. Visibility/HTTP are external
// suppliers; no React mount, native decoder or backend authorization is claimed.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(setImmediate),deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve};};
async function until(ready){const deadline=Date.now()+2000;while(!ready()){assert.ok(Date.now()<deadline,'owned source boundary did not settle');await tick();}}
function load(relative,dependencies={}) {
 const file=path.resolve(__dirname,relative),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);
 m.require=name=>Object.hasOwn(dependencies,name)?dependencies[name]:original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,file);return m.exports;
}
const origin='https://thumbnail.invalid',B={workspace_id:'workspace',project_id:'B',actor_id:'actor',mode:'team'},A={...B,project_id:'A'};
const thumbnail='/api/dataset/thumbnail/preview?file_path=%2Fsource%2FB%2Fone.png';
function fixture(t,{local=false,unselected=false,noObserver=false,responseContext=B,held=false,headersFromMain=false,status=200}={}) {
 const api=load('../../services/api.ts'),inert={getState:()=>({invalidateForDataChange(){}}),setState(){}};
 const dataset=load('../../stores/useDatasetStore.ts',{'../services/api':api,'./useTrainingStore':{useTrainingStore:inert},'./useEvaluationStore':{useEvaluationStore:inert},'./useFlowchartStore':{useFlowchartStore:inert}});
 api.setCachedPort(56050);api.setSharedApiBase(local?null:origin);if(!unselected&&!local)api.setProjectContext(B);
 const authority=dataset.readDatasetAuthority(),context=api.getProjectContext(),pending=deferred(),calls=[],observers=[],allocated=[],revoked=[],published=[],errors=[],lifetimes=[];
 const original={fetch:global.fetch,observer:global.IntersectionObserver,create:URL.createObjectURL,revoke:URL.revokeObjectURL};
 class Observer {
  constructor(callback,options){this.callback=callback;this.options=options;this.disconnected=false;observers.push(this);}
  observe(target){this.target=target;}
  disconnect(){this.disconnected=true;}
  deliver(visible=true){this.callback([{target:this.target,isIntersecting:visible}],this);}
 }
 global.IntersectionObserver=noObserver?undefined:Observer;
 URL.createObjectURL=blob=>{const url=original.create.call(URL,blob);allocated.push(url);return url;};
 URL.revokeObjectURL=url=>{revoked.push(url);return original.revoke.call(URL,url);};
 const main=load('../../../main/sharedSession.ts');let legacy='B';
 global.fetch=async(url,options={})=>{
  const parsed=new URL(url);
  if(parsed.pathname==='/api/accounts/login')return new Response(JSON.stringify({token:'fixture-capability',expires_at:Date.now()/1000+3600,user:{id:'actor',username:'fixture',administrator:true}}),{status:200});
  assert.equal(parsed.pathname,'/api/dataset/thumbnail/preview');
  const headers=new Headers(options.headers);
  if(headersFromMain)for(const[k,v]of Object.entries(main.sharedHeaders(url)))headers.set(k,v);
  const wire={url,signal:options.signal,legacy:headers.get('X-Vision-Project'),context:headers.get('X-Vision-Context')&&JSON.parse(headers.get('X-Vision-Context')),bodyFinished:false};calls.push(wire);
  if(held)await pending.promise;
  const response=new Response(status===200?new Blob(['owned-thumbnail-bytes'],{type:'image/jpeg'}):JSON.stringify({detail:'Request data is outside this project or invalid'}),{status,headers:local?{}:{'X-Vision-Context':JSON.stringify(responseContext)}});
  const blob=response.blob.bind(response);response.blob=async()=>{try{return await blob();}finally{wire.bodyFinished=true;}};return response;
 };
 const thumbnailModule=load('./ScopedDatasetThumbnail.tsx',{'../../services/api':api});
 const make=(extra={})=>{const read=thumbnailModule.createScopedThumbnailRead({path:thumbnail,context,authority,
  current:()=>authority===dataset.readDatasetAuthority(),publish:url=>published.push(url),failed:error=>errors.push(error),...extra});lifetimes.push(read);return read;};
 t.after(()=>{for(const read of lifetimes)read.retire();pending.resolve();for(const url of allocated)if(!revoked.includes(url))original.revoke.call(URL,url);global.fetch=original.fetch;global.IntersectionObserver=original.observer;URL.createObjectURL=original.create;URL.revokeObjectURL=original.revoke;});
 return{api,main,make,pending,calls,observers,allocated,revoked,published,errors,original,
  async switchMain(){if(legacy==='B'){await main.loginSharedServer({server_url:origin,username:'fixture-user',password:'fixture-password'});main.selectSharedProject('B');legacy='A';}main.selectSharedProject('A');}};
}
test('visibility starts one captured-context Blob request and retirement revokes its real object URL',async t=>{
 const f=fixture(t,{headersFromMain:true}),read=f.make(),element={};await f.switchMain();read.observe(element);
 assert.equal(f.calls.length,0);f.observers[0].deliver(false);assert.equal(f.calls.length,0);
 f.observers[0].deliver();f.observers[0].deliver();await until(()=>f.published.length||f.errors.length);
 assert.equal(f.calls.length,1);assert.equal(f.calls[0].legacy,'A');assert.deepEqual(f.calls[0].context,B);
 assert.equal(f.allocated.length,1);assert.deepEqual(f.published,f.allocated);assert.equal(f.errors.length,0);
 assert.equal(await (await f.original.fetch(f.published[0])).text(),'owned-thumbnail-bytes');
 read.retire();read.retire();assert.equal(f.calls[0].signal.aborted,true);assert.deepEqual(f.revoked,f.allocated);
 await assert.rejects(f.original.fetch(f.published[0]));
});
test('retiring a held read aborts it and ignores a transport that resolves after abort',async t=>{
 const f=fixture(t,{held:true}),read=f.make();read.observe({});f.observers[0].deliver();await tick();
 read.retire();assert.equal(f.calls[0].signal.aborted,true);f.pending.resolve();await until(()=>f.calls[0].bodyFinished);await tick();
 assert.deepEqual(f.allocated,[]);assert.deepEqual(f.published,[]);assert.deepEqual(f.errors,[]);
});
test('A to B to A cannot revive a response from an earlier accepted epoch',async t=>{
 const f=fixture(t,{held:true}),read=f.make();read.observe({});f.observers[0].deliver();await tick();
 f.api.setProjectContext(A);f.api.setProjectContext(B);f.pending.resolve();await until(()=>f.calls[0].bodyFinished);await tick();
 assert.deepEqual(f.allocated,[]);assert.deepEqual(f.published,[]);read.retire();
});
test('context is copied before visibility and async transport, not read from a changed caller object',async t=>{
 const f=fixture(t),passed={...B},read=f.make({context:passed});passed.project_id='changed-object';read.observe({});f.observers[0].deliver();await until(()=>f.published.length||f.errors.length);
 assert.deepEqual(f.calls[0].context,B);assert.equal(f.published.length,1);read.retire();
});
test('real serializer response-context mismatch is a current failure with no allocated URL',async t=>{
 const f=fixture(t,{responseContext:A}),read=f.make();read.observe({});f.observers[0].deliver();await until(()=>f.errors.length);
 assert.equal(f.errors.length,1);assert.match(f.errors[0].message,/문맥/);assert.deepEqual(f.allocated,[]);read.retire();
});
test('a current HTTP 403 remains failed and cannot publish a ready object URL',async t=>{
 const f=fixture(t,{status:403}),read=f.make();read.observe({});f.observers[0].deliver();await until(()=>f.errors.length);
 assert.equal(f.errors[0].status,403);assert.match(f.errors[0].message,/outside this project/);assert.deepEqual(f.allocated,[]);assert.deepEqual(f.published,[]);read.retire();
});
test('shared null context refuses dispatch while original local null context remains usable',async t=>{
 const f=fixture(t,{unselected:true}),read=f.make();read.observe({});f.observers[0]?.deliver();await tick();assert.equal(f.calls.length,0);read.retire();
});
test('local null uses the unchanged real local API serializer without inventing explicit context',async t=>{
 const f=fixture(t,{local:true}),read=f.make();read.observe({});f.observers[0].deliver();await until(()=>f.published.length||f.errors.length);
 assert.equal(f.calls.length,1);assert.equal(f.calls[0].url.startsWith('http://127.0.0.1:56050/'),true);assert.equal(f.calls[0].context,null);assert.equal(f.published.length,1);read.retire();
});
test('outside-origin, non-thumbnail and ambiguous paths never dispatch',async t=>{
 const f=fixture(t);
 for(const candidate of ['https://outside.invalid/api/dataset/thumbnail/one','//outside.invalid/api/dataset/thumbnail/one','/api/dataset/raw/one','/api/dataset/thumbnail/../../accounts/me','/api/dataset/thumbnail/one#fragment','/api/dataset/thumbnail/\\outside']){
  const read=f.make({path:candidate});read.observe({});f.observers.at(-1)?.deliver();read.retire();
 }
 await tick();assert.equal(f.calls.length,0);assert.equal(f.allocated.length,0);
});
test('missing IntersectionObserver stays a placeholder and never eagerly fetches',async t=>{
 const f=fixture(t,{noObserver:true}),read=f.make();read.observe({});await tick();assert.equal(f.calls.length,0);read.retire();
});
test('StrictMode-style cleanup then new lifetime neither reuses a revoked URL nor accepts old visibility',async t=>{
 const f=fixture(t),first=f.make();first.observe({});const old=f.observers[0];old.deliver();await until(()=>f.published.length===1);first.retire();
 const second=f.make();second.observe({});old.deliver();assert.equal(f.calls.length,1);f.observers[1].deliver();await until(()=>f.published.length===2);
 assert.equal(f.calls.length,2);assert.equal(f.published.length,2);assert.notEqual(f.published[0],f.published[1]);second.retire();assert.deepEqual(f.revoked,f.allocated);
});
test('a missing or non-string thumbnail address observes nothing and never throws (the app must not blank)',async t=>{
 const f=fixture(t,{headersFromMain:true});await f.switchMain();
 for(const path of [undefined,null,42,{}]){
  const read=f.make({path});assert.doesNotThrow(()=>read.observe({}));
 }
 assert.equal(f.observers.length,0,'no observer is attached for an address that is not a thumbnail path');
 assert.equal(f.calls.length,0);assert.equal(f.published.length,0);
});

test('the read-ahead margin is measured on the gallery scroll area, so clipped rows below the fold still load',async t=>{
 const f=fixture(t,{headersFromMain:true});await f.switchMain();
 const previous=globalThis.getComputedStyle;globalThis.getComputedStyle=node=>({overflowY:node.overflowY||'visible'});
 t.after(()=>{if(previous)globalThis.getComputedStyle=previous;else delete globalThis.getComputedStyle;});
 const gallery={overflowY:'auto',parentElement:{overflowY:'hidden',parentElement:null}};
 const element={parentElement:{overflowY:'visible',parentElement:gallery}};
 f.make().observe(element);
 assert.equal(f.observers[0].options.root,gallery,'the nearest vertically scrolling ancestor is the root');
 assert.equal(f.observers[0].options.rootMargin,'800px 0px');
 f.make().observe({parentElement:{overflowY:'visible',parentElement:null}});
 assert.equal(f.observers[1].options.root,null,'no scrolling ancestor falls back to the window');
});
