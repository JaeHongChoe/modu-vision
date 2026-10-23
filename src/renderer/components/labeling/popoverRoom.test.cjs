// Step 2's popovers hang below their row over the canvas. The draft queue kept its panel to the room left on screen;
// 모델 보조 used a fixed cap (100vh - 195 px) and ran past the work column at 1440x900, so its last buttons were cut
// off. Both now use the same measured room.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

function load(react, prelude = '') {
  const filename = path.join(__dirname, 'popoverRoom.ts'), m = new Module(filename, module);
  m.filename = filename;
  m.require = name => { assert.equal(name, 'react'); return react; };
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  m._compile(output.replace('"use strict";', `"use strict";${prelude}`), filename);
  return m.exports;
}

test('the room is what is left above the lowest edge the panel can show at, less 12 px, never under 240', () => {
  const {roomBelow} = load({useEffect() {}, useState: value => [value, () => {}]});
  assert.equal(roomBelow(450, [845], 900), 383, 'the work column ends above the window bottom (the step footer)');
  assert.equal(roomBelow(450, [], 900), 438, 'nothing clips it: the window');
  assert.equal(roomBelow(450, [1200, 760], 900), 298, 'the lowest clipping edge wins');
  assert.equal(roomBelow(450, [1200, 700], 900), 240, 'never under 240 (238 left)');
  assert.equal(roomBelow(800, [845], 900), 240);
});

test('the hook measures the row against every clipping ancestor, follows resize and scroll while open, then lets go', () => {
  const sets = [], cleanups = [], listeners = [];
  const react = {useState: () => [undefined, value => sets.push(value)], useEffect: fn => { const cleanup = fn(); if (cleanup) cleanups.push(cleanup); }};
  const {usePopoverRoom} = load(react,
    'var window = {innerHeight: 900, addEventListener(type, fn, capture) { globalThis.listeners.push(["+", type, capture === true]); globalThis.__fit = fn; }, removeEventListener(type, fn, capture) { globalThis.listeners.push(["-", type, capture === true]); }};'
    + 'var getComputedStyle = node => ({overflowY: node.overflowY});');
  globalThis.listeners = listeners;
  const body = {overflowY: 'visible', getBoundingClientRect: () => ({bottom: 900}), parentElement: null};
  const column = {overflowY: 'hidden', getBoundingClientRect: () => ({bottom: 845}), parentElement: body};
  const group = {overflowY: 'visible', getBoundingClientRect: () => ({bottom: 450}), parentElement: column};
  let rowBottom = 450;
  const row = {getBoundingClientRect: () => ({bottom: rowBottom}), parentElement: group};
  try {
    usePopoverRoom(true, {current: row});
    assert.deepEqual(sets, [383], 'measured at once against the work column');
    assert.deepEqual(listeners, [['+', 'resize', false], ['+', 'scroll', true]], 'any scroll (capture) and resize re-measure');
    rowBottom = 300; globalThis.__fit();
    assert.deepEqual(sets, [383, 533]);
    cleanups.forEach(cleanup => cleanup());
    assert.deepEqual(listeners.slice(2), [['-', 'resize', false], ['-', 'scroll', true]]);
    sets.length = 0; listeners.length = 0;
    usePopoverRoom(false, {current: row});
    assert.deepEqual(sets, [], 'closed: nothing measured'); assert.deepEqual(listeners, []);
  } finally { delete globalThis.__fit; delete globalThis.listeners; }
});

test('the draft queue and 모델 보조 both keep their panel to that room', () => {
  const queue = fs.readFileSync(path.join(__dirname, 'DraftReviewQueue.tsx'), 'utf8');
  assert.match(queue, /const room = usePopoverRoom\(open, rowRef\);/);
  assert.match(queue, /<div ref=\{rowRef\} data-surface="bar" className=\{`relative /);
  assert.match(queue, /<aside style=\{\{ maxHeight: room \}\}/);
  const assist = fs.readFileSync(path.join(__dirname, 'ModelAssistPanel.tsx'), 'utf8');
  assert.match(assist, /const room = usePopoverRoom\(open, rowRef\);/);
  assert.match(assist, /<div ref=\{rowRef\} className="relative z-\[70\] /);
  assert.match(assist, /<aside style=\{\{ maxHeight: room \}\} className="absolute right-3 top-full /);
});
