import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {createProject, png} from './qa/appFlow';

// Real renderer, dataset store and isolated backend. Only the split HTTP failure is controlled;
// the common error registry is never replaced or accessed by this test.
test('visible dataset errors reach the common summary and clear after retry, navigation and project change', async ({page, renderer, workspace, evidence}) => {
  test.setTimeout(180_000);
  const source = path.join(workspace.root, 'common-error-source');
  const originals: Array<{path: string; sha256: string}> = [];
  const sha = (file: string) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  for (const [labelIndex, label] of ['OK', 'NG'].entries()) {
    const folder = path.join(source, label);
    fs.mkdirSync(folder, {recursive: true});
    for (let number = 0; number < 10; number++) {
      const file = path.join(folder, `${label}-${number}.png`);
      fs.writeFileSync(file, png(32, 3, (x, y) => [80 + labelIndex * 70 + number, (x + number) % 256, y]));
      originals.push({path: file, sha256: sha(file)});
    }
  }
  const api = async (route: string, body: unknown, method = 'POST') => {
    const response = await page.request.fetch(renderer.origin + route, {method, data: body});
    expect(response.ok(), `${method} ${route}: ${await response.text()}`).toBe(true);
    return response.json();
  };
  const project = await api('/api/project/create', {name: 'Common error source project', task: 'classification'});
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'classification'});

  const pageErrors: string[] = [], externalRequests: string[] = [], executionRequests: string[] = [];
  const failures: Array<{message: string; status: number; body: unknown}> = [];
  const splitRequests: unknown[] = [];
  let nextSplitError: string | null = null;
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.route(/^https?:\/\//, async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== renderer.origin) {
      externalRequests.push(request.url());
      await route.abort('blockedbyclient');
      return;
    }
    // Opening another step must not start a model, a training job, or an assistant/provider run.
    if (request.method() !== 'GET' && /^\/api\/(?:training\/start|inference\/|assistant\/|label-suggestions\/batches|labeling-jobs)/.test(url.pathname)) {
      executionRequests.push(`${request.method()} ${url.pathname}`);
      await route.abort('blockedbyclient');
      return;
    }
    if (url.pathname === '/api/dataset/split' && request.method() === 'POST') {
      const body: unknown = request.postDataJSON();
      splitRequests.push(body);
      if (nextSplitError) {
        const message = nextSplitError;
        nextSplitError = null;
        failures.push({message, status: 503, body});
        await route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({detail: message})});
        return;
      }
    }
    await route.fallback();
  });
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
  const split = page.getByRole('button', {name: '이 비율로 나누기', exact: true});
  await expect(split).toBeEnabled();
  await expect(page.getByRole('button', {name: '전체 (20)', exact: true})).toBeVisible();
  const focus = page.getByRole('button', {name: '집중 보기', exact: true});
  if (await focus.getAttribute('aria-pressed') !== 'true') await focus.click();

  const details = page.getByRole('region', {name: '단계 준비도 상세', exact: true});
  const openDetails = async () => {
    if (!await details.isVisible()) await page.getByRole('button', {name: '왜 그런가요?', exact: true}).click();
    await expect(details).toBeVisible();
  };
  const assertVisibleError = async (message: string) => {
    // The assertion starts with the primary panel's displayed alert, before opening the common list.
    const banner = page.getByRole('alert').filter({hasText: message});
    await banner.scrollIntoViewIfNeeded();
    await expect(banner).toBeVisible();
    await expect(banner).toHaveText(message);
    const displayed = (await banner.innerText()).trim();
    await openDetails();
    const common = details.getByRole('listitem').filter({hasText: displayed});
    await common.scrollIntoViewIfNeeded();
    await expect(common).toBeVisible();
    await expect(common).toHaveText(`데이터 준비: ${displayed}`);
    await expect(common.getByText('데이터 준비', {exact: true})).toBeVisible();
  };
  const assertAbsent = async (message: string) => {
    await expect(page.getByRole('alert').filter({hasText: message})).toHaveCount(0);
    await openDetails();
    await expect(details.getByRole('listitem').filter({hasText: message})).toHaveCount(0);
  };

  const retryError = '격리 QA: 데이터 분할 응답을 확인할 수 없습니다. 다시 시도하세요.';
  nextSplitError = retryError;
  await split.click();
  await assertVisibleError(retryError);
  await evidence.screenshot(page, 'dataset-primary-error-and-common-summary');
  await page.getByRole('button', {name: '자세히 닫기', exact: true}).click();
  const retried = page.waitForResponse(response => new URL(response.url()).pathname === '/api/dataset/split'
    && response.request().method() === 'POST');
  await split.click();
  const saved = await retried;
  expect(saved.status()).toBe(200);
  const savedSplit = (await saved.json()).split as {train: number; val: number; test: number};
  expect(savedSplit.train + savedSplit.val + savedSplit.test).toBe(20);
  await expect(split).toBeEnabled();
  await assertAbsent(retryError);
  await expect(details.getByText('패널 오류', {exact: true})).toHaveCount(0);

  await page.getByRole('button', {name: '자세히 닫기', exact: true}).click();
  const scopedError = '격리 QA: 이전 프로젝트 범위의 분할 실패입니다.';
  nextSplitError = scopedError;
  await split.click();
  await assertVisibleError(scopedError);
  await page.getByRole('button', {name: '다음 단계 보기', exact: true}).click();
  await expect(page.getByRole('navigation', {name: '작업 단계 (Workflow Stages)', exact: true})
    .getByRole('button', {name: /^02/})).toHaveAttribute('aria-current', 'step');
  await assertAbsent(scopedError);

  // Returning to the same source still has its unresolved primary error; navigating only stopped displaying it.
  await page.getByRole('button', {name: '자세히 닫기', exact: true}).click();
  await page.getByRole('button', {name: '이전 단계 보기', exact: true}).click();
  await expect(split).toBeEnabled();
  await assertVisibleError(scopedError);
  await page.getByRole('button', {name: '자세히 닫기', exact: true}).click();
  await createProject(page, 'Common error clean project', '분류');
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText('Common error clean project');
  await assertAbsent(scopedError);
  await assertAbsent(retryError);
  await expect(details.getByText('패널 오류', {exact: true})).toHaveCount(0);
  await evidence.screenshot(page, 'common-summary-after-project-scope-change');

  expect(failures.map(failure => failure.message)).toEqual([retryError, scopedError]);
  expect(splitRequests).toHaveLength(3);
  for (const body of splitRequests) expect(body).toMatchObject({folder_path: source, task: 'classification'});
  for (const original of originals) expect(sha(original.path)).toBe(original.sha256);
  expect(pageErrors).toEqual([]);
  expect(externalRequests).toEqual([]);
  expect(executionRequests).toEqual([]);
  evidence.note('assistant_common_errors', {
    actual_renderer_dataset_store_and_owned_backend: true,
    controlled_same_origin_split_failures: failures,
    primary_alert_and_common_source_and_text_matched: true,
    retry_saved_real_split: savedSplit,
    retry_navigation_and_project_scope_cleanup: true,
    previous_project_id: project.id,
    source_images: originals,
    split_request_count: splitRequests.length,
    page_errors: pageErrors,
    external_requests: externalRequests,
    model_training_or_assistant_execution_requests: executionRequests,
    real_credentials_or_provider_bridge: false,
    native_electron_qa: false,
  });
});
