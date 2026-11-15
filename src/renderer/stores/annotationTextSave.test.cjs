const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function fixture(text) {
  const original = {
    id: 'machine-polygon', type: 'polygon', label: '흠집', category_id: 7, color: '#f59e0b',
    polygon: [[100, 100], [2500, 100], [2500, 1300], [100, 1300]],
    points: [[100, 100], [2500, 100], [2500, 1300], [100, 1300]],
    bbox: [100, 100, 2500, 1300], text, direction_deg: 27,
    origin: {kind: 'model_draft', proposal_id: 'proposal-1', candidate_id: 'candidate-1',
      reviewer: 'reviewer-1', adopted_at: '2026-11-14T00:00:00Z', checkpoint_sha256: 'original-checkpoint'},
  };
  const image = {image_id: 'part', file_path: '/source/part.png', width: 2560, height: 1440};
  const bodies = [];
  const file = path.join(__dirname, 'useAnnotationStore.ts');
  const compiled = new Module(file, module);
  compiled.filename = file;
  compiled.paths = Module._nodeModulePaths(__dirname);
  const requireOriginal = compiled.require.bind(compiled);
  const mocks = {
    '../services/api': {api: {}, getApiPersistenceIdentity: () => 'local', getProjectContextGeneration: () => 1},
    '../services/datasetWorkflow': {datasetWorkflow: {
      annotations: async () => ({image_id: 'part', image_width: 2560, image_height: 1440,
        annotations: [structuredClone(original)], metadata: {image_uuid: 'part', revision: 4}}),
      saveAnnotations: async body => {
        // Match the outgoing JSON boundary, including preservation of an intentional empty transcription.
        bodies.push(JSON.parse(JSON.stringify(body)));
        return {status: 'saved', metadata: {image_uuid: 'part', revision: 5}};
      },
    }},
    './useDatasetStore': {useDatasetStore: {getState: () => ({folderPath: '/source', annotationsChanged: async () => {}})}},
    '../components/labeling/foundationRequest': {labelCategoryPalette: categories => categories},
    '../components/labeling/convertedAnnotation': {},
    '../components/labeling/teamDataWorkflow': {imageLeaseToken: () => null},
  };
  compiled.require = name => Object.hasOwn(mocks, name) ? mocks[name] : requireOriginal(name);
  compiled._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText, file);
  const store = compiled.exports.useAnnotationStore;
  store.setState({currentImage: image, activeImage: image, task: 'segmentation', reviewerName: 'editor'});
  return {store, bodies, original};
}

for (const [name, text] of [['Korean transcription', '표면 흠집 01'], ['intentional empty transcription', '']]) {
  test(`a polygon geometry edit saves its ${name} together with stable identity, category, direction and origin`, async () => {
    const {store, bodies, original} = fixture(text);
    assert.equal(await store.getState().loadAnnotationsForCurrent(), true);
    const geometry = {
      polygon: [[90, 95], [2500, 100], [2500, 1300], [100, 1300]],
      points: [[90, 95], [2500, 100], [2500, 1300], [100, 1300]],
      bbox: [90, 95, 2500, 1300],
    };
    store.getState().updateAnnotation(original.id, geometry);
    assert.deepEqual(store.getState().annotations[0], {...original, ...geometry});
    assert.equal(await store.getState().saveAnnotations(), true);
    assert.equal(bodies.length, 1);
    assert.deepEqual(bodies[0].annotations[0], {...original, ...geometry});
    assert.equal(bodies[0].expected_revision, 4);
    assert.equal(bodies[0].actor, 'editor');
    assert.equal(store.getState().isDirty, false);
  });
}
