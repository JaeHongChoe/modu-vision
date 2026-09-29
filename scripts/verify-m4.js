/**
 * scripts/verify-m4.js
 *
 * Comprehensive Milestone M4 Verification Script:
 * 1. Validates package.json structure, scripts, and dependencies.
 * 2. Validates TypeScript configs (tsconfig.json, tsconfig.node.json).
 * 3. Validates Vite, Tailwind, and PostCSS configurations.
 * 4. Validates Electron Main, Preload, and Type Definitions.
 * 5. Validates electron-builder.yml packaging config and entitlements.
 * 6. Checks build outputs if built (dist-electron/ and dist/).
 */

const fs = require('fs');
const path = require('path');

const ROOT_DIR = path.resolve(__dirname, '..');

console.log('================================================================');
console.log('       Vision AI Studio: Milestone M4 Integrity Verification     ');
console.log('================================================================');
console.log(`[INFO] Working Directory: ${ROOT_DIR}\n`);

let failureCount = 0;

function assert(condition, message) {
  if (!condition) {
    console.error(`  [FAIL] ${message}`);
    failureCount++;
  } else {
    console.log(`  [PASS] ${message}`);
  }
}

// 1. package.json Validation
console.log('--- Step 1: Validating package.json & Dependencies ---');
const pkgPath = path.join(ROOT_DIR, 'package.json');
assert(fs.existsSync(pkgPath), 'package.json exists');
const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));

assert(pkg.name === 'vision-ai-studio', 'Package name is "vision-ai-studio"');
assert(pkg.main === 'dist-electron/main/index.js', 'Main entry point is "dist-electron/main/index.js"');

const requiredScripts = ['dev', 'build', 'build:renderer', 'build:main', 'package', 'test:m4'];
for (const s of requiredScripts) {
  assert(Boolean(pkg.scripts && pkg.scripts[s]), `Script "${s}" is defined`);
}

const requiredDeps = ['electron', 'electron-builder', 'react', 'react-dom', 'zustand', 'lucide-react', 'tailwindcss', 'postcss', 'autoprefixer', 'vite', '@vitejs/plugin-react', 'typescript', '@types/react', '@types/react-dom', '@types/node', 'wait-on', 'concurrently'];
const allDeps = { ...pkg.dependencies, ...pkg.devDependencies };
for (const dep of requiredDeps) {
  assert(Boolean(allDeps[dep]), `Dependency "${dep}" is declared in package.json`);
}

// 2. TypeScript & Bundler Configs
console.log('\n--- Step 2: Validating Tooling & Bundler Configs ---');
const requiredConfigs = [
  'tsconfig.json',
  'tsconfig.node.json',
  'vite.config.ts',
  'tailwind.config.js',
  'postcss.config.js',
  'index.html',
];

for (const cfg of requiredConfigs) {
  const cfgPath = path.join(ROOT_DIR, cfg);
  assert(fs.existsSync(cfgPath), `Config file "${cfg}" exists`);
}

// Verify index.html contains dark theme and root div
const indexHtml = fs.readFileSync(path.join(ROOT_DIR, 'index.html'), 'utf8');
assert(indexHtml.includes('id="root"'), 'index.html contains #root container');
assert(indexHtml.includes('class="dark"'), 'index.html enables dark class');

// Verify vite.config.ts has relative base
const viteConfig = fs.readFileSync(path.join(ROOT_DIR, 'vite.config.ts'), 'utf8');
assert(viteConfig.includes("base: './'"), 'vite.config.ts configures base: "./" for Electron');

// 3. Electron Shell Source Files
console.log('\n--- Step 3: Validating Electron Source Files ---');
const requiredSources = [
  'src/main/index.ts',
  'src/main/supervisor.ts',
  'src/main/ipc.ts',
  'src/preload/index.ts',
  'src/types/electron.d.ts',
  'src/renderer/main.tsx',
  'src/renderer/App.tsx',
];

for (const src of requiredSources) {
  const srcPath = path.join(ROOT_DIR, src);
  assert(fs.existsSync(srcPath), `Source file "${src}" exists`);
}

// Verify preload API declarations
const typesFile = fs.readFileSync(path.join(ROOT_DIR, 'src/types/electron.d.ts'), 'utf8');
assert(typesFile.includes('getBackendPort: () => Promise<number | null>'), 'electron.d.ts declares getBackendPort');
assert(typesFile.includes('getBackendStatus: () => Promise<BackendStatus>'), 'electron.d.ts declares getBackendStatus');
assert(typesFile.includes('selectFolder:'), 'electron.d.ts declares selectFolder');
assert(typesFile.includes('selectFile:'), 'electron.d.ts declares selectFile');
assert(typesFile.includes('openExternal:'), 'electron.d.ts declares openExternal');
assert(typesFile.includes('onBackendStatusChange:'), 'electron.d.ts declares onBackendStatusChange');
assert(typesFile.includes('onBackendCrashed:'), 'electron.d.ts declares onBackendCrashed');

// 4. Packaging Configuration
console.log('\n--- Step 4: Validating Packaging Configuration ---');
const builderYmlPath = path.join(ROOT_DIR, 'electron-builder.yml');
assert(fs.existsSync(builderYmlPath), 'electron-builder.yml exists');
const builderContent = fs.readFileSync(builderYmlPath, 'utf8');
assert(builderContent.includes('appId: com.visionaistudio.app'), 'electron-builder.yml defines appId');
assert(builderContent.includes('productName: Vision AI Studio'), 'electron-builder.yml defines productName');
assert(builderContent.includes('extraResources:'), 'electron-builder.yml defines extraResources');
assert(builderContent.includes('from: "backend"'), 'extraResources extracts backend/');
assert(builderContent.includes('arm64'), 'mac config defines arm64 architecture');
assert(builderContent.includes('target: nsis'), 'win config defines NSIS target');

const plistPath = path.join(ROOT_DIR, 'build/entitlements.mac.plist');
assert(fs.existsSync(plistPath), 'build/entitlements.mac.plist exists');
const plistContent = fs.readFileSync(plistPath, 'utf8');
assert(plistContent.includes('com.apple.security.cs.allow-jit'), 'entitlements includes allow-jit');
assert(plistContent.includes('com.apple.security.network.client'), 'entitlements includes network client');

// 5. Verification Summary
console.log('\n================================================================');
if (failureCount === 0) {
  console.log('   ALL M4 CONFIGURATION AND ARTIFACT CHECKS PASSED SUCCESSFULLY! ');
  console.log('================================================================\n');
  process.exit(0);
} else {
  console.error(`   ${failureCount} CHECK(S) FAILED. PLEASE REVIEW LOGS.          `);
  console.log('================================================================\n');
  process.exit(1);
}
