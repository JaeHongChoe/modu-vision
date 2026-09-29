const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const imageA = { image_id: 'a', file_name: 'a.jpg', file_path: '/test/a.jpg' };
const imageB = { image_id: 'b', file_name: 'b.jpg', file_path: '/test/b.jpg' };
const annotation = { id: 'ann-a', type: 'polygon', label: 'Bow', polygon: [[1, 1], [2, 1], [2, 2]] };
const mockApi = {
  annotations: {
    get: async () => ({ annotations: [] }),
    save: async () => ({ status: 'saved' }),
  },
  project: { update: async () => ({}), getCurrent: async () => null },
};

function loadStore(relativePath, overrides = {}) {
  const filePath = path.resolve(__dirname, relativePath);
  const compiled = ts.transpileModule(fs.readFileSync(filePath, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const storeModule = new Module(filePath, module);
  storeModule.filename = filePath;
  storeModule.paths = Module._nodeModulePaths(path.dirname(filePath));
  const originalRequire = storeModule.require.bind(storeModule);
  storeModule.require = (specifier) => {
    if (specifier === '../services/api') return { api: mockApi, getApiBaseUrl: () => '' };
    if (specifier === './useDatasetStore') {
      return { useDatasetStore: { getState: () => ({ annotationsChanged: async () => {} }) } };
    }
    if (Object.hasOwn(overrides, specifier)) return overrides[specifier];
    return originalRequire(specifier);
  };
  storeModule._compile(compiled, filePath);
  return storeModule.exports;
}

const { useAnnotationStore } = loadStore('../src/renderer/stores/useAnnotationStore.ts');

function resetToDirtyImage() {
  mockApi.annotations.get = async () => ({ annotations: [] });
  mockApi.annotations.save = async () => ({ status: 'saved' });
  useAnnotationStore.setState({
    images: [imageA, imageB], currentImageIndex: 0, currentImage: imageA, activeImage: imageA,
    annotations: [annotation], isDirty: true, isSaving: false, saveMessage: null,
    history: [], future: [],
  });
}

test('failed save keeps the current image and unsaved annotations', async () => {
  resetToDirtyImage();
  mockApi.annotations.save = async () => { throw new Error('disk full'); };
  await useAnnotationStore.getState().selectImageByIndex(1);
  assert.equal(useAnnotationStore.getState().currentImage.image_id, 'a');
  assert.equal(useAnnotationStore.getState().isDirty, true);
  assert.deepEqual(useAnnotationStore.getState().annotations, [annotation]);
});

test('new image page cannot discard annotations when save fails', async () => {
  resetToDirtyImage();
  mockApi.annotations.save = async () => { throw new Error('disk full'); };
  await useAnnotationStore.getState().setImages([imageB]);
  assert.equal(useAnnotationStore.getState().currentImage.image_id, 'a');
  assert.equal(useAnnotationStore.getState().isDirty, true);
});

test('late response for an old image never overwrites the new image', async () => {
  resetToDirtyImage();
  let resolveOld;
  mockApi.annotations.get = () => new Promise((resolve) => { resolveOld = resolve; });
  const loading = useAnnotationStore.getState().loadAnnotationsForCurrent();
  useAnnotationStore.setState({ currentImage: imageB, activeImage: imageB, annotations: [], isDirty: false });
  resolveOld({ annotations: [annotation] });
  await loading;
  assert.equal(useAnnotationStore.getState().currentImage.image_id, 'b');
  assert.deepEqual(useAnnotationStore.getState().annotations, []);
});

test('unknown save response is a failure and preserves dirty state', async () => {
  resetToDirtyImage();
  mockApi.annotations.save = async () => ({ status: 'queued' });
  assert.equal(await useAnnotationStore.getState().saveAnnotations(), false);
  assert.equal(useAnnotationStore.getState().isDirty, true);
});

test('edit made during save remains dirty after older save completes', async () => {
  resetToDirtyImage();
  let resolveSave;
  mockApi.annotations.save = () => new Promise((resolve) => { resolveSave = resolve; });
  const saving = useAnnotationStore.getState().saveAnnotations();
  useAnnotationStore.getState().addAnnotation({ ...annotation, id: 'new' });
  resolveSave({ status: 'saved' });
  assert.equal(await saving, true);
  assert.equal(useAnnotationStore.getState().isDirty, true);
});

test('step navigation waits for annotation save before leaving labeling', async () => {
  resetToDirtyImage();
  mockApi.annotations.save = async () => { throw new Error('disk full'); };
  const { useProjectStore } = loadStore('../src/renderer/stores/useProjectStore.ts', {
    './useAnnotationStore': { useAnnotationStore },
  });
  useProjectStore.setState({ activeStep: 2 });
  await useProjectStore.getState().setStep(3);
  assert.equal(useProjectStore.getState().activeStep, 2);
});
