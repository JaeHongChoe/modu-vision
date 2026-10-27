# Task-performing Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** A natural-language task creates a reviewable plan and drives the real app/web workflow through verified, resumable actions.
**Architecture:** Common app action adapters, actor-scoped durable task records, a pure resumable supervisor and a visible task card in the existing assistant. Preserve the current ChatGPT/SIWC provider and all human truth/approval gates.
**Tech Stack:** Existing TypeScript/React/Zustand/Electron/FastAPI; no new dependencies.
**Spec:** docs/superpowers/specs/2026-10-27-task-performing-assistant-design.md

## Global Constraints

- Existing user app, real projects/images/labels/models,8443 and other checkouts remain untouched. New workspace starts at ca52ac7a.
- Existing ChatGPT/SIWC only; synthetic provider QA, no real provider requests or pixel transmission.
- Plan approval covers named immutable actions/args; no model-issued approval, truth declaration, draft adoption, deployment, deletion or permission change.
- Persist execution intent before side effects; unresolved running actions reconcile before any retry.
- Existing role, CSRF, selected-context, source/model/heldout guards remain authoritative.
- App and web use identical actions and feedback; preserve the existing Korean workspace UI.

## Review Focus

1. Fresh account/no project versus registered local copies with identical logical IDs. Backend actor isolation and authority-transition tests.
2. Lost response after training/evaluation actually starts. Runner and adapter reconcile tests must show no duplicate dispatch.
3. Manual A→B→A/source/settings changes while an await is pending. Snapshot identity and live write fence tests.
4. Mid-task logout/reload/provider change and a queued second writer. Durable recovery and UI ownership tests.
5. UNKNOWN truth, no first model, unsupported report cohort and unavailable resources. Explicit human stops/refusals rather than manufactured success.

### Task1: Durable actor-scoped task ledger and API
Files: backend/engine/assistant_tasks.py; backend/api/routes_assistant_tasks.py; backend/main.py and shared_authorization.py narrowly; backend/tests/test_assistant_tasks.py.
Consumes/produces JSON DTOs in the spec.
- [x] RED: actor isolation, no-project authenticated creation, plan/grant integrity, compare-and-swap, stale event, symlink/size limit, ambiguous-running recovery.
- [x] Implement atomic persistence and guarded state transitions, authenticated bootstrap exception narrowly scoped to this preparation API.
- [x] GREEN: focused tests plus existing account/context authorization controls.
- [x] Review exact diff; no real project writes.

### Task2: Typed plan validation and pure task runner
Files: services/assistantTasks/{types,catalog,runner}.ts and *.test.cjs.
- [x] RED: no unapproved execution, persist-before-execute, immutable args, stale binding, pause during await, lost response/recovery without retry, human/job wait.
- [x] Implement spec exports and strict action schema validation; per-step latest reads; no unbounded model loop.
- [x] GREEN: pure Node regressions.
- [x] Review task state/interface with backend counterpart.

### Task3: Shared real app actions, feature knowledge and adapters
Files: services/assistantTasks/{appActions,runtime}.ts; AutoProgressDialog.tsx; assistantRuntime/Tools types as coordinated; focused tests.
- [x] RED: actual underlying actions called with exact settings/source/model/cohort and expected results; unsupported/missing prerequisites refuse.
- [x] Extract reusable auto-progress calls, expose actions for project/import/drafts/training/evaluation/flow/inspection/report/navigation.
- [x] Implement latest screen/catalog observations and reconciliation from existing receipts. Preserve single-model template boundaries and unsupported report cohort refusal.
- [x] GREEN: adapter tests plus existing automatic progress and assistant regressions.

### Task4: API client, task store and GPT tool wiring
Files: services/assistantTasks/client.ts; stores/useAssistantTaskStore.ts; assistantTools/Runtime/Session/useAssistantStore as needed.
- [x] RED: propose never executes; only UI approves; provider/project changes fence work; concurrent legacy write refused; bootstrap project transition supported.
- [x] Implement plan create/read/list/events and bounded provider-facing summaries.
- [x] Connect proposed task to card and runner; retain old short-turn tool loop as model exchange, update system directions to task-aware behavior.
- [x] GREEN: integration tests and type checks.

### Task5: Visible plan/progress/pause/resume/results UX
Files: AssistantTaskCard.tsx; AssistantPanel.tsx; DESIGN.md and UX-CONTRACT.md reflecting canonical existing UI.
- [x] RED: accessible task card draft/running/wait/failed/completed; one primary action; pause distinct from training cancel; stale handlers cannot approve.
- [x] Implement Korean plan preview, current action, evidence links, human stop and recovery states using workspace buttons/tokens.
- [x] GREEN: component tests, responsive/browser keyboard checks.

### Task6: Integrated synthetic app/web QA and publication
Files: existing E2E harness and new task agent scenario; verification docs.
- [x] Exercise normal authenticated browser/desktop paths with controlled provider: propose→wait for confirmation/no write; confirm→real actions; pause/reload/reconcile; missing truth stop; errors visible.
- [x] Verify real saved project/split/task IDs and bytes independently of chat claims; job/evaluation/flow/report paths additionally use controlled adapter receipts. No extra real-data/provider/model runs.
- [x] Run relevant suites, types/build, source/evidence guards and independent final review.
- [ ] Commit/push meaningful changes, create/attach a draft PR stacked on PR18 while it remains unmerged.
- [ ] Hosted exact-head CI qualification is separate from local synthetic checks. Preserve earlier runs; no merge/deploy automatically.
