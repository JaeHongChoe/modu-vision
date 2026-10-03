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
  expect((await request.post(`${origin}/api/dataset/imports/${view.job_id}/accept`, {
    data: { revision_id: view.result.revision.revision_id, expected_active: revisions.active_revision },
  })).ok()).toBe(true);
}

test('operator choice restores exact identity, blocks changed content and remains empty after clearing', async ({ page, renderer, workspace, evidence }) => {
  const created = await page.request.post(`${renderer.origin}/api/project/create`, { data: { name: 'Operator choice', task: 'classification' } });
  expect(created.ok()).toBe(true);
  const primary = await created.json();
  expect((await page.request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: workspace.dataset } })).ok()).toBe(true);
  await acceptSource(page.request, renderer.origin);
  // Only the health response is a UI fixture. Identity resolution and accepted
  // revisions use the real owned backend; no inspection or runtime is started.
  let inspectRequests = 0;
  await page.route('**/api/product-delivery/operator/inspect', async route => { inspectRequests++; await route.abort(); });
  await page.route('**/api/product-delivery/operator', route => route.fulfill({ json: {
    permissions: { can_inspect: true, can_control: false, can_review: false, can_configure: false },
    project: { name: 'Operator choice', task: 'classification' }, runtime_matches_active: true,
    input_health: { source_exists: true, manual_input: 'available', adapters: {}, configuration: { mode: 'manual', folder: null, camera: null } },
    service: { runtime: { status: 'ready', pipeline_id: 'ui-fixture' }, active: { deployment_id: 'ui-fixture', release: { manifest_sha256: 'a'.repeat(64) } } }, results: [],
  } }));
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  const open = async () => {
    await page.getByRole('button', { name: '운영자 검사', exact: true }).click();
    const panel = page.getByRole('region', { name: '운영자 검사 작업 공간' });
    await expect(panel).toBeVisible();
    await expect(panel.getByLabel('운영자 검사 이미지', { exact: true })).toBeEnabled();
    return panel;
  };
  let panel = await open();
  const inspect = () => panel.getByRole('button', { name: '검사 입력', exact: true });
  const selector = () => panel.getByLabel('운영자 검사 이미지', { exact: true });
  const ok = workspace.images.find(image => image.label === 'ok')!;
  const ng = workspace.images.find(image => image.label === 'ng')!;
  await expect(inspect()).toBeDisabled();
  await expect(selector().locator('option', { hasText: 'sample-ok.png' })).toHaveCount(1);
  await selector().selectOption(ok.path);
  await expect(panel).toContainText(`현재 선택: sample-ok.png · ${ok.path}`);
  await expect(inspect()).toBeEnabled();
  await page.reload();
  panel = await open();
  await expect(selector()).toHaveValue(ok.path);
  await expect(inspect()).toBeEnabled();
  await evidence.screenshot(page, 'operator-choice-restored');

  fs.copyFileSync(ng.path, ok.path);
  await acceptSource(page.request, renderer.origin);
  await panel.getByRole('button', { name: '선택 다시 확인', exact: true }).click();
  await expect(panel).toContainText('선택 이미지 내용 바뀜');
  await expect(inspect()).toBeDisabled();
  await evidence.screenshot(page, 'operator-choice-changed');
  await page.reload();
  panel = await open();
  await expect(panel).toContainText('선택 이미지 내용 바뀜');
  await expect(inspect()).toBeDisabled();
  await panel.getByRole('button', { name: '현재 이미지 다시 선택', exact: true }).click();
  await expect(inspect()).toBeEnabled();
  await panel.getByRole('button', { name: '선택 해제', exact: true }).click();
  await expect(inspect()).toBeDisabled();
  await page.reload();
  panel = await open();
  await expect(selector()).toHaveValue('');
  await expect(inspect()).toBeDisabled();
  await selector().selectOption(ng.path);
  await expect(inspect()).toBeEnabled();

  expect((await page.request.post(`${renderer.origin}/api/project/create`, { data: { name: 'Other operator', task: 'classification' } })).ok()).toBe(true);
  expect((await page.request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: workspace.dataset } })).ok()).toBe(true);
  await acceptSource(page.request, renderer.origin);
  await page.reload();
  panel = await open();
  await expect(selector()).toHaveValue('');
  await expect(inspect()).toBeDisabled();
  expect((await page.request.post(`${renderer.origin}/api/project/open`, { data: { project_dir: primary.project_dir } })).ok()).toBe(true);
  await page.reload();
  panel = await open();
  await expect(selector()).toHaveValue(ng.path);
  await expect(inspect()).toBeEnabled();
  expect(inspectRequests).toBe(0);
});
