# Focused Novice Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Implement the user's selected second-round option 3 across the existing six-stage desktop/web workflow, reducing simultaneous information without removing tools or changing authority.

**Architecture:** Keep stores, services, stage IDs, effects and guarded action handlers authoritative. One compact header/goal rail and a responsive right-hand next-action panel replace redundant global guidance; each stage retains a primary working area and named secondary/advanced disclosures. Existing theme tokens and generated light adapter own colors.

**Tech Stack:** Existing React 18, TypeScript, Zustand, Tailwind/CSS, Lucide, shared WorkspaceDialog/Radix primitives, node:test and Playwright; no added runtime dependency.

**Spec:** `docs/superpowers/specs/2026-11-14-novice-workspace-refactor-design.md`

## Global Constraints

- Selected visual: `docs/verification/assets/novice-workspace/selected-design.png` (round 2, displayed option 3).
- Preserve all ten model families, every six-stage route, native/web transports, project data, dirty labels/flows, assistant lifetimes, guards and explicit consent.
- Preserve explicit user theme preference; use warm light surfaces/forest accent and matching dark semantics.
- One primary next-action owner; navigation only navigates/focuses. It must never secretly train, evaluate, save or inspect.
- Errors, team locks, busy/uncertain states and stop/recovery actions remain visible. Hidden tools remain named and keyboard reachable.
- No installed-runtime replacement, real-account call, training, main merge, CI cancellation/duplicate dispatch or unrelated worktree changes.
- Root owns shared files/styles. Stage tasks use `modu-stage-*` classes and existing `workspace-*` tokens; do not edit shared files concurrently.

## Review Focus

- Dirty label/flow save failure refuses project/stage loss (Task 1 state-contract regression).
- At compact width or with assistant open, action/recovery remains reachable and editors retain usable size (Tasks 1/4/5 runtime geometry).
- Folding running training or flow editors preserves mounted state, keyboard ownership and cancel/stop (Tasks 3/4).
- Capability load/error/generation-only and all ten families remain truthful and reachable (Task 3).
- Recovery target navigation opens folded content, and modal focus returns to a visible trigger (Tasks 1–5 keyboard/failure tests).

### Task 1: Shared guided workspace

**Files:** `App.tsx`; `components/wizard/{WizardHeader,WizardFooter,beginnerJourney}.tsx/ts`; `components/common/OperatorGuidanceBanner.tsx`; `styles/design-tokens.css`; `styles/novice-workspace.css` (new); `scripts/generate_light_theme.cjs`; `styles/theme-light.css`; `DESIGN.md`; `UX-CONTRACT.md`.

**Interfaces:** Keep `WizardHeader` props and `WizardFooter` external API stable; parent adds `.modu-studio`, `.modu-workspace-body`, `.modu-stage-content`. `WizardFooter` owns `.modu-next-panel` inside the body. All existing readiness/actions/store hooks keep their signatures. Stage code may use `.modu-stage-header`, `.modu-stage-disclosure`, `.modu-stage-toolbar`, `.modu-stage-summary`, `.modu-stage-content` and existing semantic tokens. No stage task adds a second global next CTA.

- [x] Pin named four-goal navigation, visible settings/assistant utilities, compact action panel and preserved action guards with existing header/footer tests.
- [x] Replace redundant LearningJourneyPanel/workspace rows with named utility entries; preserve effects and transport remount key.
- [x] Make header/project and four-goal rail match chosen hierarchy; full controls remain reachable in settings and stage details.
- [x] Present readiness, main navigation action, details/recovery and optional automatic setup in the right panel; preserve dialog/keyboard lifetimes and contextual stage navigation.
- [x] Implement warm light/dark semantic tokens, responsive panel and common headers. Regenerate light adapter; no manual generated-file edits.
- [x] Run affected unit tests and typecheck; review then commit only owned files.

### Task 2: Data preparation workspace

**Files:** `components/dataset/DatasetStudio.tsx`, focused dataset helpers/styles/tests as needed; no root/shared edits.

**Interfaces:** Same store/actions, import handlers, `workflow-data`/split/recovery targets. Consume shared stage classes/tokens. Central gallery and source/label summary are primary; import/split/tools are named secondary paths. Existing labels/data must not be rewritten by a view change.

- [x] Add/update behavioral render test showing gallery/import/error primary and advanced management discoverable.
- [x] Move counts and filters into one calm toolbar; remove persistent left stack of statistics/cards from simple view. Keep split controls and warnings reachable and serious errors visible.
- [x] Use natural image ratios and plain captions matching selected reference; preserve thumbnail pagination, image-to-label navigation and source context.
- [x] Run dataset/layout tests, then report diff and evidence for independent review.

### Task 3: Training and evaluation

**Files:** `components/training/TrainingController.tsx`, training presentation helpers/tests; `components/evaluation/EvaluationStudio.tsx`, evaluation presentation helpers/tests.

**Interfaces:** Existing training/capability/evaluation stores and handlers unchanged. Consume stage classes/tokens. Use one shared advanced-settings render path where simple/full currently duplicate JSX.

- [x] Pin selected-family ready/loading/error, all ten-family access, running/cancel and stale/saved evaluation semantics.
- [x] Primary training area: selected purpose, input readiness, recommendation, actual run/start/stop. Consolidate model selection/location/transforms and advanced settings without unmounting active editors.
- [x] Primary evaluation area: model/cohort, correct/missed/overkill and image review. Put detailed metrics/history/comparison in named secondary paths; never hide missed-defect or stale-evidence blockers.
- [x] Run focused training/evaluation tests and report owned diff for review.

### Task 4: Labeling, flow and inspection

**Files:** `components/labeling/LabelingStudio.tsx`, relevant presentation/helpers/tests; `components/flowchart/FlowchartStudio.tsx`, relevant presentation/helpers/tests; `components/inference/InferenceCenterStudio.tsx`, relevant presentation/helpers/tests.

**Interfaces:** Preserve label canvas/toolbars/selection, graph ownership and IDs, primary saved-flow execution handlers, live job/stop/errors. Consume shared stage classes/tokens; never hide diagnostics by swallowing failures.

- [x] Pin editor visibility/geometry, draft preservation, team lock, graph mode navigation and saved-flow run/stop access.
- [x] Remove redundant introductory stacks; organize optional tools as named disclosures while keeping core canvas, current operation and recovery visible.
- [x] Keep advanced graph and single-model/benchmark functionality discoverable, with explicit distinction from saved-flow inspection.
- [x] Run focused layout/action tests and report owned diff for review.

### Task 5: Integration, visual verification and documentation

**Files:** `scripts/e2e/novice-workspace.spec.ts` (new), affected existing journey/a11y tests, `docs/novice-first.md`, verification report/screenshots, plan/ledger.

- [x] Exercise six stages and ten-family navigation on isolated synthetic fixtures; check unchanged dirty-save guards, recovery targets and automatic/assistant ownership.
- [x] Capture 1440×1024 light reference state, dark, compact width, assistant/modal open and key empty/error/running states. Compare selected reference and rendered screen together; correct major differences.
- [x] Run renderer/main/e2e typechecks, renderer tests, builds and required provenance/source checks. Update only changed actual-source evidence pins after meaningful checks.
- [x] Independent branch review of behavior, visual hierarchy and feature inventory. Record actual failures/limits; do not use older CI as new-head proof.
- [x] Update DESIGN/UX contract, novice guide and verification record; commit clean feature branch. Keep installation/merge/CI qualification separate.

## Execution decisions

User already authorized implementation and selected visual option 3. Use bounded parallel stage implementation with root owning common shell and integration, followed by independent review. No repeat execution-method approval is needed. Baseline is current integrated source; prior source checks are context, fresh targeted checks cover changes.

## Completion evidence

Implemented and independently reviewed on 2026-11-15 KST. See `docs/verification/2026-11-14-novice-workspace-refactor.md` and root `design-qa.md` for fresh checks, visual comparisons, preserved history and scope limits. All local tasks are complete; commit finalization is recorded in the ignored execution ledger. Runtime replacement, remote CI qualification and main integration remain separate.
