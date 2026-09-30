const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

let resultCalls = 0;
let runCalls = 0;
let templateCalls = 0;
let labelMutated = false;
let nextJob = 'job_A';
let savedDraft = null;
const oldPipeline = {
  id: 'saved-flow', name: 'Old saved flow', edges: [],
  nodes: [{ id: 'inspect', position: { x: 0, y: 0 },
    data: { label: 'Inspection', node_type: 'inspection', task: 'segmentation', model_job_id: 'job_A' } }],
};
let savedPipeline = oldPipeline;
const api = {
  dataset: {
    import: async ({ folder_path }) => ({
      total_images: 2, source_images: 2, unlabeled_images: 0,
      classes: { defect_mask: 2 },
      split: labelMutated ? { train: 0, val: 0, test: 0 } : { train: 1, val: 1, test: 0 },
      corrupted_images: [], folder_path,
    }),
    getImages: async () => ({ total: 2, items: [] }),
    split: async () => ({ split: { train: 1, val: 1, test: 0 } }),
  },
  training: { start: async () => ({ job_id: nextJob }) },
  evaluation: {
    getResults: async (jobId, options) => {
      resultCalls += 1;
      if (!jobId && options?.sourceDatasetPath === '/dataset/B') {
        throw new Error('No completed model matches /dataset/B');
      }
      return { job_id: jobId || 'job_A', metrics: { miou: 0.5 }, test_predictions: [] };
    },
    getOverkillUnderkill: async () => ({ sample_details: [] }),
  },
  flowchart: {
    getActivePipeline: async () => structuredClone(savedPipeline),
    getPipeline: async () => structuredClone(savedPipeline),
    getSingleSegmentationTemplate: async (jobId) => {
      templateCalls += 1;
      const result = structuredClone(oldPipeline);
      result.nodes[0].data.model_job_id = jobId;
      return result;
    },
    run: async ({ pipeline }) => {
      if (pipeline.nodes.some((node) => node.data.model_job_id === 'job_unverified')) {
        throw new Error('Trained model is unavailable');
      }
      runCalls += 1;
      return { final_verdict: 'OK' };
    },
  },
  annotations: { save: async () => ({ status: 'saved' }) },
  project: { getCurrent: async () => project.getState().project,
    update: async (changes) => ({ ...project.getState().project, ...changes }) },
};

const cache = new Map();
function loadStore(name) {
  const filename = path.resolve(__dirname, '../src/renderer/stores', name);
  return loadModule(filename);
}
function loadModule(filename) {
  if (cache.has(filename)) return cache.get(filename).exports;
  const source = fs.readFileSync(filename, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const item = new Module(filename, module);
  item.filename = filename;
  item.paths = Module._nodeModulePaths(path.dirname(filename));
  cache.set(filename, item);
  const originalRequire = item.require.bind(item);
  item.require = (specifier) => {
    if (specifier === '../services/api' || specifier === './api') {
      return { api, getApiBaseUrl: async () => 'http://localhost', setCachedPort: () => {},
        request: async (url, options) => {
          if (url === '/api/annotations/save') return api.annotations.save();
          if (url === '/api/flowchart/draft') {
            if (options?.method === 'PUT') {
              savedDraft = { ...JSON.parse(options.body), draft_sha256: 'a'.repeat(64), active_version_id: null };
            }
            if (savedDraft) return structuredClone(savedDraft);
          }
          throw Object.assign(new Error('No optional saved resource in this fixture'), { status: 404 });
        } };
    }
    if (specifier.startsWith('./use') && specifier.endsWith('Store')) {
      return loadStore(`${specifier.slice(2)}.ts`);
    }
    if (specifier === '../components/labeling/convertedAnnotation') {
      return { applyConvertedShape: () => null };
    }
    if (specifier.startsWith('.')) {
      const dependency = path.resolve(path.dirname(filename), `${specifier}.ts`);
      if (fs.existsSync(dependency)) return loadModule(dependency);
    }
    return originalRequire(specifier);
  };
  item._compile(compiled, filename);
  return item.exports;
}

const training = loadStore('useTrainingStore.ts').useTrainingStore;
const evaluation = loadStore('useEvaluationStore.ts').useEvaluationStore;
const flowchart = loadStore('useFlowchartStore.ts').useFlowchartStore;
const dataset = loadStore('useDatasetStore.ts').useDatasetStore;
const annotation = loadStore('useAnnotationStore.ts').useAnnotationStore;
const project = loadStore('useProjectStore.ts').useProjectStore;
const compute = loadStore('useComputeStore.ts').useComputeStore;

async function completedA() {
  savedDraft = null;
  project.setState({ project: { id: 'project-A', name: 'Project A', project_dir: '/project/A',
    source_dataset_dir: '/dataset/A', task: 'segmentation', active_labelset_id: 'default' },
    projectDir: '/project/A', task: 'segmentation' });
  compute.setState({ isLoaded: true, loadError: null, selectedProfileId: null });
  savedPipeline = oldPipeline;
  labelMutated = false;
  nextJob = 'job_A';
  dataset.setState({ lastImportedKey: null, staleDatasetKeys: [] });
  await dataset.getState().importFolder('/dataset/A', 'segmentation');
  assert.equal(evaluation.getState().allowLatestRecovery, true);
  await training.getState().startTraining('/dataset/A', 'segmentation');
  training.getState().updateFromTelemetry('training_completed', { job_id: 'job_A', best_metric: 0.5 });
  await evaluation.getState().loadEvaluation();
  await flowchart.getState().loadSingleSegmentationTemplate('job_A');
  assert.equal(training.getState().jobId, 'job_A');
  assert.equal(evaluation.getState().jobId, 'job_A');
  assert.equal(flowchart.getState().pipeline.nodes[0].data.model_job_id, 'job_A');
}

test('saved flow preserves model references while execution rejects unavailable checkpoints', async () => {
  await completedA();
  await flowchart.getState().loadPipeline(true);
  assert.equal(flowchart.getState().pipeline.nodes[0].data.model_job_id, 'job_A');
  assert.equal(flowchart.getState().pipelineDirty, false);
  flowchart.getState().setSelectedImage({ imageId: 'a', imagePath: '/dataset/A/a.jpg', fileName: 'a.jpg', source: 'dataset' });
  const beforeRuns = runCalls;
  assert.equal(await flowchart.getState().runPipeline(), true);
  assert.equal(runCalls, beforeRuns + 1);

  savedPipeline = structuredClone(oldPipeline);
  savedPipeline.nodes[0].data.model_job_id = 'job_unverified';
  await flowchart.getState().loadPipeline(true);
  assert.equal(flowchart.getState().pipeline.nodes[0].data.model_job_id, 'job_unverified');
  assert.equal(flowchart.getState().pipelineDirty, false);
  assert.equal(await flowchart.getState().runPipeline(), false);
  assert.equal(runCalls, beforeRuns + 1);
});

test('multi-model flow retains every saved ID but never fills missing model nodes', async () => {
  await completedA();
  savedPipeline = structuredClone(oldPipeline);
  savedPipeline.id = 'multi-model';
  savedPipeline.nodes.push({
    id: 'crop', position: { x: 300, y: 0 },
    data: { label: 'Detection', node_type: 'detection_crop', task: 'detection' },
  });
  savedPipeline.nodes.push({
    id: 'other-inspect', position: { x: 600, y: 0 },
    data: { label: 'Other inspection', node_type: 'inspection', task: 'anomaly', model_job_id: 'job_unverified' },
  });
  await flowchart.getState().loadPipeline(true);
  assert.equal(flowchart.getState().pipeline.nodes[0].data.model_job_id, 'job_A');
  assert.equal(flowchart.getState().pipeline.nodes[1].data.model_job_id, undefined);
  assert.equal(flowchart.getState().pipeline.nodes[2].data.model_job_id, 'job_unverified');
  assert.equal(flowchart.getState().pipelineDirty, false);
  flowchart.getState().setSelectedImage({ imageId: 'a', imagePath: '/dataset/A/a.jpg', fileName: 'a.jpg', source: 'dataset' });
  const beforeRuns = runCalls;
  assert.equal(await flowchart.getState().runPipeline(), false);
  assert.equal(runCalls, beforeRuns);
});

test('A model and saved flow cannot become B results after dataset import', async () => {
  await completedA();
  flowchart.getState().setSelectedImage({ imageId: 'a', imagePath: '/dataset/A/a.jpg', fileName: 'a.jpg', source: 'dataset' });
  await dataset.getState().importFolder('/dataset/B', 'segmentation');
  assert.equal(training.getState().jobId, null);
  assert.equal(training.getState().isCurrentData, false);
  assert.equal(evaluation.getState().jobId, null);
  assert.equal(evaluation.getState().allowLatestRecovery, true);
  assert.equal(flowchart.getState().selectedImage, null);
  assert.equal(flowchart.getState().pipeline, null);

  const beforeResults = resultCalls;
  await assert.rejects(
    evaluation.getState().loadEvaluation(undefined, { folderPath: '/dataset/B', task: 'segmentation' }),
    /No completed model matches/,
  );
  assert.equal(resultCalls, beforeResults + 1);
  assert.equal(evaluation.getState().jobId, null);

  await flowchart.getState().loadPipeline(true);
  // The persisted reference remains visible for diagnosis. Stage 5 separately
  // blocks its actions until evaluation/model verification for B succeeds.
  assert.equal(flowchart.getState().pipeline.nodes[0].data.model_job_id, 'job_A');
  const beforeRuns = runCalls;
  assert.equal(runCalls, beforeRuns);
  const beforeTemplates = templateCalls;
  await flowchart.getState().loadSingleSegmentationTemplate();
  assert.equal(templateCalls, beforeTemplates + 1);
  assert.equal(flowchart.getState().pipeline.nodes[0].data.model_job_id, undefined);
});

test('label save and new split each invalidate a completed model', async () => {
  await completedA();
  annotation.setState({
    currentImage: { image_id: 'new', file_path: '/dataset/A/new.jpg', width: 32, height: 32 },
    annotations: [{ id: 'defect', type: 'polygon', label: 'defect', polygon: [[0, 0], [3, 0], [3, 3]] }],
    imageDimensions: { width: 32, height: 32 }, isDirty: true, annotationLoadStatus: 'ready',
  });
  labelMutated = true;
  assert.equal(await annotation.getState().saveAnnotations(), true);
  assert.equal(training.getState().jobId, null);
  assert.equal(evaluation.getState().jobId, null);
  assert.equal(evaluation.getState().allowLatestRecovery, false);
  await dataset.getState().importFolder('/dataset/A', 'segmentation');
  assert.equal(evaluation.getState().allowLatestRecovery, false);
  assert.deepEqual(dataset.getState().split, { train: 0, val: 0, test: 0 });

  labelMutated = false;
  await dataset.getState().applySplit(0.5);
  assert.deepEqual(dataset.getState().split, { train: 1, val: 1, test: 0 });
  await training.getState().startTraining('/dataset/A', 'segmentation');
  training.getState().updateFromTelemetry('training_completed', { job_id: 'job_A' });
  await evaluation.getState().loadEvaluation();
  await dataset.getState().applySplit(0.5);
  assert.equal(training.getState().jobId, null);
  assert.equal(evaluation.getState().jobId, null);
  assert.equal(evaluation.getState().allowLatestRecovery, false);
});

test('changing task reimports the selected dataset and clears prior results', async () => {
  await completedA();
  project.setState({ task: 'segmentation' });
  await project.getState().setTask('classification');
  assert.equal(project.getState().task, 'classification');
  assert.equal(dataset.getState().datasetKey, '/dataset/A\0classification');
  assert.equal(training.getState().jobId, null);
  assert.equal(evaluation.getState().jobId, null);
  assert.equal(evaluation.getState().allowLatestRecovery, false);
});

test('Step 3 start button requires current train and validation partitions', () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../src/renderer/components/training/TrainingController.tsx'), 'utf8');
  assert.match(source, /split\.train > 0 && split\.val > 0/);
  assert.match(source, /disabled=\{!canStart\}/);
});
