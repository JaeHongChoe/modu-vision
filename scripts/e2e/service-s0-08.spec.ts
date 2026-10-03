import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { crc32, deflateSync } from 'node:zlib';
import type { Page } from '@playwright/test';
import { expect, test } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';

// S0-08: the S0-02 anomaly threshold path on the actual backend, with no fixture response. A model is trained through
// the app's training API on the CPU (PaDiM, no pretrained weights, so nothing is downloaded); its calibrated distance
// threshold is carried into the flow, a raw threshold of 8 is saved, reopened and used by an actual flow run; a second
// saved version (9) does not change the reopened first version, which runs with 8 again; an unsaved edit refuses a
// model family (task) change and the project keeps its task. The desktop host bridge is the test-only shim, as in the
// other specs. The trained model says nothing about model quality.

type Json = Record<string, any>;

function chunk(type: string, data: Buffer): Buffer {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, 'ascii');
  const check = Buffer.alloc(4);
  check.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])) >>> 0, 0);
  return Buffer.concat([head, data, check]);
}

/** A small RGB PNG drawn by `pixel`; distinct images for each split (the app refuses repeated images across splits). */
function png(size: number, pixel: (x: number, y: number) => [number, number, number]): Buffer {
  const stride = size * 3 + 1;
  const rows = Buffer.alloc(stride * size);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) rows.set(pixel(x, y), y * stride + 1 + x * 3);
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8;
  header[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
}

function anomalyDataset(root: string): string {
  const part = (shift: number, defect: boolean) => png(48, (x, y) => {
    if (defect && (x - 33) ** 2 + (y - 33) ** 2 < 49) return [20, 20, 20];
    return x >= 4 + shift && x < 20 + shift && y >= 4 && y < 20 ? [180, 180, 180] : [200, 200, 200];
  });
  const layout: Array<[string, number, boolean, string]> = [['train/good', 6, false, 'ok'], ['test/good', 2, false, 'ok'], ['test/defect', 2, true, 'ng']];
  let shift = 0;
  for (const [folder, count, defect, prefix] of layout) {
    mkdirSync(join(root, folder), { recursive: true });
    for (let index = 0; index < count; index++) writeFileSync(join(root, folder, `${prefix}_${index}.png`), part(shift++, defect));
  }
  return root;
}

async function trainAnomalyModel(page: Page, base: string, source: string): Promise<string> {
  expect((await page.request.post(`${base}/api/dataset/import`, { data: { folder_path: source, task: 'anomaly' } })).status()).toBe(200);
  const started = await page.request.post(`${base}/api/training/start`, { data: {
    task: 'anomaly', preset: 'fast', dataset_path: source, device: 'cpu',
    config_overrides: { pretrained: false, image_size: 32, anomaly_method: 'padim', num_workers: 0, batch_size: 2 },
  } });
  expect(started.status(), await started.text()).toBe(200);
  const jobId = (await started.json() as Json).job_id as string;
  await expect.poll(async () => {
    const status = await (await page.request.get(`${base}/api/training/status?job_id=${encodeURIComponent(jobId)}`)).json() as Json;
    if (status.status === 'failed') throw new Error(`training failed: ${JSON.stringify(status.error)}`);
    return status.status;
  }, { timeout: 180_000, intervals: [500, 1000] }).toBe('completed');
  return jobId;
}

const flowNodes = (page: Page) => page.locator('[data-flow-node-id]');
const inspectionNode = (page: Page) => flowNodes(page).filter({ hasText: /threshold/i }).filter({ hasNotText: /rule:/i });
const thresholdInput = (page: Page) => page.getByLabel('결함 판정 임계치');
/** The verdict the server decided with threshold 8: a distance above 8 is NG, below is OK (at exactly 8 the rule depends on
 * the score kind, so that case is not judged). The calibrated threshold differs from 8, so a run deciding with it fails. */
const decidedWith8 = (crop: Json) => crop.defect_score === 8 || crop.verdict === (crop.defect_score > 8 ? 'NG' : 'OK');

test('S0-08: a model trained on the actual backend carries its calibrated threshold into the flow; a saved raw threshold of 8 is reopened, kept by an earlier version and used by actual runs; an unsaved flow refuses a task change', async ({ page, renderer, workspace, evidence }) => {
  test.setTimeout(300_000);
  expect((await page.request.post(`${renderer.origin}/api/project/create`, { data: { name: 'S0-08 actual anomaly', task: 'anomaly' } })).status()).toBe(200);
  const updated = await page.request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: anomalyDataset(join(workspace.root, 's008-anomaly')) } });
  expect(updated.status()).toBe(200);
  const source = (await updated.json() as Json).source_dataset_dir as string;
  const jobId = await trainAnomalyModel(page, renderer.origin, source);
  const catalog = await (await page.request.get(`${renderer.origin}/api/flowchart/models/catalog?source_dataset_path=${encodeURIComponent(source)}`)).json() as Json;
  const model = (catalog.models as Json[]).find(row => row.job_id === jobId)!;
  expect(model.score_spec).toMatchObject({ domain: 'distance', unit: 'mahalanobis_distance', direction: 'higher_is_defect' });
  const calibrated = model.score_spec.threshold as number;
  evidence.note('trained_model', { job_id: jobId, score_spec: model.score_spec });

  await installDesktopHostShim(page, renderer.port);
  const imported = page.waitForResponse(response => new URL(response.url()).pathname === '/api/dataset/import' && response.request().method() === 'POST');
  await page.goto(renderer.url);
  expect((await imported).status()).toBe(200);
  await page.getByRole('button', { name: /05.*플로우차트/ }).click();
  await expect(inspectionNode(page)).toHaveCount(1);
  await page.getByRole('tab', { name: '편집', exact: true }).click();
  await inspectionNode(page).click();
  await page.getByLabel('완료된 학습 모델').selectOption(jobId);
  await expect(page.getByText('모델 1/1 연결')).toBeVisible();
  await expect(thresholdInput(page), 'the evaluation calibration is the flow default').toHaveValue(String(calibrated));
  await thresholdInput(page).fill('8');
  await expect(page.getByText('결함 판정 임계치 · mahalanobis_distance')).toBeVisible();
  const saved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/pipeline' && response.request().method() === 'POST');
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  expect((await saved).status()).toBe(200);
  const active = await (await page.request.get(`${renderer.origin}/api/flowchart/pipeline/active?source_dataset_path=${encodeURIComponent(source)}`)).json() as Json;
  const stored = (active.nodes as Json[]).find(node => node.data.node_type === 'inspection')!.data;
  expect(stored.threshold).toBe(8);
  expect(stored.score_spec).toMatchObject({ domain: 'distance', unit: 'mahalanobis_distance', threshold: 8 });
  await evidence.screenshot(page, 's008-01-threshold-8-saved');

  await page.reload();
  await expect(page.getByRole('navigation', { name: '프로젝트 작업 공간' })).toBeVisible();
  if (!(await inspectionNode(page).count())) await page.getByRole('button', { name: /05.*플로우차트/ }).click();
  await expect(page.getByText('모델 1/1 연결')).toBeVisible();
  await inspectionNode(page).click();
  await expect(thresholdInput(page), 'the reopened flow keeps the raw threshold').toHaveValue('8');

  await page.getByRole('button', { name: /이미지 변경/ }).click();
  await page.getByRole('button', { name: 'ng_0.png 검사 이미지 선택' }).click();
  await page.getByRole('button', { name: '선택 확정' }).click();
  await page.getByRole('tab', { name: '테스트', exact: true }).click();
  const ran = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/run' && response.request().method() === 'POST', { timeout: 120_000 });
  await page.getByRole('button', { name: /선택 이미지 검사/ }).click();
  const run = await ran;
  expect(run.status(), await run.text()).toBe(200);
  const result = await run.json() as Json;
  const sent = (run.request().postDataJSON() as Json).pipeline.nodes.find((node: Json) => node.data.node_type === 'inspection').data;
  expect(sent.threshold).toBe(8);
  const crop = (result.crops as Json[])[0];
  expect(crop.score_spec).toMatchObject({ domain: 'distance', unit: 'mahalanobis_distance', threshold: 8 });
  expect(['OK', 'NG']).toContain(crop.verdict);
  expect(decidedWith8(crop), `score ${crop.defect_score} decided ${crop.verdict} with threshold 8 (calibrated ${calibrated})`).toBe(true);
  evidence.note('actual_run', { final_verdict: result.final_verdict, defect_score: crop.defect_score, score_spec: crop.score_spec, calibrated });
  await expect(page.getByLabel('실행 점수 기준')).toHaveText(/mahalanobis_distance · 임계값 8/);
  await evidence.screenshot(page, 's008-02-actual-run');

  // Version change: a second saved version with threshold 9, then the first version is reopened and run again.
  await page.getByRole('tab', { name: '편집', exact: true }).click();
  await inspectionNode(page).click();
  await thresholdInput(page).fill('9');
  const savedAgain = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/pipeline' && response.request().method() === 'POST');
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  expect((await savedAgain).status()).toBe(200);
  const activeAgain = await (await page.request.get(`${renderer.origin}/api/flowchart/pipeline/active?source_dataset_path=${encodeURIComponent(source)}`)).json() as Json;
  expect((activeAgain.nodes as Json[]).find(node => node.data.node_type === 'inspection')!.data.threshold, 'the second version holds 9').toBe(9);
  const versions = (await (await page.request.get(`${renderer.origin}/api/flowchart/pipelines?source_dataset_path=${encodeURIComponent(source)}`)).json() as Json).pipelines as Json[];
  expect(versions).toHaveLength(2);
  const first = versions.find(version => !version.is_active)!;
  const versionSelect = page.getByLabel('저장 버전', { exact: true });
  await versionSelect.selectOption(first.version_id);
  await inspectionNode(page).click();
  await expect(thresholdInput(page), 'the reopened first version keeps its own threshold').toHaveValue('8');
  await page.getByRole('tab', { name: '테스트', exact: true }).click();
  const ranFirst = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/run' && response.request().method() === 'POST', { timeout: 120_000 });
  await page.getByRole('button', { name: /선택 이미지 검사/ }).click();
  const rerun = await ranFirst;
  expect(rerun.status(), await rerun.text()).toBe(200);
  const rerunCrop = ((await rerun.json() as Json).crops as Json[])[0];
  expect(rerunCrop.score_spec).toMatchObject({ unit: 'mahalanobis_distance', threshold: 8 });
  expect(decidedWith8(rerunCrop), `score ${rerunCrop.defect_score} decided ${rerunCrop.verdict} with threshold 8`).toBe(true);
  await expect(page.getByLabel('실행 점수 기준')).toHaveText(/mahalanobis_distance · 임계값 8/);
  evidence.note('version_change', { versions: versions.map(version => ({ version_id: version.version_id, is_active: version.is_active })), reopened: first.version_id });
  await evidence.screenshot(page, 's008-03-first-version-run');

  // Task change refused: an unsaved flow edit blocks the model family change, and the project task stays anomaly.
  await page.getByRole('tab', { name: '편집', exact: true }).click();
  await inspectionNode(page).click();
  await thresholdInput(page).fill('10');
  await page.getByRole('button', { name: /03.*오토딥러닝/ }).click();
  const capabilities = await (await page.request.get(`${renderer.origin}/api/models/capabilities`)).json() as { families: Array<{ task: string; label: string }> };
  const segmentation = capabilities.families.find(family => family.task === 'segmentation')!.label;
  const hub = page.getByRole('region', { name: '모델 학습 허브' });
  await hub.getByRole('button', { name: new RegExp(`^${segmentation}`) }).click();
  const dialog = page.getByRole('dialog', { name: '검사 작업 변경 영향' });
  await expect(dialog.getByRole('alert')).toHaveText('저장하지 않은 라벨·플로우가 있습니다. 먼저 저장하거나 변경을 취소하세요.');
  await expect(dialog.getByRole('button', { name: '영향 확인 후 변경', exact: true })).toBeDisabled();
  await evidence.screenshot(page, 's008-04-task-change-refused');
  await dialog.getByRole('button', { name: '취소', exact: true }).click();
  const current = await (await page.request.get(`${renderer.origin}/api/project/current`)).json() as Json;
  expect(current.task, 'the project task is unchanged').toBe('anomaly');
  evidence.note('scope', { actual_backend: true, fixture_responses: [], training: 'PaDiM, CPU, no pretrained weights, 6 synthetic training images', model_quality: 'not assessed' });
});
