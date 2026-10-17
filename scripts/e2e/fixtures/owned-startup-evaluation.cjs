'use strict';
// Passive fixture evidence only: no fetch, route interception, cache writes or UI actions.
const assert=require('node:assert/strict'),crypto=require('node:crypto');
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
async function before(promise,deadline,label){
 const remaining=deadline-performance.now();assert.ok(Number.isFinite(deadline)&&remaining>0,'Original 10s startup boundary expired: '+label);
 let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Original 10s startup boundary expired: '+label)),remaining);})]);}finally{if(timer)clearTimeout(timer);}
}
function ownedRequest(request,page,origin,scopes){
 const url=new URL(request.url());if(url.origin!==origin||url.pathname!=='/api/evaluation/results')return null;
 assert.equal(request.method(),'GET');assert.equal(request.frame(),page.mainFrame());
 const entries=[...url.searchParams];assert.ok(entries.length>=2&&entries.length<=3);assert.equal(new Set(entries.map(([key])=>key)).size,entries.length);
 assert.ok(entries.every(([key])=>['source_dataset_path','source_task','job_id'].includes(key)));
 const scope=scopes.find(s=>s.source===url.searchParams.get('source_dataset_path'));assert.ok(scope,'Foreign startup evaluation source');assert.equal(url.searchParams.get('source_task'),'classification');
 const headers=request.headers(),context=JSON.parse(headers['x-vision-context']);assert.equal(headers['x-vision-project'],scope.project.id);assert.equal(context.project_id,scope.project.id);
 if(scope.context)assert.deepEqual(context,scope.context);else assert.equal(context.mode,'local');
 for(const key of ['workspace_id','actor_id'])assert.match(context[key],/^[A-Za-z0-9_.:-]{1,128}$/);
 const job=url.searchParams.get('job_id');if(job!==null)assert.ok(Object.hasOwn(scope.modelHashes,job));
 return {scope,identity:{method:'GET',origin,path:url.pathname,query:entries,project_id:context.project_id,workspace_id:context.workspace_id,actor_id:context.actor_id,mode:context.mode,main_frame:true,requested_job:job}};
}
function completedBody(bytes,response,scope){
 assert.equal(response.status(),200);assert.ok(Buffer.isBuffer(bytes)&&bytes.length>0&&bytes.length<=1024*1024);const body=JSON.parse(bytes.toString('utf8'));
 assert.equal(body.task,'classification');assert.ok(Object.hasOwn(scope.modelHashes,body.job_id));assert.equal(body.binding.source_dataset_path,scope.source);assert.equal(body.binding.checkpoint_sha256,scope.modelHashes[body.job_id]);assert.equal(body.binding.labelset_id,scope.project.active_labelset_id||'default');
 assert.match(body.evaluation_id,/^[A-Za-z0-9_-]{1,128}$/);assert.ok(Array.isArray(body.test_predictions)&&body.test_predictions.length===scope.heldoutPaths.length);
 assert.deepEqual(body.test_predictions.map(p=>p.file_path).sort(),[...scope.heldoutPaths].sort());
 return {body,body_sha256:digest(bytes),body_bytes:bytes.length,job_id:body.job_id,evaluation_id:body.evaluation_id,automatic_device:'original_backend_selection_unobserved'};
}
function observe(page,origin,scopes){
 assert.ok(Array.isArray(scopes)&&scopes.length>0&&scopes.length<=2);assert.equal(new Set(scopes.map(s=>s.source)).size,scopes.length);assert.equal(new Set(scopes.map(s=>s.project.id)).size,scopes.length);
 const requests=new Map(),rows=[],waiters=new Set();let sequence=0,failure,closed=false,phase;
 const wake=()=>{for(const resolve of [...waiters])resolve();waiters.clear();};
 const poison=error=>{failure??=error;wake();};
 const onRequest=request=>{try{const own=ownedRequest(request,page,origin,scopes);if(!own)return;assert.equal(closed,false);assert.equal(requests.has(request),false);assert.ok(phase&&own.scope===phase.scope,'Original startup request outside owning phase');const started=performance.now();assert.ok(started<=phase.deadline,'Original startup request after preparation deadline');assert.ok(++sequence<=16,'Startup evaluation observation overflow');requests.set(request,{...own,sequence,started,deadline:phase.deadline,pending:true});wake();}catch(error){poison(error);}};
 const onResponse=response=>{const request=response.request(),row=requests.get(request);if(!row)return;const task=(async()=>{try{assert.equal(row.response_seen,undefined);row.response_seen=true;const deadline=row.deadline;assert.ok(Number.isFinite(deadline),'Startup request must belong to an original armed phase');const bytes=await before(response.body(),deadline,'full original evaluation body');const value=completedBody(bytes,response,row.scope);assert.equal(await before(response.finished(),deadline,'original evaluation response finish'),null);const finished=performance.now();assert.ok(finished<=deadline);row.pending=false;rows.push({...row.identity,...value,sequence:row.sequence,started:row.started,finished,deadline,finished_normally:true,original_response:true});wake();}catch(error){poison(error);}})();void task.catch(poison);};
 const onFailed=request=>{if(requests.has(request))poison(Error('Original startup evaluation request failed'));};
 page.on('request',onRequest);page.on('response',onResponse);page.on('requestfailed',onFailed);
 return {
  arm(scope,deadline){assert.equal(closed,false);assert.ok(scopes.includes(scope));assert.ok(Number.isFinite(deadline)&&deadline>performance.now());phase={scope,deadline,after:sequence};return phase.after;},
  async wait(scope,after,deadline){while(true){if(failure)throw failure;assert.equal(closed,false);const pending=[...requests.values()].filter(r=>r.pending);const own=rows.filter(r=>r.project_id===scope.project.id&&r.sequence>after);if(own.length&&pending.length===0)return own;let release;const change=new Promise(resolve=>{release=resolve;waiters.add(resolve);});try{await before(change,deadline,'original startup evaluation completion');}finally{waiters.delete(release);}}},
  snapshot(){if(failure)throw failure;assert.equal(closed,false);assert.equal([...requests.values()].filter(r=>r.pending).length,0,'Startup evaluation still pending before immutable baseline');for(const scope of scopes)assert.ok(rows.some(r=>r.project_id===scope.project.id));return {schema:'modu-vision.owned-startup-evaluation-preparation/v1',rows:[...rows],pending:0,preparation_only:true,measured_flow_credit:false,automatic_device:'original_backend_selection_unobserved'};},
  close(){if(!closed){closed=true;page.off('request',onRequest);page.off('response',onResponse);page.off('requestfailed',onFailed);wake();}},
 };
}
module.exports={before,ownedRequest,completedBody,observe};
