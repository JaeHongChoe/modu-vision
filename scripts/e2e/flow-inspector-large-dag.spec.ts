import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
const harness = require('./fixtures/harness.cjs');
type Api = (route: string, body?: any) => Promise<any>;
const sha = (file: string) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
test.use({actionTimeout: 15_000});

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: Api,
  resize: (width: number, height: number) => Promise<void>, url?: string) {
  const script = path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/flow_large_dag_control.py');
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [script, workspace.root], {encoding: 'utf8', timeout: 15_000}));
  await api('/api/project/create', {name: 'Flow inspector large DAG control', task: 'segmentation'});
  await api('/api/project/update', {source_dataset_dir: fixture.source});
  const project = await api('/api/project/current');
  await api('/api/dataset/import', {folder_path: fixture.source, task: 'segmentation'});
  const seeded = JSON.parse(execFileSync(harness.resolvePython(), [script, workspace.root, JSON.stringify(project)], {encoding: 'utf8', timeout: 15_000}));
  expect(seeded.graph.nodes).toHaveLength(46); expect(seeded.graph.edges).toHaveLength(52);
  const show = async () => {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(4).click();
    await page.getByRole('tab', {name: '편집', exact: true}).click();
    await expect(page.locator('[data-flow-node-id]')).toHaveCount(46);
  };
  await show();
  const panel = page.getByRole('complementary', {name: '플로우 속성 패널', exact: true});
  const handle = page.getByRole('separator', {name: '속성 패널 크기 조절', exact: true});
  const canvas = page.getByLabel('플로우 그래프 캔버스', {exact: true});
  const search = page.getByLabel('플로우 노드 검색', {exact: true});
  const graphBefore = await api(`/api/flowchart/pipeline?inspection_task=segmentation&source_dataset_path=${encodeURIComponent(fixture.source)}`);
  const timings: Array<{node: string; ms: number}> = [];
  for (const node of seeded.graph.nodes.filter((n: any) => n.data.node_type === 'inspection')) {
    const start = Date.now(); await search.fill(node.id);
    await page.getByRole('list', {name: '노드 검색 결과'}).getByRole('button').click();
    await expect(page.getByLabel('결함 판정 임계치', {exact: true})).toHaveValue(String(node.data.threshold));
    timings.push({node: node.id, ms: Date.now() - start});
  }
  const selected = seeded.graph.nodes.find((n: any) => n.id === 'model-007');
  const name = page.getByRole('textbox', {name: '노드 명칭', exact: true});
  await name.fill('저장 전 이름 유지');
  await page.getByRole('button', {name: '속성 패널 접기', exact: true}).click();
  await expect(panel).toHaveCSS('width', '44px'); await expect(name).toBeHidden();
  await page.getByRole('button', {name: '속성 패널 펼치기', exact: true}).click();
  await expect(name).toHaveValue('저장 전 이름 유지');
  await page.getByRole('button', {name: '플로우 실행 취소', exact: true}).click();
  await expect(name).toHaveValue(selected.data.label);
  await handle.press('Home'); await expect(handle).toHaveAttribute('aria-valuenow', '260');
  await handle.press('ArrowLeft'); await expect(handle).toHaveAttribute('aria-valuenow', '280');
  const box = await handle.boundingBox(); expect(box).not.toBeNull();
  await page.mouse.move(box!.x + 4, box!.y + 40); await page.mouse.down();
  await page.mouse.move(box!.x - 76, box!.y + 40, {steps: 8}); await page.mouse.up();
  await expect(handle).toHaveAttribute('aria-valuenow', '360');
  const preferredWidth = Number(await handle.getAttribute('aria-valuenow'));
  await handle.press('End');
  await resize(1100, 800);
  await expect.poll(async () => Number(await handle.getAttribute('aria-valuenow'))).toBeLessThanOrEqual(560);
  await expect.poll(async () => (await canvas.boundingBox())!.width).toBeGreaterThanOrEqual(299);
  await resize(1440, 900);
  await handle.press('Home'); for (let i = 0; i < 5; i++) await handle.press('ArrowLeft');
  await expect(handle).toHaveAttribute('aria-valuenow', String(preferredWidth));
  await search.fill('큰 그래프 끝 review');
  const result = page.getByRole('list', {name: '노드 검색 결과'}).getByRole('button'); await result.press('Enter');
  await expect(result).toHaveAttribute('aria-pressed', 'true');
  const target = page.locator(`[data-flow-node-id="${seeded.target_id}"]`);
  await expect.poll(async () => {
    const c = await canvas.boundingBox(), n = await target.boundingBox();
    return Boolean(c && n && n.x >= c.x - 1 && n.y >= c.y - 1 && n.x + n.width <= c.x + c.width + 1 && n.y + n.height <= c.y + c.height + 1);
  }).toBe(true);
  const nodeWidth = (await target.boundingBox())!.width; expect(nodeWidth).toBeGreaterThanOrEqual(195);
  await evidence.screenshot(page, 'large-dag-readable-node-and-resized-inspector');
  await page.getByRole('button', {name: '속성 패널 접기', exact: true}).click();
  await show(); await expect(panel).toHaveCSS('width', '44px');
  await page.getByRole('button', {name: '속성 패널 펼치기', exact: true}).click();
  await expect(handle).toHaveAttribute('aria-valuenow', String(preferredWidth));
  await search.fill('큰 그래프 끝 review'); await result.click();
  const map = page.getByRole('group', {name: '플로우 미니맵', exact: true});
  await map.focus(); await map.press('ArrowLeft'); await map.press('ArrowUp');
  await page.getByRole('button', {name: '플로우 전체 맞춤', exact: true}).click();
  await evidence.screenshot(page, 'large-dag-fit-and-layout-reopened');
  const graphAfter = await api(`/api/flowchart/pipeline?inspection_task=segmentation&source_dataset_path=${encodeURIComponent(fixture.source)}`);
  expect(graphAfter).toEqual(graphBefore); expect(sha(seeded.graph_file)).toBe(seeded.graph_sha256);
  for (const row of fixture.files) expect(sha(row.path)).toBe(row.sha256);
  expect(fs.existsSync(path.join(project.project_dir, 'flowcharts/active.json'))).toBe(false);
  evidence.note('flow_inspector_large_dag', {fixture, project, seeded, graphBefore, graphAfter, timings,
    preferredWidth, nodeWidth, native_windows: false, actual_model_execution: false, quality_approved: false,
    shared_model_nodes_checked: 8, unsaved_label_preserved: true, graph_unchanged: true, source_unchanged: true});
}
test('supported large DAG inspector resize collapse and reopen preserve graph', async ({page, request, renderer, workspace, evidence}) => {
  test.setTimeout(180_000);
  const api: Api = async (route, body) => {
    const r = body === undefined ? await request.get(renderer.origin + route) : route.endsWith('/update') ? await request.put(renderer.origin + route, {data: body}) : await request.post(renderer.origin + route, {data: body});
    expect(r.ok(), await r.text()).toBe(true); return r.json();
  };
  await exercise(page, workspace, evidence, api, async (width, height) => {await page.setViewportSize({width, height});}, renderer.url);
});
test('native supported large DAG inspector keyboard pointer and persisted layout', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  test.setTimeout(180_000);
  const {window} = electronSession, backend = await electronSession.waitForBackend();
  const api: Api = (route, body) => window.evaluate(async ({port, route, body}) => {
    const r = await fetch(`http://127.0.0.1:${port}${route}`, {...(body === undefined ? {} : {method: route.endsWith('/update') ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!r.ok) throw Error(`Owned API ${r.status}: ${await r.text()}`); return r.json();
  }, {port: backend.port, route, body});
  await exercise(window, workspace, evidence, api, async (width, height) => {
    await electronSession.app.evaluate(({BrowserWindow}, size) => BrowserWindow.getAllWindows()[0].setSize(size.width, size.height), {width, height});
  });
});
