const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const filename = path.resolve(__dirname, '../src/renderer/stores/useFlowchartStore.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const loaded = new Module(filename, module);
loaded.filename = filename;
loaded.paths = Module._nodeModulePaths(path.dirname(filename));
const activationCalls = [];
loaded.require = (name) => {
  if (name === '../services/api') return {
    api: { flowchart: {
      getPipelineVersion: async () => ({ id: 'old', name: 'Other revision', nodes: [], edges: [] }),
      activatePipelineVersion: async (versionId, sourceDatasetPath) => {
        activationCalls.push([versionId, sourceDatasetPath]);
        return { status: 'active', version_id: versionId,
          pipeline: { id: 'selected', name: 'Selected revision', nodes: [], edges: [] } };
      },
    } },
  };
  return Module.prototype.require.call(loaded, name);
};
loaded._compile(compiled, filename);
const { useFlowchartStore } = loaded.exports;

test('opening a saved revision activates that version for the selected source before showing its graph', async () => {
  const opened = await useFlowchartStore.getState().loadPipelineVersion('a'.repeat(32), '/dataset/current');

  assert.deepEqual(activationCalls, [['a'.repeat(32), '/dataset/current']]);
  assert.equal(opened.name, 'Selected revision');
  assert.equal(useFlowchartStore.getState().pipeline.name, 'Selected revision');
  assert.equal(useFlowchartStore.getState().pipelineDirty, false);
});
