'use strict';
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const MAX_BODY_BYTES = 1024 * 1024;
class OwnedMediaTasks {
  constructor() { this.rows = []; this.watchers = new Set(); }
  run(label, operation) {
    const row = {label, state:'pending', started:performance.now()};
    this.rows.push(row);
    let promise;
    try { promise = Promise.resolve(operation()); } catch(error) { promise = Promise.reject(error); }
    row.done = promise.then(value => {row.state='fulfilled'; row.value=value; row.completed=performance.now(); this.notify(); return row;},
      error => {row.state='rejected'; row.error=error; row.completed=performance.now(); this.notify(); return row;});
    return row;
  }
  notify() { for(const wake of [...this.watchers]) wake(); }
  failures() { return this.rows.filter(row=>row.state==='rejected'); }
  async settle(predicate, deadline) {
    if(!Number.isFinite(deadline)) throw Error('Original media deadline required');
    const unresolved = () => this.rows.filter(row=>row.state==='pending').map(row=>row.label);
    if(predicate()) return;
    if(performance.now()>=deadline) throw Object.assign(Error('Original media deadline expired; unresolved ownership'), {deadline,unresolved:unresolved()});
    await new Promise((resolve,reject)=>{
      let timer;
      const release=()=>{clearTimeout(timer);this.watchers.delete(wake);};
      const wake=()=>{if(predicate()){release();resolve();}};
      this.watchers.add(wake);
      timer=setTimeout(()=>{release();reject(Object.assign(Error('Original media deadline expired; unresolved ownership'),{deadline,unresolved:unresolved()}));},Math.max(0,deadline-performance.now()));
      wake();
    });
  }
  async wait(row, deadline) {
    await this.settle(()=>row.state!=='pending',deadline);
    if(row.completed>deadline) throw Object.assign(Error('Original media task completed after deadline'),{deadline,label:row.label});
    if(row.state==='rejected') throw row.error;
    return row.value;
  }
  async drain(deadline) {
    await this.settle(()=>this.rows.every(row=>row.state!=='pending'),deadline);
    const late=this.rows.filter(row=>row.completed>deadline);
    if(late.length) throw Object.assign(Error('Original media drain completed after deadline'),{deadline,late:late.map(row=>row.label)});
    return {pending:[],failures:this.failures()};
  }
}
function validateMediaRequest(row, options) {
  assert.equal(row.path,options.path,'Original owning thumbnail path');
  assert.equal(row.epoch,options.epoch,'Actual Request-start epoch');
  assert.equal(row.document,options.document,'Actual request document generation');
  assert.equal(row.mainFrame,true,'Original main frame');
  assert.equal(row.method,'GET'); assert.equal(row.origin,options.origin);
  assert.equal(row.project,options.project); assert.deepEqual(row.context,options.context); assert.deepEqual(row.echo,options.context);
  assert.equal(row.status,200); assert.equal(row.finished,true);
  assert.ok(Number.isFinite(row.started)&&Number.isFinite(row.completed)&&Number.isFinite(row.deadline));
  assert.ok(row.started<=row.completed&&row.completed<=row.deadline,'Original request/body deadline');
  assert.ok(Number.isInteger(row.bytes)&&row.bytes>0&&row.bytes<=MAX_BODY_BYTES,'Original media body bound');
  assert.match(row.sha256,/^[a-f0-9]{64}$/); assert.match(row.rgba,/^[a-f0-9]{64}$/);
  assert.equal(row.width,options.naturalWidth); assert.equal(row.height,options.naturalHeight);
}
function validateMediaJoin(rows, displayed, options) {
  assert.ok(Array.isArray(options.paths)&&options.paths.length<=6,'Original finite cohort at most six');
  assert.equal(new Set(options.paths).size,options.paths.length,'Expected paths must be unique');
  const expected=[...options.paths].sort(), grouped=new Map();
  for(const row of rows){assert.ok(expected.includes(row.path),'Foreign or hidden thumbnail path');validateMediaRequest(row,{...options,path:row.path});if(!grouped.has(row.path))grouped.set(row.path,[]);grouped.get(row.path).push(row);}
  assert.deepEqual([...grouped.keys()].sort(),expected,'Complete actual original thumbnail path set');
  const key=row=>JSON.stringify([row.bytes,row.sha256,row.width,row.height,row.rgba]);
  const accepted=expected.map(file=>{const candidates=grouped.get(file);assert.equal(new Set(candidates.map(key)).size,1,'Conflicting original bodies for same path');return candidates[0];});
  assert.equal(displayed.length,expected.length,'Rendered cohort cardinality');
  const previous=new Set(options.previousURLs||[]), urls=new Set();
  for(const row of displayed){
    assert.equal(row.document,options.document);assert.equal(row.connected,true);assert.equal(row.complete,true);
    assert.ok(typeof row.src==='string'&&row.src.startsWith('blob:')&&!previous.has(row.src),'Fresh actual displayed Blob URL');
    assert.ok(!urls.has(row.src),'One actual displayed resource per original card');urls.add(row.src);
    assert.ok(typeof row.alt==='string'&&row.alt.length>0);assert.equal(row.card,row.alt+' 라벨링에서 열기','Original accessible card identity');
    assert.equal(row.width,options.naturalWidth);assert.equal(row.height,options.naturalHeight);
    assert.ok(Number.isInteger(row.bytes)&&row.bytes>0&&row.bytes<=MAX_BODY_BYTES);assert.match(row.sha256,/^[a-f0-9]{64}$/);assert.match(row.rgba,/^[a-f0-9]{64}$/);
  }
  assert.deepEqual(displayed.map(key).sort(),accepted.map(key).sort(),'Actual displayed bytes and decoded pixel multiset');
  assert.deepEqual(displayed.map(row=>row.alt).sort(),expected.map(file=>path.basename(file)).sort(),'Original alt-name multiset');
  let perCard=true;
  for(const row of displayed){const candidates=accepted.filter(item=>path.basename(item.path)===row.alt);assert.ok(candidates.length>0);if(candidates.length===1){assert.equal(key(row),key(candidates[0]),'Unique accessible card must match its original HTTP body');}else{assert.ok(candidates.some(item=>key(item)===key(row)),'Ambiguous card lacks any owning body');perCard=false;}}
  for(const name of new Set(expected.map(file=>path.basename(file))))assert.deepEqual(displayed.filter(row=>row.alt===name).map(key).sort(),accepted.filter(row=>path.basename(row.path)===name).map(key).sort(),'Ambiguous-name group body multiplicity');
  if(new Set(accepted.map(key)).size!==accepted.length)perCard=false;
  return {paths:expected,displayed,per_card_paths_proved:perCard,association:perCard?'unique-card':'cohort-multiset-only'};
}
function snapshotQualifiedMediaRequests(rows){
  assert.equal(new Set(rows.map(row=>row.request)).size,rows.length,'Unique actual qualified Request objects required');
  return rows.map(row=>({request:row.request,values:{url:row.url,path:row.path,method:row.method,project:row.project,context:row.context?{...row.context}:null,echo:row.echo?{...row.echo}:null,sha256:row.sha256,bytes:row.bytes,epoch:row.epoch,document:row.document,record_id:row.protocolProof.record_id}}));
}
function validateQualifiedMediaRequests(rows,qualified){
  const current=snapshotQualifiedMediaRequests(rows);assert.equal(current.length,qualified.length,'Qualified original Request set changed');
  for(let index=0;index<current.length;index++){assert.equal(current[index].request,qualified[index].request,'Qualified original Request identity changed');assert.deepEqual(current[index].values,qualified[index].values,'Qualified original request body/context changed');}return true;
}
module.exports={OwnedMediaTasks,validateMediaRequest,validateMediaJoin,MAX_BODY_BYTES,snapshotQualifiedMediaRequests,validateQualifiedMediaRequests};

/**
 * The functions below are source-only preparation until Root runs a fresh case.
 * Instrumented QA observes exact original fetch/Response.blob Promises, Page
 * events and actual Blob resources. Function identity is explicitly changed.
 * Native URL factory/revoke identities are also observed; each original call/return is preserved.
 * No added HTTP interception, replacement fetch, CSP/cache change or app-state read.
 */
const {createOriginalBlobPromiseWitness}=require('./scoped-gallery-blob-promise-witness.cjs');
const IMAGE_SELECTOR='button[aria-label$=" 라벨링에서 열기"] img';
const MAX_REQUESTS_PER_EPOCH=48; // New witness retention bound, never a business/request cap.
const sha=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const contextFrom=value=>{assert.ok(typeof value==='string'&&value.length>0,'Actual context header required');const c=JSON.parse(value);assert.ok(c&&['workspace_id','project_id','actor_id'].every(k=>typeof c[k]==='string'&&c[k].length>0)&&c.mode==='local');return {workspace_id:c.workspace_id,project_id:c.project_id,actor_id:c.actor_id,mode:c.mode};};

/* This function runs only inside a genuine future owning document. All created
 * local readers, abort listener, reference Images and temporary URLs have a
 * local finally owner. One browser evaluation is retained by OwnedMediaTasks.
 */
async function readActualDisplayedMedia(params) {
  const {documentHandle, refs, expectedCount, width, height, deadlineEpoch, limit, projectName, source, nativeAuthority}=params;
  if(documentHandle!==document)throw Error('Actual original document changed');
  const controller=new AbortController(), readers=new Set(), references=new Set();
  const timer=setTimeout(()=>controller.abort(),Math.max(0,deadlineEpoch-Date.now()));
  const check=()=>{if(controller.signal.aborted||Date.now()>deadlineEpoch)throw Error('Original media frame expired');if(documentHandle!==document)throw Error('Actual original document changed');const title=document.querySelector('[title="프로젝트 관리"]');if(!projectName||!title?.textContent?.includes(projectName))throw Error('Actual owning project header changed');if(!source||![...document.querySelectorAll('span[title]')].some(node=>node.getAttribute('title')===source))throw Error('Actual saved source changed');};
  const hash=async bytes=>{check();const value=await crypto.subtle.digest('SHA-256',bytes);check();return Array.from(new Uint8Array(value),v=>v.toString(16).padStart(2,'0')).join('');};
  const pixels=async image=>{check();if(image.naturalWidth!==width||image.naturalHeight!==height)throw Error('Original decoded dimensions changed');const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;const ctx=canvas.getContext('2d',{willReadFrequently:true});if(!ctx)throw Error('Actual pixel decoder unavailable');ctx.drawImage(image,0,0);return hash(ctx.getImageData(0,0,width,height).data);};
  const current=Array.from(document.querySelectorAll('button[aria-label$=" 라벨링에서 열기"] img'));
  if(current.length!==expectedCount)throw Error('Actual original card count changed');
  const identities=current.map(image=>({image,src:image.currentSrc||image.src,alt:image.alt,card:image.closest('button')?.getAttribute('aria-label')}));
  const stillCurrent=entry=>{check();if(!entry.image.isConnected||entry.image.ownerDocument!==document||!entry.src.startsWith('blob:')||(entry.image.currentSrc||entry.image.src)!==entry.src||entry.image.alt!==entry.alt||entry.image.closest('button')?.getAttribute('aria-label')!==entry.card||!entry.image.complete)throw Error('Actual displayed card/resource changed');};
  const abortLocal=()=>{for(const entry of references)entry.image.removeAttribute('src');};
  controller.signal.addEventListener('abort',abortLocal);
  const localRead=url=>{
    check();const actual=nativeAuthority.readDisplayedURL(url);const bytes=Uint8Array.from(atob(actual.base64),c=>c.charCodeAt(0));
    if(actual.nativeURL!==url||!bytes.length||bytes.length>limit||actual.bytes!==bytes.length)throw Error('Exact app Blob/native URL body authority differs');
    return {bytes,record_id:actual.record_id};
  };
  const referenceDecode=async ref=>{
    check();const raw=Uint8Array.from(atob(ref.base64),c=>c.charCodeAt(0));if(!raw.length||raw.length>limit)throw Error('Retained HTTP body bound');
    const image=new Image(),url=URL.createObjectURL(new Blob([raw],{type:ref.mime})),entry={image,url};references.add(entry);
    try{image.src=url;await image.decode();check();return {path:ref.path,bytes:raw.length,sha256:await hash(raw),width:image.naturalWidth,height:image.naturalHeight,rgba:await pixels(image)};}
    finally{image.removeAttribute('src');image.remove();URL.revokeObjectURL(url);references.delete(entry);}
  };
  try{
    check();
    const original=[];
    for(const ref of refs)original.push(await referenceDecode(ref));
    const displayed=[];
    for(const entry of identities){stillCurrent(entry);await entry.image.decode();stillCurrent(entry);const actual=localRead(entry.src),bytes=actual.bytes;stillCurrent(entry);displayed.push({native_url_record_id:actual.record_id,src:entry.src,alt:entry.alt,card:entry.card,bytes:bytes.length,sha256:await hash(bytes),width:entry.image.naturalWidth,height:entry.image.naturalHeight,rgba:await pixels(entry.image),complete:entry.image.complete,connected:entry.image.isConnected});stillCurrent(entry);nativeAuthority.readDisplayedURL(entry.src);}
    if(document.querySelectorAll('button[aria-label$=" 라벨링에서 열기"] img').length!==identities.length)throw Error('Original gallery changed during media read');
    for(const entry of identities)stillCurrent(entry);
    nativeAuthority.retainDisplayed(identities,{projectName,source});
    return {original,displayed,local_readers_closed:readers.size===0,reference_urls_closed:references.size===0,URL_factory_untouched:false,native_object_URL_calls_observed:true};
  } finally {
    clearTimeout(timer);controller.abort();controller.signal.removeEventListener('abort',abortLocal);
    // Normal reads cancel their own readers. Failure cleanup also awaits each
    // remaining reader; an unfinished evaluation remains explicitly unresolved.
    for(const reader of [...readers]){try{await reader.cancel();}finally{reader.releaseLock();readers.delete(reader);}}
    for(const entry of [...references]){entry.image.removeAttribute('src');entry.image.remove();URL.revokeObjectURL(entry.url);references.delete(entry);}
  }
}
function createScopedGalleryMediaWitness(page,{origin,artifactDir}) {
  const backend=new URL(origin);assert.equal(backend.origin,origin);assert.ok(['127.0.0.1','localhost','[::1]'].includes(backend.hostname),'Original loopback authority required');
  assert.ok(path.isAbsolute(artifactDir)&&fs.statSync(artifactDir).isDirectory(),'Original case artifact directory required');
  const mainFrame=page.mainFrame(), epochs=[], owners=[], allHandles=new Set(), retiredURLs=new Set();
  const newOwner=(deadline,label)=>{const owner=new OwnedMediaTasks();owners.push({owner,deadline,label});return owner;};
  let active=null, sequence=0, generation=0, attached=false, closed=false;
  const rememberFailure=(epoch,error)=>{epoch.failures.push({error,at:performance.now()});};
  const onNavigated=frame=>{if(frame===mainFrame)generation++;};
  const onRequest=request=>{
    let u;
    try{u=new URL(request.url());}catch(error){if(active)rememberFailure(active,error);return;}
    if(!u.pathname.startsWith('/api/dataset/thumbnail/'))return;
    const epoch=active;if(!epoch||!attached)return;
    if(epoch.requests.length>=MAX_REQUESTS_PER_EPOCH){rememberFailure(epoch,Error('New finite media witness retention exhausted'));return;}
    const row={epoch:epoch.id,document:generation,started:performance.now(),deadline:epoch.deadline,request,resourceType:request.resourceType(),documentURL:mainFrame.url(),url:request.url(),origin:u.origin,method:request.method(),path:u.searchParams.get('file_path'),query:u.search,route:u.pathname};
    epoch.requests.push(row);
    row.task=epoch.owner.run('original thumbnail '+epoch.requests.length,async()=>{
      assert.ok(row.started<=epoch.deadline,'Actual Request started after original frame');
      assert.equal(request.frame(),mainFrame);row.mainFrame=true;
      assert.equal(row.origin,origin);assert.equal(row.method,'GET');assert.equal(row.resourceType,'fetch');assert.equal(request.redirectedFrom(),null);
      assert.equal(u.searchParams.getAll('file_path').length,1);assert.ok(row.path&&path.isAbsolute(row.path));
      const headers=request.headers();row.project=headers['x-vision-project'];row.context=contextFrom(headers['x-vision-context']);
      const doc=await mainFrame.evaluateHandle(()=>document);allHandles.add(doc);row.documentHandle=doc;
      assert.equal(generation,row.document,'Navigation changed during request document capture');
      assert.equal(await doc.evaluate(actual=>actual===document),true,'Original Request document unavailable');
      const response=await request.response();assert.ok(response,'Original thumbnail response absent');assert.equal(response.request(),request);
      row.status=response.status();row.echo=contextFrom(response.headers()['x-vision-context']);
      const declared=response.headers()['content-length'];if(declared!==undefined){assert.match(declared,/^\d+$/);assert.ok(Number(declared)<=MAX_BODY_BYTES);row.contentLength=Number(declared);}
      row.mime=response.headers()['content-type']||'';assert.match(row.mime,/^image\//);
      row.publicTiming={...request.timing()};row.publicStart=row.publicTiming.startTime;const stream=await epoch.protocol.join(row);const body=stream.body;row.protocolProof=stream.proof;assert.ok(body.length>0&&body.length<=MAX_BODY_BYTES,'Original complete actual app-Blob body bound');assert.equal(stream.proof.mime,row.mime);if(declared!==undefined)assert.equal(body.length,Number(declared));
      assert.equal(await response.finished(),null,'Original thumbnail response unfinished');
      assert.equal(generation,row.document,'Original response belongs to retired document');
      assert.equal(await doc.evaluate(actual=>actual===document),true,'Original response document changed');
      row.body=body;row.bytes=body.length;row.sha256=sha(body);row.finished=true;row.completed=performance.now();
      assert.ok(row.completed<=epoch.deadline,'Original HTTP body completed outside original frame');
      return row;
    });
  };
  const attach=()=>{assert.equal(attached,false);page.on('request',onRequest);page.on('framenavigated',onNavigated);attached=true;};
  const detach=()=>{if(attached){page.off('request',onRequest);page.off('framenavigated',onNavigated);attached=false;}};
  const run=async(owner,deadline,label,operation)=>owner.wait(owner.run(label,operation),deadline);
  const waitDOM=async(predicate,arg,deadline)=>{const handle=await page.waitForFunction(predicate,arg,{timeout:Math.max(1,Math.floor(deadline-performance.now()))});try{return true;}finally{await handle.dispose();}};
  const releaseHandles=async(owner,deadline,keep=null)=>{
    for(const handle of [...allHandles])if(handle!==keep){await run(owner,deadline,'release original document handle',()=>handle.dispose());allHandles.delete(handle);}
  };
  const persist=(epoch,label,receipt)=>{
    const safe=label.replace(/[^A-Za-z0-9_.-]/g,'_').slice(0,100);
    const file=label==='epoch-closed'?epoch.closureFile:path.join(artifactDir,'scoped-media-'+epoch.id+'-'+(++sequence)+'-'+safe+'.json');
    fs.writeFileSync(file,JSON.stringify(receipt,null,2),{flag:'wx'});
    return {file,sha256:sha(fs.readFileSync(file)),files:(receipt.requests||[]).map(row=>row.file).filter(Boolean)};
  };
  const closeEpoch=async epoch=>{
    if(epoch.lifetimeClosed)return epoch.closure;
    detach();
    await epoch.owner.drain(epoch.deadline);
    if(epoch.failures.length)throw epoch.failures[0].error;
    if(epoch.owner.failures().length)throw epoch.owner.failures()[0].error;
    assert.equal(epoch.sealed,true,'Unqualified original epoch cannot close successfully');
    validateQualifiedMediaRequests(epoch.requests,epoch.qualifiedRequests);
    await epoch.protocol.finish(epoch.requests);
    await releaseHandles(epoch.owner,epoch.deadline);await epoch.owner.drain(epoch.deadline);
    if(epoch.owner.failures().length)throw epoch.owner.failures()[0].error;
    epoch.closure=persist(epoch,'epoch-closed',{schema:'modu-vision.original-scoped-gallery-epoch-closed/v1',epoch:epoch.id,original_epoch_deadline:epoch.originalDeadline,effective_deadline:epoch.deadline,completed:performance.now(),qualified_assertions:epoch.assertions.slice(),whole_original_request_set:epoch.requests.map(row=>({epoch:row.epoch,url:row.url,method:row.method,project:row.project,context:row.context,sha256:row.sha256,record_id:row.protocolProof.record_id})),observer_lifetime_closed:true,provisional:false,tasks_settled:true,drained:true,listeners_detached:!attached,URL_factory_untouched:false,native_object_URL_calls_observed:true,original_native_URL_calls_and_results_preserved:true,original_protocol_diagnostic:epoch.protocol.diagnostic()});
    epoch.lifetimeClosed=true;return epoch.closure;
  };
  return {
    async closeCurrentEpoch(){
      assert.equal(closed,false);return active?closeEpoch(active):null;
    },
    async begin(deadline,label,{navigation=false}={}){
      assert.equal(closed,false);assert.ok(Number.isFinite(deadline)&&deadline>performance.now(),'Original media action deadline required');
      if(active&&!active.sealed)throw Error('Prior media action not asserted/drained before next transition');
      if(active)await closeEpoch(active);
      const owner=newOwner(deadline,'begin '+label);
      const immediatelyPreviousURLs=await run(owner,deadline,'prior actual displayed URLs',()=>page.locator(IMAGE_SELECTOR).evaluateAll(nodes=>nodes.map(node=>node.currentSrc||node.src).filter(value=>value.startsWith('blob:'))));
      for(const url of active?.displayedURLs||[])retiredURLs.add(url);for(const url of immediatelyPreviousURLs)retiredURLs.add(url);const previousURLs=[...retiredURLs];
      await releaseHandles(owner,deadline);await owner.drain(deadline);
      active={id:epochs.length+1,label,deadline,owner,requests:[],failures:[],previousURLs,started:performance.now(),startedWall:Date.now(),sealed:false,originalDeadline:deadline,assertions:[],lifetimeClosed:false,closureFile:path.join(artifactDir,'scoped-media-'+(epochs.length+1)+'-epoch-closed.json')};epochs.push(active);attach();
      active.protocol=createOriginalBlobPromiseWitness(page,owner,{epoch:active.id,origin,wallStart:active.startedWall,deadline,navigation});await active.protocol.start();
    },
    async assert(options){
      assert.equal(closed,false);const epoch=active;assert.ok(epoch,'Media epoch must start before original action');
      const reused=epoch.sealed, deadline=Math.min(options.deadline,epoch.deadline);epoch.deadline=deadline;for(const retained of owners)if(retained.owner===epoch.owner)retained.deadline=deadline;
      assert.ok(Number.isFinite(deadline)&&deadline>performance.now(),'Original media assertion frame expired');
      const owner=epoch.owner;
      let primary;
      try{
        if(!reused){
          // Scroll only original cards, never force IntersectionObserver/fetch.
          const images=page.locator(IMAGE_SELECTOR);
          await run(owner,deadline,'original gallery cardinality',()=>waitDOM(({selector,count})=>document.querySelectorAll(selector).length===count,{selector:IMAGE_SELECTOR,count:options.expectedPaths.length},deadline));
          for(let index=0;index<options.expectedPaths.length;index++)await run(owner,deadline,'ordinary gallery scroll '+index,()=>images.nth(index).scrollIntoViewIfNeeded({timeout:Math.max(1,Math.floor(deadline-performance.now()))}));
          await run(owner,deadline,'original decoded gallery',()=>waitDOM(({selector,count,width,height})=>{const images=[...document.querySelectorAll(selector)];return images.length===count&&images.every(image=>image.isConnected&&image.complete&&image.naturalWidth===width&&image.naturalHeight===height&&(image.currentSrc||image.src).startsWith('blob:'));},{selector:IMAGE_SELECTOR,count:options.expectedPaths.length,width:options.naturalWidth,height:options.naturalHeight},deadline));
          await owner.drain(deadline);
          if(epoch.failures.length)throw epoch.failures[0].error;
          if(owner.failures().length)throw owner.failures()[0].error;
          await epoch.protocol.checkpoint(epoch.requests,deadline);
          epoch.generation=generation;
          epoch.documentHandle=await run(owner,deadline,'actual settled original document',()=>mainFrame.evaluateHandle(()=>document));allHandles.add(epoch.documentHandle);
          for(const row of epoch.requests){assert.equal(row.document,epoch.generation,'Request started in a retired document');assert.equal(await run(owner,deadline,'Request document join',()=>row.documentHandle.evaluate((doc,other)=>doc===other,epoch.documentHandle)),true);}
        }else{
          validateQualifiedMediaRequests(epoch.requests,epoch.qualifiedRequests);
          await epoch.protocol.checkpoint(epoch.requests,deadline);
          assert.equal(await run(owner,deadline,'same-epoch retained original document',()=>epoch.documentHandle.evaluate(doc=>doc===document)),true);
          assert.deepEqual(options.expectedPaths.slice().sort(),epoch.expectedPaths,'Retained epoch owning paths changed');
          assert.deepEqual(options.context,epoch.context,'Retained epoch context changed');assert.equal(options.project,epoch.project);
        }
        const capture=await run(owner,deadline,'actual local Blob bytes and same-decoder pixels',async()=>page.evaluate(readActualDisplayedMedia,{documentHandle:epoch.documentHandle,refs:epoch.requests.map(row=>({path:row.path,base64:row.body.toString('base64'),mime:row.mime})),expectedCount:options.expectedPaths.length,width:options.naturalWidth,height:options.naturalHeight,limit:MAX_BODY_BYTES,projectName:options.projectName,source:options.source,nativeAuthority:await epoch.protocol.displayAuthority(),deadlineEpoch:Date.now()+Math.floor(deadline-performance.now())}));
        assert.equal(capture.local_readers_closed,true);assert.equal(capture.reference_urls_closed,true);
        for(let index=0;index<epoch.requests.length;index++){const decoded=capture.original[index],row=epoch.requests[index];assert.equal(decoded.path,row.path);assert.equal(decoded.sha256,row.sha256);row.width=decoded.width;row.height=decoded.height;row.rgba=decoded.rgba;}
        const displayed=capture.displayed.map(row=>({...row,document:epoch.generation}));
        const displayedOriginalPaths=[];for(const item of displayed){const exact=epoch.requests.filter(row=>row.protocolProof.record_id===item.native_url_record_id);assert.equal(exact.length,1,'Native URL must own one actual original Request/Blob record');assert.equal(item.sha256,exact[0].sha256,'Native URL must retain exact app Blob bytes');assert.equal(item.alt,path.basename(exact[0].path),'Native URL owning original path/card differs');displayedOriginalPaths.push(exact[0].path);}
        assert.deepEqual(displayedOriginalPaths.sort(),options.expectedPaths.slice().sort(),'Native URL object owners must equal the complete original path set');
        const join=validateMediaJoin(epoch.requests,displayed,{paths:options.expectedPaths,epoch:epoch.id,document:epoch.generation,origin,project:options.project,context:options.context,naturalWidth:options.naturalWidth,naturalHeight:options.naturalHeight,previousURLs:epoch.previousURLs});
        if(reused)assert.deepEqual(displayed.map(row=>row.src).sort(),epoch.displayedURLs,'Same-epoch resource changed without a new Request epoch');
        await releaseHandles(owner,deadline,epoch.documentHandle);await owner.drain(deadline);
        if(owner.failures().length)throw owner.failures()[0].error;
        const records=epoch.requests.map((row,index)=>{
          if(!row.file){row.file=path.join(artifactDir,'scoped-media-'+epoch.id+'-http-'+(index+1)+'.bin');fs.writeFileSync(row.file,row.body,{flag:'wx'});}
          return {path:row.path,url:row.url,route:row.route,query:row.query,epoch:row.epoch,document_generation:row.document,started:row.started,completed:row.completed,deadline:row.deadline,method:row.method,main_frame:row.mainFrame,project:row.project,context:row.context,echo:row.echo,status:row.status,finished:row.finished,bytes:row.bytes,sha256:row.sha256,width:row.width,height:row.height,rgba:row.rgba,file:row.file,original_body_observation:row.protocolProof};
        });
        epoch.expectedPaths=options.expectedPaths.slice().sort();epoch.context={...options.context};epoch.project=options.project;epoch.displayedURLs=displayed.map(row=>row.src).sort();epoch.sealed=true;epoch.qualifiedRequests=snapshotQualifiedMediaRequests(epoch.requests);
        const assertion=persist(epoch,options.label||epoch.label,{schema:'modu-vision.original-scoped-gallery-media-witness/v1',source_only_helper:true,actual_execution_by_caller:true,instrumented_QA:true,original_function_identity_unchanged_claim:false,observer_policy_exception:'exact original fetch, Response.blob and native object-URL create/revoke observers',epoch:epoch.id,label:options.label||epoch.label,reused_same_epoch:reused,original_action_deadline:epoch.deadline,observation_deadline:deadline,completed:performance.now(),previous_displayed_urls:epoch.previousURLs,requests:records,join,drained:false,tasks_settled:true,observer_lifetime_closed:false,provisional:true,expected_epoch_closure_file:epoch.closureFile,expected_epoch_closure_file_not_yet_written:true,unresolved:[],listeners_detached:!attached,local_readers_closed:true,test_reference_urls_closed:true,URL_factory_untouched:false,native_object_URL_calls_observed:true,per_card_bijection_inferred_from_duplicate_hashes:false});epoch.assertions.push({...assertion});return {...assertion,files:[...assertion.files,epoch.closureFile]};
      }catch(error){primary=error;throw error;}
      finally{
        if(primary){
          detach();let cleanupFailure;
          try{if(epoch.protocol)await epoch.protocol.cleanup();}catch(error){cleanupFailure=error;}
          try{await owner.drain(deadline);}catch(error){cleanupFailure=cleanupFailure||error;}
          persist(epoch,(options.label||epoch.label)+'-FAILED',{schema:'modu-vision.original-scoped-gallery-media-failure/v3',epoch:epoch.id,qualified:false,observer_lifetime_closed:false,deadline,failed:performance.now(),error:String(primary),cleanup_error:cleanupFailure?String(cleanupFailure):null,unresolved:primary.unresolved||cleanupFailure?.unresolved||[],tasks:owner.rows.map(row=>({label:row.label,state:row.state,started:row.started,completed:row.completed,error:row.error?String(row.error):null})),listener_failures:epoch.failures.map(row=>({at:row.at,error:String(row.error)})),original_protocol_diagnostic:epoch.protocol?.diagnostic?.()??null,listeners_detached:!attached});
        }
      }
    },

    async close(deadline){
      if(closed)return {epochs:epochs.length,drained:true,unresolved:[],listeners_detached:true,already_closed:true};
      const owner=newOwner(deadline,'final resource cleanup');
      let primary;
      try{
        if(active&&!active.lifetimeClosed)await closeEpoch(active);
        // Each original phase owns its own deadline; later cleanup cannot make
        // an earlier pending/late body count as a completed original operation.
        for(const retained of owners){await retained.owner.drain(retained.deadline);if(retained.owner.failures().length)throw retained.owner.failures()[0].error;}
        await releaseHandles(owner,deadline);await owner.drain(deadline);
        /* success is committed after the independent finally cleanup */
      }catch(error){primary=error;throw error;}
      finally{
        detach();let cleanupFailure;
        for(const epoch of epochs)try{if(epoch.protocol)await epoch.protocol.cleanup();}catch(error){cleanupFailure=cleanupFailure||error;}
        try{await releaseHandles(owner,deadline);}catch(error){cleanupFailure=cleanupFailure||error;}
        try{await owner.drain(deadline);}catch(error){cleanupFailure=cleanupFailure||error;}
        if(cleanupFailure){if(primary)primary.media_cleanup_failure=cleanupFailure;else throw cleanupFailure;}
      }
      closed=true;return {epochs:epochs.length,drained:true,unresolved:[],listeners_detached:true,closed_epochs:epochs.map(epoch=>epoch.closure)};

    },
  };
}
module.exports.createScopedGalleryMediaWitness=createScopedGalleryMediaWitness;



module.exports.readActualDisplayedMediaForPureControl=readActualDisplayedMedia;
