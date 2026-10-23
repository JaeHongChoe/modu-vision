// Design 5.3-2: step 2 without a model says '직접 라벨 달기'; pressed on a labelled image it opens the first image
// still without a label, through the guarded open (edits saved first, nothing moves when that save fails).
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

function load({items = [], open = '/src/img01.png', openResult = true, read, task = 'detection'} = {}) {
  const calls = {reads: [], opens: []};
  const dataset = {folderPath: '/src', hasSelectedFolder: true};
  const project = {task, isProjectBusy: false, openImageForLabeling: async (...args) => { calls.opens.push(args); return openResult; }};
  const mocks = {
    '../../services/api': {api: {dataset: {getImages: async params => { calls.reads.push(params); return read ? read(dataset, project) : {items, total: items.length}; }}}},
    '../../stores/useAnnotationStore': {useAnnotationStore: {getState: () => ({currentImage: open ? {file_path: open} : null})}},
    '../../stores/useDatasetStore': {useDatasetStore: {getState: () => dataset}},
    '../../stores/useProjectStore': {useProjectStore: {getState: () => project}},
  };
  const filename = path.join(__dirname, 'unlabeledImage.ts'), m = new Module(filename, module);
  m.filename = filename;
  m.require = name => mocks[name] ?? require(name);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, filename);
  return {...m.exports, calls, dataset, project};
}
const image = name => ({image_id: name, file_path: `/src/${name}.png`});

test('the chooser opens the first unlabelled image unless the open one still has no label', () => {
  const {unlabeledImageToOpen} = load();
  assert.equal(unlabeledImageToOpen([], '/src/a.png'), null);
  assert.equal(unlabeledImageToOpen([image('a'), image('b')], '/src/b.png'), null, 'the open image is itself unlabelled: stay');
  assert.deepEqual(unlabeledImageToOpen([image('b')], '/src/a.png'), image('b'));
  assert.deepEqual(unlabeledImageToOpen([image('b')], null), image('b'));
});

test('on a labelled image it reads the unlabelled list once and opens the first through the guarded open', async () => {
  const run = load({items: [image('img17')]});
  assert.equal(await run.openUnlabeledImage(), true);
  assert.deepEqual(run.calls.reads, [{folder_path: '/src', task: 'detection', label_status: 'unlabeled', limit: 500}]);
  assert.deepEqual(run.calls.opens, [['img17', '/src/img17.png']]);
});

test('it stays when the open image is unlabelled, when the save of its edits fails, when the read fails or the source changed', async () => {
  const stay = load({items: [image('img01'), image('img17')]});
  assert.equal(await stay.openUnlabeledImage(), false); assert.deepEqual(stay.calls.opens, []);
  const saveFailed = load({items: [image('img17')], openResult: false});
  assert.equal(await saveFailed.openUnlabeledImage(), false); assert.equal(saveFailed.calls.opens.length, 1);
  const failedRead = load({read: () => { throw new Error('offline'); }});
  assert.equal(await failedRead.openUnlabeledImage(), false);
  const moved = load({read: dataset => { dataset.folderPath = '/other'; return {items: [image('img17')], total: 1}; }});
  assert.equal(await moved.openUnlabeledImage(), false); assert.deepEqual(moved.calls.opens, []);
  const source = fs.readFileSync(path.join(__dirname, 'unlabeledImage.ts'), 'utf8');
  assert.ok(!/setImages\(|selectImageByIndex\(|setState\(/.test(source), 'the guarded open is the only way it moves');
});

test('the footer runs it only for 직접 라벨 달기 and then brings the work area into view', () => {
  const footer = fs.readFileSync(path.join(__dirname, '..', 'wizard', 'WizardFooter.tsx'), 'utf8');
  assert.match(footer, /if\(readiness\.nextAction\.target==='labeling-canvas'\)await openUnlabeledImage\(\);focusWorkflowTarget\(readiness\.nextAction\.target\);/);
});
