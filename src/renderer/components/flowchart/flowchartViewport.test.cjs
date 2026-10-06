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

test('node search finds Korean labels, IDs, types and model IDs without changing graph order',()=>{
  const nodes=[{id:'roi-2',position:{x:-500,y:300},data:{label:'한글 고정 영역',node_type:'fixed_roi'}},{id:'ocr-1',position:{x:12000,y:12000},data:{label:'문자 검사',node_type:'inspection',task:'ocr',model_job_id:'JOB_ALPHA'}}];
  const before=JSON.stringify(nodes);
  for(const [query,expected] of [[' 고정 ',['roi-2']],['ＲＯＩ-２',['roi-2']],['OCR',['ocr-1']],['job_alpha',['ocr-1']],['no-match',[]]])
    assert.deepEqual(loaded.exports.findFlowNodes(nodes,query).map(n=>n.id),expected);
  assert.equal(JSON.stringify(nodes),before);
});
test('jump keeps a distant negative-coordinate node visible at the rendered zoom',()=>{
  const nodes=[{position:{x:-500,y:-600}},{position:{x:12000,y:8000}}];
  const viewport=computeFlowchartViewport(nodes,{width:700,height:400},4);
  const target=loaded.exports.flowNodeScrollTarget(nodes[1],viewport,{width:700,height:400});
  const x=nodes[1].position.x*viewport.scale+viewport.offsetX;
  const y=nodes[1].position.y*viewport.scale+viewport.offsetY;
  assert.ok(x>=target.left && x+272*viewport.scale<=target.left+700);
  assert.ok(y>=target.top && y+220*viewport.scale<=target.top+400);
  assert.deepEqual(loaded.exports.flowNodeScrollTarget(nodes[0],viewport,{width:700,height:400}),{left:0,top:0});
});
test('minimap pans both axes and clamps letterbox clicks to scrollable content',()=>{
  const viewport={scale:1,offsetX:48,offsetY:48,contentWidth:5000,contentHeight:2500};
  const map=loaded.exports.computeFlowchartMinimap(viewport,{width:800,height:400,left:1200,top:700});
  const center={x:map.offsetX+(1200+400)*map.scale,y:map.offsetY+(700+200)*map.scale};
  const target=loaded.exports.minimapScrollTarget(center,map,viewport,{width:800,height:400});
  assert.ok(Math.abs(target.left-1200)<1e-8&&Math.abs(target.top-700)<1e-8);
  assert.deepEqual(loaded.exports.minimapScrollTarget({x:-1,y:-1},map,viewport,{width:800,height:400}),{left:0,top:0});
  assert.deepEqual(loaded.exports.minimapScrollTarget({x:9999,y:9999},map,viewport,{width:800,height:400}),{left:4200,top:2100});
  assert.equal(map.visible.width,800*map.scale);assert.equal(map.visible.height,400*map.scale);
});

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
