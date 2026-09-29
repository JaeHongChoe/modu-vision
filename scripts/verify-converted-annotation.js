const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const sourcePath = path.resolve(__dirname, '../src/renderer/components/labeling/convertedAnnotation.ts');
const sourceModule = new Module(sourcePath, module);
sourceModule.filename = sourcePath;
sourceModule.paths = Module._nodeModulePaths(path.dirname(sourcePath));
sourceModule._compile(ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, sourcePath);
const { applyConvertedShape, isShapeConvertible } = sourceModule.exports;

const maskUrl = 'data:image/png;base64,iVBORw0KGgo=';

test('polygon-to-mask keeps actual PNG pixels and removes stale vector geometry', () => {
  const original = {
    id: 'bow', type: 'polygon', label: 'Bow', category_id: 7, color: '#ef4444',
    polygon: [[10, 10], [30, 10], [30, 30], [10, 30]],
    points: [[10, 10], [30, 10], [30, 30], [10, 30]],
    bbox: [10, 10, 30, 30],
  };
  const converted = applyConvertedShape(original, 'mask', { mask_rle: maskUrl, shape: [40, 40] });
  assert.equal(converted.type, 'brush_mask');
  assert.equal(converted.mask_rle, maskUrl);
  assert.equal(converted.polygon, undefined);
  assert.equal(converted.points, undefined);
  assert.equal(converted.bbox, undefined);
  assert.equal(converted.label, 'Bow');
});

test('mask-to-polygon drops obsolete raster payload while preserving label', () => {
  const original = { id: 'bow', type: 'brush_mask', label: 'Bow', category_id: 7, mask_rle: maskUrl };
  const converted = applyConvertedShape(original, 'polygon', {
    polygon: [[12, 9], [29, 9], [29, 21], [12, 21]],
  });
  assert.equal(converted.type, 'polygon');
  assert.equal(converted.mask_rle, undefined);
  assert.deepEqual(converted.bbox, [12, 9, 29, 21]);
  assert.equal(converted.label, 'Bow');
});

test('missing mask bytes reject the conversion instead of silently deleting a label', () => {
  const original = { id: 'bow', type: 'polygon', label: 'Bow', category_id: 7, polygon: [[1, 1], [2, 1], [2, 2]] };
  assert.equal(applyConvertedShape(original, 'mask', { shape: [40, 40] }), null);
});

test('saved brush masks remain selectable for mask-to-vector conversion', () => {
  assert.equal(isShapeConvertible({ type: 'brush_mask', mask_rle: maskUrl }), true);
  assert.equal(isShapeConvertible({ type: 'brush_mask' }), false);
  assert.equal(isShapeConvertible({ type: 'tag', label: 'OK' }), false);
});
