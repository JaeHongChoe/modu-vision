const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),{EventEmitter}=require('node:events'),{spawn}=require('node:child_process');
function load(){const file=path.join(__dirname,'supervisor.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);
 m.require=name=>name==='./applicationLaunch'?{OwnedApplicationLaunch:class{}}:req(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);
 m.exports.BackendSupervisor.prototype.registerProcessHooks=()=>{};return m.exports.BackendSupervisor;}
function fake(){const proc=new EventEmitter();Object.assign(proc,{pid:'controlled-not-a-process',killed:false,exitCode:null,signalCode:null,signals:[],closes:0});proc.stdin={end(){proc.closes++;}};proc.kill=signal=>{proc.signals.push(signal);proc.killed=true;return true;};return proc;}
function owner(proc){const S=load(),s=new S({gracefulShutdownTimeoutMs:25,autoRestart:false});s.childProcess=proc;s.port=54321;s.state='HEALTHY';return s;}
const exit=proc=>{proc.exitCode=0;proc.emit('exit',0,null);};
test('a force attempt is not exit: retain original handle and block replacement until its exit event',async()=>{
 const p=fake(),s=owner(p);try{
  await assert.rejects(s.stopBackend(),/original.*exit.*unverified/i);
  assert.equal(s.childProcess,p);assert.equal(s.getStatusInfo().state,'STOPPING');assert.equal(s.getStatusInfo().pid,p.pid);
  assert.deepEqual(p.signals,['SIGTERM','SIGKILL']);assert.equal(p.closes,1);
  await assert.rejects(s.startBackend(),/previous.*backend.*exit.*unverified/i);
  exit(p);await new Promise(r=>setImmediate(r));assert.equal(s.childProcess,null);assert.equal(s.getStatusInfo().state,'STOPPED');
 }finally{s.childProcess=null;}
});
test('killed means a signal was requested, not that the original process exited',async()=>{
 const p=fake();p.killed=true;const s=owner(p);try{
  await assert.rejects(s.stopBackend(),/original.*exit.*unverified/i);assert.equal(s.childProcess,p);assert.equal(s.getStatusInfo().state,'STOPPING');
  exit(p);assert.equal(s.childProcess,null);
 }finally{s.childProcess=null;}
});
test('concurrent stops share original shutdown and cannot create duplicate timers or early completion',async()=>{
 const p=fake(),s=owner(p);try{
  const results=await Promise.allSettled([s.stopBackend(),s.stopBackend()]);
  assert.deepEqual(results.map(x=>x.status),['rejected','rejected']);assert.equal(p.closes,1);assert.deepEqual(p.signals,['SIGTERM','SIGKILL']);assert.equal(s.childProcess,p);
  exit(p);assert.equal(s.getStatusInfo().state,'STOPPED');
 }finally{s.childProcess=null;}
});
test('normal stop resolves only after the original real child handle reports exit',async()=>{
 const p=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>process.exit(0));process.stdin.resume();process.stdin.on('end',()=>process.exit(0));process.stdout.write('READY\\n');"],{stdio:['pipe','pipe','pipe']}),s=owner(p);let exited=false;
 const finished=new Promise((resolve,reject)=>{p.once('error',reject);p.once('exit',()=>{exited=true;resolve();});});
 try{
  await new Promise((resolve,reject)=>{p.stdout.once('data',resolve);p.once('error',reject);p.once('exit',()=>reject(Error('Original child exited before ready')));});
  s.config.gracefulShutdownTimeoutMs=4000;await s.stopBackend();assert.equal(exited,true);assert.equal(s.childProcess,null);assert.equal(s.getStatusInfo().state,'STOPPED');await finished;
 }finally{if(!exited){p.kill('SIGKILL');await finished;}s.childProcess=null;}
});

test('a late exit of the original timed-out handle cannot clear a different tracked successor',async()=>{
 const p=fake(),s=owner(p),next=fake();try{
  await assert.rejects(s.stopBackend(),/original.*exit.*unverified/i);
  // Controlled external replacement probes the late listener's identity guard;
  // production startBackend itself refuses replacement while p is unresolved.
  s.childProcess=next;s.state='HEALTHY';exit(p);
  assert.equal(s.childProcess,next);assert.equal(s.getStatusInfo().state,'HEALTHY');
 }finally{s.childProcess=null;}
});
