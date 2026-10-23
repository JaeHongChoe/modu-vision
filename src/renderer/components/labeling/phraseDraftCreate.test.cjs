// Design 3.3 ③: an empty project drafts its unlabeled images from a phrase, through the one draft queue.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

function load() {
  const filename = path.join(__dirname, 'PhraseDraftCreate.tsx'), m = new Module(filename, module);
  m.filename = filename;
  const stub = new Proxy({}, {get: () => () => ({})});
  m.require = name => name === 'react/jsx-runtime' ? {jsx: (type, props) => ({type, props}), jsxs: (type, props) => ({type, props})}
    : name === 'react' ? {useEffect() {}, useRef: value => ({current: value}), useState: value => [value, () => {}]}
    : name === '../../services/foundationLabelingApi' ? {foundationLabelingApi: stub, labelingJobActive: status => ['queued', 'running', 'cancelling'].includes(status)}
    : stub;
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX}}).outputText, filename);
  return m.exports;
}
const ready = (text, mask, dependency = true) => ({labelset_id: 'default', labelset_version: 'v1', configuration: {},
  providers: {grounding_dino: {ready: text, dependency_available: dependency}, foundation: {ready: mask, dependency_available: dependency}}});

test('detection drafts need only the text model; segmentation outlines need SAM2 too; each blocker says what to do', () => {
  const {phraseDraftBlocked} = load();
  assert.equal(phraseDraftBlocked(ready(true, false), 'detection'), null);
  assert.match(phraseDraftBlocked(ready(true, false), 'segmentation'), /SAM2 모델 폴더도 필요합니다/);
  assert.match(phraseDraftBlocked(ready(false, true), 'detection'), /문구 모델\(Grounding DINO\) 폴더/);
  assert.match(phraseDraftBlocked(ready(false, false, false), 'detection'), /transformers가 없습니다/);
  assert.match(phraseDraftBlocked(null, 'detection'), /읽는 중/);
});

test('the batch covers the unlabeled images with the phrase and chosen class; boxes for detection, outlines for segmentation', () => {
  const {phraseDraftRequest} = load();
  const base = {prompt: '  scratch, 긁힘 ', label: 'Scratch', classIds: {Scratch: 1}, imagePaths: ['/s/a.png'], setup: ready(true, true)};
  const detection = phraseDraftRequest({...base, task: 'detection'});
  assert.deepEqual(detection, {backend: 'grounding_dino', prompt: 'scratch, 긁힘', label: 'Scratch', class_ids: {Scratch: 1}, image_paths: ['/s/a.png'],
    output_geometry: 'bbox', threshold: 0.3, text_threshold: 0.25, max_candidates: 20, device: 'cpu', labelset_id: 'default', labelset_version: 'v1'});
  const segmentation = phraseDraftRequest({...base, task: 'segmentation'});
  assert.equal(segmentation.backend, 'foundation');
  assert.equal(segmentation.output_geometry, 'polygon');
});

test('the batch line is Korean and names failures only when there are some', () => {
  const {phraseBatchLine} = load();
  assert.equal(phraseBatchLine({status: 'running', processed: 3, total: 24, generated: 2, zero_candidates: 1, failed: 0}), '만드는 중 · 3/24장 · 초안 2장 · 찾지 못함 1장');
  assert.equal(phraseBatchLine({status: 'completed', processed: 24, total: 24, generated: 20, zero_candidates: 3, failed: 1}), '끝남 · 24/24장 · 초안 20장 · 찾지 못함 3장 · 실패 1장');
});

test('an empty detection or segmentation project offers the phrase drafts in 1. 초안 만들기, and they land in the same queue', () => {
  const queue = fs.readFileSync(path.join(__dirname, 'DraftReviewQueue.tsx'), 'utf8');
  assert.match(queue, /\{models\.length === 0 \? <>[\s\S]{0,300}\(task === 'detection' \|\| task === 'segmentation'\) && <PhraseDraftCreate key=\{scope\} task=\{task\} disabled=\{!!busy \|\| isDirty\} pendingImages=\{pendingImagePaths\} onRunningChange=\{setBatchRunning\} \/>/);
  const source = fs.readFileSync(path.join(__dirname, 'PhraseDraftCreate.tsx'), 'utf8');
  assert.match(source, /label_status: 'unlabeled'/, 'only unlabeled images are drafted');
  assert.match(source, /await collectDraftPaths\(\(offset\) => api\.dataset\.getImages\(\{[^}]*label_status: 'unlabeled'[^}]*\}\), pendingImages\)/, 'an image with a waiting draft is not drafted twice');
  assert.match(source, /const \[label, setLabel\] = useState\(''\);/, 'no class chosen means each phrase as typed');
  assert.match(source, /<option value="">문구 이름 그대로<\/option>/);
  assert.match(source, /if \(!labelingJobActive\(next\.status\)\) bumpWorkflowEvidence\(\);/, 'the footer and queue read the new drafts');
  assert.match(source, /className=\{pendingImages\.size \? 'workspace-button' : 'workspace-button workspace-button--primary'\}/, 'one main action: review first while drafts wait');
});

test('SAM2 클릭으로 그리기 opens 모델 보조 even in focus editing, for this visit only, and the request is used once', () => {
  const source = fs.readFileSync(path.join(__dirname, 'PhraseDraftCreate.tsx'), 'utf8');
  assert.match(source, /onClick=\{\(\) => useDraftReviewStore\.getState\(\)\.requestAssist\(\)\}>SAM2 클릭으로 그리기<\/button>/);
  const studio = fs.readFileSync(path.join(__dirname, 'LabelingStudio.tsx'), 'utf8');
  assert.match(studio, /\{focus && assistUnfolded \? helperPanels\['모델 보조'\] : folded\('모델 보조'\)\}/);
  assert.match(studio, /useEffect\(\(\) => \{ if \(assistRequest\) setAssistUnfolded\(true\); \}, \[assistRequest\]\);/, 'unfolded for this visit only');
  assert.ok(!/setAssistUnfolded\(true\)[^\n]*writeLabelingFocus/.test(studio), 'the stored focus choice is not changed');
  const panel = fs.readFileSync(path.join(__dirname, 'ModelAssistPanel.tsx'), 'utf8');
  assert.match(panel, /if \(assistRequest\) \{ setOpen\(true\); useDraftReviewStore\.getState\(\)\.clearAssistRequest\(\); \}/);
});

test('the batch reads every page, leaves out images whose waiting draft found something, and stops at the cap', async () => {
  const {collectDraftPaths} = load();
  const all = Array.from({length: 1200}, (_, i) => `/s/${i}.png`);
  const reads = [];
  const page = async offset => { reads.push(offset); return {items: all.slice(offset, offset + 500).map(file_path => ({file_path})), total: all.length}; };
  const pending = new Set(['/s/0.png', '/s/700.png']);
  const read = await collectDraftPaths(page, pending);
  assert.deepEqual(reads, [0, 500, 1000]);
  assert.equal(read.paths.length, 1198); assert.equal(read.more, false);
  assert.ok(!read.paths.includes('/s/700.png'));
  reads.length = 0;
  const capped = await collectDraftPaths(page, pending, 600);
  assert.equal(capped.paths.length, 600); assert.equal(capped.more, true, 'the rest waits for the next batch');
  assert.deepEqual(reads, [0, 500], 'no page is read past the cap');
});

test('the queue holds back only images whose draft found something, and the panel remounts per project', () => {
  const queue = fs.readFileSync(path.join(__dirname, 'DraftReviewQueue.tsx'), 'utf8');
  assert.match(queue, /drafts\.filter\(\(draft\) => draft\.status === 'pending' && draft\.candidates\.length > 0\)/);
  assert.match(queue, /<PhraseDraftCreate key=\{scope\}/);
});

test('refusals and batch reasons reach the panel in Korean', () => {
  const filename = path.join(__dirname, 'draftReview.ts'), m = new Module(filename, module);
  m.filename = filename;
  m.require = name => name.endsWith('/services/datasetWorkflow') ? {workflowError: error => error.message} : require(name);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, filename);
  const {draftRefusalText, draftError} = m.exports;
  assert.match(draftError(new Error('Choose an existing local model directory')), /폴더로 찾지 못했습니다/);
  assert.match(draftError(new Error('A labeling task is already running for this project')), /진행 중입니다/);
  assert.match(draftRefusalText('실패한 이미지가 있습니다 · 첫 이유: Source dataset changed during labeling batch'), /^실패한 이미지가 있습니다 · 첫 이유: 초안을 만드는 동안 데이터가 바뀌어/);
  assert.equal(draftRefusalText('그대로'), '그대로');
});
