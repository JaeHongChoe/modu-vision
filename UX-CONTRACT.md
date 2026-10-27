# UX Contract

## Product context

The audience is Korean-speaking people preparing images, reviewing labels, training models and checking inspection results in Electron or the local browser. The task assistant reduces menu discovery while keeping human truth and approval decisions explicit. Interface locale is Korean; counts use `ko-KR`. The task card formats no timestamps and introduces no timezone/calendar policy. Locale alone does not establish a deployment market. Accessibility target is WCAG 2.2 AA; source/component checks do not establish a full accessibility audit.

## Business-context sources

| Domain / scope | Authoritative source | Source type | Reviewed date |
| --- | --- | --- | --- |
| Task plan, action scope, human stops, privacy and completion | `docs/superpowers/specs/2026-10-27-task-performing-assistant-design.md` | Current task design and DTO contract | 2026-10-27 |
| Task validation, durable execution and recovery | `src/renderer/services/assistantTasks/{types,catalog,controller,runner}.ts`; `backend/engine/assistant_tasks.py` | Domain implementation and API guards | 2026-10-27 |
| Permission and selected project authority | `backend/api/shared_authorization.py`; task runtime's existing app guards | Server authorization and domain implementation | 2026-10-27 |
| Established Korean workflow and truth/approval separation | `docs/product-direction-easy-labeling-agent.md`, current sections and dated QA caveat | Product direction | 2026-10-27 |
| Beginner guidance and evidence boundaries | `docs/superpowers/specs/2026-10-26-final-ux-guidance-design.md` | Workflow design | 2026-10-27 |

Deletion/retention changes, billing, legal copy, new external data transfer and production deployment are outside this task-card contract. This file grants no permission for those operations.

## Visual contract

`DESIGN.md` documents the established visual identity. Runtime tokens remain canonical: `styles/design-tokens.css` → workspace classes and semantic variables → shared components; `styles/theme-light.css` supplies the light adapter. `index.css` owns application font, focus, reduced motion and scrollbar baseline. Compare documented values with these runtime files before changing a shared recipe. This slice preserves both themes and introduces no token generator or new dependencies.

## Canonical UI Map

Only capabilities touched by the assistant task card are resolved here. Existing domain screens retain their own behavior and authorization.

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
| --- | --- | --- | --- | --- |
| Button | `.workspace-button` in `styles/design-tokens.css` | Runtime tokens and DESIGN.md | primary / secondary; native disabled | Task-card callback and state tests; integrated keyboard check |
| Ordered plan / saved results | `AssistantTaskCard` | Task DTO and action catalog | Ordered plan; allowlisted compact receipt | `AssistantTaskCard.test.cjs` |
| Task actions / recovery | `AssistantTaskController`, `AssistantTaskRunner` | Task spec, revision and authority guards | approve / pause / resume / refresh / restore | Controller/runner and task-card tests |
| Screen navigation | Parent callback and existing wizard navigation | Action catalog / runtime stage mapping | Open related, review or result stage | Task-card callback tests; integrated navigation check |
| Status / error feedback | Inline task-card status and alerts | Task/step state and controller error | polite status / persistent alert | Task-card state and escaping tests |
| Dialog | `components/common/WorkspaceDialog.tsx` | Existing workspace primitive | App-owned modal; persistent assistant is non-modal | Existing dialog consumers and integrated keyboard check |
| Scrollbar | Application `index.css`, light theme adapter | Existing runtime baseline; DESIGN.md migration note | Task list may add stable gutter geometry | Computed style / narrow integrated check; known global baseline gap |

The task card owns no table selection, select/listbox, date picker, search, CRUD form, toast, clipboard or upload interaction. It must not create local equivalents of existing owners when those capabilities are added later.

## Component behavior

- Native buttons expose a written action and standard Enter/Space behavior. Shared CSS owns hover, focus and disabled appearance. Disabled callback guards prevent direct invocation from dispatching.
- A card has one primary action. The parent assistant suppresses competing composer/confirmation primaries while the card owns the workflow.
- The task plan uses an ordered list, written status badges and `aria-current="step"`; receipt fields use definition lists. Keep labels readable without hover.
- The status region announces concise current state and step/count information. Do not place the full plan inside the live region. Errors remain visible as alerts.
- Task-card props contain no secret or direct service access. Render goals, labels, messages and errors as text; never interpret model output as markup.

## Flow ledger

| Operation | Trigger | Pending | Success destination / feedback | Failure recovery | Focus outcome | Source ref |
| --- | --- | --- | --- | --- | --- | --- |
| Plan confirmation | “계획 확인하고 실행” on a displayed draft | Disable during state load, busy, unavailable connection or competing work | Controller receives the displayed immutable ID/revision; runner updates real state | Reject stale authority/revision; keep error and current plan visible | Keep current native control; no modal | Task spec; controller `approve` |
| Start approved plan | Ready task's “작업 시작” | Same connection/ownership guards | Run approved plan through controller | Show controller error and saved task | Keep card context | Controller `resume` |
| Pause | “작업 일시정지” | Remains available while runner is busy; unavailable during state loading | Stop the next assistant action; show paused record | Keep error visible; never claim a background job was cancelled | Keep card context | Runner `pause`; controller `pause` |
| Human review | “검토 화면 열기” | Navigation disabled while state unsettled | Open the real relevant stage; no automatic truth/adoption | Display human stop and recorded reason | Parent navigation owns screen focus | Task spec; runtime action guards |
| Continue after review | Explicit “검토 결과 확인하고 이어하기” | Prevent duplicate dispatch | Controller reads current record and guards before continuing | Stay at the human stop if conditions still fail | Keep navigation choice available | Controller `resume`; runner |
| Job wait / failed read | “저장 결과 확인” | Disable concurrent read | Refresh saved status; job wait never means success by itself | Show inline error; do not blindly retry an action | Keep card context | Task spec; controller/runner |
| Resume uncertain / paused work | “저장 결과 확인 후 이어하기” or equivalent paused action | Prevent concurrent resume | Read and reconcile saved results before continuing unresolved actions | Preserve uncertainty until evidence exists | Keep card context | Task spec; runner reconciliation |
| Restore | Empty card's “저장된 작업 불러오기” | Show loading status and disable action | Display the current actor/context's saved task or honest empty state | Keep read error and retry action visible | Keep card context | Controller `restore` |
| Complete | Last recorded step completes | No fabricated progress estimate | Show recorded counts/IDs and result-screen action | Completion does not grant quality or deployment approval | Parent navigation owns result-screen focus | Task spec |

## Navigation and responsive behavior

The existing non-modal assistant sidebar owns its `26rem` width, max-width and scrolling. The card fits the available width, wraps controls/identifiers and introduces no fixed horizontal minimum. At most 24 steps are rendered inside a bounded plan area; current-step status stays outside it. An oversized/empty plan cannot be approved. This is defense against an invalid DTO, not permission to truncate an approved execution scope.

Navigation follows the existing app stages: project/import/split 1, label drafts 2, training 3, model evaluation/report 4, flow 5, trial inspection 6. Explicit `open_step` targets remain in the supported 1–6 range. Reading a task does not change the current project, source or stage. Related/review/result navigation requires an explicit native button.

## Overlays and feedback

The task card adds no dialog, toast or native `alert`/`confirm`/`prompt`. Shared `WorkspaceDialog` owns app-modal keyboard behavior when another workflow needs a dialog. The assistant remains non-modal. A persistent error is never available only in a toast. Task status and receipts remain inspectable after any transient feedback.

## Async and resilience

- Approval is a user action against the displayed ID/revision. The pure card captures those scalars; the controller and backend remain authoritative against stale records, connection changes and actor/project/source mismatches.
- Persist intent before side effects and reconcile unresolved actions before retry. The UI never manufactures success from a sent request.
- Connection loss, logout, competing writes and loading disable mutation/resume controls with a visible explanation. A controller owns recovery and state invalidation.
- Pause stops later assistant actions. An already-started training job continues independently; cancellation stays in its existing training workflow.
- Waiting for a job, waiting for a person, paused and uncertain are separate states with separate next actions. Failed work offers a saved-result read, not an automatic retry.
- No automatic model approval, draft adoption, truth confirmation, operational deployment or background-job cancellation is introduced by the card.

## Validation and sensitive-value handling

The task catalog owns supported action IDs and argument schemas. Plans have 1–24 steps. The card displays canonical action titles and allowlisted meaningful settings; a model-written label cannot replace the real action title. Unknown argument and receipt keys are hidden. Receipt display accepts only bounded scalar identifiers, finite counts and approved training-setting fields. File paths, images, arrays, hashes and binding/protocol fields are not rendered as result payloads.

The card owns no form or field validation and therefore introduces no native validation bubbles. Existing app services retain role, source, model, held-out and readiness guards. The connection-ready prop never grants execution permission by itself.

## Migration status

This initial contract covers the assistant task workflow and records existing shared owners. It does not certify every legacy screen or replace domain policy. The global WebKit-only scrollbar baseline and studio utility-token duplication are recorded in DESIGN.md for a separate shared-style change. Do not fix those by adding card-local global CSS.

## Verification

- Focused command: `node --test src/renderer/components/assistant/AssistantTaskCard.test.cjs`.
- Whole renderer suite: `npm run test:renderer`; type/build: `npm run typecheck`, `npm run build`.
- Static premium audit: run the installed skill's `scripts/audit_project.py` with `--mode strict`; retain the report and distinguish existing whole-repository findings from this card.
- Browser matrix for the integrated parent: draft, loading, unavailable connection, blocked, running/pause, human wait, job wait, paused/reconciliation, failure, completed; both themes, long Korean/identifiers, keyboard, narrow sidebar and reduced motion.
- Component tests cover primary hierarchy, disabled callback guards, captured approval identity, navigation/continue separation, pause semantics, receipt allowlisting, bounded plan and malicious-text escaping.
- Canonical siblings: `AssistantPanel.tsx` for action hierarchy, Korean copy and sidebar geometry; `WorkspaceDialog.tsx` for shared button/focus language.
- Component/source verification is not integrated browser operation, real provider execution, actual model quality, field-data acceptance or human usability evidence. Those claims require separate receipts.
