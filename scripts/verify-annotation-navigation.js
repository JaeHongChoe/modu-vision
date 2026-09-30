const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

global.localStorage = { getItem: () => null, setItem: () => {} };

const imageA = { image_id: 'a', file_name: 'a.jpg', file_path: '/test/a.jpg' };
const imageB = { image_id: 'b', file_name: 'b.jpg', file_path: '/test/b.jpg' };
const annotation = { id: 'ann-a', type: 'polygon', label: 'Bow', polygon: [[1, 1], [2, 1], [2, 2]] };
const mockApi = {
  annotations: {
    get: async () => ({ annotations: [] }),
    save: async () => ({ status: 'saved' }),
    autoSelect: async () => ({ result: { polygon: [[1, 1], [2, 1], [2, 2]], bbox: [1, 1, 2, 2] } }),
  },
  project: { update: async () => ({}), getCurrent: async () => null },
  imageMetadata: async (filePath) => ({ image_uuid:'uuid-external', file_path:filePath, width:30, height:20, revision:1 }),
};
const datasetState = { images: [imageA, imageB], annotationsChanged: async () => {} };

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
    if (specifier === '../services/datasetWorkflow') return { datasetWorkflow: { annotations: (...args) => mockApi.annotations.get(...args), saveAnnotations: (...args) => mockApi.annotations.save(...args), image: (...args) => mockApi.imageMetadata(...args) }, workflowError: e => e.message || String(e) };
    if (specifier === '../services/api') return { api: mockApi, getApiBaseUrl: () => '' };
    if (specifier === './useDatasetStore') {
      return { useDatasetStore: { getState: () => datasetState } };
    }
    if (['./useFlowchartStore','./useTrainingStore','./useInspectionRunStore','./useModelAssistRunStore'].includes(specifier)) return { [specifier.slice(2)]: { getState: () => ({}) } };
    if (specifier === '../components/flowchart/flowchartStartup') return { getFlowchartModelReferences: () => [] };
    if (specifier === './projectFlowRecipe') return { projectFlowRecipe: () => null };
    if (specifier === '../components/labeling/convertedAnnotation') {
      return { applyConvertedShape: () => null };
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
    annotationLoadStatus: 'ready', annotationLoadError: null, history: [], future: [],
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
  useAnnotationStore.setState({ isDirty: false });
  let resolveOld;
  mockApi.annotations.get = () => new Promise((resolve) => { resolveOld = resolve; });
  const loading = useAnnotationStore.getState().loadAnnotationsForCurrent();
  useAnnotationStore.setState({ currentImage: imageB, activeImage: imageB, annotations: [], isDirty: false });
  resolveOld({ annotations: [annotation] });
  await loading;
  assert.equal(useAnnotationStore.getState().currentImage.image_id, 'b');
  assert.deepEqual(useAnnotationStore.getState().annotations, []);
});

test('failed annotation read blocks blank overwrite and retry restores stored labels', async () => {
  resetToDirtyImage();
  const saved = [];
  mockApi.annotations.save = async (payload) => {
    saved.push(payload);
    return { status: 'saved' };
  };
  mockApi.annotations.get = async () => { throw new Error('annotation read failed'); };

  await useAnnotationStore.getState().selectImageByIndex(1);
  assert.equal(useAnnotationStore.getState().currentImage.image_id, 'b');
  assert.equal(useAnnotationStore.getState().annotationLoadStatus, 'error');
  assert.match(useAnnotationStore.getState().annotationLoadError, /annotation read failed/);
  assert.equal(saved.length, 1);
  assert.equal(saved[0].image_id, 'a');
  assert.equal(await useAnnotationStore.getState().saveAnnotations(), false);
  useAnnotationStore.getState().addAnnotation(annotation);
  assert.equal(useAnnotationStore.getState().annotations.length, 0);
  assert.equal(saved.length, 1);

  mockApi.annotations.get = async () => ({ annotations: [{ ...annotation, id: 'stored-b' }] });
  assert.equal(await useAnnotationStore.getState().loadAnnotationsForCurrent(), true);
  assert.equal(useAnnotationStore.getState().annotationLoadStatus, 'ready');
  assert.equal(useAnnotationStore.getState().annotationLoadError, null);
  assert.equal(useAnnotationStore.getState().annotations[0].id, 'stored-b');
  assert.equal(saved.length, 1);
});

test('pending annotation read blocks edits and save, then loads the original labels', async () => {
  resetToDirtyImage();
  useAnnotationStore.setState({ annotations: [], isDirty: false });
  let finishRead;
  let saveCalls = 0;
  mockApi.annotations.get = () => new Promise((resolve) => { finishRead = resolve; });
  mockApi.annotations.save = async () => { saveCalls += 1; return { status: 'saved' }; };
  const selecting = useAnnotationStore.getState().selectImageByIndex(1);
  assert.equal(useAnnotationStore.getState().annotationLoadStatus, 'loading');
  useAnnotationStore.getState().markNormal(true);
  assert.equal(await useAnnotationStore.getState().saveAnnotations(), false);
  assert.equal(useAnnotationStore.getState().annotations.length, 0);
  assert.equal(saveCalls, 0);
  finishRead({ annotations: [{ ...annotation, id: 'stored-b' }] });
  await selecting;
  assert.equal(useAnnotationStore.getState().annotationLoadStatus, 'ready');
  assert.equal(useAnnotationStore.getState().annotations[0].id, 'stored-b');
});

test('failed annotation read does not trap navigation to another image', async () => {
  resetToDirtyImage();
  useAnnotationStore.setState({ annotations: [], isDirty: false });
  mockApi.annotations.get = async (imageId) => {
    if (imageId === 'b') throw new Error('offline');
    return { annotations: [{ ...annotation, id: 'stored-a' }] };
  };
  await useAnnotationStore.getState().selectImageByIndex(1);
  assert.equal(useAnnotationStore.getState().annotationLoadStatus, 'error');
  await useAnnotationStore.getState().selectImageByIndex(0);
  assert.equal(useAnnotationStore.getState().currentImage.image_id, 'a');
  assert.equal(useAnnotationStore.getState().annotationLoadStatus, 'ready');
  assert.equal(useAnnotationStore.getState().annotations[0].id, 'stored-a');
});

test('malformed annotation response cannot be treated as an unlabeled image', async () => {
  resetToDirtyImage();
  useAnnotationStore.setState({ annotations: [], isDirty: false });
  mockApi.annotations.get = async () => ({ image_id: 'b' });
  await useAnnotationStore.getState().selectImageByIndex(1);
  assert.equal(useAnnotationStore.getState().annotationLoadStatus, 'error');
  assert.equal(await useAnnotationStore.getState().saveAnnotations(), false);
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

test('opening a dataset image enters labeling with that image selected', async () => {
  resetToDirtyImage();
  const { useProjectStore } = loadStore('../src/renderer/stores/useProjectStore.ts', {
    './useAnnotationStore': { useAnnotationStore },
  });
  useProjectStore.setState({ activeStep: 1 });
  assert.equal(await useProjectStore.getState().openImageForLabeling('b', imageB.file_path), true);
  assert.equal(useProjectStore.getState().activeStep, 2);
  assert.equal(useAnnotationStore.getState().currentImage.image_id, 'b');
});

test('opening a dataset image stays put when pending annotations fail to save', async () => {
  resetToDirtyImage();
  mockApi.annotations.save = async () => { throw new Error('disk full'); };
  const { useProjectStore } = loadStore('../src/renderer/stores/useProjectStore.ts', {
    './useAnnotationStore': { useAnnotationStore },
  });
  useProjectStore.setState({ activeStep: 1 });
  assert.equal(await useProjectStore.getState().openImageForLabeling('b', imageB.file_path), false);
  assert.equal(useProjectStore.getState().activeStep, 1);
  assert.equal(useAnnotationStore.getState().currentImage.image_id, 'a');
  assert.equal(useAnnotationStore.getState().isDirty, true);
});

test('gallery opens the exact split image when image IDs are repeated', async () => {
  const trainImage = { image_id: 'shared', file_name: 'shared.jpg', file_path: '/train/shared.jpg' };
  const valImage = { image_id: 'shared', file_name: 'shared.jpg', file_path: '/val/shared.jpg' };
  datasetState.images = [trainImage, valImage];
  resetToDirtyImage();
  useAnnotationStore.setState({ isDirty: false });
  const { useProjectStore } = loadStore('../src/renderer/stores/useProjectStore.ts', {
    './useAnnotationStore': { useAnnotationStore },
  });
  useProjectStore.setState({ activeStep: 1 });
  try {
    assert.equal(await useProjectStore.getState().openImageForLabeling('shared', '/val/shared.jpg'), true);
    assert.equal(useAnnotationStore.getState().currentImage.file_path, '/val/shared.jpg');
    assert.equal(useAnnotationStore.getState().currentImageIndex, 1);
  } finally {
    datasetState.images = [imageA, imageB];
  }
});

test('direct image activation resolves duplicate IDs by path', async () => {
  resetToDirtyImage();
  const trainImage = { image_id: 'shared', file_name: 'shared.jpg', file_path: '/train/shared.jpg' };
  const valImage = { image_id: 'shared', file_name: 'shared.jpg', file_path: '/val/shared.jpg' };
  useAnnotationStore.setState({ images: [trainImage, valImage], isDirty: false });
  await useAnnotationStore.getState().setActiveImage(valImage);
  assert.equal(useAnnotationStore.getState().currentImageIndex, 1);
  assert.equal(useAnnotationStore.getState().currentImage.file_path, '/val/shared.jpg');
});

test('refreshed image page keeps the selected file when it is still present', async () => {
  resetToDirtyImage();
  useAnnotationStore.setState({ currentImageIndex: 1, currentImage: imageB, activeImage: imageB });
  const refreshed = [{ ...imageA }, { ...imageB }];
  assert.equal(await useAnnotationStore.getState().syncDatasetImages(refreshed), true);
  assert.equal(useAnnotationStore.getState().images, refreshed);
  assert.equal(useAnnotationStore.getState().currentImage.file_path, '/test/b.jpg');
  assert.equal(useAnnotationStore.getState().currentImageIndex, 1);
});

test('same image ID from a different folder does not reuse the old selection', async () => {
  resetToDirtyImage();
  useAnnotationStore.setState({ currentImageIndex: 1, currentImage: imageB, activeImage: imageB });
  const anotherFolder = [
    { image_id: 'other', file_name: 'first.jpg', file_path: '/other/first.jpg' },
    { image_id: 'b', file_name: 'b.jpg', file_path: '/other/b.jpg' },
  ];
  assert.equal(await useAnnotationStore.getState().syncDatasetImages(anotherFolder), true);
  assert.equal(useAnnotationStore.getState().currentImageIndex, 0);
  assert.equal(useAnnotationStore.getState().currentImage.file_path, '/other/first.jpg');
});

test('refresh cannot replace a selected image when its pending save fails', async () => {
  resetToDirtyImage();
  mockApi.annotations.save = async () => { throw new Error('disk full'); };
  assert.equal(await useAnnotationStore.getState().syncDatasetImages([{ ...imageB }]), false);
  assert.equal(useAnnotationStore.getState().currentImage.file_path, '/test/a.jpg');
  assert.equal(useAnnotationStore.getState().isDirty, true);
  assert.deepEqual(useAnnotationStore.getState().annotations, [annotation]);
});

test('auto selection failure is visible to the operator', async () => {
  resetToDirtyImage();
  mockApi.annotations.autoSelect = async () => { throw new Error('image unavailable'); };
  assert.equal(await useAnnotationStore.getState().triggerAutoSelect(4, 5), false);
  assert.match(useAnnotationStore.getState().autoSelectError, /image unavailable/);
  assert.deepEqual(useAnnotationStore.getState().annotations, [annotation]);
});

test('structured auto selection API error shows its specific detail', async () => {
  resetToDirtyImage();
  mockApi.annotations.autoSelect = async () => { throw { details: 'image not found', message_ko: '처리 오류' }; };
  assert.equal(await useAnnotationStore.getState().triggerAutoSelect(4, 5), false);
  assert.match(useAnnotationStore.getState().autoSelectError, /image not found/);
});

test('late auto selection response cannot annotate a different image', async () => {
  resetToDirtyImage();
  useAnnotationStore.setState({ annotations: [], isDirty: false });
  let finishAutoSelect;
  mockApi.annotations.autoSelect = () => new Promise((resolve) => { finishAutoSelect = resolve; });
  const selecting = useAnnotationStore.getState().triggerAutoSelect(4, 5);
  await useAnnotationStore.getState().selectImageByIndex(1);
  finishAutoSelect({ result: { polygon: [[1, 1], [2, 1], [2, 2]], bbox: [1, 1, 2, 2] } });
  assert.equal(await selecting, false);
  assert.equal(useAnnotationStore.getState().currentImage.image_id, 'b');
  assert.deepEqual(useAnnotationStore.getState().annotations, []);
});

test('auto selection retains edits made while the request was running', async () => {
  resetToDirtyImage();
  useAnnotationStore.setState({ annotations: [], isDirty: false });
  let finishAutoSelect;
  mockApi.annotations.autoSelect = () => new Promise((resolve) => { finishAutoSelect = resolve; });
  const selecting = useAnnotationStore.getState().triggerAutoSelect(4, 5);
  useAnnotationStore.getState().addAnnotation(annotation);
  finishAutoSelect({ result: { polygon: [[1, 1], [2, 1], [2, 2]], bbox: [1, 1, 2, 2] } });
  assert.equal(await selecting, true);
  assert.equal(useAnnotationStore.getState().annotations.length, 2);
  assert.equal(useAnnotationStore.getState().annotations[0].id, 'ann-a');
});

// A result can refer to an image hidden by the gallery's current page/filter.
test('error-image navigation loads exact source absent from visible gallery', async () => {
  resetToDirtyImage(); useAnnotationStore.setState({ isDirty:false });
  const { useProjectStore } = loadStore('../src/renderer/stores/useProjectStore.ts', { './useAnnotationStore':{useAnnotationStore} });
  useProjectStore.setState({ activeStep:4, projectDir:'/project', task:'detection' });
  assert.equal(await useProjectStore.getState().openImageForLabeling('hidden', '/test/hidden.png'),true);
  assert.equal(useAnnotationStore.getState().currentImage.file_path,'/test/hidden.png');
  assert.equal(useProjectStore.getState().activeStep,2);
});

test('pending exact-image navigation cannot activate an image after labelset switches', async () => {
  resetToDirtyImage(); useAnnotationStore.setState({ isDirty:false });
  const { useProjectStore } = loadStore('../src/renderer/stores/useProjectStore.ts', { './useAnnotationStore':{useAnnotationStore} });
  useProjectStore.setState({ activeStep:4, projectDir:'/project', task:'detection', project:{ active_labelset_id:'old' } });
  let finishMetadata;
  mockApi.imageMetadata = filePath => new Promise(resolve => { finishMetadata=() => resolve({ file_path:filePath,width:30,height:20 }); });
  const pending = useProjectStore.getState().openImageForLabeling('hidden','/test/hidden.png');
  useProjectStore.setState({ project:{ active_labelset_id:'new' } });
  finishMetadata();
  assert.equal(await pending,false);
  assert.equal(useProjectStore.getState().activeStep,4);
  assert.equal(useAnnotationStore.getState().currentImage.file_path,imageA.file_path);
});
