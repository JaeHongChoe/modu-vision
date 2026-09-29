const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const filename = path.resolve(__dirname, '../src/renderer/components/inference/batchInspection.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const loaded = new Module(filename, module);
loaded.filename = filename;
loaded.paths = Module._nodeModulePaths(path.dirname(filename));
loaded._compile(compiled, filename);
const { runBatchInspection, summarizeBatch, filterBatchRows,
  isBatchSourceReady, isBatchSourceCurrent, batchSourceResetKey } = loaded.exports;

const image = (name, split = 'test') => ({
  image_id: path.parse(name).name,
  file_name: name,
  file_path: `/data/project/${name}`,
  split,
  label: 'Bow',
  thumbnail_url: `/api/dataset/thumbnail/${name}`,
});
const result = (item, verdict = 'NG') => ({
  status: 'success', final_verdict: verdict, is_ok: verdict === 'OK',
  rejection_reason: verdict === 'NG' ? 'Defect found' : '',
  roi_count: 1, defective_roi_count: verdict === 'NG' ? 1 : 0,
  crops: [], annotated_image: 'data:image/png;base64,AA==',
  execution_steps: [{ node_id: 'inspect', name: '검사', status: 'passed', latency_ms: 3 }],
  total_latency_ms: 3, image_path: item.file_path, image_id: item.image_id,
});
const pipeline = {
  id: 'saved-flow', name: '저장된 검사 플로우', edges: [],
  nodes: [{ id: 'inspect', data: { node_type: 'inspection', task: 'segmentation', model_job_id: 'job_123' } }],
};

function fakeApi(items, runImpl = (item) => result(item)) {
  const calls = [];
  const api = {
    getPipeline: async (task, sourceFolder) => {
      calls.push(['pipeline', task, sourceFolder]);
      return pipeline;
    },
    verifyModels: async (request) => {
      calls.push(['verify', request]);
      return { verified_job_ids: ['job_123'] };
    },
    getImages: async (params) => {
      calls.push(['images', params]);
      const selected = params.split ? items.filter((item) => item.split === params.split) : items;
      return { total: selected.length, limit: params.limit, offset: params.offset, items: selected.slice(params.offset, params.offset + params.limit) };
    },
    run: async (request) => {
      calls.push(['run', request.image_path]);
      const item = items.find((candidate) => candidate.file_path === request.image_path);
      return runImpl(item, request);
    },
  };
  return { api, calls };
}

const baseOptions = { sourceFolder: '/data/project', task: 'segmentation', scope: 'test' };

const source = (overrides = {}) => ({
  folderPath: '/data/project', task: 'segmentation', datasetKey: '/data/project\0segmentation',
  contextRevision: 4, hasSelectedFolder: true, importError: null,
  isLoading: false, isSplitting: false, ...overrides,
});

test('same-folder annotation save or split invalidates an active batch and its previous report', () => {
  const started = source();
  assert.equal(isBatchSourceReady(started), true);
  assert.equal(isBatchSourceCurrent(started, started), true);
  assert.equal(isBatchSourceCurrent(source({ contextRevision: 5 }), started), false);
  assert.equal(isBatchSourceCurrent(source({ isSplitting: true }), started), false);
  assert.equal(isBatchSourceCurrent(source({ isLoading: true }), started), false);
  assert.equal(isBatchSourceReady(source({ isSplitting: true })), false);
  assert.equal(isBatchSourceReady(source({ contextRevision: 5 })), true);
  assert.notEqual(batchSourceResetKey(source({ contextRevision: 5 })), batchSourceResetKey(started));
  assert.notEqual(batchSourceResetKey(source({ isSplitting: true })), batchSourceResetKey(started));
});

test('stale model provenance is rejected before batch image inventory or remote inference', async () => {
  const { api, calls } = fakeApi([image('a.jpg')]);
  api.verifyModels = async (request) => {
    calls.push(['verify', request]);
    throw new Error('Model is stale for the selected dataset');
  };
  await assert.rejects(runBatchInspection(baseOptions, api), /stale/);
  assert.equal(calls.filter(([kind]) => kind === 'images' || kind === 'run').length, 0);
});

test('test split is the default-sized real-image scope, with source-verified saved model and exact image mapping', async () => {
  const items = [image('test-a.jpg'), image('train-a.jpg', 'train'), image('test-b.jpg')];
  const { api, calls } = fakeApi(items, (item) => result(item, item.file_name === 'test-b.jpg' ? 'OK' : 'NG'));
  const report = await runBatchInspection(baseOptions, api);
  assert.equal(report.status, 'completed');
  assert.equal(report.pipeline_id, 'saved-flow');
  assert.deepEqual(report.rows.map((row) => [row.image.file_name, row.state]), [
    ['test-a.jpg', 'NG'], ['test-b.jpg', 'OK'],
  ]);
  assert.deepEqual(summarizeBatch(report.rows), { total: 2, processed: 2, ok: 1, ng: 1, review: 0, errors: 0, unrun: 0 });
  assert.deepEqual(calls[0], ['pipeline', 'segmentation', '/data/project']);
  assert.deepEqual(calls[1], ['verify', { source_dataset_path: '/data/project', models: [{ job_id: 'job_123', task: 'segmentation' }] }]);
  assert.equal(calls.filter(([kind]) => kind === 'run').length, 2);
  assert.ok(calls.filter(([kind]) => kind === 'images').every(([, params]) => params.split === 'test'));
});

test('explicit all scope pages inventory and does not silently truncate after 500 images', async () => {
  const items = Array.from({ length: 503 }, (_, n) => image(`im-${String(n).padStart(3, '0')}.jpg`, n < 8 ? 'test' : 'train'));
  const { api, calls } = fakeApi(items, (item) => result(item, 'OK'));
  const report = await runBatchInspection({ ...baseOptions, scope: 'all' }, api);
  assert.equal(report.rows.length, 503);
  assert.equal(summarizeBatch(report.rows).processed, 503);
  assert.deepEqual(calls.filter(([kind]) => kind === 'images').map(([, params]) => params.offset), [0, 500]);
  assert.ok(calls.filter(([kind]) => kind === 'images').every(([, params]) => params.split === undefined));
});

test('per-image failure and mismatched source result are reported without an invented verdict', async () => {
  const items = [image('a.jpg'), image('b.jpg'), image('c.jpg')];
  const { api } = fakeApi(items, (item) => {
    if (item.file_name === 'a.jpg') throw new Error('SSH disconnected');
    if (item.file_name === 'b.jpg') return { ...result(item), image_path: '/data/wrong.jpg' };
    return result(item, 'REVIEW');
  });
  const report = await runBatchInspection(baseOptions, api);
  assert.deepEqual(report.rows.map((row) => row.state), ['error', 'error', 'REVIEW']);
  assert.match(report.rows[0].error, /SSH disconnected/);
  assert.match(report.rows[1].error, /원본/);
  assert.deepEqual(summarizeBatch(report.rows), { total: 3, processed: 1, ok: 0, ng: 0, review: 1, errors: 2, unrun: 0 });
});

test('missing model binding blocks the batch before any image is submitted', async () => {
  const { api, calls } = fakeApi([image('a.jpg')]);
  api.getPipeline = async () => ({ ...pipeline, nodes: [{ ...pipeline.nodes[0], data: { ...pipeline.nodes[0].data, model_job_id: null } }] });
  await assert.rejects(runBatchInspection(baseOptions, api), /모델/);
  assert.equal(calls.filter(([kind]) => kind === 'run').length, 0);
});

test('stop after current image leaves the remaining inventory explicitly unrun', async () => {
  const items = [image('a.jpg'), image('b.jpg'), image('c.jpg')];
  let reason = null;
  const { api, calls } = fakeApi(items, (item) => {
    reason = 'user_stop';
    return result(item);
  });
  const report = await runBatchInspection({ ...baseOptions, stopReason: () => reason }, api);
  assert.equal(report.status, 'stopped');
  assert.deepEqual(report.rows.map((row) => row.state), ['NG', 'skipped', 'skipped']);
  assert.equal(calls.filter(([kind]) => kind === 'run').length, 1);
});

test('source change during an active request discards the returned result', async () => {
  const items = [image('a.jpg'), image('b.jpg')];
  let reason = null;
  const { api } = fakeApi(items, (item) => {
    reason = 'source_changed';
    return result(item);
  });
  const report = await runBatchInspection({ ...baseOptions, stopReason: () => reason }, api);
  assert.equal(report.status, 'stopped');
  assert.deepEqual(report.rows.map((row) => row.state), ['skipped', 'skipped']);
});

test('same-folder annotation change during remote inference discards the in-flight verdict', async () => {
  const started = source();
  let current = started;
  const { api, calls } = fakeApi([image('a.jpg'), image('b.jpg')], (item) => {
    current = source({ contextRevision: started.contextRevision + 1 });
    return result(item, 'OK');
  });
  const report = await runBatchInspection({
    ...baseOptions,
    stopReason: () => isBatchSourceCurrent(current, started) ? null : 'source_changed',
  }, api);
  assert.equal(report.status, 'stopped');
  assert.deepEqual(report.rows.map((row) => row.state), ['skipped', 'skipped']);
  assert.equal(calls.filter(([kind]) => kind === 'run').length, 1);
});

test('result filters keep failures and unrun images visible instead of collapsing them into REVIEW', () => {
  const rows = [
    { image: image('a.jpg'), state: 'OK' },
    { image: image('b.jpg'), state: 'NG' },
    { image: image('c.jpg'), state: 'REVIEW' },
    { image: image('d.jpg'), state: 'error', error: 'No result' },
    { image: image('e.jpg'), state: 'skipped' },
  ];
  assert.deepEqual(filterBatchRows(rows, 'error').map((row) => row.image.file_name), ['d.jpg']);
  assert.deepEqual(filterBatchRows(rows, 'unrun').map((row) => row.image.file_name), ['e.jpg']);
  assert.deepEqual(filterBatchRows(rows, 'NG').map((row) => row.image.file_name), ['b.jpg']);
});
