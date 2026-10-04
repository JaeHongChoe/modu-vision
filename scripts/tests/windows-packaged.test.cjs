const test=require('node:test');const assert=require('node:assert/strict');
const h=require('../verify_windows_packaged.cjs');
const hash='a'.repeat(64),exe='C:\\bundle\\resources\\backend_bin\\vision_ai_backend.exe';
const rows=['train','evaluate','infer','export'].map(stage=>[stage,{passed:true,evidence:{architecture:'resnet18',device:'cpu',...(stage==='export'?{verdict:'OK',model_steps:{inspection:'passed'}}:{})}}]);
test('source fallback and wrong delivered executable cannot qualify',()=>{
 assert.throws(()=>h.verifyIdentity({packaged:false,execPath:'x',resourcesPath:'x'},'x'),/packaged/);
 assert.throws(()=>h.verifyIdentity({packaged:true,execPath:'other',resourcesPath:'x'},'x'),/executable/);
});
test('four actual CPU stages needed; review and empty model export refuse',()=>{
 h.verifyPreflight(Object.fromEntries(rows));
 for(const stage of ['train','evaluate','infer','export']){const value=structuredClone(Object.fromEntries(rows));value[stage].passed=false;assert.throws(()=>h.verifyPreflight(value));}
 const value=structuredClone(Object.fromEntries(rows));value.export.evidence.verdict='REVIEW';assert.throws(()=>h.verifyPreflight(value));value.export.evidence.verdict='OK';value.export.evidence.model_steps={};assert.throws(()=>h.verifyPreflight(value));
});
test('freeze requires matching actual backend and internal CPU child',()=>{
 const observation={processes:[{kind:'backend',executable:exe,sha256:hash},{kind:'preflight',executable:exe,sha256:hash}],artifacts:[{path:'run/models/job_preflight/best_model.pt',sha256:hash},{path:'run/data/test/NG/NG_test_0.png',sha256:hash}]};
 h.verifyObservation(observation,exe,hash);
 for(const kind of ['backend','preflight'])assert.throws(()=>h.verifyObservation({...observation,processes:observation.processes.filter(p=>p.kind!==kind)},exe,hash));
 assert.throws(()=>h.verifyObservation(observation,exe,'b'.repeat(64)));
 assert.throws(()=>h.verifyObservation({...observation,artifacts:[]},exe,hash));
});
test('environment has no inherited dev backend or secret channels',()=>{
 const env=h.launchEnv({PATH:'ok',VISION_AI_PYTHON:'unsafe',VISION_AI_STUDIO_DEV_SOURCE_BACKEND:'1',GH_TOKEN:'secret',CSC_LINK:'secret',NODE_OPTIONS:'bad'},'C:\\owned');
 assert.equal(env.PATH,'ok');assert.equal(env.VISION_AI_STUDIO_USER_DATA_DIR,'C:\\owned');for(const key of ['VISION_AI_PYTHON','VISION_AI_STUDIO_DEV_SOURCE_BACKEND','GH_TOKEN','CSC_LINK','NODE_OPTIONS'])assert.equal(env[key],undefined);
});
test('persisted results must equal preflight; redact bearer/header capability',()=>{
 const initial={a:1};h.verifyReadback(initial,{a:1});assert.throws(()=>h.verifyReadback(initial,{a:2}));
 const output=h.redact('Authorization: Bearer secret X-Vision-Token: capability');assert(!output.includes('secret'));assert(!output.includes('capability'));
});
test('non-Windows execution refuses before application launch',async()=>{
 if(process.platform!=='win32')await assert.rejects(h.run({}),/Windows target required/);
});
test('JSON headers are also redacted',()=>{
 const output=h.redact('"Authorization":"Bearer secret", "X-Vision-Token":"capability"');assert(!output.includes('secret'));assert(!output.includes('capability'));
});
test('locked private temp cleanup downgrades persisted qualification before inventory reads it',()=>{
 const receipt={status:'passed'},written=[];
 const result=h.finalizeEvidence(receipt,'owned-output','owned-private-temp',[],{remove(){const error=Error('private secret path');error.code='EBUSY';throw error;},write(file,data){written.push([file,data]);}});
 assert.equal(result,false);const saved=JSON.parse(written.find(([name])=>name.endsWith('packaged-receipt.json'))[1]);assert.equal(saved.status,'failed');assert.equal(saved.cleanup,'failed');assert.equal(saved.cleanup_error,'EBUSY');assert(!JSON.stringify(saved).includes('private secret path'));
});
test('passing receipt is written only after owned cleanup succeeds',()=>{
 const receipt={status:'passed'},sequence=[];assert.equal(h.finalizeEvidence(receipt,'output','temp',[],{remove(){sequence.push('removed');},write(file){sequence.push(file);}}),true);assert.equal(sequence[0],'removed');assert.equal(receipt.status,'passed');
});
test('actual API refusal retains bounded diagnostic detail and redacts capability',async()=>{
 const http=require('node:http');
 const server=http.createServer((req,res)=>{res.writeHead(422,{'Content-Type':'application/json'});res.end(JSON.stringify({detail:'Saved project schema unreadable', 'X-Vision-Token':'private-capability',padding:'x'.repeat(10000)}));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const previous=global.window;global.window={api:{getBackendPort:async()=>server.address().port}};
 try{
  const page={evaluate:(fn,arg)=>fn(arg)};
  await assert.rejects(h.api(page,'/api/workers'),error=>{
   assert.match(error.message,/status 422/);assert.match(error.message,/Saved project schema unreadable/);
   assert(!error.message.includes('private-capability'));assert(error.message.length<5000);return true;
  });
 }finally{global.window=previous;await new Promise(resolve=>server.close(resolve));}
});
