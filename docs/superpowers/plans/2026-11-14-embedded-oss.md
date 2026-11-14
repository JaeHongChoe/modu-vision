# Embedded OSS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Use checkboxes to track.

**Goal:** Internalize useful OSS engines/algorithms across the six-stage product without external tool UX/services.
**Architecture:** Existing UI/API/ledger stays authoritative; pure-source scoring, embedded sampler, local bounded tracing, versioned metric, existing canvas improvements.
**Tech Stack:** Python3.13/NumPy/Optuna5/OpenTelemetry1.45.2; React/TypeScript/OTel sdk-trace2.12/API1.9; existing tests.
**Spec:** docs/superpowers/specs/2026-11-14-embedded-oss-design.md

## Global Constraints
- Parent PR27 fbc9e3b9; PR26 remains independent. Preserve original fullCI workflow/digests and source/label/history/authority.
- No external UI/collector/dashboards/cloud/data transfer, no new real provider/model run or live installation.
- Whole library vs selected algorithm must be accurately named; pinned source/license notices preserved.
- Source/context/revision fences, explicit missing/unsupported cases, existing task execution and async ownership.

## Review Focus
- Legacy/stale/unverified-group probability evidence presented as label truth.
- Duplicate proposals, tiny budgets, bad heldout progress and partial checkpoints winning.
- Old detection scores silently changing meaning after upgrade.
- Tracing corrupts return/error identity, leaks scope/credentials, falsely links concurrent polling.
- Keyboard edits leak from child inputs or change semantic graph identity.

### Task1: Embedded classification label quality (worker)
Own backend/vendor/cleanlab + provenance; backend/engine/classification_label_quality.py; data_workbench engine/routes; dataWorkbench service, SavedReviewQueuePanel/tests; additive task action support as needed in assistantTasks files + backend ledger.
- [x] Failing adapter/provenance/stale/queue/scope tests, then normalized-margin source adapter and immutable saved analysis.
- [x] Existing review queue UI/CLI/GPT task integration, no new mandatory wait or automatic truth edit.
- [x] Actual upstream numerical comparison; focused backend/renderer checks.

### Task2: Embedded adaptive sampling (worker)
Own search_strategy.py, automated_trials.py, training_engine.py, training_cli.py, corresponding route/control/recipe tests.
- [x] Failing ask/tell/uniqueness/failure/budget/cancel/recipe tests.
- [x] Optuna adapter and auto>=8 behavior, same runner/scheduler/leases; no pruning without evidence.
- [x] Durable strategy/domain/version receipts and local/remote explicit compatibility.
- [x] Focused actual sampler + existing mocked runner/engine regressions; no training requests.

### Task3: Local diagnostic spans (worker)
Own localTrace TS/Python, api.ts, diagnostics/assistant runtime trace wiring, passive main request hooks, safe read route if needed.
- [x] Failing privacy/scope/identity/timing/concurrency/drop tests.
- [x] Real SDK bounded local exporters; explicit parents for supported assistant reads, unlinked roots elsewhere.
- [x] Existing auth/admission observers and diagnostics regression tests; no raw data or new middleware.

### Task4: Metric contract and flow accessibility (root)
- [x] Versioned AP independent fixtures for missing GT/unmatched GT/ties/empty, preserve legacy history.
- [x] New evaluation/validation metric_contract integration and documentation, actual oracle comparison.
- [x] Existing canvas keyboard/focus/Escape/live guidance with group undo and unchanged semantic identity; browser flow verification.

### Task5: Packaging/integration/review (root)
- [x] Exact package versions/locks/inventory/notices, no data-path credentials, isolated dependencies.
- [x] Add separate embedded CI, preserve existing ci.yml and current CLI workflow.
- [x] Generated API types, scoped backend + full renderer/type/build, owned synthetic browser checks and qualified-license/provenance verification.
- [x] Independent whole-branch review, regression fixes, separate draft PR with truthful implementation vs qualification/install boundaries.

## Qualification outcome

Local implementation and reviewed source are complete. A separate draft PR is the authorized integration path, stacked on PR27. Exact final renderer3135pass/1unrelatedskip; core backend235pass; additive embedded workflow126pass; detection/remote broad168pass; sourceCI50pass; actual owned browser3pass; independent review108backend+17rendererpass. Counts overlap. Two frozen-resource-count and four lazy-route fixture failures reproduce pristine parent and remain recorded. No fullCI/main merge/frozen installation/live app or web replacement occurred. See docs/verification/2026-11-14-embedded-oss.md.
