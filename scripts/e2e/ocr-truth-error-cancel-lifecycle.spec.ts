import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {Page, Request, Locator} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

test.use({actionTimeout: 10_000});
type OwnedApi = (route: string, body?: unknown, method?: string) => Promise<any>;
const sha = (raw: Buffer) => createHash('sha256').update(raw).digest('hex');
const truth = '  검사Ａ12\u00a0  ';
const rawIdentity = (s: fs.Stats) => [s.dev, s.ino, s.mode, s.nlink, s.size, s.mtimeMs, s.ctimeMs];

// Fresh reads preserve every name and byte in these declared source/label/
// prepared-data scopes. This is not an assertion about unrelated runtime DBs.
function protectedTree(root: string): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  try {fs.lstatSync(root);} catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {'.': {kind: 'absent'}};
    throw error;
  }
  const visit = (file: string) => {
    const named = fs.lstatSync(file), relative = path.relative(root, file).split(path.sep).join('/') || '.';
    expect(named.isSymbolicLink()).toBe(false);
    if (named.isDirectory()) {
      result[relative] = {kind: 'directory', identity: rawIdentity(named)};
      for (const name of fs.readdirSync(file).sort()) visit(path.join(file, name));
      expect(rawIdentity(fs.lstatSync(file))).toEqual(rawIdentity(named));
      return;
    }
    expect(named.isFile()).toBe(true); expect(named.nlink).toBe(1);
    const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    let primary: unknown;
    try {
      const opened = fs.fstatSync(fd); expect(rawIdentity(opened)).toEqual(rawIdentity(named));
      const raw = fs.readFileSync(fd); expect(raw.length).toBe(named.size);
      expect(rawIdentity(fs.fstatSync(fd))).toEqual(rawIdentity(opened));
      expect(rawIdentity(fs.lstatSync(file))).toEqual(rawIdentity(named));
      result[relative] = {kind: 'file', identity: rawIdentity(named), sha256: sha(raw)};
    } catch (error) {primary = error; throw error;}
    finally {try {fs.closeSync(fd);} catch (error) {if (primary === undefined) throw error;}}
  };
  visit(root); return result;
}

async function within<T>(work: Promise<T>, deadline: number, label: string): Promise<T> {
  const remaining = deadline - performance.now();
  if (!(remaining > 0)) throw Error('Original OCR 10s observation frame expired: ' + label);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {return await Promise.race([work, new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(Error('Original OCR 10s observation frame expired: ' + label)), remaining);
  })]);} finally {if (timer) clearTimeout(timer);}
}

async function exercise(page: Page, w: Workspace, e: Evidence, api: OwnedApi,
                        native: boolean, origin: string, url?: string) {
  const source = path.join(w.root, 'ocr-unsent-originals'); fs.mkdirSync(source);
  const relative = ['train/part-train.png', 'val/part-val.png'];
  const files = relative.map((name, index) => {
    const file = path.join(source, name); fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file, png(64, 3, (x, y) => [x, y, 70 + index * 40])); return file;
  });
  const originals = files.map(file => ({path: file, size: fs.statSync(file).size, sha256: sha(fs.readFileSync(file))}));
  expect(originals[0].sha256).not.toBe(originals[1].sha256);
  const made = await api('/api/project/create', {name: 'Owned OCR unsent truth lifecycle', task: 'classification'});
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'classification'});
  // Controlled setup labels exercise preservation; no reviewer/quality
  // approval or OCR model result is manufactured by these annotations.
  for (let i = 0; i < files.length; i++) await api('/api/annotations/save', {
    image_id: path.basename(files[i], '.png'), image_path: files[i], image_width: 64, image_height: 64,
    actor: 'ocr-lifecycle-fixture', annotations: [{id: 'owned-ocr-original-' + i,
      type: 'bbox', label: 'Defect', category_id: 1, bbox: [2, 3, 12, 13]}],
  });
  const project = await api('/api/project/current'); expect(project.id).toBe(made.id);
  expect(project.source_dataset_dir).toBe(source); expect(project.task).toBe('classification');
  const annotationRoutes = files.map(file => '/api/annotations/' + path.basename(file, '.png') + '?file_path=' + encodeURIComponent(file));
  await api('/api/team-data'); await api('/api/team-data/readiness');
  const initialMetadata = (await api('/api/dataset/metadata?limit=100')).items;
  expect(initialMetadata).toHaveLength(2);
  for (const row of initialMetadata) await api('/api/team-data/images/' + row.image_uuid);
  for (const route of annotationRoutes) await api(route);
  await api('/api/project/labelsets'); await api('/api/ocr/datasets'); await api('/api/ocr/models');

  const mutations: Array<{method: string; endpoint: string}> = [];
  const observe = (request: Request) => {
    const address = new URL(request.url());
    if (address.origin === origin && address.pathname.startsWith('/api/')
        && !['GET', 'HEAD', 'OPTIONS'].includes(request.method()))
      mutations.push({method: request.method(), endpoint: address.pathname});
  };
  page.on('request', observe);
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'});
  const ocr = page.locator('details').filter({has: page.locator('summary').filter({hasText: '문자 인식 모델 실험'})}).first();
  const images = page.getByLabel('문자 원본 이미지 선택', {exact: true});
  const text = page.getByLabel('OCR 실제 정답 문자열', {exact: true});
  const split = page.getByLabel(/^독립 이미지 분할/);
  const table = ocr.getByRole('textbox', {name: /^정답 표 · 한 줄에/});
  const folder = ocr.getByRole('textbox', {name: '문자 이미지 폴더 경로', exact: true});
  const add = ocr.getByRole('button', {name: '선택 이미지의 문자 정답 추가', exact: true});
  const load = ocr.getByRole('button', {name: '저장된 정답 읽기', exact: true});
  const save = ocr.getByRole('button', {name: '정답과 이미지 해시 저장', exact: true});
  // The owner's error is the direct paragraph in its body; the nested warm-
  // start selector independently refuses the same absent manifest on mount.
  const ownerError = ocr.locator(':scope > div > p[role="alert"]');
  const enter = async () => {
    await stages.getByRole('button', {name: /^03.*오토딥러닝/}).click();
    await page.getByRole('region', {name: '모델 학습 허브'}).getByRole('button', {name: /^문자 인식/}).click();
    await expect(ocr).toBeVisible();
    await expect(images.locator('option')).toHaveCount(files.length + 1);
    await expect(images).toBeEnabled();
    expect((await images.locator('option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value))).sort())
      .toEqual(['', ...files].sort());
    const detail = ocr.locator('details').filter({has: page.locator('summary').filter({hasText: '정답 표·가져오기 상세 설정'})});
    if (await detail.getAttribute('open') === null) await detail.locator('summary').click();
    await expect(folder).toHaveValue(source);
  };
  const emptyControls = async () => {
    await expect(images).toHaveValue(''); await expect(text).toHaveValue(''); await expect(table).toHaveValue('');
    await expect(split).toHaveValue('train'); await expect(add).toBeDisabled(); await expect(save).toBeDisabled();
    await expect(ocr.getByRole('button', {name: 'OCR 후보 학습', exact: true})).toBeDisabled();
    await expect(ocr.getByRole('button', {name: '시험 분할 평가', exact: true})).toBeDisabled();
    await expect(ocr.getByRole('button', {name: '문자 읽기', exact: true})).toBeDisabled();
  };
  const readbacks = async () => ({
    metadata: (await api('/api/dataset/metadata?limit=100')).items,
    annotations: await Promise.all(annotationRoutes.map(route => api(route))),
    team: await api('/api/team-data'), readiness: await api('/api/team-data/readiness'),
    teamImages: await Promise.all(initialMetadata.map((row: any) => api('/api/team-data/images/' + row.image_uuid))),
    labelsets: await api('/api/project/labelsets'), split: await api('/api/dataset/metadata/split'),
    datasets: await api('/api/ocr/datasets'), models: await api('/api/ocr/models'),
  });
  const capture = async (name: string, locator: Locator) => {
    await locator.scrollIntoViewIfNeeded(); await expect(locator).toBeInViewport();
    await e.screenshot(page, `${native ? 'native' : 'browser'}-ocr-${name}`);
  };
  try {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await enter();
    const baselineReads = await readbacks();
    expect(baselineReads.datasets).toEqual({datasets: []}); expect(baselineReads.models).toEqual({models: []});
    expect(baselineReads.annotations.every(row => row.annotations.length === 1 && row.metadata.workflow_state !== 'approved')).toBe(true);
    const roots = {source, annotations: project.annotations_dir,
      labelsets: path.join(project.project_dir, 'labelsets'),
      preparedOCR: path.join(project.dataset_dir, 'ocr'), modelsOCR: path.join(project.models_dir, 'ocr')};
    const before = Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, protectedTree(root)]));
    const registryBefore = sha(fs.readFileSync(path.join(project.project_dir, 'labelsets.json')));
    const unchanged = async () => {
      expect(await readbacks()).toEqual(baselineReads);
      for (const [name, root] of Object.entries(roots)) expect(protectedTree(root)).toEqual(before[name]);
      expect(sha(fs.readFileSync(path.join(project.project_dir, 'labelsets.json')))).toBe(registryBefore);
      for (const original of originals) expect({size: fs.statSync(original.path).size, sha256: sha(fs.readFileSync(original.path))})
        .toEqual({size: original.size, sha256: original.sha256});
      const active = await api('/api/project/current');
      expect([active.id, active.project_dir, active.source_dataset_dir, active.task, active.active_labelset_id])
        .toEqual([project.id, project.project_dir, source, project.task, project.active_labelset_id]);
      expect(mutations).toEqual([]);
    };
    const beforeFile = path.join(w.logs, 'ocr-unsent-protected-before.json');
    fs.writeFileSync(beforeFile, JSON.stringify({before, registryBefore, baselineReads, originals}, null, 2)); e.addFile(beforeFile);

    await emptyControls(); await expect(load).toBeEnabled();
    await folder.fill(''); await expect(load).toBeDisabled(); await expect(save).toBeDisabled();
    await folder.fill(source); await expect(load).toBeEnabled(); await unchanged();
    await capture('empty-inputs-refuse-save', save);

    // A fresh owned source really has no ocr.json/prepared data. The ordinary
    // UI GET reaches the producer and returns its actual422; no route hook,
    // delegated response, fake offline state or status injection is used.
    const deadline = performance.now() + 10_000;
    const waiting = page.waitForResponse(response => {
      const address = new URL(response.url()); return address.origin === origin
        && address.pathname === '/api/ocr/manifest' && address.searchParams.get('dataset_path') === source
        && response.request().method() === 'GET';
    }, {timeout: Math.max(1, deadline - performance.now())});
    const [reply] = await within(Promise.all([waiting, load.click()]), deadline, 'actual missing-manifest GET');
    const request = reply.request(), address = new URL(reply.url());
    expect(request.frame()).toBe(page.mainFrame()); expect(request.isNavigationRequest()).toBe(false);
    expect(address.origin).toBe(origin); expect(address.pathname).toBe('/api/ocr/manifest');
    expect(Array.from(address.searchParams.keys())).toEqual(['dataset_path']);
    expect(address.searchParams.get('dataset_path')).toBe(source); expect(request.method()).toBe('GET');
    expect(await within(request.headerValue('x-vision-project'), deadline, 'request project header')).toBe(project.id);
    const contextRaw = await within(request.headerValue('x-vision-context'), deadline, 'request context header');
    expect(contextRaw).not.toBeNull(); const context = JSON.parse(contextRaw!);
    expect(context.project_id).toBe(project.id); expect(context.workspace_id).toEqual(expect.any(String));
    expect(context.actor_id).toEqual(expect.any(String)); expect(context.mode).toBe('local');
    expect(reply.status()).toBe(422);
    const raw = await within(reply.body(), deadline, 'complete original response body');
    expect(raw.length).toBeLessThanOrEqual(4096);
    const detail = 'OCR needs explicit labels in a regular ocr.json: ' + path.join(source, 'ocr.json');
    expect(JSON.parse(raw.toString('utf8'))).toEqual({detail});
    expect(await within(reply.finished(), deadline, 'original HTTP finished')).toBeNull();
    await within(expect(ownerError).toHaveText(detail, {timeout: Math.max(1, deadline - performance.now())}), deadline, 'visible real error');
    await within(expect(load).toBeEnabled({timeout: Math.max(1, deadline - performance.now())}), deadline, 'busy released after real failure');
    await emptyControls(); await unchanged(); await capture('actual-422-original-error', ownerError);
    const bodyFile = path.join(w.logs, 'ocr-missing-manifest-actual-body.json'); fs.writeFileSync(bodyFile, raw); e.addFile(bodyFile);

    // Re-enter the actual workbench to recover its local error state. This
    // does not retry the failed HTTP operation or create a saved OCR record.
    await stages.getByRole('button', {name: /^01.*데이터 관리/}).click(); await expect(ocr).toHaveCount(0);
    await enter(); await emptyControls(); await expect(ownerError).toHaveCount(0);
    const expectedTable = relative.map((image, index) => `${image}\t${truth}\t${index ? 'val' : 'train'}`).join('\n');
    for (let i = 0; i < files.length; i++) {
      await images.selectOption(files[i]); await text.fill(truth); await split.selectOption(i ? 'val' : 'train');
      await expect(add).toBeEnabled(); await add.click();
    }
    await expect(table).toHaveValue(expectedTable); await expect(save).toBeEnabled();
    expect(await table.inputValue()).toBe(expectedTable); await unchanged();
    await capture('valid-unicode-train-val-table-unsent', save);
    // The product has no OCR cancel dialog. This case exercises abandonment
    // of valid UNSENT truth by a real stage exit, not an HTTP/job cancellation.
    await stages.getByRole('button', {name: /^01.*데이터 관리/}).click(); await expect(ocr).toHaveCount(0);
    await unchanged(); await enter(); await emptyControls(); await expect(ownerError).toHaveCount(0);
    await expect(ocr.getByText(/^검증된 정답 \d+개$/)).toHaveCount(0);
    await unchanged(); await capture('stage-abandonment-reset-no-saved-truth', save);
    const afterFile = path.join(w.logs, 'ocr-unsent-protected-after.json');
    fs.writeFileSync(afterFile, JSON.stringify({after: Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, protectedTree(root)])),
      registryAfter: sha(fs.readFileSync(path.join(project.project_dir, 'labelsets.json'))), readsAfter: await readbacks()}, null, 2)); e.addFile(afterFile);
    e.note('ocr_truth_empty_error_cancel', {
      action: 'F030.ocr-native-exact-truth-save-reopen', dimensions: ['empty', 'error', 'cancel'],
      actual_UI_controls: true, empty_save_disabled: true,
      error: {original_UI_HTTP: true, origin, path: address.pathname, source, method: request.method(), status: reply.status(),
        main_frame: true, request_context: context, body_sha256: sha(raw), body_bytes: raw.length, HTTP_finished: true},
      cancel: {kind: 'valid unsent Unicode train/val table abandoned by actual stage exit and reset on re-entry',
        explicit_cancel_dialog: false, HTTP_abort: false, job_stop: false, prepared_or_training_POSTs: 0, expected_table: expectedTable},
      original_source_images: originals, original_annotations_metadata_team_labelsets_and_prepared_namespace_exact: true,
      business_mutations_after_setup: mutations, source_electron: native,
      actual_model_inference: false, training: false, human_truth_review: false,
      installed_device_release_quality_parent_acceptance: false,
    });
  } finally {page.off('request', observe);}
}

test('OCR empty inputs real missing truth error and unsent table stage abandonment preserve original labels',
  async ({page, request, renderer, workspace, evidence}) => {
    await installDesktopHostShim(page, renderer.port);
    const api: OwnedApi = async (route, body, method) => {
      const response = await request.fetch(renderer.origin + route, {method: method || (body === undefined ? 'GET' : 'POST'),
        timeout: 10_000, ...(body === undefined ? {} : {data: body})});
      expect(response.ok(), await response.text()).toBe(true); return response.json();
    };
    await exercise(page, workspace, evidence, api, false, renderer.origin, renderer.url);
  });

test('native OCR empty inputs real missing truth error and unsent table stage abandonment preserve original labels',
  {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
    const page = electronSession.window, backend = await electronSession.waitForBackend();
    const origin = `http://127.0.0.1:${backend.port}`;
    const api: OwnedApi = (route, body, method) => page.evaluate(async ({origin, route, body, method}) => {
      const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 10_000);
      try {
        const response = await fetch(origin + route, {method: method || (body === undefined ? 'GET' : 'POST'), signal: abort.signal,
          ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
        const value = await response.json(); if (!response.ok) throw Error(`Owned OCR fixture API HTTP ${response.status}`); return value;
      } finally {clearTimeout(timer);}
    }, {origin, route, body, method});
    await exercise(page, workspace, evidence, api, true, origin);
  });
