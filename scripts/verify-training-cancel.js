const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const mockApi = {
  project: { getCurrent: async () => ({ id: 'project-A', project_dir: '/projectA', active_labelset_id: 'default', task: 'segmentation', source_dataset_dir: '/test/data' }) },
  flowchart: { modelCatalog: async () => ({ models: [] }) },
  training: {
    start: async () => ({ job_id: 'test-job' }),
    stop: async () => ({ status: 'stopping' }),
    getStatus: async () => ({ status: 'aborted' }),
  },
};
const datasetState = { isSplitting: false };
const computeState = {
  profiles: [],
  isLoaded: true,
  selectedProfileId: null,
  loadError: null,
  probeResults: {},
  getSelectedProfile: () => undefined,
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
const readinessPath = path.resolve(__dirname, '../src/renderer/utils/trainingComputeReadiness.ts');
const readinessModule = new Module(readinessPath, module);
readinessModule._compile(ts.transpileModule(fs.readFileSync(readinessPath, 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
}).outputText, readinessPath);
storeModule.require = (specifier) => specifier === '../services/api'
  ? { api: mockApi }
  : specifier === './useDatasetStore'
    ? { useDatasetStore: { getState: () => datasetState } }
    : specifier === '../utils/trainingComputeReadiness'
      ? readinessModule.exports
    : specifier === './useComputeStore'
      ? { useComputeStore: { getState: () => computeState } }
    : originalRequire(specifier);
storeModule._compile(compiled, storePath);
const store = storeModule.exports.useTrainingStore;

test('training sends the selected pretrained architecture and checkpoint without losing parent identity', async () => {
  let sent;
  mockApi.training.start = async (payload) => { sent = payload; return {job_id: 'configured-job'}; };
  const options = {model_name: 'dinov3_vits16', pretrained_checkpoint: '/weights/dino.safetensors'};
  await store.getState().startTraining('/test/data', 'segmentation', 'verified-parent', options);
  assert.deepEqual(sent.config_overrides, options);
  assert.equal(sent.warm_start_job_id, 'verified-parent');
  assert.equal(sent.task, 'segmentation');
  store.getState().updateFromTelemetry('training_completed', {job_id: 'configured-job', best_metric: .4});
});

test('polling restores complete remote epoch curves without duplicate points', async () => {
  store.setState({ jobId: 'remote-curve', jobComputeProfileId: 'gpu', isCurrentData: true, lossHistory: [] });
  mockApi.training.getStatus = async () => ({ job_id: 'remote-curve', compute_profile_id: 'gpu', status: 'completed',
    loss_history: [{ epoch: 1, train_loss: .8, val_loss: .9 }, { epoch: 2, train_loss: .6, val_loss: .7 }] });
  await store.getState().refreshCurrentJob();
  await store.getState().refreshCurrentJob();
  assert.deepEqual(store.getState().lossHistory.map(p => p.epoch), [1, 2]);
  assert.equal(store.getState().lossHistory[1].trainLoss, .6);
});

test('synthetic telemetry and polling preserve absent validation loss without inventing zero', async () => {
  store.setState({jobId: 'synthetic', jobComputeProfileId: null, isCurrentData: true, valLoss: .7, lossHistory: []});
  store.getState().updateFromTelemetry('epoch_progress', {job_id: 'synthetic', epoch: 1, total_epochs: 2,
    train_loss: .4, val_loss: null, metrics: {val_image_auroc: null}});
  assert.equal(store.getState().valLoss, null);
  assert.deepEqual(store.getState().lossHistory, [{epoch: 1, trainLoss: .4, valLoss: null, lr: undefined}]);
  mockApi.training.getStatus = async () => ({job_id: 'synthetic', status: 'running', current_val_loss: null,
    loss_history: [{epoch: 1, train_loss: .4, val_loss: null}, {epoch: 2, train_loss: .3, val_loss: null}]});
  await store.getState().refreshCurrentJob();
  assert.equal(store.getState().lossHistory.length, 2);
  assert.equal(store.getState().lossHistory[1].valLoss, null);
  assert.equal(store.getState().valLoss, null);
});

test('failed polled jobs show the concrete worker error', async () => {
  store.setState({ jobId: 'failed-remote', jobComputeProfileId: 'gpu', isCurrentData: true });
  mockApi.training.getStatus = async () => ({ job_id: 'failed-remote', compute_profile_id: 'gpu', status: 'failed',
    error: { message: 'Server Python syntax error' } });
  await store.getState().refreshCurrentJob();
  assert.match(store.getState().startError, /Python syntax/);
});

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

const recoverySource = { projectId: 'project-A', projectDir: '/projectA', labelsetId: 'default', folderPath: '/test/data', task: 'segmentation' };
function readyRecoverySource() {
  Object.assign(datasetState, { folderPath: '/test/data', datasetKey: '/test/data\0segmentation', isLoading: false });
  mockApi.project.getCurrent = async () => ({ id: 'project-A', project_dir: '/projectA', active_labelset_id: 'default', task: 'segmentation', source_dataset_dir: '/test/data' });
  store.getState().resetTraining();
}

test('restart restores verified same-task completed job and its recorded curves without evaluation', async () => {
  readyRecoverySource();
  const calls = [];
  mockApi.flowchart.modelCatalog = async (folder) => {
    assert.equal(folder, '/test/data');
    return { models: [{ job_id: 'newer-detection', task: 'detection' }, { job_id: 'completed-seg', task: 'segmentation', source_dataset_path: '/test/data' }] };
  };
  mockApi.training.getStatus = async (jobId) => {
    calls.push(jobId);
    return jobId ? { job_id: 'completed-seg', task: 'segmentation', status: 'completed', current_epoch: 2, total_epochs: 5,
      current_train_loss: .4, current_val_loss: .5, best_metric: .8, compute_profile_id: 'gpu',
      loss_history: [{ epoch: 1, train_loss: .6, val_loss: .7 }, { epoch: 2, train_loss: .4, val_loss: .5 }] } : { status: 'idle' };
  };
  await store.getState().recoverActiveJob(recoverySource);
  assert.equal(store.getState().jobId, 'completed-seg');
  assert.equal(store.getState().status, 'completed');
  assert.equal(store.getState().isTraining, false);
  assert.equal(store.getState().currentEpoch, 2);
  assert.deepEqual(store.getState().lossHistory.map(p => [p.epoch, p.trainLoss, p.valLoss]), [[1, .6, .7], [2, .4, .5]]);
  assert.deepEqual(calls, [undefined, 'completed-seg']);
});

test('completed recovery rejects stale A-to-B-to-A context while its status is pending', async () => {
  readyRecoverySource();
  let finish;
  mockApi.flowchart.modelCatalog = async () => ({ models: [{ job_id: 'old-seg', task: 'segmentation' }] });
  mockApi.training.getStatus = async (jobId) => jobId ? new Promise(resolve => { finish = resolve; }) : { status: 'idle' };
  const recovering = store.getState().recoverActiveJob(recoverySource);
  for (let i = 0; i < 20 && !finish; i++) await Promise.resolve();
  assert.equal(typeof finish, 'function');
  store.getState().invalidateForDataChange();
  finish({ job_id: 'old-seg', task: 'segmentation', status: 'completed', loss_history: [{ epoch: 1, train_loss: .1, val_loss: .2 }] });
  await recovering;
  assert.equal(store.getState().jobId, null);
  assert.deepEqual(store.getState().lossHistory, []);
});

test('completed recovery rejects changed labelset and mismatched status task', async () => {
  for (const damage of ['labelset', 'task']) {
    readyRecoverySource();
    mockApi.flowchart.modelCatalog = async () => ({ models: [{ job_id: 'seg', task: 'segmentation' }] });
    let projectReads = 0;
    mockApi.project.getCurrent = async () => ({ id: 'project-A', project_dir: '/projectA', active_labelset_id: damage === 'labelset' && ++projectReads > 1 ? 'other' : 'default', task: 'segmentation', source_dataset_dir: '/test/data' });
    mockApi.training.getStatus = async (jobId) => jobId ? { job_id: 'seg', status: 'completed', task: damage === 'task' ? 'detection' : 'segmentation' } : { status: 'idle' };
    await store.getState().recoverActiveJob(recoverySource);
    assert.equal(store.getState().jobId, null);
  }
});

test('a new training start owns state while completed recovery status is pending', async () => {
  readyRecoverySource();
  let finish;
  mockApi.flowchart.modelCatalog = async () => ({ models: [{ job_id: 'old-seg', task: 'segmentation' }] });
  mockApi.training.getStatus = async (jobId) => jobId ? new Promise(resolve => { finish = resolve; }) : { status: 'idle' };
  const recovering = store.getState().recoverActiveJob(recoverySource);
  for (let i = 0; i < 20 && !finish; i++) await Promise.resolve();
  assert.equal(typeof finish, 'function');
  mockApi.training.start = async () => ({ job_id: 'new-seg' });
  await store.getState().startTraining('/test/data', 'segmentation');
  finish({ job_id: 'old-seg', task: 'segmentation', status: 'completed', loss_history: [{ epoch: 1, train_loss: .1, val_loss: .2 }] });
  await recovering;
  assert.equal(store.getState().jobId, 'new-seg');
  assert.equal(store.getState().status, 'running');
  assert.equal(store.getState().isRecoveringTraining, false);
  assert.deepEqual(store.getState().lossHistory, []);
});

test('Step 3 checks backend activity before enabling Start', () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../src/renderer/components/training/TrainingController.tsx'), 'utf8');
  assert.match(source, /void recoverActiveJob\(/);
  assert.match(source, /!isRecoveringTraining && !importError/);
});

test('split in progress rejects training before replacing an existing completed model', async () => {
  store.setState({ jobId: 'existing-job', status: 'completed', isTraining: false, isCurrentData: true });
  datasetState.isSplitting = true;
  let startCalls = 0;
  mockApi.training.start = async () => { startCalls += 1; return { job_id: 'new-job' }; };
  try {
    await assert.rejects(store.getState().startTraining('/test/data', 'segmentation'), /분할/);
    assert.equal(startCalls, 0);
    assert.equal(store.getState().jobId, 'existing-job');
    assert.equal(store.getState().status, 'completed');
    assert.equal(store.getState().isCurrentData, true);
  } finally {
    datasetState.isSplitting = false;
  }
});
