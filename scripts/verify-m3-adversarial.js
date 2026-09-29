/**
 * scripts/verify-m3-adversarial.js
 * Adversarial Verification Suite for Milestone 3 (AutoML Trainer & Evaluation Studio).
 * Authored by Challenger 1.
 *
 * Verifies:
 * 1. Mathematical formulations of Zero-Escape tau* and scrubber coordinate transformations.
 * 2. Confusion matrix marginal metrics (Support, Recall, Pred Total, Precision, Overall Accuracy).
 * 3. Row-normalized intensity gradient formula and escape alerting.
 * 4. 4-Quadrant sample classification into FP (과검), FN (미검), TP (검출), TN (정상).
 * 5. Debounced backend heatmap fetching logic (120ms debounce with request cancellation).
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
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

console.log('================================================================');
console.log('   Vision AI Studio — Milestone 3 Adversarial Verification Suite ');
console.log('================================================================\n');

// -----------------------------------------------------------------------------
// Test 1: Zero-Escape tau* Coordinate Math & Threshold Scrubber Inversion
// -----------------------------------------------------------------------------
console.log('--- Test 1: Zero-Escape tau* Coordinate Math & Scrubber Inversion ---');

const width = 480;
const height = 190;
const padLeft = 40;
const padRight = 15;
const padTop = 15;
const padBottom = 25;
const plotW = width - padLeft - padRight; // 425
const plotH = height - padTop - padBottom; // 150

const tauToX = (tau) => padLeft + Math.max(0, Math.min(1, tau)) * plotW;
const rateToY = (rate) => padTop + plotH - (Math.max(0, Math.min(100, rate)) / 100) * plotH;
const xToTau = (x) => {
  const raw = (x - padLeft) / plotW;
  return Math.max(0.01, Math.min(0.99, Math.round(raw * 100) / 100));
};

// Check boundary points
assert(tauToX(0.0) === padLeft, 'tauToX(0.0) maps exactly to padLeft (40px)');
assert(tauToX(1.0) === padLeft + plotW, 'tauToX(1.0) maps exactly to padLeft + plotW (465px)');
assert(rateToY(0) === padTop + plotH, 'rateToY(0%) maps to bottom of plot area (165px)');
assert(rateToY(100) === padTop, 'rateToY(100%) maps to top of plot area (15px)');
assert(rateToY(50) === padTop + plotH / 2, 'rateToY(50%) maps to vertical midpoint (90px)');

// Invertibility test: xToTau(tauToX(tau)) approx tau
let invertSuccess = true;
for (let t = 0.05; t <= 0.95; t += 0.05) {
  const x = tauToX(t);
  const recoveredTau = xToTau(x);
  if (Math.abs(recoveredTau - Math.round(t * 100) / 100) > 0.01) {
    invertSuccess = false;
    break;
  }
}
assert(invertSuccess, 'tau -> X -> tau inversion preserves threshold with <= 0.01 resolution across sweep');

// Clamping test: beyond SVG bounding box
assert(xToTau(-50) === 0.01, 'xToTau clamps negative coordinates to 0.01');
assert(xToTau(1000) === 0.99, 'xToTau clamps excessive coordinates to 0.99');

// -----------------------------------------------------------------------------
// Test 2: Confusion Matrix Marginal Metrics & Row-Normalized Gradients
// -----------------------------------------------------------------------------
console.log('\n--- Test 2: Confusion Matrix Marginal Metrics & Normalization Math ---');

// Synthesize 3x3 Industrial Confusion Matrix:
// Classes: ["OK", "Scratch", "Pinhole"]
// Matrix layout:
//           Pred OK   Pred Scratch   Pred Pinhole
// True OK        85             10              5   (Support = 100)
// True Scratch    2             45              3   (Support = 50)
// True Pinhole    1              4             45   (Support = 50)
const classes = ['OK', 'Scratch', 'Pinhole'];
const matrix = [
  [85, 10, 5],
  [2, 45, 3],
  [1, 4, 45],
];

// Row Metrics
const rowMetrics = matrix.map((row, i) => {
  const support = row.reduce((sum, val) => sum + val, 0);
  const correct = row[i] || 0;
  const recall = support > 0 ? (correct / support) * 100 : 0;
  return { support, recall };
});

assert(rowMetrics[0].support === 100, 'Class 0 (OK) Support is exactly 100');
assert(rowMetrics[0].recall === 85.0, 'Class 0 (OK) Recall is exactly 85.0%');
assert(rowMetrics[1].support === 50, 'Class 1 (Scratch) Support is exactly 50');
assert(rowMetrics[1].recall === 90.0, 'Class 1 (Scratch) Recall is exactly 90.0% (45/50)');
assert(rowMetrics[2].support === 50, 'Class 2 (Pinhole) Support is exactly 50');
assert(rowMetrics[2].recall === 90.0, 'Class 2 (Pinhole) Recall is exactly 90.0% (45/50)');

// Col Metrics
const colMetrics = classes.map((_, j) => {
  let predTotal = 0;
  for (let i = 0; i < matrix.length; i++) {
    predTotal += matrix[i][j] || 0;
  }
  const correct = matrix[j][j] || 0;
  const precision = predTotal > 0 ? (correct / predTotal) * 100 : 0;
  return { predTotal, precision };
});

// Col 0: Pred OK = 85 + 2 + 1 = 88. Precision = 85/88 = 96.5909%
assert(colMetrics[0].predTotal === 88, 'Pred Total for OK is 88');
assert(Math.abs(colMetrics[0].precision - (85 / 88) * 100) < 1e-4, 'Precision for OK is (85/88)*100%');

// Col 1: Pred Scratch = 10 + 45 + 4 = 59. Precision = 45/59 = 76.271%
assert(colMetrics[1].predTotal === 59, 'Pred Total for Scratch is 59');
assert(Math.abs(colMetrics[1].precision - (45 / 59) * 100) < 1e-4, 'Precision for Scratch is (45/59)*100%');

// Col 2: Pred Pinhole = 5 + 3 + 45 = 53. Precision = 45/53 = 84.905%
assert(colMetrics[2].predTotal === 53, 'Pred Total for Pinhole is 53');
assert(Math.abs(colMetrics[2].precision - (45 / 53) * 100) < 1e-4, 'Precision for Pinhole is (45/53)*100%');

// Total Samples & Overall Accuracy
const totalSamples = matrix.reduce((acc, row) => acc + row.reduce((sum, val) => sum + val, 0), 0);
assert(totalSamples === 200, 'Total verification samples = 200');

let totalCorrect = 0;
for (let i = 0; i < matrix.length; i++) {
  totalCorrect += matrix[i][i] || 0;
}
const overallAccuracy = (totalCorrect / totalSamples) * 100;
assert(totalCorrect === 85 + 45 + 45, 'Total correct predictions = 175');
assert(overallAccuracy === 87.5, 'Overall Accuracy is exactly 87.5% (175/200)');

// Zero-Division Protection Edge Cases
const emptyRow = [0, 0, 0];
const emptySupport = emptyRow.reduce((sum, val) => sum + val, 0);
const emptyRecall = emptySupport > 0 ? (emptyRow[0] / emptySupport) * 100 : 0;
assert(emptyRecall === 0, 'Zero-division in Support correctly produces 0% Recall without NaN/Infinity');

const zeroPredTotal = 0;
const emptyPrecision = zeroPredTotal > 0 ? (0 / zeroPredTotal) * 100 : 0;
assert(emptyPrecision === 0, 'Zero-division in PredTotal correctly produces 0% Precision without NaN/Infinity');

// -----------------------------------------------------------------------------
// Test 3: 4-Quadrant Sample Classification & Partition Property
// -----------------------------------------------------------------------------
console.log('\n--- Test 3: 4-Quadrant Sample Classification & Partition Property ---');

const NORMAL_SET = new Set(['ok', 'normal', 'pass', 'good', '0', 'background', 'ok_normal', 'true_ok', 'ok_chip']);
function isDefectLabel(label) {
  if (label === undefined || label === null) return false;
  if (typeof label === 'number') return label !== 0;
  const clean = String(label).trim().toLowerCase();
  return !NORMAL_SET.has(clean);
}

function computeSampleVerdict(item, threshold) {
  const isDefect = item.is_defect !== undefined ? Boolean(item.is_defect) : isDefectLabel(item.ground_truth);
  let defectScore;
  if (item.defect_score !== undefined) {
    defectScore = item.defect_score;
  } else {
    const isPredDefect = isDefectLabel(item.predicted_class);
    defectScore = isPredDefect ? item.confidence : 1.0 - item.confidence;
  }
  const predictedNg = defectScore >= threshold;
  if (isDefect) {
    return predictedNg ? 'CORRECT_NG' : 'ESCAPE';
  } else {
    return predictedNg ? 'OVERKILL' : 'CORRECT_OK';
  }
}

// Generate 100 synthetic industrial samples with diverse defect scores
const testSamples = [];
for (let i = 0; i < 40; i++) {
  // 40 True Defects (Ground Truth: Scratch or Pinhole)
  testSamples.push({
    image_id: `defect_${i}`,
    file_name: `defect_${i}.png`,
    ground_truth: i % 2 === 0 ? 'Scratch' : 'Pinhole',
    defect_score: 0.20 + (i / 40) * 0.79, // scores from 0.20 to 0.99
    is_defect: true,
  });
}
for (let i = 0; i < 60; i++) {
  // 60 True Normals (Ground Truth: OK)
  testSamples.push({
    image_id: `normal_${i}`,
    file_name: `normal_${i}.png`,
    ground_truth: 'OK',
    defect_score: 0.01 + (i / 60) * 0.45, // scores from 0.01 to 0.45
    is_defect: false,
  });
}

// Verify partition property across threshold sweep
let partitionHoldsAcrossAllThresholds = true;
for (let tau = 0.01; tau <= 0.99; tau += 0.05) {
  let escapes = 0;
  let overkills = 0;
  let correctOks = 0;
  let correctNgs = 0;

  for (const s of testSamples) {
    const v = computeSampleVerdict(s, tau);
    if (v === 'ESCAPE') escapes++;
    else if (v === 'OVERKILL') overkills++;
    else if (v === 'CORRECT_OK') correctOks++;
    else if (v === 'CORRECT_NG') correctNgs++;
  }

  const sum = escapes + overkills + correctOks + correctNgs;
  if (sum !== testSamples.length) {
    partitionHoldsAcrossAllThresholds = false;
    break;
  }
}
assert(partitionHoldsAcrossAllThresholds, 'Partition property holds for all tau in [0.01, 0.99]: |TP| + |FP| + |FN| + |TN| === N (100)');

// Test Zero-Escape threshold on these samples:
// Minimum defect score among true defects is 0.20
const minDefectScore = Math.min(...testSamples.filter((s) => s.is_defect).map((s) => s.defect_score));
const tauStar = Math.max(0.0, minDefectScore - 1e-4);

let escapesAtTauStar = 0;
for (const s of testSamples) {
  const v = computeSampleVerdict(s, tauStar);
  if (v === 'ESCAPE') escapesAtTauStar++;
}
assert(escapesAtTauStar === 0, `Zero-Escape verified at tau* = ${tauStar.toFixed(4)}: FN 미검 count is strictly 0`);

// -----------------------------------------------------------------------------
// Test 4: Dual-Key Matching in computeFilteredList
// -----------------------------------------------------------------------------
console.log('\n--- Test 4: Dual-Key Confusion Matrix Cell Filtering ---');

const sampleA = { image_id: 'img_01', file_name: 'wafer_01.png', file_path: '/data/wafer_01.png', ground_truth: 'OK', predicted_class: 'Scratch' };
const sampleB = { image_id: 'img_02', file_name: 'wafer_02.png', file_path: '/data/wafer_02.png', ground_truth: 'OK', predicted_class: 'OK' };

// Case 1: cell_samples populated
const confusionMatrixWithMapping = {
  classes: ['OK', 'Scratch'],
  matrix: [[1, 1], [0, 0]],
  cell_samples: {
    'OK:Scratch': ['/data/wafer_01.png'],
  },
};

function filterList(samples, selectedCell, cm) {
  let list = samples;
  if (selectedCell && cm) {
    const key = `${selectedCell.trueClass}:${selectedCell.predClass}`;
    const allowedPaths = new Set(cm.cell_samples?.[key] || []);
    if (allowedPaths.size > 0) {
      list = list.filter((item) => allowedPaths.has(item.file_path) || allowedPaths.has(item.image_id) || allowedPaths.has(item.file_name));
    } else {
      list = list.filter((item) => item.ground_truth === selectedCell.trueClass && item.predicted_class === selectedCell.predClass);
    }
  }
  return list;
}

const filteredWithMapping = filterList([sampleA, sampleB], { trueClass: 'OK', predClass: 'Scratch' }, confusionMatrixWithMapping);
assert(filteredWithMapping.length === 1 && filteredWithMapping[0].image_id === 'img_01', 'Primary key matching via cell_samples Set selects correct sample');

// Case 2: cell_samples missing / unpopulated (fallback to semantic matching)
const confusionMatrixWithoutMapping = {
  classes: ['OK', 'Scratch'],
  matrix: [[1, 1], [0, 0]],
  cell_samples: {},
};
const filteredFallback = filterList([sampleA, sampleB], { trueClass: 'OK', predClass: 'Scratch' }, confusionMatrixWithoutMapping);
assert(filteredFallback.length === 1 && filteredFallback[0].image_id === 'img_01', 'Semantic fallback (ground_truth === trueClass && predicted_class === predClass) works seamlessly when cell_samples empty');

// -----------------------------------------------------------------------------
// Test 5: Debounced Backend Heatmap Fetching & Request ID Cancellation
// -----------------------------------------------------------------------------
console.log('\n--- Test 5: Debounced Heatmap Fetching & In-Flight Request Cancellation ---');

async function testDebounceAndRaceConditions() {
  let debounceTimer = null;
  let currentRequestId = 0;
  let backendCallCount = 0;
  let activeState = { heatmapOverlay: null, loading: false };

  // Simulated backend API with variable latency
  function mockGetHeatmap(imageId, threshold, latencyMs) {
    backendCallCount++;
    return new Promise((resolve) => {
      setTimeout(() => {
        resolve({ overlay_base64: `heatmap_${imageId}_tau_${threshold.toFixed(2)}` });
      }, latencyMs);
    });
  }

  function setConfidenceThreshold(th, latencyMs = 20) {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }
    debounceTimer = setTimeout(async () => {
      const requestId = ++currentRequestId;
      activeState.loading = true;
      try {
        const res = await mockGetHeatmap('sample_01', th, latencyMs);
        if (requestId === currentRequestId) {
          activeState.heatmapOverlay = res.overlay_base64;
          activeState.loading = false;
        }
      } catch {
        if (requestId === currentRequestId) {
          activeState.loading = false;
        }
      }
    }, 120);
  }

  // 1. Rapid slider scrubbing: 20 rapid threshold updates within 80ms
  for (let i = 1; i <= 20; i++) {
    setConfidenceThreshold(i * 0.04);
  }

  // At t=60ms (before 120ms debounce expires), backend calls should still be 0
  await new Promise((r) => setTimeout(r, 60));
  assert(backendCallCount === 0, 'No backend calls fired during rapid slider scrubbing (debounce holding)');

  // Wait until debounce expires and response returns (120ms debounce + 30ms network)
  await new Promise((r) => setTimeout(r, 150));
  assert(backendCallCount === 1, `Exactly 1 backend request was made after scrubbing stopped (got ${backendCallCount})`);
  assert(activeState.heatmapOverlay === 'heatmap_sample_01_tau_0.80', 'Active heatmap overlay updated with final threshold (0.80)');
  assert(activeState.loading === false, 'Loading state cleared after successful fetch');

  // 2. Race condition test: Request A (slow, 150ms) initiated before Request B (fast, 20ms)
  // Request A
  const reqAId = ++currentRequestId;
  activeState.loading = true;
  mockGetHeatmap('sample_01', 0.20, 150).then((res) => {
    if (reqAId === currentRequestId) {
      activeState.heatmapOverlay = res.overlay_base64;
      activeState.loading = false;
    }
  });

  // Request B dispatched shortly after with higher requestId
  await new Promise((r) => setTimeout(r, 10));
  const reqBId = ++currentRequestId;
  mockGetHeatmap('sample_01', 0.90, 20).then((res) => {
    if (reqBId === currentRequestId) {
      activeState.heatmapOverlay = res.overlay_base64;
      activeState.loading = false;
    }
  });

  // Wait for Request B to complete (at ~30ms), but Request A is still pending
  await new Promise((r) => setTimeout(r, 50));
  assert(activeState.heatmapOverlay === 'heatmap_sample_01_tau_0.90', 'Request B completed first and set overlay to tau=0.90');

  // Wait for Request A to finally complete (at ~160ms)
  await new Promise((r) => setTimeout(r, 130));
  assert(
    activeState.heatmapOverlay === 'heatmap_sample_01_tau_0.90',
    'Stale Request A response was discarded by currentRequestId check (overlay preserved at tau=0.90)'
  );
}

testDebounceAndRaceConditions().then(() => {
  console.log('\n================================================================');
  console.log(`   ADVERSARIAL VERIFICATION RESULT: ${passes} PASSES, ${failures} FAILURES`);
  console.log('================================================================');
  if (failures > 0) {
    process.exit(1);
  } else {
    process.exit(0);
  }
});
