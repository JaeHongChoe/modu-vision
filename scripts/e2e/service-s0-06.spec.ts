import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from './fixtures/test';

// S0-06: renderer-side recovery and security candidates judged in the real
// renderer and Electron main process. Shell actions are stubbed in the main
// process so no application or document is actually opened.

test('renderer policy allows a configured HTTPS shared server but not arbitrary plain HTTP', async ({ page, renderer }) => {
  const invalidPolicySources: string[] = [];
  page.on('console', message => {
    if (message.text().includes('contains an invalid source')) invalidPolicySources.push(message.text());
  });
  await page.goto(renderer.url);
  await expect(page.getByRole('navigation', { name: '프로젝트 작업 공간' })).toBeVisible();
  expect(invalidPolicySources, 'the browser accepts every configured CSP source').toEqual([]);
  const violations = await page.evaluate(async () => {
    const blocked: string[] = [];
    document.addEventListener('securitypolicyviolation', event => blocked.push(`${event.effectiveDirective} ${event.blockedURI}`));
    await fetch('https://shared.example.invalid/api/project/list').catch(() => undefined);
    await fetch('http://plain.example.invalid/api/project/list').catch(() => undefined);
    try { new WebSocket('wss://shared.example.invalid/ws/telemetry').close(); } catch { /* reported as a violation */ }
    const image = new Image();
    image.src = 'https://shared.example.invalid/api/dataset/thumbnail.png';
    await new Promise(resolve => setTimeout(resolve, 1000));
    return blocked;
  });
  expect(violations.filter(item => item.includes('shared.example.invalid')), 'HTTPS/WSS shared server is reachable').toEqual([]);
  expect(violations.some(item => item.includes('plain.example.invalid')), 'plain HTTP to other hosts stays blocked').toBe(true);
});

test.describe('electron main process boundaries', () => {
  test('renderer is sandboxed and shell opening is limited to links, folders and report files', { tag: '@electron' }, async ({
    electronSession, workspace, evidence,
  }) => {
    await electronSession.waitForBackend();
    const { app, window } = electronSession;
    const sandboxed = await app.evaluate(({ BrowserWindow }) => (BrowserWindow.getAllWindows()[0].webContents as any).getLastWebPreferences()?.sandbox);
    expect(sandboxed, 'renderer process sandbox').toBe(true);

    await app.evaluate(({ shell }) => {
      const opened: string[][] = [];
      (globalThis as any).__harnessOpened = opened;
      shell.openExternal = async (url: string) => { opened.push(['external', url]); };
      shell.openPath = async (target: string) => { opened.push(['path', target]); return ''; };
    });
    const report = path.join(workspace.root, 'report.html');
    fs.writeFileSync(report, '<!doctype html><title>report</title>');
    const script = path.join(workspace.root, process.platform === 'win32' ? 'run.bat' : 'run.command');
    fs.writeFileSync(script, 'echo harness');
    const bundle = path.join(workspace.root, 'Fake.app');
    fs.mkdirSync(bundle);
    const unnamedBundle = path.join(workspace.root, 'Looks Like A Folder');
    fs.mkdirSync(path.join(unnamedBundle, 'Contents'), { recursive: true });
    fs.writeFileSync(path.join(unnamedBundle, 'Contents', 'Info.plist'), '<plist/>');
    const alias = path.join(workspace.root, 'notes.pdf');
    fs.writeFileSync(alias, Buffer.concat([Buffer.from('book\0\0\0\0mark\0\0\0\0', 'latin1'), Buffer.alloc(32)]));
    const cases: Array<[string, boolean]> = [
      ['https://pytorch.org/get-started/locally/', true],
      [workspace.dataset, true],
      [report, true],
      [`file://${report.replace(/\\/g, '/')}`, true],
      [script, false],
      [`file://${script.replace(/\\/g, '/')}`, false],
      [bundle, false],
      [unnamedBundle, false],
      [alias, false],
      ['\\\\fileserver\\share\\report.html', false],
      ['//fileserver/share/report.html', false],
      ['file://fileserver/share/report.html', false],
      ['package.json', false],
      ['javascript:alert(1)', false],
    ];
    const results: Array<[string, boolean]> = [];
    for (const [target] of cases) {
      results.push([target, await window.evaluate(value => (globalThis as any).api.openExternal(value), target)]);
    }
    const opened = await app.evaluate(() => (globalThis as any).__harnessOpened as string[][]);
    evidence.note('open_external_cases', { results, opened });
    expect(results).toEqual(cases);
    const openedTargets = opened.map(([, target]) => target);
    expect(openedTargets.some(target => target.endsWith('run.command') || target.endsWith('run.bat'))).toBe(false);
    expect(openedTargets.some(target => target.endsWith('Fake.app'))).toBe(false);

    // HTTPS/WSS is admitted by the CSP for shared servers, but the main process
    // cancels requests to any origin other than the connected server.
    const failures: string[] = [];
    window.on('requestfailed', request => failures.push(`${request.url()} ${request.failure()?.errorText}`));
    await window.evaluate(() => fetch('https://unconnected.example.invalid/api/project/list').catch(() => undefined));
    await expect.poll(() => failures.join('\n')).toContain('ERR_BLOCKED_BY_CLIENT');
    evidence.note('blocked_https', failures);
  });
});
