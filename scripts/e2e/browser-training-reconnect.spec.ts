import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {BrowserContext, Page, Response} from '@playwright/test';
import {test, expect, type LoopbackGuard} from './fixtures/test';
import {browserTeamServer} from './fixtures/browser_team_server';
import {expectBrowserLoginGate,openBrowserServerManagement} from './fixtures/browserGateLogin';
import {storedZip} from './fixtures/storedZip';
import {png} from './qa/appFlow';

test.use({ignoreHTTPSErrors: true, actionTimeout: 15_000});
test.describe.configure({retries: 0});

const sha = (value: Buffer) => crypto.createHash('sha256').update(value).digest('hex');
const fileSha = (file: string) => sha(fs.readFileSync(file));
const readJson = (file: string) => JSON.parse(fs.readFileSync(file, 'utf8'));
const stage = (page: Page, index: number) => page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(index).click();
const wire = (page: Page, route: string, method: string) => page.waitForResponse(response =>
  new URL(response.url()).pathname === route && response.request().method() === method);
async function checked(response: Response) {
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}
async function expand(page: Page, text: string) {
  const summary = page.locator('summary').filter({hasText: text});
  const details = summary.locator('..');
  if (await details.getAttribute('open') === null) await summary.click();
}
async function login(page: Page, origin: string, projectId: string, projectName: string, password: string) {
  await expectBrowserLoginGate(page,origin);
  // A reload loses the in-memory CSRF material: a real login restores write authority.
  await expect(page.getByLabel('공유 계정 이름', {exact: true})).toBeVisible();
  await page.getByLabel('공유 계정 이름', {exact: true}).fill('roundtripowner');
  await page.getByLabel('공유 계정 비밀번호', {exact: true}).fill(password);
  const accepted = wire(page, '/api/accounts/login', 'POST');
  await page.getByRole('button', {name: '연결·로그인', exact: true}).click();
  await checked(await accepted);
  await openBrowserServerManagement(page);
  await expect(page.getByLabel('공동 작업 프로젝트', {exact: true})).toHaveValue(projectId);
  await expect(page.getByLabel('공동 작업 프로젝트', {exact: true})).toBeEnabled();
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(projectName);
  await page.getByRole('dialog', {name: '컴퓨팅 서버 관리', exact: true}).getByRole('button', {name: '닫기', exact: true}).click();
}
async function guardContext(context: BrowserContext, guard: LoopbackGuard) {
  // This only fences foreign loopback services. Every admitted request reaches the real server.
  const port = (url: string) => Number(new URL(url).port || (/^(https|wss):/i.test(url) ? 443 : 80));
  const loopback = /^(https?|wss?):\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?\//i;
  await context.route(loopback, route => {
    if (guard.isAllowed(port(route.request().url()))) return route.continue();
    guard.recordBlocked(route.request().url());
    return route.abort('blockedbyclient');
  });
  await context.routeWebSocket(loopback, socket => {
    if (guard.isAllowed(port(socket.url()))) socket.connectToServer();
    else {guard.recordBlocked(socket.url()); socket.close();}
  });
  await context.addInitScript(() => localStorage.setItem('modu:view-mode:v1', 'full'));
}

test('HTTPS ZIP classification trains a real ResNet18 and restores its original saved job after relogin and backend restart',
  {tag: '@owned-model'}, async ({page, browser, rendererBuild, workspace, evidence, loopbackGuard}) => {
  test.setTimeout(360_000);
  const suppliedWeights = process.env.MV_E2E_RESNET18_WEIGHTS;
  expect(suppliedWeights, 'Provide the existing official resnet18-f37072fd.pth; this test never downloads or fabricates weights').toBeTruthy();
  const weightsBytes = fs.readFileSync(path.resolve(suppliedWeights!));
  const weightsSha = sha(weightsBytes);
  expect(weightsSha.startsWith('f37072fd'), 'Official torchvision ResNet18 checkpoint SHA256 prefix').toBe(true);
  expect(weightsBytes.length).toBeGreaterThan(1_000_000);
  const torchHome = path.join(workspace.home, '.cache', 'torch');
  const cachedWeights = path.join(torchHome, 'hub', 'checkpoints', 'resnet18-f37072fd.pth');
  fs.mkdirSync(path.dirname(cachedWeights), {recursive: true});
  fs.writeFileSync(cachedWeights, weightsBytes);
  expect(fileSha(cachedWeights)).toBe(weightsSha);
  // torchvision and readiness must both use this owned cache, including across restart.
  const previousTorchHome = process.env.TORCH_HOME;
  process.env.TORCH_HOME = torchHome;
  const password = 'owned synthetic browser training password 123';
  evidence.redact(password);
  let freshContext: BrowserContext | undefined;
  let server: Awaited<ReturnType<typeof browserTeamServer>> | undefined;
  const logs: string[] = [];
  const requests: Array<{method: string; path: string; job_id: string | null}> = [];
  const observe = (target: Page) => target.on('request', request => {
    const url = new URL(request.url());
    if (['/api/training/start', '/api/evaluation/results', '/api/evaluation/reevaluate', '/api/model-execution/recipes'].includes(url.pathname))
      requests.push({method: request.method(), path: url.pathname, job_id: url.searchParams.get('job_id')});
  });
  try {
    const entries: Array<[string, Buffer]> = [];
    const pixels: Array<{relative_path: string; sha256: string}> = [];
    for (const [classIndex, label] of ['OK', 'NG'].entries()) for (let index = 0; index < 8; index++) {
      const relative = `${label}/${label.toLowerCase()}_${index}.png`;
      const bytes = png(64, 3, (x, y) => {
        const base = 120 + ((x * 3 + y * 5 + index * 11 + classIndex * 7) % 80);
        return label === 'NG' && x >= 27 + index % 3 && x <= 31 + index % 3 && y > 8 && y < 56
          ? [20 + index, 35, 45] : [base, 125 + (x + index * 7) % 60, 130 + (y + index * 9) % 60];
      });
      entries.push([relative, bytes]);
      pixels.push({relative_path: relative, sha256: sha(bytes)});
    }
    expect(new Set(pixels.map(row => row.sha256)).size).toBe(16);
    const archive = path.join(workspace.root, 'browser-classification.zip');
    fs.writeFileSync(archive, storedZip(entries));
    server = await browserTeamServer(workspace, rendererBuild.outDir);
    const owned = server;
    loopbackGuard.allow(owned.port);
    evidence.redact(owned.backend.token);
    logs.push(owned.backend.logs.stdout, owned.backend.logs.stderr);
    await owned.call('/api/accounts/bootstrap', {username: 'roundtripowner', password});
    let account = await owned.call('/api/accounts/login', {username: 'roundtripowner', password});
    evidence.redact(account.token);
    const project = await owned.call('/api/project/create', {name: 'Owned browser classification roundtrip', task: 'classification'}, account.token);
    const api = (route: string) => owned.call(route, undefined, account.token);
    observe(page);
    await page.goto(owned.origin);
    await login(page, owned.origin, project.id, project.name, password);
    await stage(page, 0);
    await page.getByRole('button', {name: '검증된 데이터 버전', exact: true}).click();
    const intake = page.getByRole('dialog', {name: '검증된 데이터 버전', exact: true});
    await intake.getByLabel('가져올 ZIP 파일').setInputFiles(archive);
    await intake.getByRole('button', {name: 'ZIP 올리고 검증', exact: true}).click();
    await expect(intake.getByRole('region', {name: '현재 가져오기'})).toContainText('검증 완료 · 채택 전', {timeout: 30_000});
    await intake.getByRole('button', {name: '이 버전 채택', exact: true}).click();
    const accepted = page.waitForResponse(response => /\/api\/dataset\/imports\/[^/]+\/accept$/.test(new URL(response.url()).pathname)
      && response.request().method() === 'POST');
    await intake.getByRole('button', {name: '채택 확인', exact: true}).click();
    const adoption = await checked(await accepted);
    const imported = await api(`/api/dataset/imports/${adoption.job_id}`);
    expect((await api('/api/project/current')).source_dataset_dir).toBe(project.source_dataset_dir);
    await intake.getByRole('button', {name: '이 ZIP으로 작업 시작', exact: true}).click();
    await expect.poll(async () => (await api('/api/project/current')).source_dataset_dir).toBe(imported.source.root);
    await expect(intake).toBeHidden();
    const current = await api('/api/project/current');
    const source: string = current.source_dataset_dir;
    expect(path.relative(workspace.root, source)).not.toMatch(/^\.\./);
    const thumbnails = page.locator('button[aria-label$="라벨링에서 열기"] img');
    await expect(thumbnails).toHaveCount(16);
    await expect.poll(() => thumbnails.evaluateAll(images => images.every(image =>
      (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    const imagesRoute = `/api/dataset/images?${new URLSearchParams({folder_path: source, task: 'classification', limit: '100'})}`;
    const images = await api(imagesRoute);
    expect(images.total).toBe(16);
    for (const pixel of pixels) expect(fileSha(path.join(source, pixel.relative_path))).toBe(pixel.sha256);
    const selected = images.items.find((image: {label: string}) => image.label === 'OK');
    expect(selected).toBeTruthy();

    const editAndRestore = async (target: Page, phase: string) => {
      await stage(target, 1);
      await target.locator('[data-labeling-filmstrip]').getByAltText(selected.file_name, {exact: true}).click();
      const picker = target.getByRole('region', {name: '이미지 클래스', exact: true});
      await expect(picker.getByRole('button', {name: 'OK', exact: true})).toHaveAttribute('aria-pressed', 'true');
      const changed = wire(target, '/api/dataset/metadata/class-override', 'PUT');
      await picker.getByRole('button', {name: 'NG', exact: true}).click();
      const changedResponse = await changed;
      expect(changedResponse.request().postDataJSON()).toMatchObject({image_path: selected.file_path, class_name: 'NG'});
      const saved = await checked(changedResponse);
      expect(saved).toMatchObject({class: 'NG', class_source: 'override', changed: true});
      expect((await api(imagesRoute)).items.find((image: {file_path: string}) => image.file_path === selected.file_path).label).toBe('NG');
      const metadata = await api(`/api/dataset/metadata/image?${new URLSearchParams({image_path: selected.file_path})}`);
      expect(metadata.revision).toBe(saved.metadata.revision);
      const restored = wire(target, '/api/dataset/metadata/class-override', 'PUT');
      await picker.getByRole('button', {name: 'OK', exact: true}).click();
      const restoredResult = await checked(await restored);
      expect(restoredResult).toMatchObject({class: 'OK', class_source: 'folder', changed: true});
      await expect(picker.getByRole('button', {name: 'OK', exact: true})).toHaveAttribute('aria-pressed', 'true');
      evidence.note(phase, {image: selected.file_path, saved, metadata, restored: restoredResult, actual_ui_put: true});
    };
    await editAndRestore(page, 'class_save_before_training');
    await stage(page, 0);
    await page.getByRole('button', {name: '60 / 20 / 20', exact: true}).click();
    const splitResponse = wire(page, '/api/dataset/split', 'POST');
    await page.getByRole('button', {name: '이 비율로 나누기', exact: true}).click();
    const split = await checked(await splitResponse);
    expect(split.split).toEqual({train: 10, val: 4, test: 2});
    const splitFile = path.join(current.dataset_dir, 'splits', sha(Buffer.from(source)) + '.json');
    const splitSha = fileSha(splitFile);
    const assignments = readJson(splitFile).assignments as Record<string, string>;
    expect(Object.keys(assignments)).toHaveLength(16);
    type SavedImage = {file_path: string; label: string; split: string};
    const imageSnapshot = (rows: SavedImage[]): Array<[string, string, string]> =>
      rows.map((row): [string, string, string] => [row.file_path, row.label, row.split]).sort();
    const savedImages = imageSnapshot((await api(imagesRoute)).items);
    const heldout = Object.entries(assignments).filter(([, partition]) => partition === 'test').map(([relative]) => path.join(source, relative)).sort();
    expect(heldout).toHaveLength(2);
    await stage(page, 2);
    const family = page.getByRole('region', {name: '모델 학습 허브'}).getByRole('button', {name: /이미지 분류/});
    if (await family.getAttribute('aria-pressed') !== 'true') await family.click();
    await page.getByRole('combobox', {name: '학습 모델 구조', exact: true}).selectOption('resnet18');
    await page.getByLabel('수동 학습 횟수', {exact: true}).fill('1');
    await page.getByLabel('수동 학습 입력 크기', {exact: true}).fill('64');
    await expand(page, '다음 학습 배치·로컬 장치 설정');
    await page.getByLabel('다음 학습 배치 크기', {exact: true}).fill('2');
    await page.getByLabel('다음 학습 로컬 장치', {exact: true}).selectOption('cpu');
    const readinessResponse = wire(page, '/api/training-workspace/readiness', 'POST');
    await page.getByRole('button', {name: '로컬 준비 검사', exact: true}).click();
    const readiness = await checked(await readinessResponse);
    expect(readiness).toMatchObject({ready: true, model: 'resnet18', runtime: {device: 'cpu'}, weights: {sha256: weightsSha, origin: 'cached_file'}});
    await expand(page, '학습 실행 예산·대기열');
    await page.getByLabel('학습 시간 제한 (분)', {exact: true}).fill('1');
    const started = wire(page, '/api/training/start', 'POST');
    await page.getByRole('button', {name: '선택 설정으로 학습 시작', exact: true}).click();
    const startedResponse = await started;
    const posted = startedResponse.request().postDataJSON();
    expect(posted).toMatchObject({task: 'classification', device: 'cpu', dataset_path: source, max_runtime_s: 60,
      config_overrides: {backbone: 'resnet18', epochs: 1, image_size: 64, batch_size: 2}});
    expect(posted.compute_profile_id).toBeUndefined();
    const training = await checked(startedResponse);
    const jobId: string = training.job_id;
    let terminal: any;
    await expect.poll(async () => {
      terminal = await api('/api/training/status?job_id=' + encodeURIComponent(jobId));
      if (['failed', 'cancelled', 'aborted', 'stopped'].includes(terminal.status)) throw new Error(JSON.stringify(terminal));
      return terminal.status;
    }, {timeout: 95_000, intervals: [500, 1000, 2000]}).toBe('completed');
    await expect(page.getByRole('region', {name: '학습 로그'})).toContainText('완료');
    const jobDir = path.join(current.models_dir, jobId);
    const checkpoint = path.join(jobDir, 'best_model.pt');
    const metadataFile = path.join(jobDir, 'model_meta.json');
    const receiptFile = path.join(jobDir, 'job_receipt.json');
    await expect.poll(() => fs.existsSync(receiptFile) && readJson(receiptFile).status).toBe('completed');
    const metadata = readJson(metadataFile), receipt = readJson(receiptFile);
    expect(metadata).toMatchObject({task: 'classification', backbone: 'resnet18', device: 'cpu',
      classes: ['OK', 'NG'], image_size: [64, 64], training_config: {epochs: 1, image_size: 64, batch_size: 2}});
    expect(metadata.class_semantics.roles).toMatchObject({OK: 'normal', NG: 'defect'});
    expect(receipt).toMatchObject({job_id: jobId, status: 'completed', task: 'classification'});
    expect(fs.statSync(checkpoint).size).toBeGreaterThan(1_000_000);
    await evidence.screenshot(page, 'browser-real-cpu-resnet18-completed');

    const healthBefore = await owned.call('/health');
    const evaluatedResponse = wire(page, '/api/evaluation/results', 'GET');
    await stage(page, 3);
    const evaluated = await checked(await evaluatedResponse);
    expect(evaluated.job_id).toBe(jobId);
    expect(evaluated.metrics.evaluated_split).toBe('test');
    expect(evaluated.metrics.selection_overlap).toBe(false);
    expect(evaluated.test_predictions).toHaveLength(heldout.length);
    expect(evaluated.test_predictions.map((row: {file_path: string}) => row.file_path).sort()).toEqual(heldout);
    expect(evaluated.confusion_matrix.matrix).toHaveLength(2);
    const counts = evaluated.confusion_matrix.matrix.flat() as number[];
    expect(counts.every(value => Number.isFinite(value) && value >= 0)).toBe(true);
    expect(counts.reduce((sum, value) => sum + value, 0)).toBe(heldout.length);
    for (const value of Object.values(evaluated.metrics)) if (typeof value === 'number') expect(Number.isFinite(value)).toBe(true);
    await expect(page.getByText('평가 데이터: 시험 분할', {exact: true})).toBeVisible();
    const healthAfter = await owned.call('/health');
    expect(healthAfter.device).toBe(healthBefore.device);
    const evaluationFile = path.join(jobDir, 'eval_results.json');
    const historyFile = path.join(current.project_dir, 'reports', 'evaluations', evaluated.evaluation_id + '.json');
    const preserved = {checkpoint_sha256: fileSha(checkpoint), metadata_sha256: fileSha(metadataFile),
      receipt_sha256: fileSha(receiptFile), evaluation_sha256: fileSha(evaluationFile), history_sha256: fileSha(historyFile)};
    const historyRoute = '/api/evaluation/history?' + new URLSearchParams({source_dataset_path: source, task: 'classification'});
    const originalHistory = (await api(historyRoute)).items.find((row: {evaluation_id: string}) => row.evaluation_id === evaluated.evaluation_id);
    expect(originalHistory.result.job_id).toBe(jobId);
    expect(originalHistory.evidence_sha256).toMatch(/^[a-f0-9]{64}$/);
    await evidence.screenshot(page, 'browser-real-heldout-evaluation');
    const trainingRequests = () => requests.filter(request => request.path === '/api/training/start').length;
    expect(trainingRequests()).toBe(1);

    const verifyRestored = async (target: Page, name: string) => {
      const restored = await api('/api/project/current');
      expect(restored).toMatchObject({id: project.id, task: 'classification', source_dataset_dir: source,
        active_labelset_id: current.active_labelset_id});
      expect(fileSha(splitFile)).toBe(splitSha);
      const restoredImages = await api(imagesRoute);
      expect(imageSnapshot(restoredImages.items)).toEqual(savedImages);
      expect(restoredImages.items.filter((row: {label: string}) => row.label === 'OK')).toHaveLength(8);
      expect(restoredImages.items.filter((row: {label: string}) => row.label === 'NG')).toHaveLength(8);
      await target.getByRole('button', {name: '작업 센터', exact: true}).click();
      const center = target.getByRole('dialog', {name: '작업 센터', exact: true});
      await center.getByLabel('저장 작업 다시 열기', {exact: true}).selectOption('training:local:' + jobId);
      const reloadedEvaluation = wire(target, '/api/evaluation/results', 'GET');
      await center.getByRole('button', {name: '완료 후보 평가로 이동', exact: true}).click();
      const restoredResult = await checked(await reloadedEvaluation);
      expect(restoredResult.job_id).toBe(jobId);
      expect(restoredResult.evaluation_id).toBe(evaluated.evaluation_id);
      await expand(target, '평가 이력 · 제품/Lot별 오류');
      const historySelection = target.getByLabel('모델별 저장 평가');
      await expect(historySelection.locator('option[value="' + evaluated.evaluation_id + '"]')).toHaveCount(1);
      await historySelection.selectOption(evaluated.evaluation_id);
      await expect(historySelection).toHaveValue(evaluated.evaluation_id);
      const sameHistory = (await api(historyRoute)).items.find((row: {evaluation_id: string}) => row.evaluation_id === evaluated.evaluation_id);
      expect(sameHistory).toEqual(originalHistory);
      expect(fileSha(checkpoint)).toBe(preserved.checkpoint_sha256);
      expect(fileSha(metadataFile)).toBe(preserved.metadata_sha256);
      expect(fileSha(receiptFile)).toBe(preserved.receipt_sha256);
      expect(fileSha(evaluationFile)).toBe(preserved.evaluation_sha256);
      expect(fileSha(historyFile)).toBe(preserved.history_sha256);
      expect(trainingRequests()).toBe(1);
      const jobs = await api('/api/training-workspace/tasks');
      expect(jobs.tasks.filter((row: {kind: string}) => row.kind === 'training').map((row: {job_id: string}) => row.job_id)).toEqual([jobId]);
      await evidence.screenshot(target, name);
    };
    await page.reload();
    await login(page, owned.origin, project.id, project.name, password);
    await verifyRestored(page, 'browser-original-job-after-reload-login');
    const restart = await owned.restart();
    evidence.redact(owned.backend.token);
    logs.push(owned.backend.logs.stdout, owned.backend.logs.stderr);
    expect(restart.current.pid).not.toBe(restart.previous.pid);
    account = await owned.call('/api/accounts/login', {username: 'roundtripowner', password});
    evidence.redact(account.token);
    freshContext = await browser.newContext({ignoreHTTPSErrors: true, viewport: {width: 1440, height: 900}});
    await guardContext(freshContext, loopbackGuard);
    const reconnected = await freshContext.newPage();
    observe(reconnected);
    await reconnected.goto(owned.origin);
    await login(reconnected, owned.origin, project.id, project.name, password);
    await verifyRestored(reconnected, 'browser-original-job-after-backend-restart-new-context');
    // A successful write through the new cookie/CSRF session proves restored authority.
    // Restore the original class; the immutable historical evaluation must remain untouched.
    await editAndRestore(reconnected, 'class_save_after_restart_new_context');
    expect(fileSha(historyFile)).toBe(preserved.history_sha256);
    expect(fileSha(splitFile)).toBe(splitSha);
    expect(trainingRequests()).toBe(1);
    for (const pixel of pixels) expect(fileSha(path.join(source, pixel.relative_path))).toBe(pixel.sha256);
    const storage = await reconnected.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
    for (const secret of [password, account.token, owned.backend.token]) expect(storage).not.toContain(secret);
    const cookies = await freshContext.cookies(owned.origin);
    expect(cookies.find(cookie => cookie.name === 'vision_session')).toMatchObject({httpOnly: true, secure: true, sameSite: 'Strict'});
    evidence.note('browser_training_roundtrip', {project_id: project.id, source, labelset_id: current.active_labelset_id || 'default',
      synthetic_only: true, quality_approved: false, pixels, archive_sha256: fileSha(archive), weights_sha256: weightsSha,
      owned_cached_weights: cachedWeights, adoption, split: split.split, split_sha256: splitSha, posted, job_id: jobId,
      terminal, metadata, receipt, evaluated, original_history: originalHistory, preserved, restart, requests,
      actual_ui_upload: true, actual_ui_class_put: true, actual_ui_split: true, actual_ui_training: true,
      actual_cpu_training: true, actual_backend_evaluation: true, evaluation_device_mode: 'automatic',
      evaluation_auto_device_readback: {before: healthBefore, after: healthAfter},
      evaluation_device_limit: 'Standard evaluation resolves get_device() automatically; training CPU flags do not select its device. Health reads use the same backend resolver.',
      actual_reload_login: true, actual_new_context_login: true, actual_owned_backend_restart: true,
      original_job_reopened: true, immutable_evaluation_history_preserved: true, new_training_jobs_after_reconnect: 0});
    for (const file of [archive, splitFile, checkpoint, metadataFile, receiptFile, evaluationFile, historyFile]) evidence.addFile(file);
  } finally {
    try {
      if (freshContext) await freshContext.close();
    } finally {
      try {
        if (server) evidence.note('browser_training_owned_server_stop', await server.close());
      } finally {
        for (const file of logs) evidence.addFile(file);
        if (previousTorchHome === undefined) delete process.env.TORCH_HOME;
        else process.env.TORCH_HOME = previousTorchHome;
      }
    }
  }
});
