const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),Module=require('node:module'),ts=require('typescript');
function load(file){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;}
// Missing production behavior is represented by an empty export until the new module exists.
const {SiwcStore}=fs.existsSync(path.join(__dirname,'siwcStore.ts'))?load(path.join(__dirname,'siwcStore.ts')):{};
const fakeEncryptor=(available=true)=>({isAvailable:()=>available,encrypt:plain=>Buffer.from('enc:'+Buffer.from(plain).toString('hex')),decrypt:cipher=>{if(!cipher.toString().startsWith('enc:'))throw new Error('not cipher');return Buffer.from(cipher.toString().slice(4),'hex').toString();}});
const tokens=(clientId='oaiapp_test_a',subject='subject-private-a')=>({issuer:'https://auth.openai.com',subject,clientId,accessToken:'private-access-placeholder',refreshToken:'private-refresh-placeholder',idToken:'private-id-token-placeholder',scopes:['openid','chatgpt.tokens.use.direct'],expiresAt:1900000000000,planUsageEnabled:true});
function fixture(t,available=true){const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'siwc-store-'))),dir=path.join(root,'assistant');const store=new SiwcStore(dir,fakeEncryptor(available));t.after(async ()=>{(await store.close());fs.rmSync(root,{recursive:true,force:true});});return{root,dir,store};}
const isCode=code=>error=>error.code===code;

test('sessions and identity stay encrypted while public accounts contain only opaque metadata',async t=>{
 const {dir,store}=fixture(t);(await store.open());const host=(await store.hostId());assert.match(host,/^urn:uuid:[a-f0-9-]{36}$/);
 const account=(await store.save(tokens(),{label:'검사 계정'}));(await store.setModel(account.recordId,'model-test'));
 assert.deepEqual((await store.state()),{accounts:[{recordId:account.recordId,label:'검사 계정',model:'model-test',signedIn:true,planUsageEnabled:true}],active:account.recordId,encryptionAvailable:true});
 assert.deepEqual((await store.credentials()),tokens());assert.deepEqual((await store.registration(account.recordId)),{issuer:tokens().issuer,subject:tokens().subject,clientId:tokens().clientId});
 const raw=fs.readFileSync(store.file,'utf8');for(const secret of [tokens().accessToken,tokens().refreshToken,tokens().idToken,tokens().subject,tokens().clientId,host,'검사 계정'])assert.ok(!raw.includes(secret));
 for(const secret of [tokens().accessToken,tokens().subject,tokens().clientId])assert.ok(!JSON.stringify((await store.state())).includes(secret));
 if(process.platform!=='win32'){assert.equal(fs.statSync(store.file).mode&0o777,0o600);assert.equal(fs.statSync(dir).mode&0o777,0o700);assert.equal(fs.statSync(store.lockFile).mode&0o777,0o600);}
});

test('exclusive lifetime lease refuses a competing owner and can be reopened after close',async t=>{
 const {dir,store}=fixture(t);(await store.open());const host=(await store.hostId()),a=(await store.save(tokens()));const second=new SiwcStore(dir,fakeEncryptor());
 (await assert.rejects(async ()=>(await second.open()),isCode('store_locked')));(await assert.rejects(async ()=>(await second.credentials()),isCode('store_locked')));
 (await store.close());(await assert.rejects(async ()=>(await store.credentials()),isCode('store_closed')));(await second.open());assert.equal((await second.hostId()),host);assert.equal((await second.credentials(a.recordId)).accessToken,tokens().accessToken);(await second.close());
 assert.deepEqual(fs.readdirSync(dir),['chatgpt-sessions.json']);
});

test('same issued identity reuses one record while distinct clients and accounts remain independent',async t=>{
 const {store}=fixture(t);const a=(await store.save(tokens(),{label:'첫 계정'}));(await store.setModel(a.recordId,'one'));
 const same=(await store.save({...tokens(),accessToken:'renewed-private-access'}));assert.equal(same.recordId,a.recordId);assert.equal(same.label,'첫 계정');assert.equal(same.model,'one');
 const b=(await store.save(tokens('oaiapp_test_b'),{label:'별도 등록',activate:false}));(await store.setModel(b.recordId,'two'));
 assert.notEqual(a.recordId,b.recordId);assert.equal((await store.state()).active,a.recordId);(await store.setActive(b.recordId));assert.equal((await store.credentials()).clientId,'oaiapp_test_b');assert.equal((await store.state()).accounts.find(x=>x.recordId===a.recordId).model,'one');
 (await assert.rejects(async ()=>(await store.save(tokens('oaiapp_test_a','different-subject'),{recordId:a.recordId})),isCode('invalid_record')));
});

test('logout removes credentials but retains registration, host, label and model for returning login',async t=>{
 const {store}=fixture(t);const a=(await store.save(tokens(),{label:'현장'})),host=(await store.hostId());(await store.setModel(a.recordId,'model-test'));(await store.clearCredentials(a.recordId));
 assert.equal((await store.credentials(a.recordId)),null);assert.equal((await store.registration(a.recordId)).clientId,'oaiapp_test_a');assert.equal((await store.hostId()),host);
 assert.deepEqual((await store.state()).accounts,[{recordId:a.recordId,label:'현장',model:'model-test',signedIn:false,planUsageEnabled:false}]);
 const again=(await store.save(tokens(),{recordId:a.recordId}));assert.equal(again.recordId,a.recordId);assert.equal(again.label,'현장');assert.equal(again.model,'model-test');(await store.setActive(null));assert.equal((await store.credentials()),null);
});

test('unavailable encryption exposes only disabled state and cannot create a host or session',async t=>{
 const {dir,store}=fixture(t,false);assert.deepEqual((await store.state()),{accounts:[],active:null,encryptionAvailable:false});(await assert.rejects(async ()=>(await store.open()),isCode('storage_unavailable')));(await assert.rejects(async ()=>(await store.save(tokens())),isCode('storage_unavailable')));assert.ok(!fs.existsSync(dir));
});

test('failed atomic replace preserves previous encrypted state and clears only its temporary file',async t=>{
 const {dir,store}=fixture(t);const a=(await store.save(tokens()));(await store.setModel(a.recordId,'before'));const raw=fs.readFileSync(store.file,'utf8');
 const rename=fs.renameSync;fs.renameSync=()=>{throw new Error('disk full with secret server detail');};
 try{(await assert.rejects(async ()=>(await store.setModel(a.recordId,'after')),error=>error.code==='store_write_failed'&&!error.message.includes('secret')));}finally{fs.renameSync=rename;}
 assert.equal(fs.readFileSync(store.file,'utf8'),raw);assert.equal((await store.state()).accounts[0].model,'before');assert.equal(fs.readdirSync(dir).length,2);
});

test('corrupted ciphertext or decrypted schema is refused and does not get overwritten',async t=>{
 const {dir,store}=fixture(t);(await store.open());const file=store.file;(await store.close());
 for(const value of ['{not json',JSON.stringify({schema_version:1,payload:Buffer.from('bad cipher').toString('base64')}),JSON.stringify({schema_version:1,payload:fakeEncryptor().encrypt(JSON.stringify({schema_version:1,hostId:'bad',active:null,accounts:[]})).toString('base64')})]){
  fs.writeFileSync(file,value,{mode:0o600});const broken=new SiwcStore(dir,fakeEncryptor());(await assert.rejects(async ()=>(await broken.open()),isCode('store_corrupt')));assert.equal(fs.readFileSync(file,'utf8'),value);assert.ok(!fs.existsSync(broken.lockFile));(await broken.close());
 }
});

test('linked targets, parent directories and foreign lease replacements are never followed or removed',{skip:process.platform==='win32'&&'symlinks need Windows privilege'},async t=>{
 const {root,dir,store}=fixture(t);fs.mkdirSync(dir,{mode:0o700});const outside=path.join(root,'outside');fs.writeFileSync(outside,'keep');fs.symlinkSync(outside,store.file);
 (await assert.rejects(async ()=>(await store.open()),isCode('unsafe_path')));assert.equal(fs.readFileSync(outside,'utf8'),'keep');fs.unlinkSync(store.file);(await store.open());
 fs.unlinkSync(store.lockFile);fs.writeFileSync(store.lockFile,'someone-else',{mode:0o600});(await assert.rejects(async ()=>(await store.credentials()),isCode('store_locked')));(await store.close());assert.equal(fs.readFileSync(store.lockFile,'utf8'),'someone-else');
 const parent=path.join(root,'linked');fs.symlinkSync(dir,parent);const linked=new SiwcStore(parent,fakeEncryptor());(await assert.rejects(async ()=>(await linked.open()),isCode('unsafe_path')));(await linked.close());
});

test('strict bounded input cannot smuggle token material into account labels or metadata',async t=>{
 const {store}=fixture(t);for(const value of [{...tokens(),planUsageEnabled:false},{...tokens(),issuer:'https://other.example'},{...tokens(),refreshToken:''},{...tokens(),expiresAt:NaN}])(await assert.rejects(async ()=>(await store.save(value)),isCode('invalid_record')));
 const a=(await store.save(tokens()));for(const value of ['bad\nlabel','x'.repeat(81)])(await assert.rejects(async ()=>(await store.setLabel(a.recordId,value)),isCode('invalid_record')));
 (await assert.rejects(async ()=>(await store.setModel(a.recordId,'model with spaces')),isCode('invalid_record')));(await assert.rejects(async ()=>(await store.setActive(crypto.randomUUID())),isCode('unknown_account')));
 const copy=(await store.credentials());copy.accessToken='tampered';assert.notEqual((await store.credentials()).accessToken,'tampered');
});

test('an independent process cannot own or read the session until the current owner closes',async t=>{
 const {store,dir}=fixture(t);(await store.open());(await store.save(tokens()));
 const source=`const fs=require('node:fs'),Module=require('node:module'),path=require('node:path'),ts=require('typescript');const file=process.argv[1],dir=process.argv[2],m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);const encryptor={isAvailable:()=>true,encrypt:p=>Buffer.from('enc:'+Buffer.from(p).toString('hex')),decrypt:c=>Buffer.from(c.toString().slice(4),'hex').toString()};void(async()=>{let store;try{store=new m.exports.SiwcStore(dir,encryptor);await store.open();process.stdout.write('opened');}catch(e){process.stdout.write(e.code||'unsafe-error');}finally{await store?.close();}})();`;
 const run=()=>require('node:child_process').spawnSync(process.execPath,['-e',source,path.join(__dirname,'siwcStore.ts'),dir],{encoding:'utf8',cwd:path.resolve(__dirname,'../../..'),timeout:10000});
 const blocked=run();assert.equal(blocked.status,0,blocked.stderr);assert.equal(blocked.stdout,'store_locked');(await store.close());const reopened=run();assert.equal(reopened.status,0,reopened.stderr);assert.equal(reopened.stdout,'opened');
});

test('a stale lease is refused without deletion, even if its owner is no longer available',async t=>{
 const {store,dir}=fixture(t);fs.mkdirSync(dir,{mode:0o700});fs.writeFileSync(store.lockFile,'retained-stale-lease',{mode:0o600});
 (await assert.rejects(async ()=>(await store.open()),isCode('store_locked')));(await store.close());assert.equal(fs.readFileSync(store.lockFile,'utf8'),'retained-stale-lease');assert.ok(!fs.existsSync(store.file));
});

test('record ID options are validated rather than silently creating or switching a registration',async t=>{
 const {store}=fixture(t);for(const value of [0,false,'',{},'not-a-record-id'])(await assert.rejects(async ()=>(await store.save(tokens(),{recordId:value})),isCode('invalid_record')));
});

test('filesystem setup errors are sanitized and never expose an OS error payload',async t=>{
 const {store}=fixture(t);const chmod=fs.chmodSync;fs.chmodSync=()=>{throw new Error('sensitive OS error detail');};
 try{(await assert.rejects(async ()=>(await store.open()),error=>error.code==='unsafe_path'&&!error.message.includes('sensitive')));}finally{fs.chmodSync=chmod;}
});

test('fresh peekState leaves OAuth storage untouched while reporting OS protection availability',async t=>{
 const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'siwc-peek-'))),dir=path.join(root,'assistant');let encryptCalls=0;
 const protect=fakeEncryptor(),encryptor={...protect,encrypt:value=>{encryptCalls++;return protect.encrypt(value);}};const store=new SiwcStore(dir,encryptor);
 t.after(async ()=>{(await store.close());fs.rmSync(root,{recursive:true,force:true});});
 assert.deepEqual((await store.peekState()),{accounts:[],active:null,encryptionAvailable:true});assert.equal(encryptCalls,0);assert.ok(!fs.existsSync(dir));
 const unavailable=new SiwcStore(dir,fakeEncryptor(false));assert.deepEqual((await unavailable.peekState()),{accounts:[],active:null,encryptionAvailable:false});(await unavailable.close());assert.ok(!fs.existsSync(dir));
});

test('peekState reads existing protected registrations through the exclusive store lease',async t=>{
 const {store,dir}=fixture(t);const a=(await store.save(tokens(),{label:'existing'}));(await store.close());const reopened=new SiwcStore(dir,fakeEncryptor());t.after(async ()=>(await reopened.close()));
 assert.equal((await reopened.peekState()).accounts[0].recordId,a.recordId);const competitor=new SiwcStore(dir,fakeEncryptor());(await assert.rejects(async ()=>(await competitor.open()),isCode('store_locked')));(await competitor.close());
});

function crashedStore(t){
 const {root,dir,store}=fixture(t);
 const source=`const fs=require('node:fs'),Module=require('node:module'),path=require('node:path'),ts=require('typescript');const file=process.argv[1],m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);const e={isAvailable:()=>true,encrypt:p=>Buffer.from('enc:'+Buffer.from(p).toString('hex')),decrypt:c=>Buffer.from(c.toString().slice(4),'hex').toString()};void(async()=>{const s=new m.exports.SiwcStore(process.argv[2],e);const a=await s.save(JSON.parse(process.argv[3]),{label:'retained account'});await s.setModel(a.recordId,'retained-model');process.stdout.write(JSON.stringify({pid:process.pid,id:a.recordId,host:await s.hostId()}));})();`;
 const child=require('node:child_process').spawnSync(process.execPath,['-e',source,path.join(__dirname,'siwcStore.ts'),dir,JSON.stringify(tokens())],{encoding:'utf8',cwd:path.resolve(__dirname,'../../..'),timeout:10000});
 assert.equal(child.status,0,child.stderr);return{root,dir,store,...JSON.parse(child.stdout)};
}

test('welcome acknowledgement and usage pause survive refresh, signout and restart without exposing identity',async t=>{
 const {dir,store}=fixture(t);const a=(await store.save(tokens()));(await store.acknowledgePlanUsage(a.recordId));(await store.setUsagePaused(a.recordId,true));
 (await store.save({...tokens(),accessToken:'replacement-access'},{recordId:a.recordId}));(await store.clearCredentials(a.recordId));
 assert.equal((await store.state()).accounts[0].planUsageAcknowledged,true);assert.equal((await store.state()).accounts[0].usagePaused,true);(await store.close());
 const reopened=new SiwcStore(dir,fakeEncryptor());t.after(async ()=>(await reopened.close()));assert.equal((await reopened.state()).accounts[0].planUsageAcknowledged,true);assert.equal((await reopened.state()).accounts[0].usagePaused,true);
 (await reopened.setUsagePaused(a.recordId,false));assert.equal((await reopened.state()).accounts[0].usagePaused,false);
 (await assert.rejects(async ()=>(await reopened.setUsagePaused(a.recordId,'false')),isCode('invalid_record')));
});

test('a real crashed owner stays locked until explicit instance-owned recovery archives ciphertext and clears credentials',async t=>{
 const {dir,store,id,host,pid}=crashedStore(t);assert.notEqual(pid,process.pid);assert.throws(()=>process.kill(pid,0),e=>e.code==='ESRCH');
 (await assert.rejects(async ()=>(await store.open()),isCode('store_locked')));const raw=fs.readFileSync(store.file,'utf8'),lock=fs.readFileSync(store.lockFile,'utf8');
 const denied=new SiwcStore(dir,fakeEncryptor());assert.equal((await denied.recoveryStatus()).available,false);(await denied.close());
 const recovery=new SiwcStore(dir,fakeEncryptor(),{canRecover:()=>true});t.after(async ()=>(await recovery.close()));
 const preview=(await recovery.recoveryStatus());assert.equal(preview.available,true);assert.equal(preview.reason,'stale_owner');assert.match(preview.ticket,/^[a-f0-9]{64}$/);
 (await assert.rejects(async ()=>(await recovery.recover({ticket:preview.ticket,confirmed:false})),isCode('recovery_confirmation_required')));
 (await assert.rejects(async ()=>(await recovery.recover({ticket:'wrong',confirmed:true})),isCode('recovery_changed')));
 assert.equal(fs.readFileSync(store.file,'utf8'),raw);assert.equal(fs.readFileSync(store.lockFile,'utf8'),lock);
 (await recovery.recover({ticket:preview.ticket,confirmed:true}));assert.equal((await recovery.credentials(id)),null);assert.equal((await recovery.hostId()),host);
 assert.equal((await recovery.registration(id)).clientId,tokens().clientId);assert.equal((await recovery.state()).accounts[0].model,'retained-model');assert.equal((await recovery.state()).accounts[0].signedIn,false);
 const archives=fs.readdirSync(dir).filter(name=>name.startsWith('chatgpt-sessions.recovery-'));assert.equal(archives.length,1);
 assert.equal(fs.readFileSync(path.join(dir,archives[0]),'utf8'),raw);if(process.platform!=='win32')assert.equal(fs.statSync(path.join(dir,archives[0])).mode&0o777,0o600);
 for(const name of fs.readdirSync(dir)){const content=fs.readFileSync(path.join(dir,name),'utf8');assert.ok(!content.includes(tokens().accessToken));assert.ok(!content.includes(tokens().subject));}
 assert.equal((await recovery.save(tokens(),{recordId:id})).recordId,id);
});

test('recovery refuses live, legacy and unverifiable owners without touching their bytes',async t=>{
 const {dir,store}=fixture(t);(await store.save(tokens()));const active=new SiwcStore(dir,fakeEncryptor(),{canRecover:()=>true});t.after(async ()=>(await active.close()));
 let raw=fs.readFileSync(store.lockFile,'utf8');assert.equal((await active.recoveryStatus()).reason,'active_owner');(await assert.rejects(async ()=>(await active.recover({ticket:'x',confirmed:true}))));assert.equal(fs.readFileSync(store.lockFile,'utf8'),raw);
 (await store.close());fs.writeFileSync(store.lockFile,'old-nonce-only-lease',{mode:0o600});raw=fs.readFileSync(store.lockFile,'utf8');assert.equal((await active.recoveryStatus()).reason,'unverifiable_owner');assert.equal(fs.readFileSync(store.lockFile,'utf8'),raw);
 fs.writeFileSync(store.lockFile,JSON.stringify({schema_version:2,pid:2147483647,nonce:'a'.repeat(64)}));const kill=process.kill;process.kill=()=>{throw Object.assign(new Error('denied'),{code:'EPERM'});};
 try{assert.equal((await active.recoveryStatus()).reason,'unverifiable_owner');}finally{process.kill=kill;}
});

test('a recovery confirmation is bound to both the lease and encrypted store snapshot',async t=>{
 const {dir,store}=crashedStore(t);const recovery=new SiwcStore(dir,fakeEncryptor(),{canRecover:()=>true});t.after(async ()=>(await recovery.close()));const preview=(await recovery.recoveryStatus());
 const original=fs.readFileSync(store.file,'utf8');fs.writeFileSync(store.file,original+' ');const changed=fs.readFileSync(store.file,'utf8'),lock=fs.readFileSync(store.lockFile,'utf8');
 (await assert.rejects(async ()=>(await recovery.recover({ticket:preview.ticket,confirmed:true})),isCode('recovery_changed')));assert.equal(fs.readFileSync(store.file,'utf8'),changed);assert.equal(fs.readFileSync(store.lockFile,'utf8'),lock);
 assert.equal(fs.readdirSync(dir).filter(name=>name.startsWith('chatgpt-sessions.recovery-')).length,0);
});

test('failed credential clearing retains the stale lease and old encrypted state for another explicit attempt',async t=>{
 const {dir,store}=crashedStore(t);const encryptor=fakeEncryptor(),recovery=new SiwcStore(dir,encryptor,{canRecover:()=>true});t.after(async ()=>(await recovery.close()));const preview=(await recovery.recoveryStatus()),raw=fs.readFileSync(store.file,'utf8'),lock=fs.readFileSync(store.lockFile,'utf8');
 encryptor.encrypt=()=>{throw new Error('sensitive failure');};(await assert.rejects(async ()=>(await recovery.recover({ticket:preview.ticket,confirmed:true})),e=>!e.message.includes('sensitive')));
 assert.equal(fs.readFileSync(store.file,'utf8'),raw);assert.equal(fs.readFileSync(store.lockFile,'utf8'),lock);(await assert.rejects(async ()=>(await new SiwcStore(dir,fakeEncryptor()).open()),isCode('store_locked')));
});
