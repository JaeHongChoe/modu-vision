const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

function load(filename, mocks = {}) {
  const m = new Module(filename, module);
  m.filename = filename;
  m.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = m.require.bind(m);
  m.require = name => Object.hasOwn(mocks, name) ? mocks[name] : original(name);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText, filename);
  return m.exports;
}
const services = path.resolve(__dirname, '../../services');
const hostModule = load(path.join(services, 'hostAdapter.ts'), {
  './browserSession': load(path.join(services, 'browserSession.ts')),
});
const componentFile = path.join(__dirname, 'BrowserFolderPathInput.tsx');
const component = load(componentFile, { '../../services/hostAdapter': hostModule });
function withWindow(value, run) {
  const previous = global.window;
  global.window = value;
  return Promise.resolve().then(run).finally(() => { global.window = previous; });
}
const render = props => renderToStaticMarkup(React.createElement(component.BrowserFolderPathInput, { onImport: async () => {}, ...props }));

// Missing description, a disconnected id, hidden/placeholder-only help, or inherited nowrap breaks this contract.
test('a browser renders persistent readable-backend-path help as the input description, including while disabled', () => withWindow({}, () => {
  for (const disabled of [false, true]) {
    const html = render({ disabled });
    const input = html.match(/<input[^>]*>/)?.[0];
    const description = input?.match(/aria-describedby="([^"]+)"/)?.[1];
    assert.ok(description, 'the path input must describe its capability limitation');
    const help = html.match(new RegExp('<p[^>]*id="' + description + '"[^>]*>(.*?)</p>'));
    assert.ok(help, 'the input description points to visible help beside the control');
    assert.match(help[1], /브라우저.*폴더.*선택.*창/);
    assert.match(help[1], /백엔드.*읽을 수 있는.*경로/);
    assert.match(help[1], /공유/);
    assert.match(help[0], /whitespace-normal/);
    assert.doesNotMatch(help[0], /hidden|truncate|whitespace-nowrap/);
    assert.match(html, /flex-wrap/);
    assert.equal(/disabled=""/.test(input), disabled);
  }
}));

test('description ids stay independent when more than one browser path input is rendered', () => withWindow({}, () => {
  const html = renderToStaticMarkup(React.createElement(React.Fragment, null,
    React.createElement(component.BrowserFolderPathInput, { onImport: async () => {} }),
    React.createElement(component.BrowserFolderPathInput, { onImport: async () => {} })));
  const descriptions = [...html.matchAll(/aria-describedby="([^"]+)"/g)].map(match => match[1]);
  assert.equal(descriptions.length, 2);
  assert.equal(new Set(descriptions).size, 2);
}));

test('a desktop host never renders the browser input or help and retains its native folder picker', () => withWindow({ api: {
  selectFolder: async () => '/native/folder', selectFile: async () => '/native/file',
} }, async () => {
  assert.equal(render(), '');
  assert.equal(hostModule.host.kind(), 'desktop');
  assert.equal(await hostModule.host.selectFolder(), '/native/folder');
}));

// Real component handlers with only React state scheduling controlled; no DOM/native process is available here.
test('path submit still trims input, ignores blanks and busy repeats, then releases after the import', () => withWindow({}, async () => {
  const slots = []; let cursor = 0, calls = [], release;
  const pending = new Promise(resolve => { release = resolve; });
  const hooks = { useId: () => ':controlled:', useState(initial) {
    const index = cursor++;
    if (!(index in slots)) slots[index] = initial;
    return [slots[index], value => { slots[index] = value; }];
  } };
  const { BrowserFolderPathInput } = load(componentFile, { react: hooks, '../../services/hostAdapter': hostModule });
  const tree = () => { cursor = 0; return BrowserFolderPathInput({ onImport: async folder => { calls.push(folder); await pending; } }); };
  const nodes = node => Array.isArray(node) ? node.flatMap(nodes) : node && typeof node === 'object' ? [node, ...nodes(node.props?.children)] : [];
  const input = root => nodes(root).find(node => node.type === 'input');
  const button = root => nodes(root).find(node => node.type === 'button');
  const submit = () => tree().props.onSubmit({ preventDefault() {} });
  assert.equal(button(tree()).props.disabled, true);
  input(tree()).props.onChange({ target: { value: '   ' } }); await submit(); assert.deepEqual(calls, []);
  input(tree()).props.onChange({ target: { value: '  /backend/inspection  ' } });
  const request = submit(); assert.deepEqual(calls, ['/backend/inspection']);
  assert.equal(input(tree()).props.disabled, true); assert.equal(button(tree()).props.disabled, true);
  await submit(); assert.deepEqual(calls, ['/backend/inspection']);
  release(); await request; assert.equal(input(tree()).props.disabled, false);
}));
