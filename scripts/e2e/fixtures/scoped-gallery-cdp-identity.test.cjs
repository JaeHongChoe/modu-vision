'use strict';
// Pure metadata/event controls. No browser, CDP, app, HTTP or process is started.
const test=require('node:test'),assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const baseline=process.env.MODU_CDP_IDENTITY_LEGACY==='1';
const {OwnedMediaTasks}=require('./scoped-gallery-media-witness.cjs');
const api=baseline?null:require('./scoped-gallery-cdp-identity.cjs');
const origin='http://127.0.0.1:9',documentURL='file:///owned/index.html';
const context={workspace_id:'w',project_id:'p',actor_id:'a',mode:'local'};
const url=origin+'/api/dataset/thumbnail/preview?file_path=%2Fowned%2Fa.png';
const headers={'X-Vision-Project':'p','X-Vision-Context':JSON.stringify(context)};
const timing={requestTime:10.125,dnsStart:-1,dnsEnd:-1,connectStart:0,connectEnd:.05,sslStart:-1,sendStart:.15,receiveHeadersEnd:1.25};
const publicTiming={startTime:1000125.006,domainLookupStart:-1,domainLookupEnd:-1,connectStart:0,connectEnd:.05,secureConnectionStart:-1,requestStart:.15,responseStart:1.25};
const requestEvent=(id='id')=>({requestId:id,frameId:'main',loaderId:'loader',documentURL,timestamp:10,wallTime:1000,type:'Fetch',request:{url,method:'GET',headers}});
const responseEvent=(id='id')=>({requestId:id,frameId:'main',loaderId:'loader',timestamp:12,type:'Fetch',response:{url,status:200,headers:{...headers,'Content-Type':'image/jpeg','Content-Length':'4'},timing:{...timing}}});
const original=(over={})=>({request:{actualOriginalRequest:true},epoch:1,mainFrame:true,resourceType:'fetch',documentURL,url,method:'GET',project:'p',context:{...context},publicStart:publicTiming.startTime,publicTiming:{...publicTiming},...over});
function fixture({deadline=performance.now()+1000,clock}={}){
 const owner=new OwnedMediaTasks();
 const model=new api.OriginalThumbnailIdentity({owner,epoch:1,origin,mainFrameId:'main',wallStart:999999,deadline,...(clock?{clock}:{})});
 const emit=(name,event)=>model.event('Network.'+name,event);
 return {model,owner,deadline,emit,start(id='id'){emit('requestWillBeSent',requestEvent(id));},response(id='id'){emit('responseReceived',responseEvent(id));},finish(id='id'){emit('loadingFinished',{requestId:id,timestamp:11.75,encodedDataLength:100});}};
}
const focused=(name,fn)=>test(name,{skip:baseline},fn);

test('exact relative vector owns the original Request despite differing absolute session starts',async()=>{
 if(baseline){
  // Run the real old absolute guard, without invoking its body/stream methods.
  const {OriginalThumbnailProtocol}=require('./scoped-gallery-cdp-stream.cjs');
  const row={id:'id',url,method:'GET',project:'p',context:{...context},documentURL,response:{timing:{...timing}},publicStart:1000125,claims:new Set()};
  const old=Object.create(OriginalThumbnailProtocol.prototype);Object.assign(old,{epoch:1,rows:new Map([['id',row]]),claims:new Map(),associations:new Map()});
  assert.doesNotThrow(()=>old.candidate(original()),'Equal relative metadata must not require equal cross-session absolute clocks');
  return;
 }
 const f=fixture(),own=original();f.start();f.response();f.finish();
 const out=await f.model.join(own);assert.equal(out.body,undefined);assert.equal(out.proof.body_source,false);assert.equal(out.proof.exact_relative_timing_join,true);assert.equal(out.proof.absolute_start_equality_claim,false);
 assert.deepEqual(out.proof.relative_timing,[-1,-1,0,.05,-1,.15,1.25]);
 f.model.validateFrameTree({id:'main',loaderId:'loader',url:documentURL},[own]);await f.owner.drain(f.deadline);
});
focused('all seven relative fields are exact; degenerate, absent and cache vectors refuse',async()=>{
 for(const field of Object.keys(publicTiming).filter(k=>k!=='startTime')){
  const f=fixture();f.start();f.response();f.finish();const own=original();own.publicTiming[field]+=.000001;await assert.rejects(f.model.join(own),/relative timing/);await f.owner.drain(f.deadline);
 }
 for(const kind of ['absent','degenerate','cache']){
  const f=fixture();f.start();const r=responseEvent();if(kind==='absent')delete r.response.timing;if(kind==='degenerate')r.response.timing={requestTime:10,...Object.fromEntries(Object.keys(timing).filter(k=>k!=='requestTime').map(k=>[k,-1]))};if(kind==='cache')f.emit('requestServedFromCache',{requestId:'id'});f.emit('responseReceived',r);f.finish();await assert.rejects(f.model.join(original()));await f.owner.drain(f.deadline);
 }
});
focused('whole tuple, actual Request identity and whole-set multiplicity cannot be substituted',async()=>{
 for(const over of [{epoch:2},{mainFrame:false},{resourceType:'image'},{documentURL:'file:///foreign.html'},{url:url+'&foreign=1'},{method:'POST'},{project:'foreign'},{context:{...context,actor_id:'foreign'}}]){
  const f=fixture();f.start();f.response();f.finish();f.model.seal();await assert.rejects(f.model.join(original(over)));await f.owner.drain(f.deadline);
 }
 const f=fixture();for(const id of ['id1','id2']){f.start(id);f.response(id);f.finish(id);}await assert.rejects(f.model.join(original()),/Ambiguous/);await f.owner.drain(f.deadline);
 const g=fixture(),first=original();g.start();g.response();g.finish();await g.model.join(first);await assert.rejects(g.model.join(original()),/another original Request/);assert.throws(()=>g.model.validateFrameTree({id:'main',loaderId:'loader',url:documentURL},[]));await g.owner.drain(g.deadline);
});
focused('final original Request object set is unique and equals both actual metadata claims',async()=>{
 const f=fixture(),first=original(),secondURL=url.replace('a.png','b.png'),second=original({url:secondURL});
 f.start('first');f.response('first');f.finish('first');
 const start=requestEvent('second');start.request.url=secondURL;f.emit('requestWillBeSent',start);
 const response=responseEvent('second');response.response.url=secondURL;f.emit('responseReceived',response);f.finish('second');
 await f.model.join(first);await f.model.join(second);
 const frame={id:'main',loaderId:'loader',url:documentURL};
 assert.doesNotThrow(()=>f.model.validateFrameTree(frame,[first,second]));
 assert.throws(()=>f.model.validateFrameTree(frame,[first,first]),/Request/);
 await f.owner.drain(f.deadline);
});
focused('original scope, redirect, response echo and settled main document remain strict',async()=>{
 for(const kind of ['origin','frame','loader','redirect','context','status','echo','worker']){
  const f=fixture(),s=requestEvent();if(kind==='origin')s.request.url='http://127.0.0.1:8/api/dataset/thumbnail/preview?file_path=%2Fowned%2Fa.png';if(kind==='frame')s.frameId='foreign';if(kind==='redirect')s.redirectResponse={status:302};if(kind==='context')s.request.headers={...headers,'X-Vision-Context':JSON.stringify({...context,actor_id:''})};f.emit('requestWillBeSent',s);
  const r=responseEvent();if(kind==='loader')r.loaderId='other';if(kind==='status')r.response.status=403;if(kind==='echo')r.response.headers['X-Vision-Context']=JSON.stringify({...context,actor_id:'other'});if(kind==='worker')r.response.fromServiceWorker=true;f.emit('responseReceived',r);f.finish();await assert.rejects(f.model.join(original()));await f.owner.drain(f.deadline);
 }
 const f=fixture(),own=original();f.start();f.response();f.finish();await f.model.join(own);for(const frame of [{id:'main',loaderId:'other',url:documentURL},{id:'main',loaderId:'loader',url:'file:///foreign.html'}])assert.throws(()=>f.model.validateFrameTree(frame,[own]));await f.owner.drain(f.deadline);
});
focused('lifecycle and same-type clocks refuse late, backwards, failed and duplicate metadata',async()=>{
 for(const kind of ['before-response','pre-start','same-type','duplicate-response','duplicate-terminal','late-data','failed']){
  const f=fixture();f.start();if(kind==='before-response')f.emit('dataReceived',{requestId:'id',timestamp:10.5,dataLength:0,encodedDataLength:0});f.response();
  if(kind==='duplicate-response')f.response();if(kind==='pre-start')f.emit('dataReceived',{requestId:'id',timestamp:9,dataLength:0,encodedDataLength:0});
  if(kind==='same-type'){for(const timestamp of [11.5,11])f.emit('dataReceived',{requestId:'id',timestamp,dataLength:0,encodedDataLength:0});}
  if(kind==='failed')f.emit('loadingFailed',{requestId:'id',timestamp:11.5,canceled:true});else f.finish();if(kind==='duplicate-terminal')f.finish();if(kind==='late-data')f.emit('dataReceived',{requestId:'id',timestamp:13,dataLength:0,encodedDataLength:0});
  await assert.rejects(f.model.join(original()));await f.owner.drain(f.deadline);assert.ok(f.owner.failures().length);
 }
});
focused('known failure rejects promptly; missing terminal, late completion and disconnect never become completion',async()=>{
 const f=fixture();f.start();f.response();f.emit('loadingFailed',{requestId:'id',timestamp:11,canceled:false});await new Promise(resolve=>setImmediate(resolve));await f.owner.drain(f.deadline);assert.equal(f.model.rows.get('id').task.state,'rejected');await assert.rejects(f.model.join(original()));
 const g=fixture({deadline:performance.now()+15});g.start();g.response();await assert.rejects(g.owner.drain(g.deadline),error=>error.unresolved?.some(x=>x.includes('metadata')));g.model.disconnect();await g.owner.drain(performance.now()+100);await assert.rejects(g.model.join(original()));
 let now=performance.now();const h=fixture({clock:()=>now,deadline:now+50});h.start();h.response();now=h.deadline+1;h.finish();await assert.rejects(h.model.join(original()),/deadline/);await h.owner.drain(performance.now()+100);
});
focused('48-record and128-event budgets retain failure without retaining more metadata',async()=>{
 const f=fixture();for(let i=0;i<49;i++)f.start('id'+i);assert.equal(f.model.rows.size,48);await assert.rejects(f.model.join(original()));f.model.seal();await f.owner.drain(f.deadline);
 const g=fixture();g.start();g.response();for(let i=0;i<130;i++)g.emit('dataReceived',{requestId:'id',timestamp:11,dataLength:0,encodedDataLength:0});assert.ok(g.model.rows.get('id').eventRecords.length<=128);await assert.rejects(g.model.join(original()));await g.owner.drain(g.deadline);
});
focused('factory sends metadata commands only, binds file main document and detaches exactly once',async()=>{
 const owner=new OwnedMediaTasks(),session=new EventEmitter(),sent=[];let detached=0;
 session.send=async(method)=>{sent.push(method);return method==='Page.getFrameTree'?{frameTree:{frame:{id:'main',loaderId:'loader',url:documentURL}}}:{};};session.detach=async()=>{detached++;};
 const page={url:()=>documentURL,context:()=>({newCDPSession:async()=>session})},deadline=performance.now()+1000;
 const capture=api.createPassiveOriginalThumbnailIdentity(page,owner,{epoch:1,origin,wallStart:999999,deadline});await capture.start();
 session.emit('Network.requestWillBeSent',requestEvent());session.emit('Network.responseReceived',responseEvent());session.emit('Network.loadingFinished',{requestId:'id',timestamp:11.75,encodedDataLength:100});const own=original();await capture.join(own);await capture.finish([own]);await Promise.all([capture.cleanup(),capture.cleanup()]);
 assert.deepEqual(sent,['Page.getFrameTree','Network.enable','Page.getFrameTree']);assert.equal(detached,1);assert.equal(session.eventNames().length,0);await owner.drain(deadline);
});
focused('unfinished detach stays owned at original deadline and no retry or original HTTP cancellation is issued',async()=>{
 const owner=new OwnedMediaTasks(),session=new EventEmitter();let release,detached=0;session.send=async method=>method==='Page.getFrameTree'?{frameTree:{frame:{id:'main',loaderId:'loader',url:documentURL}}}:{};session.detach=()=>{detached++;return new Promise(resolve=>release=resolve);};
 const page={url:()=>documentURL,context:()=>({newCDPSession:async()=>session})},deadline=performance.now()+20;const capture=api.createPassiveOriginalThumbnailIdentity(page,owner,{epoch:1,origin,wallStart:999999,deadline});await capture.start();
 await assert.rejects(capture.cleanup(),/deadline/);assert.equal(detached,1);
 // A rounded timer can reject just before the absolute deadline. Complete the
 // retained detach only after that same deadline, so this control is truly late.
 while(performance.now()<=deadline)await new Promise(resolve=>setTimeout(resolve,1));
 release();await new Promise(resolve=>setImmediate(resolve));await assert.rejects(capture.cleanup(),/deadline/);assert.equal(detached,1);
});
