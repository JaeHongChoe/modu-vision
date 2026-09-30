/**
 * scripts/verify-packaging.js
 *
 * Automated Packaging & Artifact Validation Suite for Vision AI Studio (Milestone M6).
 * Verifies:
 * 1. Production frontend web assets in dist/ (index.html, bundles).
 * 2. Electron compiled main & preload scripts in dist-electron/.
 * 3. electron-builder unpacked macOS application bundle in release/mac-arm64/.
 * 4. ASAR packaging integrity (app.asar present in Resources/).
 * 5. Python backend extraction outside ASAR in Resources/backend/.
 * 6. Exclusion filters (tests, pycache, .pyc excluded from production backend bundle).
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT_DIR = path.resolve(__dirname, '..');

console.log('================================================================');
console.log('    Vision AI Studio: Milestone M6 Packaging Integrity Check    ');
console.log('================================================================');
console.log(`[INFO] Working Directory: ${ROOT_DIR}\n`);

let passes = 0;
let failures = 0;

function assert(condition, message) {
  if (condition) {
    console.log(`  [PASS] ${message}`);
    passes++;
  } else {
    console.error(`  [FAIL] ${message}`);
    failures++;
  }
}

// 1. Production Web Bundle Verification
console.log('--- Step 1: Validating Production Frontend Assets (dist/) ---');
const distDir = path.join(ROOT_DIR, 'dist');
assert(fs.existsSync(distDir), 'dist/ directory exists');

const indexHtml = path.join(distDir, 'index.html');
assert(fs.existsSync(indexHtml), 'dist/index.html exists');
const indexHtmlContent = fs.readFileSync(indexHtml, 'utf8');
assert(indexHtmlContent.includes('<div id="root">'), 'dist/index.html contains root mounting point');

const assetsDir = path.join(distDir, 'assets');
assert(fs.existsSync(assetsDir), 'dist/assets/ directory exists');
const assetFiles = fs.readdirSync(assetsDir);
const jsBundle = assetFiles.find(f => f.endsWith('.js'));
const cssBundle = assetFiles.find(f => f.endsWith('.css'));
assert(Boolean(jsBundle), `Production JavaScript bundle present (${jsBundle})`);
assert(Boolean(cssBundle), `Production CSS stylesheet present (${cssBundle})`);

// 2. Electron Main & Preload Verification
console.log('\n--- Step 2: Validating Electron Compiled Scripts (dist-electron/) ---');
const distElectronDir = path.join(ROOT_DIR, 'dist-electron');
assert(fs.existsSync(distElectronDir), 'dist-electron/ directory exists');

const mainEntry = path.join(distElectronDir, 'main', 'index.js');
const supervisor = path.join(distElectronDir, 'main', 'supervisor.js');
const ipc = path.join(distElectronDir, 'main', 'ipc.js');
const preloadEntry = path.join(distElectronDir, 'preload', 'index.js');

assert(fs.existsSync(mainEntry), 'dist-electron/main/index.js exists');
assert(fs.existsSync(supervisor), 'dist-electron/main/supervisor.js exists');
assert(fs.existsSync(ipc), 'dist-electron/main/ipc.js exists');
assert(fs.existsSync(preloadEntry), 'dist-electron/preload/index.js exists');

// 3. Unpacked Application Bundle & ASAR Verification
console.log('\n--- Step 3: Validating Unpacked Desktop Bundle (release/mac-arm64/) ---');
const appOutDir = process.env.VISION_AI_STUDIO_PACKAGE_DIR
  ? path.resolve(process.env.VISION_AI_STUDIO_PACKAGE_DIR)
  : path.join(ROOT_DIR, 'release', 'mac-arm64');
assert(fs.existsSync(appOutDir), 'release/mac-arm64/ directory exists');

const appBundle = path.join(appOutDir, 'Vision AI Studio.app');
assert(fs.existsSync(appBundle), 'Vision AI Studio.app application bundle exists');

const contentsDir = path.join(appBundle, 'Contents');
const macOSDir = path.join(contentsDir, 'MacOS');
const resourcesDir = path.join(contentsDir, 'Resources');

assert(fs.existsSync(contentsDir), 'Contents/ directory exists in bundle');
assert(fs.existsSync(macOSDir), 'Contents/MacOS/ directory exists in bundle');
assert(fs.existsSync(resourcesDir), 'Contents/Resources/ directory exists in bundle');

const appAsar = path.join(resourcesDir, 'app.asar');
assert(fs.existsSync(appAsar), 'Contents/Resources/app.asar exists');
const asarStats = fs.statSync(appAsar);
assert(asarStats.size > 1000000, `app.asar size is valid (${(asarStats.size / (1024 * 1024)).toFixed(2)} MB)`);
const asar = require('@electron/asar');
const compiledFiles = [
  'dist/index.html',
  ...assetFiles.map(name => `dist/assets/${name}`),
  ...['main/index.js', 'main/supervisor.js', 'main/ipc.js', 'preload/index.js'].map(name => `dist-electron/${name}`),
];
const staleAssets = compiledFiles.filter(relative => {
  try {
    const expected = fs.readFileSync(path.join(ROOT_DIR, relative));
    return !asar.extractFile(appAsar, relative).equals(expected);
  } catch { return true; }
});
assert(staleAssets.length === 0, `Packaged renderer and Electron scripts match current build (stale/missing: ${staleAssets.slice(0, 5).join(', ') || 'none'})`);

// 4. Backend Extraction Outside ASAR Verification
console.log('\n--- Step 4: Validating Python Backend Extraction Outside ASAR ---');
const backendResources = path.join(resourcesDir, 'backend');
assert(fs.existsSync(backendResources), 'Resources/backend/ exists unpacked outside ASAR');

const mainPy = path.join(backendResources, 'main.py');
const apiDir = path.join(backendResources, 'api');
const engineDir = path.join(backendResources, 'engine');
const utilsDir = path.join(backendResources, 'utils');

assert(fs.existsSync(mainPy), 'Resources/backend/main.py entry point exists');
assert(fs.existsSync(apiDir), 'Resources/backend/api/ directory exists');
assert(fs.existsSync(engineDir), 'Resources/backend/engine/ directory exists');
assert(fs.existsSync(utilsDir), 'Resources/backend/utils/ directory exists');

function pythonSources(directory, relative = '') {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const rel = path.join(relative, entry.name);
    if (entry.isDirectory()) {
      return ['tests', '__pycache__', '.pytest_cache'].includes(entry.name) ? [] : pythonSources(path.join(directory, entry.name), rel);
    }
    return entry.isFile() && entry.name.endsWith('.py') ? [rel] : [];
  });
}
const digest = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const staleBackend = pythonSources(path.join(ROOT_DIR, 'backend')).filter(relative => {
  const packaged = path.join(backendResources, relative);
  return !fs.existsSync(packaged) || digest(packaged) !== digest(path.join(ROOT_DIR, 'backend', relative));
});
assert(staleBackend.length === 0, `Packaged backend matches current source (stale/missing: ${staleBackend.slice(0, 5).join(', ') || 'none'})`);
for (const name of ['inspection-service-client.mjs', 'InspectionServiceClient.cs']) {
  const packaged = path.join(resourcesDir, 'examples', name);
  assert(fs.existsSync(packaged) && digest(packaged) === digest(path.join(ROOT_DIR, 'examples', name)), `Flow export integration client packaged: ${name}`);
}

// 5. Exclusion Filters Integrity (No test files or pycaches packaged)
console.log('\n--- Step 5: Validating Packaging Exclusion Filters ---');
const testsInBackend = path.join(backendResources, 'tests');
assert(!fs.existsSync(testsInBackend), 'backend/tests/ directory is properly excluded from packaging');

const pytestCache = path.join(backendResources, '.pytest_cache');
assert(!fs.existsSync(pytestCache), '.pytest_cache is properly excluded from packaging');

function checkNoPycache(dir) {
  let hasPycache = false;
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (entry.name === '__pycache__') {
        hasPycache = true;
      } else {
        if (checkNoPycache(path.join(dir, entry.name))) {
          hasPycache = true;
        }
      }
    }
  }
  return hasPycache;
}

const pycacheFound = checkNoPycache(backendResources);
assert(!pycacheFound, '__pycache__ directories excluded from backend package');

console.log('\n================================================================');
console.log(`   PACKAGING INTEGRITY VERIFICATION: ${passes} PASSES, ${failures} FAILURES`);
console.log('================================================================\n');

if (failures > 0) {
  process.exit(1);
} else {
  process.exit(0);
}
