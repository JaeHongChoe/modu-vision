import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {Page} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {expect,test,type Evidence,type Workspace} from './fixtures/test';

test.skip(process.env.MV_E2E_GETI_PLATFORM !== '1','Owned Geti runtime qualification is not selected. Set MV_E2E_GETI_PLATFORM=1 and provide a verified Python 3.14 runtime/descriptor; a skip is not platform qualification.');

type OwnedApi = (route:string,body?:unknown,method?:string) => Promise<any>;
const digest = (bytes:Buffer) => createHash('sha256').update(bytes).digest('hex');
function sourceHashes(root:string):Record<string,string> {
  const result:Record<string,string> = {};
  const visit = (directory:string) => {for (const entry of fs.readdirSync(directory,{withFileTypes:true})) {
    const file = path.join(directory,entry.name);
    if (entry.isDirectory()) visit(file);
    else if (entry.isFile() && /\.(png|jpe?g)$/i.test(entry.name)) result[path.relative(root,file)] = digest(fs.readFileSync(file));
  }};visit(root);return result;
}

async function openTrainingStep(page:Page) {
  const simple = page.getByRole('button',{name:'간단히 보기',exact:true});
  if (await simple.isVisible()) await simple.click();
  await page.locator('[data-beginner-goal="teach"]').click();
  await page.locator('[data-beginner-step="3"]').click();
  await expect(page.getByRole('button',{name:'학습 작업대 열기 · 데이터부터 모델까지',exact:true})).toBeVisible();
}
async function openPlatform(page:Page) {
  await page.getByRole('button',{name:'학습 작업대 열기 · 데이터부터 모델까지',exact:true}).click();
  const panel = page.getByTestId('geti-platform-workspace');
  await expect(panel).toBeVisible();await expect(panel).toHaveAttribute('aria-busy','false');return panel;
}

/** Real production renderer and gateway. The sole response substitute is an explicit 503 fault injection. */
async function exercise(page:Page,api:OwnedApi,workspace:Workspace,evidence:Evidence,reload:()=>Promise<unknown>,resize:(width:number,height:number)=>Promise<unknown>,native=false) {
  const name = '합성 검사 · 데이터 준비부터 모델 결과까지 이어지는 긴 한글 프로젝트';
  const project = await api('/api/project/create',{name,task:'classification'});
  const {folder} = await api('/api/onboarding/example-dataset',{},'POST');
  expect(path.relative(workspace.root,folder)).not.toMatch(/^\.\./);
  await api('/api/project/update',{source_dataset_dir:folder},'PUT');
  await api('/api/dataset/import',{folder_path:folder,task:'classification',validate_images:true});
  const split = (await api('/api/dataset/split',{folder_path:folder,task:'classification',train_ratio:.6,val_ratio:.2,test_ratio:.2,seed:42})).split;
  const version = await api('/api/dataset/versions',{name:'학습 작업대 · 원본 고정',dataset_path:folder});
  const originals = sourceHashes(folder);expect(Object.keys(originals).length).toBeGreaterThan(0);
  const posts:Array<{path:string;body:unknown}> = [], pageErrors:string[] = [];
  page.on('pageerror',error => pageErrors.push(error.message));
  page.on('request',request => {const url = new URL(request.url());if (url.pathname.startsWith('/api/platform/geti/') && request.method() !== 'GET') posts.push({path:url.pathname,body:request.postDataJSON()});});
  await resize(1440,1024);await reload();
  await expect(page.locator('#workflow-project')).toContainText(name);
  await openTrainingStep(page);
  await page.getByText('설정·도구',{exact:true}).click();
  const light = page.getByRole('button',{name:'밝은 화면',exact:true});
  if (await light.isVisible()) await light.click();else await page.keyboard.press('Escape');
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  // Save through the existing real preview/editor, so the platform receives its scoped recipe.
  const rail = page.getByRole('tablist',{name:'학습 데이터 준비 단계'});
  await rail.getByRole('tab',{name:/사진 전처리/}).click();
  const preparation = page.getByRole('tabpanel').filter({has:page.getByRole('heading',{name:'사진 전처리',exact:true})});
  await preparation.getByRole('button',{name:'+ 밝기',exact:true}).click();
  await preparation.getByLabel('1번 밝기 배율',{exact:true}).fill('1.1');
  const previewResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/training/input-preparation/preview' && response.request().method() === 'POST');
  await preparation.getByRole('button',{name:'실제 입력 미리보기',exact:true}).click();
  const previewReply = await previewResponse;expect(previewReply.ok(),await previewReply.text()).toBe(true);
  const preview = await previewReply.json();
  await preparation.getByRole('button',{name:'규칙 저장',exact:true}).click();
  await expect(preparation).toContainText('다음 학습에 사용할 입력 규칙을 저장했습니다.');

  let panel = await openPlatform(page);
  await expect(panel.getByRole('heading',{name,exact:true})).toBeVisible();
  await expect(panel).toContainText('저장한 입력 규칙 1개: 밝기');
  expect(posts).toEqual([]);
  const initial = await api('/api/platform/geti/status');
  expect(initial.project.supported).toBe(true);expect(initial.available,initial.runtime.message).toBe(true);
  expect(initial.runtime.state).toBe('stopped');expect(initial.dataset).toBeNull();expect(initial.jobs).toEqual([]);
  const startResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/platform/geti/start' && response.request().method() === 'POST',{timeout:120_000});
  await panel.getByRole('button',{name:'학습 환경 시작',exact:true}).click();
  const started = await startResponse;expect(started.ok(),await started.text()).toBe(true);
  expect((await started.json()).runtime.state).toBe('running');
  await expect(panel.getByRole('button',{name:'학습용 데이터 준비',exact:true})).toBeEnabled();
  const prepareResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/platform/geti/prepare' && response.request().method() === 'POST',{timeout:120_000});
  await panel.getByRole('button',{name:'학습용 데이터 준비',exact:true}).click();
  const prepared = await prepareResponse;expect(prepared.ok(),await prepared.text()).toBe(true);
  const receipt = await prepared.json();
  expect(receipt.dataset_version_id).toMatch(/^v_/);expect(receipt.recipe_sha256).toBe(preview.input_recipe_sha256);
  const versions = await api('/api/dataset/versions');
  expect(versions.versions.map((row:{id:string}) => row.id)).toEqual(expect.arrayContaining([version.id,receipt.dataset_version_id]));
  expect((await api(`/api/dataset/versions/${encodeURIComponent(receipt.dataset_version_id)}/verify`)).status).toBe('verified');
  expect((await api(`/api/dataset/versions/${encodeURIComponent(version.id)}/verify`)).status).toBe('verified');
  expect(receipt.counts).toEqual({training:split.train,validation:split.val,testing:split.test,total:Object.keys(originals).length});
  await expect(panel).toContainText('학습 기준 고정됨');
  await expect(panel).not.toContainText('[object Object]');
  for (const n of [split.train,split.val,split.test]) await expect(panel.locator('.platform-counts')).toContainText(`${n.toLocaleString('ko-KR')}장`);
  await expect(panel.locator('.workspace-button--primary')).toHaveCount(1);
  await expect(panel.getByRole('heading',{name:'저장된 모델과 결과',exact:true})).toBeVisible();
  await panel.locator('summary',{hasText:'학습 기준과 데이터 식별 정보'}).click();
  await expect(panel).toContainText(receipt.dataset_version_id);await expect(panel).toContainText(receipt.recipe_sha256);
  await evidence.screenshot(page,native ? 'geti-native-real-prepared':'geti-browser-real-prepared');

  // A failed refresh preserves counts and offers a read; it cannot repeat the writes.
  let failOnce = true;
  const fault = async (route:any) => {if (failOnce) {failOnce=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'합성 503 · 현재 상태를 읽을 수 없습니다.'})});} else await route.fallback();};
  await page.route('**/api/platform/geti/status',fault);
  await panel.getByRole('button',{name:'상태 새로고침',exact:true}).click();
  await expect(panel.getByRole('alert')).toContainText('합성 503');
  await expect(panel.locator('.platform-counts')).toContainText(`${split.train}장`);
  const beforeRetry = posts.length;
  await panel.getByRole('button',{name:'상태 다시 확인',exact:true}).click();
  await expect(panel.getByRole('alert')).toHaveCount(0);
  expect(posts.length).toBe(beforeRetry);
  await page.unroute('**/api/platform/geti/status',fault);

  await page.getByRole('button',{name:'학습 작업대 닫기',exact:true}).click();
  await page.getByText('설정·도구',{exact:true}).click();
  const dark = page.getByRole('button',{name:'어두운 화면',exact:true});
  if (await dark.isVisible()) await dark.click();else await page.keyboard.press('Escape');
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  panel = await openPlatform(page);await resize(720,900);
  await expect(panel.locator('.workspace-button--primary')).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  const primary = panel.locator('.workspace-button--primary');await primary.focus();await expect(primary).toBeFocused();
  await page.keyboard.press('Tab');
  expect(await page.getByRole('dialog',{name:'학습 작업대',exact:true}).evaluate(element => element.contains(document.activeElement))).toBe(true);
  await evidence.screenshot(page,native ? 'geti-native-dark-720-keyboard':'geti-browser-dark-720-keyboard');
  // Electron cannot open AxeBuilder's analysis tab; the same axe rules run in its owned renderer frame.
  const accessibility = await new AxeBuilder({page}).setLegacyMode(native).include('[data-testid="geti-platform-workspace"]').withTags(['wcag2a','wcag2aa']).analyze();
  evidence.note('geti_platform_accessibility',accessibility.violations.map(violation => ({id:violation.id,impact:violation.impact,targets:violation.nodes.map(node => node.target)})));
  expect(accessibility.violations.filter(violation => violation.impact === 'critical' || violation.impact === 'serious')).toEqual([]);
  await page.getByRole('button',{name:'학습 작업대 닫기',exact:true}).click();
  await resize(1440,1024);

  // The native fallback keeps the distinct semantic task and all ten existing model families reachable.
  panel = await openPlatform(page);
  let capturedOldRead = false, retiredReadOutcome = '', releaseOldRead!:() => void, finishOldRead!:() => void;
  const release = new Promise<void>(resolve => {releaseOldRead=resolve;}), finished = new Promise<void>(resolve => {finishOldRead=resolve;});
  const held = async (route:any) => {
    if (capturedOldRead) {await route.fallback();return;}
    const actual = await route.fetch();capturedOldRead=true;
    await release;
    try {await route.fulfill({response:actual});retiredReadOutcome='delivered_after_retirement';} catch {retiredReadOutcome='request_retired';} finally {finishOldRead();}
  };
  await page.route('**/api/platform/geti/status',held);
  await panel.getByRole('button',{name:'상태 새로고침',exact:true}).click();
  await expect.poll(() => capturedOldRead).toBe(true);
  await page.getByRole('button',{name:'학습 작업대 닫기',exact:true}).click();
  let other:any;
  try {other = await api('/api/project/create',{name:'합성 · 시맨틱 분할은 기존 모델로',task:'segmentation'});await reload();}
  finally {releaseOldRead();await finished;await page.unroute('**/api/platform/geti/status',held);}
  await expect(page.locator('#workflow-project')).toContainText(other.name);await openTrainingStep(page);
  panel = await openPlatform(page);await expect(panel).toContainText('시맨틱 분할은 Geti의 개체별 분할과 다릅니다.');
  await expect(panel.locator('.platform-counts')).toHaveCount(0);
  await panel.getByRole('button',{name:'개체별 영역 분할로 학습 · 개별 다각형 라벨 필요',exact:true}).click();
  await expect(panel).toContainText('이번 플랫폼 학습: 개체별 영역 분할');
  expect((await api('/api/project/current')).task).toBe('segmentation');
  await panel.getByRole('button',{name:'기존 시맨틱 분할 경로 유지',exact:true}).click();
  await expect(panel).toContainText('시맨틱 분할은 Geti의 개체별 분할과 다릅니다.');
  await panel.getByRole('button',{name:'기존 모델 도구 열기',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'학습 작업대',exact:true})).toHaveCount(0);
  await page.getByRole('tablist',{name:'학습 데이터 준비 단계'}).getByRole('tab',{name:/모델과 학습/}).click();
  const modelPicker = page.locator('.training-family-catalog__picker');
  await modelPicker.locator('summary').click();await expect(modelPicker.getByRole('radio')).toHaveCount(10);
  await evidence.screenshot(page,native ? 'geti-native-specialist-fallback':'geti-browser-specialist-fallback');
  expect(sourceHashes(folder)).toEqual(originals);
  expect(posts.map(post => post.path)).toEqual(['/api/platform/geti/start','/api/platform/geti/prepare']);
  expect(posts[1].body).toMatchObject({input_recipe:{version:1,operations:[{op:'brightness',factor:1.1}]}});
  expect(pageErrors).toEqual([]);
  evidence.note('geti_platform_journey',{source_project_id:project.id,native_project_id:other.id,version_id:version.id,receipt_id:receipt.id,recipe_sha256:receipt.recipe_sha256,counts:receipt.counts,posts,source_images_unchanged:true,stale_read_handoff:{captured_old_project_status:capturedOldRead,outcome:retiredReadOutcome,foreign_receipt_hidden:true},synthetic_only:true,actual_parent_and_child:true,actual_dataset_copy_readback:true,training:false,weights_download:false,gpt:false,installed_user_app_or_web:false});
}

test('integrated Geti workspace prepares real frozen data and retains guarded native paths',async({page,request,renderer,workspace,evidence}) => {
  test.setTimeout(300_000);
  const api:OwnedApi = async (route,body,method) => {
    const response = await request.fetch(renderer.origin+route,{method:method || (body === undefined ? 'GET':'POST'),...(body === undefined ? {} : {data:body})});
    expect(response.ok(),`${route}: ${await response.text()}`).toBe(true);return response.json();
  };
  await exercise(page,api,workspace,evidence,() => page.goto(renderer.url),(width,height) => page.setViewportSize({width,height}));
});

test('native integrated Geti workspace starts its owned child and prepares synthetic data',{tag:'@electron'},async({electronSession,workspace,evidence}) => {
  test.setTimeout(420_000);
  const backend = await electronSession.waitForBackend(), page = electronSession.window;
  const api:OwnedApi = (route,body,method) => page.evaluate(async ({origin,route,body,method}) => {
    const response = await fetch(origin+route,{method:method || (body === undefined ? 'GET':'POST'),...(body === undefined ? {} : {headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    if (!response.ok) throw new Error(`Owned platform fixture API ${response.status}: ${await response.text()}`);return response.json();
  },{origin:`http://127.0.0.1:${backend.port}`,route,body,method});
  await exercise(page,api,workspace,evidence,() => page.reload(),(width,height) => electronSession.app.evaluate(({BrowserWindow},{width,height}) => BrowserWindow.getAllWindows()[0].setSize(width,height),{width,height}),true);
});
