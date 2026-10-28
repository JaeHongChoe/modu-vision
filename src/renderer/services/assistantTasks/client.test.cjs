const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(){const file=path.join(__dirname,'client.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);m.require=name=>name==='../api'?{request:()=>{throw Error('unmocked request');}}:original(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
const ID='task_'+'a'.repeat(32),task={schema_version:1,id:ID,revision:1,status:'draft',steps:[]};
test('task client uses actor-scoped preparation API and never inserts a project/account capability',async()=>{
 const calls=[],send=async(url,options)=>{calls.push([url,options]);return url==='/api/assistant-tasks'&&!options?.method?{tasks:[task]}:{task};};
 const client=load().createTaskClient(send),plan={goal:'검사 준비',binding:'local only',steps:[{action:'open_step',args:{step:1},label:'자료 준비'}]};
 assert.deepEqual(await client.create(plan),task);assert.deepEqual(await client.list(),[task]);assert.deepEqual(await client.get(ID),task);
 await client.event(ID,{expected_revision:1,binding:'local only',type:'approve',confirmed:true});
 assert.equal(calls.length,4);for(const [,options] of calls){assert.equal(options.projectContext,null);assert.equal(options.headers,undefined);}
 assert.equal(calls[0][1].method,'POST');assert.deepEqual(JSON.parse(calls[0][1].body),plan);
 assert.equal(calls[3][0],`/api/assistant-tasks/${ID}/events`);
});
test('lost response is surfaced once, not retried or treated as an empty task',async()=>{
 let calls=0;const client=load().createTaskClient(async()=>{calls++;throw Error('connection lost');});
 await assert.rejects(client.event(ID,{expected_revision:1,binding:'x',type:'begin_step',step_id:'step_1'}),/connection lost/);assert.equal(calls,1);
 const malformed=load().createTaskClient(async()=>({}));await assert.rejects(malformed.get(ID),/작업 기록/);
});
test('invalid task identity refuses before any request',async()=>{
 let calls=0;const client=load().createTaskClient(async()=>{calls++;return {task};});
 for(const id of ['../other','task_a','https://example.com'])await assert.rejects(client.get(id),/작업 ID/);
 assert.equal(calls,0);
});
test('task GETs forward only an optional abort signal while mutations keep their original wait contract',async()=>{
 const calls=[],abort=new AbortController(),client=load().createTaskClient(async(url,options)=>{calls.push([url,options]);return url==='/api/assistant-tasks'&&!options?.method?{tasks:[task]}:{task};});
 await client.list(abort.signal);await client.get(ID,abort.signal);await client.create({goal:'준비',binding:'x',steps:[]});await client.event(ID,{type:'pause',expected_revision:1,binding:'x'});
 assert.equal(calls[0][1].signal,abort.signal);assert.equal(calls[1][1].signal,abort.signal);
 assert.equal(Object.hasOwn(calls[2][1],'signal'),false);assert.equal(Object.hasOwn(calls[3][1],'signal'),false);assert.equal(calls.length,4);
});
