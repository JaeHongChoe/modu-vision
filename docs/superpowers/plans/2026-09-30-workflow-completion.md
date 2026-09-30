# Workflow Completion Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Each task has a failing behavioral test, implementation, focused verification, and review. Do not mark hardware acceptance or model quality complete from test fixtures.

**Goal:** Finish the remaining approved foundation and phase 1–4 operator workflows.

**Architecture:** Extend project-scoped persistence and the existing typed DAG/package runtime. Add domain routes and panels, immutable evaluation/release records, and shared runtime/compute state. Integrate through the current Electron app.

**Tech Stack:** React/TypeScript, FastAPI/Pydantic, PyTorch/OpenCV, SQLite, pytest, Electron.

**Spec:** `docs/superpowers/specs/2026-09-30-workflow-completion-design.md` and the approved user feature table.

## Global constraints

- No source images, labels, model weights, secrets, customer/company references in Git.
- Preserve backward compatibility, source-coordinate mapping, project/labelset ownership and version hashes.
- Invalid/missing execution inputs produce REVIEW/error, never implicit OK.
- Do not disrupt running apps, jobs or shared GPU workloads; use isolated QA profiles.
- Root owns `backend/main.py`, `src/renderer/services/api.ts`, common integration and documentation. Workers report needed shared interfaces before integration.

## Review focus

- Annotation edit after approval invalidates approval and records actor/revision.
- Round-trip formats preserve geometry and reject path escapes, duplicate IDs and unsupported task conversions.
- Class branch and patch/preprocess coordinate transforms behave identically in app/package.
- Runtime activation failure preserves the old service version; rollback readback proves the active version.
- A process crash/disconnect cannot double-allocate GPU resources or emit an OK hardware signal.

### Task 1: Data identity, review and annotation interoperability

**Files:** New `backend/engine/dataset_metadata.py`, `backend/api/routes_dataset_metadata.py`, `backend/engine/annotation_formats.py`, domain tests and dataset/label panels. Modify dataset/annotation/version/labelset APIs and stores as needed. Do not modify shared registration/client files.

**Interfaces:** Produce metadata routes under `/api/dataset/metadata`, stable image UUID/content hash/revision, workflow/audit actions, split-group and duplicate preview. Format routes use explicit source dataset and format. Export a local TypeScript domain client using root-exported `request` helper. Expose panel component integration points.

- [ ] Test stable identities/restart/project isolation, stale edit rejection, approval invalidation, actor audit and snapshot/backup preservation.
- [ ] Implement free tags, product/lot/group, review states, reviewer history and concurrency checks.
- [ ] Test and implement LabelMe/COCO/YOLO import/export round trips and safe failure for unsupported shapes.
- [ ] Test and implement group-aware splits, duplicate leakage warnings and UI metadata filters.
- [ ] Connect batch suggestions and prompt/exemplar constraints to reviewed adoption; do not invent semantic model output.
- [ ] Provide error-image stage-2 navigation hooks and run focused API/type checks.

### Task 2: Flow operators and complete model handoff

**Files:** Flow engine/routes/package/runtime/provenance, OCR/rotated/GAN/anomaly engines, FlowchartStudio and supporting flow stores/types, specialized training panels and domain tests. Own `trainer.py`; coordinate warm-start consumers with Task 3. Do not edit generic evaluation routes or shared client/registration files.

**Interfaces:** Preserve existing graph schema defaults. Add class predicate fields, patch/preprocess node types, per-node artifacts and specialized model task descriptors. Reuse checkpoint adapters for app/package; expose specialized model catalogs and package inclusion metadata.

- [ ] Test class branches (matching, absent class, invalid predicate) and legacy verdict branches.
- [ ] Implement patch-split/rotation/alignment/enhancement operators with source-coordinate evidence and editor controls.
- [ ] Persist and display per-node image/mask/intermediate artifacts with skipped/error explanations.
- [ ] Connect OCR expected-string/regex, rotated detection, anomaly mask/Blob and reviewed GAN adoption through actual flow/package paths.
- [ ] Extend rotated detection data/model capacity where needed; keep unsupported inputs explicit.
- [ ] Run focused functional and package parity tests, and real-data CPU smoke paths where truth exists.

### Task 3: Evaluation lifecycle, service deployment and compute/field adapters

**Files:** Evaluation/model-comparison/model-deployment/training/compute APIs; warm_start, remote coordinator/worker, inspection_service, industrial_adapters, new runtime deployment/shared scheduler modules; evaluation/compute/runtime panels and tests. Do not edit flow package/runtime while Task 2 is active; publish needed runtime-device interfaces to root.

**Interfaces:** Immutable evaluation IDs, comparison job progress/cancel states, release/service runtime identity readback, Modbus/HTTP adapter configuration, process-shared resource reservations. Metadata lookup consumes Task 1 stable source/image mappings without assuming fake product/lot labels.

- [ ] Test and implement immutable reevaluation history and full-test-set comparisons with progress/cancel/reopen.
- [ ] Aggregate errors by real product/lot metadata; expose filters/history and exact dataset binding.
- [ ] Implement explicit approved-release apply and rollback with package verification and runtime acknowledgment.
- [ ] Implement process-shared GPU reservation lease/recovery and preserve existing remote cancel semantics.
- [ ] Extend warm-start remote transfer/support where compatible; reviewed data remains verifiable.
- [ ] Implement Modbus TCP timeout/read/write/ack and HTTP MES mapping using loopback protocol tests; expose configuration without contacting real hardware.
- [ ] Add non-Python HTTP examples, service launch/install controls and runtime device configuration contract.
- [ ] Verify focused backend/type checks and independent service restart; no hardware/quality claims from mocks.

### Task 4: Unified provenance, integration and acceptance

**Files:** Root-owned shared registration/client, new provenance route/panel, project archive integrations, integration tests/scripts and QA documentation.

**Interfaces:** Consume Task 1 identities/review history, Task 2 graph artifacts and model adapters, Task 3 evaluation/deployment/service identities. Unified provenance accepts saved inspection row or model/flow and returns resolvable linked records.

- [ ] Test and implement provenance API/panel from image through label/split/model/flow/result.
- [ ] Register new routes and integrate panels in existing native workflow.
- [ ] Add CPU/CUDA/MPS runtime selection and fail-fast capabilities to package/service after Task 2 interfaces settle.
- [ ] Review each domain against all table clauses; dispatch fixes for missing execution handoffs.
- [ ] Run focused regressions, complete backend suite, typecheck/build and native real-data/restart/package/app-closed service QA.
- [ ] Record exact implemented/partial/unverified status and actual evidence; preserve separate pending history-publication approval.
