# Saved brush-mask class count implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Count actual saved foreground mask classes consistently across import and statistics.

**Architecture:** One read-only raster-label resolver shared by the two statistics
producers. Existing active annotation scope and training pixel normalization stay
in charge; annotation and training formats remain unchanged.

**Tech Stack:** Existing Python/FastAPI/Pillow/NumPy, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-25-brush-mask-class-counts-design.md`

## Global Constraints

- Preserve source images, saved annotation bytes, active labelsets and class guards.
- Do not alter _annotations, training readers, raw-source fallback or detection object-count contracts.
- Count distinct source images per present foreground class; ID0 supplies no foreground or OK truth.
- Preserve explicit ID255, binary normalization, unused palette entries, schema tolerance and I/O errors.
- No real accounts, original customer data, model training, deployment or dependency changes.

## Review Focus

- Palette-only and zero-raster overlays must agree across import and statistics.
- Mixed vectors/brushes and repeated strokes must count each class once per image.
- Active-labelset changes must not leak labels or pixels from a different scope.
- Unused/conflicting palette entries and explicit ID255 must preserve reader contracts.
- Read-only counters must leave source, overlay, raster and palette bytes unchanged.

### Task 1: Share active-raster foreground labels across class counters

**Files:**
- Modify: `backend/api/routes_dataset.py`
- Modify: `backend/engine/dataset_summary.py`
- Test: `backend/tests/test_dataset_reopen_counts.py`
- Test if needed: `backend/tests/test_dataset_summary.py`
- Test: `scripts/e2e/segmentation-brush-class-counts.spec.ts`

**Interfaces:** A read-only helper resolves distinct present foreground labels from
an already selected canonical overlay mask, annotations and palette. Import and
statistics retain their respective tag/count policies. No API schema changes.

- [x] Add real save/import/current-summary/statistics/reopen RED regressions for
  brush-only, mixed/dedup, palette-only, zero and active labelsets; assert literal
  counts and unchanged source/annotation hashes.
- [x] Run new cases against base; expected class-count assertions fail.
- [x] Implement the minimal shared resolver and both caller paths, preserving
  classification, detection and raw-source behavior.
- [x] Cover conflicting mapping, ID255, tag/background, missing/foreign mask
  pointers and tolerate_schema_errors where changed behavior needs protection.
- [x] Run focused + related backend tests; record scoped passes and pre-existing failures separately.
- [x] Add an independent saved two-class brush browser test with import and reopen
  class-count assertions, preserving historical executed spec bytes; run browser
  QA and E2E types without live app data.
- [ ] Run source/action gates and diff checks, independently review, record QA,
  commit and publish this bug as its own PR. No automatic main merge.

## Local validation

The scoped backend suite passes113/113; the two browser regressions and E2E types
pass. With the isolated CI-framework versions, the related backend suite has
114 passes and one pre-existing exact-response expectation failure in
`test_original_current_summary_saved_metadata_reads_close_and_drain`. The same
four additive response fields mismatch on unchanged base7a233d55; no class-count
value differs and the foreign-selector test passes. Source/action evidence gates
pass with historical GUI spec bytes unchanged. Hosted final-head CI and
independent review remain separate.
