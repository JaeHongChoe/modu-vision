// '클래스 이름 변경·병합': the apply button stays off until a clean preview is confirmed, conflicts and the untouched
// source-only count are shown as the backend reports them, one primary button at a time, a reason for every disabled
// preview, open edits saved before the preview, the stores refreshed in place after the apply (and an honest sentence
// when that failed), and no button at all for classification projects.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript'), test = require('node:test');
const React = require('react'); const { renderToStaticMarkup } = require('react-dom/server');

function load(file, mocks) {
  const name = path.resolve(__dirname, file); const mod = new Module(name, module); mod.filename = name; mod.paths = Module._nodeModulePaths(path.dirname(name));
  mod.require = ref => ref in mocks ? mocks[ref] : require(ref);
  mod._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText, name);
  return mod.exports;
}
// A zustand-like store: a selector hook with getState/setState over one mutable state object.
const store = state => Object.assign(selector => selector(state), { getState: () => state, setState: patch => Object.assign(state, typeof patch === 'function' ? patch(state) : patch) });
const dialogs = [];
const WorkspaceDialog = props => { dialogs.push(props); return React.createElement('div', { 'data-dialog': props.title }, React.createElement('p', null, props.description), props.children); };
const mocks = {
  '../../services/datasetWorkflow': { datasetWorkflow: { classRenamePreview: async () => { throw new Error('unused'); }, classRenameApply: async () => { throw new Error('unused'); } }, workflowError: error => String(error) },
  '../../stores/useAnnotationStore': { useAnnotationStore: store({ categories: [{ id: 7, name: 'Scratch', color: '#f59e0b' }], annotations: [], reviewerName: '' }) },
  '../../stores/useDatasetStore': { useDatasetStore: store({ classes: { Scratch: 3, Dent: 1 } }) },
  '../../stores/useProjectStore': { saveOpenEdits: async () => {} },
  '../../stores/workflowEvidence': { bumpWorkflowEvidence: () => {} }, '../../stores/assistContext': { reportPanelError: () => {} },
  '../common/WorkspaceDialog': { WorkspaceDialog },
};
const dialog = load('ClassRenameDialog.tsx', mocks);

const preview = (extra = {}) => ({
  applied: false, labelset_id: 'default', mapping: [{ from: 'Scratch', to: 'Crack', merge: false, source_ids: [7], target_ids: [], existing_spellings: [], source_spellings: ['Scratch'], role_from: 'defect', role_to: 'defect' }],
  overlays: { files: 2, shapes: 3, shapes_by_type: { polygon: 1, bbox: 2 }, palette_entries: 1, images: 2, images_with_masks: 1 }, review: { approved: 1 },
  source_only: { images: 0, unreadable: 0 }, labelbook: { version: null, names_old_class: false },
  drafts: { pending: 1, candidates: 1 }, flows: { versions: 1, references: 2 }, models: { count: 1, job_ids: ['job_a'] }, truth: { images: 1, declarations: 1 },
  conflicts: [], preview_sha256: 'f'.repeat(64), ...extra,
});
const noop = () => {};
const render = props => renderToStaticMarkup(React.createElement(dialog.ClassRenameDialogView, {
  names: ['Dent', 'Scratch'], from: 'Scratch', to: 'Crack', preview: null, result: null, reloaded: null, error: '', busy: null, confirmed: false,
  onFromChange: noop, onToChange: noop, onConfirmedChange: noop, onPreview: noop, onApply: noop, onClose: noop, ...props,
}));
const applyButton = html => (html.match(/<button[^>]*>(?:적용 중…|백업 후 적용)<\/button>/) || [null])[0];
const primaries = html => (html.match(/class="workspace-button workspace-button--primary"/g) || []).length;

test('before a preview the dialog says what it never touches, 미리보기 is the one primary button and the apply is not offered', () => {
  dialogs.length = 0;
  const html = render({});
  assert.equal(dialogs.at(-1).title, '클래스 이름 변경·병합'); assert.match(dialogs.at(-1).description, /원본 라벨 파일\(LabelMe·COCO·YOLO\)은 바꾸지 않습니다/);
  assert.match(html, /aria-label="바꿀 클래스"/); assert.match(html, /aria-label="새 이름"/);
  assert.match(html, /<button type="submit" class="workspace-button workspace-button--primary">미리보기<\/button>/);
  assert.equal(primaries(html), 1); assert.equal(applyButton(html), null);
  assert.doesNotMatch(html, /aria-label="백업 확인"/);
});

test('Escape and 닫기 of the shared dialog are ignored while a request runs', () => {
  let closed = 0;
  dialogs.length = 0; render({ busy: 'apply', onClose: () => { closed += 1; } }); dialogs.at(-1).onClose();
  assert.equal(closed, 0);
  dialogs.length = 0; render({ onClose: () => { closed += 1; } }); dialogs.at(-1).onClose();
  assert.equal(closed, 1);
  assert.match(render({ busy: 'preview' }), /aria-busy="true"/);
});

test('a disabled 미리보기 always says why: the same name, or no class to rename', () => {
  const same = render({ to: ' Scratch ' });
  assert.match(same, /<button type="submit" disabled="" aria-describedby="class-rename-hint"/);
  assert.match(same, /<p id="class-rename-hint"[^>]*>&quot;Scratch&quot;과\(와\) 같은 이름입니다\. 다른 이름을 입력하거나 합칠 클래스를 고르세요\.<\/p>/);
  const empty = render({ names: [], from: '', to: 'Crack' });
  assert.match(empty, /aria-describedby="class-rename-hint"/);
  assert.match(empty, /편집 라벨에 클래스가 없습니다\. 2단계에서 라벨을 저장한 뒤 다시 여세요\./);
  assert.doesNotMatch(render({}), /class-rename-hint/);
});

test('a clean preview lists every store, makes 백업 후 적용 the one primary button and still waits for the confirm', () => {
  const html = render({ preview: preview() });
  assert.match(html, /Scratch → Crack/);
  for (const store of ['편집 라벨 파일', '마스크 팔레트', '검토 상태', '원본 라벨 파일', '라벨 기준서', '검토 대기 초안', '검사 흐름', '모델', '정답 선언']) assert.match(html, new RegExp(store), store);
  assert.match(html, /2장 · 도형 3개/); assert.match(html, /원본 파일에만 있는 라벨 0장 · 바꾸지 않음/); assert.match(html, /승인 1장 취소/);
  assert.match(html, /1건 · 후보 1개/); assert.match(html, /1개 버전 · 참조 2곳/); assert.match(html, /적용 뒤 다시 선언 필요/);
  assert.match(html, /aria-label="백업 확인"/); assert.match(html, /자동 백업/); assert.match(html, /고친 라벨도 함께 되돌아가고/);
  assert.match(html, /<button type="submit" class="workspace-button">다시 미리보기<\/button>/);
  assert.equal(primaries(html), 1); assert.match(applyButton(html), /disabled=""/);
  assert.doesNotMatch(applyButton(render({ preview: preview(), confirmed: true })), /disabled=""/, 'a confirmed clean preview enables the apply');
  assert.equal(dialog.canApply(preview(), true, false), true);
  assert.equal(dialog.canApply(preview(), true, true), false, 'busy blocks the apply');
  assert.equal(dialog.canApply(null, true, false), false);
  assert.match(render({ preview: preview({ mapping: [{ ...preview().mapping[0], source_spellings: ['Scratch', 'scratch'] }] }) }), /함께 바뀌는 이름: Scratch, scratch/);
});

test('conflicts replace the confirm, keep 미리보기 primary and offer no apply', () => {
  const conflicted = preview({ conflicts: [{ code: 'id_collision', message: '"Scratch"→"Dent" 병합은 클래스 ID가 다릅니다(7, 8). 같은 ID의 클래스만 병합하거나 다른 이름을 고르세요.' },
    { code: 'busy', message: '학습이 진행 중입니다. 학습이 끝나거나 멈춘 뒤 다시 시도하세요.' }],
  mapping: [{ from: 'Scratch', to: 'Dent', merge: true, source_ids: [7], target_ids: [8], existing_spellings: ['Dent'] }] });
  const html = render({ preview: conflicted, confirmed: true });
  assert.match(html, /<div role="alert" aria-label="적용할 수 없는 이유"[^>]*><ul/); assert.match(html, /클래스 ID가 다릅니다\(7, 8\)/); assert.match(html, /학습이 진행 중입니다/);
  assert.match(html, /병합: Dent에 합침/);
  assert.doesNotMatch(html, /aria-label="백업 확인"/);
  assert.equal(applyButton(html), null); assert.equal(primaries(html), 1);
  assert.match(html, /<button type="submit" class="workspace-button workspace-button--primary">다시 미리보기<\/button>/);
  assert.equal(dialog.canApply(conflicted, true, false), false);
  const book = render({ preview: preview({ labelbook: { version: 2, names_old_class: true }, conflicts: [{ code: 'labelbook', message: '라벨 기준서 v2에 있는 "Scratch" 클래스는 이름을 바꿀 수 없습니다. 라벨 기준서에 없는 클래스만 바꾸세요.' }] }) });
  assert.match(book, /v2 · 이 클래스를 씀/); assert.match(book, /기준서에 있는 클래스는 바꿀 수 없음/); assert.match(book, /라벨 기준서에 없는 클래스만 바꾸세요/);
  const split = render({ preview: preview({ source_only: { images: 1, unreadable: 0 }, conflicts: [{ code: 'source_only', message: '원본 라벨 파일에만 "Scratch"이(가) 있는 이미지 1장(c.png)은 이름이 바뀌지 않아 클래스가 둘로 나뉩니다.' }] }) });
  assert.match(split, /원본 파일에만 있는 라벨 1장/); assert.match(split, /2단계에서 열어 저장한 뒤 다시 미리보기/);
});

test('the result names the backup, says the reload only when it happened, and 닫기 is the one primary button', () => {
  const result = { ...preview(), applied: true, backup_version_id: 'v_20261022_120000_abcdef12', actor: 'Kim', affected_images: ['a.png', 'b.png'], dataset_fingerprint: 'x' };
  const html = render({ result, reloaded: true });
  assert.match(html, /데이터 버전 v_20261022_120000_abcdef12에 백업했습니다 · 편집 라벨 2장 · 도형 3개 바꿈/);
  assert.match(html, /라벨 세트를 다시 불러왔습니다\./);
  assert.match(html, /검토 대기 초안 1건은 옛 이름/); assert.match(html, /정답 선언 1장은 다시 선언/);
  assert.doesNotMatch(html, /백업 후 적용/); assert.match(html, /class="workspace-button workspace-button--primary">닫기</); assert.equal(primaries(html), 1);
  const failed = render({ result, reloaded: false });
  assert.doesNotMatch(failed, /다시 불러왔습니다/);
  assert.match(failed, /role="alert"[^>]*>적용은 끝났지만 화면을 다시 불러오지 못했습니다\. 2단계를 다시 여세요\.</);
  assert.equal(dialog.backupNotice(result), '데이터 버전 v_20261022_120000_abcdef12에 백업했습니다 · 편집 라벨 2장 · 도형 3개 바꿈');
});

test('a backend refusal is shown in its own words', () => {
  const html = render({ error: '학습이 진행 중입니다. 학습이 끝나거나 멈춘 뒤 다시 시도하세요.' });
  assert.match(html, /role="alert"[^>]*>학습이 진행 중입니다\. 학습이 끝나거나 멈춘 뒤 다시 시도하세요\.</);
});

test('the class list holds dataset classes and labels in use, never the unused demo palette', () => {
  const demo = ['OK', 'Solder Bridge', 'Scratch', 'Crack', 'Void', 'Contamination'];
  assert.deepEqual(dialog.renameCandidates({ dent: 2, scratch: 1 }, demo.map(() => ({}))), ['dent', 'scratch']);
  assert.deepEqual(dialog.renameCandidates({ dent: 2 }, [{ label: 'Hole' }, { label: ' ' }, { label: 'dent' }]), ['dent', 'Hole']);
  assert.deepEqual(dialog.renameCandidates(null, []), []);
});

test('the palette follows the rename: entries keep their id, and a merge folds into the existing target', () => {
  const palette = [{ id: 7, name: 'Scratch', color: '#f' }, { id: 8, name: 'Dent', color: '#d' }, { id: 9, name: 'scratch', color: '#s' }];
  assert.deepEqual(dialog.renamePalette(palette, [{ from: 'Scratch', to: 'Crack' }]), [{ id: 7, name: 'Crack', color: '#f' }, { id: 8, name: 'Dent', color: '#d' }]);
  assert.deepEqual(dialog.renamePalette(palette, [{ from: 'Scratch', to: 'Dent' }]), [{ id: 8, name: 'Dent', color: '#d' }]);
  assert.deepEqual(dialog.renamePalette(palette, [{ from: 'Hole', to: 'Pit' }]), palette);
});

// The stateful dialog under a hook harness: state slots by call order, effects run after each render.
function harness({ annotation = {}, dataset = {}, workflow = {}, save } = {}) {
  const calls = [];
  const slots = []; let cursor = 0; let effects = [];
  const react = {
    useState: initial => { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial; return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
    useMemo: fn => fn(), useEffect: fn => { effects.push(fn); },
  };
  const annotationState = { categories: [{ id: 7, name: 'Scratch', color: '#f' }, { id: 8, name: 'Dent', color: '#d' }], annotations: [], reviewerName: 'Kim',
    activeCategory: { id: 7, name: 'Scratch', color: '#f' }, currentLabel: 'Scratch', currentImage: { image_id: 'a' },
    loadAnnotationsForCurrent: async () => { calls.push('load'); return true; }, ...annotation };
  const datasetState = { classes: { Scratch: 3, Dent: 1 }, activeClassFilter: 'Scratch', importError: null,
    annotationsChanged: async () => { calls.push('annotationsChanged'); }, ...dataset };
  const flow = { classRenamePreview: async (mapping, actor) => { calls.push(['preview', mapping, actor]); return preview(); },
    classRenameApply: async () => { calls.push('apply'); return { ...preview(), applied: true, backup_version_id: 'v_1', actor: 'Kim', affected_images: ['a.png'], dataset_fingerprint: 'x' }; }, ...workflow };
  global.window = { dispatchEvent: event => { calls.push(event.type); return true; } };
  const module = load('ClassRenameDialog.tsx', { ...mocks, react, 'react/jsx-runtime': { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: 'fragment' },
    '../../services/datasetWorkflow': { datasetWorkflow: flow, workflowError: error => error.message },
    '../../stores/useAnnotationStore': { useAnnotationStore: store(annotationState) }, '../../stores/useDatasetStore': { useDatasetStore: store(datasetState) },
    '../../stores/useProjectStore': { saveOpenEdits: save || (async () => { calls.push('save'); }) },
    '../../stores/workflowEvidence': { bumpWorkflowEvidence: () => calls.push('bump') } });
  const view = () => { cursor = 0; effects = []; const element = module.ClassRenameDialog({ onClose: noop }); for (const effect of effects) effect(); cursor = 0; effects = []; return module.ClassRenameDialog({ onClose: noop }).props; };
  const settle = () => new Promise(resolve => setImmediate(resolve));
  return { calls, view, settle, annotationState, datasetState };
}

test('the preview saves open canvas edits first, so it counts what the apply will find on disk', async () => {
  const h = harness();
  let props = h.view(); assert.equal(props.from, 'Dent');
  props.onFromChange('Scratch'); props = h.view(); props.onToChange('Crack'); props = h.view();
  props.onPreview(); await h.settle();
  assert.deepEqual(h.calls, ['save', ['preview', [{ from: 'Scratch', to: 'Crack' }], 'Kim']]);
  assert.equal(h.view().preview.preview_sha256, 'f'.repeat(64));
});

test('when a preview ends with the focus dropped to the page, it goes back to the dialog\'s next action', async () => {
  const focused = [];
  const next = { focus: () => focused.push('next') };
  const dialogBody = { querySelector: selector => selector.includes('workspace-button--primary') ? next : null };
  const page = { tagName: 'BODY' };
  global.document = { body: page, documentElement: {}, activeElement: page, querySelector: selector => selector === '[data-class-rename-body]' ? dialogBody : null };
  try {
    const h = harness();
    let props = h.view(); props.onToChange('Crack'); props = h.view();
    focused.length = 0;
    props.onPreview(); await h.settle();
    h.view();
    assert.ok(focused.length > 0, 'the disabled preview button let the focus fall to the page; it comes back');
    focused.length = 0;
    global.document.activeElement = { tagName: 'INPUT' };
    h.view();
    assert.deepEqual(focused, [], 'a focus the person placed elsewhere is left alone');
  } finally {
    delete global.document;
  }
});

test('a class list that arrives after the dialog opened becomes the choice', () => {
  const h = harness({ dataset: { classes: {} } });
  assert.equal(h.view().from, '');
  h.datasetState.classes = { Dent: 1 };
  assert.equal(h.view().from, 'Dent');
});

test('after the apply the stores refresh in place: palette, active class, class filter, image list, open image, statistics, evidence', async () => {
  const h = harness();
  let props = h.view(); props.onFromChange('Scratch'); props = h.view(); props.onToChange('Crack'); props = h.view();
  props.onPreview(); await h.settle(); props = h.view(); props.onConfirmedChange(true); props = h.view();
  props.onApply(); await h.settle(); await h.settle();
  assert.deepEqual(h.calls.slice(2), ['save', 'apply', 'annotationsChanged', 'load', 'dataset-statistics-changed', 'bump']);
  assert.deepEqual(h.annotationState.categories.map(row => [row.id, row.name]), [[7, 'Crack'], [8, 'Dent']]);
  assert.equal(h.annotationState.activeCategory.name, 'Crack'); assert.equal(h.annotationState.currentLabel, 'Crack');
  assert.equal(h.datasetState.activeClassFilter, null);
  props = h.view(); assert.equal(props.reloaded, true); assert.equal(props.result.backup_version_id, 'v_1'); assert.equal(props.error, '');
});

test('a reload that failed after the apply is said plainly, and evidence is still marked stale', async () => {
  const h = harness({ dataset: { annotationsChanged: async () => { h.calls.push('annotationsChanged'); h.datasetState.importError = '목록을 읽지 못했습니다.'; } } });
  let props = h.view(); props.onFromChange('Scratch'); props = h.view(); props.onToChange('Crack'); props = h.view();
  props.onPreview(); await h.settle(); props = h.view(); props.onConfirmedChange(true); props = h.view();
  props.onApply(); await h.settle(); await h.settle();
  assert.deepEqual(h.calls.slice(-3), ['annotationsChanged', 'dataset-statistics-changed', 'bump']);
  assert.equal(h.view().reloaded, false);
});

test('a refusal before the apply request keeps the preview; one from the backend drops it', async () => {
  let refuse = false;
  const h = harness({ save: async () => { if (refuse) throw new Error('검사가 진행 중입니다. 끝난 뒤 다시 시도하세요.'); } });
  let props = h.view(); props.onFromChange('Scratch'); props = h.view(); props.onToChange('Crack'); props = h.view();
  props.onPreview(); await h.settle(); props = h.view(); props.onConfirmedChange(true); props = h.view();
  refuse = true; props.onApply(); await h.settle();
  props = h.view();
  assert.ok(props.preview, 'nothing was sent, so the preview still matches the files');
  assert.equal(props.error, '검사가 진행 중입니다. 끝난 뒤 다시 시도하세요.'); assert.ok(!h.calls.includes('apply'));
  const stale = harness({ workflow: { classRenameApply: async () => { throw new Error('미리보기 이후 라벨이 바뀌었습니다. 다시 미리보기하세요.'); } } });
  props = stale.view(); props.onFromChange('Scratch'); props = stale.view(); props.onToChange('Crack'); props = stale.view();
  props.onPreview(); await stale.settle(); props = stale.view(); props.onConfirmedChange(true); props = stale.view();
  props.onApply(); await stale.settle();
  props = stale.view(); assert.equal(props.preview, null); assert.equal(props.confirmed, false); assert.match(props.error, /다시 미리보기/);
});

test('the bar offers the rename except for classification projects, and never while the project is busy', () => {
  const bar = (project, busy = false) => {
    const { LabelSetBar } = load('LabelSetBar.tsx', {
      'lucide-react': { CopyPlus: () => null, Layers3: () => null, RefreshCw: () => null, Tags: () => null },
      '../../services/api': { api: { project: { listLabelsets: async () => ({ labelsets: [] }) } } },
      '../../stores/useProjectStore': { useProjectStore: store({ project, isProjectBusy: busy, projectError: null, activateLabelset: async () => true, createAndActivateLabelset: async () => true }) },
      './ClassRenameDialog': { ClassRenameDialog: () => null, DIALOG_TITLE: '클래스 이름 변경·병합' },
    });
    return renderToStaticMarkup(React.createElement(LabelSetBar, {}));
  };
  const project = { id: 'p', project_dir: '/p', active_labelset_id: 'default', task: 'detection' };
  assert.match(bar(project), /<button type="button"[^>]*>(?:<!-- -->)?클래스 이름 변경·병합<\/button>/);
  assert.doesNotMatch(bar({ ...project, task: 'classification' }), /클래스 이름 변경·병합/);
  assert.match(bar(project, true).match(/<button type="button"[^>]*title="활성 라벨 세트[^>]*>/)[0], /disabled=""/);
});
