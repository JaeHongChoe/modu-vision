// Opt-in renderer geometry regression: node --test <this file>.
// Uses Chromium for CSS layout, with the real App/header/studio/bar markup and emitted application styles.
// Data stores are controlled, effects do not run during SSR, and the canvas drawing implementation is replaced by
// a measuring surface. This is layout evidence, not an actual-app/backend, keyboard or wheel integration test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const {chromium} = require('@playwright/test');
const postcss = require('postcss');
const tailwind = require('tailwindcss');
const root = path.resolve(__dirname, '../../../..');
const renderer = path.join(root, 'src/renderer');
const noop = () => {};
const store = state => Object.assign(select => select ? select(state) : state, {getState: () => state});
const categories = [{id: 0, name: 'OK', color: '#10b981'}, {id: 1, name: 'Scratch', color: '#ef4444'}, {id: 2, name: 'Dent', color: '#eab308'}];

function markup(focus = false, {reviewOpen = false, draftWide = false} = {}) {
  const image = {file_path: '/dataset/ok/large.png', file_name: 'large.png', image_id: 'large', label: 'OK', folder_label: 'OK', width: 4096, height: 2732};
  const project = {activeStep: 2, task: 'classification', language: 'ko', viewMode: 'full', theme: 'dark', projectName: 'Small window labeling',
    projectDir: '/project', project: {id: 'p1', project_dir: '/project', task: 'classification', source_dataset_dir: '/dataset', active_labelset_id: 'default'},
    backendStatus: {healthy: true, port: 5000, device: 'cpu'}, setStep: noop};
  const annotation = {images: [image], currentImage: image, currentImageIndex: 0, metadata: {image_uuid: 'image', workflow_state: 'unworked', tags: [], audit: [], revisions: []},
    task: 'classification', annotationLoadStatus: 'ready', annotations: [], categories, activeCategory: categories[1], reviewerName: 'worker',
    maskOpacity: 0.5, maskVisible: true, viewTransform: {scale: 0.2, offsetX: 0, offsetY: 0}, history: [], future: [], activeTool: 'select',
    isDirty: true, isSaving: false};
  const compute = {profiles: [], selectedProfileId: null, isLoaded: true, probeResults: {}, transportRevision: 0};
  const dataset = {images: [image], classes: {OK: 1, NG: 1}, page: 1, pageSize: 50, totalImagesCount: 1, hasSelectedFolder: true, totalImages: 1, sourceImages: 1, unlabeledImages: 1};
  const stateModules = {
    useProjectStore: {useProjectStore: store(project)},
    useAnnotationStore: {useAnnotationStore: store(annotation), DEFAULT_CATEGORIES: categories},
    useComputeStore: {useComputeStore: store(compute)},
    useDatasetStore: {useDatasetStore: store(dataset), readDatasetAuthority: () => 'scope'},
    useTrainingStore: {useTrainingStore: store({})},
    useEvaluationStore: {useEvaluationStore: store({testPredictions: []})},
    useFlowchartStore: {useFlowchartStore: store({})},
    useDraftReviewStore: {useDraftReviewStore: store({}), draftReviewScope: () => 'scope'},
    useModelAssistRunStore: {useModelAssistRunStore: store({})},
    useAssistantStore: {useAssistantStore: store({isOpen: false})},
    firstRunStore: {useFirstRunStore: store({open: false})},
  };
  const allowed = new Set(['App', 'WizardHeader', 'WizardFooter', 'LabelingTool', 'LabelingStudio', 'LabelingToolbar', 'LabelSetBar',
    'TeamDataPanel', 'ImageReviewPanel', 'ClassificationClassPicker', 'CategorySelector', 'DraftReviewQueue', 'ModelAssistPanel',
    'ImageFilmstrip', 'DerivedImagePanel', 'SavedReviewQueuePanel', 'WorkflowImpactPanel', 'AsyncAction',
    'labelingLayout', 'convertedAnnotation', 'coordinateMath', 'draftReview', 'popoverRoom', 'imageReadCustody',
    // A state hook the panels render through (ModelAssistPanel's error line); an empty stub returned undefined there.
    'useScopedError']);
  const empty = new Proxy({}, {get: (_, name) => name === '__esModule' ? true : noop});
  const cache = new Map();
  function compile(file) {
    if (cache.has(file)) return cache.get(file).exports;
    const mod = new Module(file, module); mod.filename = file; mod.paths = Module._nodeModulePaths(path.dirname(file)); cache.set(file, mod);
    const original = mod.require.bind(mod);
    mod.require = ref => {
      if (ref === 'react') return {...React, useLayoutEffect: React.useEffect};
      if (!ref.startsWith('.')) return original(ref);
      const name = path.basename(ref);
      if (stateModules[name]) return stateModules[name];
      if (name === 'useWorkflowReadiness') return {useWorkflowReadiness: () => ({readiness: {status: 'blocked', stage: 2,
        missingInputs: ['라벨을 확인하세요'], completedInputs: [], nextAction: {label: '직접 라벨 달기'}}, refresh: noop})};
      if (name === 'workflowReadiness') return {progressLine: () => ''};
      if (name === 'assistContext') return {assistPanelErrors: () => [], reportPanelError: noop};
      if (name === 'stepProgress') return {completedSteps: () => [1], mergeStepProgress: () => [1]};
      if (name === 'AutoProgressDialog') return {AUTO_TITLE: '원클릭 자동 진행', AutoProgressDialog: () => null, useAutoProgressStore: store({})};
      if (name === 'useDeliveryScope') return {useDeliveryScope: () => ({scope: {}, key: 'scope', projectDir: '/project'})};
      if (name === 'api') return {getApiPersistenceIdentity: () => 'local', getProjectContextGeneration: () => 0,
        subscribeProjectContext: noop, resolveApiUrl: () => 'data:,', getProjectContext: noop};
      if (name === 'teamDataWorkflow') return {teamDataScope: () => 'scope', imageLeaseToken: () => null};
      // SSR has no effects/DOM measurement; this is the room measured below the entry row in the short window.
      // The real hook's clipping/resize calculation is independently covered by popoverRoom.test.cjs.
      if (name === 'popoverRoom') return {usePopoverRoom: () => 260, usePopoverHorizontalBounds: () => draftWide ? {left: -330, width: 1000} : undefined};
      if (name === 'dataWorkbench') return {dataWorkbenchScope: () => 'scope', dataWorkbenchPersistenceScope: () => 'scope'};
      if (name === 'LabelingCanvas') return {LabelingCanvas: () => React.createElement('div', {'data-canvas-container': true, className: 'w-full h-full'})};
      if (name === 'AnnotationList') return {AnnotationList: () => React.createElement('aside', {className: 'w-80 h-full shrink-0'})};
      if (name === 'firstRun') return {EXAMPLE_NOTICE: '', shouldOpenFirstRun: () => false};
      if (!allowed.has(name)) return empty;
      const base = path.resolve(path.dirname(file), ref);
      const target = ['.tsx', '.ts'].map(ext => base + ext).find(candidate => fs.existsSync(candidate));
      return compile(target);
    };
    // Team settings are normally fetched after mount. Seed the real team's first state slots to exercise its visible
    // lock row and dirty-review warning without issuing requests or bypassing the production rendering conditions.
    if (path.basename(file) === 'TeamDataPanel.tsx') {
      const load = mod.require;
      let index = 0;
      mod.require = ref => ref === 'react' ? {...React, useState: initial => {
        const seed = index++;
        return React.useState(seed === 0 ? {scope: 'scope', value: {settings: {editing_enabled: true, review_enabled: true}}}
          : seed === 1 ? {counts: {approved: 0, total: 1}} : initial);
      }} : load(ref);
    }
    if (reviewOpen && path.basename(file) === 'ImageReviewPanel.tsx') {
      const load = mod.require;
      let index = 0;
      mod.require = ref => ref === 'react' ? {...React, useState: initial => React.useState(index++ === 0 ? true : initial)} : load(ref);
    }
    if (draftWide && path.basename(file) === 'DraftReviewQueue.tsx') {
      const load = mod.require;
      let index = 0;
      mod.require = ref => ref === 'react' ? {...React, useState: initial => {
        const slot = index++;
        return React.useState(slot === 0 || slot === 18 ? true : initial);
      }} : load(ref);
    }
    mod._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true}}).outputText, file);
    return mod.exports;
  }
  const previous = Object.fromEntries(['window', 'localStorage'].map(key => [key, Object.getOwnPropertyDescriptor(global, key)]));
  global.window = {api: {platform: 'darwin'}};
  Object.defineProperty(global, 'localStorage', {configurable: true, value: {getItem: () => focus ? '1' : '0'}});
  try { return renderToStaticMarkup(React.createElement(compile(path.join(renderer, 'App.tsx')).default)); }
  finally { for (const [key, value] of Object.entries(previous)) value === undefined ? delete global[key] : Object.defineProperty(global, key, value); }
}

test('full view at 1024×768 reserves a practical canvas and keeps save, classes, drafts and team warnings visible', async t => {
  const html = markup();
  const focusedHtml = markup(true);
  const reviewHtml = markup(false, {reviewOpen: true});
  const wideHtml = markup(false, {draftWide: true});
  const config = require(path.join(root, 'tailwind.config.js'));
  const css = await postcss([tailwind({...config, content: [{raw: html + focusedHtml + reviewHtml + wideHtml, extension: 'html'}]})])
    .process(fs.readFileSync(path.join(renderer, 'index.css'), 'utf8'), {from: path.join(renderer, 'index.css')});
  // The imports hold the shared component styles (workspace buttons and launcher rows).
  const imports = ['styles/design-tokens.css', 'styles/theme-light.css'].filter(file => fs.existsSync(path.join(renderer, file)))
    .map(file => fs.readFileSync(path.join(renderer, file), 'utf8')).join('\n');
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    await page.setViewportSize({width: 1024, height: 768});
    const styles = css.css.replace(/^@import[^;]+;/gm, '').replace(/url\(['"]?(\/fonts\/[^)'" ]+)['"]?\)/g, (_, name) =>
      `url(data:font/woff2;base64,${fs.readFileSync(path.join(root, 'public', name)).toString('base64')})`);
    const show = async contents => {
      await page.setContent(`<html lang="ko" data-view="full"><head><style>${imports}\n${styles}</style></head><body>${contents}</body></html>`);
      await page.evaluate(() => document.fonts.ready);
    };
    await show(html);
    const metrics = await page.evaluate(() => ({canvas: document.querySelector('[data-canvas-container]').getBoundingClientRect().height,
      header: document.querySelector('header').getBoundingClientRect().height, toolbar: document.querySelector('[data-testid="annotation-save-button"]').parentElement.parentElement.getBoundingClientRect().height}));
    const desktops = [];
    for (const viewport of [{width: 1280, height: 800}, {width: 1440, height: 900}, {width: 2560, height: 1440}]) {
      await page.setViewportSize(viewport);
      desktops.push({...viewport, canvas: await page.locator('[data-canvas-container]').evaluate(node => node.getBoundingClientRect().height)});
    }
    t.diagnostic(JSON.stringify({small: metrics, desktops}));
    await page.setViewportSize({width: 1024, height: 768});
    assert.ok(metrics.canvas >= 240, `full-view canvas must stay drawable: ${JSON.stringify(metrics)}`);
    for (const locator of [page.getByTestId('annotation-save-button'), page.getByRole('group', {name: '이미지 클래스 고르기'}),
      page.getByRole('button', {name: '초안 검토 패널'}), page.getByRole('button', {name: '모델 보조 라벨링 패널'}),
      page.getByRole('button', {name: '편집 시작', exact: true}), page.getByText('저장하면 이전 검수 무효화 · 재검수 필요')]) {
      const box = await locator.boundingBox();
      assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= 1024 && box.y + box.height <= 768, `entry stays in viewport: ${await locator.textContent()}`);
    }
    for (const button of await page.locator('[data-labeling-toolbar] button:visible').all()) {
      const box = await button.boundingBox();
      assert.ok(box && box.width >= 24 && box.height >= 24 && box.x >= 0 && box.x + box.width <= 1024,
        `each tool retains a visible target: ${await button.getAttribute('title') || await button.textContent()}`);
    }
    const settings = page.locator('[data-labeling-panels]');
    const sizes = await settings.evaluate(node => ({height: node.clientHeight, content: node.scrollHeight}));
    assert.ok(sizes.content > sizes.height, 'the settings strip gives up height before the canvas');
    for (const viewport of desktops) {
      // The existing dirty-review warning fixture has 221 px at 1440×900 and 376.8 px at 2560×1440. These larger
      // viewports must not lose height while fixing the short window.
      const minimum = viewport.width === 1280 ? 240 : viewport.width === 1440 ? 220 : 376;
      assert.ok(viewport.canvas >= minimum, `full view retains a practical canvas at ${viewport.width}: ${viewport.canvas}`);
    }
    await show(focusedHtml);
    const focused = await page.locator('[data-canvas-container]').evaluate(node => node.getBoundingClientRect().height);
    t.diagnostic(JSON.stringify({focused}));
    assert.ok(focused >= metrics.canvas + 20, `focus editing still gains drawing height: ${focused}`);
    assert.equal(await page.getByRole('button', {name: '집중 편집', exact: true}).getAttribute('aria-pressed'), 'true');
    assert.equal(await page.getByRole('button', {name: '초안 검토 패널'}).count(), 1, 'draft adoption stays visible in focus editing');
    await show(html);
    const restored = await page.locator('[data-canvas-container]').evaluate(node => node.getBoundingClientRect().height);
    assert.ok(Math.abs(restored - metrics.canvas) < 1, 'returning to full view restores the same drawing area');
    await show(reviewHtml);
    const review = await page.getByRole('region', {name: '이미지 정보와 검토 기록', exact: true}).boundingBox();
    const main = await page.locator('main').boundingBox();
    assert.ok(review.y + review.height <= main.y + main.height, `the image-review popover stays within its clipping workspace: ${JSON.stringify(review)}`);
    await show(wideHtml);
    const wide = await page.getByRole('complementary', {name: '초안 검토', exact: true}).boundingBox();
    assert.ok(wide.x >= main.x && wide.x + wide.width <= main.x + main.width,
      `the wide draft panel uses its measured horizontal bounds: ${JSON.stringify(wide)}`);
  } finally { await browser.close(); }
});
