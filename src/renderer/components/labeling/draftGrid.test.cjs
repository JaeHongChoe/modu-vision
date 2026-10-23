// The contact-sheet tiles: a tile is a button that includes or excludes its draft; nothing counts as seen before the
// image loaded, and the overlay only draws once it has.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');
const React = require('react'); const {renderToStaticMarkup} = require('react-dom/server');
function load(file, mocks) {
  const name = path.resolve(__dirname, file); const mod = new Module(name, module); mod.filename = name; mod.paths = Module._nodeModulePaths(path.dirname(name));
  const original = mod.require.bind(mod);
  mod.require = ref => Object.hasOwn(mocks, ref) ? mocks[ref] : ref === './draftReview' ? load('draftReview.ts', {'../../services/datasetWorkflow': {workflowError: e => String(e?.message || e)}}) : original(ref);
  mod._compile(ts.transpileModule(fs.readFileSync(name, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX}}).outputText, name);
  return mod.exports;
}
const loads = [];
const {DraftGrid} = load('DraftGrid.tsx', {
  '../common/ScopedDatasetThumbnail': {ScopedDatasetThumbnail: props => { loads.push(props.path); return React.createElement('img', {alt: props.alt, 'data-path': props.path}); }},
});
const draft = (id, image, confidences) => ({id, status: 'pending', created_at: '2026-10-22T00:00:00Z', image_path: image, image_id: image.split('/').pop().replace('.png', ''), image_width: 100, image_height: 50,
  job_id: 'job_1', batch_id: 'batch_1', candidates: confidences.map((confidence, index) => ({id: `${id}_c${index}`, confidence, annotation: {type: 'bbox', label: 'defect', bbox: [10, 10, 30, 30]}})), accepted_candidate_ids: []});
const render = (props) => renderToStaticMarkup(React.createElement(DraftGrid, {choices: new Map(), excluded: new Set(), loaded: new Set(), authority: 'a', context: null, current: () => true, disabled: false,
  onToggle() {}, onLoaded() {}, onOpen() {}, ...props}));

test('tiles are pressed buttons with a 256px lazy thumbnail; an unloaded tile says so and draws no overlay', () => {
  const html = render({drafts: [draft('d1', '/x/a.png', [0.9]), draft('d2', '/x/b.png', [0.4])], excluded: new Set(['d2'])});
  assert.match(html, /aria-label="a\.png 이 페이지 채택에 포함"[^>]*aria-pressed="true"/.source ? /aria-pressed="true"[^>]*aria-label="a\.png 이 페이지 채택에 포함"|aria-label="a\.png 이 페이지 채택에 포함"[^>]*aria-pressed="true"/ : /./);
  assert.match(html, /aria-pressed="false"[^>]*aria-label="b\.png 이 페이지 채택에 포함"|aria-label="b\.png 이 페이지 채택에 포함"[^>]*aria-pressed="false"/);
  assert.ok(loads.every(url => url.includes('size=256')), 'tiles read the small thumbnail');
  assert.equal((html.match(/불러오는 중 · 보인 뒤 채택 가능/g) || []).length, 2);
  assert.doesNotMatch(html, /<rect/, 'no overlay before the image loaded');
  assert.match(html, /학습 모델/);
});

test('a loaded tile draws its chosen candidates with a screen-width stroke', () => {
  const html = render({drafts: [draft('d1', '/x/a.png', [0.9, 0.5])], loaded: new Set(['d1']), choices: new Map([['d1', new Set(['d1_c0'])]])});
  assert.equal((html.match(/<rect/g) || []).length, 1, 'only the chosen candidate');
  assert.match(html, /vector-effect="non-scaling-stroke"/);
  assert.doesNotMatch(html, /불러오는 중/);
});

test('a tag draft names its class once the tile has shown, and boxes name their distinct chosen classes', () => {
  // Classification and anomaly drafts carry one whole-image tag the overlay cannot draw.
  const tag = (id, label) => ({...draft(id, `/x/${id}.png`, [0.87]), job_id: 'job_cls', candidates: [{id: `${id}_c1`, confidence: 0.87, annotation: {type: 'tag', label, category_id: 2}}]});
  const shown = render({drafts: [tag('a', 'scratch'), tag('b', 'OK')], loaded: new Set(['a', 'b'])});
  assert.match(shown, />scratch</);
  assert.match(shown, />OK</);
  const unseen = render({drafts: [tag('a', 'scratch')]});
  assert.doesNotMatch(unseen, /scratch/, 'nothing is shown as seen before the image loaded');
  const boxes = {...draft('d1', '/x/a.png', [0.9, 0.8, 0.7]), candidates: [
    {id: 'd1_c0', confidence: 0.9, annotation: {type: 'bbox', label: 'dent', bbox: [1, 1, 5, 5]}},
    {id: 'd1_c1', confidence: 0.8, annotation: {type: 'bbox', label: 'dent', bbox: [6, 6, 9, 9]}},
    {id: 'd1_c2', confidence: 0.7, annotation: {type: 'polygon', label: 'crack', polygon: [[1, 1], [4, 1], [4, 4]]}}]};
  assert.match(render({drafts: [boxes], loaded: new Set(['d1'])}), />dent · crack</);
  assert.match(render({drafts: [boxes], loaded: new Set(['d1']), choices: new Map([['d1', new Set(['d1_c0'])]])}), />dent</, 'only the chosen candidates');
  assert.doesNotMatch(render({drafts: [boxes], loaded: new Set(['d1']), choices: new Map([['d1', new Set(['d1_c0'])]])}), /crack/);
});
