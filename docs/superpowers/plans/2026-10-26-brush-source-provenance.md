# Brush source-provenance repair implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify the real brush-class fix through current browser controls while retaining exact historical executed sources.

**Architecture:** Existing gui-test-source-history.json supplies exact ancestor Git blobs and immutable receipt identities; current source pins still match current product bytes.

**Tech Stack:** Existing Playwright, pytest history/evidence checks and Git blob verification.

**Spec:** `docs/superpowers/specs/2026-10-26-brush-source-provenance-design.md`

## Global Constraints

- No product/guard/assertion/selection/workflow weakening or new acceptance claims.
- Preserve receipt files and each declaration's receipt, receipt_sha256, execution_source_sha, test, recorded_test_source_sha256, retained_spec_commit.
- Root owns branch integration and publication; no CI cancellation/retry/dispatch by implementer.

## Review Focus

- Every changed current spec must still authenticate its actual changed product source.
- Every retained ancestor blob, original SHA and receipt identity must remain exact.
- Wrong/foreign/descendant anchors or unrecorded current test bytes still refuse.
- The existing full browser controls run again and are not skipped/replaced.
- Historical-only proof cannot become current/native/person acceptance.

### Task 1: Evolve current pins via existing historical declarations

**Files:** Eight named scripts/e2e/*.spec.ts: flow-existing-refusal-controls, flow-model-less-reopen-controls, flow-node-limit-preflights, flow-readonly-control-preflights, label-mask-empty-preflights, mask-source-choice-empty, ocr-empty-workbench-controls, ocr-prepared-unsent-handoff; docs/verification/gui-test-source-history.json; docs/qa/2026-10-25-brush-mask-class-counts.md.

**Interfaces:** Existing v1 historical declaration; no schema or checker changes.

- [ ] Verify the current route SHA independently; inventory all167 pins, affected17 existing rows and retained ancestor blobs before changing them.
- [ ] Replace only the eight route SHA literals, then demonstrate evidence-gate RED for unrefreshed historical current-test declarations. Preserve hosted nine-failure evidence separately.
- [ ] Update only those17 rows' current_test_source_sha256 and precise history-only reason. Assert all other declaration fields and receipt bytes unchanged.
- [ ] Correct the QA sentence claiming current specs remain original bytes; preserve the historical executed bytes/receipt claim.
- [ ] Run test_gui_execution_historical_source.py, related GUI-receipt/action-evidence controls, both gates and E2E types; run all nine affected browser cases without deleting assertions, plus brush class-count regressions.
- [ ] Save exact commands/counts, RED/GREEN, failures and preserved hashes, diff-check and commit only this task. Request independent review before publication.

### Task 2: Align saved-summary expectation and retain regression CI coverage

**Files:** backend/tests/test_original_current_summary_saved_metadata.py, backend/tests/test_source_ci_model_lanes.py, .github/workflows/ci.yml and the QA result note.

**Interfaces:** Current-summary API remains unchanged; exact full JSON equality includes its four existing additive fields.

- [ ] Reconfirm the exact case RED in the isolated CI-framework-version environment, keeping old FastAPI0.108 incompatibility separate from this schema expectation.
- [ ] Add literal expected excluded_undecodable={excluded:[],recorded:true,restored:[]}, labels_missing=true, split_state=source_folders and the current paired-segmentation expected_layout text. Keep the full equality, source/API/ledger/raw9/closed-context/drain/foreign-selector assertions intact.
- [ ] Add a RED CI-selection policy control requiring test_dataset_reopen_counts.py, test_dataset_summary.py and test_original_current_summary_saved_metadata.py in the full CPU lane. Add only those3 file references; preserve all existing references, timeouts, concurrency and model exclusions. Fast selection derives automatically from the full lane.
- [ ] Run both cases plus related saved-summary/import tests in the compatible environment and related CI-selection controls. No production/API/dependency changes. Update the QA note to distinguish old failure from this new pass; do not claim all-backend acceptance.
- [ ] Diff-check and commit the scoped test/QA correction, independent review. Root retains historical failure logs and owns frozen-head hosted CI.
