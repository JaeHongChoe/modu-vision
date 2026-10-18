'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {OwnedMediaTasks}=require('./scoped-gallery-media-witness.cjs');
const {createOriginalBlobPromiseWitness}=require('./scoped-gallery-blob-promise-witness.cjs');
function fixture({deadline=performance.now()+1000,remove=async()=>({}),snapshotFailureAt=0,requirePageEnable=false,enable=async()=>({})}={}){
 const calls=[],owner=new OwnedMediaTasks();let scriptCount=0,evaluations=0,statePresent=false,snapshots=0,pageEnabled=false;
 const url='file:///owned/app/dist/index.html';
 const state={narrowDeadline:()=>{},snapshot:()=>{if(++snapshots===snapshotFailureAt)throw Error('Actual diagnostic realm unavailable');return {epoch:1,documentURL:url,records:[],tasks:[],failures:[],unresolved:[],local_readers:0};},sealAndDrain:async()=>({drained:true,records:[],local_readers_closed:true,original_descriptors_restored:true})};
 const handle={evaluate:async(fn,arg)=>fn(statePresent?state:null,arg),dispose:async()=>{calls.push('dispose-state');}};
 const session=kind=>({on(){},off(){},async send(method,params){calls.push({kind,method,params});if(method==='Page.getFrameTree')return {frameTree:{frame:{id:'main',loaderId:'loader',url}}};if(kind==='script'&&method==='Page.enable'){const result=await enable();pageEnabled=true;return result;}if(method==='Page.addScriptToEvaluateOnNewDocument')return {identifier:'actual-returned-script-id'};if(method==='Page.removeScriptToEvaluateOnNewDocument')return remove();return {};},async detach(){calls.push({kind,detach:true});}});
 const page={url:()=>url,context:()=>({newCDPSession:async()=>session(scriptCount++===0?'metadata':'script')}),evaluateHandle:async()=>{evaluations++;return handle;}};
 const witness=createOriginalBlobPromiseWitness(page,owner,{epoch:1,origin:'http://127.0.0.1:9',wallStart:Date.now(),deadline,navigation:true});
 return {owner,witness,calls,deadline,currentEvaluations:()=>evaluations,navigate:()=>{statePresent=!requirePageEnable||pageEnabled;}};
}
test('a separately owned navigation session enables its Page agent before arming and the next document qualifies without outgoing-realm injection',async()=>{
 const f=fixture({requirePageEnable:true});await f.witness.start();assert.equal(f.currentEvaluations(),0);f.navigate();await f.witness.checkpoint([],f.deadline);await f.witness.finish([]);
 const script=f.calls.filter(row=>row.kind==='script'&&row.method);assert.deepEqual(script.map(row=>row.method),['Page.enable','Page.addScriptToEvaluateOnNewDocument','Page.removeScriptToEvaluateOnNewDocument']);assert.equal(f.witness.diagnostic().closed.drained,true);
});
test('a refused Page-agent initialization never registers or late-installs a navigation observer and still detaches its owned sessions',async()=>{
 const failure=Error('Actual Page agent initialization refused'),f=fixture({enable:async()=>{throw failure;}});await assert.rejects(f.witness.start(),error=>error===failure);assert.equal(f.currentEvaluations(),0);await assert.rejects(f.witness.cleanup(),error=>error===failure);
 assert.equal(f.calls.filter(row=>row.method==='Page.addScriptToEvaluateOnNewDocument').length,0);assert.equal(f.calls.filter(row=>row.detach).length,2);assert.notEqual(f.witness.diagnostic().closed?.drained,true);
});
test('navigation arms a removable future-mainrealm script without touching outgoing functions and closes the exact returned identifier once',async()=>{
 const f=fixture();await f.witness.start();assert.equal(f.currentEvaluations(),0);const add=f.calls.find(row=>row.method==='Page.addScriptToEvaluateOnNewDocument');assert.equal(add.params.runImmediately,false);assert.equal(add.params.includeCommandLineAPI,false);assert.ok(add.params.source.includes('window.top'));
 f.navigate();await f.witness.finish([]);await f.witness.cleanup();await f.owner.drain(f.deadline);
 const removes=f.calls.filter(row=>row.method==='Page.removeScriptToEvaluateOnNewDocument');assert.equal(removes.length,1);assert.deepEqual(removes[0].params,{identifier:'actual-returned-script-id'});assert.equal(f.calls.filter(row=>row.detach&&row.kind==='script').length,1);assert.equal(f.calls.filter(row=>row.detach&&row.kind==='metadata').length,1);assert.equal(f.witness.diagnostic().closed.drained,true);
});
test('pending script removal remains separately owned, refuses original deadline and is never retried or declared closed',async()=>{
 let complete;const f=fixture({deadline:performance.now()+30,remove:()=>new Promise(resolve=>{complete=resolve;})});await f.witness.start();f.navigate();await assert.rejects(f.witness.finish([]),/deadline/);await assert.rejects(f.witness.cleanup(),/deadline|Owned future-document observer unresolved/);assert.equal(f.calls.filter(row=>row.method==='Page.removeScriptToEvaluateOnNewDocument').length,1);assert.ok(f.owner.rows.some(row=>row.label.includes('remove exact')&&row.state==='pending'));assert.notEqual(f.witness.diagnostic().removable_script.removed,true);complete({});await new Promise(resolve=>setImmediate(resolve));await assert.rejects(f.owner.drain(f.deadline),/after deadline/);
});
test('a failed realm verification or final diagnostic still releases its retained handle without successful closure',async()=>{
 for(const snapshotFailureAt of [1,4]){const f=fixture({snapshotFailureAt});await f.witness.start();f.navigate();await assert.rejects(f.witness.cleanup(),/Actual diagnostic realm unavailable/);assert.equal(f.calls.filter(row=>row==='dispose-state').length,1);assert.equal(f.calls.filter(row=>row.detach&&row.kind==='script').length,1);assert.equal(f.calls.filter(row=>row.detach&&row.kind==='metadata').length,1);assert.ok(f.owner.failures().some(row=>row.label.includes(snapshotFailureAt===1?'verify actual':'final original Blob realm')));}
});

test('provisional checkpoints retain the same realm and both sessions until final epoch closure',async()=>{
 const f=fixture();await f.witness.start();f.navigate();const proof=await f.witness.checkpoint([],f.deadline);assert.equal(proof.observer_lifetime_closed,false);assert.equal(proof.tasks_settled,true);assert.equal(f.calls.filter(row=>row==='dispose-state').length,0);assert.equal(f.calls.filter(row=>row.detach).length,0);assert.equal(f.calls.filter(row=>row.method==='Page.removeScriptToEvaluateOnNewDocument').length,0);assert.ok(await f.witness.displayAuthority());await f.witness.checkpoint([],f.deadline);await f.witness.finish([]);assert.equal(f.calls.filter(row=>row==='dispose-state').length,1);assert.equal(f.calls.filter(row=>row.detach).length,2);
});
