const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function fixture(){
 const file=path.join(__dirname,'assistantReadWait.ts');assert.ok(fs.existsSync(file),'bounded assistant GET waits must exist');
 let now=0,nextId=0;const timers=new Map(),module={exports:{}};
 const clock={setTimeout(fn,delay){const id=++nextId;timers.set(id,{at:now+delay,fn});return id;},clearTimeout(id){timers.delete(id);},
  tick(ms){now+=ms;for(const [id,row]of [...timers])if(row.at<=now){timers.delete(id);row.fn();}},timers};
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,
  {module,exports:module.exports,AbortController,setTimeout:clock.setTimeout,clearTimeout:clock.clearTimeout});
 return {...module.exports,clock};
}

test('a GET that ignores abort ends at 15 seconds, aborts its signal and never retries',async()=>{
 const {runBoundedAssistantRead,AssistantReadWaitError,clock}=fixture(),reply=deferred();let signal,calls=0,result;
 const waiting=runBoundedAssistantRead(value=>{calls++;signal=value;return reply.promise;});waiting.then(value=>result=value,cause=>result=cause);
 clock.tick(14999);await settle();assert.equal(result,undefined);assert.equal(signal.aborted,false);
 clock.tick(1);await settle();assert.ok(result instanceof AssistantReadWaitError);assert.equal(result.code,'read_timeout');assert.equal(signal.aborted,true);
 assert.equal(calls,1);assert.equal(clock.timers.size,0);
 reply.resolve('late evidence');await settle();assert.equal(result.code,'read_timeout');assert.equal(calls,1);
});

test('stopping an owned GET settles locally and ignores a later result',async()=>{
 const {runBoundedAssistantRead,clock}=fixture(),reply=deferred(),stop=new AbortController();let signal,calls=0;
 const waiting=runBoundedAssistantRead(value=>{calls++;signal=value;return reply.promise;},{signal:stop.signal,timeoutMs:40});
 const rejected=assert.rejects(waiting,cause=>cause.code==='read_stopped');stop.abort();await rejected;
 assert.equal(signal.aborted,true);assert.equal(clock.timers.size,0);reply.resolve('late');clock.tick(100);await settle();assert.equal(calls,1);
});

test('an already stopped GET never starts and completed reads remove their deadline and stop listener',async()=>{
 const {runBoundedAssistantRead,clock}=fixture(),stop=new AbortController();stop.abort();let calls=0;
 await assert.rejects(runBoundedAssistantRead(async()=>{calls++;return 'unwanted';},{signal:stop.signal}),cause=>cause.code==='read_stopped');
 assert.equal(calls,0);assert.equal(clock.timers.size,0);
 const fresh=new AbortController();let removed=0;const original=fresh.signal.removeEventListener.bind(fresh.signal);
 fresh.signal.removeEventListener=(...args)=>{removed++;original(...args);};
 assert.equal(await runBoundedAssistantRead(async()=>({value:'current'}),{signal:fresh.signal}).then(row=>row.value),'current');
 assert.equal(clock.timers.size,0);assert.equal(removed,1);fresh.abort();clock.tick(15000);
});

test('a GET rejection retains the original failure and releases its timer',async()=>{
 const {runBoundedAssistantRead,clock}=fixture(),failure=new Error('controlled read failed');
 await assert.rejects(runBoundedAssistantRead(async()=>{throw failure;}),cause=>cause===failure);assert.equal(clock.timers.size,0);
});

test('invalid deadline overrides retain a usable 15-second GET wait',async()=>{
 for(const timeoutMs of [0,-1,NaN,Infinity]){
  const {runBoundedAssistantRead,clock}=fixture(),reply=deferred();let result;
  const waiting=runBoundedAssistantRead(()=>reply.promise,{timeoutMs});waiting.catch(cause=>result=cause);
  clock.tick(14999);await settle();assert.equal(result,undefined);
  clock.tick(1);await settle();assert.equal(result?.code,'read_timeout');assert.equal(clock.timers.size,0);
 }
});

test('a synchronous GET failure releases the same timer and stop listener as an async rejection',async()=>{
 const {runBoundedAssistantRead,clock}=fixture(),failure=new Error('synchronous read failure'),stop=new AbortController();
 await assert.rejects(runBoundedAssistantRead(()=>{throw failure;},{signal:stop.signal}),cause=>cause===failure);
 assert.equal(clock.timers.size,0);stop.abort();clock.tick(15000);
});
