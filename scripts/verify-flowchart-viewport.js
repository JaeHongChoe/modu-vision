const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const sourcePath = path.resolve(__dirname, '../src/renderer/components/flowchart/flowchartViewport.ts');
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const viewportModule = new Module(sourcePath, module);
viewportModule.filename = sourcePath;
viewportModule.paths = Module._nodeModulePaths(path.dirname(sourcePath));
viewportModule._compile(compiled, sourcePath);
const { computeFlowchartViewport, FLOW_NODE_WIDTH, FLOW_NODE_HEIGHT } = viewportModule.exports;

function visibleBounds(layout, nodes) {
  const xs = nodes.map((node) => node.position.x);
  const ys = nodes.map((node) => node.position.y);
  return {
    left: Math.min(...xs) * layout.scale + layout.offsetX,
    right: (Math.max(...xs) + FLOW_NODE_WIDTH) * layout.scale + layout.offsetX,
    top: Math.min(...ys) * layout.scale + layout.offsetY,
    bottom: (Math.max(...ys) + FLOW_NODE_HEIGHT) * layout.scale + layout.offsetY,
  };
}

const singleModelNodes = [40, 360, 680, 1000].map((x) => ({ position: { x, y: 160 } }));
const detectorNodes = [40, 340, 640, 940, 1240].map((x) => ({ position: { x, y: 180 } }));

test('single segmentation graph fits beside inspector and starts below the toolbar', () => {
  const layout = computeFlowchartViewport(singleModelNodes, { width: 950, height: 540 }, 1);
  const visible = visibleBounds(layout, singleModelNodes);
  assert.equal(layout.contentWidth, 950);
  assert.equal(layout.contentHeight, 540);
  assert.ok(visible.left >= 32 && visible.right <= 918, JSON.stringify(visible));
  assert.ok(visible.top >= 32 && visible.bottom <= 508, JSON.stringify(visible));
  assert.ok(visible.top >= 48 && visible.top <= 96, `graph starts too far below the toolbar: ${JSON.stringify(visible)}`);
  assert.ok(Math.abs(visible.left - (950 - visible.right)) <= 1, `graph is not horizontally centered: ${JSON.stringify(visible)}`);
});

test('five node detector flow remains inside the canvas and fits by default', () => {
  const layout = computeFlowchartViewport(detectorNodes, { width: 1100, height: 600 }, 1);
  const visible = visibleBounds(layout, detectorNodes);
  assert.equal(layout.contentWidth, 1100);
  assert.ok(visible.left >= 32 && visible.right <= 1068, JSON.stringify(visible));
  assert.ok(visible.top >= 32 && visible.bottom <= 568, JSON.stringify(visible));
});

test('wide desktop canvas presents the graph below the toolbar at a readable scale', () => {
  const layout = computeFlowchartViewport(singleModelNodes, { width: 2200, height: 950 }, 1);
  const visible = visibleBounds(layout, singleModelNodes);
  assert.ok(layout.scale > 1 && layout.scale <= 1.25, `unexpected scale: ${layout.scale}`);
  // Phase 0-4 intentionally moved short graphs toward the toolbar; large
  // vertical centering left an empty strip before the first node.
  assert.ok(visible.top >= 48 && visible.top <= 96, `graph starts too far below the toolbar: ${JSON.stringify(visible)}`);
  assert.ok(Math.abs(visible.left - (2200 - visible.right)) <= 1, `graph is not horizontally centered: ${JSON.stringify(visible)}`);
  assert.ok(visible.bottom <= 950 - 48, JSON.stringify(visible));
  assert.equal(layout.contentHeight, 950);
  assert.equal(layout.contentWidth, 2200);
});

test('short canvas centers a fitting graph while preserving vertical clearance', () => {
  const layout = computeFlowchartViewport(singleModelNodes, { width: 950, height: 330 }, 1);
  const visible = visibleBounds(layout, singleModelNodes);
  assert.ok(visible.top >= 48 && visible.bottom <= 330 - 48, JSON.stringify(visible));
  assert.ok(Math.abs(visible.top - (330 - visible.bottom)) <= 1, JSON.stringify(visible));
  assert.equal(layout.contentWidth, 950);
  assert.equal(layout.contentHeight, 330);
});

test('zooming in expands scrollable content while reset fits again', () => {
  const fitted = computeFlowchartViewport(singleModelNodes, { width: 950, height: 540 }, 1);
  const enlarged = computeFlowchartViewport(singleModelNodes, { width: 950, height: 540 }, 2);
  assert.ok(enlarged.scale > fitted.scale);
  assert.ok(enlarged.contentWidth > 950);
  assert.equal(fitted.contentWidth, 950);
});
