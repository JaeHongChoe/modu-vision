'use strict';
// Evidence validator only. It creates no requests, cache entries, models or UI.
const assert=require('node:assert/strict');
const DETAIL='Controlled exact owning selected-node POST unavailable';
const SHA=/^[a-f0-9]{64}$/;
function request(row,scope){
 assert.equal(row.method,'POST');assert.equal(row.origin,scope.origin);assert.equal(row.path,'/api/flowchart/run');assert.equal(row.search,'');assert.equal(row.main_frame,true);
 assert.equal(row.project_header,scope.project.id);assert.equal(row.context.project_id,scope.project.id);
 if(scope.context)assert.deepEqual(row.context,scope.context);else assert.equal(row.context.mode,'local');
 for(const key of ['workspace_id','actor_id'])assert.match(row.context[key],/^[A-Za-z0-9_.:-]{1,128}$/);
 const expected={project_id:scope.project.id,pipeline:scope.pipeline,image_path:scope.image.path,image_id:scope.image.image_id,execution_target:'local',device:'cpu',stop_node_id:scope.stop_node_id};
 const prepared=row.preparation;assert.ok(prepared);assert.equal(prepared.status,200);
 assert.deepEqual(prepared.request,{method:'POST',origin:scope.origin,path:'/api/flowchart/partial-runs/prepare',search:'',main_frame:true,project_header:scope.project.id,context:row.context,body:expected});
 assert.match(prepared.body.run_id,/^[a-f0-9]{32}$/);
 for(const [key,value] of Object.entries({project_id:scope.project.id,stop_node_id:scope.stop_node_id,execution_target:'local',device:'cpu',state:'prepared',execution_finished:false}))assert.equal(prepared.body[key],value);
 assert.deepEqual(row.body,{...expected,partial_run_id:prepared.body.run_id});
 assert.equal(scope.pipeline.nodes.find(n=>n.id===scope.stop_node_id).data.model_job_id,'job_fixture_incumbent');
}
function failure(row){assert.equal(row.status,503);assert.deepEqual(row.body,{detail:DETAIL});assert.equal(row.controlled_response,true);assert.equal(row.original_backend_execution,false);}
function partial(row,scope,cache){
 assert.equal(row.status,200);assert.equal(row.controlled_response,false);assert.equal(row.original_backend_execution,true);
 const v=row.body;assert.equal(v.status,'partial');assert.equal(v.final_verdict,'REVIEW');assert.equal(v.is_ok,false);assert.equal(v.stop_node_id,scope.stop_node_id);
 assert.equal(v.partial_run_id,row.request.preparation.body.run_id);
 assert.equal(v.execution_target,'local');assert.equal(v.execution_device,'cpu');assert.equal(v.compute_profile_id,null);
 assert.equal(v.debug_cache.status,cache);assert.match(v.debug_cache.identity_sha256,SHA);
 assert.ok(Array.isArray(v.execution_steps)&&v.execution_steps.length>0);assert.ok(Array.isArray(v.crops)&&v.crops.length>0);
 assert.ok(v.execution_steps.every(s=>s.status!=='error'&&s.status!=='warning_untrained'));
 if(cache==='hit')assert.ok(v.execution_steps.every(s=>s.latency_ms===0));
}
function sequence(rows,A,B){
 assert.notEqual(A.project.id,B.project.id);assert.notEqual(A.project.project_dir,B.project.project_dir);assert.notEqual(A.image.path,B.image.path);
 assert.deepEqual(rows.map(r=>r.label),['error-A','retry-A','repeat-A','reopen-A','handoff-B','return-A']);
 for(const r of rows){const scope=r.label==='handoff-B'?B:A;request(r.request,scope);if(scope===A)assert.deepEqual(r.request.context,rows[0].request.context);assert.equal(r.deadline,r.started+10000);assert.ok(Number.isFinite(r.finished)&&r.finished>=r.started&&r.finished<=r.deadline);assert.equal(r.finished_normally,true);}
 failure(rows[0]);const succeeded=rows.slice(1);for(let i=0;i<succeeded.length;i++)partial(succeeded[i],i===3?B:A,i===0||i===3?'miss':'hit');
 const first=succeeded[0].body;for(const r of [succeeded[1],succeeded[2],succeeded[4]]){assert.equal(r.body.debug_cache.identity_sha256,first.debug_cache.identity_sha256);assert.deepEqual(r.body.crops,first.crops);}
 assert.notEqual(succeeded[3].body.debug_cache.identity_sha256,first.debug_cache.identity_sha256);
 return {cells:['F064.actual-partial-node-cache-control.error','F064.actual-partial-node-cache-control.reopen','F064.actual-partial-node-cache-control.handoff'],actual_success_bodies:5,controlled_error_bodies:1,partial_never_final_ok:true};
}
function discardConfirmation(row){
 assert.equal(row.page_matches_owner,true);assert.equal(row.type,'confirm');
 assert.equal(row.message,'저장하지 않은 플로우 변경 사항을 버리고 선택한 버전을 열까요?');
 assert.equal(row.count,1);assert.ok(Number.isFinite(row.started)&&Number.isFinite(row.deadline)&&row.started<row.deadline);
 return true;
}
module.exports={DETAIL,request,failure,partial,sequence,discardConfirmation};
