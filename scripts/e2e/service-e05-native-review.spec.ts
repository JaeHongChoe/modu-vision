import path from 'node:path';
import type { Page } from '@playwright/test';
import { expect, test } from './fixtures/test';

// Actual unpackaged Electron main/preload/renderer and its supervised backend.
// Fetch stays in the trusted renderer: Electron main adds the process token.
// The token never enters this spec, page scripts, assertion messages or evidence.
async function ownedApi<T>(window: Page, port: number, method: 'GET' | 'POST' | 'PUT', route: string, body?: unknown): Promise<T> {
  return window.evaluate(async ({ ownedPort, verb, endpoint, payload }) => {
    const response = await fetch(`http://127.0.0.1:${ownedPort}${endpoint}`, {
      method: verb,
      headers: { 'Content-Type': 'application/json' },
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
    });
    if (!response.ok) throw new Error(`${verb} ${endpoint}: HTTP ${response.status}`);
    return response.json();
  }, { ownedPort: port, verb: method, endpoint: route, payload: body });
}

type Project = { id: string; name: string; task: string; project_dir: string; source_dataset_dir: string | null };
type Metadata = { image_uuid: string; revision: number };
type Counts = { missing: number; extra: number; class: number; geometry: number; not_comparable: number };
type Profile = { profile_id: string; actor: string; task: string; reference_labelset: string; candidate_labelset: string;
  reference_snapshot: Array<{ relative_path: string }> };
type Report = { report_id: string; profile_id: string; counts: Counts; current: boolean; stale: boolean;
  stale_reasons: string[]; candidate_changes: string[]; passes: boolean; approval_eligible: boolean;
  images: Array<{ relative_path: string; conflicts: Array<{ conflict_id: string; kind: string; bbox: number[] }> }> };

const box = (id: string, label: string, bbox: number[], category = 1) => ({ id, type: 'bbox', label, category_id: category, bbox });

test('native E05 creates and reopens a placed gold review report and refuses stale approval evidence', { tag: '@electron' }, async ({
  electronSession, workspace, evidence,
}) => {
  const status = await electronSession.waitForBackend();
  const { app, window } = electronSession;
  expect(new URL(window.url()).protocol).toBe('file:');
  expect(await window.evaluate(() => (globalThis as any).api.getBackendPort())).toBe(status.port);
  const preferences = await app.evaluate(({ BrowserWindow }) => {
    const prefs = (BrowserWindow.getAllWindows()[0].webContents as any).getLastWebPreferences();
    return { sandbox: prefs?.sandbox, contextIsolation: prefs?.contextIsolation };
  });
  expect(preferences).toEqual({ sandbox: true, contextIsolation: true });

  const projectName = 'E05 native synthetic review';
  const projectDir = path.join(workspace.projects, 'e05-native-review');
  await window.getByTitle('프로젝트 관리', { exact: true }).click();
  const management = window.getByRole('dialog', { name: '프로젝트 관리', exact: true });
  await management.getByRole('button', { name: '새 프로젝트', exact: true }).click();
  await management.getByLabel(/프로젝트 이름/).fill(projectName);
  await management.getByRole('combobox', { name: '검사 유형', exact: true }).selectOption('detection');
  await management.getByPlaceholder('기본 프로젝트 폴더 사용').fill(projectDir);
  await management.getByRole('button', { name: '프로젝트 만들기', exact: true }).click();
  await expect(management).toHaveCount(0);
  const project = await ownedApi<Project>(window, status.port, 'GET', '/api/project/current');
  expect(project.name).toBe(projectName);
  expect(project.task).toBe('detection');
  expect(path.resolve(project.project_dir)).toBe(path.resolve(projectDir));

  await ownedApi(window, status.port, 'PUT', '/api/project/update', { source_dataset_dir: workspace.dataset });
  await ownedApi(window, status.port, 'PUT', '/api/team-data/settings', {
    expected_revision: 1, actor: 'owner', changes: { review_enabled: true },
  });
  const [ok, ng] = workspace.images.map(image => image.path);
  const save = (imagePath: string, annotations: unknown[]) => ownedApi<{ metadata: Metadata }>(window, status.port, 'POST', '/api/annotations/save', {
    image_id: path.parse(imagePath).name, image_path: imagePath, actor: 'ReferenceLabeler', annotations,
    image_width: 32, image_height: 32,
  });
  for (const [imagePath, annotations] of [[ok, [box('r1', 'scratch', [4, 4, 20, 20])]],
    [ng, [box('r2', 'scratch', [6, 6, 26, 26])]]] as const) {
    const saved = (await save(imagePath, [...annotations])).metadata;
    await ownedApi(window, status.port, 'POST', `/api/team-data/images/${saved.image_uuid}/review`, {
      expected_revision: saved.revision, actor: 'ReferenceReviewer', decision: 'approve',
    });
  }
  const candidate = await ownedApi<{ id: string }>(window, status.port, 'POST', '/api/project/labelsets', { name: 'Native candidate' });
  await ownedApi(window, status.port, 'PUT', `/api/project/labelsets/${candidate.id}/activate`);
  await save(ok, [box('c1', 'dent', [4, 4, 20, 20], 2)]);
  await save(ng, []);
  await ownedApi(window, status.port, 'PUT', '/api/project/labelsets/default/activate');

  // Resync through the application's startup path after API fixture preparation;
  // no store injection, desktop shim, credential bridge or transport replacement.
  await window.reload();
  await expect(window.getByTitle('프로젝트 관리', { exact: true })).toHaveText(projectName);
  await window.getByRole('button', { name: /02.*라벨링/ }).click();
  await window.getByRole('button', { name: '팀 작업 · 라벨 기준·검수', exact: true }).click();
  await window.getByRole('tab', { name: '정답 기준 검수', exact: true }).click();
  await window.getByLabel('팀 작업자 이름', { exact: true }).fill('NativeReviewer');
  const review = window.getByRole('region', { name: '정답 기준 라벨 검수', exact: true });
  await expect(review.getByLabel('비교할 라벨셋', { exact: true }).locator('option:checked')).toHaveText('Native candidate');
  await expect(review.getByText('정답 이미지 (승인된 이미지 2장)', { exact: true })).toBeVisible();
  await review.getByRole('button', { name: '모두 선택', exact: true }).click();
  await review.getByRole('button', { name: '검수 기준 만들고 실행 (2장)', exact: true }).click();
  const result = review.getByRole('region', { name: '라벨 검수 결과', exact: true });
  await expect(result.getByRole('heading')).toHaveText('검수 결과 · 누락 1 · 추가 0 · 클래스 불일치 1 · 모양 불일치 0 · 일치 0/2장');
  await expect(result).toContainText('클래스 불일치: 기준 scratch → 작업 dent (겹침 1) · 위치 [4, 4, 20, 20]');
  await expect(result).toContainText('누락: 작업 라벨에 없는 기준 객체 r2');
  await expect(result.getByRole('status')).toHaveText('현재 정답 기준으로 계산한 결과: 불일치, 라벨 없는 이미지 또는 비교할 수 없는 이미지가 있어 승인 근거로 쓸 수 없습니다.');

  // Read actual stored records, independently of the visible UI result.
  const profiles = await ownedApi<{ profiles: Profile[]; gold_policy: { include_gold_in_training: boolean } }>(window, status.port, 'GET', '/api/team-data/quality/profiles');
  expect(profiles.profiles).toHaveLength(1);
  const profile = profiles.profiles[0];
  expect(profile.actor).toBe('NativeReviewer');
  expect(profile.task).toBe('detection');
  expect(profile.reference_labelset).toBe('default');
  expect(profile.candidate_labelset).toBe(candidate.id);
  expect(profile.reference_snapshot.map(row => row.relative_path).sort()).toEqual(['ng/sample-ng.png', 'ok/sample-ok.png']);
  expect(profiles.gold_policy.include_gold_in_training).toBe(false);
  const summaries = await ownedApi<{ reports: Array<{ report_id: string }> }>(window, status.port, 'GET',
    `/api/team-data/quality/reports?profile_id=${profile.profile_id}`);
  expect(summaries.reports).toHaveLength(1);
  const reportId = summaries.reports[0].report_id;
  const current = await ownedApi<Report>(window, status.port, 'GET', `/api/team-data/quality/reports/${reportId}`);
  expect(current.counts).toEqual({ missing: 1, extra: 0, class: 1, geometry: 0, not_comparable: 0 });
  expect(current.profile_id).toBe(profile.profile_id);
  expect(current.current).toBe(true);
  expect(current.stale).toBe(false);
  expect(current.passes).toBe(false);
  expect(current.approval_eligible).toBe(false);
  expect(current.candidate_changes).toEqual([]);
  expect(current.images.map(image => image.conflicts[0]?.kind)).toEqual(['missing', 'class']);
  await evidence.screenshot(window, 'e05-native-current-report');

  // A real save withdraws reference approval and makes this kept report stale.
  await save(ok, [box('r1', 'scratch', [4, 4, 22, 22])]);
  const history = review.getByRole('region', { name: '검수 기준과 결과', exact: true });
  await history.getByRole('button', { name: /누락 1 · 추가 0 · 클래스 불일치 1/ }).click();
  await expect(result.getByRole('status')).toContainText('정답 라벨이 바뀌었습니다: ok/sample-ok.png');
  await expect(result.getByRole('status')).toContainText('정답 이미지가 더 이상 승인 상태가 아닙니다: ok/sample-ok.png');
  const stale = await ownedApi<Report>(window, status.port, 'GET', `/api/team-data/quality/reports/${reportId}`);
  expect(stale.report_id).toBe(reportId);
  expect(stale.current).toBe(false);
  expect(stale.stale).toBe(true);
  expect(stale.approval_eligible).toBe(false);
  expect(stale.stale_reasons).toEqual(['gold_label_changed:ok/sample-ok.png', 'gold_unapproved:ok/sample-ok.png']);
  expect(stale.counts).toEqual(current.counts);
  await evidence.screenshot(window, 'e05-native-stale-report');

  const rawResponse = window.waitForResponse(response => response.url().startsWith(`http://127.0.0.1:${status.port}/api/dataset/raw/`)
    && decodeURIComponent(response.url()).includes('sample-ok') && response.request().method() === 'GET');
  await result.getByRole('button', { name: 'ok/sample-ok.png', exact: true }).click();
  expect((await rawResponse).status()).toBe(200);
  await expect(window.getByRole('dialog', { name: '팀 데이터 작업', exact: true })).toHaveCount(0);
  evidence.note('native_gold_review', {
    scope: 'Actual unpackaged Electron, synthetic owned project/profile/backend; no installer, GPU, field or model-quality acceptance',
    auth: 'Real preload port and Electron-main token injection; credential never read by test',
    project_created_in_ui: true, profile_created_and_run_in_ui: true,
    profile_id: profile.profile_id, report_id: reportId, counts: current.counts,
    current_report_readback: current.current, approval_eligible: current.approval_eligible,
    stale_reasons: stale.stale_reasons, source_image_open_http_status: 200,
  });
});
