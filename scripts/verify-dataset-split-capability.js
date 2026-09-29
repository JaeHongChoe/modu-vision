const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const sourcePath = path.resolve(__dirname, '../src/renderer/utils/datasetSplitCapability.ts');
const sourceModule = new Module(sourcePath, module);
sourceModule.filename = sourcePath;
sourceModule.paths = Module._nodeModulePaths(path.dirname(sourcePath));
sourceModule._compile(ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, sourcePath);
const { isSplitUnavailable } = sourceModule.exports;

test('flat LabelMe detection follows the backend split capability', () => {
  assert.equal(isSplitUnavailable('detection', true), false);
  assert.equal(isSplitUnavailable('detection', false), true);
});

test('legacy tasks remain gated until the import response resolves capability', () => {
  assert.equal(isSplitUnavailable('detection', null), true);
  assert.equal(isSplitUnavailable('anomaly', null), true);
  assert.equal(isSplitUnavailable('classification', null), false);
  assert.equal(isSplitUnavailable('segmentation', true), false);
  assert.equal(isSplitUnavailable('segmentation', false), true);
});
