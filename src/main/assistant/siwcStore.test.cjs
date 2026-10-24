const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),Module=require('node:module'),ts=require('typescript');
function load(file){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;}
// Missing production behavior is represented by an empty export until the new module exists.
const {SiwcStore}=fs.existsSync(path.join(__dirname,'siwcStore.ts'))?load(path.join(__dirname,'siwcStore.ts')):{};
const fakeEncryptor=(available=true)=>({isAvailable:()=>available,encrypt:plain=>Buffer.from('enc:'+Buffer.from(plain).toString('hex')),decrypt:cipher=>{if(!cipher.toString().startsWith('enc:'))throw new Error('not cipher');return Buffer.from(cipher.toString().slice(4),'hex').toString();}});
const tokens=(clientId='oaiapp_test_a',subject='subject-private-a')=>({issuer:'https://auth.openai.com',subject,clientId,accessToken:'private-access-placeholder',refreshToken:'private-refresh-placeholder',idToken:'private-id-token-placeholder',scopes:['openid','chatgpt.tokens.use.direct'],expiresAt:1900000000000,planUsageEnabled:true});
function fixture(t,available=true){const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'siwc-store-'))),dir=path.join(root,'assistant');const store=new SiwcStore(dir,fakeEncryptor(available));t.after(()=>{store.close();fs.rmSync(root,{recursive:true,force:true});});return{root,dir,store};}
const isCode=code=>error=>error.code===code;

test('sessions and identity stay encrypted while public accounts contain only opaque metadata',t=>{
 const {dir,store}=fixture(t);store.open();const host=store.hostId();assert.match(host,/^urn:uuid:[a-f0-9-]{36}$/);
 const account=store.save(tokens(),{label:'검사 계정'});store.setModel(account.recordId,'model-test');
 assert.deepEqual(store.state(),{accounts:[{recordId:account.recordId,label:'검사 계정',model:'model-test',signedIn:true,planUsageEnabled:true}],active:account.recordId,encryptionAvailable:true});
 assert.deepEqual(store.credentials(),tokens());assert.deepEqual(store.registration(account.recordId),{issuer:tokens().issuer,subject:tokens().subject,clientId:tokens().clientId});
 const raw=fs.readFileSync(store.file,'utf8');for(const secret of [tokens().accessToken,tokens().refreshToken,tokens().idToken,tokens().subject,tokens().clientId,host,'검사 계정'])assert.ok(!raw.includes(secret));
 for(const secret of [tokens().accessToken,tokens().subject,tokens().clientId])assert.ok(!JSON.stringify(store.state()).includes(secret));
 if(process.platform!=='win32'){assert.equal(fs.statSync(store.file).mode&0o777,0o600);assert.equal(fs.statSync(dir).mode&0o777,0o700);assert.equal(fs.statSync(store.lockFile).mode&0o777,0o600);}
});

test('exclusive lifetime lease refuses a competing owner and can be reopened after close',t=>{
 const {dir,store}=fixture(t);store.open();const host=store.hostId(),a=store.save(tokens());const second=new SiwcStore(dir,fakeEncryptor());
 assert.throws(()=>second.open(),isCode('store_locked'));assert.throws(()=>second.credentials(),isCode('store_locked'));
 store.close();assert.throws(()=>store.credentials(),isCode('store_closed'));second.open();assert.equal(second.hostId(),host);assert.equal(second.credentials(a.recordId).accessToken,tokens().accessToken);second.close();
 assert.deepEqual(fs.readdirSync(dir),['chatgpt-sessions.json']);
});

test('same issued identity reuses one record while distinct clients and accounts remain independent',t=>{
 const {store}=fixture(t);const a=store.save(tokens(),{label:'첫 계정'});store.setModel(a.recordId,'one');
 const same=store.save({...tokens(),accessToken:'renewed-private-access'});assert.equal(same.recordId,a.recordId);assert.equal(same.label,'첫 계정');assert.equal(same.model,'one');
 const b=store.save(tokens('oaiapp_test_b'),{label:'별도 등록',activate:false});store.setModel(b.recordId,'two');
 assert.notEqual(a.recordId,b.recordId);assert.equal(store.state().active,a.recordId);store.setActive(b.recordId);assert.equal(store.credentials().clientId,'oaiapp_test_b');assert.equal(store.state().accounts.find(x=>x.recordId===a.recordId).model,'one');
 assert.throws(()=>store.save(tokens('oaiapp_test_a','different-subject'),{recordId:a.recordId}),isCode('invalid_record'));
});

test('logout removes credentials but retains registration, host, label and model for returning login',t=>{
 const {store}=fixture(t);const a=store.save(tokens(),{label:'현장'}),host=store.hostId();store.setModel(a.recordId,'model-test');store.clearCredentials(a.recordId);
 assert.equal(store.credentials(a.recordId),null);assert.equal(store.registration(a.recordId).clientId,'oaiapp_test_a');assert.equal(store.hostId(),host);
 assert.deepEqual(store.state().accounts,[{recordId:a.recordId,label:'현장',model:'model-test',signedIn:false,planUsageEnabled:false}]);
 const again=store.save(tokens(),{recordId:a.recordId});assert.equal(again.recordId,a.recordId);assert.equal(again.label,'현장');assert.equal(again.model,'model-test');store.setActive(null);assert.equal(store.credentials(),null);
});

test('unavailable encryption exposes only disabled state and cannot create a host or session',t=>{
 const {dir,store}=fixture(t,false);assert.deepEqual(store.state(),{accounts:[],active:null,encryptionAvailable:false});assert.throws(()=>store.open(),isCode('storage_unavailable'));assert.throws(()=>store.save(tokens()),isCode('storage_unavailable'));assert.ok(!fs.existsSync(dir));
});

test('failed atomic replace preserves previous encrypted state and clears only its temporary file',t=>{
 const {dir,store}=fixture(t);const a=store.save(tokens());store.setModel(a.recordId,'before');const raw=fs.readFileSync(store.file,'utf8');
 const rename=fs.renameSync;fs.renameSync=()=>{throw new Error('disk full with secret server detail');};
 try{assert.throws(()=>store.setModel(a.recordId,'after'),error=>error.code==='store_write_failed'&&!error.message.includes('secret'));}finally{fs.renameSync=rename;}
 assert.equal(fs.readFileSync(store.file,'utf8'),raw);assert.equal(store.state().accounts[0].model,'before');assert.equal(fs.readdirSync(dir).length,2);
});

test('corrupted ciphertext or decrypted schema is refused and does not get overwritten',t=>{
 const {dir,store}=fixture(t);store.open();const file=store.file;store.close();
 for(const value of ['{not json',JSON.stringify({schema_version:1,payload:Buffer.from('bad cipher').toString('base64')}),JSON.stringify({schema_version:1,payload:fakeEncryptor().encrypt(JSON.stringify({schema_version:1,hostId:'bad',active:null,accounts:[]})).toString('base64')})]){
  fs.writeFileSync(file,value,{mode:0o600});const broken=new SiwcStore(dir,fakeEncryptor());assert.throws(()=>broken.open(),isCode('store_corrupt'));assert.equal(fs.readFileSync(file,'utf8'),value);assert.ok(!fs.existsSync(broken.lockFile));broken.close();
 }
});

test('linked targets, parent directories and foreign lease replacements are never followed or removed',{skip:process.platform==='win32'&&'symlinks need Windows privilege'},t=>{
 const {root,dir,store}=fixture(t);fs.mkdirSync(dir,{mode:0o700});const outside=path.join(root,'outside');fs.writeFileSync(outside,'keep');fs.symlinkSync(outside,store.file);
 assert.throws(()=>store.open(),isCode('unsafe_path'));assert.equal(fs.readFileSync(outside,'utf8'),'keep');fs.unlinkSync(store.file);store.open();
 fs.unlinkSync(store.lockFile);fs.writeFileSync(store.lockFile,'someone-else',{mode:0o600});assert.throws(()=>store.credentials(),isCode('store_locked'));store.close();assert.equal(fs.readFileSync(store.lockFile,'utf8'),'someone-else');
 const parent=path.join(root,'linked');fs.symlinkSync(dir,parent);const linked=new SiwcStore(parent,fakeEncryptor());assert.throws(()=>linked.open(),isCode('unsafe_path'));linked.close();
});

test('strict bounded input cannot smuggle token material into account labels or metadata',t=>{
 const {store}=fixture(t);for(const value of [{...tokens(),planUsageEnabled:false},{...tokens(),issuer:'https://other.example'},{...tokens(),refreshToken:''},{...tokens(),expiresAt:NaN}])assert.throws(()=>store.save(value),isCode('invalid_record'));
 const a=store.save(tokens());for(const value of ['bad\nlabel','x'.repeat(81)])assert.throws(()=>store.setLabel(a.recordId,value),isCode('invalid_record'));
 assert.throws(()=>store.setModel(a.recordId,'model with spaces'),isCode('invalid_record'));assert.throws(()=>store.setActive(crypto.randomUUID()),isCode('unknown_account'));
 const copy=store.credentials();copy.accessToken='tampered';assert.notEqual(store.credentials().accessToken,'tampered');
});

test('an independent process cannot own or read the session until the current owner closes',t=>{
 const {store,dir}=fixture(t);store.open();store.save(tokens());
 const source=`const fs=require('node:fs'),Module=require('node:module'),path=require('node:path'),ts=require('typescript');const file=process.argv[1],dir=process.argv[2],m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);const encryptor={isAvailable:()=>true,encrypt:p=>Buffer.from('enc:'+Buffer.from(p).toString('hex')),decrypt:c=>Buffer.from(c.toString().slice(4),'hex').toString()};let store;try{store=new m.exports.SiwcStore(dir,encryptor);store.open();process.stdout.write('opened');}catch(e){process.stdout.write(e.code||'unsafe-error');}finally{store?.close();}`;
 const run=()=>require('node:child_process').spawnSync(process.execPath,['-e',source,path.join(__dirname,'siwcStore.ts'),dir],{encoding:'utf8',cwd:path.resolve(__dirname,'../../..'),timeout:10000});
 const blocked=run();assert.equal(blocked.status,0,blocked.stderr);assert.equal(blocked.stdout,'store_locked');store.close();const reopened=run();assert.equal(reopened.status,0,reopened.stderr);assert.equal(reopened.stdout,'opened');
});

test('a stale lease is refused without deletion, even if its owner is no longer available',t=>{
 const {store,dir}=fixture(t);fs.mkdirSync(dir,{mode:0o700});fs.writeFileSync(store.lockFile,'retained-stale-lease',{mode:0o600});
 assert.throws(()=>store.open(),isCode('store_locked'));store.close();assert.equal(fs.readFileSync(store.lockFile,'utf8'),'retained-stale-lease');assert.ok(!fs.existsSync(store.file));
});

test('record ID options are validated rather than silently creating or switching a registration',t=>{
 const {store}=fixture(t);for(const value of [0,false,'',{},'not-a-record-id'])assert.throws(()=>store.save(tokens(),{recordId:value}),isCode('invalid_record'));
});

test('filesystem setup errors are sanitized and never expose an OS error payload',t=>{
 const {store}=fixture(t);const chmod=fs.chmodSync;fs.chmodSync=()=>{throw new Error('sensitive OS error detail');};
 try{assert.throws(()=>store.open(),error=>error.code==='unsafe_path'&&!error.message.includes('sensitive'));}finally{fs.chmodSync=chmod;}
});

test('fresh peekState leaves OAuth storage untouched while reporting OS protection availability',t=>{
 const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'siwc-peek-'))),dir=path.join(root,'assistant');let encryptCalls=0;
 const protect=fakeEncryptor(),encryptor={...protect,encrypt:value=>{encryptCalls++;return protect.encrypt(value);}};const store=new SiwcStore(dir,encryptor);
 t.after(()=>{store.close();fs.rmSync(root,{recursive:true,force:true});});
 assert.deepEqual(store.peekState(),{accounts:[],active:null,encryptionAvailable:true});assert.equal(encryptCalls,0);assert.ok(!fs.existsSync(dir));
 const unavailable=new SiwcStore(dir,fakeEncryptor(false));assert.deepEqual(unavailable.peekState(),{accounts:[],active:null,encryptionAvailable:false});unavailable.close();assert.ok(!fs.existsSync(dir));
});

test('peekState reads existing protected registrations through the exclusive store lease',t=>{
 const {store,dir}=fixture(t);const a=store.save(tokens(),{label:'existing'});store.close();const reopened=new SiwcStore(dir,fakeEncryptor());t.after(()=>reopened.close());
 assert.equal(reopened.peekState().accounts[0].recordId,a.recordId);const competitor=new SiwcStore(dir,fakeEncryptor());assert.throws(()=>competitor.open(),isCode('store_locked'));competitor.close();
});
