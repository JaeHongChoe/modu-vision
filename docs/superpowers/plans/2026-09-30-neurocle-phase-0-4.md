# Neurocle phase 0–4 implementation plan

> **For agentic workers:** Use subagent-driven-development or executing-plans task by task. This plan records the previously agreed 0–4 scope; a passing test or UI control alone is not a field deployment claim.

**Goal:** Make the desktop workflow, saved flow, exported runtime, and operator review operate on the same versioned evidence from image import through a persistent inspection service.

**Architecture:** Keep the existing Electron/FastAPI studio and verified whole-flow package. Add project-scoped immutable inputs and label sets, typed execution operators, append-only operational decisions, specialized model paths, and a separately launched service that executes only verified packages. Persist job and verdict state in SQLite, fail closed to REVIEW, and expose explicit device and compute adapters.

**Tech stack:** React/TypeScript, FastAPI/Pydantic, PyTorch/OpenCV, SQLite, pytest, Electron.

**Spec:** Prior conversation master plan from 2026-09-29, plus `docs/NEUROT_PARITY_MATRIX_2026-09-29.md`.

## Global constraints

- Do not commit source images, labels, checkpoints, host credentials, or private seminar slides.
- Preserve source image coordinates and immutable model/graph/dataset identifiers in every inspection result.
- A missing ROI, model, server, or device result is REVIEW or error; it is never silently OK.
- Keep production model activation separate from the editable stage 5 active pointer.
- Real-data functional QA does not establish false-positive quality without a representative OK cohort.
- Do not launch an expensive training run or disrupt a shared GPU job without explicit operational authorization.

## Review focus

- Corrupt, moved, or modified source file after a saved data version: block the historical run or use a verified snapshot.
- Project switch during candidate generation, review, or active inspection: preserve original ownership and reject cross-project edits.
- Inspection process restart mid-run: recover a durable queued row once and retain a clear attempt history.
- Package hash or checkpoint mismatch: reject startup before loading the checkpoint.
- Disconnected camera, PLC, server, or webhook: record an explicit error/REVIEW state, never OK.

## Tasks

### 0. Project and immutable data

- [ ] Add project backup/restore manifest covering settings, labels, split, versions, model/flow/inspection references; verify hashes and prevent path escape.
- [ ] Add project-scoped named label sets with create/switch/list/delete, per-set annotation isolation, and conflict/restart tests.
- [ ] Bind saved training and inspection to a verifiable image+label snapshot or block a changed source.
- [ ] Verify two projects using one source folder retain independent label sets and histories after restart.

### 1. Five-stage typed inspection flow

- [ ] Add patch ROI, classification, Blob measurement, condition and aggregate node contracts to parser, validation, execution, package runtime, and editor.
- [ ] Show intermediate branch evidence, input/output type errors, version history, and Undo/Redo in stage 5.
- [ ] Run real ROI→segmentation+patch classification→aggregate, plus five distinct checkpoint chain if five valid models exist.
- [ ] Compare stage 5, stage 6, and package result on the same image, ROI and thresholds.

### 2. Closed-loop inspection and model release

- [ ] Persist a REVIEW work queue linked to row and source image; jump into label editing and retain append-only review audit.
- [ ] Add holdout-based A/B evidence and explicit candidate approval, activation, rollback and history; do not infer approval from a score.
- [ ] Add resumable stopped/error inspection semantics with a unique row identity and visible retry history.
- [ ] Prove CSV/JSON, stage 5/6 and package version and verdict parity.

### 3. Label and model families

- [ ] Extend model-assisted labeling with batch candidate, prompt/keyword constraints, review assignments and conflict detection.
- [ ] Implement real data→train→evaluate→flow→package paths for Patch Classification, OCR, rotated detection, and alignment/enhancement where practical.
- [ ] Keep procedural defect generation separate from a trained GAN and demonstrate any GAN path with a saved generator/checkpoint.
- [ ] Add warm-start retraining and candidate comparison with performance-retention gate; preserve old active model until approved.

### 4. Standalone runtime and compute

- [ ] Build a separately launched, token-authenticated, persistent package inference service with HTTP/file inbox adapters, durable queue and history.
- [ ] Add generic camera/device adapter contracts, bounded timeout/error reporting, and an outbound result delivery/retry log.
- [ ] Add REST/CLI job controls and profile-aware compute queue with per-server/GPU reservation and recovery.
- [ ] Prove app-closed operation, process restart recovery, corrupted package rejection, CPU/Edge execution, and disconnect→REVIEW behavior.

## Verification and release

- [ ] Run focused and full backend tests, typecheck, renderer build and targeted Node scripts.
- [ ] Test native app controls and real data normal/empty/error flows, restart, cancel, server disconnect and package rerun.
- [ ] Update feature matrix with implemented/partial/unverified status and exact evidence; commit and push the branch, verify remote HEAD.
