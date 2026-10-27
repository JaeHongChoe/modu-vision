// The assistant key store (main process only): keys are encrypted with the OS protection (a fake encryptor here), the
// settings file is owner-only and replaced atomically, the renderer-facing state never carries a key, and without the
// OS protection a key is refused, never written in a weaker form. No real key is used anywhere.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,cache=new Map()){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const original=m.require.bind(m);
 m.require=name=>{if(name.startsWith('.')){const target=path.resolve(path.dirname(file),name)+'.ts';if(fs.existsSync(target))return load(target,cache);}return original(name);};
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;}
const {AssistantKeyStore,electronEncryptor}=load(path.join(__dirname,'keyStore.ts'));
const PLACEHOLDER='test-placeholder-key-0001';
// Reversible but not plaintext: proves the stored bytes came from the encryptor.
const fakeEncryptor=(available=true)=>({isAvailable:()=>available,encrypt:plain=>Buffer.from('enc:'+Buffer.from(plain).toString('hex')),decrypt:cipher=>Buffer.from(cipher.toString().replace(/^enc:/,''),'hex').toString()});
function fixture(t,available=true){const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'assistant-keys-')));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 return {root,dir:path.join(root,'assistant'),store:new AssistantKeyStore(path.join(root,'assistant'),fakeEncryptor(available))};}

test('a key is stored encrypted in an owner-only file and only hasKey reaches the renderer',async t=>{
 const {store,dir}=fixture(t);
 const state=(await store.save({provider:'anthropic',baseUrl:'https://api.anthropic.com/',model:'model-for-tests',supportsImages:true,key:PLACEHOLDER}));
 assert.deepEqual(state.providers,[{provider:'anthropic',baseUrl:'https://api.anthropic.com',model:'model-for-tests',format:null,hasKey:true,supportsImages:true,label:'Anthropic'}]);
 assert.equal(state.active,'anthropic');assert.ok(!JSON.stringify(state).includes(PLACEHOLDER)&&!JSON.stringify(state).includes('enc:'),'no key or ciphertext in the public state');
 const file=path.join(dir,'providers.json'),raw=fs.readFileSync(file,'utf8');
 assert.ok(!raw.includes(PLACEHOLDER),'the plaintext key is never on disk');
 assert.equal((await store.key('anthropic')),PLACEHOLDER,'the main process can decrypt it for one call');
 if(process.platform!=='win32'){assert.equal(fs.statSync(file).mode&0o777,0o600);assert.equal(fs.statSync(dir).mode&0o777,0o700);}
 assert.deepEqual(fs.readdirSync(dir),['providers.json'],'no temporary file is left behind');
 // Saving without a key keeps the stored one; removing it clears hasKey.
 assert.equal((await store.save({provider:'anthropic',baseUrl:'https://api.anthropic.com',model:'other-model',supportsImages:false})).providers[0].hasKey,true);
 assert.equal((await store.removeKey('anthropic')).providers[0].hasKey,false);assert.equal((await store.key('anthropic')),null);
});

test('without OS protection a key is refused with a Korean reason and action; settings without a key still save',async t=>{
 const {store,dir}=fixture(t,false);
 (await assert.rejects(async ()=>(await store.save({provider:'openai-compatible',baseUrl:'https://api.openai.com/v1',model:'model-for-tests',supportsImages:false,key:PLACEHOLDER})),
  error=>error.detail?.code==='storage_unavailable'&&/보호 저장소/.test(error.detail.reason)&&/평문으로 저장하지 않습니다/.test(error.detail.action)));
 assert.ok(!fs.existsSync(path.join(dir,'providers.json')),'nothing was written');
 const state=(await store.save({provider:'openai-compatible',baseUrl:'http://127.0.0.1:11434/v1',model:'local-model',supportsImages:false}));
 assert.equal(state.encryptionAvailable,false);assert.equal(state.providers[0].hasKey,false);
 for(const name of fs.readdirSync(dir))assert.ok(!fs.readFileSync(path.join(dir,name),'utf8').includes(PLACEHOLDER));
});

test('Linux basic_text (a fixed obfuscation) counts as unavailable',async()=>{
 const storage=backend=>({isEncryptionAvailable:()=>true,encryptString:value=>Buffer.from(value),decryptString:value=>value.toString(),getSelectedStorageBackend:()=>backend});
 assert.equal(electronEncryptor(storage('basic_text'),'linux').isAvailable(),false);
 assert.equal(electronEncryptor(storage('gnome_libsecret'),'linux').isAvailable(),true);
 assert.equal(await electronEncryptor({...storage('unknown'),isAsyncEncryptionAvailable:async()=>true,encryptStringAsync:async p=>Buffer.from(p),decryptStringAsync:async c=>({result:c.toString(),shouldReEncrypt:false})},'darwin').isAvailable(),true);
 assert.equal(await electronEncryptor({...storage('x'),isEncryptionAvailable:()=>false},'darwin').isAvailable(),false);
});

test('the file is replaced atomically: a failed rename keeps the previous settings and leaves no temporary file',async t=>{
 const {store,dir}=fixture(t);
 (await store.save({provider:'openai-compatible',baseUrl:'https://api.openai.com/v1',model:'first-model',supportsImages:false,key:PLACEHOLDER}));
 const before=fs.readFileSync(path.join(dir,'providers.json'),'utf8');
 const rename=fs.renameSync;fs.renameSync=()=>{throw new Error('disk full');};
 try{(await assert.rejects(async ()=>(await store.save({provider:'openai-compatible',baseUrl:'https://api.openai.com/v1',model:'second-model',supportsImages:false})),/disk full/));}
 finally{fs.renameSync=rename;}
 assert.equal(fs.readFileSync(path.join(dir,'providers.json'),'utf8'),before);
 assert.deepEqual(fs.readdirSync(dir),['providers.json']);
});

test('addresses: HTTPS anywhere, HTTP only on this computer, no credentials or query; a key does not follow a new host',async t=>{
 const {store}=fixture(t);
 for(const baseUrl of ['http://api.example.com/v1','https://user:pass@api.example.com/v1','https://api.example.com/v1?key=x','ftp://api.example.com'])
  (await assert.rejects(async ()=>(await store.save({provider:'openai-compatible',baseUrl,model:'m',supportsImages:false})),/주소/));
 (await assert.rejects(async ()=>(await store.save({provider:'openai-compatible',baseUrl:'https://api.example.com/v1',model:'bad model id',supportsImages:false})),/모델/));
 (await assert.rejects(async ()=>(await store.save({provider:'openai-compatible',baseUrl:'https://api.example.com/v1',model:'m',supportsImages:false,key:'has space inside'})),/키/));
 (await store.save({provider:'openai-compatible',baseUrl:'https://api.example.com/v1',model:'m',supportsImages:false,key:PLACEHOLDER}));
 assert.equal((await store.save({provider:'openai-compatible',baseUrl:'https://other.example.com/v1',model:'m',supportsImages:false})).providers[0].hasKey,false);
 assert.equal((await store.config()).baseUrl,'https://other.example.com/v1');
});

test('a linked or damaged settings file is refused, not followed',{skip:process.platform==='win32'&&'symbolic links need extra rights on Windows'},async t=>{
 const {store,dir,root}=fixture(t);fs.mkdirSync(dir,{recursive:true});
 const outside=path.join(root,'outside.json');fs.writeFileSync(outside,'{}');fs.symlinkSync(outside,path.join(dir,'providers.json'));
 (await assert.rejects(async ()=>(await store.state()),/설정 파일/));
 fs.unlinkSync(path.join(dir,'providers.json'));fs.writeFileSync(path.join(dir,'providers.json'),'{not json');
 (await assert.rejects(async ()=>(await store.state()),/손상/));
});
