const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// S1-10: every telemetry (re)connection is announced so the app catches up from the job event cursor, and a pull only
// re-reads the current job when it concerns that job or the stream was reset.
function load(file,mocks){const name=path.join(__dirname,file);const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
  m.require=ref=>ref in mocks?mocks[ref]:original(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('S1-10: a pull re-reads the current job only when it concerns that job or the stream was reset',()=>{
 const {jobNeedsRefresh}=load('jobEventFeed.ts',{'./api':{getApiPersistenceIdentity:()=>'local',getProjectContext:()=>null,request:async()=>({})},'./jobEvents':load('jobEvents.ts',{})});
 const event=job=>({job_id:job});
 assert.equal(jobNeedsRefresh('job_a',{events:[event('job_b')],reset:false,stale:false}),false);
 assert.equal(jobNeedsRefresh('job_a',{events:[event('job_b'),event('job_a')],reset:false,stale:false}),true);
 assert.equal(jobNeedsRefresh('job_a',{events:[],reset:true,stale:false}),true,'a reset means the snapshot is reloaded');
 assert.equal(jobNeedsRefresh('job_a',{events:[event('job_a')],reset:true,stale:true}),false,'an answer for the previous project is ignored');
 assert.equal(jobNeedsRefresh(null,{events:[event('job_a')],reset:true,stale:false}),false,'no current job, nothing to re-read');});
test('S1-10: the stream belongs to one committed project on one API server, and asks with its cursor',async()=>{
 let context={workspace_id:'w',project_id:'p1'};const asked=[];
 const feed=load('jobEventFeed.ts',{'./api':{getApiPersistenceIdentity:()=>'shared:https://server-a',getProjectContext:()=>context,
   request:async url=>{asked.push(url);return {cursor:'c1',events:[],reset:true,reason:'initial',more:false};}},'./jobEvents':load('jobEvents.ts',{})});
 assert.equal(feed.jobEventStreamKey(),JSON.stringify(['shared:https://server-a','w','p1']));
 const stream=feed.createJobEventStream();await stream.pull();await stream.pull();
 assert.deepEqual(asked,['/api/job-events?limit=200','/api/job-events?after=c1&limit=200']);
 context=null;assert.equal(feed.jobEventStreamKey(),null);});
test('S1-10: every telemetry connection, first and after a drop, is announced to listeners',async()=>{
 const sockets=[];class FakeSocket{static OPEN=1;static CONNECTING=0;constructor(url){this.url=url;this.readyState=0;sockets.push(this);}send(){}close(){this.readyState=3;}}
 const previous=global.WebSocket;global.WebSocket=FakeSocket;const realSetTimeout=global.setTimeout;
 try{const {telemetryService}=load('websocket.ts',{'./api':{getApiBaseUrl:async()=>'http://127.0.0.1:1',getProjectContext:()=>({workspace_id:'w',project_id:'p'}),subscribeProjectContext:()=>()=>{}}});
  const heard=[];telemetryService.subscribe(event=>heard.push(event));await telemetryService.connect();
  sockets[0].readyState=1;sockets[0].onopen();assert.deepEqual(heard,['telemetry_connected']);
  global.setTimeout=(fn)=>{fn();return 0;};sockets[0].onclose();await tick();global.setTimeout=realSetTimeout;
  assert.equal(sockets.length,2,'a dropped socket reconnects');sockets[1].readyState=1;sockets[1].onopen();
  assert.deepEqual(heard,['telemetry_connected','telemetry_connected']);telemetryService.disconnect();}
 finally{global.WebSocket=previous;global.setTimeout=realSetTimeout;}});
test('S1-10 review: the app catch-up pulls until caught up, re-reads its job when concerned, and stops on a stale stream',async()=>{
 const {catchUpJobEvents}=load('jobEventFeed.ts',{'./api':{getApiPersistenceIdentity:()=>'local',getProjectContext:()=>null,request:async()=>({})},'./jobEvents':load('jobEvents.ts',{})});
 const pulls=[{events:[{job_id:'other'}],reset:false,stale:false,more:true},{events:[{job_id:'mine'}],reset:false,stale:false,more:true},{events:[],reset:false,stale:false,more:false}];
 let refreshed=0;const stream={pull:async()=>pulls.shift()};
 await catchUpJobEvents(stream,()=>'mine',async()=>{refreshed+=1;});
 assert.equal(pulls.length,0,'it pulled until no more remained');assert.equal(refreshed,1,'only the pull with its own job re-read it');
 let calls=0;await catchUpJobEvents({pull:async()=>{calls+=1;return {events:[],reset:false,stale:true,more:true};}},()=>'mine',async()=>assert.fail('stale is never applied'));
 assert.equal(calls,1,'a stale stream stops the catch-up');
 calls=0;await catchUpJobEvents({pull:async()=>{calls+=1;return {events:[],reset:false,stale:false,more:true};}},()=>null,async()=>{},3);
 assert.equal(calls,3,'bounded');});
