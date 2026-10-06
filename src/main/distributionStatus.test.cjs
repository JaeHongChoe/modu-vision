const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),crypto=require('node:crypto');
function load(name='distributionStatus.ts'){const file=path.join(__dirname,name);assert.ok(fs.existsSync(file),'distribution manager must exist');const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=key=>key==='./releaseTrust'?load('releaseTrust.ts'):original(key);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;}
function fixture(t,extra={}){const {DistributionManager}=load(),directory=fs.mkdtempSync(path.join(os.tmpdir(),'desktop-distribution-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));return new DistributionManager({appVersion:'2.3.4',packaged:false,appPath:'/Application.app',executablePath:'/Application.app/executable',userDataPath:directory,platform:'darwin',arch:'arm64',...extra});}
function response(value){return new Response(typeof value==='string'?value:JSON.stringify(value),{status:200});}
test('actual app version and development state do not invent signing or updates',async t=>{const m=fixture(t,{runner:async()=>assert.fail('no signing in development')});const state=await m.status();assert.equal(state.app_version,'2.3.4');assert.equal(state.signature.status,'development');assert.equal(state.update.status,'not_configured');assert.equal(state.update.automatic_update_available,false);});
test('channel persists and validates target before presenting an update',async t=>{const m=fixture(t,{fetcher:async()=>response({version:'2.4.0',channel:'stable',platform:'win32',arch:'x64',url:'https://releases.example/app.exe',sha256:'a'.repeat(64),size:8})});await m.configure({channel:'stable',manifest_url:'https://releases.example/manifest.json'});await assert.rejects(()=>m.check(),/platform|architecture/);assert.equal((await m.status()).update.configured,true);await assert.rejects(()=>m.configure({channel:'stable',manifest_url:'http://unsafe.example/feed'}),/HTTPS/);});
test('bounded manifest and older release cannot become a candidate',async t=>{let value='x'.repeat(70*1024);const m=fixture(t,{fetcher:async()=>response(value)});await m.configure({channel:'stable',manifest_url:'https://releases.example/manifest.json'});await assert.rejects(()=>m.check(),/64 KiB/);value={version:'2.3.0',channel:'stable',platform:'darwin',arch:'arm64',url:'https://releases.example/app.dmg',sha256:'a'.repeat(64),size:8};assert.equal((await m.check()).update.status,'up_to_date');});
test('manual download checks bytes and cannot certify an unsigned artifact',async t=>withWindowsFlushRule(async()=>{const bytes=Buffer.from('real artifact bytes'),release={version:'2.4.0',channel:'stable',platform:'darwin',arch:'arm64',url:'https://releases.example/app.dmg',sha256:crypto.createHash('sha256').update(bytes).digest('hex'),size:bytes.length};const m=fixture(t,{fetcher:async url=>String(url).endsWith('.dmg')?new Response(bytes):response(release),runner:async()=>{throw new Error('unsigned');}});await m.configure({channel:'stable',manifest_url:'https://releases.example/manifest.json'});await m.check();const result=await m.download();assert.equal(result.integrity_verified,true);assert.equal(result.handoff_ready,false);assert.equal(result.signature.status,'unsigned');assert.deepEqual(fs.readFileSync(result.path),bytes);}));
test('changed feed or corrupt download never produces a manual delivery file',async t=>{const bytes=Buffer.from('wrong'),release={version:'2.4.0',channel:'stable',platform:'darwin',arch:'arm64',url:'https://releases.example/app.dmg',sha256:'a'.repeat(64),size:bytes.length};const m=fixture(t,{fetcher:async url=>String(url).endsWith('.dmg')?new Response(bytes):response(release)});await m.configure({channel:'stable',manifest_url:'https://releases.example/manifest.json'});await m.check();await assert.rejects(()=>m.download(),/checksum/);assert.equal(fs.existsSync(path.join(m.options.userDataPath,'updates'))&&fs.readdirSync(path.join(m.options.userDataPath,'updates')).some(x=>!x.startsWith('.')),false);});
test('a valid native signature needs a real publisher identifier',async t=>{let text='Signature=adhoc\nTeamIdentifier=not set';const m=fixture(t,{packaged:true,runner:async()=>({stdout:'',stderr:text})});assert.equal((await m.status()).signature.status,'unsigned');text='TeamIdentifier=not set';assert.equal((await m.status()).signature.status,'unsigned');text='Authority=Developer ID Application\nTeamIdentifier=ACTUAL-TEST-OUTPUT';assert.equal((await m.status()).signature.status,'verified');});
test('configuration change during a pending request cannot publish an old candidate',async t=>{let resolve;const m=fixture(t,{fetcher:()=>new Promise(r=>{resolve=r;})});await m.configure({channel:'stable',manifest_url:'https://releases.example/manifest.json'});const check=m.check();await m.configure(null);resolve(response({}));await assert.rejects(()=>check,/configuration changed/);assert.equal((await m.status()).update.configured,false);});
// Windows flushes a file only through a handle opened for writing (FlushFileBuffers needs write access): an fsync through
// a read-only handle fails with EPERM there, while macOS and Linux allow it. The rule is applied on every platform; a
// handle is read-only by its access mode, and a closed handle number is forgotten.
async function withWindowsFlushRule(run){const readOnly=new Set(),{openSync,closeSync,fsyncSync}=fs;
  const readOnlyFlags=flags=>typeof flags==='number'?(flags&3)===fs.constants.O_RDONLY:flags===undefined||['r','rs','sr'].includes(flags);
  fs.openSync=(file,flags,...rest)=>{const fd=openSync(file,flags,...rest);if(readOnlyFlags(flags))readOnly.add(fd);else readOnly.delete(fd);return fd;};
  fs.closeSync=fd=>{readOnly.delete(fd);return closeSync(fd);};
  fs.fsyncSync=fd=>{if(readOnly.has(fd)){const error=new Error('EPERM: operation not permitted, fsync');error.code='EPERM';throw error;}return fsyncSync(fd);};
  try{return await run();}finally{Object.assign(fs,{openSync,closeSync,fsyncSync});}}
test('saved settings are flushed through a handle Windows can flush',async t=>withWindowsFlushRule(async()=>{const m=fixture(t);
  await m.configure({channel:'stable',manifest_url:'https://releases.example/manifest.json'});assert.equal((await m.status()).update.configuration.channel,'stable');}));

test('Windows transient replacement retries the same flushed file without removing old settings',async t=>{
  const m=fixture(t,{platform:'win32',arch:'x64'}),file=path.join(m.options.userDataPath,'distribution-channel.json');
  await m.configure({channel:'stable',manifest_url:'https://releases.example/manifest.json'});
  const before=fs.readFileSync(file),{renameSync,fsyncSync,writeFileSync,closeSync}=fs;let attempts=0,flushed=0,temporary,writeFd,closed=false;
  fs.writeFileSync=(fd,...args)=>{if(typeof fd==='number')writeFd=fd;return writeFileSync(fd,...args);};
  fs.fsyncSync=fd=>{assert.equal(fd,writeFd,'flush must follow the actual write');flushed++;return fsyncSync(fd);};
  fs.closeSync=fd=>{if(fd===writeFd){assert.equal(flushed,1,'flush must precede close');closed=true;}return closeSync(fd);};
  fs.renameSync=(from,to)=>{if(to===file){attempts++;assert.equal(flushed,1);assert.equal(closed,true);assert.deepEqual(fs.readFileSync(file),before);if(temporary)assert.equal(from,temporary);temporary=from;
    if(attempts<3){const error=new Error('controlled sharing violation');error.code=attempts===1?'EPERM':'EBUSY';throw error;}}return renameSync(from,to);};
  try{await m.configure({channel:'beta',manifest_url:'https://releases.example/beta.json'});}
  finally{Object.assign(fs,{renameSync,fsyncSync,writeFileSync,closeSync});}
  assert.equal(attempts,3);assert.equal((await m.status()).update.configuration.channel,'beta');
  assert.equal(fs.readdirSync(m.options.userDataPath).some(x=>x.endsWith('.tmp')),false);
});

test('a persistent Windows sharing error is bounded and preserves reopened settings',async t=>{
  const m=fixture(t,{platform:'win32',arch:'x64'}),file=path.join(m.options.userDataPath,'distribution-channel.json');
  await m.configure({channel:'stable',manifest_url:'https://releases.example/manifest.json'});
  const before=fs.readFileSync(file),{renameSync}=fs;let attempts=0;
  fs.renameSync=(from,to)=>{if(to===file){attempts++;const error=new Error('controlled permanent lock');error.code='EACCES';throw error;}return renameSync(from,to);};
  try{await assert.rejects(()=>m.configure({channel:'beta',manifest_url:'https://releases.example/beta.json'}),/permanent lock/);}
  finally{fs.renameSync=renameSync;}
  assert.equal(attempts,4);assert.deepEqual(fs.readFileSync(file),before);
  const {DistributionManager}=load();assert.equal((await new DistributionManager(m.options).status()).update.configuration.channel,'stable');
  assert.equal(fs.readdirSync(m.options.userDataPath).some(x=>x.endsWith('.tmp')),false);
});

for(const [platform,code] of [['darwin','EPERM'],['win32','ENOENT']])test(`replacement fails immediately for ${platform}/${code}`,async t=>{
  const m=fixture(t,{platform}),{renameSync}=fs;let attempts=0;
  fs.renameSync=()=>{attempts++;const error=new Error('controlled unexpected replacement error');error.code=code;throw error;};
  try{await assert.rejects(()=>m.configure({channel:'stable',manifest_url:'https://releases.example/manifest.json'}),/unexpected replacement error/);}
  finally{fs.renameSync=renameSync;}
  assert.equal(attempts,1);assert.equal(fs.readdirSync(m.options.userDataPath).length,0);
});

test('settings flush failure cannot rename or acknowledge the new channel',async t=>{
  const m=fixture(t),file=path.join(m.options.userDataPath,'distribution-channel.json');
  await m.configure({channel:'stable',manifest_url:'https://releases.example/manifest.json'});
  const before=fs.readFileSync(file),{fsyncSync,renameSync}=fs;let renames=0;
  fs.fsyncSync=()=>{throw new Error('controlled failed settings flush');};fs.renameSync=(...args)=>{renames++;return renameSync(...args);};
  try{await assert.rejects(()=>m.configure({channel:'beta',manifest_url:'https://releases.example/beta.json'}),/failed settings flush/);}
  finally{Object.assign(fs,{fsyncSync,renameSync});}
  assert.equal(renames,0);assert.deepEqual(fs.readFileSync(file),before);assert.equal((await m.status()).update.configuration.channel,'stable');
  assert.equal(fs.readdirSync(m.options.userDataPath).some(x=>x.endsWith('.tmp')),false);
});

test('journal flush failure during recovery preserves the previous recovery record',async t=>{
  const m=fixture(t),file=path.join(m.options.userDataPath,'distribution-delivery.json');
  const record={schema_version:1,status:'downloading',version:'2.4.0',manifest_sha256:'a'.repeat(64),candidate_sha256:'b'.repeat(64),installed_sha256:null};
  fs.writeFileSync(file,JSON.stringify(record));const before=fs.readFileSync(file),{fsyncSync,renameSync}=fs,{DistributionManager}=load();let renames=0;
  fs.fsyncSync=()=>{throw new Error('controlled failed journal flush');};fs.renameSync=(...args)=>{renames++;return renameSync(...args);};
  try{assert.throws(()=>new DistributionManager(m.options),/failed journal flush/);}
  finally{Object.assign(fs,{fsyncSync,renameSync});}
  assert.equal(renames,0);assert.deepEqual(fs.readFileSync(file),before);
  assert.equal(fs.readdirSync(m.options.userDataPath).some(x=>x.endsWith('.tmp')),false);
});


test('a packaged application refuses an unsigned manifest without a provisioned publisher authority',async t=>{
 const m=fixture(t,{packaged:true,resourcesPath:'/no-provisioned-authority',runner:async()=>({stdout:'',stderr:'TeamIdentifier=fixture-publisher'}),
 fetcher:async()=>response({version:'2.4.0',channel:'stable',platform:'darwin',arch:'arm64',url:'https://releases.example/app.dmg',sha256:'a'.repeat(64),size:8})});
 await m.configure({channel:'stable',manifest_url:'https://releases.example/manifest.json'});
 await assert.rejects(()=>m.check(),/pinned release authority/);
 assert.equal((await m.status()).update.release,null);
});

test('offline delivery verifies a pinned manifest and all packs without contacting a server or installing',async t=>{
 const resources=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'offline-delivery-')));t.after(()=>fs.rmSync(resources,{recursive:true,force:true}));
 const {publicKey,privateKey}=crypto.generateKeyPairSync('ed25519'),{canonical}=load('releaseTrust.ts');
 const compatibility={api_context:1,worker:1,runtime:1,dataset_index:3},trust={schema_version:1,publisher:'publisher-fixture',keys:{current:publicKey.export({type:'spki',format:'der'}).toString('base64')},revoked_key_ids:[],allowed_origins:['https://release.test'],compatibility};
 fs.writeFileSync(path.join(resources,'release-trust.json'),JSON.stringify(trust));fs.mkdirSync(path.join(resources,'artifacts'));
 const bytes=Buffer.from('isolated installer fixture'),pack=Buffer.from('isolated optional pack fixture'),hash=x=>crypto.createHash('sha256').update(x).digest('hex');
 fs.writeFileSync(path.join(resources,'artifacts','app.dmg'),bytes);fs.writeFileSync(path.join(resources,'artifacts','optional.zip'),pack);
 const value={version:'2.4.0',channel:'stable',platform:'darwin',arch:'arm64',url:'https://release.test/app.dmg',sha256:hash(bytes),size:bytes.length,publisher:trust.publisher,compatibility,
 artifacts:[{path:'app.dmg',kind:'installer',sha256:hash(bytes),size:bytes.length},{path:'optional.zip',kind:'runtime_pack',sha256:hash(pack),size:pack.length}]};
 const raw=Buffer.from(canonical(value)),manifest=path.join(resources,'release.json');fs.writeFileSync(manifest,JSON.stringify({schema_version:1,key_id:'current',payload_b64:raw.toString('base64'),signature_b64:crypto.sign(null,raw,privateKey).toString('base64')}));
 const m=fixture(t,{packaged:true,resourcesPath:resources,fetcher:async()=>assert.fail('offline must not fetch'),runner:async()=>{throw Error('unsigned controlled artifact');}});
 const result=await m.verifyOffline(manifest);assert.equal(result.integrity_verified,true);assert.equal(result.handoff_ready,false);assert.equal(result.artifacts_verified,2);assert.equal(result.offline,true);
 assert.deepEqual(fs.readFileSync(result.path),bytes);assert.deepEqual(fs.readFileSync(path.join(resources,'artifacts','optional.zip')),pack);
 assert.equal((await m.status()).update.recovery.status,'publisher_required');
});
