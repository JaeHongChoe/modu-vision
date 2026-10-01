import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

// Each run writes to its own directory so earlier evidence is never cleared.
// Workers re-evaluate this file and inherit the run id from the runner process.
process.env.MV_E2E_RUN_ID ||= `${new Date().toISOString().replace(/[:.]/g, '-')}-${process.pid}`;
const artifactRoot = process.env.MV_E2E_ARTIFACT_DIR
  ? path.resolve(process.env.MV_E2E_ARTIFACT_DIR)
  : path.join(os.tmpdir(), 'modu-vision-e2e');
const runDir = path.join(artifactRoot, process.env.MV_E2E_RUN_ID);
// Playwright empties its output folder; a reused run id would erase evidence.
// The runner claims the directory once; workers inherit the exact claimed path.
if (process.env.MV_E2E_RUN_DIR_CLAIMED !== runDir && !process.argv.includes('--list')) {
  if (fs.existsSync(runDir)) throw new Error(`E2E run directory already exists, choose a new MV_E2E_RUN_ID: ${runDir}`);
  fs.mkdirSync(runDir, { recursive: true });
  process.env.MV_E2E_RUN_DIR_CLAIMED = runDir;
}
process.env.MV_E2E_RUN_DIR = runDir;

export default defineConfig({
  testDir: './scripts/e2e',
  testMatch: '**/*.spec.ts',
  outputDir: path.join(runDir, 'results'),
  globalSetup: './scripts/e2e/fixtures/global-setup.ts',
  globalTeardown: './scripts/e2e/fixtures/global-teardown.ts',
  // Owned backends import the ML stack on start-up; keep runs serial and bounded.
  timeout: 240_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: [
    ['list'],
    ['json', { outputFile: path.join(runDir, 'report.json') }],
    ['html', { outputFolder: path.join(runDir, 'html'), open: 'never' }],
  ],
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      // Same renderer in a browser, served with the owned backend behind a
      // same-origin proxy that holds the process token.
      name: 'browser',
      grepInvert: /@electron/,
      use: {
        browserName: 'chromium',
        channel: process.env.MV_E2E_BROWSER_CHANNEL || undefined,
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      // Real Electron main/preload/renderer with an isolated profile. This is
      // not a Windows native gate unless it runs on a Windows target.
      name: 'electron',
      grep: /@electron/,
      timeout: 480_000,
    },
  ],
});
