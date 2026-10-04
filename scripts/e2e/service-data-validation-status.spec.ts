import fs from 'node:fs';
import path from 'node:path';
import type { Page } from '@playwright/test';
import { expect, test } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';

// Native app QA follow-up: the data step says "전체 검증 완료" once a full validation of the project's source is the
// active version, and only when that version read the whole source and still matches it. Browser project with the actual
// backend; the unreadable folder is made with POSIX permissions (not on Windows, where native QA covers file access).

const adopt = async (page: Page) => {
  await page.getByRole('button', { name: '검증된 데이터 버전', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '검증된 데이터 버전' });
  await dialog.getByRole('button', { name: '전체 검증 실행' }).click();
  const current = dialog.getByRole('region', { name: '현재 가져오기' });
  await expect(current).toContainText('검증 완료 · 채택 전', { timeout: 60_000 });
  await dialog.getByRole('button', { name: '이 버전 채택' }).click();
  await dialog.getByRole('button', { name: '채택 확인' }).click();
  await expect(current).toContainText('검증 완료 · 활성 버전', { timeout: 30_000 });
  await dialog.getByRole('button', { name: '검증된 데이터 버전 닫기' }).click();
};

test('the data step calls a validation complete only for an active version that read the whole, current source', async ({ page, renderer, workspace, evidence }) => {
  test.skip(process.platform === 'win32', 'an unreadable folder is made with POSIX permissions');
  test.setTimeout(240_000);
  const api = (p: string) => `${renderer.origin}${p}`;
  const source = path.join(workspace.root, 'status-source');
  const locked = path.join(source, 'locked');
  for (const label of ['ok', 'ng', 'locked']) fs.mkdirSync(path.join(source, label), { recursive: true });
  for (const image of workspace.images) {
    for (let copy = 0; copy < 3; copy += 1) fs.copyFileSync(image.path, path.join(source, image.label, `copy-${copy}.png`));
    fs.copyFileSync(image.path, path.join(locked, `hidden-${image.label}.png`));
  }
  fs.chmodSync(locked, 0o000);
  try {
    expect((await page.request.post(api('/api/project/create'), { data: { name: 'Data status', task: 'classification' } })).ok()).toBe(true);
    expect((await page.request.put(api('/api/project/update'), { data: { source_dataset_dir: source } })).ok()).toBe(true);
    await installDesktopHostShim(page, renderer.port);
    await page.goto(renderer.url);
    await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText('Data status', { timeout: 60_000 });
    await page.getByRole('button', { name: /01.*데이터 관리/ }).click();
    const status = page.getByRole('status').filter({ hasText: /빠른 확인|전체 검증|활성 버전/ }).first();
    await expect(status).toContainText('빠른 확인 범위가 일부입니다', { timeout: 60_000 });

    // Adopted with the step open, a version that could not read a folder is not called complete, and the quick
    // check's own gap stays.
    await adopt(page);
    await expect(status).toContainText('원본 일부를 읽지 못했습니다(읽지 못한 폴더 1곳)', { timeout: 15_000 });
    await expect(status).toContainText('빠른 확인 범위가 일부입니다');
    await expect(page.getByText('전체 검증 완료')).toHaveCount(0);
    await evidence.screenshot(page, 'data-status-unreadable-folder');

    // With the folder readable and validated again, the step says complete with the version's counts.
    fs.chmodSync(locked, 0o755);
    await page.reload();
    await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText('Data status', { timeout: 60_000 });
    await page.getByRole('button', { name: /01.*데이터 관리/ }).click();
    await adopt(page);
    const total = workspace.images.length * 4;
    await expect(status).toContainText(`전체 검증 완료 · 활성 버전`, { timeout: 15_000 });
    await expect(status).toContainText(`${total}장 중 유효 ${total}장.`);
    await evidence.screenshot(page, 'data-status-complete');

    // What macOS writes next to a file on a USB drive or SMB share (an AppleDouble "._" file) is no image of the source:
    // the quick import counts it as damaged, the validated inventory never read it, so the version still matches
    // (nqa2 review P2-1: the step compared counts defined differently and called an unchanged source different).
    fs.writeFileSync(path.join(source, 'ng', '._copy-0.png'), Buffer.from('0005160700020000', 'hex'));
    await page.reload();
    await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText('Data status', { timeout: 60_000 });
    await page.getByRole('button', { name: /01.*데이터 관리/ }).click();
    await expect(page.getByText('손상된 이미지 1건 감지')).toBeVisible({ timeout: 60_000 });
    await expect(status).toContainText(`전체 검증 완료 · 활성 버전`, { timeout: 15_000 });
    await expect(status).not.toContainText('다릅니다');
    await evidence.screenshot(page, 'data-status-apple-double');

    // The source changes after adoption: the step says the version is older than it, and how.
    fs.writeFileSync(path.join(source, 'ok', 'new-broken.png'), 'not an image');
    await page.reload();
    await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText('Data status', { timeout: 60_000 });
    await page.getByRole('button', { name: /01.*데이터 관리/ }).click();
    await expect(status).toContainText(`(${total}장)은 지금 원본(${total + 1}장)과 다릅니다(추가 1장)`, { timeout: 60_000 });
    await expect(page.getByText('전체 검증 완료')).toHaveCount(0);
    await evidence.screenshot(page, 'data-status-source-changed');

    // A failed read of the versions claims nothing.
    await page.route('**/api/dataset/revisions', route => route.fulfill({ status: 503, json: { detail: 'unavailable' } }));
    await page.reload();
    await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText('Data status', { timeout: 60_000 });
    await page.getByRole('button', { name: /01.*데이터 관리/ }).click();
    await expect(page.getByText(/손상된 이미지 [12]건 감지/)).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText('전체 검증 완료')).toHaveCount(0);
    await expect(page.getByText(/활성 버전 [0-9a-f]{12}/)).toHaveCount(0);
  } finally {
    try { fs.chmodSync(locked, 0o755); } catch { /* already readable */ }
  }
});

test('an unchanged excluded unreadable image stays validated and recovering it requires validation again', async ({ page, renderer, workspace, evidence }) => {
  test.skip(process.platform === 'win32', 'a read-denied file is made with POSIX permissions');
  test.setTimeout(240_000);
  const api = (p: string) => `${renderer.origin}${p}`;
  const source = path.join(workspace.root, 'read-error-source');
  for (const image of workspace.images) {
    const folder = path.join(source, image.label);
    fs.mkdirSync(folder, { recursive: true });
    for (let number = 0; number < 3; number += 1) fs.copyFileSync(image.path, path.join(folder, `copy-${number}.png`));
  }
  const denied = path.join(source, workspace.images[0].label, 'copy-0.png');
  const total = workspace.images.length * 3;
  fs.chmodSync(denied, 0o000);
  try {
    expect((await page.request.post(api('/api/project/create'), { data: { name: 'Read error status', task: 'classification' } })).ok()).toBe(true);
    expect((await page.request.put(api('/api/project/update'), { data: { source_dataset_dir: source } })).ok()).toBe(true);
    await installDesktopHostShim(page, renderer.port);
    await page.goto(renderer.url);
    await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText('Read error status', { timeout: 60_000 });
    await page.getByRole('button', { name: /01.*데이터 관리/ }).click();
    await adopt(page);
    const status = page.getByRole('status').filter({ hasText: /빠른 확인|전체 검증|활성 버전/ }).first();
    await expect(status).toContainText('전체 검증 완료 · 활성 버전', { timeout: 15_000 });
    await expect(status).toContainText(`${total}장 중 유효 ${total - 1}장, 손상·제외 1장`);
    await expect(status).not.toContainText('다릅니다');
    await evidence.screenshot(page, 'data-status-excluded-read-error');

    fs.chmodSync(denied, 0o644);
    await page.reload();
    await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText('Read error status', { timeout: 60_000 });
    await page.getByRole('button', { name: /01.*데이터 관리/ }).click();
    await expect(status).toContainText('다릅니다(바뀌었거나 확인되지 않은 1장)', { timeout: 15_000 });
    await expect(status).not.toContainText('전체 검증 완료');
    await adopt(page);
    await expect(status).toContainText(`${total}장 중 유효 ${total}장.`, { timeout: 15_000 });
    await expect(status).not.toContainText('손상·제외');
    await evidence.screenshot(page, 'data-status-recovered-and-validated');
  } finally {
    fs.chmodSync(denied, 0o644);
  }
});
