import type { Route } from '@playwright/test';
import { expect, test } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';
import { confirmFlowSave } from './fixtures/flowChange';

// Display-only transport regression on the actual renderer and owned backend. A single preflight reply is held;
// no model was trained or verified and the synthetic "ready" reply is not deployment-readiness evidence.
const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

for(const switchKind of ['edge','selected'] as const)test(`a late preflight reply cannot claim readiness after ${switchKind} target changes`, async ({ page, renderer, workspace, evidence }) => {
  const model = 'e07-late-display-model';
  expect((await page.request.post(`${renderer.origin}/api/project/create`, { data: { name: 'E07 late reply fixture', task: 'anomaly' } })).ok()).toBe(true);
  expect((await page.request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: workspace.dataset } })).ok()).toBe(true);
  await page.route('**/api/flowchart/models/catalog?*', route => json(route, { models: [{ job_id: model, task: 'anomaly', label: 'Display fixture',
    preset: null, created_at: null, best_metric: null, source_dataset_path: workspace.dataset, class_names: ['ng', 'ok'], class_ids: [1, 2],
    score_spec: { domain: 'distance', unit: 'mahalanobis_distance', direction: 'higher_is_defect', calibration_id: 'display-fixture', threshold: 8 } }], total: 1 }));
  await page.route('**/api/flowchart/models/verify', route => json(route, { verified_job_ids: [model] }));
  await page.route('**/api/export/flow/preflights?*', route => json(route, { reports: [] }));
  let started!: () => void;
  let release!: () => void;
  const requestStarted = new Promise<void>(resolve => { started = resolve; });
  const releaseReply = new Promise<void>(resolve => { release = resolve; });
  let captured: Record<string, unknown> = {};
  let requests = 0;
  await page.route('**/api/export/flow/preflight', async route => {
    requests += 1;
    captured = route.request().postDataJSON();
    started();
    await releaseReply;
    await json(route, { schema_version: 1, report_id: 'c'.repeat(32), checked_at: '2026-10-04T00:00:00Z', status: 'ready',
      recipe_release: { kind: 'saved_flow', version_id: captured.version_id, recipe_task: captured.recipe_task, pipeline_sha256: 'd'.repeat(64) },
      target_identity: captured.target, environment_hash: 'e'.repeat(64), environment: {}, requirements: [],
      counts: { ready: 1, missing: 0, mismatch: 0, unavailable: 0, unverified: 0 }, blocked_nodes: {}, decision_blocked: false,
      report_sha256: 'f'.repeat(64), stale: false, stale_reasons: [] });
  });
  if(switchKind==='selected')expect((await page.request.post(`${renderer.origin}/api/compute/profiles`,{data:{id:'held-preflight-profile',name:'Owned held reply target',ssh_target:'127.0.0.1',ssh_port:1,remote_root:workspace.root+'/held-preflight',runtime_kind:'python',runtime_value:'python3'}})).ok()).toBe(true);
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  await page.getByRole('button', { name: /05.*플로우차트/ }).click();
  await page.getByRole('list', { name: '목적 레시피' }).getByRole('button', { name: /^고정 ROI 검사/ }).click();
  const recipe = page.getByRole('dialog', { name: '레시피 미리보기·모델 매핑' });
  await recipe.getByLabel('검사 모델 레시피 모델', { exact: true }).selectOption(model);
  await recipe.getByLabel('검사 모델 클래스 적용 범위').selectOption('all');
  await recipe.getByRole('button', { name: '매핑 확인·새 초안으로 채택' }).click();
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  await confirmFlowSave(page, 'owned late reply display fixture');
  await expect(page.getByLabel('저장 버전', { exact: true })).not.toHaveValue('');
  await page.getByRole('tab', { name: '배포', exact: true }).click();
  const panel = page.getByRole('region', { name: '배포 전 의존성 점검' });
  await panel.getByRole('button', { name: '점검 실행' }).click();
  await requestStarted;
  expect(captured.target).toEqual({ kind: 'this_computer', device: 'cpu' });
  if(switchKind==='edge'){await panel.getByLabel('점검 대상').selectOption({label:'Edge · Windows x64 · CPU'});await expect(panel.getByLabel('점검 대상').locator('option:checked')).toHaveText('Edge · Windows x64 · CPU');}
  else {await page.getByRole('combobox').filter({has:page.locator('option[value="held-preflight-profile"]')}).first().selectOption('held-preflight-profile');await expect(panel.getByLabel('점검 대상')).toHaveValue('selected-cpu');}
  const response = page.waitForResponse(reply => reply.url().endsWith('/api/export/flow/preflight') && reply.request().method() === 'POST');
  release();
  await (await response).finished();
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(panel.getByRole('button', { name: '점검 실행' })).toBeEnabled();
  await test.info().attach('held-display-reply', { contentType: 'application/json', body: JSON.stringify({ requests,
    requested_target: captured.target, selected_target: switchKind, synthetic_display_reply: true,
    actual_backend_preflight_called: false, status_after_reply: await panel.getByRole('status').allTextContents() }) });
  expect(requests).toBe(1);
  await expect(panel.getByRole('status')).toHaveCount(0);
  await expect(panel).not.toContainText('모든 의존성이 이 대상에서 준비됐습니다.');
  await evidence.screenshot(page, 'e07-late-target-reply-discarded');
});
