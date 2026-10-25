const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');

const file = path.join(__dirname, 'DetectionEvaluationGrains.tsx');
const componentModule = new Module(file, module);
componentModule.filename = file;
componentModule.paths = Module._nodeModulePaths(__dirname);
componentModule._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText, file);
const { DetectionEvaluationGrains } = componentModule.exports;

const summary = {
  matchedClasses: 8, classSamples: 12, threshold: 0.4,
  imageTp: 7, imageFn: 2, imageFp: 3, imageTn: 4, map50: 0.625,
};
const render = props => renderToStaticMarkup(React.createElement(DetectionEvaluationGrains, {
  summary, language: 'ko', ...props,
}));

// Removing the simple branch, moving a technical metric outside it, or opening its
// details by default must fail this check against the real component's HTML.
test('simple view shows image error counts while technical detection metrics start collapsed', () => {
  const html = render({ simple: true });
  const details = html.match(/<details\b([^>]*)>([\s\S]*?)<\/details>/);
  assert.ok(details, 'technical detection metrics remain reachable in a details disclosure');
  assert.doesNotMatch(details[1], /\bopen(?:\s|=|$)/, 'technical details start closed');
  assert.match(details[2], /<summary\b[^>]*>[^<]*자세히/);
  assert.match(details[2], /τ=0\.40/);
  assert.match(details[2], /TP 7 · FN 2 · FP 3 · TN 4/);
  assert.match(details[2], /mAP@IoU 0\.5: 0\.6250/);
  const visible = html.replace(details[0], '');
  assert.match(visible, /놓친 불량[^<]*2장/);
  assert.match(visible, /잘못 잡은 정상[^<]*3장/);
  assert.match(visible, /맞힌 이미지[^<]*11장/);
  assert.doesNotMatch(visible, /τ|\b(?:TP|FN|FP|TN|mAP|IoU)\b/);
});

test('full and default views preserve the directly visible technical metrics', () => {
  for (const props of [{}, { simple: false }]) {
    const html = render(props);
    assert.doesNotMatch(html, /<details\b/);
    assert.match(html, /τ=0\.40/);
    assert.match(html, /TP 7 · FN 2 · FP 3 · TN 4/);
    assert.match(html, /8\/12 일치/);
    assert.match(html, /mAP@IoU 0\.5: 0\.6250/);
  }
});

test('English simple view keeps plain image counts outside the closed technical details', () => {
  const html = render({ simple: true, language: 'en' });
  const details = html.match(/<details\b([^>]*)>([\s\S]*?)<\/details>/);
  assert.ok(details);
  assert.doesNotMatch(details[1], /\bopen(?:\s|=|$)/);
  const visible = html.replace(details[0], '');
  assert.match(visible, /Missed defects[^<]*2 images/);
  assert.match(visible, /Normal images flagged[^<]*3 images/);
  assert.match(visible, /Correct images[^<]*11 images/);
  assert.doesNotMatch(visible, /τ|\b(?:TP|FN|FP|TN|mAP|IoU)\b/);
});
