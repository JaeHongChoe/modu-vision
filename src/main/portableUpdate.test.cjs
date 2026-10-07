const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),Module=require('node:module'),ts=require('typescript');
function load(name='portableUpdate.ts'){const file=path.join(__dirname,name),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const requireOriginal=m.require.bind(m);m.require=k=>['./releaseTrust','./persistentLaunch'].includes(k)?load(k.slice(2)+'.ts'):requireOriginal(k);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;}
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
function setup(t,extra={}){const directory=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'portable-update-main-')));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));const resources=path.join(directory,'resources'),root=path.join(directory,'owned'),user=path.join(directory,'current-user');fs.mkdirSync(resources);fs.mkdirSync(root);fs.mkdirSync(user);fs.mkdirSync(path.join(resources,'backend_bin'));const binary=Buffer.from('controlled runtime file');fs.writeFileSync(path.join(resources,'backend_bin/vision_ai_backend'),binary);fs.writeFileSync(path.join(resources,'backend_bin/backend-release.json'),JSON.stringify({executable:'vision_ai_backend',executable_sha256:hash(binary),inventory:{build_identity_sha256:'a'.repeat(64),platform:'Darwin',architecture:'arm64'}}));const pair=crypto.generateKeyPairSync('ed25519');fs.writeFileSync(path.join(resources,'release-trust.json'),JSON.stringify({schema_version:1,publisher:'controlled-publisher',keys:{fixture:pair.publicKey.export({type:'spki',format:'der'}).toString('base64')},revoked_key_ids:[],allowed_origins:['https://release.example.test'],compatibility:{api_context:1,worker:1,runtime:1,dataset_index:1}}));const calls=[];let mutate;
const status={status:'ready',installation_id:'b'.repeat(32),version:'0.0.0',update_id:null,database_fence:0,allowed_recovery:[],application_started:false};
const review={status:'reviewed',installation_id:status.installation_id,plan_sha256:'c'.repeat(64),source_sha256:'d'.repeat(64),envelope_sha256:'e'.repeat(64),authority_sha256:hash(fs.readFileSync(path.join(resources,'release-trust.json'))),version:'1.0.0',current_version:'0.0.0',publisher:'controlled-publisher',channel:'stable',application_file_count:1,application_layout:"portable/v1",application_link_count:0,pack_count:0,artifact_bytes:100,database_fence:0,copied_session_policy:'revoked',application_started:false};
review.preactivation_canary=canaryProof();
const {PortableUpdateManager}=load(),m=new PortableUpdateManager({packaged:true,platform:'darwin',arch:'arm64',resourcesPath:resources,userDataPath:user,appPath:path.join(directory,'Current.app'),signature:async()=>({status:'verified',publisher:'controlled-publisher'}),runner:async(file,args)=>{calls.push({file,args});if(mutate)await mutate(file,args);const cmd=args[1];return {stdout:JSON.stringify(cmd==='inspect'?status:cmd==='preview'?review:{status:'committed',update_id:'f'.repeat(32),version:'1.0.0',database_pointer:{fence:1}}),stderr:''};},...extra});return {m,directory,resources,root,user,calls,status,review,setMutate:f=>mutate=f};}
test('selection inspects an owned external installation; preparation is read-only',async t=>{const f=setup(t);await f.m.select(f.root);const preview=await f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins);assert.ok(preview.review_id);assert.equal(preview.version,'1.0.0');assert.deepEqual(f.calls.map(c=>c.args[1]),['inspect','preview']);assert.equal(f.calls[1].args.includes('--use-owned-version'),true);assert.equal(f.calls[1].file,path.join(f.resources,'backend_bin/vision_ai_backend'));assert.equal(fs.readdirSync(f.root).length,0);});
test('a different or replayed UI review cannot install',async t=>{const f=setup(t);await f.m.select(f.root);const preview=await f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins);await assert.rejects(()=>f.m.apply('wrong'),/review/i);assert.equal(f.calls.some(c=>c.args[1]==='install'),false);await f.m.apply(preview.review_id);const install=f.calls.find(c=>c.args[1]==='install');assert.equal(install.args[install.args.indexOf('--expected-plan-sha256')+1],f.review.plan_sha256);await assert.rejects(()=>f.m.apply(preview.review_id),/review/i);assert.equal(f.calls.filter(c=>c.args[1]==='install').length,1);});
test('failed or response-lost install requires readback instead of automatic retry',async t=>{const f=setup(t);await f.m.select(f.root);const p=await f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins);f.setMutate((_file,args)=>{if(args[1]==='install')throw Error('controlled response loss');});await assert.rejects(()=>f.m.apply(p.review_id),/response loss/);await assert.rejects(()=>f.m.apply(p.review_id),/review/i);f.setMutate(null);await f.m.inspect();assert.equal(f.calls.filter(c=>c.args[1]==='install').length,1);});
for(const scope of ['current','parent','child','linked'])test(`selection refuses ${scope} current-home overlap or link`,async t=>{const f=setup(t);let root=f.user;if(scope==='parent')root=f.directory;if(scope==='child'){root=path.join(f.user,'nested');fs.mkdirSync(root);}if(scope==='linked'){root=path.join(f.directory,'alias');fs.symlinkSync(f.root,root);}await assert.rejects(()=>f.m.select(root),/current|link/i);assert.equal(f.calls.length,0);});
test('selected paths and review cannot change during a pending command',async t=>{const f=setup(t);await f.m.select(f.root);let release;f.setMutate((_file,args)=>args[1]==='preview'?new Promise(r=>release=r):undefined);const pending=f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins);while(!release)await new Promise(r=>setImmediate(r));await assert.rejects(()=>f.m.select(f.root),/progress/i);await assert.rejects(()=>f.m.inspect(),/progress/i);release();await pending;});
test('runtime bytes or pinned authority changing after native checks refuse execution',async t=>{const f=setup(t);await f.m.select(f.root);let once=true;f.m.options.signature=async()=>{if(once){once=false;fs.appendFileSync(path.join(f.resources,'backend_bin/vision_ai_backend'),'changed');}return {status:'verified',publisher:'controlled-publisher'};};await assert.rejects(()=>f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins),/runtime|checksum|changed/i);assert.equal(f.calls.length,1);});
test('unsigned publisher and development mode cannot install or inspect',async t=>{for(const extra of [{packaged:false},{signature:async()=>({status:'unsigned'})},{platform:'win32'}]){const f=setup(t,extra);await assert.rejects(()=>f.m.select(f.root),/packaged|publisher|POSIX/i);assert.equal(f.calls.length,0);}});
test('recovery only uses the selected current intent and allowed action',async t=>{const f=setup(t);Object.assign(f.status,{status:'recovery_required',update_id:'f'.repeat(32),version:'1.0.0',allowed_recovery:['finish']});await f.m.select(f.root);await assert.rejects(()=>f.m.recover('abort',{installation_id:f.status.installation_id,update_id:f.status.update_id}),/recovery/i);await f.m.recover('finish',{installation_id:f.status.installation_id,update_id:f.status.update_id});const cmd=f.calls.find(c=>c.args[1]==='recover');assert.equal(cmd.args[cmd.args.indexOf('--intent')+1],f.status.update_id);assert.ok(cmd.args.includes('--pinned-authority-sha256'));});

test('a stale window recovery view cannot mutate a newly selected installation',async t=>{const f=setup(t);Object.assign(f.status,{status:'committed',update_id:'f'.repeat(32),allowed_recovery:['finish','forward']});await f.m.select(f.root);await assert.rejects(()=>f.m.recover('forward',{installation_id:'1'.repeat(32),update_id:f.status.update_id}),/installation/);await assert.rejects(()=>f.m.recover('forward',{installation_id:f.status.installation_id,update_id:'2'.repeat(32)}),/intent/);assert.equal(f.calls.some(c=>c.args[1]==='recover'),false);});

for(const change of ['missing_layout','invalid_layout','negative_links','portable_links','fractional_links'])test(`native review refuses ${change} before issuing a UI review token`,async t=>{const f=setup(t);await f.m.select(f.root);if(change==='missing_layout')delete f.review.application_layout;else if(change==='invalid_layout')f.review.application_layout='foreign/v9';else if(change==='negative_links')f.review.application_link_count=-1;else if(change==='portable_links')f.review.application_link_count=3;else f.review.application_link_count=1.5;await assert.rejects(()=>f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins),/review/);assert.equal(f.calls.some(c=>c.args[1]==='install'),false);});
test('native macOS review exposes the verified layout and link count without launching',async t=>{const f=setup(t);Object.assign(f.review,{application_layout:'darwin-app/v2',application_link_count:3});await f.m.select(f.root);const r=await f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins);assert.equal(r.application_layout,'darwin-app/v2');assert.equal(r.application_link_count,3);assert.equal(r.application_started,false);assert.equal(f.calls.some(c=>c.args[1]==='install'),false);});

function launchFixture(t){
 const launches=[],f=setup(t,{launchRunner:async(file,args)=>{launches.push({file,args});return{stdout:JSON.stringify(result)+'\n',stderr:''};}});
 const receiptPath=path.join(f.resources,'backend_bin/backend-release.json'),receipt=JSON.parse(fs.readFileSync(receiptPath,'utf8'));
 Object.assign(receipt.inventory,{owned_application_launch_controller_protocol:1,resources:['scripts/frozen_backend_entry.py','backend/engine/application_launch_controller.py','backend/engine/application_launch_handshake.py','backend/engine/application_launch_lease.py'].map(p=>({path:p,sha256:hash(p)}))});
 fs.writeFileSync(receiptPath,JSON.stringify(receipt));
 Object.assign(f.status,{status:'committed',update_id:'f'.repeat(32),database_fence:1});
 const expected={installation_id:f.status.installation_id,update_id:f.status.update_id,database_fence:1};
 let result={schema_version:1,status:'starting',nonce:'1'.repeat(32),...expected,bootstrap_binding_verified:false,readiness:'unverified',native_app_handshake_verified:false,backend_handshake_verified:false,actual_application_inference_verified:false,release_ready:false};
 const ordinary=f.m.options.runner;f.m.options.runner=async(file,args)=>args[0]==='--owned-application-launch-controller'?{stdout:JSON.stringify(result)+'\n',stderr:''}:ordinary(file,args);
 return {...f,expected,launches,result,setResult:value=>result=value};
}
test('launch is bound to the exact selected committed pair and fixed persistent controller arguments',async t=>{
 const f=launchFixture(t);await f.m.select(f.root);const result=await f.m.launch(f.expected);
 assert.equal(result.status,'starting');assert.equal(result.bootstrap_binding_verified,false);assert.equal(result.release_ready,false);
 assert.equal(f.launches.length,1);const {file,args}=f.launches[0];assert.equal(file,path.join(f.resources,'backend_bin/vision_ai_backend'));
 assert.equal(args[0],'--owned-application-launch-controller');
 for(const [flag,value] of [['--root',f.root],['--expected-installation-id',f.expected.installation_id],['--expected-update-id',f.expected.update_id],['--expected-database-fence','1']])assert.equal(args[args.indexOf(flag)+1],value);
 assert.equal(args.includes('--inspect'),false);assert.ok(args.includes('--pinned-authority-sha256'));
 await assert.rejects(()=>f.m.launch(f.expected),/already|read.*state/i);assert.equal(f.launches.length,1);
});
test('stale, malformed or uncommitted launch requests refuse before persistent spawn',async t=>{
 for(const change of ['installation','update','fence','bool-fence','extra-field','uncommitted','recovery']){
  const f=launchFixture(t);await f.m.select(f.root);const expected={...f.expected};
  if(change==='installation')expected.installation_id='2'.repeat(32);if(change==='update')expected.update_id='3'.repeat(32);if(change==='fence')expected.database_fence=2;
  if(change==='bool-fence')expected.database_fence=true;if(change==='extra-field')expected.executable='/untrusted';if(change==='uncommitted')f.status.status='ready';if(change==='recovery')f.status.status='recovery_required';
  await assert.rejects(()=>f.m.launch(expected));assert.equal(f.launches.length,0,change);
 }
});
test('a lost launch response is not retried and separate inspection never spawns',async t=>{
 const f=launchFixture(t);await f.m.select(f.root);let requests=0;f.m.options.launchRunner=async()=>{requests++;throw Error('controlled lost response');};
 await assert.rejects(()=>f.m.launch(f.expected),/lost response/);await assert.rejects(()=>f.m.launch(f.expected),/already|read.*state/i);assert.equal(requests,1);
 f.setResult({...f.result,status:'recovery_required',reason:'original child state is uncertain'});
 const readback=await f.m.inspectLaunch(f.expected);assert.equal(readback.status,'recovery_required');assert.equal(requests,1);
});
test('launch state rejects wrong bindings, typed corruption, false ready and invented acceptance',async t=>{
 for(const mutate of [r=>r.installation_id='2'.repeat(32),r=>r.database_fence=true,r=>r.nonce=null,r=>r.schema_version=true,r=>r.status='ready',r=>r.bootstrap_binding_verified='yes',r=>r.readiness='native_accepted',r=>r.native_app_handshake_verified=true,r=>r.backend_handshake_verified=true,r=>r.actual_application_inference_verified=true,r=>r.release_ready=true,r=>r.token='sensitive']){
  const f=launchFixture(t);await f.m.select(f.root);mutate(f.result);await assert.rejects(()=>f.m.launch(f.expected),/launch|binding|response/i);
 }
});
test('authenticated bootstrap readback remains separate from native, inference and release acceptance',async t=>{
 const f=launchFixture(t);await f.m.select(f.root);f.setResult({...f.result,status:'ready',bootstrap_binding_verified:true,readiness:'authenticated_controller_binding_only'});
 const readback=await f.m.inspectLaunch(f.expected);assert.equal(readback.status,'ready');assert.equal(readback.bootstrap_binding_verified,true);assert.equal(readback.native_app_handshake_verified,false);assert.equal(readback.actual_application_inference_verified,false);assert.equal(readback.release_ready,false);assert.equal(f.launches.length,0);
});
test('reordered IPC object fields cannot replay the same already requested committed pair',async t=>{
 const f=launchFixture(t);await f.m.select(f.root);await f.m.launch(f.expected);
 await assert.rejects(()=>f.m.launch({database_fence:1,update_id:f.expected.update_id,installation_id:f.expected.installation_id}),/already|read.*state/i);
 assert.equal(f.launches.length,1);
});
test('inspection refuses false readiness, contradictory absent state and extra capability fields',async t=>{
 for(const change of ['ready-without-bootstrap','absent-with-bootstrap','unknown-secret','invalid-nonce']){
  const f=launchFixture(t);await f.m.select(f.root);let row={...f.result};
  if(change==='ready-without-bootstrap')row.status='ready';
  if(change==='absent-with-bootstrap')Object.assign(row,{status:'absent',nonce:null,bootstrap_binding_verified:true,readiness:'authenticated_controller_binding_only'});
  if(change==='unknown-secret')row.capability='must-not-forward';if(change==='invalid-nonce')row.nonce='bad';f.setResult(row);
  await assert.rejects(()=>f.m.inspectLaunch(f.expected),/launch|binding|response/i);assert.equal(f.launches.length,0);
 }
});
test('pre-reservation CLI refusal remains a bounded error without inventing a lifecycle nonce',async t=>{
 const f=launchFixture(t);await f.m.select(f.root);const ordinary=f.m.options.runner;
 f.m.options.runner=async(file,args)=>{if(args[0]!=='--owned-application-launch-controller')return ordinary(file,args);const error=Error('controlled CLI exit2');error.stdout=JSON.stringify({schema_version:1,status:'refused',error:'Selected application binding changed'});throw error;};
 await assert.rejects(()=>f.m.inspectLaunch(f.expected),/Selected application binding changed/);assert.equal(f.launches.length,0);
});
test('legacy or incomplete frozen controller inventory refuses before either launch or inspection execution',async t=>{
 for(const change of ['missing-protocol','old-protocol','bool-protocol','missing-dispatch','missing-handshake','duplicate-resource','invalid-resource-hash']){
  for(const inspect of [false,true]){
   const f=launchFixture(t),receiptPath=path.join(f.resources,'backend_bin/backend-release.json'),receipt=JSON.parse(fs.readFileSync(receiptPath,'utf8'));
   if(change==='missing-protocol')delete receipt.inventory.owned_application_launch_controller_protocol;
   if(change==='old-protocol')receipt.inventory.owned_application_launch_controller_protocol=0;
   if(change==='bool-protocol')receipt.inventory.owned_application_launch_controller_protocol=true;
   if(change==='missing-dispatch')receipt.inventory.resources=receipt.inventory.resources.filter(r=>r.path!=='scripts/frozen_backend_entry.py');
   if(change==='missing-handshake')receipt.inventory.resources=receipt.inventory.resources.filter(r=>r.path!=='backend/engine/application_launch_handshake.py');
   if(change==='duplicate-resource')receipt.inventory.resources.push({...receipt.inventory.resources[0]});
   if(change==='invalid-resource-hash')receipt.inventory.resources[0].sha256='bad';
   fs.writeFileSync(receiptPath,JSON.stringify(receipt));await f.m.select(f.root);
   const ordinary=f.m.options.runner;let controllerInspections=0;f.m.options.runner=async(file,args)=>{if(args[0]==='--owned-application-launch-controller')controllerInspections++;return ordinary(file,args);};
   await assert.rejects(()=>inspect?f.m.inspectLaunch(f.expected):f.m.launch(f.expected),/controller.*inventory|inventory.*controller/i,change);
   assert.equal(f.launches.length,0,change);assert.equal(controllerInspections,0,change);
  }
 }
});
test('runtime inventory changing during publisher verification refuses before execution',async t=>{
 const f=launchFixture(t);await f.m.select(f.root);const receiptPath=path.join(f.resources,'backend_bin/backend-release.json');let once=true;
 f.m.options.signature=async()=>{if(once){once=false;fs.appendFileSync(receiptPath,' ');}return {status:'verified',publisher:'controlled-publisher'};};
 await assert.rejects(()=>f.m.inspectLaunch(f.expected),/inventory|changed/i);assert.equal(f.launches.length,0);
});
test('reselection returns durable live launch state without spawning and never hides refused inspection',async t=>{
 const f=launchFixture(t);f.setResult({...f.result,status:'ready',bootstrap_binding_verified:true,readiness:'authenticated_controller_binding_only'});
 const selected=await f.m.select(f.root);assert.equal(selected.launch_state.status,'ready');assert.equal(f.launches.length,0);
 f.setResult({...f.result,status:'ready',bootstrap_binding_verified:false});await assert.rejects(()=>f.m.select(f.root),/binding|response/i);assert.equal(f.launches.length,0);
});

test('canary pins are required before any preview subprocess or publisher query',async t=>{
 for(const pins of [undefined,null,{}, {workspace_id:'1'.repeat(32),project_id:'2'.repeat(32)}, {workspace_id:'1'.repeat(32),project_id:'2'.repeat(32),plan_sha256:'3'.repeat(64),command:'foreign'}, {workspace_id:true,project_id:'2'.repeat(32),plan_sha256:'3'.repeat(64)}]){
  const f=setup(t);await f.m.select(f.root);let queries=0;f.m.options.signature=async()=>{queries++;return{status:'verified',publisher:'controlled-publisher'};};
  await assert.rejects(()=>f.m.preview(path.join(f.directory,'signed.json'),'stable',pins),/기준 이미지|canary|pins/i);assert.equal(f.calls.length,1);assert.equal(queries,0);
 }
});
test('canary review refuses missing or changed independent proof binding',async t=>{
 const pins={workspace_id:'1'.repeat(32),project_id:'2'.repeat(32),plan_sha256:'3'.repeat(64)};
 for(const damage of ['missing','false-required','foreign-workspace','foreign-plan','extra-capability','bad-capability']){
  const f=setup(t);Object.assign(f.review,{preactivation_canary:canaryProof(pins)});
  if(damage==='missing')delete f.review.preactivation_canary;if(damage==='false-required')f.review.preactivation_canary.required=false;if(damage==='foreign-workspace')f.review.preactivation_canary.pins.workspace_id='5'.repeat(32);if(damage==='foreign-plan')f.review.preactivation_canary.pins.plan_sha256='6'.repeat(64);if(damage==='extra-capability')f.review.preactivation_canary.pins.command='foreign';if(damage==='bad-capability')f.review.preactivation_canary.capability_sha256='bad';
  await f.m.select(f.root);await assert.rejects(()=>f.m.preview(path.join(f.directory,'signed.json'),'stable',pins),/canary|기준 이미지|review/i);assert(!f.calls.some(c=>c.args[1]==='install'));
 }
});

const canaryPins={workspace_id:"1".repeat(32),project_id:"2".repeat(32),plan_sha256:"3".repeat(64)};
function canaryProof(pins=canaryPins){return {schema_version:1,protocol:1,required:true,policy:"same_reviewed_source_runtime_worker_v1",status:"source_ready",supported:true,pins:{...pins},capability_sha256:"4".repeat(64),candidate_runtime_source_sha256:"5".repeat(64),reason:null,candidate_main_launch_verified:false,native_application_verified:false,frozen_backend_verified:false,owned_backend_execution_origin_verified:false,worker_process_tree_exit_verified:false,model_quality_verified:false,release_ready:false};}

test('canary pins are snapshotted for review and identical fixed flags reach one consumed install',async t=>{
 const f=setup(t),pins={...canaryPins};await f.m.select(f.root);
 f.setMutate((_file,args)=>{if(args[1]==='preview')pins.project_id='9'.repeat(32);});
 const r=await f.m.preview(path.join(f.directory,'signed.json'),'stable',pins);assert(r.installable);assert.deepEqual(r.preactivation_canary.pins,canaryPins);
 await f.m.apply(r.review_id);const commands=f.calls.filter(c=>['preview','install'].includes(c.args[1]));assert.equal(commands.length,2);
 for(const c of commands)for(const [flag,value] of [['--canary-workspace-id',canaryPins.workspace_id],['--canary-project-id',canaryPins.project_id],['--canary-plan-sha256',canaryPins.plan_sha256]])assert.equal(c.args[c.args.indexOf(flag)+1],value);
 await assert.rejects(()=>f.m.apply(r.review_id),/review/i);assert.equal(f.calls.filter(c=>c.args[1]==='install').length,1);
});
test('canary unsupported target remains a readonly review and never submits install',async t=>{
 const f=setup(t);Object.assign(f.review.preactivation_canary,{status:'requires_target',supported:false,reason:'The target has no reviewed fixed worker.'});await f.m.select(f.root);
 const r=await f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins);assert.equal(r.installable,false);assert.equal(r.preactivation_canary.native_application_verified,false);
 await assert.rejects(()=>f.m.apply(r.review_id),/target.*worker/i);assert(!f.calls.some(c=>c.args[1]==='install'));
});
test('canary invented target quality tree or release acceptance refuses before review authority',async t=>{
 for(const key of ['candidate_main_launch_verified','native_application_verified','frozen_backend_verified','owned_backend_execution_origin_verified','worker_process_tree_exit_verified','model_quality_verified','release_ready']){const f=setup(t);f.review.preactivation_canary[key]=true;await f.m.select(f.root);await assert.rejects(()=>f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins),/canary/i);assert(!f.calls.some(c=>c.args[1]==='install'));}
});

function frozenCanaryProof(){return {...canaryProof(),protocol:2,policy:'same_reviewed_frozen_runtime_worker_v1',status:'frozen_ready',worker_binding:{protocol:1,executable_path:'Owned CPU.app/Contents/Resources/backend_bin/vision_ai_backend/vision_ai_backend',executable_sha256:'6'.repeat(64),build_receipt_path:'Owned CPU.app/Contents/Resources/backend_bin/vision_ai_backend/backend-release.json',build_receipt_sha256:'7'.repeat(64),build_identity_sha256:'8'.repeat(64),runtime_source_sha256:'5'.repeat(64),resource_inventory_sha256:'9'.repeat(64)}};}
test('compiled canary review snapshots derived binding and consumes only the existing pin and plan arguments',async t=>{
 const f=setup(t);f.review.preactivation_canary=frozenCanaryProof();await f.m.select(f.root);
 const r=await f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins);
 assert.equal(r.installable,true);assert.equal(r.preactivation_canary.protocol,2);assert.equal(r.preactivation_canary.worker_binding.runtime_source_sha256,'5'.repeat(64));
 r.preactivation_canary.worker_binding.executable_path='/foreign/authority';r.preactivation_canary.pins.plan_sha256='0'.repeat(64);
 assert.equal(f.review.preactivation_canary.worker_binding.executable_path,frozenCanaryProof().worker_binding.executable_path);
 await f.m.apply(r.review_id);const commands=f.calls.filter(c=>['preview','install'].includes(c.args[1]));assert.equal(commands.length,2);
 for(const c of commands){for(const [flag,value] of [['--canary-workspace-id',canaryPins.workspace_id],['--canary-project-id',canaryPins.project_id],['--canary-plan-sha256',canaryPins.plan_sha256]])assert.equal(c.args[c.args.indexOf(flag)+1],value);assert(!c.args.some(a=>a.includes('Owned CPU.app')||a.includes('worker-binding')||a==='/foreign/authority'));}
 await assert.rejects(()=>f.m.apply(r.review_id),/review/i);assert.equal(f.calls.filter(c=>c.args[1]==='install').length,1);
});
test('compiled unsupported review cannot give installation authority',async t=>{
 const f=setup(t);f.review.preactivation_canary={...frozenCanaryProof(),status:'requires_target',supported:false,worker_binding:null,reason:'Candidate needs a reviewed compiled worker.'};await f.m.select(f.root);
 const r=await f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins);assert.equal(r.installable,false);await assert.rejects(()=>f.m.apply(r.review_id),/compiled worker/i);assert(!f.calls.some(c=>c.args[1]==='install'));
});
for(const damage of ['unknown-policy','unknown-protocol','missing-binding','extra-binding','foreign-runtime','bad-hash','absolute-path','parent-path','backslash-path','control-path','unsupported-binding','missing-pins','claimed-acceptance'])test(`compiled canary refuses ${damage} without granting review`,async t=>{
 const f=setup(t),proof=frozenCanaryProof();f.review.preactivation_canary=proof;
 if(damage==='unknown-policy')proof.policy='foreign';if(damage==='unknown-protocol')proof.protocol=3;if(damage==='missing-binding')delete proof.worker_binding;if(damage==='extra-binding')proof.worker_binding.command='foreign';if(damage==='foreign-runtime')proof.worker_binding.runtime_source_sha256='a'.repeat(64);if(damage==='bad-hash')proof.worker_binding.build_identity_sha256='bad';if(damage==='absolute-path')proof.worker_binding.executable_path='/foreign';if(damage==='parent-path')proof.worker_binding.build_receipt_path='owned/../receipt';if(damage==='backslash-path')proof.worker_binding.executable_path='owned\\foreign';if(damage==='control-path')proof.worker_binding.executable_path='owned/\nforeign';if(damage==='unsupported-binding'){proof.status='requires_target';proof.supported=false;proof.reason='Unavailable.';}if(damage==='missing-pins'){proof.status='missing_pins';proof.supported=false;proof.pins=null;proof.worker_binding=null;proof.reason='Pins needed.';}if(damage==='claimed-acceptance')proof.frozen_backend_verified=true;
 await f.m.select(f.root);await assert.rejects(()=>f.m.preview(path.join(f.directory,'signed.json'),'stable',canaryPins),/canary|기준 이미지|review/i);await assert.rejects(()=>f.m.apply('foreign'),/review/i);assert(!f.calls.some(c=>c.args[1]==='install'));
});
