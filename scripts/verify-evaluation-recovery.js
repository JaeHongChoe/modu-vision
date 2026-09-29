const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const calls = [];
let finishResults;
const api = {
  evaluation: {
    getResults: (jobId, options) => {
      calls.push(['results', jobId, options]);
      return new Promise((resolve) => { finishResults = resolve; });
    },
    getOverkillUnderkill: async () => ({ sample_details: [] }),
    runBenchmark: async (params) => {
      calls.push(['benchmark', params.job_id]);
      return { status: 'success', fps: 12 };
    },
  },
};

const storePath = path.resolve(__dirname, '../src/renderer/stores/useEvaluationStore.ts');
const compiled = ts.transpileModule(fs.readFileSync(storePath, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const storeModule = new Module(storePath, module);
storeModule.filename = storePath;
storeModule.paths = Module._nodeModulePaths(path.dirname(storePath));
const originalRequire = storeModule.require.bind(storeModule);
storeModule.require = (specifier) => {
  if (specifier === '../services/api') return { api, getApiBaseUrl: async () => 'http://localhost' };
  if (specifier === './useTrainingStore') return { useTrainingStore: { getState: () => ({ jobId: null, status: 'idle', isCurrentData: false }) } };
  return originalRequire(specifier);
};
storeModule._compile(compiled, storePath);
const store = storeModule.exports.useEvaluationStore;

test('evaluation discovers a completed backend job after renderer state is lost', async () => {
  const pending = store.getState().loadEvaluation(undefined, { folderPath: '/dataset/A', task: 'segmentation' });
  assert.equal(store.getState().isLoading, true);
  assert.deepEqual(calls[0], ['results', undefined, {
    sourceDatasetPath: '/dataset/A', sourceTask: 'segmentation',
  }]);
  finishResults({
    job_id: 'completed-job',
    metrics: { miou: 0.42 },
    confusion_matrix: { classes: ['background', 'defect'], matrix: [[0, 0], [1, 2]] },
    test_predictions: [],
  });
  await pending;
  assert.equal(store.getState().jobId, 'completed-job');
  assert.equal(store.getState().errorMessage, null);
});

test('benchmark uses the recovered evaluation model ID', async () => {
  await store.getState().runBenchmark(5, 256);
  assert.deepEqual(calls.at(-1), ['benchmark', 'completed-job']);
  assert.equal(store.getState().benchmarkResult.fps, 12);
});

test('dataset change discards a late result and blocks backend latest-job fallback', async () => {
  const pending = store.getState().loadEvaluation('old-job', { folderPath: '/dataset/A', task: 'segmentation' });
  const callsBeforeInvalidation = calls.length;
  store.getState().invalidateForDataChange();
  finishResults({ job_id: 'old-job', metrics: { miou: 0.99 }, test_predictions: [] });
  await pending;
  assert.equal(store.getState().jobId, null);
  assert.deepEqual(store.getState().metrics, {});
  await store.getState().loadEvaluation(undefined, { folderPath: '/dataset/A', task: 'segmentation' });
  assert.equal(calls.length, callsBeforeInvalidation);
  assert.match(store.getState().errorMessage, /현재 데이터/);
});

test('new folder import can recover only through a source-filtered request', async () => {
  store.getState().invalidateForDataChange(true);
  const pending = store.getState().loadEvaluation(undefined, { folderPath: '/dataset/B', task: 'segmentation' });
  assert.deepEqual(calls.at(-1), ['results', undefined, {
    sourceDatasetPath: '/dataset/B', sourceTask: 'segmentation',
  }]);
  finishResults({ job_id: 'job_B', metrics: { miou: 0.4 }, test_predictions: [] });
  await pending;
  assert.equal(store.getState().jobId, 'job_B');
});
