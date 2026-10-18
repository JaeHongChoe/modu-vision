'use strict';
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const LIMIT=1024*1024,MAX_REQUESTS=64,MAX_EVENTS=128;
const hash=raw=>crypto.createHash('sha256').update(raw).digest('hex');
const headers=value=>Object.fromEntries(Object.entries(value||{}).map(([key,v])=>[key.toLowerCase(),v]));
const context=value=>{assert.ok(typeof value==='string'&&value.length>0);const c=JSON.parse(value);assert.ok(c&&['workspace_id','project_id','actor_id'].every(k=>typeof c[k]==='string'&&c[k])&&c.mode==='local');return {workspace_id:c.workspace_id,project_id:c.project_id,actor_id:c.actor_id,mode:c.mode};};
const contextKey=value=>JSON.stringify([value.workspace_id,value.project_id,value.actor_id,value.mode]);
function decode(value){
  assert.ok(typeof value==='string'&&value.length<=4*Math.ceil(LIMIT/3),'Original base64 body bound');
  assert.equal(value.length%4,0,'Canonical base64');
  assert.match(value,/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/);
  const bytes=Buffer.from(value,'base64');assert.equal(bytes.toString('base64'),value);assert.ok(bytes.length<=LIMIT);return bytes;
}
class OriginalThumbnailProtocol {
  constructor({owner,send,epoch,origin,mainFrameId,wallStart,deadline,clock=()=>performance.now()}){
    assert.ok(owner&&typeof send==='function');Object.assign(this,{owner,send,epoch,origin,mainFrameId,wallStart,deadline,clock});
    this.rows=new Map();this.errors=[];this.waiters=new Set();this.claims=new Map();this.associations=new Map();this.stopped=false;
  }
  poison(row,error){
    if(row&&!row.error)row.error=error;
    if(!row||!row.errorTask){const errorTask=this.owner.run('sticky original CDP failure '+(row?.id||'capture'),()=>{throw error;});if(row)row.errorTask=errorTask;this.errors.push({requestId:row?.id||null,error,observed:this.clock()});}
    if(row)this.progress(row);this.notify();
  }
  observe(row,timestamp,kind='stream-ack'){
    assert.equal(this.stopped,false,'CDP event after original capture seal');
    assert.ok(this.clock()<=this.deadline,'CDP event after original absolute deadline');
    if(row){row.events++;assert.ok(row.events<=MAX_EVENTS,'New finite CDP event retention exhausted');if(kind.startsWith('Network.'))row.eventRecords.push({kind,timestamp:timestamp??null,observed:this.clock()});}
    // Protocol timestamps describe different lifecycle observations; callback
    // delivery across event types is not a chronological-order guarantee.
    // Keep the original request-start lower bound and same-type monotonicity.
    if(timestamp!==undefined){assert.ok(Number.isFinite(timestamp)&&timestamp>=0);if(row){assert.ok(timestamp>=row.timestamp,'Original CDP event precedes request start');const prior=row.lastByKind[kind];if(prior!==undefined)assert.ok(timestamp>=prior,'Out-of-order same-type original CDP event');row.lastByKind[kind]=timestamp;}}
  }
  event(method,event){
    try{
      if(method==='Network.requestWillBeSent')return this.start(event);
      const row=this.rows.get(event.requestId);if(!row)return;
      if(method==='Network.requestServedFromCache'){this.observe(row,undefined,'Network.requestServedFromCache');row.cached=true;return;}
      if(method==='Network.responseReceived')return this.response(row,event);
      if(method==='Network.dataReceived')return this.data(row,event);
      if(method==='Network.loadingFinished')return this.finished(row,event);
      if(method==='Network.loadingFailed')return this.failed(row,event);
    }catch(error){this.poison(this.rows.get(event.requestId),error);}
  }
  start(event){
    const url=new URL(event.request.url),prior=this.rows.get(event.requestId);
    if(prior)throw Error('Reused or redirected original CDP requestId');
    if(!url.pathname.startsWith('/api/dataset/thumbnail/'))return;
    assert.ok(this.rows.size<MAX_REQUESTS,'New finite CDP request retention exhausted');
    let finish;
    const terminal=new Promise(resolve=>{finish=resolve;});
    const row={id:event.requestId,epoch:this.epoch,url:event.request.url,method:event.request.method,frameId:event.frameId,loaderId:event.loaderId,documentURL:event.documentURL,timestamp:event.timestamp,wallTime:event.wallTime,lastByKind:{},events:0,eventRecords:[],cached:false,chunks:[],dataBytes:0,metadataBytes:0,streamDone:false,streamRequested:false,terminal:null,finish,claims:new Set(),observed:this.clock()};
    this.rows.set(row.id,row);
    row.task=this.owner.run('original CDP complete body '+row.id,async()=>{await terminal;if(row.error)throw row.error;const body=this.buildBody(row);row.body=body;return row;});
    this.observe(row,event.timestamp,'Network.requestWillBeSent');
    assert.equal(url.origin,this.origin);assert.equal(row.method,'GET');assert.equal(event.type,'Fetch');
    assert.equal(row.frameId,this.mainFrameId);assert.ok(typeof row.loaderId==='string'&&row.loaderId);
    assert.ok(typeof row.documentURL==='string'&&row.documentURL);assert.equal(event.redirectResponse,undefined);
    assert.ok(Number.isFinite(row.wallTime)&&row.wallTime*1000>=this.wallStart,'CDP Request began before original pre-arm');
    assert.ok(typeof row.id==='string'&&row.id);assert.equal(url.searchParams.getAll('file_path').length,1);
    const h=headers(event.request.headers);row.project=h['x-vision-project'];row.context=context(h['x-vision-context']);assert.equal(row.context.project_id,row.project);
    this.arm(row);this.notify();
  }
  response(row,event){
    this.observe(row,event.timestamp,'Network.responseReceived');assert.equal(row.terminal,null);assert.equal(row.response,undefined,'Duplicate original CDP response');
    assert.equal(event.loaderId,row.loaderId);assert.equal(event.type,'Fetch');assert.equal(event.frameId,row.frameId);
    const response=event.response;row.response=response;assert.equal(response.url,row.url);assert.equal(response.status,200);
    assert.notEqual(response.fromServiceWorker,true,'Worker response cannot prove original main document');
    const h=headers(response.headers);row.echo=context(h['x-vision-context']);assert.deepEqual(row.echo,row.context);
    assert.match(h['content-type']||'',/^image\/jpeg(?:;|$)/i);assert.ok(!h['content-encoding']||h['content-encoding']==='identity','Encoded original body is ambiguous');
    if(h['content-length']!==undefined){assert.match(h['content-length'],/^\d+$/);row.contentLength=Number(h['content-length']);assert.ok(row.contentLength>0&&row.contentLength<=LIMIT);}
    row.mime=h['content-type'];row.fromDiskCache=Boolean(response.fromDiskCache);row.fromPrefetchCache=Boolean(response.fromPrefetchCache);
    const t=response.timing;
    row.timingCacheBranch=Boolean(row.cached||!t);row.publicStart=row.timingCacheBranch?row.wallTime*1000:(t.requestTime-row.timestamp+row.wallTime)*1000;
    assert.ok(Number.isFinite(row.publicStart));this.notify();
  }
  arm(row){
    assert.equal(row.streamRequested,false);row.streamRequested=true;
    row.streamTask=this.owner.run('original CDP stream command '+row.id,async()=>{
      try{const reply=await this.send('Network.streamResourceContent',{requestId:row.id});this.observe(row);row.prefix=decode(reply.bufferedData);row.acknowledged=true;row.streamDone=true;assert.ok(row.prefix.length+row.dataBytes<=LIMIT);this.progress(row);this.notify();return true;}
      catch(error){row.streamDone=true;this.poison(row,error);this.progress(row);throw error;}
    });
    this.notify();
  }
  data(row,event){
    this.observe(row,event.timestamp,'Network.dataReceived');assert.equal(row.terminal,null,'Late data after original terminal');
    assert.ok(row.response,'Data lacks original response');assert.ok(Number.isInteger(event.dataLength)&&event.dataLength>=0);assert.ok(Number.isFinite(event.encodedDataLength)&&event.encodedDataLength>=0);
    row.metadataBytes+=event.dataLength;assert.ok(row.metadataBytes<=LIMIT,'Original stream exceeds 1MiB');
    if(event.data!==undefined){const raw=decode(event.data);assert.equal(raw.length,event.dataLength);row.dataBytes+=raw.length;assert.ok(row.dataBytes+(row.prefix?.length||0)<=LIMIT);row.chunks.push(raw);}
    else if(row.acknowledged&&event.dataLength>0)throw Error('Original streaming data missing after acknowledgement');
  }
  finished(row,event){
    this.observe(row,event.timestamp,'Network.loadingFinished');assert.equal(row.terminal,null,'Duplicate original terminal');assert.ok(Number.isFinite(event.encodedDataLength)&&event.encodedDataLength>=0);row.terminal='finished';row.finishedTimestamp=event.timestamp;row.wireBytes=event.encodedDataLength;row.terminalObserved=this.clock();this.progress(row);this.notify();
  }
  failed(row,event){
    this.observe(row,event.timestamp,'Network.loadingFailed');assert.equal(row.terminal,null,'Duplicate original terminal');row.terminal='failed';row.terminalObserved=this.clock();this.poison(row,Error('Original CDP loadingFailed; cancelled='+Boolean(event.canceled)));this.progress(row);
  }
  progress(row){if(row.error||(row.terminal&&(row.streamDone||!row.streamRequested)))row.finish();}
  buildBody(row){
    assert.equal(row.terminal,'finished');assert.ok(row.response&&row.streamDone&&row.acknowledged);assert.equal(row.error,undefined);
    // The command prefix precedes all streamed payloads even when its reply
    // arrives after those data events. No replacement HTTP body is read.
    const body=Buffer.concat([row.prefix,...row.chunks]);
    assert.ok(body.length>0&&body.length<=LIMIT,'Complete original passive stream body bound');
    assert.equal(body.length,row.metadataBytes,'Every original decoded byte is accounted for');
    if(row.contentLength!==undefined)assert.equal(body.length,row.contentLength);
    return body;
  }
  publicMatch(row,original){
    return row.url===original.url&&row.method===original.method&&row.project===original.project&&contextKey(row.context||{})===contextKey(original.context||{})&&row.documentURL===original.documentURL;
  }
  candidate(original){
    assert.equal(original.epoch,this.epoch);assert.equal(original.mainFrame,true);assert.equal(original.resourceType,'fetch');assert.ok(Number.isFinite(original.publicStart));
    const byIdentity=[...this.rows.values()].filter(row=>this.publicMatch(row,original)&&row.response);
    const exact=byIdentity.filter(row=>row.publicStart===original.publicStart);
    if(!this.associations.has(original.request))assert.ok(this.associations.size<MAX_REQUESTS,'New finite CDP association retention exhausted');
    this.associations.set(original.request,{public_start:original.publicStart,candidates:byIdentity.map(row=>({requestId:row.id,computed_public_start:row.publicStart,request_timestamp:row.timestamp,request_wallTime:row.wallTime,response_requestTime:row.response?.timing?.requestTime??null,cache_branch:row.timingCacheBranch}))});
    if(exact.length>1)throw Error('Ambiguous original CDP request IDs');
    if(exact.length===0){if(byIdentity.length)throw Error('Original public/CDP timing mismatch');return null;}
    const row=exact[0],claim=this.claims.get(original.request);
    if(claim!==undefined)assert.equal(claim,row.id);
    assert.ok(row.claims.size===0||row.claims.has(original.request),'Protocol body claimed by another original Request');
    row.claims.add(original.request);this.claims.set(original.request,row.id);return row;
  }
  diagnostic(){
    return {schema:'modu-vision.original-CDP-failure-diagnostic/v1',epoch:this.epoch,deadline:this.deadline,stopped:this.stopped,
      rows:[...this.rows.values()].map(row=>({requestId:row.id,request_timestamp:row.timestamp,request_wallTime:row.wallTime,response_requestTime:row.response?.timing?.requestTime??null,computed_public_start:row.publicStart??null,cache_branch:row.timingCacheBranch??null,status:row.response?.status??null,route:new URL(row.url).pathname,URL_SHA256:hash(Buffer.from(row.url)),context_SHA256:row.context?hash(Buffer.from(contextKey(row.context))):null,terminal:row.terminal,stream_requested:row.streamRequested,stream_done:row.streamDone,stream_ack:Boolean(row.acknowledged),body_task_state:row.task.state,stream_task_state:row.streamTask?.state??null,events:row.eventRecords.map(event=>({...event}))})),
      associations:[...this.associations.values()].map(row=>({...row,candidates:row.candidates.map(value=>({...value}))})),
      no_raw_headers_tokens_or_query_paths:true};
  }
  notify(){for(const wake of [...this.waiters])wake();}
  async join(original){
    const row=await new Promise((resolve,reject)=>{
      const wake=()=>{try{if(this.errors.length)throw this.errors[0].error;const found=this.candidate(original);if(found){this.waiters.delete(wake);resolve(found);}else if(this.stopped){this.waiters.delete(wake);reject(Error('Original protocol request association unresolved'));}}catch(error){this.waiters.delete(wake);reject(error);}};
      this.waiters.add(wake);wake();
    });
    await row.task.done;if(row.task.state==='rejected')throw row.task.error;
    assert.ok(row.task.completed<=this.deadline);assert.ok(row.body&&row.body.length>0);
    return {body:row.body,proof:{body_observation:'passive-CDP-original-stream',requestId:row.id,epoch:row.epoch,frameId:row.frameId,loaderId:row.loaderId,documentURL:row.documentURL,request_timestamp:row.timestamp,request_wallTime:row.wallTime,public_timing_start:original.publicStart,exact_public_timing_join:true,loadingFinished:true,loading_failed:false,stream_command_ack:true,prefix_bytes:row.prefix.length,chunk_count:row.chunks.length,decoded_metadata_bytes:row.metadataBytes,body_bytes:row.body.length,body_sha256:hash(row.body),mime:row.mime,content_length:row.contentLength??null,wire_encoded_bytes:row.wireBytes,cached:Boolean(row.cached||row.fromDiskCache||row.fromPrefetchCache),public_timing_cache_or_no_timing_branch:row.timingCacheBranch,requestServedFromCache:row.cached,fromDiskCache:row.fromDiskCache,fromPrefetchCache:row.fromPrefetchCache,HTTP_refetch:false,Request_object_retained:true,Playwright_Response_body_not_called:true}};
  }
  validateFrameTree(frame,originalRows){
    if(this.errors.length)throw this.errors[0].error;
    assert.equal(frame.id,this.mainFrameId);assert.ok(frame.loaderId&&frame.url);
    assert.equal(this.rows.size,originalRows.length,'Every observed original CDP request has an actual Page Request');
    for(const row of this.rows.values()){assert.equal(row.loaderId,frame.loaderId);assert.equal(row.documentURL,frame.url+(frame.urlFragment||''));assert.equal(row.claims.size,1);assert.equal(row.terminal,'finished');assert.equal(row.task.state,'fulfilled');assert.equal(row.error,undefined);}
    for(const original of originalRows)assert.ok(this.claims.has(original.request));
  }
  disconnect(){for(const row of this.rows.values()){row.terminal='disconnected';this.poison(row,Error('Original CDP session disconnected'));this.progress(row);}if(this.rows.size===0)this.poison(null,Error('Original CDP session disconnected'));this.stopped=true;this.notify();}
  seal(){this.stopped=true;this.notify();}
}
function createPassiveOriginalThumbnailStream(page,owner,{epoch,origin,wallStart,deadline}){
  let session=null,model=null,expectedDetach=false,detached=false,bootstrapError=null;
  const listeners=[];
  const run=(label,operation)=>owner.wait(owner.run(label,operation),deadline);
  const onClose=()=>{if(!expectedDetach){const error=Error('Original owned CDP session disconnected');bootstrapError=error;if(model)model.disconnect();else owner.run('CDP pre-arm disconnect',()=>{throw error;});}};
  const attach=actual=>{
    actual.on('close',onClose);
    for(const method of ['Network.requestWillBeSent','Network.requestServedFromCache','Network.responseReceived','Network.dataReceived','Network.loadingFinished','Network.loadingFailed']){
      const fn=event=>{if(model)model.event(method,event);else{bootstrapError=Error('Original CDP event before document binding');owner.run('CDP premature event',()=>{throw bootstrapError;});}};actual.on(method,fn);listeners.push([method,fn]);
    }
  };
  const cleanup=async()=>{
    if(detached)return {detached:true};
    if(!session){if(bootstrapError)throw bootstrapError;throw Error('Owned original CDP session not established');}
    if(model)model.seal();for(const [method,fn]of listeners)session.off(method,fn);
    expectedDetach=true;
    try{await run('detach original owned CDP session',()=>session.detach());detached=true;return {detached:true};}
    finally{session.off('close',onClose);}
  };
  return {
    async start(){
      await run('create original page CDP session',async()=>{session=await page.context().newCDPSession(page);attach(session);return true;});
      const tree=await run('original pre-arm main frame',()=>session.send('Page.getFrameTree'));
      assert.ok(tree.frameTree&&!tree.frameTree.frame.parentId);assert.equal(tree.frameTree.frame.url+(tree.frameTree.frame.urlFragment||''),page.url());
      model=new OriginalThumbnailProtocol({owner,send:(method,params)=>session.send(method,params),epoch,origin,mainFrameId:tree.frameTree.frame.id,wallStart,deadline});
      await run('enable original CDP network observation',()=>session.send('Network.enable',{}));
      if(bootstrapError)throw bootstrapError;return true;
    },
    async join(original){assert.ok(model);if(bootstrapError)throw bootstrapError;return model.join(original);},
    diagnostic(){return model?model.diagnostic():{epoch,prearm_failed:true};},
    async finish(originalRows){
      assert.ok(model);await owner.drain(deadline);if(bootstrapError)throw bootstrapError;
      const tree=await run('original current document protocol barrier',()=>session.send('Page.getFrameTree'));model.validateFrameTree(tree.frameTree.frame,originalRows);
      await cleanup();await owner.drain(deadline);if(owner.failures().length)throw owner.failures()[0].error;return {original_CDP_stream_detached:true};
    },
    cleanup,
  };
}
module.exports={OriginalThumbnailProtocol,createPassiveOriginalThumbnailStream};
