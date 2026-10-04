import type { Page, Route } from '@playwright/test';
import { expect, test, type RendererServer, type Workspace } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';
import { confirmFlowSave } from './fixtures/flowChange';

// E07 on the actual renderer and backend (owned synthetic harness). The model catalog and reference check are
// transport fixtures as in the S2-04 spec, so the saved flow names a model this project never trained: the preflight
// must find that at its node, with what to do. No trained weights, package or deployment is claimed.
const model = 'e07-distance-model';
const score = { domain: 'distance', unit: 'mahalanobis_distance', direction: 'higher_is_defect', calibration_id: 'e07-calibration', threshold: 8 };
const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function savedFlow(page: Page, renderer: RendererServer, workspace: Workspace) {
  expect((await page.request.post(`${renderer.origin}/api/project/create`, { data: { name: 'E07 workspace', task: 'anomaly' } })).ok()).toBe(true);
  expect((await page.request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: workspace.dataset } })).ok()).toBe(true);
  await page.route('**/api/flowchart/models/catalog?*', route => json(route, { models: [{ job_id: model, task: 'anomaly', label: 'Recorded distance model',
    preset: null, created_at: null, best_metric: null, source_dataset_path: workspace.dataset, class_names: ['ng', 'ok'], class_ids: [1, 2], score_spec: score }], total: 1 }));
  await page.route('**/api/flowchart/models/verify', route => json(route, { verified_job_ids: [model] }));
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  await page.getByRole('button', { name: /05.*플로우차트/ }).click();
  await page.getByRole('list', { name: '목적 레시피' }).getByRole('button', { name: /^고정 ROI 검사/ }).click();
  const recipe = page.getByRole('dialog', { name: '레시피 미리보기·모델 매핑' });
  await recipe.getByLabel('검사 모델 레시피 모델', { exact: true }).selectOption(model);
  await recipe.getByLabel('검사 모델 클래스 적용 범위').selectOption('all');
  await recipe.getByRole('button', { name: '매핑 확인·새 초안으로 채택' }).click();
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  await confirmFlowSave(page, 'first flow');
  await expect(page.getByLabel('저장 버전', { exact: true })).not.toHaveValue('');
}

test('the preflight maps a missing model to its node with a remedy, separates this computer from an edge target and reopens', async ({ page, renderer, workspace, evidence }) => {
  await savedFlow(page, renderer, workspace);
  await page.getByRole('tab', { name: '배포', exact: true }).click();
  const panel = page.getByRole('region', { name: '배포 전 의존성 점검' });
  await expect(panel.getByLabel('점검 대상').locator('option:checked')).toHaveText('이 컴퓨터 · CPU');
  await panel.getByRole('button', { name: '점검 실행' }).click();
  await expect(panel.getByRole('status')).toContainText('차단됨: 노드');
  await expect(panel.getByRole('status')).toContainText('최종 판정 포함');
  const nodes = panel.getByRole('list', { name: '노드별 의존성' });
  const inspect = nodes.getByRole('listitem').filter({ has: page.getByRole('button', { name: /검사/ }) }).filter({ hasText: `anomaly:${model}` });
  await expect(inspect).toContainText('막힘');
  await expect(inspect).toContainText(/모델 anomaly:e07-distance-model · (없음|불일치)/);
  await expect(inspect).toContainText(/3단계에서 모델을 학습하거나|다시 학습하거나 다른 완료 모델을 연결하세요/);
  await expect(inspect).toContainText('장치 cpu · 준비됨');
  await expect(nodes.getByRole('listitem').first()).toContainText('플로우 전체');
  await evidence.screenshot(page, 'e07-01-blocked-at-node');

  // The same check for an edge computer: its runtime and device wait for the package's own preflight there.
  await panel.getByLabel('점검 대상').selectOption({ label: 'Edge · Windows x64 · CPU' });
  await expect(panel.getByRole('status')).toHaveCount(0);
  await panel.getByRole('button', { name: '점검 실행' }).click();
  await expect(nodes).toContainText('장치 cpu · 대상에서 확인 필요');
  await expect(nodes).toContainText('run_flow.py --preflight로 확인하세요');
  // Back to this computer: its kept report reopens, current.
  await panel.getByLabel('점검 대상').selectOption({ label: '이 컴퓨터 · CPU' });
  await expect(panel.getByRole('status')).toContainText('차단됨: 노드');
  await expect(panel.getByRole('alert')).toHaveCount(0);
  // The node link opens it in the editor.
  await inspect.getByRole('button').first().click();
  await expect(page.getByRole('tab', { name: '편집', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText(/^선택한 노드 · /)).toBeVisible();
  const reports = await (await page.request.get(`${renderer.origin}/api/export/flow/preflights`)).json();
  expect(reports.reports.map((row: { target_identity: { kind: string } }) => row.target_identity.kind).sort()).toEqual(['edge', 'this_computer']);
  evidence.note('preflight', { reports: reports.reports.length });
});
