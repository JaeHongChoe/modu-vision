import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
import type {AssistantBridge, AssistantConfigState, AssistantSendInput} from '../../src/types/electron';

const sha = (bytes: Buffer) => crypto.createHash('sha256').update(bytes).digest('hex');

// Real renderer/backend/stores and synthetic project files. Only provider IPC replies are scripted;
// no provider network, model, training, annotation adoption or import application is exercised.
test('ChatGPT prefill updates an already open split bar without saving; draft import stays available without image sending', async ({page, renderer, workspace, evidence}) => {
  test.setTimeout(180_000);
  const source = path.join(workspace.root, 'easy-use-source');
  const originalFiles: Array<{path: string; sha256: string}> = [];
  for (const [index, label] of ['OK', 'NG'].entries()) {
    const folder = path.join(source, label);fs.mkdirSync(folder, {recursive: true});
    for (let number = 0; number < 10; number++) {
      const file = path.join(folder, `${label}-${String(number).padStart(2, '0')}.png`);
      fs.writeFileSync(file, png(128, 3, (x, y) => [90 + index * 60 + number, (x + number) % 256, (y + index) % 256]));
      originalFiles.push({path: file, sha256: sha(fs.readFileSync(file))});
    }
  }
  const api = async (route: string, body?: unknown, method = body === undefined ? 'GET' : 'POST') => {
    const response = await page.request.fetch(renderer.origin + route, {method, ...(body === undefined ? {} : {data: body})});
    expect(response.ok(), `${method} ${route}: ${await response.text()}`).toBe(true);
    return response.json();
  };
  const project = await api('/api/project/create', {name: 'Assistant easy-use supplemental', task: 'classification'});
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'classification'});
  const saved = await api('/api/dataset/split', {folder_path: source, task: 'classification', train_ratio: .8, val_ratio: .1, test_ratio: .1});
  const splitFile = path.join(project.dataset_dir, 'splits', crypto.createHash('sha256').update(source).digest('hex') + '.json');
  const savedSplitBytes = fs.readFileSync(splitFile), savedSplitSha = sha(savedSplitBytes);
  const savedImages = await api(`/api/dataset/images?folder_path=${encodeURIComponent(source)}&task=classification&limit=50`);
  const savedAssignments = savedImages.items.map((image: {file_path: string; split: string}) => [image.file_path, image.split]);

  const pageErrors: string[] = [], externalRequests: string[] = [], writes: Array<{method: string; path: string}> = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    if (/^https?:$/.test(url.protocol) && url.origin !== renderer.origin) externalRequests.push(request.url());
    if (url.origin === renderer.origin && url.pathname.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method()))
      writes.push({method: request.method(), path: url.pathname});
  });
  await page.route(/^https?:\/\//, route => new URL(route.request().url()).origin === renderer.origin ? route.fallback() : route.abort('blockedbyclient'));
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
  await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click();
  const splitSlider = page.locator('input[type="range"][min="50"][max="90"]');
  await splitSlider.scrollIntoViewIfNeeded();await expect(splitSlider).toBeInViewport();await expect(splitSlider).toHaveValue('80');
  await expect(page.getByText('학습 · 검증 · 시험 분할 (저장됨)', {exact: true})).toBeVisible();

  await page.evaluate(() => {
    const config: AssistantConfigState = {active: 'chatgpt', encryptionAvailable: true, providers: [{
      provider: 'chatgpt', label: 'ChatGPT', baseUrl: 'https://api.openai.com/v1', model: 'scripted-no-network',
      format: 'responses', hasKey: false, supportsImages: false, ready: true,
    }],chatgpt:{accounts:[{recordId:'qa-chatgpt',label:'QA 계정',model:'scripted-no-network',signedIn:true,planUsageEnabled:true,planUsageAcknowledged:true}],active:'qa-chatgpt',encryptionAvailable:true,status:'connected',authority:'qa-authority'}};
    const sent: AssistantSendInput[] = [], intents: Array<{fileName: string; imageIdentity: string}> = [];
    const bridge: AssistantBridge = {
      getState: async () => config, saveProvider: async () => config, removeKey: async () => config,
      removeProvider: async () => config, setActive: async () => config,
      listModels: async () => ({ok: true, models: []}), cancel: async () => true, beginRun: async () => false, endRun: async () => {},
      imageIntent: async input => {intents.push(input);return {ok: true, grantId: `isolated-${intents.length}`, providerLabel: '격리 QA 공급자', message: `${input.fileName} 이미지 1장을 격리 QA 공급자로 보냅니다.`, expiresAt: Date.now() + 60_000};},
      send: async input => {
        sent.push(input);
        return sent.length === 1 ? {ok: true, text: '', stop: 'tool', toolCalls: [{id: 'prefill-only', name: 'prefill_split', arguments: {train_percent: 70}}]}
          : {ok: true, text: '70% 입력만 마쳤습니다. 저장은 사람이 합니다.', stop: 'end', toolCalls: []};
      },
    };
    if (!window.api) throw new Error('Desktop host shim did not initialize');
    Object.assign(window.api, {assistant: bridge});
    Object.assign(window, {__assistantSupplemental: {sent, intents}});
  });
  const openAssistant = async () => {
    await page.getByRole('button', {name: '왜 그런가요?', exact: true}).click();
    await page.getByRole('button', {name: '도우미에게 묻기', exact: true}).click();
    await expect(page.getByRole('complementary', {name: '도우미', exact: true})).toBeVisible();
  };
  await openAssistant();
  const assistant = page.getByRole('complementary', {name: '도우미', exact: true});
  await expect(assistant.getByRole('textbox', {name: '도우미에게 묻기', exact: true})).toBeEnabled();
  await assistant.getByRole('textbox', {name: '도우미에게 묻기', exact: true}).fill('학습 분할을 70%로 채워 두세요. 저장하지 마세요.');
  await assistant.getByRole('button', {name: '보내기', exact: true}).click();
  await expect(assistant.getByRole('log', {name: '도우미 대화'})).toContainText('70% 입력만 마쳤습니다.');
  await splitSlider.scrollIntoViewIfNeeded();await expect(splitSlider).toBeInViewport();
  await expect(splitSlider).toHaveValue('70');
  await expect(page.getByText('학습 · 검증 · 시험 분할 (예상)', {exact: true})).toBeVisible();
  expect(sha(fs.readFileSync(splitFile))).toBe(savedSplitSha);
  expect((await api(`/api/dataset/images?folder_path=${encodeURIComponent(source)}&task=classification&limit=50`)).items.map((image: {file_path: string; split: string}) => [image.file_path, image.split])).toEqual(savedAssignments);
  expect(writes.some(row => row.path === '/api/dataset/split')).toBe(false);
  await assistant.getByRole('button', {name: '닫기', exact: true}).click();
  await splitSlider.scrollIntoViewIfNeeded();await expect(splitSlider).toBeInViewport();
  await evidence.screenshot(page, 'already-open-split-bar-prefilled-70-saved-80-unchanged');

  await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(1).click();
  await expect(page.locator('[data-canvas-container] canvas').first()).toBeVisible();
  await expect(page.locator('[data-labeling-filmstrip]')).toContainText('1 / 20');
  await page.getByRole('button', {name: '초안 검토 패널', exact: true}).click();
  await page.getByRole('button', {name: '기존 라벨 가져오기 (1단계)', exact: true}).click();
  const exchange = page.locator('#workflow-label-exchange');
  await expect(exchange).toBeVisible();await expect(exchange.locator('..')).toHaveAttribute('open', '');
  const exchangePanel=page.getByRole('region', {name: '데이터 검토와 라벨 교환', exact: true});
  await expect(exchangePanel).toContainText('검색 결과 20개');
  await expect(exchangePanel.getByRole('button', {name: '라벨 폴더 선택', exact: true})).toBeEnabled();
  await exchange.scrollIntoViewIfNeeded();await expect(exchange).toBeInViewport();
  expect(writes.some(row => row.path === '/api/dataset/formats/import')).toBe(false);
  await evidence.screenshot(page, 'draft-reuse-link-opens-original-label-exchange');

  await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(1).click();
  await expect(page.locator('[data-labeling-filmstrip]')).toContainText('1 / 20');
  await openAssistant();
  await assistant.getByRole('textbox', {name: '도우미에게 묻기', exact: true}).fill('지금 열린 이미지를 설명해 주세요.');
  await expect(assistant.getByRole('button', {name: '이미지와 함께 묻기', exact: true})).toHaveCount(0);
  await expect(assistant.getByRole('group', {name: '이미지 보내기 확인', exact: true})).toHaveCount(0);
  await expect(assistant).toContainText('현재 텍스트 질문만 지원합니다');
  const nextAnnotations=page.waitForResponse(response=>new URL(response.url()).pathname.startsWith('/api/annotations/')
    && new URL(response.url()).searchParams.get('file_path')?.endsWith('NG-01.png')===true && response.request().method()==='GET');
  await page.getByLabel('Next image or page', {exact: true}).click();
  expect((await nextAnnotations).status()).toBe(200);
  await expect(page.locator('[data-labeling-filmstrip]')).toContainText('2 / 20');
  await expect(page.locator('[data-labeling-filmstrip] .border-blue-500 img')).toHaveAttribute('alt','NG-01.png');
  await expect(assistant.getByRole('group', {name: '이미지 보내기 확인', exact: true})).toHaveCount(0);
  const bridgeReadback = await page.evaluate(() => {
    const state = (window as unknown as {__assistantSupplemental: {sent: AssistantSendInput[]; intents: unknown[]}}).__assistantSupplemental;
    return {calls: state.sent.length, imageSends: state.sent.filter(call => call.image).length, intents: state.intents.length};
  });
  expect(bridgeReadback).toEqual({calls: 2, imageSends: 0, intents: 0});
  expect(pageErrors).toEqual([]);expect(externalRequests).toEqual([]);
  expect(writes.some(row => /\/api\/(training\/start|label-suggestions\/batches|labeling-jobs)/.test(row.path))).toBe(false);
  expect(sha(fs.readFileSync(splitFile))).toBe(savedSplitSha);
  for (const file of originalFiles) expect(sha(fs.readFileSync(file.path))).toBe(file.sha256);
  await evidence.screenshot(page, 'chatgpt-text-only-after-actual-next-image');
  evidence.note('assistant_easy_use_supplemental', {
    actual_browser_renderer_backend_stores: true, native_electron_qa: false, fake_provider_bridge_only: true,
    project_id: project.id, synthetic_images: originalFiles, saved_split: saved.split, saved_split_file: splitFile,
    saved_split_sha256: savedSplitSha, visible_prefill_percent: 70, saved_assignments_unchanged: true,
    original_label_exchange_opened_from_draft_ui: true, import_applied: false,
    chatgpt_text_only_after_actual_next_image: true, image_intents_created: 0, bridge: bridgeReadback,
    provider_network_requests: externalRequests.length, page_errors: pageErrors, renderer_writes: writes,
    training_or_model_execution: false, detection_metrics_ui: 'not_run_in_classification_fixture; covered by focused real React SSR tests',
  });
});
