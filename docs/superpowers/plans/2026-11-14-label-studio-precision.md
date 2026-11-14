# Label Studio Polygon Precision Implementation Plan

> **For agentic workers:** Use test-driven implementation and an independent whole-branch review. Track actual results in this plan's ignored SDD ledger.

**Goal:** Try selected Label Studio geometry in 모두비전's existing polygon editor on a separate feature branch.

**Architecture:** Keep current annotation store/save/backend authoritative. Add pure polygon editing helpers, scoped canvas point selection and a compact accessible point-control surface. No new runtime dependencies or independent annotation server.

**Tech Stack:** Existing React 18, TypeScript, Zustand, Canvas, Node renderer tests and isolated Playwright browser fixtures.

**Spec:** `docs/superpowers/specs/2026-11-14-label-studio-precision-design.md`

## Global Constraints

- Work on `feat/label-studio-precision-20261114`, base `08066611`; preserve primary main and all user data.
- Original pixel coordinates and annotation metadata stay canonical; both polygon aliases stay synchronized.
- Keep existing save/revision/lease/review/provenance behavior and Korean workspace controls.
- Pin upstream code and retain LICENSE/NOTICE/modification attribution.
- Do not install or replace the user's running app/web, run models or merge main.

## Review Focus

- Selection from an old image/project/labelset must not edit or delete current data.
- Point deletion at three vertices must preserve the polygon and explain why.
- Zoom/pan, closing-edge insertion and non-square images must retain original coordinates.
- Keyboard focus/IME/modal ownership and undo/redo must remain safe.
- Legacy points aliases and machine provenance must survive save/reopen.

### Task 1: Geometry and upstream attribution

- [x] Write failing pure tests for edge projection, duplicate/minimum geometry, immutable vertex insert/delete/move and polygon/points/bbox updates.
- [x] Add typed upstream projection with pinned LICENSE/NOTICE and local bounded segment wrapper in `src/renderer/components/labeling/polygonEditing.ts`.
- [x] Run focused tests and provenance checks; record actual results.

### Task 2: Actual canvas point editing

- [x] Add failing event/store regressions covering Shift-edge insertion, selected-point deletion/nudge, scope changes and undo.
- [x] Integrate helpers through current `updateAnnotation`; add scoped selection, highlight and point controls.
- [x] Exercise the actual canvas and controls; keep ordinary whole-shape actions unchanged when no point is selected.

### Task 3: Verification and publication

- [x] Run renderer suite, type checks, build and isolated browser geometry/save-reopen checks.
- [x] Read source-bound acceptance impact; preserve old receipts and identify changed-source limits.
- [x] Run a fresh independent review and fix important findings with regression tests.
- [ ] Commit, push the feature branch and create a draft PR with concrete behavior, validation and limits. Do not merge.
