import fs from 'node:fs';
import type { APIRequestContext } from '@playwright/test';
import { expect, test } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';

async function acceptSource(request: APIRequestContext, origin: string) {
  const started = await request.post(`${origin}/api/dataset/imports`, { data: { task: 'classification', verify: true } });
  expect(started.ok()).toBe(true);
  let view = await started.json();
  for (let n = 0; n < 200 && !['completed', 'failed', 'aborted', 'interrupted'].includes(view.state); n++) {
    await new Promise(resolve => setTimeout(resolve, 100));
    view = await (await request.get(`${origin}/api/dataset/imports/${view.job_id}`)).json();
  }
  expect(view.state).toBe('completed');
  const revisions = await (await request.get(`${origin}/api/dataset/revisions`)).json();
  const accepted = await request.post(`${origin}/api/dataset/imports/${view.job_id}/accept`, {
    data: { revision_id: view.result.revision.revision_id, expected_active: revisions.active_revision },
  });
  expect(accepted.ok()).toBe(true);
}

test('package comparison choices survive reload, retain changed images and require explicit reselection', async ({ page, renderer, workspace, evidence }) => {
  const created = await page.request.post(`${renderer.origin}/api/project/create`, { data: { name: 'Package cohort', task: 'classification' } });
  expect(created.ok()).toBe(true);
  const primary = await created.json();
  expect((await page.request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: workspace.dataset } })).ok()).toBe(true);
  await acceptSource(page.request, renderer.origin);
  const template = await page.request.get(`${renderer.origin}/api/flowchart/pipeline`, {
    params: { inspection_task: 'classification', source_dataset_path: workspace.dataset },
  });
  expect(template.ok()).toBe(true);
  const saved = await page.request.post(`${renderer.origin}/api/flowchart/pipeline`, {
    params: { source_dataset_path: workspace.dataset }, data: await template.json(),
  });
  expect(saved.ok()).toBe(true);
  let releaseRestore!: () => void;
  const restoreGate = new Promise<void>(resolve => { releaseRestore = resolve; });
  let firstRestore = true;
  await page.route('**/api/dataset/library/resolve', async route => {
    if (firstRestore) { firstRestore = false; await restoreGate; }
    await route.continue();
  });
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  const open = async (navigate = true) => {
    if (navigate) await page.getByRole('button', { name: /06.*추론/ }).click();
    const panel = page.getByRole('region', { name: '전체 검사 플로우 패키지' });
    await expect(panel).toBeVisible();
    await panel.getByLabel('고정 이미지 여러 장', { exact: true }).check();
    await panel.getByLabel('현장 서비스에 적용할 승인 포함 패키지로 만들기', { exact: true }).uncheck();
    return panel;
  };
  await page.getByRole('button', { name: /06.*추론/ }).click();
  const waiting = page.getByRole('region', { name: '전체 검사 플로우 패키지' });
  try {
    await expect(waiting.getByLabel('고정 이미지 여러 장', { exact: true })).toBeEnabled();
    await expect(waiting.getByRole('button', { name: '선택 해제', exact: true })).toBeDisabled();
    await waiting.getByLabel('한 장 (제한된 확인)', { exact: true }).check();
    await expect(waiting).toContainText('한 장 CPU 확인');
    await waiting.getByLabel('검증 안 함', { exact: true }).check();
    await waiting.getByLabel('고정 이미지 여러 장', { exact: true }).check();
    await expect(waiting.getByRole('list', { name: '데이터 버전 이미지' })).toHaveCount(0);
  } finally { releaseRestore(); }
  let panel = await open(false);
  const chosen = () => panel.getByRole('list', { name: '선택한 패키지 검증 이미지' });
  const grid = () => panel.getByRole('list', { name: '데이터 버전 이미지' });
  await expect(grid().getByRole('listitem')).toHaveCount(2);
  await grid().getByRole('listitem').nth(0).click();
  await grid().getByRole('listitem').nth(1).click();
  await expect(chosen().getByRole('listitem')).toHaveCount(2);
  await expect(panel.getByRole('button', { name: '전체 플로우 내보내기', exact: true })).toBeEnabled();
  await page.reload();
  panel = await open();
  await expect(chosen().getByRole('listitem')).toHaveCount(2);
  await expect(panel.getByRole('button', { name: '전체 플로우 내보내기', exact: true })).toBeEnabled();
  await evidence.screenshot(page, 'package-cohort-restored');

  // Only the owned synthetic fixture is changed, matching a source replacement.
  const ok = workspace.images.find(image => image.label === 'ok')!;
  const ng = workspace.images.find(image => image.label === 'ng')!;
  fs.copyFileSync(ng.path, ok.path);
  await acceptSource(page.request, renderer.origin);
  await panel.getByRole('button', { name: '저장본 새로고침', exact: true }).click();
  const pending = () => panel.getByRole('list', { name: '확인이 필요한 패키지 검증 이미지' });
  await expect(chosen().getByRole('listitem')).toHaveCount(1);
  await expect(pending()).toContainText('ok/sample-ok.png · 내용 바뀜');
  await expect(panel.getByRole('button', { name: '전체 플로우 내보내기', exact: true })).toBeDisabled();
  await expect(panel).toContainText('재선택하거나 빼기 전에는 비교 패키지를 만들 수 없습니다');
  await evidence.screenshot(page, 'package-cohort-changed');
  await page.reload();
  panel = await open();
  await expect(pending().getByRole('listitem')).toHaveCount(1);
  await expect(chosen().getByRole('listitem')).toHaveCount(1);
  await grid().getByRole('listitem').filter({ hasText: 'sample-ok.png' }).click();
  await expect(pending()).toHaveCount(0);
  await expect(chosen().getByRole('listitem')).toHaveCount(2);
  await panel.getByRole('button', { name: '선택 해제', exact: true }).click();
  await page.reload();
  panel = await open();
  await expect(chosen()).toHaveCount(0);
  await expect(pending()).toHaveCount(0);
  await expect(panel).toContainText('0장 선택');
  await expect(grid().getByRole('listitem')).toHaveCount(2);
  await grid().getByRole('listitem').nth(0).click();
  await grid().getByRole('listitem').nth(1).click();
  await expect(chosen().getByRole('listitem')).toHaveCount(2);
  expect((await page.request.post(`${renderer.origin}/api/project/create`, { data: { name: 'Separate cohort', task: 'classification' } })).ok()).toBe(true);
  expect((await page.request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: workspace.dataset } })).ok()).toBe(true);
  await acceptSource(page.request, renderer.origin);
  await page.reload();
  panel = await open();
  await expect(panel).toContainText('0장 선택');
  await expect(chosen()).toHaveCount(0);
  expect((await page.request.post(`${renderer.origin}/api/project/open`, { data: { project_dir: primary.project_dir } })).ok()).toBe(true);
  await page.reload();
  panel = await open();
  await expect(chosen().getByRole('listitem')).toHaveCount(2);
});
