import type { Page, Route } from '@playwright/test';
import { expect, test, type RendererServer, type Workspace } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';

// E04 on the actual renderer and backend (owned synthetic harness). Only the completed-model catalog and the model
// reference check are transport fixtures, as in the S2-04 spec: no trained weights, inference or deployment is claimed.
const model = 'e04-distance-model';
const score = { domain: 'distance', unit: 'mahalanobis_distance', direction: 'higher_is_defect', calibration_id: 'e04-calibration', threshold: 8 };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

async function setup(page: Page, renderer: RendererServer, workspace: Workspace) {
  expect((await page.request.post(`${renderer.origin}/api/project/create`, { data: { name: 'E04 workspace', task: 'anomaly' } })).ok()).toBe(true);
  expect((await page.request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: workspace.dataset } })).ok()).toBe(true);
  await page.route('**/api/flowchart/models/catalog?*', route => json(route, { models: [{ job_id: model, task: 'anomaly', label: 'Recorded distance model',
    preset: null, created_at: null, best_metric: null, source_dataset_path: workspace.dataset, class_names: ['ng', 'ok'], class_ids: [1, 2], score_spec: score }], total: 1 }));
  await page.route('**/api/flowchart/models/verify', route => json(route, { verified_job_ids: [model] }));
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  await page.getByRole('button', { name: /05.*플로우차트/ }).click();
  await expect(page.getByRole('heading', { name: '검사 플로우 편집기', exact: true })).toBeVisible();
  await page.getByRole('list', { name: '목적 레시피' }).getByRole('button', { name: /^고정 ROI 검사/ }).click();
  const recipe = page.getByRole('dialog', { name: '레시피 미리보기·모델 매핑' });
  await recipe.getByLabel('검사 모델 레시피 모델', { exact: true }).selectOption(model);
  await recipe.getByLabel('검사 모델 클래스 적용 범위').selectOption('all');
  await recipe.getByRole('button', { name: '매핑 확인·새 초안으로 채택' }).click();
  await expect(recipe).toHaveCount(0);
}

const saveDialog = (page: Page) => page.getByRole('dialog', { name: '검사 규칙 변경 저장' });
const threshold = (page: Page) => page.getByLabel('결함 판정 임계치');

test('a rule change is saved with its difference and reason, and the record shows the running release', async ({ page, renderer, workspace, evidence }) => {
  await setup(page, renderer, workspace);
  // The first flow: no active version, so the reason is optional.
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  await expect(saveDialog(page).getByRole('region', { name: '검사 규칙 차이' })).toContainText('새 검사 플로우');
  await expect(saveDialog(page)).toContainText('변경 사유 (선택)');
  await saveDialog(page).getByRole('button', { name: '변경 저장', exact: true }).click();
  await expect(saveDialog(page)).toHaveCount(0);
  await expect(page.getByLabel('저장 버전', { exact: true })).not.toHaveValue('');

  // A threshold change against the active version needs a reason before it can be saved.
  await page.locator('[data-flow-node-id="node_inspect"]').click();
  await threshold(page).fill('9');
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  const difference = saveDialog(page).getByRole('region', { name: '검사 규칙 차이' });
  // The threshold and the score type's threshold move together.
  await expect(difference).toContainText('검사 규칙 변경 2건');
  await expect(difference).toContainText('node_inspect · threshold: 8 → 9');
  await expect(difference).toContainText('node_inspect · score_spec.threshold: 8 → 9');
  await expect(saveDialog(page)).toContainText('변경 사유 (필수)');
  const confirm = saveDialog(page).getByRole('button', { name: '변경 저장', exact: true });
  await expect(confirm).toBeDisabled();
  await evidence.screenshot(page, 'e04-01-reason-required');
  await saveDialog(page).getByLabel('변경 사유').fill('false rejects on line 3');
  await confirm.click();
  await expect(saveDialog(page)).toHaveCount(0);

  await page.getByRole('button', { name: '변경 기록', exact: true }).click();
  const record = page.getByRole('region', { name: '검사 규칙 변경 기록' });
  await expect(record.getByRole('listitem')).toHaveCount(2);
  await expect(record.getByRole('listitem').first()).toContainText('사유: false rejects on line 3');
  await expect(record.getByRole('listitem').first()).toContainText('검사 규칙 변경 2건');
  await expect(record.getByRole('listitem').first()).toContainText('this computer · 저장');
  await expect(record.getByLabel('운영 런타임 상태')).toHaveText('운영 런타임: 적용된 릴리스 없음');
  await evidence.screenshot(page, 'e04-02-change-record');
  const listed = await (await page.request.get(`${renderer.origin}/api/flowchart/changes`)).json();
  expect(listed.integrity).toEqual({ intact: true, rows: 2 });
  expect(listed.changes[0].semantic_delta.changes).toEqual([
    { kind: 'node_changed', node_id: 'node_inspect', field: 'score_spec.threshold', before: 8, after: 9 },
    { kind: 'node_changed', node_id: 'node_inspect', field: 'threshold', before: 8, after: 9 }]);
  evidence.note('change_record', { rows: listed.changes.length, reason: listed.changes[0].reason, actor: listed.changes[0].actor });
});

test('a save after another editor saved is refused before and by the server, and the edits stay', async ({ page, renderer, workspace, evidence }) => {
  await setup(page, renderer, workspace);
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  await saveDialog(page).getByRole('button', { name: '변경 저장', exact: true }).click();
  await expect(saveDialog(page)).toHaveCount(0);
  // Another editor saves a different threshold through the API.
  const query = `recipe_task=anomaly&source_dataset_path=${encodeURIComponent(workspace.dataset)}`;
  const active = await (await page.request.get(`${renderer.origin}/api/flowchart/pipeline/active?source_dataset_path=${encodeURIComponent(workspace.dataset)}`)).json();
  const inspect = active.nodes.find((node: { id: string }) => node.id === 'node_inspect').data;
  inspect.threshold = 7;
  inspect.score_spec.threshold = 7;
  const other = await page.request.post(`${renderer.origin}/api/flowchart/pipeline?${query}&change_reason=other+editor`, { data: active });
  expect(other.ok(), await other.text()).toBe(true);

  await page.locator('[data-flow-node-id="node_inspect"]').click();
  await threshold(page).fill('9');
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  await expect(saveDialog(page).getByRole('alert')).toContainText('다른 곳에서 활성 플로우가 바뀌었습니다');
  await expect(saveDialog(page).getByRole('button', { name: '변경 저장', exact: true })).toBeDisabled();
  await evidence.screenshot(page, 'e04-03-stale');
  await saveDialog(page).getByRole('button', { name: '취소', exact: true }).click();
  await expect(threshold(page)).toHaveValue('9');
  // The server refuses it too (a client that skips the preview).
  const versions = await (await page.request.get(`${renderer.origin}/api/flowchart/pipelines?source_dataset_path=${encodeURIComponent(workspace.dataset)}`)).json();
  const first = versions.pipelines.find((version: { is_active: boolean }) => !version.is_active).version_id;
  const refused = await page.request.post(`${renderer.origin}/api/flowchart/pipeline?${query}&expected_version_id=${first}`, { data: active });
  expect(refused.status()).toBe(409);
  const listed = await (await page.request.get(`${renderer.origin}/api/flowchart/changes`)).json();
  expect(listed.changes.map((change: { reason: string | null }) => change.reason)).toEqual(['other editor', null]);
});

test('activating an earlier version shows what it changes and records the reason', async ({ page, renderer, workspace, evidence }) => {
  await setup(page, renderer, workspace);
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  await saveDialog(page).getByRole('button', { name: '변경 저장', exact: true }).click();
  await expect(saveDialog(page)).toHaveCount(0);
  await page.locator('[data-flow-node-id="node_inspect"]').click();
  await threshold(page).fill('9');
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  await saveDialog(page).getByLabel('변경 사유').fill('stricter');
  await saveDialog(page).getByRole('button', { name: '변경 저장', exact: true }).click();
  await expect(saveDialog(page)).toHaveCount(0);
  const versions = await (await page.request.get(`${renderer.origin}/api/flowchart/pipelines?source_dataset_path=${encodeURIComponent(workspace.dataset)}`)).json();
  const first = versions.pipelines.find((version: { is_active: boolean }) => !version.is_active).version_id;
  await page.getByLabel('저장 버전', { exact: true }).selectOption(first);
  await page.getByRole('button', { name: '이 버전 활성화', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '저장 버전 활성화' });
  await expect(dialog.getByRole('region', { name: '검사 규칙 차이' })).toContainText('node_inspect · threshold: 9 → 8');
  await expect(dialog.getByRole('button', { name: '활성화', exact: true })).toBeDisabled();
  await dialog.getByLabel('변경 사유').fill('roll back: too many rejects');
  await dialog.getByRole('button', { name: '활성화', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByLabel('저장 버전', { exact: true }).locator('option:checked')).toContainText('● 활성');
  await page.getByRole('button', { name: '변경 기록', exact: true }).click();
  await expect(page.getByRole('region', { name: '검사 규칙 변경 기록' }).getByRole('listitem').first()).toContainText('활성화');
  await expect(page.getByRole('region', { name: '검사 규칙 변경 기록' }).getByRole('listitem').first()).toContainText('사유: roll back: too many rejects');
  await evidence.screenshot(page, 'e04-04-activated');
});
