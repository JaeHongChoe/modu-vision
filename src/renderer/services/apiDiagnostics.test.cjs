const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function fixture(){const cache=new Map();function load(name){if(cache.has(name))return cache.get(name).exports;const file=path.join(__dirname,name+'.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);cache.set(name,m);const original=m.require.bind(m);m.require=n=>n.startsWith('./')?load(n.slice(2)):original(n);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}assert.ok(fs.existsSync(path.join(__dirname,'apiDiagnostics.ts')),'bounded request failure journal must exist');const api=load('api'),journal=load('apiDiagnostics');api.setCachedPort(56050);api.setProjectContext({workspace_id:'w',project_id:'p',actor_id:'actor',mode:'local'});return{api,journal};}
test('request journal preserves original failures, excludes payloads and successes, and bounds scope-local history',async()=>{
 const {api,journal}=fixture(),old=global.fetch;try{global.fetch=async()=>new Response(JSON.stringify({detail:{status:409,error_code:'ERR_CONFLICT',message_ko:'실패 /Users/private/a.png password=secret'}}),{status:409});
 for(let i=0;i<45;i++)await assert.rejects(api.request('/api/training/start?token=private',{method:'POST',body:'sensitive request body'}),e=>e.status===409&&e.error_code==='ERR_CONFLICT');
 const rows=journal.apiDiagnosticFailures(api.getApiDiagnosticScope());assert.equal(rows.length,40);assert.ok(rows.every(r=>r.kind==='http'&&r.code==='ERR_CONFLICT'&&r.status===409));assert.ok(rows.every(r=>r.historical===true&&r.current_error_confirmed===false));assert.doesNotMatch(JSON.stringify(rows),/token|private|secret|sensitive request body|\/api\//);
 global.fetch=async()=>new Response('{}');await api.request('/api/training/status');assert.equal(journal.apiDiagnosticFailures(api.getApiDiagnosticScope()).length,40);
 api.setProjectContext({workspace_id:'w',project_id:'p',actor_id:'other',mode:'local'});assert.deepEqual(journal.apiDiagnosticFailures(api.getApiDiagnosticScope()),[]);
 }finally{global.fetch=old;}
});
test('network, invalid JSON and context failures are distinct and delayed A-B-A failures never enter current history',async()=>{
 const {api,journal}=fixture(),old=global.fetch;try{const boom=new TypeError('network down https://private.example');global.fetch=async()=>{throw boom;};await assert.rejects(api.request('/api/training/status'),e=>e===boom);
 global.fetch=async()=>new Response('');await assert.rejects(api.request('/api/training/status'),SyntaxError);
 global.fetch=async()=>new Response('{}',{headers:{'X-Vision-Context':JSON.stringify({workspace_id:'w',project_id:'wrong',actor_id:'actor',mode:'local'})}});await assert.rejects(api.request('/api/training/status'));
 assert.deepEqual(journal.apiDiagnosticFailures(api.getApiDiagnosticScope()).map(r=>r.kind),['network','json','context']);
 let reject;global.fetch=()=>new Promise((_ok,fail)=>{reject=fail;});const pending=api.request('/api/training/status');await new Promise(r=>setImmediate(r));const original=api.getProjectContext();api.setProjectContext({...original,project_id:'other'});api.setProjectContext(original);reject(boom);await assert.rejects(pending,e=>e===boom);assert.deepEqual(journal.apiDiagnosticFailures(api.getApiDiagnosticScope()),[]);
 }finally{global.fetch=old;}
});
test('a throwing observer cannot change a structured thrown object or record request success',async()=>{
 const {api,journal}=fixture(),old=global.fetch;api.subscribeApiRequestFailures(()=>{throw new Error('observer broken');});try{
 global.fetch=async()=>new Response(JSON.stringify({detail:{error_code:'ERR_LOCK',message_ko:'잠김'}}),{status:409});
 await assert.rejects(api.request('/api/dataset/split'),e=>e.status===409&&e.error_code==='ERR_LOCK');
 assert.equal(journal.apiDiagnosticFailures(api.getApiDiagnosticScope()).length,1);
 global.fetch=async()=>new Response('{}');assert.deepEqual(await api.request('/api/dataset/split'),{});
 }finally{global.fetch=old;}
});
test('initial port discovery failures and body JSON failures are journaled, session revision changes retire them',async()=>{
 const {api,journal}=fixture(),old=global.fetch;try{
 api.setCachedPort(null);const boom=new TypeError('initial transport failed');global.fetch=async()=>{throw boom;};await assert.rejects(api.request('/api/training/status'),e=>e===boom);
 assert.equal(journal.apiDiagnosticFailures(api.getApiDiagnosticScope())[0].kind,'network');
 let revision=1;api.setBrowserSessionTransport({authenticated:()=>true,revision:()=>revision,requestOptions:()=>({}),invalidate:()=>{revision++;}});
 global.fetch=async()=>new Response('');await assert.rejects(api.request('/api/training/status'),SyntaxError);assert.equal(journal.apiDiagnosticFailures(api.getApiDiagnosticScope()).length,1);
 revision+=2;assert.deepEqual(journal.apiDiagnosticFailures(api.getApiDiagnosticScope()),[]);
 }finally{global.fetch=old;}
});
test('scope observer failure cannot reject an otherwise successful application request',async()=>{
 const {api}=fixture(),old=global.fetch;let reads=0;api.setLocalHostTransport({available:()=>true,canProjects:()=>false,requestHeaders:()=>({}),projectChanged:()=>{},generation:()=>{if(++reads===1)throw Error('instrumentation only');return 1;},acceptResponseHeaders:()=>{}});
 try{global.fetch=async()=>new Response('{}');assert.deepEqual(await api.request('/api/training/status'),{});}finally{global.fetch=old;}
});
