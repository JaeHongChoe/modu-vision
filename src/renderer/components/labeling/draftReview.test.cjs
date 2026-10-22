const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

function load(filename, mocks = {}) {
  const m = new Module(filename, module);
  m.filename = filename;
  m.paths = Module._nodeModulePaths(path.dirname(filename));
  const req = m.require.bind(m);
  m.require = name => Object.hasOwn(mocks, name) ? mocks[name] : req(name);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, filename);
  return m.exports;
}
const helpers = () => load(path.join(__dirname, 'draftReview.ts'), {
  '../../services/datasetWorkflow': {workflowError: error => error?.detail?.message || error?.message || String(error)},
});
const draft = (id, image, confidences, extra = {}) => ({id, status: 'pending', created_at: `2026-10-22T00:00:0${id.slice(-1)}Z`, image_path: image,
  candidates: confidences.map((confidence, index) => ({id: `${id}_c${index}`, confidence, annotation: {type: 'bbox', label: 'defect'}})),
  accepted_candidate_ids: [], ...extra});

test('drafts the model is least sure of come first, and the confident order reverses it', () => {
  const {orderDrafts, topConfidence, DRAFT_ORDER_TEXT} = helpers();
  const drafts = [draft('d1', '/a.png', [0.9, 0.4]), draft('d2', '/b.png', [0.55]), draft('d3', '/c.png', []),
    draft('d4', '/d.png', [0.7], {status: 'accepted'})];
  assert.equal(topConfidence(drafts[0]), 0.9);
  assert.equal(topConfidence(drafts[2]), 0, 'no candidate counts as the least sure');
  assert.deepEqual(orderDrafts(drafts, 'uncertain').map(row => row.id), ['d3', 'd2', 'd1'], 'only pending drafts, least sure first');
  assert.deepEqual(orderDrafts(drafts, 'confident').map(row => row.id), ['d1', 'd2', 'd3']);
  assert.match(DRAFT_ORDER_TEXT.uncertain, /신뢰도가 낮은 순/);
  // Equal confidence keeps the older draft first.
  assert.deepEqual(orderDrafts([draft('d6', '/x.png', [0.5]), draft('d5', '/y.png', [0.5])], 'uncertain').map(row => row.id), ['d5', 'd6']);
});

test('images waiting count once however many drafts they have, and only recorded adoptions can be taken back', () => {
  const {pendingImageCount, canTakeBack, adoptionHistory} = helpers();
  const drafts = [draft('d1', '/a.png', [0.9]), draft('d2', '/a.png', [0.4]), draft('d3', '/b.png', [0.5]),
    draft('d4', '/c.png', [0.7], {status: 'accepted', reviewed_at: '2026-10-22T01:00:00Z', adopted_annotations: [{id: 'd4_c0'}]}),
    draft('d5', '/d.png', [0.7], {status: 'accepted', reviewed_at: '2026-10-22T02:00:00Z'}),
    draft('d6', '/e.png', [0.7], {status: 'reverted', reviewed_at: '2026-10-22T01:30:00Z', reverted_at: '2026-10-22T03:00:00Z'}),
    draft('d7', '/f.png', [0.7], {status: 'rejected'})];
  assert.equal(pendingImageCount(drafts), 2);
  assert.equal(canTakeBack(drafts[3]), true);
  assert.equal(canTakeBack(drafts[4]), false, 'an adoption without its saved shapes cannot be taken back on its own');
  assert.equal(canTakeBack(drafts[5]), false);
  assert.deepEqual(adoptionHistory(drafts).map(row => row.id), ['d6', 'd5', 'd4'], 'newest first; rejected drafts are not adoptions');
});

test('a refused take-back names the data version to restore', () => {
  const {draftError} = helpers();
  const refused = {detail: {message: '채택한 도형이 그 뒤 바뀌어 이 채택만 되돌릴 수 없습니다.', backup_version_id: 'v_20261022_010203_abcd1234'}};
  assert.match(draftError(refused), /바뀌어/);
  assert.match(draftError(refused), /v_20261022_010203_abcd1234/);
  // What the API client really throws: the refusal's detail itself, with the HTTP status added.
  const thrown = Object.assign({...refused.detail, changed_candidate_ids: ['suggestion_x_c0']}, {status: 409});
  assert.match(draftError(thrown), /바뀌어/);
  assert.match(draftError(thrown), /채택 전 데이터 버전: v_20261022_010203_abcd1234/);
  assert.doesNotMatch(draftError(new Error('offline')), /데이터 버전/);
  // A draft that no longer matches the data is refused by the server; the panel says why and what to do next.
  const stale = draftError(Object.assign(new Error('Training dataset fingerprint changed after proposal generation. Generate again.'), {status: 409}));
  assert.match(stale, /직접 고쳐 저장한 라벨/);
  assert.match(stale, /3단계에서 다시 학습/);
  assert.doesNotMatch(stale, /fingerprint/);
});

test('step 2 puts waiting drafts first, with review-approved images, and leads to the draft review', () => {
  const {getNextAction} = load(path.join(__dirname, '..', 'wizard', 'workflowReadiness.ts'));
  const base = {stage: 2, projectReady: true, sourceReady: true, totalImages: 20, train: 12, val: 4, busy: false, task: 'detection', flowDirty: false,
    evidence: {status: 'ready', review: {ready: true, counts: {eligible: 8, approved: 8}}}};
  const waiting = getNextAction({...base, drafts: {pendingImages: 6}});
  assert.deepEqual(waiting.missingInputs, ['초안 6장 검토 대기 · 검수 승인 8장']);
  // Without the team review nothing is approved; saying "0 approved" would read as if no label counted.
  assert.deepEqual(getNextAction({...base, drafts: {pendingImages: 6}, evidence: {status: 'ready', review: {ready: true, counts: {eligible: 12, approved: 0}}}}).missingInputs,
    ['초안 6장 검토 대기']);
  assert.deepEqual(waiting.nextAction, {label: '초안 검토', step: 2, target: 'draft-review-queue', kind: undefined});
  assert.equal(waiting.status, 'ready', 'training is still possible; the drafts are the next thing to do');
  const blocked = getNextAction({...base, drafts: {pendingImages: 1}, evidence: {status: 'ready', review: {ready: false, counts: {eligible: 0}}}});
  assert.equal(blocked.status, 'blocked');
  assert.deepEqual(blocked.missingInputs, ['초안 1장 검토 대기']);
  const none = getNextAction({...base, drafts: {pendingImages: 0}});
  assert.equal(none.nextAction.label, '모델 학습 시작 (3단계)');
  assert.equal(getNextAction(base).nextAction.label, '모델 학습 시작 (3단계)');
  // After adopting, the saved split no longer covers the labeled images: step 3 says to apply it again.
  const stage3 = {...base, stage: 3, train: 0, val: 0, splitState: 'saved', splitMissesLabels: true,
    evidence: {status: 'ready', review: {ready: true, counts: {eligible: 49}}, impact: {models: [], model_evaluations: [], flows: [], flow_evaluations: [], approvals: [], packages: []}}};
  const resplit = getNextAction(stage3);
  assert.equal(resplit.status, 'blocked');
  assert.match(resplit.missingInputs[0], /초안에서 채택한 이미지/);
  assert.deepEqual(resplit.nextAction, {label: '분할 다시 적용 (1단계)', step: 1, target: undefined, kind: undefined});
  assert.match(getNextAction({...stage3, splitMissesLabels: false}).missingInputs[0], /분할 부족/);
});

test('a later save keeps an adopted shape\'s origin and adds none to a drawn shape', async () => {
  const bodies = [];
  const file = path.join(__dirname, '..', '..', 'stores', 'useAnnotationStore.ts');
  const m = load(file, {
    '../services/api': {api: {}, getApiPersistenceIdentity: () => 'local', getProjectContextGeneration: () => 1},
    '../services/datasetWorkflow': {datasetWorkflow: {saveAnnotations: async body => { bodies.push(body); return {status: 'saved'}; }}},
    './useDatasetStore': {useDatasetStore: {getState: () => ({annotationsChanged: async () => {}})}},
    '../components/labeling/foundationRequest': {labelCategoryPalette: categories => categories},
    '../components/labeling/convertedAnnotation': {}, '../components/labeling/teamDataWorkflow': {imageLeaseToken: () => null},
  });
  const origin = {kind: 'model_draft', proposal_id: 'suggestion_x', candidate_id: 'suggestion_x_c0', reviewer: 'kai', adopted_at: '2026-10-22T00:00:00Z'};
  m.useAnnotationStore.setState({currentImage: {image_id: 'a', file_path: '/a.png', width: 10, height: 10}, imageDimensions: {width: 10, height: 10},
    annotationLoadStatus: 'ready', isDirty: true, metadata: {image_uuid: 'a', revision: 3}, teamEditingEnabled: false,
    annotations: [{id: 'drawn', type: 'bbox', label: 'scratch', bbox: [1, 1, 4, 4]}, {id: 'suggestion_x_c0', type: 'bbox', label: 'defect', bbox: [5, 5, 8, 8], origin}]});
  assert.equal(await m.useAnnotationStore.getState().saveAnnotations(), true);
  const [drawn, adopted] = bodies[0].annotations;
  assert.equal('origin' in drawn, false);
  assert.deepEqual(adopted.origin, origin);
});
