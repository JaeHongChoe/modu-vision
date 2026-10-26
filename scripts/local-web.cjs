#!/usr/bin/env node
'use strict';
// Python owns the build, lock, account setup and HTTPS lifetime. This wrapper
// only selects an existing interpreter and preserves argv and process status.
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const ROOT = path.resolve(__dirname, '..');

function resolvePython(root = ROOT, env = process.env, platform = process.platform) {
  if (env.VISION_AI_PYTHON) return env.VISION_AI_PYTHON;
  const suffix = platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python'];
  for (const name of ['.venv', 'venv']) {
    const candidate = path.join(root, name, ...suffix);
    if (fs.existsSync(candidate)) return candidate;
  }
  return platform === 'win32' ? 'python' : 'python3';
}

function main(args = process.argv.slice(2)) {
  const child = spawn(resolvePython(), ['-m', 'backend.web', ...args], {
    cwd: ROOT, env: process.env, stdio: 'inherit', shell: false,
    // The terminal signals Node once; Node forwards to this owned Python.
    // Without a separate group, both could receive the same Ctrl+C twice.
    detached: process.platform !== 'win32',
  });
  const signals = ['SIGINT', 'SIGTERM'];
  const handlers = new Map(signals.map(signal => [signal, () => child.kill(signal)]));
  for (const [signal, handler] of handlers) process.on(signal, handler);
  function clearHandlers() {
    for (const [signal, handler] of handlers) process.removeListener(signal, handler);
  }
  child.once('error', () => {
    clearHandlers();
    console.error('Unable to launch Python. Set VISION_AI_PYTHON to an existing Python executable; see docs/local-browser.md.');
    process.exitCode = 1;
  });
  child.once('exit', (code, signal) => {
    clearHandlers();
    if (signal) {
      try { process.kill(process.pid, signal); }
      catch { process.exitCode = 1; }
    } else {
      process.exitCode = code ?? 1;
    }
  });
}

module.exports = { resolvePython, main };
if (require.main === module) main();
