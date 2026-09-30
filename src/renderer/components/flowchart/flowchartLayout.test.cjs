const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const file = path.join(__dirname, 'flowchartGraph.ts');
const item = new Module(file, module);
item.filename = file; item.paths = Module._nodeModulePaths(__dirname);
item._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, file);
const { layoutFlowchart } = item.exports;
const node = (id, type, x, y) => ({ id, position: { x, y }, data: { node_type: type, label: id,
  model_job_id: type === 'inspection' ? 'job_original' : undefined, params: { unchanged: id } } });
const edge = (source, target, branch) => ({ id: `${source}-${target}`, source, target, isBranch: branch });

test('six-node chain lays out execution order without changing IDs, edges or model parameters', () => {
  const pipeline = { id: 'chain', name: 'ROI enhancement chain', nodes: [
    node('input', 'input', 40, 160), node('inspect', 'inspection', 360, 160),
    node('decision', 'decision', 690, 160), node('output', 'output', 1020, 160),
    node('roi', 'fixed_roi', 325, 320), node('enhance', 'preprocess', 670, 320),
  ], edges: [edge('input', 'roi'), edge('roi', 'enhance'), edge('enhance', 'inspect'),
    edge('inspect', 'decision'), edge('decision', 'output')] };
  const before = structuredClone(pipeline);
  const result = layoutFlowchart(pipeline);
  assert.deepEqual(pipeline, before);
  assert.deepEqual(result.edges, pipeline.edges);
  assert.deepEqual(result.nodes.map(n => [n.id, n.data]), pipeline.nodes.map(n => [n.id, n.data]));
  const positions = Object.fromEntries(result.nodes.map(n => [n.id, n.position]));
  for (const connection of result.edges) assert.ok(positions[connection.target].x > positions[connection.source].x + 272);
  assert.equal(new Set(result.nodes.map(n => n.position.y)).size, 1);
  assert.deepEqual(layoutFlowchart(result), result);
});

test('branches and disconnected draft nodes get nonoverlapping rows while every edge points forward', () => {
  const pipeline = { id: 'branches', name: 'Branches', nodes: [
    node('input', 'input', 0, 0), node('detect', 'inspection', 0, 0),
    node('pass', 'inspection', 0, 0), node('fail', 'inspection', 0, 0),
    node('merge', 'aggregate', 0, 0), node('loose', 'preprocess', 0, 0),
    node('loose-child', 'inspection', 0, 0), node('isolated', 'fixed_roi', 0, 0),
  ], edges: [edge('input', 'detect'), edge('detect', 'pass', 'pass'), edge('detect', 'fail', 'fail'),
    edge('pass', 'merge'), edge('fail', 'merge'), edge('loose', 'loose-child')] };
  const result = layoutFlowchart(pipeline);
  const positions = Object.fromEntries(result.nodes.map(n => [n.id, n.position]));
  assert.equal(positions.pass.x, positions.fail.x);
  assert.ok(positions.fail.y >= positions.pass.y + 220);
  assert.ok(positions.loose.y > Math.max(positions.pass.y, positions.fail.y) + 220);
  assert.ok(positions.isolated.y > positions.loose.y + 220);
  for (const connection of result.edges) assert.ok(positions[connection.target].x > positions[connection.source].x + 272);
  assert.deepEqual(result.nodes.map(n => n.id), pipeline.nodes.map(n => n.id));
});

test('cyclic or dangling graph rejects layout without mutating editable data', () => {
  const pipeline = { id: 'cycle', name: 'Cycle', nodes: [node('a', 'inspection', 0, 0), node('b', 'inspection', 0, 0)],
    edges: [edge('a', 'b'), edge('b', 'a')] };
  const before = structuredClone(pipeline);
  assert.throws(() => layoutFlowchart(pipeline), /순환/);
  assert.deepEqual(pipeline, before);
  assert.throws(() => layoutFlowchart({ ...pipeline, edges: [edge('a', 'missing')] }), /연결/);
});
