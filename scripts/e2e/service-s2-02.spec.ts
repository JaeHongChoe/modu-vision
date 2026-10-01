import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from './fixtures/test';

// Actual renderer + owned backend. Historical metadata is synthetic; no model
// training, quality approval, field deployment or Windows DPI claim is made.
for (const viewport of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
  test(`impact workspace uses accessible shared controls at ${viewport.width}x${viewport.height}`, async ({ page, request, renderer, workspace, evidence }) => {
    await page.setViewportSize(viewport);
    expect((await request.post(`${renderer.origin}/api/project/create`, { data: { name: 'Design foundation fixture', task: 'segmentation' } })).status()).toBe(200);
    const project = await (await request.get(`${renderer.origin}/api/project/current`)).json();
    expect(fs.realpathSync(project.project_dir).startsWith(fs.realpathSync(workspace.projects) + path.sep)).toBe(true);
    for (const [job, metadata] of Object.entries({
      job_affected: { task: 'segmentation', training_config: { augmentation_profile: 'industrial' }, augmentation_contract: { version: 0, target_sync: false } },
      job_unknown: { task: 'segmentation' },
      job_unaffected: { task: 'segmentation', training_config: { augmentation_profile: 'photometric' } },
    })) {
      const directory = path.join(project.models_dir, job);
      fs.mkdirSync(directory, { recursive: true });
      fs.writeFileSync(path.join(directory, 'model_meta.json'), JSON.stringify(metadata));
      fs.writeFileSync(path.join(directory, 'best_model.pt'), 'synthetic metadata-only fixture');
    }
    let release: (() => void) | undefined;
    await page.route('**/api/model-operations/legacy-impact', async route => {
      await new Promise<void>(resolve => { release = resolve; });
      await route.continue();
    }, { times: 1 });
    await page.goto(renderer.url, { waitUntil: 'domcontentloaded' });
    await expect(page.getByTitle('프로젝트 관리', { exact: true }), 'the backend current project opens on load').toContainText('Design foundation fixture');
    await evidence.screenshot(page, 'impact-before-open');
    const opener = page.getByRole('button', { name: '데이터·모델 변경 영향 확인', exact: true }).first();
    await expect(opener).toBeVisible({ timeout: 10000 });
    await opener.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: '데이터·모델 변경 영향' });
    await expect(dialog).toBeVisible({ timeout: 5000 });
    await expect(dialog.getByRole('status').filter({ hasText: '기존 기록을 확인하는 중' })).toBeVisible();
    await expect(dialog.getByRole('button', { name: '확인 중…' })).toBeDisabled();
    await expect.poll(() => Boolean(release)).toBe(true);
    release!();
    await expect(dialog.getByText('영향 확인 · 1')).toBeVisible();
    await expect(dialog.getByText('근거 부족 · 1')).toBeVisible();
    await expect(dialog.getByText('해당 문제 영향 없음 · 1')).toBeVisible();
    await expect(dialog.getByText('품질 승인·현재 배포 자격을 확인한 결과가 아닙니다.', { exact: true })).toBeVisible();
    await expect(dialog.getByRole('region', { name: '기존 모델·승인 영향 조사' }).getByRole('article').getByText('job_affected', { exact: true })).toBeVisible();
    await dialog.getByLabel('기록 검색').fill('no-such-record');
    await expect(dialog.getByText('검색 조건에 맞는 기록이 없습니다.', { exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: '검색 초기화' }).click();
    const box = await dialog.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    const close = dialog.getByRole('button', { name: '데이터·모델 변경 영향 닫기' });
    await close.focus();
    await page.keyboard.press('Shift+Tab');
    expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
    await expect(opener).toBeFocused();
    await opener.press('Enter');
    await expect(dialog).toBeVisible();
    await page.route('**/api/model-operations/legacy-impact', route => route.fulfill({ status: 503, json: { detail: '기록 저장소 연결 실패' } }), { times: 1 });
    await dialog.getByRole('button', { name: '현재 입력 다시 확인', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('기록 저장소 연결 실패');
    await expect(dialog.getByRole('region', { name: '현재 입력과 저장 이력' })).toBeVisible();
    await dialog.getByRole('button', { name: '기존 기록 다시 확인' }).click();
    await expect(dialog.getByText('영향 확인 · 1')).toBeVisible();
    await expect(dialog.getByRole('alert')).toHaveCount(0);
    evidence.note('scope', { viewport, actual_renderer: true, actual_backend: true, historical_metadata: 'synthetic', windows_native_dpi: false, quality_approval: false });
    await evidence.screenshot(page, `design-foundation-${viewport.width}`);
    await page.mouse.click(3, 3);
    await expect(dialog).not.toBeVisible();
    await expect(opener).toBeFocused();
    await page.getByRole('button', { name: '데이터·판정 이력', exact: true }).click();
    const parent = page.getByRole('dialog', { name: '데이터·모델·판정 이력', exact: true });
    const nestedOpener = parent.getByRole('button', { name: '데이터·모델 변경 영향 확인', exact: true });
    await nestedOpener.click();
    await dialog.getByLabel('기록 검색').click({ timeout: 5000 });
    await dialog.getByLabel('기록 검색').fill('job_unknown', { timeout: 5000 });
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
    await expect(parent).toBeVisible();
    await expect(nestedOpener).toBeFocused();
  });
}

for (const resource of ['current', 'legacy'] as const) {
  test(`closed ${resource} request cannot replace newer evidence after reopening`, async ({ page, request, renderer, evidence }) => {
    expect((await request.post(`${renderer.origin}/api/project/create`, { data: { name: 'Impact request order fixture', task: 'classification' } })).status()).toBe(200);
    const endpoint = resource === 'current' ? '/api/provenance/impact' : '/api/model-operations/legacy-impact';
    const report = (reason: string) => resource === 'current'
      ? { models: [{ job_id: 'fixture-model', reason }], flows: [], model_evaluations: [], flow_evaluations: [], approvals: [], packages: [], required_actions: [] }
      : { affected: [{ issue: 'training_geometry', reason, source_ids: { job_id: 'fixture-model' }, lineage: {}, scope_matches: true }], unknown: [], unaffected: [], next_action: [] };
    let calls = 0;
    let release: (() => Promise<void>) | undefined;
    await page.route(`**${endpoint}`, async route => {
      calls++;
      if (calls === 1) {
        await new Promise<void>(resolve => { release = async () => { await route.fulfill({ json: report('OLD superseded evidence') }); resolve(); }; });
      } else await route.fulfill({ json: report('NEW current evidence') });
    });
    await page.goto(renderer.url);
    await expect(page.getByTitle('프로젝트 관리', { exact: true }), 'the backend current project opens on load').toContainText('Impact request order fixture');
    const opener = page.getByRole('button', { name: '데이터·모델 변경 영향 확인', exact: true }).first();
    await opener.click();
    await expect.poll(() => Boolean(release)).toBe(true);
    await page.keyboard.press('Escape');
    await opener.click();
    const dialog = page.getByRole('dialog', { name: '데이터·모델 변경 영향', exact: true });
    if (resource === 'current') await dialog.getByText('학습 모델 · 1', { exact: true }).click();
    await expect(dialog.getByText('NEW current evidence', { exact: true })).toBeVisible();
    const completed = page.waitForResponse(response => new URL(response.url()).pathname === endpoint);
    await release!();
    await (await completed).finished();
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(dialog.getByText('NEW current evidence', { exact: true })).toBeVisible();
    await expect(dialog.getByText('OLD superseded evidence', { exact: true })).toHaveCount(0);
    evidence.note('request_order', { resource, calls, endpoint_transport_fixture: true, older_response_ignored: true });
  });
}
