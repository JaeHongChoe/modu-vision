const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const mockApi = {
  training: {
    start: async () => ({ job_id: 'test-job' }),
    stop: async () => ({ status: 'stopping' }),
    getStatus: async () => ({ status: 'aborted' }),
  },
};

const storePath = path.resolve(__dirname, '../src/renderer/stores/useTrainingStore.ts');
const source = fs.readFileSync(storePath, 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const storeModule = new Module(storePath, module);
storeModule.filename = storePath;
storeModule.paths = Module._nodeModulePaths(path.dirname(storePath));
const originalRequire = storeModule.require.bind(storeModule);
storeModule.require = (specifier) => specifier === '../services/api'
  ? { api: mockApi }
  : originalRequire(specifier);
storeModule._compile(compiled, storePath);
const store = storeModule.exports.useTrainingStore;

test('cancel waits for the worker status before reporting aborted', async () => {
  let pollCount = 0;
  mockApi.training.start = async () => ({ job_id: 'running-job' });
  mockApi.training.stop = async (jobId) => {
    assert.equal(jobId, 'running-job');
    return { status: 'stopping' };
  };
  mockApi.training.getStatus = async () => ({ status: ++pollCount === 1 ? 'stopping' : 'aborted' });

  await store.getState().startTraining('/test/data', 'segmentation');
  const stopping = store.getState().stopTraining();
  assert.equal(store.getState().status, 'stopping');
  assert.equal(store.getState().isStopRequestPending, true);
  store.getState().updateFromTelemetry('training_aborted', {});
  assert.equal(store.getState().status, 'stopping');
  await stopping;
  assert.equal(store.getState().status, 'aborted');
  assert.equal(store.getState().isTraining, false);
  assert.equal(store.getState().stopError, null);
});

test('a failed stop request keeps the job visible and allows retry', async () => {
  mockApi.training.start = async () => ({ job_id: 'retry-job' });
  mockApi.training.stop = async () => { throw new Error('backend unavailable'); };

  await store.getState().startTraining('/test/data', 'segmentation');
  await store.getState().stopTraining();
  assert.equal(store.getState().status, 'running');
  assert.equal(store.getState().isTraining, true);
  assert.equal(store.getState().isStopRequestPending, false);
  assert.match(store.getState().stopError, /backend unavailable/);
});

test('cancel during start uses the newly returned job ID', async () => {
  let finishStart;
  mockApi.training.start = () => new Promise((resolve) => { finishStart = resolve; });
  mockApi.training.stop = async (jobId) => {
    assert.equal(jobId, 'late-job');
    return { status: 'stopping' };
  };
  mockApi.training.getStatus = async () => ({ status: 'aborted' });

  const starting = store.getState().startTraining('/test/data', 'segmentation');
  const stopping = store.getState().stopTraining();
  assert.equal(store.getState().status, 'stopping');
  finishStart({ job_id: 'late-job' });
  await Promise.all([starting, stopping]);
  assert.equal(store.getState().jobId, 'late-job');
  assert.equal(store.getState().status, 'aborted');
});
