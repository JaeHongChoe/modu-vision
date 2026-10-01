# Product stages 0–5 implementation plan

> **For implementers:** Use subagent-driven-development with focused behavior tests and independent review. The user approved this roadmap and direct integration into `main`. Keep file ownership explicit; the coordinator integrates shared API/router changes and runs the final broad gate.

**Goal:** Finish the connected data → training → evaluation → editable flow → inspection → independent service workflow, including recovery and traceable decisions.

**Architecture:** Extend the existing project/version, DAG, compute journal, package and managed-service contracts. New evaluation and intake records have immutable inputs and hashes. Opening a version only edits a draft. Approval, activation and actual target acceptance are separate persisted actions.

**Stack:** Python/FastAPI/PyTorch; React/TypeScript/Electron; SQLite and atomic JSON journals; SSH Python/Docker workers.

## Acceptance rules

- Empty or approved annotation is not proof of normal. Explicit truth is scoped to task, classes and source revision. Unknown truth stays unknown.
- Classification, segmentation and patch classification retain DINOv3 defaults; detection retains YOLO. Source images and source labels remain unchanged during QA.
- Saved model, evaluation, flow, package and target-run identities must agree. A failed or unperformed parity check cannot become a deployment pass.
- Real hardware, platform signing and physical equipment acceptance are reported separately from implementation and tests. No fabricated target receipts.
- Public code, documentation and commit messages use neutral product terminology. Never publish credentials or research correspondence.
- Preserve unrelated GPU processes, recovery files and existing installations. No global startup registration or costly training sweep.

## 0. Shared semantics and recovery states

1. Add a versioned class-role resolver used by flow decisions, runtime generation, analysis and renderer class colors. Explicit roles override aliases; normalize supported aliases consistently. Test Korean/English aliases, `no_defect`, explicit override and ambiguous classes.
2. Preserve cancelled, stopped, interrupted and uncertain training states in the renderer. Test terminal state restoration and action eligibility.
3. Split saved-version read from explicit activation. Test opening leaves active pointer unchanged and activation validates the chosen version.
4. Detect confirmed dead owned workers even when a stale running status file exists. Persist cancel intent and distinguish lost connection from confirmed process exit.
5. Add explicit image truth records with reviewer/source/task/class revision and invalidation on changed source or labels.

## 1. Whole-flow evaluation and deployment evidence

1. Add an immutable whole-flow evaluation engine/API over a saved graph and frozen held-out cohort. Persist OK/NG/REVIEW confusion, truth coverage, escape/overkill image lists and node/ROI evidence. Unknown truth is excluded and disclosed; absent normal truth gives unavailable overkill.
2. Connect manual approval revision selection to package export and managed service. Validate exact checkpoint/current approval revision and existing role guards.
3. Extend parity to a fixed multi-image cohort and explicit target device; preserve input/graph/model/artifact hashes and tolerance. Persist failures. Keep one-image compatibility only as an explicitly limited check.
4. Prepare an isolated real-data reference cycle from the user-supplied read-only inspection dataset. Record source hashes, split/model/flow/package and actual target execution. Never create normal ground truth from the eight unlabeled images.

## 2. Step 5 and connected UX

1. Expose all node artifacts through search/pagination and show execution/ROI path, branch input/output and skip cause.
2. Separate basic and advanced rule controls; show required fields, model class choices, node validation and units at the point of editing.
3. Show downstream impact of data/model/rule edits and required re-evaluation. Version open and activate have distinct visible controls.
4. Extend A/B target selection and preserve immutable run identity. Partial execution remains REVIEW. Cached inference is reusable only with exact input/ancestor/model/config identity.
5. Verify typography, empty/error/loading/disabled states and actual workflow navigation in the app.

## 3. Remote and local compute reliability

1. Owned-worker liveness, durable cancellation, bounded cooperative cancellation and escalation, disk-full terminal handling and safe reservation release.
2. Resumable transfer, bounded SSH connection behavior, dependency/version and cached-weight preflight. Include the actual DINOv3 and YOLO dependencies in the remote image.
3. Move local basic training to an owned subprocess using the existing training CLI and journal contract.
4. Reconnect a live worker without relaunch; a dead worker offers deterministic restart. Exact optimizer/RNG resume must be explicitly recorded if implemented, never inferred from warm-start.
5. Run bounded real server training/transfer/inference/reconnect/cancel acceptance when the configured server and weights are available. Record unsupported/unverified combinations.

## 4. Data improvement loop

1. Register service captures as intake candidates with source hash, duplicate/failure/unknown routing and explicit adoption into a versioned dataset.
2. Send inspection errors/review results to the existing review queue with originating run/node evidence. Human review and adoption remain explicit.
3. Select compatible parent models by task/classes/data lineage; changed class vocabularies block incompatible heads or require an explicit reinitialization policy.
4. Compare candidates on the same frozen cohort, show regressions and unavailable metrics, require human approval and preserve rollback.
5. Add a downstream impact report for data/label/model/rule changes and invalidate stale evidence rather than hiding it.

## 5. Independent packaging and recovery

1. Complete frozen backend packaging, self-contained launch configuration and offline prerequisite inventory. Build diagnostics bind to package/runtime identity.
2. Implement platform signing/readiness checks and release matrix; unsigned artifacts remain visibly unsigned. Windows/Linux build and install validators use target-specific checks.
3. Harden interrupted install/update, restart and rollback journals. Keep prior working service available when candidate acceptance fails.
4. Validate isolated macOS package launch and service restart with a known image where local dependencies permit it. No global installation changes.
5. Physical Windows/Linux/signing/channel acceptance remains an external prerequisite when no target or identity is configured; deliver build hooks, executable validators and actionable diagnostics.

## Ownership and integration

- Coordinator: this plan/ledger; shared `api.ts` and app router; flow version actions, job status UX, debugger/inspector; intake/lineage and integration; real acceptance; final commit/push.
- Semantics implementer: class-role backend/runtime/analyzer/evaluation renderer; explicit role API and focused tests. Additional exporter work only after a recorded handoff.
- Compute implementer: `backend/remote/*`, compute routes, remote Docker dependency manifest and focused tests. Coordinate before editing training routes or shared schemas.
- Flow evaluation implementer: new flow-evaluation engine/route/schema and dedicated panel/service; coordinator owns shared API/router registration.
- Delivery implementer: backend build scripts, electron launch/package configuration, runtime update/recovery contracts and focused tests; coordinate engine package changes before editing.

Independent file sets may run concurrently. Each implementer observes a failing behavior test before implementation, runs only affected tests and returns evidence and limitations. Nobody resets, stages or commits another person's changes. The coordinator reviews integration, runs renderer/type/build and one final backend suite, records source hashes and publishes to `main`.

## Completion tracking

Use `docs/implementation-ledger/STAGES-0-5.md` for task state, evidence, reviewed defects, target prerequisites and publication SHA. A task is complete only when its implementation and relevant acceptance evidence are recorded. Existing implemented functions are verified and extended, not rebuilt.
