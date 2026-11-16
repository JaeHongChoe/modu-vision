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
function mount(initialBlocks, scope = 'project-a', width = 1000) {
  const slots = [], effects = [], focus = [], scroll = [];
  const frames = new Map(), elements = new Map();
  let frameId = 0, observer;
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
    const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true,
    } }).outputText;
    new Function('require', 'module', 'exports', 'requestAnimationFrame', 'cancelAnimationFrame', 'ResizeObserver', compiled)(
      m.require.bind(m), m, m.exports,
      callback => { const id = ++frameId; frames.set(id, callback); return id; },
      id => frames.delete(id),
      class { constructor(callback) { observer = callback; } observe() { observer([{ contentRect: { width } }]); } disconnect() { observer = null; } },
    );
    Workflow = m.exports.TrainingDataWorkflow;
  }
  function render() {
    let turns = 0;
    do {
      if (++turns > 10) assert.fail('selection effects do not settle');
      cursor = 0; effectCursor = 0; pending = []; dirty = false;
      tree = Workflow(props);
      function commit(node, parent = null) {
        if (Array.isArray(node)) { node.forEach(child => commit(child, parent)); return; }
        if (!node || typeof node !== 'object') return;
        const key = node.props?.id || node;
        const element = elements.get(key) || { open: false };
        Object.assign(element, { id: node.props?.id || '', tagName: String(node.type).toUpperCase(), parentElement: parent,
          hidden: !!node.props?.hidden, disabled: !!node.props?.disabled, children: [], clientWidth: width,
          getBoundingClientRect: () => ({ width }),
          getAttribute: name => node.props?.[name] ?? null,
          hasAttribute: name => name === 'tabindex' && node.props?.tabIndex !== undefined,
          focus: () => { for(let owner=element;owner;owner=owner.parentElement)if(owner.hidden)return;focus.push(node.props?.id);if(global.document?.getElementById?.(element.id)===element)global.document.activeElement=element; }, scrollIntoView: () => scroll.push(node.props?.id),
          contains(other) { for (let p = other; p; p = p.parentElement) if (p === this) return true; return false; },
          querySelectorAll(selector) { const all = []; const walk = value => { for (const child of value.children) { all.push(child); walk(child); } }; walk(this); return all.filter(child => selector === '[id]' ? child.id : child.tagName === 'DETAILS'); },
          querySelector() { const all = []; const walk = value => { for (const child of value.children) { all.push(child); walk(child); } }; walk(this); return all.find(child => !child.disabled && ['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'A'].includes(child.tagName)) || null; },
        });
        elements.set(key, element); parent?.children.push(element);
        if (typeof node.props?.ref === 'function') node.props.ref(element);
        else if (node.props?.ref) node.props.ref.current = element;
        commit(node.props?.children, element);
      }
      commit(tree);
      for (const effect of pending) effect();
    } while (dirty);
    return tree;
  }
  render();
  return {
    render, focus, scroll, tree: () => tree, element: id => elements.get(id),
    frames() { const current = [...frames.values()]; frames.clear(); current.forEach(callback => callback()); },
    resize(next) { width = next; observer?.([{ contentRect: { width } }]); render(); },
    unmount() { for (const effect of effects) effect?.cleanup?.(); },
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
  assert.ok(list?.props['aria-label']); assert.equal(list.props['aria-orientation'], 'vertical');
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
  for (const [from, key, to] of [[0, 'ArrowDown', 1], [1, 'End', 2], [2, 'ArrowDown', 0], [0, 'ArrowUp', 2], [2, 'Home', 0]]) {
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

// A horizontal ARIA rail with vertical layout would make the announced keys wrong.
test('the available container width switches rail orientation and its arrow keys together', () => {
  const x = mount([block('source'), block('model')]);
  assert.equal(x.key(0, 'ArrowDown'), true);
  x.resize(600);
  assert.equal(nodes(x.tree()).find(node => node.props?.role === 'tablist').props['aria-orientation'], 'horizontal');
  assert.equal(x.key(1, 'ArrowRight'), true);
  assert.equal(x.selected().props.id, x.tabs()[0].props.id);
  assert.equal(x.key(0, 'ArrowDown'), false, 'horizontal rails leave page scrolling keys alone');
  x.resize(1100);
  assert.equal(nodes(x.tree()).find(node => node.props?.role === 'tablist').props['aria-orientation'], 'vertical');
  assert.equal(x.key(0, 'ArrowRight'), false);
});

test('a current recovery request reveals only its owned settings and opens nested disclosures after commit', () => {
  const details = { type: 'details', props: { id: 'settings-disclosure', children: {
    type: 'div', props: { id: 'training-settings', tabIndex: -1, children: { type: 'details', props: { id: 'nested-disclosure' } } },
  } } };
  const x = mount([block('source'), block('model', 'pending', details)]);
  x.change({ revealRequest: { scope: 'project-a', blockId: 'model', targetId: 'training-settings', sequence: 1 } });
  assert.equal(x.panels()[1].props.hidden, false);
  assert.deepEqual(x.focus, [], 'focus waits for the visible commit');
  x.frames();
  assert.equal(x.element('settings-disclosure').open, true);
  assert.equal(x.element('nested-disclosure').open, true);
  assert.equal(x.focus.at(-1), 'training-settings');
  x.click(0); x.change({ revealRequest: { scope: 'project-a', blockId: 'model', targetId: 'training-settings', sequence: 2 } });
  x.frames(); assert.equal(x.focus.at(-1), 'training-settings');
});

test('missing, foreign-panel and foreign-scope recovery targets cannot change selection or focus', () => {
  const target = { type: 'input', props: { id: 'model-setting' } };
  const x = mount([block('source'), block('model', 'pending', target)]);
  for (const [scope, blockId, targetId] of [['project-a', 'model', 'missing'], ['project-a', 'source', 'model-setting'], ['project-b', 'model', 'model-setting']]) {
    x.change({ revealRequest: { scope, blockId, targetId, sequence: 1 } }); x.frames();
    assert.equal(x.selected().props.id, x.tabs()[0].props.id); assert.deepEqual(x.focus, []);
  }
});

test('a pending recovery cannot steal focus after navigation, a scope change or unmount', () => {
  const make = () => mount([block('source'), block('model', 'pending', { type: 'input', props: { id: 'model-setting' } })]);
  const request = { scope: 'project-a', blockId: 'model', targetId: 'model-setting', sequence: 1 };
  const nav = make(); nav.change({ revealRequest: request }); nav.click(0); nav.frames();
  assert.equal(nav.selected().props.id, nav.tabs()[0].props.id); assert.deepEqual(nav.focus, []);
  nav.change({ revealRequest: { ...request } }); nav.frames();
  assert.equal(nav.selected().props.id, nav.tabs()[0].props.id, 'rerendering the same request cannot override navigation');
  const scope = make(); scope.change({ revealRequest: request }); scope.change({ scope: 'project-b' }); scope.frames();
  assert.deepEqual(scope.focus, []); assert.equal(scope.selected().props.id, scope.tabs()[0].props.id);
  scope.change({ scope: 'project-a' }); scope.frames();
  assert.deepEqual(scope.focus, [], 'returning to an earlier scope cannot revive its retired request');
  assert.equal(scope.selected().props.id, scope.tabs()[0].props.id);
  const filtered = make(); filtered.change({ revealRequest: request });
  filtered.change({ scope: 'project-b', revealRequest: null });
  filtered.change({ scope: 'project-a', revealRequest: request }); filtered.frames();
  assert.deepEqual(filtered.focus, [], 'parent filtering a stale request to null cannot make it fresh on return');
  assert.equal(filtered.selected().props.id, filtered.tabs()[0].props.id);
  const gone = make(); gone.change({ revealRequest: request }); gone.unmount(); gone.frames(); assert.deepEqual(gone.focus, []);
});

function loadFocusRouter() {
  const file=path.resolve(__dirname,'../wizard/workflowReadiness.ts'),m=new Module(file,module);
  m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
  return m.exports;
}

test('an explicit workflow target reveals its real hidden React panel before focus', () => {
  const x=mount([block('photos'),block('model','pending',{type:'details',props:{id:'advanced',children:{type:'input',props:{id:'training-next-settings'}}}})]);
  const router=loadFocusRouter(),frames=[];const previous={document:global.document,requestAnimationFrame:global.requestAnimationFrame,HTMLElement:global.HTMLElement};
  let current=true;const doc={getElementById:id=>x.element(id),querySelector:()=>null,activeElement:null};
  const target=x.element('training-next-settings'),focus=target.focus;
  target.focus=()=>{let p=target;while(p){if(p.hidden)return;p=p.parentElement;}doc.activeElement=target;focus();};
  global.document=doc;global.HTMLElement=class {};global.requestAnimationFrame=fn=>frames.push(fn);
  const dispose=router.registerWorkflowTargetOwner?.({handles:id=>id==='training-next-settings',isCurrent:()=>current,
    reveal:(targetId,isCurrent)=>x.change({revealRequest:{scope:'project-a',blockId:'model',targetId,sequence:1,isCurrent}})});
  try{
    router.focusWorkflowTarget('training-next-settings',()=>current);
    frames.splice(0).forEach(fn=>fn());
    assert.equal(x.panels()[1].props.hidden,false,'the owning tab must be selected, not a focus attempt on hidden DOM');
    x.frames();assert.equal(x.selected().props['aria-selected'],true);assert.equal(x.element('advanced').open,true);
    assert.equal(doc.activeElement,target);assert.equal(x.focus.at(-1),'training-next-settings');
  }finally{dispose?.();x.unmount();Object.assign(global,previous);}
});

test('live authority is checked before reveal and again before the committed focus',()=>{
  let current=false;
  const x=mount([block('photos'),block('model','pending',{type:'input',props:{id:'training-next-settings'}})]);
  x.change({revealRequest:{scope:'project-a',blockId:'model',targetId:'training-next-settings',sequence:1,isCurrent:()=>current}});
  x.frames();assert.equal(x.panels()[1].props.hidden,true);assert.deepEqual(x.focus,[]);
  current=true;x.change({revealRequest:{scope:'project-a',blockId:'model',targetId:'training-next-settings',sequence:2,isCurrent:()=>current}});
  current=false;x.frames();assert.deepEqual(x.focus,[],'actor/source can change before React commits a replacement scope');
});

test('owner replacement, same-object ABA and disposal retire pending visual requests',()=>{
 const router=loadFocusRouter(),previous=global.document;global.document={};
 let pending;const owner={handles:()=>true,isCurrent:()=>true,reveal:(_id,current)=>{pending=current;}};
 try{
  let dispose=router.registerWorkflowTargetOwner(owner);router.focusWorkflowTarget('training-next-settings',()=>true);assert.equal(pending(),true);
  const other=router.registerWorkflowTargetOwner({...owner});assert.equal(pending(),false);
  dispose();assert.equal(pending(),false,'an old cleanup cannot unregister a newer owner');
  const aba=router.registerWorkflowTargetOwner(owner);assert.equal(pending(),false,'same object and scope do not revive an old registration');
  router.focusWorkflowTarget('training-next-settings',()=>true);assert.equal(pending(),true);aba();assert.equal(pending(),false);other();
 }finally{global.document=previous;}
});

test('a scoped request waiting for mount cannot transfer after its caller or first owner changes',()=>{
 const router=loadFocusRouter(),frames=[],previous={document:global.document,requestAnimationFrame:global.requestAnimationFrame};
 global.document={getElementById:()=>null,querySelector:()=>null};global.requestAnimationFrame=fn=>frames.push(fn);
 let current=true,reveals=0;const owner={handles:()=>true,isCurrent:()=>true,reveal:()=>reveals++};
 try{
  router.focusWorkflowTarget('training-next-settings',()=>current);current=false;
  const dispose=router.registerWorkflowTargetOwner(owner);frames.splice(0).forEach(fn=>fn());assert.equal(reveals,0);dispose();
  current=true;router.focusWorkflowTarget('training-next-settings',()=>current);
  const first=router.registerWorkflowTargetOwner(owner);first();const second=router.registerWorkflowTargetOwner(owner);
  frames.splice(0).forEach(fn=>fn());assert.equal(reveals,0);second();
 }finally{Object.assign(global,previous);}
});

test('an old cleanup cannot clear a newer same-object owner registration',()=>{
 const router=loadFocusRouter(),previous={document:global.document,requestAnimationFrame:global.requestAnimationFrame};
 global.document={getElementById:()=>null,querySelector:()=>null};global.requestAnimationFrame=()=>{};
 let reveals=0;const owner={handles:()=>true,isCurrent:()=>true,reveal:()=>reveals++};
 try{
  const old=router.registerWorkflowTargetOwner(owner);const middle=router.registerWorkflowTargetOwner({...owner});
  const latest=router.registerWorkflowTargetOwner(owner);old();router.focusWorkflowTarget('training-next-settings',()=>true);
  assert.equal(reveals,1,'old A cleanup must not unregister new A after A→B→A');latest();middle();
 }finally{Object.assign(global,previous);}
});
