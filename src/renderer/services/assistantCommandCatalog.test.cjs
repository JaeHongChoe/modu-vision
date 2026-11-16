// Product catalog integration, with only authenticated transport and local authority substituted.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,cache=new Map()){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const original=m.require.bind(m);m.require=name=>{if(name.startsWith('.')){const target=path.resolve(path.dirname(file),name)+'.ts';if(fs.existsSync(target))return load(target,cache);}return original(name);};m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
const file=path.join(__dirname,'assistantCommandCatalog.ts');
const commands=fs.existsSync(file)?load(file):{};
const operation=(id='GET /health',overrides={})=>({id,method:id.split(' ')[0],path:id.split(' ')[1],group:'system',summary:'Installed operation',transport:'http',effect:'read',assistant:{mode:'read'},input:{parameters:[]},responses:{'200':{description:'OK'}},...overrides});
const catalog=operations=>({schema:'StudioOperations/v1',operations,groups:[{id:'system',count:operations.length}],total:operations.length,next_offset:null,server_version:'1',components:{schemas:{}}});
function fixture(handler){let generation=0,ready=true;const calls=[];assert.equal(typeof commands.createAssistantCommandCatalog,'function','catalog adapter must be implemented');const adapter=commands.createAssistantCommandCatalog({scope:()=>String(generation),canObserve:()=>ready,request:async(url,options)=>{calls.push([url,options]);return handler?handler(url,options):catalog([operation()]);}});return{adapter,calls,bump:()=>generation++,unready:()=>{ready=false;}};}
test('catalog discovery stays lazy, requests compact pages, and reports CLI versus GPT capability',async()=>{
 const f=fixture();assert.deepEqual(f.calls,[]);const value=await f.adapter.list({group:'system',offset:0,limit:5});
 const url=new URL(f.calls[0][0],'https://fixture.invalid');assert.equal(url.pathname,'/api/automation/catalog');assert.equal(url.searchParams.get('schemas'),'false');assert.equal(url.searchParams.get('limit'),'5');assert.equal(value.operations[0].id,'GET /health');assert.equal(value.operations[0].gpt_permission,'read');assert.match(value.permission_boundary,/CLI/);assert.equal(value.partial,false);
});
test('detail loads schemas on demand and bridges supported actions into propose_task',async()=>{
 const f=fixture(()=>({...catalog([operation('POST /api/training/start',{effect:'execute',assistant:{mode:'task',task_action:'start_training'}})]),components:{schemas:{Settings:{type:'object',properties:{device:{type:'string'}}}}}}));
 const value=await f.adapter.describe({operation:'POST /api/training/start'});assert.ok(f.calls[0][0].includes('schemas=true'));assert.equal(value.operations[0].gpt_permission,'propose_task');assert.equal(value.operations[0].assistant.task_action,'start_training');assert.match(value.operations[0].plan_bridge,/read_app_features.*propose_task/);assert.ok(value.components.schemas.Settings);
});
test('safe observation uses the exact operation and returned method, path, transport, effect and mode',async()=>{
 const f=fixture(url=>url.startsWith('/api/automation/')?catalog([operation()]):{status:'ok',token:'secret',account:{name:'Ada'},path:'/private/account.json',address:'https://private.invalid',nested:{password:'hidden'}});
 const value=await f.adapter.read({operation:'GET /health'});assert.equal(f.calls.length,2);assert.equal(f.calls[1][0],'/health');assert.equal(f.calls[1][1].method,'GET');assert.match(f.calls[1][1].traceParent.traceparent,/^00-[a-f0-9]{32}-[a-f0-9]{16}-01$/);assert.equal(value.data.status,'ok');assert.equal(value.truncated,false);for(const secret of ['secret','Ada','/private','private.invalid','hidden'])assert.ok(!JSON.stringify(value).includes(secret),secret);
});
test('assistant observes the integrated learning platform without starting its runtime or exposing source paths',async()=>{
 const id='GET /api/platform/geti/status';
 const f=fixture(url=>url.startsWith('/api/automation/')?catalog([operation(id)]):{
  available:true,runtime:{state:'stopped'},project:{name:'Private customer project',task:'classification',binding_state:'stale',binding_message:'현재 데이터를 다시 준비하세요.'},
  dataset:{id:'transfer-1',recipe_sha256:'a'.repeat(64),counts:{training:16,validation:4,testing:4,total:24},source_mapping:[{relative_path:'Customer-X/LOT-2026/private-sample.png',source_path:'/private/customer/one.png'}],training_binding:{members:{'Customer-X/LOT-2026/private-sample.png':'secret'}}},
  jobs:[{status:'completed',worker_exited:true}],models:[{variants:[{format:'OpenVINO'}]}],
 });
 const value=await f.adapter.read({operation:id});
 assert.equal(f.calls.length,2);assert.equal(f.calls[1][0],'/api/platform/geti/status');
 assert.equal(value.data.runtime.state,'stopped');assert.equal(value.data.jobs[0].status,'completed');
 assert.equal(value.data.project.binding_state,'stale');assert.equal(value.data.project.binding_message,'현재 데이터를 다시 준비하세요.');
 assert.equal(value.data.dataset.recipe_sha256,'[가림]');
 assert.equal(value.data.dataset.counts.total,24);assert.equal(value.data.dataset.source_mapping,undefined);assert.equal(value.data.dataset.training_binding,undefined);
 assert.ok(!JSON.stringify(value).includes('/private/customer'));assert.ok(!JSON.stringify(value).includes('Private customer'));
 const {sanitizedMessages}=load(path.resolve(__dirname,'../../main/assistant/service.ts'));
 const sent=sanitizedMessages([{role:'tool',toolCallId:'status',name:'read_app_command',content:JSON.stringify(value)}])[0].content;
 for(const privateValue of ['Customer-X','LOT-2026','private-sample.png','secret'])assert.ok(!sent.includes(privateValue),privateValue);
 for(const operation of ['POST /api/platform/geti/start','POST /api/platform/geti/train','POST /api/platform/geti/predict','GET /api/platform/geti/images']){
  const denied=fixture();await assert.rejects(denied.adapter.read({operation}),error=>error.code==='operation_not_permitted');assert.equal(denied.calls.length,0);
 }
});
test('computing GETs, account reads, arbitrary headers and lookalikes never dispatch an operation',async()=>{
 for(const operation of ['GET /api/evaluation/auto-threshold','GET /api/accounts/me','GET /health?probe=1','GET /health/','POST /health','GET https://private.invalid/health']){
  const f=fixture();await assert.rejects(f.adapter.read({operation}),error=>error.code==='operation_not_permitted');assert.equal(f.calls.length,0);
 }
 const f=fixture();for(const args of [{operation:'GET /health',headers:{Authorization:'x'}},{operation:'GET /health',url:'https://x'},{operation:'GET /health',body:{}}])await assert.rejects(f.adapter.read(args),error=>error.code==='invalid_arguments');assert.equal(f.calls.length,0);
});
test('server catalog cannot promote a nonmatching operation, manual action or native transport to a read',async()=>{
 for(const change of [{id:'GET /other'},{method:'POST'},{path:'/api/evaluation/auto-threshold'},{transport:'native'},{effect:'execute'},{assistant:{mode:'manual'}},{assistant:{mode:'task',task_action:'start_training'}}]){
  const f=fixture(()=>catalog([operation('GET /health',change)]));await assert.rejects(f.adapter.read({operation:'GET /health'}),error=>error.code==='operation_not_permitted'||error.code==='invalid_catalog');assert.equal(f.calls.length,1);
 }
});
test('observation requires a selected coherent source and checks authority before catalog and execution',async()=>{
 const f=fixture();f.unready();await assert.rejects(f.adapter.read({operation:'GET /health'}),error=>error.code==='context_changed');assert.equal(f.calls.length,0);
 const g=fixture(()=>{g.bump();g.bump();return catalog([operation()]);});await assert.rejects(g.adapter.read({operation:'GET /health'}),error=>error.code==='context_changed');assert.equal(g.calls.length,1);
});
test('A-B-A changes during observation discard success and failure results without stale data',async()=>{
 for(const fail of [false,true]){let f;f=fixture(url=>{if(url.startsWith('/api/automation/'))return catalog([operation()]);f.bump();f.bump();if(fail)throw Error('/private/secret.txt');return{status:'secret-result'};});await assert.rejects(f.adapter.read({operation:'GET /health'}),error=>error.code==='context_changed');assert.equal(f.calls.length,2);}
});
test('catalog discovery validates schema and returned detail identity and suppresses error secrets',async()=>{
 for(const response of [{...catalog([]),schema:'other'},catalog([operation('GET /other')])]){const f=fixture(()=>response);await assert.rejects(f.adapter.describe({operation:'GET /health'}),error=>error.code==='invalid_catalog');}
 const f=fixture(()=>{throw Error('Bearer topsecret /private/account.json https://secret.invalid');});await assert.rejects(f.adapter.list({}),error=>error.code==='catalog_unavailable'&&!/topsecret|private|secret.invalid/.test(error.message));
});
test('bounded output declares truncation and omitted rows with usable list cursors',async()=>{
 const f=fixture(()=>({...catalog(Array.from({length:40},(_,i)=>operation(`GET /api/test${i}`,{summary:'Installed operation details '.repeat(40)}))),total:80,next_offset:40}));
 const value=await f.adapter.list({limit:40,offset:0});assert.ok(JSON.stringify(value).length<=10000);assert.equal(value.truncated,true);assert.equal(value.partial,true);assert.ok(value.next_offset>0&&value.next_offset<40);assert.equal(value.omitted,80-value.operations.length);
 const g=fixture(url=>url.startsWith('/api/automation/')?catalog([operation()]):{items:Array.from({length:1000},()=>({status:'ok',details:'x'.repeat(1000)}))});const read=await g.adapter.read({operation:'GET /health'});assert.ok(JSON.stringify(read).length<=10000);assert.equal(read.truncated,true);assert.equal(read.partial,true);
});
test('injected catalog tools preserve tier, await results and stay under both provider schema budgets',async()=>{
 const {createAssistantTools}=load(path.join(__dirname,'assistantTools.ts'));const f=fixture();const tools=createAssistantTools({commands:f.adapter,diagnostics:async()=>({}),tasks:{isExecuting:()=>false,features:()=>({}),screen:()=>({}),status:()=>({}),propose:async()=>({})}});
 assert.equal(tools.tier('list_app_commands'),'read');assert.equal(tools.tier('describe_app_command'),'read');assert.equal(tools.tier('read_app_command'),'read');assert.ok(tools.specs().length<=40);assert.ok(JSON.stringify(tools.specs()).length<=24000);const result=JSON.parse((await tools.runAutomatic('list_app_commands',{})).content);assert.equal(result.schema,'StudioOperations/v1');
});
test('provider instructions retain lazy discovery, CLI boundaries and immutable plan execution',()=>{const {SYSTEM_INSTRUCTIONS}=load(path.resolve(__dirname,'../../main/assistant/service.ts'));assert.match(SYSTEM_INSTRUCTIONS,/list_app_commands/);assert.match(SYSTEM_INSTRUCTIONS,/describe_app_command/);assert.match(SYSTEM_INSTRUCTIONS,/read_app_command/);assert.match(SYSTEM_INSTRUCTIONS,/CLI/);assert.match(SYSTEM_INSTRUCTIONS,/propose_task/);assert.match(SYSTEM_INSTRUCTIONS,/불변/);});
test('provider sanitation preserves validated operation templates for command tools while redacting instance paths',()=>{
 const {sanitizedMessages}=load(path.resolve(__dirname,'../../main/assistant/service.ts'));
 const id='GET /api/training-workspace/tasks',row=operation(id);
 const values=sanitizedMessages([{role:'assistant',text:'',toolCalls:[{id:'c',name:'describe_app_command',arguments:{operation:id}}]},{role:'tool',toolCallId:'c',name:'describe_app_command',content:JSON.stringify({...catalog([row]),extra:'/private/customer/result.json'})}]);
 assert.equal(values[0].toolCalls[0].arguments.operation,id);const result=JSON.parse(values[1].content);assert.equal(result.operations[0].id,id);assert.equal(result.operations[0].path,'/api/training-workspace/tasks');assert.ok(!values[1].content.includes('/private/customer'));
 const unsafe=sanitizedMessages([{role:'tool',toolCallId:'c',name:'describe_app_command',content:JSON.stringify(catalog([operation('GET /Users/customer/private.json')]))}]);assert.ok(!unsafe[0].content.includes('/Users/customer'));
 const read=sanitizedMessages([{role:'tool',toolCallId:'c',name:'read_app_command',content:JSON.stringify({schema:'StudioOperations/v1',operation:id,data:{path:'/private/customer/data.json'}})}]);assert.equal(JSON.parse(read[0].content).operation,id);assert.ok(!read[0].content.includes('/private/customer'));
});
test('project selection aliases and account identity fields never enter safe observation output',async()=>{
 const selecting=fixture();await assert.rejects(selecting.adapter.read({operation:'GET /api/project/current'}),error=>error.code==='operation_not_permitted');assert.equal(selecting.calls.length,0);
 const f=fixture(url=>url.startsWith('/api/automation/')?catalog([operation()]):{status:'ok',display_name:'Private Person',user_id:'private-user',credentials:{key:'private-key'},session:{id:'private-session'},account_settings:{company:'Private Company'}});
 const content=JSON.stringify(await f.adapter.read({operation:'GET /health'}));for(const secret of ['Private Person','private-user','private-key','private-session','Private Company'])assert.ok(!content.includes(secret),secret);
});
test('detail schema references and their component closure survive the main provider redactor',async()=>{
 const {sanitizedMessages}=load(path.resolve(__dirname,'../../main/assistant/service.ts'));
 const f=fixture(()=>({...catalog([operation('POST /api/training/start',{effect:'execute',assistant:{mode:'task',task_action:'start_training'},input:{parameters:[],request_body:{content:{'application/json':{schema:{$ref:'#/components/schemas/Settings'}}}}},responses:{'200':{content:{'application/json':{schema:{$ref:'#/components/schemas/Receipt'}}}}}})]),components:{schemas:{Settings:{type:'object',properties:{recipe:{$ref:'#/components/schemas/Recipe'}}},Recipe:{type:'object',properties:{turns:{type:'integer'}}},Receipt:{type:'object',properties:{job_id:{type:'string'}}}}}}));
 const detail=await f.adapter.describe({operation:'POST /api/training/start'});
 const content=sanitizedMessages([{role:'tool',toolCallId:'c',name:'describe_app_command',content:JSON.stringify(detail)}])[0].content, sent=JSON.parse(content);
 assert.equal(sent.operations[0].input.request_body.content['application/json'].schema.$ref,'#/components/schemas/Settings');
 assert.equal(sent.operations[0].responses['200'].content['application/json'].schema.$ref,'#/components/schemas/Receipt');
 assert.equal(sent.components.schemas.Settings.properties.recipe.$ref,'#/components/schemas/Recipe');assert.equal(sent.components.schemas.Recipe.properties.turns.type,'integer');assert.equal(sent.components.schemas.Receipt.properties.job_id.type,'string');
});

test('long registered Pydantic schema identifiers keep a matching reference closure without preserving examples',async()=>{
 const name='Body_preview_input_preparation_api_training_input_preparation_preview_post';
 const f=fixture(()=>({...catalog([operation('POST /api/training/start',{effect:'execute',assistant:{mode:'task',task_action:'start_training'},input:{parameters:[],request_body:{schema:{$ref:`#/components/schemas/${name}`}}}})]),components:{schemas:{[name]:{type:'object',description:'Bearer private-value /private/customer/data.json',properties:{turns:{type:'integer'}}}}}}));
 const {sanitizedMessages}=load(path.resolve(__dirname,'../../main/assistant/service.ts'));
 const detail=await f.adapter.describe({operation:'POST /api/training/start'}),result=JSON.parse(sanitizedMessages([{role:'tool',toolCallId:'c',name:'describe_app_command',content:JSON.stringify(detail)}])[0].content);
 assert.equal(result.operations[0].input.request_body.schema.$ref,`#/components/schemas/${name}`);assert.equal(result.components.schemas[name].properties.turns.type,'integer');assert.ok(!JSON.stringify(result).includes('private-value'));assert.ok(!JSON.stringify(result).includes('/private/customer'));
});
test('oversized details explicitly omit missing schema references after bounding the component closure',async()=>{
 const f=fixture(()=>({...catalog([operation('POST /api/training/start',{effect:'execute',assistant:{mode:'task',task_action:'start_training'},input:{parameters:[],request_body:{schema:{$ref:'#/components/schemas/Huge'}}}})]),components:{schemas:{Huge:{type:'object',properties:Object.fromEntries(Array.from({length:40},(_,i)=>[`field${i}`,{type:'string',description:'Field description '.repeat(30)}]))}}}}));
 const result=await f.adapter.describe({operation:'POST /api/training/start'});assert.ok(JSON.stringify(result).length<=10000);assert.equal(result.truncated,true);assert.equal(result.partial,true);const schema=result.operations[0].input.request_body.schema;assert.equal(schema.$ref,undefined);assert.equal(schema.schema_omitted,true);
});
test('describe preserves credential-named JSON Schema contracts without credential examples or runtime values',async()=>{
 const contract={type:'string',minLength:12,maxLength:1024,writeOnly:true};
 const schema={type:'object',required:['password'],properties:{password:{...contract,default:'private-default-password',examples:['private-example-password'],value:'private-runtime-password'},token:{type:'string',minLength:1,maxLength:200,default:'private-default-token'}}};
 const f=fixture(()=>({...catalog([operation('POST /api/accounts/login',{effect:'execute',assistant:{mode:'manual'},input:{parameters:[],request_body:{content:{'application/json':{schema}}}},responses:{'200':{content:{'application/json':{schema}}}}})]),components:{schemas:{Login:schema}},password:'private-runtime-envelope'}));
 const {sanitizedMessages}=load(path.resolve(__dirname,'../../main/assistant/service.ts'));
 const detail=await f.adapter.describe({operation:'POST /api/accounts/login'}), sent=JSON.parse(sanitizedMessages([{role:'tool',toolCallId:'c',name:'describe_app_command',content:JSON.stringify(detail)}])[0].content);
 for(const value of [sent.operations[0].input.request_body.content['application/json'].schema,sent.operations[0].responses['200'].content['application/json'].schema,sent.components.schemas.Login]){assert.deepEqual(value.properties.password,contract);assert.deepEqual(value.required,['password']);assert.equal(value.properties.token.type,'string');assert.equal(value.properties.token.maxLength,200);}
 for(const secret of ['private-default-password','private-example-password','private-runtime-password','private-default-token','private-runtime-envelope'])assert.ok(!JSON.stringify(sent).includes(secret),secret);
 assert.equal(sent.operations[0].gpt_permission,'manual');assert.equal(sent.partial,false);
});
test('catalog discovery accepts bounded real tag names with spaces and Korean and rejects controls',async()=>{
 for(const group of ['Dataset imports','Image library','한글 자료 그룹']){const f=fixture();await f.adapter.list({group});assert.equal(new URL(f.calls[0][0],'https://fixture.invalid').searchParams.get('group'),group);}
 for(const group of ['bad\nname','bad\tname','bad\u0000name','x'.repeat(81),'']){const f=fixture();await assert.rejects(f.adapter.list({group}),error=>error.code==='invalid_arguments');assert.equal(f.calls.length,0);}
});
