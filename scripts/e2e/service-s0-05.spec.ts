import { copyFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { Page, Request, Route } from '@playwright/test';
import { expect, test, type Evidence, type RendererServer, type Workspace } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';
import { confirmFlowSave } from './fixtures/flowChange';

// S0-05: flow versions, stale results and failure states in the real renderer
// against the real backend. Responses that would need a trained model or a
// saved evaluation are fixtures; each one is listed in the run's evidence
// under fixture_responses. Flow run results are synthetic receipts (fixed NG
// verdict) that echo the submitted graph; they are not model decisions. The
// desktop host bridge is the test-only shim, not the Electron preload.

const MODEL = 'job_s005_fixture_model';
const PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
type Json = Record<string, any>;
type ScoreSpec = { domain: 'probability' | 'distance'; unit: string; direction: 'higher_is_defect'; calibration_id: string; threshold: number };

class Gate {
  private held = false;
  private waiting: Array<() => void> = [];
  private arrived: Array<() => void> = [];
  pending = 0;
  hold() { this.held = true; }
  release() { this.held = false; this.waiting.splice(0).forEach(resolve => resolve()); }
  async pass() {
    if (!this.held) return;
    this.pending++;
    this.arrived.splice(0).forEach(resolve => resolve());
    await new Promise<void>(resolve => this.waiting.push(resolve));
    this.pending--;
  }
  async reached() {
    if (this.pending) return;
    await new Promise<void>(resolve => this.arrived.push(resolve));
  }
}

const pathIs = (pathname: string) => (url: URL) => url.pathname === pathname;
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

function fixtureLog(evidence: Evidence) {
  const served: Array<{ method: string; path: string; fixture: string }> = [];
  return {
    served,
    record(request: Request, fixture: string) {
      served.push({ method: request.method(), path: new URL(request.url()).pathname, fixture });
      evidence.note('fixture_responses', served);
    },
  };
}

/** Catalog, provenance check and flow run stand in for a trained model. */
async function routeModelFixtures(page: Page, log: ReturnType<typeof fixtureLog>, options: {
  task: string; source: string; scoreSpec?: ScoreSpec; runGate: Gate; runs: Json[];
}) {
  await page.route(pathIs('/api/flowchart/models/catalog'), route => {
    log.record(route.request(), 'model catalog: one completed model (no trained weights exist)');
    return json(route, { total: 1, models: [{
      job_id: MODEL, task: options.task, label: 'S0-05 fixture model', preset: null, created_at: '2026-10-02T00:00:00Z',
      best_metric: null, source_dataset_path: options.source, class_names: ['ng', 'ok'],
      score_spec: options.scoreSpec ?? null, threshold_settings: { threshold: options.scoreSpec?.threshold ?? 0.5 },
    }] });
  });
  await page.route(pathIs('/api/flowchart/models/verify'), route => {
    const body = route.request().postDataJSON() as { models: Array<{ job_id: string }> };
    log.record(route.request(), 'model provenance verification of the fixture model');
    if (!body.models.every(model => model.job_id === MODEL)) return json(route, { detail: 'unknown model' }, 409);
    return json(route, { verified_job_ids: body.models.map(model => model.job_id) });
  });
  await page.route(pathIs('/api/flowchart/run'), async route => {
    const body = route.request().postDataJSON() as Json;
    options.runs.push(body);
    log.record(route.request(), 'synthetic flow run receipt: fixed NG verdict and 0.9 score, echoing the submitted graph node ids and score spec; not a model decision');
    await options.runGate.pass();
    const nodes = (body.pipeline?.nodes ?? []) as Json[];
    const inspection = nodes.find(node => node.data.node_type === 'inspection');
    const spec = inspection?.data.score_spec as ScoreSpec | undefined;
    return json(route, {
      status: 'complete', final_verdict: 'NG', is_ok: false, rejection_reason: 'fixture: defect score above threshold',
      roi_count: 1, defective_roi_count: 1, total_latency_ms: 4, image_path: body.image_path, image_id: body.image_id,
      execution_target: body.execution_target, execution_device: body.device, compute_profile_id: null,
      crops: [{ roi_id: 'roi-1', source_node_id: inspection?.id, label: 'ng', bbox: [0, 0, 32, 32], defect_score: 0.9,
        verdict: 'NG', crop_thumbnail: PIXEL, flaw_type: 'ng', ...(spec ? { score_spec: spec } : {}) }],
      execution_steps: nodes.map(node => ({ node_id: node.id, name: node.data.label, latency_ms: 1,
        status: node.data.node_type === 'inspection' ? 'flagged_ng' : 'passed' })),
    });
  });
}

async function createProject(page: Page, base: string, task: string, dataset: string) {
  const created = await page.request.post(`${base}/api/project/create`, { data: { name: `s0-05 ${task}`, task } });
  expect(created.status()).toBe(200);
  const updated = await page.request.put(`${base}/api/project/update`, { data: { source_dataset_dir: dataset } });
  expect(updated.status()).toBe(200);
  return await updated.json() as Json;
}

// The anomaly importer reads train/<normal> and test/<normal|defect>; the harness ok/ng tree is refused (422).
function anomalyDataset(workspace: Workspace): string {
  const root = join(workspace.root, 'anomaly-dataset');
  const ok = workspace.images.find(image => image.label === 'ok')!.path;
  const ng = workspace.images.find(image => image.label === 'ng')!.path;
  const layout: Array<[string, Array<[string, string]>]> = [
    ['train/good', [['sample-ok-1.png', ok], ['sample-ok-2.png', ok], ['sample-ok-3.png', ok]]],
    ['test/good', [['sample-ok.png', ok]]],
    ['test/defect', [['sample-ng.png', ng]]],
  ];
  for (const [folder, files] of layout) {
    mkdirSync(join(root, folder), { recursive: true });
    for (const [name, from] of files) copyFileSync(from, join(root, folder, name));
  }
  return root;
}

async function openStep(page: Page, port: number, url: string, step: RegExp) {
  await installDesktopHostShim(page, port);
  await page.goto(url);
  // Reopening a saved source restores its index; it need not issue a new import.
  // Wait for the workspace we actually use instead of a mutation side effect.
  await expect(page.getByRole('navigation', { name: '프로젝트 작업 공간' })).toBeVisible();
  await page.getByRole('button', { name: step }).click();
}

const flowNodes = (page: Page) => page.locator('[data-flow-node-id]');
const inspectionNode = (page: Page) => flowNodes(page).filter({ hasText: /threshold/i }).filter({ hasNotText: /rule:/i });
const decisionNode = (page: Page) => flowNodes(page).filter({ hasText: /rule:/i });
const nodeStatus = (node: ReturnType<typeof flowNodes>) => node.locator('[title^="Status:"]');
const resultsTab = (page: Page) => page.getByRole('button', { name: /^검사 결과/ });
const flowTab = (page: Page) => page.getByRole('tab', { name: '편집', exact: true });
const thresholdInput = (page: Page) => page.getByLabel('결함 판정 임계치');

async function waitForFlow(page: Page) {
  await expect(inspectionNode(page)).toHaveCount(1);
  await expect(page.getByText(/모델 \d+\/\d+ 연결/)).toBeVisible();
}

async function connectFixtureModel(page: Page) {
  await flowTab(page).click();
  await inspectionNode(page).click();
  await page.getByLabel('완료된 학습 모델').selectOption(MODEL);
  await expect(page.getByText('모델 1/1 연결')).toBeVisible();
}

async function chooseImage(page: Page, fileName: string) {
  await page.getByRole('button', { name: /이미지 변경/ }).click();
  await page.getByRole('button', { name: `${fileName} 검사 이미지 선택` }).click();
  await page.getByRole('button', { name: '선택 확정' }).click();
  await expect(page.getByText(fileName).first()).toBeVisible();
}

async function dragNode(page: Page, node: ReturnType<typeof flowNodes>, dx: number, dy: number, blocked=false) {
  const box = await node.boundingBox();
  if (!box) throw new Error('node is not visible');
  const before = await node.evaluate(element => (element as HTMLElement).style.left);
  await page.mouse.move(box.x + box.width / 2, box.y + 12);
  await page.mouse.down();
  for (let step = 1; step <= 6; step++) await page.mouse.move(box.x + box.width / 2 + dx * step / 6, box.y + 12 + dy * step / 6);
  await page.mouse.up();
  const position=()=>node.evaluate(element => (element as HTMLElement).style.left);
  if(blocked)await expect.poll(position).toBe(before);
  else await expect.poll(position).not.toBe(before);
}

const runButton = (page: Page) => page.getByRole('button', { name: /선택 이미지 검사/ });
const discardedRun = /검사 중 이미지나 플로우가 변경되어/;

test.describe('S0-05 flow canvas versions', () => {
  test('a layout move keeps the run current; a rule edit makes it an earlier version until undo', async ({ page, renderer, workspace, evidence }) => {
    const log = fixtureLog(evidence);
    const runs: Json[] = [];
    await createProject(page, renderer.origin, 'classification', workspace.dataset);
    await routeModelFixtures(page, log, { task: 'classification', source: workspace.dataset, runGate: new Gate(), runs });
    await openStep(page, renderer.port, renderer.url, /05.*플로우차트/);
    await waitForFlow(page);
    await connectFixtureModel(page);
    await chooseImage(page, 'sample-ng.png');

    await page.getByRole('tab', { name: '테스트', exact: true }).click();
    await runButton(page).click();
    await expect(resultsTab(page)).toHaveText(/검사 결과 \(1 ROI\)$/);
    await evidence.screenshot(page, '01-run-result');

    await flowTab(page).click();
    await expect(nodeStatus(inspectionNode(page))).toHaveAttribute('title', 'Status: FAIL');
    await dragNode(page, decisionNode(page), 140, 60);
    await expect(resultsTab(page), 'moving a node keeps the result current').toHaveText(/검사 결과 \(1 ROI\)$/);
    await expect(nodeStatus(inspectionNode(page)), 'the canvas keeps showing the current run').toHaveAttribute('title', 'Status: FAIL');
    await evidence.screenshot(page, '02-after-layout-move');

    await inspectionNode(page).click();
    await thresholdInput(page).fill('0.7');
    await expect(resultsTab(page), 'a rule edit keeps the result as an earlier version').toHaveText(/검사 결과 \(1 ROI\) · 이전 버전$/);
    await expect(nodeStatus(inspectionNode(page)), 'the canvas does not show an earlier version as the current rules').toHaveAttribute('title', 'Status: STBY');
    await expect(page.getByRole('status', { name: '이전 버전 노드 근거' })).toBeVisible();
    await evidence.screenshot(page, '03a-canvas-after-rule-edit');
    await resultsTab(page).click();
    await expect(page.getByRole('status', { name: '이전 버전 실행 결과' })).toBeVisible();
    await evidence.screenshot(page, '03-after-rule-edit');

    await flowTab(page).click();
    await page.getByRole('button', { name: '플로우 실행 취소' }).click();
    await expect(thresholdInput(page)).toHaveValue('0.5');
    await expect(resultsTab(page), 'undo returns to the executed rules').toHaveText(/검사 결과 \(1 ROI\)$/);
    await expect(nodeStatus(inspectionNode(page))).toHaveAttribute('title', 'Status: FAIL');
    await expect(page.getByRole('status', { name: '이전 버전 노드 근거' })).toHaveCount(0);
    await evidence.screenshot(page, '04-after-undo');
    expect(runs).toHaveLength(1);
  });

  test('an inflight run refuses layout moves and records changed rules as an earlier version', async ({ page, renderer, workspace, evidence }) => {
    const log = fixtureLog(evidence);
    const runs: Json[] = [];
    const gate = new Gate();
    await createProject(page, renderer.origin, 'classification', workspace.dataset);
    await routeModelFixtures(page, log, { task: 'classification', source: workspace.dataset, runGate: gate, runs });
    await openStep(page, renderer.port, renderer.url, /05.*플로우차트/);
    await waitForFlow(page);
    await connectFixtureModel(page);
    await chooseImage(page, 'sample-ng.png');

    gate.hold();
    await page.getByRole('tab', { name: '테스트', exact: true }).click();
    await runButton(page).click();
    await gate.reached();
    await flowTab(page).click();
    await dragNode(page, decisionNode(page), 120, 40,true);
    gate.release();
    await expect(resultsTab(page), 'the run that was moving still lands').toHaveText(/검사 결과 \(1 ROI\)$/);
    await expect(page.getByText(discardedRun)).toHaveCount(0);
    await evidence.screenshot(page, '01-move-during-run');

    await flowTab(page).click();
    await inspectionNode(page).click();
    gate.hold();
    await page.getByRole('tab', { name: '테스트', exact: true }).click();
    await runButton(page).click();
    await gate.reached();
    await flowTab(page).click();
    await thresholdInput(page).fill('0.8');
    gate.release();
    await expect(resultsTab(page), 'a rule edit preserves the captured run as an earlier version').toHaveText(/검사 결과 \(1 ROI\) · 이전 버전$/);
    await expect(page.getByText(discardedRun)).toHaveCount(0);
    await expect(page.getByRole('status', { name: '이전 버전 실행 결과' })).toBeVisible();
    await evidence.screenshot(page, '02-rule-edit-during-run');
    expect(runs.map(run => run.pipeline.nodes.find((node: Json) => node.data.node_type === 'inspection').data.threshold)).toEqual([0.5, 0.5]);
  });
});

/** Two saved versions, one frozen cohort and stored evaluations with an execution error on the NG image. */
async function openEvaluation({ page, renderer, workspace, evidence }: { page: Page; renderer: RendererServer; workspace: Workspace; evidence: Evidence },
  options: { failReadAfterTruth?: boolean } = {}) {
  const log = fixtureLog(evidence);
  const project = await createProject(page, renderer.origin, 'classification', workspace.dataset);
  const ngImage = join(project.source_dataset_dir,'ng','sample-ng.png');
  const scope = {
    project_id: project.id, source_dataset_path: project.source_dataset_dir, labelset_id: project.active_labelset_id || 'default',
    task: 'classification', classes: ['ng', 'ok'],
    class_semantics: { version: 1, roles: { ng: 'defect', ok: 'normal' }, basis: { ng: 'explicit', ok: 'explicit' } },
  };
  const versions = ['A', 'B'].map((name, index) => ({
    version_id: `${name.toLowerCase()}`.repeat(8) + `-s005-${index}`, pipeline_id: 'flow', name: `Flow ${name}`,
    recipe_task: 'classification', source_dataset_path: project.source_dataset_dir,
    created_at: `2026-10-02T0${index}:00:00Z`, saved_at: `2026-10-02T0${index}:00:00Z`, is_active: index === 0,
  }));
  const [versionA, versionB] = versions.map(row => row.version_id);
  const cohort = { cohort_id: 'cohort-s005', name: '시험 분할 전체', created_at: '2026-10-02T00:00:00Z', scope, count: 2, split: 'test',
    record_sha256: 'a'.repeat(64), input_sha256: 'b'.repeat(64), truth_sha256: 'c'.repeat(64), split_sha256: 'd'.repeat(64) };
  const errorRecord = {
    relative_path: 'ng/sample-ng.png', image_path: ngImage, input_sha256: 'e'.repeat(64), truth_sha256: 'f'.repeat(64),
    truth: 'NG', truth_reason: null, decision: 'REVIEW', execution_status: 'error', error: 'fixture: inspection node failed',
    rejection_reason: 'fixture: inspection node failed',
    node_evidence: [{ node_id: 'inspect', name: 'Inspection', status: 'error', selected_edge_ids: [], artifacts: [] }], roi_evidence: [],
  };
  const evaluation = (id: string, versionId: string) => ({
    evaluation_id: id, created_at: '2026-10-02T01:00:00Z', status: 'complete', version_id: versionId, cohort_id: cohort.cohort_id, scope,
    graph_sha256: '1'.repeat(64), cohort_sha256: '2'.repeat(64), truth_sha256: '3'.repeat(64), input_sha256: '4'.repeat(64),
    model_sha256: '5'.repeat(64), record_sha256: '6'.repeat(64), device: 'cpu',
    coverage: { total: 2, known: 2, unknown: 0, invalidated: 0, known_fraction: 1 },
    confusion: { OK: { OK: 1, NG: 0, REVIEW: 0 }, NG: { OK: 0, NG: 0, REVIEW: 1 } },
    metrics: { escape_rate: 0, overkill_rate: 0, review_rate: 0.5, escape_unavailable_reason: null, overkill_unavailable_reason: null, normal_count: 1, defect_count: 1 },
    validity: { valid: true, reasons: [] },
    records: [errorRecord], escapes: [], overkills: [], unknown_truth: [], errors: [errorRecord],
  });
  const evaluations = new Map<string, Json>([['eval-b-history', evaluation('eval-b-history', versionB)]]);
  const gate = new Gate();
  const summary = (row: Json) => {
    const { records: _r, escapes: _e, overkills: _o, unknown_truth: _u, errors: _x, ...rest } = row;
    return rest;
  };

  await page.route(pathIs('/api/flowchart/pipelines'), route => {
    log.record(route.request(), 'two saved flow versions');
    return json(route, { pipelines: versions, total: versions.length });
  });
  await page.route(url => url.pathname.startsWith('/api/flow-evaluations/scope/'), route => {
    log.record(route.request(), 'truth scope of the saved version');
    return json(route, scope);
  });
  await page.route(pathIs('/api/flow-evaluations/cohorts'), route => {
    log.record(route.request(), 'one frozen test cohort');
    return json(route, { cohorts: [cohort], total: 1 });
  });
  let truthRevision = 1;
  await page.route(pathIs('/api/image-truth'), route => {
    const truth = (imagePath: string | null, verdict: string, defects: string[]) => ({ image_path: imagePath, relative_path: 'ng/sample-ng.png',
      image_uuid: 'img-s005', image_revision: 1, truth_revision: truthRevision, scope, verdict, defect_classes: defects, reviewer: 'fixture',
      invalidated: false, unknown_reason: null, truth_sha256: String(truthRevision).repeat(64).slice(0, 64) });
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as Json;
      log.record(route.request(), 'explicit truth saved; stored evaluations become stale as the server reports them');
      truthRevision++;
      for (const row of evaluations.values()) row.validity = { valid: false, reasons: ['truth_or_source_changed:ng/sample-ng.png'] };
      return json(route, truth(body.image_path, body.verdict, body.defect_classes));
    }
    log.record(route.request(), 'explicit truth of the selected image');
    return json(route, truth(new URL(route.request().url()).searchParams.get('image_path'), 'NG', ['ng']));
  });
  await page.route(pathIs('/api/flow-evaluations'), async route => {
    if (route.request().method() === 'GET') {
      log.record(route.request(), 'evaluation history');
      return json(route, { evaluations: [...evaluations.values()].map(summary), total: evaluations.size });
    }
    const body = route.request().postDataJSON() as { version_id: string };
    log.record(route.request(), `evaluation run of ${body.version_id}`);
    await gate.pass();
    const row = evaluation(`eval-${body.version_id.slice(0, 1)}-run-${evaluations.size}`, body.version_id);
    evaluations.set(row.evaluation_id, row);
    return json(route, row);
  });
  await page.route(url => /^\/api\/flow-evaluations\/eval-/.test(url.pathname), route => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop()!);
    if (options.failReadAfterTruth && truthRevision > 1) {
      log.record(route.request(), `stored evaluation ${id} unavailable after the truth change`);
      return json(route, { detail: 'fixture: evaluation store unavailable' }, 503);
    }
    log.record(route.request(), `stored evaluation ${id}`);
    return evaluations.has(id) ? json(route, evaluations.get(id)) : json(route, { detail: 'not found' }, 404);
  });

  await openStep(page, renderer.port, renderer.url, /05.*플로우차트/);
  await waitForFlow(page);
  await page.getByRole('tab', { name: '일괄 평가', exact: true }).click();
  const panel = page.locator('section[aria-label="전체 흐름 평가"]');
  const versionSelect = panel.getByLabel('전체 흐름 평가 버전');
  await expect(versionSelect).toHaveValue(versionA);
  const chooseCohort = () => panel.getByLabel('전체 흐름 시험 코호트').selectOption(cohort.cohort_id);
  await chooseCohort();
  return {
    panel, versionSelect, versionA, versionB, gate, chooseCohort, ngImage,
    run: () => panel.getByRole('button', { name: '선택 코호트 평가' }).click(),
    valid: panel.getByText(/^저장된 평가 근거 유효/),
    notThisVersion: panel.getByText(/^선택한 버전의 평가 아님/),
    reviewQueue: panel.getByRole('button', { name: '오류·검토·미확인 이미지를 검토 큐로 보내기' }),
    showVersion: panel.getByRole('button', { name: '해당 버전 보기' }),
  };
}

test.describe('S0-05 whole-flow evaluation', () => {
  test('an evaluation of another version is never shown as valid for the selected version', async ({ page, renderer, workspace, evidence }) => {
    const view = await openEvaluation({ page, renderer, workspace, evidence });
    // A stored evaluation of version B read while version A is selected.
    await view.panel.getByLabel('전체 흐름 평가 이력').selectOption('eval-b-history');
    await expect(view.notThisVersion).toBeVisible();
    await expect(view.valid).toHaveCount(0);
    await expect(view.panel.getByText(`이 결과는 다른 흐름 버전(${view.versionB})의 평가입니다.`, { exact: false })).toBeVisible();
    await expect(view.reviewQueue).toBeDisabled();
    await evidence.screenshot(page, '01-history-of-other-version');

    // A finished evaluation of version A, then version B is selected.
    await view.versionSelect.selectOption(view.versionA);
    await view.chooseCohort();
    await view.run();
    await expect(view.valid).toBeVisible();
    await view.versionSelect.selectOption(view.versionB);
    await expect(view.notThisVersion).toBeVisible();
    await expect(view.valid).toHaveCount(0);
    await expect(view.reviewQueue).toBeDisabled();
    await evidence.screenshot(page, '02-version-switch-after-run');
    await view.showVersion.click();
    await expect(view.versionSelect).toHaveValue(view.versionA);
    await expect(view.valid).toBeVisible();
    await expect(view.reviewQueue).toBeEnabled();
  });

  test('an evaluation finishing after a version switch does not replace the selected version result', async ({ page, renderer, workspace, evidence }) => {
    const view = await openEvaluation({ page, renderer, workspace, evidence });
    await view.panel.getByLabel('전체 흐름 평가 이력').selectOption('eval-b-history');
    view.gate.hold();
    await view.run();
    await view.gate.reached();
    // The only control that changes the version while an evaluation runs.
    await view.showVersion.click();
    await expect(view.versionSelect).toHaveValue(view.versionB);
    view.gate.release();
    await expect(view.panel.getByText(`버전 ${view.versionA}의 평가가 끝났습니다.`, { exact: false })).toBeVisible();
    await expect(view.panel.getByText(`흐름 버전: ${view.versionB}`, { exact: false })).toHaveCount(1);
    await expect(view.valid, 'the shown result belongs to the selected version B').toBeVisible();
    await evidence.screenshot(page, '01-late-result-kept-out');
  });

  test('saving an explicit truth marks the shown evaluation for re-evaluation at once', async ({ page, renderer, workspace, evidence }) => {
    const view = await openEvaluation({ page, renderer, workspace, evidence });
    await view.run();
    await expect(view.valid).toBeVisible();
    await expect(view.reviewQueue).toBeEnabled();
    await view.panel.getByText('이미지별 명시적 정답 검토').click();
    await expect(view.panel.getByText(/현재 정답: NG/)).toBeVisible();
    await view.panel.getByLabel('정답 검토자').fill('S0-05 reviewer');
    await view.panel.getByLabel('명시적 정답 판정').selectOption('OK');
    await view.panel.getByRole('button', { name: '선택 이미지 정답 저장' }).click();
    await expect(view.panel.getByText('선택 이미지 정답을 저장했습니다.', { exact: false })).toBeVisible();
    await expect(view.valid, 'evidence computed before the truth change is not shown as valid').toHaveCount(0);
    await expect(view.panel.getByText(/^재평가 필요/)).toBeVisible();
    await expect(view.panel.getByText('truth_or_source_changed:ng/sample-ng.png')).toBeVisible();
    await expect(view.reviewQueue).toBeDisabled();
    await evidence.screenshot(page, '01-truth-saved-evaluation-stale');
  });

  test('the shown evaluation stays marked for re-evaluation when the re-read after a truth save fails', async ({ page, renderer, workspace, evidence }) => {
    const view = await openEvaluation({ page, renderer, workspace, evidence }, { failReadAfterTruth: true });
    await view.run();
    await expect(view.valid).toBeVisible();
    await view.panel.getByText('이미지별 명시적 정답 검토').click();
    await expect(view.panel.getByText(/현재 정답: NG/)).toBeVisible();
    await view.panel.getByLabel('정답 검토자').fill('S0-05 reviewer');
    await view.panel.getByLabel('명시적 정답 판정').selectOption('OK');
    await view.panel.getByRole('button', { name: '선택 이미지 정답 저장' }).click();
    await expect(view.panel.getByText('선택 이미지 정답을 저장했습니다.', { exact: false })).toBeVisible();
    await expect(view.valid).toHaveCount(0);
    await expect(view.panel.getByText(/^재평가 필요/)).toBeVisible();
    await expect(view.reviewQueue).toBeDisabled();
    await evidence.screenshot(page, '01-truth-saved-reread-failed');
  });

  test('an error image opens in the image viewer', async ({ page, renderer, workspace, evidence }) => {
    const view = await openEvaluation({ page, renderer, workspace, evidence });
    await view.run();
    await expect(view.valid).toBeVisible();
    await view.panel.getByRole('button', { name: '노드·ROI 보기' }).click();
    await evidence.screenshot(page, '01-error-evidence');
    // The labeling viewer draws the exact source image on its canvas from the raw image endpoint.
    const ngImage = view.ngImage;
    const viewerLoad = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/dataset/raw/sample-ng.png' && url.searchParams.get('file_path') === ngImage;
    });
    await view.panel.getByRole('button', { name: '원본 이미지 열기' }).click();
    await expect(page.locator('canvas').first()).toBeVisible();
    await expect(page.getByTitle('선택 및 이동 (Select / Move - 1)', {exact:true})).toBeVisible();
    expect((await viewerLoad).status()).toBe(200);
    await evidence.screenshot(page, '02-error-image-in-viewer');
  });

  test('an error image can be run on the flow canvas', async ({ page, renderer, workspace, evidence }) => {
    const runs: Json[] = [];
    await routeModelFixtures(page, fixtureLog(evidence), { task: 'classification', source: workspace.dataset, runGate: new Gate(), runs });
    const view = await openEvaluation({ page, renderer, workspace, evidence });
    const ngImage = view.ngImage;
    await view.run();
    await view.panel.getByRole('button', { name: '노드·ROI 보기' }).click();
    await view.panel.getByRole('button', { name: '흐름에서 이 이미지로 실행' }).click();
    await expect(view.panel.getByRole('button', { name: '선택 코호트 평가' })).toHaveCount(0);
    await expect(page.getByText('sample-ng.png').first()).toBeVisible();
    await connectFixtureModel(page);
    await page.getByRole('tab', { name: '테스트', exact: true }).click();
    await runButton(page).click();
    await expect(resultsTab(page)).toHaveText(/검사 결과 \(1 ROI\)$/);
    expect(runs.at(-1)!.image_path, 'the flow runs the exact error image').toBe(ngImage);
    await evidence.screenshot(page, '01-error-image-run-on-canvas');
  });
});

test('a refused model family change keeps the active family and says why', async ({ page, renderer, workspace, evidence }) => {
  const log = fixtureLog(evidence);
  await createProject(page, renderer.origin, 'classification', workspace.dataset);
  const capabilities = await (await page.request.get(`${renderer.origin}/api/models/capabilities`)).json() as { families: Array<{ task: string; label: string }> };
  const label = (task: string) => capabilities.families.find(family => family.task === task)!.label;
  await page.route(pathIs('/api/project/update'), route => {
    const body = route.request().postDataJSON() as Json;
    if (route.request().method() !== 'PUT' || !('task' in body)) return route.fallback();
    log.record(route.request(), 'task change refused while another job runs');
    return json(route, { detail: 'Another job is running (S0-05 fixture)' }, 409);
  });
  await openStep(page, renderer.port, renderer.url, /03.*오토딥러닝/);
  const hub = page.getByRole('region', { name: '모델 학습 허브' });
  const family = (task: string) => hub.getByRole('button', { name: new RegExp(`^${label(task)}`) });
  await expect(family('classification')).toHaveAttribute('aria-pressed', 'true');
  await family('segmentation').click();
  await page.getByRole('dialog', { name: '검사 작업 변경 영향' }).getByRole('button', { name: '영향 확인 후 변경', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: '모델 종류 변경 후 확인이 필요합니다: Another job is running (S0-05 fixture)' })).toBeVisible();
  await expect(family('classification')).toHaveAttribute('aria-pressed', 'true');
  await expect(family('segmentation')).toHaveAttribute('aria-pressed', 'false');
  const current = await (await page.request.get(`${renderer.origin}/api/project/current`)).json() as Json;
  expect(current.task, 'the project task is unchanged').toBe('classification');
  await evidence.screenshot(page, 'refused-family-change');
});

test('S0-02: a raw distance threshold of 8 is saved, reopened and used for the run', async ({ page, renderer, workspace, evidence }) => {
  const log = fixtureLog(evidence);
  const runs: Json[] = [];
  const project = await createProject(page, renderer.origin, 'anomaly', anomalyDataset(workspace));
  const scoreSpec: ScoreSpec = { domain: 'distance', unit: 'mahalanobis_distance', direction: 'higher_is_defect', calibration_id: 'fixture-calibration', threshold: 3.5 };
  // The canonical source the backend recorded (macOS resolves /var to /private/var) binds the model fixture.
  await routeModelFixtures(page, log, { task: 'anomaly', source: project.source_dataset_dir, scoreSpec, runGate: new Gate(), runs });
  await openStep(page, renderer.port, renderer.url, /05.*플로우차트/);
  await waitForFlow(page);
  await connectFixtureModel(page);
  await expect(thresholdInput(page)).toHaveValue('3.5');
  await thresholdInput(page).fill('8');
  await expect(page.getByText('결함 판정 임계치 · mahalanobis_distance')).toBeVisible();
  await expect(page.getByText('8.00', { exact: true })).toBeVisible();
  const saved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/pipeline' && response.request().method() === 'POST');
  await page.getByRole('button', { name: '플로우 저장', exact: true }).click();
  await confirmFlowSave(page, 'threshold 8 for the anomaly check');
  expect((await saved).status()).toBe(200);
  await evidence.screenshot(page, '01-threshold-8-saved');

  const active = await (await page.request.get(`${renderer.origin}/api/flowchart/pipeline/active?source_dataset_path=${encodeURIComponent(project.source_dataset_dir)}`)).json() as Json;
  const stored = active.nodes.find((node: Json) => node.data.node_type === 'inspection').data;
  evidence.note('stored_inspection_node', stored);
  expect(stored.threshold).toBe(8);
  expect(stored.score_spec).toMatchObject({ domain: 'distance', threshold: 8 });

  await page.reload();
  await expect(page.getByRole('navigation', { name: '프로젝트 작업 공간' })).toBeVisible();
  if (!(await inspectionNode(page).count())) await page.getByRole('button', { name: /05.*플로우차트/ }).click();
  await waitForFlow(page);
  await expect(page.getByText('모델 1/1 연결')).toBeVisible();
  await inspectionNode(page).click();
  await expect(thresholdInput(page), 'the reopened flow keeps the raw threshold').toHaveValue('8');
  await evidence.screenshot(page, '02-threshold-8-reopened');

  await chooseImage(page, 'sample-ng.png');
  await page.getByRole('tab', { name: '테스트', exact: true }).click();
  await runButton(page).click();
  await expect(resultsTab(page)).toHaveText(/검사 결과 \(1 ROI\)$/);
  const sent = runs.at(-1)!.pipeline.nodes.find((node: Json) => node.data.node_type === 'inspection').data;
  expect(sent.threshold).toBe(8);
  expect(sent.score_spec).toMatchObject({ domain: 'distance', unit: 'mahalanobis_distance', threshold: 8 });
  await expect(page.getByLabel('실행 점수 기준')).toHaveText(/mahalanobis_distance · 임계값 8/);
  await evidence.screenshot(page, '03-threshold-8-run');
});
