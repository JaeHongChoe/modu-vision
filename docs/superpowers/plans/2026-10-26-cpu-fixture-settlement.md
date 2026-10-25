# Owned CPU fixture settlement implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make the two genuine owned-CPU fixture boundaries deterministic without relaxing execution guards or deadlines.

**Architecture:** Test-only profile observers synchronize original method boundaries; existing production command, guards and callbacks remain authoritative.

**Tech Stack:** Python pytest, existing generated Python/Node fixture transports, actual CPU training worker.

**Spec:** `docs/superpowers/specs/2026-10-26-cpu-fixture-settlement-design.md`

## Global Constraints

- No production guard, training, migration, ownership or lease changes.
- Preserve original3s/4s/20s/60s bounds, source bytes and all existing refusal/terminal assertions.
- No fake trainer/callback, -c ownership bypass, unrelated process signals, retry loop or external account/model/GPU data.
- Root owns branch changes/publication/hosted CI/merges. Preserve all old failed runs and receipts.

## Review Focus

- The intended original service/main thread and exact retained object alone can be delayed.
- Exceptions raised by original methods retain their traceback and are never swallowed.
- Deadline expiry and foreign/changed token cannot produce a successful boundary.
- The same actual worker performs training on both sides of the migration without a new attempt or fence.
- Failed setup/settlement cannot leave a fixture gate held or replace its useful failure with cleanup noise.

### Task 1: Synchronize changed-nonce ordinary stop before the genuine guard

**Files:** `backend/tests/test_application_launch_quiescence_bridge.py`

**Interfaces:** Extend `_fixture_original_draining_guard_barrier(projects, original_class, retained_states)` with an explicit intended phase/boundary selection while preserving existing draining defaults and original method execution.

- [x] Add RED prepared-phase scheduling tests alongside existing profile/deadline controls: wrong object/code/thread/phase ignored; selected prepared boundary records and waits; original error still has original stack.
- [x] Extend the existing fixture profile/installation to changed_nonce prepared phase. Reuse the real refusal/ordinary-stop publications and original absolute deadline; leave production files untouched.
- [x] Run helper controls and both original changed_nonce/replay cases; test a delayed ordinary-stop schedule under the unchanged original bound. Record commands, RED/GREEN and limits.
- [x] Diff-check, commit only this task plus its design/plan, request independent review before Task2.

### Task 2: Bound actual training work while holding genuine first-step cutover

**Files:** `backend/tests/test_live_control_migration.py`; optionally a small test-only helper module under backend/tests if separation improves readability.

**Interfaces:** Temporary sitecustomize installs a one-shot return observer on the exact original `backend.remote.worker._TrainingStatusCallback.on_step_end.__code__`. It accepts only child main-thread step0/epoch0 after genuine running/current_step1 status for expected writer/job/spec. Atomic held/release markers carry a unique fixture token and the parent's original20s readiness deadline.

- [x] Add RED helper controls for code/thread/job/spec selection, successful original publication, callback exception, one-shot behavior, foreign release token and non-renewable expiry.
- [x] Follow `test_service_failure_matrix.py`'s temporary PYTHONPATH transport, retaining the actual -m worker command. Wait for held marker and authenticate it against the original process/journal before existing migration assertions.
- [x] Use3epochs/32px/batch2/two images per label per split (eight generated images total). Release only after apply and same-PID/fence/duplicate-exclusion checks. Preserve join60 and every terminal/source/control assertion.
- [x] Release in finally before ordinary owned cancellation. Add bounded failure details for current status/progress, worker exit, log tail and observer stack; no extra request or extended wait.
- [x] Run the exact real cutover case and related live migration/actual recovery tests; record actual duration/progress, all failed attempts and unresolved hosted cause separately.
- [x] Diff-check, commit this task and obtain independent review. Root later runs final exact-head hosted CI once after all source is frozen.

Local tasks and their independent reviews are complete. Final branch review and hosted exact-head qualification remain controller-owned and pending.
