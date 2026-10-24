// Step 2's popovers hang below their row over the canvas. The draft queue kept its panel to the room left on screen;
// 모델 보조 used a fixed cap (100vh - 195 px) and ran past the work column at 1440x900, so its last buttons were cut
// off. Both now use the same measured room.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

test('draft popovers stay inside the clipping workspace at full, wide and drawer widths', () => {
  const layout = load({});
  assert.equal(typeof layout.horizontalPopoverBounds, 'function', 'horizontal placement is derived from the row and clipping workspace');
  for (const row of [
    {name: '1024 wide, middle third', anchor: {left: 342, right: 682}, clips: [{left: 0, right: 1024}], viewport: 1024, preferred: 1200, want: {left: -330, width: 1000}},
    {name: '1024 normal, middle third', anchor: {left: 342, right: 682}, clips: [{left: 0, right: 1024}], viewport: 1024, preferred: 540, want: {left: -212, width: 540}},
    {name: 'native 1280 wide', anchor: {left: 427, right: 853}, clips: [{left: 0, right: 1280}], viewport: 1280, preferred: 1200, want: {left: -415, width: 1200}},
    {name: 'drawer reserves 416px', anchor: {left: 200, right: 400}, clips: [{left: 0, right: 1024}, {left: 0, right: 608}], viewport: 1024, preferred: 1200, want: {left: -188, width: 584}},
    {name: 'normal drawer popup', anchor: {left: 200, right: 400}, clips: [{left: 0, right: 608}], viewport: 1024, preferred: 540, want: {left: -188, width: 540}},
    {name: 'offset work column', anchor: {left: 440, right: 640}, clips: [{left: 240, right: 1024}], viewport: 1440, preferred: 1200, want: {left: -188, width: 760}},
  ]) assert.deepEqual(layout.horizontalPopoverBounds(row.anchor, row.clips, row.viewport, row.preferred), row.want, row.name);
});

test('horizontal placement follows a drawer resizing the row without a window resize and disconnects on close', () => {
  const cleanups = [], sets = [], observed = [], listeners = [];
  let state;
  const layout = load({useState: () => [undefined, next => { const value = typeof next === 'function' ? next(state) : next; if (value !== state) sets.push(value); state = value; }],
    useLayoutEffect: fn => cleanups.push(fn())},
    'var window = {innerWidth: 1024, addEventListener(type, fn, capture) { globalThis.boundsListeners.push(["+", type, capture === true]); }, removeEventListener(type, fn, capture) { globalThis.boundsListeners.push(["-", type, capture === true]); }};'
      + 'var getComputedStyle = node => ({overflowX: node.overflowX});'
      + 'var ResizeObserver = class { constructor(fn) {globalThis.boundsResize=fn;} observe(node) {globalThis.boundsObserved.push(node);} disconnect() {globalThis.boundsDisconnected=true;} };');
  assert.equal(typeof layout.usePopoverHorizontalBounds, 'function', 'the draft panel can follow changes to its clipping column');
  const clip = {overflowX: 'hidden', getBoundingClientRect: () => ({left: 0, right: 608}), parentElement: null};
  let anchor = {left: 200, right: 400};
  const row = {getBoundingClientRect: () => anchor, parentElement: clip};
  Object.assign(globalThis, {boundsListeners: listeners, boundsObserved: observed, boundsDisconnected: false});
  try {
    layout.usePopoverHorizontalBounds(true, {current: row}, 1200);
    assert.deepEqual(sets, [{left: -188, width: 584}]);
    assert.deepEqual(observed, [row, clip]);
    clip.getBoundingClientRect = () => ({left: 0, right: 500}); anchor = {left: 167, right: 333}; globalThis.boundsResize();
    assert.deepEqual(sets.at(-1), {left: -155, width: 476});
    const before = sets.length; globalThis.boundsResize(); assert.equal(sets.length, before, 'unchanged geometry reuses state');
    cleanups.forEach(fn => fn()); assert.equal(globalThis.boundsDisconnected, true);
    assert.deepEqual(listeners, [['+', 'resize', false], ['+', 'scroll', true], ['-', 'resize', false], ['-', 'scroll', true]]);
  } finally { for (const key of ['boundsListeners', 'boundsObserved', 'boundsDisconnected', 'boundsResize']) delete globalThis[key]; }
});

function load(react, prelude = '') {
  const filename = path.join(__dirname, 'popoverRoom.ts'), m = new Module(filename, module);
  m.filename = filename;
  m.require = name => { assert.equal(name, 'react'); return react; };
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  m._compile(output.replace('"use strict";', `"use strict";${prelude}`), filename);
  return m.exports;
}

test('the room fits above the lowest clipping edge, including a drawer leaving less than 240px', () => {
  const {roomBelow} = load({useEffect() {}, useState: value => [value, () => {}]});
  assert.equal(roomBelow(450, [845], 900), 383, 'the work column ends above the window bottom (the step footer)');
  assert.equal(roomBelow(450, [], 900), 438, 'nothing clips it: the window');
  assert.equal(roomBelow(450, [1200, 760], 900), 298, 'the lowest clipping edge wins');
  assert.equal(roomBelow(450, [1200, 700], 900), 238, 'a minimum height cannot extend below the clipping edge');
  assert.equal(roomBelow(651, [845], 900), 182, 'the real drawer layout leaves 182px');
  assert.equal(roomBelow(800, [845], 900), 33);
  assert.equal(roomBelow(850, [845], 900), 0);
});

test('vertical room follows ancestor layout changes and releases its resize observer', () => {
  const observed = [], sets = [], cleanups = [];
  const {usePopoverRoom} = load({useState: () => [undefined, value => sets.push(value)], useEffect: fn => cleanups.push(fn())},
    'var window = {innerHeight: 900, addEventListener() {}, removeEventListener() {}};'
      + 'var getComputedStyle = node => ({overflowY: node.overflowY});'
      + 'var ResizeObserver = class { constructor(fn) {globalThis.verticalResize=fn;} observe(node) {globalThis.verticalObserved.push(node);} disconnect() {globalThis.verticalDisconnected=true;} };');
  const clip = {overflowY: 'hidden', getBoundingClientRect: () => ({bottom: 845}), parentElement: null};
  const settings = {previousElementSibling: null};
  const beforeRow = {previousElementSibling: null};
  const bars = {overflowY: 'visible', parentElement: clip, previousElementSibling: settings};
  let bottom = 450;
  const row = {getBoundingClientRect: () => ({bottom}), parentElement: bars, previousElementSibling: beforeRow};
  Object.assign(globalThis, {verticalObserved: observed, verticalDisconnected: false});
  try {
    usePopoverRoom(true, {current: row});
    assert.deepEqual(sets, [383]);
    assert.deepEqual(observed, [row, beforeRow, bars, settings, clip], 'preceding rows and the settings strip can move the popup while its ancestor sizes stay unchanged');
    bottom = 651; globalThis.verticalResize(); assert.equal(sets.at(-1), 182);
    cleanups.forEach(fn => fn()); assert.equal(globalThis.verticalDisconnected, true);
  } finally { for (const key of ['verticalObserved', 'verticalDisconnected', 'verticalResize']) delete globalThis[key]; }
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
  assert.match(queue, /<aside style=\{\{ maxHeight: room(?:,| \}\})/);
  const assist = fs.readFileSync(path.join(__dirname, 'ModelAssistPanel.tsx'), 'utf8');
  assert.match(assist, /const room = usePopoverRoom\(open, rowRef\);/);
  assert.match(assist, /<div ref=\{rowRef\} className="relative z-\[70\] /);
  assert.match(assist, /<aside style=\{\{ maxHeight: room \}\} className="absolute right-3 top-full /);
});
