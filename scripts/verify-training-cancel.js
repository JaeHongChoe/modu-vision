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

test('data change retains the active job only until it can be stopped', async () => {
  mockApi.training.start = async () => ({ job_id: 'old-running-job' });
  mockApi.training.stop = async () => ({ status: 'stopping' });
  mockApi.training.getStatus = async () => ({ status: 'aborted' });
  await store.getState().startTraining('/old/data', 'segmentation');
  store.getState().invalidateForDataChange();
  assert.equal(store.getState().jobId, 'old-running-job');
  assert.equal(store.getState().isCurrentData, false);
  await store.getState().stopTraining();
  assert.equal(store.getState().jobId, null);
  assert.equal(store.getState().status, 'idle');
});

test('late A telemetry cannot change a new B training run', async () => {
  mockApi.training.start = async () => ({ job_id: 'job_A' });
  await store.getState().startTraining('/A', 'segmentation');
  store.getState().updateFromTelemetry('training_completed', { job_id: 'job_A', best_metric: 0.8 });
  assert.equal(store.getState().status, 'completed');

  mockApi.training.start = async () => ({ job_id: 'job_B' });
  await store.getState().startTraining('/B', 'segmentation');
  store.getState().updateFromTelemetry('step_progress', { job_id: 'job_A', step: 99, current_loss: 0.01 });
  store.getState().updateFromTelemetry('training_completed', { job_id: 'job_A', best_metric: 0.8 });
  store.getState().updateFromTelemetry('training_error', { job_id: 'job_A' });
  assert.equal(store.getState().jobId, 'job_B');
  assert.equal(store.getState().status, 'running');
  assert.equal(store.getState().currentStep, 0);
  store.getState().updateFromTelemetry('step_progress', { job_id: 'job_B', step: 1, current_loss: 0.4 });
  store.getState().updateFromTelemetry('training_completed', { job_id: 'job_B', best_metric: 0.4 });
  assert.equal(store.getState().status, 'completed');
  assert.equal(store.getState().currentStep, 1);
  assert.equal(store.getState().bestMetric, 0.4);
});

test('terminal B telemetry before start response is applied only after B ID is known', async () => {
  let finishStart;
  mockApi.training.start = () => new Promise((resolve) => { finishStart = resolve; });
  const starting = store.getState().startTraining('/B', 'segmentation');
  store.getState().updateFromTelemetry('training_completed', { job_id: 'job_A', best_metric: 0.9 });
  store.getState().updateFromTelemetry('training_completed', { job_id: 'job_B', best_metric: 0.3 });
  assert.equal(store.getState().status, 'running');
  finishStart({ job_id: 'job_B' });
  await starting;
  assert.equal(store.getState().jobId, 'job_B');
  assert.equal(store.getState().status, 'completed');
  assert.equal(store.getState().bestMetric, 0.3);
});

test('renderer reload recovers an active backend job and exposes its Stop action', async () => {
  store.getState().resetTraining();
  mockApi.training.getStatus = async (jobId) => jobId
    ? { status: 'aborted' }
    : { job_id: 'recovered-job', status: 'running', is_training: true,
      current_epoch: 2, total_epochs: 10 };
  mockApi.training.stop = async (jobId) => {
    assert.equal(jobId, 'recovered-job');
    return { status: 'stopping' };
  };
  await store.getState().recoverActiveJob();
  assert.equal(store.getState().jobId, 'recovered-job');
  assert.equal(store.getState().isTraining, true);
  assert.equal(store.getState().isCurrentData, false);
  assert.equal(store.getState().currentEpoch, 2);
  await store.getState().stopTraining();
  assert.equal(store.getState().jobId, null);
  assert.equal(store.getState().status, 'idle');
});

test('Step 3 checks backend activity before enabling Start', () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../src/renderer/components/training/TrainingController.tsx'), 'utf8');
  assert.match(source, /void recoverActiveJob\(\)/);
  assert.match(source, /!isRecoveringTraining && !importError/);
});
