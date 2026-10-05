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

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: OwnedApi, native: boolean, startupUrl?: string) {
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
  const editRow=report.images[0];
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
  await page.getByRole('button',{name:'Save Changes',exact:true}).click();
  await expect(editReturn.getByRole('button',{name:'원래 판정 근거로 돌아가기',exact:true})).toBeEnabled();
  const afterLabels=await api(`/api/annotations/${editRow.image_id}?file_path=${encodeURIComponent(editRow.file_path)}`);
  expect(afterLabels.metadata.revision).toBeGreaterThan(beforeLabels.metadata.revision);
  expect(afterLabels.annotations.some((row:any)=>row.is_normal)).toBe(true);
  expect(afterLabels.metadata.content_hash).toBe(editRow.image_sha256);
  await page.reload();
  await expect(editReturn).toContainText(editRow.image_id);
  await editReturn.getByRole('button',{name:'근거 이미지의 현재 라벨 다시 열기',exact:true}).click();
  await expect(page.getByRole('button',{name:'Normal (OK) Part',exact:true})).toBeVisible();
  await evidence.screenshot(page,native?'native-evidence-label-reopened':'browser-evidence-label-reopened');
  await editReturn.getByRole('button',{name:'원래 판정 근거로 돌아가기',exact:true}).click();
  await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(secondJob.report_id);
  await expect(panel.getByLabel('모델 비교 제품 필터')).toHaveValue('(미지정)');
  await expect(panel.getByLabel('모델 비교 Lot 필터')).toHaveValue('(미지정)');
  await expect(panel.locator('[data-comparison-image].ring-cyan-400')).toHaveAttribute('data-comparison-image',editRow.file_path);
  expect(crypto.createHash('sha256').update(JSON.stringify(await api(`/api/evaluation/model-comparisons/${secondJob.report_id}${query}`))).digest('hex')).toBe(beforeReportSha);
  await evidence.screenshot(page,native?'native-evidence-label-return':'browser-evidence-label-return');
  evidence.note('evidence_labeling',{comparison_id:secondJob.report_id,image_id:editRow.image_id,image_path:editRow.file_path,
    captured_hash:editRow.image_sha256,labelset_id:report.labelset_id,before_revision:beforeLabels.metadata.revision,after_revision:afterLabels.metadata.revision,
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
  await exercise(window, workspace, evidence, api, true);
});
