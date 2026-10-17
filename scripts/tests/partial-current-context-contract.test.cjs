'use strict';
const assert=require('node:assert/strict'),test=require('node:test');
const contract=require('../e2e/fixtures/partialNodeCacheContract.cjs');
const startup=require('../e2e/fixtures/owned-startup-evaluation.cjs');
const ID='a'.repeat(32),context={workspace_id:'workspace',project_id:'project',actor_id:'actual-owner',mode:'team'};
const scope={origin:'https://127.0.0.1:45555',project:{id:'project'},context,
 pipeline:{nodes:[{id:'stop',data:{model_job_id:'job_fixture_incumbent'}}]},image:{path:'/source/test.png',image_id:'source-image'},stop_node_id:'stop'};
const body={project_id:'project',pipeline:scope.pipeline,image_path:scope.image.path,image_id:scope.image.image_id,execution_target:'local',device:'cpu',stop_node_id:'stop'};
const request=(path,body)=>({method:'POST',origin:scope.origin,path,search:'',main_frame:true,project_header:'project',context:{...context},body});
const prepared=()=>({status:200,request:request('/api/flowchart/partial-runs/prepare',structuredClone(body)),body:{run_id:ID,project_id:'project',stop_node_id:'stop',execution_target:'local',device:'cpu',state:'prepared',execution_finished:false}});
const run=()=>({...request('/api/flowchart/run',{...structuredClone(body),partial_run_id:ID}),preparation:prepared()});
test('current real team owner binds prepare and run to exact actor/project/CPU capability',()=>assert.doesNotThrow(()=>contract.request(run(),scope)));
for(const mutation of [r=>r.context.actor_id='foreign',r=>r.preparation.request.context.actor_id='foreign',r=>r.body.partial_run_id='b'.repeat(32),r=>delete r.preparation]){
 test('current request refuses changed or absent original capability/context '+mutation.toString(),()=>{const r=run();mutation(r);assert.throws(()=>contract.request(r,scope));});
}
const frame={};const page={mainFrame:()=>frame};
const startupScope={project:scope.project,context,source:'/source',modelHashes:{job_fixture_incumbent:'c'.repeat(64)}};
const startupRequest=actor=>({url:()=>scope.origin+'/api/evaluation/results?source_dataset_path=%2Fsource&source_task=classification',method:()=> 'GET',frame:()=>frame,headers:()=>({'x-vision-project':'project','x-vision-context':JSON.stringify({...context,actor_id:actor})})});
test('passive startup observer admits only the exact captured team owner context',()=>{
 assert.equal(startup.ownedRequest(startupRequest('actual-owner'),page,scope.origin,[startupScope]).identity.mode,'team');
 assert.throws(()=>startup.ownedRequest(startupRequest('foreign'),page,scope.origin,[startupScope]));
});
test('legacy no-context startup contract continues to require local scope',()=>{
 const local={...startupScope};delete local.context;
 assert.throws(()=>startup.ownedRequest(startupRequest('actual-owner'),page,scope.origin,[local]));
});
