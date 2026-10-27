const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,cache=new Map()){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const req=m.require.bind(m);m.require=n=>n.startsWith('.')&&fs.existsSync(path.resolve(path.dirname(file),n)+'.ts')?load(path.resolve(path.dirname(file),n)+'.ts',cache):req(n);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;}
const {AssistantKeyStore,electronEncryptor}=load(path.join(__dirname,'keyStore.ts'));const {SiwcStore}=load(path.join(__dirname,'siwcStore.ts'));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};const tick=()=>new Promise(r=>setImmediate(r));
const cryptoFake={isAvailable:()=>true,encrypt:p=>Buffer.from('enc:'+p),decrypt:c=>c.toString().slice(4)};
function dir(t){const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'async-vault-')));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;}
const token={issuer:'https://auth.openai.com',subject:'fake-subject',clientId:'oaiapp_fake',accessToken:'fake-access',refreshToken:'fake-refresh',idToken:'fake-id',scopes:['openid','chatgpt.tokens.use.direct'],expiresAt:1900000000000,planUsageEnabled:true};

test('macOS asynchronous adapter waits without calling synchronous Keychain APIs and unwraps legacy result',async t=>{
 const available=deferred(),decrypt=deferred();let sync=0;const forbidden=()=>{sync++;throw Error('synchronous Keychain called');};
 const adapter=electronEncryptor({isEncryptionAvailable:forbidden,encryptString:forbidden,decryptString:forbidden,isAsyncEncryptionAvailable:()=>available.promise,encryptStringAsync:async p=>Buffer.from('async:'+p),decryptStringAsync:()=>decrypt.promise},'darwin');
 const pending=adapter.isAvailable();await tick();assert.equal(sync,0);available.resolve(true);assert.equal(await pending,true);
 const result=adapter.decrypt(Buffer.from('legacy'));await tick();decrypt.resolve({result:'fake-key',shouldReEncrypt:true});assert.equal(await result,'fake-key');assert.equal(sync,0);
 assert.equal(await electronEncryptor({isEncryptionAvailable:()=>true,encryptString:forbidden,decryptString:forbidden},'darwin').isAvailable(),false);
});
test('queued provider updates preserve both entries and credentials are one atomic snapshot',async t=>{
 const root=dir(t),gate=deferred();let first=true;const store=new AssistantKeyStore(root,{...cryptoFake,encrypt:async p=>{if(first){first=false;await gate.promise;}return cryptoFake.encrypt(p);}});
 const a=store.save({provider:'openai-compatible',baseUrl:'https://a.example/v1',model:'a',key:'fake-key-a'}),b=store.save({provider:'anthropic',baseUrl:'https://b.example',model:'b',key:'fake-key-b'});await tick();gate.resolve();await Promise.all([a,b]);
 assert.equal((await store.state()).providers.length,2);assert.deepEqual(await store.credentials('openai-compatible'),{config:{provider:'openai-compatible',baseUrl:'https://a.example/v1',model:'a',format:'chat',supportsImages:false},key:'fake-key-a'});
});
test('concurrent account open is single-flight and close during decrypt fences plaintext without rewriting bytes',async t=>{
 const root=dir(t),store=new SiwcStore(root,cryptoFake);await store.open();await store.save(token);await store.close();const before=fs.readFileSync(store.file);
 const gate=deferred();let decrypts=0;const next=new SiwcStore(root,{...cryptoFake,decrypt:async c=>{decrypts++;await gate.promise;return cryptoFake.decrypt(c);}});
 const a=next.open(),b=next.open();const outcomes=Promise.allSettled([a,b]);await tick();assert.equal(decrypts,1);const closed=next.close();gate.resolve();assert.ok((await outcomes).every(x=>x.status==='rejected'&&x.reason.code==='store_closed'));await closed;
 assert.deepEqual(fs.readFileSync(store.file),before);assert.deepEqual(fs.readdirSync(root),['chatgpt-sessions.json']);
});
test('close during encryption refuses late write and preserves exact encrypted bytes',async t=>{
 const root=dir(t),gate=deferred();let hold=false;const store=new SiwcStore(root,{...cryptoFake,encrypt:async p=>{if(hold)await gate.promise;return cryptoFake.encrypt(p);}});await store.open();const before=fs.readFileSync(store.file);hold=true;
 const saving=store.save(token);const rejected=assert.rejects(saving,e=>e.code==='store_closed');await tick();const closed=store.close();gate.resolve();await rejected;await closed;assert.deepEqual(fs.readFileSync(store.file),before);assert.deepEqual(fs.readdirSync(root),['chatgpt-sessions.json']);
});
test('lease replacement during awaited encryption prevents any temp file or rename',async t=>{
 const root=dir(t),gate=deferred();let hold=false;const store=new SiwcStore(root,{...cryptoFake,encrypt:async p=>{if(hold)await gate.promise;return cryptoFake.encrypt(p);}});await store.open();const before=fs.readFileSync(store.file);hold=true;const saving=store.save(token),rejected=assert.rejects(saving,e=>e.code==='store_locked');await tick();fs.writeFileSync(store.lockFile,'replacement',{mode:0o600});gate.resolve();await rejected;await store.close();assert.deepEqual(fs.readFileSync(store.file),before);assert.equal(fs.readFileSync(store.lockFile,'utf8'),'replacement');assert.equal(fs.readdirSync(root).filter(n=>n.endsWith('.tmp')).length,0);
});
const {SiwcCredentialOwner,SiwcConnection}=load(path.join(__dirname,'siwcConnection.ts'));
for(const kind of ['native','actor'])test(`${kind} disposal invalidates immediately and bounds never-resolving cleanup while retaining unsafe lease`,async()=>{
 const clearing=deferred();let closed=null;const owner=new SiwcCredentialOwner(),s={clearCredentials:()=>clearing.promise,close:retain=>{closed=retain;}};
 const target=kind==='actor'?owner:new SiwcConnection({store:s,openExternal:()=>{}});target.unsafeCredentials.add('unsafe');
 const started=Date.now(),closing=kind==='actor'?target.dispose(s):target.dispose();assert.equal(kind==='actor'?target.closed:target.disposed,true);await closing;assert.ok(Date.now()-started<2400);assert.equal(closed,true);
 clearing.resolve();await tick();assert.equal(closed,true);
});
test('account mutation while initial protected state is pending cannot reach a provider',async()=>{
 const gate=deferred();let first=true,calls=0;const account={recordId:'record-1',label:'fake',model:'fake-model',signedIn:true,planUsageEnabled:true};
 const store={state:async()=>{if(first){first=false;await gate.promise;}return {accounts:[account],active:'record-1',encryptionAvailable:true};},registration:()=>({issuer:token.issuer,subject:token.subject,clientId:token.clientId}),credentials:()=>token,setActive:()=>{},close:()=>{}};
 const connection=new SiwcConnection({store,openExternal:()=>{},fetchImpl:async()=>{calls++;return Response.json({models:[]});}});
 const pending=connection.listModels(),rejected=assert.rejects(pending,e=>e.detail?.code==='cancelled');await tick();await connection.selectAccount(null);gate.resolve();await rejected;assert.equal(calls,0);await connection.dispose();
});
test('guarded account save cancelled while encrypting never publishes tokens',async t=>{
 const root=dir(t),gate=deferred();let hold=false,current=true;const store=new SiwcStore(root,{...cryptoFake,encrypt:async p=>{if(hold)await gate.promise;return cryptoFake.encrypt(p);}});await store.open();const before=fs.readFileSync(store.file);hold=true;
 const saving=store.save(token,{},()=>current),rejected=assert.rejects(saving,e=>e.code==='store_closed');await tick();current=false;gate.resolve();await rejected;assert.deepEqual(fs.readFileSync(store.file),before);await store.close();
});
for(const kind of ['native','actor'])test(`${kind} disposal finishes while a real store decrypt remains pending`,async t=>{
 const root=dir(t),original=new SiwcStore(root,cryptoFake);await original.open();await original.close();const gate=deferred();
 const store=new SiwcStore(root,{...cryptoFake,decrypt:async c=>{await gate.promise;return cryptoFake.decrypt(c);}}),owner=new SiwcCredentialOwner(),connection=new SiwcConnection({store,openExternal:()=>{},...(kind==='actor'?{credentialOwner:owner}:{})});
 const pending=connection.state();await tick();const closing=kind==='actor'?owner.dispose(store):connection.dispose();const outcome=await Promise.race([closing.then(()=>true),new Promise(r=>setTimeout(()=>r(false),80))]);gate.resolve();await pending;await closing;await connection.dispose();assert.equal(outcome,true);assert.equal(fs.existsSync(store.lockFile),false);
});
test('deferred API settings retired with their web tab cannot write a key after encryption finishes',async t=>{
 const {TabKeyStore}=load(path.join(__dirname,'../localHost/assistantScope.ts')),{AssistantService}=load(path.join(__dirname,'service.ts'));
 const root=dir(t),gate=deferred();let began;const started=new Promise(r=>began=r),owner=new AssistantKeyStore(root,{...cryptoFake,encrypt:async plain=>{began();await gate.promise;return cryptoFake.encrypt(plain);}}),service=new AssistantService({store:new TabKeyStore(owner)});
 const saving=service.save({provider:'openai-compatible',baseUrl:'https://a.example/v1',model:'fake',key:'fixture-secret'});const outcome=Promise.allSettled([saving]);await started;await service.dispose();gate.resolve();assert.equal((await outcome)[0].status,'rejected');assert.equal(fs.existsSync(owner.file),false);await owner.close();
});
test('service disposal retires a deferred public state result even with an injected dependency',async()=>{
 const {AssistantService}=load(path.join(__dirname,'service.ts')),gate=deferred(),service=new AssistantService({store:{state:()=>gate.promise}});
 const pending=service.state(),rejected=assert.rejects(pending,/취소/);await service.dispose();gate.resolve({providers:[],active:null,encryptionAvailable:true});await rejected;
});
test('credential snapshot uses one file version even if an external replacement occurs between awaited steps',async t=>{
 const root=dir(t),store=new AssistantKeyStore(root,cryptoFake);await store.save({provider:'openai-compatible',baseUrl:'https://a.example/v1',model:'a',key:'fixture-key-a'});
 const data=JSON.parse(fs.readFileSync(store.file,'utf8'));data.providers['openai-compatible'].baseUrl='https://b.example/v1';data.providers['openai-compatible'].key=cryptoFake.encrypt('fixture-key-b').toString('base64');
 const snapshot=store.credentials('openai-compatible');queueMicrotask(()=>fs.writeFileSync(store.file,JSON.stringify(data)));const value=await snapshot;assert.equal(value.config.baseUrl,'https://a.example/v1');assert.equal(value.key,'fixture-key-a');await store.close();
});
test('an empty key input cannot carry the previous origin credential to another host',async t=>{
 const store=new AssistantKeyStore(dir(t),cryptoFake);await store.save({provider:'openai-compatible',baseUrl:'https://a.example/v1',model:'a',key:'fixture-key-a'});
 await store.save({provider:'openai-compatible',baseUrl:'https://b.example/v1',model:'a',key:''});assert.equal((await store.credentials()).key,null);await store.close();
});
test('closing while availability is pending does not start a later encryption request',async t=>{
 const gate=deferred();let calls=0;const store=new AssistantKeyStore(dir(t),{...cryptoFake,isAvailable:()=>gate.promise,encrypt:plain=>{calls++;return cryptoFake.encrypt(plain);}});
 const saving=store.save({provider:'anthropic',baseUrl:'https://a.example',model:'fake',key:'fixture-key'}),outcome=Promise.allSettled([saving]);await tick();await store.close();gate.resolve(true);assert.equal((await outcome)[0].status,'rejected');assert.equal(calls,0);
});
test('an older account selection awaiting registration cannot replace a newer explicit selection',async()=>{
 const gate=deferred();let active=null;const store={state:()=>({accounts:[],active,encryptionAvailable:true}),registration:async id=>{if(id==='old')await gate.promise;return {issuer:token.issuer,subject:token.subject,clientId:token.clientId};},setActive:id=>{active=id;},close:()=>{}};
 const c=new SiwcConnection({store,openExternal:()=>{}}),old=c.selectAccount('old'),outcome=Promise.allSettled([old]);await tick();await c.selectAccount('new');gate.resolve();assert.equal((await outcome)[0].status,'rejected');assert.equal(active,'new');await c.dispose();
});
for(const ownership of ['native','actor'])for(const phase of ['decrypt','encrypt'])test(`${ownership} disposal during explicit stale recovery ${phase} retains the lease and blocks old refresh reuse`,async t=>{
 const root=dir(t),original=new SiwcStore(root,cryptoFake),account=await original.save(token);await original.close();const before=fs.readFileSync(original.file);
 const stale=JSON.stringify({schema_version:2,pid:2147483647,nonce:'a'.repeat(64)});fs.writeFileSync(original.lockFile,stale,{mode:0o600});
 const gate=deferred();let started;const began=new Promise(r=>started=r),actor=new SiwcCredentialOwner(),store=new SiwcStore(root,{...cryptoFake,decrypt:async cipher=>{if(phase==='decrypt'){started();await gate.promise;}return cryptoFake.decrypt(cipher);},encrypt:async plain=>{if(phase==='encrypt'){started();await gate.promise;}return cryptoFake.encrypt(plain);}},{canRecover:()=>true}),connection=new SiwcConnection({store,openExternal:()=>{throw Error('No OS');},...(ownership==='actor'?{credentialOwner:actor}:{})});
 const preview=await store.recoveryStatus();assert.equal(preview.available,true);const pending=connection.recover({ticket:preview.ticket,confirmed:true}),outcome=Promise.allSettled([pending]);await began;await connection.dispose();if(ownership==='actor')await actor.dispose(store);
 const retained=fs.existsSync(store.lockFile);const competing=new SiwcStore(root,cryptoFake),reuse=await Promise.allSettled([competing.credentials(account.recordId)]);await competing.close();gate.resolve();assert.equal((await outcome)[0].status,'rejected');
 assert.equal(retained,true,'the retained stale lease must survive close while recovery decrypt waits');assert.equal(reuse[0].status,'rejected');assert.equal(reuse[0].reason.code,'store_locked');assert.deepEqual(fs.readFileSync(store.file),before);assert.equal(fs.readFileSync(store.lockFile,'utf8'),stale);assert.equal(fs.readdirSync(root).filter(name=>name.endsWith('.tmp')).length,0);
});
for(const method of ['removeKey','removeProvider'])test(`retired tab queued ${method} preserves shared encrypted credentials`,async t=>{
 const {TabKeyStore}=load(path.join(__dirname,'../localHost/assistantScope.ts')),{AssistantService}=load(path.join(__dirname,'service.ts'));
 const root=dir(t),gate=deferred();let hold=false,started;const began=new Promise(r=>started=r),owner=new AssistantKeyStore(root,{...cryptoFake,decrypt:async cipher=>{if(hold){started();await gate.promise;}return cryptoFake.decrypt(cipher);}});
 await owner.save({provider:'anthropic',baseUrl:'https://a.example',model:'fake',key:'fixture-key'});const service=new AssistantService({store:new TabKeyStore(owner)});await service.state();const before=fs.readFileSync(owner.file);hold=true;
 const reading=owner.credentials('anthropic');await began;const mutation=service[method]('anthropic'),outcome=Promise.allSettled([mutation]);await tick();await service.dispose();gate.resolve();await reading;assert.equal((await outcome)[0].status,'rejected');
 assert.deepEqual(fs.readFileSync(owner.file),before,'a rejected stale result cannot conceal a late shared credential mutation');const state=await owner.state();assert.equal(state.providers.length,1);assert.equal(state.providers[0].hasKey,true);assert.equal((await owner.credentials('anthropic')).key,'fixture-key');await owner.close();
});
