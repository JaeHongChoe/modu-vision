import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from './test';

// Smoke checks for the harness itself. Product behaviour belongs to the task
// specs that build on these fixtures.

test('browser renderer reaches the owned backend through the token-holding server', async ({
  page, request, backend, renderer, workspace, evidence,
}) => {
  const direct = await request.get(`${backend.baseUrl}/api/project/list`);
  expect(direct.status(), 'the backend refuses calls without the process token').toBe(401);

  const projectName = `harness smoke ${test.info().testId}`;
  const created = await request.post(`http://127.0.0.1:${renderer.port}/api/project/create`, {
    data: { name: projectName, task: 'classification' },
  });
  expect(created.status()).toBe(200);
  const current = await (await request.get(`http://127.0.0.1:${renderer.port}/api/project/current`)).json();
  expect(current.name).toBe(projectName);
  const projectFiles = fs.readdirSync(workspace.projects, { recursive: true }).map(String);
  expect(projectFiles.some(file => path.basename(file) === 'project.json'), 'project lives in the temporary workspace').toBe(true);
  evidence.note('temporary_project', { name: projectName, files: projectFiles.slice(0, 20) });

  const telemetry = page.waitForEvent('websocket', socket => socket.url().includes('/ws/telemetry'));
  await page.goto(renderer.url);
  await expect(page.getByRole('navigation', { name: '프로젝트 작업 공간' })).toBeVisible();
  const socket = await telemetry;
  // The backend closes unauthenticated sockets before registering them, so any
  // telemetry broadcast or ping reply proves the upgrade carried the token.
  const frame = await socket.waitForEvent('framereceived', { timeout: 30_000 });
  const payload = String(frame.payload);
  expect(payload === 'pong' || typeof JSON.parse(payload).event === 'string').toBe(true);
  expect(socket.isClosed()).toBe(false);
  expect(renderer.stats().proxied).toBeGreaterThan(2);
  await evidence.screenshot(page, 'browser-shell');
});

test('electron starts its own backend in an isolated profile', { tag: '@electron' }, async ({
  electronSession, workspace, evidence,
}) => {
  const backend = await electronSession.waitForBackend();
  expect(backend.health.status).toBe('ok');
  const window = electronSession.window;
  await expect(window.getByRole('navigation', { name: '프로젝트 작업 공간' })).toBeVisible();
  expect(window.url()).toMatch(/^file:.*\/dist\/index\.html$/);
  expect(fs.existsSync(path.join(workspace.userData, 'projects')), 'supervisor uses the temporary profile').toBe(true);
  await evidence.screenshot(window, 'electron-shell');
});
