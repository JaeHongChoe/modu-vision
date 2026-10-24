import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {createProject, pickDataset} from './qa/appFlow';

// Automated feature verification only. Self-reported milestones never prove a
// novice completed a workflow, produced correct labels or met a usability target.
test('local first-use study survives setup and guide closure, exports no private input and seals changed scope', async ({page, renderer, workspace, evidence}) => {
  await page.setViewportSize({width: 2560, height: 1440});
  const privateName = 'Study private project sentinel 7319';
  const originals = workspace.images.map(image => ({file: image.path, hash: image.sha256}));
  const external: string[] = [], forbidden: string[] = [], pageErrors: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.route(/^https?:\/\//, async route => {
    const request = route.request(), address = new URL(request.url());
    if (address.origin !== renderer.origin) {external.push(address.origin); await route.abort('blockedbyclient'); return;}
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method()) &&
      /^\/api\/(?:annotations(?:\/|$)|label-candidates|label-suggestions|labeling-jobs|training\/start|assistant(?:\/|$)|flowchart\/run|inspections(?:\/|$))/.test(address.pathname)) {
      forbidden.push(request.method() + ' ' + address.pathname); await route.abort('blockedbyclient'); return;
    }
    await route.fallback();
  });
  await installDesktopHostShim(page, renderer.port);
  // Only the native folder dialog answer is a fixture; import, scope binding,
  // stores, observers and the JSON download are the production implementations.
  await page.goto(renderer.url);
  await page.evaluate(folder => {
    if (!window.api) throw new Error('Desktop host fixture must be installed first');
    window.api.selectFolder = async () => folder;
  }, workspace.dataset);
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toBeVisible();
  const guide = page.getByRole('dialog', {name: '처음 시작하기', exact: true});
  const study = guide.getByRole('region', {name: '처음 사용자 로컬 측정', exact: true});
  const openGuide = async () => {
    if (!await guide.isVisible()) await page.getByRole('button', {name: '처음 시작 안내', exact: true}).click();
    if (!await study.isVisible()) await guide.getByText('시간·조작 수 직접 기록 (선택)', {exact: false}).click();
    await expect(study).toBeVisible();
  };
  await openGuide();
  await study.getByLabel('측정 방식', {exact: true}).selectOption('automated');
  await study.getByLabel('측정 과제', {exact: true}).selectOption('first_flow');
  await study.getByRole('button', {name: '로컬 측정 시작', exact: true}).click();
  const began = Date.now();
  await guide.getByRole('button', {name: '처음 시작하기 닫기', exact: true}).click();
  await createProject(page, privateName, '분류');
  await pickDataset(page);
  await expect(page.getByRole('button', {name: '전체 (2)', exact: true})).toBeVisible();
  await openGuide();
  await study.getByRole('button', {name: '현재 프로젝트·폴더로 고정', exact: true}).click();
  await study.getByRole('button', {name: '기록 끝내기', exact: true}).click();
  const downloadReport = async () => {
    const pending = page.waitForEvent('download');
    await study.getByRole('button', {name: '측정 JSON 저장', exact: true}).click();
    const download = await pending;
    expect(download.suggestedFilename()).toBe('modu-first-use-study.json');
    const destination = path.join(workspace.root, crypto.randomUUID() + '.json');
    await download.saveAs(destination);
    return JSON.parse(fs.readFileSync(destination, 'utf8'));
  };
  const first = (await downloadReport()).sessions[0];
  expect(first).toMatchObject({mode: 'automated', task: 'first_flow', status: 'completed', binding: 'bound',
    acceptance: 'unverified', target_assessment: 'unverified', external_actions: {source: 'unobserved', count: null}});
  expect(first.elapsed_ms).toBeGreaterThan(0);
  expect(first.elapsed_ms).toBeLessThanOrEqual(Date.now() - began + 1000);
  expect(first.workflow_actions.pointer).toBeGreaterThan(0);
  expect(first.recorder_actions.pointer).toBeGreaterThan(0);
  expect(first.build.build_sha).toBeNull();

  // The same recording controls also support honest per-image comparisons.
  // These are automated self-reported markers, without drawing/adopting labels.
  await study.getByLabel('측정 방식', {exact: true}).selectOption('automated');
  await study.getByLabel('측정 과제', {exact: true}).selectOption('manual_label');
  await study.getByRole('button', {name: '로컬 측정 시작', exact: true}).click();
  await study.getByRole('button', {name: '현재 프로젝트·폴더로 고정', exact: true}).click();
  await study.getByRole('button', {name: '이미지 1장 완료로 기록', exact: true}).click();
  await study.getByRole('button', {name: '기록 끝내기', exact: true}).click();
  await study.getByLabel('측정 과제', {exact: true}).selectOption('assisted_draft');
  await study.getByRole('button', {name: '로컬 측정 시작', exact: true}).click();
  await study.getByRole('button', {name: '현재 프로젝트·폴더로 고정', exact: true}).click();
  await study.getByRole('button', {name: '이미지 1장 완료로 기록', exact: true}).click();
  await guide.getByRole('button', {name: '처음 시작하기 닫기', exact: true}).click();
  await createProject(page, 'Study next private scope 9251', '분류');
  await openGuide();
  const report = await downloadReport();
  expect(report.acceptance).toBe('unverified');
  expect(report.sessions).toHaveLength(3);
  expect(report.sessions[0]).toEqual(first);
  expect(report.sessions[1]).toMatchObject({mode: 'automated', task: 'manual_label', status: 'completed', images_completed_self_reported: 1});
  expect(report.sessions[2]).toMatchObject({mode: 'automated', task: 'assisted_draft', status: 'aborted', images_completed_self_reported: 1});
  expect(['scope_changed', 'scope_unavailable']).toContain(report.sessions[2].reason);
  for (const session of report.sessions) {
    expect(session.acceptance).toBe('unverified');
    expect(session.target_assessment).toBe('unverified');
    expect(session.build.build_sha).toBeNull();
  }
  const raw = JSON.stringify(report);
  for (const value of [privateName, 'Study next private scope 9251', workspace.root, workspace.dataset, ...originals.map(row => row.file)]) expect(raw).not.toContain(value);
  const persisted = await page.evaluate(() => Object.keys(localStorage));
  expect(persisted.filter(key => /study|first.use/i.test(key))).toEqual([]);
  for (const original of originals) expect(crypto.createHash('sha256').update(fs.readFileSync(original.file)).digest('hex')).toBe(original.hash);
  expect(external).toEqual([]); expect(forbidden).toEqual([]); expect(pageErrors).toEqual([]);
  await study.scrollIntoViewIfNeeded();
  await evidence.screenshot(page, 'local-study-2560-automated-partial');
  await page.setViewportSize({width: 1024, height: 768});
  await study.scrollIntoViewIfNeeded();
  const bounds = await study.boundingBox();
  expect(bounds).not.toBeNull(); expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(1024);
  await expect(guide.getByRole('button', {name: '처음 시작하기 닫기', exact: true})).toBeVisible();
  await expect(study.getByRole('button', {name: '측정 JSON 저장', exact: true})).toBeVisible();
  await evidence.screenshot(page, 'local-study-1024-automated-partial');
  evidence.note('local_first_use_study', {actual_renderer_store_backend: true, native_folder_dialog: 'controlled answer',
    automated_feature_verification: true, human_usability_acceptance: 'unverified', field_model_quality: 'unverified',
    report, external_requests: external, forbidden_writes: forbidden, original_hashes_unchanged: true});
});
