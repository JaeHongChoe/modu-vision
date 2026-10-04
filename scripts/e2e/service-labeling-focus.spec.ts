import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import type { Page } from '@playwright/test';
import { expect, test } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';

// UI finish from the native app QA (Codex): on a 1440x900 window the stacked helper panels above the labeling canvas
// left it a small strip. Focus editing folds them; the canvas itself (not only its area) takes the height and an image
// shown at Fit is fitted again, the choice survives a reload, and the label set, the classes, the team row with the
// shared editing lock and a label load error stay. Browser project with the actual backend and the test-only desktop
// host shim; not native app QA.

const openLabeling = async (page: Page, project: string) => {
  // The first project sync sets the step from the remembered one: click the step only once the project is open.
  await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText(project, { timeout: 60_000 });
  await page.getByRole('button', { name: /02.*라벨링/ }).click();
  await expect(page.locator('[data-labeling-work-area]')).toBeVisible({ timeout: 60_000 });
};

const canvasHeight = async (page: Page) => (await page.locator('[data-canvas-container] canvas').first().boundingBox())!.height;
// A large image (its Fit is not clamped at 250 % in either layout, unlike the 32x32 harness images), written here.
const CRC = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc32 = (bytes: Buffer) => { let c = 0xffffffff; for (const x of bytes) c = CRC[(c ^ x) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
const pngChunk = (type: string, data: Buffer) => {
  const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]); const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
};
function largePng(width: number, height: number): Buffer {
  const plain = Buffer.alloc(1 + width * 3, 160); plain[0] = 0;
  const band = Buffer.from(plain); band.fill(40, 1 + Math.floor(width / 4) * 3, 1 + Math.floor(width / 2) * 3);
  const rows = Array.from({ length: height }, (_, y) => (y > height / 4 && y < height / 2 ? band : plain));
  const header = Buffer.alloc(13); header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), pngChunk('IHDR', header),
    pngChunk('IDAT', zlib.deflateSync(Buffer.concat(rows), { level: 1 })), pngChunk('IEND', Buffer.alloc(0))]);
}

// The HUD's scale (percent of the image's pixels per screen pixel).
const hudScale = (page: Page) => page.evaluate(() => {
  const label = [...document.querySelectorAll('span')].find(span => span.textContent === 'scale');
  return Number(label?.nextElementSibling?.textContent);
});

test('focus editing gives the labeling canvas the window height, refits the image and is remembered', async ({ page, renderer, workspace, evidence }) => {
  test.setTimeout(240_000);
  // The large image opens first; the harness images follow (the label load error step moves to the next one).
  const source = path.join(workspace.root, 'focus-source');
  fs.mkdirSync(path.join(source, 'ok'), { recursive: true });
  fs.writeFileSync(path.join(source, 'ok', '0000-large-4096x2732.png'), largePng(4096, 2732));
  for (const image of workspace.images) fs.copyFileSync(image.path, path.join(source, 'ok', `${image.label}.png`));
  const created = await page.request.post(`${renderer.origin}/api/project/create`, { data: { name: 'Labeling focus', task: 'segmentation' } });
  expect(created.ok()).toBe(true);
  expect((await page.request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: source } })).ok()).toBe(true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  await openLabeling(page, 'Labeling focus');
  const toggle = page.getByRole('button', { name: /^집중 편집/ });
  // One name for the switch; whether focus editing is on is its pressed state.
  const focusSwitch = (pressed: boolean) => page.getByRole('button', { name: '집중 편집', exact: true, pressed });
  // Measured only once the image is loaded and fitted (the HUD leaves its default 100 %).
  await expect.poll(() => hudScale(page), { timeout: 90_000, message: 'the large image is fitted' }).toBeLessThan(100);
  await page.waitForTimeout(500);
  const before = { canvas: await canvasHeight(page), scale: await hudScale(page) };
  await evidence.screenshot(page, 'labeling-full-layout');

  // Switched on in place, with no pointer input after it: the canvas element grows and the image is fitted to it.
  await page.mouse.move(5, 5);
  await toggle.click();
  await expect.poll(() => canvasHeight(page), { message: 'the canvas element itself takes the height' }).toBeGreaterThan(before.canvas * 2);
  await expect.poll(() => hudScale(page), { message: 'the image shown at Fit is fitted again to the larger canvas' }).toBeGreaterThan(before.scale * 1.5);
  await expect(focusSwitch(true)).toBeVisible();
  const focused = { canvas: await canvasHeight(page), scale: await hudScale(page) };
  // What focus editing keeps: the label set in use, the classes, and compact rows.
  await expect(page.getByLabel('레이블셋 목록 새로고침')).toBeVisible();
  await expect(page.getByTitle('Add new category')).toBeVisible();
  await expect(page.getByText('활성 레이블셋의 편집 라벨만 학습·평가에 사용합니다', { exact: false })).toHaveCount(0);
  const filmstrip = page.locator('[data-labeling-filmstrip]');
  const focusFilmstrip = (await filmstrip.boundingBox())!.height;
  evidence.note('labeling_canvas', { viewport: [1440, 900], image: [4096, 2732], full: { canvas: Math.round(before.canvas), scale: before.scale },
    focus: { canvas: Math.round(focused.canvas), scale: focused.scale } });
  await evidence.screenshot(page, 'labeling-focus-layout');

  // A label load error is shown in focus editing too.
  await page.route('**/api/annotations/**', route => route.request().method() === 'GET' ? route.fulfill({ status: 500, body: '{"detail":"probe"}' }) : route.continue());
  await page.getByLabel('Next image or page').click();
  await expect(page.getByRole('alert').filter({ hasText: '기존 라벨 조회 실패' })).toBeVisible();
  await page.unroute('**/api/annotations/**');

  await page.reload();
  await openLabeling(page, 'Labeling focus');
  await expect(focusSwitch(true)).toBeVisible();
  await expect.poll(() => canvasHeight(page), { message: 'a remembered focus layout opens with the tall canvas' }).toBeGreaterThan(before.canvas * 2);
  await focusSwitch(true).click();
  await expect(focusSwitch(false)).toBeVisible();
  await expect.poll(() => canvasHeight(page)).toBeLessThan(focused.canvas);
  expect(focusFilmstrip, 'in focus editing the filmstrip is one slim row').toBeLessThan((await filmstrip.boundingBox())!.height / 2);
});

type Lease = { owner: string; expires_at: number } | null;

test('the shared editing lock is taken, renewed and kept in focus editing, and a remembered focus layout can save', async ({ page, renderer, workspace, evidence }) => {
  test.setTimeout(240_000);
  const api = (path: string) => `${renderer.origin}${path}`;
  expect((await page.request.post(api('/api/project/create'), { data: { name: 'Focus team lock', task: 'classification' } })).ok()).toBe(true);
  expect((await page.request.put(api('/api/project/update'), { data: { source_dataset_dir: workspace.dataset } })).ok()).toBe(true);
  const team = await (await page.request.get(api('/api/team-data'))).json();
  const changed = await page.request.put(api('/api/team-data/settings'), { data: { expected_revision: team.settings.revision, actor: 'lock-owner', changes: { editing_enabled: true } } });
  expect(changed.ok(), await changed.text()).toBe(true);
  const serverLease = async (): Promise<Lease> => {
    const queue = await (await page.request.get(api('/api/team-data/queue?limit=30'))).json();
    return queue.items.find((row: { team?: { edit_lease: Lease } }) => row.team?.edit_lease)?.team.edit_lease ?? null;
  };
  const save = async () => {
    const saved = page.waitForResponse(response => response.url().includes('/api/annotations/save'));
    await page.locator('button', { hasText: /^(Saved|Save Changes)$/ }).first().click();
    return (await saved).status();
  };
  await page.setViewportSize({ width: 1440, height: 900 });
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  await openLabeling(page, 'Focus team lock');
  const toggle = page.getByRole('button', { name: '집중 편집', exact: true });
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');

  // The team row stays in focus editing; the worker name is set once in the team dialog, the lock is taken in the row.
  const row = page.getByRole('button', { name: /팀 작업 · 라벨 기준·검수/ }).locator('xpath=../..');
  await expect(row.getByText('편집 시작 필요')).toBeVisible({ timeout: 60_000 });
  await row.getByRole('button', { name: /팀 작업 · 라벨 기준·검수/ }).click();
  const dialog = page.getByRole('dialog', { name: '팀 데이터 작업' });
  await dialog.getByLabel('팀 작업자 이름').fill('lock-owner');
  await dialog.getByRole('button', { name: '팀 데이터 작업 닫기' }).click();
  await row.getByRole('button', { name: '편집 시작', exact: true }).click();
  await expect(row.getByText('현재 이미지 편집 중')).toBeVisible();
  const taken = await serverLease();
  expect(taken?.owner).toBe('lock-owner');

  // Still in focus editing after more than the 30 s renewal period: renewed, and a save passes the server's lock.
  await page.waitForTimeout(35_000);
  const renewed = await serverLease();
  expect(renewed!.expires_at, 'the lock was renewed in focus editing').toBeGreaterThan(taken!.expires_at);
  expect(await save(), 'a save in focus editing passes the lock').toBe(200);

  // Focus off and on again keeps the same lock: the row still holds it and a save passes.
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(row.getByText('현재 이미지 편집 중')).toBeVisible();
  expect(await save()).toBe(200);
  await row.getByRole('button', { name: '편집 종료', exact: true }).click();
  await expect(row.getByText('편집 시작 필요')).toBeVisible();
  await expect.poll(serverLease).toBeNull();
  await evidence.screenshot(page, 'labeling-focus-team-row');

  // A fresh load with focus remembered loads the team settings: the lock can be taken in the row and a save passes.
  await page.reload();
  await openLabeling(page, 'Focus team lock');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(row.getByText('편집 시작 필요')).toBeVisible({ timeout: 60_000 });
  await row.getByRole('button', { name: /팀 작업 · 라벨 기준·검수/ }).click();
  const actorField = dialog.getByLabel('팀 작업자 이름');
  if ((await actorField.inputValue()) !== 'lock-owner') await actorField.fill('lock-owner');
  await dialog.getByRole('button', { name: '팀 데이터 작업 닫기' }).click();
  await row.getByRole('button', { name: '편집 시작', exact: true }).click();
  await expect(row.getByText('현재 이미지 편집 중')).toBeVisible();
  expect(await save(), 'a remembered focus layout can save').toBe(200);
  evidence.note('labeling_focus_lock', { taken: taken!.expires_at, renewed: renewed!.expires_at });
});
