# Team Data Quality and Workflow Completion Implementation Plan

> **For agentic workers:** Use scoped parallel workers with disjoint file ownership and a final independent review. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Complete the selected current-workflow and team-data quality improvements in the existing app.

**Architecture:** Extend project metadata transactions, shared authorization, preparation/task handoffs and existing review queues. Separate backend team contracts, model workflow, delivery compatibility and renderer integration. Reuse current source/version/provenance and preserve immutable originals.

**Tech Stack:** Python/FastAPI/Pydantic, React/TypeScript/Zustand, Electron, project JSON and existing metadata locks.

**Spec:** ../specs/2026-10-01-team-workflow-completion-design.md

## Global Constraints
- Preserve source image and annotation bytes.
- Use generic product names in all public changes; exclude private source paths and credentials.
- Existing projects retain training eligibility until team review is explicitly enabled.
- Derive actor from authenticated identity when shared accounts are enabled.
- Distinguish execution job identity from model identity.
- Keep UI, persistence, failure, handoff and physical acceptance separate.
- Signing and release credentials are never fabricated or embedded.

## Review Focus
- Two clients editing one image: foreign/stale/expired lease cannot overwrite or release a renewed lease.
- Book or image changes after review: old votes cannot approve changed content; direct metadata approval cannot bypass review.
- Remote specialist job: cancel uses execution ID, reopen/evaluate/flow use the exact saved model and prepared input.
- Unknown or relocated project schema: reject before writing; legacy migration retains metadata and a recoverable backup.
- Project switch while a request is pending: old response cannot alter new project, model, book or lease state.

## Task 1: Team-data persistence and server guards
**Files:** Create `backend/engine/team_data.py`, `backend/api/routes_team_data.py`, `backend/tests/test_team_data.py`, API/concurrency tests. Modify metadata/annotation/metadata API/version restoration, shared authorization/account member lookup, grouped dataset views and training provenance where required. Do not edit `backend/main.py` or renderer files.
**Interfaces:** Produce the `/api/team-data` contracts and service/type description for Task 4. Consume existing request-scoped project and metadata transaction. Readiness reports eligibility and frozen guidance/policy identity.
- [x] Write and run failing tests for versioned book, path scope, assignments, lease conflicts, votes/invalidation, authorization, approved-only source selection and provenance.
- [x] Implement server APIs/guards with backward-compatible policy defaults.
- [x] Verify focused tests and report exact API payloads, role requirements and integration imports.

## Task 2: Consistent training and exact model handoffs
**Files:** Modify `model_catalog.py`, `routes_training_workspace.py`, training preparation/task services/types/components, specialist workbenches, scoped model/flow handoff and `FlowchartStudio.tsx`. Update README after current behavior verification. Do not edit shared App mounts, team-data or delivery files.
**Interfaces:** Consume current compute runner and prepared dataset contracts. Produce exact execution/model IDs, target-aware preflight and a scoped flow handoff. Task 4 mounts any new common UI.
- [x] Write and run regression tests for remote capability, remote specialist identity, full config/parent parity, actual-target readiness and non-destructive flow handoff.
- [x] Implement common execution target and exact task/model restoration.
- [x] Verify backend/frontend affected tests, typecheck and report integration requirements.

## Task 3: Safe project compatibility and configured delivery
**Files:** Modify `routes_project.py`, `product_delivery.py`, inspection service registration and delivery UI/service as needed. Create focused schema migration/distribution modules/tests. Main process/preload/ipc and package/build changes belong to this worker. Do not edit `backend/main.py`, App mounts, team-data, training or README.
**Interfaces:** Produce schema preview/apply, actual version/signing/update readiness and idempotent service registration. New router imports are handed to Task 4.
- [x] Reproduce future-schema write and service-registration/release-pinning failures in focused tests.
- [x] Implement pre-open gate and legacy schema 1 normalization with backup/receipt, preserving unknown manifest fields.
- [x] Implement configured update/signature evidence without publishing or inventing certification; keep unconfigured state actionable.
- [x] Implement durable idempotent registration resolving active approved release.
- [x] Verify focused backend/main-process tests, types and report platform limitations.

## Task 4: Team workspace and end-to-end integration
**Files:** Create team-data renderer service/panel/tests. Modify `LabelingStudio.tsx`, category/review/annotation stores as agreed, saved review queue handoff, `backend/main.py` router registration and narrowly scoped App mounts. Create acceptance ledger.
**Interfaces:** Consume Task 1 exact API; connect Task 2 preparation/handoff and Task 3 readiness without duplicating business rules.
- [x] Write failing behavior tests for scoped loading, book form, lease acquire/renew/release, review return/disagreement, queue filters and training handoff.
- [x] Implement accessible project/image selectors, progress, errors and stale-state protection.
- [x] Run integration regression, typecheck/build and independent review; resolve concrete findings.
- [x] Exercise isolated native UI routes using supplied images read-only, record exact observed paths and preserve original hashes.
- [x] Record each family and external prerequisite honestly; do not mark unobserved routes accepted.

## Execution decisions
The user explicitly selected priorities 1 and 3 for implementation. Work continues without another authorization prompt. Current clean main checkout is reused under prior authorization to integrate directly. Workers use disjoint ownership; the controller owns router/App mounts and final integration. Signing identities, deployment credentials and physical-device verification remain external prerequisites and are reported explicitly.

## Recorded outcome
All implementation workstreams above are integrated. Native observations, overlapping regression results and external prerequisites are recorded in `docs/implementation-ledger/TEAM-WORKFLOW.md`. Completed implementation does not imply all-ten-family native training, signed release, equipment acceptance or operational model approval.
