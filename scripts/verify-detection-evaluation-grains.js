const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const filename = path.resolve(__dirname, '../src/renderer/components/evaluation/DetectionEvaluationGrains.tsx');
const loaded = new Module(filename, module);
loaded.filename = filename;
loaded.paths = Module._nodeModulePaths(path.dirname(filename));
loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
}).outputText, filename);
const { summarizeDetectionGrains, DetectionEvaluationGrains } = loaded.exports;

test('detection result keeps top class, localization, and thresholded image verdicts separate', () => {
  const summary = summarizeDetectionGrains({
    matrix: [[0, 0], [2, 6]],
    verdicts: Array(8).fill('ESCAPE'),
    threshold: 0.5,
    map50: 0,
  });
  assert.deepEqual(summary, {
    matchedClasses: 6, classSamples: 8, threshold: 0.5,
    imageTp: 0, imageFn: 8, imageFp: 0, imageTn: 0,
    map50: 0,
  });
  const markup = renderToStaticMarkup(React.createElement(DetectionEvaluationGrains, {
    summary, language: 'ko',
  }));
  assert.match(markup, /이미지 OK\/NG/);
  assert.match(markup, /τ=0\.50/);
  assert.match(markup, /FN 8/);
  assert.match(markup, /상위 박스 클래스/);
  assert.match(markup, /6\/8/);
  assert.match(markup, /τ·IoU 미적용/);
  assert.match(markup, /행·열 일치율/);
  assert.match(markup, /mAP@IoU 0\.5/);
  assert.match(markup, /0\.0000/);
});
