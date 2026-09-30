/** Existing evaluation models remain discoverable after switching workspace/label context. */
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const esbuild = require('esbuild');

const root = path.resolve(__dirname, '..');
const projectA = {
  id: 'a', name: 'A', task: 'segmentation', project_dir: '/tmp/recovery-project-a',
  source_dataset_dir: '/tmp/shared-recovery-source', active_labelset_id: 'default',
};
const projectB = { ...projectA, id: 'b', name: 'B', project_dir: '/tmp/recovery-project-b' };
let backendProject = projectA;
const resultsCalls = [];
const qa = {
  api: {
    dataset: {
      import: async () => ({ total_images: 1, split: { train: 1, val: 0 }, classes: {} }),
      getImages: async () => ({ total: 1, items: [] }),
    },
    project: {
      getCurrent: async () => backendProject,
      update: async (values) => (backendProject = { ...backendProject, ...values }),
      activateLabelset: async (id) => (backendProject = { ...backendProject, active_labelset_id: id }),
      open: async (directory) => (backendProject = directory === projectA.project_dir ? projectA : projectB),
      list: async () => ({ projects: [] }),
    },
    evaluation: {
      getResults: async (jobId, source) => {
        resultsCalls.push({ jobId, source, projectId: backendProject.id, labelset: backendProject.active_labelset_id });
        return { job_id: `${backendProject.id}-${backendProject.active_labelset_id}`, metrics: {}, test_predictions: [] };
      },
      getOverkillUnderkill: async () => ({ sample_details: [] }),
    },
  },
  training: { isTraining: false, isCurrentData: false, status: 'idle', jobId: null, invalidateForDataChange() {} },
  flow: { pipelineDirty: false, isRunning: false, isLoading: false, isSaving: false, invalidateForDataChange() {} },
  annotation: { isDirty: false, async setImages() { return true; }, setTask() {} },
  inspection: { isRunning: false },
  modelAssist: { activeOperations: 0 },
};
globalThis.__projectLabelsetRecoveryQa = qa;

const mocks = {
  '../services/api': 'export const api = globalThis.__projectLabelsetRecoveryQa.api; export const setCachedPort = () => {}; export const getApiBaseUrl = async () => "http://localhost";',
  './useAnnotationStore': 'export const useAnnotationStore = { getState: () => globalThis.__projectLabelsetRecoveryQa.annotation };',
  './useTrainingStore': 'export const useTrainingStore = { getState: () => globalThis.__projectLabelsetRecoveryQa.training };',
  './useFlowchartStore': 'export const useFlowchartStore = { getState: () => globalThis.__projectLabelsetRecoveryQa.flow };',
  './useInspectionRunStore': 'export const useInspectionRunStore = { getState: () => globalThis.__projectLabelsetRecoveryQa.inspection };',
  './useModelAssistRunStore': 'export const useModelAssistRunStore = { getState: () => globalThis.__projectLabelsetRecoveryQa.modelAssist };',
};

async function main() {
  const result = await esbuild.build({
    stdin: {
      contents: `export { useProjectStore } from './src/renderer/stores/useProjectStore';\nexport { useDatasetStore } from './src/renderer/stores/useDatasetStore';\nexport { useEvaluationStore } from './src/renderer/stores/useEvaluationStore';`,
      resolveDir: root, sourcefile: 'project-labelset-recovery-qa.ts', loader: 'ts',
    },
    bundle: true, platform: 'node', format: 'cjs', write: false,
    plugins: [{ name: 'recovery-test-mocks', setup(build) {
      build.onResolve({ filter: /^(\.\/use(Annotation|Training|Flowchart|InspectionRun|ModelAssistRun)Store|\.\.\/services\/api)$/ },
        (args) => ({ path: args.path, namespace: 'qa-mock' }));
      build.onLoad({ filter: /.*/, namespace: 'qa-mock' },
        (args) => ({ contents: mocks[args.path], loader: 'js' }));
    } }],
  });
  const bundled = new Module(path.join(root, 'scripts/.project-labelset-recovery-qa.cjs'), module);
  bundled.filename = path.join(root, 'scripts/.project-labelset-recovery-qa.cjs');
  bundled.paths = Module._nodeModulePaths(root);
  bundled._compile(result.outputFiles[0].text, bundled.filename);
  const { useProjectStore: projects, useDatasetStore: dataset, useEvaluationStore: evaluation } = bundled.exports;
  projects.setState({ project: projectA, projectDir: projectA.project_dir, task: projectA.task });

  const recover = async (expectedJob) => {
    const before = resultsCalls.length;
    await evaluation.getState().loadEvaluation(undefined, { folderPath: projectA.source_dataset_dir, task: projectA.task });
    assert.equal(resultsCalls.length, before + 1, evaluation.getState().errorMessage || 'source-filtered model recovery was blocked');
    assert.deepEqual(resultsCalls.at(-1).source, { sourceDatasetPath: projectA.source_dataset_dir, sourceTask: projectA.task });
    assert.equal(evaluation.getState().jobId, expectedJob);
  };
  await dataset.getState().importFolder(projectA.source_dataset_dir, projectA.task);
  await recover('a-default');

  assert.equal(await projects.getState().activateLabelset('ls_111111111111'), true);
  await recover('a-ls_111111111111');
  assert.equal(await projects.getState().activateLabelset('default'), true);
  await recover('a-default');

  assert.equal(await projects.getState().openProject(projectB.project_dir), true);
  await dataset.getState().ensureImported(projectB.task);
  await recover('b-default');
  assert.equal(await projects.getState().openProject(projectA.project_dir), true);
  await dataset.getState().ensureImported(projectA.task);
  await recover('a-default');

  // Editing labels in the current context still invalidates frontend recovery.
  await dataset.getState().annotationsChanged();
  const beforeEditRecovery = resultsCalls.length;
  await evaluation.getState().loadEvaluation(undefined, { folderPath: projectA.source_dataset_dir, task: projectA.task });
  assert.equal(resultsCalls.length, beforeEditRecovery);
  assert.equal(evaluation.getState().jobId, null);
  assert.match(evaluation.getState().errorMessage, /현재 데이터/);
  console.log('Project/labelset recovery regression passed: 5 source-filtered recoveries and current-label edit invalidation.');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
