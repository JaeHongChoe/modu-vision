const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// Execute the actual TSX handlers and coordinate helper. React state/effect
// scheduling and the deferred external request boundary are controlled. No DOM,
// HTTP, backend, decoder or geometric fitting engine is executed or claimed.
const jsx = (type, props) => ({type, props: props || {}});
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === 'object'
  ? [tree, ...nodes(tree.props?.children)] : [];
const text = tree => Array.isArray(tree) ? tree.map(text).join('') : tree && typeof tree === 'object'
  ? text(tree.props?.children) : String(tree ?? '');
const tick = () => new Promise(setImmediate);
const SOURCE = '/owned/source';
const IMAGE = `${SOURCE}/image-a.png`;
const OTHER_IMAGE = `${SOURCE}/image-b.png`;
const FIT = Object.freeze({box: Object.freeze({cx: 160, cy: 80, width: 192, height: 96, angle_deg: 0}),
  polygon: Object.freeze([[64, 32], [256, 32], [256, 128], [64, 128]].map(Object.freeze)), source_sha256: 'a'.repeat(64)});

function load(file, mocks) {
  const filename = path.resolve(__dirname, file), m = new Module(filename, module);
  m.filename = filename; m.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = m.require.bind(m);
  m.require = name => {
    if (name in mocks) return mocks[name];
    const relative = name.startsWith('.') ? path.resolve(path.dirname(filename), name + '.ts') : null;
    return relative && fs.existsSync(relative) ? load(relative, mocks) : original(name);
  };
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {fileName: filename, compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  }}).outputText, filename);
  return m.exports;
}

function fixture(t) {
  let cursor = 0, dirty = true, live = true, tree, postUnmountUpdates = 0;
  const slots = [], effects = [], requests = [], appends = [];
  const props = {source: SOURCE, scope: 'project-A', onAppend: row => appends.push(row)};
  const react = {
    useState(initial) {
      const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], value => {
        if (!live) postUnmountUpdates++;
        slots[i] = typeof value === 'function' ? value(slots[i]) : value; dirty = true;
      }];
    },
    useRef(initial) { const i = cursor++; return slots[i] ?? (slots[i] = {current: initial}); },
    useEffect(fn, deps) {
      const i = cursor++, previous = slots[i];
      if (!previous || deps.length !== previous.deps.length || deps.some((value, j) => !Object.is(value, previous.deps[j]))) {
        const slot = {deps: [...deps]}; slots[i] = slot;
        effects.push(() => {previous?.cleanup?.(); slot.cleanup = fn();});
      }
    },
  };
  const request = (route, options) => {
    if (route.startsWith('/api/rotated-detection/fit-source?')) {
      const image = new URL(route, 'http://owned.invalid').searchParams.get('image_path');
      assert.ok([IMAGE, OTHER_IMAGE].includes(image), 'preview remains bound to an explicitly selected owned image');
      return Promise.resolve(Object.freeze({source_size: Object.freeze([512, 256]),
        preview_data_url: 'data:image/png;base64,b3duZWQtcHJldmlldw==', source_sha256: 'a'.repeat(64)}));
    }
    assert.equal(route, '/api/rotated-detection/fit-box'); assert.equal(options.method, 'POST');
    const body = Object.freeze(JSON.parse(options.body));
    return new Promise((resolve, reject) => requests.push(Object.freeze({body, resolve, reject})));
  };
  const {RotatedBoxFitting} = load('RotatedBoxFitting.tsx', {
    react, 'react/jsx-runtime': {jsx, jsxs: jsx}, '../../services/api': {request},
    '../../stores/useDatasetStore': {useDatasetStore: selector => selector({images: [
      {image_id: 'a', file_name: 'image-a.png', file_path: IMAGE},
      {image_id: 'b', file_name: 'image-b.png', file_path: OTHER_IMAGE},
    ]})}, './ProgramWorkbenchControls': {programButton: '', programInput: ''},
  });
  function render() {
    assert.ok(live); let passes = 0;
    do {
      assert.ok(passes++ < 20, 'component state/effect render must settle');
      cursor = 0; dirty = false; tree = RotatedBoxFitting(props); effects.splice(0).forEach(run => run());
    } while (dirty);
  }
  async function settle() {if (live) render(); await tick(); if (live && dirty) render();}
  const button = label => {const node = nodes(tree).find(n => n.type === 'button' && text(n) === label); assert.ok(node, label); return node;};
  async function click(label) {const node = button(label); assert.ok(!node.props.disabled, `${label} is enabled`); node.props.onClick(); await settle();}
  async function select(image) {const node = nodes(tree).find(n => n.type === 'select' && nodes(n).some(c => c.type === 'option' && c.props.value === IMAGE)); assert.ok(node); node.props.onChange({target: {value: image}}); await settle();}
  async function draw() {
    for (const [clientX, clientY] of [[160, 80], [256, 80], [160, 128]]) {
      const svg = nodes(tree).find(n => n.type === 'svg'); assert.ok(svg, 'selected original preview is rendered');
      svg.props.onClick({clientX, clientY, currentTarget: {getBoundingClientRect: () => ({left: 0, top: 0, width: 512, height: 256})}});
      await settle();
    }
  }
  function unmount() {if (live) {slots.forEach(slot => slot?.cleanup?.()); live = false;}}
  t.after(unmount);
  const f = {props, requests, appends, settle, button, click, select, draw, unmount,
    all: () => nodes(tree), text: () => text(tree), postUnmountUpdates: () => postUnmountUpdates,
    change: async patch => {Object.assign(props, patch); await settle();},
    start: async () => {await settle(); await select(IMAGE); await draw(); await click('회전 박스 맞추기'); return requests.at(-1);},
  };
  return f;
}

function noProposal(f) {
  assert.equal(f.button('정답 표에 추가').props.disabled, true, 'discarded fit must not become appendable');
  assert.equal(f.all().filter(n => n.type === 'polygon').length, 0);
  assert.deepEqual(f.appends, []);
}

test('a fresh original fit uses selected source pixels and appends the exact returned box', async t => {
  const f = fixture(t), pending = await f.start();
  assert.deepEqual(pending.body, {image_path: IMAGE, mode: 'center', points: [[160, 80], [256, 80], [160, 128]], source_sha256: 'a'.repeat(64)});
  noProposal(f); pending.resolve(FIT); await f.settle();
  assert.equal(f.button('정답 표에 추가').props.disabled, false);
  assert.equal(f.all().filter(n => n.type === 'polygon').length, 1);
  await f.click('정답 표에 추가');
  assert.deepEqual(f.appends, ['image-a.png\tdefect\t160,80,192,96,0\ttrain']);
  assert.equal(f.button('정답 표에 추가').props.disabled, true);
});

test('redraw discards a pending fit and leaves new points ready for another real request', async t => {
  const f = fixture(t), old = await f.start();
  await f.click('점 다시 그리기'); old.resolve(FIT); await f.settle(); noProposal(f);
  await f.draw(); await f.click('회전 박스 맞추기');
  f.requests.at(-1).resolve(FIT); await f.settle();
  assert.equal(f.button('정답 표에 추가').props.disabled, false);
});

test('mode change discards the pending old-mode proposal', async t => {
  const f = fixture(t), old = await f.start();
  await f.click('한 면 기준'); old.resolve(FIT); await f.settle(); noProposal(f);
  await f.draw(); await f.click('회전 박스 맞추기'); assert.equal(f.requests.at(-1).body.mode, 'face');
  f.requests.at(-1).resolve(FIT); await f.settle(); assert.equal(f.button('정답 표에 추가').props.disabled, false);
});

for (const intent of ['image', 'project', 'source']) test(`${intent} A-to-B-to-A rejects the first pending proposal`, async t => {
  const f = fixture(t), old = await f.start();
  if (intent === 'image') {await f.select(OTHER_IMAGE); await f.select(IMAGE);}
  if (intent === 'project') {await f.change({scope: 'project-B'}); await f.change({scope: 'project-A'}); await f.select(IMAGE);}
  if (intent === 'source') {await f.change({source: '/owned/other-source'}); await f.change({source: SOURCE}); await f.select(IMAGE);}
  old.resolve(FIT); await f.settle(); noProposal(f);
});

test('a changed source clears its old-image proposal before another request', async t => {
  const f = fixture(t), old = await f.start(); old.resolve(FIT); await f.settle();
  await f.change({source: '/owned/other-source'}); noProposal(f);
  assert.equal(f.all().filter(n => n.type === 'svg').length, 0);
});

test('an old rejected fit cannot release or contaminate the newer pending fit', async t => {
  const f = fixture(t), old = await f.start();
  await f.select(OTHER_IMAGE); await f.select(IMAGE); await f.draw();
  assert.equal(f.button('회전 박스 맞추기').props.disabled, false, 'new image intent owns an editable proposal');
  await f.click('회전 박스 맞추기'); const current = f.requests.at(-1);
  old.reject(new Error('Stale request refused')); await f.settle();
  assert.equal(f.button('회전 박스 맞추기').props.disabled, true, 'the current request remains busy');
  assert.equal(f.all().filter(n => n.props?.role === 'alert').length, 0);
  current.resolve(FIT); await f.settle(); assert.equal(f.button('정답 표에 추가').props.disabled, false);
});

test('an old successful fit cannot overwrite the newer fit refusal', async t => {
  const f = fixture(t), old = await f.start(); await f.select(OTHER_IMAGE); await f.select(IMAGE); await f.draw();
  assert.equal(f.button('회전 박스 맞추기').props.disabled, false);
  await f.click('회전 박스 맞추기'); f.requests.at(-1).reject(new Error('Current source refused'));
  await f.settle(); old.resolve(FIT); await f.settle(); noProposal(f);
  assert.match(f.text(), /Current source refused/);
});

test('a refused refit cannot leave the previous successful proposal appendable', async t => {
  const f = fixture(t), first = await f.start(); first.resolve(FIT); await f.settle();
  assert.equal(f.button('정답 표에 추가').props.disabled, false);
  await f.click('회전 박스 맞추기'); f.requests.at(-1).reject(new Error('Fitting source changed after preview'));
  await f.settle(); noProposal(f); assert.match(f.text(), /Fitting source changed after preview/);
});

test('a current original refusal is shown without creating a business row', async t => {
  const f = fixture(t), pending = await f.start(); pending.reject(new Error('Current source cannot be decoded'));
  await f.settle(); noProposal(f); assert.match(f.text(), /Current source cannot be decoded/);
  assert.equal(f.button('회전 박스 맞추기').props.disabled, false);
});

test('unmount discards a pending original fit without updating removed state', async t => {
  const f = fixture(t), pending = await f.start(); f.unmount(); pending.resolve(FIT); await f.settle();
  assert.equal(f.postUnmountUpdates(), 0); assert.deepEqual(f.appends, []);
});
