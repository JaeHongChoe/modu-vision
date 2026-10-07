const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),Module=require('node:module'),ts=require('typescript');
function load(){const file=path.join(__dirname,'applicationLaunch.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;}
test('private framing rejects duplicate keys, nonfinite values and oversized frames',()=>{const {parsePrivateDocument}=load();
 for(const raw of ['{"a":1,"a":2}','{"a":1e999}','{"a":{"k":1,"k":2}}','x'.repeat(65537)])assert.throws(()=>parsePrivateDocument(Buffer.from(raw)));
 assert.deepEqual(parsePrivateDocument(Buffer.from('{"created_at":1700000000.0,"pid":17}')).value,{created_at:1700000000,pid:17});
 assert.equal(parsePrivateDocument(Buffer.from('{"created_at":1700000000.0,"pid":17}')).canonical,'{"created_at":1700000000.0,"pid":17}');});
test('ordinary absence returns without creating scopes while partial owned context refuses',async()=>{const {authenticateMainLaunch}=load();
 assert.equal(await authenticateMainLaunch({}),null);
 await assert.rejects(authenticateMainLaunch({VISION_APPLICATION_LAUNCH_NONCE:'a'.repeat(32)}),/context|root/i);});
test('owned current application cannot start without a private descriptor or create project/auth scopes',async()=>{const {authenticateMainLaunch}=load();
 const root=fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()),'owned-main-refusal-'));
 try {fs.writeFileSync(path.join(root,'.global-migration-owner.json'),'{}');fs.writeFileSync(path.join(root,'application-active.json'),'{}');
 await assert.rejects(authenticateMainLaunch({VISION_AI_STUDIO_USER_DATA_DIR:root}),/descriptor|context/i);
 assert.equal(fs.existsSync(path.join(root,'projects')),false);assert.equal(fs.existsSync(path.join(root,'auth')),false);
 }finally{fs.rmSync(root,{recursive:true,force:true});}});
for(const name of ['.global-migration-owner.json','application-active.json','application-launch-lease.json','.application-launches','application-database-ownership.lock'])test(`dangling ${name} refuses before ordinary fallback creates scopes`,async()=>{const {authenticateMainLaunch}=load();const root=fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()),'owned-link-refusal-'));
 try{fs.symlinkSync(path.join(root,'missing-target'),path.join(root,name));await assert.rejects(authenticateMainLaunch({VISION_AI_STUDIO_USER_DATA_DIR:root}),/link/i);assert.equal(fs.existsSync(path.join(root,'projects')),false);assert.equal(fs.existsSync(path.join(root,'auth')),false);}finally{fs.rmSync(root,{recursive:true,force:true});}});
test('malformed original owner refuses before ordinary mutable startup',async()=>{const {authenticateMainLaunch}=load();const root=fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()),'owned-invalid-owner-'));
 try{fs.writeFileSync(path.join(root,'.global-migration-owner.json'),'{}');await assert.rejects(authenticateMainLaunch({VISION_AI_STUDIO_USER_DATA_DIR:root}),/identity/i);assert.equal(fs.existsSync(path.join(root,'projects')),false);}finally{fs.rmSync(root,{recursive:true,force:true});}});
test('actual index refusal reaches neither instance lock nor supervisor nor IPC/window startup',async()=>{const root=fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()),'owned-index-refusal-')),saved=process.env.VISION_AI_STUDIO_USER_DATA_DIR,calls=[];
 try{fs.writeFileSync(path.join(root,'.global-migration-owner.json'),'{}');fs.writeFileSync(path.join(root,'application-active.json'),'{}');process.env.VISION_AI_STUDIO_USER_DATA_DIR=root;
 const file=path.join(__dirname,'index.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
 m.require=name=>name==='electron'?{app:{isPackaged:false,on:()=>{},exit:code=>calls.push(['exit',code]),quit:()=>calls.push('quit'),whenReady:()=>{calls.push('whenReady');return new Promise(()=>{});}},BrowserWindow:class{constructor(){calls.push('window');}}}:name==='./supervisor'?{BackendSupervisor:class{constructor(){calls.push('supervisor');}}}:name==='./instanceLock'?{acquireAppInstanceLock:()=>{calls.push('instanceLock');return true;}}:name==='./ipc'?{registerIpcHandlers:()=>calls.push('ipc')}:name==='./sharedSession'?{}:name==='./applicationLaunch'?load():original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);
 await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(calls,[['exit',2]]);assert.equal(fs.existsSync(path.join(root,'projects')),false);assert.equal(fs.existsSync(path.join(root,'auth')),false);
 }finally{if(saved===undefined)delete process.env.VISION_AI_STUDIO_USER_DATA_DIR;else process.env.VISION_AI_STUDIO_USER_DATA_DIR=saved;fs.rmSync(root,{recursive:true,force:true});}});

test('application manifest accepts reviewed 8MiB and 20000 member bounds but control/executable bounds stay strict',()=>{
 const {OwnedApplicationLaunch,parsePrivateDocument}=load(),crypto=require('node:crypto');
 const digest=raw=>crypto.createHash('sha256').update(raw).digest('hex');
 const root=fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()),'owned-manifest-boundary-'));
 const nonce='a'.repeat(32),installation='b'.repeat(32),generation='c'.repeat(32),databaseGeneration='d'.repeat(32);
 const application=path.join(root,'.application-generations',generation,'application');
 const executable=path.join(application,'Owned.app/Contents/MacOS/owned-main');
 const manifestPath=path.join(application,'portable-application.json'),journalPath=path.join(root,'.application-launches',nonce,'journal.json');
 const main={pid:process.pid,created_at:1,command_sha256:'e'.repeat(64)},savedArgv=process.argv[1];
 const write=(name,value)=>{fs.mkdirSync(path.dirname(name),{recursive:true});fs.writeFileSync(name,JSON.stringify(value));};
 try{
 fs.mkdirSync(path.dirname(executable),{recursive:true});fs.writeFileSync(executable,'owned executable fixture');
 const st=fs.statSync(root),entry={path:path.relative(application,executable).split(path.sep).join('/'),size:fs.statSync(executable).size,sha256:digest(fs.readFileSync(executable)),executable:true};
 const database={schema_version:1,installation_id:installation,generation_id:databaseGeneration,sealed_sha256:'f'.repeat(64),fence:1};
 const binding={installation_id:installation,update_id:generation,application_generation:generation,database_pointer:database,
 database_generation_path:path.join(root,'.global-generations',databaseGeneration),executable,executable_sha256:entry.sha256};
 write(path.join(root,'.global-migration-owner.json'),{schema_version:1,installation_id:installation,root_identity:{path:root,device:st.dev,inode:st.ino},
 scopes:{ledger:'jobs/ledger.sqlite3',leases:'resource_leases.sqlite3',profiles:'compute_profiles.json',accounts:'auth/accounts.sqlite',context:'projects/.context.sqlite3',local_journals:'local_jobs',remote_journals:'remote_jobs'}});
 write(path.join(root,'application-active.json'),{schema_version:1,installation_id:installation,update_id:generation,application_generation:generation,database_pointer:database});
 write(path.join(root,'global-active.json'),database);
 const installManifest=raw=>{fs.writeFileSync(manifestPath,raw);binding.application_manifest_sha256=digest(raw);
 const journal={nonce,revision:1,state:'starting',supervisor:{pid:process.ppid},process:main,binding};write(journalPath,journal);
 write(path.join(root,'application-launch-lease.json'),{nonce,installation_id:installation,revision:1,record_sha256:digest(parsePrivateDocument(fs.readFileSync(journalPath)).canonical)});};
 const rows=count=>[entry,...Array.from({length:count-1},(_,i)=>({path:'Owned.app/Contents/Resources/member-'+String(i).padStart(5,'0')+'.txt',size:1,sha256:'0'.repeat(64),executable:false}))];
 // Controlled manifest reader fixture only: metadata rows are not claimed as
 // a complete installer, authenticated descriptor or native process proof.
 process.argv[1]=executable;const owner=new OwnedApplicationLaunch(root,nonce,binding,main,'controlled reader',{assertEmpty(){}});
 const accepted=Buffer.from(JSON.stringify({schema_version:2,entrypoint:entry.path,files:rows(20000)}));
 assert(accepted.length>1024**2&&accepted.length<8*1024**2);installManifest(accepted);
 assert.deepEqual(owner.executable(executable),{sha256:entry.sha256,build:null});
 const exactLimit=Buffer.concat([accepted,Buffer.alloc(8*1024**2-accepted.length,32)]);installManifest(exactLimit);
 assert.deepEqual(owner.executable(executable),{sha256:entry.sha256,build:null});
 installManifest(Buffer.concat([exactLimit,Buffer.from(' ')]));assert.throws(()=>owner.executable(executable),/bounded regular/);
 installManifest(Buffer.from(JSON.stringify({schema_version:2,entrypoint:entry.path,files:rows(20001)})));assert.throws(()=>owner.executable(executable),/entrypoint/);
 installManifest(accepted);fs.writeFileSync(executable,'');assert.throws(()=>owner.executable(executable),/bounded regular/);
 fs.writeFileSync(executable,'owned executable fixture');fs.writeFileSync(path.join(root,'.global-migration-owner.json'),'');assert.throws(()=>owner.executable(executable),/bounded regular/);
 }finally{process.argv[1]=savedArgv;fs.rmSync(root,{recursive:true,force:true});}
});
