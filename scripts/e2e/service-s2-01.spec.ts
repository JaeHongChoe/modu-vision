import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';

// S2-01 on the actual renderer and backend: a first start opens the guide with the order of a first project; the
// example runs end to end on this computer's CPU through the app's own calls (the backend draws the synthetic images,
// nothing is downloaded); it ends in the saved inspection history under an example notice, and the example cannot be
// approved. In the browser project the desktop host bridge is the test-only shim; this is not desktop app QA.

test('S2-01: a first start guides the order and builds the example end to end on this computer', async ({ page, renderer, workspace, evidence }) => {
  test.setTimeout(600_000);
  writeFileSync(join(workspace.userData, 'onboarding.json'), JSON.stringify({ version: 1, dismissed: false }));
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);

  const guide = page.getByRole('dialog', { name: '처음 시작하기' });
  await expect(guide).toBeVisible({ timeout: 60_000 });
  const order = guide.locator('ol').first();
  for (const item of ['작업 방식', '이 컴퓨터·서버 사전 점검', '프로젝트 만들기', '데이터 가져오기']) await expect(order.getByText(item, { exact: true })).toBeVisible();
  await expect(guide.getByText(/실제 공정의 품질 승인이나 배포 근거로 쓸 수 없습니다/)).toBeVisible();
  await evidence.screenshot(page, 's201-01-guide');

  const started = Date.now();
  await guide.getByRole('button', { name: '예제 프로젝트 만들기' }).click();
  const progress = guide.getByRole('list', { name: '예제 진행' });
  await expect(progress).toBeVisible();
  const ready = guide.getByRole('status').filter({ hasText: /^예제 검사 12장/ });
  const failed = guide.getByRole('alert');
  await expect(ready.or(failed)).toBeVisible({ timeout: 540_000 });
  expect(await failed.count(), await failed.allInnerTexts().then(texts => texts.join(' | '))).toBe(0);
  const steps = await progress.getByRole('listitem').allInnerTexts();
  evidence.note('example', { seconds: Math.round((Date.now() - started) / 1000), steps, summary: await ready.innerText() });
  expect(steps.every(text => text.startsWith('✓'))).toBe(true);
  // The example's model really separates the classes: each saved row's verdict against its image's folder (OK or NG),
  // at least 10 of the 12 test images right. A model at chance gets about 6 (the review's no-defect mutant: 5 and 6).
  const current = await (await page.request.get(`${renderer.origin}/api/project/current`)).json() as { source_dataset_dir: string };
  const runs = await (await page.request.get(`${renderer.origin}/api/inspections/runs?${new URLSearchParams({
    source_folder: current.source_dataset_dir, task: 'classification' })}`)).json() as { runs: Array<{ run_id: string }> };
  const run = await (await page.request.get(`${renderer.origin}/api/inspections/runs/${runs.runs[0].run_id}`)).json() as {
    rows: Array<{ image: { file_path: string }; result?: { final_verdict?: string } }> };
  const graded = run.rows.map(row => ({ label: /[\\/](OK|NG)[\\/][^\\/]+$/.exec(row.image.file_path)?.[1], verdict: row.result?.final_verdict }));
  expect(graded).toHaveLength(12);
  expect(graded.every(row => row.label), JSON.stringify(graded)).toBe(true);
  const right = graded.filter(row => row.verdict === row.label).length;
  evidence.note('example_accuracy', { right, of: graded.length, summary: await ready.innerText() });
  expect(right, JSON.stringify(graded)).toBeGreaterThanOrEqual(10);
  await evidence.screenshot(page, 's201-02-example-ready');

  // The example's own inspection history, under the example notice.
  await guide.getByRole('button', { name: '검사 결과 보기 (6단계)' }).click();
  await expect(guide).toHaveCount(0);
  await expect(page.getByRole('note', { name: '예제 프로젝트' })).toContainText('품질 승인이나 배포 근거로 쓸 수 없습니다');
  const history = page.locator('[aria-label="검사 이력"]');
  await expect(history.getByRole('button', { name: /완료/ }).first()).toBeVisible({ timeout: 60_000 });
  await expect(history.getByRole('button', { name: /12장/ }).first()).toBeVisible();
  await evidence.screenshot(page, 's201-03-example-history');

  // An approval of the example is refused by the backend's own release gate.
  const project = await (await page.request.get(`${renderer.origin}/api/project/current`)).json() as { example?: { id: string }; source_dataset_dir: string };
  expect(project.example?.id).toBe('surface-scratch-classification');
  const approval = await page.request.post(`${renderer.origin}/api/model-deployments/approve`, { data: {
    comparison_id: 'example', source_dataset_path: project.source_dataset_dir, task: 'classification',
    reviewer: 'QA', reason: 'approval attempt on the example', holdout_reviewed: true } });
  expect(approval.status()).toBe(409);
  expect(JSON.stringify(await approval.json())).toContain('품질 승인·배포 대상이 아닙니다');

  // Right after the example, with stage 03 never opened, the user's own project is made and the example opened again:
  // the finished example training no longer counts as running (s201s3 review P2).
  const exampleDir = (await (await page.request.get(`${renderer.origin}/api/project/current`)).json() as { project_dir: string }).project_dir;
  const projectTitle = page.getByTitle('프로젝트 관리', { exact: true });
  const projects = page.getByRole('dialog', { name: '프로젝트 관리' });
  await projectTitle.click();
  await projects.getByRole('button', { name: '새 프로젝트', exact: true }).click();
  await projects.getByPlaceholder('예: 세라믹 표면 결함 검사').fill('S2-01 own project');
  await projects.getByRole('button', { name: '프로젝트 만들기' }).click();
  await expect(projectTitle).toContainText('S2-01 own project', { timeout: 30_000 });
  await projectTitle.click();
  await projects.getByRole('button', { name: '폴더에서 열기', exact: true }).click();
  await projects.getByPlaceholder('/path/to/project').fill(exampleDir);
  await projects.getByRole('button', { name: '프로젝트 열기' }).click();
  await expect(projectTitle).toContainText('예제 · 표면 긁힘 분류', { timeout: 30_000 });
  await expect(page.getByRole('alert').filter({ hasText: '학습이 진행 중입니다' })).toHaveCount(0);

  // The example's CPU job left the user's own device setting for later trainings as it was.
  await page.getByRole('button', { name: /03.*오토딥러닝/ }).click();
  await page.getByText('다음 학습 배치·로컬 장치 설정').click();
  await expect(page.getByLabel('다음 학습 로컬 장치')).toHaveValue('auto');

  // After a reload the guide does not open by itself (the project has data); reopened, it offers the example to open.
  await page.reload();
  await expect(page.getByRole('navigation', { name: '프로젝트 작업 공간' })).toBeVisible();
  await expect(guide).toHaveCount(0);
  await page.getByRole('button', { name: '처음 시작 안내' }).click();
  await expect(guide.getByRole('list', { name: '만들어 둔 예제 프로젝트' })).toContainText('예제 · 표면 긁힘 분류');
  await expect(guide.getByRole('button', { name: '예제 프로젝트 새로 만들기' })).toBeVisible();
  await evidence.screenshot(page, 's201-04-guide-reopened');
  evidence.note('scope', { actual_backend: true, fixture_responses: [], training: 'resnet18 without pretrained weights, 20 epochs, CPU', network: 'the page reaches only the local renderer and backend (fixture); pretrained weights are off' });
});
