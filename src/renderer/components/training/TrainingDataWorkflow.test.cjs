const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const nodes = value => Array.isArray(value) ? value.flatMap(nodes)
  : value && typeof value === 'object' ? [value, ...nodes(value.props?.children)] : [];
const text = value => Array.isArray(value) ? value.map(text).join('')
  : value && typeof value === 'object' ? text(value.props?.children) : String(value ?? '');
const block = (id, status = 'ready', content = null) => ({ id, title: `${id} 설정`, summary: `${id} 준비 근거`, status, content });
let instance = 0;

// Render the real component with the repository's lightweight hook harness.
// A missing implementation renders nothing, so RED reports the absent UI contract.
function mount(initialBlocks, scope = 'project-a') {
  const slots = [], effects = [], focus = [], scroll = [];
  let cursor = 0, effectCursor = 0, pending = [], dirty = false, tree;
  let props = { scope, blocks: initialBlocks };
  const prefix = `workflow-${++instance}`;
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], next => {
        const value = typeof next === 'function' ? next(slots[i]) : next;
        if (!Object.is(value, slots[i])) { slots[i] = value; dirty = true; }
      }];
    },
    useRef(initial) { const i = cursor++; return slots[i] ?? (slots[i] = { current: initial }); },
    useId() { const i = cursor++; return slots[i] ?? (slots[i] = `${prefix}-${i}`); },
    useEffect(fn, deps) {
      const i = effectCursor++, previous = effects[i];
      if (!previous || !deps || deps.some((value, j) => !Object.is(value, previous.deps[j]))) {
        pending.push(() => { previous?.cleanup?.(); effects[i] = { deps, cleanup: fn() }; });
      }
    },
  };
  const file = path.join(__dirname, 'TrainingDataWorkflow.tsx');
  let Workflow = () => null;
  if (fs.existsSync(file)) {
    const m = new Module(file, module); m.filename = file; m.paths = Module._nodeModulePaths(__dirname);
    const original = m.require.bind(m);
    m.require = name => name === 'react' ? { __esModule: true, default: react, ...react }
      : name.endsWith('.css') ? {} : original(name);
    m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true,
    } }).outputText, file);
    Workflow = m.exports.TrainingDataWorkflow;
  }
  function render() {
    let turns = 0;
    do {
      if (++turns > 10) assert.fail('selection effects do not settle');
      cursor = 0; effectCursor = 0; pending = []; dirty = false;
      tree = Workflow(props);
      for (const node of nodes(tree)) if (node.props?.role === 'tab' && typeof node.props.ref === 'function') {
        node.props.ref({ focus: () => focus.push(node.props.id), scrollIntoView: () => scroll.push(node.props.id) });
      }
      for (const effect of pending) effect();
    } while (dirty);
    return tree;
  }
  render();
  return {
    render, focus, scroll, tree: () => tree,
    tabs: () => nodes(tree).filter(node => node.props?.role === 'tab'),
    panels: () => nodes(tree).filter(node => node.props?.role === 'tabpanel'),
    selected() { return this.tabs().find(tab => tab.props['aria-selected']); },
    change(next) { props = { ...props, ...next }; render(); },
    click(index) { this.tabs()[index].props.onClick(); render(); },
    key(index, key) {
      let prevented = false;
      this.tabs()[index].props.onKeyDown({ key, preventDefault() { prevented = true; } });
      render(); return prevented;
    },
  };
}

// Missing priority or selecting the last problem instead would show the wrong first task.
test('initial selection opens the first attention/error block in displayed order', () => {
  const x = mount([block('source'), block('labels', 'attention'), block('split', 'error')]);
  assert.equal(x.tabs().length, 3);
  assert.match(text(x.selected()), /labels 설정/);
  assert.equal(x.panels().filter(panel => !panel.props.hidden).length, 1);
  const y = mount([block('source', 'optional'), block('model', 'ready')]);
  assert.match(text(y.selected()), /source 설정/);
});

test('all native tabs and panels expose one coherent named ARIA relationship', () => {
  const x = mount([block('source'), block('split', 'attention')]);
  const list = nodes(x.tree()).find(node => node.props?.role === 'tablist');
  assert.ok(list?.props['aria-label']); assert.equal(list.props['aria-orientation'], 'horizontal');
  assert.equal(x.tabs().length, 2);
  x.tabs().forEach((tab, index) => {
    const panel = x.panels()[index];
    assert.equal(tab.type, 'button'); assert.equal(tab.props.type, 'button');
    assert.equal(tab.props['aria-controls'], panel.props.id);
    assert.equal(panel.props['aria-labelledby'], tab.props.id);
    assert.equal(tab.props.tabIndex, tab.props['aria-selected'] ? 0 : -1);
    assert.equal(panel.props.hidden, !tab.props['aria-selected']);
  });
  const y = mount([block('source'), block('split')]);
  assert.ok(!x.tabs().some(tab => y.tabs().some(other => other.props.id === tab.props.id)), 'instances cannot share DOM IDs');
});

// Conditional rendering or index-based keys would lose an editor's uncommitted work.
test('changing blocks only hides panels and keeps each editor mounted with its stable identity', () => {
  const draft = { type: 'input', props: { defaultValue: '작성 중인 경로', 'aria-label': '저장하지 않은 경로' } };
  const x = mount([block('source', 'attention', draft), block('labels')]);
  const before = x.panels().map(panel => ({ id: panel.props.id, key: panel.props.key }));
  x.click(1);
  assert.equal(x.panels().length, 2);
  assert.equal(x.panels()[0].props.hidden, true);
  assert.ok(nodes(x.panels()[0]).includes(draft));
  x.click(0);
  assert.ok(nodes(x.panels()[0]).includes(draft));
  assert.deepEqual(x.panels().map(panel => ({ id: panel.props.id, key: panel.props.key })), before);
});

test('arrow keys wrap and Home/End select and focus their actual destination tab', () => {
  const x = mount([block('source'), block('labels'), block('split')]);
  assert.equal(x.tabs().length, 3);
  for (const [from, key, to] of [[0, 'ArrowRight', 1], [1, 'End', 2], [2, 'ArrowRight', 0], [0, 'ArrowLeft', 2], [2, 'Home', 0]]) {
    assert.equal(x.key(from, key), true);
    assert.equal(x.selected().props.id, x.tabs()[to].props.id);
    assert.equal(x.focus.at(-1), x.tabs()[to].props.id);
  }
  assert.equal(x.key(0, 'Tab'), false);
  assert.equal(x.selected().props.id, x.tabs()[0].props.id);
});

test('ordinary parent status updates retain the chosen block; a new scope resets to its first problem', () => {
  const x = mount([block('source', 'attention'), block('labels'), block('split')]);
  assert.equal(x.tabs().length, 3); x.click(2);
  x.change({ blocks: [block('source', 'ready'), block('labels', 'error'), block('split')] });
  assert.match(text(x.selected()), /split 설정/);
  x.change({ scope: 'project-b' });
  assert.match(text(x.selected()), /labels 설정/);
  x.click(0); x.change({ scope: 'project-a' });
  assert.match(text(x.selected()), /labels 설정/);
});

test('removing the selected block falls back to the current first problem without dropping other panels', () => {
  const x = mount([block('source'), block('labels', 'attention'), block('split')]);
  assert.equal(x.tabs().length, 3); x.click(2);
  x.change({ blocks: [block('source', 'error'), block('labels')] });
  assert.match(text(x.selected()), /source 설정/); assert.equal(x.panels().length, 2);
});

test('actual readiness and blocking reasons remain written in the rail; selection executes no content action', () => {
  let writes = 0;
  const content = { type: 'button', props: { onClick: () => writes++, children: '학습 시작' } };
  const x = mount([
    { ...block('source', 'ready'), summary: '원본 12장 확인' },
    { ...block('labels', 'attention'), summary: '학습 가능한 라벨이 없어 시작할 수 없습니다' },
    block('split', 'optional'), block('preprocess', 'working'), block('model', 'error', content), block('settings', 'pending'),
  ]);
  assert.equal(x.tabs().length, 6);
  const railText = text(nodes(x.tree()).find(node => node.props?.role === 'tablist'));
  for (const label of ['입력 준비됨', '확인 필요', '선택 사항', '진행 중', '오류', '설정 확인']) assert.ok(railText.includes(label), label);
  assert.ok(railText.includes('학습 가능한 라벨이 없어 시작할 수 없습니다'));
  x.click(4); x.key(4, 'Home');
  assert.equal(writes, 0);
});

test('initial and scope-reset selections reveal the chosen block without stealing keyboard focus', () => {
  const x = mount([block('source'), block('labels'), block('model', 'error')]);
  assert.equal(x.tabs().length, 3);
  assert.equal(x.scroll.at(-1), x.selected().props.id);
  assert.deepEqual(x.focus, []);
  x.click(0); x.change({ scope: 'project-b' });
  assert.equal(x.scroll.at(-1), x.selected().props.id);
  assert.match(text(x.selected()), /model 설정/);
});

test('previous and next settings navigation names its destination, preserves editors and never applies work', () => {
  let writes = 0;
  const draft = { type: 'input', props: { defaultValue: '유지할 편집 내용' } };
  const action = { type: 'button', props: { onClick: () => writes++, children: '실제 적용' } };
  const x = mount([block('source', 'ready', draft), block('labels', 'pending', action), block('model')]);
  const currentButtons = () => nodes(x.panels().find(panel => !panel.props.hidden))
    .filter(node => node.type === 'button' && /^이전 설정:|^다음 설정:/.test(text(node)));
  assert.equal(x.tabs().length, 3);
  assert.deepEqual(currentButtons().map(text), ['다음 설정: labels 설정']);
  currentButtons()[0].props.onClick(); x.render();
  assert.deepEqual(currentButtons().map(text), ['이전 설정: source 설정', '다음 설정: model 설정']);
  assert.equal(x.focus.at(-1), x.selected().props.id);
  currentButtons()[1].props.onClick(); x.render();
  assert.deepEqual(currentButtons().map(text), ['이전 설정: labels 설정']);
  currentButtons()[0].props.onClick(); x.render();
  currentButtons()[0].props.onClick(); x.render();
  assert.ok(nodes(x.panels()[0]).includes(draft));
  assert.equal(x.panels().length, 3); assert.equal(writes, 0);
  assert.ok(currentButtons().every(button => button.props.type === 'button'));
});

test('an empty block list has an honest empty state and can receive blocks later', () => {
  const x = mount([]);
  assert.match(text(x.tree()), /준비.*항목.*없/);
  assert.equal(x.tabs().length, 0); assert.equal(x.panels().length, 0);
  x.change({ blocks: [block('source', 'attention')] });
  assert.match(text(x.selected()), /source 설정/);
});
