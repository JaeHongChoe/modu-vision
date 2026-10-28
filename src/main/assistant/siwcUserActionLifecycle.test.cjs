// Initiating-tab lifetime for explicit account changes. Generated stores and deferred crypto only.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {load}=require('../localHost/testSupport.cjs'),cache=new Map();
const {SiwcStore}=load(path.join(__dirname,'siwcStore.ts'),cache),{SiwcConnection,SiwcCredentialOwner}=load(path.join(__dirname,'siwcConnection.ts'),cache),{tabSiwcStore}=load(path.join(__dirname,'../localHost/assistantScope.ts'),cache);
const deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve:value=>resolve(value)}};
const cryptoFake={isAvailable:()=>true,encrypt:value=>Buffer.from('fixture:'+value),decrypt:value=>value.toString().slice(8)};
const token={issuer:'https://auth.openai.com',subject:'fixture-lifecycle',clientId:'oaiapp_fixture',accessToken:'fixture-access',refreshToken:'fixture-refresh',idToken:'fixture-id',scopes:['openid','chatgpt.tokens.use.direct'],expiresAt:1900000000000,planUsageEnabled:true};
async function fixture(t,method,phase){
 const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'siwc-action-lifetime-'))),gate=deferred(),started=deferred();let hold=false,decrypts=0;
 const encryptor={...cryptoFake,isAvailable:async()=>{if(hold&&method==='recover'&&(phase==='initial-read'||phase==='queue')){started.resolve();await gate.promise;}return true;},
  encrypt:async value=>{if(hold&&phase==='encrypt'){started.resolve();await gate.promise;}return cryptoFake.encrypt(value);},
  decrypt:async value=>{if(hold){decrypts++;if((phase==='initial-read'||phase==='queue')&&method!=='recover'||phase==='mutation-read'&&decrypts===(method==='recover'?1:2)){started.resolve();await gate.promise;}}return cryptoFake.decrypt(value);}};
 const seed=new SiwcStore(root,cryptoFake),account=await seed.save(token);await seed.setUsagePaused(account.recordId,true);await seed.close();
 const stale=JSON.stringify({schema_version:2,pid:2147483647,nonce:'c'.repeat(64)});if(method==='recover')fs.writeFileSync(seed.lockFile,stale,{mode:0o600});
 const owner=new SiwcStore(root,encryptor,{canRecover:()=>true}),credentialOwner=new SiwcCredentialOwner(),connection=new SiwcConnection({store:tabSiwcStore(owner),credentialOwner,openExternal:()=>{throw Error('No OS')},fetchImpl:()=>{throw Error('No transport')}});
 let input=account.recordId;if(method==='recover'){const preview=await owner.recoveryStatus();assert.equal(preview.available,true);input={ticket:preview.ticket,confirmed:true};}else await connection.selectAccount(account.recordId);
 const before=fs.readFileSync(owner.file);t.after(async()=>{hold=false;gate.resolve();await connection.dispose();await credentialOwner.dispose(owner);fs.rmSync(root,{recursive:true,force:true});});
 return {root,owner,credentialOwner,connection,input,account,before,stale,started,hold:()=>{hold=true;},release:()=>{hold=false;gate.resolve();},begin:()=>{hold=true;if(phase==='queue'){const blocker=method==='recover'?owner.recoveryStatus():owner.state();void blocker.catch(()=>undefined);}return connection[method](input)}};
}
for(const method of ['acknowledgePlanUsage','resumeUsage','recover'])for(const phase of ['encrypt','mutation-read','initial-read','queue'])test(`${method}: tab-only disposal during ${phase} rejects without changing protected state`,async t=>{
 const f=await fixture(t,method,phase),operation=f.begin(),outcome=Promise.allSettled([operation]);await f.started.promise;await f.connection.dispose();assert.equal(f.credentialOwner.closed,false,'the shared actor remains live');f.release();
 const result=(await outcome)[0];assert.equal(result.status,'rejected');assert.deepEqual(fs.readFileSync(f.owner.file),f.before,'no retired explicit action may persist');assert.equal(fs.readdirSync(f.root).filter(n=>n.endsWith('.tmp')).length,0);
 if(method==='recover'){
  assert.equal(fs.readFileSync(f.owner.lockFile,'utf8'),f.stale);if(phase!=='encrypt')assert.equal(fs.readdirSync(f.root).some(n=>n.includes('.recovery-')),false,'retired reads cannot begin an archive');const competing=new SiwcStore(f.root,cryptoFake);await assert.rejects(competing.credentials(f.account.recordId),error=>error.code==='store_locked');await competing.close();
  await f.credentialOwner.dispose(f.owner);assert.equal(fs.readFileSync(f.owner.lockFile,'utf8'),f.stale,'later actor disposal must not release interrupted recovery');
 }else {const after=(await f.owner.state()).accounts[0];assert.equal(after.usagePaused,true);assert.notEqual(after.planUsageAcknowledged,true);assert.equal((await f.owner.credentials(f.account.recordId)).refreshToken,'fixture-refresh');}
});
for(const method of ['acknowledgePlanUsage','resumeUsage','recover'])test(`${method}: a live tab persists the explicitly requested change`,async t=>{
 const f=await fixture(t,method,'encrypt'),operation=f.begin();await f.started.promise;f.release();await operation;const after=(await f.owner.state()).accounts[0];assert.notDeepEqual(fs.readFileSync(f.owner.file),f.before);
 if(method==='acknowledgePlanUsage')assert.equal(after.planUsageAcknowledged,true);else if(method==='resumeUsage')assert.equal(after.usagePaused,false);else {assert.equal(after.signedIn,false);assert.equal(await f.owner.credentials(f.account.recordId),null);}
});
for(const method of ['acknowledgePlanUsage','resumeUsage','recover'])test(`${method}: cancellation during initial read cannot revive an obsolete generation`,async t=>{
 const f=await fixture(t,method,'initial-read'),operation=f.begin(),outcome=Promise.allSettled([operation]);await f.started.promise;const cancelled=f.connection.cancel();f.release();await cancelled;assert.equal((await outcome)[0].status,'rejected');assert.deepEqual(fs.readFileSync(f.owner.file),f.before);
});
for(const cleanup of ['rate-limit-pause','credential-clear'])test(`actor-owned ${cleanup} still persists when one tab closes`,async t=>{
 const f=await fixture(t,'resumeUsage','encrypt');await f.owner.setUsagePaused(f.account.recordId,false);const facade=tabSiwcStore(f.owner);f.hold();
 const operation=cleanup==='rate-limit-pause'?facade.setUsagePaused(f.account.recordId,true):facade.clearCredentials(f.account.recordId);await f.started.promise;await f.connection.dispose();f.release();await operation;
 const after=(await f.owner.state()).accounts[0];if(cleanup==='rate-limit-pause')assert.equal(after.usagePaused,true);else assert.equal(after.signedIn,false);assert.equal(f.credentialOwner.closed,false);
});
