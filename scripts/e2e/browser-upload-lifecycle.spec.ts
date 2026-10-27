import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {test, expect} from './fixtures/test';
import {browserTeamServer} from './fixtures/browser_team_server';
import {expectBrowserLoginGate,openBrowserServerManagement} from './fixtures/browserGateLogin';
import {storedZip} from './fixtures/storedZip';

test.use({ignoreHTTPSErrors: true, actionTimeout: 15_000});

test('empty HTTPS browser project uploads and explicitly adopts its own images', async ({
  page, rendererBuild, workspace, evidence, loopbackGuard,
}) => {
  test.setTimeout(180_000);
  const server = await browserTeamServer(workspace, rendererBuild.outDir);
  loopbackGuard.allow(server.port);
  evidence.redact(server.backend.token);
  const password = 'owned browser roundtrip password 123';
  evidence.redact(password);
  try {
    await server.call('/api/accounts/bootstrap', {username: 'webowner', password});
    const owner = await server.call('/api/accounts/login', {username: 'webowner', password});
    evidence.redact(owner.token);
    const project = await server.call('/api/project/create', {name: 'Empty web upload', task: 'classification'}, owner.token);
    evidence.note('project_before_upload', project);
    await page.goto(server.origin);
    await expectBrowserLoginGate(page,server.origin);
    await page.getByLabel('공유 계정 이름', {exact: true}).fill('webowner');
    await page.getByLabel('공유 계정 비밀번호', {exact: true}).fill(password);
    await page.getByRole('button', {name: '연결·로그인', exact: true}).click();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await openBrowserServerManagement(page);
    await expect(page.getByLabel('공동 작업 프로젝트', {exact: true})).toBeEnabled();
    await page.getByRole('dialog', {name: '컴퓨팅 서버 관리', exact: true}).getByRole('button', {name: '닫기', exact: true}).click();
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click();
    await evidence.screenshot(page, 'empty-web-project-before-upload');
    const intake = page.getByRole('button', {name: '검증된 데이터 버전', exact: true});
    await expect(intake, 'An empty web project must accept a ZIP without requiring a server-local folder first').toBeEnabled();
    await intake.click();
    const dialog = page.getByRole('dialog', {name: '검증된 데이터 버전', exact: true});
    const bytes = storedZip(workspace.images.map(image => [`${image.label}/${path.basename(image.path)}`, fs.readFileSync(image.path)] as [string, Buffer]));
    const archive = path.join(workspace.root, 'browser-images.zip');
    fs.writeFileSync(archive, bytes);
    await dialog.getByLabel('가져올 ZIP 파일').setInputFiles(archive);
    await dialog.getByRole('button', {name: 'ZIP 올리고 검증', exact: true}).click();
    await expect(dialog.getByRole('region', {name: '현재 가져오기'})).toContainText('검증 완료 · 채택 전', {timeout: 30_000});
    await dialog.getByRole('button', {name: '이 버전 채택', exact: true}).click();
    const accepted = page.waitForResponse(r => /\/api\/dataset\/imports\/[^/]+\/accept$/.test(new URL(r.url()).pathname) && r.request().method() === 'POST');
    await dialog.getByRole('button', {name: '채택 확인', exact: true}).click();
    const response = await accepted;
    expect(response.ok(), await response.text()).toBe(true);
    const adoption = await response.json();
    const completedImport = await server.call(`/api/dataset/imports/${adoption.job_id}`, undefined, owner.token);
    const approved = await server.call('/api/project/current', undefined, owner.token);
    evidence.note('after_archive_accept', {adoption, completedImport, current: approved});
    expect(approved.source_dataset_dir).toBe(project.source_dataset_dir);
    const useSource = dialog.getByRole('button', {name: '이 ZIP으로 작업 시작', exact: true});
    await expect(useSource, 'An approved uploaded ZIP must have an explicit path into the labeling/training workspace').toBeEnabled();
    await useSource.click();
    await expect.poll(async () => (await server.call('/api/project/current', undefined, owner.token)).source_dataset_dir).toBe(completedImport.source.root);
    await expect(dialog).toBeHidden();
    const current = await server.call('/api/project/current', undefined, owner.token);
    expect(current.source_dataset_dir).not.toBe(project.source_dataset_dir);
    const thumbnails = page.locator('button[aria-label$="라벨링에서 열기"] img');
    await expect(thumbnails).toHaveCount(workspace.images.length);
    await expect.poll(async () => thumbnails.evaluateAll(images => images.every(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    await evidence.screenshot(page, 'web-project-after-explicit-upload-adoption');
    evidence.note('browser_upload', {project_id: project.id, current, archive_sha256: crypto.createHash('sha256').update(bytes).digest('hex'), actual_ui_upload: true, actual_ui_adoption: true});
  } finally {
    evidence.note('owned_server_stop', await server.close());
    evidence.addFile(server.backend.logs.stdout);
    evidence.addFile(server.backend.logs.stderr);
  }
});
