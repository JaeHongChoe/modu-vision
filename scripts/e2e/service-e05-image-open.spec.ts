import crypto from 'node:crypto';
import type { APIRequestContext, Page } from '@playwright/test';
import { expect, test, type RendererServer, type Workspace } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';

// Full application renderer/LabelingStudio and real stores/backend. The only
// controlled boundary is delivery of one real annotations HTTP response.
async function seed(request: APIRequestContext, renderer: RendererServer, workspace: Workspace) {
  const call = async (method: 'post' | 'put', route: string, data?: unknown) => {
    const response = await request[method](`${renderer.origin}${route}`, data === undefined ? {} : { data });
    expect(response.ok(), `${route}: ${await response.text()}`).toBe(true);
    return response.json();
  };
  const project = await call('post', '/api/project/create', { name: 'E05 image selection', task: 'detection' });
  await call('put', '/api/project/update', { source_dataset_dir: workspace.dataset });
  await call('put', '/api/team-data/settings', { expected_revision: 1, actor: 'owner', changes: { review_enabled: true } });
  const image = workspace.images[0];
  const save = (annotations: unknown[]) => call('post', '/api/annotations/save', {
    image_id: 'sample-ok', image_path: image.path, actor: 'Reference labeler', annotations, image_width: 32, image_height: 32,
  });
  const original = await save([{ id: 'gold-object', type: 'bbox', label: 'scratch', category_id: 1, bbox: [4, 4, 20, 20] }]);
  await call('post', `/api/team-data/images/${original.metadata.image_uuid}/review`, {
    expected_revision: original.metadata.revision, actor: 'Reviewer', decision: 'approve',
  });
  const candidate = await call('post', '/api/project/labelsets', { name: 'Other image scope' });
  await call('put', `/api/project/labelsets/${candidate.id}/activate`);
  await save([]);
  await call('put', '/api/project/labelsets/default/activate');
  const profile = await call('post', '/api/team-data/quality/profiles', {
    task: 'detection', reference_labelset: 'default', candidate_labelset: candidate.id,
    gold_images: [image.path], tolerance: 0.5, actor: 'Reviewer',
  });
  await call('post', `/api/team-data/quality/profiles/${profile.profile_id}/reports`);
  return { image, project, candidate };
}

async function openReport(page: Page, renderer: RendererServer) {
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  await page.getByRole('button', { name: /02.*라벨링/ }).click();
  // A registered source is readable even when this detection fixture cannot
  // import a training dataset: the actual gallery is empty, as in the failure.
  await expect(page.locator('[data-labeling-filmstrip]')).toContainText('0 / 0');
  await page.getByRole('button', { name: '팀 작업 · 라벨 기준·검수', exact: true }).click();
  await page.getByRole('tab', { name: '정답 기준 검수', exact: true }).click();
  await page.getByLabel('팀 작업자 이름', { exact: true }).fill('Park');
  const review = page.getByRole('region', { name: '정답 기준 라벨 검수', exact: true });
  await review.getByRole('button', { name: /누락 1 · 추가 0 · 클래스 불일치 0/ }).click();
  const result = review.getByRole('region', { name: '라벨 검수 결과', exact: true });
  await expect(result).toBeVisible();
  return result;
}

async function holdAnnotations(page: Page) {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let held = false;
  let delivered = false;
  await page.route('**/api/annotations/sample-ok?**', async route => {
    if (held) return route.continue();
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    held = true;
    await gate;
    await route.fulfill({ response });
    delivered = true;
  });
  return { release, held: () => held, delivered: () => delivered };
}

async function flush(page: Page) {
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

test('gold result outside the gallery stays selected while labels load and opens the actual canvas', async ({ page, renderer, workspace, evidence }) => {
  const { image } = await seed(page.request, renderer, workspace);
  const result = await openReport(page, renderer);
  const gate = await holdAnnotations(page);
  const raw = page.waitForResponse(response => response.url().includes('/api/dataset/raw/')
    && decodeURIComponent(response.url()).includes(image.path));
  try {
    await result.getByRole('button', { name: 'ok/sample-ok.png', exact: true }).click();
    await expect.poll(gate.held).toBe(true);
    await flush(page);
    // This selection must survive the mounted LabelingStudio gallery-sync effect
    // before the annotation request resolves, not merely after its continuation.
    await expect(page.locator('[data-labeling-filmstrip] img[alt="sample-ok.png"]')).toHaveCount(1, { timeout: 2000 });
    gate.release();
    await expect.poll(gate.delivered).toBe(true);
    const response = await raw;
    expect(response.status()).toBe(200);
    expect(crypto.createHash('sha256').update(await response.body()).digest('hex')).toBe(image.sha256);
    await expect(page.getByRole('dialog', { name: '팀 데이터 작업', exact: true })).toHaveCount(0);
    await expect(page.locator('[data-labeling-filmstrip] img[alt="sample-ok.png"]')).toBeVisible();
    await expect(page.getByText('Annotations (1)', { exact: true })).toBeVisible();
    const canvas = page.locator('[data-canvas-container] canvas').first();
    await expect.poll(() => canvas.evaluate((element: HTMLCanvasElement) =>
      [...element.getContext('2d')!.getImageData(Math.floor(element.width / 2), Math.floor(element.height / 2), 1, 1).data]
    )).toEqual([180, 180, 180, 255]);
    evidence.note('outside_gallery_image_open', { real_labeling_studio: true, real_backend: true,
      delayed_actual_annotation_response: true, image_path: image.path, raw_sha256: image.sha256,
      canvas_center_rgba: [180, 180, 180, 255], original_annotation_count: 1, dialog_closed: true });
    await evidence.screenshot(page, 'e05-external-image-canvas');
  } finally { gate.release(); }
});

for (const scope of ['labelset', 'project']) {
  test(`late image labels cannot restore the previous selection after a real ${scope} switch`, async ({ page, renderer, workspace, evidence }) => {
    const { project, candidate } = await seed(page.request, renderer, workspace);
    const result = await openReport(page, renderer);
    const gate = await holdAnnotations(page);
    try {
      await result.getByRole('button', { name: 'ok/sample-ok.png', exact: true }).click();
      await expect.poll(gate.held).toBe(true);
      await page.getByRole('button', { name: '팀 데이터 작업 닫기', exact: true }).click();
      if (scope === 'labelset') {
        await page.getByLabel('활성 레이블셋', { exact: true }).selectOption(candidate.id);
        await expect(page.getByLabel('활성 레이블셋', { exact: true })).toHaveValue(candidate.id);
      } else {
        await page.getByTitle('프로젝트 관리', { exact: true }).click();
        const management = page.getByRole('dialog', { name: '프로젝트 관리', exact: true });
        await management.getByRole('button', { name: '새 프로젝트', exact: true }).click();
        await management.getByLabel(/프로젝트 이름/).fill('Replacement E05 image scope');
        await management.getByRole('combobox', { name: '검사 유형', exact: true }).selectOption('detection');
        await management.getByRole('button', { name: '프로젝트 만들기', exact: true }).click();
        await expect(management).toHaveCount(0);
        await expect(page.getByTitle('프로젝트 관리', { exact: true })).toHaveText('Replacement E05 image scope');
        await page.getByRole('button', { name: /02.*라벨링/ }).click();
      }
      await expect(page.locator('[data-labeling-filmstrip] img')).toHaveCount(0);
      gate.release();
      await expect.poll(gate.delivered).toBe(true);
      await flush(page);
      await expect(page.locator('[data-labeling-filmstrip] img')).toHaveCount(0);
      await expect(page.getByText('Annotations (0)', { exact: true })).toBeVisible();
      await expect(page.getByRole('dialog', { name: '팀 데이터 작업', exact: true })).toHaveCount(0);
      const current = await (await page.request.get(`${renderer.origin}/api/project/current`)).json();
      if (scope === 'labelset') expect(current).toMatchObject({ id: project.id, active_labelset_id: candidate.id });
      else expect(current.id).not.toBe(project.id);
      evidence.note('late_image_scope_guard', { scope, current_project: current.id, active_labelset: current.active_labelset_id,
        real_ui_switch: true, old_selection_restored: false, actual_delayed_response_delivered: true });
    } finally { gate.release(); }
  });
}
