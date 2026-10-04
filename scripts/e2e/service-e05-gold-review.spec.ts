import type { APIRequestContext } from '@playwright/test';
import { expect, test, type RendererServer, type Workspace } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';

// E05 on the actual renderer and backend (owned synthetic harness): two label sets on the harness's two images, the
// reference approved through the team review API, compared in the team panel's gold review tab. No transport fixtures.
const box = (id: string, label: string, bbox: number[], category = 1) => ({ id, type: 'bbox', label, category_id: category, bbox });

async function seed(request: APIRequestContext, renderer: RendererServer, workspace: Workspace) {
  const call = async (method: 'post' | 'put', path: string, data?: unknown) => {
    const response = await request[method](`${renderer.origin}${path}`, data === undefined ? {} : { data });
    expect(response.ok(), `${path}: ${await response.text()}`).toBe(true);
    return response.json();
  };
  await call('post', '/api/project/create', { name: 'E05 workspace', task: 'detection' });
  await call('put', '/api/project/update', { source_dataset_dir: workspace.dataset });
  await call('put', '/api/team-data/settings', { expected_revision: 1, actor: 'owner', changes: { review_enabled: true } });
  const [ok, ng] = workspace.images.map(image => image.path);
  const save = (path: string, annotations: unknown[]) => call('post', '/api/annotations/save',
    { image_id: path.split('/').pop()!.replace('.png', ''), image_path: path, actor: 'Lee', annotations, image_width: 32, image_height: 32 });
  // Reference (the default label set): approved by another reviewer.
  for (const [path, annotations] of [[ok, [box('r1', 'scratch', [4, 4, 20, 20])]], [ng, [box('r2', 'scratch', [6, 6, 26, 26])]]] as const) {
    const saved = (await save(path, [...annotations])).metadata;
    await call('post', `/api/team-data/images/${saved.image_uuid}/review`, { expected_revision: saved.revision, actor: 'Kim', decision: 'approve' });
  }
  // The labeler's set (a new set starts as a copy of the active one): another class on the first image, nothing on the
  // second.
  const candidate = await call('post', '/api/project/labelsets', { name: 'Labeler B' });
  await call('put', `/api/project/labelsets/${candidate.id}/activate`);
  await save(ok, [box('c1', 'dent', [4, 4, 20, 20], 2)]);
  await save(ng, []);
  await call('put', '/api/project/labelsets/default/activate');
  return { ok, ng, save };
}

test('a labeler set compared with approved gold images gives placed conflicts, stales when gold changes, and keeps gold out of training', async ({ page, renderer, workspace, evidence }) => {
  const { ok, save } = await seed(page.request, renderer, workspace);
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  await page.getByRole('button', { name: /02.*라벨링/ }).click();
  await page.getByRole('button', { name: '팀 작업 · 라벨 기준·검수' }).click();
  await page.getByRole('tab', { name: '정답 기준 검수' }).click();
  await expect(page.getByText('위의 작업자 이름을 입력하세요.')).toBeVisible();
  await page.getByLabel('팀 작업자 이름').fill('Park');
  const review = page.getByRole('region', { name: '정답 기준 라벨 검수' });
  await expect(review.getByLabel('비교할 라벨셋')).toHaveValue(/.+/);
  await expect(review.getByLabel('비교할 라벨셋').locator('option:checked')).toHaveText('Labeler B');
  await review.getByRole('button', { name: '모두 선택' }).click();
  await review.getByRole('button', { name: '검수 기준 만들고 실행 (2장)' }).click();
  const result = review.getByRole('region', { name: '라벨 검수 결과' });
  await expect(result.getByRole('heading')).toHaveText('검수 결과 · 누락 1 · 추가 0 · 클래스 불일치 1 · 모양 불일치 0 · 일치 0/2장');
  await expect(result).toContainText('클래스 불일치: 기준 scratch → 작업 dent (겹침 1) · 위치 [4, 4, 20, 20]');
  await expect(result).toContainText('누락: 작업 라벨에 없는 기준 객체');
  // Conflicts are not agreement: the report is current, but it does not support an approval (e05s1 review P2-1).
  await expect(result.getByRole('status')).toHaveText('현재 정답 기준으로 계산한 결과: 불일치, 라벨 없는 이미지 또는 비교할 수 없는 이미지가 있어 승인 근거로 쓸 수 없습니다.');
  await evidence.screenshot(page, 'e05-01-report');

  // Changing a gold label stales the saved report and its approval eligibility.
  await save(ok, [box('r1', 'scratch', [4, 4, 22, 22])]);
  await review.getByRole('region', { name: '검수 기준과 결과' }).getByRole('button', { name: /누락 1 · 추가 0 · 클래스 불일치 1/ }).click();
  await expect(result.getByRole('status')).toContainText('승인 근거로 쓸 수 없습니다 (정답 라벨이 바뀌었습니다: ok/sample-ok.png');
  // Saving the label through the app also withdrew its approval, which the report names too (e05s1 review P2-2).
  await expect(result.getByRole('status')).toContainText('정답 이미지가 더 이상 승인 상태가 아닙니다: ok/sample-ok.png');
  await evidence.screenshot(page, 'e05-02-stale');

  // Gold images are out of training and test use until the policy keeps them.
  const policy = review.getByLabel('정답 이미지도 학습·시험에 사용');
  await expect(policy).not.toBeChecked();
  const policyChangedAfter = Date.now();
  await policy.click(); // checked once the server has recorded the policy
  await expect(policy).toBeChecked();
  const profiles = await (await page.request.get(`${renderer.origin}/api/team-data/quality/profiles`)).json();
  expect(profiles.gold_policy.include_gold_in_training).toBe(true);
  expect(profiles.gold_policy.changed_by).toBe('Park');
  expect(profiles.gold_policy.changed_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  const policyChangedAt = Date.parse(profiles.gold_policy.changed_at);
  expect(policyChangedAt).toBeGreaterThanOrEqual(policyChangedAfter - 1000);
  expect(policyChangedAt).toBeLessThanOrEqual(Date.now());
  expect(profiles.profiles[0].reference_snapshot.map((row: { relative_path: string }) => row.relative_path).sort()).toEqual(['ng/sample-ng.png', 'ok/sample-ok.png']);

  // Each conflict links to its image: the labeling view loads it (its raw pixels) and the dialog closes over it.
  const loaded = page.waitForRequest(request => request.url().includes('/api/dataset/raw/') && decodeURIComponent(request.url()).includes('sample-ok'));
  await result.getByRole('button', { name: 'ok/sample-ok.png' }).click();
  await loaded;
  await expect(page.getByRole('dialog', { name: '팀 데이터 작업' })).toHaveCount(0);
  evidence.note('gold_review', { counts: { missing: 1, extra: 0, class: 1, geometry: 0 }, stale_after_gold_change: true, gold_policy: true, image: ok });
});
