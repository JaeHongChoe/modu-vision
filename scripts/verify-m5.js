/**
 * scripts/verify-m5.js
 * Comprehensive Verification Suite for Milestone M5: React 4-Step Wizard UI & 3-Layer Interactive Canvas.
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
let failures = 0;
let passes = 0;

function assert(condition, message) {
  if (condition) {
    console.log(`  [PASS] ${message}`);
    passes++;
  } else {
    console.error(`  [FAIL] ${message}`);
    failures++;
  }
}

console.log('================================================================');
console.log('       Vision AI Studio: Milestone M5 Integrity Verification     ');
console.log('================================================================');

// --- Section 1: Types & Math Utilities ---
console.log('\n--- Section 1: Types & Coordinate Math ---');
const typesIndex = path.join(ROOT, 'src/renderer/types/index.ts');
assert(fs.existsSync(typesIndex), 'src/renderer/types/index.ts exists');
const typesContent = fs.readFileSync(typesIndex, 'utf8');
assert(typesContent.includes('export type VisionTask'), 'types/index.ts defines VisionTask');
assert(typesContent.includes('export interface ImageMeta'), 'types/index.ts defines ImageMeta');
assert(typesContent.includes('export interface AnnotationItem'), 'types/index.ts defines AnnotationItem');
assert(typesContent.includes('export interface HardwareStats'), 'types/index.ts defines HardwareStats');
assert(typesContent.includes('export interface ConfusionMatrixData'), 'types/index.ts defines ConfusionMatrixData');
assert(typesContent.includes('export interface TestPredictionItem'), 'types/index.ts defines TestPredictionItem');
assert(typesContent.includes('export interface EvaluationResults'), 'types/index.ts defines EvaluationResults');
assert(typesContent.includes('export interface ErrorCatalogItem'), 'types/index.ts defines ErrorCatalogItem');

const coordMath = path.join(ROOT, 'src/renderer/utils/coordinateMath.ts');
assert(fs.existsSync(coordMath), 'src/renderer/utils/coordinateMath.ts exists');
const coordContent = fs.readFileSync(coordMath, 'utf8');
assert(coordContent.includes('calculateZoomAtPoint'), 'coordinateMath.ts has cursor-centered zoom');
assert(coordContent.includes('calculateFitToScreen'), 'coordinateMath.ts has fit-to-screen transform');
assert(coordContent.includes('clampPointToImage'), 'coordinateMath.ts clamps points within bounds');
assert(coordContent.includes('getBBoxHandles'), 'coordinateMath.ts calculates 8 BBox handles');
assert(coordContent.includes('resizeBBoxWithHandle'), 'coordinateMath.ts resizes BBox with handles');
assert(coordContent.includes('moveBBox'), 'coordinateMath.ts translates BBox within bounds');
assert(coordContent.includes('pointInPolygon'), 'coordinateMath.ts performs point-in-polygon hit test');

// --- Section 2: API & WebSocket Services ---
console.log('\n--- Section 2: API & WebSocket Services ---');
const apiService = path.join(ROOT, 'src/renderer/services/api.ts');
assert(fs.existsSync(apiService), 'src/renderer/services/api.ts exists');
const apiContent = fs.readFileSync(apiService, 'utf8');
assert(apiContent.includes('getBackendPort'), 'api.ts dynamically resolves backend port');
assert(apiContent.includes('resolveApiUrl'), 'api.ts resolves asset URLs via http://127.0.0.1:${port}');
assert(apiContent.includes('/api/dataset/import'), 'api.ts binds dataset import endpoint');
assert(apiContent.includes('/api/dataset/generate'), 'api.ts binds dataset generate endpoint');
assert(apiContent.includes('/api/dataset/split'), 'api.ts binds dataset split endpoint');
assert(apiContent.includes('/api/dataset/images'), 'api.ts binds dataset images endpoint');
assert(apiContent.includes('/api/annotations/save'), 'api.ts binds annotation save endpoint');
assert(apiContent.includes('/api/training/start'), 'api.ts binds training start endpoint');
assert(apiContent.includes('/api/training/stop'), 'api.ts binds training stop endpoint');
assert(apiContent.includes('/api/evaluation/results'), 'api.ts binds evaluation results endpoint');
assert(apiContent.includes('/api/evaluation/heatmap/'), 'api.ts binds dynamic threshold heatmap endpoint');
assert(apiContent.includes('/api/report/export'), 'api.ts binds report export endpoint');
assert(apiContent.includes('/api/errors'), 'api.ts binds bilingual error catalog endpoint');

const wsService = path.join(ROOT, 'src/renderer/services/websocket.ts');
assert(fs.existsSync(wsService), 'src/renderer/services/websocket.ts exists');
const wsContent = fs.readFileSync(wsService, 'utf8');
assert(wsContent.includes('class WebSocketTelemetryService'), 'websocket.ts defines telemetry service class');
assert(wsContent.includes('/ws/telemetry'), 'websocket.ts connects to telemetry endpoint');
assert(wsContent.includes('reconnectTimer'), 'websocket.ts handles auto-reconnect');
assert(wsContent.includes('telemetryService'), 'websocket.ts exports telemetryService singleton');

// --- Section 3: Zustand State Stores ---
console.log('\n--- Section 3: Zustand State Stores ---');
const stores = [
  { file: 'useProjectStore.ts', checks: ['useProjectStore', 'activeStep', 'setTask', 'setLanguage', 'showError'] },
  { file: 'useDatasetStore.ts', checks: ['useDatasetStore', 'importFolder', 'generateSynthetic', 'applySplit', 'loadImages'] },
  { file: 'useAnnotationStore.ts', checks: ['useAnnotationStore', 'activeTool', 'addAnnotation', 'saveAnnotations', 'undo', 'redo', 'markNormal'] },
  { file: 'useTrainingStore.ts', checks: ['useTrainingStore', 'preset', 'startTraining', 'stopTraining', 'lossHistory', 'hardware'] },
  { file: 'useEvaluationStore.ts', checks: ['useEvaluationStore', 'confusionMatrix', 'selectCell', 'confidenceThreshold', 'exportReport'] },
];

stores.forEach((st) => {
  const p = path.join(ROOT, 'src/renderer/stores', st.file);
  assert(fs.existsSync(p), `stores/${st.file} exists`);
  const c = fs.readFileSync(p, 'utf8');
  st.checks.forEach((fn) => {
    assert(c.includes(fn), `stores/${st.file} includes ${fn}`);
  });
});

// --- Section 4: 4-Step Wizard UI & 3-Layer Canvas Components ---
console.log('\n--- Section 4: 4-Step Wizard UI & 3-Layer Canvas Components ---');
const components = [
  'wizard/WizardHeader.tsx',
  'wizard/WizardFooter.tsx',
  'dataset/ProceduralGeneratorModal.tsx',
  'dataset/DatasetStudio.tsx',
  'labeling/types.ts',
  'labeling/Layer1BaseImage.tsx',
  'labeling/Layer2MaskRaster.tsx',
  'labeling/Layer3VectorUI.tsx',
  'labeling/BoundingBoxTool.tsx',
  'labeling/PolygonTool.tsx',
  'labeling/BrushTool.tsx',
  'labeling/CanvasToolbar.tsx',
  'labeling/CanvasSidebar.tsx',
  'labeling/CanvasContainer.tsx',
  'labeling/CategorySelector.tsx',
  'labeling/AnnotationList.tsx',
  'labeling/MaskLayerControls.tsx',
  'labeling/ImageFilmstrip.tsx',
  'labeling/LabelingCanvas.tsx',
  'labeling/LabelingToolbar.tsx',
  'labeling/LabelingTool.tsx',
  'labeling/Step2Labeling.tsx',
  'training/TrainingController.tsx',
  'evaluation/EvaluationStudio.tsx',
  'common/ErrorModal.tsx',
  'common/ErrorDiagnosticsModal.tsx',
  'App.tsx',
  'main.tsx',
];

components.forEach((comp) => {
  const p = path.join(ROOT, 'src/renderer/components', comp.startsWith('App') || comp.startsWith('main') ? `../${comp}` : comp);
  assert(fs.existsSync(p), `src/renderer/${comp} exists`);
});

// --- Section 5: Production Bundle Verification ---
console.log('\n--- Section 5: Production Bundle Verification ---');
const distHtml = path.join(ROOT, 'dist/index.html');
assert(fs.existsSync(distHtml), 'dist/index.html exists');
const distAssets = fs.readdirSync(path.join(ROOT, 'dist/assets'));
const jsBundle = distAssets.find((f) => f.endsWith('.js'));
const cssBundle = distAssets.find((f) => f.endsWith('.css'));
assert(Boolean(jsBundle), `Production JS bundle found: ${jsBundle}`);
assert(Boolean(cssBundle), `Production CSS bundle found: ${cssBundle}`);

console.log('\n================================================================');
console.log(`   M5 INTEGRITY VERIFICATION RESULT: ${passes} PASSES, ${failures} FAILURES`);
console.log('================================================================\n');

if (failures > 0) {
  process.exit(1);
} else {
  process.exit(0);
}
