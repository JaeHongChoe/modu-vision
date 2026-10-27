const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(name,cache=new Map()){const file=path.join(__dirname,name);if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);cache.set(file,m);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);m.require=ref=>ref.startsWith('./')&&fs.existsSync(path.join(__dirname,ref+'.ts'))?load(ref+'.ts',cache):original(ref);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
async function fixture(run,{discovery='missing',hold=false,admissionStatus=200}={}){
 const oldWindow=global.window,oldFetch=global.fetch,origin='https://localhost:8443',calls=[];let release,ready=discovery==='ready',tabs=0;
 global.window={location:{origin}};
 const capabilities=()=>Response.json({capabilities:{pickPaths:true,openPaths:true,projects:true,assistant:true,updates:true}});
 global.fetch=async(url,options={})=>{const route=new URL(url).pathname;calls.push({route,options});if(route==='/api/accounts/login')return Response.json({expires_at:Date.now()/1000+3600,user:{id:'local-owner',username:'fixture',administrator:true},csrf_token:'fixture_csrf_abcdefghijklmnopqrstuvwxyz'});
  if(route==='/api/local-host/capabilities'){if(hold)return new Promise(resolve=>release=resolve);if(ready)return capabilities();if(discovery==='expired')return Response.json({detail:'Account session expired or unavailable'},{status:401});if(discovery==='disabled')return Response.json({capabilities:{pickPaths:false,openPaths:false,projects:false,assistant:false,updates:false},reasons:{local_host:'Helper unavailable'}});if(discovery==='html')return new Response('<html>Studio fallback</html>');if(discovery==='malformed')return Response.json({capabilities:{pickPaths:'yes'}});if(discovery==='network')throw new Error('Connection unavailable');return Response.json({detail:'Not Found'},{status:404});}
  if(route==='/api/local-host/session'&&admissionStatus!==200)return Response.json({detail:'Native admission unavailable'},{status:admissionStatus});
  if(route==='/api/local-host/session')return ready?Response.json({tab_id:'tab-'+ ++tabs}):Response.json({detail:'Not Found'},{status:404});
  if(route==='/api/local-host/approve')return Response.json({approved:true});if(route==='/api/local-host/dialog/folder')return Response.json({path:'/project/staged',grant_id:'selected-proof'});
  return Response.json({id:'project',name:'Project',task:'detection',project_dir:'/owned/project'});
 };
 const cache=new Map(),session=load('browserSession.ts',cache).browserSession,host=load('browserHost.ts',cache).browserHost,api=load('api.ts',cache),login=()=>session.login({server_url:origin,username:'fixture',password:'fixture password'});
 try{await login();await run({session,host,api,calls,login,enable:()=>ready=true,unhold:()=>hold=false,held:()=>!!release,release:value=>release(value),capabilities});}finally{global.window=oldWindow;global.fetch=oldFetch;}
}
for(const discovery of ['missing','disabled','html','malformed','network'])test('generic HTTPS server '+discovery+' discovery never admits a tab or retries native discovery per project/API request',()=>fixture(async f=>{
 await f.host.initialize().catch(()=>{});
 await f.api.api.project.getCurrent();await f.api.api.project.create({name:'Ordinary project',task:'detection'});
 f.host.projectChanged();await f.api.api.project.getCurrent();await f.host.initialize();
 assert.deepEqual(f.calls.filter(c=>c.route.startsWith('/api/local-host/')).map(c=>[c.options.method,c.route]),[['GET','/api/local-host/capabilities']]);
 assert.deepEqual(f.calls.filter(c=>c.options.method==='POST').map(c=>c.route),['/api/accounts/login','/api/project/create'],'automatic discovery must not add mutation ledger entries');
 const write=f.calls.find(c=>c.route==='/api/project/create');assert.equal(new Headers(write.options.headers).get('X-Vision-CSRF'),'fixture_csrf_abcdefghijklmnopqrstuvwxyz');assert.equal(new Headers(write.options.headers).get('X-Vision-Local-Tab'),null);assert.equal(f.session.connection()?.user.id,'local-owner');assert.equal(f.host.can('pickPaths'),false);
 },{discovery}));
test('a new authenticated session retries prior negative discovery and obtains a fresh native tab only after capabilities',()=>fixture(async f=>{
 await f.host.initialize().catch(()=>{});f.enable();await f.login();await f.host.initialize();assert.equal(f.host.can('pickPaths'),true);assert.deepEqual(f.calls.filter(c=>c.route.startsWith('/api/local-host/')).map(c=>[c.options.method,c.route]),[['GET','/api/local-host/capabilities'],['GET','/api/local-host/capabilities'],['POST','/api/local-host/session']]);
 await f.host.selectFolder({purpose:'dataset-source'});const chooser=f.calls.find(c=>c.route.endsWith('/dialog/folder'));assert.equal(chooser.options.headers['X-Vision-Local-Tab'],'tab-1');
}));
test('installed helper negotiates once before admission and project replacement gets a fresh tab under the same authenticated discovery',()=>fixture(async f=>{
 await Promise.all([f.host.initialize(),f.host.initialize()]);await f.host.initialize();assert.deepEqual(f.calls.filter(c=>c.route.startsWith('/api/local-host/')).map(c=>[c.options.method,c.route]),[['GET','/api/local-host/capabilities'],['POST','/api/local-host/session']]);
 f.host.projectChanged();await f.host.initialize();assert.equal(f.calls.filter(c=>c.route.endsWith('/capabilities')).length,1);assert.equal(f.calls.filter(c=>c.route.endsWith('/session')).length,2);assert.equal(f.host.requestHeaders('/api/project/current')['X-Vision-Local-Tab'],'tab-2');
},{discovery:'ready'}));
test('an old capability response after project replacement cannot admit a tab or republish capabilities',()=>fixture(async f=>{
 const pending=f.host.initialize();pending.catch(()=>{});for(let i=0;i<100&&!f.held();i++)await new Promise(setImmediate);assert.equal(f.held(),true);f.host.projectChanged();f.release(f.capabilities());await assert.rejects(pending,/바뀌/);assert.equal(f.calls.some(c=>c.route.endsWith('/session')),false);assert.equal(f.host.can('pickPaths'),false);
},{discovery:'ready',hold:true}));

test('a new login does not join or accept the old session capability negotiation',()=>fixture(async f=>{
 const old=f.host.initialize();old.catch(()=>{});for(let i=0;i<100&&!f.held();i++)await new Promise(setImmediate);assert.equal(f.held(),true);
 await f.login();f.unhold();const fresh=f.host.initialize();fresh.catch(()=>{});for(let i=0;i<100&&f.calls.filter(c=>c.route.endsWith('/capabilities')).length<2;i++)await new Promise(setImmediate);
 assert.equal(f.calls.filter(c=>c.route.endsWith('/capabilities')).length,2,'new authority must negotiate independently of the old pending request');
 await fresh;f.release(f.capabilities());await assert.rejects(old,/바뀌/);assert.equal(f.host.can('pickPaths'),true);assert.equal(f.calls.filter(c=>c.route.endsWith('/session')).length,1);
},{discovery:'ready',hold:true}));

test('failed tab admission is bounded to the authenticated lifecycle after successful read-only discovery',()=>fixture(async f=>{
 await f.host.initialize().catch(()=>{});await f.api.api.project.getCurrent();await f.api.api.project.getCurrent();f.host.projectChanged();await f.host.initialize();
 assert.deepEqual(f.calls.filter(c=>c.route.startsWith('/api/local-host/')).map(c=>[c.options.method,c.route]),[['GET','/api/local-host/capabilities'],['POST','/api/local-host/session']]);assert.equal(f.host.can('pickPaths'),false);assert.equal(f.session.connection()?.user.id,'local-owner');
},{discovery:'ready',admissionStatus:503}));
test('explicit host reset permits fresh read-only negotiation after a negative result',()=>fixture(async f=>{
 await f.host.initialize().catch(()=>{});f.enable();f.host.reset();await f.host.initialize();assert.equal(f.host.can('pickPaths'),true);assert.equal(f.calls.filter(c=>c.route.endsWith('/capabilities')).length,2);assert.equal(f.calls.filter(c=>c.route.endsWith('/session')).length,1);
}));
test('a definitive local discovery 401 retires memory authority and never admits a tab',()=>fixture(async f=>{
 await assert.rejects(f.host.initialize());assert.equal(f.session.connection(),null);await assert.rejects(f.api.api.project.getCurrent(),error=>error.name==='BrowserSessionRequiredError');assert.deepEqual(f.calls.filter(c=>c.route.startsWith('/api/local-host/')).map(c=>[c.options.method,c.route]),[['GET','/api/local-host/capabilities']]);
},{discovery:'expired'}));
test('old automatic preparation failure cannot clear native readiness established by a new login',()=>fixture(async f=>{
 const old=f.host.prepareRequest();for(let i=0;i<100&&!f.held();i++)await new Promise(setImmediate);assert.equal(f.held(),true);await f.login();f.unhold();await f.host.initialize();assert.equal(f.host.can('pickPaths'),true);
 f.release(f.capabilities());await old;assert.equal(f.host.can('pickPaths'),true,'a retired request must not clear the current authenticated host');
},{discovery:'ready',hold:true}));
test('normal API preparation after re-login cannot reuse the previous authenticated tab',()=>fixture(async f=>{
 await f.host.initialize();await f.login();await f.api.api.project.getCurrent();assert.equal(f.calls.filter(c=>c.route.endsWith('/capabilities')).length,2);assert.equal(f.calls.filter(c=>c.route.endsWith('/session')).length,2);const read=f.calls.findLast(c=>c.route.endsWith('/projects/current'));assert.equal(new Headers(read.options.headers).get('X-Vision-Local-Tab'),'tab-2');
},{discovery:'ready'}));
