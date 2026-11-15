/** Actual assistant registry/catalog/request → original authorized ASGI → existing diagnostic projection.
 * Only the owned stdin transport and store snapshot are controlled. No socket or provider/model. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),{spawn}=require('node:child_process'),readline=require('node:readline');
test('owned assistant read survives original local API and returns the same causal chain through diagnostics',{skip:!process.env.MODU_TRACE_TEST_PYTHON,timeout:30000},async()=>{
 const child=spawn(process.env.MODU_TRACE_TEST_PYTHON,['-u','-m','backend.tests.fixtures.local_trace_owned_bridge'],{cwd:path.resolve(__dirname,'../../..'),env:process.env,stdio:['pipe','pipe','pipe']});
 const queued=[],waiters=[];let stderr='';child.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:child.stdout});lines.on('line',line=>{let value;try{value=JSON.parse(line);}catch{return;}if(waiters.length)waiters.shift()(value);else queued.push(value);});
 const next=()=>queued.length?Promise.resolve(queued.shift()):new Promise(r=>waiters.push(r));
 const old=global.fetch;
 try {
  const ready=await next();assert.equal(ready.ready,true);
  const stores={project:{projectDir:'/owned',project:{id:ready.context.project_id,project_dir:'/owned',task:'classification',source_dataset_dir:'/owned-source',active_labelset_id:'default'},task:'classification'},data:{hasSelectedFolder:true,folderPath:'/owned-source',datasetKey:'/owned-source\0classification',lastImportedKey:'/owned-source\0classification',sourceAuthority:'owned-source'},compute:{transportRevision:1}};
  const store=s=>({getState:()=>s,subscribe:()=>()=>{}}),mocks={'../stores/useProjectStore':{useProjectStore:store(stores.project)},'../stores/useDatasetStore':{useDatasetStore:store(stores.data),readDatasetAuthority:()=>'owned-source'},'../stores/useComputeStore':{useComputeStore:store(stores.compute)},'../stores/assistContext':{assistPanelErrors:()=>[]}};
  for(const [file,exportName] of [['useTrainingStore','useTrainingStore'],['useEvaluationStore','useEvaluationStore'],['useFlowchartStore','useFlowchartStore'],['useAnnotationStore','useAnnotationStore'],['useInspectionRunStore','useInspectionRunStore']])mocks['../stores/'+file]={[exportName]:store({}),currentEvaluationGuidanceSource:()=>null};
  const cache=new Map();function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const original=m.require.bind(m);m.require=n=>mocks[n]||(n.startsWith('.')?load(path.resolve(path.dirname(file),n)+'.ts'):original(n));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
  const api=load(path.join(__dirname,'api.ts')),trace=load(path.join(__dirname,'localTrace.ts')).localTrace;
  api.setCachedPort(56050);api.setProjectContext(ready.context);
  global.fetch=async(url,opts)=>{child.stdin.write(JSON.stringify({path:new URL(url).pathname+new URL(url).search,method:opts.method||'GET',headers:Object.fromEntries(opts.headers)})+'\n');const result=await next();return new Response(result.body,{status:result.status,headers:result.headers});};
  const commands=load(path.join(__dirname,'assistantCommandCatalog.ts')).createAssistantCommandCatalog({scope:()=>api.getApiDiagnosticScope(),canObserve:()=>true,request:api.request});
  const diagnostics=load(path.join(__dirname,'assistantDiagnosticsRuntime.ts')).readAppDiagnostics;
  const tools=load(path.join(__dirname,'assistantTools.ts')).createAssistantTools({commands,diagnostics,snapshot:()=>null,facts:()=>({})});
  const read=await tools.runAutomatic('read_app_command',{operation:'GET /api/project/labelsets'});assert.equal(read.ok,true);
  const tool=trace.read().spans.find(r=>r.operation==='assistant.read_app_command');assert.ok(tool);
  const apiSpan=trace.read().spans.find(r=>r.parent_span_id===tool.span_id&&r.operation==='api.project');assert.ok(apiSpan);assert.equal(apiSpan.status,'ok');
  const readback=JSON.parse((await tools.runAutomatic('read_app_diagnostics',{section:'traces'})).content);
  assert.equal(readback.state,'observed');assert.equal(readback.async_jobs,'unlinked');
  const backend=readback.spans.find(r=>r.side==='backend'&&r.parent_span_id===apiSpan.span_id);assert.ok(backend,'original backend child is visible through existing diagnostics');assert.equal(backend.trace_id,tool.trace_id);assert.equal(backend.status,'ok');assert.equal(backend.code,200);assert.ok(backend.duration_ms>=0);
  assert.doesNotMatch(JSON.stringify(readback),/owned-source|Vision-Token|actor_id|project_id|prompt|stack/);
 } finally {global.fetch=old;child.stdin.end();await new Promise(resolve=>{if(child.exitCode!==null){resolve();return;}const timer=setTimeout(()=>{child.kill();resolve();},2000);child.once('exit',()=>{clearTimeout(timer);resolve();});});lines.close();}
});
