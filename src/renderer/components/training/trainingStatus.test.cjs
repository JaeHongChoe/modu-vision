const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const file = path.resolve(__dirname, '../../stores/useTrainingStore.ts');
const mod = new Module(file, module); mod.filename=file; mod.paths=Module._nodeModulePaths(path.dirname(file));
const original=mod.require.bind(mod);
mod.require=name=> ({'../services/api':{api:{}},'./useComputeStore':{useComputeStore:{}},'./useDatasetStore':{useDatasetStore:{}},'../utils/trainingComputeReadiness':{}}[name] || original(name));
mod._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
test('reopened terminal and uncertain jobs retain their state and cannot appear idle',()=>{
  assert.equal(typeof mod.exports.statusFromJob, 'function');
  for(const status of ['cancelled','stopped','interrupted','disconnected']) assert.equal(mod.exports.statusFromJob({status}),status);
  assert.equal(mod.exports.statusFromJob({status:'future_server_state'}),'unverified');
  assert.equal(mod.exports.statusFromJob({status:'started'}),'running');
  assert.equal(mod.exports.statusFromJob({status:'failed',phase:'running'}),'failed');
});
