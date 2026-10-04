import { expect, test } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';

// E03 on the actual renderer and backend: a measurement node makes a spatial calibration from known lengths on a planar
// fixture; a length that does not agree refuses the whole calibration with each segment's error; the made calibration
// is the project's artifact, selected with mm limits. Browser project with the test-only desktop host shim.
test('a calibration is made from known lengths in the measurement editor and refused when a length disagrees', async ({ page, renderer, workspace, evidence }) => {
  const created = await page.request.post(`${renderer.origin}/api/project/create`, { data: { name: 'Calibration', task: 'segmentation' } });
  expect(created.ok()).toBe(true);
  expect((await page.request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: workspace.dataset } })).ok()).toBe(true);
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  await page.getByRole('button', { name: /05.*플로우차트/ }).click();
  await expect(page.getByRole('heading', { name: '검사 플로우 편집기', exact: true })).toBeVisible({ timeout: 60_000 });
  await page.getByText('빠른 노드 추가·연결·실행 자원').click();
  await page.getByRole('button', { name: '길이·면적 측정' }).click();
  const panel = page.getByRole('region', { name: '실제 치수 교정' });
  await expect(panel).toBeVisible();
  await page.getByLabel('원본 너비').fill('320');
  await page.getByLabel('원본 높이').fill('240');
  // Calibration availability does not reinterpret existing pixel limits.
  await page.getByLabel('최소 길이 (px)', { exact: true }).fill('10');
  await page.getByLabel('최대 길이 (px)', { exact: true }).fill('30');
  const manual = page.getByRole('checkbox', { name: '실제 길이 교정 (mm, 직접 입력·검증 안 됨)', exact: true });
  await manual.check();
  await expect(panel.getByLabel('측정 기준 단위')).toHaveValue('px');
  await expect(page.getByLabel('최소 길이 (px)', { exact: true })).toHaveValue('10');
  await manual.uncheck();
  await expect(page.getByLabel('최대 길이 (px)', { exact: true })).toHaveValue('30');
  await panel.getByLabel('측정 기준 단위').selectOption('mm');
  await expect(page.getByLabel('최소 길이 (mm)', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('최대 길이 (mm)', { exact: true })).toHaveValue('');
  await panel.getByText('기준 길이로 새 교정 만들기').click();
  await panel.getByLabel('교정 카메라 ID').fill('line3-top');
  await panel.getByLabel('교정 카메라 설정').fill('resolution=320x240\nlens=16mm\nworking_distance_mm=300');
  await panel.getByLabel('교정 허용 오차').fill('0.01');
  // A planar fixture at 0.05 mm/px on X and 0.07 mm/px on Y.
  const segments = [[[10, 10], [310, 10], 15], [[10, 10], [10, 230], 15.4], [[10, 230], [310, 10], Math.hypot(15, 15.4)]] as const;
  for (const [index, [[x1, y1], [x2, y2], length]] of segments.entries()) {
    for (const [label, value] of [['1점 X', x1], ['1점 Y', y1], ['2점 X', x2], ['2점 Y', y2]] as const) {
      await panel.getByLabel(`${index + 1}번 기준선 ${label}`).fill(String(value));
    }
    await panel.getByLabel(`${index + 1}번 기준선 실제 길이 (mm)`).fill(String(index === 2 ? length + 0.5 : length));
  }
  await panel.getByRole('button', { name: '교정 만들기' }).click();
  await expect(panel.getByRole('alert')).toContainText('교정을 만들지 않았습니다');
  await expect(panel.getByText(/오차 0\.\d+ mm/).first()).toBeVisible();
  await evidence.screenshot(page, 'e03-calibration-refused');
  await panel.getByLabel('3번 기준선 실제 길이 (mm)').fill(String(segments[2][2]));
  await panel.getByRole('button', { name: '교정 만들기' }).click();
  const choice = panel.getByLabel('교정 artifact');
  await expect(choice).not.toHaveValue('', { timeout: 30_000 });
  await expect(choice.locator('option:checked')).toContainText('line3-top · 기준 길이 교정 · X 0.0500 / Y 0.0700 mm/px · 320×240');
  await expect(panel.getByLabel('측정 기준 단위')).toHaveValue('mm');
  const listed = await (await page.request.get(`${renderer.origin}/api/geometry/calibrations`)).json() as { calibrations: Array<{ ref: string; camera_id: string; residual: number }> };
  expect(listed.calibrations).toHaveLength(1);
  expect(listed.calibrations[0].ref).toBe(await choice.inputValue());
  evidence.note('calibration', { ref: listed.calibrations[0].ref, residual_mm: listed.calibrations[0].residual });
  await evidence.screenshot(page, 'e03-calibration-made');
});
