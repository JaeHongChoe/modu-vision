import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page, Response} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {browserTeamServer} from './fixtures/browser_team_server';
import {expectBrowserLoginGate,openBrowserServerManagement} from './fixtures/browserGateLogin';
import {storedZip} from './fixtures/storedZip';
import {png} from './qa/appFlow';

test.use({ignoreHTTPSErrors: true, actionTimeout: 15_000});
test.describe.configure({retries: 0});

const sha = (bytes: Buffer) => crypto.createHash('sha256').update(bytes).digest('hex');
const fileSha = (file: string) => sha(fs.readFileSync(file));
const stage = (page: Page, index: number) => page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(index).click();
async function checked(response: Response) {
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}
async function login(page: Page, origin: string, projectId: string, projectName: string, password: string) {
  await expectBrowserLoginGate(page,origin);
  await expect(page.getByLabel('공유 계정 이름', {exact: true})).toBeVisible();
  await page.getByLabel('공유 계정 이름', {exact: true}).fill('annotationowner');
  await page.getByLabel('공유 계정 비밀번호', {exact: true}).fill(password);
  const accepted = page.waitForResponse(response => new URL(response.url()).pathname === '/api/accounts/login'
    && response.request().method() === 'POST');
  await page.getByRole('button', {name: '연결·로그인', exact: true}).click();
  await checked(await accepted);
  await openBrowserServerManagement(page);
  await expect(page.getByLabel('공동 작업 프로젝트', {exact: true})).toHaveValue(projectId);
  await expect(page.getByLabel('공동 작업 프로젝트', {exact: true})).toBeEnabled();
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(projectName);
  await page.getByRole('dialog', {name: '컴퓨팅 서버 관리', exact: true}).getByRole('button', {name: '닫기', exact: true}).click();
}

test('HTTPS empty detection project uploads an image and restores its real saved canvas bbox after reload and relogin',
  async ({page, rendererBuild, workspace, evidence, loopbackGuard}) => {
  test.setTimeout(180_000);
  const password = 'owned synthetic browser annotation password 123';
  evidence.redact(password);
  const bytes = png(256, 3, (x, y) => [x, y, (x * 3 + y * 5 + 17) % 256]);
  const imageSha = sha(bytes);
  const entries: Array<[string, Buffer]> = [['part.png', bytes]];
  const archive = path.join(workspace.root, 'browser-annotation.zip');
  fs.writeFileSync(archive, storedZip(entries));
  const server = await browserTeamServer(workspace, rendererBuild.outDir);
  loopbackGuard.allow(server.port);
  evidence.redact(server.backend.token);
  const annotationPosts: unknown[] = [];
  const modelRequests: Array<{method: string; path: string}> = [];
  page.on('request', request => {
    const route = new URL(request.url()).pathname;
    if (route === '/api/annotations/save' && request.method() === 'POST') annotationPosts.push(request.postDataJSON());
    if (['/api/training/start', '/api/automated-training/start', '/api/model-execution/recipes', '/api/evaluation/results'].includes(route))
      modelRequests.push({method: request.method(), path: route});
  });
  try {
    // API setup creates only the private test account and an empty project.
    await server.call('/api/accounts/bootstrap', {username: 'annotationowner', password});
    const account = await server.call('/api/accounts/login', {username: 'annotationowner', password});
    evidence.redact(account.token);
    const project = await server.call('/api/project/create', {name: 'Owned browser bbox annotation roundtrip', task: 'detection'}, account.token);
    const api = (route: string) => server.call(route, undefined, account.token);
    await page.goto(server.origin);
    await login(page, server.origin, project.id, project.name, password);
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
    const imagePath = path.join(source, 'part.png');
    expect(fileSha(imagePath)).toBe(imageSha);
    const imagesRoute = '/api/dataset/images?' + new URLSearchParams({folder_path: source, task: 'detection', limit: '100'});
    const importedImages = await api(imagesRoute);
    expect(importedImages.total).toBe(1);
    expect(importedImages.items[0]).toMatchObject({file_name: 'part.png', file_path: imagePath, width: 256, height: 256});
    const thumbnails = page.locator('button[aria-label$="라벨링에서 열기"] img');
    await expect(thumbnails).toHaveCount(1);
    await expect.poll(() => thumbnails.evaluateAll(images => images.every(image =>
      (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    const query = '/api/annotations/part?' + new URLSearchParams({file_path: imagePath});
    const before = await api(query);
    expect(before.annotations).toEqual([]);
    expect(before).toMatchObject({image_id: 'part', image_width: 256, image_height: 256});

    await stage(page, 1);
    const focus = page.getByRole('button', {name: '집중 편집', exact: true});
    if (await focus.getAttribute('aria-pressed') !== 'true') await focus.click();
    // The image must really decode and Fit into the live focus layout before using 1:1 image coordinates.
    await expect.poll(async () => Number((await page.getByTestId('canvas-hud').innerText())
      .match(/배율\s*([\d.]+)\s*%/)?.[1] || 0)).toBeGreaterThan(100);
    await page.getByTitle('실제 크기 (1:1)', {exact: true}).click();
    await expect(page.getByTestId('canvas-hud')).toContainText('100%');
    // An unlabeled detection source has no owned defect class: the visible default is defect (id 1).
    const category = page.getByRole('button', {name: 'defect', exact: true});
    await expect(category).toHaveAttribute('aria-pressed', 'true');
    await category.click();
    await expect(category).toHaveAttribute('aria-pressed', 'true');
    await page.getByTitle('바운딩 박스 (BBox - 2)', {exact: true}).click();
    const canvas = await page.locator('[data-canvas-container]').boundingBox();
    expect(canvas).not.toBeNull();
    const point = (x: number, y: number) => ({x: canvas!.x + (canvas!.width - 256) / 2 + x,
      y: canvas!.y + (canvas!.height - 256) / 2 + y});
    const from = point(32, 40), to = point(144, 168);
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, {steps: 8});
    await page.mouse.up();
    await expect(page.getByRole('button', {name: 'defect 1', exact: true})).toHaveAttribute('aria-pressed', 'true');
    const savedResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/annotations/save'
      && response.request().method() === 'POST' && response.request().postDataJSON()?.image_path === imagePath);
    await page.getByRole('button', {name: '변경 저장', exact: true}).click();
    const response = await savedResponse;
    const posted = response.request().postDataJSON();
    if (typeof posted.lease_token === 'string') evidence.redact(posted.lease_token);
    const saved = await checked(response);
    expect(posted).toMatchObject({image_id: 'part', image_path: imagePath, image_width: 256, image_height: 256});
    expect(Number.isSafeInteger(posted.expected_revision)).toBe(true);
    expect(posted.annotations).toHaveLength(1);
    const expectedBox: [number, number, number, number] = [32, 40, 144, 168];
    expect(posted.annotations[0]).toMatchObject({type: 'bbox', label: 'defect', category_id: 1, bbox: expectedBox});
    expect(saved).toMatchObject({status: 'saved', image_id: 'part', count: 1, mask_generated: false});
    expect(saved.metadata.revision).toBeGreaterThan(posted.expected_revision);
    const stored = await api(query);
    expect(stored.annotations).toHaveLength(1);
    expect(stored.annotations[0]).toMatchObject({id: posted.annotations[0].id, type: 'bbox', label: 'defect', category_id: 1, bbox: expectedBox});
    expect(stored.metadata).toMatchObject({image_uuid: before.metadata.image_uuid, file_path: imagePath, content_hash: imageSha});
    expect(stored.metadata.revision).toBe(saved.metadata.revision);
    const sourceKey = sha(Buffer.from(source)).slice(0, 16);
    const annotationFile = path.join(current.annotations_dir, 'by_dataset', sourceKey, 'part.json');
    const annotationSha = fileSha(annotationFile);
    // Bbox-only detection labels are vector JSON; masks are generated for polygon/raster content.
    expect(stored.mask_file).toBeNull();
    expect(JSON.parse(fs.readFileSync(annotationFile, 'utf8')).annotations).toEqual(stored.annotations);
    await evidence.screenshot(page, 'browser-uploaded-image-real-bbox-saved');

    await page.reload();
    await login(page, server.origin, project.id, project.name, password);
    const reopenedProject = await api('/api/project/current');
    expect(reopenedProject).toMatchObject({id: project.id, task: 'detection', source_dataset_dir: source,
      active_labelset_id: current.active_labelset_id, annotations_dir: current.annotations_dir});
    await stage(page, 1);
    await expect(page.getByRole('button', {name: 'defect 1', exact: true})).toBeVisible();
    await expect(page.getByTitle('라벨 삭제', {exact: true})).toHaveCount(1);
    const reopened = await api(query);
    expect(reopened.annotations).toEqual(stored.annotations);
    expect(reopened).toMatchObject({image_id: 'part', image_width: 256, image_height: 256, mask_file: null});
    expect(reopened.metadata).toMatchObject({image_uuid: stored.metadata.image_uuid, file_path: imagePath, content_hash: imageSha});
    const reopenedImages = await api(imagesRoute);
    expect(reopenedImages.total).toBe(1);
    expect(reopenedImages.items[0]).toMatchObject({file_name: 'part.png', file_path: imagePath, width: 256, height: 256});
    expect(fileSha(imagePath)).toBe(imageSha);
    expect(fileSha(annotationFile)).toBe(annotationSha);
    expect(annotationPosts).toHaveLength(1);
    expect(modelRequests).toEqual([]);
    const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
    for (const secret of [password, account.token, server.backend.token]) expect(storage).not.toContain(secret);
    const cookies = await page.context().cookies(server.origin);
    expect(cookies.find(cookie => cookie.name === 'vision_session')).toMatchObject({httpOnly: true, secure: true, sameSite: 'Strict'});
    await evidence.screenshot(page, 'browser-uploaded-image-bbox-after-reload-login');
    evidence.note('browser_upload_annotation_roundtrip', {project_id: project.id, source, labelset_id: current.active_labelset_id,
      archive_sha256: fileSha(archive), image_path: imagePath, image_sha256: imageSha, imported_images: importedImages,
      adoption, posted, saved, stored, reopened, annotation_file: annotationFile, annotation_sha256: annotationSha,
      mask_generated: false, expected_bbox: expectedBox, default_class: {name: 'defect', id: 1},
      actual_ui_zip_upload: true, actual_ui_explicit_adoption: true, actual_canvas_draw: true,
      actual_annotation_save_post: true, actual_reload_relogin: true, original_image_unchanged: true,
      saved_annotation_unchanged: true, model_requests: modelRequests,
      synthetic_only: true, representative_annotation_quality: false, model_training: false});
    for (const file of [archive, imagePath, annotationFile]) evidence.addFile(file);
  } finally {
    try {
      evidence.note('browser_annotation_owned_server_stop', await server.close());
    } finally {
      evidence.addFile(server.backend.logs.stdout);
      evidence.addFile(server.backend.logs.stderr);
    }
  }
});
