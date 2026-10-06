const assert=require('node:assert/strict'),test=require('node:test'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function fixture(){
 const file=path.resolve(__dirname,'../../services/modelExecution.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);
 const project={projectDir:'/project',project:{id:'one',source_dataset_dir:'/source',active_labelset_id:'one'}};const state={selectedProfileId:'server',isLoaded:true,profiles:[{id:'server',name:'Owned server',gpu_selector:'2'}],transportRevision:0},calls=[];let reply;
 m.require=k=>({'./api':{getApiPersistenceIdentity:()=> 'owned',getProjectContextGeneration:()=>0,request:async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});if(reply)return reply();return{source_sha256:'verified',execution:{compute_profile_id:'server',device:'cpu'}};}},'../stores/useComputeStore':{useComputeStore:{getState:()=>state}},'../stores/useProjectStore':{useProjectStore:{getState:()=>project}}}[k]);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
 return {state,project,calls,m:m.exports,setReply:f=>reply=f};
}
test('every native specialist uses the selected profile and requested server CPU without local invocation',async()=>{
 for(const [task,stage] of [['rotation','predict'],['ocr','evaluate'],['rotated_detection','predict'],['enhancement','evaluate'],['defect_gan','generate'],['patch_classification','predict']]){
  const f=fixture();await f.m.executeModelRecipe(task,stage,{job_id:'saved',device:'cpu',image_path:'/source/image'},()=>assert.fail('no local execution'));
  assert.deepEqual(f.calls[0].body,{task,stage,execution_target:'selected_compute',compute_profile_id:'server',device:'cpu',params:{job_id:'saved',image_path:'/source/image'}});
 }
});
test('selection failure and unavailable target never execute the local callback',async()=>{
 const f=fixture();f.setReply(()=>{throw Error('selected connection lost');});await assert.rejects(()=>f.m.executeModelRecipe('ocr','predict',{job_id:'model',device:'cpu'},()=>assert.fail()),/connection lost/);
 f.state.isLoaded=false;await assert.rejects(()=>f.m.executeModelRecipe('ocr','predict',{job_id:'model',device:'cpu'},()=>assert.fail()),/설정/);
 f.state.isLoaded=true;await assert.rejects(()=>f.m.executeModelRecipe('ocr','predict',{job_id:'model',device:'mps'},()=>assert.fail()),/MPS/);
});
test('result is discarded when server selection changes during the request',async()=>{
 const f=fixture();f.setReply(()=>{f.state.selectedProfileId=null;return{source_sha256:'old'};});
 await assert.rejects(()=>f.m.executeModelRecipe('ocr','predict',{job_id:'model',device:'cpu'},()=>assert.fail()),/변경/);
});
test('explicit local trial persists a recipe and refreshes only its current model context',async()=>{
 const f=fixture();f.state.selectedProfileId=null;const notifications=[];const stop=f.m.subscribeModelRecipes(row=>notifications.push(row));
 const result=await f.m.executeModelRecipe('rotation','predict',{job_id:'saved',device:'cpu',image_path:'/source/image'},()=>assert.fail('legacy bypass must not run'));
 assert.equal(result.source_sha256,'verified');assert.deepEqual(f.calls[0].body,{task:'rotation',stage:'predict',execution_target:'local',device:'cpu',params:{job_id:'saved',image_path:'/source/image'}});
 assert.equal(notifications.length,1);assert.equal(notifications[0].task,'rotation');assert.equal(notifications[0].jobId,'saved');assert.equal(notifications[0].context,f.m.getExecutionContextIdentity());stop();
 await f.m.executeModelRecipe('rotation','predict',{job_id:'saved',device:'cpu'});assert.equal(notifications.length,1);
});
test('changed project and local target discard late replies and emit no completion',async()=>{
 for(const change of [f=>{f.project.project.id='two';},f=>{f.state.selectedProfileId='server';}]){
  const f=fixture();f.state.selectedProfileId=null;const notifications=[];f.m.subscribeModelRecipes(v=>notifications.push(v));f.setReply(()=>{change(f);return{source_sha256:'stale'};});
  await assert.rejects(()=>f.m.executeModelRecipe('rotation','predict',{job_id:'saved',device:'cpu'}),/변경/);assert.equal(notifications.length,0);
 }
});
