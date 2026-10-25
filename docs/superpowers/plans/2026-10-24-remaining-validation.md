# Remaining Validation Implementation Plan

> For agentic workers: execute the three independent tasks with focused tests and an independent review; continue without a new general approval.

**Goal:** Complete software that collects the remaining usability evidence, removes repeated-image labeling work, and prepares explicit real-account validation.

**Architecture:** Dependency-free recorder plus existing onboarding/store integration; bounded content-addressed SAM2 embedding entry; production-native acceptance runner with no automatic provider activity.

**Tech Stack:** Existing TypeScript/React/Zustand, Python/PIL/PyTorch, Electron/Playwright CJS. Existing dependencies only.

**Spec:** `docs/superpowers/specs/2026-10-24-remaining-validation-design.md`.

## Global Constraints

- Preserve original images, confirmed labels, fingerprint/label-set/truth/approval, backup/undo and team guards.
- No credentials or image transmission without explicit human participation; no other app's auth files.
- Recorder acceptance always unverified; no raw input/text/path/token capture; no automatic writes or acceptance.
- Preserve remote CI, stopped recurring monitor and unrelated worktrees. Root owns Git publication/conditional merge.

## Review Focus

- Pre-project setup counts must survive until one explicit binding; later authority/scope changes seal a partial.
- Incomplete native-dialog observation must not become a 40-click success claim.
- Cancelled/failed/concurrent SAM2 requests must never mix image embeddings and prompts.
- The real-account runner must not accidentally retain fixture network hooks or inherited secrets.
- Exported snapshots must stay immutable and contain only allowlisted scalar/counter fields.

## Task 1: First-use measurement

Files: new controller/store/component beside `components/onboarding/firstRun.ts`; integrate `FirstRunStudio.tsx` and one global observer in `App.tsx`; existing test conventions.

Interfaces: `start({mode,task,build})`, `bindCurrentScope()`, `count(kind)`, `mark(enum)`, `abort(enum)`, `stop()`, `snapshot()`. Scope stays private; observers use existing stores/API context and clean up listeners.

- [x] Fake-clock/controller RED for wait-inclusive time, duplicate starts, immutable exports, private payload exclusion, modes, scope abort and unobserved native actions.
- [x] Implement minimal controller/state and explicit bind/export controls; optional manual/assisted image markers and honest comparison.
- [x] Mounted-control tests and one owned browser trial in automated mode; verify no label/provider writes, listeners cleaned and original inputs unchanged.
- [x] Independent spec/quality review; fix findings before integration closure.

## Task 2: SAM2 repeated-click cache

Files: `backend/engine/foundation_labeling.py` and existing/focused backend tests.

- [x] RED: fresh PIL objects with identical pixels currently encode repeatedly; in-place pixel mutation currently keeps stale embeddings.
- [x] Implement one image key and preserve per-request decode/cancellation/serialization.
- [x] GREEN: unchanged pixels reuse once; changed pixels/shape/mode invalidate; prompt changes decode again; cancellation/failure cannot publish a partial entry.
- [x] Minimal offline synthetic real-model repeated-click comparison if needed; retain matching output/latency/conditions without field-quality claims.
- [x] Independent review of cache lifetime, model identity, locks and cancellation.

## Task 3: Native real-account preparation

Files: QA runner beside `scripts/qa/chatgpt-native.cjs`, bounded tests and `scripts/qa/README.md`.

- [x] Inspect existing production IPC/events and environment allowlist; test that prepare-only cannot authenticate/send and output projection excludes private fields.
- [x] Build isolated normal-keychain app without fake provider/browser bootstrap. Require explicit human controls for real network actions.
- [x] Provide bounded one-request/empty-tool verification capability and privacy-safe result projection; never auto-run it.
- [x] Prepare/build and open the reviewable login-ready app when appropriate, then request only the necessary human login/consent while independent tasks continue.
- [x] Final independent review, affected local suites and evidence gates.
- [ ] Meaningful commit/push and exact-head hosted qualification/conditional merge (separate from local checks).
