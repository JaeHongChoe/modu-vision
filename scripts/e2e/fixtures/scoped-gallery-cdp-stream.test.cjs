'use strict';
// Pure protocol/event models only. No CDP, browser, HTTP, app or server starts.
const test=require('node:test'),assert=require('node:assert/strict');
const {OwnedMediaTasks}=require('./scoped-gallery-media-witness.cjs');
const {OriginalThumbnailProtocol}=require('./scoped-gallery-cdp-stream.cjs');
const context={workspace_id:'w',project_id:'p',actor_id:'a',mode:'local'},origin='http://127.0.0.1:9',url=origin+'/api/dataset/thumbnail/a.png?file_path=%2Fowned%2Fa.png';
const h={'X-Vision-Project':'p','X-Vision-Context':JSON.stringify(context)};
const start=(id='id')=>({requestId:id,loaderId:'loader',documentURL:origin+'/index.html',frameId:'frame',timestamp:10,wallTime:1000,type:'Fetch',request:{url,method:'GET',headers:h}});
const response=(id='id')=>({requestId:id,loaderId:'loader',frameId:'frame',timestamp:11,type:'Fetch',response:{url,status:200,fromServiceWorker:false,headers:{...h,'Content-Length':'4','Content-Type':'image/jpeg'},timing:{requestTime:10.125}}});
const original=(over={})=>({request:{actualOriginalRequest:true},url,method:'GET',project:'p',context,documentURL:origin+'/index.html',epoch:1,mainFrame:true,resourceType:'fetch',publicStart:1000125,...over});
function fixture({ack=()=>Promise.resolve({bufferedData:Buffer.from('AB').toString('base64')}),deadline=performance.now()+1000,clock}={}){
 const owner=new OwnedMediaTasks(),sent=[];
 const model=new OriginalThumbnailProtocol({owner,send:(method,params)=>{sent.push({method,params});return ack();},epoch:1,origin,mainFrameId:'frame',wallStart:999999,deadline,...(clock?{clock}: {})});
 const emit=(method,event)=>model.event('Network.'+method,event);
 return {owner,model,sent,deadline,emit,
 start(id='id'){emit('requestWillBeSent',start(id));},
 response(id='id'){emit('responseReceived',response(id));emit('dataReceived',{requestId:id,timestamp:11.5,dataLength:2,encodedDataLength:2});},
 chunk(id='id',over={}){emit('dataReceived',{requestId:id,timestamp:12,dataLength:2,encodedDataLength:2,data:Buffer.from('CD').toString('base64'),...over});},
 finish(id='id'){emit('loadingFinished',{requestId:id,timestamp:13,encodedDataLength:100});}};
}
test('observed empty Playwright body cannot substitute: original stream prefix plus chunk yields exact four original bytes even when acknowledgement arrives last',async()=>{
 let ack;const f=fixture({ack:()=>new Promise(resolve=>ack=resolve)});f.start();f.response();f.chunk();f.finish();ack({bufferedData:Buffer.from('AB').toString('base64')});
 const own=original(),out=await f.model.join(own);assert.equal(out.body.toString(),'ABCD');assert.equal(out.proof.prefix_bytes,2);assert.equal(out.proof.chunk_count,1);assert.equal(out.proof.HTTP_refetch,false);
 f.model.validateFrameTree({id:'frame',loaderId:'loader',url:origin+'/index.html'},[own]);
 assert.deepEqual(f.sent,[{method:'Network.streamResourceContent',params:{requestId:'id'}}]);await f.owner.drain(f.deadline);
});
test('exact nontrivial public timing formula is required; wallTime-only, nearest timing and cached-branch substitutions refuse',async()=>{
 for(const publicStart of [1000000,1000125.001,1000124.999]){
  const f=fixture();f.start();f.response();f.chunk();f.finish();await assert.rejects(f.model.join(original({publicStart})),/timing mismatch/);await f.owner.drain(f.deadline);
 }
 const f=fixture();f.start();f.emit('requestServedFromCache',{requestId:'id'});f.response();f.chunk();f.finish();
 const o=original({publicStart:1000000}),out=await f.model.join(o);assert.equal(out.body.toString(),'ABCD');assert.equal(out.proof.cached,true);await f.owner.drain(f.deadline);
});
test('late/overflow/missing chunks, noncanonical base64, duplicate terminal and current loadingFailed are sticky failures',async()=>{
 for(const kind of ['late','overflow','missing','base64','duplicate-terminal','failed']){
  const f=fixture();f.start();f.response();await new Promise(resolve=>setImmediate(resolve));
  if(kind==='overflow')f.chunk('id',{dataLength:1024*1024+1,data:undefined});
  else if(kind==='missing')f.chunk('id',{data:undefined});
  else if(kind==='base64')f.chunk('id',{data:'YQ==junk'});
  else f.chunk();
  f.finish();if(kind==='late')f.chunk('id',{timestamp:14});if(kind==='duplicate-terminal')f.finish();if(kind==='failed')f.emit('loadingFailed',{requestId:'id',timestamp:14,canceled:true,errorText:'CONTROL_ONLY'});
  await assert.rejects(f.model.join(original()));await f.owner.drain(f.deadline);assert.ok(f.owner.failures().length>0,kind);
 }
});
test('original redirects, reused IDs, wrong frame/loader/echo, compression and settled document mismatch refuse',async()=>{
 for(const kind of ['redirect','reused','frame','loader','echo','encoding']){
  const f=fixture(),s=start();if(kind==='redirect')s.redirectResponse={status:302};if(kind==='frame')s.frameId='foreign';f.emit('requestWillBeSent',s);if(kind==='reused')f.start();
  const r=response();if(kind==='loader')r.loaderId='other';if(kind==='echo')r.response.headers['X-Vision-Context']=JSON.stringify({...context,actor_id:'foreign'});if(kind==='encoding')r.response.headers['Content-Encoding']='gzip';f.emit('responseReceived',r);f.chunk();f.finish();
  await assert.rejects(f.model.join(original()));await f.owner.drain(f.deadline);
 }
 const f=fixture(),o=original();f.start();f.response();f.chunk();f.finish();await f.model.join(o);
 assert.throws(()=>f.model.validateFrameTree({id:'frame',loaderId:'foreign',url:origin+'/index.html'},[o]));assert.throws(()=>f.model.validateFrameTree({id:'frame',loaderId:'loader',url:origin+'/foreign'},[o]));
});
test('two exact CDP timing/URL/context identities cannot be guessed into one original Request',async()=>{
 const f=fixture();for(const id of ['id1','id2']){f.start(id);f.response(id);f.chunk(id);f.finish(id);}
 await assert.rejects(f.model.join(original()),/Ambiguous/);await f.owner.drain(f.deadline);
});
test('unexpected disconnect retains pending stream send; an unfinished original drain fails rather than reporting closure',async()=>{
 let refuse;const f=fixture({ack:()=>new Promise((_resolve,reject)=>refuse=reject)});f.start();f.response();f.model.disconnect();
 await assert.rejects(f.owner.drain(performance.now()+15),error=>error.unresolved?.some(s=>s.includes('CDP')));
 refuse(Error('model CDP disconnected'));await f.owner.drain(f.deadline);await assert.rejects(f.model.join(original()),/disconnect/);assert.ok(f.owner.failures().length>0);
});
test('missing loadingFinished or late event cannot renew the original frame or silently complete an owned body',async()=>{
 const f=fixture({deadline:performance.now()+20});f.start();f.response();f.chunk();
 await assert.rejects(f.owner.drain(f.deadline),error=>error.unresolved?.some(s=>s.includes('complete body')));
 f.model.disconnect();await f.owner.drain(performance.now()+100);await assert.rejects(f.model.join(original()));
 let now=performance.now();const g=fixture({deadline:now+100,clock:()=>now});g.start();g.response();g.chunk();now=g.deadline+1;g.finish();await assert.rejects(g.model.join(original()),/deadline/);g.model.disconnect();await g.owner.drain(performance.now()+100);
});


// Regression controls for Root-owned browser-empty-cdp-01 observer failures.
test('immediate arm sends one original stream command at validated Request start before any response event',async()=>{
 const f=fixture();f.start();
 assert.deepEqual(f.sent,[{method:'Network.streamResourceContent',params:{requestId:'id'}}]);
 f.response();f.chunk();f.finish();await f.model.join(original());await f.owner.drain(f.deadline);
 for(const kind of ['foreign-origin','foreign-frame','invalid-context']){const g=fixture(),s=start();if(kind==='foreign-origin')s.request.url=s.request.url.replace(origin,'http://127.0.0.1:8');if(kind==='foreign-frame')s.frameId='foreign';if(kind==='invalid-context')s.request.headers={...h,'X-Vision-Context':JSON.stringify({...context,project_id:'foreign'})};g.emit('requestWillBeSent',s);assert.deepEqual(g.sent,[],kind);await g.owner.drain(g.deadline);assert.ok(g.owner.failures().length,kind);}
});
test('sticky stream rejection without a later terminal event settles body ownership promptly and retains the command error',async()=>{
 const failure=Error('model original request already finished loading');const f=fixture({ack:()=>Promise.reject(failure),deadline:performance.now()+50});f.start();f.response();
 await new Promise(resolve=>setImmediate(resolve));
 try{await f.owner.drain(f.deadline);assert.equal(f.model.rows.get('id').task.state,'rejected');assert.ok(f.owner.failures().some(r=>r.error===failure));await assert.rejects(f.model.join(original()),/already finished/);}
 finally{f.model.disconnect();await f.owner.drain(performance.now()+100);}
});
test('response/data/finish timestamps may cross while same-type order, request-start and lifecycle remain strict',async()=>{
 const f=fixture({deadline:performance.now()+100});f.start();const r=response();r.timestamp=12;f.emit('responseReceived',r);f.emit('dataReceived',{requestId:'id',timestamp:11,dataLength:2,encodedDataLength:2});f.chunk('id',{timestamp:11.5});f.emit('loadingFinished',{requestId:'id',timestamp:11.75,encodedDataLength:100});
 try{const out=await f.model.join(original());assert.equal(out.body.toString(),'ABCD');await f.owner.drain(f.deadline);}
 finally{f.model.disconnect();await f.owner.drain(performance.now()+100);}
 for(const kind of ['pre-start','same-type-decrease']){const g=fixture();g.start();g.response();g.chunk();g.chunk('id',{timestamp:kind==='pre-start'?9:11.75});g.finish();await assert.rejects(g.model.join(original()));g.model.disconnect();await g.owner.drain(performance.now()+100);assert.ok(g.owner.failures().length);}
});
test('exact timing mismatch keeps finite raw numeric inputs and event kinds without weakening association',async()=>{
 const f=fixture();f.start();f.response();f.chunk();f.finish();const own=original({publicStart:1000125.001});await assert.rejects(f.model.join(own),/timing mismatch/);
 const d=f.model.diagnostic();assert.equal(d.rows[0].request_timestamp,10);assert.equal(d.rows[0].request_wallTime,1000);assert.equal(d.rows[0].response_requestTime,10.125);assert.equal(d.rows[0].computed_public_start,1000125);assert.equal(d.associations[0].public_start,1000125.001);assert.equal(d.associations[0].candidates[0].requestId,'id');assert.deepEqual(d.rows[0].events.map(e=>e.kind),['Network.requestWillBeSent','Network.responseReceived','Network.dataReceived','Network.dataReceived','Network.loadingFinished']);await f.owner.drain(f.deadline);
});
