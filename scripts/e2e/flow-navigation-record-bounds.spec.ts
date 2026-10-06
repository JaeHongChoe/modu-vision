import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
const harness = require('./fixtures/harness.cjs');
type Api = (route: string, body?: any) => Promise<any>;
test.use({actionTimeout: 15_000});
const sha = (file: string) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: Api, url?: string) {
  const script = path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/flow_navigation_control.py');
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [script, workspace.root], {encoding: 'utf8', timeout: 15_000}));
  await api('/api/project/create', {name: 'Flow navigation controls', task: 'classification'});
  await api('/api/project/update', {source_dataset_dir: fixture.source});
  const project = await api('/api/project/current');
  await api('/api/dataset/import', {folder_path: fixture.source, task: 'classification'});
  const seeded = JSON.parse(execFileSync(harness.resolvePython(), [script, workspace.root, JSON.stringify(project)], {encoding: 'utf8', timeout: 15_000}));
  const show = async () => {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(4).click();
    await page.getByRole('tab', {name: '편집', exact: true}).click();
    await expect(page.getByLabel('플로우 노드 검색', {exact: true})).toBeEnabled();
    await expect(page.locator('[data-flow-node-id]')).toHaveCount(seeded.graph.nodes.length);
  };
  await show();
  const canvas = page.getByLabel('플로우 그래프 캔버스', {exact: true});
  const search = page.getByLabel('플로우 노드 검색', {exact: true});
  const position = () => canvas.evaluate(element => ({left: element.scrollLeft, top: element.scrollTop}));
  const graphBefore = await api(`/api/flowchart/pipeline?inspection_task=classification&source_dataset_path=${encodeURIComponent(fixture.source)}`);
  await search.fill('존재하지않는노드');
  await expect(page.getByRole('status').filter({hasText: '일치하는 노드가 없습니다.'})).toBeVisible();
  await search.fill('먼곳 판정');
  const result = page.getByRole('list', {name: '노드 검색 결과'}).getByRole('button', {name: /먼곳 판정/});
  await result.press('Enter');
  await expect(result).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(async () => (await position()).left).toBeGreaterThan(100);
  await expect.poll(async () => (await position()).top).toBeGreaterThan(100);
  const target = page.locator(`[data-flow-node-id="${seeded.target_id}"]`);
  await expect.poll(async () => {
    const c = await canvas.boundingBox(), n = await target.boundingBox();
    return Boolean(c && n && n.x >= c.x - 1 && n.y >= c.y - 1 && n.x + n.width <= c.x + c.width + 1 && n.y + n.height <= c.y + c.height + 1);
  }).toBe(true);
  const jumped = await position();
  await evidence.screenshot(page, 'search-jumps-to-distant-node');
  await search.fill('JOB_NAVIGATION_UNAVAILABLE');
  await expect(page.getByRole('list', {name: '노드 검색 결과'}).getByRole('button')).toHaveCount(1);
  await search.fill('');
  const map = page.getByRole('group', {name: '플로우 미니맵', exact: true});
  await map.focus();
  await map.press('ArrowLeft'); await map.press('ArrowUp');
  await expect.poll(async () => (await position()).left).toBeLessThan(jumped.left);
  await expect.poll(async () => (await position()).top).toBeLessThan(jumped.top);
  await map.getByRole('button', {name: '미니맵에서 먼곳 판정 보기', exact: true}).press('Enter');
  await expect.poll(async () => (await position()).left).toBe(jumped.left);
  await expect.poll(async () => (await position()).top).toBe(jumped.top);
  await map.click({position: {x: 3, y: 3}});
  await expect.poll(async () => (await position()).left).toBe(0);
  await expect.poll(async () => (await position()).top).toBe(0);
  await page.getByRole('button', {name: '플로우 전체 맞춤', exact: true}).click();
  await evidence.screenshot(page, 'minimap-keyboard-and-fit-all');
  const graphAfter = await api(`/api/flowchart/pipeline?inspection_task=classification&source_dataset_path=${encodeURIComponent(fixture.source)}`);
  expect(graphAfter).toEqual(graphBefore); expect(sha(seeded.graph_file)).toBe(seeded.graph_sha256);
  await show(); await search.fill('먼곳 판정'); await result.click();
  await expect.poll(async () => (await position()).top).toBeGreaterThan(100);
  const comparisons = await api('/api/flow-workspace/comparisons');
  expect(comparisons.invalid).toContainEqual({comparison_id: 'duplicate-control', reason: 'duplicate_record_field'});
  await page.getByRole('tab', {name: '일괄 평가', exact: true}).click();
  const details = page.locator('details').filter({has: page.getByText('고정 테스트 세트 · 플로우 버전 A/B 비교', {exact: true})}).first();
  if (await details.getAttribute('open') === null) await details.locator('summary').first().click();
  await expect(page.getByRole('alert').filter({hasText: '무결성을 확인할 수 없는 저장 비교 1개'})).toBeVisible();
  await evidence.screenshot(page, 'ambiguous-comparison-record-quarantined');
  expect(sha(seeded.duplicate_record)).toBe(seeded.duplicate_sha256);
  for (const row of fixture.files) expect(sha(row.path)).toBe(row.sha256);
  expect(fs.existsSync(path.join(project.project_dir, 'flowcharts/active.json'))).toBe(false);
  evidence.note('flow_navigation', {fixture, project, seeded, jumped, graphBefore, graphAfter, comparisons, graph_unchanged: true, source_unchanged: true, history_record_preserved: true, actual_model_execution: false, quality_approved: false});
}

test('actual graph search and minimap preserve graph on navigation and reopen', async ({page, request, renderer, workspace, evidence}) => {
  test.setTimeout(120_000);
  const api: Api = async (route, body) => {
    const r = body === undefined ? await request.get(renderer.origin + route) : route.endsWith('/update') ? await request.put(renderer.origin + route, {data: body}) : await request.post(renderer.origin + route, {data: body});
    expect(r.ok(), await r.text()).toBe(true); return r.json();
  };
  await exercise(page, workspace, evidence, api, renderer.url);
});
test('native minimap keyboard navigation and duplicate history quarantine', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  test.setTimeout(120_000);
  const {window} = electronSession, backend = await electronSession.waitForBackend();
  const api: Api = (route, body) => window.evaluate(async ({port, route, body}) => {
    const r = await fetch(`http://127.0.0.1:${port}${route}`, {...(body === undefined ? {} : {method: route.endsWith('/update') ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!r.ok) throw Error(`Owned API ${r.status}: ${await r.text()}`); return r.json();
  }, {port: backend.port, route, body});
  await exercise(window, workspace, evidence, api);
});
