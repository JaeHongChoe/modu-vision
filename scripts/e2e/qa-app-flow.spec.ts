import { join } from 'node:path';
import { expect, test } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';
import { closeDialog, createProject, openTaskCenter, pickDataset, segmentationDataset, stage, startTraining, trainingStatus } from './qa/appFlow';
import { confirmFlowSave } from './fixtures/flowChange';

// The app's user flow on the actual backend, driven through the screens only (no API shortcut, no fixture answer).
// In the browser project the desktop host bridge is the test-only shim and its folder dialog is answered with the
// dataset path; this is supporting evidence, not desktop app QA (nothing here runs in Electron yet). Training
// runs on this computer's CPU; GPU servers are not part of this run.

// A step that cannot find its control fails within 30 s instead of waiting for the whole test's limit.
test.use({ actionTimeout: 30_000 });
// The whole flow trains a model on the CPU twice (about 8 minutes here), so it runs when asked for, not in every e2e run.
test.skip(process.env.MV_E2E_APP_FLOW !== '1', 'the full app flow trains on the CPU (about 8 minutes); run it with MV_E2E_APP_FLOW=1');

test('app flow · segmentation (UNet) on this computer: register data, train, abort, release, restart, evaluate, flow, inspect, reopen, export', async ({ page, renderer, workspace, evidence }) => {
  test.setTimeout(1_500_000);
  // Every third image is clean (an empty mask), so the class branch meets both cases.
  const dataset = segmentationDataset(join(workspace.root, 'qa-segmentation'), { train: 40, val: 8 }, { cleanEvery: 3 });
  await installDesktopHostShim(page, renderer.port);
  await page.addInitScript(folder => { (window as unknown as { api: { selectFolder: () => Promise<string> } }).api.selectFolder = async () => folder; }, dataset);
  await page.goto(renderer.url);

  // 01 data: register the folder; the app reads its split from the folders.
  await createProject(page, 'QA segmentation', '영역 분할');
  await pickDataset(page);
  await expect(page.getByText(/Train:\s*40/)).toBeVisible();
  await evidence.screenshot(page, 'qa-01-dataset');

  // 02 labels: what the labeling screen shows for the mask folders the trainer reads (recorded as found).
  await stage(page, '02', '라벨링').click();
  await expect(page.getByText(/^\d+ \/ \d+$/).first()).toBeVisible({ timeout: 60_000 });
  const noLabels = await page.getByText('No annotations on this image').count();
  evidence.note('labels_step_02', { mask_labels_shown: noLabels === 0,
    note: noLabels ? 'QA finding: the mask folders train the model but step 02 shows no labels for them (S3-01 mask binding gap)' : 'mask labels shown' });
  await evidence.screenshot(page, 'qa-02-labels');

  // 03 training: the app's start button, real progress, then the app's abort button.
  const started = page.waitForResponse(response => new URL(response.url()).pathname === '/api/training/start', { timeout: 60_000 });
  await startTraining(page, { family: /^영역 분할/, structure: 'UNet · 기존 구조', device: 'cpu' });
  const firstResponse = await started;
  expect(firstResponse.ok(), await firstResponse.text()).toBe(true);
  const first = await firstResponse.json() as { job_id: string };
  evidence.note('first_job', first);
  await expect(page.getByText(/STEP [1-9]\d*\/\d+/)).toBeVisible({ timeout: 240_000 });
  // Progress and log the app shows while training: the loss readout and the training log's epoch lines.
  const trainingLog = page.getByRole('log');
  await expect(trainingLog).toContainText(`작업 ${first.job_id} 시작`);
  await expect(trainingLog).toContainText(/epoch 1\/\d+ · 학습 손실 \d/, { timeout: 120_000 });
  await expect(page.getByRole('button', { name: /^학습 손실 \d/ })).toBeVisible();
  await evidence.screenshot(page, 'qa-03-training-running');
  await page.getByRole('button', { name: /학습 중단/ }).click();
  await expect(trainingStatus(page, /^(CANCELLED|STOPPED|ABORTED)$/)).toBeVisible({ timeout: 120_000 });
  await expect(trainingLog).toContainText(/학습 (중단됨|취소됨)/);
  evidence.note('first_job_log', await trainingLog.innerText());
  await evidence.screenshot(page, 'qa-04-training-aborted');

  // The Task Center shows the cancel steps and the released reservation for that job.
  const center = await openTaskCenter(page);
  const steps = center.getByRole('list', { name: '취소 확인 단계' });
  await expect(steps.getByText('✓ 예약 반환')).toBeVisible({ timeout: 60_000 });
  evidence.note('first_job_cancel_steps', await steps.innerText());
  await evidence.screenshot(page, 'qa-05-task-center-released');
  await closeDialog(page, '작업 센터');

  // Restart with the same button; the run completes on the CPU.
  const restarted = page.waitForResponse(response => new URL(response.url()).pathname === '/api/training/start', { timeout: 60_000 });
  await page.getByRole('button', { name: '선택 설정으로 학습 시작' }).click();
  const secondResponse = await restarted;
  expect(secondResponse.ok(), await secondResponse.text()).toBe(true);
  const second = await secondResponse.json() as { job_id: string };
  expect(second.job_id).not.toBe(first.job_id);
  evidence.note('second_job', second);
  await expect(trainingStatus(page, 'COMPLETED')).toBeVisible({ timeout: 600_000 });
  await expect(trainingLog).toContainText(/학습 완료/);
  evidence.note('second_job_log', await trainingLog.innerText());
  await evidence.screenshot(page, 'qa-06-training-completed');

  // 04 evaluation: the completed model is evaluated on the held-out split when the step opens.
  const evaluated = page.waitForResponse(response => new URL(response.url()).pathname === '/api/evaluation/results', { timeout: 300_000 });
  await stage(page, '04', '평가').click();
  const evaluation = await evaluated;
  expect(evaluation.status()).toBe(200);
  const results = await evaluation.json() as { job_id: string; metrics: Record<string, unknown> };
  expect(results.job_id).toBe(second.job_id);
  evidence.note('evaluation_metrics', results.metrics);
  await evidence.screenshot(page, 'qa-07-evaluation');

  // 05 flow: the fixed-ROI recipe mapped to the completed model, an ROI inside the image, a class branch checked
  // against the model's recorded classes, saved, then one image run with each node's intermediate result.
  // Every flowchart request around the recipe, to explain a graph that changes under an open recipe (QA finding).
  const flowRequests: string[] = [];
  const opened = Date.now();
  page.on('request', request => { const url = new URL(request.url()); if (url.pathname.startsWith('/api/flowchart')) flowRequests.push(`${Date.now() - opened}ms ${request.method()} ${url.pathname}`); });
  await stage(page, '05', '플로우차트').click();
  await expect(page.getByRole('heading', { name: '검사 플로우 편집기', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '편집', exact: true }).click();
  const recipe = page.getByRole('dialog', { name: '레시피 미리보기·모델 매핑' });
  // The recipe opens only after the flow finished opening (its model check binds the completed model), so the graph
  // cannot change under the open recipe and adoption is not refused.
  flowRequests.push(`${Date.now() - opened}ms recipe opened`);
  await page.getByRole('list', { name: '목적 레시피' }).getByRole('button', { name: /고정 ROI 검사/ }).click();
  await recipe.getByLabel('검사 모델 레시피 모델').selectOption(second.job_id);
  await recipe.getByLabel('검사 모델 클래스 적용 범위').selectOption('all');
  await recipe.getByRole('button', { name: '매핑 확인·새 초안으로 채택' }).click();
  flowRequests.push(`${Date.now() - opened}ms adopt clicked`);
  await expect(recipe.getByRole('alert')).toHaveCount(0);
  await expect(recipe).toHaveCount(0);
  evidence.note('flow_requests_until_adopted', flowRequests);
  // The fixed ROI node is selected after adoption: keep the ROI inside the 64 px images.
  for (const [label, value] of [['너비', '48'], ['높이', '48'], ['X 시작', '8'], ['Y 시작', '8']]) {
    const field = page.getByLabel(`${label} (px)`, { exact: true });
    await field.fill(value);
    await field.press('Enter');
  }
  await expect(page.getByText('원본 기준 [8, 8, 56, 56]')).toBeVisible();
  // A class branch on the model's result: the class input offers the model's recorded names, a name with a stray
  // space is flagged on the connection, the exact name clears it.
  await page.getByRole('button', { name: /^Select connection node_inspect to node_decision/ }).click();
  await page.getByLabel('클래스 조건').selectOption('present');
  await expect.poll(() => page.locator('#flow-predicate-classes option').count(), { timeout: 30_000 }).toBeGreaterThan(0);
  const offered = await page.locator('#flow-predicate-classes option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value));
  evidence.note('model_classes_offered', offered);
  const defect = offered.find(name => name !== 'background');
  expect(defect, `the model records a defect class: ${offered.join(', ')}`).toBeTruthy();
  await page.getByLabel('분기 클래스 이름').fill(`${defect} `);
  await expect(page.getByRole('list', { name: '선택한 항목의 문제' })).toContainText('앞뒤 공백을 지우세요');
  await page.getByLabel('분기 클래스 이름').fill(defect!);
  await expect(page.getByRole('list', { name: '선택한 항목의 문제' })).toHaveCount(0);
  await evidence.screenshot(page, 'qa-08-flow-branch');
  const saved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/pipeline' && response.request().method() === 'POST', { timeout: 60_000 });
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  await confirmFlowSave(page, 'QA: fixed ROI with the class branch');
  expect((await saved).status()).toBe(200);
  // QA finding fixed: right after saving, the editor equals its saved version (no 'rules differ' readiness block). The
  // readiness bar first decides (its next item is the whole-flow evaluation), then the block must be absent.
  await expect(page.getByRole('heading', { name: '검사 플로우 편집기', exact: true })).toBeVisible();
  await expect(page.getByText(/저장 버전과 현재 정답에 일치하는 전체 플로우 평가 필요/).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/편집 화면과 활성 저장 버전의 규칙이 다릅니다/)).toHaveCount(0);
  await page.getByRole('button', { name: /이미지 변경/ }).click();
  await page.getByRole('button', { name: 'part_val_0.png 검사 이미지 선택' }).click();
  await page.getByRole('button', { name: '선택 확정' }).click();
  await page.getByRole('tab', { name: '테스트', exact: true }).click();
  const ran = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/run' && response.request().method() === 'POST', { timeout: 120_000 });
  await page.getByRole('button', { name: /선택 이미지 검사/ }).click();
  const run = await ran;
  expect(run.status(), await run.text()).toBe(200);
  const flowResult = await run.json() as { final_verdict: string; execution_steps: Array<{ node_id: string; status: string; output_count: number; selected_edge_ids: string[];
    artifacts?: Array<{ bbox?: number[] }> }>; execution_resources?: { device?: string } };
  evidence.note('flow_run', { final_verdict: flowResult.final_verdict, device: flowResult.execution_resources?.device,
    steps: flowResult.execution_steps.map(step => ({ node: step.node_id, status: step.status, out: step.output_count, edges: step.selected_edge_ids })) });
  expect(flowResult.execution_steps.map(step => step.node_id)).toEqual(expect.arrayContaining(['node_fixed_roi', 'node_inspect']));
  // The fixed ROI's own output in the run: the region drawn in the editor, in source pixels.
  expect(flowResult.execution_steps.find(step => step.node_id === 'node_fixed_roi')?.artifacts?.[0]?.bbox).toEqual([8, 8, 56, 56]);
  // Each node's intermediate result: the inspector of the edit tab shows the selected node's inputs and outputs.
  await page.getByRole('tab', { name: '편집', exact: true }).click();
  await page.locator('[data-flow-node-id="node_inspect"]').click();
  const nodeEvidence = page.getByRole('region', { name: '선택 노드 실행 근거' });
  await expect(nodeEvidence.getByRole('heading', { name: '실행 근거 · 검사 모델' })).toBeVisible();
  await expect(nodeEvidence).toContainText(/입력 1개 → 출력 1개/);
  await expect(nodeEvidence.getByRole('heading', { name: '출력 · 1개' })).toBeVisible();
  await evidence.screenshot(page, 'qa-09-flow-run-node-evidence');
  await page.locator('[data-flow-node-id="node_fixed_roi"]').click();
  await expect(nodeEvidence.getByRole('heading', { name: '실행 근거 · 고정 ROI' })).toBeVisible();
  await expect(nodeEvidence).toContainText(/입력 1개 → 출력 1개/);
  evidence.note('node_evidence', { inspect: 'input 1 -> output 1', fixed_roi: 'input 1 -> output 1' });

  // 06 inspection: a batch over the val split with the saved flow on this computer's CPU, its history reopened after a
  // reload, and the run exported.
  await stage(page, '06', '추론').click();
  const batch = page.getByRole('region', { name: '실제 이미지 일괄 검사' });
  await expect(batch).toBeVisible();
  await batch.getByLabel('검사 범위').selectOption('val');
  await batch.getByLabel('일괄 검사 실행 위치').selectOption({ label: '이 컴퓨터 · CPU' });
  await batch.getByRole('button', { name: '검사 시작' }).click();
  await expect(batch.getByText(/^완료 · 8\/8 확인$/)).toBeVisible({ timeout: 300_000 });
  await evidence.screenshot(page, 'qa-10-batch-done');
  const runId = await batch.getByText(/^실행 ID /).getAttribute('title');
  expect(runId).toMatch(/^[0-9a-f-]{36}$/);
  const history = page.locator('[aria-label="검사 이력"]');
  const counts = /(\d+)장 · NG (\d+) · 검토 (\d+)/.exec(await history.getByRole('button', { name: /완료/ }).first().innerText());
  const [total, ng, review] = [Number(counts?.[1]), Number(counts?.[2]), Number(counts?.[3])];
  evidence.note('batch_verdicts', { total, ok: total - ng - review, ng, review, clean_images: 2 });
  // Both branches ran: the defect images and the clean ones are not all judged the same (at least two kinds of verdict).
  expect([ng, total - ng - review, review].filter(count => count > 0).length, `verdicts NG ${ng}, review ${review} of ${total}`).toBeGreaterThan(1);

  // Reopen after a reload: the saved flow (its ROI and class branch) and the saved inspection run.
  await page.reload();
  await expect(page.getByRole('navigation', { name: '프로젝트 작업 공간' })).toBeVisible();
  await stage(page, '05', '플로우차트').click();
  await expect(page.getByLabel('저장 버전', { exact: true })).not.toHaveValue('', { timeout: 60_000 });
  await expect(page.getByText(`${defect} 있음`).first()).toBeVisible();
  await page.getByRole('tab', { name: '편집', exact: true }).click();
  await page.locator('[data-flow-node-id="node_fixed_roi"]').click();
  await expect(page.getByText('원본 기준 [8, 8, 56, 56]')).toBeVisible();
  await evidence.screenshot(page, 'qa-11-flow-reopened');
  await stage(page, '06', '추론').click();
  const savedRun = history.getByRole('button', { name: /완료/ }).first();
  await expect(savedRun).toBeVisible({ timeout: 60_000 });
  // The reload cleared the page's memory: the newest saved run opens from the stored history, as the same run.
  await expect(savedRun).toHaveAttribute('aria-pressed', 'true', { timeout: 60_000 });
  await expect(batch.getByText(/^실행 ID /)).toHaveAttribute('title', runId as string);
  await expect(batch.getByText(/^완료 · 8\/8 확인$/)).toBeVisible();
  await savedRun.click();
  await expect(savedRun).toHaveAttribute('aria-pressed', 'true');
  await evidence.screenshot(page, 'qa-12-history-reopened');
  const download = page.waitForEvent('download');
  await batch.getByRole('button', { name: /CSV/ }).click();
  evidence.note('history_export', { file: (await download).suggestedFilename() });

  // Export the saved flow as a package and check it gives the app's verdict on one image (this computer, CPU).
  const packagePanel = page.getByRole('region', { name: '전체 검사 플로우 패키지' });
  await packagePanel.scrollIntoViewIfNeeded();
  await packagePanel.getByLabel('한 장 (제한된 확인)').check();
  const checkImage = packagePanel.getByLabel('한 장 확인 이미지');
  await expect.poll(() => checkImage.locator('option').count(), { timeout: 60_000 }).toBeGreaterThan(1);
  await checkImage.selectOption({ index: 1 });
  const approvals = packagePanel.getByLabel('현장 서비스에 적용할 승인 포함 패키지로 만들기');
  if (await approvals.isChecked()) await approvals.uncheck();
  const device = packagePanel.getByLabel('실행 장치');
  if (await device.count()) await device.selectOption('cpu').catch(() => undefined);
  const exported = page.waitForResponse(response => new URL(response.url()).pathname === '/api/export/flow' && response.request().method() === 'POST', { timeout: 300_000 });
  await packagePanel.getByRole('button', { name: '전체 플로우 내보내기' }).click();
  expect((await exported).status()).toBe(200);
  await expect(packagePanel.getByText(/패키지 생성 완료/)).toBeVisible({ timeout: 120_000 });
  await expect(packagePanel.getByText(/이미지 1장 CPU 결과 일치/)).toBeVisible();
  evidence.note('package_export', { result: await packagePanel.getByText(/패키지 생성 완료/).innerText() });
  await evidence.screenshot(page, 'qa-13-package-exported');
});
