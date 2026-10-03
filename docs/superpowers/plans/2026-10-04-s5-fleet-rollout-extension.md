# S5-06 staged fleet rollout extension

## Scope and contract

Extend the existing central `FleetRegistry` and project-scoped routes, and connect the actual Fleet panel to those routes. No live target deployment is part of this slice. The existing Field Agent apply/runtime protocol and per-target deployment ledger remain the execution path; no alternative release runner is introduced.

`RolloutPlan` is a persisted schema-version-1 JSON contract in the private fleet SQLite database: plan ID, optimistic revision, staged manifest/device/release policy, immutable target endpoint snapshot, canary IDs, bounded batch size, explicit canary confirmation, operation (`deploy` or `rollback`), per-target status and committed deployment IDs, last observed runtime/readback timestamps, pause reason, reviewer and audit events. Tokens are omitted from the plan/readback. The release is the target's desired release, and each target's timestamped runtime readback is the observed release/health. Execution device support remains enforced by the existing Field Agent/runtime path; this slice does not invent a hardware capability inventory.

Creating a plan stages and records the approved package, without issuing target apply commands. The first advance applies only the selected canaries (their count cannot exceed the reviewed batch bound). Each acknowledged target must return the desired manifest and device and match its exact active deployment receipt. Additional targets require an explicit canary-confirm request, which rechecks the canaries before issuing at most the configured number of apply commands. Each later advance rechecks already applied targets and applies one bounded batch. Every target apply/rollback reuses the current release authority, eligibility, package/policy verification and receipt validation from the existing per-target path.

## Durability and failure behavior

Target intent is persisted before release execution and each verified result is saved with a revisioned append-only event. Revision conflicts require reloading; concurrent mutations of one plan are rejected by the existing process/state lock. Pause takes effect between bounded action requests, not midway through a target's synchronous transport call. A failed/offline target pauses the plan immediately; subsequent targets receive no release command. Pending targets continue to use their existing acknowledged field runtime. A disconnected central client does not establish that a new release was applied.

Resume sends fresh runtime reads for affected/already applied targets. It may adopt an interrupted apply only when the existing target ledger identifies this rollout and its exact live hash/device/receipt match. Rollback runs in reverse target order, at most one configured batch per request, through previous acknowledged target history and current rollback eligibility. A persisted rollback operation cannot resume forward deployment. Interrupted committed rollback is adopted only when the exact previous `restored_from` receipt, request time and fresh ready/hash/device readback match. Missing previous history or changed active receipts pause for review; no fabricated rollback success is recorded.

An unpublished target apply intent must pass explicit live resume/adoption before rollback target selection. This prevents the plan from skipping a forward release that already committed in the target ledger but whose receipt had not yet been saved in the rollout plan. A mismatched live receipt pauses adoption rather than resetting that target to pending.

## Renderer integration

`FleetPanel.tsx` uses `fleetRollouts.ts` for project-scoped list/create/read/advance/pause/resume/rollback. The panel exposes target and canary selection, bounded batch size, separate canary approval, next-batch controls, pause reason, live-readback resume and rollback capability gates. Scope/selected-plan checks discard stale responses. Last stored readback is labeled with its timestamp; reading a saved plan is not presented as a fresh target observation. An interrupted action reloads the durable plan before another revision-bound command is offered.

## Verification and qualification

Focused red/green receipts are saved outside the repository in the extension batch's `model-adapters` evidence directory. The CPU tests exercise actual orchestration, SQLite reopen/audit, HTTP route validation and existing deployment ledger against an authenticated simulated transport. They cover canary confirmation, bounded batches/cohort, offline pause, changed canary readback, revoked current eligibility, stale revisions, partial rollback resume and interruption between committed rollback and plan publication. Renderer tests exercise real service request construction and shared control state; TypeScript checks the actual panel integration.

Simulated transport proves dispatch, receipts and failure handling, not real network/target readiness. Windows, GPU, real fleet failover, optional hardware capability inventory and operator/native UI visual qualification remain separate. No package publication, real target deployment, model-quality approval or operational acceptance is claimed.

Independent review should inspect per-target current eligibility, manifest/device and exact receipt readback, canary confirmation, cohort/batch bounds, target endpoint replacement, crash recovery, irreversible rollback operation direction, revision conflicts, scope changes, unexposed secrets and honest stored-vs-live UI wording.
