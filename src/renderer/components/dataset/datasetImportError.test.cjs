const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');

const FALLBACK = '요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.';

// Execute the real panel's API rejection -> state -> alert path. Only React's
// hook scheduler and the API boundary are replaced; the panel/formatter run as shipped.
function panelHarness(failure) {
  let cursor = 0;
  const slots = [], effects = [];
  const react = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat() }),
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = initial;
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
    },
    useRef(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = { current: initial };
      return slots[i];
    },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || deps.some((d, j) => d !== slots[i].deps[j])) {
        slots[i]?.cleanup?.();
        slots[i] = { deps };
        effects.push(() => { slots[i].cleanup = fn(); });
      }
    },
  };
  function load(filename) {
    const m = new Module(filename, module);
    m.filename = filename;
    m.paths = Module._nodeModulePaths(path.dirname(filename));
    m.require = ref => {
      if (ref === 'react') return react;
      if (ref === '../../services/api') return { api: { datasetImports: { revisions: async () => { throw failure; } } } };
      if (ref === '../../services/archiveUpload') return {};
      if (ref.startsWith('.')) return load(path.resolve(path.dirname(filename), `${ref}.ts`));
      return Module.prototype.require.call(m, ref);
    };
    m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022, esModuleInterop: true },
    }).outputText, filename);
    return m.exports;
  }
  const panel = load(path.join(__dirname, 'DatasetImportPanel.tsx')).DatasetImportPanel;
  const fakeWindow = { localStorage: { getItem: () => null }, addEventListener() {}, removeEventListener() {} };
  function render() {
    const previous = global.window;
    global.window = fakeWindow;
    try {
      cursor = 0;
      const tree = panel({ projectId: 'p', datasetPath: 'source', registeredSource: 'source',
        task: 'detection', onClose() {}, onAccepted() {} });
      effects.splice(0).forEach(fn => fn());
      return tree;
    } finally { global.window = previous; }
  }
  function alerts(node, found = []) {
    if (node && typeof node === 'object') {
      if (node.props?.role === 'alert') found.push(node.children.join(''));
      node.children?.forEach(child => alerts(child, found));
    }
    return found;
  }
  return { render, alerts };
}

for (const [name, failure, expected] of [
  ['empty validation list', [], FALLBACK],
  ['null rejection', null, FALLBACK],
  ['empty Error', new Error(''), FALLBACK],
  ['undefined rejection', undefined, FALLBACK],
  ['whitespace detail', '   ', FALLBACK],
  ['empty catalog detail', { message_ko: '', message: null, code: 'IMPORT_FAILED', status: 500 }, FALLBACK],
  ['blank Korean field with useful English', { message_ko: ' ', message: 'Source folder is unavailable.' }, 'Source folder is unavailable.'],
  ['mixed validation list', [null, {}, { msg: '파일 형식을 확인하세요.' }, []], '파일 형식을 확인하세요.'],
  ['nested server detail', { detail: [{ msg: '경로를 확인하세요.' }] }, '경로를 확인하세요.'],
  ['validation list', [{ msg: '원본 폴더가 없습니다.' }, { msg: '파일 형식을 확인하세요.' }], '원본 폴더가 없습니다. · 파일 형식을 확인하세요.'],
  ['Korean catalog error', { message_ko: '이미지가 손상되었습니다.', message: 'Image is corrupt', code: 'CORRUPT' }, '이미지가 손상되었습니다.'],
]) {
  test(`import panel displays a useful alert for ${name}`, async () => {
    const h = panelHarness(failure);
    h.render();
    await new Promise(setImmediate);
    assert.deepEqual(h.alerts(h.render()), [expected]);
  });
}

test('a circular error detail cannot replace the original failure with a rendering exception', async () => {
  const failure = {};
  failure.detail = failure;
  const h = panelHarness(failure);
  h.render();
  await new Promise(setImmediate);
  assert.deepEqual(h.alerts(h.render()), [FALLBACK]);
});
