const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const filename = path.join(__dirname, 'flowchartViewport.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const loaded = new Module(filename, module);
loaded.filename = filename;
loaded.paths = Module._nodeModulePaths(__dirname);
loaded._compile(compiled, filename);
const { computeFlowchartViewport, readableFlowScale } = loaded.exports;

test('long saved graph starts near toolbar instead of halfway down a tall canvas', () => {
  const nodes = [50, 320, 620, 920, 1200].map((x) => ({ position: { x, y: 180 } }));
  const viewport = computeFlowchartViewport(nodes, { width: 1050, height: 1050 });
  const graphTop = viewport.offsetY + 180 * viewport.scale;
  assert.ok(graphTop >= 48 && graphTop <= 120, `graph top was ${graphTop}`);
  assert.ok(viewport.contentWidth >= 1050);
  assert.ok(viewport.contentHeight >= 1050);
});

test('zoomed multirow graph remains inside a scrollable content area', () => {
  const nodes = [{ position: { x: 40, y: 50 } }, { position: { x: 1150, y: 750 } }];
  const viewport = computeFlowchartViewport(nodes, { width: 780, height: 450 }, 1.5);
  const right = viewport.offsetX + (1150 + 272) * viewport.scale;
  const bottom = viewport.offsetY + (750 + 220) * viewport.scale;
  assert.ok(viewport.contentWidth >= right + 47);
  assert.ok(viewport.contentHeight >= bottom + 47);
});

test('five-model chain opens at readable scale while fit-all remains available', () => {
  const nodes = Array.from({ length: 9 }, (_, index) => ({ position: { x: 40 + index * 300, y: 180 } }));
  const fit = computeFlowchartViewport(nodes, { width: 1050, height: 1050 });
  assert.ok(fit.scale < 0.5);
  const readable = readableFlowScale(fit.scale);
  assert.ok(readable >= 0.72);
  const viewport = computeFlowchartViewport(nodes, { width: 1050, height: 1050 }, readable / fit.scale);
  assert.ok(viewport.scale >= 0.72);
  assert.ok(viewport.contentWidth > 1050, 'readable graph should scroll horizontally');
  assert.equal(computeFlowchartViewport(nodes, { width: 1050, height: 1050 }).scale, fit.scale);
});
