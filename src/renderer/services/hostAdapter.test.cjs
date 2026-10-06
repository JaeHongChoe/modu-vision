const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// S1-10: desktop host features go through the preload bridge; a plain browser refuses them with an explicit message
// (never a silent no-op), opens links in a new tab without access to the page, and needs no bridge for anything else.
function load(name='hostAdapter.ts'){const file=path.join(__dirname,name);const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);m.require=key=>key==='./browserSession'?load('browserSession.ts'):original(key);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
function withWindow(value,run){const previous=global.window;global.window=value;return Promise.resolve().then(run).finally(()=>{global.window=previous;});}
test('S1-10: a browser host refuses desktop features with what to do instead',()=>withWindow({open:()=>null},async()=>{
 const {host,HostUnavailable,hostErrorMessage}=load();
 assert.equal(host.kind(),'browser');for(const capability of ['pickPaths','openPaths','sharedSessions','updates'])assert.equal(host.can(capability),false,capability);
 await assert.rejects(()=>host.selectFolder({title:'x'}),error=>error instanceof HostUnavailable&&error.capability==='pickPaths'&&/경로를 직접 입력하세요/.test(error.message));
 await assert.rejects(()=>host.selectFile(),error=>error.capability==='pickPaths');
 await assert.rejects(()=>host.openPath('/data/report.html'),error=>error.capability==='openPaths'&&/폴더나 파일을 열 수 없습니다/.test(error.message));
 await assert.rejects(()=>host.shared.login({server_url:'https://a',username:'u',password:'p'}),error=>error.capability==='sharedSessions');
 await assert.rejects(()=>host.shared.select('p1'),error=>error.capability==='sharedSessions');
 await assert.rejects(()=>host.updates.status(),error=>error.capability==='updates');
 assert.equal(await host.shared.connection(),null,'no session to read is not an error');await host.shared.disconnect();
 assert.equal(hostErrorMessage(new HostUnavailable('openPaths','설명'),'기본'),'설명');assert.equal(hostErrorMessage('x','기본'),'기본');}));
test('S1-10: a browser opens web links in a new tab without access to the page, and nothing else',()=>{const opened=[];return withWindow({open:(...args)=>{opened.push(args);return {};}},async()=>{
 const {host}=load();assert.equal(await host.openLink('https://pytorch.org/get-started/locally/'),true);
 assert.deepEqual(opened,[['https://pytorch.org/get-started/locally/','_blank','noopener,noreferrer']]);
 assert.equal(await host.openLink('file:///etc/passwd'),false,'only web links');assert.equal(await host.openLink('/data/folder'),false);assert.equal(opened.length,1);});});
test('S1-10: the desktop host uses the preload bridge for every feature',()=>{const calls=[];const api={
  selectFolder:async options=>{calls.push(['selectFolder',options]);return '/picked';},selectFile:async options=>{calls.push(['selectFile',options]);return '/file.zip';},
  openExternal:async target=>{calls.push(['openExternal',target]);return true;},getSharedConnection:async()=>({server_url:'https://s'}),
  loginSharedServer:async input=>{calls.push(['login',input.username]);return {server_url:input.server_url};},selectSharedProject:async id=>{calls.push(['select',id]);return {project_id:id};},
  disconnectSharedServer:async()=>{calls.push(['disconnect']);},getDistributionStatus:async()=>({app_version:'1'}),configureUpdateChannel:async c=>({c}),checkForUpdate:async()=>({}),downloadUpdate:async()=>({})};
 return withWindow({api,open:()=>assert.fail('the desktop opens links through its shell')},async()=>{
  const {host}=load();assert.equal(host.kind(),'desktop');for(const capability of ['pickPaths','openPaths','sharedSessions','updates'])assert.equal(host.can(capability),true,capability);
  assert.equal(await host.selectFolder({title:'t'}),'/picked');assert.equal(await host.selectFile({title:'f'}),'/file.zip');
  assert.equal(await host.openLink('https://pytorch.org'),true);assert.equal(await host.openPath('/data/report.html'),true);
  assert.deepEqual(await host.shared.connection(),{server_url:'https://s'});await host.shared.login({server_url:'https://s',username:'u',password:'p'});await host.shared.select('p1');await host.shared.disconnect();
  assert.deepEqual((await host.updates.status()).app_version,'1');
  assert.deepEqual(calls.map(call=>call[0]),['selectFolder','selectFile','openExternal','openExternal','login','select','disconnect']);});});
test('S1-10: a bridge without a feature is treated as not having it',()=>withWindow({api:{selectFolder:async()=>'/x'}},async()=>{
 const {host}=load();assert.equal(host.kind(),'desktop');assert.equal(host.can('pickPaths'),false,'both pickers are needed');
 assert.equal(await host.selectFolder(),'/x');await assert.rejects(()=>host.selectFile(),error=>error.capability==='pickPaths');await assert.rejects(()=>host.openPath('/x'),error=>error.capability==='openPaths');}));
test('S1-10: components never call the bridge directly (backend status in App and the port lookup excepted)',()=>{
 const root=path.join(__dirname,'..');const offenders=[];
 const visit=directory=>{for(const entry of fs.readdirSync(directory,{withFileTypes:true})){const file=path.join(directory,entry.name);
  if(entry.isDirectory())visit(file);else if(/\.tsx?$/.test(entry.name)&&!entry.name.endsWith('.d.ts')){const text=fs.readFileSync(file,'utf8');
   for(const match of text.matchAll(/window(?: as any\))?\.api\??\.(\w+)/g)){const allowed=(file.endsWith('App.tsx')&&/^(getBackendStatus|onBackendStatusChange|onBackendCrashed)$/.test(match[1]))
    ||(file.endsWith(path.join('services','api.ts'))&&match[1]==='getBackendPort')||(file.endsWith('WizardHeader.tsx')&&match[1]==='platform');if(!allowed)offenders.push(`${path.relative(root,file)}: ${match[0]}`);}}}};
 visit(root);assert.deepEqual(offenders,[]);});
