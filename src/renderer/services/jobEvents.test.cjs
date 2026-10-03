const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(){const file=path.join(__dirname,'jobEvents.ts');const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
const {JobEventStream}=load();
const event=(job,seq)=>({id:`${job}:${seq}`,job_id:job,kind:'training',seq,event:'transition',from_state:null,to_state:'running',at:seq,payload:null});
const page=(cursor,events,extra={})=>({cursor,events,reset:false,reason:null,more:false,...extra});
test('S1-10: the first pull resets to the server cursor, later pulls continue from it and never repeat an event',async()=>{
 const asked=[];const answers=[page('c1',[],{reset:true,reason:'initial'}),page('c2',[event('a',1),event('a',2)]),page('c3',[event('a',2),event('b',1)])];
 const stream=new JobEventStream(async after=>{asked.push(after);return answers.shift();},()=>'project-1');
 assert.deepEqual(await stream.pull(),{events:[],reset:true,stale:false,more:false});
 assert.deepEqual((await stream.pull()).events.map(e=>e.id),['a:1','a:2']);
 assert.deepEqual((await stream.pull()).events.map(e=>e.id),['b:1'],'a:2 was already seen');
 assert.deepEqual(asked,[null,'c1','c2']);});
test('S1-10: a client far behind reads every page in one pull, within a bound',async()=>{
 let n=0;const stream=new JobEventStream(async()=>{n+=1;return page(`c${n}`,[event('a',n)],{more:true});},()=>'p');
 const pulled=await stream.pull();assert.equal(pulled.events.length,20,'at most 20 pages per pull');assert.equal(n,20);
 const next=await stream.pull();assert.equal(next.events[0].id,'a:21','the next pull continues where the bound stopped');});
test('S1-10: an expired cursor resets the stream; the client reloads its snapshot and continues from the new cursor',async()=>{
 const asked=[];const answers=[page('c1',[event('a',1)]),page('c9',[],{reset:true,reason:'cursor_expired'}),page('c10',[event('a',1)])];
 const stream=new JobEventStream(async after=>{asked.push(after);return answers.shift();},()=>'p');
 await stream.pull();const expired=await stream.pull();assert.equal(expired.reset,true);assert.deepEqual(expired.events,[]);
 assert.deepEqual((await stream.pull()).events.map(e=>e.id),['a:1'],'after a reset an id may be delivered again');assert.deepEqual(asked,[null,'c1','c9']);});
test('S1-10: an answer for the previous project is discarded and the new project starts without a cursor',async()=>{
 let key='project-1';const asked=[];let release;
 const stream=new JobEventStream(after=>{asked.push(after);if(asked.length===1)return Promise.resolve(page('p1-c1',[],{reset:true}));if(asked.length===2)return new Promise(r=>{release=r;});return Promise.resolve(page('p2-c1',[],{reset:true}));},()=>key);
 await stream.pull();const pending=stream.pull();key='project-2';release(page('p1-c2',[event('old',1)]));
 assert.deepEqual(await pending,{events:[],reset:false,stale:true,more:false});
 const fresh=await stream.pull();assert.equal(fresh.reset,true);assert.deepEqual(asked,[null,'p1-c1',null],'no cursor of project 1 is sent for project 2');});
test('S1-10: pulls during a pull share one more read after it, and no project means nothing is read',async()=>{
 let calls=0;const stream=new JobEventStream(async()=>{calls+=1;return page(`c${calls}`,calls===1?[event('a',1)]:[event('a',2)]);},()=>'p');
 const [first,second,third]=await Promise.all([stream.pull(),stream.pull(),stream.pull()]);
 assert.equal(calls,2,'the in-flight read, then one more for every trigger that arrived during it');
 assert.deepEqual(first.events.map(e=>e.id),['a:1']);assert.equal(second,third);assert.deepEqual(second.events.map(e=>e.id),['a:2'],'what was recorded after their trigger');
 const none=new JobEventStream(async()=>assert.fail('no request without a project'),()=>null);assert.deepEqual(await none.pull(),{events:[],reset:false,stale:true,more:false});});
test('S1-10 review: a failed page or a discarded pull moves nothing, so the next pull reads those events again',async()=>{
 const asked=[];let fail=true;
 const stream=new JobEventStream(async after=>{asked.push(after);if(after===null)return page('c1',[],{reset:true});if(after==='c1')return page('c2',[event('a',1)],{more:true});if(fail)throw new Error('connection dropped');return page('c3',[event('a',2)]);},()=>'p');
 await stream.pull();
 await assert.rejects(stream.pull(),/connection dropped/);
 fail=false;const again=await stream.pull();
 assert.deepEqual(again.events.map(e=>e.id),['a:1','a:2'],'the first page of the failed pull is delivered, not lost');
 assert.deepEqual(asked,[null,'c1','c2','c1','c2']);
 let key='A',switched=false;const switching=new JobEventStream(async after=>{if(after===null)return page('a1',[],{reset:true});if(!switched){switched=true;key='B';}return page('a2',[event('x',1)]);},()=>key);
 await switching.pull();const stale=await switching.pull();assert.equal(stale.stale,true);
 key='A';const back=await switching.pull();assert.deepEqual(back.events.map(e=>e.id),['x:1'],'returning to the stream, the discarded page is read again');});
test('S1-10 review: a pull that stops at the page limit says more remain',async()=>{
 let n=0;const stream=new JobEventStream(async()=>{n+=1;return page(`c${n}`,[event('a',n)],{more:true});},()=>'p');
 const pulled=await stream.pull();assert.equal(pulled.more,true);assert.equal(pulled.events.length,20);});
test('S1-10 follow-up: reads never overlap, even for a pull that arrives as a read settles with a follow-up queued',async()=>{
 let active=0,most=0,calls=0;const gates=[];
 const stream=new JobEventStream(async()=>{calls+=1;active+=1;most=Math.max(most,active);await new Promise(resolve=>gates.push(resolve));active-=1;return page(`c${calls}`,[event('a',calls)]);},()=>'p');
 const first=stream.pull();const second=stream.pull();
 await new Promise(setImmediate);gates.shift()();await first;
 const third=stream.pull();  // the first read settled; the follow-up is queued but may not have started
 await new Promise(setImmediate);while(gates.length)gates.shift()();
 const [b,c]=await Promise.all([second,third]);
 assert.equal(b,c,'the late pull joined the queued follow-up');assert.equal(most,1,'never two reads at once');assert.equal(calls,2);});
test('S1-10 follow-up: a pull discarded by a project switch commits nothing, against a server that honours the cursor',async()=>{
 const log=[{id:'x:1'},{id:'x:2'}].map((e,i)=>({...event('x',i+1)}));const asked=[];let key='A',switchOnce=true;
 const stream=new JobEventStream(async after=>{asked.push(after);
   if(after===null)return page('p0',[],{reset:true});
   const position=Number(after.slice(1));if(switchOnce&&position===0){switchOnce=false;key='B';}
   const events=log.slice(position);return page(`p${log.length}`,events);},()=>key);
 await stream.pull();const stale=await stream.pull();assert.equal(stale.stale,true);
 key='A';const back=await stream.pull();
 assert.deepEqual(back.events.map(e=>e.id),['x:1','x:2'],'nothing was lost');assert.deepEqual(asked,[null,'p0','p0'],'the discarded read did not move the cursor');});
