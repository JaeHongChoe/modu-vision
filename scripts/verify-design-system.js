#!/usr/bin/env node
/**
 * scripts/verify-design-system.js
 *
 * Vision AI Studio — Comprehensive Commercial Machine Vision E2E Verification Suite.
 * Verifying industrial design tokens, workflow coverage, and operator guidance.
 *
 * Verification Tiers:
 * - Tier 1: Feature Coverage (Dark Steel Surfaces, Discrete LED Indicators, 6 Stages, 16 AI Terms)
 * - Tier 2: Boundary & Anti-AI Corner Cases (Zero Gradients, Zero Blurs, Zero 28px Glows, Contrast Compliance)
 * - Tier 3: Cross-Feature Combinations & Typography (Tabular Numbers, Standardized Units: ms, FPS, px, μm, °)
 * - Tier 4: Real-World Application Workflows (M5 100/100 Checks, TypeScript Strict Typecheck, Production Build)
 *
 * Execution:
 *   node scripts/verify-design-system.js
 *   node scripts/verify-design-system.js --tier=1
 *   node scripts/verify-design-system.js --tier=2
 *   node scripts/verify-design-system.js --tier=3
 *   node scripts/verify-design-system.js --tier=4
 *   node scripts/verify-design-system.js --skip-build
 *   node scripts/verify-design-system.js --json
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const RENDERER_ROOT = path.join(ROOT, 'src/renderer');

// ANSI Color formatting
const RESET = '\x1b[0m';
const BOLD = '\x1b[1m';
const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const YELLOW = '\x1b[33m';
const CYAN = '\x1b[36m';
const GRAY = '\x1b[90m';

// Parse CLI flags
const args = process.argv.slice(2);
const tierArg = args.find((a) => a.startsWith('--tier='));
const targetTier = tierArg ? parseInt(tierArg.split('=')[1], 10) : null;
const skipBuild = args.includes('--skip-build');
const jsonOutput = args.includes('--json');

// Results tracking
const results = {
  tier1: { name: 'Tier 1 - Feature Coverage', passes: 0, failures: 0, checks: [], errors: [] },
  tier2: { name: 'Tier 2 - Boundary & Anti-AI Corner Cases', passes: 0, failures: 0, checks: [], errors: [] },
  tier3: { name: 'Tier 3 - Cross-Feature & Typography', passes: 0, failures: 0, checks: [], errors: [] },
  tier4: { name: 'Tier 4 - Real-World Application Workflows', passes: 0, failures: 0, checks: [], errors: [] },
};

function recordPass(tierKey, message) {
  results[tierKey].passes++;
  results[tierKey].checks.push({ status: 'PASS', message });
  if (!jsonOutput) {
    console.log(`  ${GREEN}[PASS]${RESET} ${message}`);
  }
}

function recordFail(tierKey, message, details = null) {
  results[tierKey].failures++;
  const errorObj = { message, details };
  results[tierKey].checks.push({ status: 'FAIL', message, details });
  results[tierKey].errors.push(errorObj);
  if (!jsonOutput) {
    console.error(`  ${RED}[FAIL]${RESET} ${BOLD}${message}${RESET}`);
    if (details) {
      if (Array.isArray(details)) {
        details.slice(0, 5).forEach((d) => console.error(`    ${GRAY}↳ ${d}${RESET}`));
        if (details.length > 5) {
          console.error(`    ${GRAY}↳ ... and ${details.length - 5} more${RESET}`);
        }
      } else {
        console.error(`    ${GRAY}↳ ${details}${RESET}`);
      }
    }
  }
}

/**
 * Recursively find files matching extensions
 */
function findFiles(dir, extensions = ['.ts', '.tsx', '.css', '.js']) {
  let fileList = [];
  if (!fs.existsSync(dir)) return fileList;

  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== 'node_modules' && entry.name !== 'dist' && entry.name !== '.git') {
        fileList = fileList.concat(findFiles(fullPath, extensions));
      }
    } else if (entry.isFile()) {
      const ext = path.extname(entry.name);
      if (extensions.includes(ext)) {
        fileList.push(fullPath);
      }
    }
  }
  return fileList;
}

if (!jsonOutput) {
  console.log(`${BOLD}${CYAN}========================================================================${RESET}`);
  console.log(`${BOLD}${CYAN}   Vision AI Studio — Commercial Machine Vision E2E Verification Suite   ${RESET}`);
  console.log(`${BOLD}${CYAN}   Industrial Design Tokens and Workflow Coverage Verification    ${RESET}`);
  console.log(`${BOLD}${CYAN}========================================================================${RESET}`);
  console.log(`${GRAY}Root: ${ROOT}${RESET}\n`);
}

// ============================================================================
// TIER 1: FEATURE COVERAGE
// ============================================================================
function runTier1() {
  if (!jsonOutput) console.log(`${BOLD}--- Tier 1: Feature Coverage ---${RESET}`);
  const tier = 'tier1';

  // 1.1 Dark Steel Surface Tokens
  if (!jsonOutput) console.log(`\n${CYAN}1.1 Dark Steel Surface Tokens Definition (#0B0E14, #131822, #1A212E, #2B3547)${RESET}`);
  const tailwindPath = path.join(ROOT, 'tailwind.config.js');
  const indexCssPath = path.join(RENDERER_ROOT, 'index.css');

  const tailwindContent = fs.existsSync(tailwindPath) ? fs.readFileSync(tailwindPath, 'utf8') : '';
  const indexCssContent = fs.existsSync(indexCssPath) ? fs.readFileSync(indexCssPath, 'utf8') : '';
  const combinedStylingConfig = (tailwindContent + '\n' + indexCssContent).toLowerCase();

  const surfaceTokens = [
    { code: '#0b0e14', role: 'chassis background (deep steel charcoal)' },
    { code: '#131822', role: 'panel background (intermediate steel)' },
    { code: '#1a212e', role: 'card / surface container' },
    { code: '#2b3547', role: 'hairline border (1px precision border)' },
  ];

  for (const token of surfaceTokens) {
    if (combinedStylingConfig.includes(token.code)) {
      recordPass(tier, `Surface token ${token.code.toUpperCase()} (${token.role}) is defined in Tailwind/CSS`);
    } else {
      recordFail(tier, `Surface token ${token.code.toUpperCase()} (${token.role}) is NOT defined in tailwind.config.js or src/renderer/index.css`);
    }
  }

  // 1.2 Discrete LED Status Indicators
  if (!jsonOutput) console.log(`\n${CYAN}1.2 Discrete LED Status Indicators (#10B981, #EF4444, #F59E0B)${RESET}`);
  const indicatorTokens = [
    { code: '#10b981', role: 'PASS / OK green LED indicator' },
    { code: '#ef4444', role: 'FAIL / NG red LED indicator' },
    { code: '#f59e0b', role: 'STANDBY / WARNING amber LED indicator' },
  ];

  for (const token of indicatorTokens) {
    if (combinedStylingConfig.includes(token.code)) {
      recordPass(tier, `LED indicator token ${token.code.toUpperCase()} (${token.role}) is defined`);
    } else {
      recordFail(tier, `LED indicator token ${token.code.toUpperCase()} (${token.role}) is NOT defined`);
    }
  }

  // Check LedAnnunciator component existence and state modeling
  const ledAnnunciatorPath = path.join(RENDERER_ROOT, 'components/common/LedAnnunciator.tsx');
  if (fs.existsSync(ledAnnunciatorPath)) {
    recordPass(tier, `LedAnnunciator component exists at components/common/LedAnnunciator.tsx`);
    const ledContent = fs.readFileSync(ledAnnunciatorPath, 'utf8');
    if (ledContent.includes('pass') && ledContent.includes('fail')) {
      recordPass(tier, `LedAnnunciator component implements pass/fail discrete states`);
    } else {
      recordFail(tier, `LedAnnunciator component is missing pass/fail discrete states`);
    }
  } else {
    recordFail(tier, `LedAnnunciator component does NOT exist at src/renderer/components/common/LedAnnunciator.tsx`);
  }

  // 1.3 6 Stages Components & App Routing
  if (!jsonOutput) console.log(`\n${CYAN}1.3 6 Stages Workflow Components & Orchestration${RESET}`);
  const stageComponents = [
    { step: 1, name: 'Stage 1: Dataset Studio', file: 'components/dataset/DatasetStudio.tsx', exportName: 'DatasetStudio' },
    { step: 2, name: 'Stage 2: Labeling Studio', file: 'components/labeling/LabelingTool.tsx', exportName: 'LabelingTool' },
    { step: 3, name: 'Stage 3: AutoML Trainer', file: 'components/training/TrainingController.tsx', exportName: 'TrainingController' },
    { step: 4, name: 'Stage 4: Evaluation & Overkill Studio', file: 'components/evaluation/EvaluationStudio.tsx', exportName: 'EvaluationStudio' },
    { step: 5, name: 'Stage 5: Flowchart Studio', file: 'components/flowchart/FlowchartStudio.tsx', exportName: 'FlowchartStudio' },
    { step: 6, name: 'Stage 6: Inference & Export Studio', file: 'components/inference/InferenceCenterStudio.tsx', exportName: 'InferenceCenterStudio' },
  ];

  const appPath = path.join(RENDERER_ROOT, 'App.tsx');
  const appContent = fs.existsSync(appPath) ? fs.readFileSync(appPath, 'utf8') : '';

  for (const stage of stageComponents) {
    const compPath = path.join(RENDERER_ROOT, stage.file);
    if (fs.existsSync(compPath)) {
      const compContent = fs.readFileSync(compPath, 'utf8');
      if (compContent.includes(stage.exportName)) {
        recordPass(tier, `${stage.name} exists and exports ${stage.exportName}`);
      } else {
        recordFail(tier, `${stage.name} exists but does not export ${stage.exportName}`);
      }
    } else {
      recordFail(tier, `${stage.name} component file does not exist at src/renderer/${stage.file}`);
    }

    if (appContent.includes(stage.exportName) && appContent.includes(`activeStep === ${stage.step}`)) {
      recordPass(tier, `App.tsx routes step ${stage.step} to <${stage.exportName} />`);
    } else {
      recordFail(tier, `App.tsx does not route step ${stage.step} to <${stage.exportName} />`);
    }
  }

  // 1.4 16 Manufacturing AI Terms in Glossary
  if (!jsonOutput) console.log(`\n${CYAN}1.4 Manufacturing AI Glossary Dictionary (16 Shop-Floor Terms)${RESET}`);
  const jargonDictPath = path.join(RENDERER_ROOT, 'data/jargonDictionary.ts');
  if (!fs.existsSync(jargonDictPath)) {
    recordFail(tier, `Glossary dictionary file missing at src/renderer/data/jargonDictionary.ts`);
  } else {
    recordPass(tier, `Glossary dictionary file exists at src/renderer/data/jargonDictionary.ts`);
    const dictContent = fs.readFileSync(jargonDictPath, 'utf8');

    const requiredTerms = [
      { key: 'focal_loss', label: 'Focal Loss (초점 손실 함수)' },
      { key: 'auroc', label: 'AUROC (이상 탐지 종합 변별력)' },
      { key: 'map_50', label: 'mAP@0.5 (결함 박스 평균 검출 정확도)' },
      { key: 'dice', label: 'Dice Coefficient (결함 형상 일치도)' },
      { key: 'iou', label: 'IoU (영역 교차 비율)' },
      { key: 'p95_latency', label: 'P95 Latency (95% 안정 구간 검사 지연시간)' },
      { key: 'early_stopping', label: 'Early Stopping (과적합 방지 자동 조기 종료)' },
      { key: 'batch_size', label: 'Batch Size (1회 동시 처리 묶음 크기)' },
      { key: 'learning_rate', label: 'Learning Rate (인공지능 학습 보폭)' },
      { key: 'epochs', label: 'Epochs (전체 데이터 학습 완주 회수)' },
      { key: 'underkill', label: 'Underkill (🚨 미검 불량 제품 유출 오류)' },
      { key: 'overkill', label: 'Overkill (⚠️ 과검 멀쩡한 제품 오경보/폐기)' },
      { key: 'optimal_threshold', label: 'Optimal Threshold (미검 제로 최적 임계값 τ*)' },
      { key: 'roi_crop_padding', label: 'ROI Crop Padding (관심 영역 확장 여유 공간)' },
      { key: 'tact_time', label: 'Tact Time (공정 생산 사이클 시간)' },
      { key: 'confusion_matrix', label: 'Confusion Matrix (정오 판정 행렬)' },
    ];

    let termsFoundCount = 0;
    for (const item of requiredTerms) {
      if (dictContent.includes(`${item.key}:`)) {
        termsFoundCount++;
      } else {
        recordFail(tier, `Glossary missing required manufacturing AI term: ${item.label} [${item.key}]`);
      }
    }

    if (termsFoundCount === 16) {
      recordPass(tier, `All 16 manufacturing AI terms are fully defined in JARGON_DICTIONARY`);
    } else {
      recordFail(tier, `Found ${termsFoundCount}/16 manufacturing AI terms in JARGON_DICTIONARY`);
    }

    // Verify JargonGlossaryModal exists and binds dictionary
    const modalPath = path.join(RENDERER_ROOT, 'components/common/JargonGlossaryModal.tsx');
    if (fs.existsSync(modalPath)) {
      const modalContent = fs.readFileSync(modalPath, 'utf8');
      if (modalContent.includes('JARGON_DICTIONARY')) {
        recordPass(tier, `JargonGlossaryModal.tsx binds JARGON_DICTIONARY for shop-floor operators`);
      } else {
        recordFail(tier, `JargonGlossaryModal.tsx does not bind JARGON_DICTIONARY`);
      }
    } else {
      recordFail(tier, `JargonGlossaryModal.tsx not found`);
    }
  }
}

// ============================================================================
// TIER 2: BOUNDARY & ANTI-AI CORNER CASES
// ============================================================================
function runTier2() {
  if (!jsonOutput) console.log(`\n${BOLD}--- Tier 2: Boundary & Anti-AI Corner Cases ---${RESET}`);
  const tier = 'tier2';

  const sourceFiles = findFiles(RENDERER_ROOT, ['.ts', '.tsx', '.css']);

  // 2.1 Scan for zero `bg-gradient-to` violations
  if (!jsonOutput) console.log(`\n${CYAN}2.1 Scanning for zero 'bg-gradient-to' violations in src/renderer/${RESET}`);
  const gradientViolations = [];
  const gradientRegex = /bg-gradient-to-[a-z]+/g;

  for (const filePath of sourceFiles) {
    const content = fs.readFileSync(filePath, 'utf8');
    const lines = content.split('\n');
    lines.forEach((line, idx) => {
      if (gradientRegex.test(line)) {
        const relPath = path.relative(ROOT, filePath);
        gradientViolations.push(`${relPath}:${idx + 1} -> ${line.trim()}`);
      }
    });
  }

  if (gradientViolations.length === 0) {
    recordPass(tier, `Zero 'bg-gradient-to' violations found in ${sourceFiles.length} files (Anti-AI rule compliant)`);
  } else {
    recordFail(
      tier,
      `Found ${gradientViolations.length} 'bg-gradient-to' violation(s) in src/renderer/ (Must be 0)`,
      gradientViolations
    );
  }

  // 2.2 Scan for zero `backdrop-blur` violations
  if (!jsonOutput) console.log(`\n${CYAN}2.2 Scanning for zero 'backdrop-blur' violations in src/renderer/${RESET}`);
  const blurViolations = [];
  const blurRegex = /backdrop-blur(-[a-z0-9]+)?/g;

  for (const filePath of sourceFiles) {
    const content = fs.readFileSync(filePath, 'utf8');
    const lines = content.split('\n');
    lines.forEach((line, idx) => {
      if (blurRegex.test(line)) {
        const relPath = path.relative(ROOT, filePath);
        blurViolations.push(`${relPath}:${idx + 1} -> ${line.trim()}`);
      }
    });
  }

  if (blurViolations.length === 0) {
    recordPass(tier, `Zero 'backdrop-blur' violations found in ${sourceFiles.length} files (Anti-AI rule compliant)`);
  } else {
    recordFail(
      tier,
      `Found ${blurViolations.length} 'backdrop-blur' violation(s) in src/renderer/ (Must be 0)`,
      blurViolations
    );
  }

  // 2.3 Scan for zero diffuse 28px glows
  if (!jsonOutput) console.log(`\n${CYAN}2.3 Scanning for zero diffuse 28px glows in src/renderer/${RESET}`);
  const glowViolations = [];
  const glowRegex = /(shadow-\[[^\]]*28px[^\]]*\]|0_0_28px|drop-shadow-\[[^\]]*28px[^\]]*\])/g;

  for (const filePath of sourceFiles) {
    const content = fs.readFileSync(filePath, 'utf8');
    const lines = content.split('\n');
    lines.forEach((line, idx) => {
      if (glowRegex.test(line)) {
        const relPath = path.relative(ROOT, filePath);
        glowViolations.push(`${relPath}:${idx + 1} -> ${line.trim()}`);
      }
    });
  }

  if (glowViolations.length === 0) {
    recordPass(tier, `Zero diffuse 28px glow violations found in ${sourceFiles.length} files`);
  } else {
    recordFail(
      tier,
      `Found ${glowViolations.length} diffuse 28px glow violation(s) in src/renderer/ (Must be 0)`,
      glowViolations
    );
  }

  // 2.4 Text contrast compliance (no low-contrast slate-600 on dark backgrounds)
  if (!jsonOutput) console.log(`\n${CYAN}2.4 Scanning for text contrast compliance (no low-contrast 'slate-600')${RESET}`);
  const contrastViolations = [];
  const lowContrastRegex = /text-slate-600/g;

  for (const filePath of sourceFiles) {
    if (filePath.includes('/components/')) {
      const content = fs.readFileSync(filePath, 'utf8');
      const lines = content.split('\n');
      lines.forEach((line, idx) => {
        if (lowContrastRegex.test(line)) {
          const relPath = path.relative(ROOT, filePath);
          contrastViolations.push(`${relPath}:${idx + 1} -> ${line.trim()}`);
        }
      });
    }
  }

  if (contrastViolations.length === 0) {
    recordPass(tier, `Zero low-contrast 'text-slate-600' violations in UI components`);
  } else {
    recordFail(
      tier,
      `Found ${contrastViolations.length} low-contrast 'text-slate-600' violation(s) in UI components (Must be 0)`,
      contrastViolations
    );
  }
}

// ============================================================================
// TIER 3: CROSS-FEATURE COMBINATIONS & TYPOGRAPHY
// ============================================================================
function runTier3() {
  if (!jsonOutput) console.log(`\n${BOLD}--- Tier 3: Cross-Feature Combinations & Typography ---${RESET}`);
  const tier = 'tier3';

  // 3.1 `tabular-nums` usage on digital readouts, FPS, latency, and coordinate indicators
  if (!jsonOutput) console.log(`\n${CYAN}3.1 Tabular Numbers ('tabular-nums') on Digital Readouts & Telemetry${RESET}`);
  const digitalReadoutComponents = [
    { file: 'components/inference/InferenceCenterStudio.tsx', role: 'Inference FPS & P95 takt time' },
    { file: 'components/training/TrainingController.tsx', role: 'Loss curves & hardware telemetry' },
    { file: 'components/evaluation/EvaluationStudio.tsx', role: 'Confusion matrix & zero-escape tau*' },
    { file: 'components/flowchart/CustomNode.tsx', role: 'Flowchart DAG node latency' },
    { file: 'components/labeling/LabelingCanvas.tsx', role: 'Canvas coordinates and angles' },
  ];

  for (const comp of digitalReadoutComponents) {
    const fullPath = path.join(RENDERER_ROOT, comp.file);
    if (fs.existsSync(fullPath)) {
      const content = fs.readFileSync(fullPath, 'utf8');
      if (content.includes('tabular-nums')) {
        recordPass(tier, `'tabular-nums' applied in ${comp.file} (${comp.role})`);
      } else {
        recordFail(tier, `'tabular-nums' is MISSING in ${comp.file} (${comp.role})`);
      }
    } else {
      recordFail(tier, `Component file not found: ${comp.file}`);
    }
  }

  // 3.2 Standardized Unit Labels (ms, FPS, px, μm, °)
  if (!jsonOutput) console.log(`\n${CYAN}3.2 Standardized Unit Labels Presence (ms, FPS, px, μm, °)${RESET}`);
  const allUiFiles = findFiles(path.join(RENDERER_ROOT, 'components'), ['.tsx']);
  let allUiContent = '';
  for (const f of allUiFiles) {
    allUiContent += fs.readFileSync(f, 'utf8') + '\n';
  }

  const units = [
    { unit: 'ms', regex: /(\b\d+(\.\d+)?\s*ms\b|ms<\/span>|ms<\/div>|'ms'|"ms")/i, desc: 'milliseconds (latency/takt time)' },
    { unit: 'FPS', regex: /(\bFPS\b|FPS<\/span>|FPS<\/div>|'FPS'|"FPS")/, desc: 'Frames Per Second' },
    { unit: 'px', regex: /(\d+\s*px(?![a-zA-Z0-9-])|\b[wh]Px\b.*px(?![a-zA-Z0-9-])|px<\/span>|px<\/div>|['"`]px['"`]|px\s*`)/, desc: 'pixels (dimensions/padding/coordinates)' },
    { unit: 'μm', regex: /(μm|\u03BCm|\u00B5m|\bmicrons?\b)/, desc: 'micrometers (subpixel defect precision)' },
    { unit: '°', regex: /[°\u00B0]/, desc: 'degrees (rotation angle)' },
  ];

  for (const u of units) {
    if (u.regex.test(allUiContent)) {
      recordPass(tier, `Unit label '${u.unit}' (${u.desc}) is rendered in UI metrics`);
    } else {
      recordFail(tier, `Unit label '${u.unit}' (${u.desc}) is NOT found in any UI component readouts`);
    }
  }
}

// ============================================================================
// TIER 4: REAL-WORLD APPLICATION WORKFLOWS
// ============================================================================
function runTier4() {
  if (!jsonOutput) console.log(`\n${BOLD}--- Tier 4: Real-World Application Workflows ---${RESET}`);
  const tier = 'tier4';

  // 4.1 Verify verify-m5.js continues to pass 100/100 checks
  if (!jsonOutput) console.log(`\n${CYAN}4.1 Executing Milestone M5 Baseline Verification (verify-m5.js)${RESET}`);
  const m5ScriptPath = path.join(ROOT, 'scripts/verify-m5.js');
  if (!fs.existsSync(m5ScriptPath)) {
    recordFail(tier, `Milestone M5 script not found at scripts/verify-m5.js`);
  } else {
    try {
      const output = execSync('node scripts/verify-m5.js', { cwd: ROOT, encoding: 'utf8' });
      if (output.includes('100 PASSES, 0 FAILURES') || output.includes('0 FAILURES')) {
        recordPass(tier, `verify-m5.js passed all baseline checks (100/100 passes, 0 failures)`);
      } else {
        recordFail(tier, `verify-m5.js did not report 0 failures`, output.slice(-300));
      }
    } catch (err) {
      recordFail(tier, `verify-m5.js exited with non-zero status`, err.message);
    }
  }

  // 4.2 Verify npm run typecheck exits 0
  if (!jsonOutput) console.log(`\n${CYAN}4.2 Executing TypeScript Strict Typecheck (npm run typecheck)${RESET}`);
  try {
    const typecheckOut = execSync('npm run typecheck', { cwd: ROOT, encoding: 'utf8', stdio: 'pipe' });
    recordPass(tier, `TypeScript typecheck passed with 0 errors (npm run typecheck exits 0)`);
  } catch (err) {
    const stderr = err.stderr ? err.stderr.toString() : err.message;
    const stdout = err.stdout ? err.stdout.toString() : '';
    recordFail(tier, `npm run typecheck failed with compilation errors`, [stderr, stdout]);
  }

  // 4.3 Verify npm run build exits 0
  if (!jsonOutput) console.log(`\n${CYAN}4.3 Executing Production Bundle Build (npm run build)${RESET}`);
  if (skipBuild) {
    if (!jsonOutput) console.log(`  ${YELLOW}[SKIP]${RESET} npm run build skipped via --skip-build flag`);
  } else {
    try {
      const buildOut = execSync('npm run build', { cwd: ROOT, encoding: 'utf8', stdio: 'pipe' });
      const distHtml = path.join(ROOT, 'dist/index.html');
      const distAssets = path.join(ROOT, 'dist/assets');
      if (fs.existsSync(distHtml) && fs.existsSync(distAssets)) {
        recordPass(tier, `npm run build succeeded and generated dist/ artifacts (exits 0)`);
      } else {
        recordFail(tier, `npm run build completed but dist/ output files are missing`);
      }
    } catch (err) {
      const stderr = err.stderr ? err.stderr.toString() : err.message;
      recordFail(tier, `npm run build failed with errors`, stderr);
    }
  }
}

// ============================================================================
// SUITE EXECUTION & REPORTING
// ============================================================================
const startTime = Date.now();

if (targetTier === null || targetTier === 1) runTier1();
if (targetTier === null || targetTier === 2) runTier2();
if (targetTier === null || targetTier === 3) runTier3();
if (targetTier === null || targetTier === 4) runTier4();

const durationMs = Date.now() - startTime;

if (jsonOutput) {
  const jsonReport = {
    summary: {
      totalPasses: Object.values(results).reduce((acc, t) => acc + t.passes, 0),
      totalFailures: Object.values(results).reduce((acc, t) => acc + t.failures, 0),
      durationMs,
      timestamp: new Date().toISOString(),
    },
    tiers: results,
  };
  console.log(JSON.stringify(jsonReport, null, 2));
  process.exit(jsonReport.summary.totalFailures > 0 ? 1 : 0);
}

// Final Summary
console.log(`\n${BOLD}${CYAN}========================================================================${RESET}`);
console.log(`${BOLD}${CYAN}              E2E DESIGN SYSTEM VERIFICATION SUMMARY                   ${RESET}`);
console.log(`${BOLD}${CYAN}========================================================================${RESET}`);

let totalPasses = 0;
let totalFailures = 0;

for (const [key, t] of Object.entries(results)) {
  if (targetTier !== null && key !== `tier${targetTier}`) continue;
  totalPasses += t.passes;
  totalFailures += t.failures;
  const statusColor = t.failures === 0 ? GREEN : RED;
  const statusText = t.failures === 0 ? 'ALL PASSED' : `${t.failures} FAILED`;
  console.log(
    `  ${BOLD}${t.name.padEnd(46)}${RESET} [ ${statusColor}${statusText.padStart(10)}${RESET} ]  (${t.passes} pass, ${t.failures} fail)`
  );
}

console.log(`${BOLD}${CYAN}------------------------------------------------------------------------${RESET}`);
console.log(`  ${BOLD}Total Checks Executed:${RESET} ${totalPasses + totalFailures}`);
console.log(`  ${BOLD}Total Passed:${RESET}          ${GREEN}${totalPasses}${RESET}`);
console.log(`  ${BOLD}Total Failed:${RESET}          ${totalFailures > 0 ? RED : GREEN}${totalFailures}${RESET}`);
console.log(`  ${BOLD}Execution Duration:${RESET}    ${durationMs}ms`);
console.log(`${BOLD}${CYAN}========================================================================${RESET}\n`);

if (totalFailures > 0) {
  console.error(`${RED}${BOLD}E2E Verification Suite detected ${totalFailures} defect(s) / gap(s).${RESET}`);
  console.error(`${YELLOW}Refer to the diagnostics above for remediation details.${RESET}\n`);
  process.exit(1);
} else {
  console.log(`${GREEN}${BOLD}ALL E2E DESIGN SYSTEM VERIFICATION CHECKS PASSED SUCCESSFULLY!${RESET}\n`);
  process.exit(0);
}
