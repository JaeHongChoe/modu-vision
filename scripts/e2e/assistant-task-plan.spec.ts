import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {test, expect} from './fixtures/test';
import {localAssistantBrowser} from './fixtures/localAssistantBrowser';
import {expectBrowserLoginGate,chooseBrowserProject,browserLoginWithin} from './fixtures/browserGateLogin';
import {png} from './qa/appFlow';
import type {AssistantTask, TaskEvent} from '../../src/renderer/services/assistantTasks/types';
import type {AssistantConfigState, AssistantSendInput} from '../../src/types/electron';

const {assertPrivateEvidencePolicy} = require('./fixtures/localAssistantHelper.cjs');
// Authentication, CSRF and helper traffic must never enter a raw trace.
test.use({ignoreHTTPSErrors: true, actionTimeout: 15_000, trace: 'off'});
const sha = (bytes: Buffer) => crypto.createHash('sha256').update(bytes).digest('hex');
const deferred = () => {
  let resolve!: () => void;
  const promise = new Promise<void>(done => {resolve = done;});
  return {promise, resolve};
};

// Public browser UI, real cookie/CSRF/backend/task records and application actions. Only the helper's provider,
// generated login and native folder choice are controlled; no preload/store injection, model or external image exists.
test('task plan approval persists real actions; pause, human resume and reload retain saved evidence without duplicate writes', async ({page, rendererBuild, workspace, evidence, loopbackGuard, trace}) => {
  assertPrivateEvidencePolicy(trace);
  test.setTimeout(300_000);
  const server = await localAssistantBrowser(workspace, rendererBuild.outDir);
  loopbackGuard.allow(server.port);
  evidence.redact(server.backend.token);
  const username = 'taskfixtureowner', password = 'owned task fixture password 123';
  evidence.redact(password);
  const source = path.join(workspace.root, 'task-plan-originals');
  const originals: Array<{path: string; sha256: string}> = [];
  for (const [index, label] of ['OK', 'NG'].entries()) {
    fs.mkdirSync(path.join(source, label), {recursive: true});
    for (let number = 0; number < 10; number++) {
      const file = path.join(source, label, `${label}-${number}.png`);
      fs.writeFileSync(file, png(64, 3, (x, y) => [100 + index * 50 + number, x % 256, y % 256]));
      originals.push({path: file, sha256: sha(fs.readFileSync(file))});
    }
  }
  const pageErrors: string[] = [], foreign: string[] = [], prohibited: string[] = [];
  const splitWrites: unknown[] = [], importWrites: unknown[] = [], summaryRefreshes: unknown[] = [];
  const taskEvents: Array<{id: string; event: TaskEvent}> = [];
  const responseChecks: Promise<void>[] = [];
  const splitEntered = deferred(), releaseSplit = deferred();
  let holdSplit = true;
  const ledger = () => fs.existsSync(server.ledger)
    ? fs.readFileSync(server.ledger, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
  try {
    // All identities and files below belong to this disposable fixture, including its generated local cookie account.
    await server.call('/api/accounts/bootstrap', {username, password});
    const owner = await server.call('/api/accounts/login', {username, password});
    evidence.redact(owner.token);
    const api = (route: string, body?: unknown, method?: string) => server.call(route, body, owner.token, method);
    const project = await api('/api/project/create', {name: 'Task plan controlled project', task: 'classification'});
    await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
    await api('/api/dataset/import', {folder_path: source, task: 'classification'});
    await api('/api/dataset/split', {folder_path: source, task: 'classification', train_ratio: .8, val_ratio: .1, test_ratio: .1});
    const splitFile = path.join(project.dataset_dir, 'splits', crypto.createHash('sha256').update(source).digest('hex') + '.json');
    const initialSplitSha = sha(fs.readFileSync(splitFile));
    const savedCounts = (file = splitFile) => Object.values(JSON.parse(fs.readFileSync(file, 'utf8')).assignments)
      .reduce<Record<string, number>>((counts, name) => {const key = String(name); counts[key] = (counts[key] || 0) + 1; return counts;}, {});
    const readTask = async (id: string): Promise<AssistantTask> => (await api(`/api/assistant-tasks/${id}`)).task;
    const findTask = async (goal: string): Promise<AssistantTask | undefined> => (await api('/api/assistant-tasks')).tasks.find((task: AssistantTask) => task.goal === goal);
    const waitTask = async (id: string, status: AssistantTask['status']) => {
      await expect.poll(async () => (await readTask(id)).status).toBe(status);
      return readTask(id);
    };

    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('request', request => {
      const url = new URL(request.url());
      if (!/^https?:$/.test(url.protocol) || url.origin !== server.origin) return;
      if (request.method() === 'POST' && url.pathname === '/api/dataset/split') splitWrites.push(request.postDataJSON());
      if (request.method() === 'POST' && url.pathname === '/api/dataset/import') {
        const body = request.postDataJSON();
        // currentSummary's compatibility fallback is a validate_images:false metadata read, not the chosen-folder action.
        if (body.validate_images === false) summaryRefreshes.push(body); else importWrites.push(body);
      }
      const event = /^\/api\/assistant-tasks\/(task_[a-f0-9]{32})\/events$/.exec(url.pathname);
      if (event && request.method() === 'POST') taskEvents.push({id: event[1], event: request.postDataJSON()});
      if (request.method() !== 'GET' && /\/api\/(training\/start|label-suggestions\/batches|labeling-jobs|inspections\/runs|flow-evaluation\/)/.test(url.pathname)) prohibited.push(url.pathname);
    });
    page.on('response', response => {
      if (new URL(response.url()).pathname === '/api/accounts/login') responseChecks.push(response.json().then(value => {
        evidence.redact(value.csrf_token);
        expect(value.token).toBeUndefined();
      }));
    });
    await page.context().route(/^https?:\/\//, route => {
      const url = new URL(route.request().url());
      if (url.origin === server.origin) return route.fallback();
      foreign.push(url.origin); return route.abort('blockedbyclient');
    });
    await page.context().routeWebSocket(/^wss?:\/\//, socket => {
      if (new URL(socket.url()).host === new URL(server.origin).host) socket.connectToServer();
      else {foreign.push(new URL(socket.url()).origin); socket.close();}
    });
    await page.route('**/api/dataset/split', async route => {
      if (route.request().method() === 'POST' && holdSplit) {
        holdSplit = false; splitEntered.resolve(); await releaseSplit.promise;
      }
      await route.fallback();
    });

    const login = async () => {
      await expectBrowserLoginGate(page, server.origin);
      await page.getByLabel('공유 계정 이름', {exact: true}).fill(username);
      await page.getByLabel('공유 계정 비밀번호', {exact: true}).fill(password);
      const browserLoginDeadline=performance.now()+20_000;
      await page.getByRole('button', {name: '연결·로그인', exact: true}).click();
      await chooseBrowserProject(page,project.id,'local_host',browserLoginDeadline);
      await browserLoginWithin(()=>expect(page.getByRole('main', {name: '브라우저 계정 로그인', exact: true})).toHaveCount(0),browserLoginDeadline);
    };
    await page.goto(server.origin);
    await login();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await expect(page.getByRole('button', {name: '전체 (20)', exact: true})).toBeVisible();
    const openAssistant = async () => {
      await page.getByRole('button', {name: '왜 그런가요?', exact: true}).click();
      await page.getByRole('button', {name: '도우미에게 묻기', exact: true}).click();
      return page.getByRole('complementary', {name: '도우미', exact: true});
    };
    let assistant = await openAssistant();
    const reconnectAssistant = async () => {
      assistant = await openAssistant();
      const accounts = assistant.getByLabel('ChatGPT 계정', {exact: true});
      await expect(accounts).toBeVisible();
      if (!await accounts.inputValue()) {
        const record = await accounts.locator('option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value).find(Boolean));
        expect(record).toBeTruthy();
        await accounts.selectOption(record!);
      }
      await assistant.getByRole('button', {name: '모델 목록 불러오기', exact: true}).click();
      const restored = page.waitForResponse(response => new URL(response.url()).pathname === '/api/assistant-tasks' && response.request().method() === 'GET');
      await assistant.getByLabel('ChatGPT 모델', {exact: true}).selectOption('fixture-model');
      await restored;
      await expect(assistant.getByRole('textbox', {name: '도우미에게 묻기', exact: true})).toBeEnabled();
    };
    await assistant.getByRole('checkbox', {name: '약관과 전송·보관 안내를 읽고 동의합니다.', exact: true}).check();
    await assistant.getByRole('button', {name: 'Continue with ChatGPT', exact: true}).click();
    await page.getByRole('dialog', {name: 'ChatGPT 플랜 사용 안내', exact: true}).getByRole('button', {name: '안내 확인', exact: true}).click();
    await assistant.getByRole('button', {name: '모델 목록 불러오기', exact: true}).click();
    const initialRestore = page.waitForResponse(response => new URL(response.url()).pathname === '/api/assistant-tasks' && response.request().method() === 'GET');
    await assistant.getByLabel('ChatGPT 모델', {exact: true}).selectOption('fixture-model');
    await initialRestore;
    const ask = async (text: string) => {
      // This scenario preserves the optional manual-plan route; direct work requests are tested separately.
      await assistant.getByRole('checkbox', {name:'설명만 받기 · 작업은 실행하지 않음',exact:true}).check();
      await assistant.getByRole('textbox', {name: '도우미에게 묻기', exact: true}).fill(text);
      await assistant.getByRole('button', {name: '보내기', exact: true}).click();
      await expect(assistant.getByRole('textbox', {name: '도우미에게 묻기', exact: true})).toBeEnabled();
    };
    const card = () => assistant.getByRole('region', {name: '도우미 작업', exact: true});
    await expect(card()).toHaveAttribute('aria-busy', 'false');

    await ask('TASK_PLAN_FIXTURE 합성 자료를 70/20/10으로 나누고 CPU 배치 8로 준비해 주세요.');
    await expect(card().getByRole('button', {name: '계획 확인하고 실행', exact: true})).toBeEnabled();
    await expect(card()).toContainText('학습 70% · 검증 20% · 시험 10%');
    await expect(card()).toContainText('배치 크기: 8');
    const firstDraft = await findTask('합성 자료 분할과 학습 설정 준비');
    expect(firstDraft?.status).toBe('draft');
    expect(firstDraft?.approval).toBeNull();
    expect(splitWrites).toHaveLength(0);
    expect(sha(fs.readFileSync(splitFile))).toBe(initialSplitSha);
    await expect(assistant.locator('button.workspace-button--primary:visible')).toHaveCount(1);
    await expect(assistant.getByRole('button', {name: '보내기', exact: true})).not.toHaveClass(/workspace-button--primary/);
    await evidence.screenshot(page, 'task-plan-review-before-any-action');

    const narrowLayouts: Array<Record<string, unknown>> = [];
    for (const theme of ['light', 'dark']) {
      await assistant.getByRole('button', {name: '닫기', exact: true}).click();
      await page.setViewportSize({width: 1440, height: 900});
      if (await page.locator('html').getAttribute('data-theme') !== theme)
        await page.getByRole('button', {name: theme === 'light' ? '밝은 화면' : '어두운 화면', exact: true}).click();
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      assistant = await openAssistant();
      await page.setViewportSize({width: 390, height: 844});
      await expect(card().getByRole('button', {name: '계획 확인하고 실행', exact: true})).toBeEnabled();
      await expect(assistant.locator('button.workspace-button--primary:visible')).toHaveCount(1);
      const layout = await card().evaluate(element => {
        const box = element.getBoundingClientRect();
        return {left: box.left, right: box.right, clientWidth: element.clientWidth, scrollWidth: element.scrollWidth};
      });
      expect(layout.left).toBeGreaterThanOrEqual(-1);
      expect(layout.right).toBeLessThanOrEqual(391);
      expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth + 1);
      narrowLayouts.push({theme, viewport: {width: 390, height: 844}, ...layout});
      await card().scrollIntoViewIfNeeded();
      await evidence.screenshot(page, `task-plan-390-${theme}`);
    }

    // Pause after the real app dispatched its split request, while the network result is still pending.
    const approve = card().getByRole('button', {name: '계획 확인하고 실행', exact: true});
    await assistant.getByRole('button', {name: '닫기', exact: true}).focus();
    for (let tabs = 0; tabs < 40; tabs++) {
      await page.keyboard.press('Tab');
      if (await approve.evaluate(element => element === document.activeElement)) break;
    }
    await expect(approve).toBeFocused();
    await evidence.screenshot(page, 'task-plan-390-keyboard-approval-focus');
    await page.keyboard.press('Enter');
    await splitEntered.promise;
    await card().getByRole('button', {name: '작업 일시정지', exact: true}).click();
    await waitTask(firstDraft!.id, 'paused');
    releaseSplit.resolve();
    await expect.poll(async () => (await readTask(firstDraft!.id)).steps[0].status).toBe('completed');
    const paused = await readTask(firstDraft!.id);
    expect(paused.status).toBe('paused');
    expect(paused.steps[0].receipt?.split).toEqual({train: 14, val: 4, test: 2});
    expect(paused.steps.slice(1).map(step => step.status)).toEqual(['pending', 'pending']);
    expect(savedCounts()).toEqual({train: 14, val: 4, test: 2});
    expect(splitWrites).toHaveLength(1);
    evidence.note('task_plan_paused_receipt', paused);
    await expect(card().getByRole('button', {name: '저장 결과 확인하고 이어하기', exact: true})).toBeEnabled();
    await evidence.screenshot(page, 'task-paused-retains-committed-split-evidence');
    await card().getByRole('button', {name: '저장 결과 확인하고 이어하기', exact: true}).click();
    const firstCompleted = await waitTask(firstDraft!.id, 'completed');
    expect(firstCompleted.steps.map(step => step.status)).toEqual(['completed', 'completed', 'completed']);
    expect(firstCompleted.steps[1].receipt?.settings).toMatchObject({preset: 'fast', device: 'cpu', batch_size: 8});
    expect(firstCompleted.steps[2].receipt?.step).toBe(3);
    evidence.note('task_plan_completed_before_reload', firstCompleted);
    await expect(card()).toContainText('완료 3/3개');
    await expect(card()).toContainText('실제 배치 크기');
    await page.setViewportSize({width: 1440, height: 900});
    const trainingScreen = page.locator('#workflow-training');
    await expect(trainingScreen).toBeVisible();
    await trainingScreen.getByText('다음 학습 배치·로컬 장치 설정', {exact: true}).click();
    await expect(trainingScreen.getByLabel('다음 학습 배치 크기', {exact: true})).toHaveValue('8');
    await expect(trainingScreen.getByLabel('다음 학습 로컬 장치', {exact: true})).toHaveValue('cpu');
    expect(splitWrites).toHaveLength(1);

    // Reload restores the durable result through the public account/session path and never replays completed actions.
    await page.reload();
    await login();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('navigation', {name: '작업 단계 (Workflow Stages)', exact: true}).getByRole('button', {name: /^01 데이터 준비/}).click();
    await expect(page.getByRole('button', {name: '전체 (20)', exact: true})).toBeVisible();
    await reconnectAssistant();
    await expect(card()).toContainText('합성 자료 분할과 학습 설정 준비');
    await expect(card()).toContainText('완료 3/3개');
    await expect(card().getByRole('button', {name: /^저장 설정 복원/})).toContainText('CPU · 배치 8');
    expect((await readTask(firstDraft!.id)).revision).toBe(firstCompleted.revision);
    expect(splitWrites).toHaveLength(1);

    await ask('TASK_IMPORT_FIXTURE 제가 고른 합성 폴더를 가져오고 60/20/20으로 준비해 주세요.');
    await expect(card().getByRole('button', {name: '계획 확인하고 실행', exact: true})).toBeEnabled();
    const secondDraft = await findTask('합성 자료 가져오기와 분할 준비');
    expect(secondDraft?.status).toBe('draft');
    await card().getByRole('button', {name: '계획 확인하고 실행', exact: true}).click();
    const human = await waitTask(secondDraft!.id, 'waiting_human');
    expect(human.steps[0].receipt?.settings).toMatchObject({device: 'cpu', batch_size: 8});
    expect(human.steps[1].receipt).toMatchObject({not_started: true});
    expect(human.steps.slice(2).map(step => step.status)).toEqual(['pending', 'pending']);
    evidence.note('task_plan_human_wait_before_reload', human);
    await expect(card()).toContainText('자료 폴더 선택을 취소했습니다.');
    expect(importWrites).toHaveLength(0);
    expect(splitWrites).toHaveLength(1);
    await evidence.screenshot(page, 'task-human-folder-choice-stop');

    await page.reload();
    await login();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('navigation', {name: '작업 단계 (Workflow Stages)', exact: true}).getByRole('button', {name: /^01 데이터 준비/}).click();
    await expect(page.getByRole('button', {name: '전체 (20)', exact: true})).toBeVisible();
    await reconnectAssistant();
    await expect(card()).toContainText('합성 자료 가져오기와 분할 준비');
    await expect(card()).toContainText('직접 확인 필요');
    await expect(card()).toContainText('폴더 선택 필요');
    expect((await readTask(secondDraft!.id)).revision).toBe(human.revision);
    expect(importWrites).toHaveLength(0);
    // Simulates a native user choice, restricted by the helper to this fixture's generated directory and consumed once.
    fs.writeFileSync(path.join(workspace.root, 'controlled-native-host', 'controlled-task-folder.json'), JSON.stringify({folder: source}), {mode: 0o600});
    const eventCountBeforeSettings = taskEvents.length;
    await card().getByRole('button', {name: /^저장 설정 복원/}).click();
    await expect(card().getByRole('button', {name: '폴더 선택하고 이어하기', exact: true})).toBeEnabled();
    expect((await readTask(secondDraft!.id)).revision).toBe(human.revision);
    expect(taskEvents).toHaveLength(eventCountBeforeSettings);
    expect(importWrites).toHaveLength(0);
    expect(splitWrites).toHaveLength(1);
    await evidence.screenshot(page, 'task-explicit-settings-restored-without-dispatch');
    await card().getByRole('button', {name: '폴더 선택하고 이어하기', exact: true}).click();
    const secondCompleted = await waitTask(secondDraft!.id, 'completed');
    expect(secondCompleted.steps[1].receipt).toMatchObject({project_id: project.id, imported: true, images: 20, not_started: false});
    expect(secondCompleted.steps[2].receipt?.split).toEqual({train: 12, val: 4, test: 4});
    expect(secondCompleted.steps[3].receipt?.step).toBe(1);
    const finalSource = String(secondCompleted.steps[1].receipt?.source_dataset_path);
    expect(finalSource.startsWith(path.join(project.project_dir, '.local-host-imports') + path.sep)).toBe(true);
    const finalSplitFile = path.join(project.dataset_dir, 'splits', crypto.createHash('sha256').update(finalSource).digest('hex') + '.json');
    expect(savedCounts(finalSplitFile)).toEqual({train: 12, val: 4, test: 4});
    expect(savedCounts()).toEqual({train: 14, val: 4, test: 2});
    expect(importWrites).toHaveLength(1);
    expect(splitWrites).toHaveLength(2);
    await expect(card()).toContainText('완료 4/4개');

    for (const task of [firstCompleted, secondCompleted]) {
      expect(task.approval?.plan_sha256).toBe(task.plan_sha256);
      expect(taskEvents.filter(row => row.id === task.id && row.event.type === 'approve')).toHaveLength(1);
      expect(taskEvents.filter(row => row.id === task.id && row.event.type === 'begin_step')).toHaveLength(task.steps.length);
    }
    const rows = ledger();
    expect(rows.filter(row => row.route === '/v1/responses')).toHaveLength(4);
    expect(rows.filter(row => row.kind === 'controlled_native_choice').map(row => row.selected)).toEqual([false, true]);
    expect(rows.filter(row => row.kind === 'host' && row.method === 'dialog.folder')).toHaveLength(2);
    expect(rows.some(row => row.kind === 'blocked_provider_url')).toBe(false);
    for (const file of originals) expect(sha(fs.readFileSync(file.path))).toBe(file.sha256);
    expect(prohibited).toEqual([]);
    expect(foreign).toEqual([]);
    expect(pageErrors).toEqual([]);
    await Promise.all(responseChecks);
    const rendererBoundary = await page.evaluate(() => ({preload: typeof window.api}));
    expect(rendererBoundary.preload).toBe('undefined');
    await evidence.screenshot(page, 'task-resumed-import-completed-and-saved-split');
    evidence.note('assistant_task_plan', {
      actual_browser_renderer_backend_actions: true, synthetic_local_cookie_account: true,
      controlled_provider_no_external_fallback: true, controlled_native_choice: true,
      actual_provider_or_SIWC_login: false, model_or_training_execution: false, external_image_transmission: false,
      project_id: project.id, originals, imported_source: finalSource, initial_split_sha256: initialSplitSha,
      original_source_split_sha256: sha(fs.readFileSync(splitFile)), final_split_sha256: sha(fs.readFileSync(finalSplitFile)),
      pause_retained_receipt: paused, completed_tasks: [firstCompleted, secondCompleted],
      narrow_layouts: narrowLayouts, keyboard_plan_approval_count: 1, duplicate_composer_primary: false,
      human_wait_before_reload: human, task_events: taskEvents, split_writes: splitWrites,
      explicit_import_validation_requests: importWrites, summary_refresh_requests: summaryRefreshes,
      final_split_counts: savedCounts(finalSplitFile), original_source_split_counts: savedCounts(),
      controlled_provider_call_count: 4, page_errors: pageErrors,
      external_network_requests: foreign, forbidden_model_operations: prohibited, renderer_boundary: rendererBoundary,
    });

    // Seed a prior cancelled-folder record through the actual actor-scoped ledger. This is generated fixture
    // history, not evidence of a new native dialog action; the original real cancelled/accepted choices above remain.
    const heldDraft: AssistantTask = (await api('/api/assistant-tasks', {
      goal: '이전 폴더 선택 대기', binding: secondCompleted.binding,
      steps: [{action: 'import_dataset', args: {}, label: '사용자가 폴더를 고르기 전 대기'}],
    })).task;
    const heldApproved: AssistantTask = (await api(`/api/assistant-tasks/${heldDraft.id}/events`, {
      type: 'approve', confirmed: true, binding: heldDraft.binding, expected_revision: heldDraft.revision,
    })).task;
    const heldStarted: AssistantTask = (await api(`/api/assistant-tasks/${heldDraft.id}/events`, {
      type: 'begin_step', binding: heldApproved.binding, expected_revision: heldApproved.revision,
      step_id: heldApproved.steps[0].id, attempt_id: 'fixture-prior-folder-wait',
    })).task;
    const held: AssistantTask = (await api(`/api/assistant-tasks/${heldDraft.id}/events`, {
      type: 'wait_step', binding: heldStarted.binding, expected_revision: heldStarted.revision,
      step_id: heldStarted.steps[0].id, attempt_id: 'fixture-prior-folder-wait',
      wait_kind: 'human', receipt: {not_started: true}, message: '자료 폴더 선택을 취소했습니다. 폴더를 선택한 뒤 이어가세요.',
    })).task;
    await page.reload(); await login(); await reconnectAssistant();
    await expect(card()).toContainText('이전 폴더 선택 대기');
    await expect(card()).toContainText('폴더 선택 필요');
    const question = 'TASK_PLAN_FIXTURE_RECOVERY 이전 계획은 기록으로 남기고 현재 합성 자료로 새 계획을 준비해 주세요.';
    await assistant.getByRole('checkbox', {name:'설명만 받기 · 작업은 실행하지 않음',exact:true}).check();
    await assistant.getByRole('textbox', {name: '도우미에게 묻기', exact: true}).fill(question);
    const beforeNewRequest = ledger().filter(row => row.route === '/v1/responses').length;
    const beforeRecoveryImports = importWrites.length, beforeRecoverySplits = splitWrites.length;
    await card().getByRole('button', {name: '이 계획 멈추고 새 요청', exact: true}).click();
    await expect(card()).toContainText('새 요청을 입력하세요.');
    await expect(assistant.getByRole('textbox', {name: '도우미에게 묻기', exact: true})).toHaveValue(question);
    await expect(assistant.getByRole('textbox', {name: '도우미에게 묻기', exact: true})).toBeFocused();
    const parked = await readTask(held.id);
    expect(parked.status).toBe('paused');
    expect(parked.steps).toEqual(held.steps);
    expect(parked.approval).toEqual(held.approval);
    expect(parked.plan_sha256).toBe(held.plan_sha256);
    expect(ledger().filter(row => row.route === '/v1/responses')).toHaveLength(beforeNewRequest);
    expect(importWrites).toHaveLength(beforeRecoveryImports);
    expect(splitWrites).toHaveLength(beforeRecoverySplits);
    await evidence.screenshot(page, 'task-recovery-new-request-with-held-history');

    await assistant.getByRole('button', {name: '보내기', exact: true}).click();
    await expect(card().getByRole('button', {name: '계획 확인하고 실행', exact: true})).toBeEnabled();
    const replacement = await findTask('이전 계획 보관 뒤 새 합성 자료 준비');
    expect(replacement?.id).not.toBe(firstDraft!.id);
    expect(replacement?.id).not.toBe(held.id);
    expect(replacement?.approval).toBeNull();
    await card().getByRole('button', {name: '계획 확인하고 실행', exact: true}).click();
    const recoveryCompleted = await waitTask(replacement!.id, 'completed');
    await expect(card()).toContainText('완료 3/3개');
    expect((await readTask(held.id)).revision).toBe(parked.revision);
    await page.reload(); await login(); await reconnectAssistant();
    await expect(card().locator(':scope > p.font-semibold')).toHaveText(recoveryCompleted.goal);
    await expect(card()).toContainText('완료 3/3개');
    await expect(card().getByRole('heading', {name: '작업 계획', exact: true})).toBeVisible();
    expect((await readTask(held.id)).revision).toBe(parked.revision);
    for (const file of originals) expect(sha(fs.readFileSync(file.path))).toBe(file.sha256);
    expect(prohibited).toEqual([]); expect(foreign).toEqual([]); expect(pageErrors).toEqual([]);
    await evidence.screenshot(page, 'task-recovery-new-completed-plan-after-reload');
    evidence.note('assistant_task_recovery', {
      seeded_actor_scoped_human_wait: held, parked_without_execution: parked,
      replacement_completed: recoveryCompleted, unsent_question_preserved: true,
      no_provider_request_on_prepare: true, no_import_or_split_on_prepare: true,
      newest_completed_restored_instead_of_older_paused: true,
      real_GPT_or_model_execution: false, external_image_transmission: false,
    });
  } finally {
    releaseSplit.resolve();
    evidence.note('assistant_task_plan_observed_requests', {
      task_events: taskEvents, split_writes: splitWrites, explicit_import_validation_requests: importWrites,
      summary_refresh_requests: summaryRefreshes, page_errors: pageErrors, external_network_requests: foreign,
      forbidden_model_operations: prohibited,
    });
    evidence.note('assistant_task_plan_close', await server.close());
  }
});

// Native scope is deliberately narrower than the public browser scenario. The owned source Electron app, preload,
// backend and task ledger are real. Only two provider-facing IPC handlers return generated config/scripted replies;
// this is neither SIWC login nor acceptance of Electron's production provider implementation.
test('native task plan uses the actual app window and persists prepared settings with no model or provider execution', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  test.setTimeout(240_000);
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const origin = `http://127.0.0.1:${backend.port}`;
  const foreign: string[] = [], prohibited: string[] = [], pageErrors: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    if (/^https?:$/.test(url.protocol) && url.origin === origin && request.method() !== 'GET'
      && /\/api\/(training\/start|label-suggestions\/batches|labeling-jobs|inspections\/runs)/.test(url.pathname)) prohibited.push(url.pathname);
  });
  await page.route(/^https?:\/\//, route => {
    const url = new URL(route.request().url());
    if (url.origin === origin) return route.fallback();
    foreign.push(url.origin); return route.abort('blockedbyclient');
  });
  const api = (route: string, body?: unknown, method?: string): Promise<any> => page.evaluate(async input => {
    const response = await fetch(input.origin + input.route, {
      method: input.method || (input.body === undefined ? 'GET' : 'POST'),
      ...(input.body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(input.body)}),
    });
    if (!response.ok) throw new Error(`Owned native task API ${response.status}: ${await response.text()}`);
    return response.json();
  }, {origin, route, body, method});
  const source = path.join(workspace.root, 'native-task-originals'), originals: Array<{path: string; sha256: string}> = [];
  for (const [index, label] of ['OK', 'NG'].entries()) {
    fs.mkdirSync(path.join(source, label), {recursive: true});
    for (let number = 0; number < 10; number++) {
      const file = path.join(source, label, `${number}.png`);
      fs.writeFileSync(file, png(64, 3, (x, y) => [90 + index * 50 + number, x % 256, y % 256]));
      originals.push({path: file, sha256: sha(fs.readFileSync(file))});
    }
  }
  const project = await api('/api/project/create', {name: 'Native task controlled project', task: 'classification'});
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'classification'});
  await api('/api/dataset/split', {folder_path: source, task: 'classification', train_ratio: .8, val_ratio: .1, test_ratio: .1});
  const config: AssistantConfigState = {
    active: 'chatgpt', encryptionAvailable: true,
    providers: [{provider: 'chatgpt', label: 'Controlled native provider', baseUrl: 'https://api.openai.com/v1', model: 'native-script-no-network', format: 'responses', hasKey: false, supportsImages: false, ready: true, connectionAuthority: 'native-script-authority'}],
    chatgpt: {accounts: [{recordId: 'native-script-account', label: 'Generated native fixture', model: 'native-script-no-network', signedIn: true, planUsageEnabled: true, planUsageAcknowledged: true}], active: 'native-script-account', encryptionAvailable: true, status: 'connected', authority: 'native-script-authority'},
  };
  await electronSession.app.evaluate(({ipcMain, BrowserWindow}, state) => {
    const calls = {providerCalls: 0, imageSends: 0};
    Object.assign(globalThis, {__nativeTaskProviderCalls: calls});
    const authorize = (event: Electron.IpcMainInvokeEvent) => {
      if (event.senderFrame !== BrowserWindow.getAllWindows()[0]?.webContents.mainFrame) throw new Error('Unexpected native fixture frame');
    };
    ipcMain.removeHandler('assistant:get-state');
    ipcMain.handle('assistant:get-state', event => {authorize(event); return state;});
    ipcMain.removeHandler('assistant:send');
    ipcMain.handle('assistant:send', (event, input: AssistantSendInput) => {
      authorize(event); calls.providerCalls++;
      if (input.image) {calls.imageSends++; throw new Error('Native task fixture cannot send images');}
      if (calls.providerCalls === 1) return {ok: true, text: '', stop: 'tool', toolCalls: [{id: 'native-task-plan', name: 'propose_task', arguments: {
        goal: '네이티브 합성 자료 학습 설정 준비', steps: [
          {action: 'open_step', args: {step: 1}, label: '현재 자료 화면 확인'},
          {action: 'configure_training', args: {preset: 'fast', device: 'cpu', batch_size: 8}, label: 'CPU · 배치 8 준비'},
          {action: 'open_step', args: {step: 3}, label: '준비한 학습 화면 열기'},
        ],
      }}]};
      if (calls.providerCalls === 2) return {ok: true, text: '제어된 네이티브 계획 준비 완료', stop: 'end', toolCalls: []};
      throw new Error('Unexpected native fixture provider call');
    });
  }, config);
  await page.reload();
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
  await expect(page.getByRole('button', {name: '전체 (20)', exact: true})).toBeVisible();
  await page.getByRole('button', {name: '왜 그런가요?', exact: true}).click();
  await page.getByRole('button', {name: '도우미에게 묻기', exact: true}).click();
  const assistant = page.getByRole('complementary', {name: '도우미', exact: true});
  const card = assistant.getByRole('region', {name: '도우미 작업', exact: true});
  await expect(card).toHaveAttribute('aria-busy', 'false');
  await assistant.getByRole('checkbox', {name:'설명만 받기 · 작업은 실행하지 않음',exact:true}).check();
  await assistant.getByRole('textbox', {name: '도우미에게 묻기', exact: true}).fill('합성 자료의 CPU 배치 8 설정을 준비해 주세요.');
  await assistant.getByRole('button', {name: '보내기', exact: true}).click();
  await expect(card.getByRole('button', {name: '계획 확인하고 실행', exact: true})).toBeEnabled();
  const draft: AssistantTask = (await api('/api/assistant-tasks')).tasks.find((task: AssistantTask) => task.goal === '네이티브 합성 자료 학습 설정 준비');
  expect(draft.status).toBe('draft');
  expect(draft.approval).toBeNull();
  expect(draft.steps.map(step => step.status)).toEqual(['pending', 'pending', 'pending']);
  await expect(page.getByRole('navigation', {name: '작업 단계 (Workflow Stages)', exact: true})
    .getByRole('button', {name: /^01 데이터 준비/})).toHaveAttribute('aria-current', 'step');
  await expect(page.locator('#workflow-training')).toHaveCount(0);
  await evidence.screenshot(page, 'native-task-plan-before-approval');
  await card.getByRole('button', {name: '계획 확인하고 실행', exact: true}).click();
  await expect.poll(async () => (await api(`/api/assistant-tasks/${draft.id}`)).task.status).toBe('completed');
  const completed: AssistantTask = (await api(`/api/assistant-tasks/${draft.id}`)).task;
  expect(completed.steps.map(step => step.status)).toEqual(['completed', 'completed', 'completed']);
  expect(completed.steps[1].receipt?.settings).toMatchObject({preset: 'fast', device: 'cpu', batch_size: 8});
  expect(completed.steps[2].receipt?.step).toBe(3);
  expect(completed.approval?.plan_sha256).toBe(completed.plan_sha256);
  await expect(card).toContainText('완료 3/3개');
  const screen = page.locator('#workflow-training');
  await expect(screen).toBeVisible();
  await screen.getByText('다음 학습 배치·로컬 장치 설정', {exact: true}).click();
  await expect(screen.getByLabel('다음 학습 배치 크기', {exact: true})).toHaveValue('8');
  await expect(screen.getByLabel('다음 학습 로컬 장치', {exact: true})).toHaveValue('cpu');
  const provider = await electronSession.app.evaluate(() => (globalThis as typeof globalThis & {__nativeTaskProviderCalls: {providerCalls: number; imageSends: number}}).__nativeTaskProviderCalls);
  expect(provider).toEqual({providerCalls: 2, imageSends: 0});
  expect(prohibited).toEqual([]); expect(foreign).toEqual([]); expect(pageErrors).toEqual([]);
  for (const file of originals) expect(sha(fs.readFileSync(file.path))).toBe(file.sha256);
  await evidence.screenshot(page, 'native-task-completed-settings-and-ledger');
  evidence.note('assistant_native_task_smoke', {
    actual_owned_source_electron_window: true, actual_preload_backend_task_ledger: true,
    controlled_provider_main_IPC_get_state_and_send: true, actual_provider_or_SIWC_login: false,
    project_id: project.id, source_images: originals, completed_task: completed, provider,
    model_or_training_execution: false, external_image_transmission: false, external_network_requests: foreign,
    forbidden_model_operations: prohibited, page_errors: pageErrors,
  });
});
