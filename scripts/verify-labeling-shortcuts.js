const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const sourcePath = path.resolve(__dirname, '../src/renderer/components/labeling/labelingShortcuts.ts');
const sourceModule = new Module(sourcePath, module);
sourceModule.filename = sourcePath;
sourceModule.paths = Module._nodeModulePaths(path.dirname(sourcePath));
sourceModule._compile(ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, sourcePath);
const { resolveLabelingShortcut } = sourceModule.exports;

test('number keys select the six tools named in the labeling toolbar', () => {
  const expected = ['select', 'bbox', 'rotated_bbox', 'polygon', 'brush', 'eraser'];
  for (let n = 1; n <= 6; n += 1) {
    assert.deepEqual(resolveLabelingShortcut({ key: String(n) }), { kind: 'tool', tool: expected[n - 1] });
  }
});

test('F fits the image and standard platform undo/redo shortcuts are distinct', () => {
  assert.deepEqual(resolveLabelingShortcut({ key: 'f' }), { kind: 'fit' });
  assert.deepEqual(resolveLabelingShortcut({ key: 'F', shiftKey: true }), { kind: 'fit' });
  assert.deepEqual(resolveLabelingShortcut({ key: 'ㄹ', code: 'KeyF' }), { kind: 'fit' });
  assert.deepEqual(resolveLabelingShortcut({ key: 'z', metaKey: true }), { kind: 'undo' });
  assert.deepEqual(resolveLabelingShortcut({ key: 'y', ctrlKey: true }), { kind: 'redo' });
  assert.deepEqual(resolveLabelingShortcut({ key: 'Z', metaKey: true, shiftKey: true }), { kind: 'redo' });
});

test('modified and composing keystrokes do not select a tool or fit the view', () => {
  assert.equal(resolveLabelingShortcut({ key: '1', metaKey: true }), null);
  assert.equal(resolveLabelingShortcut({ key: 'f', altKey: true }), null);
  assert.equal(resolveLabelingShortcut({ key: 'ㄹ', code: 'KeyF', metaKey: true }), null);
  assert.equal(resolveLabelingShortcut({ key: '1', isComposing: true }), null);
});
