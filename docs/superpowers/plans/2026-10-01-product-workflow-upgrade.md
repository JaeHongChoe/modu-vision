# Product Workflow Upgrade Implementation Plan

> For agentic workers: use subagent-driven-development (or executing-plans for integration-owned tasks). Execute this approved plan continuously.

**Goal:** Implement every approved product workflow improvement and record evidence without overstating GUI or hardware acceptance.
**Architecture:** Extend existing scoped APIs and persisted project artifacts; introduce shared workspace components and a separate requirement ledger.
**Tech Stack:** Electron, React, TypeScript, Zustand, FastAPI, Python/PyTorch.
**Spec:** `docs/superpowers/specs/2026-10-01-product-workflow-upgrade-design.md`.

## Global constraints
- Preserve supplied source images, existing projects, candidate review, active model/flow identity and remote jobs.
- No public competitor/company benchmark names, private research, personal source paths, credentials or speculative parity claims.
- No unknown process cancellation, costly GPU submission or physical approval claim.
- Integration owns App.tsx, global styling, backend app router registration and program status updates. Workers own disjoint feature areas and write their execution ledger.
- Test behavior before new logic; run focused regression for changed contracts. Run typecheck/build and combined integration regression before delivery.
- All U001-U033 remain tracked even when execution spans context compaction; do not close the plan after the first batch.

## Review focus
1. Source/project/task switches and in-flight stale responses: every worker owns its source guards and reopen test.
2. Cancellation or lost connection: training owner tests requested versus acknowledged termination; delivery tests operational rollback/ACK.
3. Malformed shapes/ROI/graph and absent models: data/flow owners test validation and visible failure, never silent fallback.
4. Persistence after process/UI restart: each owner tests reload from disk/storage, not just React rerender.
5. Credentials, unsupported device claims and protocol failures: delivery owner tests redaction and mock failures; integration reviews public diff.

## Task 1 — Training and first-use workflow (U001-U004, U017, U019, U032)
- [x] Read `ErrorModal.tsx`, training/model catalogs, stores, automated-training and compute contracts.
- [x] Add failing behavioral tests for real action effects, task identity/cancellation, budget validation and anomaly purpose.
- [x] Implement truthful resolution actions, common preparation UI, unified task-center component, readiness helper, budgets/profile display and capability boundaries.
- [x] Improve specialist entry using actual project images and labels; retain expert import paths in details. All ten supported families must have an explicit preparation route.
- [x] Run focused frontend/backend tests; record RED/GREEN commands and evidence in `docs/implementation-ledger/UPGRADE-training.md`.

## Task 2 — Data and labeling (U013-U016, U018, U020)
- [x] Read dataset/review/annotation/provider/OBB contracts and existing versions.
- [x] Add failing tests for immutable edit transforms, near-duplicate/blur exposure checks, review order/reopen, Korean provider candidate scope and independent direction persistence.
- [x] Implement project-scoped diagnostics and derived image edit APIs and frontend panels; support bbox/polygon/mask/rotated boxes.
- [x] Implement ranked resumable queue and originating evaluation return.
- [x] Implement configured VLM provider with Korean conditions and positive/negative image examples; candidates require explicit review. Add separate OBB direction target/output without changing axial angles.
- [x] Run focused tests; record evidence in `docs/implementation-ledger/UPGRADE-data.md`.

## Task 3 — Delivery and field workspace (U005, U021-U025, U031, U033)
- [x] Read export/runtime/compute/fleet/SDK contracts.
- [x] Add failing tests for package reopen/selection, setup preflight, protocol form roundtrip, operator state and diagnostic redaction.
- [x] Implement saved package library, generic connection wizard, device verification display, PLC/MES forms with advanced JSON and local protocol receiver exercises.
- [x] Implement operator workspace, installation/update compatibility diagnostics, redacted bundle and SDK prerequisite presentation.
- [x] Run focused tests; record evidence in `docs/implementation-ledger/UPGRADE-delivery.md`.

## Task 4 — Flow workspace (U006-U012)
- [x] Read flow store, graph/compiler/execution, versions and intermediate artifact contracts.
- [x] Add failing tests for stop-node execution, stale evidence, original-image ROI bounds, A/B input identity and mapping validation.
- [x] Implement graphical ROI selection/preview, linked debugger/decision explanation, selected-node execution, reusable templates and fixed-test A/B comparison.
- [x] Integrate workspace layout and draft/saved/deployed states; retain existing graph validation and undo/redo.
- [x] Run graph, execution, package parity and new feature tests; record `docs/implementation-ledger/UPGRADE-flow.md`.

## Task 5 — Shared design and integration (U026-U029)
- [x] Mount new components and routers; preserve source/task context guards.
- [x] Normalize controls/statuses/loading/error states, details disclosure and keyboard focus.
- [x] Make guidance collapsible and simplify training visualization; improve image area/readability without removing useful metrics.
- [x] Run typecheck/build, all frontend behavioral suites and relevant backend integration; resolve failures.

## Task 6 — Acceptance and publication (U030, all IDs)
- [x] Validate program coverage and all evidence paths; independently review changes against each requirement.
- [x] Launch isolated owned app, exercise actual GUI routes with source-derived project fixtures, persist and reopen. Record button actions, screenshot/artifact evidence and remaining external hardware limits.
- [x] Verify original data hashes unchanged, packaging/SDK contents, application readiness and diagnostics redaction.
- [ ] Mark only evidenced states. Merge/push verified delivery using prior authorization; verify remote HEAD. Do not rewrite history.

## Commands
- Frontend: `npm run typecheck`, `npm run build`, `node --test <changed *.test.cjs>` (inspect existing harness before invoking).
- Backend: `python3 -m pytest <focused changed contract tests>`, then combined regression once implementation settles.
- Registry: `python3 scripts/verify_product_upgrade.py` (create independent coverage/evidence verifier).
- Package: existing `scripts/verify-packaging.js` and saved app manifest checks.

## Execution ledger
Per-owner ledgers persist task steps, tests, changed files, integration mounts and limitations. The program JSON is the master status record. A completed function is not an accepted end-to-end route until GUI persistence/reopen/error/handoff evidence is recorded.

## Final review repair gates
- [x] Completed specialist tasks reopen the exact model and prepared dataset and close the task overlay.
- [x] Export, optimization and inspection tasks open their exact saved package/job/run; absent targets show an error rather than selecting the newest item.
- [x] Server changes invalidate old response/cache generations after the actual API transport changes.
- [x] Data diagnostics show the saved project split, and transformed polygon bbox caches match transformed points.
- [x] Cross-project templates bind the target model's ordered class vocabulary and remap downstream class rules.
- [x] Rebuild the native bundle after these repairs, reopen the owned project and record final verification without manufacturing or hardware approval claims.

## Final execution result

All 33 approved requirements are implemented and integration verified. Final backend 1555 passed/27 skipped; frontend 115 passed; typecheck/build and native bundle integrity 40 passed. Actual native ROI save/reopen, fixed-test selection, task cancellation/lease return, exact model handoff, derived-image preservation, diagnostic export and local protocol ACK are recorded in the integration ledger. Complete unexercised native routes and external hardware/provider execution remain pending in the registry. Existing Git history is preserved.
