const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function load(file) {
  const compiled = new Module(file, module);
  compiled.filename = file;
  compiled.paths = Module._nodeModulePaths(path.dirname(file));
  const requireOriginal = compiled.require.bind(compiled);
  compiled.require = name => {
    if (name.startsWith('.')) {
      const local = path.resolve(path.dirname(file), name) + '.ts';
      if (fs.existsSync(local)) return load(local);
    }
    return requireOriginal(name);
  };
  compiled._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText, file);
  return compiled.exports;
}

const file = path.join(__dirname, 'polygonEditing.ts');
const editing = fs.existsSync(file) ? load(file) : {};
function edit(name, ...args) {
  assert.equal(typeof editing[name], 'function', `${name} must be implemented`);
  return editing[name](...args);
}
function frozen(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(frozen);
    Object.freeze(value);
  }
  return value;
}
const rectangle = () => [[100, 100], [2500, 100], [2500, 1300], [100, 1300]];
function annotation(extra = {}) {
  return frozen({id: 'machine-shape', type: 'polygon', label: '흠집', category_id: 7,
    color: '#f59e0b', text: '원래 전사', direction_deg: 27,
    polygon: rectangle(), points: rectangle(), bbox: [100, 100, 2500, 1300],
    origin: {kind: 'model_draft', proposal_id: 'p', candidate_id: 'c', reviewer: 'kai',
      adopted_at: '2026-11-14T00:00:00Z', checkpoint_sha256: 'original-checkpoint'}, ...extra});
}

// Literal image-pixel expectations catch accidental percent conversion or viewport scaling.
test('closest edge uses original non-square image coordinates and the supplied hit radius', () => {
  const points = frozen(rectangle());
  assert.deepEqual(edit('findClosestPolygonEdge', points, {x: 1300, y: 103}, 4),
    {edgeIndex: 0, point: {x: 1300, y: 100}});
  assert.equal(edit('findClosestPolygonEdge', points, {x: 1300, y: 103}, 2), null);
  assert.deepEqual(points, rectangle());
});

test('closest edge includes the closing segment', () => {
  assert.deepEqual(edit('findClosestPolygonEdge', rectangle(), {x: 98, y: 650}, 3),
    {edgeIndex: 3, point: {x: 100, y: 650}});
});

test('segment projection clamps beyond its endpoint and projects diagonal interiors', () => {
  assert.deepEqual(edit('findClosestPolygonEdge', [[0, 0], [10, 0], [10, 10]], {x: 12, y: -1}, 3),
    {edgeIndex: 0, point: {x: 10, y: 0}});
  assert.deepEqual(edit('findClosestPolygonEdge', [[0, 0], [10, 10], [20, 0]], {x: 5, y: 7}, 2),
    {edgeIndex: 0, point: {x: 6, y: 6}});
});

test('a zero-length edge never produces NaN and ties have a stable edge index', () => {
  assert.deepEqual(edit('findClosestPolygonEdge', [[0, 0], [0, 0], [10, 0]], {x: 0, y: 1}, 1),
    {edgeIndex: 0, point: {x: 0, y: 0}});
});

test('edge lookup rejects malformed coordinates, cursor and radius', () => {
  for (const [points, cursor, radius] of [
    [[], {x: 1, y: 1}, 2],
    [[[0, 0]], {x: 1, y: 1}, 2],
    [[[0, 0], [Infinity, 1]], {x: 1, y: 1}, 2],
    [rectangle(), {x: NaN, y: 1}, 2],
    [rectangle(), {x: 1, y: 1}, -1],
    [rectangle(), {x: 1, y: 1}, Infinity],
  ]) assert.equal(edit('findClosestPolygonEdge', points, cursor, radius), null);
});

test('insertion projects to an edge and returns geometry only without mutating machine metadata', () => {
  const original = annotation();
  const before = structuredClone(original);
  const result = edit('insertPolygonVertex', original, 0, {x: 1300, y: 90});
  assert.deepEqual(result, {vertexIndex: 1, updates: {
    polygon: [[100, 100], [1300, 100], [2500, 100], [2500, 1300], [100, 1300]],
    points: [[100, 100], [1300, 100], [2500, 100], [2500, 1300], [100, 1300]],
    bbox: [100, 100, 2500, 1300],
  }});
  assert.deepEqual(original, before);
  assert.deepEqual({...original, ...result.updates}.origin, before.origin);
  assert.equal({...original, ...result.updates}.category_id, 7);
  assert.notEqual(result.updates.polygon, original.polygon);
  assert.notEqual(result.updates.points, original.points);
  assert.notEqual(result.updates.polygon[0], original.polygon[0]);
});

test('closing-edge insertion appends the projected point and selects its new index', () => {
  const result = edit('insertPolygonVertex', annotation(), 3, {x: 90, y: 650});
  assert.deepEqual(result.updates.polygon,
    [[100, 100], [2500, 100], [2500, 1300], [100, 1300], [100, 650]]);
  assert.equal(result.vertexIndex, 4);
});

test('legacy points are editable and both aliases are synchronized after every operation', () => {
  const legacy = annotation({polygon: undefined, points: [[10, 10], [30, 10], [30, 20], [10, 20]]});
  const cases = [
    ['insertPolygonVertex', 0, {x: 20, y: 12}],
    ['deletePolygonVertex', 1],
    ['movePolygonVertex', 0, {x: 5, y: 8}],
  ];
  for (const [name, ...args] of cases) {
    const result = edit(name, legacy, ...args);
    assert.ok(result.updates, name);
    assert.deepEqual(result.updates.polygon, result.updates.points);
    assert.notEqual(result.updates.points, legacy.points);
  }
});

test('the canonical polygon replaces stale points alias coordinates', () => {
  const result = edit('movePolygonVertex', annotation({points: [[1, 1], [2, 1], [2, 2]]}), 1, {x: 2400, y: 90});
  assert.deepEqual(result.updates.polygon, [[100, 100], [2400, 90], [2500, 1300], [100, 1300]]);
  assert.deepEqual(result.updates.points, result.updates.polygon);
});

test('insertion rejects vertices duplicated by endpoint clamping', () => {
  assert.deepEqual(edit('insertPolygonVertex', annotation(), 0, {x: 2500, y: 100}), {error: 'duplicate_point'});
  assert.deepEqual(edit('insertPolygonVertex', annotation(), 0, {x: 3000, y: 100}), {error: 'duplicate_point'});
});

test('deletion recomputes bounds and keeps selection on a surviving vertex', () => {
  const original = annotation({polygon: [[0, 0], [10, 0], [20, 5], [10, 10], [0, 10]]});
  const result = edit('deletePolygonVertex', original, 2);
  assert.deepEqual(result, {vertexIndex: 2, updates: {
    polygon: [[0, 0], [10, 0], [10, 10], [0, 10]],
    points: [[0, 0], [10, 0], [10, 10], [0, 10]], bbox: [0, 0, 10, 10],
  }});
  assert.equal(edit('deletePolygonVertex', annotation(), 3).vertexIndex, 2);
});

test('deletion preserves at least three points and rejects a degenerate remaining polygon', () => {
  const triangle = annotation({polygon: [[0, 0], [10, 0], [0, 10]]});
  assert.deepEqual(edit('deletePolygonVertex', triangle, 1), {error: 'minimum_points'});
  const onlyRaisedVertex = annotation({polygon: [[0, 0], [5, 0], [10, 0], [0, 10]]});
  assert.deepEqual(edit('deletePolygonVertex', onlyRaisedVertex, 3), {error: 'invalid_polygon'});
});

test('movement keeps fractional image pixels, recomputes bounds and leaves all input coordinates intact', () => {
  const original = annotation();
  const before = structuredClone(original);
  assert.deepEqual(edit('movePolygonVertex', original, 0, {x: 75.25, y: 80.5}), {vertexIndex: 0, updates: {
    polygon: [[75.25, 80.5], [2500, 100], [2500, 1300], [100, 1300]],
    points: [[75.25, 80.5], [2500, 100], [2500, 1300], [100, 1300]], bbox: [75.25, 80.5, 2500, 1300],
  }});
  assert.deepEqual(original, before);
});

test('movement rejects another vertex and zero-area results', () => {
  assert.deepEqual(edit('movePolygonVertex', annotation(), 0, {x: 2500, y: 100}), {error: 'duplicate_point'});
  assert.deepEqual(edit('movePolygonVertex', annotation({polygon: [[0, 0], [10, 0], [0, 10]]}), 2,
    {x: 5, y: 0}), {error: 'invalid_polygon'});
});

test('each edit rejects invalid indices without modifying the annotation', () => {
  for (const index of [-1, 4, 1.5, NaN]) {
    for (const name of ['insertPolygonVertex', 'deletePolygonVertex', 'movePolygonVertex']) {
      const original = annotation();
      const before = structuredClone(original);
      assert.deepEqual(edit(name, original, index, {x: 500, y: 100}), {error: 'invalid_vertex'}, `${name} ${index}`);
      assert.deepEqual(original, before);
    }
  }
});

test('edits reject missing, nonfinite, duplicate, zero-area and non-polygon source geometry', () => {
  const invalid = [
    annotation({polygon: undefined, points: undefined}),
    annotation({polygon: []}),
    annotation({polygon: [[0, 0], [10, 0]]}),
    annotation({polygon: [[0, 0], [Infinity, 0], [0, 10]]}),
    annotation({polygon: [[0, 0], [10, 0], [10, 0], [0, 10]]}),
    annotation({polygon: [[0, 0], [10, 0], [20, 0]]}),
    annotation({type: 'bbox'}),
  ];
  for (const source of invalid) {
    for (const name of ['insertPolygonVertex', 'deletePolygonVertex', 'movePolygonVertex']) {
      assert.deepEqual(edit(name, source, 0, {x: 500, y: 100}), {error: 'invalid_polygon'}, name);
    }
  }
  for (const name of ['insertPolygonVertex', 'movePolygonVertex']) {
    assert.deepEqual(edit(name, annotation(), 0, {x: NaN, y: 100}), {error: 'invalid_polygon'});
  }
});
