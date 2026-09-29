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
  isBatchSourceReady, isBatchSourceCurrent, isInspectionHistoryContextCurrent,
  batchSourceResetKey, stopInspectionRunKeepalive, createInspectionRunExitGuard } = loaded.exports;

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

test('batch uses the source-scoped active mixed graph ahead of an older task graph', async () => {
  const { api, calls } = fakeApi([image('a.jpg')]);
  api.getActivePipeline = async (sourceFolder) => {
    calls.push(['active-pipeline', sourceFolder]);
    return { ...pipeline, id: 'active-mixed-flow', name: '혼합 검사',
      nodes: [{ ...pipeline.nodes[0], data: { ...pipeline.nodes[0].data, task: 'classification' } }] };
  };
  const report = await runBatchInspection(baseOptions, api);
  assert.equal(report.pipeline_id, 'active-mixed-flow');
  assert.deepEqual(calls[0], ['active-pipeline', '/data/project']);
  assert.equal(calls.filter(([kind]) => kind === 'pipeline').length, 0);
  assert.deepEqual(calls[1], ['verify', { source_dataset_path: '/data/project', models: [{ job_id: 'job_123', task: 'classification' }] }]);
});

test('batch falls back to the source-scoped task graph only if no active graph exists', async () => {
  const { api, calls } = fakeApi([image('a.jpg')]);
  api.getActivePipeline = async () => { const error = new Error('No active graph'); error.status = 404; throw error; };
  const report = await runBatchInspection(baseOptions, api);
  assert.equal(report.pipeline_id, 'saved-flow');
  assert.deepEqual(calls[0], ['pipeline', 'segmentation', '/data/project']);

  api.getActivePipeline = async () => { const error = new Error('Server error'); error.status = 500; throw error; };
  const before = calls.length;
  await assert.rejects(runBatchInspection(baseOptions, api), /Server error/);
  assert.equal(calls.slice(before).filter(([kind]) => kind === 'pipeline').length, 0);
});

test('batch runs the exact saved graph shown in its handoff card', async () => {
  const { api, calls } = fakeApi([image('a.jpg')]);
  api.getActivePipeline = async () => {
    throw new Error('A later active graph must not replace the selected saved graph');
  };
  const selected = { ...pipeline, id: 'selected-version-flow', name: 'Displayed flow' };
  const report = await runBatchInspection({ ...baseOptions, pipeline: selected }, api);
  assert.equal(report.pipeline_id, 'selected-version-flow');
  assert.equal(calls.filter(([kind]) => kind === 'active-pipeline').length, 0);
});

test('batch accepts a saved flow with segmentation and patch classification models', async () => {
  const { api, calls } = fakeApi([image('a.jpg'), image('b.jpg')]);
  const mixed = { ...pipeline, nodes: [
    pipeline.nodes[0],
    { id: 'patch', data: { node_type: 'inspection', task: 'patch_classification', model_job_id: 'job_patch' } },
  ] };
  const report = await runBatchInspection({ ...baseOptions, pipeline: mixed }, api);
  assert.equal(report.status, 'completed');
  assert.equal(report.rows.length, 2);
  assert.deepEqual(calls.find(([kind]) => kind === 'verify')[1].models, [
    { job_id: 'job_123', task: 'segmentation' },
    { job_id: 'job_patch', task: 'patch_classification' },
  ]);
});

test('unmount during run creation stops only the newly created durable run', async () => {
  const { api, calls } = fakeApi([image('a.jpg'), image('b.jpg')]);
  let resolveCreation;
  let unmounted = false;
  const created = [];
  api.createRun = () => new Promise((resolve) => { resolveCreation = resolve; });
  api.finishRun = async (runId, status) => { calls.push(['finish', runId, status]); };
  const pending = runBatchInspection({ ...baseOptions,
    stopReason: () => unmounted ? 'source_changed' : null,
    onRunCreated: (runId) => created.push(runId),
  }, api);
  await new Promise((resolve) => setImmediate(resolve));
  unmounted = true;
  resolveCreation('owned-run');
  const report = await pending;
  assert.equal(report.status, 'stopped');
  assert.deepEqual(created, ['owned-run']);
  assert.deepEqual(calls.filter(([kind]) => kind === 'finish'), [['finish', 'owned-run', 'stopped']]);
  assert.equal(calls.filter(([kind]) => kind === 'run').length, 0);
});

test('unmount after a keepalive stop treats the same-run finish conflict as stopped', async () => {
  const { api, calls } = fakeApi([image('a.jpg')]);
  let resolveRun;
  let unmounted = false;
  api.createRun = async () => 'owned-run';
  api.run = () => new Promise((resolve) => { resolveRun = resolve; });
  api.finishRun = async (runId, status) => {
    calls.push(['finish', runId, status]);
    const conflict = new Error('Already stopped');
    conflict.status = 409;
    throw conflict;
  };
  const pending = runBatchInspection({ ...baseOptions,
    stopReason: () => unmounted ? 'source_changed' : null,
  }, api);
  await new Promise((resolve) => setImmediate(resolve));
  unmounted = true;
  resolveRun(result(image('a.jpg')));
  const report = await pending;
  assert.equal(report.status, 'stopped');
  assert.deepEqual(calls.filter(([kind]) => kind === 'finish'), [['finish', 'owned-run', 'stopped']]);
});

test('pagehide cleanup targets only its own run with keepalive', async () => {
  assert.equal(typeof stopInspectionRunKeepalive, 'function');
  const sent = [];
  await stopInspectionRunKeepalive('owned-run', 'http://127.0.0.1:8000', async (url, options) => {
    sent.push({ url, options });
    return { ok: true };
  });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, 'http://127.0.0.1:8000/api/inspections/runs/owned-run/finish');
  assert.equal(sent[0].options.method, 'PUT');
  assert.equal(sent[0].options.keepalive, true);
  assert.deepEqual(JSON.parse(sent[0].options.body), { status: 'stopped' });
});

test('exit guard stops only its owned run once across creation and unmount races', () => {
  assert.equal(typeof createInspectionRunExitGuard, 'function');
  const stopped = [];
  const guard = createInspectionRunExitGuard((runId) => stopped.push(runId));
  guard.close();
  guard.created('owned-run');
  guard.close();
  assert.deepEqual(stopped, ['owned-run']);

  const next = createInspectionRunExitGuard((runId) => stopped.push(runId));
  next.created('finished-run');
  next.release('finished-run');
  next.close();
  assert.deepEqual(stopped, ['owned-run']);
});

const source = (overrides = {}) => ({
  folderPath: '/data/project', task: 'segmentation', datasetKey: '/data/project\0segmentation',
  projectDir: '/workspaces/project-a',
  contextRevision: 4, hasSelectedFolder: true, importError: null,
  isLoading: false, isSplitting: false, ...overrides,
});

test('same-folder annotation save or split invalidates an active batch and its previous report', () => {
  const started = source();
  assert.equal(isBatchSourceReady(started), true);
  assert.equal(isBatchSourceCurrent(started, started), true);
  assert.equal(isBatchSourceCurrent(source({ contextRevision: 5 }), started), false);
  assert.equal(isBatchSourceCurrent(source({ projectDir: '/workspaces/project-b' }), started), false);
  assert.equal(isBatchSourceCurrent(source({ isSplitting: true }), started), false);
  assert.equal(isBatchSourceCurrent(source({ isLoading: true }), started), false);
  assert.equal(isBatchSourceReady(source({ isSplitting: true })), false);
  assert.equal(isBatchSourceReady(source({ contextRevision: 5 })), true);
  assert.notEqual(batchSourceResetKey(source({ contextRevision: 5 })), batchSourceResetKey(started));
  assert.notEqual(batchSourceResetKey(source({ projectDir: '/workspaces/project-b' })), batchSourceResetKey(started));
  assert.notEqual(batchSourceResetKey(source({ isSplitting: true })), batchSourceResetKey(started));
});

test('inspection history response requires the same project even when source and task match', () => {
  const started = source();
  assert.equal(isInspectionHistoryContextCurrent(started, source()), true);
  assert.equal(isInspectionHistoryContextCurrent(source({ projectDir: '/workspaces/project-b' }), started), false);
  assert.equal(isInspectionHistoryContextCurrent(source({ folderPath: '/data/other' }), started), false);
  assert.equal(isInspectionHistoryContextCurrent(source({ task: 'detection' }), started), false);
  assert.equal(isInspectionHistoryContextCurrent(source({ contextRevision: 5 }), started), true);
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

test('each completed row is durably recorded before the next image and original model results remain separate', async () => {
  const items = [image('a.jpg'), image('b.jpg')];
  const events = [];
  const { api } = fakeApi(items, (item) => {
    events.push(`infer:${item.file_name}`);
    return result(item, item.file_name === 'a.jpg' ? 'NG' : 'OK');
  });
  api.createRun = async (report, savedPipeline) => {
    assert.equal(savedPipeline.id, 'saved-flow');
    assert.equal(report.rows.length, 2);
    events.push('create');
    return 'run-1';
  };
  api.recordRow = async (runId, row) => {
    assert.equal(runId, 'run-1');
    events.push(`record:${row.image.file_name}:${row.state}`);
    await Promise.resolve();
  };
  api.finishRun = async (runId, status) => events.push(`finish:${runId}:${status}`);
  const report = await runBatchInspection(baseOptions, api);
  assert.equal(report.run_id, 'run-1');
  assert.deepEqual(events, [
    'create', 'infer:a.jpg', 'record:a.jpg:NG',
    'infer:b.jpg', 'record:b.jpg:OK', 'finish:run-1:completed',
  ]);
});
