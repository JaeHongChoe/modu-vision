const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
const nodes = value => Array.isArray(value) ? value.flatMap(nodes)
  : value && typeof value === 'object' ? [value, ...nodes(value.props?.children)] : [];
const text = value => Array.isArray(value) ? value.map(text).join('')
  : value && typeof value === 'object' ? text(value.props?.children) : String(value ?? '');

// The repository's hook harness renders the actual component; only external project/host state is isolated.
function mount(t, language = 'ko') {
  t.mock.method(console, 'error', () => {});
  const slots = [], effects = [], listeners = new Set();
  let cursor = 0, effectCursor = 0, pending = [], tree, mounted = true, generation = 1;
  const state = { language, project: { id: 'project-a', source_dataset_dir: '/scratch/source-a', active_labelset_id: 'default' }, projectDir: '/scratch/a', task: 'detection' };
  const calls = [];
  const run = () => { const result = deferred(); calls.push(result); return result.promise; };
  let props = { title: '사진이 없습니다', description: '사진을 먼저 준비하세요.', stepContext: '1단계',
    actions: [{ label: '합성 데이터 생성', onClick: run }, { label: '폴더 선택', onClick: run }] };
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], next => {
        assert.ok(mounted, 'an unmounted banner must not publish state');
        slots[i] = typeof next === 'function' ? next(slots[i]) : next;
      }];
    },
    useRef(initial) { const i = cursor++; return slots[i] ?? (slots[i] = { current: initial }); },
    useEffect(fn, deps) {
      const i = effectCursor++, previous = effects[i];
      if (!previous || !deps || deps.some((v, j) => !Object.is(v, previous.deps[j]))) {
        pending.push(() => { previous?.cleanup?.(); effects[i] = { deps, cleanup: fn() }; });
      }
    },
    useSyncExternalStore(_subscribe, snapshot) { return snapshot(); },
  };
  const store = Object.assign(selector => selector ? selector(state) : state, { getState: () => state });
  const file = path.join(__dirname, 'GuardrailBanner.tsx'), module = new Module(file);
  module.filename = file; module.paths = Module._nodeModulePaths(__dirname);
  const original = module.require.bind(module);
  const mocks = {
    react: { __esModule: true, default: react, ...react },
    'lucide-react': new Proxy({}, { get: () => 'icon' }),
    '../../stores/useProjectStore': { useProjectStore: store },
    '../../services/hostAdapter': { hostErrorMessage: (error, fallback) => error instanceof Error && error.message ? error.message : fallback },
    '../../services/api': {
      getProjectContextGeneration: () => generation,
      subscribeProjectContext: listener => { listeners.add(listener); return () => listeners.delete(listener); },
    },
  };
  module.require = name => name in mocks ? mocks[name] : original(name);
  module._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true,
  } }).outputText, file);
  const render = () => {
    cursor = 0; effectCursor = 0; pending = [];
    tree = module.exports.GuardrailBanner(props);
    for (const effect of pending) effect();
    return tree;
  };
  render();
  const x = {
    calls, render, state,
    buttons: () => nodes(tree).filter(n => n.type === 'button'),
    alerts: () => nodes(tree).filter(n => n.props?.role === 'alert').map(text),
    changeBanner() { props = { ...props, title: '새 안내', actions: [{ label: '새 작업', onClick: run }] }; render(); },
    refreshCallbacks() { props = { ...props, actions: props.actions.map(action => ({ ...action, onClick: () => run() })) }; render(); },
    changeProject() { state.project = { id: 'project-b' }; state.projectDir = '/scratch/b'; render(); },
    changeSource(renderNow = true) { state.project = { ...state.project, source_dataset_dir: '/scratch/source-b' }; if (renderNow) render(); },
    changeLabelset(renderNow = true) { state.project = { ...state.project, active_labelset_id: 'labelset-b' }; if (renderNow) render(); },
    changeTask() { state.task = 'segmentation'; render(); },
    advanceAuthority(renderNow = true) { generation++; for (const listener of listeners) listener(); if (renderNow) render(); },
    unmount() { for (const effect of effects) effect?.cleanup?.(); mounted = false; },
  };
  t.after(() => { if (mounted) x.unmount(); });
  return x;
}
const flush = () => new Promise(setImmediate);

// Removing the catch's visible feedback reproduces the live silent rejection.
test('a rejected action explains local-host approval in Korean without retrying', async t => {
  const x = mount(t);
  x.buttons()[0].props.onClick();
  x.calls[0].reject(Object.assign(new Error('Approve this web account in the local host dialog first'), { status: 403 }));
  await flush(); x.render();
  assert.equal(x.alerts().length, 1);
  assert.match(x.alerts()[0], /로컬 보조 프로그램.*승인/);
  assert.ok(!x.alerts()[0].includes('Approve this web account'));
  assert.ok(x.buttons().every(button => !button.props.disabled));
  assert.equal(x.calls.length, 1);
});

test('a service Error remains inspectable as text and an unknown detail uses a localized fallback', async t => {
  for (const error of [new Error('원본 폴더를 읽을 수 없습니다. <script>'), { code: 'fixture_private_detail', secret: 'fixture-secret' }]) {
    const x = mount(t);
    x.buttons()[0].props.onClick(); x.calls[0].reject(error); await flush(); x.render();
    assert.equal(x.alerts().length, 1);
    if (error instanceof Error) assert.ok(x.alerts()[0].includes(error.message));
    else { assert.match(x.alerts()[0], /완료하지 못|실패/); assert.ok(!x.alerts()[0].includes('fixture-secret')); }
    assert.ok(!nodes(x.render()).some(n => n.type === 'script' || n.props?.dangerouslySetInnerHTML));
  }
});

test('English UI uses English failure and approval guidance', async t => {
  const x = mount(t, 'en'); x.buttons()[0].props.onClick();
  x.calls[0].reject(new Error('Approve this web account in the local host dialog first'));
  await flush(); x.render();
  assert.match(x.alerts()[0] || '', /local host dialog/);
  assert.ok(!/[가-힣]/.test(x.alerts()[0] || ''));
});

// A missing synchronous lock dispatches twice before React has rendered the disabled state.
test('an explicit retry clears the prior error, disables all buttons and never double-dispatches', async t => {
  const x = mount(t); x.buttons()[0].props.onClick(); x.calls[0].reject(Error('첫 요청 실패'));
  await flush(); x.render(); assert.equal(x.alerts().length, 1);
  const retry = x.buttons()[1].props.onClick;
  retry(); retry(); x.render();
  assert.equal(x.calls.length, 2); assert.deepEqual(x.alerts(), []);
  assert.ok(x.buttons().every(button => button.props.disabled));
  x.calls[1].resolve(); await flush(); x.render();
  assert.deepEqual(x.alerts(), []); assert.ok(x.buttons().every(button => !button.props.disabled));
});

test('ordinary parent callback recreation preserves pending state and its eventual error', async t => {
  const x = mount(t); x.buttons()[0].props.onClick(); x.refreshCallbacks();
  assert.ok(x.buttons().every(button => button.props.disabled));
  x.calls[0].reject(Error('현재 요청 실패')); await flush(); x.render();
  assert.ok(x.alerts()[0].includes('현재 요청 실패'));
});

// Removing any ownership fence allows the old rejection/finally to contaminate the replacement banner.
for (const change of ['changeBanner', 'changeProject', 'changeTask', 'advanceAuthority', 'changeSource', 'changeLabelset']) {
  for (const outcome of ['resolve', 'reject']) test(`late ${outcome} after ${change} cannot overwrite a newer pending action`, async t => {
    const x = mount(t); const oldClick = x.buttons()[0].props.onClick; oldClick(); x[change]();
    assert.deepEqual(x.alerts(), []); assert.ok(x.buttons().every(button => !button.props.disabled));
    oldClick(); assert.equal(x.calls.length, 1, 'a retained callback cannot dispatch in the replacement context');
    x.buttons()[0].props.onClick(); x.render();
    if (outcome === 'resolve') x.calls[0].resolve(); else x.calls[0].reject(Error('오래된 오류'));
    await flush(); x.render();
    assert.deepEqual(x.alerts(), []); assert.ok(x.buttons().every(button => button.props.disabled));
    x.calls[1].resolve(); await flush(); x.render();
    assert.ok(x.buttons().every(button => !button.props.disabled));
  });
  test(`a displayed error clears after ${change}`, async t => {
    const x = mount(t); x.buttons()[0].props.onClick(); x.calls[0].reject(Error('지난 안내 오류'));
    await flush(); x.render(); assert.equal(x.alerts().length, 1);
    x[change](); assert.deepEqual(x.alerts(), []);
  });
}

test('authority invalidation before the next render cannot publish an old error', async t => {
  const x = mount(t); x.buttons()[0].props.onClick(); x.advanceAuthority(false);
  x.calls[0].reject(Error('오래된 권한 오류')); await flush(); x.render();
  assert.deepEqual(x.alerts(), []);
});

for (const change of ['changeSource', 'changeLabelset']) test(`${change} immediately refuses the old action before the next render`, async t => {
  const x = mount(t); x.buttons()[0].props.onClick(); x.calls[0].resolve(); await flush(); x.render();
  const oldClick = x.buttons()[0].props.onClick;
  x[change](false); oldClick();
  assert.equal(x.calls.length, 1, 'a source or labelset switch must invalidate the captured action immediately');
});

for (const outcome of ['resolve', 'reject']) test(`a late ${outcome} after unmount publishes no state`, async t => {
  const x = mount(t); const click = x.buttons()[0].props.onClick; click(); x.unmount();
  click(); assert.equal(x.calls.length, 1);
  if (outcome === 'resolve') x.calls[0].resolve(); else x.calls[0].reject(Error('지난 화면 오류'));
  await flush();
});
