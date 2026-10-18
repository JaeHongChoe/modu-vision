'use strict';
// Metadata identity only. This module never requests or reads an HTTP body.
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const path=require('node:path');
const MAX_RECORDS=48,MAX_EVENTS=128,MAX_DECLARED_BODY=1024*1024;
const PUBLIC_FIELDS=['domainLookupStart','domainLookupEnd','connectStart','connectEnd','secureConnectionStart','requestStart','responseStart'];
const CDP_FIELDS=['dnsStart','dnsEnd','connectStart','connectEnd','sslStart','sendStart','receiveHeadersEnd'];
const digest=value=>crypto.createHash('sha256').update(value).digest('hex');
const lower=value=>Object.fromEntries(Object.entries(value||{}).map(([key,item])=>[key.toLowerCase(),item]));
const context=value=>{assert.ok(typeof value==='string'&&value);const row=JSON.parse(value);assert.ok(row&&['workspace_id','project_id','actor_id'].every(key=>typeof row[key]==='string'&&row[key])&&row.mode==='local');return {workspace_id:row.workspace_id,project_id:row.project_id,actor_id:row.actor_id,mode:row.mode};};
const contextKey=value=>JSON.stringify([value?.workspace_id,value?.project_id,value?.actor_id,value?.mode]);
function vector(value,fields){
 assert.ok(value&&typeof value==='object','Original relative timing is absent');
 const result=fields.map(key=>value[key]);assert.ok(result.every(item=>typeof item==='number'&&Number.isFinite(item)&&item>=-1),'Original relative timing is invalid');
 assert.ok(result[5]>=0&&result[6]>=result[5]&&result.some(item=>item>0),'Original relative timing is degenerate');return result;
}
class OriginalThumbnailIdentity{
 constructor({owner,epoch,origin,mainFrameId,wallStart,deadline,clock=()=>performance.now()}){
  assert.ok(owner&&Number.isFinite(deadline)&&Number.isFinite(wallStart));const address=new URL(origin);assert.equal(address.origin,origin);assert.ok(['http:','https:'].includes(address.protocol)&&['127.0.0.1','localhost','[::1]'].includes(address.hostname));
  Object.assign(this,{owner,epoch,origin,mainFrameId,wallStart,deadline,clock});this.rows=new Map();this.errors=[];this.claims=new Map();this.associations=new Map();this.waiters=new Set();this.stopped=false;this.globalFailure=false;
 }
 notify(){for(const wake of [...this.waiters])wake();}
 poison(row,error){
  if(row&&!row.error)row.error=error;
  if(row?!row.errorTask:!this.globalFailure){const task=this.owner.run('sticky original CDP metadata failure '+(row?.id||'capture'),()=>{throw error;});if(row)row.errorTask=task;else this.globalFailure=true;this.errors.push({requestId:row?.id||null,error,observed:this.clock()});}
  if(row)row.finish();this.notify();
 }
 observe(row,event,kind){
  assert.equal(this.stopped,false,'CDP metadata after capture seal');assert.ok(this.clock()<=this.deadline,'Original CDP metadata deadline expired');
  row.events++;assert.ok(row.events<=MAX_EVENTS,'New finite CDP metadata event budget exhausted');
  const timestamp=event.timestamp;row.eventRecords.push({kind,timestamp:timestamp??null,observed:this.clock()});
  if(timestamp!==undefined){assert.ok(Number.isFinite(timestamp)&&timestamp>=row.timestamp,'Original metadata precedes request start');const prior=row.lastByKind[kind];if(prior!==undefined)assert.ok(timestamp>=prior,'Original same-type metadata is out of order');row.lastByKind[kind]=timestamp;}
 }
 event(kind,event){
  try{
   if(kind==='Network.requestWillBeSent')return this.start(event);
   const row=this.rows.get(event.requestId);if(!row)return;
   if(kind==='Network.requestServedFromCache'){this.observe(row,event,kind);row.cached=true;return;}
   if(kind==='Network.responseReceived')return this.response(row,event);
   if(kind==='Network.dataReceived'){this.observe(row,event,kind);assert.ok(row.responseSeen&&row.terminal===null,'Original data metadata lifecycle');assert.ok(Number.isInteger(event.dataLength)&&event.dataLength>=0&&Number.isFinite(event.encodedDataLength)&&event.encodedDataLength>=0);return;}
   if(kind==='Network.loadingFinished')return this.finished(row,event);
   if(kind==='Network.loadingFailed'){this.observe(row,event,kind);assert.equal(row.terminal,null,'Duplicate original terminal');row.terminal='failed';this.poison(row,Error('Original CDP loadingFailed'));}
  }catch(error){this.poison(this.rows.get(event?.requestId),error);}
 }
 start(event){
  const address=new URL(event.request.url);assert.ok(!this.rows.has(event.requestId),'Reused or redirected original requestId');
  if(!address.pathname.startsWith('/api/dataset/thumbnail/'))return;
  assert.ok(this.rows.size<MAX_RECORDS,'New finite CDP metadata record budget exhausted');let finish;
  const terminal=new Promise(resolve=>{finish=resolve;});const row={id:event.requestId,epoch:this.epoch,url:event.request.url,method:event.request.method,frameId:event.frameId,loaderId:event.loaderId,documentURL:event.documentURL,timestamp:event.timestamp,wallTime:event.wallTime,events:0,eventRecords:[],lastByKind:{},cached:false,responseSeen:false,terminal:null,finish,claims:new Set()};this.rows.set(row.id,row);
  row.task=this.owner.run('original CDP metadata terminal '+row.id,async()=>{await terminal;if(row.error)throw row.error;assert.equal(row.terminal,'finished');assert.ok(row.responseSeen&&!row.cached&&!row.fromDiskCache&&!row.fromPrefetchCache,'Original metadata cache or response unavailable');return row;});
  this.observe(row,event,'Network.requestWillBeSent');assert.ok(typeof row.id==='string'&&row.id);assert.equal(address.origin,this.origin);assert.equal(row.method,'GET');assert.equal(event.type,'Fetch');assert.equal(row.frameId,this.mainFrameId);assert.ok(typeof row.loaderId==='string'&&row.loaderId&&typeof row.documentURL==='string'&&row.documentURL);assert.equal(event.redirectResponse,undefined);
  assert.ok(Number.isFinite(row.timestamp)&&row.timestamp>=0&&Number.isFinite(row.wallTime)&&row.wallTime*1000>=this.wallStart,'Original request before pre-arm');assert.equal(address.searchParams.getAll('file_path').length,1);assert.ok(path.isAbsolute(address.searchParams.get('file_path')||''));
  const h=lower(event.request.headers);row.context=context(h['x-vision-context']);row.project=h['x-vision-project'];assert.equal(row.project,row.context.project_id);this.notify();
 }
 response(row,event){
  this.observe(row,event,'Network.responseReceived');assert.equal(row.terminal,null);assert.equal(row.responseSeen,false,'Duplicate original response');assert.equal(event.frameId,row.frameId);assert.equal(event.loaderId,row.loaderId);assert.equal(event.type,'Fetch');
  const response=event.response;assert.equal(response.url,row.url);assert.equal(response.status,200);assert.notEqual(response.fromServiceWorker,true);const h=lower(response.headers);const echo=context(h['x-vision-context']);assert.deepEqual(echo,row.context);assert.match(h['content-type']||'',/^image\/jpeg(?:;|$)/i);assert.ok(!h['content-encoding']||h['content-encoding']==='identity');
  let length=null;if(h['content-length']!==undefined){assert.match(h['content-length'],/^\d+$/);length=Number(h['content-length']);assert.ok(Number.isSafeInteger(length)&&length>0&&length<=MAX_DECLARED_BODY);}
  row.relative=vector(response.timing,CDP_FIELDS);row.requestTime=response.timing.requestTime;assert.ok(Number.isFinite(row.requestTime));row.computedAbsolute=(row.requestTime-row.timestamp+row.wallTime)*1000;
  Object.assign(row,{responseSeen:true,status:200,echo,mime:h['content-type'],contentLength:length,fromDiskCache:Boolean(response.fromDiskCache),fromPrefetchCache:Boolean(response.fromPrefetchCache)});assert.ok(!row.cached&&!row.fromDiskCache&&!row.fromPrefetchCache,'Original cached metadata cannot qualify');this.notify();
 }
 finished(row,event){
  this.observe(row,event,'Network.loadingFinished');assert.equal(row.terminal,null,'Duplicate original terminal');assert.ok(row.responseSeen,'Original terminal lacks response');assert.ok(Number.isFinite(event.encodedDataLength)&&event.encodedDataLength>=0);row.terminal='finished';row.finishedTimestamp=event.timestamp;row.wireBytes=event.encodedDataLength;row.finish();this.notify();
 }
 same(row,original){return row.url===original.url&&row.method===original.method&&row.project===original.project&&contextKey(row.context)===contextKey(original.context)&&row.documentURL===original.documentURL;}
 candidate(original){
  assert.equal(original.epoch,this.epoch);assert.equal(original.mainFrame,true);assert.equal(original.resourceType,'fetch');assert.ok(original.request&&typeof original.request==='object');const relative=vector(original.publicTiming,PUBLIC_FIELDS);
  const candidates=[...this.rows.values()].filter(row=>row.responseSeen&&this.same(row,original));const exact=candidates.filter(row=>row.relative.every((value,index)=>value===relative[index]));
  if(!this.associations.has(original.request))assert.ok(this.associations.size<MAX_RECORDS,'New finite original association budget exhausted');this.associations.set(original.request,{relative:[...relative],absolute:original.publicTiming.startTime??null,candidates:candidates.map(row=>({requestId:row.id,relative:[...row.relative],absolute:row.computedAbsolute}))});
  assert.ok(exact.length<=1,'Ambiguous original relative timing identities');if(!exact.length){if(candidates.length)throw Error('Original relative timing mismatch');return null;}
  const row=exact[0],prior=this.claims.get(original.request);if(prior!==undefined)assert.equal(prior,row.id);assert.ok(row.claims.size===0||row.claims.has(original.request),'Metadata claimed by another original Request');row.claims.add(original.request);this.claims.set(original.request,row.id);return row;
 }
 async join(original){
  const row=await new Promise((resolve,reject)=>{const wake=()=>{try{if(this.errors.length)throw this.errors[0].error;const row=this.candidate(original);if(row){this.waiters.delete(wake);resolve(row);}else if(this.stopped){this.waiters.delete(wake);reject(Error('Original metadata association unresolved'));}}catch(error){this.waiters.delete(wake);reject(error);}};this.waiters.add(wake);wake();});
  await row.task.done;if(row.task.state==='rejected')throw row.task.error;assert.ok(row.task.completed<=this.deadline,'Original metadata completed after deadline');
  return {proof:{observation:'passive-CDP-original-metadata-only',body_source:false,epoch:row.epoch,requestId:row.id,frameId:row.frameId,loaderId:row.loaderId,documentURL:row.documentURL,url:row.url,method:row.method,project:row.project,context:{...row.context},echo:{...row.echo},status:row.status,mime:row.mime,content_length:row.contentLength,relative_timing:[...row.relative],relative_fields:[...PUBLIC_FIELDS],exact_relative_timing_join:true,absolute_start_equality_claim:false,absolute_public_start:original.publicTiming.startTime??null,absolute_CDP_start:row.computedAbsolute,loadingFinished:true,loading_failed:false,HTTP_refetch:false,Response_body_called:false,stream_body_command_called:false,Request_object_retained:true}};
 }
 validateFrameTree(frame,originalRows){
  if(this.errors.length)throw this.errors[0].error;assert.equal(frame.id,this.mainFrameId);assert.ok(frame.loaderId&&frame.url);assert.equal(this.rows.size,originalRows.length,'Every original metadata row needs one actual Request');
  const requests=new Set(originalRows.map(original=>original.request));assert.equal(requests.size,originalRows.length,'Duplicate final original Request object');assert.equal(requests.size,this.claims.size,'Final original Request set differs from actual claims');
  for(const [request,id]of this.claims){assert.ok(requests.has(request),'Claimed original Request omitted from final set');assert.ok(this.rows.get(id)?.claims.has(request),'Original Request claim has no matching metadata row');}
  for(const row of this.rows.values()){assert.equal(row.loaderId,frame.loaderId);assert.equal(row.documentURL,frame.url+(frame.urlFragment||''));assert.equal(row.claims.size,1);assert.equal(row.terminal,'finished');assert.equal(row.task.state,'fulfilled');assert.equal(row.error,undefined);assert.ok(!row.cached&&!row.fromDiskCache&&!row.fromPrefetchCache);}
  for(const row of this.rows.values())for(const request of row.claims)assert.equal(this.claims.get(request),row.id,'Metadata row claim differs from original Request map');
 }
 diagnostic(){return {schema:'modu-vision.original-CDP-metadata-diagnostic/v1',epoch:this.epoch,deadline:this.deadline,stopped:this.stopped,body_source:false,rows:[...this.rows.values()].map(row=>({requestId:row.id,route:new URL(row.url).pathname,URL_SHA256:digest(row.url),context_SHA256:row.context?digest(contextKey(row.context)):null,request_timestamp:row.timestamp,request_wallTime:row.wallTime,relative:row.relative?[...row.relative]:null,absolute_CDP_start:row.computedAbsolute??null,terminal:row.terminal,task_state:row.task.state,events:row.eventRecords.map(event=>({...event}))})),associations:[...this.associations.values()].map(row=>({...row,relative:[...row.relative],candidates:row.candidates.map(value=>({...value,relative:[...value.relative]}))})),no_raw_headers_tokens_or_query_paths:true};}
 disconnect(){for(const row of this.rows.values())this.poison(row,Error('Original CDP metadata session disconnected'));if(!this.rows.size)this.poison(null,Error('Original CDP metadata session disconnected'));this.stopped=true;this.notify();}
 seal(){this.stopped=true;for(const row of this.rows.values())if(row.terminal===null&&!row.error)this.poison(row,Error('Original metadata terminal unresolved at seal'));this.notify();}
}
function createPassiveOriginalThumbnailIdentity(page,owner,{epoch,origin,wallStart,deadline}){
 let session=null,model=null,bootstrapError=null,expectedDetach=false,detachRow=null;const listeners=[];
 const run=(label,operation)=>owner.wait(owner.run(label,operation),deadline);
 const onClose=()=>{if(!expectedDetach){bootstrapError=Error('Original owned CDP metadata disconnected');if(model)model.disconnect();else owner.run('metadata pre-arm disconnect',()=>{throw bootstrapError;});}};
 const cleanup=async()=>{
  if(!session)throw bootstrapError||Error('Original metadata session unavailable');
  if(!detachRow){if(model)model.seal();for(const [name,fn]of listeners)session.off(name,fn);expectedDetach=true;detachRow=owner.run('detach original metadata session',async()=>{try{await session.detach();return {detached:true};}finally{session.off('close',onClose);}});}
  return owner.wait(detachRow,deadline);
 };
 return {
  async start(){
   await run('create original metadata session',async()=>{session=await page.context().newCDPSession(page);session.on('close',onClose);for(const kind of ['Network.requestWillBeSent','Network.requestServedFromCache','Network.responseReceived','Network.dataReceived','Network.loadingFinished','Network.loadingFailed']){const fn=event=>{if(model)model.event(kind,event);else{bootstrapError=Error('Original metadata event before frame binding');owner.run('premature original metadata',()=>{throw bootstrapError;});}};session.on(kind,fn);listeners.push([kind,fn]);}return true;});
   const tree=await run('original metadata pre-arm main frame',()=>session.send('Page.getFrameTree'));assert.ok(tree.frameTree&&!tree.frameTree.frame.parentId);assert.equal(tree.frameTree.frame.url+(tree.frameTree.frame.urlFragment||''),page.url());model=new OriginalThumbnailIdentity({owner,epoch,origin,mainFrameId:tree.frameTree.frame.id,wallStart,deadline});await run('enable original metadata observation',()=>session.send('Network.enable',{}));if(bootstrapError)throw bootstrapError;return true;
  },
  async join(original){assert.ok(model);if(bootstrapError)throw bootstrapError;return model.join(original);},
  async finish(originalRows){assert.ok(model);await owner.drain(deadline);if(bootstrapError)throw bootstrapError;if(owner.failures().length)throw owner.failures()[0].error;const tree=await run('original metadata document barrier',()=>session.send('Page.getFrameTree'));model.validateFrameTree(tree.frameTree.frame,originalRows);await cleanup();await owner.drain(deadline);if(owner.failures().length)throw owner.failures()[0].error;return {original_CDP_metadata_detached:true};},
  cleanup,diagnostic(){return model?model.diagnostic():{epoch,prearm_failed:true,body_source:false};},
 };
}
module.exports={OriginalThumbnailIdentity,createPassiveOriginalThumbnailIdentity,PUBLIC_FIELDS,CDP_FIELDS};
