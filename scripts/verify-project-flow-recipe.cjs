const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/renderer/stores/projectFlowRecipe.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const exportedBindings = {};
vm.runInNewContext(compiled, { exports: exportedBindings });

const graph = (...models) => ({
  nodes: models.map((item, index) => ({ id: String(index), data: item })),
  edges: [],
});
const inspection = (task) => ({ node_type: 'inspection', task, model_job_id: `job_${task}` });
const detector = { node_type: 'detection_crop', task: 'detection', model_job_id: 'job_detector' };

assert.equal(exportedBindings.projectFlowRecipe(graph(inspection('segmentation'), inspection('classification'))), 'mixed');
assert.equal(exportedBindings.projectFlowRecipe(graph(detector, inspection('segmentation'))), 'segmentation');
assert.equal(exportedBindings.projectFlowRecipe(graph(inspection('anomaly'))), 'anomaly');
assert.equal(exportedBindings.projectFlowRecipe(graph(detector)), 'detection');
assert.throws(() => exportedBindings.projectFlowRecipe(graph({ node_type: 'input' })));
console.log('Project flow recipe cases passed: mixed, single inspection, detector chain, detection only, invalid draft.');
