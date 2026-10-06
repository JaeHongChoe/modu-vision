import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page, BrowserContext} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {browserTeamServer} from './fixtures/browser_team_server';

test.use({ignoreHTTPSErrors: true, actionTimeout: 15000});

test('independent HTTPS team clients conflict, lose revoked authority and reopen preserved edits after backend restart',
  async ({page, rendererBuild, workspace, evidence, loopbackGuard}) => {
  test.setTimeout(240000);
  const server = await browserTeamServer(workspace, rendererBuild.outDir);
  loopbackGuard.allow(server.port); evidence.redact(server.backend.token);
  const password = 'owned team recovery fixture 123'; evidence.redact(password);
  let secondContext: BrowserContext | undefined;
  const original = fs.readdirSync(workspace.dataset, {recursive: true}).filter(name => String(name).endsWith('.png'))
    .map(name => ({file: String(name), hash: crypto.createHash('sha256').update(fs.readFileSync(workspace.dataset + '/' + name)).digest('hex')}));
  try {
    await server.call('/api/accounts/bootstrap', {username: 'teamowner', password});
    const owner = await server.call('/api/accounts/login', {username: 'teamowner', password}); evidence.redact(owner.token);
    const project = await server.call('/api/project/create', {name: 'Two real team clients', task: 'classification'}, owner.token);
    const users = [];
    for (const username of ['labelerone', 'labelertwo']) {
      const user = await server.call('/api/accounts/users', {username, password}, owner.token);
      await server.call(`/api/accounts/projects/${project.id}/members`, {user_id: user.id, role: 'labeler'}, owner.token, 'PUT');
      users.push(user);
    }
    await server.call('/api/project/update', {source_dataset_dir: workspace.dataset}, owner.token, 'PUT');
    await server.call('/api/dataset/import', {folder_path: workspace.dataset, task: 'classification'}, owner.token);
    await server.call('/api/team-data/settings', {expected_revision: 1, changes: {editing_enabled: true}}, owner.token, 'PUT');
    secondContext = await page.context().browser()!.newContext({ignoreHTTPSErrors: true});
    await secondContext.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === server.origin || ['data:', 'blob:', 'about:'].includes(url.protocol) ? route.continue() : route.abort();
    });
    const second = await secondContext.newPage();
    const login = async (client: Page, username: string) => {
      await client.goto(server.origin);
      await client.getByRole('button', {name: '서버 관리', exact: true}).click();
      await client.getByText('공동 작업 서버', {exact: true}).click();
      await client.getByLabel('공유 계정 이름', {exact: true}).fill(username);
      await client.getByLabel('공유 계정 비밀번호', {exact: true}).fill(password);
      const response = client.waitForResponse(r => new URL(r.url()).pathname === '/api/accounts/login' && r.request().method() === 'POST');
      await client.getByRole('button', {name: '연결·로그인', exact: true}).click();
      const loggedIn = await (await response).json();
      evidence.redact(loggedIn.csrf_token);
      expect(loggedIn.token).toBeUndefined();
      await expect(client.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
      await client.getByRole('dialog', {name: '컴퓨팅 서버 관리', exact: true}).getByRole('button', {name: '닫기', exact: true}).click();
      const cookies = await client.context().cookies(server.origin);
      expect(cookies.find(row => row.name === 'vision_session')).toMatchObject({httpOnly: true, secure: true, sameSite: 'Strict'});
      return loggedIn.csrf_token as string;
    };
    const csrf = await Promise.all([login(page, users[0].username), login(second, users[1].username)]);
    const clients = [page, second];
    const request = (index: number, route: string, body?: any, method = body ? 'POST' : 'GET') => clients[index].evaluate(async args => {
      const reply = await fetch(args.route, {method: args.method, headers: {'Content-Type': 'application/json',
        'X-Vision-Project': args.project, ...(args.body ? {'X-Vision-CSRF': args.csrf} : {})},
        ...(args.body ? {body: JSON.stringify(args.body)} : {})});
      return {status: reply.status, value: await reply.json()};
    }, {route, body, method, project: project.id, csrf: csrf[index]});
    const queue = await request(0, '/api/team-data/queue'); expect(queue.status).toBe(200);
    const image = queue.value.items[0], leasePath = `/api/team-data/images/${image.image_uuid}/lease/`;
    const raced = await Promise.all(clients.map((_, index) => request(index, leasePath + 'acquire',
      {expected_revision: image.revision, actor: 'teamowner'})));
    expect(raced.map(row => row.status).sort()).toEqual([200, 409]);
    const winner = raced.findIndex(row => row.status === 200), loser = 1 - winner, acquired = raced[winner].value;
    evidence.redact(acquired.lease_token);
    expect(acquired.image.team.edit_lease.owner).toBe(users[winner].username);
    const annotationId = path.parse(image.file_path).name;
    const write = {image_id: annotationId, image_path: image.file_path,
      actor: 'teamowner', expected_revision: acquired.image.revision, lease_token: acquired.lease_token,
      annotations: [{type: 'tag', label: 'OK', category_id: 0, is_normal: true}]};
    const saved = await request(winner, '/api/annotations/save', write); expect(saved.status, JSON.stringify(saved.value)).toBe(200);
    expect(saved.value.metadata.team.annotation_actor).toBe(users[winner].username);
    const conflict = await request(loser, '/api/annotations/save', {...write, expected_revision: saved.value.metadata.revision});
    expect(conflict.status).toBe(409);
    const metadataPath = '/api/dataset/metadata/image?' + new URLSearchParams({image_path: image.file_path});
    const before = await request(loser, metadataPath); expect(before.status).toBe(200);
    await server.call(`/api/accounts/projects/${project.id}/members/${users[winner].id}`, undefined, owner.token, 'DELETE');
    const revoked = await request(winner, '/api/annotations/save', {...write, expected_revision: before.value.revision});
    expect(revoked.status).toBe(403);
    const restart = await server.restart(); loopbackGuard.allow(server.backend.port); evidence.redact(server.backend.token);
    evidence.addFile(restart.previous.logs.stdout); evidence.addFile(restart.previous.logs.stderr);
    expect(restart.stopped.backend_port_closed).toBe(true);
    expect(restart.current.pid).not.toBe(restart.previous.pid);
    const reopened = await request(loser, metadataPath); expect(reopened.status).toBe(200);
    expect(reopened.value).toEqual(before.value);
    const remainsRevoked = await request(winner, metadataPath); expect(remainsRevoked.status).toBe(403);
    await clients[loser].reload();
    await expect(clients[loser].getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    const preserved = await request(loser, '/api/annotations/' + annotationId + '?' + new URLSearchParams({file_path: image.file_path}));
    expect(preserved.status).toBe(200);
    expect(preserved.value.annotations).toHaveLength(write.annotations.length);
    expect(preserved.value.annotations).toMatchObject(write.annotations);
    await evidence.screenshot(clients[loser], 'team-conflict-revocation-restart');
    for (const row of original) expect(crypto.createHash('sha256').update(fs.readFileSync(workspace.dataset + '/' + row.file)).digest('hex')).toBe(row.hash);
    evidence.note('team_recovery', {project_id: project.id, independent_browser_contexts: true,
      accounts: users.map(user => user.username), simultaneous_lease_statuses: raced.map(row => row.status),
      stored_actor: saved.value.metadata.team.annotation_actor, foreign_lease_write_status: conflict.status,
      revoked_write_status: revoked.status, revoked_read_after_restart: remainsRevoked.status,
      same_metadata_after_restart: true, original_images_unchanged: true, restart,
      production_https_deployment: false, process_quality_approved: false});
  } finally {
    await secondContext?.close(); evidence.note('team_recovery_stop', await server.close());
    evidence.addFile(server.backend.logs.stdout); evidence.addFile(server.backend.logs.stderr);
  }
});
