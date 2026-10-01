const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

// Explicit argv works on Windows as well as shells that do not expand ** globs.
const root = path.resolve(__dirname, '..');
function discover(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const filename = path.join(directory, entry.name);
    return entry.isDirectory() ? discover(filename)
      : entry.isFile() && entry.name.endsWith('.test.cjs') ? [path.relative(root, filename)] : [];
  });
}
const files = discover(path.join(root, 'src', 'renderer')).sort();
if (!files.length) throw new Error('No renderer regression files discovered');
if (process.argv.includes('--list')) console.log(JSON.stringify(files, null, 2));
else {
  const result = spawnSync(process.execPath, ['--test', '--test-concurrency=4', ...files], {
    cwd: root, stdio: 'inherit', shell: false,
  });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
}
