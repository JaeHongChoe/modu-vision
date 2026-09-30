/** Browser store regression: imported source state and mixed flow survive a switch. */
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const esbuild = require('esbuild');

const root = path.resolve(__dirname, '..');
const projectA = {
  id: 'a', name: 'A', task: 'segmentation', project_dir: '/tmp/project-a',
  source_dataset_dir: '/tmp/source-a', dataset_dir: '/tmp/project-a/dataset',
  models_dir: '/tmp/project-a/models', reports_dir: '/tmp/project-a/reports',
  annotations_dir: '/tmp/project-a/annotations', description: '', active_preset: 'fast',
  created_at: '', updated_at: '',
};
const projectB = { ...projectA, id: 'b', name: 'B', project_dir: '/tmp/project-b', source_dataset_dir: null };
const calls = { sourceUpdates: [], flowSaves: [], draftSaves: [], verified: [], opens: 0, annotationSaves: 0, imageResets: 0 };
let backendProject = projectA;

globalThis.__projectSwitchQa = {
  api: {
    dataset: {
      import: async () => ({ total_images: 1, source_images: 1, unlabeled_images: 0, classes: {}, split: { train: 1, val: 0 } }),
      getImages: async () => ({ total: 1, items: [] }),
    },
    project: {
      update: async (values) => {
        calls.sourceUpdates.push(values);
        backendProject = { ...backendProject, ...values };
        return backendProject;
      },
      open: async (directory) => {
        calls.opens += 1;
        backendProject = directory === projectA.project_dir ? projectA : projectB;
        return backendProject;
      },
      getCurrent: async () => backendProject,
      list: async () => ({ projects: [] }),
    },
    flowchart: {
      verifyModels: async (values) => { calls.verified.push(values); return { verified_job_ids: values.models.map((item) => item.job_id) }; },
    },
  },
  training: { isTraining: false, invalidateForDataChange() {} },
  evaluation: { invalidateForDataChange() {} },
  flow: {
    pipeline: null, pipelineDirty: false, isRunning: false, isLoading: false, isSaving: false,
    errorMessage: null, invalidateForDataChange() {},
    draftSuccess: true,
    async saveDraft() {
      calls.draftSaves.push(this.pipeline);
      if (!this.draftSuccess) return false;
      this.pipelineDirty = false;
      this.errorMessage = null;
      return true;
    },
    async savePipeline(_, recipe, source) {
      calls.flowSaves.push({ recipe, source });
      this.pipelineDirty = false;
      this.errorMessage = null;
    },
  },
  annotation: { isDirty: false, async setImages() { calls.imageResets += 1; return true; }, async saveAnnotations() { calls.annotationSaves += 1; this.isDirty = false; return true; }, setTask() {} },
  inspection: { isRunning: false },
  modelAssist: { activeOperations: 0 },
};

const mockModules = {
  '../services/api': 'export const api = globalThis.__projectSwitchQa.api; export const setCachedPort = () => {};',
  './useAnnotationStore': 'export const useAnnotationStore = { getState: () => globalThis.__projectSwitchQa.annotation };',
  './useTrainingStore': 'export const useTrainingStore = { getState: () => globalThis.__projectSwitchQa.training };',
  './useEvaluationStore': 'export const useEvaluationStore = { getState: () => globalThis.__projectSwitchQa.evaluation };',
  './useFlowchartStore': 'export const useFlowchartStore = { getState: () => globalThis.__projectSwitchQa.flow };',
  './useInspectionRunStore': 'export const useInspectionRunStore = { getState: () => globalThis.__projectSwitchQa.inspection };',
  './useModelAssistRunStore': 'export const useModelAssistRunStore = { getState: () => globalThis.__projectSwitchQa.modelAssist };',
};

async function main() {
  const result = await esbuild.build({
    stdin: {
      contents: `export { useProjectStore } from './src/renderer/stores/useProjectStore';\nexport { useDatasetStore } from './src/renderer/stores/useDatasetStore';`,
      resolveDir: root, sourcefile: 'project-switch-qa.ts', loader: 'ts',
    },
    bundle: true, platform: 'node', format: 'cjs', write: false,
    plugins: [{
      name: 'store-test-mocks',
      setup(build) {
        build.onResolve({ filter: /^(\.\/use(Annotation|Training|Evaluation|Flowchart|InspectionRun|ModelAssistRun)Store|\.\.\/services\/api)$/ },
          (args) => ({ path: args.path, namespace: 'qa-mock' }));
        build.onLoad({ filter: /.*/, namespace: 'qa-mock' },
          (args) => ({ contents: mockModules[args.path], loader: 'js' }));
      },
    }],
  });
  const bundled = new Module(path.join(root, 'scripts/.project-switch-qa.cjs'), module);
  bundled.filename = path.join(root, 'scripts/.project-switch-qa.cjs');
  bundled.paths = Module._nodeModulePaths(root);
  bundled._compile(result.outputFiles[0].text, bundled.filename);
  const { useProjectStore, useDatasetStore } = bundled.exports;

  useProjectStore.setState({ project: projectA, projectName: projectA.name, projectDir: projectA.project_dir, task: projectA.task });
  await useDatasetStore.getState().importFolder('/tmp/source-b', 'segmentation');
  assert.equal(useProjectStore.getState().project.source_dataset_dir, '/tmp/source-b');
  assert.equal(useDatasetStore.getState().isLoading, false);

  // Simulate an older renderer that still holds the previous source path.
  useProjectStore.setState({ project: projectA });
  globalThis.__projectSwitchQa.flow.pipeline = {
    id: 'mixed', name: 'Mixed', edges: [], nodes: [
      { id: 's', data: { node_type: 'inspection', task: 'segmentation', model_job_id: 'job_seg' } },
      { id: 'c', data: { node_type: 'inspection', task: 'classification', model_job_id: 'job_cls' } },
    ],
  };
  globalThis.__projectSwitchQa.flow.pipelineDirty = true;
  const switched = await useProjectStore.getState().openProject(projectB.project_dir);
  assert.equal(switched, true, useProjectStore.getState().projectError || 'switch rejected');
  assert.equal(calls.draftSaves.at(-1).id, 'mixed');
  assert.equal(calls.flowSaves.length, 0, 'switching must not create an executable version');
  assert.equal(calls.sourceUpdates.length, 2);

  // An in-flight proposal must finish before the backend project changes.
  const opensBeforeAssist = calls.opens;
  globalThis.__projectSwitchQa.modelAssist.activeOperations = 1;
  assert.equal(await useProjectStore.getState().openProject(projectA.project_dir), false);
  assert.equal(calls.opens, opensBeforeAssist);
  globalThis.__projectSwitchQa.modelAssist.activeOperations = 0;

  // The daemon may come back with another active project. Keep unsaved labels
  // in the renderer rather than autosaving them into that backend workspace.
  backendProject = projectA;
  globalThis.__projectSwitchQa.annotation.isDirty = true;
  const resetsBeforeSync = calls.imageResets;
  const savesBeforeSync = calls.annotationSaves;
  await useProjectStore.getState().syncCurrentProject();
  assert.equal(useProjectStore.getState().project.id, projectB.id);
  assert.match(useProjectStore.getState().projectError, /저장하지 않은 라벨/);
  assert.equal(calls.imageResets, resetsBeforeSync);
  assert.equal(calls.annotationSaves, savesBeforeSync);
  assert.equal(await useProjectStore.getState().openProject(projectA.project_dir), true);
  assert.equal(calls.annotationSaves, savesBeforeSync + 1);
  assert.equal(useProjectStore.getState().project.id, projectA.id);

  // An enhancement model plus a still-empty inspection is a valid editable
  // draft. Switching must preserve it without weakening executable saves.
  const draft = { id: 'enhancement-draft', edges: [], nodes: [
    { id: 'enhance', data: { node_type: 'preprocess', params: { operation: 'enhancement' }, model_job_id: 'job_enhance' } },
    { id: 'inspect', data: { node_type: 'inspection', task: 'segmentation', model_job_id: null } },
  ] };
  const flow = globalThis.__projectSwitchQa.flow;
  flow.pipeline = draft; flow.pipelineDirty = true;
  useDatasetStore.setState({ folderPath: projectA.source_dataset_dir, datasetKey: `${projectA.source_dataset_dir}\0${projectA.task}`, importError: null });
  flow.draftSuccess = false;
  const opensBeforeDraft = calls.opens;
  assert.equal(await useProjectStore.getState().openProject(projectB.project_dir), false);
  assert.equal(calls.opens, opensBeforeDraft, 'failed durable save must block the switch');
  assert.equal(flow.pipelineDirty, true);
  flow.draftSuccess = true;
  assert.equal(await useProjectStore.getState().openProject(projectB.project_dir), true, useProjectStore.getState().projectError);
  assert.equal(calls.draftSaves.at(-1), draft);
  assert.equal(calls.flowSaves.length, 0);
  console.log('Project switch regression passed: source sync, mixed flow save, async assist guard, and dirty restart recovery.');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
