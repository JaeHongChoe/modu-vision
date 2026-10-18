
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {OwnedMediaTasks, validateMediaJoin, validateMediaRequest, MAX_BODY_BYTES, snapshotQualifiedMediaRequests, validateQualifiedMediaRequests} = require('./scoped-gallery-media-witness.cjs');
const context = {workspace_id:'w',project_id:'p',actor_id:'a',mode:'local'};
const digest = 'a'.repeat(64);
const request = (path,over={}) => ({path,epoch:2,document:3,mainFrame:true,method:'GET',origin:'http://127.0.0.1:9',started:10,completed:20,deadline:100,project:'p',context:{...context},echo:{...context},status:200,finished:true,bytes:12,sha256:digest,rgba:'b'.repeat(64),width:32,height:32,...over});
const rendered = (alt,over={}) => ({alt,card:alt+' 라벨링에서 열기',src:'blob:owned-'+alt,document:3,bytes:12,sha256:digest,rgba:'b'.repeat(64),width:32,height:32,complete:true,connected:true,...over});
const options = {paths:['/owned/a.png','/owned/b.png'],epoch:2,document:3,origin:'http://127.0.0.1:9',project:'p',context,naturalWidth:32,naturalHeight:32,previousURLs:[]};
test('complete original path/context set and rendered body multiset retain multiplicity, with unique alt identity',()=>{const r=validateMediaJoin([request('/owned/a.png'),request('/owned/b.png',{sha256:'c'.repeat(64)})],[rendered('a.png'),rendered('b.png',{sha256:'c'.repeat(64)})],options);assert.equal(r.paths.length,2);assert.equal(r.displayed.length,2);assert.equal(r.per_card_paths_proved,true);});
test('request-start epoch, document, frame, project, context and echoed context cannot be substituted',()=>{for(const over of [{epoch:1},{document:2},{mainFrame:false},{project:'foreign'},{context:{...context,actor_id:'other'}},{echo:{...context,workspace_id:'other'}},{method:'POST'},{origin:'http://127.0.0.1:8'},{started:101},{completed:101},{finished:false},{status:403},{bytes:MAX_BODY_BYTES+1}]) assert.throws(()=>validateMediaRequest(request('/owned/a.png',over),{...options,path:'/owned/a.png'}));});
test('missing, duplicate-inconsistent, hidden or foreign path never becomes a count-only pass',()=>{for(const rows of [[request('/owned/a.png')],[request('/owned/a.png'),request('/foreign/b.png')],[request('/owned/a.png'),request('/owned/b.png'),request('/owned/a.png',{sha256:'c'.repeat(64)})]])assert.throws(()=>validateMediaJoin(rows,[rendered('a.png'),rendered('b.png')],options));});
test('rendered stale URL, missing/changed card, wrong body/decode or document refuses',()=>{for(const over of [{src:'blob:old'},{alt:'foreign.png'},{card:'wrong'},{sha256:'c'.repeat(64)},{rgba:'c'.repeat(64)},{document:2},{complete:false},{connected:false},{width:0},{bytes:MAX_BODY_BYTES+1}])assert.throws(()=>validateMediaJoin([request('/owned/a.png'),request('/owned/b.png')],[rendered('a.png',over),rendered('b.png')],{...options,previousURLs:['blob:old']}));});
test('duplicate basenames and identical bodies prove cohort multiplicity only, never per-card bijection',()=>{const paths=['/owned/OK/same.png','/owned/NG/same.png'];const rows=paths.map(p=>request(p));const out=validateMediaJoin(rows,[rendered('same.png',{src:'blob:one'}),rendered('same.png',{src:'blob:two'})],{...options,paths});assert.equal(out.per_card_paths_proved,false);assert.equal(out.association,'cohort-multiset-only');assert.throws(()=>validateMediaJoin(rows,[rendered('same.png')],{...options,paths}));});
test('task rejection is stored immediately and remains observable through finite drain',async()=>{const owner=new OwnedMediaTasks();const row=owner.run('body',()=>Promise.reject(Error('body failed')));await new Promise(resolve=>setImmediate(resolve));assert.equal(owner.failures().length,1);await assert.rejects(owner.wait(row,performance.now()+50),/body failed/);const out=await owner.drain(performance.now()+50);assert.equal(out.pending.length,0);assert.equal(out.failures.length,1);});
test('unfinished task drain fails unresolved at the same absolute deadline without inventing completion',async()=>{const owner=new OwnedMediaTasks();let finish;const row=owner.run('original body',()=>new Promise(resolve=>finish=resolve));const deadline=performance.now()+15;await assert.rejects(owner.drain(deadline),error=>error.unresolved?.includes('original body')&&error.deadline===deadline);assert.equal(row.state,'pending');finish(1);await owner.drain(performance.now()+50);assert.equal(row.state,'fulfilled');});
test('completed late task cannot pass an earlier original absolute frame',async()=>{const owner=new OwnedMediaTasks();const deadline=performance.now()-1;const row=owner.run('late body',()=>1);await new Promise(resolve=>setImmediate(resolve));await assert.rejects(owner.wait(row,deadline),/deadline/);});

test('a reused assertion owner remains retained: unresolved and late Blob work cannot be hidden by close',async()=>{
  const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
  const {createScopedGalleryMediaWitness}=require('./scoped-gallery-media-witness.cjs');
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'owned-scoped-media-pure-'));
  let resolvePending,evaluations=0;
  const handle=()=>({evaluate:async()=>true,dispose:async()=>{}});
  const blobState={narrowDeadline:()=>{},snapshot:()=>({epoch:1,documentURL:options.origin+'/index.html',records:[],tasks:[],failures:[],unresolved:[],local_readers:0}),sealAndDrain:async()=>({drained:true,records:[],local_readers_closed:true,original_descriptors_restored:true})};
  const blobHandle={evaluate:async(fn,arg)=>fn(blobState,arg),dispose:async()=>{}};
  const frame={evaluateHandle:async()=>handle()};
  const protocol={on(){},off(){},async send(method){return method==='Page.getFrameTree'?{frameTree:{frame:{id:'main',loaderId:'loader',url:options.origin+'/index.html'}}}:{};},async detach(){}};
  const page={evaluateHandle:async()=>blobHandle,mainFrame:()=>frame,url:()=>options.origin+'/index.html',context:()=>({newCDPSession:async()=>protocol}),on(){},off(){},locator:()=>({evaluateAll:async()=>[]}),waitForFunction:async()=>handle(),
    evaluate:()=>++evaluations===1?Promise.resolve({original:[],displayed:[],local_readers_closed:true,reference_urls_closed:true}):new Promise(resolve=>{resolvePending=resolve;})};
  const media=createScopedGalleryMediaWitness(page,{origin:options.origin,artifactDir:dir});
  try{
    let deadline=performance.now()+1000;await media.begin(deadline,'pure empty');
    await media.assert({deadline,expectedPaths:[],project:'p',context,projectName:'P',source:'/owned/empty',naturalWidth:32,naturalHeight:32});
    deadline=performance.now()+20;
    await assert.rejects(media.assert({deadline,expectedPaths:[],project:'p',context,projectName:'P',source:'/owned/empty',naturalWidth:32,naturalHeight:32}),error=>error.unresolved?.includes('actual local Blob bytes and same-decoder pixels'));
    await assert.rejects(media.close(performance.now()+100),error=>error.unresolved?.includes('actual local Blob bytes and same-decoder pixels'));
    resolvePending({original:[],displayed:[],local_readers_closed:true,reference_urls_closed:true});await new Promise(resolve=>setImmediate(resolve));
    await assert.rejects(media.close(performance.now()+100),/after deadline/);
  }finally{if(resolvePending)resolvePending({});fs.rmSync(dir,{recursive:true,force:true});}
});
test('even zero-image observation refuses a changed owning UI before any Blob/reference operation',async()=>{
  const {readActualDisplayedMediaForPureControl}=require('./scoped-gallery-media-witness.cjs');
  const prior=global.document,doc={querySelector:()=>({textContent:'Foreign project'}),querySelectorAll:()=>[]};
  global.document=doc;
  try{await assert.rejects(readActualDisplayedMediaForPureControl({documentHandle:doc,refs:[],expectedCount:0,width:32,height:32,deadlineEpoch:Date.now()+1000,limit:MAX_BODY_BYTES,projectName:'P',source:'/owned/empty'}),/owning project header/);}
  finally{global.document=prior;}
});


test('per-assertion proof is provisional with live listeners; next begin seals prior epoch and final close restores all ownership',async()=>{
 const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{createScopedGalleryMediaWitness}=require('./scoped-gallery-media-witness.cjs');const dir=fs.mkdtempSync(path.join(os.tmpdir(),'owned-native-url-pure-'));let listeners=0,seals=0,disposals=0;
 const state={narrowDeadline:()=>{},snapshot:()=>({epoch:1,documentURL:'http://127.0.0.1:9/index.html',records:[],tasks:[],failures:[],unresolved:[],local_readers:0}),sealAndDrain:async()=>{seals++;return{drained:true};}};const blobHandle={evaluate:async(fn,arg)=>fn(state,arg),dispose:async()=>{disposals++;}};const docHandle={evaluate:async()=>true,dispose:async()=>{}};
 const session={on(){},off(){},send:async method=>method==='Page.getFrameTree'?{frameTree:{frame:{id:'main',loaderId:'loader',url:'http://127.0.0.1:9/index.html'}}}:{},detach:async()=>{}};
 const frame={url:()=> 'http://127.0.0.1:9/index.html',evaluateHandle:async()=>docHandle};const page={mainFrame:()=>frame,url:()=>frame.url(),context:()=>({newCDPSession:async()=>session}),evaluateHandle:async()=>blobHandle,on(){listeners++;},off(){listeners--;},locator:()=>({evaluateAll:async()=>[]}),waitForFunction:async()=>docHandle,evaluate:async()=>({original:[],displayed:[],local_readers_closed:true,reference_urls_closed:true})};
 const media=createScopedGalleryMediaWitness(page,{origin:'http://127.0.0.1:9',artifactDir:dir}),deadline=performance.now()+1000;
 try{await media.begin(deadline,'empty');const args={deadline,expectedPaths:[],project:'p',context,projectName:'P',source:'/owned/empty',naturalWidth:32,naturalHeight:32};const a=await media.assert(args);const receipt=JSON.parse(fs.readFileSync(a.file));assert.equal(receipt.observer_lifetime_closed,false);assert.equal(receipt.provisional,true);assert.equal(receipt.listeners_detached,false);assert.equal(receipt.URL_factory_untouched,false);assert.ok(a.files.some(name=>name.endsWith('-epoch-closed.json')));assert.equal(seals,0);assert.equal(listeners,2);await media.assert({...args,deadline:deadline+1000});assert.equal(seals,0);const closed=await media.close(deadline);assert.equal(closed.drained,true);assert.equal(seals,1);assert.equal(listeners,0);assert.equal(disposals,1);const final=fs.readdirSync(dir).find(name=>name.includes('epoch-closed'));assert.ok(final);const q=JSON.parse(fs.readFileSync(path.join(dir,final)));assert.equal(q.qualified_assertions.length,2);assert.equal(q.observer_lifetime_closed,true);assert.equal(q.original_epoch_deadline,deadline);}finally{await media.close(deadline).catch(()=>{});fs.rmSync(dir,{recursive:true,force:true});}
});

test('final qualified Request objects and original body/context set cannot grow or be replaced after provisional media proof',()=>{const first={request:{actual:1},url:'http://127.0.0.1:9/api/dataset/thumbnail/a',path:'/owned/a.png',method:'GET',project:'p',context:{...context},echo:{...context},sha256:digest,protocolProof:{record_id:1}};const second={...first,request:{actual:2}};const snapshot=snapshotQualifiedMediaRequests([first]);assert.equal(validateQualifiedMediaRequests([first],snapshot),true);for(const rows of [[first,second],[second],[{...first,sha256:'b'.repeat(64)}],[{...first,context:{...context,actor_id:'foreign'}}]])assert.throws(()=>validateQualifiedMediaRequests(rows,snapshot));});

function transitionFixture(){
 const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{createScopedGalleryMediaWitness}=require('./scoped-gallery-media-witness.cjs');
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'owned-pretransition-media-'));let loader='loader-A',seals=0;
 const url='http://127.0.0.1:9/index.html',doc={evaluate:async()=>true,dispose:async()=>{}};
 const frame={url:()=>url,evaluateHandle:async()=>doc};
 const page={mainFrame:()=>frame,url:()=>url,on(){},off(){},locator:()=>({evaluateAll:async()=>[]}),waitForFunction:async()=>doc,evaluate:async()=>({original:[],displayed:[],local_readers_closed:true,reference_urls_closed:true}),
  context:()=>({newCDPSession:async()=>({on(){},off(){},send:async method=>method==='Page.getFrameTree'?{frameTree:{frame:{id:'main',loaderId:loader,url}}}:{},detach:async()=>{}})}),
  evaluateHandle:async(_install,config)=>{const ownLoader=loader,state={narrowDeadline:()=>{},snapshot:()=>({epoch:config.epoch,documentURL:url,records:[],tasks:[],failures:[],unresolved:[],local_readers:0}),sealAndDrain:async()=>{assert.equal(loader,ownLoader,'Original owning document changed');seals++;return{drained:true};}};return{evaluate:async(fn,arg)=>fn(state,arg),dispose:async()=>{}};}};
 const media=createScopedGalleryMediaWitness(page,{origin:'http://127.0.0.1:9',artifactDir:dir}),deadline=performance.now()+1000;
 return {fs,path,dir,media,deadline,args:{deadline,expectedPaths:[],project:'p',context,projectName:'P',source:'/owned/empty',naturalWidth:32,naturalHeight:32},transition:()=>{loader='loader-B';},seals:()=>seals};
}
test('explicit current-epoch close finishes the original loader before the next ordinary project/reload transition, without closing the next epoch',async()=>{
 const f=transitionFixture();try{
  assert.equal(await f.media.closeCurrentEpoch(),null);await f.media.begin(f.deadline,'own first');await f.media.assert(f.args);
  const closed=await f.media.closeCurrentEpoch();assert.equal(JSON.parse(f.fs.readFileSync(closed.file)).observer_lifetime_closed,true);assert.equal(f.seals(),1);
  f.transition();await f.media.begin(f.deadline,'own next');await f.media.assert(f.args);assert.equal(f.seals(),1);await f.media.close(f.deadline);assert.equal(f.seals(),2);
 }finally{await f.media.close(f.deadline).catch(()=>{});f.fs.rmSync(f.dir,{recursive:true,force:true});}
});
test('current-epoch close still refuses a loader already changed by an unclosed ordinary transition',async()=>{
 const f=transitionFixture();try{await f.media.begin(f.deadline,'own first');await f.media.assert(f.args);f.transition();await assert.rejects(f.media.begin(f.deadline,'late next'),error=>error.code==='ERR_ASSERTION');await assert.rejects(f.media.closeCurrentEpoch(),error=>error.code==='ERR_ASSERTION');assert.equal(f.fs.existsSync(f.path.join(f.dir,'scoped-media-1-epoch-closed.json')),false);}
 finally{await f.media.close(f.deadline).catch(()=>{});f.fs.rmSync(f.dir,{recursive:true,force:true});}
});
