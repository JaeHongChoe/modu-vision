import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { crc32, deflateSync } from 'node:zlib';
import type { Page } from '@playwright/test';
import { expect } from '@playwright/test';

// The desktop app's user flow as page steps (no API shortcuts, no fixture answers). They drive the browser project
// (with the test-only desktop host bridge) and are meant to drive an Electron variant (the real main/preload/renderer)
// once the desktop binary is installed; no Electron spec uses them yet. Only the native folder dialog is answered by
// the caller, never an API response.

function chunk(type: string, data: Buffer): Buffer {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, 'ascii');
  const check = Buffer.alloc(4);
  check.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])) >>> 0, 0);
  return Buffer.concat([head, data, check]);
}

/** A PNG of `size` px: RGB (channels 3) or 8-bit grey (channels 1), drawn by `pixel`. */
export function png(size: number, channels: 1 | 3, pixel: (x: number, y: number) => number[]): Buffer {
  const stride = size * channels + 1;
  const rows = Buffer.alloc(stride * size);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) rows.set(pixel(x, y), y * stride + 1 + x * channels);
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8;
  header[9] = channels === 3 ? 2 : 0;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
}

/** A small segmentation dataset: images/{train,val}, binary 0/255 masks/{train,val}; every image differs. With
 *  ``cleanEvery`` every n-th image of a split has no defect (an empty mask), so both verdicts occur. */
export function segmentationDataset(root: string, counts = { train: 8, val: 4 }, { cleanEvery = 0 } = {}): string {
  let shift = 0;
  for (const [split, count] of Object.entries(counts)) {
    mkdirSync(join(root, 'images', split), { recursive: true });
    mkdirSync(join(root, 'masks', split), { recursive: true });
    for (let index = 0; index < count; index++, shift++) {
      const left = 8 + (shift * 3) % 30, top = 10 + (shift * 5) % 24;
      const clean = cleanEvery > 0 && index % cleanEvery === cleanEvery - 1;
      const inside = (x: number, y: number) => !clean && x >= left && x < left + 14 && y >= top && y < top + 10;
      writeFileSync(join(root, 'images', split, `part_${split}_${index}.png`), png(64, 3, (x, y) => inside(x, y) ? [40, 40, 40] : [190 + (shift % 9), 190, 190]));
      writeFileSync(join(root, 'masks', split, `part_${split}_${index}.png`), png(64, 1, (x, y) => [inside(x, y) ? 255 : 0]));
    }
  }
  return root;
}

export const stage = (page: Page, number: string, name: string) => page.getByRole('button', { name: new RegExp(`${number}.*${name}`) });

/** Create a project through the project dialog. */
export async function createProject(page: Page, name: string, taskLabel: string): Promise<void> {
  await page.getByTitle('프로젝트 관리', { exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '프로젝트 관리' });
  await dialog.getByRole('button', { name: '새 프로젝트' }).click();
  await dialog.getByLabel(/프로젝트 이름/).fill(name);
  await dialog.getByLabel('검사 유형').selectOption({ label: taskLabel });
  await dialog.getByRole('button', { name: '프로젝트 만들기' }).click();
  await expect(dialog).toHaveCount(0);
}

/** Pick the dataset folder in step 01 (the caller answers the native folder dialog with `folder`). */
export async function pickDataset(page: Page): Promise<void> {
  await stage(page, '01', '데이터 관리').click();
  const imported = page.waitForResponse(response => new URL(response.url()).pathname === '/api/dataset/import' && response.request().method() === 'POST');
  await page.getByRole('button', { name: '데이터셋 폴더 열기' }).click();
  expect((await imported).status()).toBe(200);
}

/** The training status the app shows (the STATUS annunciator's value, e.g. RUNNING, CANCELLED, COMPLETED). */
export const trainingStatus = (page: Page, value: string | RegExp) => page.getByText(value, { exact: true });

/** Open the Task Center dialog from the workspace bar (it lists this project's jobs with their cancel steps). */
export async function openTaskCenter(page: Page) {
  await page.getByRole('button', { name: '작업 센터', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '작업 센터' });
  await expect(dialog).toBeVisible();
  return dialog;
}

export const closeDialog = (page: Page, title: string) => page.getByRole('button', { name: `${title} 닫기` }).click();

/** Choose the family, structure and local device in step 03 and start training with the app's own button. */
export async function startTraining(page: Page, { family, structure, device }: { family: RegExp; structure?: string; device?: string }): Promise<void> {
  await stage(page, '03', '오토딥러닝').click();
  const hub = page.getByRole('region', { name: '모델 학습 허브' });
  const button = hub.getByRole('button', { name: family });
  if ((await button.getAttribute('aria-pressed')) !== 'true') await button.click();
  if (structure) await page.getByLabel('학습 모델 구조').selectOption({ label: structure });
  if (device) {
    await page.getByText('다음 학습 배치·로컬 장치 설정').click();
    await page.getByLabel('다음 학습 로컬 장치').selectOption(device);
  }
  await page.getByRole('button', { name: '선택 설정으로 학습 시작' }).click();
}
