import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {Request, Response} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

const historyRoute = '/api/flow-workspace/comparisons';
const sha = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
test.use({actionTimeout: 10_000});

// Removing the saved-result refusal makes these original persisted rows crash
// the real renderer (map/property access or cached-latency toFixed), or exposes
// a comparison verdict/preview without displayable execution evidence.
test('persisted legacy comparison results refuse invalid execution evidence and recover on a normal saved row', async ({page, renderer, workspace, evidence}) => {
  const api = async (route: string, body?: unknown, method?: string) => {
    const response = await page.request.fetch(renderer.origin + route, {
      method: method || (body === undefined ? 'GET' : 'POST'),
      timeout: 10_000,
      ...(body === undefined ? {} : {data: body}),
    });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  await installDesktopHostShim(page, renderer.port);
  await api('/api/project/create', {name: 'Synthetic saved comparison refusal', task: 'segmentation'});
  const project = await api('/api/project/update', {source_dataset_dir: workspace.dataset}, 'PUT');
  await api('/api/dataset/import', {folder_path: project.source_dataset_dir, task: 'segmentation', validate_images: true});

  // Make ordinary project reads valid through their real APIs. Import completion
  // below verifies only the two synthetic images; it is not a training job.
  expect((await api('/api/dataset/revisions')).active_revision).toBeNull();
  const admitted = await api('/api/dataset/imports', {task: 'segmentation', verify: true, invalid_policy: 'reject', follow_links: false});
  let imported: any;
  await expect.poll(async () => {
    imported = await api('/api/dataset/imports/' + admitted.job_id);
    return imported.state;
  }, {timeout: 10_000}).toBe('completed');
  expect(imported.result.revision).toMatchObject({state: 'prepared', image_count: 2, valid_count: 2,
    error_count: 0, verified_all: true});
  const accepted = await api('/api/dataset/imports/' + admitted.job_id + '/accept', {
    revision_id: imported.result.revision.revision_id, expected_active: null,
  });
  expect(accepted).toEqual({active_revision: imported.result.revision.revision_id, job_id: admitted.job_id});
  const library = await api('/api/dataset/library/images?state=valid&limit=120');
  expect(library.items).toHaveLength(2);

  // An editable inspection template may have an unbound inspection node. Save
  // and activate it without creating any model binding, checkpoint or result.
  const graph = await api('/api/flowchart/templates/single-segmentation');
  graph.name = 'Unbound synthetic history display baseline';
  expect(graph.nodes.every((node: any) => !node.data.model_job_id)).toBe(true);
  const savedFlow = await api('/api/flowchart/pipeline?' + new URLSearchParams({
    recipe_task: 'segmentation', source_dataset_path: project.source_dataset_dir,
    change_reason: 'Synthetic saved history display baseline; no model or execution', expected_version_id: 'none',
  }), graph);
  expect(savedFlow.status).toBe('saved');
  expect(savedFlow.version_id).toMatch(/^[a-f0-9]{32}$/);
  const draftContext = {project_id: project.id, source_dataset_path: project.source_dataset_dir,
    labelset_id: project.active_labelset_id || 'default'};
  const savedDraft = await api('/api/flowchart/draft', {pipeline: graph, context: draftContext,
    base_version_id: savedFlow.version_id}, 'PUT');
  expect(savedDraft.active_version_id).toBe(savedFlow.version_id);
  const activeFlowRoute = '/api/flowchart/pipeline/active/record?source_dataset_path=' + encodeURIComponent(project.source_dataset_dir);
  const activeFlow = await api(activeFlowRoute);
  expect(activeFlow.version_id).toBe(savedFlow.version_id);
  expect(activeFlow.pipeline).toEqual(savedDraft.pipeline);

  const image = workspace.images[0];
  const preview = 'data:image/png;base64,' + fs.readFileSync(image.path).toString('base64');
  const result = (name: string) => ({
    status: 'completed', final_verdict: 'REVIEW', is_ok: false, annotated_image: preview,
    total_latency_ms: 1, debug_cache: {status: 'hit', original_latency_ms: 2},
    execution_steps: [{node_id: 'synthetic-step', name, status: 'review_required',
      branch_verdict: 'REVIEW', input_count: 1, output_count: 1}],
  });
  const normalA = result('Synthetic saved source step');
  const normalB = result('Synthetic saved candidate step');
  const controls = [
    {id: 'legacy-missing-steps-a', side: 'A', invalid: {}},
    {id: 'legacy-null-steps-b', side: 'B', invalid: {...normalB, execution_steps: null}},
    {id: 'legacy-object-steps-a', side: 'A', invalid: {...normalA, execution_steps: {node_id: 'not-an-array'}}},
    {id: 'legacy-null-step-a', side: 'A', invalid: {...normalA, execution_steps: [null]}},
    {id: 'legacy-object-step-name-b', side: 'B', invalid: {...normalB,
      execution_steps: [{node_id: 'synthetic-step', name: {untrusted: 'object-name'}, status: 'passed'}]}},
    {id: 'legacy-invalid-total-latency-a', side: 'A', invalid: {...normalA, total_latency_ms: 'unknown'}},
    {id: 'legacy-invalid-cache-latency-b', side: 'B', invalid: {...normalB,
      debug_cache: {status: 'hit', original_latency_ms: 'unknown'}}},
  ];
  const cases = [...controls, {id: 'legacy-normal-control', side: '', invalid: null}];
  const directory = path.join(project.project_dir, 'flowcharts', 'comparisons');
  fs.mkdirSync(directory, {recursive: true});
  const originals = new Map<string, Buffer>();
  const records = cases.map((control, index) => {
    const row = {
      image_path: image.path, file_name: path.basename(image.path), image_sha256: image.sha256,
      fixture_case: control.id,
      result_a: control.side === 'A' ? control.invalid : normalA,
      result_b: control.side === 'B' ? control.invalid : normalB,
      difference: {verdict_changed: false, roi_changed: false, changed_nodes: [],
        verdict_a: 'REVIEW', verdict_b: 'REVIEW',
        reason_a: 'Synthetic historical display fixture only', reason_b: 'Synthetic historical display fixture only'},
    };
    // Legacy format is deliberate: no schema_version or record_sha256. These
    // are synthetic persisted display fixtures, never a fabricated verified run.
    const record = {comparison_id: control.id, name: control.id, project_id: project.id,
      source_dataset_path: project.source_dataset_dir, labelset_id: project.active_labelset_id || 'default',
      created_at: `2000-01-${String(index + 1).padStart(2, '0')}T00:00:00Z`, status: 'completed',
      version_a: 'synthetic-history-a', version_b: 'synthetic-history-b', quality_approved: false,
      rows: [row]};
    const file = path.join(directory, control.id + '.json');
    const bytes = Buffer.from(JSON.stringify(record, null, 2) + '\n');
    fs.writeFileSync(file, bytes, {flag: 'wx'});
    originals.set(file, bytes);
    evidence.addFile(file);
    return {...control, record, file};
  });
  const protectedFiles = new Map<string, Buffer>(originals);
  protectedFiles.set(path.join(project.project_dir, 'project.json'), fs.readFileSync(path.join(project.project_dir, 'project.json')));
  const draftFile = path.join(project.project_dir, 'flowcharts', 'drafts', draftContext.labelset_id,
    sha(Buffer.from(project.source_dataset_dir)).slice(0, 16), 'draft.json');
  for (const file of [draftFile, path.join(project.project_dir, 'flowcharts', 'active.json'),
    path.join(project.project_dir, 'flowcharts', 'versions', savedFlow.version_id + '.json')]) {
    protectedFiles.set(file, fs.readFileSync(file)); evidence.addFile(file);
  }
  for (const source of workspace.images) {
    const bytes = fs.readFileSync(source.path);
    expect(sha(bytes)).toBe(source.sha256);
    protectedFiles.set(source.path, bytes);
    evidence.addFile(source.path);
  }
  const unchanged = () => {
    expect(fs.readdirSync(directory).sort()).toEqual(records.map(item => path.basename(item.file)).sort());
    for (const [file, bytes] of protectedFiles) expect(fs.readFileSync(file).equals(bytes), file).toBe(true);
  };
  const initialHistory = await api(historyRoute);
  expect(initialHistory.invalid).toEqual([]);
  expect(initialHistory.comparisons).toHaveLength(8);
  for (const item of records) expect(initialHistory.comparisons).toContainEqual({...item.record, integrity: 'legacy_unverified'});

  const pageErrors: string[] = [], consoleErrors: string[] = [], mutations: unknown[] = [], reads: unknown[] = [];
  const onError = (error: Error) => pageErrors.push(error.message);
  const onConsole = (message: {type(): string; text(): string}) => {if (message.type() === 'error') consoleErrors.push(message.text());};
  const onRequest = (request: Request) => {
    const url = new URL(request.url());
    if (url.origin === renderer.origin && url.pathname.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method()))
      mutations.push({method: request.method(), path: url.pathname, body: request.postData()});
  };
  page.on('pageerror', onError); page.on('console', onConsole); page.on('request', onRequest);
  const recordRead = async (response: Response) => {
    expect(response.status()).toBe(200);
    const bytes = await response.body();
    expect(bytes.length).toBeLessThan(512 * 1024);
    expect(await response.finished()).toBeNull();
    expect(JSON.parse(bytes.toString('utf8'))).toEqual(initialHistory);
    reads.push({method: response.request().method(), url: response.url(), status: response.status(),
      body_base64: bytes.toString('base64'), sha256: sha(bytes), bytes: bytes.length, finished: true});
  };
  const show = async () => {
    const originalRead = page.waitForResponse(response => {
      const request = response.request(), url = new URL(response.url());
      return request.method() === 'GET' && request.frame() === page.mainFrame()
        && url.origin === renderer.origin && url.pathname === historyRoute
        && request.headers()['x-vision-project'] === project.id;
    });
    await page.goto(renderer.url);
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(4).click();
    await page.getByRole('tab', {name: '일괄 평가', exact: true}).click();
    const panel = page.locator('details').filter({has: page.getByText('고정 테스트 세트 · 플로우 버전 A/B 비교', {exact: true})}).first();
    if (await panel.getAttribute('open') === null) await panel.locator('summary').first().click();
    await recordRead(await originalRead);
    await expect(panel.getByLabel('저장 비교 결과', {exact: true}).locator('option')).toHaveCount(9);
    return panel;
  };
  try {
    let panel = await show();
    const normal = async () => {
      await panel.getByLabel('저장 비교 결과', {exact: true}).selectOption('legacy-normal-control');
      await expect(panel.getByRole('alert').filter({hasText: '실행 결과가 불완전해'})).toHaveCount(0);
      await expect(panel.getByText('저장된 원본 비교 결과 JSON', {exact: true})).toHaveCount(0);
      await expect(panel.getByRole('status').filter({hasText: '이전 비교 기록'})).toContainText('해시 미기록');
      await expect(panel.getByText('REVIEW → REVIEW', {exact: false})).toBeVisible();
      await panel.getByText('중간 이미지와 원본 해시', {exact: true}).click();
      await expect(panel.getByText('Synthetic saved source step: REVIEW (1 → 1)', {exact: true})).toBeVisible();
      await expect(panel.getByText('Synthetic saved candidate step: REVIEW (1 → 1)', {exact: true})).toBeVisible();
      for (const side of ['A', 'B']) {
        const img = panel.getByAltText(`버전 ${side} 검사 결과`, {exact: true});
        await expect(img).toHaveAttribute('src', preview);
        await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth === 32 && el.naturalHeight === 32)).toBe(true);
      }
      unchanged();
    };
    await normal();
    for (const control of records.filter(item => item.side)) {
      await panel.getByLabel('저장 비교 결과', {exact: true}).selectOption(control.id);
      const refusal = panel.getByRole('alert').filter({hasText: `저장된 버전 ${control.side} 실행 결과가 불완전해`});
      await expect(refusal).toBeVisible();
      await expect(refusal).toContainText('원본 기록은 변경하지 않았습니다.');
      await expect(panel.getByText('REVIEW → REVIEW', {exact: false})).toHaveCount(0);
      await expect(panel.getByAltText('버전 A 검사 결과', {exact: true})).toHaveCount(0);
      await expect(panel.getByAltText('버전 B 검사 결과', {exact: true})).toHaveCount(0);
      await panel.getByText('저장된 원본 비교 결과 JSON', {exact: true}).click();
      const raw = panel.locator('pre');
      await expect(raw).toBeVisible();
      expect(JSON.parse((await raw.textContent())!)).toEqual(control.record.rows[0]);
      await refusal.scrollIntoViewIfNeeded();
      await expect(refusal).toBeInViewport();
      await evidence.screenshot(page, control.id + '-refused-original-json');
      unchanged();
      await normal();
      expect(pageErrors).toEqual([]); expect(consoleErrors).toEqual([]);
    }
    panel = await show();
    await normal();
    await panel.getByText('Synthetic saved source step: REVIEW (1 → 1)', {exact: true}).scrollIntoViewIfNeeded();
    await evidence.screenshot(page, 'legacy-normal-recovered-after-real-reload');
    expect(await api(historyRoute)).toEqual(initialHistory);
    expect(await api('/api/dataset/library/images?state=valid&limit=120')).toEqual(library);
    expect(await api('/api/flowchart/draft')).toEqual(savedDraft);
    expect(await api(activeFlowRoute)).toEqual(activeFlow);
    unchanged();
    expect(pageErrors).toEqual([]); expect(consoleErrors).toEqual([]); expect(mutations).toEqual([]);
    const proof = {cell: 'F064.saved-comparison.invalid-results', project_id: project.id,
      invalid_controls: controls.map(control => ({id: control.id, side: control.side})),
      real_original_history_reads: reads,
      actual_API_created_baseline: {project_task: project.task, dataset_import: imported,
        accepted_revision: accepted, library, saved_flow: savedFlow, saved_draft: savedDraft, active_flow: activeFlow,
        completed_training_job_created: false, model_binding_created: false, evaluation_result_created: false},
      protected_files: [...protectedFiles].map(([file, bytes]) => ({file, bytes: bytes.length, sha256: sha(bytes)})),
      synthetic_legacy_fixtures: true, legacy_integrity: 'legacy_unverified', synthetic_saved_previews_decoded: true,
      normal_selection_recovered_after_each_refusal: true, actual_renderer_reload_recovered: true,
      original_record_bytes_unchanged: true, UI_mutations: mutations, page_errors: pageErrors, console_errors: consoleErrors,
      model_execution: false, model_training: false, model_GPU_compute: false,
      installed_native: false, source_Electron: false, quality_approved: false};
    const file = path.join(workspace.logs, 'flow-saved-history-invalid-results-proof.json');
    fs.writeFileSync(file, JSON.stringify(proof, null, 2) + '\n', {flag: 'wx'});
    evidence.addFile(file); evidence.note('flow_saved_history_invalid_results', proof);
  } finally {
    evidence.note('flow_saved_history_diagnostics', {page_errors: pageErrors, console_errors: consoleErrors, UI_mutations: mutations, history_reads_completed: reads.length});
    page.off('pageerror', onError); page.off('console', onConsole); page.off('request', onRequest);
  }
});
