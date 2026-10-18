'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {installOriginalBlobObservation}=require('./scoped-gallery-original-blob.cjs');
const ctx={workspace_id:'w',project_id:'p',actor_id:'a',mode:'local'},origin='http://127.0.0.1:9',url=origin+'/api/dataset/thumbnail/a.png?file_path=%2Fowned%2Fa.png',docURL=origin+'/index.html';
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function fixture({bytes='ABCD',deadline=Date.now()+1000,fetchGate,blobGate,BlobClass=Blob,createError,revokeError}={}){
 const calls={fetch:[],blob:[],clone:0,create:[],revoke:[]},doc={images:[]};doc.querySelectorAll=()=>doc.images;
 class NativeURL extends URL{}
 Object.defineProperty(NativeURL,'createObjectURL',{value:function(...args){if(createError)throw createError;const result=Reflect.apply(URL.createObjectURL,this,args);calls.create.push({receiver:this,args,result});return result;},writable:true,configurable:true});
 Object.defineProperty(NativeURL,'revokeObjectURL',{value:function(...args){calls.revoke.push({receiver:this,args});if(revokeError)throw revokeError;return Reflect.apply(URL.revokeObjectURL,this,args);},writable:true,configurable:true});
 class OriginalResponse{
  constructor(){this.url=url;this.status=200;this.headers=new Headers({'content-type':'image/jpeg','content-length':String(bytes.length),'x-vision-context':JSON.stringify(ctx)});this.bodyUsed=false;}
  blob(...args){calls.blob.push({receiver:this,args});this.bodyUsed=true;return blobGate?.promise||blobPromise;}
  clone(){calls.clone++;throw Error('Original Response must never be cloned');}
 }
 const blob=new BlobClass([bytes],{type:'image/jpeg'}),blobPromise=Promise.resolve(blob),response=new OriginalResponse(),fetchPromise=fetchGate?.promise||Promise.resolve(response);
 const realm={document:doc,location:{href:docURL},Response:OriginalResponse,Blob:BlobClass,URL:NativeURL,Headers,Promise,Uint8Array,Date,setTimeout,clearTimeout,btoa:value=>Buffer.from(value,'binary').toString('base64')};
 Object.defineProperty(realm,'fetch',{value:function(...args){calls.fetch.push({receiver:this,args});return fetchPromise;},writable:true,configurable:true,enumerable:true});
 const fetchDescriptor=Object.getOwnPropertyDescriptor(realm,'fetch'),blobDescriptor=Object.getOwnPropertyDescriptor(OriginalResponse.prototype,'blob'),createDescriptor=Object.getOwnPropertyDescriptor(NativeURL,'createObjectURL'),revokeDescriptor=Object.getOwnPropertyDescriptor(NativeURL,'revokeObjectURL');
 const config={key:'owned-original-blob-test',epoch:1,origin,documentURL:docURL,deadlineEpoch:deadline,limit:1024*1024,maxRecords:48};
 const state=installOriginalBlobObservation(realm,config),init={method:'GET',headers:new Headers({'x-vision-project':'p','x-vision-context':JSON.stringify(ctx)})};
 const identity={url,method:'GET',project:'p',context:ctx,echo:ctx,status:200,mime:'image/jpeg',contentLength:bytes.length,epoch:1,documentURL:docURL};
 return {realm,state,config,identity,init,response,blob,fetchPromise,blobPromise,calls,fetchDescriptor,blobDescriptor,createDescriptor,revokeDescriptor,doc};
}
async function application(f){const returned=f.realm.fetch.call(f.realm,url,f.init);assert.equal(returned,f.fetchPromise);const response=await returned;assert.equal(response,f.response);const body=response.blob();assert.equal(body,f.blobPromise);assert.equal(await body,f.blob);return response;}
test('same actual fetch and blob Promises/receivers/arguments yield the immutable app Blob bytes without clone, replacement or second consume',async()=>{
 const f=fixture();await application(f);const record=await f.state.read(f.identity,'actual-request-1');assert.equal(Buffer.from(record.base64,'base64').toString(),'ABCD');assert.equal(record.proof.actual_same_Response_and_app_Blob,true);
 assert.equal(f.calls.fetch.length,1);assert.equal(f.calls.fetch[0].receiver,f.realm);assert.equal(f.calls.fetch[0].args[1],f.init);assert.equal(f.calls.blob.length,1);assert.equal(f.calls.blob[0].receiver,f.response);assert.equal(f.calls.clone,0);const done=await f.state.sealAndDrain();assert.equal(done.drained,true);assert.deepEqual(Object.getOwnPropertyDescriptor(f.realm,'fetch'),f.fetchDescriptor);assert.deepEqual(Object.getOwnPropertyDescriptor(f.realm.Response.prototype,'blob'),f.blobDescriptor);
});
test('full original tuple, same document, epoch and one-to-one claim refuse changed, missing or duplicate owners',async()=>{
 const f=fixture();await application(f);for(const over of [{epoch:2},{documentURL:origin+'/other'},{project:'other'},{context:{...ctx,actor_id:'foreign'}},{url:url+'&other=1'},{contentLength:5}])await assert.rejects(f.state.read({...f.identity,...over},'foreign'));
 await f.state.read(f.identity,'first');await assert.rejects(f.state.read(f.identity,'second'));await f.state.sealAndDrain();
 const g=fixture();await application(g);await application(g);await assert.rejects(g.state.read(g.identity,'one'));await assert.rejects(g.state.sealAndDrain());
});
test('original transport/blob rejection stays the same app rejection and becomes a sticky observation failure',async()=>{
 for(const kind of ['fetch','blob']){const gate=deferred(),failure=Error('actual '+kind+' rejection'),f=fixture(kind==='fetch'?{fetchGate:gate}:{blobGate:gate});
  let original;if(kind==='fetch')original=f.realm.fetch(url,f.init);else{await f.realm.fetch(url,f.init);original=f.response.blob();}assert.equal(original,gate.promise);gate.reject(failure);await assert.rejects(original,error=>error===failure);await new Promise(resolve=>setImmediate(resolve));await assert.rejects(f.state.sealAndDrain());assert.ok(f.state.snapshot().failures.length);}
});
test('pending original work, body overflow and foreign descriptor changes cannot report successful closure',async()=>{
 const gate=deferred(),f=fixture({fetchGate:gate,deadline:Date.now()+20});f.realm.fetch(url,f.init);await assert.rejects(f.state.sealAndDrain(),error=>error.unresolved?.length>0);gate.resolve(f.response);await new Promise(resolve=>setImmediate(resolve));
 const g=fixture({bytes:'X'.repeat(1024*1024+1)});await application(g);await assert.rejects(g.state.read(g.identity,'one'));await assert.rejects(g.state.sealAndDrain());
 const h=fixture();const foreign=function foreignFetch(){};Object.defineProperty(h.realm,'fetch',{...Object.getOwnPropertyDescriptor(h.realm,'fetch'),value:foreign});await assert.rejects(h.state.sealAndDrain());assert.equal(h.realm.fetch,foreign);
});

test('owned immutable-Blob reader cancels/releases once; changed document and late Blob cannot become body evidence',async()=>{
 let unblock,cancelled=0,released=0;
 class HeldBlob extends Blob{get size(){return super.size;}get type(){return super.type;}stream(){return {getReader:()=>({read:()=>new Promise(resolve=>{unblock=resolve;}),cancel:()=>{cancelled++;unblock?.({done:true});return Promise.resolve();},releaseLock:()=>{released++;}})};}}
 const f=fixture({BlobClass:HeldBlob});await application(f);await new Promise(resolve=>setImmediate(resolve));const refused=assert.rejects(f.state.read(f.identity,'one'));await assert.rejects(f.state.sealAndDrain());await refused;assert.equal(cancelled,1);assert.equal(released,1);assert.equal(f.state.snapshot().local_readers,0);
 const g=fixture();await g.realm.fetch(url,g.init);g.realm.document={};assert.equal(await g.response.blob(),g.blob);await assert.rejects(g.state.read(g.identity,'one'));await assert.rejects(g.state.sealAndDrain());
 const gate=deferred(),h=fixture({blobGate:gate,deadline:Date.now()+20});await h.realm.fetch(url,h.init);const original=h.response.blob();await assert.rejects(h.state.sealAndDrain(),error=>error.unresolved?.some(label=>label.includes('blob Promise')));gate.resolve(h.blob);assert.equal(await original,h.blob);await new Promise(resolve=>setImmediate(resolve));assert.ok(h.state.snapshot().failures.length>0);
});
test('new48-record budget never blocks original invocations, and foreign descriptor flags/private state cannot be overwritten or blessed',async()=>{
 const f=fixture();const pending=[];for(let i=0;i<49;i++)pending.push(f.realm.fetch(url,f.init));await Promise.all(pending);assert.equal(f.calls.fetch.length,49);assert.equal(f.state.snapshot().records.length,48);await assert.rejects(f.state.sealAndDrain());
 const g=fixture();const changed={...Object.getOwnPropertyDescriptor(g.realm,'fetch'),enumerable:false};Object.defineProperty(g.realm,'fetch',changed);await assert.rejects(g.state.sealAndDrain());assert.deepEqual(Object.getOwnPropertyDescriptor(g.realm,'fetch'),changed);
 const h=fixture();const foreign={foreign:true};Object.defineProperty(h.realm,h.config.key,{value:foreign,configurable:true});await assert.rejects(h.state.sealAndDrain());assert.equal(h.realm[h.config.key],foreign);
});

// Causal desired controls: the old observer cannot bind the actual native URL.
function visible(f,url){const image={isConnected:true,ownerDocument:f.doc,currentSrc:url,src:url,alt:'a.png',complete:true,naturalWidth:32,naturalHeight:32,closest:()=>({getAttribute:()=> 'a.png 라벨링에서 열기'})};f.doc.images.push(image);return {image,src:url,alt:'a.png',card:'a.png 라벨링에서 열기'};}
async function nativeURLFixture(){const f=fixture();await application(f);await f.state.read(f.identity,'actual-request-1');const value=f.realm.URL.createObjectURL(f.blob);return {...f,value};}
test('exact app Blob to one native URL retains original receiver args result and immutable bytes without Blob URL fetch',async()=>{
 const f=await nativeURLFixture();try{const actual=f.state.readDisplayedURL(f.value);assert.equal(actual.base64,Buffer.from('ABCD').toString('base64'));assert.equal(actual.nativeURL,f.value);assert.equal(actual.record_id,1);assert.equal(f.calls.create.length,1);assert.equal(f.calls.create[0].receiver,f.realm.URL);assert.equal(f.calls.create[0].args[0],f.blob);assert.equal(f.calls.create[0].result,f.value);f.state.retainDisplayed([visible(f,f.value)]);assert.equal(f.state.snapshot().native_urls.length,1);await f.state.sealAndDrain();assert.equal(f.state.snapshot().local_readers,0);}finally{URL.revokeObjectURL(f.value);}
});
test('unknown foreign identical-bytes revoked retired and mutated native URL authority refuse instead of hash or count acceptance',async()=>{
 const f=await nativeURLFixture();let foreign;try{
  foreign=f.realm.URL.createObjectURL(new Blob(['ABCD'],{type:'image/jpeg'}));assert.throws(()=>f.state.readDisplayedURL(foreign),/native|Native|URL|Unknown/);assert.throws(()=>f.state.readDisplayedURL('blob:invented'),/native|Native|URL|Unknown/);
  assert.equal(f.state.readDisplayedURL(f.value).record_id,1);const entry=visible(f,f.value);f.state.retainDisplayed([entry]);entry.image.currentSrc=foreign;assert.throws(()=>f.state.verifyDisplayed(),/changed|differs|URL/);entry.image.currentSrc=f.value;
  f.realm.URL.revokeObjectURL(f.value);assert.equal(f.calls.revoke.length,1);assert.equal(f.calls.revoke[0].receiver,f.realm.URL);assert.equal(f.calls.revoke[0].args[0],f.value);assert.throws(()=>f.state.readDisplayedURL(f.value),/revoked/);await assert.rejects(f.state.sealAndDrain());
 }finally{URL.revokeObjectURL(f.value);if(foreign)URL.revokeObjectURL(foreign);}
 const g=await nativeURLFixture();try{g.realm.document={};assert.throws(()=>g.state.readDisplayedURL(g.value),/document/);await assert.rejects(g.state.sealAndDrain());}finally{URL.revokeObjectURL(g.value);}
});
test('same epoch intervening native revoke stays observed until final closure and all four descriptors restore despite foreign replacement',async()=>{
 const f=await nativeURLFixture();try{f.state.retainDisplayed([visible(f,f.value)]);assert.equal(f.state.verifyDisplayed(),true);f.realm.URL.revokeObjectURL(f.value);assert.throws(()=>f.state.verifyDisplayed(),/revoked/);await assert.rejects(f.state.sealAndDrain());assert.equal(f.realm.fetch,f.fetchDescriptor.value);assert.equal(f.realm.Response.prototype.blob,f.blobDescriptor.value);}finally{URL.revokeObjectURL(f.value);}
 const g=fixture();const originalRevoke=g.revokeDescriptor.value;const foreign=function foreignCreate(){};Object.defineProperty(g.realm.URL,'createObjectURL',{...Object.getOwnPropertyDescriptor(g.realm.URL,'createObjectURL'),value:foreign});await assert.rejects(g.state.sealAndDrain());assert.equal(g.realm.URL.createObjectURL,foreign);assert.equal(g.realm.URL.revokeObjectURL,originalRevoke);assert.equal(g.realm.fetch,g.fetchDescriptor.value);
});

test('two native URLs of one actual app Blob cannot impersonate two independent displayed owners',async()=>{const f=await nativeURLFixture();const second=f.realm.URL.createObjectURL(f.blob);assert.notEqual(second,f.value);try{assert.throws(()=>f.state.retainDisplayed([visible(f,f.value),visible(f,second)]),/one|One|independent|record|Blob/);}finally{URL.revokeObjectURL(f.value);URL.revokeObjectURL(second);await f.state.sealAndDrain().catch(()=>{});}});

test('native revocation in the final drain microtask remains observed before synchronous descriptor restoration',async()=>{const f=await nativeURLFixture();try{f.state.retainDisplayed([visible(f,f.value)]);queueMicrotask(()=>f.realm.URL.revokeObjectURL(f.value));await assert.rejects(f.state.sealAndDrain(),/revoked/);assert.equal(f.calls.revoke.length,1);}finally{URL.revokeObjectURL(f.value);}});

test('original synchronous native create and revoke exceptions/results pass through unchanged',async()=>{const createError=Error('original create error'),revokeError=Error('original revoke error'),f=fixture({createError,revokeError});assert.throws(()=>f.realm.URL.createObjectURL(f.blob),error=>error===createError);assert.throws(()=>f.realm.URL.revokeObjectURL('blob:unknown'),error=>error===revokeError);assert.equal(f.calls.revoke.length,1);await f.state.sealAndDrain();const g=fixture();assert.equal(g.realm.URL.revokeObjectURL('blob:unknown'),undefined);assert.equal(g.calls.revoke.length,1);await g.state.sealAndDrain();});

test('empty epoch final seal rechecks actual owning project and source after provisional qualification',async()=>{for(const change of ['project','source']){const f=fixture();let project='P',source='/owned/empty';f.doc.querySelector=()=>({textContent:project});f.doc.querySelectorAll=selector=>selector.includes('라벨링에서 열기')?f.doc.images:[{getAttribute:()=>source}];f.state.retainDisplayed([],{projectName:'P',source:'/owned/empty'});if(change==='project')project='Foreign';else source='/foreign';await assert.rejects(f.state.sealAndDrain(),/owning project|saved source/);}});

test('final current gallery is the exact retained image-object set including the empty cohort',async()=>{for(const empty of [true,false]){const f=empty?fixture():await nativeURLFixture();try{f.state.retainDisplayed(empty?[]:[visible(f,f.value)]);f.doc.images.push({isConnected:true,ownerDocument:f.doc,currentSrc:f.value||'blob:unknown',src:f.value||'blob:unknown',alt:'a.png',complete:true});await assert.rejects(f.state.sealAndDrain(),/cohort|gallery|image|Image/);}finally{if(f.value)URL.revokeObjectURL(f.value);}}});
