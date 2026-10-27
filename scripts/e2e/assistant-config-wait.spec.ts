import type {AssistantBridge, AssistantConfigState} from '../../src/types/electron';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

// Real panel and store with a deferred public bridge reply. No native storage,
// account login, provider request, model or user-owned project is exercised.
test('assistant settings deadline, stop and reopen keep one read and reject retired replies', async ({page, renderer, evidence}) => {
  const failures: string[] = [], external: string[] = [];
  page.on('pageerror', error => failures.push(error.message));
  await page.route(/^https?:\/\//, async route => {
    const url = new URL(route.request().url());
    if (url.origin === renderer.origin) return route.fallback();
    external.push(url.origin);
    return route.abort('blockedbyclient');
  });
  await installDesktopHostShim(page, renderer.port);
  await page.goto(renderer.url);
  await page.evaluate(() => {
    const calls: string[] = [];
    const config: AssistantConfigState = {active: null, providers: [], encryptionAvailable: true,
      chatgpt: {accounts: [], active: null, encryptionAvailable: true, status: 'idle', authority: '0'}};
    let resolveRead: ((state: AssistantConfigState) => void) | undefined;
    const unexpected = async (): Promise<never> => {calls.push('unexpected-operation'); throw Error('No operation is allowed by this settings-read fixture');};
    const bridge: AssistantBridge = {
      getState: () => {calls.push('getState'); return new Promise(resolve => {resolveRead = resolve;});},
      saveProvider: unexpected, removeKey: unexpected, removeProvider: unexpected,
      setActive: unexpected, listModels: unexpected, send: unexpected, cancel: unexpected,
      imageIntent: unexpected, beginRun: unexpected, endRun: unexpected,
      chatgpt: {begin: unexpected, cancel: unexpected, selectAccount: unexpected,
        selectModel: unexpected, signOut: unexpected, acknowledgePlanUsage: unexpected,
        resumeUsage: unexpected, recover: unexpected},
    };
    if (!window.api) throw Error('Expected the isolated desktop-host bridge');
    window.api.assistant = bridge;
    Object.assign(window, {__assistantWaitQA: {calls, resolve: () => {
      if (!resolveRead) throw Error('No pending read');
      resolveRead(structuredClone(config));
    }}});
  });
  await page.getByRole('button', {name: '왜 그런가요?', exact: true}).click();
  await page.getByRole('button', {name: '도우미에게 묻기', exact: true}).click();
  const panel = page.getByRole('complementary', {name: '도우미', exact: true});
  const connection = panel.locator('section[aria-label="연결 상태 확인"]');
  const pending = connection.getByRole('status').filter({hasText: '연결 상태를 확인하는 중입니다.'});
  const stop = connection.getByRole('button', {name: '설정 확인 대기 중지', exact: true});
  const unverified = connection.getByRole('alert');
  await expect(pending).toBeVisible();
  await expect(unverified).toBeVisible({timeout: 20_000});
  await expect(pending).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__assistantWaitQA.calls)).toEqual(['getState']);
  await connection.getByRole('button', {name: '연결 상태 다시 확인', exact: true}).click();
  await expect(pending).toBeVisible();
  await stop.click();
  await expect(unverified).toBeVisible();
  await connection.getByRole('button', {name: '연결 상태 다시 확인', exact: true}).click();
  await expect(pending).toBeVisible();
  await panel.getByRole('button', {name: '닫기', exact: true}).click();
  await expect(panel).toHaveCount(0);
  await page.getByRole('button', {name: '왜 그런가요?', exact: true}).click();
  await page.getByRole('button', {name: '도우미에게 묻기', exact: true}).click();
  await expect(pending).toBeVisible();
  expect(await page.evaluate(() => (window as any).__assistantWaitQA.calls)).toEqual(['getState']);
  await stop.click();
  await page.evaluate(() => (window as any).__assistantWaitQA.resolve());
  await expect(unverified).toBeVisible();
  await expect(pending).toHaveCount(0);
  await expect(panel.getByRole('textbox', {name: '도우미에게 묻기', exact: true})).toBeDisabled();
  await evidence.screenshot(page, 'assistant-retired-settings-reply-keeps-unverified-state');
  await panel.getByRole('button', {name: '닫기', exact: true}).click();
  await expect(panel).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__assistantWaitQA.calls)).toEqual(['getState']);
  expect(failures).toEqual([]);
  expect(external).toEqual([]);
  evidence.note('assistant_config_wait', {public_bridge_reads: 1, deadline_wait_observed: true, stop_recheck_close_reopen: true,
    retired_reply_not_accepted: true, provider_requests: 0, actual_OS_crypto: false,
    actual_account_login: false, actual_model_execution: false});
});
