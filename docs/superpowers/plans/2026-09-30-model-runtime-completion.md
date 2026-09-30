# Model and portable runtime completion implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Complete remaining parent training and generic CPU Edge deployment, expose all implemented model families accurately.
**Architecture:** Extend current scoped model resolvers and workbenches; export a CPU portable deployment profile using the existing full-flow runtime. Keep model capability metadata grounded in actual factories.
**Tech Stack:** Python 3.10+, PyTorch, FastAPI, React, TypeScript, Electron.
**Spec:** docs/superpowers/specs/2026-09-30-model-runtime-completion-design.md

## Global constraints
Original inputs read-only; preserve parent weights; validate scope/source/classes/architecture/hash; no private inputs in Git; no false hardware or model-quality acceptance; normal main push is authorized.

## Review focus
- Verify DINOv3 CLS/SEG/patch and YOLO detection defaults through real pretrained weights, training, offline reconstruction, export and UI request fields. Preserve legacy reads and source weight receipts.
- Changed classes/architecture or tampered/stale checkpoint must reject continuation before modifying parent/candidate.
- Different project/source/labelset and restore aliases must preserve the current ownership contract.
- Cancellation must not publish an incomplete model as eligible.
- Edge bundle must refuse incompatible runtime/dependencies or changed manifest/model files, with no implicit device fallback.
- Catalog and selectors must show real supported paths and keep context changes from applying stale selections.

### Task 1: Verified parent training across families
Files: backend/engine/warm_start.py, trainer.py and family trainers; backend/api/routes_training.py and specialist routes; backend/tests/test_*warm* and scoped family tests.
Consumes current completed/scoped models and training provenance; produces compatible parent listing, strict initial weight loading or explicit statistical refit, immutable new candidate lineage and updated request fields.
- [x] Write failing signature/scope/hash/class/lineage and cancellation tests.
- [x] Implement detection/patch and specialist parent continuation with family-compatible resolvers; anomaly uses honest statistical semantics.
- [x] Run focused regressions and actual small image training where genuine inputs exist.

### Task 2: Generic CPU Edge deployment
Files: new backend/engine/edge_runtime.py and tests; full-flow package/export route and corresponding Stage 6 package UI.
Produces an explicit edge_cpu export profile with target declarations, requirements, installation/preflight/launch paths and manifest verification.
- [x] Write failing portable profile/preflight/integrity tests.
- [x] Implement profile without changing current export defaults or runtime device semantics.
- [x] Execute a real exported model package on CPU; distinguish host proof from board certification.

### Task 3: Model catalog and parent selection UI
Files: new capability API/engine module, Stage 3 catalog component, TrainingController and specialist workbenches/services/types; frontend/backend contract tests.
Consumes actual factory capabilities and Task 1 parent request fields; produces model-family visibility, prerequisites/device support, and stale-safe parent selection.
- [x] Write capability truth and UI lifecycle regressions.
- [x] Implement nine-family catalog and specialist selectors.
- [x] Verify TypeScript and native model/catalog flow.

### Task 4: Integration, evidence and publication
Files: docs/feature-completion.md and private QA evidence only outside public roots.
- [x] Independent scope/code review; fix actionable findings.
- [x] Focused/full backend and frontend/build/package gates.
- [x] Native real-data run/restart and complete CPU package proof.
- [ ] Update scope documentation; commit, main fast-forward, normal push and remote HEAD readback.
