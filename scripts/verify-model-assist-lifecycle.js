const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

function mount() {
  const slots = [], effects = [];
  let cursor = 0;
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
    useRef(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { current: initial }; return slots[i]; },
    useMemo(fn) { cursor++; return fn(); },
    useEffect(fn, deps) { const i = cursor++; const old = slots[i]; if (!old || deps.some((d, n) => d !== old.deps[n])) { old?.cleanup?.(); const entry = { deps }; slots[i] = entry; effects.push(() => { entry.cleanup = fn(); }); } },
  };
  const image = { image_id: 'same', file_path: '/source/same.jpg', file_name: 'same.jpg' };
  const project = { projectDir: '/project', project: { active_labelset_id: 'default', dataset: { task: 'detection' } } };
  const annotation = { currentImage: image, isDirty: false, annotationLoadStatus: 'ready', reviewerName: 'Reviewer', setReviewerName() {}, loadAnnotationsForCurrent: async () => {} };
  const dataset = { images: [image], hasSelectedFolder: true, annotationsChanged: async () => { annotation.currentImage = { ...annotation.currentImage }; } };
  let operations = 0, generations = 0;
  const pending = { id: 'proposal', image_path: image.file_path, image_id: 'same', created_at: new Date().toISOString(), status: 'pending', accepted_candidate_ids: [], candidates: [{ id: 'candidate', confidence: .9, annotation: { type: 'bbox', label: 'scratch', bbox: [1, 2, 5, 6] } }] };
  const store = state => Object.assign(selector => selector(state), { getState: () => state });
  const workflow = { review: async () => ({ ...pending, status: 'accepted', accepted_candidate_ids: ['candidate'], backup_version_id: 'backup' }), generateModel: async () => { generations++; return { ...pending, id: 'next' }; } };
  const suggestionsApi = { models: async () => ({ models: [{ job_id: 'model' }] }), list: async () => ({ suggestions: [pending] }) };
  const stubs = {
    react, 'lucide-react': new Proxy({}, { get: (_, key) => String(key) }),
    '../../stores/useAnnotationStore': { useAnnotationStore: store(annotation) },
    '../../stores/useProjectStore': { useProjectStore: store(project) },
    '../../stores/useDatasetStore': { useDatasetStore: store(dataset) },
    '../../stores/useModelAssistRunStore': { useModelAssistRunStore: store({ begin: () => operations++, end: () => operations-- }) },
    '../../services/api': { resolveApiUrl: x => x, api: { labelSuggestions: suggestionsApi } },
    '../../services/datasetWorkflow': { datasetWorkflow: workflow },
    './BulkLabelAssist': { BulkLabelAssist: 'BulkLabelAssist' },
    './CandidateProviderControls': { CandidateProviderControls: 'CandidateProviderControls' },
    './LabelAssistDeviceSizes': { LabelAssistDeviceSizes: 'LabelAssistDeviceSizes' },
  };
  const filename = path.resolve(__dirname, '../src/renderer/components/labeling/ModelAssistPanel.tsx');
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true } }).outputText;
  const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  mod.require = specifier => Object.hasOwn(stubs, specifier) ? stubs[specifier] : require(specifier);
  mod._compile(compiled, filename);
  const render = () => { cursor = 0; const tree = mod.exports.ModelAssistPanel(); effects.splice(0).forEach(fn => fn()); return tree; };
  const flatten = tree => !tree || typeof tree !== 'object' ? [] : [tree, ...(tree.props?.children || []).flat(Infinity).flatMap(flatten)];
  const text = tree => (tree?.props?.children || []).flat(Infinity).map(x => typeof x === 'string' ? x : x && typeof x === 'object' ? text(x) : '').join('');
  const button = (tree, label) => flatten(tree).find(x => x.type === 'button' && (x.props['aria-label'] === label || text(x).includes(label)));
  return { render, flatten, button, annotation, project, dataset, workflow, suggestionsApi, get operations() { return operations; }, get generations() { return generations; } };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('accept survives gallery object refresh and enables another generation', async () => {
  const ui = mount();
  ui.button(ui.render(), '모델 보조 라벨링 패널').props.onClick();
  ui.render(); await settle();
  const before = ui.annotation.currentImage;
  ui.button(ui.render(), '선택 항목 채택').props.onClick(); await settle();
  assert.notEqual(ui.annotation.currentImage, before, 'gallery replaced the image object');
  assert.equal(ui.operations, 0);
  const next = ui.button(ui.render(), '제안 생성');
  assert.equal(next.props.disabled, false, 'review cleanup must enable generation');
  const provider = ui.flatten(ui.render()).find(x => x.type === 'CandidateProviderControls');
  assert.equal(provider.props.disabled, false, 'semantic and exemplar controls must also unlock');
  next.props.onClick(); await settle();
  assert.equal(ui.generations, 1);
  assert.equal(ui.button(ui.render(), '제안 생성').props.disabled, false);
});

test('late old-labelset review cannot clear the new labelset loading state', async () => {
  const ui = mount();
  ui.button(ui.render(), '모델 보조 라벨링 패널').props.onClick();
  ui.render(); await settle();
  let finishReview, finishList;
  ui.workflow.review = () => new Promise(resolve => { finishReview = resolve; });
  ui.button(ui.render(), '선택 항목 채택').props.onClick();
  ui.project.project = { ...ui.project.project, active_labelset_id: 'second' };
  ui.suggestionsApi.list = () => new Promise(resolve => { finishList = resolve; });
  ui.render();
  finishReview({ id: 'proposal', status: 'accepted', accepted_candidate_ids: ['candidate'] });
  await settle();
  assert.equal(ui.operations, 0);
  assert.equal(ui.button(ui.render(), '제안 생성').props.disabled, true, 'old review cannot unlock pending new-labelset request');
  finishList({ suggestions: [] }); await settle();
  assert.equal(ui.button(ui.render(), '제안 생성').props.disabled, false);
});
