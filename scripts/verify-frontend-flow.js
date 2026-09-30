const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const esbuild = require('esbuild');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const root = path.resolve(__dirname, '..');
const bundled = esbuild.buildSync({
  stdin: {
    contents: `export { DatasetStudio } from './src/renderer/components/dataset/DatasetStudio';
export { EvaluationStudio } from './src/renderer/components/evaluation/EvaluationStudio';
export { LabelingStudio } from './src/renderer/components/labeling/LabelingStudio';
export { TrainingController } from './src/renderer/components/training/TrainingController';
export { WizardFooter } from './src/renderer/components/wizard/WizardFooter';
export { useDatasetStore } from './src/renderer/stores/useDatasetStore';
export { useEvaluationStore } from './src/renderer/stores/useEvaluationStore';
export { useAnnotationStore } from './src/renderer/stores/useAnnotationStore';
export { useTrainingStore } from './src/renderer/stores/useTrainingStore';
export { useProjectStore } from './src/renderer/stores/useProjectStore';`,
    resolveDir: root,
    sourcefile: 'frontend-flow-test-entry.tsx',
    loader: 'tsx',
  },
  bundle: true,
  write: false,
  platform: 'node',
  format: 'cjs',
  jsx: 'automatic',
  external: ['react', 'react-dom', 'react-dom/server', 'lucide-react'],
}).outputFiles[0].text;
const frontendModule = new Module(path.join(root, 'frontend-flow-test-bundle.cjs'), module);
frontendModule.filename = path.join(root, 'frontend-flow-test-bundle.cjs');
frontendModule.paths = Module._nodeModulePaths(root);
frontendModule._compile(bundled, frontendModule.filename);
const { DatasetStudio, EvaluationStudio, LabelingStudio, TrainingController, WizardFooter, useAnnotationStore, useDatasetStore, useEvaluationStore, useTrainingStore, useProjectStore } = frontendModule.exports;

function renderCurrent(Component) {
  const original = React.useSyncExternalStore;
  React.useSyncExternalStore = (_subscribe, getSnapshot) => getSnapshot();
  try {
    return renderToStaticMarkup(React.createElement(Component));
  } finally {
    React.useSyncExternalStore = original;
  }
}

function openingTagForText(html, text) {
  const index = html.indexOf(text);
  assert.ok(index >= 0, `Missing button text: ${text}`);
  const start = html.lastIndexOf('<button', index);
  assert.ok(start >= 0, `Missing button for: ${text}`);
  return html.slice(start, html.indexOf('>', start) + 1);
}

test('report controls explain why they are unavailable without an evaluated model', () => {
  useProjectStore.setState({ language: 'ko' });
  useEvaluationStore.setState({ jobId: null, isLoading: false, isExportingReport: false });
  const html = renderCurrent(EvaluationStudio);
  for (const label of ['HTML 리포트 내보내기', 'JSON']) {
    const tag = openingTagForText(html, label);
    assert.match(tag, /disabled=""/);
    assert.match(tag, /title="[^"]*모델[^"]*"/);
  }
});

test('report controls become available when evaluation has a model', () => {
  useProjectStore.setState({ language: 'ko' });
  useEvaluationStore.setState({
    jobId: 'job_valid', isLoading: false, isExportingReport: false,
    metrics: { accuracy: 0.8 },
    testPredictions: [{ image_id: 'sample', file_name: 'sample.jpg', file_path: '/sample.jpg', ground_truth: 'OK', predicted_class: 'OK', confidence: 0.9 }],
  });
  const html = renderCurrent(EvaluationStudio);
  for (const label of ['HTML 리포트 내보내기', 'JSON']) {
    assert.doesNotMatch(openingTagForText(html, label), /disabled=""/);
  }
});

test('a job ID without evaluation samples does not enable report export', () => {
  useProjectStore.setState({ language: 'ko' });
  useEvaluationStore.setState({
    jobId: 'job_incomplete', isLoading: false, isExportingReport: false,
    metrics: {}, testPredictions: [],
  });
  const html = renderCurrent(EvaluationStudio);
  for (const label of ['HTML 리포트 내보내기', 'JSON']) {
    const tag = openingTagForText(html, label);
    assert.match(tag, /disabled=""/);
    assert.match(tag, /title="[^"]*결과[^"]*"/);
  }
});

test('idle training points to the next stage as a preview', () => {
  useProjectStore.setState({ activeStep: 3, language: 'ko' });
  useTrainingStore.setState({ jobId: null, status: 'idle', isCurrentData: false });
  useEvaluationStore.setState({ jobId: null });
  const html = renderCurrent(WizardFooter);
  assert.match(html, /다음 단계 보기: [^<]*품질 평가/);
});

test('completed current training points to evaluation as the next work stage', () => {
  useProjectStore.setState({ activeStep: 3, language: 'ko' });
  useTrainingStore.setState({ jobId: 'job_valid', status: 'completed', isCurrentData: true });
  useEvaluationStore.setState({ jobId: null });
  const html = renderCurrent(WizardFooter);
  assert.match(html, /다음: [^<]*품질 평가/);
  assert.doesNotMatch(html, /다음 단계 보기: [^<]*품질 평가/);
});

test('flowchart without a completed model points to export as a preview', () => {
  useProjectStore.setState({ activeStep: 5, language: 'ko' });
  useTrainingStore.setState({ jobId: null, status: 'idle', isCurrentData: false });
  useEvaluationStore.setState({ jobId: null });
  const html = renderCurrent(WizardFooter);
  assert.match(html, /다음 단계 보기: [^<]*추론/);
});

test('failed annotation read shows a retry action and blocks the canvas', () => {
  useAnnotationStore.setState({
    currentImage: { image_id: 'sample', file_name: 'sample.jpg', file_path: '/sample.jpg' },
    annotationLoadStatus: 'error', annotationLoadError: 'disk unavailable',
    annotations: [], isDirty: false,
  });
  const html = renderCurrent(LabelingStudio);
  assert.match(html, /role="alert"[^>]*>[^]*disk unavailable/);
  assert.match(html, /다시 시도/);
  assert.match(html, /기존 라벨을 불러오지 못해 편집을 중지했습니다/);
  assert.match(html, /<button[^>]*disabled=""[^>]*title="기존 라벨 조회가 완료되어야 저장할 수 있습니다\."/);
});

test('unsupported task split explains why it is unavailable', () => {
  useProjectStore.setState({ task: 'detection', language: 'ko' });
  useDatasetStore.setState({ totalImages: 10, sourceImages: 10, splitError: null, splitSupported: null, splitUnavailableReason: null });
  const html = renderCurrent(DatasetStudio);
  assert.match(openingTagForText(html, '3-Way 분할 적용'), /disabled=""/);
  assert.match(html, /원본 train\/val\/test 폴더 구성을 사용하세요/);
});

test('structured segmentation split capability disables the dataset action', () => {
  useProjectStore.setState({ task: 'segmentation', language: 'ko' });
  useDatasetStore.setState({ totalImages: 10, sourceImages: 10, splitError: null,
    splitSupported: false, splitUnavailableReason: 'images/masks 구성은 재분할을 지원하지 않습니다.' });
  const html = renderCurrent(DatasetStudio);
  assert.match(openingTagForText(html, '3-Way 분할 적용'), /disabled=""/);
  assert.match(html, /images\/masks 구성은 재분할을 지원하지 않습니다/);
});

test('dataset split error is visible beside the action', () => {
  useProjectStore.setState({ task: 'segmentation', language: 'ko' });
  useDatasetStore.setState({ totalImages: 10, sourceImages: 10, splitSupported: true, splitUnavailableReason: null,
    splitError: '일반 이미지·마스크 세그멘테이션의 재분할은 지원되지 않습니다.' });
  const html = renderCurrent(DatasetStudio);
  assert.match(html, /role="alert"[^>]*>[^]*일반 이미지·마스크 세그멘테이션의 재분할은 지원되지 않습니다/);
});

test('training stage shows the split error from its one click action', () => {
  useProjectStore.setState({ task: 'segmentation', language: 'ko' });
  useDatasetStore.setState({ totalImages: 10, split: { train: 0, val: 0, test: 0 }, splitSupported: true, splitUnavailableReason: null,
    splitError: '일반 이미지·마스크 세그멘테이션의 재분할은 지원되지 않습니다.' });
  useTrainingStore.setState({ isTraining: false, isRecoveringTraining: false, status: 'idle' });
  const html = renderCurrent(TrainingController);
  assert.match(html, /role="alert"[^>]*>[^]*일반 이미지·마스크 세그멘테이션의 재분할은 지원되지 않습니다/);
});

test('training stage directs unsupported tasks to source partitions', () => {
  useProjectStore.setState({ task: 'detection', language: 'ko' });
  useDatasetStore.setState({ totalImages: 10, split: { train: 0, val: 0, test: 0 }, splitError: null, splitSupported: null, splitUnavailableReason: null });
  const html = renderCurrent(TrainingController);
  assert.match(html, /원본 train\/val\/test 폴더/);
  assert.doesNotMatch(html, /80:20 기본 검증 분할 즉시 적용/);
});

test('structured segmentation split capability removes the training quick split action', () => {
  useProjectStore.setState({ task: 'segmentation', language: 'ko' });
  useDatasetStore.setState({ totalImages: 10, split: { train: 0, val: 0, test: 0 }, splitError: null,
    splitSupported: false, splitUnavailableReason: 'images/masks 구성은 재분할을 지원하지 않습니다.' });
  const html = renderCurrent(TrainingController);
  assert.match(html, /images\/masks 구성은 재분할을 지원하지 않습니다/);
  assert.doesNotMatch(html, /80:20 기본 검증 분할 즉시 적용/);
});

test('class distribution uses the applied pre-split counts rather than the slider preview', () => {
  useProjectStore.setState({ task: 'classification', language: 'ko' });
  useDatasetStore.setState({
    totalImages: 8, sourceImages: 8, classes: { B: 4, C: 4 },
    split: { train: 4, val: 2, test: 2 }, trainRatio: 0.8,
    classSplitCounts: { B: { train: 2, val: 1, test: 1 }, C: { train: 2, val: 1, test: 1 } },
    images: [], totalImagesCount: 8, splitSupported: true, splitUnavailableReason: null,
    activeSplitFilter: 'all', activeClassFilter: null, activeLabelFilter: 'all',
  });
  const text = renderCurrent(DatasetStudio).replace(/<[^>]*>/g, '');
  assert.equal((text.match(/T: 2V: 1Test: 1/g) || []).length, 2);
  assert.doesNotMatch(text, /T: 3V: 0Test: 1/);
});

test('unapplied class split counts are clearly described as a preview', () => {
  useProjectStore.setState({ task: 'classification', language: 'ko' });
  useDatasetStore.setState({
    totalImages: 8, classes: { B: 4, C: 4 }, split: { train: 0, val: 0, test: 0 },
    classSplitCounts: null, trainRatio: 0.8, splitSupported: true,
  });
  const text = renderCurrent(DatasetStudio).replace(/<[^>]*>/g, '');
  assert.match(text, /분할 예상/);
});

test('an applied split without its class summary never shows ratio estimates as saved counts', () => {
  useProjectStore.setState({ task: 'classification', language: 'ko' });
  useDatasetStore.setState({
    totalImages: 8, classes: { B: 4, C: 4 }, split: { train: 4, val: 2, test: 2 },
    classSplitCounts: null, trainRatio: 0.8, splitSupported: true,
  });
  const text = renderCurrent(DatasetStudio).replace(/<[^>]*>/g, '');
  assert.match(text, /적용된 분할 집계 확인 중/);
  assert.doesNotMatch(text, /T: 3V: 0Test: 1/);
});
