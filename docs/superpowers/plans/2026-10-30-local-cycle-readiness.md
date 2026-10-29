# Local cycle readiness repair implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline with TDD and an independent final review. Preserve all runtime QA evidence.

**Goal:** Complete the real native and localhost browser Stage 1–6 QA cycle, repairing the three observed readiness and recovery defects.

**Architecture:** Keep backend SSH administration and project authorization unchanged. The compute store may recognize a local-only nonadministrator execution context only from a valid same-origin browser session, accepted matching actor/project context, and current native projects capability. Inspection guidance consumes the actual saved run lifecycle instead of inferring completion from a flow evaluation. Project selection uses the existing authenticated gate and preserves recovery ownership.

**Tech stack:** TypeScript, Zustand, React, existing node:test harnesses, Electron and the approved localhost native bridge.

**Spec/evidence:** User-authorized actual Stage 1–6 validation. Reproductions are in `.superpowers/sdd/2026-10-29-continuous-assistant-training/followup/actual-cycle-20261030/web-actual-training-request-UI.txt` and `native-stage6-running0-premature-footer.txt`. Existing product and permission contract: `DESIGN.md`, `docs/local-browser.md`, `services/browserHost.ts`, `services/api.ts`, and `backend/api/routes_compute.py`.

## Global constraints

- Source baseline main `0c74816dc123306679e0a199ccf19134abfbc93b`, verified product tree `0ecb4b0e08eb69bd9ac01c7910cd95fce8907d61`.
- Reuse the existing linked worktree; preserve the primary checkout and all user/ignored data. Do not edit backend authorization or grant administrator access.
- No real GPT/provider calls, image external transmission, remote GPU work, operational approval, deployment, or human field acceptance.
- Reuse the existing Korean controls, surfaces, vocabulary, and canonical state owners. No visual redesign.
- Actual toy model quality (native test accuracy 50%, FN6/FP0) remains visible; do not retrain to manufacture a better result.
- Preserve original synthetic sources, label audit, checkpoints, saved evaluation/flow/run records, and both result exports.
- No repeated completed CI, training, evaluation, or dispatch. Any new-source CI is separate from completed main CI.

## Review focus

1. A generic HTTPS/shared server, unknown native capability, admin account, expired session, actor mismatch, or remote project must retain the existing compute-discovery behavior and refusal.
2. Project/account changes during native readiness discovery must not publish stale local readiness.
3. Stage 6 cannot claim completion before persisted `completed`, including 12/12 rows while finish persistence is pending; stopped/error runs need recovery guidance.
4. Inspection progress from another source scope cannot affect this project.
5. A 503 selection failure must support placeholder reset and retry of the same project; the 409 location recovery and busy guards must retain ownership.

## Task 1: Separate confirmed local execution from SSH administration

**Files:** `src/renderer/stores/useComputeStore.ts`, canonical `services/api.ts`/`browserHost.ts` transport registration, `src/renderer/stores/computeContextReadiness.test.cjs`, new `localBrowserComputeReadiness.test.cjs`. The transport-only SharedProjectPanel test fixture must mirror the new API helper without changing its existing assertions.

**Interface:** Existing `ComputeState.load(): Promise<void>` remains unchanged. Confirmed local-only discovery publishes profiles `[]`, selected target `null`, loaded true, no stale probes/error, without SSH GETs. All writes/probes/selections retain existing API authorization.

- [ ] Run the unchanged relevant renderer tests and save their baseline results.
- [ ] Add and watch a failing test using real browser session/host capability and accepted context with mocked transport only: nonadmin local CPU becomes ready and SSH configuration is never requested.
- [ ] Add counterexamples from review focus 1–2. Preserve `an accepted shared discovery failure retains the genuine error`.
- [ ] Implement the narrow local readiness branch with identity/lifecycle fencing. Unknown or nonlocal contexts use original discovery.
- [ ] Run focused compute and mounted-header tests; read actual failures and results.

## Task 2: Publish and consume the real inspection lifecycle

**Files:** Canonical `workflowProgress.ts` located by repository file inventory, `BatchInspectionPanel.tsx`, `workflowReadiness.ts`, and their existing tests.

**Interface:** Publish actual persisted run status and runtime running state in the same source scope as counts. Status-only changes must notify subscribers. Existing count consumers remain compatible.

- [ ] Add and watch failing readiness tests for no run, 0/12 running, 12/12 still running, stopped, completed with errors, and completed without errors; add cross-scope and status-only publication tests.
- [ ] Extend the existing progress contract and publisher and wire the real batch report/status.
- [ ] Return Korean guidance matching the actual phase; keep model quality and deployment approval separate.
- [ ] Run progress and readiness related suites.

## Task 3: Permit retry after a failed local project selection

**Files:** `src/renderer/components/compute/SharedProjectPanel.tsx`, `LocalProjectGate.test.cjs`.

**Interface:** Existing select handler first honors busy refusal. An empty ID clears selected value and transient error/recovery state, performs no request, then allows reselecting the same project. A direct 409 recovery failure retains the selected project.

- [ ] Add and watch a failing actual component transition test: 503 → placeholder reset → same project selected successfully, with no request for empty selection.
- [ ] Implement the minimal handler change and retain busy/409/cancel contracts.
- [ ] Run the local project gate and shared-project readiness tests.

## Task 4: Integrate and finish actual runtime verification

The actual normal native restart left the renderer in connecting/project-loading while the token-free public health was HTTP 200. One normal reload restored the same PID/port/model/flow/run. This proves reload recovery, not the exact cause. Source inspection found an independently reproducible stale initial status/cleanup response path in `App.tsx` that can overwrite newer state and clear the cached port/project context.

- [ ] In `App.tsx` and a focused adjacent test, first reproduce a delayed STARTING snapshot arriving after a fresh HEALTHY event, and a response arriving after effect disposal. Exercise the actual App effect, not a copy or source-text assertion.
- [ ] Fence the initial snapshot and effect lifetime while preserving genuine later crash/restart/new PID/new port events. A healthy-forever latch is forbidden. Keep browser bootstrap, authorization, project guards and real errors intact.
- [ ] Label this as a verified source lifecycle defect; do not claim the original host freeze's event ordering was observed. Confirm actual new-build native restarts and saved-data readback separately.

- [ ] Run typecheck, renderer suite/build and appropriate backend permission regression; independently review the complete source diff once.
- [ ] Prepare the changed web frontend without terminating any unknown user work. Use the owned QA account/project only.
- [ ] Complete the existing web cycle: real CPU ResNet18 training (2 epochs, 64px, batch4, 3 minute budget), actual test evaluation, saved flow evaluation, CPU inspection and JSON/CSV exports.
- [ ] Reconnect/reopen native and web projects, compare immutable input/model/flow/evaluation/run IDs and saved hashes; do not start new jobs on reconnect. Restore the native original project/view.
- [ ] Record source revision, actual results/limits, product defects repaired, CI/publication/install status separately. If publication is performed, use exact-head CI and normal merge policy without bypass.
