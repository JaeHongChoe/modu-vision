# Codex service extension execution

> For agentic workers: execute the existing phase plans with test-first changes and independent review. This record assigns work; it does not mark parent acceptance complete.

**Goal:** Complete the additional model, data and operations implementations selected by the user from the existing 82-item service program.

**Architecture:** Extend the existing modular API, training workers and durable inspection runtime. Keep existing checkpoint/API behavior by default and introduce explicit versioned opt-in capabilities. Preserve shared checkout changes through an isolated branch and integrate tested slices separately.

**Spec:** `../specs/2026-10-02-windows-open-source-service-design.md`

**Tech stack:** Python/FastAPI/SQLite/PyTorch, React/TypeScript/Electron, optional Ultralytics, Windows SCM.

## Ownership and order

| Lane | Existing requirements | Owned implementation boundaries |
| --- | --- | --- |
| Model adapters | S4-06, S4-07 | OCR/OBB engines, routes and workbenches; local explicit model paths and backward-compatible checkpoint dispatch |
| Training | S4-01, S4-12 | DINO backbone/trainer, checkpoint state, selected-profile AutoDL trial orchestration, training controls |
| Inspection | S5-01, S5-02 | Service manager/SCM entry, durable admission identity, capacity/deadline/dead-letter, input adapters |
| Data | S3-08, S3-09 | Immutable brightness edits, captured-input review priority, source/run identity and explicit adoption |
| Operations | S5-06, S5-08, S5-09 | Staged fleet rollout, distribution drift, protected retention and backup/restore |
| Verification | S7-01, S6-09 | Slice receipts, fresh combined regression, independent review and publication hygiene |

As of the final 2026-10-04 handoff, Codex owns the remaining development, including E01–E08, S2-01/S2-05, labeling and native app QA. The extension batch is merged in PR #3, NQA4 in PR #4, and the E06 sampling component in PR #5. Preserve the received source separately and compose reviewed slices onto current main. Parent acceptance stays scoped; the current progress summary is in `../../implementation-ledger/SERVICE-UPGRADE.md`. GPU execution is temporarily deferred for the user's network move; local implementation continues.

## Execution checks for every lane

- [x] Read the existing phase acceptance, current implementation and callers.
- [x] Write failing tests for missing behavior, including backwards compatibility and malformed/stale inputs; record the actual red result.
- [x] Implement through the existing production entry points and user controls, with explicit capability/provenance boundaries.
- [x] Run the focused tests and relevant existing suites; bind all receipts to source hashes and source commit.
- [x] Review independently, fix material findings, and re-run the affected checks.
- [x] Commit the owned batch and publish a review branch without overwriting Claude's working files; main integration is separate.

Executed evidence and remaining qualification are recorded in `../../verification/2026-10-04-service-extension-evidence.md`. Combined backend verification covers the complete collected file set in three isolated pytest processes with disjoint file lists; source hashes and per-process receipts are retained outside the public repository.

## Review focus

- Changed recipe during queued work must preserve the admission recipe, artifact hashes and delivery identity.
- A resume request must restore optimizer/scheduler/scaler/RNG and training identity or reject it; warm-start is distinct.
- Optional remote/OBB support must never silently fall back or download weights.
- Derived edits and field predictions must preserve source evidence and never grant truth/quality approval.
- Rollout/retention must protect active, pinned, offline and referenced artifacts; failures must remain visible and recoverable.

## Qualification gates

CPU/simulator regression, native macOS interaction, real GPU execution, Windows Session0/reboot, hardware readback, licensing/signing and model quality are recorded separately. This batch does not infer unexecuted gates from unit tests. Actual OS service registration, field deployment, retention of user data and automatic promotion are separate operational actions.
