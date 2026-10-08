import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {Page, Route} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

type Api = (route: string, body?: unknown, method?: string) => Promise<any>;
const sha = (raw: Buffer) => createHash('sha256').update(raw).digest('hex');

// These are unsent opinions and a private local draft. No label is approved.
async function exercise(page: Page, w: Workspace, e: Evidence, api: Api, native: boolean, url?: string) {
  const source = path.join(w.root, 'review-draft-source'); fs.mkdirSync(source);
  const image = path.join(source, 'part.png'); fs.writeFileSync(image, png(256, 3, (x, y) => [x, y, 100]));
  const imageHash = sha(fs.readFileSync(image));
  const project = await api('/api/project/create', {name: 'Owned review draft lifecycle', task: 'segmentation'});
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation'});
  await api('/api/team-data/books', {expected_version: 0, actor: 'fixture-owner', title: 'Controlled draft book', categories: [
    {id: 0, name: 'OK', color: '#10b981'}, {id: 2, name: 'Scratch', color: '#f59e0b'},
  ]});
  await api('/api/team-data/settings', {expected_revision: 1, actor: 'fixture-owner', changes: {
    review_enabled: true, required_reviews: 2, prevent_self_review: true, approved_only_training: true,
  }}, 'PUT');
  const saved = await api('/api/annotations/save', {image_id: 'part', image_path: image, image_width: 256,
    image_height: 256, actor: 'fixture-labeler', annotations: [
      {id: 'original-label', type: 'bbox', label: 'Scratch', category_id: 2, bbox: [2, 3, 20, 21]},
    ]});
  const uuid = saved.metadata.image_uuid, imageRoute = '/api/team-data/images/' + uuid;
  const query = '/api/annotations/part?file_path=' + encodeURIComponent(image);
  // One explicit API setup vote leaves both review controls available. It is
  // separate from the zero unsent UI votes tested below and from human truth.
  const originalVote = await api(imageRoute + '/review', {expected_revision: saved.metadata.revision,
    actor: 'fixture-original-reviewer', decision: 'approve', reason: 'Synthetic original opinion'});
  expect(originalVote.image.team.reviews).toHaveLength(1);
  expect(originalVote.image.team.review_status).toBe('pending');
  const annotationDir = path.join(project.annotations_dir, 'by_dataset', sha(Buffer.from(fs.realpathSync(source))).slice(0, 16));
  const labelFile = path.join(annotationDir, 'part.json'), maskFile = path.join(annotationDir, 'masks', 'part.png');
  const metadataFile = path.join(annotationDir, 'metadata', 'workflow.json');
  const rawFiles = () => {
    for (const file of [labelFile, metadataFile]) {
      expect(fs.lstatSync(file).isFile()).toBe(true); expect(fs.lstatSync(file).isSymbolicLink()).toBe(false);
    }
    return {annotation_json: {path: labelFile, sha256: sha(fs.readFileSync(labelFile))},
      metadata_json: {path: metadataFile, sha256: sha(fs.readFileSync(metadataFile))},
      mask: fs.existsSync(maskFile) ? {path: maskFile, sha256: sha(fs.readFileSync(maskFile))} : null,
      source: {path: image, sha256: sha(fs.readFileSync(image))}};
  };
  const read = async () => ({annotation: await api(query), image: await api(imageRoute),
    workspace: await api('/api/team-data'), queue: await api('/api/team-data/queue?offset=0&limit=30'), files: rawFiles()});
  if (url) await page.goto(url); else await page.reload();
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
  await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(1).click();
  await page.getByRole('button', {name: '집중 편집', exact: true}).click();
  const team = page.getByRole('dialog', {name: '팀 데이터 작업', exact: true});
  const work = team.getByRole('region', {name: '현재 이미지 팀 작업', exact: true});
  const actor = team.getByLabel('팀 작업자 이름', {exact: true}), reason = work.getByLabel('검수 이유', {exact: true});
  const openTeam = async () => {
    await page.getByRole('button', {name: '팀 작업 · 라벨 기준·검수', exact: true}).click();
    await expect(team).toBeVisible(); await expect(work).toContainText('fixture-original-reviewer');
  };
  const closeTeam = async () => {
    await team.getByRole('button', {name: '팀 데이터 작업 닫기', exact: true}).click(); await expect(team).toHaveCount(0);
  };
  await openTeam(); const baseline = await read(); expect(baseline.files.mask).toBeNull();
  expect(baseline.annotation.metadata.team.reviews).toEqual(originalVote.image.team.reviews);
  const writes: Array<{method: string; path: string}> = [];
  const observe = (request: any) => {
    const pathname = new URL(request.url()).pathname;
    if (request.method() !== 'GET' && /^\/api\/(team-data|annotations|training|dataset\/metadata)(\/|$)/.test(pathname))
      writes.push({method: request.method(), path: pathname});
  };
  const unchanged = async () => {expect(await read()).toEqual(baseline); expect(writes).toEqual([]);};
  const lifecycle: Array<{action: string; unsent_reason: string; cancel_no_POST: boolean; reload_reopen_no_POST: boolean}> = [];
  const compareResponses: Array<{status: number; original_GET_dispatched: boolean}> = [];
  const compareDialog = page.getByRole('dialog', {name: '서버 라벨과 로컬 초안 비교', exact: true});
  const compareButton = page.getByRole('button', {name: '저장 전 초안 비교·복구', exact: true});
  const closeCompare = async () => {
    await compareDialog.getByRole('button', {name: '서버 라벨과 로컬 초안 비교 닫기', exact: true}).click();
    await expect(compareDialog).toHaveCount(0);
  };
  const drafts = () => page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('modu-annotation-draft:v1:')));
  let controlledGETs = 0;
  const unavailable = async (route: Route) => {
    const request = route.request(), target = new URL(request.url());
    expect(request.method()).toBe('GET'); expect(target.pathname).toBe('/api/annotations/part');
    expect(target.searchParams.get('file_path')).toBe(image); controlledGETs++;
    await route.fulfill({status: 503, json: {detail: 'Controlled draft comparison GET unavailable'}});
  };
  page.on('request', observe);
  try {
    for (const [action, name] of [['approve-vote', '승인 표 제출'], ['reject-vote', '반려 표 제출']] as const) {
      await actor.fill('fixture-unsent-reviewer'); const unsent = 'Unsent synthetic ' + action; await reason.fill(unsent);
      const control = work.getByRole('button', {name, exact: true}); await expect(control).toBeEnabled();
      await e.screenshot(page, `${native ? 'native' : 'browser'}-${action}-valid-unsent-form`);
      await closeTeam(); await unchanged();
      await page.reload(); await openTeam(); await expect(reason).toHaveValue('');
      await actor.fill('fixture-unsent-reviewer'); await reason.fill('Reopened but still unsent ' + action);
      await expect(control).toBeEnabled(); await unchanged();
      await e.screenshot(page, `${native ? 'native' : 'browser'}-${action}-original-vote-reopened-no-post`);
      lifecycle.push({action, unsent_reason: unsent, cancel_no_POST: true, reload_reopen_no_POST: true});
      await closeTeam();
      if (action === 'approve-vote') await openTeam();
    }
    const recovery = page.getByRole('region', {name: '라벨 연결 복구'});
    await expect(recovery).toBeVisible(); await expect(compareButton).toHaveCount(0); expect(await drafts()).toEqual([]);
    await unchanged(); await e.screenshot(page, `${native ? 'native' : 'browser'}-compare-empty-no-local-draft`);
    // Make the local draft through the actual canvas. No save or apply button
    // is clicked, and the original server labels and review ledger are pinned.
    await page.getByTitle('100% Zoom (1:1)', {exact: true}).click();
    await page.getByRole('button', {name: /^Scratch(?: \d+)?$/}).click();
    await page.getByTitle('바운딩 박스 (BBox - 2)', {exact: true}).click();
    const box = (await page.locator('[data-canvas-container]').boundingBox())!;
    const point = (x: number, y: number) => ({x: box.x + (box.width - 256) / 2 + x, y: box.y + (box.height - 256) / 2 + y});
    const start = point(40, 40), end = point(80, 80);
    await page.mouse.move(start.x, start.y); await page.mouse.down(); await page.mouse.move(end.x, end.y, {steps: 6}); await page.mouse.up();
    await expect(recovery).toContainText('이 컴퓨터에 초안 보존됨');
    const entries = await drafts(); expect(entries).toHaveLength(1); const [draftKey, draftRaw] = entries[0];
    const local = JSON.parse(draftRaw); expect(local.image_path).toBe(image); expect(local.source_sha256).toBe(imageHash);
    expect(local.base_revision).toBe(baseline.annotation.metadata.revision); expect(local.annotations).toHaveLength(2);
    expect(local.annotations[1].bbox).toEqual([40, 40, 80, 80]); await unchanged();
    // A controlled private storage input carries a wrong source pin. The real
    // comparison must refuse it even after explicit confirmation is checked.
    const invalidRaw = JSON.stringify({...local, source_sha256: '0'.repeat(64)});
    await page.evaluate(({key, raw}) => localStorage.setItem(key, raw), {key: draftKey, raw: invalidRaw});
    await page.reload(); await expect(compareButton).toBeVisible();
    let reply = page.waitForResponse(r => r.request().method() === 'GET' && new URL(r.url()).pathname === '/api/annotations/part');
    await compareButton.click(); expect((await reply).status()).toBe(200); compareResponses.push({status: 200, original_GET_dispatched: true});
    await expect(compareDialog.getByRole('alert')).toContainText('원본 이미지 또는 크기가 달라');
    await compareDialog.getByRole('checkbox').check();
    await expect(compareDialog.getByRole('button', {name: '비교한 초안을 명시적으로 적용', exact: true})).toBeDisabled();
    await unchanged(); expect(await drafts()).toEqual([[draftKey, invalidRaw]]);
    await e.screenshot(page, `${native ? 'native' : 'browser'}-compare-invalid-source-pin-cannot-apply`); await closeCompare();
    await page.evaluate(({key, raw}) => localStorage.setItem(key, raw), {key: draftKey, raw: draftRaw});
    await page.reload(); await expect(compareButton).toBeVisible();
    await page.route('**/api/annotations/part?*', unavailable);
    reply = page.waitForResponse(r => r.request().method() === 'GET' && new URL(r.url()).pathname === '/api/annotations/part');
    await compareButton.click(); expect((await reply).status()).toBe(503); compareResponses.push({status: 503, original_GET_dispatched: false});
    await expect(compareDialog.getByRole('alert')).toContainText('Controlled draft comparison GET unavailable');
    await page.unroute('**/api/annotations/part?*', unavailable); expect(controlledGETs).toBe(1);
    await unchanged(); expect(await drafts()).toEqual([[draftKey, draftRaw]]);
    await expect(compareDialog.getByRole('button', {name: '비교한 초안을 명시적으로 적용', exact: true})).toHaveCount(0);
    await e.screenshot(page, `${native ? 'native' : 'browser'}-compare-503-retains-exact-local-draft`); await closeCompare();
    await page.reload(); await expect(compareButton).toBeVisible(); expect(await drafts()).toEqual([[draftKey, draftRaw]]);
    reply = page.waitForResponse(r => r.request().method() === 'GET' && new URL(r.url()).pathname === '/api/annotations/part');
    await compareButton.click(); expect((await reply).status()).toBe(200); compareResponses.push({status: 200, original_GET_dispatched: true});
    await expect(compareDialog).toContainText('같은 기준 버전');
    for (const [title, rows] of [['초안 작성 당시', local.base_annotations], ['현재 서버 라벨', baseline.annotation.annotations], ['보존된 로컬 초안', local.annotations]] as const) {
      const detail = compareDialog.locator('details').filter({hasText: title});
      await expect(detail).toHaveCount(1);
      expect(JSON.parse(await detail.locator('pre').innerText())).toEqual(rows);
    }
    await expect(compareDialog.getByRole('checkbox')).not.toBeChecked();
    await expect(compareDialog.getByRole('button', {name: '비교한 초안을 명시적으로 적용', exact: true})).toBeDisabled();
    await unchanged(); await e.screenshot(page, `${native ? 'native' : 'browser'}-compare-exact-draft-reopened-without-apply`);
    await closeCompare(); expect(await drafts()).toEqual([[draftKey, draftRaw]]); await unchanged();
    for (const file of [image, labelFile, metadataFile]) e.addFile(file);
    e.note('team_review_draft_lifecycle', {record_id: 'F024', actions: {'approve-vote': ['cancel', 'reopen'],
      'reject-vote': ['cancel', 'reopen'], 'compare-draft': ['empty', 'invalid', 'error', 'reopen']},
      project_id: project.id, image_uuid: uuid, baseline, final: await read(), lifecycle,
      explicit_fixture_vote_setup: {count: 1, response: originalVote, human_truth: false}, ui_writes: writes,
      compare: {empty_control_absent: true, local_created_by_actual_canvas: true, draft_key: draftKey,
        original_draft: local, original_raw_sha256: sha(Buffer.from(draftRaw)), invalid_source_sha256: '0'.repeat(64),
        invalid_raw_sha256: sha(Buffer.from(invalidRaw)), invalid_apply_disabled_after_confirmation: true,
        controlled_GET_503_count: controlledGETs, responses: compareResponses, original_draft_retained_exact: true,
        actual_reload_reopen: true, applied: false, saved: false}, actual_source_ui: true, source_electron: native,
      synthetic_review_controls: true, quality_or_human_truth_approved: false, actual_model_inference: false,
      installed_target_verified: false, gpu_used: false});
  } finally {
    page.off('request', observe);
    if (!page.isClosed()) await page.unroute('**/api/annotations/part?*', unavailable);
  }
}

test('unsent reviews and private draft comparison preserve original votes labels and metadata through reload', async ({page, renderer, workspace, evidence}) => {
  const api: Api = async (route, body, method) => {
    const response = await page.request.fetch(renderer.origin + route, {method: method || (body ? 'POST' : 'GET'), data: body});
    expect(response.ok(), await response.text()).toBe(true); return response.json();
  };
  await installDesktopHostShim(page, renderer.port); await exercise(page, workspace, evidence, api, false, renderer.url);
});
test('native unsent reviews and local comparison retain exact original votes labels metadata and draft', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, status = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body ? 'POST' : 'GET'),
      headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
    if (!response.ok) throw Error(`Owned draft fixture HTTP ${response.status}`); return response.json();
  }, {port: status.port, route, body, method});
  await exercise(page, workspace, evidence, api, true);
});
