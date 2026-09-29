const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const sourcePath = path.resolve(__dirname, '../src/renderer/components/inference/exportGuidance.ts');
const sourceModule = new Module(sourcePath, module);
sourceModule.filename = sourcePath;
sourceModule.paths = Module._nodeModulePaths(path.dirname(sourcePath));
sourceModule._compile(ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, sourcePath);
const { exportPackageGuidance } = sourceModule.exports;

test('detection export guidance describes detector scope and threshold without segmentation tiles', () => {
  const ko = exportPackageGuidance('detection', true);
  const en = exportPackageGuidance('detection', false);
  assert.match(ko, /검출 모델/);
  assert.match(ko, /신뢰도 임계값/);
  assert.doesNotMatch(ko, /분할 모델|타일/);
  assert.match(en, /detection model/);
  assert.match(en, /confidence threshold/);
  assert.doesNotMatch(en, /segmentation|tiles/);
});

test('segmentation export guidance keeps tiled inference limitations', () => {
  assert.match(exportPackageGuidance('segmentation', true), /분할 모델.*겹치는 타일/);
  assert.match(exportPackageGuidance('segmentation', false), /segmentation.*overlapping tiles/);
});

test('classification and unavailable anomaly exports have their own guidance', () => {
  assert.match(exportPackageGuidance('classification', true), /분류 모델/);
  assert.doesNotMatch(exportPackageGuidance('classification', true), /분할 모델|타일/);
  assert.match(exportPackageGuidance('anomaly', true), /현재 제공되지 않습니다/);
});
