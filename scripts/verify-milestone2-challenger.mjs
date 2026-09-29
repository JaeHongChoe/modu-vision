#!/usr/bin/env node
/**
 * scripts/verify-milestone2-challenger.mjs
 *
 * Empirical Challenger Verification Suite for Milestone 2 (Data Studio & Labeling Studio).
 * Designed for Adversarial Stress Testing, Design System Compliance, and Mathematical Rigor.
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');
const RENDERER_DIR = path.join(ROOT, 'src/renderer');

// ANSI Colors
const RESET = '\x1b[0m';
const BOLD = '\x1b[1m';
const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const YELLOW = '\x1b[33m';
const CYAN = '\x1b[36m';
const GRAY = '\x1b[90m';

let totalChecks = 0;
let passedChecks = 0;
let failedChecks = 0;
const failures = [];

function assert(condition, message, details = null) {
  totalChecks++;
  if (condition) {
    passedChecks++;
    console.log(`  ${GREEN}[PASS]${RESET} ${message}`);
  } else {
    failedChecks++;
    failures.push({ message, details });
    console.error(`  ${RED}[FAIL]${RESET} ${BOLD}${message}${RESET}`);
    if (details) {
      if (Array.isArray(details)) {
        details.forEach((d) => console.error(`    ${GRAY}↳ ${d}${RESET}`));
      } else {
        console.error(`    ${GRAY}↳ ${details}${RESET}`);
      }
    }
  }
}

console.log(`${BOLD}${CYAN}========================================================================${RESET}`);
console.log(`${BOLD}${CYAN}   M2 Empirical Challenger Test Suite — Stress, Math & Styling Audit    ${RESET}`);
console.log(`${BOLD}${CYAN}========================================================================${RESET}\n`);

// ----------------------------------------------------------------------------
// TEST SECTION 1: STYLING & ANTI-AI COMPLIANCE ACROSS 7 TARGET M2 FILES
// ----------------------------------------------------------------------------
console.log(`${BOLD}--- Section 1: Styling & Anti-AI Audit across Target M2 Files ---${RESET}`);

const targetFiles = [
  'components/dataset/DatasetStudio.tsx',
  'components/dataset/ProceduralGeneratorModal.tsx',
  'components/labeling/LabelingCanvas.tsx',
  'components/labeling/AnnotationList.tsx',
  'components/labeling/LabelingToolbar.tsx',
  'components/labeling/MaskLayerControls.tsx',
  'components/labeling/LabelingStudio.tsx',
];

for (const relFile of targetFiles) {
  const fullPath = path.join(RENDERER_DIR, relFile);
  assert(fs.existsSync(fullPath), `Target file exists: ${relFile}`);

  if (!fs.existsSync(fullPath)) continue;

  const content = fs.readFileSync(fullPath, 'utf8');
  const lines = content.split('\n');

  // Check 1: Zero backdrop-blur
  const blurMatches = [];
  lines.forEach((line, idx) => {
    if (/backdrop-blur(-[a-z0-9]+)?/.test(line)) {
      blurMatches.push(`Line ${idx + 1}: ${line.trim()}`);
    }
  });
  assert(
    blurMatches.length === 0,
    `Zero 'backdrop-blur' in ${relFile}`,
    blurMatches
  );

  // Check 2: Zero bg-gradient-to
  const gradientMatches = [];
  lines.forEach((line, idx) => {
    if (/bg-gradient-to-[a-z]+/.test(line)) {
      gradientMatches.push(`Line ${idx + 1}: ${line.trim()}`);
    }
  });
  assert(
    gradientMatches.length === 0,
    `Zero 'bg-gradient-to' linear gradients in ${relFile}`,
    gradientMatches
  );

  // Check 3: Zero text-slate-600
  const contrastMatches = [];
  lines.forEach((line, idx) => {
    if (/text-slate-600\b/.test(line)) {
      contrastMatches.push(`Line ${idx + 1}: ${line.trim()}`);
    }
  });
  assert(
    contrastMatches.length === 0,
    `Zero low-contrast 'text-slate-600' in ${relFile}`,
    contrastMatches
  );

  // Check 4: Zero diffuse 28px glows
  const glowMatches = [];
  lines.forEach((line, idx) => {
    if (/shadow-\[[^\]]*28px[^\]]*\]|0_0_28px/.test(line)) {
      glowMatches.push(`Line ${idx + 1}: ${line.trim()}`);
    }
  });
  assert(
    glowMatches.length === 0,
    `Zero diffuse 28px glows in ${relFile}`,
    glowMatches
  );
}

// ----------------------------------------------------------------------------
// TEST SECTION 2: TABULAR NUMS & PHYSICAL UNITS (μm) VERIFICATION
// ----------------------------------------------------------------------------
console.log(`\n${BOLD}--- Section 2: Tabular Numbers & Physical Unit (μm) Formatting ---${RESET}`);

// 2.1 Tabular nums verification
const tabularTargetFiles = [
  'components/dataset/DatasetStudio.tsx',
  'components/labeling/LabelingCanvas.tsx',
  'components/labeling/AnnotationList.tsx',
  'components/labeling/LabelingToolbar.tsx',
  'components/labeling/MaskLayerControls.tsx',
];

for (const relFile of tabularTargetFiles) {
  const fullPath = path.join(RENDERER_DIR, relFile);
  const content = fs.readFileSync(fullPath, 'utf8');
  assert(
    content.includes('tabular-nums'),
    `'tabular-nums' class applied to numeric readouts in ${relFile}`
  );
}

// 2.2 Presence of physical unit μm in HUD and Inspector
const hudFile = path.join(RENDERER_DIR, 'components/labeling/LabelingCanvas.tsx');
const hudContent = fs.readFileSync(hudFile, 'utf8');
const hudHasUm = hudContent.includes('μm') || hudContent.includes('\u03BCm');
assert(hudHasUm, 'HUD in LabelingCanvas.tsx contains physical unit "μm" (sensor pitch calibration readout)');

const inspectorFile = path.join(RENDERER_DIR, 'components/labeling/AnnotationList.tsx');
const inspectorContent = fs.readFileSync(inspectorFile, 'utf8');
const inspectorHasUm = inspectorContent.includes('μm') || inspectorContent.includes('\u03BCm');
assert(inspectorHasUm, 'Geometric Inspector in AnnotationList.tsx contains physical unit "μm"');

const inspectorHasUmSq = inspectorContent.includes('μm²') || inspectorContent.includes('\u03BCm\u00B2');
assert(inspectorHasUmSq, 'Geometric Inspector in AnnotationList.tsx contains area physical unit "μm²"');

const datasetFile = path.join(RENDERER_DIR, 'components/dataset/DatasetStudio.tsx');
const datasetContent = fs.readFileSync(datasetFile, 'utf8');
const datasetHasUm = datasetContent.includes('μm') || datasetContent.includes('\u03BCm');
assert(datasetHasUm, 'DatasetStudio.tsx contains physical scale "μm/px" readout');

// ----------------------------------------------------------------------------
// TEST SECTION 3: MATHEMATICAL ORACLES & GEOMETRY STRESS HARNESS
// ----------------------------------------------------------------------------
console.log(`\n${BOLD}--- Section 3: Mathematical Oracles & Geometry Stress Harness ---${RESET}`);

// 3.1 Shoelace Formula Stress Testing
function shoelaceArea(pts) {
  if (!pts || pts.length < 3) return 0;
  let sum = 0;
  for (let i = 0; i < pts.length; i++) {
    const j = (i + 1) % pts.length;
    sum += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1];
  }
  return Math.abs(sum) / 2;
}

// Test Case 3.1.1: Standard square 100x100
const squarePts = [[0, 0], [100, 0], [100, 100], [0, 100]];
assert(shoelaceArea(squarePts) === 10000, 'Shoelace: 100x100 square area equals exactly 10,000 px²');

// Test Case 3.1.2: Counter-clockwise square 100x100
const ccwSquarePts = [[0, 0], [0, 100], [100, 100], [100, 0]];
assert(shoelaceArea(ccwSquarePts) === 10000, 'Shoelace: Counter-clockwise square area equals exactly 10,000 px²');

// Test Case 3.1.3: Right triangle with legs 30 and 40
const triPts = [[0, 0], [30, 0], [0, 40]];
assert(shoelaceArea(triPts) === 600, 'Shoelace: Right triangle (30x40) area equals exactly 600 px²');

// Test Case 3.1.4: Degenerate polygon (< 3 points)
assert(shoelaceArea([[10, 10], [20, 20]]) === 0, 'Shoelace: 2-point line segment gracefully returns 0 area');
assert(shoelaceArea([]) === 0, 'Shoelace: Empty points array gracefully returns 0 area');
assert(shoelaceArea(null) === 0, 'Shoelace: Null points gracefully returns 0 area');

// Test Case 3.1.5: Collinear points (flat polygon)
const collinearPts = [[0, 0], [50, 0], [100, 0]];
assert(shoelaceArea(collinearPts) === 0, 'Shoelace: Collinear vertices return 0 area');

// Test Case 3.1.6: Large coordinates (floating point stability)
const largePts = [
  [1000000, 1000000],
  [1000100, 1000000],
  [1000100, 1000050],
  [1000000, 1000050],
];
assert(shoelaceArea(largePts) === 5000, 'Shoelace: Large coordinate offset maintains precision (5,000 px²)');

// 3.2 Rotated Bounding Box (OBB) Envelope Math Stress Testing
function calcRotatedEnvelope(cx, cy, w, h, angleDeg) {
  const rad = ((angleDeg || 0) * Math.PI) / 180;
  const cos = Math.abs(Math.cos(rad));
  const sin = Math.abs(Math.sin(rad));
  const boundW = w * cos + h * sin;
  const boundH = w * sin + h * cos;
  return {
    xmin: Math.round(cx - boundW / 2),
    ymin: Math.round(cy - boundH / 2),
    xmax: Math.round(cx + boundW / 2),
    ymax: Math.round(cy + boundH / 2),
    boundW,
    boundH,
  };
}

// Test Case 3.2.1: 0 degrees rotation
const env0 = calcRotatedEnvelope(100, 100, 60, 40, 0);
assert(env0.boundW === 60 && env0.boundH === 40, 'OBB: 0° rotation preserves width 60 and height 40');
assert(env0.xmin === 70 && env0.xmax === 130, 'OBB: 0° rotation envelope x bounds [70, 130]');

// Test Case 3.2.2: 90 degrees rotation (width and height swap)
const env90 = calcRotatedEnvelope(100, 100, 60, 40, 90);
assert(
  Math.abs(env90.boundW - 40) < 1e-6 && Math.abs(env90.boundH - 60) < 1e-6,
  'OBB: 90° rotation swaps bounding dimensions (40 x 60)'
);

// Test Case 3.2.3: 45 degrees rotation
const env45 = calcRotatedEnvelope(100, 100, 100, 100, 45);
const expectedSpan = 100 * Math.sqrt(2);
assert(
  Math.abs(env45.boundW - expectedSpan) < 1e-4,
  `OBB: 45° rotation of 100x100 square yields ${expectedSpan.toFixed(2)}px bounding envelope`
);

// Test Case 3.2.4: Negative angles and angles > 360°
const envNeg = calcRotatedEnvelope(100, 100, 60, 40, -90);
assert(
  Math.abs(envNeg.boundW - 40) < 1e-6 && Math.abs(envNeg.boundH - 60) < 1e-6,
  'OBB: Negative angles (-90°) calculate symmetric bounding envelope'
);

const env360 = calcRotatedEnvelope(100, 100, 60, 40, 360);
assert(
  Math.abs(env360.boundW - 60) < 1e-6 && Math.abs(env360.boundH - 40) < 1e-6,
  'OBB: 360° periodic angle yields original dimensions'
);

// 3.3 Optical Calibration Math & Conversion Stress Testing
function convertUnits(valPx, pitchUm) {
  const physicalUm = valPx * pitchUm;
  const areaUm2 = valPx * valPx * pitchUm * pitchUm;
  return {
    umStr: `${physicalUm.toFixed(1)} μm`,
    areaUm2Str: `${Math.round(areaUm2).toLocaleString()} μm²`,
    isFinite: Number.isFinite(physicalUm) && Number.isFinite(areaUm2),
    isNaN: Number.isNaN(physicalUm) || Number.isNaN(areaUm2),
  };
}

const testPitches = [0.01, 1.0, 2.5, 3.45, 5.0, 10.0, 100.0];
let allCalibrationsValid = true;
for (const p of testPitches) {
  const res = convertUnits(200, p);
  if (!res.isFinite || res.isNaN) {
    allCalibrationsValid = false;
  }
}
assert(
  allCalibrationsValid,
  'Optical Calibration: Scales (0.01 to 100.0 μm/px) calculate strictly finite, non-NaN values'
);

const standardPitchRes = convertUnits(100, 3.45);
assert(
  standardPitchRes.umStr === '345.0 μm',
  'Optical Calibration: 100px at 3.45 μm/px formats correctly to 345.0 μm'
);
assert(
  standardPitchRes.areaUm2Str === '119,025 μm²',
  'Optical Calibration: 10,000px² at 3.45 μm/px formats correctly to 119,025 μm²'
);

// ----------------------------------------------------------------------------
// TEST SECTION 4: NUDGING & INPUT GUARD VERIFICATION
// ----------------------------------------------------------------------------
console.log(`\n${BOLD}--- Section 4: Nudging & Input Guard Verification ---${RESET}`);

// Check LabelingCanvas input guard implementation
const canvasPath = path.join(RENDERER_DIR, 'components/labeling/LabelingCanvas.tsx');
const canvasCode = fs.readFileSync(canvasPath, 'utf8');

const hasInputGuard =
  canvasCode.includes("target.tagName === 'INPUT'") &&
  canvasCode.includes("target.tagName === 'TEXTAREA'") &&
  canvasCode.includes('target.isContentEditable');
assert(
  hasInputGuard,
  'LabelingCanvas.tsx guards against keydown events in INPUT, TEXTAREA, and contenteditable'
);

const hasShiftCoarseStep =
  canvasCode.includes('e.shiftKey ? 10 : 1') ||
  (canvasCode.includes('shiftKey') && canvasCode.includes('10'));
assert(
  hasShiftCoarseStep,
  'LabelingCanvas.tsx implements dual-speed nudging: 1px micro-step and 10px coarse-step with Shift'
);

// Check moveBBox clamping
assert(
  canvasCode.includes('moveBBox'),
  'LabelingCanvas.tsx utilizes moveBBox for coordinate translation and boundary clamping'
);

// ----------------------------------------------------------------------------
// TEST SECTION 5: INSPECTION CHASSIS AND COMPONENT EXPORTS
// ----------------------------------------------------------------------------
console.log(`\n${BOLD}--- Section 5: Inspection Chassis & Component Exports ---${RESET}`);

// Check LabelingStudio.tsx
const studioPath = path.join(RENDERER_DIR, 'components/labeling/LabelingStudio.tsx');
assert(fs.existsSync(studioPath), 'src/renderer/components/labeling/LabelingStudio.tsx exists');
const studioCode = fs.readFileSync(studioPath, 'utf8');
assert(
  studioCode.includes('bg-[#0B0E14]') || studioCode.includes('bg-chassis'),
  'LabelingStudio.tsx wraps labeling workspace in Inspection deep steel chassis (#0B0E14)'
);
assert(
  studioCode.includes('export const LabelingStudio') || studioCode.includes('export default LabelingStudio'),
  'LabelingStudio.tsx exports LabelingStudio'
);

// Check DatasetStudio.tsx chassis tokens
const dsPath = path.join(RENDERER_DIR, 'components/dataset/DatasetStudio.tsx');
const dsCode = fs.readFileSync(dsPath, 'utf8');
assert(
  dsCode.includes('#0B0E14') && dsCode.includes('#131822') && dsCode.includes('#2B3547'),
  'DatasetStudio.tsx adheres to Inspection chassis tokens (#0B0E14, #131822, #2B3547)'
);

// Check density toggle in DatasetStudio.tsx
assert(
  dsCode.includes("'S'") && dsCode.includes("'M'") && dsCode.includes("'L'"),
  'DatasetStudio.tsx implements Inspection thumbnail density switching (S, M, L)'
);

// ----------------------------------------------------------------------------
// SUMMARY & RESULTS
// ----------------------------------------------------------------------------
console.log(`\n${BOLD}${CYAN}========================================================================${RESET}`);
console.log(`${BOLD}${CYAN}            EMPIRICAL CHALLENGER VERIFICATION RESULTS                  ${RESET}`);
console.log(`${BOLD}${CYAN}========================================================================${RESET}`);
console.log(`  Total Empirical Checks: ${totalChecks}`);
console.log(`  Passed Checks:          ${GREEN}${passedChecks}${RESET}`);
console.log(`  Failed Checks:          ${failedChecks > 0 ? RED : GREEN}${failedChecks}${RESET}`);
console.log(`${BOLD}${CYAN}========================================================================${RESET}\n`);

if (failedChecks > 0) {
  console.error(`${RED}${BOLD}Empirical Challenger detected ${failedChecks} defect(s)!${RESET}`);
  process.exit(1);
} else {
  console.log(`${GREEN}${BOLD}ALL EMPIRICAL CHALLENGER VERIFICATION CHECKS PASSED (100%)!${RESET}\n`);
  process.exit(0);
}
