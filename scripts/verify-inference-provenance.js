const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const sourcePath = path.resolve(__dirname, '../src/renderer/components/inference/selectInferenceJob.ts');
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUnderTest = new Module(sourcePath, module);
moduleUnderTest.filename = sourcePath;
moduleUnderTest.paths = Module._nodeModulePaths(path.dirname(sourcePath));
moduleUnderTest._compile(compiled, sourcePath);
const { selectInferenceJobId } = moduleUnderTest.exports;

test('Step 6 uses a completed model from the current data', () => {
  assert.equal(selectInferenceJobId(
    { jobId: 'job_A', status: 'completed', isCurrentData: true },
    { jobId: 'older_eval', allowLatestRecovery: false },
  ), 'job_A');
});

test('Step 6 rejects old A while B is selected or training', () => {
  assert.equal(selectInferenceJobId(
    { jobId: 'job_A', status: 'stopping', isCurrentData: false },
    { jobId: 'job_A', allowLatestRecovery: false },
  ), null);
  assert.equal(selectInferenceJobId(
    { jobId: 'job_B', status: 'running', isCurrentData: true },
    { jobId: 'job_A', allowLatestRecovery: false },
  ), null);
  assert.equal(selectInferenceJobId(
    { jobId: 'job_B', status: 'completed', isCurrentData: true },
    { jobId: null, allowLatestRecovery: false },
  ), 'job_B');
});

test('Step 6 recovers latest only before the data changes', () => {
  const training = { jobId: null, status: 'idle', isCurrentData: false };
  assert.equal(selectInferenceJobId(training, {
    jobId: 'job_A', allowLatestRecovery: true,
  }), 'job_A');
  assert.equal(selectInferenceJobId(training, {
    jobId: 'job_A', allowLatestRecovery: false,
  }), null);
});
