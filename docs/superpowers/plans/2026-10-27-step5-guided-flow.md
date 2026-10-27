# Step 5 Guided Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the default Step 5 a short guided inspection preparation sequence while retaining advanced graph editing and evidence gates.

**Architecture:** Pure preparation component receives canonical graph/evidence summaries and guarded callbacks from FlowchartStudio. Studio switches presentation while existing save/recipe/test/evaluation owners retain state and side effects. Evaluation only changes explanatory Korean copy.

**Tech Stack:** Existing React 18, TypeScript, shared workspace CSS, Node tests, owned Playwright/Electron fixtures.

**Spec:** docs/superpowers/specs/2026-10-27-step5-guided-flow-design.md

## Global Constraints

- No new dependencies, provider requests, model training, user-data writes or deployment in QA.
- Keep graph/version/hash/truth/approval guards; never interpret a saved draft or single-image run as a valid saved-flow evaluation.
- Keep installed app, real localhost services, private QA and other branches untouched.
- Simple view defaults to preparation, full view retains the editor; view switching cannot save, replace or execute the graph.
- Use shared workspace tokens, plain Korean, written statuses and one primary action on each visible work surface.

## Review Focus

- A dirty or different graph must invalidate preparation's evaluation-complete state.
- Busy/source-changing state must not allow recipe/save/navigation mutations from a retained callback.
- Advanced multi-model graphs remain intact and intelligible in the summary, never reset to a single-model default.
- Returning from advanced editing preserves unsaved graph and recalculates preparation state.
- Narrow/light/keyboard users can reach the next action and advanced editor without horizontal overflow or hidden duplicate primaries.

### Task 1: Pure preparation panel

**Files:** Create src/renderer/components/flowchart/FlowPreparationPanel.tsx and FlowPreparationPanel.test.cjs.

**Interface:** Props contain area, models:{label,task,jobId}[], rules:string[], exactSaved:boolean, evaluationCurrent:boolean, busy:boolean, loading:boolean, problem?:string, next:{label:string,disabled:boolean,onClick:()=>void}, onAdvanced, onPrepare, onEvaluate, onReviewModel callbacks. No stores/services inside panel.

- [x] Write behavioral rendering/callback tests for pending model, dirty saved flow, stale evaluation, completed evaluation, busy callbacks and escaped labels. Observe failures before implementation.
- [x] Implement ordered summary using canonical workspace classes, one primary next action only on edit area, written statuses and accessible navigation. Secondary callbacks are guarded while busy/loading.
- [x] Run component tests and report evidence.

### Task 2: Integrate presentation and clarify evaluation

**Files:** Modify FlowchartStudio.tsx, FlowEditorWorkspace.tsx if necessary, FlowEvaluationPanel.tsx and related tests.

- [x] Add failing integration coverage for simple preparation default, full editor, advanced roundtrip preserving graph, exact saved evaluation and one visible primary.
- [x] Wire summaries and guarded next action to existing model/recipe/save/evaluation/step navigation. Hide advanced editor toolbar/canvas/pickers in guided mode while preserving canonical stores and dialogs.
- [x] Reset only view-local advanced mode on scope changes; remeasure canvas when revealed. Existing task/issue navigation that needs editing must open advanced mode.
- [x] Clarify model versus flow evaluation and simple-mode cohort action copy; retain all API calls and advanced truth/history controls.
- [x] Run related flow suites and types.

### Task 3: Verify and publish

- [x] Run renderer suite, typechecks, build and affected evidence/static gates; distinguish baseline failures if any.
- [x] Use owned synthetic UI fixture for guided → advanced → guided, save/evaluation prerequisite states, narrow light/dark and keyboard. No real model/provider requests.
- [x] Independent review of whole diff; fix concrete findings and verify scope.
- [x] Update DESIGN.md/UX-CONTRACT.md and Korean usage/verification notes with observed limits.
- [x] Commit/push only intended files and open/attach a draft PR based on feat/task-performing-assistant-20261027. Read back exact head/base/body and report hosted CI separately.

### Coverage follow-up requested by user

- [x] Test and fix the guide treating a valid receipt with zero confirmed truths as ready; keep the existing truth-review prerequisite.
- [x] Test and include learned enhancement/rotation preprocess model bindings in the summary.
- [x] Scoped independent review of both fixes; no backend policy change.
- [x] Verify the zero-truth/enhancement path in the owned browser and publish the follow-up to PR20 (publication readback retained in the private progress checkpoint).
