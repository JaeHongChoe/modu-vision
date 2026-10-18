'use strict';
const assert=require('node:assert/strict'),crypto=require('node:crypto');
const {installOriginalBlobObservation}=require('./scoped-gallery-original-blob.cjs');

function createOriginalBlobPromiseWitness(page,owner,{epoch,origin,wallStart,deadline,navigation=false}){
  const {createPassiveOriginalThumbnailIdentity}=require('./scoped-gallery-cdp-identity.cjs');
  const metadata=createPassiveOriginalThumbnailIdentity(page,owner,{epoch,origin,wallStart,deadline});
  const claims=new Map();let config=null,stateTask=null,scriptSession=null,scriptIdentifier=null,scriptCreateTask=null,scriptEnableTask=null,scriptAddTask=null;
  let removalAttempted=false,removed=false,expectedDetach=false,sessionDetached=false,closeError=null;
  let lastRealm=null,lastClosed=null,cleanupTask=null,stateReleaseTask=null,started=false;
  const run=(label,operation)=>owner.wait(owner.run(label,operation),deadline);
  const releaseState=handle=>{if(!stateReleaseTask)stateReleaseTask=owner.run('release owned Blob observation handle',()=>handle.dispose());return owner.wait(stateReleaseTask,deadline);};
  const onScriptSessionClose=()=>{if(!expectedDetach){closeError=Error('Owned original-Blob script session disconnected');owner.run('original Blob script session disconnect',()=>{throw closeError;});}};
  const getState=async(row=null,optional=false)=>{
    if(!stateTask){stateTask=owner.run('retain actual original-Blob realm',()=>row?row.documentHandle.evaluateHandle((doc,key)=>{if(doc!==document)throw Error('Original Page Request document changed');return globalThis[key]||null;},config.key):page.evaluateHandle(key=>globalThis[key]||null,config.key));}
    const handle=await owner.wait(stateTask,deadline);
    if(stateReleaseTask){await owner.wait(stateReleaseTask,deadline);if(optional)return null;throw Error('Original observation handle already released');}
    let present;try{present=await run('verify actual original-Blob realm',()=>handle.evaluate((state,{epoch,documentURL})=>Boolean(state&&state.snapshot().epoch===epoch&&state.snapshot().documentURL===documentURL),config));}
    catch(error){try{await releaseState(handle);}catch(disposeError){throw new AggregateError([error,disposeError],'Original realm verification and release failed');}throw error;}
    if(!present){await releaseState(handle);if(optional)return null;throw Error('Original main-document Blob observer absent');}return handle;
  };
  const removeScript=async()=>{
    if(!scriptIdentifier||removalAttempted)return;removalAttempted=true;
    await run('remove exact future-document Blob observer',()=>scriptSession.send('Page.removeScriptToEvaluateOnNewDocument',{identifier:scriptIdentifier}));removed=true;
  };
  const closeScriptSession=async()=>{
    if(!scriptSession||expectedDetach)return;expectedDetach=true;
    try{await run('detach owned Blob script session',()=>scriptSession.detach());sessionDetached=true;}
    finally{scriptSession.off('close',onScriptSessionClose);}
  };
  const closeRealm=async(optional)=>{
    const handle=await getState(null,optional);if(!handle)return;
    let primary;
    try{lastRealm=await run('retain original Blob realm diagnostic',()=>handle.evaluate(state=>state.snapshot()));}catch(error){primary=error;}
    try{lastClosed=await run('restore originals and drain exact Blob observation',()=>handle.evaluate(state=>state.sealAndDrain()));}catch(error){primary=primary||error;}
    try{lastRealm=await run('retain final original Blob realm diagnostic',()=>handle.evaluate(state=>state.snapshot()));}catch(error){primary=primary||error;}
    try{await releaseState(handle);}catch(error){primary=primary||error;}
    if(primary)throw primary;
  };
  const cleanup=async()=>{
    if(!cleanupTask){cleanupTask=owner.run('original Blob witness cleanup',async()=>{
      let primary;try{if(scriptCreateTask)await owner.wait(scriptCreateTask,deadline);if(scriptEnableTask)await owner.wait(scriptEnableTask,deadline);if(scriptAddTask)await owner.wait(scriptAddTask,deadline);}catch(error){primary=error;}
      try{await removeScript();}catch(error){primary=primary||error;}
      try{if(config)await closeRealm(true);}catch(error){primary=primary||error;}
      try{await closeScriptSession();}catch(error){primary=primary||error;}
      try{await metadata.cleanup();}catch(error){primary=primary||error;}
      if(primary)throw primary;if(closeError)throw closeError;
      assert.ok(!scriptIdentifier||removed,'Owned future-document observer unresolved');
      assert.ok(!scriptSession||sessionDetached,'Owned observer script session unresolved');return {closed:true};
    });}
    return owner.wait(cleanupTask,deadline);
  };
  return {
    async start(){
      assert.equal(started,false);started=true;
      config={key:'__moduOriginalBlob_'+crypto.randomUUID().replaceAll('-',''),epoch,origin,documentURL:page.url(),deadlineEpoch:Date.now()+Math.floor(deadline-performance.now()),limit:1048576,maxRecords:48};
      await metadata.start();
      if(navigation){
        scriptCreateTask=owner.run('create removable original-Blob script session',async()=>{scriptSession=await page.context().newCDPSession(page);scriptSession.on('close',onScriptSessionClose);return true;});await owner.wait(scriptCreateTask,deadline);
        scriptEnableTask=owner.run('enable owned original-Blob Page agent',()=>scriptSession.send('Page.enable'));await owner.wait(scriptEnableTask,deadline);
        const source='('+installOriginalBlobObservation.toString()+')('+JSON.stringify(config)+');';
        scriptAddTask=owner.run('arm fresh-main-document original-Blob observer',async()=>{const response=await scriptSession.send('Page.addScriptToEvaluateOnNewDocument',{source,runImmediately:false,includeCommandLineAPI:false});assert.ok(typeof response.identifier==='string'&&response.identifier);scriptIdentifier=response.identifier;return true;});await owner.wait(scriptAddTask,deadline);
      }else{
        stateTask=owner.run('install current-main-document original-Blob observer',()=>page.evaluateHandle(installOriginalBlobObservation,config));await owner.wait(stateTask,deadline);
      }
      return true;
    },
    async join(row){
      assert.ok(config);assert.equal(row.epoch,epoch);assert.equal(row.documentURL,config.documentURL);assert.equal(row.mainFrame,true);assert.equal(row.resourceType,'fetch');
      if(!claims.has(row.request)){assert.ok(claims.size<48,'Finite48 actual Page Request claims exhausted');claims.set(row.request,'actual-PageRequest-'+(claims.size+1));}
      const identity={url:row.url,method:row.method,project:row.project,context:row.context,echo:row.echo,status:row.status,mime:row.mime,contentLength:row.contentLength,epoch,documentURL:row.documentURL};
      const bodyTask=owner.run('same actual Response/blob-Promise bytes '+claims.get(row.request),async()=>{
        await getState(row);return row.documentHandle.evaluate((doc,{key,identity,claim})=>{if(doc!==document)throw Error('Original Page Request document changed');const state=globalThis[key];if(!state)throw Error('Owned original Blob observer absent');return state.read(identity,claim);},{key:config.key,identity,claim:claims.get(row.request)});
      });
      const identityTask=owner.run('exact metadata/PageRequest identity '+claims.get(row.request),()=>metadata.join(row));
      const [actual,joined]=await Promise.all([owner.wait(bodyTask,deadline),owner.wait(identityTask,deadline)]);
      const body=Buffer.from(actual.base64,'base64');assert.equal(body.toString('base64'),actual.base64);assert.ok(body.length>0&&body.length<=1048576);assert.equal(body.length,actual.bytes);
      assert.equal(actual.proof.actual_same_Response_and_app_Blob,true);assert.equal(actual.proof.HTTP_refetch,false);
      return {body,proof:{...actual.proof,mime:row.mime,exact_relative_CDP_identity:joined.proof,source_Electron_or_browser_instrumented_QA:true,installed_native_acceptance:false}};
    },
    async displayAuthority(){return getState(null,false);},
    async checkpoint(rows,nextDeadline){
      deadline=Math.min(deadline,nextDeadline);
      const handle=await getState(null,false);
      await run('narrow original native-URL observation frame',()=>handle.evaluate((state,value)=>state.narrowDeadline(value),Date.now()+Math.floor(deadline-performance.now())));
      await owner.drain(deadline);if(owner.failures().length)throw owner.failures()[0].error;
      lastRealm=await run('provisional original Blob/request whole-epoch join',()=>handle.evaluate(state=>state.snapshot()));
      assert.equal(lastRealm.records.length,rows.length);assert.equal(new Set(lastRealm.records.map(row=>row.claim)).size,rows.length);assert.ok(lastRealm.records.every(row=>[...claims.values()].includes(row.claim)));
      return {tasks_settled:true,observer_lifetime_closed:false,provisional:true};
    },
    async finish(rows){
      await owner.drain(deadline);await metadata.finish(rows);await removeScript();
      // No future realm can inherit an observer after this removal. The one
      // actual realm must restore its exact originals and settle all work.
      const handle=await getState(null,false);
      lastRealm=await run('snapshot actual Blob/request whole-epoch join',()=>handle.evaluate(state=>state.snapshot()));
      assert.equal(lastRealm.records.length,rows.length);assert.equal(new Set(lastRealm.records.map(row=>row.claim)).size,rows.length);assert.ok(lastRealm.records.every(row=>[...claims.values()].includes(row.claim)));
      await cleanup();await owner.drain(deadline);if(owner.failures().length)throw owner.failures()[0].error;
      assert.equal(lastClosed?.drained,true);return {original_Blob_witness_closed:true};
    },
    cleanup,
    diagnostic(){return {body_observation:'exact-original-fetch-Response-blob-Promise',epoch,navigation,metadata:metadata.diagnostic(),realm:lastRealm,closed:lastClosed,removable_script:{identifier:scriptIdentifier,removal_attempted:removalAttempted,removed,session_detached:sessionDetached},actual_original_Response_and_app_Blob_required:true,clone_refetch_or_second_Response_consume:false,URL_factory_untouched:false,native_object_URL_calls_observed:true};},
  };
}
module.exports={createOriginalBlobPromiseWitness};
