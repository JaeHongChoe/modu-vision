import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';
import {storedZip} from './fixtures/storedZip';
import {execFileSync} from 'node:child_process';
import {test, expect} from './fixtures/test';
import {browserTeamServer} from './fixtures/browser_team_server';
const harness = require('./fixtures/harness.cjs');
test.use({ignoreHTTPSErrors: true, actionTimeout: 15000});
test('browser HTTPS cookie login opens authorized flow and role revocation refuses writes', async ({page, rendererBuild, workspace, evidence, loopbackGuard}) => {
  test.setTimeout(240000);
  const server = await browserTeamServer(workspace, rendererBuild.outDir);
  loopbackGuard.allow(server.port); evidence.redact(server.backend.token);
  const password = 'browser fixture password 123'; evidence.redact(password);
  try {
    await server.call('/api/accounts/bootstrap', {username: 'administrator', password});
    const administrator = await server.call('/api/accounts/login', {username:'administrator', password});
    evidence.redact(administrator.token);
    const project = await server.call('/api/project/create', {name: 'Browser team session flow', task: 'segmentation'}, administrator.token);
    const viewer = await server.call('/api/accounts/users', {username: 'vieweruser', password}, administrator.token);
    await server.call(`/api/accounts/projects/${project.id}/members`, {user_id: viewer.id, role: 'viewer'}, administrator.token, 'PUT');
    const script = path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/flow_large_dag_control.py');
    const fixture = JSON.parse(execFileSync(harness.resolvePython(), [script, workspace.root], {encoding: 'utf8'}));
    await server.call('/api/project/update', {source_dataset_dir: fixture.source}, administrator.token, 'PUT');
    await server.call('/api/dataset/import', {folder_path: fixture.source, task: 'segmentation'}, administrator.token);
    const graph = JSON.parse(execFileSync(harness.resolvePython(), [script, workspace.root, JSON.stringify(project)], {encoding: 'utf8'}));
    await page.goto(server.origin);
    await page.getByRole('button',{name:'서버 관리',exact:true}).click();
    await page.getByText('공동 작업 서버', {exact: true}).click();
    await expect(page.getByLabel('공동 작업 서버 주소')).toHaveValue(server.origin);
    await page.getByLabel('공유 계정 이름', {exact: true}).fill('vieweruser');
    await page.getByLabel('공유 계정 비밀번호', {exact: true}).fill(password);
    await page.getByRole('button', {name: '연결·로그인', exact: true}).click();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await expect(page.getByLabel('공유 계정 비밀번호', {exact: true})).toHaveCount(0);
    await expect(page.getByLabel('공동 작업 프로젝트', {exact: true})).toHaveValue(project.id);
    await page.getByRole('dialog',{name:'컴퓨팅 서버 관리',exact:true}).getByRole('button',{name:'닫기',exact:true}).click();
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(4).click();
    await expect(page.locator('[data-flow-node-id]')).toHaveCount(46);
    await evidence.screenshot(page, 'browser-cookie-session-authorized-flow');
    const refused = await page.evaluate(async () => {
      const r = await fetch('/api/project/update', {method: 'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({description:'unauthorized browser mutation'})});
      return {status:r.status,body:await r.json()};
    });
    expect(refused.status).toBe(403);
    await server.call(`/api/accounts/projects/${project.id}/members/${viewer.id}`, undefined, administrator.token, 'DELETE');
    const revoked = await page.evaluate(async id => {const r=await fetch('/api/project/current',{headers:{'X-Vision-Project':id}});return r.status;},project.id);
    expect(revoked).toBe(403);
    const cookies = await page.context().cookies(server.origin);
    expect(cookies.find(c => c.name === 'vision_session')).toMatchObject({httpOnly: true, secure: true, sameSite: 'Strict'});
    const storage=await page.evaluate(()=>JSON.stringify({local:{...localStorage},session:{...sessionStorage}}));
    for(const secret of [password,administrator.token,server.backend.token])expect(storage).not.toContain(secret);
    expect(storage).not.toMatch(/\"(?:token|csrf_token|password)\"\s*:/);
    await page.getByRole('button',{name:'서버 관리',exact:true}).click();
    await page.getByLabel('공동 작업 서버 연결 해제', {exact:true}).click();
    await page.getByText('공동 작업 서버',{exact:true}).click();
    await expect(page.getByLabel('공유 계정 이름', {exact: true})).toBeVisible();
    const loggedOut = await page.evaluate(async () => (await fetch('/api/accounts/me')).status); expect(loggedOut).toBe(401);
    await page.getByLabel('공동 작업 서버 주소').fill(server.origin);
    await page.getByLabel('공유 계정 이름',{exact:true}).fill('administrator');
    await page.getByLabel('공유 계정 비밀번호',{exact:true}).fill(password);
    await page.getByRole('button',{name:'연결·로그인',exact:true}).click();
    await expect(page.getByLabel('공동 작업 프로젝트',{exact:true})).toHaveValue(project.id);
    await page.getByRole('dialog',{name:'컴퓨팅 서버 관리',exact:true}).getByRole('button',{name:'닫기',exact:true}).click();
    await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();
    await page.getByRole('button',{name:'검증된 데이터 버전',exact:true}).click();
    const uploadDialog=page.getByRole('dialog',{name:'검증된 데이터 버전'});
    const archive=storedZip([['train/OK/browser.png',fs.readFileSync(fixture.files[0].path)]]);
    const archivePath=path.join(workspace.root,'browser-upload.zip');fs.writeFileSync(archivePath,archive);
    await uploadDialog.getByLabel('가져올 ZIP 파일').setInputFiles(archivePath);
    const uploaded=page.waitForResponse(r=>/\/api\/artifacts\/uploads\/[a-f0-9]+\/complete$/.test(new URL(r.url()).pathname));
    await uploadDialog.getByRole('button',{name:'ZIP 올리고 검증',exact:true}).click();
    const uploadResponse=await uploaded;expect(uploadResponse.status()).toBe(200);
    const artifact=(await uploadResponse.json()).artifact_ref;
    await expect(uploadDialog.getByRole('region',{name:'현재 가져오기'})).toContainText('검증 완료 · 채택 전',{timeout:30000});
    const download=await page.evaluate(async ref=>{
      const r=await fetch(`/api/artifacts/${ref.id}/content?revision=${ref.revision}&sha256=${ref.sha256}`);
      return {status:r.status,bytes:Array.from(new Uint8Array(await r.arrayBuffer()))};
    },artifact);
    expect(download.status).toBe(200);expect(Buffer.from(download.bytes)).toEqual(archive);
    for(const file of fixture.files)expect(crypto.createHash('sha256').update(fs.readFileSync(file.path)).digest('hex')).toBe(file.sha256);
    expect(crypto.createHash('sha256').update(fs.readFileSync(graph.graph_file)).digest('hex')).toBe(graph.graph_sha256);
    expect((await server.call('/api/project/current',undefined,administrator.token)).source_dataset_dir).toBe(fixture.source);
    expect((await server.call('/api/dataset/revisions',undefined,administrator.token)).active_revision).toBeNull();
    evidence.note('browser_transfer',{artifact,archive_sha256:crypto.createHash('sha256').update(archive).digest('hex'),download_status:download.status,
      upload_through_actual_ui:true,download_through_cookie_api:true,automatic_adoption:false,original_source_and_graph_unchanged:true});
    evidence.note('browser_team_session', {project, graph_sha256:graph.graph_sha256, source_images:fixture.files,
      cookie_http_only:true, secure:true, mutation_without_csrf:refused.status, membership_revoked:revoked,
      logged_out:loggedOut, actual_inference:false, production_https_deployment:false});
  } finally {
    evidence.note('browser_team_stop', await server.close());
    evidence.addFile(server.backend.logs.stdout); evidence.addFile(server.backend.logs.stderr);
  }
});
