import path from 'node:path';
import { chromium, type FullConfig } from '@playwright/test';

// eslint-disable-next-line @typescript-eslint/no-require-imports
const harness = require('./harness.cjs');

// Fails fast with an actionable message instead of a deep Playwright error,
// and before Electron could ask its supervisor to install Python packages.
export default async function globalSetup(config: FullConfig) {
  const runDir = process.env.MV_E2E_RUN_DIR as string;
  const projects = new Set(config.projects.map(project => project.name));
  // `--project` selections only decide which prerequisites are required.
  const selected: string[] = [];
  process.argv.forEach((argument, index) => {
    if (argument.startsWith('--project=')) selected.push(...argument.slice('--project='.length).split(','));
    else if (argument === '--project' && process.argv[index + 1]) selected.push(process.argv[index + 1]);
  });
  const wants = (name: string) => (selected.length ? selected.includes(name) : projects.has(name));
  const facts = harness.collectPreflightFacts({ env: process.env });
  const problems: string[] = harness.evaluatePreflight(facts, {
    needBrowser: wants('browser'), needElectron: wants('electron'), needPython: true,
  });
  // Launch the configured browser once: the headless binary differs from the
  // full Chromium path, so a file check alone cannot prove the project can run.
  let browserLaunch: Record<string, unknown> = { checked: false };
  if (wants('browser')) {
    const channel = process.env.MV_E2E_BROWSER_CHANNEL || undefined;
    try {
      const browser = await chromium.launch({ channel, timeout: 60_000 });
      browserLaunch = { checked: true, ok: true, channel: channel ?? 'bundled', version: browser.version() };
      await browser.close();
      problems.splice(0, problems.length, ...problems.filter(item => !item.startsWith('No browser for the browser project')));
    } catch (error) {
      browserLaunch = { checked: true, ok: false, channel: channel ?? 'bundled', error: String(error).split('\n')[0] };
      problems.push(`The browser project cannot launch Chromium (${browserLaunch.error}); run "npx playwright install chromium" or set MV_E2E_BROWSER_CHANNEL`);
    }
  }
  harness.writeManifest(path.join(runDir, 'preflight.json'), {
    receipt: 'HarnessPreflight', source: harness.sourceIdentity(), facts, browser_launch: browserLaunch, problems,
  });
  if (problems.length) throw new Error(`E2E preflight failed:\n- ${problems.join('\n- ')}`);
}
