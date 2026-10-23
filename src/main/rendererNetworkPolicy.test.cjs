// The renderer's network policy (docs/product-direction-easy-labeling-agent.md §4.4, assistant stage 3 precondition).
// In a browser the page reaches only its own origin and loopback: no blanket https:/wss: connect or image source. In the
// desktop app the static policy keeps https:/wss: for the one shared project server chosen at run time, and the main
// process cancels every other HTTPS/WSS request, assistant providers included. Provider calls exist only in the main
// process; the renderer's assistant code has no network call of its own and no browser storage.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),Module=require('node:module'),ts=require('typescript');
const root=path.resolve(__dirname,'..','..');
const read=file=>fs.readFileSync(path.join(root,file),'utf8');
const html=read('index.html');
const parse=policy=>new Map(policy.split(';').map(part=>part.trim()).filter(Boolean).map(part=>{const [name,...sources]=part.split(/\s+/);return [name,sources];}));
const staticPolicy=parse(html.match(/<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"/)[1]);
const LOOPBACK=/^(http|ws):\/\/(127\.0\.0\.1|localhost):\*$/;

function browserPolicy(location,api){
 const script=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match=>match[1]);
 assert.equal(script.length,1,'one inline script, the policy narrowing');
 const appended=[];
 vm.runInNewContext(script[0],{window:{api},location,document:{createElement:()=>({}),head:{appendChild:node=>appended.push(node)}}});
 return appended;
}

test('the browser policy has no blanket https:/wss: and reaches only its own origin and loopback',()=>{
 for(const [location,own] of [[{protocol:'https:',host:'team.example.com'},['wss://team.example.com']],[{protocol:'http:',host:'127.0.0.1:5173'},[]]]){
  const [meta,...extra]=browserPolicy(location,undefined);
  assert.equal(extra.length,0);assert.equal(meta.httpEquiv,'Content-Security-Policy');
  const narrowed=parse(meta.content);
  for(const directive of ['connect-src','img-src']){
   const sources=narrowed.get(directive);
   assert.ok(sources,`${directive} is narrowed`);
   for(const blanket of ['https:','wss:','http:','ws:','*','https://*','wss://*'])assert.ok(!sources.includes(blanket),`${directive} has no ${blanket}`);
   const remote=sources.filter(source=>!["'self'",'data:','blob:'].includes(source)&&!LOOPBACK.test(source));
   assert.deepEqual(remote,directive==='connect-src'?own:[],`${directive}: only the page's own WSS origin besides loopback`);
  }
 }
});

test('the desktop policy keeps https:/wss: only for the shared server, and the bridge skips the narrowing',()=>{
 assert.deepEqual(browserPolicy({protocol:'file:',host:''},{platform:'darwin'}),[],'with the preload bridge the page keeps the desktop policy');
 assert.deepEqual(staticPolicy.get('default-src'),["'self'"]);assert.deepEqual(staticPolicy.get('object-src'),["'none'"]);
 const remote=directive=>staticPolicy.get(directive).filter(source=>!["'self'",'data:','blob:'].includes(source)&&!LOOPBACK.test(source));
 assert.deepEqual(remote('connect-src'),['https:','wss:'],'no plain HTTP elsewhere and no provider host in the page policy');
 assert.deepEqual(remote('img-src'),['https:']);
 const main=read('src/main/index.ts');
 assert.match(main,/onBeforeRequest\(\{ urls: \['https:\/\/\*\/\*', 'wss:\/\/\*\/\*'\] \}, \(details, callback\) => \{\s*callback\(\{ cancel: !isSharedServerUrl\(details\.url\) \}\);/,
  'the main process cancels every renderer HTTPS/WSS request except the connected shared server');
});

test('the main-process filter refuses assistant providers even while a shared server is connected',async()=>{
 const file=path.join(root,'src/main/sharedSession.ts');const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
 const session=m.exports;
 const providers=['https://api.openai.com/v1/chat/completions','https://api.openai.com/v1/responses','https://api.anthropic.com/v1/messages','https://auth.openai.com/api/accounts/authorize','wss://api.openai.com/v1/realtime'];
 for(const url of providers)assert.equal(session.isSharedServerUrl(url),false,`not connected: ${url}`);
 const fetch=globalThis.fetch;
 globalThis.fetch=async()=>({ok:true,json:async()=>({token:'placeholder-session',expires_at:Date.now()/1000+3600,user:{id:'u1',username:'tester',administrator:false}})});
 try{await session.loginSharedServer({server_url:'https://team.example.com',username:'tester',password:'placeholder-password'});}
 finally{globalThis.fetch=fetch;}
 for(const url of providers)assert.equal(session.isSharedServerUrl(url),false,`connected elsewhere: ${url}`);
 assert.equal(session.isSharedServerUrl('https://team.example.com/api/project/list'),true);
 assert.equal(session.isSharedServerUrl('wss://team.example.com/ws/telemetry'),true);
 assert.equal(session.isSharedServerUrl('https://team.example.com/collect'),false,'only the server\'s /api and /ws paths');
 globalThis.fetch=async()=>({ok:true});try{await session.disconnectSharedServer();}finally{globalThis.fetch=fetch;}
});

test('provider calls live only in the main process; the renderer assistant has no network or storage of its own',()=>{
 const files=['src/renderer/stores/useAssistantStore.ts',
  ...fs.readdirSync(path.join(root,'src/renderer/services')).filter(name=>/^assistant.*\.ts$/.test(name)).map(name=>`src/renderer/services/${name}`),
  ...fs.readdirSync(path.join(root,'src/renderer/components/assistant')).filter(name=>name.endsWith('.tsx')).map(name=>`src/renderer/components/assistant/${name}`)];
 assert.ok(files.length>=8);
 for(const file of files){
  const source=read(file).replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm,'');
  for(const pattern of [/\bfetch\(/,/XMLHttpRequest/,/new WebSocket/,/EventSource/,/sendBeacon/,/localStorage/,/sessionStorage/,/indexedDB/,/api\.openai\.com/,/anthropic\.com\/v1/,/x-api-key/i,/Authorization/])
   assert.ok(!pattern.test(source),`${file} must not use ${pattern}`);
 }
 const main=['src/main/assistant/providers.ts','src/main/assistant/service.ts'].map(read).join('\n');
 assert.match(main,/redirect: 'error'/,'a redirect never carries a key to another host');
 assert.ok(!/console\.(log|info|warn|error)/.test(read('src/main/assistant/providers.ts')+read('src/main/assistant/keyStore.ts')+read('src/main/assistant/service.ts')),'nothing in the key path logs');
});
