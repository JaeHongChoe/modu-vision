# Saved evaluation threshold implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent unsaved threshold previews from changing Step 4 readiness.

**Architecture:** Keep interactive and saved threshold state in the existing
evaluation store. Readiness derives verdicts only from accepted evaluation rows
and saved authority; no parallel backend schema or new persistence is introduced.

**Tech Stack:** Existing TypeScript/React/Zustand, Node test runner, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-25-saved-evaluation-threshold-design.md`

## Global Constraints

- Preserve source data, labelsets, recorded roles, evaluation/calibration guards.
- Preserve interactive preview, manual navigation and existing API save contract.
- No real account/provider calls, model training, deployment or dependency changes.
- Preserve zero and distance-domain thresholds; unknown authority cannot advance.

## Review Focus

- Preview and recommendation changes must not release a saved missed-defect stop.
- Reloaded calibration and optional legacy carriers must agree with saved values.
- Pending/failed/malformed/stale calibration must retain prior saved authority.
- Source/project/model replacement must not reuse previous threshold authority.
- Unknown thresholds and strict anomaly patch-score comparison must remain safe.

### Task 1: Separate saved threshold authority from preview

**Files:**
- Modify: `src/renderer/stores/useEvaluationStore.ts`
- Modify if needed: `src/renderer/types/index.ts`
- Modify: `src/renderer/components/wizard/useWorkflowReadiness.ts`
- Modify: `src/renderer/components/evaluation/EvaluationStudio.tsx`
- Test: `src/renderer/components/wizard/workflowReadiness.test.cjs`
- Test: `src/renderer/stores/evaluationCalibrationSource.test.cjs`
- Test: `src/renderer/stores/evaluationClassSemantics.test.cjs`

**Interfaces:** The store produces `persistedThreshold: number | null`, scoped to
its accepted results. Existing preview setters and API signatures remain intact.
The hook consumes that authority and accepted rows; the view explains differences.

- [x] Add real-behavior RED tests for saved .50/preview .20/score .30, reverse,
  live analysis, zero/distance/unknown thresholds, and calibration/scope changes.
- [x] Run focused tests; expected new assertions fail against unmodified main.
- [x] Add the minimal saved-threshold resolver/state lifecycle and hook usage.
- [x] Add the mismatch explanation and a rendered behavior regression.
- [x] Run focused suites; expected all pass with no authority guard weakened.
- [x] Run full Node suite, type checks, build and evidence gates; report failures.
- [ ] Review the whole diff independently, record verification, then commit only
  this bug's code/tests/docs. Publish a separate PR; no automatic main merge.
