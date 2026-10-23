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
  // A draft that found nothing waits on no one: not counted, and listed among the empty ones to close together.
  const empty = draft('e1', '/g.png', []);
  assert.equal(pendingImageCount([...drafts, empty]), 2);
  assert.deepEqual(helpers().emptyPendingDrafts([...drafts, empty]).map(row => row.id), ['e1']);
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
  // A model trained before the change may draft again (2026-10-22 decision), so the words no longer send people to retrain.
  assert.doesNotMatch(stale, /3단계에서 다시 학습/);
  assert.match(stale, /같은 모델로도 만들 수 있습니다/);
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

test('step 2 counts confirmed, drafted and remaining images, and its action reduces the remaining ones', () => {
  const {getNextAction, stepTwoProgress} = load(path.join(__dirname, '..', 'wizard', 'workflowReadiness.ts'));
  const base = {stage: 2, projectReady: true, sourceReady: true, totalImages: 54, train: 34, val: 10, busy: false, task: 'detection', flowDirty: false,
    evidence: {status: 'ready', review: {ready: true, counts: {eligible: 48}}}};
  assert.equal(stepTwoProgress({}), null, 'no per-image label count: no progress line');
  assert.deepEqual(stepTwoProgress({labels: {total: 54, unlabeled: 6}, drafts: {pendingImages: 4}}), {confirmed: 48, drafts: 4, remaining: 2});
  assert.deepEqual(stepTwoProgress({labels: {total: 54, unlabeled: 6}, drafts: {pendingImages: 9}}), {confirmed: 48, drafts: 6, remaining: 0}, 'drafts never exceed the unlabeled images');
  // No model yet: a person labels the remaining images on the canvas (the class picker for classification).
  const byHand = getNextAction({...base, labels: {total: 54, unlabeled: 6}, drafts: {pendingImages: 0}, currentModels: 0});
  assert.equal(byHand.status, 'ready', 'training is still possible');
  assert.deepEqual(byHand.nextAction, {label: '직접 라벨 달기', step: 2, target: 'labeling-canvas', kind: undefined});
  assert.deepEqual(byHand.missingInputs, ['라벨 없는 이미지 6장']);
  assert.deepEqual(byHand.progress, {confirmed: 48, drafts: 0, remaining: 6});
  assert.equal(getNextAction({...base, task: 'classification', labels: {total: 54, unlabeled: 6}, currentModels: 0}).nextAction.target, 'image-class-picker');
  // A model trained on the current data drafts them instead; the target sits inside the draft panel's popover.
  const byModel = getNextAction({...base, labels: {total: 54, unlabeled: 6}, drafts: {pendingImages: 0}, currentModels: 1});
  assert.deepEqual(byModel.nextAction, {label: '초안 만들기', step: 2, target: 'draft-create', kind: undefined});
  // Waiting drafts still come first, with the progress line; nothing left to label goes on to training.
  const waiting = getNextAction({...base, labels: {total: 54, unlabeled: 6}, drafts: {pendingImages: 6}, currentModels: 1});
  assert.equal(waiting.nextAction.label, '초안 검토');
  assert.deepEqual(waiting.progress, {confirmed: 48, drafts: 6, remaining: 0});
  const done = getNextAction({...base, labels: {total: 54, unlabeled: 0}, drafts: {pendingImages: 0}, currentModels: 1});
  assert.equal(done.nextAction.label, '모델 학습 시작 (3단계)');
  assert.deepEqual(done.progress, {confirmed: 54, drafts: 0, remaining: 0});
  // Review blockers keep their text and target, with the progress line beside them.
  const blocked = getNextAction({...base, labels: {total: 54, unlabeled: 6}, currentModels: 1, evidence: {status: 'ready', review: {ready: false, counts: {eligible: 0}, blockers: ['두 사람 검토 대기 이미지 3장']}}});
  assert.deepEqual(blocked.missingInputs, ['두 사람 검토 대기 이미지 3장']);
  assert.equal(blocked.nextAction.target, 'workflow-label-review');
  assert.deepEqual(blocked.progress, {confirmed: 48, drafts: 0, remaining: 6});
});

test('several drafts adopt together only from one batch, one per image, and the loop stops on a stale batch', () => {
  const {multiAdoptionRefusal, staleBatchRefusal, adoptionSummary} = helpers();
  const a = draft('d1', '/a.png', [0.9], {batch_id: 'batch_1'}), b = draft('d2', '/b.png', [0.8], {batch_id: 'batch_1'});
  assert.equal(multiAdoptionRefusal([a]), null, 'one draft needs no grouping');
  assert.equal(multiAdoptionRefusal([a, b]), null);
  assert.match(multiAdoptionRefusal([a, draft('d3', '/c.png', [0.5], {batch_id: 'batch_2'})]), /같은 묶음/);
  assert.match(multiAdoptionRefusal([a, draft('d4', '/d.png', [0.5])]), /하나씩/);
  assert.match(multiAdoptionRefusal([a, draft('d5', '/a.png', [0.5], {batch_id: 'batch_1'})]), /같은 이미지/);
  assert.equal(staleBatchRefusal(new Error('Training dataset fingerprint changed after proposal generation. Generate again.')), true);
  assert.equal(staleBatchRefusal(new Error('Image, labels, or checkpoint changed after proposal generation. Generate again.')), false);
  const summary = adoptionSummary([{draft: a, adopted: 2}, {draft: b, adopted: 0, error: '이 이미지가 바뀌었습니다'}], true);
  assert.match(summary, /1장의 초안에서 2개를 라벨로 채택/);
  assert.match(summary, /1장은 채택하지 못했습니다: b\.png · 이 이미지가 바뀌었습니다/);
  assert.match(summary, /남은 초안은 데이터가 바뀌어 멈췄습니다/);
  assert.doesNotMatch(summary, /3단계에서 다시 학습/, 'an external batch gone stale is drafted again, not retrained');
  assert.doesNotMatch(adoptionSummary([{draft: a, adopted: 1}], false), /채택하지 못했습니다|멈췄습니다/);
  const {adoptable, draftError} = helpers();
  assert.equal(adoptable(draft('d8', '/h.png', [])), false, 'nothing to adopt: only rejectable');
  assert.equal(adoptable(a), true);
  assert.match(draftError(new Error('Select one or more proposal candidates to accept.')), /채택할 후보가 없습니다/);
});

test('every draft names its source, mixed sources get the note, and external refusals read in Korean', () => {
  const {draftSourceText, sourcesMixed, draftError, staleBatchRefusal} = helpers();
  assert.equal(draftSourceText({job_id: 'job_1'}), '학습 모델', 'older records without a backend are trained-model drafts');
  assert.equal(draftSourceText({backend: 'foundation', job_id: 'foundation'}), 'SAM2');
  assert.equal(draftSourceText({backend: 'foundation', job_id: 'foundation', prompt: 'scratch, dent'}), '문구 + SAM2 · "scratch, dent"');
  assert.equal(draftSourceText({backend: 'grounding_dino', job_id: 'grounding_dino', prompt: ' 긁힘 '}), '문구 초안 · "긁힘"');
  assert.equal(draftSourceText({backend: 'template_match', job_id: 'template_match'}), '예시 템플릿');
  assert.equal(draftSourceText({backend: 'trained_model', job_id: 'job_1', model_data_state: 'changed'}), '학습 모델 · 데이터가 바뀐 뒤 학습한 모델 · 평가 근거 아님',
    'the 2026-10-22 decision\'s full mark');
  assert.equal(sourcesMixed([draft('d1', '/a.png', [0.9]), draft('d2', '/b.png', [0.9], {backend: 'trained_model'})]), false);
  assert.equal(sourcesMixed([draft('d1', '/a.png', [0.9]), draft('d2', '/b.png', [0.9], {backend: 'vlm'})]), true);
  assert.match(draftError(new Error('Source labels or dataset changed; generate candidates again')), /데이터가 바뀌어/);
  assert.match(draftError(new Error('Foundation model changed')), /모델 파일이 바뀌어/);
  assert.equal(staleBatchRefusal(new Error('Source labels or dataset changed; generate candidates again')), true, 'an external batch gone stale stops the loop too');
  assert.equal(staleBatchRefusal(new Error('Foundation model changed')), false);
});

test('the adoption line says how a model fared and the threshold advice stays quiet on small samples', () => {
  const {adoptionLine, adoptionTitle, thresholdAdvice, modelStats} = helpers();
  const row = {job_id: 'job_1', backend: 'trained_model', drafts: 7, pending: 2, adopted: 3, rejected: 1, reverted: 1, edited_after: 1, edited_unknown: 0,
    reviewed_candidates: 6, adopted_candidates: 3, rejected_candidates: 3, mean_confidence_adopted: 0.9, mean_confidence_rejected: 0.5, advice: {reason: 'too_few', reviewed_candidates: 6}};
  assert.equal(modelStats([row], 'job_1'), row);
  assert.equal(modelStats([row], 'job_2'), null);
  assert.equal(adoptionLine(row), '초안 7장 · 채택 3 · 거절 1 · 채택 뒤 수정 1 · 되돌림 1');
  assert.equal(adoptionTitle(row), '채택 후보 평균 신뢰도 90% · 거절 후보 50%');
  assert.match(thresholdAdvice(row), /검토한 후보 6개 — 20개부터/);
  assert.equal(adoptionLine(null), '아직 검토한 초안이 없습니다.');
  assert.equal(thresholdAdvice(null), '');
  assert.match(thresholdAdvice({...row, advice: {reason: 'suggestion', reviewed_candidates: 30, threshold: 0.65, adopted_rate_above: 0.95, candidates_above: 12}}), /신뢰도 0\.65 이상은 95%가 채택/);
  assert.match(thresholdAdvice({...row, advice: {reason: 'already_high', reviewed_candidates: 30, adopted_rate: 0.93}}), /93%를 채택.*충분/);
  assert.match(thresholdAdvice({...row, advice: {reason: 'none_reaches', reviewed_candidates: 30}}), /다시 학습/);
  assert.match(thresholdAdvice({...row, advice: {reason: 'task_not_applicable', reviewed_candidates: 30}}), /후보가 한 개라/);
});

test('an older model\'s candidate whose class the project no longer has starts unchecked and says so', () => {
  const {defaultChoice, unknownClassCandidate, UNKNOWN_CLASS_MARK} = helpers();
  const draft = (state) => ({model_data_state: state, candidates: [
    {id: 'a', confidence: 0.9, annotation: {type: 'bbox', label: 'scratch', bbox: [0, 0, 1, 1]}},
    {id: 'b', confidence: 0.8, annotation: {type: 'bbox', label: 'old_name', bbox: [0, 0, 1, 1]}}]});
  const known = new Set(['scratch', 'dent']);
  assert.deepEqual([...defaultChoice(draft('changed'), known)], ['a'], 'the retired class is left for the person to pick');
  assert.equal(unknownClassCandidate(draft('changed'), 'old_name', known), true);
  assert.deepEqual([...defaultChoice(draft('current'), known)], ['a', 'b'], 'a current model names the classes it was trained with');
  assert.deepEqual([...defaultChoice(draft('changed'), new Set())], ['a', 'b'], 'an empty project\'s first drafts name new classes on purpose');
  assert.match(UNKNOWN_CLASS_MARK, /클래스 목록에 없는/);
});

test('grid pages hold one batch each, one draft per image, and a page adopts only once every tile has shown', () => {
  const {gridPages, pageAdoptionRefusal} = helpers();
  const b1 = (id, image, confidences = [0.8]) => draft(id, image, confidences, {batch_id: 'batch_1'});
  const drafts = [b1('d1', '/a.png'), b1('d2', '/b.png'), b1('d3', '/c.png'), b1('d4', '/a.png'), draft('d5', '/e.png', [0.7], {batch_id: 'batch_2'}),
    draft('d6', '/f.png', [0.7]), b1('d7', '/g.png', [])];
  const {pages, singles} = gridPages(drafts, 2);
  assert.deepEqual(pages.map(page => page.map(row => row.id)), [['d1', 'd2'], ['d3'], ['d5']], 'pages never cross a batch');
  assert.deepEqual(singles.map(row => row.id), ['d4', 'd6', 'd7'], 'a second draft of an image, a draft without a batch and one without candidates stay singles');
  const page = pages[0];
  assert.match(pageAdoptionRefusal(page, new Set(), new Set()), /아직 보이지 않은 초안 2건/);
  assert.match(pageAdoptionRefusal(page, new Set(), new Set(['d1'])), /아직 보이지 않은 초안 1건/);
  assert.equal(pageAdoptionRefusal(page, new Set(), new Set(['d1', 'd2'])), null);
  assert.equal(pageAdoptionRefusal(page, new Set(['d2']), new Set(['d1'])), null, 'an excluded tile need not have shown');
  assert.match(pageAdoptionRefusal(page, new Set(['d1', 'd2']), new Set()), /채택할 초안이 없습니다/);
});

test('limits read as one line whether a provider stored a sentence or an older record a list, and a closed draft says what became of it', () => {
  const {supportLimitsText, closedDraftText, OPEN_REQUEST_MISSING_TEXT} = helpers();
  assert.equal(supportLimitsText('SAM2 IoU predictions are not calibrated. Review every proposed label.'), 'SAM2 IoU predictions are not calibrated. Review every proposed label.');
  assert.equal(supportLimitsText(['a', '', 'b']), 'a · b');
  assert.equal(supportLimitsText(undefined), '');
  assert.match(closedDraftText({status: 'accepted', image_path: '/x/a.png'}), /a\.png.*이미 채택/);
  assert.match(closedDraftText({status: 'rejected', image_path: '/x/a.png'}), /이미 거절/);
  assert.match(closedDraftText({status: 'reverted', image_path: '/x/a.png'}), /되돌렸습니다/);
  assert.match(OPEN_REQUEST_MISSING_TEXT, /찾지 못했습니다/);
});

test('an open request records its scope and the kept candidates, and a cleared request never reuses its nonce', () => {
  const {useDraftReviewStore} = load(path.join(__dirname, '..', '..', 'stores', 'useDraftReviewStore.ts'));
  useDraftReviewStore.getState().publish('scope-A', 2, 1);
  useDraftReviewStore.getState().requestOpen('d1', ['d1_c1']);
  const first = useDraftReviewStore.getState().openRequest;
  assert.deepEqual({id: first.id, scope: first.scope, candidateIds: first.candidateIds}, {id: 'd1', scope: 'scope-A', candidateIds: ['d1_c1']}, 'defaults to the scope the queue published');
  useDraftReviewStore.getState().clearOpenRequest();
  assert.equal(useDraftReviewStore.getState().openRequest, null);
  useDraftReviewStore.getState().requestOpen('d1', undefined, 'scope-B');
  const second = useDraftReviewStore.getState().openRequest;
  assert.equal(second.scope, 'scope-B');
  assert.ok(second.nonce > first.nonce, 'a new request after a clear is a new request');
  assert.equal('candidateIds' in second, false);
});

// A small hook runtime for the panels: render the component function, run its effects, let promises land, repeat.
// Child components stay plain elements, so the tree is exactly what the component itself renders.
function hookRuntime() {
  const slots = [], effects = [];
  let cursor = 0;
  const changed = (old, deps) => !old || !deps || deps.length !== old.deps.length || deps.some((dep, index) => !Object.is(dep, old.deps[index]));
  const react = {
    createElement: (type, props, ...children) => ({type, props: {...props, children}}),
    Fragment: 'Fragment',
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = {value: typeof initial === 'function' ? initial() : initial};
      const slot = slots[index];
      return [slot.value, value => { slot.value = typeof value === 'function' ? value(slot.value) : value; }];
    },
    useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = {current: initial}; return slots[index]; },
    useMemo(fn, deps) { const index = cursor++; if (changed(slots[index], deps)) slots[index] = {deps, value: fn()}; return slots[index].value; },
    useCallback(fn, deps) { return react.useMemo(() => fn, deps); },
    useEffect(fn, deps) {
      const index = cursor++, old = slots[index];
      if (changed(old, deps)) { const entry = {deps}; slots[index] = entry; effects.push(() => { old?.cleanup?.(); entry.cleanup = fn(); }); }
    },
  };
  react.default = react;
  return {react, async settle(component, cycles = 12) {
    let tree;
    for (let cycle = 0; cycle < cycles; cycle++) { cursor = 0; tree = component(); effects.splice(0).forEach(fn => fn()); await new Promise(resolve => setImmediate(resolve)); }
    return tree;
  }};
}
const nodes = node => !node || typeof node !== 'object' ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = node => node == null || typeof node === 'boolean' ? '' : typeof node === 'string' || typeof node === 'number' ? String(node) : Array.isArray(node) ? node.map(text).join('') : text(node.props?.children);
const byLabel = (tree, label) => nodes(tree).find(node => node.props?.['aria-label'] === label);
const buttonWith = (tree, words) => nodes(tree).find(node => node.type === 'button' && (node.props['aria-label'] === words || text(node).includes(words)));
const fakeStore = state => Object.assign(selector => selector(state), {getState: () => state, setState: patch => Object.assign(state, patch)});
// `prelude` declares module-local stand-ins (a `window` for the popover's fit), on the first line so line numbers hold.
function loadComponent(file, mocks, prelude = '') {
  const filename = path.join(__dirname, file), m = new Module(filename, module);
  m.filename = filename;
  m.paths = Module._nodeModulePaths(path.dirname(filename));
  m.require = name => { assert.ok(Object.hasOwn(mocks, name), `${file} imports ${name}`); return mocks[name]; };
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true}}).outputText;
  m._compile(output.replace('"use strict";', `"use strict";${prelude}`), filename);
  return m.exports;
}
const icons = new Proxy({}, {get: (_, key) => String(key)});
const row = (id, image, extra = {}) => ({id, status: 'pending', image_path: image, image_id: id, image_width: 100, image_height: 50, created_at: '2026-10-23T00:00:00Z',
  job_id: 'job_b', batch_id: 'batch_1', candidates: [{id: `${id}_c0`, confidence: 0.8, annotation: {type: 'bbox', label: 'defect', bbox: [1, 1, 5, 5]}},
    {id: `${id}_c1`, confidence: 0.6, annotation: {type: 'bbox', label: 'defect', bbox: [6, 6, 9, 9]}}], accepted_candidate_ids: [], ...extra});

function queueFixture({drafts, request = null, models = [], stats = async () => ({models: []}), list, reviewerName = 'kai', workflow = {}}) {
  const runtime = hookRuntime(), calls = {list: 0, stats: 0}, at = {scope: 'scope-B'};
  const annotation = {currentImage: null, isDirty: false, reviewerName, setReviewerName() {}};
  const review = {publish() {}, openRequest: request, clearOpenRequest() { review.openRequest = null; }, sidePanel: null, showSidePanel(panel) { review.sidePanel = panel; }};
  const api = {labelSuggestions: {
    list: async () => { calls.list++; return list ? list(at.scope) : {suggestions: drafts.map(draft => ({...draft}))}; },
    models: async () => ({models}),
    stats: () => { calls.stats++; return stats(); },
  }};
  const {DraftReviewQueue} = loadComponent('DraftReviewQueue.tsx', {
    react: runtime.react, 'lucide-react': icons,
    '../../services/api': {api, getProjectContext: () => null},
    '../../services/datasetWorkflow': {datasetWorkflow: workflow},
    '../../services/teamDataApi': {teamDataApi: {}},
    '../../stores/useAnnotationStore': {useAnnotationStore: fakeStore(annotation)},
    '../../stores/useDatasetStore': {readDatasetAuthority: () => 'a', useDatasetStore: fakeStore({annotationsChanged: async () => {}})},
    '../../stores/useProjectStore': {useProjectStore: fakeStore({projectDir: '/project-B', project: {source_dataset_dir: '/source-B'}, task: 'detection', openImageForLabeling: async () => true})},
    '../../stores/useModelAssistRunStore': {useModelAssistRunStore: fakeStore({begin() {}, end() {}})},
    '../../stores/useDraftReviewStore': {draftReviewScope: () => at.scope, useDraftReviewStore: fakeStore(review)},
    '../../stores/workflowEvidence': {bumpWorkflowEvidence() {}, subscribeWorkflowEvidence: () => () => {}},
    '../../stores/assistContext': {reportPanelError() {}},
    './BulkLabelAssist': {BulkLabelAssist: 'BulkLabelAssist'}, './PhraseDraftCreate': {PhraseDraftCreate: 'PhraseDraftCreate'}, './SuggestionPreview': {SuggestionPreview: 'SuggestionPreview'}, './DraftGrid': {DraftGrid: 'DraftGrid'},
    './teamDataWorkflow': {imageLeaseToken: () => undefined}, '../common/Explain': {Explain: 'Explain'},
    './draftReview': helpers(),
  }, 'var window = {innerHeight: 1000, addEventListener() {}, removeEventListener() {}};');
  return {calls, review, at, annotation, settle: cycles => runtime.settle(DraftReviewQueue, cycles)};
}

test('an external draft whose limits are one sentence opens in the queue and shows that sentence', async () => {
  // Every provider stores support_limits as a string; joining it like a list threw and unmounted the window.
  const limits = 'SAM2 IoU predictions and DINO cosine scores are not calibrated class probabilities. Review every proposed label.';
  const sam = row('suggestion_sam', '/b/one.png', {backend: 'foundation', job_id: 'foundation', batch_id: undefined, support_limits: limits});
  const queue = queueFixture({drafts: [sam], request: {id: 'suggestion_sam', nonce: 1, scope: 'scope-B'}});
  const block = byLabel(await queue.settle(), '선택한 초안');
  assert.ok(block, 'the selected draft opens');
  assert.ok(text(block).includes(limits));
  assert.match(text(block), /출처: SAM2/);
});

test('an open request from another project is dropped, a missing draft is read once more, and every handled request is cleared', async () => {
  const drafts = [row('d1', '/b/1.png'), row('d2', '/b/2.png', {status: 'accepted'})];
  // Asked in project A, met in project B: dropped without a read of its own or an opened popover.
  let queue = queueFixture({drafts, request: {id: 'from_a', nonce: 1, scope: 'scope-A'}});
  let tree = await queue.settle(30);
  assert.equal(queue.review.openRequest, null);
  assert.equal(queue.calls.list, 1, 'only the queue\'s own first read');
  assert.equal(byLabel(tree, '초안 검토 패널').props['aria-expanded'], false);
  // Not in this project's list: one more read (it may be newer than the list), then a notice instead of reading forever.
  queue = queueFixture({drafts, request: {id: 'gone', nonce: 2, scope: 'scope-B'}});
  tree = await queue.settle(30);
  assert.equal(queue.calls.list, 2);
  assert.equal(queue.calls.stats, 2);
  assert.equal(queue.review.openRequest, null);
  assert.equal(byLabel(tree, '초안 검토 패널').props['aria-expanded'], true);
  assert.match(text(tree), /그 초안을 찾지 못했습니다/);
  // Found and waiting: opened, then cleared so coming back to step 2 does not open it again.
  queue = queueFixture({drafts, request: {id: 'd1', nonce: 3, scope: 'scope-B'}});
  tree = await queue.settle();
  assert.equal(queue.review.openRequest, null);
  assert.ok(byLabel(tree, '선택한 초안'));
  // Found but already adopted: a notice, not an empty selection.
  queue = queueFixture({drafts, request: {id: 'd2', nonce: 4, scope: 'scope-B'}});
  tree = await queue.settle();
  assert.equal(queue.review.openRequest, null);
  assert.equal(byLabel(tree, '선택한 초안'), undefined);
  assert.match(text(tree), /2\.png의 그 초안은 이미 채택했습니다/);
});

test('a request from 모델 보조 opens with only the candidates kept there', async () => {
  const queue = queueFixture({drafts: [row('d1', '/b/1.png')], request: {id: 'd1', nonce: 1, scope: 'scope-B', candidateIds: ['d1_c1', 'not_this_draft']}});
  const block = byLabel(await queue.settle(), '선택한 초안');
  assert.deepEqual([...nodes(block).find(node => node.type === 'SuggestionPreview').props.selectedIds], ['d1_c1']);
  assert.deepEqual(nodes(block).filter(node => node.type === 'input' && node.props.type === 'checkbox').map(node => node.props.checked), [false, true],
    'the unchecked box stays out of 선택 후보 채택');
});

test('the grid keeps a page\'s exclusions when a tile is opened or the list is visited', async () => {
  const drafts = [row('d1', '/b/1.png'), row('d2', '/b/2.png'), row('d3', '/b/3.png')];
  const queue = queueFixture({drafts});
  let tree = await queue.settle();
  buttonWith(tree, '초안 검토 패널').props.onClick(); tree = await queue.settle();
  buttonWith(tree, '격자 (한눈 검토)').props.onClick(); tree = await queue.settle();
  const grid = () => nodes(tree).find(node => node.type === 'DraftGrid');
  grid().props.onToggle('d2'); tree = await queue.settle();
  for (const id of ['d1', 'd2', 'd3']) grid().props.onLoaded(id);
  tree = await queue.settle();
  assert.match(text(buttonWith(tree, '이 페이지 채택')), /2장/);
  grid().props.onOpen(drafts[0]); tree = await queue.settle();
  assert.ok(grid(), "'열기' stays in the grid");
  assert.ok(byLabel(tree, '선택한 초안'), 'with the opened draft below it');
  assert.deepEqual([...grid().props.excluded], ['d2']);
  buttonWith(tree, '목록').props.onClick(); tree = await queue.settle();
  buttonWith(tree, '격자 (한눈 검토)').props.onClick(); tree = await queue.settle();
  for (const id of ['d1', 'd2', 'd3']) grid().props.onLoaded(id);
  tree = await queue.settle();
  assert.deepEqual([...grid().props.excluded], ['d2']);
  assert.match(text(buttonWith(tree, '이 페이지 채택')), /2장/, 'the excluded draft is still left out');
  assert.equal(nodes(tree).filter(node => node.props?.['aria-label'] === '초안 검토자 이름').length, 1, 'one reviewer field');
});

test('a page whose tiles have all shown says the reviewer name is what keeps it from adopting', async () => {
  const queue = queueFixture({drafts: [row('d1', '/b/1.png'), row('d2', '/b/2.png')], reviewerName: ''});
  let tree = await queue.settle();
  buttonWith(tree, '초안 검토 패널').props.onClick(); tree = await queue.settle();
  buttonWith(tree, '격자 (한눈 검토)').props.onClick(); tree = await queue.settle();
  const grid = nodes(tree).find(node => node.type === 'DraftGrid');
  for (const id of ['d1', 'd2']) grid.props.onLoaded(id);
  tree = await queue.settle();
  const reason = '검토자 이름을 입력하면 이 페이지를 채택할 수 있습니다.';
  const adopt = buttonWith(tree, '이 페이지 채택');
  assert.equal(adopt.props.disabled, true);
  assert.equal(adopt.props.title, reason, 'the hover reason names the reviewer, not the tiles');
  assert.ok(nodes(tree).some(node => node.type === 'p' && text(node) === reason), 'and the same reason shows beside the button');
  queue.annotation.reviewerName = 'kai'; tree = await queue.settle();
  assert.equal(buttonWith(tree, '이 페이지 채택').props.disabled, false);
  assert.equal(buttonWith(tree, '이 페이지 채택').props.title, undefined);
  assert.ok(!nodes(tree).some(node => text(node) === reason));
});

test('drafts that found nothing are not waiting work and close together as rejections, labels untouched', async () => {
  const reviewed = [];
  const empty = (id, image) => ({...row(id, image), candidates: []});
  const queue = queueFixture({drafts: [row('d1', '/b/1.png'), empty('e1', '/b/2.png'), empty('e2', '/b/3.png')],
    workflow: {review: async (id, decision, ids, reviewer) => { reviewed.push([id, decision, ids, reviewer]); return {id, status: 'rejected'}; }}});
  let tree = await queue.settle();
  buttonWith(tree, '초안 검토 패널').props.onClick(); tree = await queue.settle();
  assert.match(text(tree), /검토 대기 1장/, 'only the draft with candidates waits');
  assert.match(text(tree), /후보 없는 초안 2건/);
  buttonWith(tree, '모두 닫기').props.onClick(); tree = await queue.settle();
  assert.deepEqual(reviewed, [['e1', 'reject', [], 'kai'], ['e2', 'reject', [], 'kai']]);
  assert.match(text(tree), /후보 없는 초안 2건을 닫았습니다. 라벨은 바뀌지 않았습니다/);
});

test('step 2\'s two right-side popovers open one at a time', async () => {
  const {useDraftReviewStore} = load(path.join(__dirname, '..', '..', 'stores', 'useDraftReviewStore.ts'));
  assert.equal(useDraftReviewStore.getState().sidePanel, null);
  useDraftReviewStore.getState().showSidePanel('assist');
  assert.equal(useDraftReviewStore.getState().sidePanel, 'assist');
  const queue = queueFixture({drafts: [row('d1', '/b/1.png')]});
  let tree = await queue.settle();
  buttonWith(tree, '초안 검토 패널').props.onClick(); tree = await queue.settle();
  assert.equal(queue.review.sidePanel, 'drafts', 'opening the queue names it, which closes 모델 보조');
  queue.review.sidePanel = 'assist'; tree = await queue.settle();
  assert.equal(byLabel(tree, '초안 검토 패널').props['aria-expanded'], false, '모델 보조 opening closes the queue');
  for (const file of ['DraftReviewQueue.tsx', 'ModelAssistPanel.tsx']) {
    const source = fs.readFileSync(path.join(__dirname, file), 'utf8');
    assert.match(source, /if \(sidePanel && sidePanel !== '(drafts|assist)'\) setOpen\(false\)/, file);
  }
});

test('the drafts load without waiting for the adoption numbers, a failed count shows nothing, and a changed-data model carries the full mark', async () => {
  const models = [{job_id: 'job_b', task: 'detection', data_state: 'changed'}];
  let queue = queueFixture({drafts: [row('d1', '/b/1.png')], models, stats: () => new Promise(() => {})});
  let tree = await queue.settle();
  assert.match(text(byLabel(tree, '초안 검토 패널')), /검토 대기 1장/);
  buttonWith(tree, '초안 검토 패널').props.onClick(); tree = await queue.settle();
  assert.equal(byLabel(tree, '모델 채택률'), undefined, 'nothing until the numbers arrive');
  assert.match(text(byLabel(tree, '초안 모델')), /job_b · detection · 데이터가 바뀐 뒤 학습한 모델 · 평가 근거 아님/);
  queue = queueFixture({drafts: [row('d1', '/b/1.png')], models, stats: async () => { throw new Error('offline'); }});
  tree = await queue.settle();
  buttonWith(tree, '초안 검토 패널').props.onClick(); tree = await queue.settle();
  assert.equal(byLabel(tree, '모델 채택률'), undefined);
  assert.doesNotMatch(text(tree), /아직 검토한 초안이 없습니다/, 'a failed count is not "none reviewed"');
});

test('another project\'s drafts leave the screen as soon as the scope changes', async () => {
  let pendingB;
  const queue = queueFixture({drafts: [], list: scope => scope === 'scope-B' ? {suggestions: [row('d1', '/b/1.png')]} : new Promise(resolve => { pendingB = resolve; })});
  let tree = await queue.settle();
  assert.match(text(byLabel(tree, '초안 검토 패널')), /검토 대기 1장/);
  queue.at.scope = 'scope-C';
  tree = await queue.settle();
  assert.ok(pendingB, 'the new scope is being read');
  assert.match(text(byLabel(tree, '초안 검토 패널')), /검토 대기 0장/);
});

test('모델 보조 hands over the candidates the person kept and marks a model trained before the data changed', async () => {
  const runtime = hookRuntime(), requests = [];
  const proposal = row('p1', '/b/1.png', {batch_id: undefined});
  const {ModelAssistPanel} = loadComponent('ModelAssistPanel.tsx', {
    react: runtime.react, 'lucide-react': icons,
    '../../services/api': {api: {labelSuggestions: {models: async () => ({models: [{job_id: 'job_x', task: 'detection', data_state: 'changed'}]}), list: async () => ({suggestions: [proposal]})}}},
    '../../stores/useAnnotationStore': {useAnnotationStore: fakeStore({currentImage: {file_path: '/b/1.png', file_name: '1.png'}, isDirty: false, annotationLoadStatus: 'ready', reviewerName: 'kai', setReviewerName() {}})},
    '../../stores/useDraftReviewStore': {draftReviewScope: input => `scope:${input.projectDir}`, useDraftReviewStore: fakeStore({requestOpen: (...args) => requests.push(args), sidePanel: null, showSidePanel() {}})},
    '../../stores/useDatasetStore': {useDatasetStore: fakeStore({images: [], hasSelectedFolder: true, annotationsChanged: async () => {}})},
    '../../stores/useProjectStore': {useProjectStore: fakeStore({projectDir: '/project-B', project: {active_labelset_id: 'default', task: 'detection', source_dataset_dir: '/source-B'}, task: 'detection'})},
    '../../stores/useModelAssistRunStore': {useModelAssistRunStore: fakeStore({begin() {}, end() {}})},
    './LabelAssistDeviceSizes': {LabelAssistDeviceSizes: 'LabelAssistDeviceSizes'}, './BulkLabelAssist': {BulkLabelAssist: 'BulkLabelAssist'},
    './KoreanConditionLabeler': {KoreanConditionLabeler: 'KoreanConditionLabeler'}, './CandidateProviderControls': {CandidateProviderControls: 'CandidateProviderControls'},
    '../../services/datasetWorkflow': {datasetWorkflow: {}}, './SuggestionPreview': {SuggestionPreview: 'SuggestionPreview'}, '../common/Explain': {Explain: 'Explain'},
    './draftReview': helpers(),
  });
  let tree = await runtime.settle(ModelAssistPanel);
  buttonWith(tree, '모델 보조 라벨링 패널').props.onClick(); tree = await runtime.settle(ModelAssistPanel);
  const option = nodes(tree).find(node => node.type === 'option' && node.props.value === 'job_x');
  assert.equal(text(option), 'job_x · detection · 데이터가 바뀐 뒤 학습한 모델 · 평가 근거 아님');
  assert.match(text(tree), /데이터가 바뀐 뒤 학습한 모델 · 평가 근거 아님 — 초안만 만들 수 있고/);
  const box = nodes(nodes(tree).find(node => node.type === 'label' && node.props.key === 'p1_c0')).find(node => node.type === 'input');
  box.props.onChange({target: {checked: false}}); tree = await runtime.settle(ModelAssistPanel);
  buttonWith(tree, '초안 검토에서 채택').props.onClick();
  assert.deepEqual(requests, [['p1', ['p1_c1'], 'scope:/project-B']]);
});

// U020: the product canvas and its work area take no keyboard focus, so a real canvas click leaves BODY or CANVAS as the
// key target (a tabIndex on the work area made it a DIV). '직접 라벨 달기' still brings the work area into view, and the
// footer button it was pressed from lets go of the keyboard.
test('the labeling work area takes no focus, and a footer jump to it releases the pressed button', () => {
  const studio = fs.readFileSync(path.join(__dirname, 'LabelingStudio.tsx'), 'utf8');
  const area = studio.match(/<div ref=\{workAreaRef\} id="labeling-canvas"[^>]*>/);
  assert.ok(area && !/tabIndex/.test(area[0]), 'no tabIndex on the work area');
  const readiness = fs.readFileSync(path.join(__dirname, '..', 'wizard', 'workflowReadiness.ts'), 'utf8');
  assert.match(readiness, /element\.focus\(\);[\s\S]{0,400}if\(document\.activeElement!==element&&document\.activeElement instanceof HTMLElement\)document\.activeElement\.blur\(\);/);
});

// U020 (second half): with more rows above the work area, the selected shape's fields outgrew the sidebar and a focused
// field scrolled the overflow-hidden work area, moving the canvas under the bars. The sidebar scrolls itself instead.
test('the labeling sidebar is its own scroll container and its list shrinks first', () => {
  const list = fs.readFileSync(path.join(__dirname, 'AnnotationList.tsx'), 'utf8');
  assert.match(list, /<div className="w-80 [^"]*flex flex-col h-full min-h-0 overflow-y-auto /);
  assert.match(list, /<div className="flex-1 min-h-0 overflow-y-auto p-2 space-y-1\.5">/);
});
