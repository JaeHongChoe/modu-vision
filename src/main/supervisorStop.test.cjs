const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),{EventEmitter}=require('node:events');
// S1-09: the backend is stopped gracefully through its stdin on every platform, and a force stop ends the backend
// process only, through its own handle, never the owned training workers it started (own session or process group).
// The fake backends below are never real processes and carry no process number a real process could have: their pid
// is a string, truthy so the stop path runs, which Node's process.kill rejects before any system call. process.kill
// itself is replaced by a recorder for this whole file, its exit included (every supervisor registers exit hooks that
// outlive a test), and the file fails if anything ever called it.
const signalledAnywhere=[];process.kill=(...args)=>{signalledAnywhere.push(args);return true;};
process.on('exit',()=>{if(signalledAnywhere.length){console.error('process.kill was called:',JSON.stringify(signalledAnywhere));process.exitCode=1;}});
function load(commands){const file=path.join(__dirname,'supervisor.ts');const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
  m.require=name=>name==='child_process'?{spawn:()=>{throw new Error('not spawned in this test');},execSync:command=>{commands.push(command);}}:original(name);
  m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;}
function backend({exitsOnStdinClose}){const proc=new EventEmitter();proc.pid='not-a-process';proc.killed=false;proc.exitCode=null;proc.signalCode=null;proc.events=[];
  proc.stdin={end:()=>{proc.events.push('stdin closed');if(exitsOnStdinClose)setImmediate(()=>{proc.exitCode=0;proc.emit('exit',0,null);});}};
  proc.kill=signal=>{proc.events.push(signal);return true;};return proc;}
// (a recorder, not a throwing trap: forceStop's own try/catch would swallow a throw)
async function stop(platform,proc,commands){const previous=Object.getOwnPropertyDescriptor(process,'platform');const before=signalledAnywhere.length;let supervisor;
  Object.defineProperty(process,'platform',{value:platform});
  try{const {BackendSupervisor}=load(commands);supervisor=new BackendSupervisor({gracefulShutdownTimeoutMs:30,autoRestart:false});supervisor.childProcess=proc;supervisor.state='HEALTHY';await supervisor.stopBackend();
   assert.deepEqual(signalledAnywhere.slice(before),[],'no process number is ever signalled');return supervisor;}
  finally{Object.defineProperty(process,'platform',previous);if(supervisor)supervisor.childProcess=null;}}
test('the backend keeps stdin as its stop channel and opens no console window',()=>{const {backendSpawnOptions}=load([]);
  const options=backendSpawnOptions('/app',{PATH:'/bin',VISION_AI_STUDIO_API_TOKEN:'token'});
  assert.deepEqual(options.stdio,['pipe','pipe','pipe']);assert.equal(options.windowsHide,true);assert.equal(options.cwd,'/app');
  assert.equal(options.env.VISION_AI_STUDIO_STOP_ON_STDIN_EOF,'1');assert.equal(options.env.PATH,'/bin');});
test('on Windows a stop closes stdin and sends no signal that would terminate the backend outright',async()=>{const commands=[],proc=backend({exitsOnStdinClose:true});
  const supervisor=await stop('win32',proc,commands);assert.deepEqual(proc.events,['stdin closed']);assert.deepEqual(commands,[]);assert.equal(supervisor.getStatusInfo().state,'STOPPED');});
test('on POSIX a stop closes stdin, then sends SIGTERM',async()=>{const commands=[],proc=backend({exitsOnStdinClose:true});
  await stop('darwin',proc,commands);assert.deepEqual(proc.events,['stdin closed','SIGTERM']);assert.deepEqual(commands,[]);});
test('a force-stopped backend that exits after a restart started its successor is not taken for a crash of it',()=>{
 const {BackendSupervisor}=load([]);const supervisor=new BackendSupervisor({autoRestart:false});const crashes=[];supervisor.handleUnexpectedCrash=(...args)=>crashes.push(args);
 const previous=backend({exitsOnStdinClose:false}),successor=backend({exitsOnStdinClose:false});previous.stdout=null;previous.stderr=null;successor.stdout=null;successor.stderr=null;
 supervisor.attachProcessListeners(previous);supervisor.attachProcessListeners(successor);supervisor.childProcess=successor;supervisor.isShuttingDown=false;
 previous.emit('exit',null,'SIGKILL');assert.deepEqual(crashes,[],'the old backend is no longer tracked');assert.equal(supervisor.childProcess,successor);
 successor.emit('exit',1,null);assert.equal(crashes.length,1,'the tracked backend exiting is a crash');
 supervisor.childProcess=null;assert.deepEqual(signalledAnywhere,[]);});  // nothing is left for the exit hook to stop
test('a backend that does not stop is forced to stop alone, through its own handle',async()=>{for(const platform of ['win32','linux']){const commands=[],proc=backend({exitsOnStdinClose:false});
  await stop(platform,proc,commands);assert.deepEqual(commands,[],`${platform}: no taskkill of the process tree`);
  assert.deepEqual(proc.events,platform==='win32'?['stdin closed','SIGKILL']:['stdin closed','SIGTERM','SIGKILL']);}});
