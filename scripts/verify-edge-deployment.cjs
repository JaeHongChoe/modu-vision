const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

function load() {
  const filename = path.resolve(__dirname, '../src/renderer/components/inference/edgeDeployment.ts');
  if (!fs.existsSync(filename)) return {};
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, filename);
  return loaded.exports;
}

test('standard exports omit stale Edge target fields and preserve existing requests', () => {
  const { flowDeploymentOptions } = load();
  assert.equal(typeof flowDeploymentOptions, 'function');
  assert.deepEqual(flowDeploymentOptions('standard', { os: 'linux', architecture: 'arm64' }), {});
  assert.deepEqual(flowDeploymentOptions('edge_cpu', { os: 'macos', architecture: 'arm64' }), {
    deployment_profile: 'edge_cpu', target_os: 'macos', target_arch: 'arm64',
  });
});

test('a cross target declaration never requests image execution on the export host', () => {
  const { canVerifyFlowOnHost } = load();
  assert.equal(typeof canVerifyFlowOnHost, 'function');
  assert.equal(canVerifyFlowOnHost('edge_cpu', { os: 'linux', architecture: 'arm64' }, { os: 'macos', architecture: 'arm64' }), false);
  assert.equal(canVerifyFlowOnHost('edge_cpu', { os: 'macos', architecture: 'arm64' }, { os: 'macos', architecture: 'arm64' }), true);
  assert.equal(canVerifyFlowOnHost('edge_cpu', { os: 'macos', architecture: 'arm64' }, null), false);
  assert.equal(canVerifyFlowOnHost('standard', { os: 'linux', architecture: 'arm64' }, null), true);
});

test('exported Edge launch instructions use the target interpreter and keep image paths quoted', () => {
  const { edgeDeploymentCommands } = load();
  assert.equal(typeof edgeDeploymentCommands, 'function');
  const windows = edgeDeploymentCommands({ os: 'windows', architecture: 'x86_64' });
  assert.equal(windows.install, 'python edge.py install');
  assert.equal(windows.preflight, '.\\.edge_venv\\Scripts\\python.exe edge.py preflight');
  assert.equal(windows.run, '.\\.edge_venv\\Scripts\\python.exe edge.py run -- --image "C:\\inputs\\image.png" --output result.json');
  assert.equal(edgeDeploymentCommands({ os: 'linux', architecture: 'x86_64' }).preflight, '.edge_venv/bin/python edge.py preflight');
});
