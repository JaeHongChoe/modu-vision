import path from 'node:path';

// eslint-disable-next-line @typescript-eslint/no-require-imports
const harness = require('./harness.cjs');

// Reaps anything still referencing this run directory, for example workers of a
// test worker that was killed before its fixtures could stop them.
export default async function globalTeardown() {
  const runDir = process.env.MV_E2E_RUN_DIR as string;
  const scan = harness.findProcessesReferencing(runDir);
  const leftovers = await harness.terminateProcesses(scan.owned, scan.rows);
  harness.writeManifest(path.join(runDir, 'teardown.json'), {
    receipt: 'HarnessTeardown', run_dir: runDir, leftovers, unowned_references: scan.others, scan_error: scan.error,
  });
  if (scan.error) throw new Error(`E2E teardown could not list processes: ${scan.error}`);
  if (leftovers.length) throw new Error(`E2E run left ${leftovers.length} owned process(es); see ${path.join(runDir, 'teardown.json')}`);
}
