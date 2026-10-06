import type { Page } from '@playwright/test';
import { expect, test } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';

// S1-05: this computer's support states and a real preflight from the model training hub, on the actual backend with
// no fixture answer. The preflight trains, evaluates, infers and exports a tiny synthetic classification model on the
// CPU in a child process of the backend; it verifies the stages for this runtime, not model quality.

const cell = (page: Page, device: string, stage: string) => page.getByRole('region', { name: '이 컴퓨터 지원 상태' })
  .getByRole('cell', { name: new RegExp(`^${device} ${stage}: `) });

test('S1-05: a CPU preflight from the model hub moves the classification stages from unverified to verified', async ({ page, renderer, workspace, evidence }) => {
  test.setTimeout(240_000);
  expect((await page.request.post(`${renderer.origin}/api/project/create`, { data: { name: 'S1-05 worker', task: 'classification' } })).status()).toBe(200);
  expect((await page.request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: workspace.dataset } })).status()).toBe(200);
  await installDesktopHostShim(page, renderer.port);
  const imported = page.waitForResponse(response => new URL(response.url()).pathname === '/api/dataset/current-summary' && response.request().method() === 'GET');
  await page.goto(renderer.url);
  expect((await imported).status()).toBe(200);
  await page.getByRole('button', { name: /03.*오토딥러닝/ }).click();
  const hub = page.getByRole('region', { name: '모델 학습 허브' });
  await hub.getByText('선택 모델의 데이터·장치·학습 구조').click();
  const support = hub.getByRole('region', { name: '이 컴퓨터 지원 상태' });
  await expect(support).toBeVisible();
  for (const stage of ['학습', '평가', '추론', '내보내기']) await expect(cell(page, 'CPU', stage)).toHaveText('미검증');
  await expect(cell(page, 'CPU', '자동 탐색')).toHaveText('미검증');
  // What "verified" will be about: the small architecture the preflight trains, not the family's default model.
  await expect(support.getByText(/점검 범위: resnet18 구조를 작은 합성 데이터로/)).toBeVisible();
  await evidence.screenshot(page, 's105-01-unverified');

  const started = page.waitForResponse(response => new URL(response.url()).pathname === '/api/workers/local/preflight');
  await support.getByRole('button', { name: 'CPU 사전 점검', exact: true }).click();
  expect((await started).status()).toBe(202);
  await expect(support.getByRole('status').filter({ hasText: /사전 점검 중/ })).toBeVisible();
  await expect(support.getByRole('status').filter({ hasText: '마지막 CPU 사전 점검' }))
    .toHaveText('마지막 CPU 사전 점검: 학습 통과 · 평가 통과 · 추론 통과 · 내보내기 통과', { timeout: 180_000 });
  for (const stage of ['학습', '평가', '추론', '내보내기']) await expect(cell(page, 'CPU', stage)).toHaveText('검증됨');
  await expect(cell(page, 'CPU', '자동 탐색'), 'search had no preflight').toHaveText('미검증');
  const workers = await (await page.request.get(`${renderer.origin}/api/workers`)).json() as { workers: Array<{ preflight: Record<string, { passed: boolean; evidence: Record<string, unknown> }> }> };
  const preflight = workers.workers[0].preflight;
  expect(Object.keys(preflight).sort()).toEqual(['classification:evaluate:cpu', 'classification:export:cpu', 'classification:infer:cpu', 'classification:train:cpu']);
  expect(Object.values(preflight).every(row => row.passed)).toBe(true);
  expect(Object.values(preflight).map(row => row.evidence.architecture)).toEqual(['resnet18', 'resnet18', 'resnet18', 'resnet18']);
  expect(['OK', 'NG']).toContain(preflight['classification:export:cpu'].evidence.verdict);
  evidence.note('preflight', preflight);
  await evidence.screenshot(page, 's105-02-verified');

  // The record outlives a reload; nothing in the browser holds it.
  await page.reload();
  await page.getByRole('button', { name: /03.*오토딥러닝/ }).click();
  await hub.getByText('선택 모델의 데이터·장치·학습 구조').click();
  await expect(cell(page, 'CPU', '학습')).toHaveText('검증됨');
  evidence.note('scope', { actual_backend: true, fixture_responses: [], preflight: 'tiny synthetic classification on the CPU', model_quality: 'not assessed' });
});
