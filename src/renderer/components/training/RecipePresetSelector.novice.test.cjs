const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');
function render(isTraining = false) {
  const changes = [], React = {createElement: (type, props, ...children) => ({type, props: {...props, children}}), useState: value => [value, () => {}]}; React.default = React;
  const file = path.join(__dirname, 'RecipePresetSelector.tsx'), m = new Module(file, module);
  m.require = name => name === 'react' ? React : name.endsWith('/useProjectStore') ? {useProjectStore: selector => selector({viewMode: 'simple'})} : new Proxy({}, {get: (_, key) => key});
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true}}).outputText, file);
  const tree = m.exports.RecipePresetSelector({preset: 'fast', setPreset: value => changes.push(value), isTraining, language: 'ko'});
  const nodes = value => Array.isArray(value) ? value.flatMap(nodes) : value && typeof value === 'object' ? [value, ...nodes(value.props.children)] : [];
  return {changes, cards: nodes(tree).filter(node => node.props['aria-pressed'] !== undefined)};
}
test('preset choices are native buttons and training disables switching recipes', () => {
  const idle = render(); assert.equal(idle.cards.length, 2); assert.ok(idle.cards.every(node => node.type === 'button'));
  idle.cards[1].props.onClick(); assert.deepEqual(idle.changes, ['precision']);
  assert.deepEqual(idle.cards.map(node => node.props['aria-pressed']), [true, false]);
  const running = render(true); assert.ok(running.cards.every(node => node.props.disabled));
  running.cards[1].props.onClick(); assert.deepEqual(running.changes, []);
});
