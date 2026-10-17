const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');
const tick = () => new Promise(setImmediate);
const elements = tree => Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === 'object'
  ? [tree, ...elements(tree.props?.children)] : [];
const text = tree => Array.isArray(tree) ? tree.map(text).join(' ') : tree && typeof tree === 'object'
  ? text(tree.props?.children) : String(tree ?? '');
const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); promise.catch(() => {}); return {promise, resolve, reject};};
const A = 'a'.repeat(32), B = 'b'.repeat(32);
function pipeline({type = 'inspection', task = 'segmentation', operation, bound = false} = {}) {
  return {id: 'saved-display-flow', name: 'Saved editable flow', nodes: [
    {id: 'input', position: {x: 0, y: 0}, data: {node_type: 'input', label: 'Input'}},
    {id: 'model', position: {x: 200, y: 0}, data: {node_type: type, task, label: 'Required model node',
      model_job_id: bound ? 'job_completed' : null, ...(operation ? {params: {operation}} : {})}},
    {id: 'decision', position: {x: 400, y: 0}, data: {node_type: 'decision', label: 'Decision', rule: 'any_defect_is_ng'}},
    {id: 'output', position: {x: 600, y: 0}, data: {node_type: 'output', label: 'Output'}},
  ], edges: [{id: 'first', source: 'input', target: 'model', payload_type: 'image'},
    {id: 'second', source: 'model', target: 'decision', payload_type: 'result'},
    {id: 'third', source: 'decision', target: 'output', payload_type: 'result'}]};
}
function fixture(config = {}) {
  let cursor = 0, dirty = true, tree;
  const slots = [], effects = [], calls = [];
  const props = {sourceDatasetPath: '/source', savedVersionId: A, contextKey: 'project-A', embedded: true, visible: true};
  const scopes = {[A]: {project_id: 'project', source_dataset_path: '/source', labelset_id: 'default', task: 'segmentation',
    classes: ['background', 'defect'], class_semantics: {version: 1, roles: {background: 'normal', defect: 'defect'}, basis: {background: 'explicit', defect: 'explicit'}}}};
  scopes[B] = {...scopes[A], task: 'classification'};
  const react = {
    useState(initial) {const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], value => {slots[i] = typeof value === 'function' ? value(slots[i]) : value; dirty = true;}];},
    useRef(initial) {const i = cursor++; return slots[i] ?? (slots[i] = {current: initial});},
    useEffect(fn, deps) {const i = cursor++, old = slots[i];
      if (!old || deps.some((value, index) => !Object.is(value, old.deps[index]))) {
        slots[i] = {deps}; effects.push(() => {old?.cleanup?.(); slots[i].cleanup = fn();});
      }},
  };
  const graph = config.graph || pipeline();
  const api = {flowchart: {getPipelineVersion: async id => {calls.push(['pipeline', id]);
    if (config.readGraph) return config.readGraph(id);
    if (config.graphError) throw config.graphError;
    return id === B ? pipeline({bound: true, task: 'classification'}) : graph;
  }}};
  const flowEvaluation = {
    versions: async () => ({pipelines: [A, B].map(version_id => ({version_id, name: version_id, saved_at: '2000-01-01', is_active: version_id === A}))}),
    cohorts: async () => ({cohorts: []}),
    history: async () => ({evaluations: [{evaluation_id: 'evaluation_saved', created_at: '2000-01-01', coverage: {total: 2}, validity: {valid: false}}]}),
    scope: async id => {calls.push(['scope', id]); if (config.readScope) return config.readScope(id); if (config.scopeError) throw config.scopeError; return scopes[id];},
  };
  const service = {flowEvaluation, formatFlowMetric: () => 'unavailable', isCompatibleFlowCohort: () => false};
  const jsx = (type, props) => ({type, props});
  function load(file) {
    const m = new Module(file, module); m.filename = file; m.paths = Module._nodeModulePaths(path.dirname(file));
    const original = m.require.bind(m);
    m.require = key => key === 'react' ? react : key === 'react/jsx-runtime' ? {jsx, jsxs: jsx}
      : key.endsWith('/services/flowEvaluation') ? service
      : key.endsWith('/services/api') ? {api}
      : key.endsWith('/services/datasetWorkflow') ? {datasetWorkflow: {list: async () => ({items: [], total: 0})}, workflowError: error => error.message}
      : key === './WholeFlowApprovalPanel' ? {WholeFlowApprovalPanel: () => null}
      : key.startsWith('.') && fs.existsSync(path.resolve(path.dirname(file), key) + '.ts') ? load(path.resolve(path.dirname(file), key) + '.ts') : original(key);
    m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    }}).outputText, file);
    return m.exports;
  }
  const {FlowEvaluationPanel} = load(path.join(__dirname, 'FlowEvaluationPanel.tsx'));
  const render = () => {cursor = 0; dirty = false; tree = FlowEvaluationPanel(props); effects.splice(0).forEach(fn => fn()); return tree;};
  return {calls, props, scopes, render, tree: () => tree,
    async settle() {for (let i = 0; i < 25; i++) {if (dirty) render(); await tick(); if (!dirty && !effects.length) return tree;} throw Error('Panel did not settle');},
    change(id) {elements(tree).find(node => node.props?.['aria-label'] === '전체 흐름 평가 버전').props.onChange({target: {value: id}});},
    context(value) {props.contextKey = value; dirty = true;},
    close() {slots.forEach(slot => slot?.cleanup?.());},
  };
}
for (const graph of [pipeline(), pipeline({type: 'detection_crop', task: 'detection'}),
  pipeline({type: 'preprocess', operation: 'enhancement'}), pipeline({type: 'preprocess', operation: 'learned_rotation'})]) {
  test(`saved unbound ${graph.nodes[1].data.node_type} ${graph.nodes[1].data.params?.operation || ''} shows the model prerequisite without requesting executable scope`, async () => {
    const f = fixture({graph}); try {
      await f.settle();
      assert.deepEqual(f.calls, [['pipeline', A]]);
      assert.match(text(f.tree()), /Required model node.*완료 모델.*연결/);
      const nodes = elements(f.tree());
      assert.equal(nodes.find(node => node.props?.['aria-label'] === '전체 흐름 시험 코호트').props.disabled, true);
      assert.ok(nodes.some(node => node.type === 'option' && node.props.value === 'evaluation_saved'), 'existing saved history stays offered');
      assert.ok(!nodes.some(node => node.props?.role === 'alert'), 'a missing binding is a prerequisite, not a failed HTTP request');
    } finally {f.close();}
  });
}
test('a selected fully bound flow reads authoritative scope and clears the old unbound prerequisite', async () => {
  const f = fixture(); try {
    await f.settle(); f.change(B); await f.settle();
    assert.deepEqual(f.calls, [['pipeline', A], ['pipeline', B], ['scope', B]]);
    assert.doesNotMatch(text(f.tree()), /완료 모델.*연결/);
    assert.match(text(f.tree()), /정답 범위:.*classification/);
    assert.equal(elements(f.tree()).find(node => node.props?.['aria-label'] === '전체 흐름 시험 코호트').props.disabled, false);
  } finally {f.close();}
});
for (const message of ['Saved flow belongs to another source or labelset', 'Model checkpoint changed']) {
  test(`bound scope conflict remains authoritative: ${message}`, async () => {
    const f = fixture({graph: pipeline({bound: true}), scopeError: Object.assign(new Error(message), {status: 409})}); try {
      await f.settle();
      assert.deepEqual(f.calls, [['pipeline', A], ['scope', A]]);
      assert.ok(elements(f.tree()).some(node => node.props?.role === 'alert' && text(node).includes(message)));
      assert.equal(elements(f.tree()).find(node => node.props?.['aria-label'] === '전체 흐름 시험 코호트').props.disabled, true);
    } finally {f.close();}
  });
}
test('missing selected saved graph remains an error before scope dispatch', async () => {
  const f = fixture({graphError: Object.assign(new Error('Selected saved version is missing'), {status: 404})}); try {
    await f.settle(); assert.deepEqual(f.calls, [['pipeline', A]]);
    assert.ok(elements(f.tree()).some(node => node.props?.role === 'alert' && text(node).includes('Selected saved version is missing')));
  } finally {f.close();}
});
for (const outcome of ['unbound', 'bound', 'error']) {
  test(`late ${outcome} graph read cannot overwrite the next selected version`, async () => {
    const held = deferred(), f = fixture({readGraph: id => id === A ? held.promise : pipeline({bound: true, task: 'classification'})});
    try {
      await f.settle(); f.change(B); await f.settle();
      if (outcome === 'error') held.reject(new Error('Old graph error'));
      else held.resolve(pipeline({bound: outcome === 'bound'}));
      await f.settle();
      assert.deepEqual(f.calls, [['pipeline', A], ['pipeline', B], ['scope', B]]);
      assert.match(text(f.tree()), /정답 범위:.*classification/);
      assert.doesNotMatch(text(f.tree()), /완료 모델.*연결|Old graph error/);
    } finally {f.close();}
  });
}
for (const outcome of ['scope', 'error']) {
  test(`late ${outcome} response cannot restore the previous selected scope or error`, async () => {
    const held = deferred(); let f;
    f = fixture({graph: pipeline({bound: true}), readScope: id => id === A ? held.promise : f.scopes[B]});
    try {
      await f.settle(); f.change(B); await f.settle();
      if (outcome === 'error') held.reject(new Error('Old scope conflict'));
      else held.resolve(f.scopes[A]);
      await f.settle();
      assert.deepEqual(f.calls, [['pipeline', A], ['scope', A], ['pipeline', B], ['scope', B]]);
      assert.match(text(f.tree()), /정답 범위:.*classification/);
      assert.doesNotMatch(text(f.tree()), /Old scope conflict/);
    } finally {f.close();}
  });
}
test('same-version context A-B-A keeps only the latest graph admission', async () => {
  const held = [deferred(), deferred(), deferred()]; let next = 0;
  const f = fixture({readGraph: () => held[next++].promise});
  try {
    await f.settle(); f.context('project-B'); await f.settle(); f.context('project-A'); await f.settle();
    held[2].resolve(pipeline({bound: true})); await f.settle();
    held[0].resolve(pipeline()); held[1].reject(new Error('Old project graph error')); await f.settle();
    assert.deepEqual(f.calls, [['pipeline', A], ['pipeline', A], ['pipeline', A], ['scope', A]]);
    assert.match(text(f.tree()), /정답 범위:.*segmentation/);
    assert.doesNotMatch(text(f.tree()), /완료 모델.*연결|Old project graph error/);
  } finally {f.close();}
});
