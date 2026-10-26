const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const filename = path.join(__dirname, 'uploadedDatasetSource.ts');
function production() {
  const m = new Module(filename, module); m.filename = filename; m.paths = Module._nodeModulePaths(__dirname);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, filename);
  return m.exports;
}
const {acceptedUploadedArchive, useAcceptedUploadedSource} = production();
const deferred = () => {let resolve; const promise = new Promise(yes => {resolve = yes;}); return {promise, resolve};};
const tick = () => new Promise(setImmediate);
const sha = 'a'.repeat(64), manifest = 'b'.repeat(64);
function fixture() {
  const scope = {key: 'live', ownerKey: 'owner-live', context: {workspace_id: 'w', project_id: 'p', actor_id: 'owner', mode: 'team'},
    projectId: 'p', projectDir: '/projects/p', task: 'classification', labelsetId: 'default', source: '/old',
    expectedSource: {project_id: 'p', source_dataset_dir: '/old', task: 'classification', annotations_dir: '/projects/p/annotations', active_labelset_id: 'default'}};
  const receipt = {revision_id: 'revision-1', state: 'prepared', manifest_sha256: manifest, image_count: 8, valid_count: 8, error_count: 0};
  const job = {job_id: 'job-1', revision: 3, state: 'completed', result: {revision: receipt},
    source: {root: '/projects/p/dataset_imports/server-selected-suffix', artifact: {id: 'artifact-1', revision: 1, sha256: sha}}};
  const listed = {active_revision: 'revision-1', revisions: [{...receipt, source_root: job.source.root,
    publication_key: 'job-1', task: 'classification', active: true}]};
  const project = {id: 'p', project_dir: '/projects/p', task: 'classification', active_labelset_id: 'default', annotations_dir: '/projects/p/annotations', source_dataset_dir: '/old'};
  const reads = [], used = [];
  const deps = {
    context: async () => {reads.push('context'); return {project_context: {...scope.context}};},
    project: async () => {reads.push('project'); return {...project};},
    job: async () => {reads.push('job'); return structuredClone(job);},
    revisions: async () => {reads.push('revisions'); return structuredClone(listed);},
    sourceStatus: async () => {reads.push('sourceStatus'); return {revision_id: 'revision-1', source_root: job.source.root, matches: true};},
  };
  let current = true;
  return {scope, job, listed, project, deps, reads, used, retire: () => {current = false;},
    run: () => useAcceptedUploadedSource({candidate: acceptedUploadedArchive(job, listed, 'classification'), scope,
      isCurrent: () => current, reads: deps, onUse: async source => {used.push(source);}})};
}

test('an explicit use action rereads scoped proof and passes only the fresh server source', async () => {
  const f = fixture();
  assert.ok(acceptedUploadedArchive(f.job, f.listed, 'classification'));
  assert.deepEqual(f.used, [], 'qualification and active acceptance do not choose a source');
  await f.run();
  assert.deepEqual(f.reads, ['context', 'project', 'job', 'revisions', 'sourceStatus']);
  assert.deepEqual(f.used, ['/projects/p/dataset_imports/server-selected-suffix']);
});
for (const [name, change] of [
  ['folder import', f => {f.job.source.artifact = null;}],
  ['unfinished job', f => {f.job.state = 'running';}],
  ['unaccepted revision', f => {f.listed.active_revision = null;}],
  ['different revision', f => {f.listed.revisions[0].revision_id = 'other';}],
  ['different root', f => {f.listed.revisions[0].source_root = '/other';}],
  ['different task', f => {f.listed.revisions[0].task = 'segmentation';}],
  ['different publishing job', f => {f.listed.revisions[0].publication_key = 'other';}],
  ['different manifest', f => {f.listed.revisions[0].manifest_sha256 = 'c'.repeat(64);}],
  ['all invalid', f => {f.job.result.revision.valid_count = 0;}],
]) test('cannot choose an uploaded source from ' + name, async () => {
  const f = fixture(); change(f);
  assert.equal(acceptedUploadedArchive(f.job, f.listed, 'classification'), null);
  await assert.rejects(f.run()); assert.deepEqual(f.used, []);
});
for (const stage of ['context', 'project', 'job', 'revisions', 'sourceStatus']) test('a retired action after held ' + stage + ' cannot choose a source', async () => {
  const f = fixture(), gate = deferred(), original = f.deps[stage];
  f.deps[stage] = async (...args) => {const value = await original(...args); await gate.promise; return value;};
  const pending = f.run(); await tick(); f.retire(); gate.resolve();
  await assert.rejects(pending); assert.deepEqual(f.used, []);
});
for (const [name, alter] of [
  ['actor', f => {f.deps.context = async () => ({project_context: {...f.scope.context, actor_id: 'other'}});}],
  ['workspace', f => {f.deps.context = async () => ({project_context: {...f.scope.context, workspace_id: 'other'}});}],
  ['server source', f => {f.deps.project = async () => ({...f.project, source_dataset_dir: '/newer'});}],
  ['labelset', f => {f.deps.project = async () => ({...f.project, active_labelset_id: 'new'});}],
  ['annotation root', f => {f.deps.project = async () => ({...f.project, annotations_dir: '/other'});}],
  ['artifact', f => {f.deps.job = async () => ({...f.job, source: {...f.job.source, artifact: {...f.job.source.artifact, sha256: 'c'.repeat(64)}}});}],
  ['job root', f => {f.deps.job = async () => ({...f.job, source: {...f.job.source, root: '/other'}});}],
  ['active revision', f => {f.deps.revisions = async () => ({...f.listed, active_revision: 'new'});}],
  ['changed extraction', f => {f.deps.sourceStatus = async () => ({revision_id: 'revision-1', source_root: f.job.source.root, matches: false});}],
]) test('fresh ' + name + ' mismatch blocks source selection', async () => {
  const f = fixture(); alter(f); await assert.rejects(f.run()); assert.deepEqual(f.used, []);
});
test('a source callback write refusal propagates without a success result', async () => {
  const f = fixture(), refused = new Error('project.manage refused');
  await assert.rejects(useAcceptedUploadedSource({candidate: acceptedUploadedArchive(f.job, f.listed, 'classification'),
    scope: f.scope, isCurrent: () => true, reads: f.deps, onUse: async () => {throw refused;}}), error => error === refused);
});
