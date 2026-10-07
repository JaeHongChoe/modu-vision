import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
test.use({actionTimeout:10_000});
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'native':'browser',name='Derived workflow qualification';
 const seed=(project?:any)=>JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/derived_workflow_data.py'),workspace.root,...(project?[JSON.stringify(project)]:[])],{encoding:'utf8',timeout:15_000}));
 const fixture=seed();await api('/api/project/create',{name,task:'classification'});
 const project=await api('/api/project/update',{source_dataset_dir:fixture.source},'PUT');
 await api('/api/dataset/import',{folder_path:fixture.source,task:'classification'});const split=seed(project);
 const original=path.join(fixture.source,'train/NG/train_NG_00.png');
 const labels=[{id:'box',type:'bbox',label:'NG',category_id:1,bbox:[8,10,40,44],direction_deg:20,text:'A01'}];
 await api('/api/annotations/save',{image_id:path.basename(original,'.png'),image_path:original,image_width:64,image_height:64,actor:'fixture-editor',annotations:labels});
 const originalLabels=await api('/api/annotations/'+path.basename(original,'.png')+'?file_path='+encodeURIComponent(original));
 const explicitTruth=[];
 for(const image of fixture.images.filter((r:any)=>r.split==='test')){
  const row=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(image.path));const verdict=image.relative_path.includes('/OK/')?'OK':'NG';
  const truth=await api('/api/image-truth',{image_path:image.path,task:'classification',classes:['OK','NG'],verdict,defect_classes:verdict==='NG'?['NG']:[],reviewer:'controlled-synthetic-fixture',expected_revision:0,expected_image_revision:row.revision,note:'Known generated class; no representative process quality claim'},'PUT');expect(truth.verdict).toBe(verdict);explicitTruth.push(truth);
 }
 const train=async(source:string,parent?:string,version?:string)=>{
  const accepted=await api('/api/training/start',{task:'classification',preset:'fast',dataset_path:source,device:'cpu',queue:false,max_runtime_s:90,config_overrides:{backbone:'resnet18',pretrained:false,epochs:parent?1:6,image_size:64,batch_size:4,num_workers:0,learning_rate:parent?1e-12:.001},...(parent?{warm_start_job_id:parent}:{}),...(version?{dataset_version_id:version}:{})});
  let terminal:any;await expect.poll(async()=>{terminal=await api('/api/training/status?job_id='+accepted.job_id);if(['failed','cancelled'].includes(terminal.status))throw Error(JSON.stringify(terminal));return terminal.status;},{timeout:95_000}).toBe('completed');return {accepted,terminal};
 };
 const originalSnapshot=await api('/api/dataset/versions',{name:'Before derived edits',note:'Controlled synthetic data; not quality acceptance'});
 const baseline=await train(fixture.source,undefined,originalSnapshot.id);
 const pipeline=await api('/api/flowchart/templates/single-segmentation?inspection_task=classification&job_id='+baseline.accepted.job_id);
 const saved=await api('/api/flowchart/pipeline?'+new URLSearchParams({source_dataset_path:fixture.source,recipe_task:'classification'}),pipeline);
 const cohort=await api('/api/flow-evaluations/cohorts',{version_id:saved.version_id,name:'Unchanged synthetic test cohort'});expect(cohort.count).toBe(16);expect(cohort.scope.classes).toEqual(['OK','NG']);expect(cohort.samples.filter((r:any)=>r.truth.verdict==='OK')).toHaveLength(8);expect(cohort.samples.filter((r:any)=>r.truth.verdict==='NG')).toHaveLength(8);
 const navigate=async(stage=0)=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(stage).click();};
 await navigate();await page.getByRole('button',{name:path.basename(original)+' 라벨링에서 열기',exact:true}).click();
 await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();const review=page.getByRole('region',{name:'이미지 정보와 검토 기록'});await review.getByLabel('작업자·검토자 이름').fill('fixture-editor');await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();
 await page.locator('summary').filter({hasText:'원본 보존 이미지 편집'}).click();const edit=page.getByRole('region',{name:'파생 이미지 편집'});
 await edit.getByLabel('파생 이미지 정렬 각도').fill('30');let wire=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/data-workbench/derived'&&r.request().method()==='POST');await edit.getByRole('button',{name:'정렬 새 버전 저장',exact:true}).click();let response=await wire;expect(response.ok(),await response.text()).toBe(true);const aligned=await response.json();expect(aligned.operation).toEqual({kind:'align',degrees:30});expect(aligned.annotations[0]).toMatchObject({type:'polygon',direction_deg:50,text:'A01'});
 await expect(edit.getByLabel('파생 편집 기준 버전')).toHaveValue(aligned.id);await expect(edit.getByRole('img',{name:'편집 원본과 변환 라벨 미리보기'})).toHaveAttribute('viewBox',`0 0 ${aligned.size.join(' ')}`);
 await edit.getByLabel('파생 이미지 밝기 배율').fill('0.7');wire=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/data-workbench/derived'&&r.request().method()==='POST');await edit.getByRole('button',{name:'밝기 새 버전 저장',exact:true}).click();response=await wire;expect(response.ok(),await response.text()).toBe(true);const bright=await response.json();expect(bright.parent_id).toBe(aligned.id);expect(bright.source_sha256).toBe(aligned.source_sha256);
 await expect(edit.getByLabel('파생 편집 기준 버전')).toHaveValue(bright.id);await expect(edit.getByRole('button',{name:'검수한 파생본으로 새 학습 데이터 저장',exact:true})).toBeDisabled();
 await edit.getByLabel('파생본 검수 근거').fill('Controlled synthetic coordinates and blank edges inspected; not quality acceptance');await edit.getByRole('button',{name:'변환 이미지·라벨 검수 후 채택 후보',exact:true}).click();await expect(edit.getByText('채택 후보 · fixture-editor',{exact:false})).toBeVisible();await evidence.screenshot(page,`${prefix}-aligned-labels-explicit-review`);
 await navigate();await page.getByRole('button',{name:path.basename(original)+' 라벨링에서 열기',exact:true}).click();await page.locator('summary').filter({hasText:'원본 보존 이미지 편집'}).click();await expect(edit.getByLabel('파생 편집 기준 버전')).toBeVisible();await edit.getByLabel('파생 편집 기준 버전').selectOption(bright.id);await expect(edit.getByText('채택 후보 · fixture-editor',{exact:false})).toBeVisible();
 await edit.getByLabel('파생본 새 학습 데이터 이름').fill('Reviewed derived training source');await edit.getByRole('button',{name:'검수한 파생본으로 새 학습 데이터 저장',exact:true}).click();await expect(edit.getByRole('status')).toContainText('새 학습 데이터 Reviewed derived training source');
 const version=(await api('/api/data-workbench/derived-adoptions')).versions[0];expect(version.fixed_cohorts.map((r:any)=>r.cohort_id)).toContain(cohort.cohort_id);expect(version.fixed_test_records).toHaveLength(16);expect((await api('/api/project/current')).source_dataset_dir).toBe(fixture.source);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();await page.getByRole('button',{name:'파생 편집 · 검수한 학습 데이터 버전',exact:true}).click();const versions=page.getByRole('region',{name:'파생 편집 데이터 버전'});
 await versions.getByRole('button',{name:'검수한 파생 버전을 데이터 원본으로 선택',exact:true}).click();await expect.poll(async()=>(await api('/api/project/current')).source_dataset_dir).toBe(version.source_dataset_path);await expect(versions.getByRole('button',{name:'검증한 편집 전 원본으로 돌아가기',exact:true})).toBeEnabled();await versions.getByRole('button',{name:'닫기',exact:true}).click();
 const adopted=version.adopted[0],image=path.join(version.source_dataset_path,adopted.relative_path);let row=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(image));expect(row).toMatchObject({workflow_state:'needs_review',usage_state:'not_used'});
 await page.getByRole('button',{name:path.basename(image)+' 라벨링에서 열기',exact:true}).click();await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();await review.getByLabel('작업자·검토자 이름').fill('fixture-reviewer');await review.getByRole('button',{name:'라벨 승인',exact:true}).click();await expect.poll(async()=>(await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(image))).workflow_state).toBe('approved');
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();await page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true}).click();const data=page.getByRole('region',{name:'데이터 검토와 라벨 교환'});
 // The derivative is beyond the first page. The explicit approved-state filter
 // brings the one reviewed new input into view, without including every row.
 await data.getByLabel('검토 상태 필터').selectOption('approved');await data.getByLabel(adopted.relative_path+' 선택',{exact:true}).check();await data.getByLabel('선택 이미지 학습 사용 여부').selectOption('active');await data.getByRole('button',{name:'선택 이미지에 정보 적용',exact:true}).click();await expect.poll(async()=>(await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(image))).usage_state).toBe('active');
 row=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(image));const snapshot=await api('/api/dataset/versions',{name:'Reviewed derived input',note:'New branch and labels bound; original heldout immutable'});const candidate=await train(version.source_dataset_path,baseline.accepted.job_id,snapshot.id);
 await navigate(3);
 // Evaluation hydrates the preferred model asynchronously and reloads the
 // comparison catalog. Wait for that exact job before choosing the manual
 // pair, so a late catalog load cannot disable the button during the click.
 await expect(page.getByText(`[${candidate.accepted.job_id}]`,{exact:true})).toBeVisible();
 const compare=page.getByRole('region',{name:'현행과 후보 모델 비교'});
 const incumbentChoice=compare.getByLabel('비교 기준 모델',{exact:true}),candidateChoice=compare.getByLabel('후보 모델',{exact:true});
 await incumbentChoice.selectOption(baseline.accepted.job_id);await candidateChoice.selectOption(candidate.accepted.job_id);
 await expect(incumbentChoice).toHaveValue(baseline.accepted.job_id);await expect(candidateChoice).toHaveValue(candidate.accepted.job_id);
 await expect(compare).toContainText('실행 위치: 로컬 CPU');await expect(compare.getByLabel('test 이미지 수',{exact:true})).toHaveValue('0');
 const compareButton=compare.getByRole('button',{name:'동일 test 이미지로 비교',exact:true});await expect(compareButton).toBeEnabled();
 wire=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/evaluation/model-comparisons/jobs'&&r.request().method()==='POST');
 await compareButton.click();response=await wire;expect(response.ok(),await response.text()).toBe(true);
 const submittedComparison=response.request().postDataJSON();expect(submittedComparison).toMatchObject({source_dataset_path:version.source_dataset_path,task:'classification',incumbent_job_id:baseline.accepted.job_id,candidate_job_id:candidate.accepted.job_id,full_test:true,execution_target:'local_cpu',compute_profile_id:null,device:'cpu'});
 const comparisonJob=await response.json();const query='?'+new URLSearchParams({source_dataset_path:version.source_dataset_path,task:'classification'});let completed:any;await expect.poll(async()=>{completed=await api('/api/evaluation/model-comparisons/jobs/'+comparisonJob.job_id+query);if(completed.status==='failed')throw Error(JSON.stringify(completed));return completed.status;},{timeout:65_000}).toBe('completed');
 const comparison=await api('/api/evaluation/model-comparisons/'+completed.report_id+query);expect(comparison.images).toHaveLength(16);expect(comparison.summary.error_images).toBe(0);expect(comparison.summary.known_ok_images).toBe(8);expect(comparison.summary.known_ng_images).toBe(8);const assessment=await api('/api/model-deployments/assess/'+completed.report_id+query);expect(assessment.reasons.join(' ')).not.toMatch(/계보|출처|체크포인트|stale/i);
 const approval=page.getByRole('region',{name:'모델 승인과 롤백'});await approval.getByRole('button',{name:'새로고침',exact:true}).click();await approval.getByRole('combobox',{name:'저장된 후보 비교',exact:true}).selectOption(completed.report_id);await expect(approval.getByText(`후보 ${candidate.accepted.job_id}`,{exact:false})).toBeVisible();await expect(approval.getByRole('checkbox')).not.toBeChecked();await expect(approval.getByRole('button',{name:'후보 승인 revision 저장',exact:true})).toBeDisabled();expect((await api('/api/model-deployments/active'+query)).active).toBeNull();await evidence.screenshot(page,`${prefix}-same-fixed-heldout-comparison-assessment`);
 await navigate();await page.getByRole('button',{name:'파생 편집 · 검수한 학습 데이터 버전',exact:true}).click();await expect(versions.getByRole('article',{name:version.name})).toBeVisible();await versions.getByRole('button',{name:'검증한 편집 전 원본으로 돌아가기',exact:true}).click();await expect.poll(async()=>(await api('/api/project/current')).source_dataset_dir).toBe(fixture.source);await evidence.screenshot(page,`${prefix}-verified-original-source-return`);
 for(const image of fixture.images){expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);expect(sha(fs.readFileSync(path.join(version.source_dataset_path,image.relative_path)))).toBe(image.sha256);evidence.addFile(image.path);}
 expect((await api('/api/annotations/'+path.basename(original,'.png')+'?file_path='+encodeURIComponent(original))).annotations).toEqual(originalLabels.annotations);
 for(const file of [split.split_path,path.join(path.dirname(version.source_dataset_path),'record.json'),bright.file_path,path.join(bright.dataset_path,'review.json'),image])evidence.addFile(file);
 evidence.note('derived_workflow_closure',{project,fixture,split,original,originalLabels,explicitTruth,originalSnapshot,baseline,pipeline,saved,cohort,aligned,bright,version,row,snapshot,candidate,submittedComparison,comparisonJob,completed,comparison,assessment,exact_candidate_evaluation_hydrated_before_comparison:true,actual_ui_edit_review_adopt_select_include_compare_return:true,actual_original_and_warm_start_cpu_training:true,reapproval_connected_human_attestation_unchecked:true,synthetic_not_quality_or_independent_human_approval:true});
}
test('derived edits reach reviewed new data actual retraining fixed comparison and verified undo',async({page,request,renderer,workspace,evidence})=>{test.setTimeout(300_000);await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);});
test('native derived review branch retraining comparison and original return',{tag:'@electron'},async({electronSession,workspace,evidence})=>{test.setTimeout(300_000);const {window}=electronSession,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned derivative API ${r.status}: ${await r.text()}`);return r.json();},{port:status.port,route,body,method});await exercise(window,workspace,evidence,api,true);});
