const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

function load(file, mocks = {}) {
  const m = new Module(file, module);
  m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m);
  m.require = key => Object.hasOwn(mocks, key) ? mocks[key]
    : key.endsWith('/classSemantics') ? load(path.resolve(path.dirname(file), key) + '.ts') : original(key);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText, file);
  return m.exports;
}
const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); promise.catch(() => {}); return {promise, resolve, reject};};
function fixture(task = 'classification') {
  const authority = {epoch: 0};
  const project = {task, projectDir: '/project/A', isProjectBusy: false,
    project: {id: 'A', task, project_dir: '/project/A', source_dataset_dir: '/source/A', active_labelset_id: 'default'}};
  const dataset = {folderPath: '/source/A', datasetKey: `/source/A\0${task}`, hasSelectedFolder: true, isLoading: false, importError: null};
  const training = {isCurrentData: false, status: 'idle', jobId: null};
  const source = {folderPath: '/source/A', task};
  const calls = [], api = {
    flowchart: {modelCatalog: async folder => {calls.push(['catalog', folder]); return {models: [], total: 0};}},
    evaluation: {getResults: async (job, options) => {
      calls.push(['evaluation', job, options]); throw new Error('Evaluation data or model checkpoint not found');
    }},
  };
  const {useEvaluationStore: store} = load(path.join(__dirname, 'useEvaluationStore.ts'), {
    '../services/api': {api, getApiPersistenceIdentity: () => 'local', getProjectContext: () => null,
      getProjectContextGeneration: () => authority.epoch},
    './useProjectStore': {useProjectStore: {getState: () => project}},
    './useDatasetStore': {useDatasetStore: {getState: () => dataset}},
    './useTrainingStore': {useTrainingStore: {getState: () => training}},
  });
  return {store, state: store.getState, source, calls, api, project, dataset, training, authority};
}

for (const outcome of ['success', 'failure']) test(`late report ${outcome} cannot restore another evaluation's message or saved path`, async () => {
  const f=fixture(), held=deferred();f.api.report={export:()=>held.promise};
  f.store.setState({jobId:'old_job'});
  const pending=f.state().exportReport('json').then(value=>({value}),error=>({error}));
  f.state().invalidateForDataChange();f.authority.epoch++;
  f.store.setState({jobId:'current_job',errorMessage:'현재 오류',exportedReportPath:'/current.json',isExportingReport:true});
  if(outcome==='success')held.resolve({file_path:'/old.json'});else held.reject(new Error('이전 보고서 오류'));
  await pending;
  assert.equal(f.state().errorMessage,'현재 오류');assert.equal(f.state().exportedReportPath,'/current.json');
  assert.equal(f.state().isExportingReport,true,'the old completion cannot finish the current export');
});
test('a current report failure remains visible and retry can save its path', async () => {
  const f=fixture();f.store.setState({jobId:'job_current'});f.api.report={export:async()=>{throw new Error('현재 보고서 오류');}};
  await assert.rejects(f.state().exportReport('json'),/현재 보고서 오류/);assert.equal(f.state().errorMessage,'현재 보고서 오류');
  f.api.report.export=async()=>({file_path:'/current.json'});await f.state().exportReport('json');
  assert.equal(f.state().isExportingReport,false);assert.equal(f.state().exportedReportPath,'/current.json');
});

// Without optional discovery, opening an untrained source dispatches an
// evaluation request that correctly fails404, including in Flowchart startup.
for (const task of ['classification', 'segmentation', 'detection', 'anomaly', 'patch_classification']) {
  test(`an untrained ${task} source remains an honest empty evaluation without requesting a missing job`, async () => {
    const f = fixture(task);
    await assert.doesNotReject(f.state().loadEvaluation(undefined, f.source));
    assert.deepEqual(f.calls, [['catalog', '/source/A']]);
    assert.equal(f.state().jobId, null);
    assert.equal(f.state().executionEvidence, null);
    assert.deepEqual(f.state().metrics, {});
    assert.deepEqual(f.state().testPredictions, []);
    assert.equal(f.state().isLoading, false);
    assert.match(f.state().errorMessage, /학습.*모델.*없/);
  });
}
test('a completed model for another task cannot trigger latest evaluation for the selected task', async () => {
  const f = fixture('segmentation');
  f.api.flowchart.modelCatalog = async folder => {f.calls.push(['catalog', folder]); return {models: [
    {job_id: 'job_other', task: 'classification', source_dataset_path: '/source/A'}], total: 1};};
  await assert.doesNotReject(f.state().loadEvaluation(undefined, f.source));
  assert.deepEqual(f.calls, [['catalog', '/source/A']]);
  assert.equal(f.state().jobId, null);
});
test('a discovered completed model keeps backend latest resolution and adopts the actual returned evidence', async () => {
  const f = fixture('segmentation');
  f.api.flowchart.modelCatalog = async folder => {f.calls.push(['catalog', folder]); return {models: [
    {job_id: 'job_completed', task: 'segmentation', source_dataset_path: '/source/A'}], total: 1};};
  const result = {job_id: 'job_completed', metrics: {accuracy: .7}, test_predictions: [], common_cohort: {}};
  f.api.evaluation.getResults = async (job, options) => {f.calls.push(['evaluation', job, options]); return result;};
  await f.state().loadEvaluation(undefined, f.source);
  assert.deepEqual(f.calls, [['catalog', '/source/A'], ['evaluation', undefined,
    {sourceDatasetPath: '/source/A', sourceTask: 'segmentation', evaluationDatasetVersionId: undefined,
      computeProfileId: undefined, device: undefined}]]);
  assert.equal(f.state().jobId, 'job_completed');
  assert.equal(f.state().executionEvidence, result);
  assert.deepEqual(f.state().metrics, {accuracy: .7});
});
test('a checkpoint disappearing after discovery remains a real evaluation error', async () => {
  const f = fixture();
  f.api.flowchart.modelCatalog = async folder => {f.calls.push(['catalog', folder]); return {models: [
    {job_id: 'job_removed', task: 'classification', source_dataset_path: '/source/A'}], total: 1};};
  await assert.rejects(f.state().loadEvaluation(undefined, f.source), /checkpoint not found/);
  assert.deepEqual(f.calls.map(call => call[0]), ['catalog', 'evaluation']);
  assert.match(f.state().errorMessage, /checkpoint not found/);
});
for (const selected of ['explicit', 'stored', 'current-completed']) {
  test(`a missing ${selected} job remains an evaluation error even when discovery would be empty`, async () => {
    const f = fixture();
    if (selected === 'stored') f.store.setState({jobId: 'job_missing'});
    if (selected === 'current-completed') Object.assign(f.training, {isCurrentData: true, status: 'completed', jobId: 'job_missing'});
    await assert.rejects(f.state().loadEvaluation(selected === 'explicit' ? 'job_missing' : undefined, f.source), /checkpoint not found/);
    assert.equal(f.calls.length, 1);
    assert.deepEqual(f.calls[0].slice(0, 2), ['evaluation', 'job_missing']);
    assert.match(f.state().errorMessage, /checkpoint not found/);
    assert.equal(f.state().executionEvidence, null);
  });
}
test('discovery failure remains an error and cannot be relabeled as no completed model', async () => {
  const f = fixture();
  f.api.flowchart.modelCatalog = async () => {throw new Error('Catalog permission changed');};
  await assert.rejects(f.state().loadEvaluation(undefined, f.source), /permission changed/);
  assert.equal(f.calls.length, 0);
  assert.match(f.state().errorMessage, /permission changed/);
});
test('caller mutation while discovery is pending cannot redirect its evaluation source', async () => {
  const f = fixture(), held = deferred();
  f.api.flowchart.modelCatalog = async folder => {f.calls.push(['catalog', folder]); return held.promise;};
  f.api.evaluation.getResults = async (job, options) => {
    f.calls.push(['evaluation', job, options]);
    return {job_id: 'job_completed', metrics: {}, test_predictions: [], common_cohort: {}};
  };
  const pending = f.state().loadEvaluation(undefined, f.source);
  f.source.folderPath = '/foreign'; f.source.task = 'segmentation';
  held.resolve({models: [{job_id: 'job_completed', task: 'classification', source_dataset_path: '/source/A'}], total: 1});
  await pending;
  assert.equal(f.calls[0][0], 'catalog');
  assert.equal(f.calls[1][2].sourceDatasetPath, '/source/A');
  assert.equal(f.calls[1][2].sourceTask, 'classification');
  assert.equal(f.state().jobId, 'job_completed');
});
for (const reply of ['empty', 'model', 'failure']) {
  test(`late ${reply} discovery across A-B-A cannot dispatch an evaluation or overwrite the current state`, async () => {
    const f = fixture(), held = deferred();
    f.api.flowchart.modelCatalog = async () => held.promise;
    const pending = f.state().loadEvaluation(undefined, f.source);
    f.authority.epoch += 2;
    f.store.setState({jobId: 'job_current', isLoading: false, metrics: {accuracy: .8}, errorMessage: 'current scope'});
    if (reply === 'failure') held.reject(new Error('old discovery failure'));
    else held.resolve({models: reply === 'empty' ? [] : [{job_id: 'job_old', task: 'classification', source_dataset_path: '/source/A'}], total: reply === 'empty' ? 0 : 1});
    await pending;
    assert.deepEqual(f.calls, []);
    assert.equal(f.state().jobId, 'job_current');
    assert.deepEqual(f.state().metrics, {accuracy: .8});
    assert.equal(f.state().errorMessage, 'current scope');
    assert.equal(f.state().isLoading, false);
  });
}
