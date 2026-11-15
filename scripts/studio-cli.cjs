#!/usr/bin/env node
'use strict';
// The CLI selects the same existing Python as localhost web; it starts no app/server.
const {spawnSync} = require('node:child_process');
const path = require('node:path');
const {resolvePython} = require('./local-web.cjs');
const result = spawnSync(resolvePython(), ['-m', 'backend.studio_cli', ...process.argv.slice(2)], {
  cwd: path.resolve(__dirname, '..'), env: process.env, stdio: 'inherit', shell: false,
});
if (result.error) {
  process.stdout.write(JSON.stringify({schema: 'StudioCLIResult/v1', ok: false, outcome: 'not_started',
    error: {code: 'python_unavailable', message: 'Set VISION_AI_PYTHON to an existing Python executable.', retryable: false}}) + '\n');
}
process.exitCode = result.status ?? 1;
