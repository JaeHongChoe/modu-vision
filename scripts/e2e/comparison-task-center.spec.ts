import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png, openTaskCenter} from './qa/appFlow';
const harness = require('./fixtures/harness.cjs');
test.use({actionTimeout: 10_000});
type OwnedApi = (route: string, body?: unknown) => Promise<any>;
type NativeMetadataRead = (url: string, nonce: string, expiresAt: number, contextHeaders: Record<string,string>) => Promise<{
  status: number; headers: Record<string,string>; body: number[];
}>;
const metadataDelegateParameter='mv_e2e_current_label_delegate';
const metadataContextHeaderNames=['content-type','x-vision-project','x-vision-context'] as const;

// Fresh reads of only this fixture's named originals and saved label/report files.
// A full read has leaf FD/named and lexical ancestor guards; atime is excluded.
function ownedBytes(file: string, scope: string) {
  const root=path.resolve(scope),target=path.resolve(file),relative=path.relative(root,target);
  expect(relative !== '' && relative !== '..' && !relative.startsWith('..'+path.sep) && !path.isAbsolute(relative)).toBe(true);
  const identity=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);
  const ancestors: Array<[string,string[]]>=[];
  for(let parent=path.dirname(target);;parent=path.dirname(parent)){
    const value=fs.lstatSync(parent,{bigint:true});expect(value.isDirectory()&&!value.isSymbolicLink()).toBe(true);
    ancestors.push([parent,identity(value)]);if(parent===root)break;
    expect(path.dirname(parent)).not.toBe(parent);
  }
  const before=fs.lstatSync(target,{bigint:true});expect(before.isFile()&&!before.isSymbolicLink()).toBe(true);expect(before.nlink).toBe(1n);
  const fd=fs.openSync(target,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW|fs.constants.O_NONBLOCK);
  let raw:Buffer;
  try{expect(identity(fs.fstatSync(fd,{bigint:true}))).toEqual(identity(before));raw=fs.readFileSync(fd);expect(BigInt(raw.length)).toBe(before.size);expect(identity(fs.fstatSync(fd,{bigint:true}))).toEqual(identity(before));}
  finally{fs.closeSync(fd);}
  expect(identity(fs.lstatSync(target,{bigint:true}))).toEqual(identity(before));
  for(const [parent,pin] of ancestors)expect(identity(fs.lstatSync(parent,{bigint:true}))).toEqual(pin);
  return {path:target,size:raw.length,sha256:crypto.createHash('sha256').update(raw).digest('hex'),identity:identity(before),raw};
}

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: OwnedApi, native: boolean, startupUrl?: string, readNativeMetadata?: NativeMetadataRead) {
  const source = path.join(workspace.root, 'comparison-source');
  const originals: Record<string, string> = {};
  for (const split of ['train', 'val', 'test']) for (const [label, value] of [['OK', 220], ['NG', 40]] as const) {
    const folder = path.join(source, split, label); fs.mkdirSync(folder, {recursive: true});
    const file = path.join(folder, `${label}.png`); fs.writeFileSync(file, png(64, 3, (x, y) => [value, x, y]));
    originals[file] = crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  }
  const project = await api('/api/project/create', {name: 'Comparison handoff fixture', task: 'classification'});
  await api('/api/project/update', {source_dataset_dir: source});
  await api('/api/dataset/import', {folder_path: source, task: 'classification'});
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/comparison_models.py'), source, project.models_dir], {
    cwd: harness.REPO_ROOT, env: {...process.env, VISION_AI_STUDIO_USER_DATA_DIR: workspace.userData}, encoding: 'utf8', timeout: 60_000,
  }).trim());
  let posted = 0, jobId = '';
  page.on('response', async response => {
    if (new URL(response.url()).pathname === '/api/evaluation/model-comparisons/jobs' && response.request().method() === 'POST') {
      posted++; jobId = (await response.json()).job_id;
    }
  });
  if (startupUrl) await page.goto(startupUrl); else await page.reload();
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText('Comparison handoff fixture');
  await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(1).click();
  await expect(page.getByRole('region',{name:'판정 근거에서 시작한 라벨 편집'})).toHaveCount(0);
  await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(3).click();
  const panel = page.getByRole('region', {name: '현행과 후보 모델 비교'});
  await panel.getByLabel('비교 기준 모델', {exact: true}).selectOption('job_fixture_incumbent');
  await panel.getByLabel('후보 모델', {exact: true}).selectOption('job_fixture_candidate');
  await panel.getByRole('button', {name: '동일 test 이미지로 비교', exact: true}).click();
  await expect.poll(() => jobId).not.toBe('');
  await expect(panel.getByRole('status')).toHaveText('비교 2/2장 · completed', {timeout: 60_000});
  const query = `?source_dataset_path=${encodeURIComponent(source)}&task=classification`;
  const job = await api(`/api/evaluation/model-comparisons/jobs/${jobId}${query}`);
  expect(job.status).toBe('completed'); expect(job.result_available).toBe(true);
  const report = await api(`/api/evaluation/model-comparisons/${job.report_id}${query}`);
  expect(report.images).toHaveLength(2); expect(report.summary.disagreements).toBe(2);
  // The two untrained constant predictors produce a real CPU miss and
  // overkill on the explicit folder truth, without certifying model quality.
  const missed=report.images.find((row:any)=>row.ground_truth_verdict==='NG'&&row.incumbent.verdict==='OK');
  const overkill=report.images.find((row:any)=>row.ground_truth_verdict==='OK'&&row.candidate.verdict==='NG');
  expect(missed).toBeTruthy();expect(overkill).toBeTruthy();
  const evidenceCases=[];
  for(const row of [missed,overkill]){
    await panel.locator('[data-comparison-image]').filter({hasText:row.file_name}).getByRole('button',{name:'원판정 근거 보기',exact:true}).click();
    const exact=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});
    await expect(exact).toContainText(row.file_path);await expect(exact).toContainText(row.image_sha256);await expect(exact).toContainText(job.report_id);
    await expect(exact.getByRole('heading')).toContainText('읽기 전용');
    await exact.getByRole('button',{name:'모델 비교로 돌아가기',exact:true}).click();
    await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(job.report_id);
    evidenceCases.push({path:row.file_path,sha256:row.image_sha256,truth:row.ground_truth_verdict,base:row.incumbent.verdict,candidate:row.candidate.verdict});
  }
  evidence.note('miss_overkill_originals',{report_id:job.report_id,cases:evidenceCases,actual_cpu_forward:true,untrained_fixture_not_quality:true});
  const dialog = await openTaskCenter(page);
  await dialog.getByLabel('저장 작업 다시 열기').selectOption(`model_comparison:local:${jobId}`);
  await expect(dialog).toContainText('2/2장');
  await dialog.getByRole('button', {name: '비교 작업·결과 열기', exact: true}).click();
  await expect(panel.getByLabel('비교 작업 다시 열기')).toHaveValue(jobId);
  await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(job.report_id);
  await page.reload();
  await expect(panel.getByLabel('비교 작업 다시 열기')).toHaveValue(jobId);
  await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(job.report_id);
  expect(posted).toBe(1);
  const beforeFilter=await panel.getByLabel('모델 비교 제품 필터').inputValue();
  await panel.getByRole('button',{name:'원판정 근거 보기',exact:true}).first().click();
  const comparisonViewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});
  await expect(comparisonViewer.getByLabel('근거 이미지 종류')).toHaveValue('original');
  await expect(comparisonViewer).toContainText(job.report_id);
  await comparisonViewer.getByRole('button',{name:'모델 비교로 돌아가기',exact:true}).click();
  await expect(panel.getByLabel('비교 작업 다시 열기')).toHaveValue(jobId);
  await expect(panel.getByLabel('모델 비교 제품 필터')).toHaveValue(beforeFilter);
  for (const [file, sha] of Object.entries(originals)) expect(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')).toBe(sha);
  await panel.scrollIntoViewIfNeeded(); await evidence.screenshot(page, native ? 'native-comparison-reopened' : 'browser-comparison-reopened');
  evidence.note('scope', {actual_renderer: true, actual_owned_backend: true, actual_electron_main_preload: native,
    actual_cpu_inference: true, actual_training: false, checkpoint_kind: fixture.checkpoint_kind, server: false,
    posted, job_id: jobId, report_id: job.report_id, tested_images: report.images.length, disagreement_count: report.summary.disagreements,
    handoff_same_id: true, refresh_same_id: true, source_unchanged: true, job, report});

  // A later manual saved-report selection must beat the same persistent
  // handoff on refresh. A fresh task-center selection then takes priority.
  const secondCreated=await api('/api/evaluation/model-comparisons/jobs',{source_dataset_path:source,task:'classification',
    incumbent_job_id:'job_fixture_incumbent',candidate_job_id:'job_fixture_candidate',max_images:2,full_test:true,execution_target:'local_cpu',device:'cpu'});
  let secondJob=secondCreated;
  await expect.poll(async()=>{secondJob=await api(`/api/evaluation/model-comparisons/jobs/${secondCreated.job_id}${query}`);return secondJob.status;},{timeout:60_000}).toBe('completed');
  expect(secondJob.report_id).not.toBe(job.report_id);
  await page.reload();await expect(panel.getByLabel('비교 작업 다시 열기')).toHaveValue(job.job_id);
  await panel.getByLabel('저장된 모델 비교').selectOption(secondJob.report_id);
  await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(secondJob.report_id);
  const postsBeforeReopen=posted;
  await page.reload();await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(secondJob.report_id);
  await expect(panel.getByLabel('비교 작업 다시 열기')).toHaveValue('');expect(posted).toBe(postsBeforeReopen);
  await evidence.screenshot(page,native?'native-manual-report-reopened':'browser-manual-report-reopened');
  const newHandoff=await openTaskCenter(page);await newHandoff.getByLabel('저장 작업 다시 열기').selectOption(`model_comparison:local:${job.job_id}`);
  await newHandoff.getByRole('button',{name:'비교 작업·결과 열기',exact:true}).click();
  await expect(panel.getByLabel('비교 작업 다시 열기')).toHaveValue(job.job_id);await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(job.report_id);
  evidence.note('manual_report_reopen',{report_id:secondJob.report_id,job_id:secondJob.job_id,same_persistent_handoff_superseded:true,
    no_new_submission_on_refresh:true,fresh_handoff_restores_original_job:true,actual_cpu_comparisons:2});

  const queue=await api('/api/data-workbench/review-queues',{comparison_id:secondJob.report_id,threshold:.5,margin:.05});
  expect(queue.items.length).toBeGreaterThan(0);
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  await page.locator('summary').filter({hasText:'저장 검토 큐 · 오류·불일치·임계값 우선'}).click();
  const reviewQueue=page.getByRole('region',{name:'저장된 검토 큐'});
  await reviewQueue.getByLabel('저장 검토 큐 선택').selectOption(queue.id);await reviewQueue.getByRole('button',{name:'현재 항목 열기',exact:true}).click();
  await expect(reviewQueue.getByRole('button',{name:'검토 완료 · 다음',exact:true})).toBeEnabled();
  await reviewQueue.getByRole('button',{name:'원래 평가·비교로 돌아가기',exact:true}).click();
  await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(secondJob.report_id);
  await expect(panel.getByLabel('비교 작업 다시 열기')).toHaveValue('');
  const focused=panel.locator('[data-comparison-image].ring-cyan-400');
  await expect(focused).toHaveCount(1);await expect(focused).toHaveAttribute('data-comparison-image',queue.items[queue.cursor].file_path);
  await evidence.screenshot(page,native?'native-review-return':'browser-review-return');
  const beforeQueueReopen=posted;await page.reload();await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(secondJob.report_id);expect(posted).toBe(beforeQueueReopen);
  evidence.note('queue_return',{queue_id:queue.id,comparison_id:secondJob.report_id,image_path:queue.items[queue.cursor].file_path,
    old_handoff_superseded:true,exact_image_highlight:true,refresh_same_report:true,labels_edited:false,no_new_submission:true});

  // A real saved classification flow uses the same controlled weights. The
  // source hash comes from server execution, never a client-supplied verdict.
  await api('/api/flowchart/models/verify', {source_dataset_path: source, models:[{job_id:'job_fixture_incumbent', task:'classification'}]});
  const pipeline = await api('/api/flowchart/templates/single-segmentation?inspection_task=classification&job_id=job_fixture_incumbent');
  const saved = await api(`/api/flowchart/pipeline?source_dataset_path=${encodeURIComponent(source)}`, pipeline);
  const frozen = await api(`/api/flowchart/pipelines/${saved.version_id}`);
  const images = (await api(`/api/dataset/images?folder_path=${encodeURIComponent(source)}&task=classification&split=test`)).items;
  const run = await api('/api/inspections/runs', {source_folder:source, task:'classification', scope:'test', pipeline:frozen, images, execution_target:'local', device:'cpu', project_id:project.id});
  for (const image of images) {const result=await api(`/api/inspections/runs/${run.run_id}/execute`, {image_path:image.file_path});expect(result.execution_device).toBe('cpu');}
  await api(`/api/inspections/runs/${run.run_id}/finish`, {status:'completed'});
  await page.getByRole('navigation', {name:'Workflow Stages'}).getByRole('button').nth(5).click();
  const history = page.locator('[aria-label="검사 이력"]');
  await history.getByRole('button').filter({hasText:frozen.name}).click();
  await page.getByRole('button', {name:'원본·판정 근거 보기', exact:true}).click();
  const viewer = page.getByRole('dialog', {name:'이미지 판정 근거 보기', exact:true});
  await expect(viewer.getByLabel('근거 이미지 종류')).toHaveValue('original');
  await expect(viewer).toContainText(run.run_id); await expect(viewer).toContainText(saved.version_id);
  await viewer.getByLabel('근거 이미지 확대', {exact:true}).click(); await expect(viewer.getByRole('status')).toHaveText('125%');
  await viewer.getByLabel('근거 이미지 축소', {exact:true}).click(); await expect(viewer.getByRole('status')).toHaveText('100%');
  await viewer.getByLabel('근거 이미지 확대', {exact:true}).click(); await expect(viewer.getByRole('status')).toHaveText('125%');
  await viewer.getByLabel('근거 이미지 이동 영역').focus(); await page.keyboard.press('ArrowRight');
  expect(await viewer.locator('img[alt="해시 확인된 원본"]').evaluate(el => el.parentElement!.style.transform)).toContain('translate(30px, 0px)');
  await viewer.getByRole('button', {name:'전체 맞춤', exact:true}).click(); await expect(viewer.getByRole('status')).toHaveText('100%');
  await viewer.getByLabel('근거 겹침 이미지').selectOption('overlay');
  await viewer.getByLabel('근거 겹침 투명도').focus();await page.keyboard.press('Home');for(let i=0;i<5;i++)await page.keyboard.press('ArrowRight');
  await expect(viewer.getByAltText('겹침 저장된 판정 overlay')).toHaveCSS('opacity','0.25');
  await viewer.getByLabel('근거 ROI 라벨').uncheck();
  await evidence.screenshot(page,native?'native-evidence-viewer':'browser-evidence-viewer');
  await viewer.getByRole('button',{name:'검사 결과로 돌아가기',exact:true}).click();await expect(viewer).toHaveCount(0);
  await expect(page.locator('[aria-label="선택한 검사 실행 식별자"]')).toContainText(saved.version_id.slice(0,8));
  await page.getByRole('button', {name:'원본·판정 근거 보기', exact:true}).click(); await expect(viewer.getByLabel('근거 이미지 종류')).toHaveValue('original');
  await page.keyboard.press('Escape');await expect(viewer).toHaveCount(0);
  for(const [file,sha] of Object.entries(originals))expect(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')).toBe(sha);
  evidence.note('viewer',{actual_cpu_inspection:true,actual_training:false,synthetic_checkpoint:true,run_id:run.run_id,saved_version_id:saved.version_id,
    original_server_hash_verified:true,zoom:true,keyboard_pan:true,fit:true,opacity:true,readonly:true,return_same_version:true,source_unchanged:true});

  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();
  await panel.getByLabel('저장된 모델 비교').selectOption(secondJob.report_id);
  // Explicit current-label mode preserves the frozen report and its filters.
  await panel.getByLabel('모델 비교 제품 필터').selectOption('(미지정)');
  await panel.getByLabel('모델 비교 Lot 필터').selectOption('(미지정)');
  const selectedReport=await api(`/api/evaluation/model-comparisons/${secondJob.report_id}${query}`);
  expect(selectedReport.comparison_id).toBe(secondJob.report_id);expect(selectedReport.project_id).toBe(project.id);
  expect(selectedReport.source_dataset_path).toBe(source);expect(selectedReport.task).toBe('classification');
  expect(selectedReport.labelset_id).toBe(project.active_labelset_id||'default');
  const editRow=selectedReport.images[0];
  const initialMetadata=await api(`/api/dataset/metadata/image?image_path=${encodeURIComponent(editRow.file_path)}`);
  expect(initialMetadata.image_uuid).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  expect(initialMetadata.image_uuid).toBe(editRow.image_uuid);expect(initialMetadata.file_path).toBe(editRow.file_path);
  expect(initialMetadata.content_hash).toBe(editRow.image_sha256);expect(initialMetadata.revision).toBe(editRow.revision);
  const beforeLabels=await api(`/api/annotations/${editRow.image_id}?file_path=${encodeURIComponent(editRow.file_path)}`);
  const beforeReportSha=crypto.createHash('sha256').update(JSON.stringify(await api(`/api/evaluation/model-comparisons/${secondJob.report_id}${query}`))).digest('hex');
  await panel.locator('[data-comparison-image]').filter({hasText:editRow.file_name}).getByRole('button',{name:'원판정 근거 보기',exact:true}).click();
  await expect(comparisonViewer.getByLabel('근거 이미지 종류')).toHaveValue('original');
  await comparisonViewer.getByRole('button',{name:'현재 라벨 편집',exact:true}).click();
  const editReturn=page.getByRole('region',{name:'판정 근거에서 시작한 라벨 편집'});
  await expect(editReturn).toContainText(editRow.image_id);
  await expect(page.getByRole('button',{name:'Mark as Normal (OK)',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Mark as Normal (OK)',exact:true}).click();
  await expect(editReturn.getByRole('button',{name:'원래 판정 근거로 돌아가기',exact:true})).toBeDisabled();
  await expect(editReturn.getByRole('button',{name:'근거 이미지의 현재 라벨 다시 열기',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Save Changes',exact:true}).click();
  await expect(editReturn.getByRole('button',{name:'원래 판정 근거로 돌아가기',exact:true})).toBeEnabled();
  const afterLabels=await api(`/api/annotations/${editRow.image_id}?file_path=${encodeURIComponent(editRow.file_path)}`);
  expect(afterLabels.metadata.revision).toBeGreaterThan(beforeLabels.metadata.revision);
  expect(afterLabels.annotations.some((row:any)=>row.is_normal)).toBe(true);
  expect(afterLabels.metadata.content_hash).toBe(editRow.image_sha256);
  expect(beforeLabels.metadata.image_uuid).toBe(initialMetadata.image_uuid);
  expect(beforeLabels.metadata.revision).toBe(initialMetadata.revision);
  expect(afterLabels.metadata.image_uuid).toBe(initialMetadata.image_uuid);
  const savedMetadata=await api(`/api/dataset/metadata/image?image_path=${encodeURIComponent(editRow.file_path)}`);
  expect(savedMetadata).toEqual(afterLabels.metadata);
  const datasetKey=(folder:string)=>crypto.createHash('sha256').update(fs.realpathSync(folder)).digest('hex').slice(0,16);
  const annotationFile=path.join(project.annotations_dir,'by_dataset',datasetKey(path.dirname(editRow.file_path)),`${editRow.image_id}.json`);
  const metadataFile=path.join(project.annotations_dir,'by_dataset',datasetKey(source),'metadata','workflow.json');
  const reportFiles=[job.report_id,secondJob.report_id].map(id=>path.join(project.reports_dir,'model_comparisons',`${id}.json`));
  const protectedFiles=[...Object.keys(originals),annotationFile,metadataFile,...reportFiles];
  expect(protectedFiles).toHaveLength(10);expect(new Set(protectedFiles).size).toBe(10);
  const custody=()=>protectedFiles.map(file=>{const {raw,...pin}=ownedBytes(file,workspace.root);return pin;});
  const savedAnnotation=JSON.parse(ownedBytes(annotationFile,workspace.root).raw.toString('utf8'));
  expect(savedAnnotation.image_id).toBe(editRow.image_id);expect(savedAnnotation.annotations).toEqual(afterLabels.annotations);
  const savedLedger=JSON.parse(ownedBytes(metadataFile,workspace.root).raw.toString('utf8'));
  expect(savedLedger.images[savedMetadata.relative_path]).toMatchObject({image_uuid:savedMetadata.image_uuid,file_path:editRow.file_path,content_hash:editRow.image_sha256,revision:savedMetadata.revision});
  await page.reload();
  await expect(editReturn).toContainText(editRow.image_id);
  const reopen=editReturn.getByRole('button',{name:'근거 이미지의 현재 라벨 다시 열기',exact:true});
  const originSnapshot=()=>page.evaluate(()=>JSON.stringify(Object.entries(localStorage).filter(([key])=>key.startsWith('modu-evidence-edit:')).sort()));
  const storedOrigin=await originSnapshot();
  const originRecords=JSON.parse(storedOrigin);expect(originRecords).toHaveLength(1);
  expect(JSON.parse(originRecords[0][1])).toMatchObject({comparison_id:secondJob.report_id,project_id:project.id,source,task:'classification',labelset_id:selectedReport.labelset_id,image_id:editRow.image_id,file_path:editRow.file_path,image_sha256:editRow.image_sha256,revision:beforeLabels.metadata.revision});
  const errorCustodyBefore=custody();const boundaryPosts=posted;let errorMetadataReads=0;
  const metadataRoute='**/api/dataset/metadata/image?*';
  const exactMetadata=(url:string)=>new URL(url).searchParams.get('image_path')===editRow.file_path;
  await page.route(metadataRoute,r=>{if(!exactMetadata(r.request().url()))return r.continue();errorMetadataReads++;return r.fulfill({status:503,json:{detail:'Controlled current-label metadata unavailable'}});});
  await reopen.click();await expect(editReturn.getByRole('alert')).toContainText('Controlled current-label metadata unavailable');
  await expect(reopen).toBeEnabled();const errorOriginAfter=await originSnapshot();expect(errorOriginAfter).toBe(storedOrigin);
  expect(await api(`/api/annotations/${editRow.image_id}?file_path=${encodeURIComponent(editRow.file_path)}`)).toEqual(afterLabels);
  const errorCustodyAfter=custody();expect(errorCustodyAfter).toEqual(errorCustodyBefore);expect(errorMetadataReads).toBe(1);expect(posted).toBe(boundaryPosts);
  await evidence.screenshot(page,native?'native-evidence-label-reopen-error':'browser-evidence-label-reopen-error');
  await page.unroute(metadataRoute);await reopen.click();
  await expect(editReturn.getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Normal (OK) Part',exact:true})).toBeVisible();
  await expect(reopen).toBeEnabled();
  await expect(editReturn).toContainText(`확인한 수정 버전 ${savedMetadata.revision}`);
  await evidence.screenshot(page,native?'native-evidence-label-reopened':'browser-evidence-label-reopened');

  // Deliberately defer the first genuine metadata read. Leaving and returning
  // to the same editor cancels this request; no late label read or navigation.
  let capturedMetadata:any=null;
  const nativeMetadataNonce=native?crypto.randomUUID():'';
  let nativeMetadataUrl='',nativeDelegatedMetadataReads=0,nativeMetadataPreflights=0;
  let nativeMetadataContextHeaders:Record<string,string>={};
  let release!:()=>void,metadataStarted=false,metadataReleased=false,metadataReads=0,annotationReads=0;
  const held=new Promise<void>(resolve=>{release=resolve;});
  const countAnnotation=(r:any)=>{if(r.method()==='GET'&&new URL(r.url()).pathname===`/api/annotations/${editRow.image_id}`)annotationReads++;};
  page.on('request',countAnnotation);
  await page.route(metadataRoute,async r=>{
    const request=r.request(),address=new URL(request.url());
    if(native&&address.searchParams.get(metadataDelegateParameter)===nativeMetadataNonce){
      // Only this exact test-owned renderer delegate bypasses the held-response control.
      const expected=new URL(nativeMetadataUrl);expected.searchParams.set(metadataDelegateParameter,nativeMetadataNonce);
      expect(address.href).toBe(expected.href);
      if(request.method()==='OPTIONS'){
        expect(request.headers()['access-control-request-method']).toBe('GET');
        nativeMetadataPreflights++;expect(nativeMetadataPreflights).toBeLessThanOrEqual(1);
        await r.continue();return;
      }
      expect(request.method()).toBe('GET');
      expect(metadataContextHeaderNames.every(name=>request.headers()[name]===nativeMetadataContextHeaders[name])).toBe(true);
      nativeDelegatedMetadataReads++;expect(nativeDelegatedMetadataReads).toBe(1);
      await r.continue();return;
    }
    if(!exactMetadata(request.url())){await r.continue();return;}
    expect(request.method()).toBe('GET');expect(address.searchParams.has(metadataDelegateParameter)).toBe(false);
    metadataReads++;expect(metadataReads).toBe(1);
    let deliver:()=>Promise<void>;
    if(native){
      expect(readNativeMetadata).toBeTruthy();nativeMetadataUrl=request.url();
      // Preserve only the renderer-visible project context; the main process
      // supplies its hidden capability. Never read or copy capability headers.
      const originalHeaders=request.headers();
      nativeMetadataContextHeaders=Object.fromEntries(metadataContextHeaderNames.filter(name=>name in originalHeaders).map(name=>[name,originalHeaders[name]]));
      const deadline=performance.now()+10_000;
      const expiresAt=Date.now()+Math.max(0,Math.floor(deadline-performance.now()));
      let timer:ReturnType<typeof setTimeout>|undefined;
      let actual:Awaited<ReturnType<NativeMetadataRead>>;
      try{
        actual=await Promise.race([
          readNativeMetadata!(nativeMetadataUrl,nativeMetadataNonce,expiresAt,nativeMetadataContextHeaders),
          new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error('Native metadata delegation exceeded original 10s deadline')),Math.max(0,Math.floor(deadline-performance.now())));}),
        ]);
        expect(performance.now()).toBeLessThan(deadline);
      }finally{clearTimeout(timer);}
      expect(actual.status>=200&&actual.status<300).toBe(true);
      const body=Buffer.from(actual.body);capturedMetadata=JSON.parse(body.toString('utf8'));
      expect(nativeDelegatedMetadataReads).toBe(1);
      deliver=()=>r.fulfill({status:actual.status,headers:actual.headers,body});
    }else{
      const response=await r.fetch({maxRedirects:0,maxRetries:0,timeout:10_000});
      expect(response.ok()).toBe(true);capturedMetadata=await response.json();
      deliver=()=>r.fulfill({response});
    }
    expect(capturedMetadata).toEqual(savedMetadata);
    metadataStarted=true;await held;await deliver();metadataReleased=true;
  });
  const cancelCustodyBefore=custody();
  const beforeCancelledOrigin=await originSnapshot();await reopen.click();await expect.poll(()=>metadataStarted).toBe(true);await expect(reopen).toBeDisabled();
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();
  await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(secondJob.report_id);
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  await expect(editReturn).toContainText(editRow.image_id);await expect(reopen).toBeEnabled();const readsBeforeRelease=annotationReads;
  const cancelledResponse=page.waitForResponse(r=>exactMetadata(r.url())&&r.request().method()==='GET',{timeout:10_000});
  release();await expect.poll(()=>metadataReleased).toBe(true);const delivered=await cancelledResponse;expect(delivered.ok()).toBe(true);await delivered.finished();await page.waitForTimeout(300);
  expect(metadataReads).toBe(1);expect(nativeDelegatedMetadataReads).toBe(native?1:0);expect(annotationReads).toBe(readsBeforeRelease);expect(await originSnapshot()).toBe(beforeCancelledOrigin);await expect(editReturn.getByRole('alert')).toHaveCount(0);
  const cancelledLateAnnotationReads=annotationReads-readsBeforeRelease;expect(cancelledLateAnnotationReads).toBe(0);
  expect(await api(`/api/annotations/${editRow.image_id}?file_path=${encodeURIComponent(editRow.file_path)}`)).toEqual(afterLabels);
  const cancelCustodyAfter=custody();expect(cancelCustodyAfter).toEqual(cancelCustodyBefore);expect(posted).toBe(boundaryPosts);
  await evidence.screenshot(page,native?'native-evidence-label-cancelled-read':'browser-evidence-label-cancelled-read');
  await page.unroute(metadataRoute);page.off('request',countAnnotation);await reopen.click();await expect(page.getByRole('button',{name:'Normal (OK) Part',exact:true})).toBeVisible();await expect(reopen).toBeEnabled();
  evidence.note('evidence_label_boundary_custody',{cells:['U030.evidence-current-label-return.error','U030.evidence-current-label-return.cancel'],
    comparison_id:secondJob.report_id,project_id:project.id,source,task:'classification',labelset_id:selectedReport.labelset_id,
    image_id:editRow.image_id,image_uuid:savedMetadata.image_uuid,image_path:editRow.file_path,image_sha256:editRow.image_sha256,
    historical_revision:editRow.revision,current_revision:savedMetadata.revision,error_metadata_reads:errorMetadataReads,
    error:{before:errorCustodyBefore,after:errorCustodyAfter,origin_before:storedOrigin,origin_after:errorOriginAfter},
    cancel:{before:cancelCustodyBefore,after:cancelCustodyAfter,original_response_read_once:true,original_response_delivered:true,late_annotation_reads:cancelledLateAnnotationReads,native_renderer_delegated_reads:nativeDelegatedMetadataReads,native_preflight_requests:nativeMetadataPreflights},
    actual_gui:true,actual_owned_backend:true,actual_electron_main_preload:native,controlled_transport:true,
    no_new_comparison:true,full_source_and_saved_label_report_bytes_unchanged:true,model_quality_acceptance:false,physical_target_acceptance:false,complete_feature_acceptance:false});
  evidence.note('evidence_label_reopen_boundaries',{image_id:editRow.image_id,current_revision:capturedMetadata.revision,
    controlled_metadata_failure:503,error_keeps_origin_and_annotations:true,deliberate_retry:true,
    leave_and_reenter_same_stage_cancels_old_request:true,cancelled_metadata_reads:metadataReads,
    cancelled_late_annotation_reads:cancelledLateAnnotationReads,new_editor_not_busy:true,new_deliberate_request_succeeds:true});
  await editReturn.getByRole('button',{name:'원래 판정 근거로 돌아가기',exact:true}).click();
  await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(secondJob.report_id);
  await expect(panel.getByLabel('모델 비교 제품 필터')).toHaveValue('(미지정)');
  await expect(panel.getByLabel('모델 비교 Lot 필터')).toHaveValue('(미지정)');
  await expect(panel.locator('[data-comparison-image].ring-cyan-400')).toHaveAttribute('data-comparison-image',editRow.file_path);
  expect(crypto.createHash('sha256').update(JSON.stringify(await api(`/api/evaluation/model-comparisons/${secondJob.report_id}${query}`))).digest('hex')).toBe(beforeReportSha);
  await evidence.screenshot(page,native?'native-evidence-label-return':'browser-evidence-label-return');
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  await expect(editReturn).toHaveCount(0);expect(await originSnapshot()).toBe('[]');
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();
  await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(secondJob.report_id);
  evidence.note('evidence_labeling',{comparison_id:secondJob.report_id,image_id:editRow.image_id,image_path:editRow.file_path,
    captured_hash:editRow.image_sha256,labelset_id:selectedReport.labelset_id,image_uuid:savedMetadata.image_uuid,before_revision:beforeLabels.metadata.revision,after_revision:afterLabels.metadata.revision,
    historical_report_sha256:beforeReportSha,normal_label_saved:true,dirty_return_refused:true,reopened_current_label:true,
    returned_exact_report_image_filters:true,no_new_comparison:true,actual_brush_eraser:false,team_lease_fixture:false});

}

test('CPU comparison reopens the exact completed job from Task Center and after refresh', async ({page, request, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: OwnedApi = async (route, body) => {
    const response = body === undefined ? await request.get(renderer.origin + route)
      : route.endsWith('/update')||route.endsWith('/finish') ? await request.put(renderer.origin + route, {data: body}) : await request.post(renderer.origin + route, {data: body});
    expect(response.ok(), await response.text()).toBe(true); return response.json();
  };
  await exercise(page, workspace, evidence, api, false, renderer.url);
});

test('native CPU comparison reopens the exact completed job from Task Center and after refresh', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const {window} = electronSession; const backend = await electronSession.waitForBackend();
  const api: OwnedApi = (route, body) => window.evaluate(async ({port, route, body}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, body === undefined ? {} : {
      method: route.endsWith('/update')||route.endsWith('/finish') ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`Owned fixture API: HTTP ${response.status}`); return response.json();
  }, {port: backend.port, route, body});
  const readNativeMetadata:NativeMetadataRead=async(url,nonce,expiresAt,contextHeaders)=>{
    const address=new URL(url);expect(address.origin).toBe(`http://127.0.0.1:${backend.port}`);
    expect(address.pathname).toBe('/api/dataset/metadata/image');
    expect(address.searchParams.has(metadataDelegateParameter)).toBe(false);
    expect(Object.keys(contextHeaders).every(name=>metadataContextHeaderNames.includes(name as typeof metadataContextHeaderNames[number]))).toBe(true);
    return window.evaluate(async({url,nonce,parameter,expiresAt,contextHeaders})=>{
      // A query marker grants no capability. The original trusted renderer/main
      // request interceptor supplies authentication without exposing its token.
      const address=new URL(url);address.searchParams.set(parameter,nonce);
      const remaining=Math.floor(expiresAt-Date.now());
      if(remaining<=0)throw new Error('Native metadata original deadline spent before request');
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),remaining);
      try{
        const response=await fetch(address.href,{method:'GET',headers:contextHeaders,redirect:'error',cache:'no-store',signal:controller.signal});
        const body=Array.from(new Uint8Array(await response.arrayBuffer()));
        const headers:Record<string,string>={};response.headers.forEach((value,name)=>{headers[name]=value;});
        return {status:response.status,headers,body};
      }finally{clearTimeout(timer);}
    },{url,nonce,parameter:metadataDelegateParameter,expiresAt,contextHeaders});
  };
  await exercise(window, workspace, evidence, api, true,undefined,readNativeMetadata);
});
