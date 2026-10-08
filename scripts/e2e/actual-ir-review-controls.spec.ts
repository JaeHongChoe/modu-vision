import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page, Request, Route, Locator} from '@playwright/test';
import {test, expect} from './fixtures/test';
const harness = require('./fixtures/harness.cjs');
const sha = (file: string) => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function hashes(root: string): Record<string, string> {
  const result: Record<string, string> = {};
  const visit = (folder: string) => {
    for (const item of fs.readdirSync(folder, {withFileTypes: true})) {
      const file = path.join(folder, item.name);
      expect(item.isSymbolicLink()).toBe(false);
      if (item.isDirectory()) visit(file);
      else { expect(item.isFile()).toBe(true); result[path.relative(root, file).split(path.sep).join('/')] = sha(file); }
    }
  };
  visit(root); return result;
}
const routePath = (request: Request) => new URL(request.url()).pathname;
async function openProject(page: Page, projectDir: string) {
  await page.getByTitle('프로젝트 관리', {exact: true}).click();
  const dialog = page.getByRole('dialog', {name: '프로젝트 관리', exact: true});
  await dialog.getByRole('button', {name: '폴더에서 열기', exact: true}).click();
  await dialog.getByPlaceholder('/path/to/project', {exact: true}).fill(projectDir);
  const reply = page.waitForResponse(response => routePath(response.request()) === '/api/project/open' && response.request().method() === 'POST');
  await dialog.getByRole('button', {name: '프로젝트 열기', exact: true}).click();
  expect((await reply).status()).toBe(200); await expect(dialog).toHaveCount(0);
}

// Real two-image conversion is a CPU control. Generated all-OK weights and
// permissive synthetic reviews supply no human truth or quality acceptance.
test('native actual IR candidate review and library controls preserve exact original bindings', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  test.setTimeout(480_000);
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const api = (route: string, body?: unknown, method?: string): Promise<any> => page.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`Owned IR control ${route}: HTTP ${response.status}`);
    return response.json();
  }, {port: backend.port, route, body, method});
  expect(process.env.MV_E2E_OPENVINO_PYTHON, 'Actual IR controls require the explicit CPU interpreter').toBeTruthy();
  const capabilities = await api('/api/export/runtime-capabilities');
  expect(capabilities.openvino.available, JSON.stringify(capabilities.openvino)).toBe(true);
  expect(capabilities.openvino.devices).toContain('CPU');
  const fixtureEnv = {...process.env, HOME: workspace.home, USERPROFILE: workspace.home,
    XDG_CACHE_HOME: path.join(workspace.home, '.cache'), TORCH_HOME: path.join(workspace.home, '.cache', 'torch'),
    HF_HOME: path.join(workspace.home, '.cache', 'huggingface'), VISION_AI_STUDIO_USER_DATA_DIR: workspace.userData};
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/converted_flow_control.py'), workspace.root],
    {cwd: harness.REPO_ROOT, env: fixtureEnv, encoding: 'utf8', timeout: 120_000}).trim());
  const emptyProject = await api('/api/project/create', {name: 'Empty IR library controls', task: 'classification', project_dir: path.join(workspace.projects, 'empty-ir-library')});
  await api('/api/project/open', {project_dir: fixture.project.project_dir});
  const writes: Array<{method: string; pathname: string; body: unknown}> = [];
  const observe = (request: Request) => {
    const pathname = routePath(request);
    if (request.method() !== 'GET' && (/^\/api\/(export\/flow|flow-evaluations|product-delivery|runtime-services)/.test(pathname)))
      writes.push({method: request.method(), pathname, body: request.postData() ? request.postDataJSON() : null});
  };
  page.on('request', observe);
  const dimensions: Record<string, unknown> = {}, jobs: string[] = [], cleanupJobs: unknown[] = [];
  let releaseHeld = () => {}, removeHeldObservers = () => {}, heldHandler: ((route: Route) => Promise<void>) | undefined;
  const library = () => page.getByRole('region', {name: '저장된 검사 패키지 보관함', exact: true});
  const optimization = () => page.getByRole('region', {name: 'Runtime 최적화와 양자화', exact: true});
  const precision = () => optimization().getByRole('group', {name: '정밀도·Runtime 별도 승인', exact: true});
  const approve = () => precision().getByRole('button', {name: '검토 후 새 승인 패키지 생성', exact: true});
  const openDelivery = async () => {
    await page.getByRole('button', {name: '패키지·장치·진단', exact: true}).click();
    await expect(library()).toBeVisible();
  };
  const closeDelivery = async () => {
    await page.getByRole('button', {name: '패키지·장치·설치·진단 닫기', exact: true}).click();
    await expect(library()).toHaveCount(0); await expect(optimization()).toHaveCount(0);
  };
  const snapshot = async (name: string, control: Locator) => {
    await control.scrollIntoViewIfNeeded(); await expect(control).toBeInViewport(); await evidence.screenshot(page, 'native-ir-' + name);
  };
  const optimizeOriginal = async () => {
    await library().getByRole('article').filter({hasText: 'original_cpu_control'}).getByRole('button', {name: '최적화로 이동', exact: true}).click();
    const cohort = optimization().getByRole('group', {name: '변환 오차 검증 · test / val', exact: true});
    await expect(cohort.getByRole('checkbox')).toHaveCount(16);
    for (const checkbox of await cohort.getByRole('checkbox').all()) await checkbox.uncheck();
    await cohort.getByRole('checkbox', {name: 'ok_00.png', exact: true}).check();
    await cohort.getByRole('checkbox', {name: 'ng_00.png', exact: true}).check();
  };
  const fillPrecision = async () => {
    await precision().getByLabel('검토자', {exact: true}).fill('Synthetic IR control authority');
    await precision().getByLabel(/^검토 근거/).fill('Two actual CPU/IR heldout controls; no human truth or manufacturing quality acceptance');
    await precision().getByRole('checkbox').check();
    await precision().getByLabel('최대 절대 오차', {exact: true}).fill('0.001');
  };
  try {
    await page.reload(); await openDelivery(); await optimizeOriginal();
    const submit = () => optimization().getByRole('button', {name: '독립 후보 패키지 생성', exact: true});
    const firstReply = page.waitForResponse(response => routePath(response.request()) === '/api/export/flow/optimize' && response.request().method() === 'POST');
    await submit().click(); const firstResponse = await firstReply; expect(firstResponse.status()).toBe(200);
    const first = await firstResponse.json(); jobs.push(first.job_id);
    expect(first.options.package_dir).toBe(fixture.package.package_path);
    expect(first.options.input_receipt.source_dataset_path).toBe(fixture.source);
    expect(['queued', 'running', 'stopping']).toContain(first.status);
    const cancelPath = `/api/export/flow/optimization-jobs/${first.job_id}/cancel`;
    const cancelledReply = page.waitForResponse(response => routePath(response.request()) === cancelPath && response.request().method() === 'POST');
    await optimization().getByRole('button', {name: '변환 중단', exact: true}).click();
    const cancelledResponse = await cancelledReply; expect(cancelledResponse.status()).toBe(200);
    const acknowledged = await cancelledResponse.json(); expect(acknowledged.job_id).toBe(first.job_id);
    let cancelled: any;
    await expect.poll(async () => { cancelled = await api(`/api/export/flow/optimization-jobs/${first.job_id}`); return cancelled.status; }, {timeout: 40_000}).toBe('cancelled');
    await expect(optimization().getByRole('status').first()).toContainText('cancelled');
    expect(cancelled.result).toBeNull(); expect(fs.existsSync(cancelled.package_path)).toBe(false);
    dimensions.candidate_cancel = {job_id: first.job_id, original_start: first, cancel_path: cancelPath, response: acknowledged,
      terminal: cancelled, actual_conversion_owner_cancelled: true, candidate_published: false, whole_process_tree_verified: false};
    await snapshot('original-candidate-terminal-cancelled', submit());

    const measuredReply = page.waitForResponse(response => routePath(response.request()) === '/api/export/flow/optimize' && response.request().method() === 'POST');
    await submit().click(); const measuredResponse = await measuredReply; expect(measuredResponse.status()).toBe(200);
    const measured = await measuredResponse.json(); jobs.push(measured.job_id); expect(measured.job_id).not.toBe(first.job_id);
    await expect(optimization().getByRole('status').first()).toContainText('completed', {timeout: 160_000});
    const completed = await api(`/api/export/flow/optimization-jobs/${measured.job_id}`);
    expect(completed.status).toBe('completed'); expect(completed.result.quality_approved).toBe(false);
    expect(completed.options.package_dir).toBe(fixture.package.package_path);
    expect(completed.options.input_receipt.source_dataset_path).toBe(fixture.source);
    expect(completed.result.heldout_flow_count).toBe(2); expect(completed.result.heldout_flow_passed_count).toBe(2);
    const candidateFiles = hashes(completed.result.package_path);
    expect(sha(path.join(completed.result.package_path, 'manifest.json'))).toBe(completed.result.candidate_manifest_sha256);
    const heldout = await Promise.all([0, 1].map(index => api(`/api/export/flow/optimization-jobs/${measured.job_id}/heldout-results/${index}`)));
    for (let index = 0; index < heldout.length; index++) {
      expect(heldout[index].image_sha256).toBe(sha(fixture.heldout[index])); expect(heldout[index].comparison.status).toBe('passed');
    }
    await expect(optimization().getByLabel('선택한 최적화 작업', {exact: true})).toContainText(measured.job_id);
    await expect(optimization().getByText('2 / 2 동일 판정·공간 결과', {exact: false})).toBeVisible();
    await optimization().getByRole('button', {name: '다음', exact: true}).click();
    await expect(optimization().getByText('2 / 2 · passed', {exact: false})).toBeVisible();
    await expect(optimization()).toContainText(heldout[1].image_sha256);
    await expect(precision()).toBeVisible();
    const prerequisites = await api(`/api/export/flow/optimization-jobs/${measured.job_id}/approval-prerequisites`);
    dimensions.candidate_handoff = {job_id: measured.job_id, completed, heldout, candidate_files: candidateFiles,
      approval_prerequisites: prerequisites, actual_same_job_precision_view: true, human_quality_approved: false};
    await snapshot('exact-measured-candidate-heldout-review-handoff', precision().getByLabel('검토자', {exact: true}));

    const approvalPath = `/api/export/flow/optimization-jobs/${measured.job_id}/approve`;
    const approvalCount = () => writes.filter(row => row.pathname === approvalPath).length;
    const beforeEmpty = approvalCount(); await expect(approve()).toBeDisabled();
    await precision().getByRole('checkbox').check(); await precision().getByLabel(/^검토 근거/).fill('Explicit synthetic unsent review reason');
    await precision().getByLabel('검토자', {exact: true}).fill(''); await expect(approve()).toBeDisabled(); expect(approvalCount()).toBe(beforeEmpty);
    dimensions.review_empty = {blank_reviewer: true, approval_disabled: true, approval_POSTs: 0, candidate_manifest_sha256: completed.result.candidate_manifest_sha256};
    await snapshot('blank-reviewer-no-approval', approve());
    await fillPrecision(); await precision().getByLabel('최대 절대 오차', {exact: true}).fill('-0.001');
    await expect(approve()).toBeDisabled(); expect(approvalCount()).toBe(beforeEmpty);
    await expect(precision().getByLabel('최대 절대 오차', {exact: true})).toHaveValue('-0.001');
    expect(hashes(completed.result.package_path)).toEqual(candidateFiles);
    dimensions.review_invalid = {actual_numeric_input: -0.001, negative_drift_disabled: true,
      extra_approval_POST: 0, backend_validation_or_transport_error_claimed: false, approval_package_published: false, candidate_unchanged: true};
    await snapshot('negative-drift-disabled-no-approval', approve());

    await fillPrecision(); let errorPayload: any, errorRequests = 0;
    const refuseApproval = async (route: Route) => {
      expect(route.request().method()).toBe('POST'); errorPayload = route.request().postDataJSON(); errorRequests++;
      await route.fulfill({status: 503, json: {detail: 'Controlled exact IR approval transport refusal'}});
    };
    await page.route('**' + approvalPath, refuseApproval);
    try {
      const failedReply = page.waitForResponse(response => routePath(response.request()) === approvalPath && response.request().method() === 'POST');
      await approve().click(); expect((await failedReply).status()).toBe(503); expect(errorRequests).toBe(1);
      expect(errorPayload).toEqual({reviewer: 'Synthetic IR control authority', reason: 'Two actual CPU/IR heldout controls; no human truth or manufacturing quality acceptance',
        holdout_reviewed: true, maximum_absolute_drift: 0.001, approval_revision_ids: prerequisites.approval_revision_ids});
      await expect(optimization().getByRole('alert')).toContainText('Controlled exact IR approval transport refusal');
      await expect(approve()).toBeEnabled(); await snapshot('exact-approval-controlled-503', optimization().getByRole('alert'));
    } finally { await page.unroute('**' + approvalPath, refuseApproval); }
    expect((await api(`/api/export/flow/optimization-jobs/${measured.job_id}`))).toEqual(completed);
    expect(hashes(completed.result.package_path)).toEqual(candidateFiles);
    expect(fs.readdirSync(path.join(fixture.project.project_dir, 'exports/flows')).filter(name => name.startsWith('approved_runtime_'))).toEqual([]);
    dimensions.review_error = {controlled_status: 503, exact_path: approvalPath, payload: errorPayload, backend_dispatch: false,
      unchanged_original_job: true, unchanged_candidate: true, approval_publication: false};

    const beforeClose = approvalCount(); await closeDelivery(); expect(approvalCount()).toBe(beforeClose);
    await openDelivery(); await optimizeOriginal();
    await expect(optimization().getByRole('status').first()).toContainText('completed');
    await expect(optimization().getByLabel('선택한 최적화 작업', {exact: true})).toContainText(measured.job_id);
    await expect(precision().getByLabel('검토자', {exact: true})).toHaveValue('');
    await expect(precision().getByLabel(/^검토 근거/)).toHaveValue('');
    await expect(precision().getByRole('checkbox')).not.toBeChecked(); await expect(approve()).toBeDisabled();
    expect(approvalCount()).toBe(beforeClose); expect(hashes(completed.result.package_path)).toEqual(candidateFiles);
    const draftScope = {completed_unsent_draft_closed: true, actual_delivery_close: true, extra_approval_POST: 0,
      actual_same_package_optimization_reselected: true, reopened_job_id: measured.job_id, reviewer_reason_checkbox_reset: true,
      candidate_files_unchanged: true, inflight_request_cancellation: false};
    dimensions.review_cancel = draftScope; dimensions.review_reopen = draftScope;
    await snapshot('same-job-reopen-clears-unsent-review', approve());

    await fillPrecision();
    const approvalReply = page.waitForResponse(response => routePath(response.request()) === approvalPath && response.request().method() === 'POST');
    const previewReply = page.waitForResponse(response => routePath(response.request()).endsWith('/runtime-preview') && response.request().method() === 'POST');
    await approve().click(); const approvalResponse = await approvalReply; expect(approvalResponse.status()).toBe(200);
    const approvedResult = await approvalResponse.json(); const previewResponse = await previewReply; expect(previewResponse.status()).toBe(200);
    const preview = await previewResponse.json(); expect(preview.device_accepted).toBe(false);
    expect(preview.base_revision_id).toBe(fixture.whole_flow_revision); expect(preview.metrics.normal_count).toBe(1); expect(preview.metrics.defect_count).toBe(1);
    const review = optimization().getByRole('group', {name: '변환 후 전체 흐름 검토', exact: true});
    await expect(review.getByText('정상 1 · 불량 1', {exact: false})).toBeVisible({timeout: 40_000});
    await expect(review.getByRole('button', {name: '변환 전체 흐름 검토 저장', exact: true})).toBeDisabled();
    await review.getByLabel('변환 전체 흐름 검토자', {exact: true}).fill('Synthetic IR control authority');
    await review.getByLabel('변환 전체 흐름 검토 이유', {exact: true}).fill('Actual frozen synthetic OK/NG control; not human truth or manufacturing quality acceptance');
    await review.getByLabel('변환 전체 흐름 직접 검토', {exact: true}).check();
    const runtimeReviewReply = page.waitForResponse(response => routePath(response.request()).endsWith('/runtime-review') && response.request().method() === 'POST');
    await review.getByRole('button', {name: '변환 전체 흐름 검토 저장', exact: true}).click();
    const runtimeReviewResponse = await runtimeReviewReply; expect(runtimeReviewResponse.status()).toBe(200);
    const runtimeReview = await runtimeReviewResponse.json(); expect(runtimeReview.device_accepted).toBe(false);
    await expect(review.getByRole('status')).toContainText('변환 후 전체 흐름 검토를 저장했습니다.');
    await library().getByRole('button', {name: '새로고침', exact: true}).click();
    const inventory = await api('/api/product-delivery/packages');
    const saved = inventory.packages.find((row: any) => row.package_path === approvedResult.package_path);
    expect(saved).toBeTruthy(); expect(saved.integrity).toBe('verified'); expect(saved.scope_matches).toBe(true);
    expect(saved.approval_present).toBe(true); expect(saved.runtime.device).toBe('openvino:CPU');
    const approvedFiles = hashes(saved.package_path), policyHash = sha(approvedResult.release_policy_path);
    const currentReview = await api(`/api/flow-evaluations/approvals/${fixture.whole_flow_revision}/runtime-preview`, {package_path: saved.package_path, device: 'openvino:CPU'});
    expect(currentReview.review_valid).toBe(true); expect(currentReview.review_revision_id).toBe(runtimeReview.revision_id);
    expect(currentReview.manifest_sha256).toBe(saved.manifest_sha256);

    await closeDelivery(); await openProject(page, emptyProject.project_dir); await openDelivery();
    const emptyInventory = await api('/api/product-delivery/packages'); expect(emptyInventory.packages).toEqual([]);
    await library().getByRole('button', {name: '새로고침', exact: true}).click();
    await expect(library()).toContainText('전체 플로우를 내보내면 이곳에 보관됩니다.');
    await expect(library().getByRole('article')).toHaveCount(0); await expect(library().getByRole('button', {name: '다시 열기', exact: true})).toHaveCount(0);
    dimensions.library_empty = {actual_empty_project: emptyProject, real_inventory: emptyInventory, reopen_control_absent: true};
    await snapshot('real-empty-library-no-reopen', library().getByRole('button', {name: '새로고침', exact: true}));
    await closeDelivery(); await openProject(page, fixture.project.project_dir); await openDelivery();
    const actualInventory = await api('/api/product-delivery/packages');
    const invalidInventory = {...actualInventory, packages: actualInventory.packages.map((row: any) => row.package_id === saved.package_id
      ? {...row, integrity: 'failed', scope_matches: false, error: 'Controlled actual IR row integrity/scope metadata refusal'} : row)};
    const selectCount = () => writes.filter(row => row.pathname.startsWith('/api/product-delivery/packages/') && row.pathname.endsWith('/select')).length;
    const beforeInvalid = selectCount();
    const metadataFault = async (route: Route) => { expect(route.request().method()).toBe('GET'); await route.fulfill({status: 200, json: invalidInventory}); };
    await page.route('**/api/product-delivery/packages', metadataFault);
    try {
      await library().getByRole('button', {name: '새로고침', exact: true}).click();
      const row = library().getByRole('article').filter({hasText: saved.name});
      await expect(row).toContainText('무결성 실패'); await expect(row).toContainText('소스 연결 확인 필요');
      for (const button of await row.getByRole('button').all()) await expect(button).toBeDisabled();
      expect(selectCount()).toBe(beforeInvalid); await snapshot('controlled-ir-row-metadata-disables-reopen', row.getByRole('button').first());
    } finally { await page.unroute('**/api/product-delivery/packages', metadataFault); }
    dimensions.library_invalid = {package_id: saved.package_id, original_manifest_sha256: saved.manifest_sha256,
      controlled_metadata_integrity: 'failed', controlled_scope_matches: false, all_row_actions_disabled: true, extra_select_POST: 0,
      actual_backend_integrity_corruption_or_target_failure: false};
    await library().getByRole('button', {name: '새로고침', exact: true}).click();
    await expect(library().getByRole('article').filter({hasText: saved.name})).toContainText('무결성 확인');
    const libraryFailure = async (route: Route) => { expect(route.request().method()).toBe('GET'); await route.fulfill({status: 503, json: {detail: 'Controlled IR library refresh transport refusal'}}); };
    await page.route('**/api/product-delivery/packages', libraryFailure);
    try {
      const failedReply = page.waitForResponse(response => routePath(response.request()) === '/api/product-delivery/packages' && response.request().method() === 'GET');
      await library().getByRole('button', {name: '새로고침', exact: true}).click(); expect((await failedReply).status()).toBe(503);
      await expect(library().getByRole('alert')).toContainText('Controlled IR library refresh transport refusal');
      expect(selectCount()).toBe(beforeInvalid); await snapshot('exact-library-refresh-controlled-503', library().getByRole('alert'));
    } finally { await page.unroute('**/api/product-delivery/packages', libraryFailure); }
    dimensions.library_error = {controlled_status: 503, exact_GET: '/api/product-delivery/packages', backend_dispatch: false, extra_select_POST: 0};
    await library().getByRole('button', {name: '새로고침', exact: true}).click(); await expect(library().getByRole('alert')).toHaveCount(0);

    // Native loopback auth belongs to the original renderer. Snapshot the real
    // authenticated inventory first; the later held UI200 is controlled and is
    // not presented as a delayed original authenticated backend response.
    const snapshotInventory = await api('/api/product-delivery/packages');
    const gate = new Promise<void>(resolve => { releaseHeld = resolve; });
    let heldRequest: Request | undefined;
    let disposition: any, handlerDisposition: any;
    const responseObserved = async (response: import('@playwright/test').Response) => {
      if (response.request() !== heldRequest) return;
      disposition = {kind: 'response', status: response.status(), finished: await response.finished()};
    };
    const failureObserved = (request: Request) => { if (request === heldRequest) disposition = {kind: 'aborted', failure: request.failure()}; };
    // Observe before closing the view or switching context. A genuine abort
    // can occur during that transition, before the fixture releases its gate.
    page.on('response', responseObserved); page.on('requestfailed', failureObserved);
    removeHeldObservers = () => { page.off('response', responseObserved); page.off('requestfailed', failureObserved); };
    const heldSeen = new Promise<void>(resolve => {
      heldHandler = async (route: Route) => {
        if (heldRequest) { await route.continue(); return; }
        expect(route.request().method()).toBe('GET'); heldRequest = route.request(); resolve(); await gate;
        if (disposition?.kind === 'aborted') { handlerDisposition = {kind: 'already_aborted_not_fulfilled'}; return; }
        try { await route.fulfill({status: 200, json: snapshotInventory}); handlerDisposition = {kind: 'controlled_fulfilled'}; }
        catch (cause) { handlerDisposition = {kind: 'fulfill_refused', error: String(cause)}; }
      };
    });
    await page.route('**/api/product-delivery/packages', heldHandler!);
    await library().getByRole('button', {name: '새로고침', exact: true}).click(); await heldSeen;
    await closeDelivery(); await openProject(page, emptyProject.project_dir);
    releaseHeld(); await expect.poll(() => disposition, {timeout: 20_000}).toBeTruthy();
    await expect.poll(() => handlerDisposition, {timeout: 20_000}).toBeTruthy();
    if (disposition.kind === 'response') {
      expect(disposition.status).toBe(200); expect(disposition.finished).toBeNull(); expect(handlerDisposition.kind).toBe('controlled_fulfilled');
    } else {
      expect(disposition.failure?.errorText).toMatch(/ABORT|cancel/i);
      expect(['already_aborted_not_fulfilled', 'fulfill_refused']).toContain(handlerDisposition.kind);
    }
    // Unroute only after the original callback has settled. Subsequent empty
    // scope reads are real backend requests, never the captured old payload.
    await page.unroute('**/api/product-delivery/packages', heldHandler!); heldHandler = undefined; removeHeldObservers();
    await openDelivery(); await expect(library().getByRole('article')).toHaveCount(0);
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(library()).not.toContainText(saved.name); await expect(library().getByRole('group', {name: '변환 후 전체 흐름 검토', exact: true})).toHaveCount(0);
    expect(selectCount()).toBe(beforeInvalid);
    dimensions.library_cancel = {old_request_url: heldRequest!.url(), disposition, handler_disposition: handlerDisposition, real_authenticated_snapshot_package_id: saved.package_id,
      old_ui_response_controlled: true, original_delayed_HTTP_provenance_verified: false, actual_view_close_and_empty_project_switch: true,
      zero_stale_ir_row_or_review_repaint: true, extra_select_POST: 0};
    await snapshot('closed-old-refresh-empty-scope-no-stale-ir', library().getByRole('button', {name: '새로고침', exact: true}));
    await closeDelivery(); await openProject(page, fixture.project.project_dir); await openDelivery();
    const approvedRow = library().getByRole('article').filter({hasText: saved.name}); await expect(approvedRow).toContainText('무결성 확인');
    const selectionPath = `/api/product-delivery/packages/${saved.package_id}/select`;
    const selectionReply = page.waitForResponse(response => routePath(response.request()) === selectionPath && response.request().method() === 'POST');
    const prepare = approvedRow.getByRole('button', {name: '배포 준비', exact: true}); await prepare.click();
    const selectionResponse = await selectionReply; expect(selectionResponse.status()).toBe(200);
    const selected = await selectionResponse.json(); await expect(prepare).toBeEnabled();
    expect(selected.package_id).toBe(saved.package_id); expect(selected.package_path).toBe(saved.package_path);
    expect(selected.manifest_sha256).toBe(saved.manifest_sha256); expect(selected.runtime.device).toBe('openvino:CPU');
    const service = page.getByRole('region', {name: '검사 서비스 배포', exact: true});
    await expect(service.locator(':scope > div').getByLabel('배포할 저장 패키지', {exact: true})).toHaveValue(saved.package_id);
    await expect(service.getByLabel('승인 패키지 경로', {exact: true})).toHaveValue(saved.package_path);
    await expect(service.getByLabel('서비스 전체 흐름 검토 revision', {exact: true})).toHaveValue(fixture.whole_flow_revision);
    await expect(service.getByRole('button', {name: '승인 패키지 적용', exact: true})).toBeDisabled();
    dimensions.library_handoff = {select_path: selectionPath, actual_select_response: selected, displayed_package_id: saved.package_id,
      displayed_package_path: saved.package_path, displayed_base_flow_revision: fixture.whole_flow_revision,
      independently_saved_runtime_review: currentReview, service_device_or_manifest_display_handoff_claimed: false, applied: false};
    await snapshot('approved-ir-selection-handoff-no-apply', service.locator(':scope > div').getByLabel('배포할 저장 패키지', {exact: true}));

    expect(hashes(fixture.package.package_path)).toEqual(fixture.original_package_files);
    expect(hashes(fixture.source)).toEqual(fixture.images); expect(hashes(fixture.project.models_dir)).toEqual(fixture.model_files);
    expect(hashes(completed.result.package_path)).toEqual(candidateFiles); expect(hashes(saved.package_path)).toEqual(approvedFiles);
    expect(sha(approvedResult.release_policy_path)).toBe(policyHash);
    expect(writes.filter(row => row.pathname.startsWith('/api/runtime-services'))).toEqual([]);
    expect(approvalCount()).toBe(2); // Controlled503, then explicit actual synthetic approval200.
    expect(writes.filter(row => row.pathname.endsWith('/runtime-review'))).toHaveLength(1);
    expect(Object.keys(dimensions)).toHaveLength(12);
    evidence.note('actual_ir_review_controls', {feature: 'F099', actions: {
      'actual-ir-independent-candidate': ['cancel', 'handoff'], 'actual-ir-explicit-runtime-review': ['empty', 'invalid', 'error', 'cancel', 'reopen'],
      'actual-ir-library-review-reopen': ['empty', 'invalid', 'error', 'cancel', 'handoff']}, dimensions, fixture, capabilities,
      approved_result: approvedResult, saved_package: saved, saved_runtime_review: currentReview, original_package_unchanged: true,
      images_models_candidate_approved_package_and_policy_unchanged: true, observed_writes: writes,
      actual_CPU_to_IR_conversion: true, actual_two_image_spatial_and_verdict_parity: true, synthetic_all_OK_model_misses_NG: true,
      synthetic_reviews_are_human_truth: false, human_quality_approved: false, actual_managed_service_inference: false,
      installed_service_or_publisher_acceptance: false, frozen_packaged_native_acceptance: false, whole_process_tree_verified: false,
      GPU_used: false, Windows_qualified: false});
  } finally {
    releaseHeld(); removeHeldObservers(); if (heldHandler) await page.unroute('**/api/product-delivery/packages', heldHandler);
    await api('/api/project/open', {project_dir: fixture.project.project_dir});
    // Only exact job IDs returned by this fixture's original create commands.
    for (const id of jobs) {
      const row = await api(`/api/export/flow/optimization-jobs/${id}`);
      if (['queued', 'running', 'stopping'].includes(row.status)) await api(`/api/export/flow/optimization-jobs/${id}/cancel`, undefined, 'POST');
      let terminal: any;
      await expect.poll(async () => { terminal = await api(`/api/export/flow/optimization-jobs/${id}`); return terminal.status; }, {timeout: 40_000}).toMatch(/^(completed|cancelled|failed|interrupted)$/);
      cleanupJobs.push({job_id: id, status: terminal.status});
    }
    evidence.note('owned_exact_job_cleanup', cleanupJobs); page.off('request', observe);
  }
});
