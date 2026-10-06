import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {test,expect} from './fixtures/test';
const harness=require('./fixtures/harness.cjs');
const sha=(file:string)=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

test('native actual retraining cycle reopens frozen manual review and refuses inadequate quality samples',
  {tag:['@electron','@owned-model']},async({electronSession,workspace,evidence})=>{
  test.setTimeout(360_000);
  test.skip(!process.env.MV_E2E_DINO_WEIGHTS,'Explicit authentic local DINOv3 weights required; no download');
  const {window}=electronSession,backend=await electronSession.waitForBackend();
  const api=(method:string,route:string,body?:unknown):Promise<any>=>window.evaluate(async({port,method,route,body})=>{
    const r=await fetch(`http://127.0.0.1:${port}${route}`,{method,...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    if(!r.ok)throw Error(`${r.status}: ${await r.text()}`);return r.json();
  },{port:backend.port,method,route,body});
  const fixtureScript=path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/dino_classification_data.py');
  const fixture=JSON.parse(execFileSync(harness.resolvePython(),[fixtureScript,workspace.root],{encoding:'utf8'}));
  await api('POST','/api/project/create',{name:'Actual manual model improvement handoff',task:'classification'});
  const project=await api('PUT','/api/project/update',{source_dataset_dir:fixture.source});
  await api('POST','/api/dataset/import',{folder_path:fixture.source,task:'classification'});
  const split=JSON.parse(execFileSync(harness.resolvePython(),[fixtureScript,workspace.root,JSON.stringify(project)],{encoding:'utf8'}));
  const started=await api('POST','/api/automated-training/start',{task:'classification',dataset_path:fixture.source,device:'cpu',mode:'quick',epochs_per_trial:1,
    budget:{max_trials:1,max_total_epochs:1,max_seconds:120,max_memory_mb:4096},
    search_space:{architectures:['dinov3_vits16'],learning_rates:[.001],weight_decays:[.0001],image_sizes:[64],batch_sizes:[2],augmentation_profiles:['none']},
    base_config:{train_mode:'head_only',num_workers:0,pretrained_checkpoint:fixture.weights,pretrained_sha256:fixture.weights_sha256}});
  let parent:any;
  await expect.poll(async()=>{parent=await api('GET','/api/automated-training/jobs/'+started.search_id);if(parent.status==='failed')throw Error(JSON.stringify(parent));return parent.status;},{timeout:125_000}).toBe('completed');
  const parentHash=sha(parent.winner.checkpoint_path);
  const policy=await api('PUT','/api/model-operations/policy',{task:'classification',parent_job_id:parent.winner.trial_id,
    reviewer:'Synthetic functional control',auto_label:false,auto_retrain:true,require_label_review:false,auto_approve:false,auto_deploy:false,
    training_device:'cpu',epochs_per_trial:1,budget:{max_trials:1,max_total_epochs:1,max_seconds:120,max_memory_mb:4096}});
  const fresh=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/operations_new_image.py'),workspace.root,fixture.source],{encoding:'utf8'}));
  const cycle=await api('POST','/api/model-operations/run',{background:false});
  expect(cycle.status,JSON.stringify(cycle)).toBe('awaiting_approval');
  expect(cycle.result.training.status).toBe('completed');
  const subject=cycle.result.review_handoff;
  expect(subject.comparison_sha256).toBe(sha(path.join(project.reports_dir,'model_comparisons',subject.comparison_id+'.json')));
  expect(subject.candidate_checkpoint_sha256).toBe(sha(cycle.result.training.winner.checkpoint_path));
  const route='/api/model-operations/cycles/'+cycle.cycle_id+'/review-handoff';
  const checked=await api('GET',route);
  expect(checked.state).toBe('revalidation_required');expect(checked.next_step).toBeNull();expect(checked.automatic_action).toBe('none');
  expect(checked.reasons.some((reason:string)=>reason.includes('8장'))).toBe(true);
  expect(checked.reasons.some((reason:string)=>reason.includes('계보'))).toBe(false);
  await window.reload();await expect(window.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
  await window.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(5).click();
  await window.getByText('새 데이터 · 자동 라벨 · 모델 개선 운영',{exact:true}).click();
  const operations=window.locator('details').filter({has:window.locator('summary').filter({hasText:'새 데이터 · 자동 라벨 · 모델 개선 운영'})}).last();
  await operations.getByRole('button').filter({hasText:'후보 승인 대기'}).click();
  const review=window.getByRole('region',{name:'모델 개선 수동 검토 연결'});
  await expect(review).toContainText(subject.candidate_job_id);await expect(review).toContainText(subject.comparison_id);
  await expect(review).toContainText('근거 변경 · 재평가 필요');
  await expect(review.getByRole('button',{name:'모델 개선 다음 단계 열기'})).toBeDisabled();
  await evidence.screenshot(window,'native-actual-cycle-manual-quality-refusal');
  await window.reload();
  await window.getByText('새 데이터 · 자동 라벨 · 모델 개선 운영',{exact:true}).click();
  await operations.getByRole('button').filter({hasText:'후보 승인 대기'}).click();
  await expect(review).toContainText(subject.comparison_id);await expect(review.getByRole('button',{name:'모델 개선 다음 단계 열기'})).toBeDisabled();
  const history=await api('GET','/api/model-operations');
  expect(history.cycles.find((row:any)=>row.cycle_id===cycle.cycle_id)).toEqual(cycle);
  expect(await api('GET',route)).toEqual(checked);
  expect((await api('GET','/api/model-deployments/active?'+new URLSearchParams({source_dataset_path:fixture.source,task:'classification'}))).active).toBeNull();
  expect(sha(parent.winner.checkpoint_path)).toBe(parentHash);expect(sha(fresh.path)).toBe(fresh.sha256);
  for(const file of fixture.files)expect(sha(file.path)).toBe(file.sha256);
  evidence.note('manual_operations_handoff',{project,fixture,split,parent,policy,fresh,cycle,checked,
    actual_pretrained_cpu_retraining:true,actual_native_ui_reopen:true,
    inadequate_synthetic_holdout_refused:true,model_approved:false,service_applied:false,device_accepted:false});
});
