const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

function load(relative) {
  const file = path.resolve(__dirname, relative);
  const instance = new Module(file, module);
  instance.filename = file;
  instance.paths = Module._nodeModulePaths(path.dirname(file));
  instance._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText, file);
  return instance.exports;
}

test('form field associates its visible label, help and error with the real control', () => {
  const { FormField } = load('FormField.tsx');
  const html = renderToStaticMarkup(React.createElement(FormField, {
    id: 'name', label: '기록 검색', hint: '모델 ID 입력', error: '입력 값을 확인하세요',
    children: props => React.createElement('input', { ...props, value: '', readOnly: true }),
  }));
  assert.match(html, /for="name"/);
  assert.match(html, /id="name"/);
  assert.match(html, /aria-describedby="name-hint name-error"/);
  assert.match(html, /aria-invalid="true"/);
  assert.match(html, /role="alert"/);
});

test('pending action prevents duplicate submission and exposes its operation state', () => {
  const { AsyncAction } = load('AsyncAction.tsx');
  const html = renderToStaticMarkup(React.createElement(AsyncAction, {
    pending: true, pendingLabel: '기록 확인 중', children: '새로고침',
  }));
  assert.match(html, /type="button"/);
  assert.match(html, /disabled=""/);
  assert.match(html, /aria-busy="true"/);
  assert.match(html, /기록 확인 중/);
  assert.doesNotMatch(html, />새로고침</);
});

test('status includes visible meaning and hides its decorative symbol from assistive technology', () => {
  const { StatusBadge } = load('StatusBadge.tsx');
  for (const tone of ['success', 'warning', 'danger', 'neutral', 'info']) {
    const html = renderToStaticMarkup(React.createElement(StatusBadge, { tone, children: '근거 부족' }));
    assert.match(html, /근거 부족/);
    assert.match(html, /aria-hidden="true"/);
    assert.match(html, new RegExp(`data-tone="${tone}"`));
  }
});

test('error feedback retains the cause and a keyboard actionable remedy', () => {
  const { WorkspaceFeedback } = load('../layout/WorkspaceFeedback.tsx');
  const html = renderToStaticMarkup(React.createElement(WorkspaceFeedback, {
    kind: 'error', title: '기록을 불러오지 못했습니다', description: '저장소 연결 실패',
    action: React.createElement('button', { type: 'button' }, '다시 확인'),
  }));
  assert.match(html, /role="alert"/);
  assert.match(html, /저장소 연결 실패/);
  assert.match(html, /<button type="button">다시 확인<\/button>/);
});
