import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { _electron as electron, expect, test as base, type ElectronApplication, type Page } from '@playwright/test';

// The CommonJS core is shared with the Node bootstrap checks.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const harness = require('./harness.cjs');

export type Workspace = {
  root: string; userData: string; projects: string; home: string; dataset: string; logs: string;
  images: Array<{ label: string; path: string; bytes: number; sha256: string }>;
};

export type OwnedBackend = {
  pid: number; port: number; baseUrl: string; health: Record<string, unknown>; portPolicy: string;
  logs: { stdout: string; stderr: string }; startedAt: string;
};

export type RendererServer = {
  url: string; port: number; origin: string;
  stats(): { served: number; proxied: number; rejected: number; foreignOrigin: number; rejectedPaths: Array<{ status: number; path: string }> };
};

export type ElectronSession = {
  app: ElectronApplication;
  window: Page;
  appDir: string;
  waitForBackend(timeoutMs?: number): Promise<{ port: number; pid: number | null; state: string; health: Record<string, unknown> }>;
};

export type Evidence = {
  note(key: string, value: unknown): void;
  addFile(file: string): void;
  redact(secret: string): void;
  screenshot(target: Page, name: string): Promise<string>;
};

export type LoopbackGuard = {
  allow(port: number): void; isAllowed(port: number): boolean; recordBlocked(url: string): void; blocked(): string[];
};

type TestFixtures = {
  evidence: Evidence;
  loopbackGuard: LoopbackGuard;
  workspace: Workspace;
  backend: OwnedBackend;
  renderer: RendererServer;
  electronSession: ElectronSession;
};

type Build = { outDir: string; index: { path: string; sha256?: string } };
type WorkerFixtures = { rendererBuild: Build; electronBuild: { appDir: string; outputs: unknown[] } };

const LOOPBACK = /^(https?|wss?):\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?\//i;

function toolVersion(file: string): string | null {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')).version ?? null; } catch { return null; }
}

function pythonVersion(python: string): string | null {
  try {
    return execFileSync(python, ['--version'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim() || null;
  } catch {
    return null;
  }
}

const runDir = () => process.env.MV_E2E_RUN_DIR || path.join(os.tmpdir(), 'modu-vision-e2e', 'unconfigured');

export const test = base.extend<TestFixtures, WorkerFixtures>({
  rendererBuild: [async ({}, use, workerInfo) => {
    const prebuilt = process.env.MV_E2E_RENDERER_DIR;
    if (prebuilt) {
      await use({ outDir: path.resolve(prebuilt), index: harness.describeFile(path.join(prebuilt, 'index.html')) });
      return;
    }
    const appDir = path.join(runDir(), `app-w${workerInfo.workerIndex}`);
    await use(await harness.buildRenderer({ outDir: path.join(appDir, 'dist'), logFile: path.join(appDir, 'renderer-build.log') }));
  }, { scope: 'worker', timeout: 300_000 }],

  // Main and preload are compiled into the run folder next to the renderer, so
  // the checkout's own dist outputs are never rewritten by a test run.
  electronBuild: [async ({ rendererBuild }, use, workerInfo) => {
    let appDir = path.dirname(rendererBuild.outDir);
    if (path.basename(rendererBuild.outDir) !== 'dist' || !appDir.startsWith(runDir())) {
      appDir = path.join(runDir(), `electron-app-w${workerInfo.workerIndex}`);
      await harness.buildRenderer({ outDir: path.join(appDir, 'dist'), logFile: path.join(appDir, 'renderer-build.log') });
    }
    await use(await harness.buildElectronApp({ appDir, logFile: path.join(appDir, 'electron-build.log') }));
  }, { scope: 'worker', timeout: 300_000 }],

  evidence: async ({}, use, testInfo) => {
    const python = harness.resolvePython();
    const secrets: string[] = [];
    const record: Record<string, unknown> = {
      receipt: 'HarnessReceipt',
      contract: 'E2EHarness',
      mode: testInfo.project.name === 'electron' ? 'electron' : 'browser',
      test: testInfo.titlePath.join(' › '),
      project: testInfo.project.name,
      source: harness.sourceIdentity(),
      platform: { os: process.platform, arch: process.arch, release: os.release() },
      // An unpackaged run never stands in for the Windows native install gate.
      windows_native_gate: process.platform === 'win32'
        ? 'windows_unpackaged_run_native_install_not_covered' : 'not_a_windows_run',
      versions: {
        node: process.version,
        playwright: toolVersion(path.join(harness.REPO_ROOT, 'node_modules', '@playwright', 'test', 'package.json')),
        electron: toolVersion(path.join(harness.REPO_ROOT, 'node_modules', 'electron', 'package.json')),
        python: pythonVersion(python),
      },
      python,
      screenshots: [] as string[],
    };
    const files: string[] = [];
    await use({
      note: (key, value) => { record[key] = value; },
      addFile: file => { files.push(file); },
      redact: secret => { secrets.push(secret); },
      screenshot: async (target, name) => {
        const file = testInfo.outputPath(`${name}.png`);
        await target.screenshot({ path: file, fullPage: false });
        (record.screenshots as string[]).push(file);
        files.push(file);
        await testInfo.attach(name, { path: file, contentType: 'image/png' });
        return file;
      },
    });
    record.status = testInfo.status;
    record.expected_status = testInfo.expectedStatus;
    record.result = testInfo.status === testInfo.expectedStatus ? 'pass' : 'fail';
    const manifestPath = testInfo.outputPath('harness-manifest.json');
    harness.writeManifest(manifestPath, { ...record, files }, { redact: secrets });
    await testInfo.attach('harness-manifest', { path: manifestPath, contentType: 'application/json' });
  },

  loopbackGuard: async ({}, use) => {
    const allowed = new Set<number>();
    const blocked: string[] = [];
    await use({
      allow: port => { allowed.add(port); },
      isAllowed: port => allowed.has(port),
      recordBlocked: url => { blocked.push(url); },
      blocked: () => [...blocked],
    });
  },

  page: async ({ page, evidence, loopbackGuard }, use) => {
    const pageErrors: string[] = [];
    const consoleErrors: string[] = [];
    // The renderer falls back to port 8000 without ?port=, which on a shared
    // machine could reach another application's backend. Only loopback ports
    // registered by fixtures are reachable from the page.
    const portOf = (url: string) => Number(new URL(url).port || (/^(https|wss):/i.test(url) ? 443 : 80));
    await page.context().route(LOOPBACK, route => {
      const url = route.request().url();
      if (loopbackGuard.isAllowed(portOf(url))) return route.continue();
      loopbackGuard.recordBlocked(url);
      return route.abort('blockedbyclient');
    });
    await page.context().routeWebSocket(LOOPBACK, socket => {
      if (loopbackGuard.isAllowed(portOf(socket.url()))) socket.connectToServer();
      else { loopbackGuard.recordBlocked(socket.url()); socket.close(); }
    });
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    await use(page);
    evidence.note('browser', { name: page.context().browser()?.browserType().name(), version: page.context().browser()?.version() });
    evidence.note('page_errors', pageErrors);
    evidence.note('console_errors', consoleErrors);
    evidence.note('blocked_loopback_requests', loopbackGuard.blocked());
  },

  workspace: async ({ evidence }, use, testInfo) => {
    // Short paths keep nested backend and Windows paths under MAX_PATH.
    const id = crypto.createHash('sha256').update(testInfo.testId).digest('hex').slice(0, 8);
    const root = path.join(runDir(), 'ws', `${testInfo.workerIndex}-${id}-r${testInfo.retry}`);
    const workspace: Workspace = harness.createWorkspace(root);
    evidence.note('workspace', { root: workspace.root, images: workspace.images });
    await use(workspace);
  },

  backend: async ({ workspace, evidence }, use) => {
    const python = harness.resolvePython();
    const isolateHome = process.env.MV_E2E_ISOLATE_HOME !== '0';
    const command = harness.backendCommand({ python, workspace, isolateHome });
    const backend = await harness.startOwnedBackend({
      ...command, timeoutMs: Number(process.env.MV_E2E_BACKEND_TIMEOUT_MS || 180_000),
    });
    evidence.redact(backend.token);
    evidence.note('backend', {
      pid: backend.pid, port: backend.port, port_policy: backend.portPolicy, health: backend.health,
      started_at: backend.startedAt, python, isolate_home: isolateHome, project_dir: workspace.projects,
    });
    try {
      await use(backend);
    } finally {
      const healthyAtEnd = backend.running()
        ? await harness.httpGet(`${backend.baseUrl}/health`).then((r: { status: number }) => r.status === 200, () => false)
        : false;
      const stopped = await backend.stop();
      const portClosed = await harness.waitForPortClosed(backend.port, 10_000);
      const pidAlive = harness.processAlive(backend.pid);
      evidence.note('backend_stop', { ...stopped, healthy_at_end: healthyAtEnd, port_closed: portClosed, pid_alive: pidAlive });
      evidence.addFile(backend.logs.stdout);
      evidence.addFile(backend.logs.stderr);
      const problems = [
        stopped.exitedBeforeStop && 'backend exited before the test finished',
        !stopped.exited && 'backend did not exit',
        !portClosed && 'backend port stayed open',
        pidAlive && 'backend process is still alive',
        stopped.groupAlive && 'backend process group survived',
        stopped.escaped.length && `${stopped.escaped.length} worker(s) outside the process group referenced the workspace`,
        stopped.scanError && `process listing failed: ${stopped.scanError}`,
      ].filter(Boolean);
      if (problems.length) throw new Error(`Owned backend did not stop cleanly: ${problems.join('; ')}`);
    }
  },

  renderer: async ({ rendererBuild, backend, evidence, loopbackGuard }, use) => {
    const server = await harness.startRendererServer({ staticDir: rendererBuild.outDir, backend });
    loopbackGuard.allow(server.port);
    evidence.note('renderer', { url: server.url, build: rendererBuild });
    try {
      await use(server);
    } finally {
      evidence.note('renderer_requests', server.stats());
      await server.close();
    }
  },

  electronSession: async ({ workspace, evidence, electronBuild }, use) => {
    const devServerUrl = await harness.closedLoopbackUrl();
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const electronPath: string = require('electron');
    const options = harness.electronLaunchOptions({
      appDir: electronBuild.appDir, workspace, devServerUrl, electronPath,
      isolateHome: process.env.MV_E2E_ELECTRON_ISOLATE_HOME === '1',
    });
    evidence.note('electron', { build: electronBuild, user_data: workspace.userData, dev_server_url: devServerUrl });
    const mainLog = path.join(workspace.logs, 'electron-main.log');
    const app = await electron.launch({ ...options, timeout: 60_000 });
    const electronPid = app.process().pid;
    const logStream = fs.createWriteStream(mainLog, { flags: 'a' });
    app.process().stdout?.pipe(logStream, { end: false });
    app.process().stderr?.pipe(logStream, { end: false });
    const seenBackends = new Map<number, { port: number; pid: number | null }>();
    const readStatus = async (page: Page) => {
      if (page.isClosed()) throw new Error('Native application window closed before its backend status could be read');
      try {
        return await page.evaluate(() => (globalThis as any).api?.getBackendStatus?.() ?? null);
      } catch (error) {
        // A navigation can temporarily replace a live execution context. A
        // closed native window cannot recover inside this owned session.
        if (page.isClosed()) throw new Error('Native application window closed while reading its backend status', {cause: error});
        if (/Execution context was destroyed|navigation/i.test(String(error))) return null;
        throw error;
      }
    };
    let appWindow: Page | null = null;
    try {
      appWindow = await app.firstWindow({ timeout: 60_000 });
      // In an unpackaged run the main process first tries the dev server URL and
      // then falls back to the built renderer; wait for that navigation to settle.
      await appWindow.waitForURL(url => url.protocol === 'file:', { timeout: 60_000 });
      await appWindow.waitForLoadState('domcontentloaded');
      const window = appWindow;
      const session: ElectronSession = {
        app,
        window,
        appDir: electronBuild.appDir,
        async waitForBackend(timeoutMs = 180_000) {
          const deadline = Date.now() + timeoutMs;
          let status: any = null;
          while (Date.now() < deadline) {
            status = await readStatus(window);
            // The preload bridge returns { port, healthy, pid, device }.
            if (status?.healthy === true && status.port) break;
            await new Promise(resolve => setTimeout(resolve, 500));
          }
          expect(status?.healthy, `Electron supervisor reports a healthy backend: ${JSON.stringify(status)}`).toBe(true);
          const response = await harness.httpGet(`http://127.0.0.1:${status.port}/health`);
          const health = JSON.parse(response.body);
          seenBackends.set(status.port, { port: status.port, pid: status.pid ?? null });
          evidence.note('electron_backend', { port: status.port, pid: status.pid ?? null, status, health });
          return { port: status.port, pid: status.pid ?? null, state: 'HEALTHY', health };
        },
      };
      await use(session);
    } finally {
      // Re-read the backend identity just before closing to catch supervisor restarts.
      const finalStatus = appWindow ? await readStatus(appWindow).catch(() => null) : null;
      if (finalStatus?.port) seenBackends.set(finalStatus.port, { port: finalStatus.port, pid: finalStatus.pid ?? null });
      // A hung before-quit must not skip the cleanup checks below.
      let closeTimedOut = false;
      let closeTimer: NodeJS.Timeout | undefined;
      await Promise.race([
        app.close().catch(() => undefined),
        new Promise<void>(resolve => { closeTimer = setTimeout(() => { closeTimedOut = true; resolve(); }, 30_000); }),
      ]);
      clearTimeout(closeTimer);
      if (closeTimedOut && electronPid && harness.processAlive(electronPid)) {
        const rows = harness.scanProcesses().rows;
        const own = rows.find((row: { pid: number }) => row.pid === electronPid) || { pid: electronPid, ppid: 0, pgid: null, command: 'electron' };
        await harness.terminateProcesses([own], rows);
      }
      logStream.end();
      const logged = fs.existsSync(mainLog) ? fs.readFileSync(mainLog, 'utf8') : '';
      // Early start-up lines can precede the log pipe; shutdown lines name the PID too.
      const loggedPids = [...new Set([...logged.matchAll(/(?:spawned with PID|shutdown for PID|Daemon PID) (\d+)/g)]
        .map(match => Number(match[1])))];
      const backendChecks = [];
      for (const seen of seenBackends.values()) {
        backendChecks.push({ ...seen, port_closed: await harness.waitForPortClosed(seen.port, 20_000),
          pid_alive: seen.pid ? harness.processAlive(seen.pid) : null });
      }
      const loggedAlive = loggedPids.filter(pid => harness.processAlive(pid));
      const scan = harness.findProcessesReferencing(workspace.root);
      const escaped = await harness.terminateProcesses(scan.owned, scan.rows);
      const stopEvidence = {
        electron_alive: electronPid ? harness.processAlive(electronPid) : null,
        backends: backendChecks, logged_backend_pids: loggedPids, logged_backend_pids_alive: loggedAlive,
        backend_restarted: new Set([...seenBackends.values()].map(seen => seen.pid).filter(Boolean).concat(loggedPids)).size > 1,
        escaped, unowned_references: scan.others, scan_error: scan.error, close_timed_out: closeTimedOut,
      };
      evidence.note('electron_stop', stopEvidence);
      evidence.addFile(mainLog);
      const problems = [
        stopEvidence.electron_alive && 'Electron is still running',
        backendChecks.some(check => !check.port_closed || check.pid_alive) && 'Electron left its backend running',
        loggedAlive.length && 'a backend started by Electron is still alive',
        escaped.length && `${escaped.length} worker(s) referencing the workspace were still running`,
        scan.error && `process listing failed: ${scan.error}`,
        closeTimedOut && 'Electron did not close within 30 s',
      ].filter(Boolean);
      if (problems.length) throw new Error(`Electron session did not stop cleanly: ${problems.join('; ')}`);
    }
  },
});

export { expect };
