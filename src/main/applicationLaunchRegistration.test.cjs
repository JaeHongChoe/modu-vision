// Controlled private-channel unit fixtures only: no native/process-tree or lease release authority.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),Module=require('node:module'),vm=require('node:vm'),ts=require('typescript'),crypto=require('node:crypto');
const {Duplex}=require('node:stream'),{EventEmitter}=require('node:events');
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const canonical=value=>value===null||typeof value!=='object'?JSON.stringify(value):Array.isArray(value)?'['+value.map(canonical).join(',')+']':'{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});void promise.catch(()=>{});return{promise,resolve,reject};}
function load(clock){
 const file=path.join(__dirname,'applicationLaunch.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);
 const compiled=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
 const wrapped=new vm.Script(Module.wrap(compiled),{filename:file}).runInNewContext({Buffer,TextDecoder,console,process,setTimeout,clearTimeout,performance:{now:()=>clock.value}});
 wrapped.call(m.exports,m.exports,m.require.bind(m),m,file,path.dirname(file));return m.exports;
}
function fixture(t,mode='held'){
 const root=fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()),'owned-registration-unit-')),nonce='a'.repeat(32),writerId='b'.repeat(32),clock={value:100000};
 const main={pid:process.pid,created_at:1000,command_sha256:'c'.repeat(64)},controller={pid:process.ppid,created_at:1000,command_sha256:'d'.repeat(64)},backendProcess={pid:4242,created_at:1000,command_sha256:'e'.repeat(64)};
 const binding={installation_id:'f'.repeat(32),update_id:'1'.repeat(32),application_generation:'1'.repeat(32),database_pointer:{generation_id:'2'.repeat(32),fence:2}};
 const epochBinding={installation_id:binding.installation_id,nonce,update_id:binding.update_id,application_generation:binding.application_generation,database_generation_id:binding.database_pointer.generation_id,database_fence:2,launch_binding_sha256:hash(canonical(binding)),controller};
 const lockIdentity={device:1,inode:2},registrationHash=hash(canonical({binding_sha256:hash(canonical(epochBinding)),writer_id:writerId,role:'backend',lock_identity:lockIdentity}));
 const registry={schema_version:1,binding:epochBinding,revision:2,state:'open',authority_lock_identity:{device:1,inode:3},writers:[{writer_id:writerId,role:'backend',status:'reserved',lock_identity:lockIdentity,registration_sha256:registrationHash,process:null,exit_code:null,reason_code:null}]};
 const epochDir=path.join(root,'.application-writer-epochs',nonce),journalDir=path.join(root,'.application-launches',nonce);fs.mkdirSync(epochDir,{recursive:true});fs.mkdirSync(journalDir,{recursive:true});
 fs.writeFileSync(path.join(journalDir,'journal.json'),JSON.stringify({state:'starting',claimed:true,supervisor:controller,process:main}));
 function publish(){const bytes=Buffer.from(JSON.stringify(registry));fs.writeFileSync(path.join(epochDir,'registry.json'),bytes);fs.writeFileSync(path.join(epochDir,'publication.json'),JSON.stringify({schema_version:1,nonce,revision:registry.revision,registry_sha256:hash(bytes)}));return hash(bytes);}
 const writer={writer_id:writerId,registration_sha256:registrationHash,registration_registry_sha256:publish()},proofSent=deferred(),pending=[],sent=[],reads=[];
 let claim,ackRaw=null,queued=null,closed=false,confirmationRaw=null;const cpuForwarded=deferred(),backendRequests=[];
 const processHandle=new EventEmitter();Object.assign(processHandle,{pid:backendProcess.pid,exitCode:null,signalCode:null});
 const backendChannel=new Duplex({read(){},write(raw,encoding,done){
  const challenge=JSON.parse(raw.toString());if(challenge.kind==='cpu_execution_request'){backendRequests.push(challenge);done();cpuForwarded.resolve(challenge);return;}assert.equal(challenge.kind,'backend_challenge');
  claim={schema_version:1,kind:'backend_claim',challenge:challenge.challenge,epoch:challenge.epoch,nonce,binding_sha256:hash(canonical(binding)),process:backendProcess,executable:challenge.backend_executable,executable_sha256:'3'.repeat(64),build_identity_sha256:null,frozen:false};
  const claimed=JSON.stringify(claim).replace(/"created_at":1000/g,'"created_at":1000.0'),ready=claimed.replace('"kind":"backend_claim"','"kind":"backend_ready"');
  done();queueMicrotask(()=>this.push(Buffer.from(claimed+'\n'+ready+'\n')));
 }});processHandle.stdio=[null,null,null,backendChannel];
 const controllerChannel=new EventEmitter();controllerChannel.destroy=()=>{if(closed)return;closed=true;for(const item of pending.splice(0))item.reject(Error('Controlled original controller descriptor closed'));};
 function release(){
  registry.revision=3;registry.writers[0].status='active';registry.writers[0].process=backendProcess;const registryHash=publish();
  const proof=sent.find(row=>row.value.kind==='backend_proof');assert(proof,'original backend proof must precede registration');
  const parsed=load(clock).parsePrivateDocument(Buffer.from(proof.value.claim_b64,'base64'));
  const ack={schema_version:1,kind:'backend_registered',nonce,epoch:claim.epoch,binding_sha256:hash(canonical(binding)),backend_claim_sha256:hash(parsed.canonical),main_process:main,backend_process:backendProcess,writer_id:writerId,registration_sha256:registrationHash,active_registry_sha256:registryHash};
  if(mode==='nonce')ack.nonce='9'.repeat(32);
  if(mode==='epoch')ack.epoch='9'.repeat(32);
  if(mode==='writer')ack.writer_id='9'.repeat(32);
  if(mode==='backend')ack.backend_process={...backendProcess,pid:backendProcess.pid+1};
  if(mode==='hash')ack.active_registry_sha256='9'.repeat(64);
  if(mode==='late')clock.value=proof.deadline;
  ackRaw=Buffer.from(JSON.stringify(ack).replace(/"created_at":1000/g,'"created_at":1000.0'));
  if(pending.length)pending.shift().resolve(ackRaw);
 }
 const frames={channel:controllerChannel,assertEmpty(){if(queued)throw Error('Unexpected private frame replay');},async send(value,deadline){
  sent.push({value,deadline});
  if(value.kind==='backend_proof'){clock.value+=137;proofSent.resolve();if(mode!=='held'&&mode!=='missing')release();}
 },async sendCanonical(raw,deadline){
  const value=JSON.parse(raw);sent.push({value,deadline});confirmationRaw=raw;assert.equal(value.kind,'backend_registration_admitted');
  if(mode==='held'){queued=Buffer.from(JSON.stringify({schema_version:1,kind:'cpu_execution_request',challenge:'5'.repeat(64),request_id:'6'.repeat(32),nonce,epoch:claim.epoch,binding_sha256:hash(canonical(binding)),backend_claim_sha256:value.backend_claim_sha256,workspace_id:'7'.repeat(32),project_id:'8'.repeat(32),plan_sha256:'9'.repeat(64)}));}
 },read(budget){
  reads.push({budget,now:clock.value});if(closed)return Promise.reject(Error('Controlled original controller descriptor closed'));
  if(mode==='missing'&&reads.length===1)return Promise.reject(Error('Private descriptor timeout'));
  if(ackRaw&&reads.length===1)return Promise.resolve(ackRaw);
  if(queued){const raw=queued;queued=null;return Promise.resolve(raw);}
  const item=deferred();pending.push(item);return item.promise;
 }};
 const {OwnedApplicationLaunch}=load(clock),owner=new OwnedApplicationLaunch(root,nonce,binding,main,'4'.repeat(64),frames,writer);
 // Only original process/manifest lookup is a controlled unit boundary. The
 // real bindBackend frame validation, durable registry check and ACK wait run.
 owner.bindingCurrent=()=>{};owner.executable=()=>({sha256:'3'.repeat(64),build:null});
 t.after(()=>{owner.refuse();backendChannel.destroy();fs.rmSync(root,{recursive:true,force:true});});
 return{root,owner,processHandle,proofSent:proofSent.promise,cpuForwarded:cpuForwarded.promise,backendRequests,release,sent,reads,registry,clock,get confirmationRaw(){return confirmationRaw;}};
}
test('owned backend readiness waits for durable registration and consumption acknowledgement',{timeout:5000},async t=>{
 const f=fixture(t);let healthy=false;const bound=f.owner.bindBackend(f.processHandle,path.join(f.root,'controlled-backend'),null).then(()=>{healthy=true;});void bound.catch(()=>{});
 await f.proofSent;await tick();assert.equal(healthy,false,'readiness preceded durable backend registration');assert.equal(f.registry.writers[0].status,'reserved');assert.equal(f.registry.writers[0].process,null);
 assert.equal(f.sent.filter(row=>row.value.kind==='backend_registration_admitted').length,0);assert.equal(f.reads[0].budget,210000-137);
 f.release();await bound;assert.equal(healthy,true);assert.equal(f.registry.writers[0].status,'active');
 await f.cpuForwarded;assert.equal(f.backendRequests.length,1,'queued original controller CPU frame was not forwarded exactly once');assert.equal(f.backendRequests[0].request_id,'6'.repeat(32));
 const admitted=f.sent.find(row=>row.value.kind==='backend_registration_admitted'),proof=f.sent.find(row=>row.value.kind==='backend_proof');
 assert(admitted,'readiness lacks original registration consumption confirmation');assert.equal(admitted.deadline,proof.deadline,'registration confirmation renewed the proof deadline');
 assert.match(f.confirmationRaw,/"created_at":1000\.0/,'registration confirmation reminted original Python numeric tokens');assert.equal(admitted.value.writer_id,'b'.repeat(32));
 assert.equal(f.registry.state,'open','registration acknowledgement must not retire the epoch');assert.equal(fs.existsSync(path.join(f.root,'application-launch-lease.json')),false,'unit fixture cannot grant launch-lease release');
});
test('owned backend registration rejects foreign, changed, missing and late acknowledgements',{timeout:5000},async t=>{
 for(const mode of ['nonce','epoch','writer','backend','hash','missing','late']){
  const f=fixture(t,mode);let healthy=false;const bound=f.owner.bindBackend(f.processHandle,path.join(f.root,'controlled-backend'),null).then(()=>{healthy=true;});void bound.catch(()=>{});
  await assert.rejects(bound,/registration|Private descriptor timeout/i,'invalid backend registration acknowledgement must reject before readiness');
  assert.equal(healthy,false);assert.equal(f.sent.filter(row=>row.value.kind==='backend_registration_admitted').length,0,'refused registration cannot send consumption confirmation');
  if(mode==='late')assert.equal(f.reads.length,0,'expired proof phase must refuse before another read');
  else assert.equal(f.reads[0].budget,210000-137,'ACK read renewed the original proof phase');
 }
});
