import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {test,expect} from './fixtures/test';
const harness=require('./fixtures/harness.cjs');
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');

test('native complete improvement cycle prepares an inactive learned candidate, reviews graph and serves its package',
 {tag:['@electron','@owned-model']},async({electronSession,workspace,evidence})=>{
 test.setTimeout(600_000);
 test.skip(!process.env.MV_E2E_DINO_WEIGHTS,'Explicit authentic local DINOv3 weights required; no download');
 const page=electronSession.window,backend=await electronSession.waitForBackend();
 const api=(route:string,body?:unknown,method?:string):Promise<any>=>page.evaluate(async({port,route,body,method})=>{
  const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  const value=await response.json();if(!response.ok)throw Error(`${route}: ${response.status} ${JSON.stringify(value)}`);return value;
 },{port:backend.port,route,body,method});
 const seed=path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/operations_classification_data.py');
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[seed,workspace.root],{encoding:'utf8',env:{...process.env,MV_E2E_RUN_DIR:process.env.MV_E2E_RUN_DIR}}));
 expect(fixture.distinct_heldout_hashes).toBe(24);
 await api('/api/project/create',{name:'Actual CPU improvement to independent candidate flow',task:'classification'});
 const project=await api('/api/project/update',{source_dataset_dir:fixture.source},'PUT');
 await api('/api/dataset/import',{folder_path:fixture.source,task:'classification'});
 const split=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/dino_classification_data.py'),workspace.root,JSON.stringify(project)],{encoding:'utf8'}));
 const started=await api('/api/automated-training/start',{task:'classification',dataset_path:fixture.source,device:'cpu',mode:'quick',epochs_per_trial:1,
  budget:{max_trials:1,max_total_epochs:1,max_seconds:120,max_memory_mb:4096},
  search_space:{architectures:['dinov3_vits16'],learning_rates:[.000001],weight_decays:[.0001],image_sizes:[64],batch_sizes:[2],augmentation_profiles:['none']},
  base_config:{train_mode:'head_only',num_workers:0,pretrained_checkpoint:fixture.weights,pretrained_sha256:fixture.weights_sha256}});
 let parent:any;
 await expect.poll(async()=>{parent=await api('/api/automated-training/jobs/'+started.search_id);if(parent.status==='failed')throw Error(JSON.stringify(parent));return parent.status;},{timeout:135_000}).toBe('completed');
 const parentHash=sha(parent.winner.checkpoint_path),parentId=parent.winner.trial_id;
 const graph=await api('/api/flowchart/templates/single-segmentation?'+new URLSearchParams({job_id:parentId,inspection_task:'classification'}));
 const saved=await api('/api/flowchart/pipeline?'+new URLSearchParams({recipe_task:'classification',source_dataset_path:fixture.source,change_reason:'Synthetic functional incumbent flow before explicit candidate graph preparation'}),graph);
 const activePath=path.join(project.project_dir,'flowcharts/active.json'),activeBefore=fs.readFileSync(activePath);
 const scope=await api('/api/flow-evaluations/scope/'+saved.version_id);
 const heldout=fixture.files.filter((row:any)=>row.split==='test');
 for(const file of heldout){
  const params=new URLSearchParams({image_path:file.path,task:scope.task,class_roles:JSON.stringify(scope.class_semantics.roles)});
  for(const name of scope.classes)params.append('classes',name);
  const truth=await api('/api/image-truth?'+params);
  await api('/api/image-truth',{image_path:file.path,task:scope.task,classes:scope.classes,class_roles:scope.class_semantics.roles,
   verdict:file.label==='OK'?'OK':'NG',defect_classes:file.label==='OK'?[]:[file.label],reviewer:'Synthetic functional authority',
   expected_revision:truth.truth_revision,expected_image_revision:truth.image_revision,note:'Authored synthetic pixel class; not representative manufacturing truth'},'PUT');
 }
 const policy=await api('/api/model-operations/policy',{task:'classification',parent_job_id:parentId,
  reviewer:'Synthetic functional authority',auto_label:false,auto_retrain:true,require_label_review:true,auto_approve:false,auto_deploy:false,
  training_device:'cpu',epochs_per_trial:1,budget:{max_trials:1,max_total_epochs:1,max_seconds:120,max_memory_mb:4096}},'PUT');
 const fresh=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/operations_new_image.py'),workspace.root,fixture.source],{encoding:'utf8'}));
 const waiting=await api('/api/model-operations/run',{background:false});expect(waiting.status,JSON.stringify(waiting)).toBe('needs_review');
 let metadata=await api('/api/dataset/metadata/image?'+new URLSearchParams({image_path:fresh.path}));
 await api('/api/annotations/save',{image_id:path.basename(fresh.path,'.png'),image_path:fresh.path,image_width:64,image_height:48,
  annotations:[{type:'tag',label:'scratch',category_id:1}],expected_revision:metadata.revision,actor:'Synthetic functional authority'});
 metadata=await api('/api/dataset/metadata/image?'+new URLSearchParams({image_path:fresh.path}));
 const reviewed=await api('/api/dataset/metadata/'+metadata.image_uuid,{expected_revision:metadata.revision,actor:'Synthetic functional authority',changes:{workflow_state:'approved'}},'PATCH');
 expect(reviewed.workflow_state).toBe('approved');
 const cycle=await api('/api/model-operations/run',{background:false});
 expect(cycle.status,JSON.stringify(cycle)).toBe('awaiting_approval');
 const candidate=cycle.result.training.winner,subject=cycle.result.review_handoff;
 expect(cycle.result.training.status).toBe('completed');expect(candidate.trial_id).not.toBe(parentId);
 expect(cycle.result.evaluation.comparison.selected_image_count).toBe(24);
 const ready=await api('/api/model-operations/cycles/'+cycle.cycle_id+'/review-handoff');
 expect(ready.state,JSON.stringify(ready)).toBe('awaiting_human_review');
 const approval=await api('/api/model-deployments/approve',{source_dataset_path:fixture.source,task:'classification',comparison_id:subject.comparison_id,
  reviewer:'Synthetic functional authority',reason:'Actual learned candidate and incumbent on 24 distinct controlled holdout inputs; not manufacturing quality acceptance',holdout_reviewed:true});
 await page.reload();await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(5).click();
 await page.getByText('새 데이터 · 자동 라벨 · 모델 개선 운영',{exact:true}).click();
 const operations=page.locator('details').filter({has:page.locator('summary').filter({hasText:'새 데이터 · 자동 라벨 · 모델 개선 운영'})}).last();
 await operations.getByRole('button').filter({hasText:'후보 승인 대기'}).click();
 const panel=page.getByRole('region',{name:'모델 개선 수동 검토 연결'});
 await expect(panel).toContainText('현재 모델 승인 확인');
 await panel.getByLabel('개선 후보 원본 플로우',{exact:true}).selectOption(saved.version_id);
 await panel.getByLabel('후보 플로우 준비 담당자',{exact:true}).fill('Synthetic functional authority');
 await panel.getByLabel('후보 플로우 준비 이유',{exact:true}).fill('Independent learned candidate graph for explicit separate process review; leave the incumbent active');
 await panel.getByRole('button',{name:'검토용 후보 플로우 준비',exact:true}).click();
 await expect(panel).toContainText('전체 흐름 검토 대기',{timeout:30_000});
 const handoff=await api('/api/model-operations/cycles/'+cycle.cycle_id+'/review-handoff'),prepared=handoff.prepared_flow;
 expect(prepared.flow_activated).toBe(false);expect(prepared.service_applied).toBe(false);
 expect(fs.readFileSync(activePath).equals(activeBefore)).toBe(true);expect(sha(parent.winner.checkpoint_path)).toBe(parentHash);
 await evidence.screenshot(page,'native-actual-learned-candidate-inactive');
 await page.reload();await page.getByText('새 데이터 · 자동 라벨 · 모델 개선 운영',{exact:true}).click();
 await operations.getByRole('button').filter({hasText:'후보 플로우 검토 대기'}).click();
 await expect(panel).toContainText(prepared.version_id);await expect(panel.getByRole('button',{name:'모델 개선 다음 단계 열기'})).toContainText('후보 플로우 검토 단계 열기');
 evidence.note('actual_improvement_preparation',{fixture,project,split,parent,policy,fresh,waiting,reviewed,cycle,approval,prepared,
  actual_pretrained_cpu_training:true,actual_retraining:true,actual_native_candidate_preparation:true,
  independent_candidate_graph:true,incumbent_active_graph_unchanged:true,synthetic_control:true,
  manufacturing_quality_accepted:false,device_accepted:false,physical_target_verified:false,windows_excluded:true});
 const cohort=await api('/api/flow-evaluations/cohorts',{version_id:prepared.version_id,name:'24 distinct authored synthetic holdout pixels'});
 const evaluation=await api('/api/flow-evaluations',{version_id:prepared.version_id,cohort_id:cohort.cohort_id});
 expect(evaluation.status,JSON.stringify(evaluation)).toBe('completed');expect(evaluation.coverage.known).toBe(24);
 const whole=await api('/api/flow-evaluations/approvals',{evaluation_id:evaluation.evaluation_id,reviewer:'Synthetic functional authority',
  reason:'Functional control under intentionally permissive synthetic process policy; no representative quality or field acceptance',holdout_reviewed:true,
  policy:{policy_id:'synthetic-improvement-functional',revision:1,minimum_normal:8,minimum_defect:8,maximum_escape_rate:1,maximum_overkill_rate:1,maximum_review_rate:1}});
 const exported=await api('/api/export/flow',{source_dataset_path:fixture.source,recipe_task:'classification',package_name:'learned_improvement_control',version_id:prepared.version_id,
  parity_images:heldout.map((row:any)=>({path:row.path})),parity_device:'cpu',approval_revision_ids:{[candidate.trial_id]:approval.revision.revision_id}});
 expect(exported.parity.status,JSON.stringify(exported)).toBe('passed');
 const qualified=await api('/api/flow-evaluations/approvals/'+whole.revision_id+'/qualify-package',{package_path:exported.package_path,device:'cpu'});
 try{
  const applied=await api('/api/runtime-services/apply',{package_path:exported.package_path,device:'cpu',reviewer:'Synthetic functional authority',whole_flow_revision_id:whole.revision_id});
  expect(applied.ack.status,JSON.stringify(applied)).toBe('ready');
  const config=JSON.parse(fs.readFileSync(path.join(project.project_dir,'runtime_service/service.json'),'utf8'));evidence.redact(config.token);
  const sent=await fetch(`http://127.0.0.1:${config.port}/v1/jobs/upload`,{method:'POST',headers:{'X-Vision-Token':config.token},body:fs.readFileSync(heldout[0].path)});
  expect(sent.status).toBe(202);const jobId=(await sent.json() as any).job_id;let result:any;
  await expect.poll(async()=>{result=await(await fetch(`http://127.0.0.1:${config.port}/v1/jobs/${jobId}`,{headers:{'X-Vision-Token':config.token}})).json();return result.state;},{timeout:40_000}).toBe('completed');
  expect(fs.readFileSync(activePath).equals(activeBefore)).toBe(true);expect(sha(parent.winner.checkpoint_path)).toBe(parentHash);expect(sha(fresh.path)).toBe(fresh.sha256);
  for(const file of fixture.files)expect(sha(file.path)).toBe(file.sha256);
  const state=await api('/api/runtime-services');
  const delivered=await api('/api/model-operations/cycles/'+cycle.cycle_id+'/review-handoff');
  expect(delivered.delivery.whole_flow_current).toBe(true);expect(delivered.delivery.service_application_recorded).toBe(true);
  expect(delivered.delivery.service_runtime_ready).toBe(true);expect(delivered.delivery.device_accepted).toBe(false);
  await page.reload();await page.getByText('새 데이터 · 자동 라벨 · 모델 개선 운영',{exact:true}).click();
  await operations.getByRole('button').filter({hasText:'후보 플로우 검토 대기'}).click();
  await expect(panel).toContainText('현재 전체 흐름 검토 확인');await expect(panel).toContainText('현재 독립 서비스 응답 확인');
  await evidence.screenshot(page,'native-actual-learned-candidate-service-readback');
  evidence.note('actual_improvement_cycle',{fixture,project,split,parent,policy,fresh,waiting,reviewed,cycle,approval,prepared,cohort,evaluation,whole,exported,qualified,state,result,delivered,
   actual_pretrained_cpu_training:true,actual_retraining:true,actual_native_candidate_preparation:true,actual_managed_cpu_inference:true,
   independent_candidate_graph:true,incumbent_active_graph_unchanged:true,synthetic_control:true,
   manufacturing_quality_accepted:false,device_accepted:false,physical_target_verified:false,windows_excluded:true});
 }finally{evidence.note('owned_service_cleanup',await api('/api/runtime-services/stop',{}));}
});
